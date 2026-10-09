"""Freeze selected user context across real briefing, restart and retry routes."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from drafting_fixtures import drafting_input, response
from src.bot.telegram_bot import TrendBotApp
from src.editorial.models import SourceRecord, UserContext, VaultNoteRef
from src.editorial.pipeline import EditorialPipeline
from test_drafting import FakeLLM
from test_pipeline import setup_pipeline


def context_fixture(tmp_path):
    path = tmp_path / "explicit-run.log"
    raw = b"I ran the queue on one worker; pending jobs survived restart.\n"
    path.write_bytes(raw)
    run = SourceRecord(url=path.as_uri(), kind="run_log", title="Explicit queue run", text=raw.decode().strip(),
        sha256=hashlib.sha256(raw).hexdigest(), locations=("run:1",), fetched_at="2026-10-08T00:00:00Z", snapshot_path=path)
    note = VaultNoteRef(path=tmp_path / "private-note.md", sha256="0" * 64, title="Queue interest",
        snippet="PRIVATE_VAULT_BODY", retrieval_method="local_search")
    context = UserContext(goals=("Preserve jobs across restarts",), constraints=("One worker deployment",),
        interests=("Queues",), experience_refs=(run,), note_refs=(note,), depth="documented")
    return context, path, raw


def briefing_app(tmp_path, drafting_input, monkeypatch, *, damage_before_registration=None):
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    llm = FakeLLM(response(), response(), response())
    pipeline.writer.llm = llm
    context, original, raw = context_fixture(tmp_path)
    if damage_before_registration == "changed":
        original.write_bytes(b"Changed before briefing")
    elif damage_before_registration == "missing":
        original.unlink()
    from src.profiler.interest_profiler import UserProfile
    profile = UserProfile(core_interests=[], knowledge_depth={}, avoid_topics=[], target_domains=[], search_keywords=[], user_context=context)
    app = TrendBotApp(pipeline=pipeline)
    app.profiler.build_profile = lambda **kwargs: profile
    app.collector.collect_all = AsyncMock(return_value=[])
    app.matcher.curate_top_5 = lambda *args: [pipeline.store.topic(topic_id)]
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(app.trigger_briefing(context=SimpleNamespace(bot=bot)))
    callback = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    return app, bot, callback, profile, context, original, raw, llm


def select(app, bot, callback):
    query = SimpleNamespace(data=callback, answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    artifacts = list(app.workflow.store.root.rglob("artifact.json"))
    assert artifacts, "The stored selection must produce a visible review artifact after restart"
    run = app.workflow.find_run(artifacts[-1].parent.name)
    return app.workflow.pipeline(run).get_draft(run.draft_id)


def assert_bound_context(artifact, context, raw, llm):
    brief = json.loads((artifact.content_path.parent / "brief.json").read_text(encoding="utf-8"))
    stored = brief["user_context"]
    assert stored["goals"] == list(context.goals)
    assert stored["constraints"] == list(context.constraints)
    assert stored["depth"] == "documented"
    run = stored["experience_refs"][0]
    assert run["url"] == context.experience_refs[0].url
    assert run["sha256"] == hashlib.sha256(raw).hexdigest()
    copied = Path(run["snapshot_path"])
    assert copied.is_relative_to(artifact.content_path.parent)
    assert copied.read_bytes() == raw
    assert artifact.status == "REVIEW_READY"
    prompt = llm.prompts[-1]
    assert context.goals[0] in prompt and context.constraints[0] in prompt
    assert context.experience_refs[0].text in prompt and '"R1"' in prompt
    assert "PRIVATE_VAULT_BODY" not in prompt


def test_real_briefing_selection_uses_frozen_context_and_run_bytes(tmp_path, drafting_input, monkeypatch):
    app, bot, callback, profile, context, original, raw, llm = briefing_app(tmp_path, drafting_input, monkeypatch)
    profile.user_context = UserContext()  # A later profile must not change a selected card.
    original.unlink()
    artifact = select(app, bot, callback)
    assert_bound_context(artifact, context, raw, llm)
    app.workflow.pipeline(app.workflow.find_run(artifact.id)).store.verify(artifact)


def test_restart_selection_and_retry_preserve_original_context(tmp_path, drafting_input, monkeypatch):
    app, bot, callback, profile, context, original, raw, llm = briefing_app(tmp_path, drafting_input, monkeypatch)
    original.unlink()
    restarted = EditorialPipeline(app.pipeline.store.root, writer=app.pipeline.writer)
    restarted_app = TrendBotApp(pipeline=restarted)
    artifact = select(restarted_app, bot, callback)
    assert_bound_context(artifact, context, raw, llm)
    run = restarted_app.workflow.find_run(artifact.id)
    revised = asyncio.run(restarted_app.workflow.revise(run.id, artifact.id, artifact.content_sha256, reviewer='telegram:7:7'))
    retried = restarted_app.workflow.pipeline(revised).get_draft(revised.draft_id)
    assert_bound_context(retried, context, raw, llm)
    assert artifact.id != retried.id


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_changed_or_missing_frozen_run_blocks_visibly_before_model(tmp_path, drafting_input, monkeypatch, damage):
    app, bot, callback, profile, context, original, raw, llm = briefing_app(tmp_path, drafting_input, monkeypatch)
    snapshots = list(app.workflow.store.root.glob("*/review/topics/*-runs/*.bin"))
    assert snapshots, "Explicit run bytes must be frozen at briefing registration"
    if damage == "changed":
        snapshots[-1].write_bytes(b"tampered evidence")
    else:
        snapshots[-1].unlink()
    artifact = select(app, bot, callback)
    assert artifact.status == "NEEDS_RESEARCH"
    expected = "user_context_run_snapshot_changed" if damage == "changed" else "user_context_run_snapshot_unavailable"
    assert expected in artifact.report_path.read_text(encoding="utf-8")
    assert llm.prompts == []


@pytest.mark.parametrize("explicit", [False, True])
def test_actual_cli_profile_context_reaches_generation(tmp_path, drafting_input, monkeypatch, explicit):
    import main
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    context, original, raw = context_fixture(tmp_path)
    llm = FakeLLM(response())
    pipeline.writer.llm = llm
    monkeypatch.setattr(main.InterestProfiler, "build_profile", lambda *args, **kwargs: SimpleNamespace(user_context=UserContext() if explicit else context))
    monkeypatch.setattr(main.TrendOrchestrator, "collect_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.TrendMatcher, "curate_top_5", lambda *args: [pipeline.store.topic(topic_id)])
    artifacts = asyncio.run(main.test_pipeline(output_root=tmp_path / "cli", writer=pipeline.writer,
        user_context=context if explicit else None))
    assert_bound_context(artifacts[0], context, raw, llm)


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_invalid_run_at_briefing_registration_stays_visible(tmp_path, drafting_input, monkeypatch, damage):
    app, bot, callback, profile, context, original, raw, llm = briefing_app(
        tmp_path, drafting_input, monkeypatch, damage_before_registration=damage)
    artifact = select(app, bot, callback)
    assert artifact.status == "NEEDS_RESEARCH"
    expected = "user_context_run_snapshot_changed" if damage == "changed" else "user_context_run_snapshot_unavailable"
    assert expected in artifact.report_path.read_text(encoding="utf-8")
    stored = json.loads((artifact.content_path.parent / "user-context.json").read_text(encoding="utf-8"))
    assert stored["goals"] == list(context.goals) and len(stored["experience_refs"]) == 1
    assert llm.prompts == []


def test_changed_bound_user_metadata_does_not_default_to_empty(tmp_path, drafting_input, monkeypatch):
    app, bot, callback, profile, context, original, raw, llm = briefing_app(tmp_path, drafting_input, monkeypatch)
    path = next(path for path in app.workflow.store.root.glob("*/review/topics/*.json")
                if json.loads(path.read_text(encoding="utf-8"))["user_context"]["goals"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["user_context"]["goals"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    query = SimpleNamespace(data=callback, answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    run = app.workflow.status(callback.split(':')[1])
    assert run.status == "NEEDS_RESEARCH"
    assert run.events[-1]['action'] == 'research_failed'
    assert llm.prompts == []


def test_selected_cli_accepts_explicit_bound_context(tmp_path, drafting_input):
    from scripts import generate_selected
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    context, original, raw = context_fixture(tmp_path)
    llm = FakeLLM(response())
    pipeline.writer.llm = llm
    artifacts = asyncio.run(generate_selected.main(output_root=tmp_path / "selected-cli", writer=pipeline.writer,
        selected_topics=[pipeline.store.topic(topic_id)], user_context=context))
    assert_bound_context(artifacts[0], context, raw, llm)


def test_obsolete_rank_selection_cannot_substitute_empty_context(tmp_path, drafting_input, monkeypatch):
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    llm = FakeLLM(response())
    pipeline.writer.llm = llm
    app = TrendBotApp(pipeline=pipeline)
    app.current_topics = {1: pipeline.store.topic(topic_id)}
    query = SimpleNamespace(data="select_1", answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert list(pipeline.store.root.glob("*/artifact.json")) == []
    assert llm.prompts == []
    assert "refresh" in query.edit_message_text.call_args.args[0]
