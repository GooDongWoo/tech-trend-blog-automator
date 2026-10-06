"""Build conservative verbatim claims and select complete relevant prompt chunks."""
import hashlib
from pathlib import Path
import re

from src.curator.matcher import CuratedTopic
from src.editorial.models import EvidenceClaim, ResearchBlocked, ResearchPacket, SourceRecord, SourceRef, canonical_topic_url, stable_topic_id
from src.research.extract import extract_sections


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _conflicts(claims: list[EvidenceClaim]) -> set[int]:
    """Flag explicit polarity reversals; this is not general semantic detection."""
    propositions = {}
    conflicts = set()
    for index, claim in enumerate(claims):
        for sentence in re.split(r"(?<=[.!?])\s+", claim.text):
            sentence = sentence.casefold().strip().rstrip(".!?")
            negative = bool(re.search(r"\bdoes not support\b|\bis not\b|\bcannot\b", sentence))
            key = re.sub(r"\bdoes not support\b", "supports", sentence)
            key = re.sub(r"\bis not\b", "is", key)
            key = re.sub(r"\bcannot\b", "can", key)
            for other_index, other_negative in propositions.get(key, []):
                if negative != other_negative:
                    conflicts.update((index, other_index))
            propositions.setdefault(key, []).append((index, negative))
    return conflicts


def _persist(result: ResearchPacket | ResearchBlocked, artifact_dir: Path | str) -> ResearchPacket | ResearchBlocked:
    directory = Path(artifact_dir)
    serialized = result.model_dump_json(indent=2)
    name = hashlib.sha256(serialized.encode()).hexdigest()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(serialized, encoding="utf-8")
        if isinstance(result, ResearchBlocked):
            summary = "# NEEDS_RESEARCH\n\n" + "\n".join(f"- {reason}" for reason in result.reasons)
            evidence = result.packet
        else:
            summary = f"# Research packet\n\nQuestion: {result.question}\nTopic ID: {result.topic_id}"
            evidence = result
        summary += "\n\n## Sources\n\n" + "\n".join(
            f"- {source.url} ({source.role}) SHA-256: {source.sha256}; snapshot: {source.snapshot_path}; error: {source.error}"
            for source in result.sources)
        if evidence:
            summary += "\n\n## Source claims (unverified unless disputed)\n\n" + "\n\n".join(
                f"{claim.text}\n\n" + "; ".join(f"{ref.url} [{ref.location}] SHA-256: {ref.sha256}" for ref in claim.source_refs)
                for claim in evidence.claims)
            summary += "\n\n## Gaps\n\n" + "\n".join(evidence.gaps)
        (directory / f"{name}.md").write_text(summary + "\n", encoding="utf-8")
    except OSError as error:
        return ResearchBlocked(reasons=("packet_write_failed", type(error).__name__), sources=result.sources,
                               packet=result.packet if isinstance(result, ResearchBlocked) else result)
    return result


def build_packet(topic: CuratedTopic, sources: list[SourceRecord], *, artifact_dir: Path | str = Path("temp/research/packets")) -> ResearchPacket | ResearchBlocked:
    unique = {}
    for source in sources:
        key = canonical_topic_url(source.url)
        if key in unique and source != unique[key]:
            return _persist(ResearchBlocked(reasons=("duplicate_source_url",), sources=tuple(sources)), artifact_dir)
        unique[key] = source
    ordered = sorted(unique.values(), key=lambda source: ({"primary": 0, "unknown": 1, "secondary": 2}[source.role], source.url))
    failures = tuple(dict.fromkeys(source.error for source in ordered if source.error))
    if failures:
        return _persist(ResearchBlocked(reasons=failures, sources=ordered), artifact_dir)
    eligible = [source for source in ordered if source.role != "secondary"]
    if not eligible:
        return _persist(ResearchBlocked(reasons=("missing_primary_source",), sources=ordered), artifact_dir)
    claims = []
    for source in eligible:
        try:
            extracted = extract_sections(source)
        except ValueError:
            return _persist(ResearchBlocked(reasons=("partial_extraction",), sources=ordered), artifact_dir)
        for section in extracted:
            for paragraph in _paragraphs(section.text):
                claims.append(EvidenceClaim(text=paragraph, kind="source_claim", source_refs=(
                    SourceRef(url=source.url, sha256=source.sha256, location=section.location),)))
    if not claims:
        return _persist(ResearchBlocked(reasons=("no_citable_text",), sources=ordered), artifact_dir)
    disputed = _conflicts(claims)
    claims = [claim.model_copy(update={"status": "disputed"}) if index in disputed else claim for index, claim in enumerate(claims)]
    snapshot_hash = hashlib.sha256("\n".join(f"{source.url} {source.sha256}" for source in ordered).encode()).hexdigest()
    gaps = []
    if any(source.role == "unknown" for source in eligible):
        gaps.append("primary_source_authority_unconfirmed")
    if disputed:
        gaps.append("conflicting_claims")
    result = ResearchPacket(topic_id=stable_topic_id(topic.url, snapshot_hash), question=topic.suggested_angle.strip() or topic.title,
                            sources=ordered, claims=claims, gaps=gaps)
    if disputed:
        return _persist(ResearchBlocked(reasons=("conflicting_claims",), sources=ordered, packet=result), artifact_dir)
    return _persist(result, artifact_dir)


def select_context(packet: ResearchPacket, *, max_chars: int = 12000) -> str:
    """Rank whole paragraphs by question relevance; snapshots remain complete."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    terms = set(re.findall(r"\w{3,}", packet.question.casefold()))
    candidates = []
    for source_index, source in enumerate(packet.sources):
        if source.error:
            continue
        for section_index, section in enumerate(extract_sections(source)):
            for paragraph_index, paragraph in enumerate(_paragraphs(section.text)):
                words = set(re.findall(r"\w{3,}", f"{section.title} {paragraph}".casefold()))
                relevance = len(terms & words)
                role_order = {"primary": 0, "unknown": 1, "secondary": 2}[source.role]
                chunk = f"[{source.role}] {source.url} [{section.location}]\n{paragraph}"
                candidates.append(((role_order, -relevance, source_index, section_index, paragraph_index), chunk))
    selected, used, seen = [], 0, set()
    for _, chunk in sorted(candidates):
        if chunk in seen:
            continue
        seen.add(chunk)
        size = len(chunk) + (2 if selected else 0)
        if used + size <= max_chars:
            selected.append(chunk)
            used += size
    return "\n\n".join(selected)
