import asyncio
import pytest
from test_workflow_runs import workflow, ready
from config import settings


def approved(service, mode='shadow'):
    run = ready(service)
    asyncio.run(service.deliver(run.id, reviewer='cli:alice'))
    artifact = service.pipeline(run).get_draft(run.draft_id)
    service.approve(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    if mode == 'reviewed_trial':
        service.trial(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    return service.status(run.id), service.pipeline(run).get_draft(artifact.id)


def test_shadow_and_explicit_one_post_trial(workflow, monkeypatch):
    from src.workflow.publication import authorize_publication
    service, _ = workflow
    run, artifact = approved(service)
    store = service.pipeline(run).store
    with pytest.raises(ValueError, match='shadow'):
        authorize_publication(store, artifact)
    monkeypatch.setattr(settings, 'editorial_shadow_mode', True)
    service.trial(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    authorize_publication(store, artifact)
    assert settings.editorial_shadow_mode is True
    assert service.status(run.id).mode == 'reviewed_trial'
    with pytest.raises(ValueError, match='current|scope'):
        service.trial(run.id, 'x' * 16, artifact.content_sha256, reviewer='cli:alice')


def test_direct_publisher_without_scoped_authorization_denies_before_git(workflow):
    from src.publisher.git_publisher import GitPublisher
    service, _ = workflow
    run, artifact = approved(service)
    class NoGit(GitPublisher):
        def _repository(self):
            raise AssertionError('must authorize before Git')
    result = NoGit().publish(service.pipeline(run).store, artifact.id, artifact.content_sha256)
    assert result['success'] is False and 'shadow' in result['error']


def test_production_requires_real_quality_and_cutover(workflow, monkeypatch):
    from src.workflow.publication import authorize_publication
    service, _ = workflow
    run, artifact = approved(service)
    run.mode = 'production'
    service.store.save(run)
    monkeypatch.setattr(settings, 'editorial_shadow_mode', False)
    monkeypatch.setattr(settings, 'editorial_cutover_authorized', False)
    with pytest.raises(ValueError, match='cutover'):
        authorize_publication(service.pipeline(run).store, artifact)
    monkeypatch.setattr(settings, 'editorial_cutover_authorized', True)
    monkeypatch.setattr(settings, 'editorial_quality_gate_report', service.store.root / 'absent.json')
    with pytest.raises(ValueError, match='quality'):
        authorize_publication(service.pipeline(run).store, artifact)
from drafting_fixtures import drafting_input

def test_trial_reserves_one_target_even_after_failed_attempt(workflow):
    service, _ = workflow
    run, artifact = approved(service, 'reviewed_trial')
    class Publisher:
        def publish(self, *args, **kwargs):
            return {'success': False, 'status': 'PUSH_UNCERTAIN', 'sync_status': 'NOT_STARTED'}
    service.publisher = Publisher()
    asyncio.run(service.publish(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice'))
    with pytest.raises(ValueError, match='attempt|target'):
        asyncio.run(service.revise(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice'))


def test_legacy_trial_requires_proven_delivered_identity(workflow):
    service, adapters = workflow
    run = ready(service)
    artifact = service.pipeline(run).get_draft(run.draft_id)
    pipeline = service.pipeline(run)
    pipeline.approve(artifact.id, artifact.content_sha256)
    with pytest.raises(ValueError, match='deliver'):
        service.import_trial(pipeline.store, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    asyncio.run(service.deliver(run.id, reviewer='cli:alice'))
    imported = service.import_trial(pipeline.store, artifact.id, artifact.content_sha256, reviewer='cli:alice')
    assert imported.id == run.id and imported.mode == 'reviewed_trial'

def test_workflow_publication_reconcile_and_sync_only_retry(workflow):
    service, _ = workflow
    run, artifact = approved(service, 'reviewed_trial')
    calls = []
    class Publisher:
        def publish(self, *args, **kwargs):
            calls.append(kwargs['reconcile'])
            if len(calls) == 1:
                return {'success': False, 'status': 'PUSH_UNCERTAIN', 'sync_status': 'NOT_STARTED'}
            if len(calls) == 2:
                assert kwargs['reconcile'] is True
                return {'success': True, 'status': 'PUBLISHED', 'sync_status': 'SYNC_FAILED', 'commit_sha': 'fixture'}
            return {'success': True, 'status': 'PUBLISHED', 'sync_status': 'SYNCED', 'commit_sha': 'fixture'}
    service.publisher = Publisher()
    args = (run.id, artifact.id, artifact.content_sha256)
    first = asyncio.run(service.publish(*args, reviewer='cli:alice'))
    assert first['status'] == 'PUSH_UNCERTAIN'
    second = asyncio.run(service.publish(*args, reviewer='cli:alice', reconcile=True))
    assert second['sync_status'] == 'SYNC_FAILED'
    third = asyncio.run(service.publish(*args, reviewer='cli:alice'))
    fourth = asyncio.run(service.publish(*args, reviewer='cli:alice'))
    assert fourth == third and calls == [False, True, False]
    categories = {e['action']: e['category'] for e in service.status(run.id).events}
    assert categories['approve'] == 'routine' and categories['reconcile'] == 'recovery'

def test_resume_never_publishes_approval_without_prior_attempt(workflow):
    service, _ = workflow
    run, artifact = approved(service, 'reviewed_trial')
    class Publisher:
        def publish(self, *args, **kwargs):
            raise AssertionError('approval alone never starts publication')
    service.publisher = Publisher()
    resumed = asyncio.run(service.resume(run.id))
    assert resumed.status == 'APPROVED' and resumed.publication_target is None


def test_resume_recovers_reserved_uncertain_push_then_sync_only(workflow):
    service, _ = workflow
    run, artifact = approved(service, 'reviewed_trial')
    calls = []
    class Publisher:
        def publish(self, *args, **kwargs):
            calls.append(kwargs['reconcile'])
            if len(calls) == 1:
                return {'success': False, 'status': 'PUSH_UNCERTAIN', 'sync_status': 'NOT_STARTED'}
            if len(calls) == 2:
                return {'success': True, 'status': 'PUBLISHED', 'sync_status': 'SYNC_FAILED', 'commit_sha': 'fixture'}
            return {'success': True, 'status': 'PUBLISHED', 'sync_status': 'SYNCED', 'commit_sha': 'fixture'}
    service.publisher = Publisher()
    asyncio.run(service.publish(run.id, artifact.id, artifact.content_sha256, reviewer='cli:alice'))
    first = asyncio.run(service.resume(run.id))
    assert first.publication['sync_status'] == 'SYNC_FAILED'
    second = asyncio.run(service.resume(run.id))
    assert second.publication['sync_status'] == 'SYNCED'
    assert calls == [False, True, False]

def test_approved_legacy_bundle_cannot_bypass_policy_without_workflow_record(workflow):
    from src.publisher.git_publisher import GitPublisher
    service, _ = workflow
    run = ready(service)
    pipeline = service.pipeline(run)
    artifact = pipeline.get_draft(run.draft_id)
    pipeline.approve(artifact.id, artifact.content_sha256)
    class NoGit(GitPublisher):
        def _repository(self):
            raise AssertionError('unscoped publication reached Git')
    result = NoGit().publish(pipeline.store, artifact.id, artifact.content_sha256)
    assert not result['success'] and 'scoped workflow authorization required' in result['error']
