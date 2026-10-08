"""Editorial decisions must resolve to source prose, not invented experiments."""
import hashlib
import importlib
from pathlib import Path

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


@pytest.mark.parametrize("missing", [None, "mechanism", "constraint", "reversal"])
def test_paper_methodology_and_explicit_sampling_boundary(missing):
    sections = [
        ("Methodology", "Only the conditioning skill changes."),
        ("Comparison", "Rollout evaluation is the alternative baseline."),
        ("Reference sampling", "We retain only trajectories with both reference sets nonempty."),
        ("Analysis", "This proxy does not guarantee improvement for every proposal."),
    ]
    if missing is not None:
        index = {"mechanism": 0, "constraint": 2, "reversal": 3}[missing]
        sections[index] = (sections[index][0], "This information is not reported.")
    text = "\f".join(f"# {heading}\n{body}" for heading, body in sections)
    source = packet("paper").sources[0].model_copy(update={
        "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "locations": tuple(f"section:{i}" for i in range(len(sections))),
    })
    claims = tuple(EvidenceClaim(text=body, kind="source_claim", source_refs=(
        SourceRef(url=source.url, sha256=source.sha256, location=source.locations[i]),))
        for i, (_, body) in enumerate(sections))
    research = packet("paper").model_copy(update={"sources": (source,), "claims": claims})
    result = builder()(research, UserContext())
    if missing is None:
        assert isinstance(result, EditorialBrief)
        assert result.key_mechanism == sections[0][1]
        assert sections[2][1] in result.adoption_constraints
        assert sections[3][1] in result.reversal_conditions
    else:
        assert isinstance(result, ResearchBlocked)
        code = {"mechanism": "missing_key_mechanism", "constraint": "missing_adoption_constraints",
                "reversal": "missing_reversal_condition"}[missing]
        assert code in result.reasons


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
        baseline="in-memory queue", conditions="one worker with the same workload")})
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


def test_explicit_missing_facts_cannot_supply_complete_brief():
    from src.research.extract import markdown_sections, snapshot_text
    raw = (Path(__file__).parent / "fixtures" / "brief-missing-facts.md").read_bytes()
    title, sections = markdown_sections(raw.decode("utf-8"))
    source = SourceRecord(url="https://example.invalid/absent", title=title, kind="markdown",
        sha256=hashlib.sha256(raw).hexdigest(), text=snapshot_text(sections, "markdown"),
        locations=tuple(section.location for section in sections), fetched_at="2026-10-07T00:00:00Z", role="primary")
    claims = tuple(EvidenceClaim(text=section.text, kind="source_claim", source_refs=(SourceRef(
        url=source.url, sha256=source.sha256, location=section.location),)) for section in sections if section.text)
    research = ResearchPacket(topic_id="absent-facts", question="When should Queue be adopted?", sources=(source,), claims=claims)
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert set(result.reasons) == {"missing_key_mechanism", "missing_comparison_alternative",
                                   "missing_adoption_constraints", "missing_reversal_condition"}
    assert result.packet == research


@pytest.mark.parametrize("field, invented, reason", [
    ("unit", "milliseconds", "metric_unit_mismatch:section:5"),
    ("target", "memory use", "unsupported_metric_target:section:5"),
    ("baseline", "the production deployment", "unsupported_metric_baseline:section:5"),
    ("baseline", "durable storage", "unsupported_metric_baseline:section:5"),
    ("conditions", "sixteen workers on a GPU", "unsupported_metric_conditions:section:5"),
    ("conditions", "Persist the request before acknowledgement", "unsupported_metric_conditions:section:5"),
    ("conditions", "one worker with the same QueueBench workload", "unsupported_metric_conditions:section:5"),
])
def test_every_metric_context_field_needs_source_support(field, invented, reason):
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    metadata = dict(value=25, unit="%", target="throughput", baseline="in-memory queue",
                    conditions="one worker with the same workload")
    metadata[field] = invented
    claim = research.claims[-1].model_copy(update={"metric_context": MetricContext(**metadata)})
    research = research.model_copy(update={"claims": (*research.claims[:-1], claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert reason in result.reasons


def test_same_value_cannot_hide_wrong_unit_target_baseline_and_conditions():
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    claim = research.claims[-1].model_copy(update={"metric_context": MetricContext(value=25,
        unit="milliseconds", target="memory use", baseline="invented deployment", conditions="sixteen GPU workers")})
    research = research.model_copy(update={"claims": (*research.claims[:-1], claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert set(result.reasons) >= {"metric_unit_mismatch:section:5", "unsupported_metric_target:section:5",
                                  "unsupported_metric_baseline:section:5", "unsupported_metric_conditions:section:5"}


@pytest.mark.parametrize("claimed_conditions", ["one worker with the same workload", "Throughput improves by 25% and latency improves by 90%."])
def test_compound_results_block_until_each_has_its_own_context(claimed_conditions):
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    compound = "Throughput improves by 25% and latency improves by 90%."
    source = research.sources[0].model_copy(update={"text": research.sources[0].text.replace("Throughput improves by 25%.", compound)})
    claim = research.claims[-1].model_copy(update={"text": compound, "metric_context": MetricContext(value=25,
        unit="%", target="throughput", baseline="in-memory queue", conditions=claimed_conditions)})
    research = research.model_copy(update={"sources": (source,), "claims": (*research.claims[:-1], claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, ResearchBlocked)
    assert "uncovered_numeric_results:section:5" in result.reasons


def test_reported_unit_without_space_keeps_its_correct_context():
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    source = research.sources[0].model_copy(update={"text": research.sources[0].text
        .replace("Throughput improves by 25%.", "Latency is 25ms.")
        .replace("Throughput measured on one worker", "Latency measured on one worker")})
    metrics = research.claims[-2].model_copy(update={"text": "Latency measured on one worker with the same workload."})
    result_claim = research.claims[-1].model_copy(update={"text": "Latency is 25ms.", "metric_context": MetricContext(value=25,
        unit="milliseconds", target="latency", baseline="in-memory queue", conditions="one worker with the same workload")})
    research = research.model_copy(update={"sources": (source,), "claims": (*research.claims[:-2], metrics, result_claim)})
    result = builder()(research, UserContext())
    assert isinstance(result, EditorialBrief)
    assert result.evidence[-1].metric_context.unit == "milliseconds"


@pytest.mark.parametrize("setup_cue", ["ExperimentalSetup", "Setting"])
def test_reported_metric_accepts_pdf_setup_and_table_setting_cues(setup_cue):
    from src.editorial.models import MetricContext
    research = packet("paper", metric=True)
    original = "Throughput measured on one worker with the same workload."
    setup = f"{setup_cue}: QwenBackbone."
    source = research.sources[0].model_copy(update={"text": research.sources[0].text
        .replace("Metrics and experimental conditions", "Configuration")
        .replace(original, setup)})
    setup_claim = research.claims[-2].model_copy(update={"text": setup})
    result_claim = research.claims[-1].model_copy(update={"metric_context": MetricContext(
        value=25, unit="%", target="throughput", baseline="in-memory queue",
        conditions="QwenBackbone")})
    research = research.model_copy(update={"sources": (source,),
        "claims": (*research.claims[:-2], setup_claim, result_claim)})
    assert isinstance(builder()(research, UserContext()), EditorialBrief)
