"""Extract full documents without a prefix or line-count cutoff."""
from dataclasses import dataclass
from io import BytesIO
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from pypdf import PdfReader

from src.editorial.models import SourceRecord


@dataclass(frozen=True)
class Section:
    location: str
    title: str
    text: str


def extract_sections(source: SourceRecord) -> list[Section]:
    """Form feed is the snapshot's lossless section/page boundary.

    Each location maps to one complete block. Reject mismatched mappings rather
    than claiming an arbitrary location covers unrelated source prose.
    """
    if not source.text.strip():
        return []
    blocks = source.text.split("\f")
    if len(blocks) != len(source.locations):
        raise ValueError("source location mapping does not match extracted blocks")
    result = []
    for location, block in zip(source.locations, blocks):
        heading = None if source.kind == "pdf" else re.match(r"^#{1,6} (.+)\n", block)
        title = heading.group(1) if heading else location
        text = block[heading.end():].strip() if heading else block.strip()
        result.append(Section(location, title, text))
    return result


def _location(index: int, title: str, anchor: str | None = None) -> str:
    slug = anchor or re.sub(r"[^\w-]+", "-", title.casefold()).strip("-") or "body"
    return f"section:{index}:{slug}"


def _heading_context(headings: list[tuple[int, str]], level: int, title: str) -> str:
    """Retain ancestry while replacing equal/deeper headings at a sibling."""
    while headings and headings[-1][0] >= level:
        headings.pop()
    headings.append((level, title))
    return " > ".join(text for _, text in headings)


def html_sections(content: str) -> tuple[str, list[Section]]:
    soup = BeautifulSoup(content, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or "Document"
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "head", "title"]):
        tag.decompose()
    root = soup.body or soup
    sections = []
    heading, anchor, fragments = "Document", None, []
    headings, heading_seen = [], False

    def flush():
        nonlocal fragments
        text = "\n\n".join(part for part in fragments if part.strip()).strip()
        if text or heading_seen:
            sections.append(Section(_location(len(sections) + 1, heading, anchor), heading, text))
        fragments = []

    def visit(node):
        nonlocal heading, anchor, heading_seen
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            if node.strip():
                fragments.append(str(node).strip())
        elif isinstance(node, Tag):
            if re.fullmatch(r"h[1-6]", node.name):
                flush()
                heading = _heading_context(headings, int(node.name[1]), node.get_text(" ", strip=True) or "Untitled section")
                anchor, heading_seen = node.get("id"), True
            elif node.name in {"p", "pre", "li", "table", "blockquote"}:
                fragments.append(node.get_text(" " if node.name != "pre" else "", strip=True))
            else:
                for child in node.children:
                    visit(child)

    visit(root)
    flush()
    return title, sections


def markdown_sections(content: str) -> tuple[str, list[Section]]:
    sections, fragments = [], []
    heading, title, in_code = "Document", "README", False
    headings, heading_seen = [], False

    def flush():
        nonlocal fragments
        text = "\n".join(fragments).strip()
        if text or heading_seen:
            sections.append(Section(_location(len(sections) + 1, heading), heading, text))
        fragments = []

    for line in content.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_code = not in_code
        match = None if in_code else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            heading = _heading_context(headings, len(match.group(1)), match.group(2))
            heading_seen = True
            if title == "README":
                title = match.group(2)
        else:
            fragments.append(line)
    flush()
    return title, sections


def pdf_sections(content: bytes) -> tuple[list[Section], str | None, tuple[str, ...]]:
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            return [], "unreadable_pdf", ("encrypted_pdf",)
        result, missing = [], []
        for index, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            location = f"page:{index}"
            if sum(character.isalpha() for character in text) < 8 or text.count("\ufffd") > len(text) * .05:
                missing.append(location)
            result.append(Section(location, f"Page {index}", text))
        if not result or len(missing) == len(result):
            return result, "unreadable_pdf", tuple(missing or ["no_pages"])
        if missing:
            return result, "partial_extraction", tuple(missing)
        return result, None, ()
    except Exception as error:
        return [], "unreadable_pdf", (f"pdf_parser:{type(error).__name__}",)


def snapshot_text(sections: list[Section], kind: str) -> str:
    if kind == "pdf":
        return "\f".join(section.text for section in sections)
    return "\f".join(f"# {section.title}\n\n{section.text}" for section in sections)
