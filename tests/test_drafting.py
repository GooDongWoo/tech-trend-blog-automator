"""Generation must preserve failures and make bounded, section-only repairs."""
import asyncio
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from drafting_fixtures import drafting_input, response


def api():
    try:
        return importlib.import_module("src.editorial.draft")
    except ModuleNotFoundError:
        pytest.fail("Unit 4 write_draft is missing")


class FakeLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item


def test_drafts_from_policy_and_preserves_claim_map(drafting_input):
    packet, brief = drafting_input
    llm = FakeLLM(response())
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "REVIEW_READY"
    assert draft.packet == packet
    assert draft.sections[0].claims[0].evidence_ids == ("E1",)
    assert "policy_version: 1.1" in llm.prompts[0]
    assert "snapshot" in llm.prompts[0].lower()
    assert packet.sources[0].sha256 in llm.prompts[0]
    assert "[MEME_1]" not in draft.content
    assert "```mermaid" not in draft.content


@pytest.mark.parametrize("failure", ["", "not JSON", {}, TimeoutError("fixture outage")])
def test_llm_failure_is_visible_and_retains_packet(drafting_input, failure):
    packet, brief = drafting_input
    llm = FakeLLM(failure)
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert draft.packet == packet
    assert draft.report.issues
    assert draft.content == ""
    assert len(llm.prompts) == 1


def test_revision_replaces_only_implicated_section_and_injects_policy(drafting_input):
    packet, brief = drafting_input
    bad = response()
    bad["sections"][0]["text"] = "직접 써보니 정말 빨랐다."
    bad["sections"][0]["claims"] = []
    repair = {"sections": [response()["sections"][0]]}
    llm = FakeLLM(bad, repair)
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "REVIEW_READY"
    assert draft.sections[1].text == response()["sections"][1]["text"]
    assert len(llm.prompts) == 2
    assert "policy_version: 1.1" in llm.prompts[1]
    assert "flow" in llm.prompts[1]
    assert "invented_experience" in llm.prompts[1]


def test_revision_budget_leaves_bad_draft_blocked(drafting_input):
    packet, brief = drafting_input
    bad = response()
    bad["sections"][0]["text"] = "직접 써보니 빨랐다."
    bad["sections"][0]["claims"] = []
    repair = {"sections": [bad["sections"][0]]}
    llm = FakeLLM(bad, repair, repair)
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert len(llm.prompts) == 3
    assert "직접 써보니" in draft.content


def test_revision_cannot_mutate_unimplicated_sections(drafting_input):
    packet, brief = drafting_input
    bad = response()
    bad["sections"][0]["text"] = "직접 써보니 빨랐다."
    bad["sections"][0]["claims"] = []
    llm = FakeLLM(bad, {"sections": response()["sections"]})
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert any(issue.code == "revision_scope_violation" for issue in draft.report.issues)
    assert draft.sections[1].text == bad["sections"][1]["text"]


def test_missing_runtime_policy_blocks_without_llm(drafting_input, monkeypatch, tmp_path):
    packet, brief = drafting_input
    monkeypatch.setattr("src.editorial.brief.POLICY_PATH", tmp_path / "absent.md")
    llm = FakeLLM(response())
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert draft.report.issues[0].code == "editorial_policy_unavailable"
    assert llm.prompts == []


def test_writer_keeps_local_review_artifacts_out_of_blog(drafting_input, tmp_path):
    from src.curator.matcher import CuratedTopic
    from src.writer.blog_writer import BlogWriter
    packet, brief = drafting_input
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture",
        one_line_summary="queue", relevance_reason="reliability", suggested_angle=packet.question)
    blog = tmp_path / "blog"
    writer = BlogWriter(blog, artifact_dir=tmp_path / "drafts", llm=FakeLLM(response()))
    writer.researcher.research = AsyncMock(return_value={"packet": packet, "status": "RESEARCH_READY"})
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "REVIEW_READY"
    assert result["publishable"] is False
    assert not blog.exists()
    directory = Path(result["file_path"]).parent
    assert Path(result["file_path"]).is_relative_to(tmp_path / "drafts")
    assert {"packet.json", "packet.md", "brief.json", "brief.md", "draft.json", "draft.md", "validation.json", "validation.md"} <= {p.name for p in directory.iterdir()}
    assert json.loads((directory / "validation.json").read_text(encoding="utf-8"))["status"] == "REVIEW_READY"


def test_writer_outage_retains_packet_and_never_creates_blog(drafting_input, tmp_path):
    from src.curator.matcher import CuratedTopic
    from src.writer.blog_writer import BlogWriter
    packet, _ = drafting_input
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts", llm=FakeLLM(""))
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture",
        one_line_summary="queue", relevance_reason="reliability", suggested_angle=packet.question)
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_REVISION"
    assert result["packet"] == packet
    assert result["content"] == ""
    assert not (tmp_path / "blog").exists()
    assert Path(result["evidence_path"]).exists()


# Telegram lifecycle acceptance moved to test_pipeline.py in Unit 6.


def test_invalid_research_packet_blocks_before_model_call(drafting_input):
    packet, brief = drafting_input
    packet = packet.model_copy(update={"claims": ()})
    llm = FakeLLM(response())
    draft = api().write_draft(brief, packet, llm)
    assert draft.report.status == "NEEDS_REVISION"
    assert llm.prompts == []
    assert draft.packet == packet


def test_writer_returns_bad_yaml_as_blocked_artifact(drafting_input, tmp_path):
    from src.curator.matcher import CuratedTopic
    from src.writer.blog_writer import BlogWriter
    packet, _ = drafting_input
    payload = response()
    payload["frontmatter"] = '---\nlayout: post\ntitle: [broken\n---'
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts", llm=FakeLLM(payload, TimeoutError("repair unavailable")))
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture", one_line_summary="queue",
        relevance_reason="test", suggested_angle=packet.question)
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_REVISION"
    assert "invalid_frontmatter" in result["reasons"]
    assert result["draft"].revision_attempts == 1
    assert Path(result["evidence_path"]).exists()
