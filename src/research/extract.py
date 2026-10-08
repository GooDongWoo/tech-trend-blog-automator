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


def visible_markdown(content: str) -> str:
    """Render embedded HTML presentation while retaining literal Markdown code.

    Raw input bytes remain in the fetch snapshot. This view keeps located prose
    and image descriptions, never layout attributes or invisible comments.
    """
    marker = "__EVIDENCE_LITERAL_CODE__"
    while marker in content:
        marker += "_"
    literals = {}

    def protect(text):
        token = marker + str(len(literals)) + "__"
        literals[token] = text
        return token

    parts, code, fence = [], [], None
    for line in content.splitlines(keepends=True):
        if fence is not None:
            code.append(line)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                parts.append(protect("".join(code)))
                code, fence = [], None
        else:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening:
                fence, code = opening.group(1), [line]
            else:
                parts.append(line)
    if code:
        parts.append(protect("".join(code)))
    prepared = "".join(parts)
    prepared = re.sub(r"(`+)(.+?)\1", lambda match: protect(match.group()), prepared, flags=re.S)
    # Markdown autolinks are visible text, not HTML tags.
    prepared = re.sub(r"<(?:https?://[^<>\s]+|[^<>\s]+@[^<>\s]+)>",
                      lambda match: protect(match.group()), prepared)
    soup = BeautifulSoup(prepared, "html.parser")
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for image in soup.find_all("img"):
        alt = image.get("alt", "").strip()
        image.replace_with(NavigableString("\n\n" + alt + "\n\n" if alt else ""))
    for line_break in soup.find_all("br"):
        line_break.replace_with(NavigableString("\n"))
    for block in soup(["p", "div", "blockquote", "li", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "summary"]):
        block.insert_before(NavigableString("\n\n"))
        block.insert_after(NavigableString("\n\n"))
    visible = soup.get_text()
    for token, literal in literals.items():
        visible = visible.replace(token, literal)
    return visible.strip()


def metric_scope(section: Section) -> tuple[str, ...]:
    """Use preserved heading ancestry to bound a reported experiment.

    Named experiments own nested setup/results sections. Otherwise use the
    immediate parent path, conservatively distinguishing sibling studies.
    Flat explicit documents retain their single unnamed scope.
    """
    headings = tuple(part.strip().casefold() for part in section.title.split(" > "))
    for index in range(len(headings) - 1, -1, -1):
        match = re.search(r"\b(?:experiment|study|trial|실험|연구)\s+"
                          r"(?!conditions?\b|setup\b|results?\b|method\b|metrics?\b|limitations?\b|baseline\b)"
                          r"(?!조건\b|설정\b|결과\b|방법\b|지표\b|한계\b|기준선\b)"
                          r"[^\s:]+", headings[index])
        if match:
            # Keep the complete owning heading. A parsed prefix can collapse
            # 1.1/1.2, 1/1/1/2, or multiword experiment IDs into one scope.
            return headings[:index + 1]
    return headings[:-1]


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
    pdf_headings = []
    for location, block in zip(source.locations, blocks):
        if source.kind == "pdf":
            # Keep original page locations, while giving each literal span its
            # section ancestry. No text or raw snapshot bytes are rewritten.
            heading_pattern = r"(?m)^((?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*) )([A-Z][A-Z –—\-]+)\s*$"
            cursor = 0
            title = " > ".join(t for _, t in pdf_headings) or location
            for match in re.finditer(heading_pattern, block):
                if block[cursor:match.start()].strip():
                    result.append(Section(location, title, block[cursor:match.start()].strip()))
                identifier = match[1].strip()
                level = identifier.count('.') + 1
                title = _heading_context(pdf_headings, level, match[0].strip())
                cursor = match.end()
            if block[cursor:].strip():
                result.append(Section(location, title, block[cursor:].strip()))
            page_sections = [s for s in result if s.location == location]
            if len(page_sections) > 1:
                for index, section in enumerate(page_sections, 1):
                    result[result.index(section)] = Section(f"{location}#section:{index}", section.title, section.text)
            continue
        heading = None if source.kind == "pdf" else re.match(r"^#{1,6} (.+)\n", block)
        title = heading.group(1) if heading else location
        text = block[heading.end():].strip() if heading else block.strip()
        if source.kind in {"markdown", "text"}:
            text = visible_markdown(text)
        result.append(Section(location, title, text))
    return result


def verify_located_excerpt(source: SourceRecord, location: str, text: str) -> bool:
    """Literal text must occur at the asserted snapshot page/section."""
    return bool(text.strip()) and any((s.location == location or s.location.split('#')[0] == location) and text in s.text
                                      for s in extract_sections(source))


def verify_metric_context(source: SourceRecord, location: str, text: str, *,
                          baseline: str, conditions: str, target: str,
                          value: float, unit: str) -> bool:
    """Verify a single literal outcome and same-study context, never truth."""
    sections = extract_sections(source)
    owners = [s for s in sections if (s.location == location or s.location.split('#')[0] == location) and text in s.text]
    if len(owners) != 1:
        return False
    outcome = list(re.finditer(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(%|percent\b|ms\b|seconds?\b|tokens/s\b|x\b)", text, re.I))
    if len(outcome) != 1 or float(outcome[0][1]) != value or outcome[0][2].casefold() != unit.casefold():
        return False
    if target not in text:
        return False
    # Dataset names explicitly attached to outcomes delimit shared setup prose.
    # A different named dataset's split cannot be borrowed just because both
    # benchmarks appear beneath a common Experiments heading.
    datasets = set(re.findall(r"\b(?:on|for) ([A-Z][A-Za-z0-9_-]+)", source.text))
    outcome_datasets = {name for name in datasets if re.search(r"\b" + re.escape(name) + r"\b", text)}
    condition_datasets = {name for name in datasets if re.search(r"\b" + re.escape(name) + r"\b", conditions)}
    if outcome_datasets and condition_datasets and not outcome_datasets & condition_datasets:
        return False
    scoped = [s for s in sections if metric_scope(s) == metric_scope(owners[0])]
    return all(phrase.strip() and any(phrase in s.text for s in scoped)
               for phrase in (baseline, conditions, target))


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
        text = visible_markdown("\n".join(fragments))
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
