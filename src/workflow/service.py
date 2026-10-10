"""Typed workflow shared by CLI and Telegram. All writes here are local review state."""
import asyncio
import copy
import inspect
import json
from pathlib import Path
import secrets
import time
from datetime import datetime, timezone

from src.llm.captures import canonical_capture_paths
from src.workflow.models import WorkflowRun, TelegramBriefing, BriefingPayload
from src.workflow.store import RunStore, atomic_json
from src.editorial.pipeline import EditorialPipeline
from src.editorial.models import DraftStatus, ResearchPacket, ResearchBlocked, SourceRecord
from src.profiler.interest_profiler import InterestProfiler, UserProfile
from src.collector.orchestrator import TrendOrchestrator
from src.collector.base import TrendItem
from src.curator.matcher import TrendMatcher, CuratedTopic


def json_value(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


class ProductionAdapters:
    def __init__(self, profiler=None, collector=None, matcher=None):
        self.profiler = profiler or InterestProfiler()
        self.collector = collector or TrendOrchestrator()
        self.matcher = matcher or TrendMatcher()
    def profile(self, days, directory):
        profiler = copy.copy(self.profiler)
        profiler.artifact_dir = directory / 'model-calls'
        return profiler.build_profile(days=days)
    async def collect(self):
        items = await self.collector.collect_all(limit_per_source=8)
        usable = [item for item in items if item.title.strip() and item.url.startswith(('https://', 'http://'))]
        if not usable:
            return {'status': 'NEEDS_RESEARCH', 'reasons': ['collection_empty_or_unavailable']}
        return usable
    def curate(self, profile, items, count, directory):
        matcher = copy.copy(self.matcher)
        matcher.artifact_dir = directory / 'model-calls'
        topics = matcher.curate_top_5(UserProfile.model_validate(profile), [TrendItem.model_validate(x) for x in items])[:count]
        usable = [topic for topic in topics if topic.title.strip() and topic.url.startswith(('https://', 'http://'))]
        if not usable or len({topic.rank for topic in usable}) != len(usable):
            return {'status': 'NEEDS_RESEARCH', 'reasons': ['curation_no_selectable_topics']}
        return usable
    async def research(self, pipeline, topic_id, directory):
        researcher = copy.copy(pipeline.writer.researcher)
        researcher.artifact_dir = directory / 'research'
        return await researcher.research(pipeline.store.topic(topic_id))
    async def draft(self, pipeline, topic_id, research):
        recorded = dict(research)
        packet = recorded.get('packet')
        if isinstance(packet, dict):
            recorded['packet'] = ResearchBlocked.model_validate(packet) if packet.get('status') == 'NEEDS_RESEARCH' else ResearchPacket.model_validate(packet)
        if isinstance(recorded.get('source'), dict):
            recorded['source'] = SourceRecord.model_validate(recorded['source'])
        return await pipeline.generate(topic_id, research=recorded, revision=recorded.pop('_workflow_revision', None))


class WorkflowService:
    def __init__(self, root=None, *, writer=None, adapters=None, publisher=None, sync=None, briefing_sender=None):
        self.store = RunStore(root or Path(__file__).resolve().parents[2] / 'temp' / 'workflow')
        self.writer = writer
        self.adapters = adapters or ProductionAdapters()
        self.publisher, self.sync = publisher, sync
        self.briefing_sender = briefing_sender
    def pipeline(self, run):
        return EditorialPipeline(Path(run.review_root) if run.review_root else self.store.directory(run.id) / 'review', writer=self.writer)
    def status(self, run_id):
        return self.store.get(run_id)
    def _event(self, run, action, category='routine', **fields):
        actor = 'operator' if action in {'request', 'resume', 'select', 'approve', 'revise', 'explicit_reviewed_trial', 'publish', 'reconcile'} else 'system'
        run.events.append(dict(action=action, actor=actor, category=category, at=datetime.now(timezone.utc).isoformat(), **fields))
        captures = set(self.store.directory(run.id).rglob('model-call-*.json'))
        if run.review_root and run.draft_id:
            captures.update(self.pipeline(run).store.directory(run.draft_id).rglob('model-call-*.json'))
        run.usage_refs = [str(p) for p in canonical_capture_paths(captures)]
        self.store.save(run)
    def _identity(self, run, reviewer):
        if not reviewer or reviewer != run.reviewer:
            raise ValueError('unauthorized reviewer')
    def _current(self, run, draft_id, content_sha256):
        if draft_id != run.draft_id:
            raise ValueError('draft is not current revision')
        artifact = self.pipeline(run).get_draft(draft_id)
        if artifact.content_sha256 != content_sha256:
            raise ValueError('current content hash mismatch')
        try:
            self.pipeline(run).store.verify(artifact)
        except ValueError:
            if artifact.status == DraftStatus.APPROVED:
                self.pipeline(run).store.save(artifact.transition(DraftStatus.NEEDS_REVISION))
                run.approval = None
                run.status = 'NEEDS_REVISION'
                self._event(run, 'approval_invalidated', 'failure')
            raise
        return artifact
    async def request(self, *, mode='shadow', days=14, topic_count=5, reviewer=None, intent='', briefing=None):
        if briefing is not None:
            from src.workflow.telegram import configured_recipient
            briefing = TelegramBriefing.model_validate(briefing)
            chat, user = configured_recipient()
            if (briefing.chat_id, briefing.user_id) != (chat, user) or reviewer != f'telegram:{chat}:{user}':
                raise ValueError('unauthorized Telegram briefing recipient/reviewer')
        run = WorkflowRun(id=secrets.token_hex(8), mode=mode, days=days, topic_count=topic_count, reviewer=reviewer, intent=intent, briefing=briefing)
        self._event(run, 'request')
        return run
    async def create_briefing(self, *, mode='shadow', days=14, topic_count=5, intent='Telegram topic briefing', sender=None):
        from src.workflow.telegram import configured_recipient
        chat, user = configured_recipient()
        run = await self.request(mode=mode, days=days, topic_count=topic_count, reviewer=f'telegram:{chat}:{user}', intent=intent,
            briefing=TelegramBriefing(chat_id=chat, user_id=user))
        return await self.resume(run.id, briefing_sender=sender)

    async def _deliver_briefing(self, run, sender=None):
        from src.workflow.telegram import configured_recipient, send_briefing
        from src.editorial.store import bound_input_hash
        reason = 'telegram_briefing_delivery_failed'
        started = time.monotonic()
        try:
            chat, user = configured_recipient()
            if (run.briefing.chat_id, run.briefing.user_id) != (chat, user) or run.reviewer != f'telegram:{chat}:{user}':
                reason = 'telegram_briefing_recipient_changed'
                raise ValueError(reason)
            if run.briefing.delivered:
                return True
            if run.briefing.payload is None:
                topics = [CuratedTopic.model_validate(t) for t in run.checkpoints['curation']]
                rows = [[{'text': f'{t.rank}번 선택', 'callback_data': f'w:{run.id}:{t.rank}'}] for t in topics]
                rows.append([{'text': '새 run으로 새로고침', 'callback_data': 'refresh_topics'}])
                run.briefing.payload = BriefingPayload(text=TrendMatcher.format_telegram_card(topics), rows=rows)
                run.briefing.payload_sha256 = bound_input_hash(run.briefing.payload.model_dump(mode='json'))
                self._event(run, 'briefing_payload_frozen')
            if bound_input_hash(run.briefing.payload.model_dump(mode='json')) != run.briefing.payload_sha256:
                reason = 'telegram_briefing_payload_changed'
                raise ValueError(reason)
            payload = {'chat_id': run.briefing.chat_id, **run.briefing.payload.model_dump(mode='json')}
            self._event(run, 'briefing_started')
            await (sender or self.briefing_sender or send_briefing)(payload)
            run.briefing.delivered = True
            run.failures.pop('briefing', None)
            self._event(run, 'briefing_completed', elapsed_seconds=time.monotonic()-started)
            return True
        except Exception as error:
            run.status = 'NEEDS_RESEARCH'
            run.failures['briefing'] = {'status': run.status, 'reason_codes': [reason], 'error': type(error).__name__}
            self._event(run, 'briefing_failed', 'failure', elapsed_seconds=time.monotonic()-started)
            return False

    async def _call(self, fn, *args):
        if inspect.iscoroutinefunction(fn):
            return await fn(*args)
        return await asyncio.to_thread(fn, *args)
    async def _stage(self, run, name, fn, *args):
        if name in run.checkpoints:
            return run.checkpoints[name]
        started = time.monotonic()
        self._event(run, name + '_started')
        try:
            result = await self._call(fn, *args)
            data = json_value(result)
            if name == 'profile':
                from src.editorial.models import UserContext
                from src.editorial.store import _snapshot_context
                context, reasons = _snapshot_context(UserContext.model_validate(data.get('user_context', {})), self.store.directory(run.id) / 'profile-runs')
                if reasons:
                    context = context.model_copy(update={'diagnostics': (*context.diagnostics, *reasons)})
                data['user_context'] = context.model_dump(mode='json')
            if isinstance(data, dict) and data.get('status') in {'NEEDS_RESEARCH', 'NEEDS_REVISION'}:
                import re
                path = self.store.directory(run.id) / ('failed-' + name + '.json')
                atomic_json(path, data)
                run.failures[name] = {'status': data['status'], 'artifact_path': str(path), 'reason_codes': [r for r in data.get('reasons', []) if isinstance(r, str) and re.fullmatch('[a-z0-9_:.]+', r)]}
                raise ValueError('stage_blocked')
            run.failures.pop(name, None)
            run.checkpoints[name] = data
            self._event(run, name + '_completed', elapsed_seconds=time.monotonic()-started)
            return data
        except Exception as error:
            run.status = 'NEEDS_REVISION' if name == 'draft' else 'NEEDS_RESEARCH'
            run.failures.setdefault(name, {'status': run.status, 'reason_codes': [name + '_failed'], 'error': type(error).__name__})
            self._event(run, name + '_failed', 'failure', error=type(error).__name__, elapsed_seconds=time.monotonic()-started)
            return None
    async def resume(self, run_id, *, briefing_sender=None):
        with self.store.lock(run_id):
            run = self.status(run_id)
            category = 'recovery' if run.failures or run.publication_target else 'routine'
            self._event(run, 'resume', category)
            if not run.publication_target:
                return await self._resume(run, briefing_sender=briefing_sender)
            target = dict(run.publication_target)
            journal_path = self.pipeline(run).store.directory(target['draft_id']) / 'publication.json'
            journal = json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
            reconcile = journal.get('phase') in {'PUSHING', 'PUSH_UNCERTAIN'} or bool(run.publication and run.publication.get('status') == 'PUSH_UNCERTAIN')
        # A recorded publication attempt carries its exact prior user intent.
        # Release the run lock before the publisher acquires its reservation.
        await self.publish(run_id, target['draft_id'], target['content_sha256'], reviewer=target['reviewer'], reconcile=reconcile)
        return self.status(run_id)
    async def _resume(self, run, *, briefing_sender=None):
        directory = self.store.directory(run.id)
        if run.briefing is not None:
            from src.workflow.telegram import configured_recipient
            try:
                current = configured_recipient()
            except ValueError:
                current = None
            if current != (run.briefing.chat_id, run.briefing.user_id):
                await self._deliver_briefing(run, briefing_sender)
                return run
        profile = await self._stage(run, 'profile', self.adapters.profile, run.days, directory)
        if profile is None: return run
        items = await self._stage(run, 'collection', self.adapters.collect)
        if items is None: return run
        topics = await self._stage(run, 'curation', self.adapters.curate, profile, items, run.topic_count, directory)
        if topics is None: return run
        pipeline = self.pipeline(run)
        if 'topics' not in run.checkpoints:
            from src.editorial.models import UserContext
            context = UserContext.model_validate(profile.get('user_context', {}))
            run.checkpoints['topics'] = [pipeline.register_topic(CuratedTopic.model_validate(t), user_context=context) for t in topics]
            self._event(run, 'topics_frozen')
        if run.briefing is not None and not await self._deliver_briefing(run, briefing_sender):
            return run
        if run.selected_rank is None:
            run.status = 'AWAITING_SELECTION' if topics else 'NEEDS_RESEARCH'
            self.store.save(run)
            return run
        research = await self._stage(run, 'research', self.adapters.research, pipeline, run.topic_id, directory)
        if research is None: return run
        if 'draft' not in run.checkpoints:
            started = time.monotonic()
            self._event(run, 'draft_started')
            try:
                draft_input = {**research, '_workflow_revision': run.revision_request} if run.revision_request else research
                artifact = await self._call(self.adapters.draft, pipeline, run.topic_id, draft_input)
                run.draft_id = artifact.id
                run.status = artifact.status.value
                if artifact.status == DraftStatus.REVIEW_READY:
                    run.checkpoints['draft'] = artifact.id
                    run.failures.pop('draft', None)
                else:
                    run.failures['draft'] = {'status': artifact.status.value, 'artifact_path': str(artifact.report_path), 'reason_codes': ['draft_blocked']}
                self._event(run, 'draft_completed' if artifact.status == DraftStatus.REVIEW_READY else 'draft_blocked', 'routine' if artifact.status == DraftStatus.REVIEW_READY else 'failure', elapsed_seconds=time.monotonic()-started)
            except Exception as error:
                run.status = 'NEEDS_REVISION'
                run.failures['draft'] = {'status': run.status, 'reason_codes': ['draft_failed'], 'error': type(error).__name__}
                self._event(run, 'draft_failed', 'failure', error=type(error).__name__, elapsed_seconds=time.monotonic()-started)
        return run
    async def select(self, run_id, rank, *, reviewer):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            if 'topics' not in run.checkpoints:
                raise ValueError('resume curation before selection')
            topics = run.checkpoints['curation']
            indices = [i for i,t in enumerate(topics) if t['rank'] == rank]
            if len(indices) != 1:
                raise ValueError('invalid topic rank')
            if run.selected_rank is not None and run.selected_rank != rank:
                raise ValueError('run already selected; request a new run')
            run.selected_rank, run.topic_id = rank, run.checkpoints['topics'][indices[0]]
            self._event(run, 'select')
            return await self._resume(run)
    async def deliver(self, run_id, *, reviewer, sender=None):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            artifact = self.pipeline(run).get_draft(run.draft_id)
            self.pipeline(run).store.verify(artifact)
            bundle = {'draft_id': artifact.id, 'content_sha256': artifact.content_sha256,
                      'files': [{'path': str(p), 'text': p.read_text(encoding='utf-8')} for p in (artifact.content_path, artifact.content_path.parent / 'review.md')],
                      'media_paths': [str(p) for p in artifact.media_paths]}
            binding = dict(draft_id=artifact.id, content_sha256=artifact.content_sha256, review_sha256=artifact.review_sha256, reviewer=reviewer)
            if run.delivery != binding:
                if sender is not None:
                    await sender(artifact)
                run.delivery = binding
                atomic_json(self.pipeline(run).store.directory(artifact.id) / 'review-delivery.json', {**binding, 'run_path': str(self.store.directory(run.id) / 'run.json')})
                self._event(run, 'review_delivered')
            return bundle
    def approve(self, run_id, draft_id, content_sha256, *, reviewer):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            artifact = self._current(run, draft_id, content_sha256)
            expected = dict(draft_id=draft_id, content_sha256=content_sha256, review_sha256=artifact.review_sha256, reviewer=reviewer)
            if run.delivery != expected:
                raise ValueError('current full review must be delivered before approval')
            if run.approval != expected:
                if artifact.status != DraftStatus.APPROVED or artifact.approved_sha256 != content_sha256:
                    artifact = self.pipeline(run).approve(draft_id, content_sha256)
                run.approval = expected
                if run.mode == 'reviewed_trial':
                    run.trial_scope = dict(expected)
                run.status = 'APPROVED'
                self._event(run, 'approve')
            atomic_json(self.pipeline(run).store.directory(draft_id) / 'workflow-authorization.json', {'run_path': str(self.store.directory(run.id) / 'run.json')})
            return run
    async def revise(self, run_id, draft_id, content_sha256, *, reviewer, instruction='', section_id=None):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            import hashlib
            key = hashlib.sha256(json.dumps([draft_id, content_sha256, instruction, section_id], ensure_ascii=False).encode()).hexdigest()
            if key in run.revisions:
                return run
            artifact = self._current(run, draft_id, content_sha256)
            if run.publication_target:
                raise ValueError('publication attempt reserved this target; only retry/reconcile/sync is allowed')
            if artifact.status == DraftStatus.PUBLISHED or run.publication and run.publication.get('success'):
                raise ValueError('published one-post run cannot be revised; create a new run')
            if section_id is not None and not instruction.strip():
                raise ValueError('section revision requires an explicit instruction')
            directory = self.pipeline(run).store.directory(draft_id)
            run.revision_request = dict(base_draft_id=draft_id, base_content_sha256=content_sha256, instruction=instruction, section_id=section_id,
                baseline=json.loads((directory / 'draft.json').read_text(encoding='utf-8')), media_choice=json.loads((directory / 'media.json').read_text(encoding='utf-8'))) if instruction.strip() else None
            run.revisions[key] = True
            run.delivery = run.approval = run.trial_scope = None
            run.checkpoints.pop('draft', None)
            run.draft_id = None
            self._event(run, 'revise', 'editorial_intervention')
            atomic_json(directory / 'superseded.json', {'reason': 'workflow_revision'})
            return await self._resume(run)
    def trial(self, run_id, draft_id, content_sha256, *, reviewer):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            self._current(run, draft_id, content_sha256)
            if not run.approval or run.approval != run.delivery:
                raise ValueError('exact delivered approval required for trial')
            if run.mode == 'production':
                raise ValueError('production run cannot be silently downgraded')
            run.mode = 'reviewed_trial'
            run.trial_scope = dict(run.approval)
            self._event(run, 'explicit_reviewed_trial')
            return run
    def find_run(self, draft_id):
        matches = [self.store.get(p.parent.name) for p in self.store.root.glob('*/run.json')
                   if json.loads(p.read_text(encoding='utf-8')).get('draft_id') == draft_id]
        if len(matches) != 1:
            raise ValueError('no unique durable run for this draft; explicitly deliver the legacy review')
        return matches[0]

    async def review_legacy(self, store, draft_id, content_sha256, *, reviewer, sender=None):
        artifact = store.get_draft(draft_id)
        store.verify(artifact)
        if artifact.content_sha256 != content_sha256 or not reviewer:
            raise ValueError('legacy reviewer/content hash mismatch')
        recipient_path = store.directory(draft_id) / 'reviewer.json'
        if recipient_path.exists():
            recipient = json.loads(recipient_path.read_text(encoding='utf-8'))
            if reviewer != f"telegram:{recipient['chat_id']}:{recipient['user_id']}":
                raise ValueError('unauthorized legacy reviewer')
        marker = store.directory(draft_id) / 'review-delivery.json'
        if marker.exists():
            prior = json.loads(marker.read_text(encoding='utf-8'))
            if prior['reviewer'] != reviewer:
                raise ValueError('unauthorized legacy reviewer')
            run = WorkflowRun.model_validate_json(Path(prior['run_path']).read_text(encoding='utf-8'))
            if Path(prior['run_path']).parent.parent != self.store.root:
                raise ValueError('legacy review belongs to another workflow root')
        else:
            run = await self.request(reviewer=reviewer, intent='explicit legacy review')
            run.review_root, run.draft_id, run.topic_id = str(store.root), artifact.id, artifact.topic_id
            run.status = artifact.status.value
            bound = json.loads((store.directory(draft_id) / 'input.json').read_text(encoding='utf-8'))
            run.checkpoints.update(profile=UserProfile(core_interests=[], knowledge_depth={}, avoid_topics=[], target_domains=[], search_keywords=[], user_context=bound['user_context']).model_dump(mode='json'),
                collection=[], curation=[bound['topic']], topics=[artifact.topic_id])
            run.selected_rank = bound['topic']['rank']
            packet = json.loads(artifact.evidence_path.read_text(encoding='utf-8'))
            if packet and packet.get('status') != 'NEEDS_RESEARCH':
                run.checkpoints['research'] = {'status': 'RESEARCH_READY', 'packet': packet}
            if artifact.status in {DraftStatus.REVIEW_READY, DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
                run.checkpoints['draft'] = artifact.id
            self.store.save(run)
        bundle = await self.deliver(run.id, reviewer=reviewer, sender=sender)
        if artifact.status in {DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
            with self.store.lock(run.id):
                run = self.status(run.id)
                if artifact.approved_sha256 != content_sha256:
                    raise ValueError('legacy approved hash mismatch')
                run.approval = dict(run.delivery)
                self._event(run, 'legacy_approval_retained')
                atomic_json(store.directory(draft_id) / 'workflow-authorization.json', {'run_path': str(self.store.directory(run.id) / 'run.json')})
        return bundle

    def import_trial(self, store, draft_id, content_sha256, *, reviewer):
        marker = store.directory(draft_id) / 'review-delivery.json'
        try:
            prior = json.loads(marker.read_text(encoding='utf-8'))
            run = WorkflowRun.model_validate_json(Path(prior['run_path']).read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError):
            raise ValueError('legacy full review must be delivered before explicit trial import') from None
        if Path(prior['run_path']).parent.parent != self.store.root:
            raise ValueError('legacy delivery belongs to another workflow root')
        self._identity(run, reviewer)
        artifact = self._current(run, draft_id, content_sha256)
        if artifact.status not in {DraftStatus.APPROVED, DraftStatus.PUBLISHED} or artifact.approved_sha256 != content_sha256:
            raise ValueError('legacy exact APPROVED revision required')
        with self.store.lock(run.id):
            run = self.status(run.id)
            if run.delivery != {k: prior[k] for k in ('draft_id', 'content_sha256', 'review_sha256', 'reviewer')}:
                raise ValueError('legacy delivered binding changed')
            run.approval = dict(run.delivery)
            self._event(run, 'legacy_approval_retained')
            atomic_json(store.directory(draft_id) / 'workflow-authorization.json', {'run_path': str(self.store.directory(run.id) / 'run.json')})
        return self.trial(run.id, draft_id, content_sha256, reviewer=reviewer)

    async def publish(self, run_id, draft_id, content_sha256, *, reviewer, reconcile=False):
        with self.store.lock(run_id):
            run = self.status(run_id)
            self._identity(run, reviewer)
            from src.workflow.publication import authorize_publication, confirmed_recovery
            pipeline = self.pipeline(run)
            if draft_id != run.draft_id:
                raise ValueError('draft is not current revision')
            artifact = pipeline.get_draft(draft_id)
            if artifact.content_sha256 != content_sha256:
                raise ValueError('current content hash mismatch')
            if not confirmed_recovery(pipeline.store, artifact, run):
                artifact = self._current(run, draft_id, content_sha256)
            authorize_publication(pipeline.store, artifact)
            if run.publication and run.publication.get('success') and run.publication.get('sync_status') == 'SYNCED' and not run.publication.get('local_state_error'):
                return run.publication
            is_retry = run.publication_target is not None
            run.publication_target = dict(run.approval)
            self._event(run, 'publication_target_reserved')
        # The reservation prevents revision while the publisher holds its own
        # draft/repository locks; an overlapping call fails visibly at that lock.
        from src.publisher.git_publisher import GitPublisher
        from src.publisher.obsidian_sync import ObsidianSync
        result = await asyncio.to_thread((self.publisher or GitPublisher()).publish, pipeline.store, draft_id, content_sha256,
                                        sync=self.sync or ObsidianSync(), reconcile=reconcile)
        with self.store.lock(run_id):
            run = self.status(run_id)
            run.publication = result
            run.status = result['status']
            self._event(run, 'reconcile' if reconcile else 'publish', 'recovery' if reconcile or is_retry else 'routine')
        return result
