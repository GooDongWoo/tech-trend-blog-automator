"""Review-only compatibility adapter for the evidence-led drafting pipeline."""
import asyncio
import hashlib
from pathlib import Path
from typing import Any

from config import settings
from src.llm.client import ModelClient
from src.curator.matcher import CuratedTopic
from src.editorial.brief import build_brief
from src.editorial.draft import LLMClient, write_draft
from src.editorial.media import MemeCatalog, apply_media, load_catalog
from src.editorial.models import ResearchBlocked, ResearchPacket, UserContext, ValidationIssue
from src.editorial.validate import parse_frontmatter
from .deep_researcher import DeepResearcher


class BlogWriter:
    """Produce local review artifacts; publishing is a separate approval boundary."""

    def __init__(self, blog_repo_path: Path | None = None, *, artifact_dir: Path | None = None, llm: LLMClient | None = None, media_catalog: MemeCatalog | None = None):
        self.blog_repo_path = blog_repo_path or settings.blog_repo_path
        self.posts_dir = self.blog_repo_path / "_posts"  # Compatibility path only.
        self.artifact_dir = artifact_dir or Path("temp/drafts")
        self.researcher = DeepResearcher(self.artifact_dir / "research", model=ModelClient())
        self.llm = llm or self
        self.media_catalog = media_catalog

    def _call_llm(self, prompt: str) -> str:
        return ModelClient().generate("draft", prompt, artifact_dir=self.artifact_dir / "model-calls")

    def generate(self, prompt: str) -> str:
        return self._call_llm(prompt)

    async def generate_post(self, topic: CuratedTopic, *, user_context: UserContext | None = None, persist_artifacts: bool = True) -> dict[str, Any]:
        research = await self.researcher.research(topic)
        packet = research.get("packet")
        if research.get("status") == "NEEDS_RESEARCH":
            return {**research, "publishable": False}
        if not isinstance(packet, ResearchPacket):
            return {"status": "NEEDS_RESEARCH", "reasons": ["research_packet_missing"], "packet": packet, "publishable": False}
        brief = build_brief(packet, user_context or UserContext())
        if isinstance(brief, ResearchBlocked):
            return {"status": brief.status, "reasons": list(brief.reasons), "packet": packet, "brief": brief, "publishable": False}
        draft = await asyncio.to_thread(write_draft, brief, packet, self.llm)
        try:
            catalog = self.media_catalog if self.media_catalog is not None else load_catalog()
            draft, media_choice = apply_media(brief, draft, catalog)
        except (OSError, ValueError):
            report = draft.report.model_copy(update={"status": "NEEDS_REVISION", "static_passed": False,
                "issues": (*draft.report.issues, ValidationIssue(code="media_catalog_unavailable"))})
            draft = draft.model_copy(update={"report": report})
            media_choice = None
        result = {"status": draft.report.status, "publishable": False, "topic": topic, "packet": packet,
            "brief": brief, "draft": draft, "validation": draft.report, "content": draft.content, "media_choice": media_choice,
            "reasons": [issue.code for issue in draft.report.issues]}
        if not persist_artifacts:
            return result
        identity = hashlib.sha256((packet.model_dump_json() + brief.model_dump_json() + draft.model_dump_json()).encode()).hexdigest()[:24]
        directory = self.artifact_dir / identity
        try:
            # Intermediate artifacts are local review outputs, never blog _posts.
            directory.mkdir(parents=True, exist_ok=True)
            for name, model in (("packet", packet), ("brief", brief), ("draft", draft), ("validation", draft.report)):
                (directory / f"{name}.json").write_text(model.model_dump_json(indent=2), encoding="utf-8")
            packet_md = f"# Evidence packet\n\nQuestion: {packet.question}\nTopic: {packet.topic_id}\n"
            packet_md += "\n## Source snapshots\n\n" + "\n".join(
                f"- {source.url} [{source.role}]; SHA-256: {source.sha256}; error: {source.error}" for source in packet.sources)
            packet_md += "\n\n## Located evidence\n\n" + "\n\n".join(
                f"E{index + 1} [{claim.kind}, {claim.status}]: {claim.text}\n" + "; ".join(
                    f"{ref.url} [{ref.location}] SHA-256: {ref.sha256}" for ref in claim.source_refs)
                for index, claim in enumerate(packet.claims))
            (directory / "packet.md").write_text(packet_md + "\n", encoding="utf-8")
            brief_md = f"# Editorial brief\n\nKind: {brief.post_kind}\nPolicy: {brief.policy_version}\nQuestion: {brief.question}\n"
            for label, values in (("Thesis", (brief.thesis,)), ("Mechanism", (brief.key_mechanism,)),
                ("Alternatives", brief.comparison), ("Criteria", brief.decision_criteria), ("Reversal", brief.reversal_conditions)):
                brief_md += f"\n## {label}\n\n" + "\n".join(f"- {value}" for value in values)
            (directory / "brief.md").write_text(brief_md + "\n", encoding="utf-8")
            (directory / "draft.md").write_text(draft.content, encoding="utf-8")
            report_md = f"# {draft.report.status}\n\nStatic: {draft.report.static_passed}\nGrounding: {draft.report.grounding_passed}\n"
            report_md += "\n".join(f"- {issue.code} [{issue.section_id or 'document'}]: {issue.detail}" for issue in draft.report.issues)
            report_md += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in draft.report.warnings)
            (directory / "validation.md").write_text(report_md + "\n", encoding="utf-8")
        except OSError as error:
            return {**result, "status": "NEEDS_REVISION", "reasons": [*result["reasons"], "draft_artifact_write_failed", type(error).__name__]}
        try:
            title = parse_frontmatter(draft.frontmatter)["title"]
        except (ValueError, TypeError):
            title = topic.title
        return {**result, "id": identity, "title": title, "filename": "draft.md", "file_path": str(directory / "draft.md"),
            "evidence_path": str(directory / "packet.json"), "report_path": str(directory / "validation.json")}
