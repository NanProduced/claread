"""B-1 cover & image pipeline tests.

Covers: ichef upgrade rules (new + legacy formats), pixel dimension
probing, rule gate (tracking pixel / icon-like / min width), layout tag
mapping, storage fallback + OSS mock, candidate collection, LLM selection
degradation, and the article-level cover orchestration.
"""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.daily_reader.cover_download import (
    REASON_BELOW_MIN_WIDTH,
    REASON_DOWNLOAD_FAILED,
    REASON_EXTREME_BANNER,
    REASON_ICON_LIKE,
    REASON_TRACKING_PIXEL,
    FetchedImage,
    fetch_image,
    probe_cover_eligible,
    probe_image_dimensions,
    process_article_covers,
    upgrade_image_url,
    validate_image_dimensions,
)
from app.services.daily_reader.cover_select import (
    LAYOUT_FULL_BLEED,
    LAYOUT_HALF_FLOAT,
    LAYOUT_TWO_THIRD,
    SELECTION_MODE_DETERMINISTIC_SOURCE,
    SELECTION_MODE_NONE,
    VISUAL_FALLBACK_REASON_CAPTION_MISSING,
    build_image_block,
    layout_for_dimensions,
    select_cover_images,
    visual_fallback_eligible,
)
from app.services.daily_reader.cover_storage import (
    LocalCoverStorage,
    OssCoverStorage,
    get_cover_storage,
)
from app.services.daily_reader.discovery import (
    IMAGE_POSITION_BODY,
    IMAGE_POSITION_META,
    DiscoveredArticle,
    ImageCandidate,
    collect_image_candidates_from_html,
    discover_guardian,
    upsert_image_candidate,
)

# --- fixtures: minimal real image headers ---


def _png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def _gif_bytes(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 16


def _jpeg_bytes(width: int, height: int) -> bytes:
    # SOI + SOF0 segment: length, precision, height, width, components.
    return (
        b"\xff\xd8\xff\xc0"
        + struct.pack(">H", 11)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x00" * 8
    )


def _webp_vp8x_bytes(width: int, height: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 30)
        + b"WEBPVP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
        + b"\x00" * 8
    )


def _fetched(data: bytes, url: str = "https://example.com/img.jpg") -> FetchedImage:
    return FetchedImage(data=data, content_type="image/jpeg", source_url=url)


# --- upgrade rules ---


class TestUpgradeImageUrl:
    def test_ichef_ace_standard_new_format(self):
        url = "https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/abc.jpg"
        assert upgrade_image_url(url) == (
            "https://ichef.bbci.co.uk/ace/standard/1280/cpsprodpb/abc.jpg"
        )

    def test_ichef_news_path_format(self):
        url = "https://ichef.bbci.co.uk/news/240/cpsprodpb/xyz.png"
        assert upgrade_image_url(url) == (
            "https://ichef.bbci.co.uk/news/1280/cpsprodpb/xyz.png"
        )

    def test_ichef_never_downgrades_wider_images(self):
        url = "https://ichef.bbci.co.uk/ace/standard/2048/cpsprodpb/abc.jpg"
        assert upgrade_image_url(url) == url

    def test_legacy_width_segment_format(self):
        url = "https://ichef.bbci.co.uk/images/ic/240_width/pic.jpg"
        assert "/1280_width/" in upgrade_image_url(url)

    def test_legacy_height_segment_format(self):
        url = "https://ichef.bbci.co.uk/images/ic/240_height/pic.jpg"
        assert "/1280_width/" in upgrade_image_url(url)

    def test_bbc_legacy_suffix_format(self):
        url = "https://ichef.bbci.co.uk/media/images/123_640.jpg"
        assert upgrade_image_url(url).endswith("_1280.jpg")

    def test_guardian_trailing_width(self):
        url = "https://media.guim.co.uk/abcdef/0_0_5472_3648/140.jpg"
        assert upgrade_image_url(url) == (
            "https://media.guim.co.uk/abcdef/0_0_5472_3648/1280.jpg"
        )

    def test_npr_width_suffix_upgraded(self):
        url = "https://media.npr.org/assets/img/2026/x_wide-abc_s800.jpg"
        assert upgrade_image_url(url).endswith("s1400.jpg")

    def test_npr_width_suffix_not_downgraded(self):
        url = "https://media.npr.org/assets/img/2026/x_wide-abc_s1400.jpg"
        assert upgrade_image_url(url) == url

    def test_unknown_source_unchanged(self):
        url = "https://other.example.com/photo/123.jpg"
        assert upgrade_image_url(url) == url


