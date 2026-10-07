"""Optional media from inspected bytes with documented permission.

The catalog is a maintainer-reviewed observation record, never LLM output.
Static matching cannot establish arbitrary scene prose or licenses as true.
"""
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import field_validator, model_validator

from src.editorial.models import Contract, DraftSection, DraftText, EditorialBrief, NonEmpty, Sha256, ValidationIssue, ValidationReport

CATALOG_PATH = Path(__file__).resolve().parents[2] / "assets" / "memes" / "catalog.json"
IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")
MEDIA_INPUT = re.compile(r"!\[|<img\b|<picture\b|<video\b|<iframe\b", re.I)
_MEDIA_PROSE = re.compile(r"(?:짤|밈|GIF)\s*(?:설명|속|에서는|에서)|이\s*(?:짤|밈|GIF)\b", re.I)


class MemeAsset(Contract):
    id: NonEmpty
    file: NonEmpty
    observed_scene: NonEmpty
    alt: NonEmpty
    visual_status: Literal["inspected", "unknown"]
    inspected_sha256: Sha256
    source_note: NonEmpty
    use_note: NonEmpty
    usage_status: Literal["approved", "unknown", "excluded"]
    contexts: tuple[NonEmpty, ...] = ()
    post_kinds: tuple[Literal["paper", "tool", "protocol", "design_comparison"], ...] = ()

    @field_validator("file")
    @classmethod
    def local_filename(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.gif", value):
            raise ValueError("media file must be a plain local GIF filename")
        return value

    @field_validator("alt")
    @classmethod
    def plain_alt(cls, value):
        if re.search(r"[\[\]()<>\r\n\\]", value):
            raise ValueError("alt must be plain text without Markdown or HTML")
        return value

    @property
    def url(self):
        return f"/assets/images/memes/{self.file}"


class MemeCatalog(Contract):
    version: Literal["1.0"] = "1.0"
    asset_root: Path
    assets: tuple[MemeAsset, ...] = ()
    excluded_assets: tuple[MemeAsset, ...] = ()

    @model_validator(mode="after")
    def unique_assets(self):
        all_assets = (*self.assets, *self.excluded_assets)
        if len({item.id for item in all_assets}) != len(all_assets) or len({item.file for item in all_assets}) != len(all_assets):
            raise ValueError("catalog asset IDs and filenames must be unique")
        return self


class MediaChoice(Contract):
    id: NonEmpty
    asset_path: Path
    inspected_sha256: Sha256
    alt: NonEmpty
    url: NonEmpty
    context: NonEmpty
    source_note: NonEmpty
    use_note: NonEmpty

    @field_validator("alt")
    @classmethod
    def plain_alt(cls, value):
        return MemeAsset.plain_alt(value)

    @model_validator(mode="after")
    def local_url(self):
        if self.url != f"/assets/images/memes/{self.asset_path.name}":
            raise ValueError("media URL must match its local asset filename")
        MemeAsset.local_filename(self.asset_path.name)
        return self


def load_catalog(path: Path = CATALOG_PATH) -> MemeCatalog:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "asset_root" in data:
        raise ValueError("catalog must be an object; asset root is derived from its local path")
    return MemeCatalog(**data, asset_root=path.parent.resolve())


def _asset_issue(item, root):
    if item.visual_status != "inspected" or item.usage_status != "approved":
        return "media_use_not_verified"
    path = root / item.file
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return "missing_media_asset"
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing_media_asset"
    if actual != item.inspected_sha256:
        return "media_asset_changed"
    return None


def choose_media(brief: EditorialBrief, draft: DraftText, catalog: MemeCatalog) -> MediaChoice | None:
    """Return 0 or 1 GIF after validated prose; technical visuals take priority."""
    if (not draft.sections or draft.report is None or draft.report.status != "REVIEW_READY"
        or not draft.report.static_passed or not draft.report.grounding_passed or draft.report.issues):
        return None
    if re.search(r"```|~~~|!\[|<img\b|<svg\b|<table\b|^\s*\|?.*\|.*\n\s*\|?\s*:?-{3}", draft.content, re.I | re.M):
        return None
    argument = "\n".join((brief.thesis, brief.key_mechanism or "", *brief.comparison, *brief.decision_criteria, *brief.adoption_constraints)).casefold()
    body = "\n".join(section.text for section in draft.sections).casefold()
    for item in sorted(catalog.assets, key=lambda item: item.id):
        if brief.post_kind not in item.post_kinds or _asset_issue(item, catalog.asset_root):
            continue
        for context in sorted(item.contexts):
            if context.casefold() in argument and context.casefold() in body:
                if validate_media(draft.content + f"\n\n![{item.alt}]({item.url})", catalog):
                    continue
                return MediaChoice(id=item.id, asset_path=(catalog.asset_root / item.file).resolve(),
                    inspected_sha256=item.inspected_sha256, alt=item.alt, url=item.url,
                    context=context, source_note=item.source_note, use_note=item.use_note)
    return None


def render_media(choice: MediaChoice) -> str:
    """Accessible image only, with no caption or imagined quote."""
    if not isinstance(choice, MediaChoice):
        raise TypeError("render_media requires a verified MediaChoice")
    try:
        valid = hashlib.sha256(choice.asset_path.read_bytes()).hexdigest() == choice.inspected_sha256
    except OSError:
        valid = False
    if not valid:
        raise ValueError("media asset missing or changed after inspection")
    return f"![{choice.alt}]({choice.url})"


def validate_media(content: str, catalog: MemeCatalog) -> tuple[ValidationIssue, ...]:
    """Require canonical inline syntax, inspected alt, approved bytes and <=1 image."""
    issues = []
    def issue(code):
        issues.append(ValidationIssue(code=code))
    images = list(IMAGE.finditer(content))
    without_images = IMAGE.sub("", content)
    unsupported = len(re.findall(r"!\[|<img\b|<picture\b|<video\b|<iframe\b", without_images, re.I))
    if len(images) + unsupported > 1:
        issue("excessive_media_count")
    if unsupported:
        issue("unsupported_media_syntax")
    if _MEDIA_PROSE.search(without_images):
        issue("unsupported_media_prose")
    paragraphs = re.split(r"\r?\n\s*\r?\n", content)
    for index, paragraph in enumerate(paragraphs):
        matches = list(IMAGE.finditer(paragraph))
        if matches and paragraph.strip() != matches[0].group():
            issue("unsupported_media_caption")
        if matches and index + 1 < len(paragraphs):
            following = paragraphs[index + 1].strip()
            if re.match(r"(?:>|[*_]{1,2}(?!\s)|[\"'“‘]|<figcaption\b)", following, re.I):
                issue("unsupported_media_caption")
    by_url = {item.url: item for item in catalog.assets}
    for match in images:
        alt, url = match.groups()
        item = by_url.get(url)
        if item is None:
            issue("unregistered_media")
            continue
        if alt != item.alt:
            issue("unverified_media_alt")
        asset_issue = _asset_issue(item, catalog.asset_root)
        if asset_issue:
            issue(asset_issue)
        if (alt and alt in without_images) or re.search(r"^\s*\*?▲", without_images, re.M):
            issue("duplicate_media_caption")
    return tuple(issues)


def apply_media(brief: EditorialBrief, draft: DraftText, catalog: MemeCatalog) -> tuple[DraftText, MediaChoice | None]:
    """Attach a verified image locally; Unit 6 owns asset persistence."""
    issues = validate_media(draft.content, catalog)
    # This boundary accepts prose before selection, not model-selected media.
    # Registered paths and factual alt alone do not establish relevance or a
    # MediaChoice that Unit 6 can persist and bind to approval.
    if MEDIA_INPUT.search(draft.content) and not any(
        issue.code == "model_supplied_media" for issue in (draft.report.issues if draft.report else ())
    ):
        issues = (*issues, ValidationIssue(code="model_supplied_media"))
    if issues:
        prior = draft.report
        report = ValidationReport(status="NEEDS_REVISION", issues=(*(prior.issues if prior else ()), *issues),
            static_passed=False, grounding_passed=prior.grounding_passed if prior else False,
            warnings=prior.warnings if prior else ())
        return draft.model_copy(update={"report": report}), None
    choice = choose_media(brief, draft, catalog)
    if choice is None:
        return draft, None
    try:
        rendered = render_media(choice)
    except ValueError:
        report = draft.report.model_copy(update={"status": "NEEDS_REVISION", "static_passed": False,
            "issues": (*draft.report.issues, ValidationIssue(code="media_asset_changed"))})
        return draft.model_copy(update={"report": report}), None
    sid = "media"
    while sid in {section.id for section in draft.sections}:
        sid += "_image"
    return draft.model_copy(update={"sections": (*draft.sections, DraftSection(id=sid, text=rendered))}), choice
