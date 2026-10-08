"""Conservative source-grounded editorial decisions before any prose generation.

This deterministic selector recognizes explicit document/section cues. Ambiguous
or implicit facts block for further research; they are never supplied by an LLM.
"""
from pathlib import Path
import re

from src.editorial.models import (
    ClaimKind, EditorialBrief, PaperStudy, ResearchBlocked, ResearchPacket,
    UserContext, canonical_topic_url,
)
from src.research.extract import extract_sections, metric_scope

POLICY_PATH = Path(__file__).with_name("policy.md")


def load_policy() -> tuple[str, str]:
    """Load the versioned runtime policy independently of maintenance AGENTS.md."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    match = re.search(r"^policy_version: (\S+)$", text, re.MULTILINE)
    if not match or match.group(1) != "1.0":
        raise ValueError("unsupported editorial policy version")
    return match.group(1), text


def _kind(material: str, *, identity=False) -> str | None:
    paper = (r"\b(?:research paper|benchmark report|benchmark study|dataset paper)\b|논문|벤치마크 보고서"
             if identity else r"\b(?:research paper|benchmarks?|dataset|ablation)\b|논문|벤치마크")
    rules = (
        ("paper", paper),
        ("design_comparison", r"\b(?:design comparison|architecture comparison|trade-offs|tradeoffs)\b|설계 비교"),
        ("protocol", r"\b(?:protocol|standard|rfc|message flow|wire format)\b|프로토콜|표준"),
        ("tool", r"\b(?:library|tool|sdk|api|installation)\b|라이브러리|도구"),
    )
    for kind, pattern in rules:
        if re.search(pattern, material, re.IGNORECASE):
            return kind
    return None


def _document_kind(sections) -> str | None:
    """Use the opening identity and explicit usage structure before examples."""
    opening = next((section for section in sections if section.title != "Document"), None)
    if opening is not None:
        declared = _kind(opening.title)
        if declared:
            return declared
    for section in sections:
        if section.title == "Document" or section is opening:
            introduction = re.split(r"\n\s*\n", section.text, maxsplit=1)[0]
            declared = _kind(introduction, identity=True)
            if declared:
                return declared
    if any(section.text and re.search(r"\b(?:quickstart|getting started|installation|sdk|api)\b|빠른 시작|설치",
                                     section.title, re.I) for section in sections):
        return "tool"
    return None


def _documents_absence(text: str) -> bool:
    """Absence of documentation is not the missing mechanism/experiment itself.

    Keep real technical negatives (e.g. writes unavailable, incompatible clients)
    eligible. These patterns concern absent information, not feature polarity.
    """
    return bool(re.search(
        r"\b(?:not|never)\s+(?:\w+\s+){0,2}(?:documented|reported|specified|provided|described|measured|evaluated|known)\b"
        r"|\bno\b[^.!?\n]*\b(?:documented|reported|specified|provided|described|measured|evaluated)\b"
        r"|\b(?:undocumented|unreported|unspecified|unknown|missing)\b"
        r"|(?:명시|제공|보고|측정|확인)되지 않|(?:자료|정보)가 없|미제공",
        text, re.IGNORECASE))


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized = " ".join(text.casefold().split())
    wanted = " ".join(phrase.casefold().split())
    return bool(re.search(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", normalized))


def _unit(unit: str) -> str:
    unit = unit.casefold().strip()
    for aliases, canonical in (
        (("%", "percent", "percentage"), "%"),
        (("ms", "millisecond", "milliseconds"), "ms"),
        (("s", "sec", "second", "seconds"), "s"),
        (("byte", "bytes"), "bytes"),
    ):
        if unit in aliases:
            return canonical
    return unit


def _metric_reasons(claim, sections, roles):
    """Match each reported value/unit and literal context in its own source.

    The contract has one MetricContext per claim. Compound numerical results
    therefore block until split into separate, fully contextualized claims.
    Literal context matching is conservative; it does not certify study validity.
    """
    location = claim.source_refs[0].location
    context = claim.metric_context
    if context is None:
        return [f"missing_metric_context:{location}"]
    reasons = []
    number_pattern = r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])"
    quantities = list(re.finditer(
        r"(?<![\w.])(?P<value>[-+]?\d+(?:\.\d+)?)\s*"
        r"(?P<unit>%|(?:percent(?:age)?|milliseconds?|ms|seconds?|sec|s|tokens/s|[kmg]i?b|bytes?|x)\b)",
        claim.text, re.IGNORECASE))
    values = [*re.findall(number_pattern, claim.text), *(match["value"] for match in quantities)]
    if not any(float(value) == context.value for value in values):
        reasons.append(f"metric_context_mismatch:{location}")
    # Contextual sample sizes/worker counts are not extra outcome values. Other
    # numerical outcomes still need an independently bound MetricContext.
    residual = claim.text
    for phrase in (context.baseline, context.conditions):
        residual = re.sub(re.escape(phrase), "", residual, flags=re.IGNORECASE)
    if len(quantities) > 1 or len(re.findall(number_pattern, residual)) > 1:
        reasons.append(f"uncovered_numeric_results:{location}")
    reported = [match for match in quantities if float(match["value"]) == context.value]
    if not reported:
        reasons.append(f"missing_reported_metric_unit:{location}")
    elif not any(_unit(match["unit"]) == _unit(context.unit) for match in reported):
        reasons.append(f"metric_unit_mismatch:{location}")
    referenced = [sections[(ref.url, ref.location)] for ref in claim.source_refs
                  if (ref.url, ref.location) in sections and roles.get(ref.url) != "secondary"]
    if not any(_contains_phrase(section.title + "\n" + section.text, context.target)
               and not _documents_absence(section.text) for section in referenced):
        reasons.append(f"unsupported_metric_target:{location}")
    scopes = {(ref.url, metric_scope(sections[(ref.url, ref.location)])) for ref in claim.source_refs
              if (ref.url, ref.location) in sections and roles.get(ref.url) != "secondary"}
    context_sections = {
        "baseline": r"\b(?:baseline|alternatives?|compare|compared|comparison|against|versus)\b|기준선|비교|대안",
        "conditions": r"\b(?:conditions?|experimental setup|settings|environment|workloads?|hardware|workers?|batch)\b|실험 조건|환경|부하",
    }
    for field, cues in context_sections.items():
        # Same-document context is insufficient: an experiment cannot borrow a
        # sibling's setup. Explicitly missing setup also blocks manual metadata.
        scoped_sections = [section for (url, _), section in sections.items()
                           if (url, metric_scope(section)) in scopes
                           and re.search(cues, section.title + "\n" + section.text, re.IGNORECASE)]
        supported = (not any(_documents_absence(section.text) for section in scoped_sections)
                     and any(_contains_phrase(section.text, getattr(context, field)) for section in scoped_sections))
        if not supported:
            reasons.append(f"unsupported_metric_{field}:{location}")
    return reasons


def build_brief(packet: ResearchPacket, user_context: UserContext) -> EditorialBrief | ResearchBlocked:
    """Choose an attributed thesis from literal, located source claims.

    Section cues select facts, not article headings. Inferences/hypotheses are
    deliberately not eligible core evidence; prose generation belongs to Unit 4.
    """
    def blocked(reasons):
        return ResearchBlocked(reasons=tuple(dict.fromkeys(reasons)), sources=packet.sources, packet=packet)

    try:
        version, _ = load_policy()
    except (OSError, ValueError):
        return blocked(["editorial_policy_unavailable"])
    failures = [source.error for source in packet.sources if source.error]
    if failures:
        return blocked(failures)
    question = packet.question.strip()
    if "\n" in question or len(re.split(r"(?<=[.!?。！？])\s+", question)) != 1:
        return blocked(["question_requires_one_sentence"])
    sections = {}
    material = []
    document_kinds = []
    roles = {}
    try:
        for source in packet.sources:
            if source.kind == "run_log":
                continue
            url = canonical_topic_url(source.url)
            roles[url] = source.role
            document_sections = extract_sections(source)
            for section in document_sections:
                sections[(url, section.location)] = section
            if source.role != "secondary":
                material.append(source.title + "\n" + "\n".join(section.title + "\n" + section.text for section in document_sections))
                document_kinds.append(_document_kind(document_sections))
    except ValueError:
        return blocked(["source_location_mapping_invalid"])
    # The document's stated type takes precedence over incidental examples:
    # a library README can contain a benchmark without becoming a paper.
    titles = "\n".join(source.title for source in packet.sources if source.role != "secondary" and source.kind != "run_log")
    kind = _kind(titles) or next((kind for kind in document_kinds if kind), None) or _kind("\n".join(material))
    if kind is None:
        return blocked(["missing_post_kind"])
    eligible, reasons = [], []
    for claim in packet.claims:
        if claim.status == "disputed":
            reasons.append("disputed_claim")
            continue
        if claim.kind not in {ClaimKind.SOURCE_CLAIM, ClaimKind.MEASUREMENT}:
            continue
        matched = []
        for ref in claim.source_refs:
            section = sections.get((ref.url, ref.location))
            if section is None or claim.text not in section.text:
                reasons.append(f"unsupported_claim:{ref.location}")
            elif roles[ref.url] != "secondary":
                matched.append(section.title)
        if matched:
            # Numbers in result prose cannot bypass the stronger measurement
            # contract by arriving as an unverified source_claim from Unit 2.
            numeric_result = re.search(r"\d(?:[\d.,]*\s*)(?:%|percent\b|ms\b|seconds\b|tokens/s\b|x\b)|\b(?:faster|slower|throughput|latency|accuracy)\b[^\n]*\d", claim.text, re.IGNORECASE)
            if numeric_result or claim.metric_context is not None:
                reasons.extend(_metric_reasons(claim, sections, roles))
            eligible.append((claim, " ".join(matched)))
    if reasons:
        return blocked(reasons)

    def select(pattern):
        return tuple(claim for claim, heading in eligible
                     if not _documents_absence(claim.text)
                     and re.search(pattern, heading + "\n" + claim.text, re.IGNORECASE))

    mechanism = select(r"\b(?:mechanism|method|api|message flow|wire format)\b|작동 원리|방법|메시지 흐름")
    comparison = select(r"\b(?:baseline|alternative|compare|comparison)\b|대안|비교")
    constraints = select(r"\b(?:constraints|requirements|compatibility|limitations|requires)\b|제약|요구사항|호환성|한계")
    reversal = select(r"\b(?:unsuitable|unless|cannot|failure|fails|selection boundary)\b|부적합|실패|사용할 수 없")
    missing = [name for name, values in (("missing_key_mechanism", mechanism), ("missing_comparison_alternative", comparison),
                ("missing_adoption_constraints", constraints), ("missing_reversal_condition", reversal)) if not values]
    if missing:
        return blocked(missing)
    warnings = list(packet.gaps)
    authority = tuple(f"{source.url}: {source.role}" for source in packet.sources)
    if "unknown" in roles.values():
        warnings.append("primary_source_authority_unconfirmed")
    study = None
    if kind == "paper":
        study = PaperStudy(dataset=select(r"\bdataset\b|데이터셋"), baseline=comparison,
            metrics=select(r"\bmetrics?\b|실험 조건|측정 지표"), ablation=select(r"\bablation\b|절제 실험"),
            limitations=select(r"\blimitations?\b|한계"))
        for field in ("dataset", "baseline", "metrics", "ablation", "limitations"):
            if not getattr(study, field):
                warnings.append(f"{field}_not_reported")
    first = mechanism[0]
    thesis = f"Source claim ({first.source_refs[0].url}): {first.text}"
    # Keep the full packet locally; the brief carries only the claims actually
    # chosen for its analysis, so an unrelated appendix is not mandatory input.
    selected = [*mechanism, *comparison, *constraints, *reversal]
    if study:
        for field in ("dataset", "baseline", "metrics", "ablation", "limitations"):
            selected.extend(getattr(study, field))
    selected.extend(claim for claim, _ in eligible if claim.metric_context is not None)
    return EditorialBrief(topic_id=packet.topic_id, post_kind=kind, question=question,
        thesis=thesis, comparison=tuple(claim.text for claim in comparison),
        key_mechanism=first.text, adoption_constraints=tuple(claim.text for claim in constraints),
        decision_criteria=tuple(dict.fromkeys([*(claim.text for claim in constraints), *user_context.goals, *user_context.constraints])),
        reversal_conditions=tuple(claim.text for claim in reversal), evidence=tuple(claim for claim, _ in eligible if claim in selected),
        source_authority=authority, study=study, warnings=tuple(dict.fromkeys(warnings)),
        user_context=user_context, policy_version=version)
