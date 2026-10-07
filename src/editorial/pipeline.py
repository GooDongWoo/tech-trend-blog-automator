"""Generate complete local review bundles; approval never publishes by itself."""
import base64
import copy
import json
from pathlib import Path
import re

from src.editorial.models import DraftArtifact, DraftStatus
from src.editorial.store import DraftStore, write_json
from src.writer.blog_writer import BlogWriter


def approval_callback(artifact: DraftArtifact) -> str:
    encoded = base64.urlsafe_b64encode(bytes.fromhex(artifact.content_sha256)).decode().rstrip("=")
    return f"a:{artifact.id}:{encoded}"


def decode_callback(data: str) -> tuple[str, str]:
    if not re.fullmatch(r"a:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{43}", data):
        raise ValueError("invalid or obsolete approval callback")
    _, draft_id, encoded = data.split(":")
    return draft_id, base64.urlsafe_b64decode(encoded + "=").hex()


class EditorialPipeline:
    def __init__(self, output_root: Path | None = None, *, writer: BlogWriter | None = None):
        self.store = DraftStore(output_root or Path(__file__).resolve().parents[2] / "temp" / "review")
        self.writer = writer or BlogWriter()

    def register_topic(self, topic) -> str:
        return self.store.register_topic(topic)

    async def generate(self, topic_id: str) -> DraftArtifact:
        topic = self.store.topic(topic_id)
        identity = self.store.begin(topic_id)
        writer = copy.copy(self.writer)
        writer.researcher = copy.copy(self.writer.researcher)
        writer.researcher.artifact_dir = self.store.directory(identity) / "working" / "research"
        try:
            result = await writer.generate_post(topic, persist_artifacts=False)
        except Exception as error:
            result = {"status": "NEEDS_RESEARCH", "reasons": ["generation_failed", type(error).__name__]}
        return self.store.persist(identity, topic_id, result)

    def get_draft(self, draft_id: str) -> DraftArtifact:
        return self.store.get_draft(draft_id)

    def approve(self, draft_id: str, expected_sha256: str) -> DraftArtifact:
        return self.store.approve(draft_id, expected_sha256)

    async def retry(self, draft_id: str) -> DraftArtifact:
        artifact = self.get_draft(draft_id)
        if artifact.status in {DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
            raise ValueError("approved drafts require a separate new review")
        marker = self.store.directory(draft_id) / "superseded.json"
        # Write before any await: an old button becomes invalid even if retry fails.
        if marker.exists():
            raise ValueError("draft already superseded")
        write_json(marker, {"reason": "retry_requested"})
        result = await self.generate(artifact.topic_id)
        write_json(marker, {"replacement_id": result.id})
        return result

    def bind_reviewer(self, draft_id: str, chat_id: str, user_id: str):
        path = self.store.directory(draft_id) / "reviewer.json"
        recipient = {"chat_id": str(chat_id), "user_id": str(user_id)}
        if path.exists() and json.loads(path.read_text()) != recipient:
            raise ValueError("draft reviewer already bound")
        write_json(path, recipient)

    def check_reviewer(self, draft_id: str, chat_id: str, user_id: str):
        path = self.store.directory(draft_id) / "reviewer.json"
        if json.loads(path.read_text()) != {"chat_id": str(chat_id), "user_id": str(user_id)}:
            raise ValueError("unauthorized draft reviewer")
