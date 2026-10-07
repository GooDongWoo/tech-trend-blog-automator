"""Known defects of the original pipeline, not desired acceptance criteria.

These are ordinary passing characterization tests (not xfail). Each marker names
the unit that must replace the assertion when its implementation changes.
"""
import asyncio
import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from drafting_fixtures import drafting_input
from src.curator.matcher import CuratedTopic
from src.writer.blog_writer import BlogWriter
from src.writer.deep_researcher import DeepResearcher

FIXTURES = Path(__file__).parent / "fixtures"


def test_offline_guard_rejects_network_and_processes():
    with socket.socket() as client:
        with pytest.raises(AssertionError, match="Offline tests"):
            client.connect(("127.0.0.1", 8765))
    with pytest.raises(AssertionError, match="Offline tests"):
        httpx.get("https://docs.example.invalid")
    with pytest.raises(AssertionError, match="Offline tests"):
        subprocess.run(["git", "push"], check=True)


@pytest.fixture
def topic():
    return CuratedTopic(rank=1, title="Fixture queue", url="https://docs.example.invalid/queue",
                        source="fixture", one_line_summary="A persistent job queue",
                        relevance_reason="Queue reliability", suggested_angle="Compare retry behavior")


@pytest.fixture
def fake_http(monkeypatch, tmp_path):
    """Use only in-memory fixture responses; httpx cannot reach a transport."""
    responses = {}
    requests = []

    class FixtureClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            requests.append(url)
            assert url in responses, f"Unexpected fixture URL: {url}"
            response = responses[url]
            response.request = httpx.Request("GET", url)
            return response

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.research.fetch.httpx.Client", FixtureClient)
    return responses, requests


def test_html_and_readme_use_local_known_claims(topic, fake_http):
    responses, requests = fake_http
    responses[topic.url] = httpx.Response(200, content=(FIXTURES / "source.html").read_bytes())
    research = asyncio.run(DeepResearcher().research(topic))
    assert "persists pending jobs" in research["raw_content"]
    assert "Navigation must be removed" not in research["raw_content"]
    topic.url = "https://github.com/fixture/queue"
    readme_url = "https://api.github.com/repos/fixture/queue/readme"
    responses[readme_url] = httpx.Response(200, content=(FIXTURES / "README.md").read_bytes())
    research = asyncio.run(DeepResearcher().research(topic))
    assert "enqueue(job)" in research["raw_content"]
    assert requests == ["https://docs.example.invalid/queue", readme_url]


def test_pdf_body_has_page_provenance(topic, fake_http):
    responses, requests = fake_http
    topic.url = "https://docs.example.invalid/source.pdf"
    pdf = (FIXTURES / "source.pdf").read_bytes()
    assert b"The queue persists pending jobs before acknowledging them." in pdf
    responses[topic.url] = httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
    research = asyncio.run(DeepResearcher().research(topic))
    assert research["status"] == "RESEARCH_READY"
    assert "persists pending jobs" in research["raw_content"]
    assert research["packet"].claims[0].source_refs[0].location == "page:1"
    assert requests == [topic.url]


def test_empty_llm_stays_blocked_without_fallback(topic, drafting_input, tmp_path, monkeypatch):
    packet, _ = drafting_input
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts")
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    monkeypatch.setattr(writer, "_call_llm", lambda prompt: "")
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_REVISION"
    assert result["content"] == ""
    assert result["packet"] == packet
    assert not (tmp_path / "blog").exists()


def test_writer_never_inserts_random_memes_into_failed_draft(topic, drafting_input, tmp_path, monkeypatch):
    packet, _ = drafting_input
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts")
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    responses = json.loads((FIXTURES / "llm-responses.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(writer, "_call_llm", lambda prompt: responses["unsupported_quantitative"])
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_REVISION"
    assert result["content"] == ""
    assert not (tmp_path / "blog" / "assets").exists()


@pytest.mark.current_behavior(owner="Unit 6: approval/publish")
def test_current_publish_attempt_allowed_after_500_character_preview(topic, tmp_path):
    from src.bot.telegram_bot import TrendBotApp

    # Skip constructors that instantiate real integrations; exercise the real handler.
    app = TrendBotApp.__new__(TrendBotApp)
    content = "VISIBLE-" + "x" * 700 + "UNREVIEWED-TAIL"
    path = tmp_path / "blog" / "_posts" / "draft.md"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    draft = {"title": "Fixture draft", "relative_path": "_posts/draft.md",
             "content": content, "file_path": str(path), "slug": "fixture"}
    app.current_topics = {1: topic}
    app.last_draft = None
    app.writer = SimpleNamespace(generate_post=AsyncMock(return_value=draft))
    app.publisher = SimpleNamespace(publish=Mock(return_value={"success": False, "message": "fake failure"}))
    app.obsidian_sync = SimpleNamespace(sync_post=Mock(return_value={"note_path": "fake-note"}))
    query = SimpleNamespace(data="select_1", answer=AsyncMock(), edit_message_text=AsyncMock(),
                            message=SimpleNamespace(chat_id=1))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    update = SimpleNamespace(callback_query=query)
    asyncio.run(app.handle_callback(update, context))
    preview = context.bot.send_message.call_args.kwargs
    assert f"{content[:500]}..." in preview["text"]
    assert "UNREVIEWED-TAIL" not in preview["text"]
    buttons = preview["reply_markup"].inline_keyboard
    assert buttons[0][0].callback_data == "approve_push"
    app.publisher.publish.assert_not_called()
    query.data = "approve_push"
    asyncio.run(app.handle_callback(update, context))
    app.publisher.publish.assert_called_once_with(str(path), "Fixture draft")
    # Also freeze the adjacent failure: sync and success message follow a failed push.
    app.obsidian_sync.sync_post.assert_called_once_with(draft)
    assert "성공적으로 배포" in context.bot.send_message.call_args.kwargs["text"]
