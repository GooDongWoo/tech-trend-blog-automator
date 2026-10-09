import asyncio
import subprocess
REAL_POPEN = subprocess.Popen
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from drafting_fixtures import drafting_input
from test_pipeline import setup_pipeline


class Adapters:
    def __init__(self, pipeline, topic):
        self.pipeline, self.topic = pipeline, topic
        self.calls = Counter()
        self.fail = None
    def profile(self, days, directory):
        self.calls['profile'] += 1
        return {'user_context': {}, 'core_interests': [], 'knowledge_depth': {}, 'avoid_topics': [], 'target_domains': [], 'search_keywords': []}
    async def collect(self):
        self.calls['collection'] += 1
        if self.fail == 'collection':
            raise RuntimeError('private provider content')
        return []
    def curate(self, profile, items, count, directory):
        self.calls['curation'] += 1
        return [self.topic.model_dump(mode='json')]
    async def research(self, pipeline, topic_id, directory):
        self.calls['research'] += 1
        return await pipeline.writer.researcher.research(pipeline.store.topic(topic_id))
    async def draft(self, pipeline, topic_id, research):
        self.calls['draft'] += 1
        if self.fail == 'draft':
            raise RuntimeError('private failure')
        from src.workflow.service import ProductionAdapters
        return await ProductionAdapters().draft(pipeline, topic_id, research)


@pytest.fixture
def workflow(tmp_path, drafting_input):
    from src.workflow.service import WorkflowService
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    adapters = Adapters(pipeline, pipeline.store.topic(topic_id))
    service = WorkflowService(tmp_path / 'runs', writer=pipeline.writer, adapters=adapters)
    return service, adapters


def test_resume_freezes_completed_stages_across_restart(workflow):
    from src.workflow.service import WorkflowService
    service, adapters = workflow
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        adapters.fail = 'collection'
        run = await service.resume(run.id)
        assert run.status == 'NEEDS_RESEARCH'
        assert 'private' not in run.model_dump_json()
        restarted = WorkflowService(service.store.root, writer=service.writer, adapters=adapters)
        adapters.fail = None
        run = await restarted.resume(run.id)
        assert run.status == 'AWAITING_SELECTION'
        assert adapters.calls['profile'] == 1
        adapters.fail = 'draft'
        run = await restarted.select(run.id, 1, reviewer='cli:alice')
        assert run.status == 'NEEDS_REVISION'
        adapters.fail = None
        run = await service.resume(run.id)
        assert run.status == 'REVIEW_READY'
        assert adapters.calls['research'] == 1
        before = adapters.calls.copy()
        await service.resume(run.id)
        await service.select(run.id, 1, reviewer='cli:alice')
        assert adapters.calls == before
    asyncio.run(scenario())


def ready(service):
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        await service.resume(run.id)
        return await service.select(run.id, 1, reviewer='cli:alice')
    return asyncio.run(scenario())


def test_delivery_required_identity_hash_and_stale_revision(workflow):
    service, adapters = workflow
    run = ready(service)
    artifact = service.pipeline(run).get_draft(run.draft_id)
    with pytest.raises(ValueError, match='delivered'):
        service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    asyncio.run(service.deliver(run.id, reviewer='cli:alice'))
    with pytest.raises(ValueError, match='reviewer'):
        service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:bob')
    with pytest.raises(ValueError, match='hash'):
        service.approve(run.id, artifact.id, '0' * 64, reviewer='cli:alice')
    service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    changed = asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice'))
    assert changed.draft_id != artifact.id and changed.approval is None and changed.delivery is None
    again = asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice'))
    assert again.draft_id == changed.draft_id
    with pytest.raises(ValueError, match='current'):
        service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')


def test_process_lock_busy_and_release(workflow):
    from src.workflow.store import RunBusy
    service, _ = workflow
    run = asyncio.run(service.request())
    with service.store.lock(run.id):
        with pytest.raises(RunBusy):
            asyncio.run(service.resume(run.id))
    assert asyncio.run(service.resume(run.id)).status == 'AWAITING_SELECTION'

def test_structured_cli_request_status_review_and_trial(workflow, capsys):
    import main
    import json
    service, _ = workflow
    assert main.main(['request', '--reviewer', 'cli:alice', '--intent', 'queue article'], service=service) == 0
    run_id = json.loads(capsys.readouterr().out)['run']['id']
    assert main.main(['resume', run_id], service=service) == 0
    capsys.readouterr()
    assert main.main(['select', run_id, '--rank', '1', '--reviewer', 'cli:alice'], service=service) == 0
    capsys.readouterr()
    assert main.main(['review', run_id, '--reviewer', 'cli:alice'], service=service) == 0
    bundle = json.loads(capsys.readouterr().out)['review']
    assert bundle['files'][0]['text'] and bundle['files'][1]['text']
    args = [run_id, bundle['draft_id'], bundle['content_sha256'], '--reviewer', 'cli:alice']
    assert main.main(['approve', *args], service=service) == 0
    capsys.readouterr()
    assert main.main(['trial', *args], service=service) == 0
    assert json.loads(capsys.readouterr().out)['run']['mode'] == 'reviewed_trial'

