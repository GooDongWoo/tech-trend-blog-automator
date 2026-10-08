"""Fail-closed static and conservative evidence cross-checks for local drafts.

Literal source support is deliberately conservative, not a semantic entailment
oracle. Inferences remain visibly conditional and are reviewed by a human before
approval. A citation or a model-supplied role never establishes source support.
"""
from datetime import datetime
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import yaml

from src.editorial.brief import build_brief, load_policy
from src.editorial.models import (
    ClaimKind, DraftText, EditorialBrief, ResearchBlocked, ResearchPacket,
    ValidationIssue, ValidationReport, canonical_topic_url,
)
from src.research.extract import extract_sections, verify_located_excerpt
from src.editorial.grounding import bound_verdicts, reported_number_supported, derived_difference
from src.editorial.media import IMAGE, load_catalog, validate_media


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("frontmatter keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def parse_frontmatter(text: str) -> dict:
    """Read bounded Jekyll YAML, rejecting duplicates, bad dates and field types."""
    if not text.startswith("---\n") or not text.endswith("\n---") or len(text) > 10000:
        raise ValueError("frontmatter requires exact opening/closing delimiters")
    try:
        data = yaml.load(text[4:-4], Loader=_UniqueLoader)
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("invalid frontmatter YAML") from error
    if not isinstance(data, dict) or data.get("layout") != "post":
        raise ValueError("layout must be post")
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise ValueError("title must be nonempty text")
    date = data.get("date")
    if not isinstance(date, (str, datetime)):
        raise ValueError("date must include a timezone")
    parsed = datetime.fromisoformat(date) if isinstance(date, str) else date
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("date must include a timezone")
    for field in ("categories", "tags"):
        if field in data and (not isinstance(data[field], list) or not all(isinstance(x, str) and x.strip() for x in data[field])):
            raise ValueError(f"{field} must be a list of strings")
    return data


def evidence_catalog(packet: ResearchPacket) -> dict:
    """IDs are stable within the persisted, ordered packet, not invented by LLMs."""
    return {f"E{i + 1}": claim for i, claim in enumerate(packet.claims)}


def run_catalog(brief: EditorialBrief, packet: ResearchPacket) -> dict:
    runs = list(brief.user_context.experience_refs)
    for claim in packet.claims:
        if claim.metric_context and claim.metric_context.run_record and claim.metric_context.run_record not in runs:
            runs.append(claim.metric_context.run_record)
    return {f"R{i + 1}": run for i, run in enumerate(runs)}


_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
_PERSONAL = re.compile(
    r"직접[^.!?\n]{0,35}(?:써|쓰|사용|테스트|실행|측정|배포)|써\s?보니"
    r"|(?:내가|나는|우리(?:가|는)|내\s*(?:프로젝트|서비스|시스템))[^.!?\n]{0,40}(?:썼|써봤|사용했|테스트했|실행했|측정했|배포해|배포했|겪었|도입했|해봤|없앴)"
    r"|\b(?:I|we)\s+(?:(?:have|had)\s+)?(?:tested|used|ran|deployed|measured|tried)\b", re.I)
_FACTUAL = re.compile(r"guarantee|ensures?|supports?|always|never loses|보장|지원한다|작동한다|개선된다|사라졌다", re.I)

_NUMERIC = re.compile(
    r"\d[\d.,]*\s*(?:%|퍼센트|배|ms(?![A-Za-z])|milliseconds?(?![A-Za-z])|seconds?(?![A-Za-z])|밀리초|초|tokens/s|times(?![A-Za-z])|x(?![A-Za-z]))"
    r"|\b(?:faster|slower|throughput|latency|accuracy)\b[^.!?\n]*\d|(?:성능|속도|정확도|지연)[^.!?\n]*\d"
    r"|\b(?:two|three|four|five|six|seven|eight|nine|ten|hundred)\s+(?:times|percent)\b"
    r"|\b(?:twice|doubled|tripled|halved|half)\b|(?:두|세|네|다섯|열)\s*배|반으로\s*줄", re.I)

# Written quantities remain quantitative even when a fallible semantic reviewer
# approves them. This detects forms, not values; no Korean number normalization
# exists, so these forms cannot be certified by the deterministic metric layer.
_KOREAN_QUANTITY = re.compile(
    r"(?<![가-힣A-Za-z0-9])(?:[영일이삼사오육칠팔구십백천만억][영일이삼사오육칠팔구십백천만억조]*"
    r"|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰|예순|일흔|여든|아흔)"
    r"\s*(?:퍼센트포인트|퍼센트|밀리초|초|분|시간|회|배|건|개|명|원|바이트|토큰)"
    r"(?=$|[\s.,!?;:)]|은|는|이|가|을|를|로|으|에|만|씩|도|라|였|다|의|당)")


def _clean(text):
    prose = re.sub(r"^\s*#{1,6}\s+", "", text)
    return " ".join(_LINK.sub("", prose).strip().split())


def _neutral_heading(text):
    """Only positive neutral labels bypass mapping, never arbitrary sentences.

    An optional one-token topic identifier can name the subject. Assertions in
    body headings must instead pass the ordinary claim-map checks; frontmatter
    has no claim-map field, so factual headlines remain blocked.
    """
    clean = _clean(text)
    if not _has_quantity(clean) and not _PERSONAL.search(clean) and re.fullmatch(
        r'(?:언제|어떤 조건에서|무엇을|어떻게) [^.!?\n]{1,60}(?:고려할까|선택할까|비교할까|검토할까|확인할까)\?', clean):
        return True
    labels = (
        "작동 원리", "구현 원리", "채택 조건", "선택 기준", "대안", "대안 비교",
        "비교", "제약 조건", "한계", "판단 기준", "검증 계획", "실험 조건",
        "결론", "Mechanism", "Alternatives", "Alternative comparison", "Comparison",
        "Adoption criteria", "Decision criteria", "Constraints", "Limitations",
        "Verification plan", "Experiment conditions", "Conclusion",
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    topic = r"[^\W\d_]\w*(?:[./-]\w+)*"
    return bool(re.fullmatch(r"(?:" + topic + r"(?:\s*:\s*|\s+))?(?:" + label_pattern + r")", _clean(text), re.I))


_IDENTIFIER = re.compile(
    r"\b(?:RFC|ISO(?:/IEC)?)\s+\d+(?:[.:-]\d+)*\b"
    r"|\b(?:HTTP|TLS|MQTT|AMQP)/\d+(?:\.\d+)*\b"
    r"|\b(?:version)\s+\d+(?:\.\d+)*\b"
    r"|\b[A-Za-z_][A-Za-z0-9_-]*\d[A-Za-z0-9_.-]*\b", re.I)
_NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*")
_COMPACT_QUANTITY_PREFIX = re.compile(
    r"\b(?:USD|EUR|KRW|JPY|GBP|CNY|CAD|AUD|rps|qps|tps|bps|[KMGT]i?B)\d", re.I)


def _has_quantity(text):
    """Unknown units cannot evade measurement checks by missing a unit list.

    Named standards, protocol versions and lexical machine identifiers are not
    numerical outcomes. Everything else with an explicit number is conservatively
    quantitative, including byte/memory, currency, rates and unfamiliar units.
    """
    clean = _clean(text)
    # USD200 and rps1000 are unit-first quantities, not machine IDs. Check
    # these forms before identifier masking; generic unknown units still use
    # the fallback number gate rather than an allow-by-missing-unit decision.
    if _COMPACT_QUANTITY_PREFIX.search(clean):
        return True
    prose = _IDENTIFIER.sub(" ", clean)
    prose = re.sub(r"^\s*\d+[.)]\s+", "", prose)  # List position, not outcome.
    return bool(_NUMERIC.search(prose) or _NUMBER.search(prose) or _KOREAN_QUANTITY.search(prose))


def _source_text(sentence, evidence):
    """Permit literal source wording with a narrow attribution wrapper only."""
    clean = _clean(sentence)
    quote = " ".join(evidence.text.split())
    if clean == quote:
        return True
    wrappers = (
        rf"(?:원문|원저자|저자|문서)(?:은|는) [“\"]{re.escape(quote)}[”\"](?:라고|고) (?:설명|보고|명시|주장)한다\.",
        rf"(?:The (?:author|source|document) (?:reports|states|claims):)\s*{re.escape(quote)}",
    )
    return any(re.fullmatch(pattern, clean, re.I) for pattern in wrappers)


def _attributed(sentence):
    return bool(re.match(r"(?:원문|원저자|저자|문서|논문|연구진)(?:은|는)|저자 보고값으로|(?:논문|문서)에 따르면|The (?:author|source|document) (?:reports|states|claims):", _clean(sentence), re.I))


def _conditional(text):
    return bool(re.search(r"(?:조건|경우|다면|라면|이면)|\b(?:if|when|unless|provided)\b", text, re.I))


def _editorial_only(text):
    """Unmapped signposts/questions/opinions need no artificial evidence IDs.

    This small conservative allowance must never swallow numbers, source-like
    factual assertions or past-tense experience. Unfamiliar prose can block for
    an explicit claim map instead of silently certifying a factual statement.
    """
    text = _clean(text)
    if not text:
        return True
    if _has_quantity(text) or _PERSONAL.search(text):
        return False
    if _FACTUAL.search(text) or re.search(r"faster|is\b|are\b", text, re.I):
        return False
    if text.endswith("?") or re.fullmatch(r"(?:다음|이제|먼저|여기서)[^.!?\n]*(?:살펴보자|정리해 보자|비교해 보자|따져 보자|판단해 보자)\.", text):
        return True
    return _conditional(text) and bool(re.search(r"(?:고려|선택|판단|선호|검토)|\b(?:consider|choose|prefer)\b", text, re.I))


def _mentions(text, fact):
    # Require substantive words, not shared articles or a model's role labels.
    ignored = {"the", "with", "when", "before", "compare", "requires", "are", "and", "this", "source", "claim"}
    terms = {word.casefold() for word in re.findall(r"[\w-]{3,}", fact) if word.casefold() not in ignored}
    return bool(terms) and len(terms & set(re.findall(r"[\w-]{3,}", text.casefold()))) >= min(2, len(terms))


def _run_issue(run):
    path = run.snapshot_path
    if path is None and run.url.startswith("file:///"):
        raw = unquote(urlsplit(run.url).path)
        if re.match(r"/[A-Za-z]:/", raw):
            raw = raw[1:]
        path = Path(raw)
    try:
        snapshot = path.read_bytes() if path is not None else run.text.encode("utf-8")
    except OSError:
        return "run_snapshot_unavailable"
    if hashlib.sha256(snapshot).hexdigest() != run.sha256:
        return "run_snapshot_changed"
    try:
        inspected_text = " ".join(snapshot.decode("utf-8-sig").split())
    except UnicodeDecodeError:
        return "run_text_mismatch"
    if " ".join(run.text.split()) not in inspected_text:
        return "run_text_mismatch"
    return None


def _run_metric_supported(metric, run, sentence):
    values = re.findall(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(%|[A-Za-z/]+|퍼센트|배|초)", sentence)
    aliases = {"percent": "%", "percentage": "%", "milliseconds": "ms", "millisecond": "ms", "seconds": "s", "second": "s"}
    unit = aliases.get(metric.unit.casefold(), metric.unit.casefold())
    if len(values) != 1 or float(values[0][0]) != metric.value or aliases.get(values[0][1].casefold(), values[0][1].casefold()) != unit:
        return False
    return all(getattr(metric, field).casefold() in run.text.casefold() for field in ("target", "baseline", "conditions"))


def validate_draft(text: DraftText, packet: ResearchPacket, brief: EditorialBrief, *, grounding=None, media_catalog=None) -> ValidationReport:
    issues = []
    def issue(code, section=None, sentence="", detail="", *, grounding=False):
        issues.append(ValidationIssue(code=code, section_id=section, sentence=sentence, detail=detail,
            check="grounding" if grounding else "static"))

    try:
        verdicts = bound_verdicts(text, packet, grounding)
    except ValueError as error:
        issue(str(error), grounding=True)
        verdicts = {}
    try:
        version, _ = load_policy()
        if brief.policy_version != version or text.policy_version != version:
            issue("editorial_policy_version_mismatch")
    except (OSError, ValueError):
        issue("editorial_policy_unavailable")
    try:
        issues.extend(validate_media(text.content, media_catalog if media_catalog is not None else load_catalog()))
    except (OSError, ValueError):
        issue("media_catalog_unavailable")
    if packet.topic_id != brief.topic_id:
        issue("topic_mismatch", grounding=True)
    rebuilt = build_brief(packet, brief.user_context)
    if isinstance(rebuilt, ResearchBlocked):
        issue("research_not_ready", detail=", ".join(rebuilt.reasons), grounding=True)
    if not brief.key_mechanism or not brief.comparison or not brief.adoption_constraints:
        issue("brief_incomplete", grounding=True)
    try:
        metadata = parse_frontmatter(text.frontmatter)
        if not _neutral_heading(metadata["title"]):
            issue("unsupported_claim", "frontmatter", metadata["title"], grounding=True)
        if _PERSONAL.search(metadata["title"]):
            issue("invented_experience", "frontmatter", metadata["title"])
        if _has_quantity(metadata["title"]):
            issue("unsupported_metric", "frontmatter", metadata["title"])
    except (ValueError, yaml.YAMLError, TypeError, RecursionError):
        issue("invalid_frontmatter", "frontmatter")

    evidence = evidence_catalog(packet)
    runs = run_catalog(brief, packet)
    sections = {}
    for source in packet.sources:
        if source.kind != "run_log" and not source.error:
            try:
                for section in extract_sections(source):
                    sections[(canonical_topic_url(source.url), section.location)] = section.text
            except ValueError:
                issue("source_location_mapping_invalid", grounding=True)
    roles = set()
    seen_sections = set()
    for section in text.sections:
        sid = section.id
        if sid in seen_sections:
            issue("duplicate_section_id", sid)
        seen_sections.add(sid)
        remainder = IMAGE.sub("", section.text)
        # Unresolved placeholders include numeric footnotes without packet IDs.
        bare = _LINK.sub("", section.text)
        if re.search(r"\[(?:MEME_\w+|citation needed|출처[^\]]*|TODO|TBD|\d+)\]", bare, re.I):
            issue("placeholder_citation", sid)
        for label, url in _LINK.findall(section.text):
            claim = evidence.get(label)
            allowed = {ref.url for ref in claim.source_refs} if claim else set()
            try:
                canonical = canonical_topic_url(url)
            except ValueError:
                canonical = None
            if canonical not in allowed:
                issue("unresolved_citation", sid, detail=label)
        if re.search(r"\[E\d+\](?!\()", section.text):
            issue("unresolved_citation", sid)
        for index, mapped in enumerate(section.claims):
            sentence = mapped.sentence
            verdict = verdicts.get(f'{sid}:{index}') if verdicts is not None else None
            semantic = verdict == 'supported'
            if verdicts is not None and not semantic:
                issue('grounding_' + (verdict or 'unknown'), sid, sentence, grounding=True)
            if section.text.count(sentence) != 1 or not re.search(r"(?<!\S)" + re.escape(sentence) + r"(?!\S)", section.text):
                issue("claim_span_mismatch", sid, sentence)
                continue
            remainder = remainder.replace(sentence, " ", 1)
            refs = [evidence[eid] for eid in mapped.evidence_ids if eid in evidence]
            if len(refs) != len(mapped.evidence_ids) or len(set(mapped.evidence_ids)) != len(mapped.evidence_ids):
                issue("unknown_evidence", sid, sentence)
            if mapped.kind != "inference" and mapped.evidence_ids and not (mapped.kind == "measurement" and mapped.run_ids):
                reader_ids = {label for label, _ in _LINK.findall(sentence)}
                if reader_ids != set(mapped.evidence_ids):
                    issue("citation_evidence_mismatch", sid, sentence)
            attached_runs = [runs[rid] for rid in mapped.run_ids if rid in runs]
            if len(attached_runs) != len(mapped.run_ids):
                issue("unknown_run", sid, sentence)
            valid_runs = []
            for run in attached_runs:
                failed = _run_issue(run)
                if failed:
                    issue(failed, sid, sentence, grounding=True)
                elif _clean(sentence) in " ".join(run.text.split()):
                    valid_runs.append(run)
            if mapped.kind == "measurement" and not valid_runs and (not refs or any(claim.kind != ClaimKind.MEASUREMENT for claim in refs)):
                issue("claim_kind_mismatch", sid, sentence, grounding=True)
            if mapped.kind == "source_claim" and any(claim.kind != ClaimKind.SOURCE_CLAIM for claim in refs):
                issue("claim_kind_mismatch", sid, sentence, grounding=True)
            quoted_source = bool(refs) and all(_source_text(sentence, claim) for claim in refs) and _attributed(sentence)
            if _PERSONAL.search(sentence) and not quoted_source and not valid_runs:
                issue("invented_experience", sid, sentence)
            numeric = bool(_has_quantity(sentence))
            if numeric:
                metrics = [claim.metric_context for claim in refs if claim.metric_context]
                # Every result is literal and original-author attributed; an
                # inference cannot invent a number and pass by citing a metric.
                own_metric = mapped.kind == "measurement" and metrics and valid_runs and all(
                    metric.run_record in valid_runs and _run_metric_supported(metric, metric.run_record, sentence) for metric in metrics)
                reported = semantic and mapped.kind == 'source_claim' and _attributed(sentence) and reported_number_supported(sentence, refs, packet)
                derived = False
                if mapped.derived_from:
                    try:
                        value, unit = derived_difference(packet, mapped.derived_from)
                        prose = _clean(sentence)
                        expected_unit = '(?:퍼센트포인트|percentage points)' if unit == 'percentage_points' else re.escape(unit)
                        found = re.findall(r'(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*' + expected_unit, prose)
                        derived = (semantic and mapped.kind == 'source_claim' and set(mapped.derived_from) == set(mapped.evidence_ids)
                            and bool(re.search(r'계산한 차이|derived difference', prose, re.I)) and len(found) == 1
                            and float(found[0]) == value and len(_NUMBER.findall(prose)) == 1 and _attributed(sentence))
                    except ValueError:
                        pass
                if (_KOREAN_QUANTITY.search(_clean(sentence)) or mapped.kind == "inference"
                    or not metrics or not (quoted_source or own_metric or reported or derived)):
                    issue("unsupported_metric", sid, sentence, grounding=True)
                else:
                    context_text = section.text.casefold()
                    if any(not all(str(getattr(metric, field)).casefold() in context_text for field in ("target", "baseline", "conditions")) for metric in metrics):
                        issue("missing_metric_conditions", sid, sentence, grounding=True)
            if mapped.kind == "inference":
                if (not semantic and not re.search(r"추론|inference", sentence, re.I)) or not _conditional(sentence):
                    issue("unlabeled_inference", sid, sentence, grounding=True)
                if not refs or _FACTUAL.search(sentence) or re.search(r"확실히|항상|반드시|proven", sentence, re.I):
                    issue("unsupported_claim", sid, sentence, grounding=True)
            elif valid_runs and mapped.kind == "measurement":
                pass
            elif not refs or not (semantic and _attributed(sentence)) and not any(_source_text(sentence, claim) for claim in refs):
                issue("unsupported_claim", sid, sentence, grounding=True)
            for claim in refs:
                if claim.status == "disputed" or claim.kind in {ClaimKind.INFERENCE, ClaimKind.HYPOTHESIS}:
                    issue("unsupported_claim", sid, sentence, grounding=True)
                for ref in claim.source_refs:
                    source = next((s for s in packet.sources if s.url == ref.url), None)
                    if source is None or not verify_located_excerpt(source, ref.location, claim.text):
                        issue("unsupported_claim", sid, sentence, grounding=True)
            if mapped.role == "mechanism" and brief.key_mechanism and any(claim.text == brief.key_mechanism and (semantic or _source_text(sentence, claim)) for claim in refs):
                roles.add("mechanism")
            if mapped.role == "alternative" and any(claim.text in brief.comparison and (semantic or _source_text(sentence, claim)) for claim in refs):
                roles.add("alternative")
            if mapped.role == "decision" and mapped.kind == "inference" and _conditional(sentence):
                if semantic and any(c.text in brief.comparison for c in refs) and any(c.text in brief.adoption_constraints for c in refs):
                    roles.add('decision')
                elif (any(claim.text in brief.comparison and _mentions(sentence, claim.text) for claim in refs)
                    and any(claim.text in brief.adoption_constraints and _mentions(sentence, claim.text) for claim in refs)
                    and any(_mentions(sentence, condition) for condition in brief.reversal_conditions)):
                    roles.add("decision")
        # Only neutral unmapped labels may bypass ordinary prose coverage.
        for line in remainder.splitlines():
            if line.lstrip().startswith("#"):
                if not _neutral_heading(line):
                    issue("unmapped_claim", sid, line, grounding=True)
                if _PERSONAL.search(line):
                    issue("invented_experience", sid, line)
                if _has_quantity(line):
                    issue("unsupported_metric", sid, line)
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
                if _PERSONAL.search(sentence):
                    issue("invented_experience", sid, sentence)
                if _has_quantity(sentence):
                    issue("unsupported_metric", sid, sentence, grounding=True)
                if not _editorial_only(sentence):
                    issue("unmapped_claim", sid, sentence, grounding=True)
    for role in ("mechanism", "alternative", "decision"):
        if role not in roles:
            issue(f"missing_{role}", grounding=True)
    return ValidationReport(status="NEEDS_REVISION" if issues else "REVIEW_READY", issues=tuple(issues),
        static_passed=not any(item.check == "static" for item in issues),
        grounding_passed=not any(item.check == "grounding" for item in issues), warnings=brief.warnings)
