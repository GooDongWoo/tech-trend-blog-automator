"""Durable review boundary, exercised without network or external writes."""
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from drafting_fixtures import drafting_input, response
from src.curator.matcher import CuratedTopic
from src.writer.blog_writer import BlogWriter
from test_media import catalog


def api():
    try:
        return importlib.import_module("src.editorial.pipeline")
    except ModuleNotFoundError:
        pytest.fail("durable editorial pipeline is missing")


def setup_pipeline(tmp_path, drafting_input, *, payload=None, research=None, user_context=None):
    packet, _ = drafting_input
    writer = BlogWriter(artifact_dir=tmp_path / "adapter", llm=SimpleNamespace(
        generate=lambda prompt: json.dumps(response() if payload is None else payload)))
    writer.researcher.research = AsyncMock(return_value=research or {"packet": packet})
    pipeline = api().EditorialPipeline(tmp_path / "review", writer=writer)
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture",
        one_line_summary="queue", relevance_reason="reliability", suggested_angle=packet.question)
    topic_id = pipeline.register_topic(topic, user_context=user_context)
    return pipeline, topic_id


def test_parallel_drafts_survive_restart_and_approve_exact_revision(tmp_path, drafting_input):
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    async def generate_both():
        return await asyncio.gather(pipeline.generate(topic_id), pipeline.generate(topic_id))
    first, second = asyncio.run(generate_both())
    assert first.id != second.id
    assert first.content_path != second.content_path
    restarted = api().EditorialPipeline(tmp_path / "review")
    approved = restarted.approve(first.id, first.content_sha256)
    assert approved.status == "APPROVED"
    assert restarted.get_draft(second.id).status == "REVIEW_READY"
    assert not settings.blog_repo_path.exists()
    assert not settings.obsidian_vault_path.exists()
    directory = first.content_path.parent
    for name in ("packet.json", "brief.json", "draft.json", "validation.json", "run.json", "review.md", "manifest.json"):
        assert (directory / name).is_file()
    assert list((directory / "sources").glob("*.json"))


def test_retry_supersedes_old_button_even_after_restart(tmp_path, drafting_input):
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    first = asyncio.run(pipeline.generate(topic_id))
    second = asyncio.run(pipeline.retry(first.id))
    restarted = api().EditorialPipeline(tmp_path / "review")
    with pytest.raises(ValueError, match="superseded"):
        restarted.approve(first.id, first.content_sha256)
    assert restarted.approve(second.id, second.content_sha256).status == "APPROVED"


@pytest.mark.parametrize("filename", ["draft.md", "validation.json", "packet.json", "brief.json"])
def test_changed_review_files_reject_approval(tmp_path, drafting_input, filename):
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    (artifact.content_path.parent / filename).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        pipeline.approve(artifact.id, artifact.content_sha256)
    assert pipeline.get_draft(artifact.id).status == "NEEDS_REVISION"


def test_wrong_hash_and_legacy_callback_rejected(tmp_path, drafting_input):
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    with pytest.raises(ValueError, match="hash"):
        pipeline.approve(artifact.id, "0" * 64)
    with pytest.raises(ValueError):
        api().decode_callback("approve_push")
    token = api().approval_callback(artifact)
    assert len(token.encode()) <= 64
    assert api().decode_callback(token) == (artifact.id, artifact.content_sha256)


@pytest.mark.parametrize("research_blocked", [False, True])
def test_blocked_runs_persist_reasons_and_cannot_approve(tmp_path, drafting_input, research_blocked):
    packet, _ = drafting_input
    research = {"packet": packet, "status": "NEEDS_RESEARCH", "reasons": ["context_budget_exhausted"]} if research_blocked else None
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, payload={}, research=research)
    artifact = asyncio.run(pipeline.generate(topic_id))
    expected = "NEEDS_RESEARCH" if research_blocked else "NEEDS_REVISION"
    assert artifact.status == expected
    assert json.loads((artifact.content_path.parent / "run.json").read_text())["status"] == expected
    assert artifact.evidence_path.is_file()
    assert expected in (artifact.content_path.parent / "review.md").read_text(encoding="utf-8")
    if research_blocked:
        assert "context_budget_exhausted" in artifact.report_path.read_text()
    with pytest.raises(ValueError):
        pipeline.approve(artifact.id, artifact.content_sha256)