# --- pixel probing ---


class TestProbeImageDimensions:
    def test_png(self):
        assert probe_image_dimensions(_png_bytes(1600, 900)) == (1600, 900)

    def test_gif(self):
        assert probe_image_dimensions(_gif_bytes(640, 480)) == (640, 480)

    def test_jpeg(self):
        assert probe_image_dimensions(_jpeg_bytes(1280, 720)) == (1280, 720)

    def test_webp_vp8x(self):
        assert probe_image_dimensions(_webp_vp8x_bytes(1920, 1080)) == (1920, 1080)

    def test_garbage_returns_none(self):
        assert probe_image_dimensions(b"<html>not an image</html>") is None

    def test_too_short_returns_none(self):
        assert probe_image_dimensions(b"\x89PNG") is None


# --- rule gate ---


class TestValidateImageDimensions:
    def test_tracking_pixel_rejected(self):
        result = validate_image_dimensions(1, 1)
        assert not result.ok and result.reason == REASON_TRACKING_PIXEL

    def test_icon_like_rejected(self):
        result = validate_image_dimensions(300, 290)
        assert not result.ok and result.reason == REASON_ICON_LIKE

    def test_below_min_width_rejected(self):
        result = validate_image_dimensions(800, 600)
        assert not result.ok and result.reason == REASON_BELOW_MIN_WIDTH

    def test_extreme_banner_rejected(self):
        # BBC-style divider strip: wide enough but not a photo.
        result = validate_image_dimensions(1600, 263)
        assert not result.ok and result.reason == REASON_EXTREME_BANNER

    def test_wide_enough_passes(self):
        result = validate_image_dimensions(1280, 720)
        assert result.ok and result.reason is None


# --- layout tags ---


class TestLayoutForDimensions:
    def test_full_bleed(self):
        assert layout_for_dimensions(2100, 900) == LAYOUT_FULL_BLEED

    def test_two_third(self):
        assert layout_for_dimensions(1280, 853) == LAYOUT_TWO_THIRD

    def test_half_float(self):
        assert layout_for_dimensions(1200, 1150) == LAYOUT_HALF_FLOAT

    def test_zero_height_safe(self):
        assert layout_for_dimensions(1280, 0) == LAYOUT_TWO_THIRD

    def test_image_block_contract(self):
        block = build_image_block(
            block_id="img_cover",
            role="cover",
            url="https://x/y.jpg",
            width=2100,
            height=900,
            source_caption="Photo: BBC",
        )
        assert block["layout"] == LAYOUT_FULL_BLEED
        # P-0: new output never carries an AI Chinese caption.
        assert block["caption_zh"] is None
        assert block["source_caption"] == "Photo: BBC"
        assert block["role"] == "cover"

    def test_image_block_without_source_caption_is_null(self):
        block = build_image_block(
            block_id="img_cover",
            role="cover",
            url="https://x/y.jpg",
            width=1280,
            height=720,
        )
        assert block["source_caption"] is None
        assert block["caption_zh"] is None


# --- storage ---


class _FakeSettings:
    def __init__(self, backend: str, key: str = "", secret: str = ""):
        self.cover_storage_backend = backend
        self.cover_oss_public_url_base = ""
        self.aliyun_oss_bucket = "claread-covers"
        self.aliyun_oss_endpoint = "https://oss-cn-shenzhen.aliyuncs.com"
        self.server_base_url = "http://127.0.0.1:8000"
        self._key = key
        self._secret = secret

    def resolve_aliyun_oss_credentials(self):
        return self._key, self._secret


