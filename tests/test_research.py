"""Offline acceptance cases for source snapshots and inspectable evidence."""
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib
from io import BytesIO
import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from src.curator.matcher import CuratedTopic
from src.editorial.models import SourceRecord

FIXTURES = Path(__file__).parent / "fixtures"


def api(module, name):
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        pytest.fail(f"Missing Unit 2 behavior: {module}.{name}")


@pytest.fixture
def topic():
    return CuratedTopic(rank=1, title="Fixture queue", url="https://docs.example.invalid/queue",
                        source="fixture", one_line_summary="SUMMARY MUST NEVER BECOME EVIDENCE",
                        relevance_reason="Queue reliability", suggested_angle="Compare retry behavior")


@pytest.fixture
def http_fixture(monkeypatch, tmp_path):
    responses = {}
    monkeypatch.chdir(tmp_path)

    def request(client, method, url, **kwargs):
        url = str(url)
        assert method == "GET" and url in responses, f"Unexpected fixture request: {method} {url}"
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        status, body, content_type, final_url = value
        return httpx.Response(status, content=body, headers={"content-type": content_type},
                              request=httpx.Request("GET", final_url or url))

    monkeypatch.setattr(httpx.Client, "request", request)
    return responses


def serve(responses, url, fixture="source.html", kind="text/html", status=200, final_url=None):
    responses[url] = (status, (FIXTURES / fixture).read_bytes(), kind, final_url)


def fetch(url):
    return api("src.research.fetch", "fetch_source")(url)


def sections(source):
    return api("src.research.extract", "extract_sections")(source)


def packet(topic, sources):
    return api("src.research.packet", "build_packet")(topic, sources)


def test_html_snapshot_preserves_late_sections_and_provenance(topic, http_fixture):
    prefix = "<p>Unrelated setup paragraph.</p>" * 160
    body = ("<html><title>Long queue document</title><body><nav>Navigation</nav>" + prefix
            + '<h2 id="retry">Retry behavior</h2><p>A failed worker leaves the job pending for retry.</p>'
            + "<footer>Footer</footer></body></html>").encode()
    http_fixture[topic.url] = (200, body, "text/html", "https://docs.example.invalid/final")
    source = fetch(topic.url)
    assert source.error is None
    assert source.url == topic.url
    assert source.final_url == "https://docs.example.invalid/final"
    assert source.fetched_at.utcoffset() is not None
    assert source.sha256 == hashlib.sha256(body).hexdigest()
    assert source.text.count("Unrelated setup paragraph.") == 160
    assert "pending for retry" in source.text
    assert "Navigation" not in source.text and "Footer" not in source.text
    retry = next(item for item in sections(source) if "pending for retry" in item.text)
    assert "retry" in retry.location
    saved = json.loads(source.snapshot_path.read_text(encoding="utf-8"))
    assert saved["text"] == source.text
    assert source.snapshot_path.with_suffix(".bin").read_bytes() == body
    assert "pending for retry" in source.snapshot_path.with_suffix(".md").read_text(encoding="utf-8")


def test_repository_readme_uses_default_branch_endpoint(http_fixture):
    url = "https://github.com/fixture/queue"
    endpoint = "https://api.github.com/repos/fixture/queue/readme"
    serve(http_fixture, endpoint, "README.md", "application/vnd.github.raw+json")
    source = fetch(url)
    assert source.error is None
    assert source.kind == "markdown" and source.role == "primary"
    assert source.url == url and source.final_url == endpoint
    assert "enqueue(job)" in source.text
    assert any(item.title == "API" and "ack(job_id)" in item.text for item in sections(source))


def test_pdf_packet_binds_actual_page_text(topic, http_fixture):
    topic.url = "https://docs.example.invalid/paper.pdf"
    serve(http_fixture, topic.url, "source.pdf", "application/pdf")
    source = fetch(topic.url)
    result = packet(topic, [source])
    assert source.kind == "pdf" and source.error is None
    assert sections(source)[0].location == "page:1"
    assert "persists pending jobs" in sections(source)[0].text
    assert result.claims[0].source_refs[0].location == "page:1"
    assert result.claims[0].source_refs[0].sha256 == source.sha256
    assert result.claims[0].text in source.text
    assert list(Path("temp/research/packets").glob("*.json"))
    assert list(Path("temp/research/packets").glob("*.md"))


