"""Offline frozen-output replay and audit. No fetch, provider, Git or Vault calls.

Synthetic records exercise control flow; they cannot establish model/post quality.
Historical published bodies alone cannot reproduce their generating inputs.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import time

from src.editorial.brief import build_brief
from src.editorial.draft import write_draft
from src.editorial.models import DraftPayload, DraftText, ResearchPacket, ResearchBlocked, UserContext
from src.editorial.validate import validate_draft

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("one_shot", "packet", "packet_validator", "full")
SCORES = ("technical_depth", "claim_provenance", "decision_clarity", "naturalness", "meme_fit")
CRITICAL = ("invented_experience", "untraceable_core_numbers", "unsupported_scene_descriptions", "broken_links")
UNSUPPORTED = {"unsupported_claim", "unmapped_claim", "unsupported_metric", "missing_metric_conditions",
               "unlabeled_inference", "unknown_evidence", "missing_citation"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def local_file(root, name, expected=None):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("fixture_path_outside_manifest")
    raw = path.read_bytes()
    if expected is not None and digest(raw) != expected:
        raise ValueError("frozen_file_hash_mismatch")
    return path


def check_sources(packet, root):
    for source in packet.sources:
        if source.error:
            continue
        if source.snapshot_path:
            path = local_file(root, str(source.snapshot_path))
            raw = path.with_suffix(".bin").read_bytes()
        elif source.kind in {"text", "markdown"}:
            raw = source.text.encode("utf-8")
        else:
            raise ValueError("original_source_bytes_missing")
        if digest(raw) != source.sha256:
            raise ValueError("source_snapshot_hash_mismatch")


class ReplayLLM:
    def __init__(self, responses, prompt_hashes=None):
        self.responses = responses
        self.prompt_hashes = prompt_hashes
        self.calls = []

    def generate(self, prompt):
        index = len(self.calls)
        prompt_hash = digest(prompt.encode())
        self.calls.append(prompt_hash)
        if self.prompt_hashes is not None and (index >= len(self.prompt_hashes) or self.prompt_hashes[index] != prompt_hash):
            raise ValueError("recorded_prompt_mismatch")
        if index >= len(self.responses):
            raise ValueError("frozen_responses_exhausted")
        response = self.responses[index]
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


def release_gate(runs, reviews, required_topic_types):
    """Human attestations are pending until a person supplies bound scores/checks.

    Passing this function is evidence for a cutover decision, never authorization.
    """
    if not isinstance(runs, list) or not isinstance(reviews, list) or any(not isinstance(row, dict) for row in (*runs, *reviews)):
        raise ValueError("invalid_gate_records")
    candidates = [row for row in runs if row["variant"] == "full"]
    if any(row.get("pipeline_status") == "REVIEW_READY" and not isinstance(row.get("counts"), dict) for row in candidates):
        raise ValueError("invalid_gate_counts")
    reasons = []
    def bound_identity(identity, sha256):
        return (isinstance(identity, str) and bool(identity.strip()) and identity == identity.strip()
                and isinstance(sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is not None)
    candidate_ids = [row.get("blind_id") for row in candidates]
    if any(not bound_identity(row.get("blind_id"), row.get("draft_sha256")) for row in candidates):
        reasons.append("invalid_candidate_identity")
    if len([identity for identity in candidate_ids if isinstance(identity, str)]) != len(set(identity for identity in candidate_ids if isinstance(identity, str))):
        reasons.append("duplicate_candidate_ids")
    review_ids = [row.get("id") for row in reviews]
    if any(not bound_identity(row.get("id"), row.get("draft_sha256")) for row in reviews):
        reasons.append("invalid_review_identity")
    if len([identity for identity in review_ids if isinstance(identity, str)]) != len(set(identity for identity in review_ids if isinstance(identity, str))):
        reasons.append("duplicate_review_ids")
    if not candidates:
        reasons.append("no_candidates")
    if any(row.get("synthetic", True) for row in candidates):
        reasons.append("synthetic_inputs")
    if any(row.get("reproducibility") != "RECORDED_REPLAY" for row in candidates):
        reasons.append("unreproducible_candidates")
    if any(row.get("pipeline_status") != "REVIEW_READY" or row.get("counts", {}).get("validator_issues", 1) for row in candidates):
        reasons.append("automatic_defects_or_blocked_drafts")
    if len(required_topic_types) < 2 or not set(required_topic_types).issubset({row["topic_type"] for row in candidates}):
        reasons.append("topic_types_missing")
    valid = []
    for candidate in candidates:
        if not bound_identity(candidate.get("blind_id"), candidate.get("draft_sha256")):
            reasons.append("human_reviews_pending")
            continue
        matches = [review for review in reviews if review.get("id") == candidate.get("blind_id")]
        if len(matches) != 1:
            reasons.append("human_reviews_pending")
            continue
        for review in matches:
            scores, defects = review.get("scores"), review.get("critical_defects")
            if (review.get("reviewer_kind") != "human"
                or any(not isinstance(review.get(key), str) or not review[key].strip() for key in ("reviewer", "reviewed_at", "evidence_notes"))
                or not bound_identity(review.get("id"), review.get("draft_sha256"))
                or review.get("draft_sha256") != candidate.get("draft_sha256")
                or not isinstance(scores, dict) or any(type(scores.get(key)) is not int or not 1 <= scores[key] <= 5 for key in SCORES)
                or not isinstance(defects, dict) or any(type(defects.get(key)) is not int or defects[key] < 0 for key in CRITICAL)):
                reasons.append("human_reviews_pending")
                continue
            if any(defects[key] for key in CRITICAL):
                reasons.append("critical_defects")
            valid.append((candidate["topic_type"], scores))
    means = {}
    # A strong type must not mask a weak type's scores.
    for topic_type in sorted(set(required_topic_types) | {row["topic_type"] for row in candidates}):
        selected = [scores for kind, scores in valid if kind == topic_type]
        means[topic_type] = {key: sum(row[key] for row in selected) / len(selected) if selected else None
                             for key in ("technical_depth", "decision_clarity")}
        if selected and any(value < 4 for value in means[topic_type].values()):
            reasons.append("human_score_below_threshold")
    return {"eligible": not reasons, "reasons": sorted(set(reasons)), "per_type_means": means,
            "cutover_authorized": False}


def audit_counts(draft, report):
    claims = [claim for section in draft.sections for claim in section.claims]
    unmapped = {(issue.section_id, issue.sentence) for issue in report.issues if issue.code == "unmapped_claim"}
    unsupported = {(issue.section_id, issue.sentence) for issue in report.issues if issue.code in UNSUPPORTED}
    personal = {(issue.section_id, issue.sentence) for issue in report.issues if issue.code == "invented_experience"}
    return {"unsupported_claims": len(unsupported), "first_person_without_logs": len(personal),
            "source_coverage": {"covered": sum(bool(claim.evidence_ids or claim.run_ids) for claim in claims),
                                "total": len(claims) + len(unmapped)},
            "validator_issues": len(report.issues), "issue_codes": dict(Counter(issue.code for issue in report.issues))}


def workflow_receipt(service, run_id, *, synthetic):
    """Read durable receipts; caller declares provenance, never human quality.

    Model captures contain redacted metadata. Missing usage stays unknown, and
    synthetic transport duration is not live SDK/model latency. Routine operator
    choices are separate from editorial/recovery interventions and system faults.
    """
    run = service.status(run_id)
    events = run.events
    captures, errors = [], []
    from src.llm.captures import canonical_capture_paths
    for ref in canonical_capture_paths(run.usage_refs):
        try:
            record = load_json(ref)
            if not isinstance(record, dict) or not isinstance(record.get('attempts'), list):
                raise ValueError('invalid_capture')
            captures.append(record)
        except (OSError, ValueError, TypeError):
            errors.append('capture_unavailable')
    attempts = [attempt for record in captures for attempt in record['attempts']]
    complete_usage = bool(attempts) and not errors and all(
        isinstance(attempt, dict) and isinstance(attempt.get('usage'), dict)
        and all(type(attempt['usage'].get(key)) is int and attempt['usage'][key] >= 0
                for key in ('input_tokens', 'output_tokens', 'total_tokens')) for attempt in attempts)
    tokens = {key: sum(attempt['usage'][key] for attempt in attempts)
              for key in ('input_tokens', 'output_tokens', 'total_tokens')} if complete_usage and not synthetic else None
    timings = [dict(action=e['action'], elapsed_seconds=e['elapsed_seconds'])
               for e in events if 'elapsed_seconds' in e]
    durations = [record.get('elapsed_seconds') for record in captures]
    model_seconds = sum(durations) if not synthetic and durations and not errors and all(
        type(value) in (int, float) and value >= 0 for value in durations) else None
    counts, identity = None, None
    if run.draft_id:
        artifact = service.pipeline(run).get_draft(run.draft_id)
        service.pipeline(run).store.verify(artifact)
        draft = DraftText.model_validate(load_json(artifact.content_path.parent / 'draft.json'))
        from src.editorial.models import ValidationReport
        validation = load_json(artifact.report_path)
        report = ValidationReport.model_validate({key: value for key, value in validation.items() if key in ValidationReport.model_fields})
        counts = audit_counts(draft, report)
        identity = dict(draft_id=artifact.id, content_sha256=artifact.content_sha256)
    def operator(category):
        return sum(e.get('actor') == 'operator' and e.get('category') == category for e in events)
    return dict(schema_version=1, run_id=run.id, synthetic=synthetic,
        mode='synthetic_workflow_replay' if synthetic else 'durable_run_receipts',
        measurement_scope='available_captures_only',
        captured_model_stages=sorted({record['stage'] for record in captures if isinstance(record.get('stage'), str)}),
        usage_capture_count=len(captures), usage_reference_count=len(run.usage_refs),
        status=run.status, identity=identity, delivery=run.delivery, approval=run.approval,
        publication=run.publication, counts=counts, human_scores=None,
        operator_interventions=operator('editorial_intervention') + operator('recovery'),
        editorial_interventions=operator('editorial_intervention'), recovery_interventions=operator('recovery'),
        routine_operator_actions=operator('routine'),
        system_failures=sum(e.get('actor') == 'system' and e.get('category') == 'failure' for e in events),
        events=events, capture_errors=errors,
        measurements=dict(tokens=tokens, cost=None, model_call_seconds=model_seconds, stage_timings=timings),
        limitations=['receipts_are_not_quality_scores', 'stage_timings_are_not_full_pipeline_wall_time',
            'unknown_usage_is_not_zero', 'capture_list_does_not_prove_full_run_coverage',
            'synthetic_replay_is_not_live_generation', 'no_paired_comparison'])


def evaluate_variant(case, variant, root):
    row = {"case_id": case["id"], "topic_type": case["topic_type"], "variant": variant,
        "synthetic": case.get("synthetic", False), "model": case.get("model"), "budget": case.get("budget"),
        "reproducibility": "UNREPRODUCIBLE", "pipeline_status": "NOT_RUN", "reasons": [],
        "counts": None, "human_scores": None, "meme_fit": None, "draft_sha256": None,
        "measurements": {"tokens": None, "cost": None, "model_elapsed_seconds": None, "offline_replay_seconds": None}}
    started = time.perf_counter()
    try:
        record = case["variants"][variant]
        if not case.get("model") or not case.get("budget"):
            raise ValueError("model_or_budget_missing")
        if record.get("model", case["model"]) != case["model"] or record.get("budget", case["budget"]) != case["budget"]:
            raise ValueError("model_or_budget_mismatch")
        packet_path = local_file(root, case["packet"], case.get("packet_sha256"))
        packet = ResearchPacket.model_validate(load_json(packet_path))
        check_sources(packet, root)
        brief = build_brief(packet, UserContext())
        if isinstance(brief, ResearchBlocked):
            raise ValueError("research_not_ready:" + ",".join(brief.reasons))
        response_path = local_file(root, record["responses"], record.get("responses_sha256"))
        responses = load_json(response_path)
        if not isinstance(responses, list) or not responses:
            raise ValueError("frozen_responses_missing")
        if not row["synthetic"]:
            # Recorded capture must identify its real model, sources, prompt and output bytes.
            for key in ("responses_sha256", "prompt_sha256", "prompt", "captured_at", "model", "budget"):
                if not record.get(key):
                    raise ValueError("capture_metadata_missing:" + key)
            local_file(root, record["prompt"], record["prompt_sha256"])
            if not case.get("packet_sha256"):
                raise ValueError("packet_hash_missing")
        if variant == "full":
            if not row["synthetic"] and (not record.get("call_prompt_sha256") or record["call_prompt_sha256"][0] != record["prompt_sha256"]):
                raise ValueError("frozen_initial_prompt_mismatch")
            llm = ReplayLLM(responses, None if row["synthetic"] else record.get("call_prompt_sha256", []))
            draft = write_draft(brief, packet, llm)
            row["replay_calls"] = len(llm.calls)
            row["prompt_sha256"] = llm.calls
            if len(llm.calls) > case["budget"]["max_calls"]:
                raise ValueError("declared_call_budget_exceeded")
            if draft.report and any(issue.code in {"draft_generation_failed", "draft_revision_failed"} for issue in draft.report.issues):
                raise ValueError("recorded_replay_failed")
            row["pipeline_status"] = draft.report.status
            row["execution"] = "draft_validator_targeted_repair;media_omitted"
        else:
            payload = DraftPayload.model_validate(responses[0])
            draft = DraftText(**payload.model_dump(), packet=packet)
            row["pipeline_status"] = "NOT_VALIDATED"
            row["execution"] = "frozen_output_audit;generation_not_reexecuted"
        audit = validate_draft(draft, packet, brief)
        if variant == "packet_validator":
            row["pipeline_status"] = audit.status
        row.update(reproducibility="SYNTHETIC_REPLAY" if row["synthetic"] else "RECORDED_REPLAY",
                   counts=audit_counts(draft, audit), draft_sha256=digest(draft.content.encode()),
                   source_packet_sha256=digest(packet_path.read_bytes()), response_sha256=digest(response_path.read_bytes()))
        row["audit"] = audit.model_dump(mode="json")
        row["measurements"]["offline_replay_seconds"] = time.perf_counter() - started
        # This time measures Python replay/audit only; no model latency or cost is inferred.
        return row, draft.content, packet.model_dump(mode="json")
    except (OSError, ValueError, KeyError, TypeError) as error:
        row["reasons"] = [str(error) if isinstance(error, ValueError) else type(error).__name__]
        return row, None, None


def new_output_directory(output_root):
    output_root = Path(output_root)
    # Refuse live destinations even if the caller accidentally supplies one.
    from config import settings
    target = output_root.resolve()
    if any(target == root.resolve() or target.is_relative_to(root.resolve()) for root in (settings.blog_repo_path, settings.obsidian_vault_path)):
        raise ValueError("evaluation_output_must_not_be_blog_or_vault")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("evaluation_output_must_be_empty; preserve existing reviews")
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root


def run_evaluation(manifest_path, output_root, *, baseline_path=ROOT / "tests/fixtures/baseline-manifest.json", human_reviews=None, seed=7):
    manifest_path = Path(manifest_path)
    output_root = new_output_directory(output_root)
    blind = output_root / "blind"
    blind.mkdir()
    cases = load_json(manifest_path)["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate_case_ids")
    runs, material = [], []
    for case in cases:
        for variant in VARIANTS:
            row, content, packet = evaluate_variant(case, variant, manifest_path.parent)
            runs.append(row)
            if content is not None:
                material.append((row, content, packet))
    random.Random(seed).shuffle(material)
    keys, pending = {}, []
    for index, (row, content, packet) in enumerate(material, 1):
        label = f"D{index:03d}"
        row["blind_id"] = label
        keys[label] = {"case_id": row["case_id"], "variant": row["variant"], "draft_sha256": row["draft_sha256"]}
        # Bind scores to exact emitted bytes, including on Windows.
        (blind / (label + ".md")).write_bytes(content.encode("utf-8"))
        (blind / (label + "-sources.json")).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.append({"id": label, "draft_sha256": row["draft_sha256"], "reviewer_kind": "human",
            "reviewer": None, "reviewed_at": None, "scores": None, "critical_defects": None, "evidence_notes": None})
    (blind / "scores.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pending), encoding="utf-8")
    (blind / "README.md").write_text(
        "# Anonymous review packet\n\nRead each entire draft and its sources; variant labels are withheld. "
        "Do not open parent report/key files before scoring. Blinding removes labels, not textual clues.\n\n"
        "The bundled diagnostic is synthetic and cannot establish real article quality. "
        "No scores have been supplied. Agents are not human raters.\n\n"
        "Score each dimension 1-5: technical depth (traceable mechanism, conditions, failures); "
        "claim provenance (inspectable locations and attribution); decision clarity (alternatives, constraints, reversal); "
        "naturalness (direct prose); meme fit (verified scene, alt and placement, including appropriate omission). "
        "1 = absent/unsupported, 3 = partial, 5 = complete and inspectable.\n\n"
        "Record passage/location evidence and counts for invented_experience, untraceable_core_numbers, "
        "unsupported_scene_descriptions and broken_links. Unknown counts stay null and block release. "
        "Supply your identity/date and retain individual scores in scores.jsonl. Do not replace a human review with model grading.\n",
        encoding="utf-8")
    (output_root / "private-key.json").write_text(json.dumps(keys, indent=2), encoding="utf-8")
    baselines = [{"path": post["repository_relative_path"], "sha256": post["sha256"], "topic_type": post["topic_type"],
        "status": "UNREPRODUCIBLE", "variants": list(VARIANTS),
        "reasons": ["historical_source_snapshots_missing", "historical_model_responses_missing", "historical_prompt_and_budget_missing"]}
        for post in load_json(baseline_path)["posts"]]
    reviews = [] if human_reviews is None else [json.loads(line) for line in Path(human_reviews).read_text(encoding="utf-8").splitlines() if line.strip()]
    required_types = sorted({post["topic_type"] for post in baselines})
    result = {"schema_version": 1, "manifest_sha256": digest(manifest_path.read_bytes()),
        "mode": "offline_frozen_response_replay", "limitations": ["no_live_generation", "synthetic_diagnostics_are_not_quality_evidence",
        "offline_time_is_not_model_time", "human_scores_pending_unless_supplied", "source_coverage_counts_mappings_not_truth",
        "historical_topic_ablation_unreproducible", "full_replay_omits_media;media_needs_separate_review"],
        "runs": runs, "historical_baselines": baselines, "human_reviews": reviews, "required_topic_types": required_types,
        "release_gate": release_gate(runs, reviews, set(required_types))}
    (output_root / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/fixtures/evaluation/cases.json")
    parser.add_argument("--output", type=Path, required=True, help="new local directory; never a blog/Vault")
    parser.add_argument("--human-reviews", type=Path)
    parser.add_argument('--workflow-run', help='read durable run receipts instead of variant replay')
    parser.add_argument('--workflow-root', type=Path)
    parser.add_argument('--synthetic-replay', action='store_true', help='declare injected/synthetic run provenance')
    args = parser.parse_args(argv)
    if args.workflow_run:
        if args.workflow_root is None or args.human_reviews:
            parser.error('workflow receipts require --workflow-root and cannot generate human reviews')
        from src.workflow.service import WorkflowService
        result = workflow_receipt(WorkflowService(args.workflow_root), args.workflow_run, synthetic=args.synthetic_replay)
        output = new_output_directory(args.output)
        path = output / 'report.json'
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'run_id': result['run_id'], 'status': result['status'], 'report': str(path)}, ensure_ascii=False))
        return 0
    if args.workflow_root or args.synthetic_replay:
        parser.error('--workflow-root/--synthetic-replay require --workflow-run')
    result = run_evaluation(args.manifest, args.output, human_reviews=args.human_reviews)
    print(json.dumps({"runs": len(result["runs"]), "historical_unreproducible": len(result["historical_baselines"]),
                      "release_gate": result["release_gate"], "report": str(args.output / "report.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
