"""Offline replay evidence must never become a human quality certification."""
import asyncio
import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from drafting_fixtures import drafting_input, response
from src.editorial.pipeline import approval_callback
from test_pipeline import setup_pipeline


def evaluator():
    try:
        return importlib.import_module("scripts.evaluate_drafts")
    except ModuleNotFoundError:
        pytest.fail("offline ablation evaluator is missing")


def replay_case(tmp_path, drafting_input):
    packet, _ = drafting_input
    (tmp_path / "packet.json").write_text(packet.model_dump_json(), encoding="utf-8")
    clean = response()
    bad = copy.deepcopy(clean)
    bad["sections"][0]["text"] += "\n\n직접 써봤다. It is 73% faster."
    repair = {"sections": [clean["sections"][0]]}
    variants = {}
    for variant in ("one_shot", "packet", "packet_validator", "full"):
        data = [bad, repair] if variant == "full" else [bad]
        path = tmp_path / (variant + "-response.json")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        variants[variant] = {"responses": path.name}
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({"cases": [{"id": "queue-diagnostic", "topic_type": "library/tool",
        "synthetic": True, "model": "hand-authored-fixture-no-model", "budget": {"max_calls": 3},
        "packet": "packet.json", "variants": variants}]}), encoding="utf-8")
    return manifest


def test_replay_reports_raw_defects_repairs_and_unknown_cost(tmp_path, drafting_input):
    manifest = replay_case(tmp_path, drafting_input)
    result = evaluator().run_evaluation(manifest, tmp_path / "output")
    runs = {row["variant"]: row for row in result["runs"]}
    assert set(runs) == {"one_shot", "packet", "packet_validator", "full"}
    assert runs["packet"]["pipeline_status"] == "NOT_VALIDATED"
    assert runs["packet_validator"]["pipeline_status"] == "NEEDS_REVISION"
    assert runs["packet"]["counts"]["unsupported_claims"] >= 1
    assert runs["packet"]["counts"]["first_person_without_logs"] == 1
    assert runs["packet"]["counts"]["source_coverage"] == {"covered": 4, "total": 6}
    assert runs["full"]["counts"]["first_person_without_logs"] == 0
    assert runs["full"]["pipeline_status"] == "REVIEW_READY"
    assert runs["full"]["counts"]["unsupported_claims"] == 0
    assert runs["full"]["measurements"]["tokens"] is None
    assert runs["full"]["measurements"]["cost"] is None
    assert runs["full"]["measurements"]["model_elapsed_seconds"] is None
    assert runs["full"]["human_scores"] is None
    assert runs["full"]["meme_fit"] is None
    assert result["release_gate"]["eligible"] is False
    assert "synthetic_inputs" in result["release_gate"]["reasons"]
    baseline = result["historical_baselines"]
    assert len(baseline) == 8
    assert all(row["status"] == "UNREPRODUCIBLE" for row in baseline)
    assert all(len(row["variants"]) == 4 for row in baseline)


def test_blind_packet_hides_variant_keys_and_binds_hashes(tmp_path, drafting_input):
    result = evaluator().run_evaluation(replay_case(tmp_path, drafting_input), tmp_path / "out")
    blind = tmp_path / "out" / "blind"
    keys = json.loads((tmp_path / "out" / "private-key.json").read_text())
    assert len(keys) == 4
    pending = [json.loads(line) for line in (blind / "scores.jsonl").read_text().splitlines()]
    assert len(pending) == 4 and all(row["scores"] is None for row in pending)
    assert all(row["critical_defects"] is None for row in pending)
    assert all(row["reviewer_kind"] == "human" and row["reviewer"] is None for row in pending)
    for row in pending:
        assert "variant" not in row and "case_id" not in row
        assert (blind / (row["id"] + ".md")).is_file()
        assert row["draft_sha256"] == keys[row["id"]]["draft_sha256"]
        assert hashlib.sha256((blind / (row["id"] + ".md")).read_bytes()).hexdigest() == row["draft_sha256"]
    assert "one_shot" not in (blind / "README.md").read_text()
    assert result["release_gate"]["eligible"] is False


