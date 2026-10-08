"""Compatibility adapter for the source-snapshot research pipeline."""
import asyncio
from pathlib import Path
from typing import Any
import json

from src.curator.matcher import CuratedTopic
from src.editorial.models import ResearchBlocked
from src.research.fetch import fetch_source
from src.research.packet import build_packet, select_context, select_proposed_evidence, _persist
from src.research.extract import extract_sections


class DeepResearcher:
    def __init__(self, artifact_dir: Path = Path("temp/research"), *, fetcher=None, model=None, max_sources=5):
        if not 1 <= max_sources <= 10:
            raise ValueError("max_sources must be between 1 and 10")
        self.artifact_dir = artifact_dir
        self.fetcher = fetcher
        self.model = model
        self.max_sources = max_sources

    async def research(self, topic: CuratedTopic) -> dict[str, Any]:
        queue, visited, sources = [topic.url], set(), []
        while queue and len(sources) < self.max_sources:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            fetched = await asyncio.to_thread(self.fetcher or fetch_source, url, snapshot_dir=self.artifact_dir / "sources")
            sources.append(fetched)
            if not fetched.error:
                queue.extend(link for link in fetched.links if link not in visited and link not in queue)
        source = sources[0]
        packet = build_packet(topic, sources, artifact_dir=self.artifact_dir / "packets")
        if self.model is not None and any(s.kind == 'pdf' for s in sources) and not isinstance(packet, ResearchBlocked):
            try:
                # Bound input, select complete sections rather than prefix text.
                sections, used = [], 0
                for item in sorted(sources, key=lambda s: s.kind != "pdf"):
                    for section in extract_sections(item):
                        if 'appendix' in section.title.casefold() or section.title.startswith(('A ', 'B ', 'C ', 'D ')):
                            continue
                        entry = {"url":item.url,"location":section.location,"heading":section.title,"text":section.text}
                        size = len(json.dumps(entry, ensure_ascii=False))
                        if used + size <= 48000:
                            sections.append(entry)
                            used += size
                prompt = ('Select sufficient literal source excerpts for mechanism, alternatives, limitations, '
                    'reversal conditions and experiment setup, datasets, metrics and ablation. '
                    'Return JSON {"claims":[{"url":...,"location":...,"text":...,"metric_context":null}],"gaps":[]}. '
                    'Every text must be an exact contiguous excerpt; split compound outcomes into separate literal spans. '
                    'Each outcome needs metric_context {value,unit,target,baseline,conditions}; context strings must be '
                    'exact source excerpts from the same study. Preserve authors comparison protocol and dataset/split/model '
                    'conditions. Never take setup from another benchmark, appendix prompts or another model-size study. '
                    'Do not invent code implementation from a promised release. Missing facts go in gaps. '
                    'Do not rewrite excerpts or infer personal experience. Sources: ' + json.dumps(sections, ensure_ascii=False))
                response = await asyncio.to_thread(self.model.generate, 'research', prompt,
                    max_output_tokens=6000, artifact_dir=self.artifact_dir / 'model-calls')
                proposals = json.loads(response)
                packet = select_proposed_evidence(packet, proposals)
                if proposals['gaps']:
                    packet = ResearchBlocked(reasons=tuple(proposals['gaps']), sources=tuple(sources), packet=packet)
                packet = _persist(packet, self.artifact_dir / 'packets')
            except Exception as error:
                packet = _persist(ResearchBlocked(reasons=('invalid_extraction_proposal', type(error).__name__),
                    sources=tuple(sources), packet=packet if not isinstance(packet, ResearchBlocked) else packet.packet),
                    self.artifact_dir / 'packets')
        gaps = ["source_follow_budget_exhausted"] if queue else []
        if gaps and not isinstance(packet, ResearchBlocked):
            packet = _persist(ResearchBlocked(reasons=tuple(gaps), sources=tuple(sources), packet=packet),
                              self.artifact_dir / 'packets')
        result = {"topic": topic.title, "url": topic.url, "suggested_angle": topic.suggested_angle,
                  "relevance_reason": topic.relevance_reason, "source": source, "packet": packet,
                  "source_following_gaps": gaps}
        if isinstance(packet, ResearchBlocked):
            return {**result, "status": packet.status, "reasons": list(packet.reasons), "raw_content": ""}
        context = select_context(packet)
        if not context:
            return {**result, "status": "NEEDS_RESEARCH", "reasons": ["context_budget_exhausted"], "raw_content": ""}
        return {**result, "status": "RESEARCH_READY", "raw_content": source.text,
                "context": context}
