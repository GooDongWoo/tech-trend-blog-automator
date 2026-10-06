"""Known defects of the original pipeline, not desired acceptance criteria.

These are ordinary passing characterization tests (not xfail). Each marker names
the unit that must replace the assertion when its implementation changes.
"""
import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

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
def fake_http(monkeypatch):
    """Use only in-memory fixture responses; httpx cannot reach a transport."""
    responses = {}
    requests = []

    class FixtureClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            requests.append(url)
            assert url in responses, f"Unexpected fixture URL: {url}"
            return responses[url]

    monkeypatch.setattr("src.writer.deep_researcher.httpx.AsyncClient", FixtureClient)
    return responses, requests


def fake_research(writer, topic):
    writer.researcher.research = AsyncMock(return_value={
        "raw_content": (FIXTURES / "README.md").read_text(encoding="utf-8")})


def test_html_and_readme_use_local_known_claims(topic, fake_http):
    responses, requests = fake_http
    responses[topic.url] = httpx.Response(200, content=(FIXTURES / "source.html").read_bytes())
    research = asyncio.run(DeepResearcher().research(topic))
    assert "persists pending jobs" in research["raw_content"]
    assert "Navigation must be removed" not in research["raw_content"]
    topic.url = "https://github.com/fixture/queue"
    readme_url = "https://raw.githubusercontent.com/fixture/queue/main/README.md"
    responses[readme_url] = httpx.Response(200, content=(FIXTURES / "README.md").read_bytes())
    research = asyncio.run(DeepResearcher().research(topic))
    assert "enqueue(job)" in research["raw_content"]
    assert requests == ["https://docs.example.invalid/queue", readme_url]


@pytest.mark.current_behavior(owner="Unit 2: extraction")
def test_current_pdf_body_is_missing(topic, fake_http):
    responses, requests = fake_http
    topic.url = "https://docs.example.invalid/source.pdf"
    pdf = (FIXTURES / "source.pdf").read_bytes()
    assert b"The queue persists pending jobs before acknowledging them." in pdf
    responses[topic.url] = httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
    research = asyncio.run(DeepResearcher().research(topic))
    assert "PDF Whitepaper: Fixture queue" in research["raw_content"]
    assert "persists pending jobs" not in research["raw_content"]
    assert requests == [topic.url]


@pytest.mark.current_behavior(owner="Unit 4: writer")
def test_current_empty_llm_writes_success_shaped_template(topic, tmp_path, monkeypatch):
    writer = BlogWriter(tmp_path / "blog")
    fake_research(writer, topic)
    responses = json.loads((FIXTURES / "llm-responses.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(writer, "_call_llm", lambda prompt: responses["empty"])
    result = asyncio.run(writer.generate_post(topic))
    assert Path(result["file_path"]).is_relative_to(tmp_path)
    assert Path(result["file_path"]).read_text(encoding="utf-8") == result["content"]
    assert "layout: post" in result["content"]
    assert "실무 개발자가 뜯어본 솔직 후기" in result["title"]
    assert "오늘도 평화롭게 깃허브를 서핑" in result["content"]
    assert "status" not in result  # No blocked status distinguishes the fallback.


@pytest.mark.current_behavior(owner="Unit 5: media")
def test_current_two_memes_inserted_without_topic_matching(topic, tmp_path, monkeypatch):
    writer = BlogWriter(tmp_path / "blog")
    fake_research(writer, topic)
    shutil.copyfile(FIXTURES / "local.gif", writer.meme_mgr.memes_dir / "local.gif")
    unrelated = {"caption": "A white pixel unrelated to queues", "url": "/assets/images/memes/local.gif"}
    choose = Mock(return_value=unrelated)
    monkeypatch.setattr(writer.meme_mgr, "get_random_meme", choose)
    responses = json.loads((FIXTURES / "llm-responses.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(writer, "_call_llm", lambda prompt: responses["unsupported_quantitative"])
    result = asyncio.run(writer.generate_post(topic))
    assert choose.call_count == 2
    assert all(not call.args and not call.kwargs for call in choose.call_args_list)
    assert result["content"].count("![]") == 0
    assert result["content"].count("![A white pixel unrelated to queues]") == 2
    assert responses["unsupported_claim"] in result["content"]
    assert "[MEME_" not in result["content"]


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
