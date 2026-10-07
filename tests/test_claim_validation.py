"""Claim, metric, experience, citation and frontmatter rejection gates."""
import importlib

import pytest

from drafting_fixtures import drafting_input, response
from src.editorial.models import SourceRecord, UserContext


def validate(payload, drafting_input):
    try:
        draft = importlib.import_module("src.editorial.draft").DraftText.model_validate(payload)
        validator = importlib.import_module("src.editorial.validate").validate_draft
    except ModuleNotFoundError:
        pytest.fail("Unit 4 claim validator is missing")
    return validator(draft, *drafting_input)


def codes(report):
    return {issue.code for issue in report.issues}


def replace_fact(payload, sentence, *, evidence_ids=("E1",), kind="source_claim"):
    payload["sections"][0]["text"] = sentence
    payload["sections"][0]["claims"] = [{"sentence": sentence, "evidence_ids": evidence_ids, "kind": kind, "role": "mechanism"}]
    return payload


def test_source_attribution_and_conditional_decision_pass(drafting_input):
    report = validate(response(), drafting_input)
    assert report.status == "REVIEW_READY"
    assert report.static_passed and report.grounding_passed


@pytest.mark.parametrize("sentence", ["직접 써보니 빨랐다.", "내가 배포해서 장애를 없앴다.", "I tested PageIndex in production."])
def test_pageindex_cannot_invent_personal_use(drafting_input, sentence):
    report = validate(replace_fact(response(), sentence), drafting_input)
    assert report.status == "NEEDS_REVISION"
    assert "invented_experience" in codes(report)


def test_citing_real_id_does_not_support_invented_claim(drafting_input):
    payload = replace_fact(response(), "The queue guarantees zero data loss.")
    report = validate(payload, drafting_input)
    assert "unsupported_claim" in codes(report)
    assert not report.grounding_passed


