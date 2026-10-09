"""Traceable user interests; notes and LLM output never establish expertise."""
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from config import settings
from src.llm.client import ModelClient
from src.editorial.models import SourceRecord, UserContext, UserStatement, VaultNoteRef
from .daily_scanner import DailyScanner
from .rag_checker import RAGChecker


def build_user_context(*, statements=(), note_results=(), run_logs=()) -> UserContext:
    """Use inspectable note references for interest; attach experience explicitly."""
    statements = tuple(UserStatement.model_validate(item) for item in statements)
    notes, diagnostics = [], []
    for item in note_results:
        try:
            path = Path(item["path"]).resolve()
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if item.get("sha256") and item["sha256"] != digest:
                diagnostics.append("note_snapshot_changed")
                continue
            if not text.strip():
                raise ValueError("empty note")
            snippet = item.get("snippet", text[:300]).strip()
            if not snippet or snippet not in text:
                snippet = text[:300].strip()
            notes.append(VaultNoteRef(path=path, sha256=digest,
                title=path.stem, snippet=snippet,
                retrieval_method=item.get("retrieval_method", "note_scan")))
            if item.get("daemon_status") == "unavailable":
                diagnostics.append("daemon_unavailable:local_search")
        except (OSError, UnicodeError, KeyError, ValueError):
            diagnostics.append("note_unavailable")
    public = tuple(item for item in statements if item.category == "experience" and item.publication_allowed)
    run_logs = tuple(SourceRecord.model_validate(item) for item in run_logs)
    if not notes and not statements and not run_logs:
        diagnostics.append("profile_evidence_absent")
    return UserContext(goals=tuple(item.text for item in statements if item.category == "goal"),
        constraints=tuple(item.text for item in statements if item.category == "constraint"),
        interests=tuple(dict.fromkeys([*(item.text for item in statements if item.category == "interest"), *(note.title for note in notes)])),
        statements=statements, note_refs=tuple(notes), experience_refs=run_logs,
        published_experience=public, depth="documented" if run_logs or public else "unknown",
        diagnostics=tuple(dict.fromkeys(diagnostics)))


class UserProfile(BaseModel):
    core_interests: List[str]
    knowledge_depth: Dict[str, str]
    avoid_topics: List[str]
    target_domains: List[str]
    search_keywords: List[str]
    user_context: UserContext = Field(default_factory=UserContext)

    @field_validator("knowledge_depth")
    @classmethod
    def unproven_depth_is_unknown(cls, value):
        # Legacy JSON and model prose cannot declare user expertise. Explicit
        # experience is available separately through user_context provenance.
        return {topic: "unknown" for topic in value} or {"overall": "unknown"}


class InterestProfiler:
    """Extract interests from referenced notes; experience requires explicit input."""

    def __init__(self, vault_path: Optional[Path] = None, daemon_url: Optional[str] = None):
        self.vault_path = vault_path or settings.obsidian_vault_path
        self.daemon_url = daemon_url or settings.qdrant_rag_url
        self.scanner = DailyScanner(self.vault_path)
        self.rag = RAGChecker(self.vault_path, self.daemon_url)

    def _call_llm(self, prompt: str) -> str:
        return ModelClient().generate("profile", prompt, artifact_dir=getattr(self, "artifact_dir", None))

    def build_profile(self, days: int = 14, *, statements=(), run_logs=()) -> UserProfile:
        notes = self.scanner.get_recent_daily_notes(days=days)[:5] + self.scanner.get_active_projects()[:3]
        note_results = [{"path": note["path"], "title": Path(note["path"]).stem,
                         "retrieval_method": "note_scan"} for note in notes]
        context = build_user_context(statements=statements, note_results=note_results, run_logs=run_logs)
        interests, domains, keywords = list(context.interests), [], list(context.interests)
        if context.note_refs or context.statements:
            inspectable = "\n".join([*(f"{note.title}\n{note.snippet}" for note in context.note_refs),
                                      *(item.text for item in context.statements)])
            prompt = f"""최근 기록에서 명시된 관심 주제만 JSON으로 추출하라.
노트는 경험이나 전문성의 증거가 아니다. 지식 깊이는 unknown이다.
관심 주제와 키워드는 원문에 존재하는 문자열만 사용한다.
{{"core_interests": [], "knowledge_depth": {{"overall": "unknown"}},
 "avoid_topics": [], "target_domains": [], "search_keywords": []}}
[출처가 있는 기록]
{inspectable}
"""
            try:
                cleaned = self._call_llm(prompt).strip()
                if "```" in cleaned:
                    cleaned = cleaned.split("```")[1].removeprefix("json").strip()
                suggested = UserProfile(**json.loads(cleaned))
                grounded = inspectable.casefold()
                interests = [item for item in suggested.core_interests if item.strip() and item.casefold() in grounded]
                domains = [item for item in suggested.target_domains if item.strip() and item.casefold() in grounded]
                keywords = [item for item in suggested.search_keywords if item.strip() and item.casefold() in grounded]
            except Exception:
                # Degraded interest extraction still retains actual references.
                context = context.model_copy(update={"diagnostics": (*context.diagnostics, "profile_extraction_unavailable")})
        return UserProfile(core_interests=interests,
            knowledge_depth={item: "unknown" for item in interests} or {"overall": "unknown"},
            avoid_topics=[], target_domains=domains, search_keywords=keywords, user_context=context)
