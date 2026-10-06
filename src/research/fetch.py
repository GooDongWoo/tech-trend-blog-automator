"""Fetch one source and persist its raw bytes and complete extracted snapshot."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from src.editorial.models import SourceRecord, canonical_topic_url
from src.research.extract import html_sections, markdown_sections, pdf_sections, snapshot_text


def fetch_source(url: str, *, snapshot_dir: Path | str = Path("temp/research/sources")) -> SourceRecord:
    original_url = canonical_topic_url(url)
    fetched_at = datetime.now(timezone.utc)
    target = original_url
    parts = urlsplit(target)
    repo = re.fullmatch(r"/([^/]+)/([^/]+?)(?:\.git)?/?", parts.path)
    github_readme = parts.hostname == "github.com" and repo is not None
    if github_readme:
        target = f"https://api.github.com/repos/{repo.group(1)}/{repo.group(2)}/readme"
    headers = {"User-Agent": "EvidenceBlogResearch/1.0", "Accept": "application/vnd.github.raw+json" if github_readme else "*/*"}
    data = dict(url=original_url, final_url=target, fetched_at=fetched_at,
                title=original_url, kind="markdown" if github_readme else "html",
                role="primary" if github_readme else "unknown")
    body = b""
    try:
        with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as client:
            response = client.get(target)
        body = response.content
        data["final_url"] = canonical_topic_url(str(response.url))
        data["sha256"] = hashlib.sha256(body).hexdigest()
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        is_pdf = body.startswith(b"%PDF-") or content_type == "application/pdf" or urlsplit(data["final_url"]).path.lower().endswith(".pdf")
        if is_pdf:
            data["kind"] = "pdf"
        elif github_readme or content_type in {"text/markdown", "application/vnd.github.raw+json"} or urlsplit(data["final_url"]).path.lower().endswith(".md"):
            data["kind"] = "markdown"
        elif content_type == "text/plain":
            data["kind"] = "text"
        if response.status_code != 200:
            data["error"] = "partial_response" if response.status_code == 206 else f"http_{response.status_code}"
            data["diagnostics"] = (f"HTTP status {response.status_code}",)
        elif data["kind"] == "pdf":
            extracted, error, diagnostics = pdf_sections(body)
            data.update(text=snapshot_text(extracted, "pdf"), locations=tuple(item.location for item in extracted),
                        error=error, diagnostics=diagnostics)
        else:
            if data["kind"] == "html":
                title, extracted = html_sections(response.text)
            else:
                title, extracted = markdown_sections(response.text)
            data.update(title=title, text=snapshot_text(extracted, data["kind"]),
                        locations=tuple(item.location for item in extracted))
            blocked_title = re.search(r"^(access denied|just a moment|attention required|robot check)", title, re.I)
            blocked_body = re.search(r"verify (?:that )?you are human|enable javascript and cookies to continue", data["text"], re.I)
            if blocked_title or blocked_body:
                data.update(error="blocked_page", diagnostics=("access challenge detected",))
            elif not extracted:
                data.update(error="empty_extraction", diagnostics=("no inspectable body text",))
    except httpx.HTTPError as error:
        data.update(error="fetch_failed", diagnostics=(type(error).__name__,))
    except (ValueError, UnicodeError) as error:
        data.update(error="extraction_failed", diagnostics=(type(error).__name__,))
    source = SourceRecord(**data)
    directory = Path(snapshot_dir)
    identity = hashlib.sha256(f"{original_url}\n{source.sha256}\n{fetched_at.isoformat()}".encode()).hexdigest()
    path = directory / f"{identity}.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        source = source.model_copy(update={"snapshot_path": path.resolve()})
        path.with_suffix(".bin").write_bytes(body)
        path.with_suffix(".md").write_text(
            f"# {source.title}\n\nURL: {source.url}\nFinal URL: {source.final_url}\n"
            f"Fetched: {source.fetched_at.isoformat()}\nSHA-256: {source.sha256}\n"
            f"Error: {source.error or 'none'}\n\n" + "\n\n".join(
                f"## {location}\n\n{text}" for location, text in zip(source.locations, source.text.split("\f"))),
            encoding="utf-8")
        path.write_text(source.model_dump_json(indent=2), encoding="utf-8")
    except OSError as error:
        source = source.model_copy(update={"error": "snapshot_write_failed", "snapshot_path": None,
                                         "diagnostics": (*source.diagnostics, type(error).__name__)})
    return source
