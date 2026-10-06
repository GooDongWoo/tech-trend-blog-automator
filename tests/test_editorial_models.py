"""Evidence contracts reject unsupported claims and approval shortcuts offline."""
import hashlib
import importlib
import json

import pytest
from pydantic import ValidationError


def models():
    try:
        return importlib.import_module("src.editorial.models")
    except ModuleNotFoundError:
        pytest.fail("Evidence contracts are missing; unsupported measurements cannot be rejected")


def source(**updates):
    data = dict(url="https://example.invalid/paper", fetched_at="2026-10-07T00:00:00Z",
                title="Queue", kind="html", sha256="a" * 64,
                text="Measured throughput", locations=["results"], error=None)
    data.update(updates)
    return models().SourceRecord(**data)


def ref(**updates):
    data = dict(url="https://example.invalid/paper", sha256="a" * 64, location="results")
    data.update(updates)
    return data


def metric(**updates):
    data = dict(value=73, unit="%", target="throughput", baseline="previous queue",
                conditions="same worker and workload")
    data.update(updates)
    return data


def draft(tmp_path, status="REVIEW_READY"):
    path = tmp_path / "draft.md"
    path.write_text("Reviewed draft", encoding="utf-8")
    return models().DraftArtifact(id="draft-1", topic_id="topic-1", content_path=path,
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        evidence_path=tmp_path / "evidence.json", report_path=tmp_path / "report.md", status=status)


def test_measurement_without_provenance_is_rejected():
    with pytest.raises(ValidationError, match="provenance"):
        models().EvidenceClaim(text="73% faster", kind="measurement", metric_context=metric())


@pytest.mark.parametrize("field", ["baseline", "unit", "target", "conditions"])
def test_measurement_requires_comparison_context(field):
    with pytest.raises(ValidationError):
        models().EvidenceClaim(text="73% faster", kind="measurement", source_refs=[ref()],
                               metric_context=metric(**{field: ""}))


def test_measurement_requires_metric_context():
    with pytest.raises(ValidationError, match="metric_context"):
        models().EvidenceClaim(text="73% faster", kind="measurement", source_refs=[ref()])


def test_source_claim_requires_traceable_reference():
    with pytest.raises(ValidationError, match="source_refs"):
        models().EvidenceClaim(text="73% faster", kind="source_claim")


def test_unknown_claim_kind_is_rejected():
    with pytest.raises(ValidationError):
        models().EvidenceClaim(text="Claim", kind="personal_experience", source_refs=[ref()])


@pytest.mark.parametrize("updates", [dict(url="not-url"), dict(sha256="bad"), dict(location="")])
def test_invalid_source_reference_is_rejected(updates):
    with pytest.raises(ValidationError):
        models().SourceRef(**ref(**updates))


@pytest.mark.parametrize("updates", [dict(url="https://other.invalid"), dict(sha256="b" * 64), dict(location="missing")])
def test_packet_rejects_references_outside_snapshot(updates):
    claim = models().EvidenceClaim(text="Queue result", kind="source_claim", source_refs=[ref(**updates)])
    with pytest.raises(ValidationError, match="snapshot"):
        models().ResearchPacket(topic_id="topic-1", question="How?", sources=[source()], claims=[claim])


def test_packet_requires_an_inspectable_source():
    with pytest.raises(ValidationError, match="inspectable"):
        models().ResearchPacket(topic_id="topic-1", question="How?", sources=[source(text="", error="fetch failed")])


def test_packet_rejects_duplicate_canonical_sources():
    with pytest.raises(ValidationError, match="duplicate"):
        models().ResearchPacket(topic_id="topic-1", question="How?", sources=[source(), source(url="https://EXAMPLE.invalid:443/paper#results")])


def test_measurement_accepts_inspectable_run_record():
    run = source(url="file:///tmp/run.log", kind="run_log", locations=["line 1"])
    claim = models().EvidenceClaim(text="73% faster", kind="measurement", metric_context=metric(run_record=run))
    assert claim.metric_context.run_record.text == "Measured throughput"


