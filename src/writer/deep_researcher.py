"""Compatibility adapter for the source-snapshot research pipeline."""
import asyncio
from pathlib import Path
from typing import Any

from src.curator.matcher import CuratedTopic
from src.editorial.models import ResearchBlocked
from src.research.fetch import fetch_source
from src.research.packet import build_packet, select_context


class DeepResearcher:
    def __init__(self, artifact_dir: Path = Path("temp/research")):
        self.artifact_dir = artifact_dir

    async def research(self, topic: CuratedTopic) -> dict[str, Any]:
        source = await asyncio.to_thread(fetch_source, topic.url, snapshot_dir=self.artifact_dir / "sources")
        packet = build_packet(topic, [source], artifact_dir=self.artifact_dir / "packets")
        result = {"topic": topic.title, "url": topic.url, "suggested_angle": topic.suggested_angle,
                  "relevance_reason": topic.relevance_reason, "source": source, "packet": packet}
        if isinstance(packet, ResearchBlocked):
            return {**result, "status": packet.status, "reasons": list(packet.reasons), "raw_content": ""}
        context = select_context(packet)
        if not context:
            return {**result, "status": "NEEDS_RESEARCH", "reasons": ["context_budget_exhausted"], "raw_content": ""}
        return {**result, "status": "RESEARCH_READY", "raw_content": source.text,
                "context": context}