@pytest.mark.parametrize("content_type,url", [
    ("application/pdf", "https://docs.example.invalid/download"),
    ("application/octet-stream", "https://docs.example.invalid/source.pdf"),
])
def test_pdf_format_detected_by_body_and_type(topic, http_fixture, content_type, url):
    topic.url = url
    serve(http_fixture, url, "source.pdf", content_type)
    assert fetch(url).kind == "pdf"


def test_blank_pdf_returns_visible_blocked_result(topic, http_fixture):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    topic.url = "https://docs.example.invalid/blank.pdf"
    http_fixture[topic.url] = (200, output.getvalue(), "application/pdf", None)
    source = fetch(topic.url)
    result = packet(topic, [source])
    assert source.error == "unreadable_pdf"
    assert result.status == "NEEDS_RESEARCH"
    assert "unreadable_pdf" in result.reasons
    assert result.sources[0] == source
    assert topic.one_line_summary not in source.text
    assert source.snapshot_path.is_file()


def test_pdf_with_unreadable_page_blocks_partial_extraction(topic, http_fixture):
    from pypdf import PdfReader
    writer = PdfWriter()
    writer.add_page(PdfReader(FIXTURES / "source.pdf").pages[0])
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    topic.url = "https://docs.example.invalid/partial.pdf"
    http_fixture[topic.url] = (200, output.getvalue(), "application/pdf", None)
    source = fetch(topic.url)
    assert source.error == "partial_extraction"
    assert "page:2" in " ".join(source.diagnostics)
    assert packet(topic, [source]).status == "NEEDS_RESEARCH"


@pytest.mark.parametrize("status,error", [(403, "http_403"), (404, "http_404"), (206, "partial_response"), (500, "http_500")])
def test_http_failures_never_use_topic_summary(topic, http_fixture, status, error):
    serve(http_fixture, topic.url, status=status)
    source = fetch(topic.url)
    result = packet(topic, [source])
    assert source.error == error
    assert result.status == "NEEDS_RESEARCH" and error in result.reasons
    assert topic.one_line_summary not in source.text


@pytest.mark.parametrize("body", [b"<html><title>Access denied</title><p>Verify you are human</p></html>",
                                  b"<html><title>Empty document</title><script>hidden()</script></html>"])
def test_blocked_and_empty_html_are_not_evidence(topic, http_fixture, body):
    http_fixture[topic.url] = (200, body, "text/html", None)
    source = fetch(topic.url)
    assert source.error in {"blocked_page", "empty_extraction"}
    assert packet(topic, [source]).status == "NEEDS_RESEARCH"


def test_network_timeout_has_diagnostic_and_blocked_state(topic, http_fixture):
    http_fixture[topic.url] = httpx.ReadTimeout("fixture timeout")
    source = fetch(topic.url)
    assert source.error == "fetch_failed"
    assert "ReadTimeout" in " ".join(source.diagnostics)
    assert packet(topic, [source]).status == "NEEDS_RESEARCH"


def test_conflicting_claims_are_preserved_and_block_drafting(topic, http_fixture):
    http_fixture[topic.url] = (200, b"<article><h2>Guarantees</h2><p>The queue supports exactly-once delivery.</p>"
                             b"<p>The queue does not support exactly-once delivery.</p></article>", "text/html", None)
    source = fetch(topic.url)
    result = packet(topic, [source])
    assert result.status == "NEEDS_RESEARCH" and "conflicting_claims" in result.reasons
    assert "supports exactly-once" in result.sources[0].text
    assert "does not support exactly-once" in result.sources[0].text
    assert len(result.packet.claims) == 2
    assert all(claim.status == "disputed" for claim in result.packet.claims)


def test_secondary_reporting_is_context_only(topic, http_fixture):
    serve(http_fixture, topic.url)
    primary = fetch(topic.url).model_copy(update={"role": "primary"})
    secondary_url = "https://news.example.invalid/queue"
    http_fixture[secondary_url] = (200, b"<article><p>Journalists report a 900% improvement.</p></article>", "text/html", None)
    secondary = fetch(secondary_url).model_copy(update={"role": "secondary"})
    result = packet(topic, [secondary, primary])
    assert result.sources[0].role == "primary"
    assert all(ref.url == primary.url for claim in result.claims for ref in claim.source_refs)
    assert secondary in result.sources
    assert packet(topic, [secondary]).status == "NEEDS_RESEARCH"


