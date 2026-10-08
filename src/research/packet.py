"""Build conservative verbatim claims and select complete relevant prompt chunks."""
import hashlib
import json
from pathlib import Path
import re

from src.curator.matcher import CuratedTopic
from src.editorial.models import EvidenceClaim, MetricContext, ResearchBlocked, ResearchPacket, SourceRecord, SourceRef, canonical_topic_url, stable_topic_id
from src.research.extract import extract_sections, metric_scope


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _reported_metric(text, result_section, sections):
    """Enrich only an unambiguous literal result in its own experiment scope.

    This small grammar handles explicit reported comparison/setup prose. It does
    not infer experimental details from arbitrary paragraphs, tables or numbers.
    Unsupported formats retain no context and the brief's stronger gate blocks.
    """
    quantities = list(re.finditer(
        r"(?<![\w.])(?P<value>[-+]?\d+(?:\.\d+)?)\s*"
        r"(?P<unit>%|(?:percent(?:age)?|milliseconds?|ms|seconds?|sec|s|tokens/s|[kmg]i?b|bytes?|x)\b)", text, re.I))
    targets = set(re.findall(r"\b(?:throughput|latency|accuracy|memory use)\b", text, re.I))
    if len(quantities) != 1 or len(targets) != 1:
        return None
    phrases = {"baseline": set(), "conditions": set()}
    for section in sections:
        if metric_scope(section) != metric_scope(result_section):
            continue
        for paragraph in _paragraphs(section.text):
            for field, heading, pattern in (
                ("baseline", r"\b(?:baseline|alternatives?|comparison)\b", r"(?:baseline\s*:\s*|compare with\s+)([^.!?\n]+)"),
                ("conditions", r"\b(?:conditions?|experimental setup|settings|environment)\b",
                 r"(?:conditions?\s*:\s*|(?:throughput|latency|accuracy|memory use) measured on\s+)([^.!?\n]+)"),
            ):
                if re.search(heading, section.title, re.I):
                    # An explicit absent setup in this experiment cannot be
                    # filled from other prose, even within the same scope.
                    if re.search(r"\b(?:unknown|missing|unreported|unspecified|not reported|not documented)\b", paragraph, re.I):
                        return None
                    phrases[field].update(match.group(1).strip() for match in re.finditer(pattern, paragraph, re.I))
    if any(len(values) != 1 for values in phrases.values()):
        return None
    quantity = quantities[0]
    return MetricContext(value=float(quantity["value"]), unit=quantity["unit"], target=next(iter(targets)),
        baseline=next(iter(phrases["baseline"])), conditions=next(iter(phrases["conditions"])))


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
                claims.append(EvidenceClaim(text=paragraph, kind="source_claim", metric_context=_reported_metric(paragraph, section, extracted), source_refs=(
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


class ContextBudgetExceeded(ValueError):
    """Required evidence cannot fit; never discard or shorten it silently."""


def _select_whole(candidates, required, render, max_chars):
    selected = [key for _, key in candidates if key in required]
    if len(selected) != len(required):
        raise ValueError("required evidence missing from packet")
    if len(render(selected)) > max_chars:
        raise ContextBudgetExceeded("required_context_budget_exhausted")
    for _, key in sorted(candidates):
        if key not in selected and len(render([*selected, key])) <= max_chars:
            selected.append(key)
    return render(selected)


def select_context(packet: ResearchPacket, *, max_chars: int = 12000, required_claims=(), envelope=None) -> str:
    """Rank complete chunks using one budget for research and model prompts.

    The drafting envelope contains selected claims with their original packet
    IDs and source metadata, never full source bodies. Its entire serialization
    (brief and explicit run logs included) must fit. Required evidence blocks on
    overflow; optional chunks may be skipped intact. Snapshots remain complete.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    terms = set(re.findall(r"\w{3,}", packet.question.casefold()))
    candidates = []
    sections = {(source.url, section.location): section for source in packet.sources if not source.error
                for section in extract_sections(source)}
    if envelope is not None:
        catalog = {}
        for index, claim in enumerate(packet.claims):
            key = f"E{index + 1}"
            headings = [sections[(ref.url, ref.location)].title for ref in claim.source_refs
                        if (ref.url, ref.location) in sections]
            catalog[key] = {**claim.model_dump(mode="json"), "headings": headings}
            words = set(re.findall(r"\w{3,}", (" ".join(headings) + " " + claim.text).casefold()))
            roles = [source.role for source in packet.sources if any(ref.url == source.url for ref in claim.source_refs)]
            rank = (min(({"primary": 0, "unknown": 1, "secondary": 2}[role] for role in roles), default=3),
                    -len(terms & words), index)
            candidates.append((rank, key))
        required = {key for key, claim in zip(catalog, packet.claims) if claim in required_claims}
        if any(claim not in packet.claims for claim in required_claims):
            raise ValueError("required evidence missing from packet")

        def render(keys):
            refs = [ref for key in keys for ref in catalog[key]["source_refs"]]
            sources = []
            for source in packet.sources:
                locations = sorted({ref["location"] for ref in refs if ref["url"] == source.url})
                if locations:
                    metadata = source.model_dump(mode="json", exclude={"text", "locations"})
                    sources.append({**metadata, "selected_locations": locations})
            return json.dumps({**envelope, "evidence": {key: catalog[key] for key in keys}, "sources": sources}, ensure_ascii=False)

        return _select_whole(candidates, required, render, max_chars)
    seen = set()
    for source_index, source in enumerate(packet.sources):
        if source.error:
            continue
        for section_index, section in enumerate(extract_sections(source)):
            for paragraph_index, paragraph in enumerate(_paragraphs(section.text)):
                words = set(re.findall(r"\w{3,}", f"{section.title} {paragraph}".casefold()))
                relevance = len(terms & words)
                role_order = {"primary": 0, "unknown": 1, "secondary": 2}[source.role]
                chunk = f"[{source.role}] {source.url} [{section.location}]\n{section.title}\n{paragraph}"
                if chunk not in seen:
                    seen.add(chunk)
                    candidates.append(((role_order, -relevance, source_index, section_index, paragraph_index), chunk))
    return _select_whole(candidates, set(), lambda keys: "\n\n".join(keys), max_chars)