def test_measurement_rejects_non_run_log_as_experience():
    with pytest.raises(ValidationError, match="run_log"):
        models().EvidenceClaim(text="73% faster", kind="measurement", metric_context=metric(run_record=source()))


def test_contracts_round_trip_json(tmp_path):
    m = models()
    claim = m.EvidenceClaim(text="73% faster", kind="measurement", source_refs=[ref()], metric_context=metric())
    records = [source(), claim, m.ResearchPacket(topic_id="topic-1", question="How?", sources=[source()], claims=[claim], gaps=["No ablation"]),
        m.UserContext(goals=["reliability"], constraints=["offline"], interests=["queues"], experience_refs=[]),
        m.EditorialBrief(topic_id="topic-1", post_kind="tool", thesis="Use under constraints", comparison=["old queue"], decision_criteria=["latency"], reversal_conditions=["lost jobs"]),
        draft(tmp_path)]
    for record in records:
        assert type(record).model_validate_json(record.model_dump_json()) == record


def test_stable_topic_id_normalizes_url_and_includes_snapshot():
    m = models()
    first = m.stable_topic_id("HTTPS://EXAMPLE.invalid:443/paper#results", "a" * 64)
    assert first == m.stable_topic_id("https://example.invalid/paper", "a" * 64)
    assert first != m.stable_topic_id("https://example.invalid/paper", "b" * 64)
    assert first != m.stable_topic_id("https://example.invalid/other", "a" * 64)
    assert m.canonical_topic_url("https://EXAMPLE.invalid:443/paper?q=1#part") == "https://example.invalid/paper?q=1"


def test_needs_research_cannot_skip_review(tmp_path):
    artifact = draft(tmp_path, "NEEDS_RESEARCH")
    with pytest.raises(ValueError, match="transition"):
        artifact.transition("APPROVED", content_sha256=artifact.content_sha256)


def test_approval_requires_explicit_hash_check(tmp_path):
    with pytest.raises(ValueError, match="hash"):
        draft(tmp_path).transition("APPROVED")


