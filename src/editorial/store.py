"""Local immutable review bundles and durable state; never a publication target."""
import hashlib
import json
from pathlib import Path
import re
import secrets
from urllib.parse import unquote, urlsplit

from config import settings
from src.curator.matcher import CuratedTopic
from src.editorial.models import DraftArtifact, DraftStatus, ResearchBlocked, ResearchPacket, UserContext
from src.editorial.validate import _run_issue


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value):
    """Replace a complete record atomically, including after bot restart."""
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _input_hash(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _snapshot_context(context, directory):
    """Copy explicitly supplied run bytes; never re-read Vault note bodies."""
    runs, reasons = [], []
    for index, run in enumerate(context.experience_refs, 1):
        target = directory / f"R{index}.bin"
        directory.mkdir(parents=True, exist_ok=True)
        path = run.snapshot_path
        if path is None and run.url.startswith("file:///"):
            raw_path = unquote(urlsplit(run.url).path)
            if re.match(r"/[A-Za-z]:/", raw_path):
                raw_path = raw_path[1:]
            path = Path(raw_path)
        try:
            raw = path.read_bytes() if path is not None else run.text.encode("utf-8")
            target.write_bytes(raw)
        except OSError:
            reasons.append("user_context_run_snapshot_unavailable")
        local = run.model_copy(update={"snapshot_path": target})
        issue = _run_issue(local)
        if issue:
            reasons.append("user_context_" + issue)
        runs.append(local)
    return context.model_copy(update={"experience_refs": tuple(runs)}), tuple(dict.fromkeys(reasons))


class DraftStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        for external in (settings.blog_repo_path, settings.obsidian_vault_path):
            if self.root.is_relative_to(Path(external).resolve()):
                raise ValueError("review output must be outside external blog/Vault roots")

    def directory(self, draft_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16}", draft_id):
            raise ValueError("invalid draft ID")
        path = self.root / draft_id
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("draft path escaped store")
        return path

    def begin(self, topic_id: str) -> str:
        identity = secrets.token_urlsafe(12)
        directory = self.directory(identity)
        directory.mkdir(parents=True)
        write_json(directory / "run.json", {"topic_id": topic_id, "status": "NEEDS_RESEARCH", "reasons": ["generation_incomplete"]})
        return identity

    def register_topic(self, topic: CuratedTopic, *, user_context: UserContext | None = None) -> str:
        context = user_context if user_context is not None else UserContext()
        data = {"topic": topic.model_dump(mode="json"), "user_context": context.model_dump(mode="json")}
        identity = _input_hash(data)
        directory = self.root / "topics"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (identity + ".json")
        if not path.exists():
            # Identity binds original metadata; deterministic sibling paths bind
            # its run bytes through the source hashes. Re-registration cannot
            # replace an earlier card's frozen input with a later mutable file.
            _snapshot_context(context, directory / (identity + "-runs"))
            write_json(path, data)
        return identity

    def _topic_input(self, topic_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}", topic_id):
            raise ValueError("invalid topic ID")
        data = json.loads((self.root / "topics" / (topic_id + ".json")).read_text(encoding="utf-8"))
        if "topic" not in data:
            # Old topic-only cards have no recoverable user context. Preserve
            # their topic for inspection; generation will block visibly.
            topic = CuratedTopic.model_validate(data)
            if hashlib.sha256(topic.model_dump_json().encode()).hexdigest() != topic_id:
                raise ValueError("topic input changed")
            return {"topic": topic.model_dump(mode="json"), "user_context": None}
        if _input_hash(data) != topic_id:
            raise ValueError("topic input changed")
        return data

    def topic(self, topic_id: str) -> CuratedTopic:
        return CuratedTopic.model_validate(self._topic_input(topic_id)["topic"])

    def prepare_context(self, topic_id: str, draft_id: str):
        directory = self.directory(draft_id)
        try:
            data = self._topic_input(topic_id)
            write_json(directory / "input.json", data)
            if data["user_context"] is None:
                return None, ("user_context_input_unavailable",)
            context = UserContext.model_validate(data["user_context"])
        except (OSError, ValueError, KeyError, TypeError):
            return None, ("user_context_input_unavailable_or_changed",)
        frozen_runs = tuple(run.model_copy(update={"snapshot_path": self.root / "topics" / (topic_id + "-runs") / f"R{index}.bin"})
                            for index, run in enumerate(context.experience_refs, 1))
        context, reasons = _snapshot_context(context.model_copy(update={"experience_refs": frozen_runs}), directory / "input-runs")
        write_json(directory / "user-context.json", context)
        return context, reasons

    def get_draft(self, draft_id: str) -> DraftArtifact:
        directory = self.directory(draft_id)
        artifact = DraftArtifact.model_validate_json((directory / "artifact.json").read_text(encoding="utf-8"))
        if artifact.id != draft_id or any(not path.resolve().is_relative_to(directory.resolve()) for path in
            (artifact.content_path, artifact.evidence_path, artifact.report_path, *artifact.media_paths)):
            raise ValueError("artifact path or identity escaped draft")
        return artifact

    def save(self, artifact: DraftArtifact):
        write_json(self.directory(artifact.id) / "artifact.json", artifact)

    def persist(self, draft_id: str, topic_id: str, result: dict) -> DraftArtifact:
        directory = self.directory(draft_id)
        reasons = list(result.get("reasons", []))
        status = DraftStatus(result.get("status", "NEEDS_REVISION"))
        if status not in {DraftStatus.NEEDS_RESEARCH, DraftStatus.NEEDS_REVISION, DraftStatus.REVIEW_READY}:
            status = DraftStatus.NEEDS_REVISION
            reasons.append("invalid_generation_status")
        packet = result.get("packet")
        blocked = packet if isinstance(packet, ResearchBlocked) else None
        evidence = blocked.packet if blocked else packet
        sources = blocked.sources if blocked else evidence.sources if isinstance(evidence, ResearchPacket) else ()
        if not sources and result.get("source") is not None:
            sources = (result["source"],)
        snapshots = directory / "sources"
        snapshots.mkdir()
        local_sources = []
        for index, source in enumerate(sources):
            (snapshots / f"{index}.md").write_text(source.text, encoding="utf-8")
            if source.snapshot_path is not None:
                raw_path = source.snapshot_path if source.kind == "run_log" else source.snapshot_path.with_suffix(".bin")
                try:
                    raw = raw_path.read_bytes()
                    if source.sha256 and hashlib.sha256(raw).hexdigest() != source.sha256:
                        raise ValueError("source_snapshot_changed")
                    (snapshots / f"{index}.bin").write_bytes(raw)
                except (OSError, ValueError):
                    reasons.append("source_snapshot_unavailable_or_changed")
                    status = DraftStatus.NEEDS_RESEARCH
            local_path = snapshots / f"{index}.bin" if source.kind == "run_log" else snapshots / f"{index}.json"
            local = source.model_copy(update={"snapshot_path": local_path})
            write_json(snapshots / f"{index}.json", local)
            local_sources.append(local)
        if isinstance(evidence, ResearchPacket):
            replacements = {source.url: source for source in local_sources}
            evidence = evidence.model_copy(update={"sources": tuple(replacements.get(source.url, source) for source in evidence.sources)})
            packet = blocked.model_copy(update={"sources": tuple(local_sources), "packet": evidence}) if blocked else evidence
        elif blocked:
            packet = blocked.model_copy(update={"sources": tuple(local_sources)})
        write_json(directory / "packet.json", packet)
        write_json(directory / "brief.json", result.get("brief"))
        write_json(directory / "draft.json", result.get("draft"))
        (directory / "draft.md").write_text(result.get("content", ""), encoding="utf-8")
        media_paths = ()
        choice = result.get("media_choice")
        if choice is not None:
            try:
                data = choice.asset_path.read_bytes()
                if hashlib.sha256(data).hexdigest() != choice.inspected_sha256:
                    raise ValueError("media_changed")
                media_dir = directory / "media"
                media_dir.mkdir()
                target = media_dir / choice.asset_path.name
                target.write_bytes(data)
                choice = choice.model_copy(update={"asset_path": target.resolve()})
                media_paths = (target.resolve(),)
            except (OSError, ValueError):
                reasons.append("media_snapshot_unavailable_or_changed")
                status = DraftStatus.NEEDS_REVISION
        write_json(directory / "media.json", choice)
        report = result.get("validation")
        if status == DraftStatus.REVIEW_READY and (report is None or report.status != "REVIEW_READY" or
            not report.static_passed or not report.grounding_passed or report.issues or not isinstance(evidence, ResearchPacket)):
            reasons.append("incomplete_validation")
            status = DraftStatus.NEEDS_REVISION
        validation = report.model_dump(mode="json") if report else {}
        validation.update(status=status.value, reasons=reasons)
        write_json(directory / "validation.json", validation)
        write_json(directory / "run.json", {"topic_id": topic_id, "status": status.value, "reasons": reasons})
        summary = f"# {status.value}\n\nDraft: {draft_id}\n\n## Unresolved issues\n\n"
        summary += "\n".join(f"- {reason}" for reason in reasons) or "None reported."
        summary += "\n\n## Source provenance\n\n" + "\n".join(
            f"- [{source.title}]({source.url}) — {source.role}; SHA-256: {source.sha256}; locations: {', '.join(source.locations)}; error: {source.error}" for source in sources)
        summary += "\n\n## Validation\n\n```json\n" + json.dumps(validation, ensure_ascii=False, indent=2) + "\n```\n"
        if result.get("user_context") is not None:
            summary += "\n## Bound user context\n\n```json\n" + result["user_context"].model_dump_json(indent=2) + "\n```\n"
        draft = result.get("draft")
        if draft is not None:
            # Telegram delivers this report before approval. Include every
            # section, even one with no claims, and preserve uncited inferences.
            mappings = [{"section_id": section.id, "claims": [claim.model_dump(mode="json") for claim in section.claims]}
                        for section in draft.sections]
            summary += "\n## Section-to-claim map\n\n```json\n" + json.dumps(mappings, ensure_ascii=False, indent=2) + "\n```\n"
        if evidence:
            summary += "\n## Evidence packet\n\n```json\n" + evidence.model_dump_json(indent=2) + "\n```\n"
        if result.get("brief"):
            summary += "\n## Editorial brief\n\n```json\n" + result["brief"].model_dump_json(indent=2) + "\n```\n"
        summary += "\n## Selected media\n\n" + (choice.model_dump_json(indent=2) if choice else "None.")
        (directory / "review.md").write_text(summary, encoding="utf-8")
        files = [path for path in directory.rglob("*") if path.is_file() and "working" not in path.relative_to(directory).parts]
        manifest = {path.relative_to(directory).as_posix(): digest(path) for path in sorted(files)}
        write_json(directory / "manifest.json", manifest)
        artifact = DraftArtifact(id=draft_id, topic_id=topic_id, content_path=directory / "draft.md",
            content_sha256=digest(directory / "draft.md"), evidence_path=directory / "packet.json", report_path=directory / "validation.json",
            status=status, media_paths=media_paths, review_sha256=digest(directory / "manifest.json"))
        self.save(artifact)
        return artifact

    def verify(self, artifact: DraftArtifact):
        """Publication must call this again; approval does not make disk immutable."""
        directory = self.directory(artifact.id)
        if (directory / "superseded.json").exists():
            raise ValueError("draft superseded by retry")
        try:
            manifest_path = directory / "manifest.json"
            if digest(manifest_path) != artifact.review_sha256:
                raise ValueError("review manifest changed")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for name, expected in manifest.items():
                path = directory / name
                if not path.resolve().is_relative_to(directory.resolve()) or digest(path) != expected:
                    raise ValueError("review file changed: " + name)
            if digest(artifact.content_path) != artifact.content_sha256:
                raise ValueError("content hash changed")
        except OSError as error:
            raise ValueError("review file changed or missing") from error

    def approve(self, draft_id: str, expected_sha256: str) -> DraftArtifact:
        artifact = self.get_draft(draft_id)
        if artifact.content_sha256 != expected_sha256:
            raise ValueError("reviewed content hash mismatch")
        if artifact.status != DraftStatus.REVIEW_READY:
            raise ValueError("only REVIEW_READY drafts can be approved")
        try:
            self.verify(artifact)
        except ValueError:
            self.save(artifact.transition(DraftStatus.NEEDS_REVISION))
            raise
        approved = artifact.transition(DraftStatus.APPROVED, content_sha256=expected_sha256)
        self.save(approved)
        return approved
