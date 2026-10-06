"""Versioned, serializable evidence contracts; no pipeline entry points or API calls.

References bind a URL, snapshot hash and extracted location. These contracts
validate provenance structure, not the truth of source prose. Review and publish
transitions also read the draft bytes to bind approval to the reviewed file.
"""
from datetime import datetime
from enum import StrEnum
import hashlib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, TypeAdapter, field_validator, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def canonical_topic_url(url: str) -> str:
    """Normalize host/scheme/default ports and discard fragments, preserving query.

    Queries and path case can select different documents and are not discarded.
    Titles never enter identity. Redirect resolution belongs to the fetcher.
    """
    validated = str(TypeAdapter(HttpUrl).validate_python(url.strip()))
    parts = urlsplit(validated)
    if parts.scheme.lower() not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("topic/source URL must be an absolute HTTP(S) URL without credentials")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and (scheme, port) not in {("https", 443), ("http", 80)}:
        host += f":{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def stable_topic_id(url: str, source_sha256: Sha256) -> str:
    """Deterministic identity of a canonical topic URL at a source snapshot."""
    # Validate callers outside a Pydantic model too.
    if len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
    identity = f"{canonical_topic_url(url)}\n{source_sha256}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_copy(self, *, update=None, deep=False):
        """Validate copied updates rather than Pydantic's trusted-data shortcut."""
        copied = super().model_copy(deep=deep)
        data = copied.model_dump()
        data.update(update or {})
        return type(self).model_validate(data)


class SourceRecord(Contract):
    url: NonEmpty
    fetched_at: datetime
    title: NonEmpty
    kind: Literal["html", "pdf", "markdown", "text", "run_log"]
    sha256: Sha256 | None = None
    text: str = ""
    locations: tuple[NonEmpty, ...] = ()
    error: NonEmpty | None = None

    @field_validator("fetched_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at requires a timezone")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.kind == "run_log" and self.url.startswith("file:///"):
            if not urlsplit(self.url).path:
                raise ValueError("run_log requires an inspectable path")
        else:
            canonical_topic_url(self.url)
        if self.error is None and (self.sha256 is None or not self.text.strip() or not self.locations):
            raise ValueError("successful source requires an inspectable snapshot, hash and locations")
        return self


class SourceRef(Contract):
    url: NonEmpty
    sha256: Sha256
    location: NonEmpty

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return canonical_topic_url(value)


class MetricContext(Contract):
    value: float = Field(allow_inf_nan=False)
    unit: NonEmpty
    target: NonEmpty
    baseline: NonEmpty
    conditions: NonEmpty
    run_record: SourceRecord | None = None

    @field_validator("run_record")
    @classmethod
    def inspectable_run(cls, value: SourceRecord | None) -> SourceRecord | None:
        if value is not None and (value.kind != "run_log" or value.error is not None):
            raise ValueError("experience requires an inspectable run_log record")
        return value


class ClaimKind(StrEnum):
    SOURCE_CLAIM = "source_claim"
    MEASUREMENT = "measurement"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class EvidenceClaim(Contract):
    text: NonEmpty
    kind: ClaimKind
    source_refs: tuple[SourceRef, ...] = ()
    metric_context: MetricContext | None = None
    status: Literal["unverified", "verified", "disputed"] = "unverified"

    @model_validator(mode="after")
    def validate_provenance(self):
        if self.kind in {ClaimKind.SOURCE_CLAIM, ClaimKind.INFERENCE} and not self.source_refs:
            raise ValueError("source_refs are required for source claims and inference premises")
        if self.kind == ClaimKind.MEASUREMENT:
            if self.metric_context is None:
                raise ValueError("measurement requires metric_context")
            if not self.source_refs and self.metric_context.run_record is None:
                raise ValueError("measurement requires source or run record provenance")
        return self


class ResearchPacket(Contract):
    topic_id: NonEmpty
    question: NonEmpty
    sources: tuple[SourceRecord, ...]
    claims: tuple[EvidenceClaim, ...] = ()
    gaps: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def validate_references(self):
        snapshots = {}
        for source in self.sources:
            url = source.url if source.kind == "run_log" and source.url.startswith("file:///") else canonical_topic_url(source.url)
            if url in snapshots:
                raise ValueError("duplicate canonical source URL")
            snapshots[url] = source
        if not any(source.error is None for source in self.sources):
            raise ValueError("research packet requires an inspectable source")
        for claim in self.claims:
            for ref in claim.source_refs:
                source = snapshots.get(ref.url)
                if source is None or source.error is not None or ref.sha256 != source.sha256 or ref.location not in source.locations:
                    raise ValueError("reference does not match an inspectable source snapshot/location")
        return self


