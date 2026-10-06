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
from src.research.extract import extract_sections

POLICY_PATH = Path(__file__).with_name("policy.md")


def load_policy() -> tuple[str, str]:
    """Load the versioned runtime policy independently of maintenance AGENTS.md."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    match = re.search(r"^policy_version: (\S+)$", text, re.MULTILINE)
    if not match or match.group(1) != "1.0":
        raise ValueError("unsupported editorial policy version")
    return match.group(1), text


def _kind(material: str) -> str | None:
    rules = (
        ("paper", r"\b(?:research paper|benchmark|dataset|ablation)\b|논문|벤치마크"),
        ("design_comparison", r"\b(?:design comparison|architecture comparison|trade-offs|tradeoffs)\b|설계 비교"),
        ("protocol", r"\b(?:protocol|standard|rfc|message flow|wire format)\b|프로토콜|표준"),
        ("tool", r"\b(?:library|tool|sdk|api|installation)\b|라이브러리|도구"),
    )
    for kind, pattern in rules:
        if re.search(pattern, material, re.IGNORECASE):
            return kind
    return None


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
    roles = {}
    try:
        for source in packet.sources:
            if source.kind == "run_log":
                continue
            url = canonical_topic_url(source.url)
            roles[url] = source.role
            for section in extract_sections(source):
                sections[(url, section.location)] = section
            if source.role != "secondary":
                material.append(source.title + "\n" + source.text)
    except ValueError:
        return blocked(["source_location_mapping_invalid"])
    # The document's stated type takes precedence over incidental examples:
    # a library README can contain a benchmark without becoming a paper.
    titles = "\n".join(source.title for source in packet.sources if source.role != "secondary" and source.kind != "run_log")
    kind = _kind(titles) or _kind("\n".join(material))
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
            if numeric_result and claim.metric_context is None:
                reasons.append(f"missing_metric_context:{claim.source_refs[0].location}")
            elif numeric_result:
                value = re.escape(format(claim.metric_context.value, "g"))
                if not re.search(rf"(?<![\d.]){value}(?:\.0+)?(?![\d.])", claim.text):
                    reasons.append(f"metric_context_mismatch:{claim.source_refs[0].location}")
            eligible.append((claim, " ".join(matched)))
    if reasons:
        return blocked(reasons)

    def select(pattern):
        return tuple(claim for claim, heading in eligible
                     if re.search(pattern, heading + "\n" + claim.text, re.IGNORECASE))

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
    return EditorialBrief(topic_id=packet.topic_id, post_kind=kind, question=question,
        thesis=thesis, comparison=tuple(claim.text for claim in comparison),
        key_mechanism=first.text, adoption_constraints=tuple(claim.text for claim in constraints),
        decision_criteria=tuple(dict.fromkeys([*(claim.text for claim in constraints), *user_context.goals, *user_context.constraints])),
        reversal_conditions=tuple(claim.text for claim in reversal), evidence=tuple(claim for claim, _ in eligible),
        source_authority=authority, study=study, warnings=tuple(dict.fromkeys(warnings)),
        user_context=user_context, policy_version=version)