def test_unmapped_confident_claim_is_rejected(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\nPageIndex guarantees zero data loss."
    report = validate(payload, drafting_input)
    assert "unmapped_claim" in codes(report)


def test_claim_map_cannot_point_to_absent_sentence(drafting_input):
    payload = response()
    payload["sections"][0]["claims"][0]["sentence"] = "Persist the request before acknowledgement."
    assert "claim_span_mismatch" in codes(validate(payload, drafting_input))


@pytest.mark.parametrize("sentence", ["성능이 90% 좋아졌다.", "PageIndex is ten times faster.", "추론: latency improves by 99%."])
def test_numeric_outcomes_without_measurement_context_are_rejected(drafting_input, sentence):
    report = validate(replace_fact(response(), sentence), drafting_input)
    assert "unsupported_metric" in codes(report)


def test_unknown_evidence_and_unresolved_citation_block(drafting_input):
    payload = replace_fact(response(), "원문은 보존한다. [E99](https://example.invalid/missing)", evidence_ids=("E99",))
    report = validate(payload, drafting_input)
    assert {"unknown_evidence", "unresolved_citation"} <= codes(report)


@pytest.mark.parametrize("placeholder", ["[citation needed]", "[MEME_1]", "[출처 필요]", "[1]"])
def test_placeholder_citations_are_not_drafts(drafting_input, placeholder):
    payload = response()
    payload["sections"][0]["text"] += "\n" + placeholder
    assert "placeholder_citation" in codes(validate(payload, drafting_input))


def test_link_cannot_redirect_a_valid_evidence_id(drafting_input):
    payload = response()
    payload["sections"][0]["text"] = payload["sections"][0]["text"].replace("https://example.invalid/pageindex", "https://evil.invalid/")
    payload["sections"][0]["claims"][0]["sentence"] = payload["sections"][0]["text"].split("\n\n")[-1]
    assert "unresolved_citation" in codes(validate(payload, drafting_input))


@pytest.mark.parametrize("bad", [
    "---\nlayout: page\ntitle: test\ndate: 2026-10-07\n---",
    "---\nlayout: post\ntitle: [wrong, type]\ndate: 2026-10-07\n---",
    "---\nlayout: post\ntitle: test\ndate: yesterday\n---",
    "---\nlayout: post\nlayout: page\ntitle: test\ndate: 2026-10-07\n---",
    "---\nlayout: post\ntitle: test\ndate: 2026-10-07\ncategories: wrong\n---",
    "no frontmatter",
])
def test_invalid_jekyll_frontmatter_is_rejected(drafting_input, bad):
    payload = response()
    payload["frontmatter"] = bad
    assert "invalid_frontmatter" in codes(validate(payload, drafting_input))


def test_absent_meaningful_alternative_and_decision_block(drafting_input):
    payload = response()
    payload["sections"] = payload["sections"][:1]
    assert {"missing_alternative", "missing_decision"} <= codes(validate(payload, drafting_input))


def test_role_label_cannot_replace_alternative_content(drafting_input):
    payload = response()
    payload["sections"][1] = {"id": "compare", **{k: v for k, v in response()["sections"][0].items() if k != "id"}}
    payload["sections"][1]["claims"][0]["role"] = "alternative"
    assert "missing_alternative" in codes(validate(payload, drafting_input))


def test_explicit_inspectable_run_log_allows_first_person(drafting_input, tmp_path):
    packet, brief = drafting_input
    sentence = "직접 써보니 요청을 보존했다."
    path = tmp_path / "run.log"
    path.write_text(sentence, encoding="utf-8")
    import hashlib
    run = SourceRecord(url=path.as_uri(), title="Explicit supplied run", kind="run_log",
        text=sentence, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        locations=("line:1",), snapshot_path=path, fetched_at="2026-10-07T00:00:00Z")
    brief = brief.model_copy(update={"user_context": UserContext(experience_refs=(run,))})
    payload = response()
    payload["sections"].append({"id": "run", "text": sentence, "claims": [{"sentence": sentence,
        "kind": "measurement", "evidence_ids": [], "run_ids": ["R1"], "role": "context"}]})
    assert validate(payload, (packet, brief)).status == "REVIEW_READY"
    path.write_text("changed record", encoding="utf-8")
    assert "run_snapshot_changed" in codes(validate(payload, (packet, brief)))


def test_author_quoted_first_person_does_not_become_our_experience(drafting_input):
    packet, brief = drafting_input
    claim = packet.claims[0].model_copy(update={"text": "I tested the queue and persisted the request before acknowledgement."})
    source = packet.sources[0].model_copy(update={"text": packet.sources[0].text.replace(packet.claims[0].text, claim.text)})
    packet = packet.model_copy(update={"sources": (source,), "claims": (claim, *packet.claims[1:])})
    brief = brief.model_copy(update={"key_mechanism": claim.text, "evidence": packet.claims})
    sentence = '원저자는 “I tested the queue and persisted the request before acknowledgement.”라고 설명한다. [E1](https://example.invalid/pageindex)'
    assert "invented_experience" not in codes(validate(replace_fact(response(), sentence), (packet, brief)))


def test_source_metric_retains_original_author_and_conditions():
    from test_brief import packet as research_packet
    from src.editorial.brief import build_brief
    from src.editorial.models import MetricContext
    research = research_packet("paper", metric=True)
    metric = research.claims[-1].model_copy(update={"metric_context": MetricContext(value=25, unit="%",
        target="throughput", baseline="in-memory queue", conditions="one worker with the same workload")})
    research = research.model_copy(update={"claims": (*research.claims[:-1], metric)})
    brief = build_brief(research, UserContext())
    payload = response()
    # Same mechanisms, alternative and constraints but references use this packet.
    for section in payload["sections"]:
        section["text"] = section["text"].replace("https://example.invalid/pageindex", "https://example.invalid/source")
        for claim in section["claims"]:
            claim["sentence"] = claim["sentence"].replace("https://example.invalid/pageindex", "https://example.invalid/source")
    result_claims = []
    for idx in (1, 4, 5):
        eid = f"E{idx+1}"
        sentence = f'원저자는 “{research.claims[idx].text}”라고 보고한다. [{eid}](https://example.invalid/source)'
        result_claims.append({"sentence": sentence, "kind": "source_claim", "evidence_ids": [eid], "role": "context"})
    payload["sections"].append({"id": "results", "text": "\n\n".join(c["sentence"] for c in result_claims), "claims": result_claims})
    assert validate(payload, (research, brief)).status == "REVIEW_READY"
    payload["sections"][-1]["text"] = result_claims[-1]["sentence"]
    payload["sections"][-1]["claims"] = [result_claims[-1]]
    assert "missing_metric_conditions" in codes(validate(payload, (research, brief)))


def test_citation_ids_must_match_the_sentence_claim_map(drafting_input):
    payload = response()
    sentence = payload["sections"][0]["claims"][0]["sentence"].replace("[E1]", "[E2]")
    payload["sections"][0]["text"] = sentence
    payload["sections"][0]["claims"][0]["sentence"] = sentence
    assert "citation_evidence_mismatch" in codes(validate(payload, drafting_input))


def test_source_claim_requires_its_reader_visible_citation(drafting_input):
    payload = response()
    sentence = '원문은 “Persist the request before acknowledgement.”라고 설명한다.'
    payload["sections"][0]["text"] = sentence
    payload["sections"][0]["claims"][0]["sentence"] = sentence
    assert "citation_evidence_mismatch" in codes(validate(payload, drafting_input))


def test_fact_hidden_in_question_cannot_skip_claim_map(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\nPageIndex supports infinite storage?"
    assert "unmapped_claim" in codes(validate(payload, drafting_input))


def test_inference_cannot_state_a_guaranteed_fact(drafting_input):
    payload = replace_fact(response(), "추론: 조건에 맞으면 PageIndex는 데이터 보존을 보장한다.", kind="inference")
    assert "unsupported_claim" in codes(validate(payload, drafting_input))


def test_factual_heading_cannot_bypass_claim_map(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\n## PageIndex guarantees zero data loss"
    assert "unmapped_claim" in codes(validate(payload, drafting_input))


def test_conditional_personal_judgment_is_not_invented_experience(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\n나는 durable storage가 필요하다면 이 선택을 고려한다."
    assert validate(payload, drafting_input).status == "REVIEW_READY"


def test_first_person_project_deployment_is_experience_not_judgment(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\n내 프로젝트에 도입했더니 장애가 사라졌다."
    assert "invented_experience" in codes(validate(payload, drafting_input))


def test_source_quote_cannot_smuggle_additional_fact(drafting_input):
    payload = response()
    sentence = payload["sections"][0]["claims"][0]["sentence"]
    sentence = sentence.replace('라고 설명한다.', '라고 설명한다. 모든 장애를 제거한다.')
    assert "unsupported_claim" in codes(validate(replace_fact(payload, sentence), drafting_input))


def test_conditional_editorial_prose_cannot_smuggle_a_factual_guarantee(drafting_input):
    payload = response()
    payload["sections"][0]["text"] += "\n\n조건에 맞으면 이 도구를 선택하고 데이터 무손실을 보장한다."
    assert "unmapped_claim" in codes(validate(payload, drafting_input))


def test_own_numeric_run_requires_source_supported_context(drafting_input, tmp_path):
    from src.editorial.models import EvidenceClaim, MetricContext
    import hashlib
    packet, brief = drafting_input
    sentence = "직접 측정한 latency는 25ms였다."
    conditions = "baseline: in-memory queue; conditions: one worker with the same workload"
    log = sentence + "\n" + conditions
    path = tmp_path / "numeric-run.log"
    path.write_text(log, encoding="utf-8")
    run = SourceRecord(url=path.as_uri(), title="Supplied measurement", kind="run_log", text=log,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), locations=("line:1", "line:2"),
        snapshot_path=path, fetched_at="2026-10-07T00:00:00Z")
    metric = MetricContext(value=25, unit="ms", target="latency", baseline="in-memory queue",
        conditions="one worker with the same workload", run_record=run)
    claim = EvidenceClaim(text=sentence, kind="measurement", metric_context=metric)
    packet = packet.model_copy(update={"claims": (*packet.claims, claim)})
    brief = brief.model_copy(update={"user_context": UserContext(experience_refs=(run,))})
    payload = response()
    mapped = {"sentence": sentence, "kind": "measurement", "evidence_ids": ["E4"], "run_ids": ["R1"], "role": "context"}
    context_mapping = {"sentence": conditions, "kind": "measurement", "evidence_ids": [], "run_ids": ["R1"], "role": "context"}
    payload["sections"].append({"id": "run", "text": sentence + "\n" + conditions, "claims": [mapped, context_mapping]})
    assert validate(payload, (packet, brief)).status == "REVIEW_READY"
    invented = metric.model_copy(update={"baseline": "invented queue"})
    packet = packet.model_copy(update={"claims": (*packet.claims[:-1], claim.model_copy(update={"metric_context": invented}))})
    assert "unsupported_metric" in codes(validate(payload, (packet, brief)))


@pytest.mark.parametrize("sentence", ["추론: latency가 중요하다면 25ms였다.", "추론: 조건이 맞으면 latency is halved.", "추론: 조건이 맞으면 두배 빨라진다."])
def test_compact_units_and_worded_ratios_cannot_evade_numeric_gate(drafting_input, sentence):
    report = validate(replace_fact(response(), sentence, kind="inference"), drafting_input)
    assert "unsupported_metric" in codes(report)


def test_run_prose_must_match_the_inspected_log_bytes(drafting_input, tmp_path):
    import hashlib
    packet, brief = drafting_input
    path = tmp_path / "real-run.log"
    path.write_text("No personal use was recorded.", encoding="utf-8")
    sentence = "직접 써보니 요청을 보존했다."
    run = SourceRecord(url=path.as_uri(), title="Supplied log", kind="run_log", text=sentence,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), locations=("line:1",),
        snapshot_path=path, fetched_at="2026-10-07T00:00:00Z")
    brief = brief.model_copy(update={"user_context": UserContext(experience_refs=(run,))})
    payload = response()
    payload["sections"].append({"id": "run", "text": sentence, "claims": [{"sentence": sentence,
        "kind": "measurement", "evidence_ids": [], "run_ids": ["R1"], "role": "context"}]})
    assert "run_text_mismatch" in codes(validate(payload, (packet, brief)))


def test_source_claim_cannot_be_promoted_to_our_measurement(drafting_input):
    payload = response()
    payload["sections"][0]["claims"][0]["kind"] = "measurement"
    assert "claim_kind_mismatch" in codes(validate(payload, drafting_input))


def test_confident_factual_title_is_not_unsupported_marketing(drafting_input):
    payload = response()
    payload["frontmatter"] = payload["frontmatter"].replace("PageIndex 채택 조건", "PageIndex guarantees zero data loss")
    assert "unsupported_claim" in codes(validate(payload, drafting_input))


@pytest.mark.parametrize("assertion", [
    "PageIndex encrypts all stored data",
    "I have used PageIndex in production",
    "PageIndex deletes stale records automatically",
    "We have deployed PageIndex in production",
    "PageIndex는 모든 저장 데이터를 암호화한다",
])
@pytest.mark.parametrize("position", ["heading", "title"])
def test_unmapped_title_and_heading_cannot_assert_new_facts_or_experience(drafting_input, assertion, position):
    payload = response()
    if position == "heading":
        payload["sections"][0]["text"] += "\n\n## " + assertion
    else:
        payload["frontmatter"] = payload["frontmatter"].replace("PageIndex 채택 조건", assertion)
    report = validate(payload, drafting_input)
    assert report.status == "NEEDS_REVISION"
    assert codes(report) & {"unmapped_claim", "unsupported_claim", "invented_experience"}


@pytest.mark.parametrize("heading", ["작동 원리", "PageIndex 선택 기준", "Alternative comparison"])
def test_neutral_heading_labels_need_no_artificial_claims(drafting_input, heading):
    payload = response()
    payload["sections"][0]["text"] = "## " + heading + "\n\n" + payload["sections"][0]["text"]
    assert validate(payload, drafting_input).status == "REVIEW_READY"


def test_factual_heading_can_use_a_supported_claim_map(drafting_input):
    payload = response()
    sentence = "## " + payload["sections"][0]["claims"][0]["sentence"]
    payload["sections"][0]["text"] = sentence
    payload["sections"][0]["claims"][0]["sentence"] = sentence
    assert validate(payload, drafting_input).status == "REVIEW_READY"


@pytest.mark.parametrize("outcome", [
    "PageIndex uses 4 GB less memory than Redis.",
    "PageIndex costs $200 less per month than Redis.",
    "PageIndex processes 1000 rps.",
    "PageIndex uses 2 MiB less memory.",
    "PageIndex costs USD 200 per month.",
    "PageIndex costs USD200 per month.",
    "PageIndex delivers rps1000.",
    "PageIndex saves €80 per month.",
    "PageIndex saves 200만원 per month.",
    "PageIndex improves the result by 17 frobnitz.",
    "RFC 9110 integration uses 4GB less memory.",
])
def test_quantitative_inference_cannot_use_an_unrelated_mechanism_as_measurement(drafting_input, outcome):
    payload = response()
    sentence = "추론: durable storage 조건이라면 " + outcome
    payload["sections"].append({"id": "new_result", "text": sentence, "claims": [{
        "sentence": sentence, "kind": "inference", "evidence_ids": ["E1"], "role": "context"}]})
    report = validate(payload, drafting_input)
    assert report.status == "NEEDS_REVISION"
    assert "unsupported_metric" in codes(report)


@pytest.mark.parametrize("identifier", ["RFC 9110", "HTTP/2", "Python3.12", "request_id_200", "CVE-2026-1234", "v2.1"])
def test_identifiers_in_conditional_editorial_plans_are_not_quantitative_results(drafting_input, identifier):
    payload = response()
    sentence = f"추론: durable storage 조건이라면 {identifier} 관련 검토를 고려한다."
    payload["sections"].append({"id": "plan", "text": sentence, "claims": [{
        "sentence": sentence, "kind": "inference", "evidence_ids": ["E1"], "role": "context"}]})
    report = validate(payload, drafting_input)
    assert "unsupported_metric" not in codes(report)
    assert report.status == "REVIEW_READY"
