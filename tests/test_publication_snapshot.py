"""Publication consumes the actual approved bound input without Git or network."""
import asyncio
import json

import pytest

from drafting_fixtures import drafting_input
from src.editorial.store import digest, write_json
from src.publisher.git_publisher import GitPublisher
from test_context_delivery import context_fixture
from test_pipeline import setup_pipeline


def approved_input(tmp_path, drafting_input, *, approve=True):
    context, original, raw = context_fixture(tmp_path)
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, user_context=context)
    artifact = asyncio.run(pipeline.generate(topic_id))
    assert artifact.status == "REVIEW_READY"
    if approve:
        artifact = pipeline.approve(artifact.id, artifact.content_sha256)
    return pipeline, artifact, context, original, raw


def test_actual_generated_approved_bound_input_can_be_snapshotted(tmp_path, drafting_input):
    pipeline, artifact, context, original, raw = approved_input(tmp_path, drafting_input)
    original.unlink()
    directory = artifact.content_path.parent
    assert (directory / "input-runs" / "R1.bin").read_bytes() == raw
    assert json.loads((directory / "input.json").read_text(encoding="utf-8"))["user_context"]["goals"] == list(context.goals)
    files, published, info = GitPublisher(tmp_path / "blog")._snapshot(pipeline.store, artifact)
    assert published.status == "PUBLISHED"
    assert list(files.values()) == [artifact.content_path.read_bytes()]
    assert info["topic"]["title"] == "PageIndex"
    assert not (tmp_path / "blog").exists()


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_later_registration_metadata_cannot_replace_reviewed_input(tmp_path, drafting_input, damage):
    pipeline, artifact, *_ = approved_input(tmp_path, drafting_input)
    registered = pipeline.store.root / "topics" / (artifact.topic_id + ".json")
    if damage == "changed":
        registered.write_text('{"topic": "later mutable metadata"}', encoding="utf-8")
    else:
        registered.unlink()
    _, _, info = GitPublisher(tmp_path / "blog")._snapshot(pipeline.store, artifact)
    assert info["topic"]["title"] == "PageIndex"


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_reviewed_input_damage_blocks_publication(tmp_path, drafting_input, damage):
    pipeline, artifact, *_ = approved_input(tmp_path, drafting_input)
    reviewed = artifact.content_path.parent / "input.json"
    if damage == "changed":
        data = json.loads(reviewed.read_text(encoding="utf-8"))
        data["user_context"]["goals"] = ["Changed after approval"]
        write_json(reviewed, data)
    else:
        reviewed.unlink()
    with pytest.raises(ValueError, match="changed|missing"):
        GitPublisher(tmp_path / "blog")._snapshot(pipeline.store, artifact)
    assert pipeline.get_draft(artifact.id).status == "NEEDS_REVISION"
    assert not (tmp_path / "blog").exists()


@pytest.mark.parametrize("legacy", ["topic_only", "absent", "null_context", "mismatched_context"])
def test_legacy_review_bundle_requires_fresh_bound_input(tmp_path, drafting_input, legacy):
    pipeline, artifact, *_ = approved_input(tmp_path, drafting_input, approve=False)
    directory = artifact.content_path.parent
    reviewed = directory / "input.json"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Reproduce an older reviewed bundle with no bound UserContext contract.
    # Its manifest is internally consistent; absence cannot be caught by hashes.
    if legacy == "topic_only":
        write_json(reviewed, json.loads(reviewed.read_text(encoding="utf-8"))["topic"])
        manifest["input.json"] = digest(reviewed)
    elif legacy == "absent":
        reviewed.unlink()
        manifest.pop("input.json")
    else:
        data = json.loads(reviewed.read_text(encoding="utf-8"))
        if legacy == "null_context":
            data["user_context"] = None
        else:
            data["user_context"]["goals"] = ["Different captured input"]
        write_json(reviewed, data)
        manifest["input.json"] = digest(reviewed)
    write_json(manifest_path, manifest)
    artifact = artifact.model_copy(update={"review_sha256": digest(manifest_path)})
    pipeline.store.save(artifact)
    artifact = pipeline.approve(artifact.id, artifact.content_sha256)
    with pytest.raises(ValueError, match="bound input"):
        GitPublisher(tmp_path / "blog")._snapshot(pipeline.store, artifact)


def test_input_read_remains_bound_after_initial_manifest_verification(tmp_path, drafting_input, monkeypatch):
    pipeline, artifact, *_ = approved_input(tmp_path, drafting_input)
    verify = pipeline.store.verify
    def mutate_after_verify(approved):
        verify(approved)
        (approved.content_path.parent / "input.json").write_text('{"topic": "changed"}', encoding="utf-8")
    monkeypatch.setattr(pipeline.store, "verify", mutate_after_verify)
    with pytest.raises(ValueError, match="changed or absent from manifest: input.json"):
        GitPublisher(tmp_path / "blog")._snapshot(pipeline.store, artifact)
