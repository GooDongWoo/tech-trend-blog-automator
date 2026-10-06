"""Temporary Vault notes indicate interest, never proof of hands-on expertise."""
import hashlib
import importlib
import json

import pytest

from src.curator.matcher import TrendMatcher
from src.collector.base import TrendItem
from src.profiler.interest_profiler import InterestProfiler, UserProfile
from src.profiler.rag_checker import RAGChecker


def context_api():
    module = importlib.import_module("src.profiler.interest_profiler")
    assert hasattr(module, "build_user_context"), "Traceable build_user_context is missing"
    return module.build_user_context


def test_no_profile_data_means_unknown_and_no_invented_topics(tmp_path):
    profile = InterestProfiler(tmp_path).build_profile()
    assert profile.core_interests == []
    assert profile.knowledge_depth == {"overall": "unknown"}
    assert profile.avoid_topics == []
    assert profile.user_context.experience_refs == ()
    assert "profile_evidence_absent" in profile.user_context.diagnostics


def test_llm_expertise_claim_cannot_promote_vault_note_to_experience(tmp_path, monkeypatch):
    notes = tmp_path / "20_Projects"
    notes.mkdir()
    (notes / "Queue.md").write_text("status: active\nLearning queue reliability", encoding="utf-8")
    profiler = InterestProfiler(tmp_path)
    monkeypatch.setattr(profiler, "_call_llm", lambda prompt: json.dumps({
        "core_interests": ["Queue"], "knowledge_depth": {"Queue": "Expert: deployed at scale"},
        "avoid_topics": ["Queue basics"], "target_domains": ["Queues"], "search_keywords": ["queue"]}))
    profile = profiler.build_profile()
    assert profile.knowledge_depth == {"Queue": "unknown"}
    assert profile.avoid_topics == []
    assert profile.user_context.experience_refs == ()
    assert profile.user_context.depth == "unknown"
    assert profile.user_context.note_refs[0].path == notes / "Queue.md"


def test_daemon_unavailable_results_are_labeled_local_search(tmp_path, monkeypatch):
    resources = tmp_path / "40_Resources"
    resources.mkdir()
    note = resources / "queue.md"
    note.write_text("Queue storage notes", encoding="utf-8")
    checker = RAGChecker(tmp_path)
    monkeypatch.setattr(checker, "is_daemon_alive", lambda: False)
    results = checker.query_knowledge_depth("queue")
    assert results[0]["retrieval_method"] == "local_search"
    assert results[0]["daemon_status"] == "unavailable"
    context = context_api()(note_results=results)
    assert context.note_refs[0].retrieval_method == "local_search"
    assert context.note_refs[0].sha256 == hashlib.sha256(note.read_bytes()).hexdigest()
    assert context.experience_refs == ()
    assert context.depth == "unknown"


def test_traceable_user_statements_distinguish_interests_from_public_experience():
    models = importlib.import_module("src.editorial.models")
    assert hasattr(models, "UserStatement"), "UserStatement provenance is missing"
    interest = models.UserStatement(text="I want durable queues", reference="user:message:17", category="interest")
    private = models.UserStatement(text="I deployed Queue", reference="user:message:18", category="experience")
    public = models.UserStatement(text="I tested retry recovery", reference="user:message:19", category="experience", publication_allowed=True)
    context = context_api()(statements=(interest, private, public))
    assert context.interests == ("I want durable queues",)
    assert context.published_experience == (public,)
    assert context.experience_refs == ()
    assert context.statements == (interest, private, public)


def test_curator_fallback_never_proposes_unperformed_hands_on_review(monkeypatch):
    matcher = TrendMatcher()
    monkeypatch.setattr(matcher, "_call_llm", lambda prompt: "")
    profile = UserProfile(core_interests=[], knowledge_depth={"overall": "unknown"}, avoid_topics=[], target_domains=[], search_keywords=[])
    item = TrendItem(title="Queue", url="https://example.invalid/queue", source="fixture")
    result = matcher.curate_top_5(profile, [item])[0]
    assert "실제 써보고" not in result.suggested_angle
    assert "?" in result.suggested_angle


def test_missing_or_unreadable_note_reference_does_not_create_context(tmp_path):
    context = context_api()(note_results=[{"title": "Queue", "path": str(tmp_path / "missing.md"), "snippet": "Expert", "retrieval_method": "local_search"}])
    assert context.interests == ()
    assert context.note_refs == ()
    assert "note_unavailable" in context.diagnostics


def test_legacy_profile_expertise_is_unknown_without_explicit_evidence():
    profile = UserProfile(core_interests=["Queue"], knowledge_depth={"Queue": "Expert: deployed at scale"},
                          avoid_topics=[], target_domains=[], search_keywords=[])
    assert profile.knowledge_depth == {"Queue": "unknown"}


def test_context_rejects_changed_search_snapshot(tmp_path):
    note = tmp_path / "Queue.md"
    note.write_text("Current notes", encoding="utf-8")
    result = context_api()(note_results=[{"path": str(note), "title": "Queue", "snippet": "Old notes",
        "sha256": "a" * 64, "retrieval_method": "local_search"}])
    assert result.note_refs == ()
    assert "note_snapshot_changed" in result.diagnostics


def test_explicit_attached_run_log_is_separate_from_interest():
    models = importlib.import_module("src.editorial.models")
    log = models.SourceRecord(url="file:///tmp/queue-run.log", fetched_at="2026-10-07T00:00:00Z",
        title="Queue run", kind="run_log", sha256=hashlib.sha256(b"Recovered a job").hexdigest(),
        text="Recovered a job", locations=("line:1",))
    result = context_api()(run_logs=(log,))
    assert result.experience_refs == (log,)
    assert result.interests == ()
    assert result.depth == "documented"


def test_public_experience_cannot_bypass_publication_flag():
    models = importlib.import_module("src.editorial.models")
    from pydantic import ValidationError
    private = models.UserStatement(text="I tested Queue", reference="user:20", category="experience")
    with pytest.raises(ValidationError, match="marked for publication"):
        models.UserContext(statements=(private,), published_experience=(private,))