def test_bot_sends_complete_documents_sources_and_approves_after_restart(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    app = TrendBotApp(pipeline=pipeline)
    asyncio.run(app.send_review(bot, 7, artifact))
    documents = [call.kwargs["filename"] for call in bot.send_document.call_args_list]
    assert "draft.md" in documents and "review.md" in documents
    card = bot.send_message.call_args.kwargs
    assert str(artifact.content_path) in card["text"]
    assert "SHA-256" in card["text"]
    rows = card["reply_markup"].inline_keyboard
    assert any(button.url == "https://example.invalid/pageindex" for row in rows for button in row)
    approval = next(button.callback_data for row in rows for button in row if button.callback_data and button.callback_data.startswith("a:"))
    app = TrendBotApp(pipeline=api().EditorialPipeline(tmp_path / "review"))
    query = SimpleNamespace(data=approval, answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert pipeline.get_draft(artifact.id).status == "APPROVED"
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


@pytest.mark.parametrize("chat,user", [(7, 8), (8, 7), (8, 8)])
def test_unauthorized_callback_cannot_approve(tmp_path, drafting_input, monkeypatch, chat, user):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    app = TrendBotApp(pipeline=pipeline)
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(app.send_review(bot, 7, artifact))
    query = SimpleNamespace(data=api().approval_callback(artifact), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=chat), from_user=SimpleNamespace(id=user))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert pipeline.get_draft(artifact.id).status == "REVIEW_READY"


def test_store_refuses_blog_and_vault_roots(tmp_path):
    for root in (settings.blog_repo_path, settings.blog_repo_path / "_posts", settings.obsidian_vault_path / "drafts"):
        with pytest.raises(ValueError, match="external"):
            api().EditorialPipeline(root)
        assert not root.exists()


def test_draft_id_cannot_escape_store(tmp_path):
    pipeline = api().EditorialPipeline(tmp_path / "review")
    for identity in ("../secret", "C:/secret", "unknown"):
        with pytest.raises((ValueError, FileNotFoundError)):
            pipeline.get_draft(identity)


def test_selected_media_is_copied_reviewable_and_hash_bound(tmp_path, drafting_input, catalog, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    item = catalog.assets[0].model_copy(update={"contexts": ("durable storage",)})
    pipeline.writer.media_catalog = catalog.model_copy(update={"assets": (item,)})
    artifact = asyncio.run(pipeline.generate(topic_id))
    assert artifact.status == "REVIEW_READY"
    assert len(artifact.media_paths) == 1
    copied = artifact.media_paths[0]
    assert copied.is_relative_to(artifact.content_path.parent)
    original = catalog.asset_root / item.file
    assert copied.read_bytes() == original.read_bytes()
    original.unlink()
    pipeline.store.verify(artifact)
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(TrendBotApp(pipeline=pipeline).send_review(bot, 7, artifact))
    assert "pixel.gif" in [call.kwargs["filename"] for call in bot.send_document.call_args_list]
    copied.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        pipeline.approve(artifact.id, artifact.content_sha256)


def test_dry_run_defaults_to_temporary_output_and_reports_blocked_state(tmp_path, drafting_input, capsys):
    import main
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, payload={})
    artifacts = asyncio.run(main.test_pipeline(topics=[pipeline.store.topic(topic_id)], writer=pipeline.writer))
    artifact = artifacts[0]
    assert artifact.status == "NEEDS_REVISION"
    assert artifact.content_path.is_file()
    assert not artifact.content_path.is_relative_to(Path.cwd())
    assert "NEEDS_REVISION" in capsys.readouterr().out
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


def test_selected_script_uses_durable_artifacts_and_preserves_blocked_runs(tmp_path, drafting_input, capsys):
    from scripts import generate_selected
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, payload={})
    artifacts = asyncio.run(generate_selected.main(output_root=tmp_path / "selected", writer=pipeline.writer,
        selected_topics=[pipeline.store.topic(topic_id)]))
    assert artifacts[0].status == "NEEDS_REVISION"
    assert artifacts[0].report_path.is_file()
    assert "NEEDS_REVISION" in capsys.readouterr().out
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


def test_source_snapshots_remain_inspectable_after_original_removed(tmp_path, drafting_input):
    packet, brief = drafting_input
    original = tmp_path / "original.json"
    original.write_text(packet.sources[0].model_dump_json(), encoding="utf-8")
    raw_path = original.with_suffix(".bin")
    raw_path.write_bytes(packet.sources[0].text.encode())
    source = packet.sources[0].model_copy(update={"snapshot_path": original})
    packet = packet.model_copy(update={"sources": (source,)})
    pipeline, topic_id = setup_pipeline(tmp_path, (packet, brief))
    artifact = asyncio.run(pipeline.generate(topic_id))
    persisted = json.loads(artifact.evidence_path.read_text(encoding="utf-8"))
    copy_path = Path(persisted["sources"][0]["snapshot_path"])
    assert copy_path.is_relative_to(artifact.content_path.parent)
    assert copy_path.with_suffix(".bin").read_bytes() == raw_path.read_bytes()
    original.unlink()
    raw_path.unlink()
    assert pipeline.approve(artifact.id, artifact.content_sha256).status == "APPROVED"