def test_bot_rejects_private_briefing_destination_before_profile(workflow, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    from config import settings
    service, adapters = workflow
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    app = TrendBotApp(workflow=service)
    with pytest.raises(ValueError, match='review'):
        asyncio.run(app.trigger_briefing(chat_id='8'))
    assert adapters.calls == {}


def test_bot_full_delivery_failure_never_records_approval_delivery(workflow, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    from config import settings
    from unittest.mock import AsyncMock
    service, _ = workflow
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    run = ready(service)
    run.reviewer = 'telegram:7:7'
    service.store.save(run)
    app = TrendBotApp(workflow=service)
    artifact = service.pipeline(run).get_draft(run.draft_id)
    bot = SimpleNamespace(send_document=AsyncMock(side_effect=[None, RuntimeError('send failed')]), send_message=AsyncMock())
    with pytest.raises(RuntimeError):
        asyncio.run(app.send_review(bot, '7', artifact))
    assert service.status(run.id).delivery is None
    assert bot.send_message.call_count == 0

@pytest.mark.parametrize('failure', ['profile', 'collection', 'curation', 'research', 'draft'])
def test_restart_after_each_failed_stage_preserves_completed_inputs(workflow, failure):
    from src.workflow.service import WorkflowService
    service, adapters = workflow
    fn_name = {'collection': 'collect', 'curation': 'curate'}.get(failure, failure)
    original = getattr(adapters, fn_name)
    def broken(*args):
        raise RuntimeError('temporary outage')
    setattr(adapters, fn_name, broken)
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        run = await service.resume(run.id)
        if failure in {'research', 'draft'}:
            run = await service.select(run.id, 1, reviewer='cli:alice')
        completed = set(run.checkpoints)
        previous = adapters.calls.copy()
        setattr(adapters, fn_name, original)
        restarted = WorkflowService(service.store.root, writer=service.writer, adapters=adapters)
        run = await restarted.resume(run.id)
        if run.selected_rank is None:
            run = await restarted.select(run.id, 1, reviewer='cli:alice')
        assert run.status == 'REVIEW_READY'
        for stage in completed & {'profile', 'collection', 'curation', 'research'}:
            assert adapters.calls[stage] == previous[stage]
    asyncio.run(scenario())


def test_process_crash_releases_kernel_lock(workflow, monkeypatch):
    import subprocess
    import sys
    from src.workflow.store import RunBusy
    service, _ = workflow
    run = asyncio.run(service.request())
    code = "from src.workflow.store import RunStore; import sys; s=RunStore(sys.argv[1]);\nwith s.lock(sys.argv[2]):\n print('locked', flush=True); sys.stdin.read()"
    child = REAL_POPEN([sys.executable, '-c', code, str(service.store.root), run.id], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(RunBusy):
            with service.store.lock(run.id): pass
    finally:
        child.kill()
        child.communicate(timeout=10)
    with service.store.lock(run.id): pass


def test_sync_profile_and_curation_do_not_block_polling(workflow):
    import threading
    service, adapters = workflow
    entered, release = threading.Event(), threading.Event()
    original = adapters.profile
    def profile(*args):
        entered.set()
        release.wait(2)
        return original(*args)
    adapters.profile = profile
    async def scenario():
        run = await service.request()
        task = asyncio.create_task(service.resume(run.id))
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.005)
        assert entered.is_set() and not task.done()
        release.set()
        assert (await task).status == 'AWAITING_SELECTION'
    asyncio.run(scenario())


def test_default_adapters_reuse_frozen_research_and_record_actual_calls(tmp_path, drafting_input, monkeypatch):
    from src.workflow.service import WorkflowService, ProductionAdapters
    from src.profiler.interest_profiler import UserProfile
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    topic = pipeline.store.topic(topic_id)
    calls = Counter()
    class Profiler:
        def build_profile(self, *, days):
            calls['profile'] += 1
            assert days == 14
            return UserProfile(core_interests=[], knowledge_depth={}, avoid_topics=[], target_domains=[], search_keywords=[])
    class Collector:
        async def collect_all(self, *, limit_per_source):
            calls['collection'] += 1
            return []
    class Matcher:
        def curate_top_5(self, profile, items):
            calls['curation'] += 1
            return [topic]
    original = pipeline.writer.researcher.research
    async def research(topic):
        calls['research'] += 1
        return await original(topic)
    pipeline.writer.researcher.research = research
    adapters = ProductionAdapters(Profiler(), Collector(), Matcher())
    service = WorkflowService(tmp_path / 'default-runs', writer=pipeline.writer, adapters=adapters)
    run = ready(service)
    assert run.status == 'REVIEW_READY'
    assert calls == {'profile': 1, 'collection': 1, 'curation': 1, 'research': 1}
    asyncio.run(service.revise(run.id, run.draft_id, service.pipeline(run).get_draft(run.draft_id).content_sha256, reviewer='cli:alice'))
    assert calls['research'] == 1

def test_user_revision_instruction_reaches_prompt_preserves_other_sections(tmp_path, drafting_input):
    import json
    from src.workflow.service import WorkflowService, ProductionAdapters
    from src.editorial.models import DraftText
    from test_drafting import FakeLLM
    from drafting_fixtures import response
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    adapters = Adapters(pipeline, pipeline.store.topic(topic_id))
    service = WorkflowService(tmp_path / 'instruction-runs', writer=pipeline.writer, adapters=adapters)
    run = ready(service)
    artifact = service.pipeline(run).get_draft(run.draft_id)
    original = DraftText.model_validate_json((artifact.content_path.parent / 'draft.json').read_text(encoding='utf-8'))
    target = original.sections[0]
    replacement = target.model_copy(update={'text': target.text + '\n\nWhich workload would fit?'})
    llm = FakeLLM({'sections': [replacement.model_dump(mode='json')]})
    service.writer.llm = llm
    # Keep injected offline collection/profile but exercise actual production drafting.
    adapters.draft = ProductionAdapters().draft
    instruction = '이 절의 판단 기준을 한 문장으로 정리해줘'
    revised = asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice', instruction=instruction, section_id=target.id))
    assert revised.draft_id != artifact.id
    assert revised.status == 'REVIEW_READY'
    updated = DraftText.model_validate_json((service.pipeline(revised).store.directory(revised.draft_id) / 'draft.json').read_text(encoding='utf-8'))
    assert updated.sections[0].text == replacement.text
    assert updated.sections[1:] == original.sections[1:]
    assert instruction in llm.prompts[0]
    assert 'Runtime editorial policy' in llm.prompts[0]
    assert original.sections[-1].text not in llm.prompts[0]
    again = asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice', instruction=instruction, section_id=target.id))
    assert again.draft_id == revised.draft_id
    with pytest.raises(ValueError, match='current'):
        asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice', instruction='다른 지시', section_id=target.id))

def test_returned_research_failure_retries_stage_without_recollecting(workflow):
    service, adapters = workflow
    original = adapters.research
    async def blocked(*args):
        adapters.calls['research'] += 1
        return {'status': 'NEEDS_RESEARCH', 'reasons': ['context_budget_exhausted']}
    adapters.research = blocked
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        await service.resume(run.id)
        run = await service.select(run.id, 1, reviewer='cli:alice')
        assert run.status == 'NEEDS_RESEARCH' and 'research' not in run.checkpoints
        adapters.research = original
        run = await service.resume(run.id)
        assert run.status == 'REVIEW_READY'
        assert adapters.calls['research'] == 2
        assert adapters.calls['collection'] == 1
    asyncio.run(scenario())


def test_returned_draft_failure_retries_drafting_not_research(workflow):
    import json
    from drafting_fixtures import response
    service, adapters = workflow
    service.writer.llm = SimpleNamespace(generate=lambda prompt: '{}')
    run = ready(service)
    assert run.status == 'NEEDS_REVISION' and 'draft' not in run.checkpoints
    service.writer.llm = SimpleNamespace(generate=lambda prompt: json.dumps(response()))
    recovered = asyncio.run(service.resume(run.id))
    assert recovered.status == 'REVIEW_READY'
    assert adapters.calls['research'] == 1 and adapters.calls['draft'] == 2


def test_failed_directed_revision_resumes_same_instruction_after_restart(workflow):
    import json
    from src.workflow.service import WorkflowService
    from src.editorial.models import DraftText
    from test_drafting import FakeLLM
    service, adapters = workflow
    run = ready(service)
    artifact = service.pipeline(run).get_draft(run.draft_id)
    original = DraftText.model_validate_json((artifact.content_path.parent / 'draft.json').read_text(encoding='utf-8'))
    target = original.sections[0]
    service.writer.llm = FakeLLM({})
    failed = asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice', instruction='Make this section concise', section_id=target.id))
    assert failed.status == 'NEEDS_REVISION'
    llm = FakeLLM({'sections': [target.model_dump(mode='json')]})
    service.writer.llm = llm
    restarted = WorkflowService(service.store.root, writer=service.writer, adapters=adapters)
    recovered = asyncio.run(restarted.resume(run.id))
    assert recovered.status == 'REVIEW_READY'
    assert 'Make this section concise' in llm.prompts[0]
    updated = DraftText.model_validate_json((restarted.pipeline(recovered).store.directory(recovered.draft_id) / 'draft.json').read_text(encoding='utf-8'))
    assert updated.sections[1:] == original.sections[1:]
    assert recovered.revision_request['base_content_sha256'] == artifact.content_sha256

def test_per_run_profile_curation_draft_and_review_capture_refs(tmp_path, drafting_input, monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from src.workflow.service import WorkflowService, ProductionAdapters
    from src.llm.client import ModelClient
    from src.profiler.interest_profiler import InterestProfiler
    from src.curator.matcher import TrendMatcher
    from src.writer.blog_writer import BlogWriter
    from test_llm_client import config
    from drafting_fixtures import response
    packet, _ = drafting_input
    topic = {'rank': 1, 'title': 'Queue', 'url': packet.sources[0].url, 'source': 'fixture', 'one_line_summary': 'queue', 'relevance_reason': 'queue', 'suggested_angle': packet.question}
    def transport(**request):
        prompt = request['prompt']
        if '\nREVIEW_INPUT\n' in prompt:
            data = json.loads(prompt.split('\nREVIEW_INPUT\n')[1])
            return json.dumps({'judgments': [{'id': c['id'], 'verdict': 'supported'} for c in data['claims']]})
        if '\nDrafting task:\n' in prompt:
            return json.dumps(response())
        if 'URL:' in prompt:
            return json.dumps([topic])
        return json.dumps({'core_interests': ['queue'], 'knowledge_depth': {}, 'avoid_topics': [], 'target_domains': [], 'search_keywords': ['queue']})
    client = ModelClient(config=config(), transport=transport)
    for module in ('src.profiler.interest_profiler', 'src.curator.matcher', 'src.writer.blog_writer'):
        monkeypatch.setattr(module + '.ModelClient', lambda: client)
    monkeypatch.setattr(BlogWriter, '_call_llm', lambda self, prompt: client.generate('draft', prompt, artifact_dir=self.artifact_dir / 'model-calls'))
    note = tmp_path / 'interest.md'
    note.write_text('queue interest', encoding='utf-8')
    profiler = InterestProfiler()
    profiler.scanner.get_recent_daily_notes = lambda days: [{'path': note}]
    profiler.scanner.get_active_projects = lambda: []
    item = {'title': 'Queue', 'url': topic['url'], 'source': 'fixture'}
    collector = SimpleNamespace(collect_all=AsyncMock(return_value=[item]))
    # Collector contract emits TrendItem instances.
    from src.collector.base import TrendItem
    collector.collect_all.return_value = [TrendItem.model_validate(item)]
    writer = BlogWriter(artifact_dir=tmp_path / 'unused')
    writer.researcher.research = AsyncMock(return_value={'packet': packet, 'status': 'RESEARCH_READY'})
    service = WorkflowService(tmp_path / 'capture-runs', writer=writer, adapters=ProductionAdapters(profiler, collector, TrendMatcher()))
    run = ready(service)
    assert run.status == 'REVIEW_READY'
    captures = [json.loads(Path(p).read_text(encoding='utf-8')) for p in run.usage_refs]
    assert {c['stage'] for c in captures} == {'profile', 'curate', 'draft', 'grounding'}
    assert all(Path(p).is_relative_to(service.store.directory(run.id)) for p in run.usage_refs)
    assert all(c['attempts'][0]['usage'] is None and c['elapsed_seconds'] >= 0 for c in captures)

def test_domain_failure_records_actionable_codes_not_operator_intervention(workflow):
    service, adapters = workflow
    async def blocked(*args):
        return {'status': 'NEEDS_RESEARCH', 'reasons': ['context_budget_exhausted']}
    adapters.research = blocked
    async def scenario():
        run = await service.request(reviewer='cli:alice')
        await service.resume(run.id)
        run = await service.select(run.id, 1, reviewer='cli:alice')
        assert run.failures['research']['reason_codes'] == ['context_budget_exhausted']
        failure = run.events[-1]
        assert failure['actor'] == 'system' and failure['category'] == 'failure'
    asyncio.run(scenario())

def test_structured_cli_keeps_adapter_diagnostics_out_of_json(workflow, capsys):
    import main
    import json
    service, adapters = workflow
    run = asyncio.run(service.request(reviewer='cli:alice'))
    original = adapters.profile
    def noisy(*args):
        print('adapter diagnostic')
        return original(*args)
    adapters.profile = noisy
    assert main.main(['resume', run.id], service=service) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['run']['status'] == 'AWAITING_SELECTION'
    assert 'adapter diagnostic' in output.err