def test_context_budget_selects_relevant_late_text_without_truncating_snapshot(topic, http_fixture):
    body = ("<article><h2>Setup</h2>" + "<p>Unrelated setup paragraph.</p>" * 160
            + '<h2 id="retry">Retry behavior</h2><p>A failed worker leaves the job pending for retry.</p></article>').encode()
    http_fixture[topic.url] = (200, body, "text/html", None)
    result = packet(topic, [fetch(topic.url)])
    context = api("src.research.packet", "select_context")(result, max_chars=450)
    assert len(context) <= 450
    assert "pending for retry" in context
    assert "retry" in context and result.sources[0].url in context
    assert result.sources[0].text.count("Unrelated setup paragraph.") == 160


def test_legacy_adapter_returns_blocked_instead_of_summary(topic, http_fixture):
    from src.writer.deep_researcher import DeepResearcher
    serve(http_fixture, topic.url, status=403)
    result = asyncio.run(DeepResearcher().research(topic))
    assert result["status"] == "NEEDS_RESEARCH"
    assert result["raw_content"] == ""
    assert "http_403" in result["reasons"]


def test_writer_propagates_research_failure_without_draft(topic, http_fixture, tmp_path):
    from src.writer.blog_writer import BlogWriter
    serve(http_fixture, topic.url, status=403)
    writer = BlogWriter(tmp_path / "blog")
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_RESEARCH"
    assert list(writer.posts_dir.iterdir()) == []


def test_writer_prompt_uses_relevance_context_instead_of_prefix(topic, http_fixture, tmp_path, monkeypatch):
    from src.writer.blog_writer import BlogWriter
    body = ("<article><h2>Setup</h2>" + "<p>Unrelated setup paragraph.</p>" * 160
            + '<h2 id="retry">Retry behavior</h2><p>A failed worker leaves the job pending for retry.</p></article>').encode()
    http_fixture[topic.url] = (200, body, "text/html", None)
    writer = BlogWriter(tmp_path / "blog")
    prompts = []
    monkeypatch.setattr(writer, "_call_llm", lambda prompt: prompts.append(prompt) or "")
    asyncio.run(writer.generate_post(topic))
    assert "pending for retry" in prompts[0]
    assert "[section:2:retry]" in prompts[0]


def test_source_metadata_round_trips_without_losing_snapshot(topic, http_fixture):
    serve(http_fixture, topic.url)
    source = fetch(topic.url)
    assert SourceRecord.model_validate_json(source.model_dump_json()) == source


def test_section_extraction_rejects_unmapped_location():
    source = SourceRecord(url="https://docs.example.invalid/queue", fetched_at=datetime.now(timezone.utc),
                          title="queue", kind="text", sha256="a" * 64, text="Unmapped body",
                          locations=("page:1", "page:2"))
    with pytest.raises(ValueError, match="location"):
        sections(source)


def test_html_comments_do_not_become_source_claims(topic, http_fixture):
    http_fixture[topic.url] = (200, b"<article><!-- fabricated hidden claim --><p>Inspectable source prose.</p></article>", "text/html", None)
    source = fetch(topic.url)
    assert "fabricated hidden claim" not in source.text


def test_empty_context_budget_blocks_adapter(topic, http_fixture):
    from src.writer.deep_researcher import DeepResearcher
    http_fixture[topic.url] = (200, ("<p>" + "longunbrokenword" * 1200 + "</p>").encode(), "text/html", None)
    result = asyncio.run(DeepResearcher().research(topic))
    assert result["status"] == "NEEDS_RESEARCH"
    assert "context_budget_exhausted" in result["reasons"]
    assert result["raw_content"] == ""
    assert len(result["source"].text) > 12000


def test_pdf_content_that_looks_like_markdown_keeps_page_text():
    source = SourceRecord(url="https://docs.example.invalid/paper.pdf", fetched_at=datetime.now(timezone.utc),
                          title="paper", kind="pdf", sha256="a" * 64,
                          text="# Original PDF heading\nMeasured body text.", locations=("page:1",))
    assert sections(source)[0].text == source.text
