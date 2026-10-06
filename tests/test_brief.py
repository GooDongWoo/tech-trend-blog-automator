"""Editorial decisions must resolve to source prose, not invented experiments."""
import hashlib
import importlib

import pytest

from src.editorial.models import EvidenceClaim, EditorialBrief, ResearchBlocked, ResearchPacket, SourceRecord, SourceRef, UserContext


def builder():
    try:
        return importlib.import_module("src.editorial.brief").build_brief
    except ModuleNotFoundError:
        pytest.fail("Unit 3 build_brief is missing")


def packet(kind="protocol", *, role="primary", metric=False):
    titles = {
        "protocol": ("Wire protocol standard", "Message flow", "Compatibility"),
        "tool": ("Queue library", "API", "Deployment constraints"),
        "paper": ("Research paper benchmark", "Method", "Limitations"),
        "design_comparison": ("Architecture design comparison", "Mechanism", "Operational constraints"),
    }
    title, mechanism, constraints = titles[kind]
    sections = [(mechanism, "Persist the request before acknowledgement."),
                ("Baseline alternatives", "Compare with the in-memory queue."),
                (constraints, "Requires durable storage; unsuitable when writes are unavailable.")]
    if kind == "paper":
        sections += [("Dataset", "Study uses the QueueBench dataset."),
                     ("Metrics and experimental conditions", "Throughput measured on one worker with the same workload."),
                     ("Results", "Throughput improves by 25%." if metric else "Pending jobs survive restart in this study.")]
    text = "\f".join(f"# {heading}\n{body}" for heading, body in sections)
    source = SourceRecord(url="https://example.invalid/source", title=title, kind="markdown",
                          sha256=hashlib.sha256(text.encode()).hexdigest(), text=text,
                          locations=tuple(f"section:{i}" for i in range(len(sections))),
                          fetched_at="2026-10-07T00:00:00Z", role=role)
    claims = tuple(EvidenceClaim(text=body, kind="source_claim", source_refs=(
        SourceRef(url=source.url, sha256=source.sha256, location=source.locations[i]),))
        for i, (_, body) in enumerate(sections))
    return ResearchPacket(topic_id="topic-3", question="When should a durable queue be adopted?", sources=(source,), claims=claims)


@pytest.mark.parametrize("kind", ["paper", "tool", "protocol", "design_comparison"])
def test_source_material_selects_kind_and_supports_editorial_decision(kind):
    research = packet(kind)
    # A misleading topic question must not override the actual source kind.
    research = research.model_copy(update={"question": "Should this library implement the wire protocol?"})
    brief = builder()(research, UserContext())
    assert isinstance(brief, EditorialBrief)
    assert brief.post_kind == kind
    assert brief.question == research.question
    assert "Persist the request" in brief.key_mechanism
    assert "in-memory queue" in brief.comparison[0]
    assert "durable storage" in brief.adoption_constraints[0]
    assert "writes are unavailable" in brief.reversal_conditions[0]
    assert brief.evidence
    assert all(claim in research.claims for claim in brief.evidence)
    assert brief.policy_version == "1.0"


def test_protocol_does_not_invent_study_or_ablation():
    brief = builder()(packet(), UserContext())
    assert isinstance(brief, EditorialBrief)
    assert brief.study is None


def test_paper_captures_only_reported_fields():
    brief = builder()(packet("paper"), UserContext())
    assert isinstance(brief, EditorialBrief)
    assert brief.study.dataset[0].text == "Study uses the QueueBench dataset."
    assert brief.study.baseline[0].source_refs
    assert brief.study.metrics[0].text.startswith("Throughput measured")
    assert brief.study.ablation == ()
    assert "ablation_not_reported" in brief.warnings


def test_benchmark_bare_percentage_blocks_with_missing_context():
    result = builder()(packet("paper", metric=True), UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "missing_metric_context:section:5" in result.reasons


def test_empty_claims_and_missing_mechanism_are_missing_facts():
    research = packet().model_copy(update={"claims": ()})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "missing_key_mechanism" in result.reasons
    assert result.packet == research


def test_proposed_thesis_not_present_in_source_is_rejected():
    research = packet()
    invented = research.claims[0].model_copy(update={"text": "I deployed it and eliminated all outages."})
    research = research.model_copy(update={"claims": (invented, *research.claims[1:])})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "unsupported_claim:section:0" in result.reasons


def test_unknown_authority_remains_visible():
    brief = builder()(packet(role="unknown"), UserContext())
    assert isinstance(brief, EditorialBrief)
    assert brief.source_authority == ("https://example.invalid/source: unknown",)
    assert "primary_source_authority_unconfirmed" in brief.warnings
    assert "source" in brief.thesis.lower()


def test_disputed_mechanism_blocks_instead_of_thesis():
    research = packet()
    research = research.model_copy(update={"claims": (research.claims[0].model_copy(update={"status": "disputed"}), *research.claims[1:])})
    assert isinstance(builder()(research, UserContext()), ResearchBlocked)


def test_question_requires_one_sentence():
    research = packet().model_copy(update={"question": "How does it work? Should we use it?"})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "question_requires_one_sentence" in result.reasons


def test_tool_benchmark_section_does_not_change_source_kind():
    research = packet("tool")
    source = research.sources[0]
    source = source.model_copy(update={"text": source.text + "\f# Benchmark\nNo timing experiment was run.",
                                     "locations": (*source.locations, "section:extra")})
    research = research.model_copy(update={"sources": (source,)})
    result = builder()(research, UserContext())
    assert isinstance(result, EditorialBrief)
    assert result.post_kind == "tool"


def test_complete_reported_metric_context_is_preserved():
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    claim = research.claims[-1].model_copy(update={"metric_context": MetricContext(value=25, unit="%", target="throughput",
        baseline="in-memory queue", conditions="one worker with the same QueueBench workload")})
    research = research.model_copy(update={"claims": (*research.claims[:-1], claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, EditorialBrief)
    assert result.evidence[-1].kind == "source_claim"
    assert result.evidence[-1].metric_context.value == 25


def test_metric_context_value_must_match_reported_number():
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    claim = research.claims[-1].model_copy(update={"metric_context": MetricContext(value=99, unit="%", target="throughput",
        baseline="in-memory queue", conditions="one worker")})
    research = research.model_copy(update={"claims": (*research.claims[:-1], claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "metric_context_mismatch:section:5" in result.reasons


def test_policy_missing_blocks_before_editorial_result(tmp_path, monkeypatch):
    module = importlib.import_module("src.editorial.brief")
    monkeypatch.setattr(module, "POLICY_PATH", tmp_path / "missing.md")
    result = builder()(packet(), UserContext())
    assert isinstance(result, ResearchBlocked)
    assert result.reasons == ("editorial_policy_unavailable",)
