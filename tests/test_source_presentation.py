"""Presentation markup is not a metric; SDK identity survives benchmarks."""
import hashlib

import pytest

from src.curator.matcher import CuratedTopic
from src.editorial.brief import build_brief
from src.editorial.models import EditorialBrief, ResearchBlocked, SourceRecord, UserContext
from src.research.extract import extract_sections, markdown_sections, snapshot_text
from src.research.fetch import fetch_source
from src.research.packet import build_packet
from test_research import http_fixture


TOOL = """# Queue library

## API
Persist the request before acknowledgement.

## Baseline alternatives
Compare with the in-memory queue.

## Deployment constraints
Requires durable storage; unsuitable when writes are unavailable.
"""


def inspect(tmp_path, raw, *, title=None):
    detected_title, sections = markdown_sections(raw)
    text = snapshot_text(sections, "markdown")
    source = SourceRecord(url="https://example.invalid/readme.md", kind="markdown", title=title or detected_title,
        text=text, sha256=hashlib.sha256(raw.encode()).hexdigest(), locations=tuple(section.location for section in sections),
        fetched_at="2026-10-08T00:00:00Z", role="primary")
    topic = CuratedTopic(rank=1, title=source.title, url=source.url, source="fixture", one_line_summary="queue",
        relevance_reason="reliability", suggested_angle="When should a durable queue be adopted?")
    packet = build_packet(topic, [source], artifact_dir=tmp_path)
    return source, packet, build_brief(packet, UserContext())


@pytest.mark.parametrize("image", ['<p><img width="80%" src="layout.png"></p>',
    '<div align="center"><a href="https://example.invalid"><img width="70%" style="height:90%" src="layout.png"></a></div>'])
def test_display_only_html_does_not_become_metric_claim(tmp_path, image):
    raw = TOOL + "\n" + image + "\n"
    source, packet, brief = inspect(tmp_path, raw)
    assert isinstance(brief, EditorialBrief), getattr(brief, "reasons", None)
    assert brief.post_kind == "tool"
    assert len(packet.claims) == 3
    assert not any("img" in claim.text or "%" in claim.text for claim in packet.claims)
    assert packet.sources[0] == source


def test_mixed_markdown_html_keeps_visible_prose_alt_code_and_raw_bytes(tmp_path, http_fixture):
    url = "https://example.invalid/README.md"
    code = '<p><img width="80%" alt="example syntax"></p>'
    raw = (TOOL + '\n<p>Visible <b>mechanism</b> details.</p>\n'
        '<p><img width="80%" alt="Queue state diagram" src="layout.png"></p>\n'
        '<!-- hidden comment -->\n<div></div>\n'
        f'```html\n{code}\n```\n\nUse `{code}` as an example.\n').encode()
    http_fixture[url] = (200, raw, "text/markdown", None)
    source = fetch_source(url, snapshot_dir=tmp_path / "sources")
    assert source.error is None
    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    assert source.snapshot_path.with_suffix(".bin").read_bytes() == raw
    assert "Visible mechanism details." in source.text
    assert "Queue state diagram" in source.text
    assert f"```html\n{code}\n```" in source.text
    assert f"`{code}`" in source.text
    assert "hidden comment" not in source.text and "<div></div>" not in source.text
    sections = extract_sections(source)
    assert len(sections) == len(source.locations)
    assert sections[-1].location == source.locations[-1]


@pytest.mark.parametrize("visible", ['<p>Throughput improves by 25%.</p>',
    '<img width="80%" alt="Throughput improves by 25%." src="chart.png">'])
def test_visible_quantitative_html_or_alt_still_requires_metric_context(tmp_path, visible):
    _, packet, brief = inspect(tmp_path, TOOL + "\n" + visible)
    assert any(claim.text == "Throughput improves by 25%." for claim in packet.claims)
    assert isinstance(brief, ResearchBlocked)
    assert any(reason.startswith("missing_metric_context:") for reason in brief.reasons)


def test_preexisting_mixed_snapshot_retains_original_text_and_locations(tmp_path):
    _, packet, _ = inspect(tmp_path, TOOL)
    source = packet.sources[0]
    raw = source.text + '\n<p><img width="80%" src="layout.png"></p>'
    source = source.model_copy(update={"text": raw})
    topic = CuratedTopic(rank=1, title=source.title, url=source.url, source="fixture", one_line_summary="queue",
        relevance_reason="reliability", suggested_angle=packet.question)
    rebuilt = build_packet(topic, [source], artifact_dir=tmp_path / "frozen")
    brief = build_brief(rebuilt, UserContext())
    assert isinstance(brief, EditorialBrief), getattr(brief, "reasons", None)
    assert rebuilt.sources[0].text == raw and rebuilt.sources[0].locations == source.locations
    assert len(rebuilt.claims) == 3


def test_untyped_sdk_readme_with_benchmark_is_tool(tmp_path):
    raw = TOOL.replace("# Queue library", "# PageIndex") + """
## Quickstart
```bash
pip install -U pageindex
```

## Benchmarks
The benchmark dataset contains documents.
"""
    _, _, brief = inspect(tmp_path, raw)
    assert isinstance(brief, EditorialBrief), getattr(brief, "reasons", None)
    assert brief.post_kind == "tool" and brief.study is None


@pytest.mark.parametrize("identity", ["Research paper", "Queue benchmark report"])
def test_genuine_paper_identity_takes_precedence_over_reproduction_installation(tmp_path, identity):
    raw = TOOL.replace("# Queue library", "# " + identity).replace("## API", "## Method") + """
## Quickstart
```bash
pip install queuebench
```
## Dataset
Study uses the QueueBench dataset.
## Metrics and experimental conditions
Throughput measured on one worker with the same workload.
## Results
Pending jobs survive restart in this study.
## Limitations
The study covers one deployment configuration.
"""
    _, _, brief = inspect(tmp_path, raw, title="QueueBench")
    assert isinstance(brief, EditorialBrief), getattr(brief, "reasons", None)
    assert brief.post_kind == "paper" and brief.study is not None
