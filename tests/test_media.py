"""Observed scenes and explicit usage permission bound every optional GIF."""
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from drafting_fixtures import drafting_input, response
from src.editorial.models import DraftSection, DraftText, EditorialBrief, ValidationReport


def api():
    try:
        return importlib.import_module("src.editorial.media")
    except ModuleNotFoundError:
        pytest.fail("Unit 5 media selection and validation are missing")


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "pixel.gif"
    path.write_bytes((Path(__file__).parent / "fixtures" / "local.gif").read_bytes())
    item = dict(id="pixel", file="pixel.gif", observed_scene="단색 픽셀이며 인물이나 대사가 없다.",
        alt="단색 픽셀", visual_status="inspected", inspected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_note="Locally authored synthetic test fixture.", use_note="Fixture author permits offline test use.",
        usage_status="approved", contexts=["fixture debugging"], post_kinds=["tool"])
    return api().MemeCatalog(asset_root=tmp_path, assets=[item])


def brief(kind="tool", thesis="fixture debugging needs bounded retries"):
    return EditorialBrief(topic_id="media-fixture", post_kind=kind, thesis=thesis,
        comparison=("retry", "stop"), decision_criteria=("retry budget",), reversal_conditions=("exhausted",))


def draft(text="fixture debugging has a bounded retry budget", ready=True):
    return DraftText(sections=(DraftSection(id="decision", text=text),),
        report=ValidationReport(status="REVIEW_READY" if ready else "NEEDS_REVISION",
            static_passed=ready, grounding_passed=ready))


def test_unrelated_protocol_and_empty_or_blocked_drafts_get_no_meme(catalog):
    assert api().choose_media(brief("protocol", "OIDC delegation scopes"), draft("OIDC message flow"), catalog) is None
    assert api().choose_media(brief(), DraftText(), catalog) is None
    assert api().choose_media(brief(), draft(ready=False), catalog) is None


def test_context_must_be_in_the_written_argument_and_brief(catalog):
    assert api().choose_media(brief(), draft("unrelated protocol"), catalog) is None
    assert api().choose_media(brief(thesis="protocol delegation"), draft(), catalog) is None


def test_relevant_fixture_has_one_stable_choice_and_factual_alt_without_caption(catalog):
    choices = [api().choose_media(brief(), draft(), catalog) for _ in range(5)]
    assert all(choice == choices[0] for choice in choices)
    assert choices[0].alt == "단색 픽셀"
    assert api().render_media(choices[0]) == "![단색 픽셀](/assets/images/memes/pixel.gif)"
    assert api().validate_media(api().render_media(choices[0]), catalog) == ()


def test_choice_is_stable_across_catalog_order(catalog):
    (catalog.asset_root / "another.gif").write_bytes((catalog.asset_root / "pixel.gif").read_bytes())
    another = catalog.assets[0].model_copy(update={"id": "another", "file": "another.gif"})
    ordered = catalog.model_copy(update={"assets": (*catalog.assets, another)})
    reversed_catalog = catalog.model_copy(update={"assets": tuple(reversed(ordered.assets))})
    assert api().choose_media(brief(), draft(), ordered).id == "another"
    assert api().choose_media(brief(), draft(), reversed_catalog).id == "another"


def test_optional_image_is_omitted_if_it_would_repeat_existing_prose(catalog):
    text = draft("fixture debugging uses 단색 픽셀 as a sample")
    result, choice = api().apply_media(brief(), text, catalog)
    assert choice is None
    assert result.content == text.content


@pytest.mark.parametrize("text", [
    "fixture debugging\n```python\nretry(job)\n```",
    "fixture debugging\n```mermaid\nflowchart LR\n A --> B\n```",
    "fixture debugging\n| Case | Result |\n| --- | --- |\n| retry | blocked |",
    "fixture debugging\n![existing](diagram.svg)",
])
def test_existing_code_diagram_table_or_image_takes_priority(catalog, text):
    assert api().choose_media(brief(), draft(text), catalog) is None