class TestCoverStorage:
    async def test_local_storage_writes_and_returns_url(self, tmp_path: Path):
        storage = LocalCoverStorage(cover_dir=tmp_path)
        url = await storage.store(b"imgdata", filename="abc.jpg", content_type="image/jpeg")
        assert url.endswith("/static/covers/abc.jpg")
        assert (tmp_path / "abc.jpg").read_bytes() == b"imgdata"

    def test_factory_defaults_to_local(self):
        with patch(
            "app.services.daily_reader.cover_storage.get_settings",
            return_value=_FakeSettings("local"),
        ):
            assert isinstance(get_cover_storage(), LocalCoverStorage)

    def test_factory_falls_back_to_local_without_credentials(self):
        with (
            patch(
                "app.services.daily_reader.cover_storage.get_settings",
                return_value=_FakeSettings("oss"),
            ),
            patch("app.services.daily_reader.cover_storage.logger") as mock_logger,
        ):
            storage = get_cover_storage()
        assert isinstance(storage, LocalCoverStorage)
        mock_logger.warning.assert_called()

    async def test_oss_storage_mocked_upload(self):
        storage = OssCoverStorage(
            access_key_id="key",
            access_key_secret="secret",
            bucket="claread-covers",
            endpoint="https://oss-cn-shenzhen.aliyuncs.com",
        )
        storage._bucket_instance = MagicMock()
        url = await storage.store(b"imgdata", filename="abc.jpg", content_type="image/jpeg")
        assert url == "https://claread-covers.oss-cn-shenzhen.aliyuncs.com/daily-covers/abc.jpg"
        storage._bucket_instance.put_object.assert_called_once()

    def test_oss_public_url_base_override(self):
        storage = OssCoverStorage(
            access_key_id="key",
            access_key_secret="secret",
            bucket="claread-covers",
            endpoint="https://oss-cn-shenzhen.aliyuncs.com",
            public_url_base="https://cdn.claread.app/",
        )
        assert storage._public_url_base == "https://cdn.claread.app"

    def test_oss_missing_credentials_fail_closed(self):
        with pytest.raises(ValueError):
            OssCoverStorage(
                access_key_id="",
                access_key_secret="",
                bucket="b",
                endpoint="e",
            )


# --- candidate collection ---


class TestCollectImageCandidates:
    HTML = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.com/hero.jpg">
    </head><body>
      <figure>
        <img src="/media/photo1.jpg">
        <figcaption>The launch site at dawn. Photo: Example</figcaption>
      </figure>
      <figure><img src="data:image/gif;base64,AAA"></figure>
      <figure><img src="https://cdn.example.com/logo-small.png"></figure>
    </body></html>
    """

    def test_collects_meta_and_body_candidates(self):
        candidates = collect_image_candidates_from_html(
            self.HTML, "https://example.com/article"
        )
        urls = [c.url for c in candidates]
        assert "https://cdn.example.com/hero.jpg" in urls
        assert "https://example.com/media/photo1.jpg" in urls  # relative resolved
        body = next(c for c in candidates if c.position == IMAGE_POSITION_BODY)
        assert body.caption.startswith("The launch site")
        meta = next(c for c in candidates if c.position == IMAGE_POSITION_META)
        assert meta.caption == ""

    def test_skips_data_urls_and_logos(self):
        urls = {
            c.url
            for c in collect_image_candidates_from_html(self.HTML, "https://example.com/a")
        }
        assert not any(url.startswith("data:") for url in urls)
        assert not any("logo" in url for url in urls)

    def test_empty_html(self):
        assert collect_image_candidates_from_html("", "https://example.com") == []

    HTML_OG_CAPTION = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.com/hero.jpg">
    </head><body>
      <figure>
        <img src="https://cdn.example.com/hero.jpg">
        <figcaption>A nurse examines X-rays. Credit: Reuters</figcaption>
      </figure>
    </body></html>
    """

    def test_same_url_meta_caption_filled_by_figcaption(self):
        candidates = collect_image_candidates_from_html(
            self.HTML_OG_CAPTION, "https://example.com/article"
        )
        hero = [c for c in candidates if "hero.jpg" in c.url]
        assert len(hero) == 1
        assert hero[0].caption == "A nurse examines X-rays. Credit: Reuters"
        # first (meta) position is preserved so og:image keeps the main signal
        assert hero[0].position == IMAGE_POSITION_META

    HTML_BODY_FIRST_NESTED = """
    <figure><img src="https://cdn.example.com/hero.jpg">
      <figcaption><span>Fig 1. </span>Skyline at dusk, <em>Photograph: BBC</em></figcaption>
    </figure>
    <figure><img src="https://cdn.example.com/hero.jpg">
      <figcaption>Fig 2. A different caption</figcaption>
    </figure>
    """

    def test_first_non_empty_caption_wins_and_nested_tags_kept(self):
        candidates = collect_image_candidates_from_html(
            self.HTML_BODY_FIRST_NESTED, "https://example.com/article"
        )
        hero = [c for c in candidates if "hero.jpg" in c.url]
        assert len(hero) == 1
        assert "Skyline at dusk" in hero[0].caption
        assert "Photograph: BBC" in hero[0].caption  # nested tag visible text kept