@pytest.mark.parametrize("fault", ["missing_response", "wrong_model", "bad_hash", "missing_packet"])
def test_incomplete_or_mismatched_replay_stays_unreproducible(tmp_path, drafting_input, fault):
    manifest = replay_case(tmp_path, drafting_input)
    data = json.loads(manifest.read_text())
    case = data["cases"][0]
    if fault == "missing_response":
        (tmp_path / "full-response.json").unlink()
    elif fault == "wrong_model":
        case["variants"]["full"]["model"] = "different-model"
    elif fault == "bad_hash":
        case["variants"]["full"]["responses_sha256"] = "0" * 64
    else:
        (tmp_path / "packet.json").unlink()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = evaluator().run_evaluation(manifest, tmp_path / "out")
    full = next(row for row in result["runs"] if row["variant"] == "full")
    assert full["reproducibility"] == "UNREPRODUCIBLE"
    assert full["counts"] is None and full["human_scores"] is None
    assert not result["release_gate"]["eligible"]


def test_gate_rejects_agent_scores_and_missing_topic_types(tmp_path, drafting_input):
    result = evaluator().run_evaluation(replay_case(tmp_path, drafting_input), tmp_path / "out")
    candidate = next(row for row in result["runs"] if row["variant"] == "full")
    candidate.update(synthetic=False, reproducibility="RECORDED_REPLAY")
    review = {"id": candidate["blind_id"], "draft_sha256": candidate["draft_sha256"],
        "reviewer_kind": "agent", "reviewer": "Codex", "reviewed_at": "2026-10-07",
        "scores": {"technical_depth": 5, "claim_provenance": 5, "decision_clarity": 5,
                   "naturalness": 5, "meme_fit": 5},
        "critical_defects": {"invented_experience": 0, "untraceable_core_numbers": 0,
            "unsupported_scene_descriptions": 0, "broken_links": 0}, "evidence_notes": "checked"}
    gate = evaluator().release_gate([candidate], [review], {"library/tool", "protocol/standard"})
    assert not gate["eligible"] and "human_reviews_pending" in gate["reasons"]
    review["reviewer_kind"] = "human"
    gate = evaluator().release_gate([candidate], [review], {"library/tool", "protocol/standard"})
    assert not gate["eligible"] and "topic_types_missing" in gate["reasons"]


def test_shadow_shows_complete_review_but_blocks_old_publish_callbacks(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    app = TrendBotApp(pipeline=pipeline)
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    asyncio.run(app.send_review(bot, 7, artifact))
    assert "shadow" in bot.send_message.call_args.kwargs["text"].lower()
    assert len(bot.send_document.call_args_list) >= 2
    query = SimpleNamespace(data=approval_callback(artifact), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert pipeline.get_draft(artifact.id).status == "APPROVED"
    assert not query.edit_message_text.call_args.kwargs.get("reply_markup")
    query.data = "p:" + approval_callback(artifact)[2:]
    asyncio.run(app.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
    assert "shadow" in query.edit_message_text.call_args.args[0].lower()
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


def test_disabling_shadow_alone_still_blocks_publication(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "telegram_chat_id", "7")
    monkeypatch.setattr(settings, "editorial_shadow_mode", False, raising=False)
    pipeline, topic_id = setup_pipeline(tmp_path, drafting_input)
    artifact = asyncio.run(pipeline.generate(topic_id))
    pipeline.bind_reviewer(artifact.id, "7", "7")
    artifact = pipeline.approve(artifact.id, artifact.content_sha256)
    query = SimpleNamespace(data="p:" + approval_callback(artifact)[2:], answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=7), from_user=SimpleNamespace(id=7))
    asyncio.run(TrendBotApp(pipeline=pipeline).handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))
    assert "cutover" in query.edit_message_text.call_args.args[0].lower()
    assert not settings.blog_repo_path.exists() and not settings.obsidian_vault_path.exists()


def test_gate_requires_zero_defects_and_passes_each_topic_type(tmp_path, drafting_input):
    result = evaluator().run_evaluation(replay_case(tmp_path, drafting_input), tmp_path / "out")
    candidate = next(row for row in result["runs"] if row["variant"] == "full")
    candidate.update(synthetic=False, reproducibility="RECORDED_REPLAY")
    second = copy.deepcopy(candidate)
    second.update(case_id="other-case", topic_type="protocol/standard", blind_id="D999")
    reviews = [{"id": row["blind_id"], "draft_sha256": row["draft_sha256"], "reviewer_kind": "human",
        "reviewer": "Test-only human attestation stub", "reviewed_at": "2026-10-07",
        "scores": dict.fromkeys(("technical_depth", "claim_provenance", "decision_clarity", "naturalness", "meme_fit"), 4),
        "critical_defects": dict.fromkeys(("invented_experience", "untraceable_core_numbers", "unsupported_scene_descriptions", "broken_links"), 0),
        "evidence_notes": "Test input only; no real review occurred"} for row in (candidate, second)]
    types = {"library/tool", "protocol/standard"}
    assert evaluator().release_gate([candidate, second], reviews, types)["eligible"]
    reviews[1]["scores"]["technical_depth"] = 3
    assert not evaluator().release_gate([candidate, second], reviews, types)["eligible"]
    reviews[1]["scores"]["technical_depth"] = 4
    reviews[1]["critical_defects"]["broken_links"] = 1
    assert not evaluator().release_gate([candidate, second], reviews, types)["eligible"]
    reviews[1]["critical_defects"]["broken_links"] = None
    assert not evaluator().release_gate([candidate, second], reviews, types)["eligible"]


def test_cutover_authorization_requires_inspectable_gate_report(tmp_path, drafting_input, monkeypatch):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "editorial_shadow_mode", False)
    monkeypatch.setattr(settings, "editorial_cutover_authorized", True)
    monkeypatch.setattr(settings, "editorial_quality_gate_report", tmp_path / "missing.json")
    pipeline, _ = setup_pipeline(tmp_path, drafting_input)
    app = TrendBotApp(pipeline=pipeline)
    assert "unavailable" in app.publication_block_reason()
    settings.editorial_quality_gate_report.write_text(json.dumps({"release_gate": {"eligible": True}}))
    assert "invalid" in app.publication_block_reason()