@pytest.mark.parametrize("update", [
    {"usage_status": "unknown"}, {"usage_status": "excluded"}, {"visual_status": "unknown"},
    {"inspected_sha256": "0" * 64},
])
def test_unknown_rights_unseen_or_changed_assets_are_never_selected(catalog, update):
    item = catalog.assets[0].model_copy(update=update)
    altered = catalog.model_copy(update={"assets": (item,)})
    assert api().choose_media(brief(), draft(), altered) is None


def test_missing_local_asset_is_not_selected_or_rendered(catalog):
    choice = api().choose_media(brief(), draft(), catalog)
    choice.asset_path.unlink()
    assert api().choose_media(brief(), draft(), catalog) is None
    with pytest.raises(ValueError, match="asset"):
        api().render_media(choice)
    assert "missing_media_asset" in {i.code for i in api().validate_media("![단색 픽셀](/assets/images/memes/pixel.gif)", catalog)}


@pytest.mark.parametrize("suffix,code", [
    ("\n*▲ 단색 픽셀*", "duplicate_media_caption"),
    ("\n단색 픽셀", "duplicate_media_caption"),
    ("\n짤 설명: 개발자가 모니터를 던진다.", "unsupported_media_prose"),
    ("\n이 GIF 속 인물이 '배포 성공'이라고 말한다.", "unsupported_media_prose"),
    ("\n*개발자가 컴퓨터를 던진다.*", "unsupported_media_caption"),
    ("\n> '배포 성공!'", "unsupported_media_caption"),
])
def test_duplicate_caption_and_scene_or_quote_explanations_block(catalog, suffix, code):
    content = "![단색 픽셀](/assets/images/memes/pixel.gif)" + suffix
    assert code in {i.code for i in api().validate_media(content, catalog)}


@pytest.mark.parametrize("content,code", [
    ("![개발자가 컴퓨터를 던진다](/assets/images/memes/pixel.gif)", "unverified_media_alt"),
    ("![](/assets/images/memes/pixel.gif)", "unverified_media_alt"),
    ("![단색 픽셀](/assets/images/memes/absent.gif)", "unregistered_media"),
    ("![단색 픽셀](https://example.invalid/pixel.gif)", "unregistered_media"),
    ("![단색 픽셀][gif]\n[gif]: /assets/images/memes/pixel.gif", "unsupported_media_syntax"),
    ('<img src="/assets/images/memes/pixel.gif" alt="단색 픽셀">', "unsupported_media_syntax"),
    ("![단색 픽셀](/assets/images/memes/pixel.gif)\n![단색 픽셀](/assets/images/memes/pixel.gif)", "excessive_media_count"),
])
def test_media_must_match_registered_scene_path_and_count(catalog, content, code):
    assert code in {i.code for i in api().validate_media(content, catalog)}


def test_unknown_rights_and_changed_bytes_block_existing_media(catalog):
    unknown = catalog.model_copy(update={"assets": (catalog.assets[0].model_copy(update={"usage_status": "unknown"}),)})
    content = "![단색 픽셀](/assets/images/memes/pixel.gif)"
    assert "media_use_not_verified" in {i.code for i in api().validate_media(content, unknown)}
    (catalog.asset_root / "pixel.gif").write_bytes(b"changed")
    assert "media_asset_changed" in {i.code for i in api().validate_media(content, catalog)}


def test_catalog_paths_cannot_escape_root(catalog):
    with pytest.raises(ValueError):
        catalog.assets[0].model_copy(update={"file": "../outside.gif"})
    with pytest.raises(ValueError):
        catalog.assets[0].model_copy(update={"alt": "invented](https://example.invalid)"})


def test_media_selection_requires_both_validation_checks(catalog):
    text = draft().model_copy(update={"report": ValidationReport(status="REVIEW_READY", static_passed=True, grounding_passed=False)})
    assert api().choose_media(brief(), text, catalog) is None