def test_selection_and_blocked_review_never_offer_approval(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, payload={})
    app = TrendBotApp(pipeline=pipeline)
    app.current_topics = {1: pipeline.store.topic(topic_id)}
    from src.profiler.interest_profiler import UserProfile
    app.profiler.build_profile = lambda **kwargs: UserProfile(core_interests=[], knowledge_depth={}, avoid_topics=[], target_domains=[], search_keywords=[])
    app.collector.collect_all = AsyncMock(return_value=[])
    app.matcher.curate_top_5 = lambda *args: [pipeline.store.topic(topic_id)]
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    run = asyncio.run(app.trigger_briefing(context=SimpleNamespace(bot=bot)))
    query = SimpleNamespace(data=f'w:{run.id}:1', answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    card = bot.send_message.call_args.kwargs
    assert "NEEDS_REVISION" in card["text"]
    assert not any((button.callback_data or "").startswith("a:") for row in card["reply_markup"].inline_keyboard for button in row)
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


@pytest.mark.parametrize("research_blocked", [False, True])
def test_empty_blocked_draft_still_delivers_report_and_retry(tmp_path, drafting_input, monkeypatch, research_blocked):
    from telegram.error import BadRequest
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    packet, _ = drafting_input
    research = {"packet": packet, "status": "NEEDS_RESEARCH", "reasons": ["context_budget_exhausted"]} if research_blocked else None
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input, payload={}, research=research)
    artifact = asyncio.run(pipeline.generate(topic_id))
    assert artifact.content_path.read_bytes() == b""
    documents = {}
    async def send_document(*, chat_id, document, filename):
        content = document.read()
        if not content:
            raise BadRequest("File must be non-empty")
        documents[filename] = content
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=send_document)
    asyncio.run(TrendBotApp(pipeline=pipeline).send_review(bot, 7, artifact))
    assert "draft.md" not in documents
    assert artifact.status.value.encode() in documents["review.md"]
    card = bot.send_message.call_args.kwargs
    assert artifact.status.value in card["text"]
    callbacks = [button.callback_data for row in card["reply_markup"].inline_keyboard for button in row if button.callback_data]
    assert "r:" + api().approval_callback(artifact)[2:] in callbacks
    assert not any(callback.startswith("a:") for callback in callbacks)


def test_legacy_approval_has_no_side_effects_even_from_authorized_user(tmp_path, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline = api().EditorialPipeline(tmp_path / "review")
    query = SimpleNamespace(data="approve_push", answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(TrendBotApp(pipeline=pipeline).handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert "obsolete" in query.edit_message_text.call_args.args[0]
    assert not (tmp_path / "review").exists()
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


def test_actual_research_fetch_keeps_all_outputs_under_run_root(tmp_path, monkeypatch):
    import httpx
    monkeypatch.chdir(tmp_path)
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            return httpx.Response(200, content=b"# Queue\n\nPersist before acknowledgement.",
                request=httpx.Request("GET", url), headers={"content-type": "text/markdown"})
    monkeypatch.setattr("src.research.fetch.httpx.Client", Client)
    pipeline = api().EditorialPipeline(tmp_path / "review")
    topic = CuratedTopic(rank=1, title="Queue", url="https://example.invalid/queue.md", source="fixture",
        one_line_summary="queue", relevance_reason="test", suggested_angle="When is the queue useful?")
    artifact = asyncio.run(pipeline.generate(pipeline.register_topic(topic)))
    assert artifact.status == "NEEDS_RESEARCH"
    assert list((artifact.content_path.parent / "sources").glob("*.bin"))
    assert list((artifact.content_path.parent / "working").rglob("*.bin"))
    assert not (tmp_path / "temp").exists()


def test_restart_approval_requires_the_review_recipient_binding(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    # A generated but never delivered draft cannot be approved through Telegram.
    query = SimpleNamespace(data=api().approval_callback(artifact), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(TrendBotApp(pipeline=pipeline).handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert pipeline.get_draft(artifact.id).status == "REVIEW_READY"


def test_group_review_requires_explicit_user_and_rejects_other_group_members(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "-100123")
    monkeypatch.setattr(settings, "telegram_reviewer_user_id", "")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    app = TrendBotApp(pipeline=pipeline)
    with pytest.raises(ValueError, match="configured"):
        app.reviewer_identity()
    monkeypatch.setattr(settings, "telegram_reviewer_user_id", "7")
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(app.send_review(bot, -100123, artifact))
    query = SimpleNamespace(data=api().approval_callback(artifact), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=-100123), from_user=SimpleNamespace(id=8))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert pipeline.get_draft(artifact.id).status == "REVIEW_READY"
    query.edit_message_text.assert_not_called()
    assert query.answer.call_count == 1
    assert query.answer.call_args.kwargs["show_alert"] is True
    query.from_user.id = 7
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert pipeline.get_draft(artifact.id).status == "APPROVED"


def test_explicit_run_source_still_points_to_exact_log_bytes(tmp_path, drafting_input):
    import hashlib
    from src.editorial.models import SourceRecord
    packet, brief = drafting_input
    path = tmp_path / "explicit.log"
    path.write_bytes(b"Observed fixture run.")
    run = SourceRecord(url=path.as_uri(), title="Explicit fixture run", kind="run_log", snapshot_path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), text="Observed fixture run.",
        locations=("line:1",), fetched_at="2026-10-07T00:00:00Z")
    packet = packet.model_copy(update={"sources": (*packet.sources, run)})
    pipeline, topic_id = setup_pipeline(tmp_path, (packet, brief))
    artifact = asyncio.run(pipeline.generate(topic_id))
    persisted = json.loads(artifact.evidence_path.read_text(encoding="utf-8"))
    local = Path(persisted["sources"][1]["snapshot_path"])
    assert local.read_bytes() == b"Observed fixture run."
