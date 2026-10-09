"""Synthetic first-trial excerpt replay, never a model/human quality comparison."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import settings
from drafting_fixtures import drafting_input
from test_publish_workflow import publication
from test_primary_research import uniskill_source
from test_workflow_runs import Adapters, workflow, ready
from src.writer.deep_researcher import DeepResearcher
from src.llm.client import ModelClient
from src.workflow.service import WorkflowService
from scripts.evaluate_drafts import workflow_receipt


@pytest.fixture
def replayed_run(publication, monkeypatch):
    p = publication
    source = uniskill_source().model_copy(update={'fetched_at': datetime(2026, 10, 8, tzinfo=timezone.utc)})
    # A minimal, deliberately non-numeric excerpt subset. Full study coverage
    # and automatic extraction regressions live in test_primary_research.py.
    excerpts = [
        ('page:6', 'At each training step, actor rollouts and skill proposals are generated from the same pre-update policy\nand skillbank snapshot.'),
        ('page:6', 'We compare UniSkill with training-free, RL-only, and\nmemory- or skill-augmented RL baselines.'),
        ('page:4', 'Accordingly, we retain only trajectories for which both reference sets remain nonempty after excluding\nthe proposer inputs, with all trajectories sharing the same task, retrieved skills, and current actor.'),
        ('page:9', 'Thus,Ralign is informative in aggregate among the sampled proposals\nbut does not guarantee improvement for every proposal.'),
    ]
    topic = p.pipeline.store.topic(p.artifact.topic_id).model_copy(update={'title': 'UniSkill research paper', 'url': source.url, 'suggested_angle': 'When should joint learning be considered?'})
    proposals = [{'url': source.url, 'location': location, 'text': text} for location, text in excerpts]
    research = asyncio.run(DeepResearcher(p.repo.parent / 'frozen-research', fetcher=lambda *args, **kwargs: source,
        model=SimpleNamespace(generate=lambda *args, **kwargs: json.dumps({'claims': proposals, 'gaps': []}))).research(topic))
    packet = research['packet']
    roles = ['mechanism', 'alternative', 'constraint', 'reversal']
    sections = []
    for index, (_, text) in enumerate(excerpts, 1):
        sentence = f'원문은 “{text}”라고 설명한다. [E{index}]({source.url})'
        sections.append({'id': roles[index-1], 'text': sentence, 'claims': [{'sentence': sentence, 'kind': 'source_claim', 'evidence_ids': [f'E{index}'], 'role': roles[index-1]}]})
    decision = '추론: reference sets가 nonempty 조건이면 training-free, RL-only 대안과 비교해 고려한다. aggregate sampled proposals 조건에서는 선택을 바꾼다.'
    sections.append({'id': 'decision', 'text': decision, 'claims': [{'sentence': decision, 'kind': 'inference', 'evidence_ids': ['E2', 'E3', 'E4'], 'role': 'decision'}]})
    payload = {'frontmatter': '---\nlayout: post\ntitle: "UniSkill 채택 조건"\ndate: "2026-10-09 09:00:00 +0900"\ncategories: [Tech]\ntags: [skills]\n---', 'sections': sections}
    edited = copy.deepcopy(sections[-1])
    edited['text'] = edited['text'].replace('고려한다.', '채택을 고려한다.')
    edited['claims'][0]['sentence'] = edited['text']
    def transport(**kwargs):
        result = {'sections': [edited]} if 'Explicit user editing directive' in kwargs['prompt'] else payload
        return json.dumps(result, ensure_ascii=False)
    model = ModelClient(transport=transport)
    p.pipeline.writer.llm = SimpleNamespace(generate=lambda prompt: model.generate('draft', prompt, artifact_dir=service.store.directory(run.id) / 'model-calls'))
    p.pipeline.writer.researcher.research.return_value = {'packet': packet}
    adapters = Adapters(p.pipeline, topic)
    monkeypatch.setattr(settings, 'telegram_chat_id', '7')
    monkeypatch.setattr(settings, 'telegram_reviewer_user_id', '7')
    sent = []
    async def telegram(payload):
        sent.append(payload)
        if len(sent) == 1:
            raise RuntimeError('offline interrupted delivery')
    def restart():
        return WorkflowService(p.repo.parent / 'runs', writer=p.pipeline.writer, adapters=adapters, publisher=p.publisher, sync=p.sync, briefing_sender=telegram)
    service = restart()
    run = asyncio.run(service.create_briefing(intent='Vault interests to Telegram'))
    assert run.status == 'NEEDS_RESEARCH'
    frozen = run.briefing.payload_sha256
    service = restart()
    run = asyncio.run(service.resume(run.id))
    assert run.briefing.payload_sha256 == frozen and sent[0] == sent[1]
    assert adapters.calls['profile'] == adapters.calls['collection'] == adapters.calls['curation'] == 1
    reviewer = run.reviewer
    run = asyncio.run(service.select(run.id, 1, reviewer=reviewer))
    assert run.status == 'REVIEW_READY', run.failures
    first = service.pipeline(run).get_draft(run.draft_id)
    # A hand-authored operator edit is counted separately from routine approval.
    run = asyncio.run(service.revise(run.id, first.id, first.content_sha256, reviewer=reviewer,
        instruction='판단 표현을 채택을 고려한다로 수정해줘', section_id='decision'))
    service = restart()
    bundle = asyncio.run(service.deliver(run.id, reviewer=reviewer))
    artifact = service.pipeline(run).get_draft(bundle['draft_id'])
    assert artifact.id != first.id
    assert artifact.content_sha256 != first.content_sha256
    assert bundle['files'][0]['text'].find('채택을 고려한다') >= 0
    assert hashlib.sha256(artifact.content_path.read_bytes()).hexdigest() == bundle['content_sha256']
    assert bundle['files'][0]['text'] and bundle['files'][1]['text']
    with pytest.raises(ValueError, match='current'):
        service.approve(run.id, first.id, first.content_sha256, reviewer=reviewer)
    service.approve(run.id, artifact.id, artifact.content_sha256, reviewer=reviewer)
    service.trial(run.id, artifact.id, artifact.content_sha256, reviewer=reviewer)
    actual_sync = p.sync.sync_post
    monkeypatch.setattr(p.sync, 'sync_post', lambda info: {'success': False, 'error': 'offline sync interruption'})
    result = asyncio.run(service.publish(run.id, artifact.id, artifact.content_sha256, reviewer=reviewer))
    assert result['success'] and result['sync_status'] == 'SYNC_FAILED'
    commit = result['commit_sha']
    monkeypatch.setattr(p.sync, 'sync_post', actual_sync)
    service = restart()
    resumed = asyncio.run(service.resume(run.id))
    assert resumed.publication['sync_status'] == 'SYNCED'
    assert resumed.publication['commit_sha'] == commit == p.git('rev-parse', 'HEAD')
    assert p.events == ['push']
    assert len(list((p.repo / '_posts').glob('*.md'))) == len(list(p.sync.wiki_dir.glob('*.md'))) == 1
    assert adapters.calls['research'] == 1 and settings.editorial_shadow_mode is True
    assert asyncio.run(service.resume(run.id)).publication == resumed.publication
    return workflow_receipt(service, run.id, synthetic=True)


def test_operator_edits_are_not_counted_as_automatic_quality(replayed_run):
    assert replayed_run['operator_interventions'] > 0
    assert replayed_run['human_scores'] is None
    assert replayed_run['synthetic'] is True
    assert replayed_run['measurements']['tokens'] is None
    assert replayed_run['measurements']['cost'] is None
    assert replayed_run['usage_capture_count'] == 2
    assert replayed_run['system_failures'] > 0
    assert replayed_run['routine_operator_actions'] > 0
    assert replayed_run['publication']['sync_status'] == 'SYNCED'


def test_receipt_missing_usage_is_unknown(workflow, tmp_path):
    service, _ = workflow
    run = ready(service)
    run.usage_refs = [str(tmp_path / 'missing-capture.json')]
    service.store.save(run)
    receipt = workflow_receipt(service, run.id, synthetic=False)
    assert receipt['measurements']['tokens'] is None
    assert receipt['capture_errors'] == ['capture_unavailable']


def test_receipt_preserves_known_call_usage_without_claiming_full_run(workflow, tmp_path):
    service, _ = workflow
    run = ready(service)
    capture = tmp_path / 'capture.json'
    capture.write_text(json.dumps({'stage': 'draft', 'elapsed_seconds': 7.16, 'attempts': [
        {'usage': {'input_tokens': 5892, 'output_tokens': 1569, 'total_tokens': 7461}}]}), encoding='utf-8')
    run.usage_refs = [str(capture)]
    service.store.save(run)
    receipt = workflow_receipt(service, run.id, synthetic=False)
    assert receipt['measurements']['tokens']['total_tokens'] == 7461
    assert receipt['measurements']['model_call_seconds'] == 7.16
    assert receipt['measurement_scope'] == 'available_captures_only'
    assert receipt['captured_model_stages'] == ['draft']
    assert receipt['human_scores'] is None and receipt['measurements']['cost'] is None


def test_blocked_run_receipt_keeps_failure_without_prose(workflow):
    service, _ = workflow
    service.writer.researcher.research.return_value = {'status': 'NEEDS_RESEARCH', 'reasons': ['missing_source']}
    run = ready(service)
    receipt = workflow_receipt(service, run.id, synthetic=True)
    assert receipt['status'] == 'NEEDS_RESEARCH'
    assert receipt['counts'] is None


def test_receipt_cli_writes_local_json_and_preserves_prior_report(workflow, tmp_path, capsys):
    from scripts.evaluate_drafts import main
    service, _ = workflow
    run = ready(service)
    output = tmp_path / 'receipt-output'
    args = ['--workflow-run', run.id, '--workflow-root', str(service.store.root), '--output', str(output), '--synthetic-replay']
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['run_id'] == run.id
    record = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert record['identity']['draft_id'] == run.draft_id and record['synthetic'] is True
    with pytest.raises(ValueError, match='empty'):
        main(args)
    args[args.index(str(output))] = str(settings.obsidian_vault_path)
    with pytest.raises(ValueError, match='blog_or_vault'):
        main(args)