def test_approval_rejects_changed_file_even_with_old_hash(tmp_path):
    artifact = draft(tmp_path)
    artifact.content_path.write_text("Unreviewed change", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        artifact.transition("APPROVED", content_sha256=artifact.content_sha256)


def test_approval_rejects_wrong_hash(tmp_path):
    with pytest.raises(ValueError, match="hash"):
        draft(tmp_path).transition("APPROVED", content_sha256="b" * 64)


def test_reviewed_draft_can_be_approved_and_published(tmp_path):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    assert approved.status == "APPROVED"
    assert artifact.status == "REVIEW_READY"
    assert approved.transition("PUBLISHED", content_sha256=artifact.content_sha256).status == "PUBLISHED"
    assert type(approved).model_validate_json(approved.model_dump_json()) == approved


def test_approved_draft_changes_block_publication(tmp_path):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    approved.content_path.write_text("Unreviewed change", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        approved.transition("PUBLISHED", content_sha256=artifact.content_sha256)


def test_status_cannot_be_mutated_to_bypass_transition(tmp_path):
    artifact = draft(tmp_path, "NEEDS_RESEARCH")
    with pytest.raises(ValidationError):
        artifact.status = "APPROVED"


def test_loaded_approved_draft_requires_bound_hash(tmp_path):
    data = draft(tmp_path).model_dump()
    data["status"] = "APPROVED"
    with pytest.raises(ValidationError, match="approved_sha256"):
        models().DraftArtifact.model_validate(data)


def test_source_reference_rejects_invalid_hostname():
    with pytest.raises(ValidationError):
        models().SourceRef(**ref(url="https://invalid host/paper"))


def test_copy_cannot_skip_approval_validation(tmp_path):
    with pytest.raises(ValidationError, match="approved_sha256"):
        draft(tmp_path).model_copy(update={"status": "APPROVED"})


def test_copy_cannot_approve_even_with_matching_hash(tmp_path):
    artifact = draft(tmp_path)
    with pytest.raises(ValueError, match="transition"):
        artifact.model_copy(update={"status": "APPROVED", "approved_sha256": artifact.content_sha256})


def test_failed_source_is_serializable_but_not_citable():
    failed = source(text="", locations=[], sha256=None, error="timeout")
    assert failed.error == "timeout"
    claim = models().EvidenceClaim(text="Result", kind="source_claim", source_refs=[ref()])
    with pytest.raises(ValidationError, match="snapshot"):
        models().ResearchPacket(topic_id="topic-1", question="How?", sources=[failed, source(url="https://other.invalid")], claims=[claim])


def test_user_context_rejects_vault_note_as_experience():
    with pytest.raises(ValidationError, match="run_log"):
        models().UserContext(experience_refs=[source(kind="markdown")])


def test_measurement_rejects_nonfinite_values():
    with pytest.raises(ValidationError):
        models().EvidenceClaim(text="Result", kind="measurement", source_refs=[ref()], metric_context=metric(value=float("nan")))


def test_revision_clears_prior_approval(tmp_path):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    revised = approved.transition("NEEDS_REVISION")
    assert revised.approved_sha256 is None
    with pytest.raises(ValueError, match="transition"):
        revised.transition("PUBLISHED", content_sha256=artifact.content_sha256)


def test_missing_draft_file_blocks_approval(tmp_path):
    artifact = draft(tmp_path)
    artifact.content_path.unlink()
    with pytest.raises(FileNotFoundError):
        artifact.transition("APPROVED", content_sha256=artifact.content_sha256)


def test_same_status_copy_cannot_replace_approved_content(tmp_path):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    unreviewed = tmp_path / "unreviewed.md"
    unreviewed.write_text("Unreviewed article", encoding="utf-8")
    new_hash = hashlib.sha256(unreviewed.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="approval-bound"):
        approved.model_copy(update={"content_path": unreviewed,
                                   "content_sha256": new_hash,
                                   "approved_sha256": new_hash})


@pytest.mark.parametrize("field,value", [
    ("id", "different-draft"), ("topic_id", "different-topic"),
    ("evidence_path", "different-evidence.json"),
    ("report_path", "different-review.md"), ("media_paths", ["unreviewed.gif"]),
])
def test_approved_copy_cannot_change_review_identity(tmp_path, field, value):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    with pytest.raises(ValueError, match="approval-bound"):
        approved.model_copy(update={field: value})


def test_content_edit_requires_revision_and_new_approval(tmp_path):
    artifact = draft(tmp_path)
    approved = artifact.transition("APPROVED", content_sha256=artifact.content_sha256)
    unreviewed = tmp_path / "revised.md"
    unreviewed.write_text("Reviewed revision", encoding="utf-8")
    new_hash = hashlib.sha256(unreviewed.read_bytes()).hexdigest()
    revised = approved.transition("NEEDS_REVISION").model_copy(
        update={"content_path": unreviewed, "content_sha256": new_hash})
    ready = revised.transition("REVIEW_READY")
    renewed = ready.transition("APPROVED", content_sha256=new_hash)
    assert renewed.transition("PUBLISHED", content_sha256=new_hash).content_sha256 == new_hash


@pytest.mark.parametrize("collection", ["sources", "claims", "gaps", "source_refs", "locations"])
def test_validated_packet_collections_cannot_be_cleared(collection):
    claim = models().EvidenceClaim(text="Queue result", kind="source_claim", source_refs=[ref()])
    packet = models().ResearchPacket(topic_id="topic-1", question="How?",
        sources=[source()], claims=[claim], gaps=["No ablation"])
    collections = dict(sources=packet.sources, claims=packet.claims, gaps=packet.gaps,
                       source_refs=packet.claims[0].source_refs,
                       locations=packet.sources[0].locations)
    with pytest.raises(AttributeError):
        collections[collection].clear()
    serialized = packet.model_dump_json()
    assert json.loads(serialized)["claims"][0]["source_refs"] == [ref()]
    assert type(packet).model_validate_json(serialized) == packet
