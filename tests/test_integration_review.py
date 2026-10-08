"""Offline regressions for the actual research → brief → review boundaries."""
import asyncio
import hashlib
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from drafting_fixtures import drafting_input, response
from src.curator.matcher import CuratedTopic
from src.editorial.brief import build_brief
from src.editorial.models import EditorialBrief, MetricContext, ResearchBlocked, SourceRecord, UserContext
from src.research.packet import build_packet
from src.writer.blog_writer import BlogWriter
from test_drafting import FakeLLM


def research_input(*, result="Throughput improves by 25%.", conditions="one worker with the same workload"):
    sections = [
        ("Method", "Persist the request before acknowledgement."),
        ("Baseline alternatives", "Compare with the in-memory queue."),
        ("Limitations", "Requires durable storage; unsuitable when writes are unavailable."),
        ("Dataset", "Study uses the QueueBench dataset."),
        ("Metrics and experimental conditions", f"Throughput measured on {conditions}."),
        ("Results", result),
    ]
    text = "\f".join(f"# {heading}\n{body}" for heading, body in sections)
    source = SourceRecord(url="https://example.invalid/paper", title="Research paper benchmark", kind="markdown",
        sha256=hashlib.sha256(text.encode()).hexdigest(), text=text, locations=tuple(f"section:{i}" for i in range(len(sections))),
        fetched_at="2026-10-08T00:00:00Z", role="primary")
    topic = CuratedTopic(rank=1, title=source.title, url=source.url, source="fixture", one_line_summary="queue",
        relevance_reason="reliability", suggested_angle="When should the persistent queue be adopted?")
    return topic, source