# --- P-0 source caption merge semantics ---


class TestUpsertImageCandidate:
    def test_new_url_appended(self):
        out: list[ImageCandidate] = []
        upsert_image_candidate(
            out,
            ImageCandidate(url="https://a/x.jpg", position=IMAGE_POSITION_META),
        )
        assert len(out) == 1

    def test_same_url_fills_empty_caption_and_keeps_position(self):
        out: list[ImageCandidate] = [
            ImageCandidate(url="https://a/x.jpg", position=IMAGE_POSITION_META)
        ]
        upsert_image_candidate(
            out,
            ImageCandidate(
                url="https://a/x.jpg", caption="Photo: Getty", position=IMAGE_POSITION_BODY
            ),
        )
        assert len(out) == 1
        assert out[0].caption == "Photo: Getty"
        assert out[0].position == IMAGE_POSITION_META  # first position kept

    def test_empty_caption_never_overwrites_non_empty(self):
        out: list[ImageCandidate] = [
            ImageCandidate(
                url="https://a/x.jpg", caption="Photo: Reuters", position=IMAGE_POSITION_META
            )
        ]
        upsert_image_candidate(
            out, ImageCandidate(url="https://a/x.jpg", position=IMAGE_POSITION_BODY)
        )
        assert out[0].caption == "Photo: Reuters"

    def test_conflicting_non_empty_keeps_first_saved(self):
        out: list[ImageCandidate] = [
            ImageCandidate(
                url="https://a/x.jpg", caption="First caption", position=IMAGE_POSITION_BODY
            )
        ]
        upsert_image_candidate(
            out,
            ImageCandidate(
                url="https://a/x.jpg", caption="Second caption", position=IMAGE_POSITION_BODY
            ),
        )
        assert out[0].caption == "First caption"

    def test_whitespace_only_caption_treated_as_empty(self):
        out: list[ImageCandidate] = [
            ImageCandidate(url="https://a/x.jpg", position=IMAGE_POSITION_META)
        ]
        upsert_image_candidate(
            out,
            ImageCandidate(url="https://a/x.jpg", caption="   ", position=IMAGE_POSITION_BODY),
        )
        assert out[0].caption == ""


class TestDiscoverGuardianCaptionMerge:
    async def test_thumbnail_and_body_same_url_merged(self):
        body_html = (
            '<figure><img src="https://media.guim.co.uk/abc/0_0_5472_3648/140.jpg">'
            "<figcaption>A nurse checks a patient. Credit: Reuters</figcaption></figure>"
        )
        response_payload = {
            "response": {
                "results": [
                    {
                        "webUrl": "https://www.theguardian.com/society/2026/aug/21/sample",
                        "webTitle": "Sample headline",
                        "fields": {
                            "headline": "Sample headline",
                            "standfirst": "Standfirst",
                            "byline": "Jane",
                            "wordcount": "1200",
                            "thumbnail": "https://media.guim.co.uk/abc/0_0_5472_3648/140.jpg",
                            "body": body_html,
                        },
                    }
                ]
            }
        }

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **params):
                return _Resp(response_payload)

        with (
            patch(
                "app.services.daily_reader.discovery.get_settings",
                return_value=SimpleNamespace(guardian_api_key="test-key"),
            ),
            patch("app.services.daily_reader.discovery.httpx.AsyncClient", _Client),
        ):
            articles = await discover_guardian()

        # Guardian iterates each registered section; the mock echoes the same
        # payload for the configured sections, so inspect the first result.
        assert articles
        by_url = [c for c in articles[0].image_candidates if "media.guim.co.uk" in c.url]
        assert len(by_url) == 1
        assert by_url[0].caption == "A nurse checks a patient. Credit: Reuters"
        assert by_url[0].position == IMAGE_POSITION_META  # thumbnail position kept


