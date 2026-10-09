"""Shared fail-closed live publication policy, including direct operator calls."""
import json
from config import settings
from src.workflow.models import WorkflowRun


def production_block_reason():
    if settings.editorial_shadow_mode:
        return 'shadow mode: production publication denied'
    if not settings.editorial_cutover_authorized:
        return 'cutover authorization required'
    try:
        from scripts.evaluate_drafts import load_json, release_gate
        report = load_json(settings.editorial_quality_gate_report)
        gate = release_gate(report['runs'], report['human_reviews'], set(report['required_topic_types']))
        if not gate['eligible']:
            return 'quality gate blocked: ' + ', '.join(gate['reasons'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'quality gate report unavailable or invalid'
    return None


def authorize_publication(store, artifact, *, reserve=False):
    directory = store.directory(artifact.id)
    try:
        from pathlib import Path
        pointer = json.loads((directory / 'workflow-authorization.json').read_text(encoding='utf-8'))
        run = WorkflowRun.model_validate_json(Path(pointer['run_path']).read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('scoped workflow authorization required') from None
    expected_root = Path(run.review_root) if run.review_root else Path(pointer['run_path']).parent / 'review'
    if expected_root.resolve() != store.root or run.draft_id != artifact.id:
        raise ValueError('publication run/draft scope mismatch')
    expected = dict(draft_id=artifact.id, content_sha256=artifact.content_sha256, review_sha256=artifact.review_sha256, reviewer=run.reviewer)
    if not run.reviewer or run.approval != expected or run.delivery != expected or artifact.approved_sha256 != artifact.content_sha256:
        raise ValueError('exact delivered reviewer approval/hash required')
    if run.mode == 'shadow':
        raise ValueError('shadow mode: publication denied; explicitly request reviewed_trial for this approved revision')
    if run.mode == 'reviewed_trial':
        if run.trial_scope != expected:
            raise ValueError('one-post reviewed trial scope mismatch')
    elif reason := production_block_reason():
        raise ValueError(reason)
    # Verify every immutable bundle, including on sync-only retries.
    store.verify(artifact)
    if run.publication_target and run.publication_target != expected:
        raise ValueError('publication target scope changed')
    if reserve:
        from src.workflow.store import RunStore
        run_store = RunStore(Path(pointer['run_path']).parent.parent)
        with run_store.lock(run.id):
            current = run_store.get(run.id)
            if current.model_dump() != run.model_dump():
                raise ValueError('publication authorization changed concurrently; retry')
            current.publication_target = expected
            run_store.save(current)
    return run