def test_bundled_catalog_records_unknown_rights_as_excluded_not_safe():
    bundled = api().load_catalog()
    assert bundled.assets == ()
    assert len(bundled.excluded_assets) == 8
    assert all(item.usage_status == "unknown" for item in bundled.excluded_assets)
    assert all(item.visual_status == "inspected" and item.source_note and item.use_note for item in bundled.excluded_assets)
    assert api().choose_media(brief(), draft(), bundled) is None


@pytest.mark.parametrize("data", [[], {"asset_root": "C:/unreviewed", "assets": []}])
def test_invalid_catalog_cannot_override_the_inspected_asset_root(tmp_path, data):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        api().load_catalog(path)


def test_media_failure_retains_research_and_reports_blocked(drafting_input, tmp_path, monkeypatch):
    from src.curator.matcher import CuratedTopic
    from src.writer.blog_writer import BlogWriter
    packet, _ = drafting_input
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture",
        one_line_summary="queue", relevance_reason="reliability", suggested_angle=packet.question)
    class LocalLLM:
        def generate(self, prompt):
            return json.dumps(response(), ensure_ascii=False)
    def invalid_catalog():
        raise ValueError("unreadable catalog fixture")
    monkeypatch.setattr("src.writer.blog_writer.load_catalog", invalid_catalog)
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts", llm=LocalLLM())
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "NEEDS_REVISION"
    assert "media_catalog_unavailable" in result["reasons"]
    assert result["packet"] == packet
    assert result["media_choice"] is None
    assert not (tmp_path / "blog").exists()


def test_legacy_manager_constructor_has_no_blog_writes_and_requires_verified_choice(catalog, tmp_path):
    from src.writer.meme_manager import MemeManager
    blog = tmp_path / "blog"
    manager = MemeManager(blog)
    assert not blog.exists()
    choice = api().choose_media(brief(), draft(), catalog)
    assert manager.format_meme_markdown(choice) == "![단색 픽셀](/assets/images/memes/pixel.gif)"
    with pytest.raises((TypeError, ValueError)):
        manager.format_meme_markdown({"caption": "fictional scene", "url": "/assets/images/memes/pixel.gif"})


def test_draft_validation_cannot_certify_an_unverified_model_image(drafting_input):
    from src.editorial.validate import validate_draft
    packet, editorial_brief = drafting_input
    text = DraftText.model_validate({**response(), "packet": packet})
    media = DraftSection(id="media", text="![invented scene](/assets/images/memes/github-star.gif)")
    text = text.model_copy(update={"sections": (*text.sections, media)})
    report = validate_draft(text, packet, editorial_brief)
    assert report.status == "NEEDS_REVISION"
    assert any(issue.code == "unregistered_media" for issue in report.issues)


def test_writer_selects_after_validated_prose_without_blog_write(drafting_input, catalog, tmp_path):
    from src.curator.matcher import CuratedTopic
    from src.writer.blog_writer import BlogWriter
    packet, _ = drafting_input
    item = catalog.assets[0].model_copy(update={"contexts": ("durable storage",)})
    catalog = catalog.model_copy(update={"assets": (item,)})
    topic = CuratedTopic(rank=1, title="PageIndex", url=packet.sources[0].url, source="fixture",
        one_line_summary="queue", relevance_reason="reliability", suggested_angle=packet.question)
    class LocalLLM:
        def generate(self, prompt):
            return json.dumps(response(), ensure_ascii=False)
    writer = BlogWriter(tmp_path / "blog", artifact_dir=tmp_path / "drafts", llm=LocalLLM(), media_catalog=catalog)
    writer.researcher.research = AsyncMock(return_value={"packet": packet})
    result = asyncio.run(writer.generate_post(topic))
    assert result["status"] == "REVIEW_READY"
    assert result["media_choice"].id == "pixel"
    assert result["content"].count("![") == 1
    assert result["content"].endswith("![단색 픽셀](/assets/images/memes/pixel.gif)")
    assert not (tmp_path / "blog").exists()
