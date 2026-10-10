import asyncio
import json
from pathlib import Path
from src.workflow.service import WorkflowService


def test_persisted_call_copies_count_once_but_identical_distinct_calls_survive(tmp_path):
    from src.editorial.store import DraftStore
    from src.llm.client import ModelClient
    from scripts.evaluate_drafts import workflow_receipt
    service = WorkflowService(tmp_path / 'runs')
    run = asyncio.run(service.request(reviewer='cli:test'))
    store = DraftStore(service.store.directory(run.id) / 'review')
    draft_id = store.begin('fixture')
    working = store.directory(draft_id) / 'working/model-calls'
    client = ModelClient(transport=lambda **kwargs: 'same output', clock=lambda: 0)
    client.generate('draft', 'same prompt', artifact_dir=working)
    client.generate('draft', 'same prompt', artifact_dir=working)
    originals = sorted(working.glob('*.json'))
    for path in originals:
        record = json.loads(path.read_text())
        record['elapsed_seconds'] = 2
        record['attempts'][0]['usage'] = dict(input_tokens=7, output_tokens=3, total_tokens=10)
        path.write_text(json.dumps(record))
    store.persist(draft_id, 'fixture', {'status': 'NEEDS_RESEARCH', 'content': ''})
    # A later mutable working copy must not replace its immutable persisted copy.
    record['attempts'][0]['usage']['total_tokens'] = 999
    originals[0].write_text(json.dumps(record))
    unfinished = store.directory(store.begin('fixture')) / 'working/model-calls'
    client.generate('research', 'unfinished', artifact_dir=unfinished)
    missing = list(unfinished.glob('*.json'))[0]
    data = json.loads(missing.read_text())
    data['attempts'][0]['usage'] = dict(input_tokens=1, output_tokens=1, total_tokens=2)
    data['elapsed_seconds'] = 1
    missing.write_text(json.dumps(data))
    old_refs = [str(p) for p in service.store.directory(run.id).rglob('model-call-*.json')]
    run.usage_refs = old_refs
    service.store.save(run)
    receipt = workflow_receipt(service, run.id, synthetic=False)
    assert receipt['usage_capture_count'] == 3
    assert receipt['measurements']['tokens'] == dict(input_tokens=15, output_tokens=7, total_tokens=22)
    assert receipt['measurements']['model_call_seconds'] == 5
    assert receipt['human_scores'] is None and receipt['measurements']['cost'] is None
    assert 'capture_list_does_not_prove_full_run_coverage' in receipt['limitations']
    service._event(run, 'fixture')
    assert len(run.usage_refs) == 3
    assert str(missing.resolve()) in run.usage_refs
    assert all(str(path.resolve()) not in run.usage_refs for path in originals)
    data['attempts'][0]['usage'] = None
    missing.write_text(json.dumps(data))
    assert workflow_receipt(service, run.id, synthetic=False)['measurements']['tokens'] is None