# --- LLM selection + degradation ---


def _validated(
    width: int, height: int, url: str, caption: str = "", position: str = IMAGE_POSITION_META
) -> object:
    from app.services.daily_reader.cover_download import ValidatedCandidate

    return ValidatedCandidate(
        url=url,
        caption=caption,
        position=position,
        fetched=_fetched(_jpeg_bytes(width, height), url),
        width=width,
        height=height,
    )


class TestSelectCoverImages:
    def test_no_candidates_returns_none(self):
        selection = select_cover_images([])
        assert selection.mode == SELECTION_MODE_NONE
        assert selection.cover_index is None

    def test_meta_pool_preferred_over_body(self):
        candidates = [
            _validated(1800, 1013, "https://a/body-wide.jpg", position=IMAGE_POSITION_BODY),
            _validated(1200, 675, "https://a/meta.jpg"),
        ]
        selection = select_cover_images(candidates)
        assert selection.mode == SELECTION_MODE_DETERMINISTIC_SOURCE
        assert selection.cover_index == 1  # meta pool wins over wider body

    def test_no_meta_uses_body_pool(self):
        candidates = [
            _validated(1200, 675, "https://a/body1.jpg", position=IMAGE_POSITION_BODY),
            _validated(1400, 788, "https://a/body2.jpg", position=IMAGE_POSITION_BODY),
        ]
        selection = select_cover_images(candidates)
        assert selection.cover_index == 1  # wider within body pool

    def test_source_caption_beats_width_within_pool(self):
        candidates = [
            _validated(2000, 1125, "https://a/wide-nocaption.jpg", caption=""),
            _validated(1200, 675, "https://a/captioned.jpg", caption="Photo: Reuters"),
        ]
        selection = select_cover_images(candidates)
        assert selection.mode == SELECTION_MODE_DETERMINISTIC_SOURCE
        assert selection.cover_index == 1  # captioned preferred over wider

    def test_width_breaks_tie_preserving_original_order(self):
        candidates = [
            _validated(1280, 720, "https://a/first.jpg"),
            _validated(1280, 720, "https://a/second.jpg"),
        ]
        selection = select_cover_images(candidates)
        assert selection.cover_index == 0

    def test_only_cover_no_inline_result(self):
        candidates = [_validated(1600, 900, "https://a/1.jpg")]
        selection = select_cover_images(candidates)
        assert selection.cover_index == 0
        assert not hasattr(selection, "inline")

    def test_cover_select_has_no_llm_path(self):
        import inspect

        import app.services.daily_reader.cover_select as cs

        source = inspect.getsource(cs)
        assert "BinaryImage" not in source
        assert "_CoverSelectOutput" not in source
        assert "build_model_for_route" not in source
        # Pure deterministic seam: single argument, no async IO.
        assert list(inspect.signature(select_cover_images).parameters) == ["candidates"]


class TestVisualFallbackEligible:
    def test_multiple_no_caption_meta_pool_eligible(self):
        candidates = [
            _validated(1280, 720, "https://a/m1.jpg"),
            _validated(1600, 900, "https://a/m2.jpg"),
        ]
        assert visual_fallback_eligible(candidates) is True

    def test_single_meta_not_eligible(self):
        candidates = [_validated(1280, 720, "https://a/m1.jpg")]
        assert visual_fallback_eligible(candidates) is False

    def test_captioned_pool_not_eligible(self):
        candidates = [
            _validated(1280, 720, "https://a/m1.jpg", caption=""),
            _validated(1600, 900, "https://a/m2.jpg", caption="Photo: Getty"),
        ]
        assert visual_fallback_eligible(candidates) is False

    def test_empty_pool_not_eligible(self):
        assert visual_fallback_eligible([]) is False


# --- probe + orchestration ---