def test_evaluation_preserves_existing_review_and_rejects_live_output(tmp_path, drafting_input):
    manifest = replay_case(tmp_path, drafting_input)
    evaluator().run_evaluation(manifest, tmp_path / "out")
    with pytest.raises(ValueError, match="empty"):
        evaluator().run_evaluation(manifest, tmp_path / "out")
    for root in (settings.blog_repo_path, settings.obsidian_vault_path):
        with pytest.raises(ValueError, match="blog_or_vault"):
            evaluator().run_evaluation(manifest, root)
        assert not root.exists()


def test_bundled_diagnostic_cli_produces_blocked_report_and_pending_packet(tmp_path, capsys):
    assert evaluator().main(["--output", str(tmp_path / "evaluation")]) == 0
    report = json.loads((tmp_path / "evaluation/report.json").read_text(encoding="utf-8"))
    assert len(report["runs"]) == 4
    full = next(row for row in report["runs"] if row["variant"] == "full")
    assert full["pipeline_status"] == "REVIEW_READY"
    assert full["counts"]["unsupported_claims"] == 0
    assert not report["release_gate"]["eligible"]
    assert len(report["historical_baselines"]) == 8
    assert "historical_unreproducible" in capsys.readouterr().out


@pytest.mark.parametrize("data", [[], {"runs": [None], "human_reviews": [], "required_topic_types": []},
    {"runs": [{"variant": "full", "pipeline_status": "REVIEW_READY", "counts": None}],
     "human_reviews": [], "required_topic_types": []}])
def test_malformed_gate_is_visible_blocked_status(tmp_path, drafting_input, monkeypatch, data):
    from src.bot.telegram_bot import TrendBotApp
    monkeypatch.setattr(settings, "editorial_shadow_mode", False)
    monkeypatch.setattr(settings, "editorial_cutover_authorized", True)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(settings, "editorial_quality_gate_report", path)
    pipeline, _ = setup_pipeline(tmp_path, drafting_input)
    assert "invalid" in TrendBotApp(pipeline=pipeline).publication_block_reason()