class UserContext(Contract):
    goals: tuple[NonEmpty, ...] = ()
    constraints: tuple[NonEmpty, ...] = ()
    interests: tuple[NonEmpty, ...] = ()
    experience_refs: tuple[SourceRecord, ...] = ()

    @field_validator("experience_refs")
    @classmethod
    def require_run_logs(cls, value: tuple[SourceRecord, ...]) -> tuple[SourceRecord, ...]:
        for record in value:
            MetricContext.inspectable_run(record)
        return value


class EditorialBrief(Contract):
    topic_id: NonEmpty
    post_kind: Literal["paper", "tool", "protocol", "design_comparison"]
    thesis: NonEmpty
    comparison: tuple[NonEmpty, ...]
    decision_criteria: tuple[NonEmpty, ...] = Field(min_length=1)
    reversal_conditions: tuple[NonEmpty, ...] = Field(min_length=1)


class DraftStatus(StrEnum):
    NEEDS_RESEARCH = "NEEDS_RESEARCH"
    NEEDS_REVISION = "NEEDS_REVISION"
    REVIEW_READY = "REVIEW_READY"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class DraftArtifact(Contract):
    id: NonEmpty
    topic_id: NonEmpty
    content_path: Path
    content_sha256: Sha256
    evidence_path: Path
    report_path: Path
    status: DraftStatus = DraftStatus.NEEDS_RESEARCH
    media_paths: tuple[Path, ...] = ()
    approved_sha256: Sha256 | None = None

    def model_copy(self, *, update=None, deep=False):
        copied = super().model_copy(update=update, deep=deep)
        if copied.status != self.status:
            raise ValueError("state changes require transition() and its hash check")
        if self.status in {DraftStatus.APPROVED, DraftStatus.PUBLISHED} and copied != self:
            raise ValueError("approval-bound draft edits require revision and renewed approval")
        return copied

    @model_validator(mode="after")
    def approval_bound_to_content(self):
        if self.status in {DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
            if self.approved_sha256 != self.content_sha256:
                raise ValueError("approved_sha256 must match the reviewed content hash")
        elif self.approved_sha256 is not None:
            raise ValueError("unapproved draft cannot retain approved_sha256")
        return self

    def transition(self, target: DraftStatus | str, *, content_sha256: str | None = None) -> "DraftArtifact":
        """Return a new state; approval/publish require reviewed and disk hashes.

        File access errors propagate as visible failures. A changed approved file
        must return to NEEDS_REVISION and undergo review again before publication.
        """
        target = DraftStatus(target)
        allowed = {
            DraftStatus.NEEDS_RESEARCH: {DraftStatus.NEEDS_REVISION, DraftStatus.REVIEW_READY},
            DraftStatus.NEEDS_REVISION: {DraftStatus.NEEDS_RESEARCH, DraftStatus.REVIEW_READY},
            DraftStatus.REVIEW_READY: {DraftStatus.NEEDS_RESEARCH, DraftStatus.NEEDS_REVISION, DraftStatus.APPROVED},
            DraftStatus.APPROVED: {DraftStatus.NEEDS_REVISION, DraftStatus.PUBLISHED},
            DraftStatus.PUBLISHED: set(),
        }
        if target not in allowed[self.status]:
            raise ValueError(f"illegal transition: {self.status} -> {target}")
        if target in {DraftStatus.APPROVED, DraftStatus.PUBLISHED}:
            if content_sha256 != self.content_sha256:
                raise ValueError("explicit reviewed content hash check required")
            if hashlib.sha256(self.content_path.read_bytes()).hexdigest() != self.content_sha256:
                raise ValueError("draft file hash changed; approval is invalid")
        data = self.model_dump()
        data.update(status=target, approved_sha256=self.content_sha256 if target in {DraftStatus.APPROVED, DraftStatus.PUBLISHED} else None)
        return type(self).model_validate(data)