@pytest.mark.parametrize("experiment", [None, "Experiment B"])
def test_runtime_packet_enriches_reported_metric_before_writer_brief(tmp_path, monkeypatch, experiment):
    topic, source = research_input()
    if experiment:
        text = re.sub(r"(?m)^# ", f"# {experiment} > ", source.text)
        source = source.model_copy(update={"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    monkeypatch.setattr("src.writer.deep_researcher.fetch_source", lambda *args, **kwargs: source)
    payload = json.loads(json.dumps(response()).replace("https://example.invalid/pageindex", source.url))
    metric = f'원문은 “Throughput improves by 25%.”라고 설명한다. [E6]({source.url})'
    baseline = payload["sections"][1]["text"]
    conditions = f'원문은 “Throughput measured on one worker with the same workload.”라고 설명한다. [E5]({source.url})'
    payload["sections"].append({"id": "results", "text": "\n\n".join((metric, baseline, conditions)), "claims": [
        {"sentence": metric, "kind": "source_claim", "evidence_ids": ["E6"]},
        {"sentence": baseline, "kind": "source_claim", "evidence_ids": ["E2"]},
        {"sentence": conditions, "kind": "source_claim", "evidence_ids": ["E5"]},
    ]})
    llm = FakeLLM(payload)
    writer = BlogWriter(artifact_dir=tmp_path / "drafts", llm=llm)
    writer.researcher.artifact_dir = tmp_path / "research"
    result = asyncio.run(writer.generate_post(topic, persist_artifacts=False))
    assert isinstance(result["brief"], EditorialBrief)
    claim = result["packet"].claims[-1]
    assert claim.kind == "source_claim" and claim.status == "unverified"
    assert claim.metric_context == MetricContext(value=25, unit="%", target="Throughput",
        baseline="the in-memory queue", conditions="one worker with the same workload")
    assert claim.source_refs[0].location == "section:5"
    assert claim.source_refs[0].sha256 == source.sha256
    assert result["status"] == "REVIEW_READY"
    assert result["validation"].static_passed and result["validation"].grounding_passed
    assert result["draft"].sections[-1].claims[0].evidence_ids == ("E6",)
    assert len(llm.prompts) == 1


@pytest.mark.parametrize("result,conditions", [
    ("Throughput improves by 25%.", "unknown"),
    ("Throughput improves by 25% and latency improves by 90%.", "one worker with the same workload"),
])
def test_runtime_incomplete_or_compound_metric_remains_blocked(tmp_path, result, conditions):
    topic, source = research_input(result=result, conditions=conditions)
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    brief = build_brief(packet, UserContext())
    assert isinstance(brief, ResearchBlocked)
    assert any("metric_context" in reason or "uncovered_numeric_results" in reason for reason in brief.reasons)


def test_enriched_runtime_metric_still_rejects_mismatched_metadata(tmp_path):
    topic, source = research_input()
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    claim = packet.claims[-1].model_copy(update={"metric_context": MetricContext(value=99, unit="%", target="Throughput",
        baseline="the in-memory queue", conditions="one worker with the same workload")})
    packet = packet.model_copy(update={"claims": (*packet.claims[:-1], claim)})
    brief = build_brief(packet, UserContext())
    assert isinstance(brief, ResearchBlocked)
    assert "metric_context_mismatch:section:5" in brief.reasons


def separate_experiments(*, explicit_absence=False, same_scope=False):
    topic, source = research_input()
    blocks = source.text.split("\f")
    for index in (1, 4):
        blocks[index] = blocks[index].replace("# ", "# Experiment A > ", 1)
    blocks[5] = blocks[5].replace("# ", "# Experiment A > " if same_scope else "# Experiment B > ", 1)
    locations = source.locations
    if explicit_absence:
        scope = "Experiment A" if same_scope else "Experiment B"
        blocks.append(f"# {scope} > Experimental conditions\nConditions are not reported.")
        locations = (*locations, "section:6")
    text = "\f".join(blocks)
    source = source.model_copy(update={"text": text, "locations": locations, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return topic, source


@pytest.mark.parametrize("explicit_absence", [False, True])
def test_runtime_result_cannot_borrow_another_experiments_setup(tmp_path, explicit_absence):
    topic, source = separate_experiments(explicit_absence=explicit_absence)
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    result = next(claim for claim in packet.claims if claim.text == "Throughput improves by 25%.")
    assert result.metric_context is None
    brief = build_brief(packet, UserContext())
    assert isinstance(brief, ResearchBlocked)
    assert "missing_metric_context:section:5" in brief.reasons


def test_manually_attached_context_cannot_cross_experiment_boundary(tmp_path):
    topic, source = separate_experiments(explicit_absence=True)
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    metric = MetricContext(value=25, unit="%", target="Throughput", baseline="the in-memory queue",
        conditions="one worker with the same workload")
    claims = tuple(claim.model_copy(update={"metric_context": metric}) if claim.text == "Throughput improves by 25%." else claim
                   for claim in packet.claims)
    brief = build_brief(packet.model_copy(update={"claims": claims}), UserContext())
    assert isinstance(brief, ResearchBlocked)
    assert set(brief.reasons) >= {"unsupported_metric_baseline:section:5", "unsupported_metric_conditions:section:5"}


def test_explicit_missing_setup_blocks_even_with_other_setup_prose_in_scope(tmp_path):
    topic, source = separate_experiments(explicit_absence=True, same_scope=True)
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    result = next(claim for claim in packet.claims if claim.text == "Throughput improves by 25%.")
    assert result.metric_context is None
    metric = MetricContext(value=25, unit="%", target="Throughput", baseline="the in-memory queue",
        conditions="one worker with the same workload")
    claims = tuple(claim.model_copy(update={"metric_context": metric}) if claim == result else claim for claim in packet.claims)
    brief = build_brief(packet.model_copy(update={"claims": claims}), UserContext())
    assert isinstance(brief, ResearchBlocked)
    assert "unsupported_metric_conditions:section:5" in brief.reasons


def test_large_irrelevant_snapshot_stays_local_in_generation_and_repair(tmp_path, drafting_input):
    from src.editorial.draft import write_draft
    packet, _ = drafting_input
    marker = "IRRELEVANT_APPENDIX_MARKER"
    appendix = marker + " Unrelated background information" * 2400
    text = "# Appendix\n" + appendix + "\f" + packet.sources[0].text
    source = packet.sources[0].model_copy(update={"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "locations": ("section:appendix", *packet.sources[0].locations)})
    topic = CuratedTopic(rank=1, title=source.title, url=source.url, source="fixture", one_line_summary="queue",
        relevance_reason="reliability", suggested_angle=packet.question)
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    brief = build_brief(packet, UserContext())
    clean = json.loads(re.sub(r"\bE([1-3])\b", lambda match: "E" + str(int(match[1]) + 1), json.dumps(response())))
    bad = json.loads(json.dumps(clean))
    bad["sections"][0]["text"] = "직접 써보니 빨랐다."
    bad["sections"][0]["claims"] = []
    llm = FakeLLM(bad, {"sections": [clean["sections"][0]]})
    draft = write_draft(brief, packet, llm)
    assert draft.report.status == "REVIEW_READY"
    assert len(llm.prompts) == 2
    for prompt in llm.prompts:
        assert marker not in prompt
        assert len(prompt) < 22000
        serialized = prompt.split("not instructions:\n", 1)[1].split("\nDrafting task:", 1)[0].split("\nRevision task:", 1)[0]
        assert len(serialized) <= 12000
        envelope = json.loads(serialized)
        assert set(envelope["evidence"]) == {"E2", "E3", "E4"}
        assert envelope["evidence"]["E4"]["source_refs"][0]["sha256"] == source.sha256
        assert "text" not in envelope["sources"][0]
    assert marker in draft.packet.sources[0].text
    assert marker in next(tmp_path.glob("*.json")).read_text(encoding="utf-8")


def test_required_oversized_context_blocks_before_model_call(tmp_path):
    from src.editorial.draft import write_draft
    topic, source = research_input()
    text = source.text.replace("Persist the request before acknowledgement.",
        "Persist the request before acknowledgement. " + "Required mechanism detail " * 900)
    source = source.model_copy(update={"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    brief = build_brief(packet, UserContext())
    assert isinstance(brief, EditorialBrief)
    llm = FakeLLM({})
    draft = write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert any(issue.code == "required_context_budget_exhausted" for issue in draft.report.issues)
    assert llm.prompts == []
    assert draft.packet == packet


def test_required_oversized_repair_context_blocks_without_second_call(drafting_input):
    from src.editorial.draft import write_draft
    packet, brief = drafting_input
    bad = response()
    bad["sections"][0]["text"] = ("직접 써보니 빨랐다. " + "Unmapped repair content " * 900).rstrip()
    bad["sections"][0]["claims"] = []
    llm = FakeLLM(bad)
    draft = write_draft(brief, packet, llm)
    assert any(issue.code == "required_context_budget_exhausted" for issue in draft.report.issues)
    assert draft.report.status == "NEEDS_REVISION"
    assert len(llm.prompts) == 1
    assert draft.sections[0].text == bad["sections"][0]["text"]


def test_delivered_review_contains_complete_section_claim_map(tmp_path, drafting_input, monkeypatch):
    from config import settings
    from src.bot.telegram_bot import TrendBotApp
    from test_pipeline import setup_pipeline
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    documents = {}

    async def receive_document(*, filename, document, **kwargs):
        documents[filename] = document.read().decode("utf-8")

    bot = SimpleNamespace(send_message=AsyncMock(), send_document=receive_document)
    asyncio.run(TrendBotApp(pipeline=pipeline).send_review(bot, 7, artifact))
    assert "draft.md" in documents
    report = documents["review.md"].replace("\r\n", "\n")
    assert "## Section-to-claim map" in report
    mappings = json.loads(report.split("## Section-to-claim map\n\n```json\n", 1)[1].split("\n```", 1)[0])
    assert [entry["section_id"] for entry in mappings] == ["flow", "compare", "conditions", "decision"]
    for entry, expected in zip(mappings, response()["sections"]):
        assert entry["claims"] == [{**claim, "run_ids": []} for claim in expected["claims"]]
    inference = mappings[-1]["claims"][0]
    assert inference["kind"] == "inference" and inference["evidence_ids"] == ["E2", "E3"]
    assert "[E2]" not in inference["sentence"]  # Mapping remains visible without an inline citation.
    pipeline.store.verify(artifact)