def test_recorded_full_capture_rejects_inconsistent_frozen_prompt(tmp_path, drafting_input):
    """Test-only recording format: the provider remains a supplied fake response."""
    from src.editorial.draft import write_draft
    prompts = []
    class Recorder:
        def generate(self, prompt):
            prompts.append(prompt)
            return json.dumps(response())
    packet, brief = drafting_input
    assert write_draft(brief, packet, Recorder()).report.status == "REVIEW_READY"
    manifest = replay_case(tmp_path, drafting_input)
    data = json.loads(manifest.read_text())
    case = data["cases"][0]
    case.update(synthetic=False, model="test-only-capture-format", packet_sha256=hashlib.sha256((tmp_path / "packet.json").read_bytes()).hexdigest())
    (tmp_path / "recorded-prompt.txt").write_bytes(prompts[0].encode())
    (tmp_path / "full-response.json").write_text(json.dumps([response()]), encoding="utf-8")
    for record in case["variants"].values():
        record.update(model=case["model"], budget=case["budget"], prompt="recorded-prompt.txt",
            prompt_sha256=hashlib.sha256(prompts[0].encode()).hexdigest(), captured_at="2026-10-07T00:00:00Z",
            call_prompt_sha256=[hashlib.sha256(prompt.encode()).hexdigest() for prompt in prompts],
            responses_sha256=hashlib.sha256((tmp_path / record["responses"]).read_bytes()).hexdigest())
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = evaluator().run_evaluation(manifest, tmp_path / "valid")
    full = next(row for row in result["runs"] if row["variant"] == "full")
    assert full["reproducibility"] == "RECORDED_REPLAY"
    # Self-consistent file/hash but incompatible with the prompt that generated the response.
    (tmp_path / "changed-prompt.txt").write_bytes(b"different historical prompt")
    case["variants"]["full"].update(prompt="changed-prompt.txt", prompt_sha256=hashlib.sha256(b"different historical prompt").hexdigest())
    manifest.write_text(json.dumps(data), encoding="utf-8")
    changed = evaluator().run_evaluation(manifest, tmp_path / "changed")
    full = next(row for row in changed["runs"] if row["variant"] == "full")
    assert full["reproducibility"] == "UNREPRODUCIBLE"


def gate_candidate(kind, identity, sha256="0" * 64):
    return {"variant": "full", "case_id": identity, "topic_type": kind, "blind_id": identity,
        "draft_sha256": sha256, "synthetic": False, "reproducibility": "RECORDED_REPLAY",
        "pipeline_status": "REVIEW_READY", "counts": {"validator_issues": 0}}


def gate_review(candidate, depth=4):
    return {"id": candidate.get("blind_id"), "draft_sha256": candidate.get("draft_sha256"),
        "reviewer_kind": "human", "reviewer": "test-only attestation stub", "reviewed_at": "2026-10-07",
        "scores": {"technical_depth": depth, "claim_provenance": 4, "decision_clarity": depth,
            "naturalness": 4, "meme_fit": 4},
        "critical_defects": dict.fromkeys(("invented_experience", "untraceable_core_numbers", "unsupported_scene_descriptions", "broken_links"), 0),
        "evidence_notes": "test input only; no human review occurred"}


@pytest.mark.parametrize("fault", ["missing_id", "null_id", "blank_id", "missing_hash", "null_hash", "bad_hash", "duplicate_candidate", "duplicate_review"])
def test_gate_rejects_unbound_or_duplicate_candidate_reviews(fault):
    candidates = [gate_candidate("library/tool", "D001"), gate_candidate("protocol/standard", "D002")]
    if fault == "missing_id":
        for row in candidates:
            row.pop("blind_id")
    elif fault == "missing_hash":
        for row in candidates:
            row.pop("draft_sha256")
    elif fault in {"null_id", "blank_id", "duplicate_candidate"}:
        for row in candidates:
            row["blind_id"] = {"null_id": None, "blank_id": " ", "duplicate_candidate": "D001"}[fault]
    elif fault in {"null_hash", "bad_hash"}:
        for row in candidates:
            row["draft_sha256"] = None if fault == "null_hash" else "not-a-sha256"
    reviews = [gate_review(row) for row in candidates]
    if fault == "duplicate_review":
        reviews.append(copy.deepcopy(reviews[0]))
    gate = evaluator().release_gate(candidates, reviews, {"library/tool", "protocol/standard"})
    assert not gate["eligible"]


def test_gate_scores_included_types_outside_required_coverage():
    candidates = [gate_candidate("library/tool", "D001"), gate_candidate("protocol/standard", "D002"),
                  gate_candidate("paper/benchmark", "D003")]
    reviews = [gate_review(row, 1 if row["topic_type"] == "paper/benchmark" else 5) for row in candidates]
    gate = evaluator().release_gate(candidates, reviews, {"library/tool", "protocol/standard"})
    assert not gate["eligible"]
    assert gate["per_type_means"]["paper/benchmark"]["technical_depth"] == 1