class TestProbeCoverEligible:
    async def test_qualified_primary(self):
        article = DiscoveredArticle(
            url="u",
            title="t",
            image_candidates=[ImageCandidate(url="https://a/hero.jpg")],
        )
        with patch(
            "app.services.daily_reader.cover_download.fetch_image",
            new=AsyncMock(return_value=_fetched(_png_bytes(1600, 900))),
        ):
            assert await probe_cover_eligible(article) is True

    async def test_low_res_primary_rejected(self):
        article = DiscoveredArticle(
            url="u", title="t", cover_image_url="https://a/small.jpg"
        )
        with patch(
            "app.services.daily_reader.cover_download.fetch_image",
            new=AsyncMock(return_value=_fetched(_png_bytes(240, 134))),
        ):
            assert await probe_cover_eligible(article) is False

    async def test_no_candidates(self):
        article = DiscoveredArticle(url="u", title="t")
        assert await probe_cover_eligible(article) is False

    async def test_download_failure(self):
        article = DiscoveredArticle(url="u", title="t", cover_image_url="https://a/x.jpg")
        with patch(
            "app.services.daily_reader.cover_download.fetch_image",
            new=AsyncMock(return_value=None),
        ):
            assert await probe_cover_eligible(article) is False


class TestProcessArticleCovers:
    def _article(self, candidates: list[ImageCandidate]) -> DiscoveredArticle:
        return DiscoveredArticle(
            url="u",
            title="Test article",
            text="Article body text.",
            image_candidates=candidates,
        )

    async def test_end_to_end_with_mixed_candidates(self, tmp_path: Path):
        article = self._article(
            [
                ImageCandidate(url="https://a/hero.jpg", position=IMAGE_POSITION_META),
                ImageCandidate(url="https://a/tiny.gif", position=IMAGE_POSITION_BODY),
            ]
        )

        async def fake_fetch(url: str) -> FetchedImage | None:
            if "hero" in url:
                return _fetched(_png_bytes(2100, 900), url)
            if "tiny" in url:
                return _fetched(_gif_bytes(1, 1), url)
            return None

        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(side_effect=fake_fetch),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article, tracker=tracker)

        assert outcome.cover_url is not None
        assert outcome.cover_url.endswith(".jpg")
        assert len(outcome.image_blocks) == 1
        block = outcome.image_blocks[0]
        assert block["role"] == "cover"
        assert block["layout"] == LAYOUT_FULL_BLEED  # 2100/900
        assert block["caption_zh"] is None
        assert outcome.meta["selection_mode"] == SELECTION_MODE_DETERMINISTIC_SOURCE
        assert outcome.meta["visual_fallback_eligible"] is False
        assert outcome.meta["visual_fallback_reason"] is None
        reasons = [c["reason"] for c in outcome.meta["candidates"]]
        assert REASON_TRACKING_PIXEL in reasons
        tracker.add_error.assert_awaited()

    async def test_zero_model_calls_integration(self, tmp_path: Path):
        # P-0: cover selection must never hit the LLM router or a provider.
        article = self._article(
            [ImageCandidate(url="https://a/hero.jpg", position=IMAGE_POSITION_META)]
        )
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(return_value=_fetched(_png_bytes(1600, 900))),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        assert outcome.cover_url is not None

    async def test_six_qualified_candidates_all_participate(self, tmp_path: Path):
        # The 5th qualified (widest+captioned) candidate must win; no LLM 4-cutoff.
        candidates = [
            ImageCandidate(url=f"https://a/c{i}.jpg", position=IMAGE_POSITION_BODY)
            for i in range(6)
        ]
        article = self._article(candidates)

        async def fake_fetch(url: str) -> FetchedImage | None:
            width = 1280 if "c4" not in url else 2000
            return _fetched(_png_bytes(width, width), url)

        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(side_effect=fake_fetch),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        assert outcome.meta["selected"]["cover"]["source_url"] == "https://a/c4.jpg"

    async def test_source_caption_passthrough(self, tmp_path: Path):
        caption = "A nurse checks a patient. Photograph: Jane Doe/The Guardian"
        article = self._article(
            [
                ImageCandidate(
                    url="https://a/hero.jpg", position=IMAGE_POSITION_META, caption=caption
                )
            ]
        )
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(return_value=_fetched(_png_bytes(1600, 900))),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        block = outcome.image_blocks[0]
        assert block["source_caption"] == caption
        assert block["caption_zh"] is None
        assert outcome.meta["candidates"][0]["source_caption"] == caption
        assert outcome.meta["selected"]["cover"]["source_caption"] == caption

    async def test_source_caption_missing_is_null_not_error(self, tmp_path: Path):
        article = self._article(
            [ImageCandidate(url="https://a/hero.jpg", position=IMAGE_POSITION_META)]
        )
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(return_value=_fetched(_png_bytes(1600, 900))),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        assert outcome.cover_url is not None  # article not aborted
        block = outcome.image_blocks[0]
        assert block["source_caption"] is None
        assert block["caption_zh"] is None
        assert outcome.meta["selected"]["cover"]["source_caption"] is None

    async def test_multiple_no_caption_pool_records_visual_eligibility(self, tmp_path: Path):
        article = self._article(
            [
                ImageCandidate(url="https://a/m1.jpg", position=IMAGE_POSITION_META),
                ImageCandidate(url="https://a/m2.jpg", position=IMAGE_POSITION_META),
            ]
        )
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(side_effect=lambda url: _fetched(_png_bytes(1600, 900), url)),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        assert outcome.cover_url is not None  # deterministic cover still output
        assert outcome.meta["visual_fallback_eligible"] is True
        assert outcome.meta["visual_fallback_reason"] == VISUAL_FALLBACK_REASON_CAPTION_MISSING
        assert outcome.meta["selection_mode"] == SELECTION_MODE_DETERMINISTIC_SOURCE

    async def test_captioned_pool_not_eligible(self, tmp_path: Path):
        article = self._article(
            [
                ImageCandidate(url="https://a/m1.jpg", position=IMAGE_POSITION_META),
                ImageCandidate(
                    url="https://a/m2.jpg", position=IMAGE_POSITION_META, caption="Photo: AP"
                ),
            ]
        )
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(side_effect=lambda url: _fetched(_png_bytes(1600, 900), url)),
            ),
            patch(
                "app.services.daily_reader.cover_storage.get_cover_storage"
            ) as mock_storage_factory,
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article)

        assert outcome.meta["visual_fallback_eligible"] is False
        assert outcome.meta["visual_fallback_reason"] is None

    async def test_all_candidates_fail_returns_null_cover(self):
        article = self._article(
            [ImageCandidate(url="https://a/small.jpg", position=IMAGE_POSITION_META)]
        )
        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        with patch(
            "app.services.daily_reader.cover_download.fetch_image",
            new=AsyncMock(return_value=_fetched(_png_bytes(240, 134))),
        ):
            outcome = await process_article_covers(article, tracker=tracker)

        assert outcome.cover_url is None
        assert outcome.image_blocks == []
        assert outcome.meta["selection_mode"] == SELECTION_MODE_NONE
        assert outcome.meta["visual_fallback_eligible"] is False
        assert outcome.meta["candidates"][0]["reason"] == REASON_BELOW_MIN_WIDTH
        tracker.add_error.assert_awaited()

    async def test_no_candidates_records_error(self):
        article = self._article([])
        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        outcome = await process_article_covers(article, tracker=tracker)
        assert outcome.cover_url is None
        assert outcome.meta["selection_mode"] == SELECTION_MODE_NONE
        tracker.add_error.assert_awaited()

    async def test_download_failure_recorded(self):
        article = self._article(
            [ImageCandidate(url="https://a/hero.jpg", position=IMAGE_POSITION_META)]
        )
        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        with patch(
            "app.services.daily_reader.cover_download.fetch_image",
            new=AsyncMock(return_value=None),
        ):
            outcome = await process_article_covers(article, tracker=tracker)
        assert outcome.cover_url is None
        assert outcome.meta["candidates"][0]["reason"] == REASON_DOWNLOAD_FAILED
        tracker.add_error.assert_awaited()


class TestFetchImageFallbackOrder:
    async def test_tries_upgraded_url_first(self):
        calls: list[str] = []

        class _Resp:
            headers = {"content-type": "image/jpeg"}
            content = _jpeg_bytes(1280, 720)

            def raise_for_status(self):
                return None

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                calls.append(url)
                if "/standard/1280/" in url:
                    raise Exception("upgraded not found")
                return _Resp()

        with patch("app.services.daily_reader.cover_download.httpx.AsyncClient", _Client):
            result = await fetch_image(
                "https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/abc.jpg"
            )

        assert result is not None
        assert "/standard/1280/" in calls[0]  # upgraded tried first
        # upgraded URL is retried per header variant before falling back
        assert any("/standard/240/" in call for call in calls[1:])  # original fallback
