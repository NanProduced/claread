"""B-1 cover & image pipeline tests.

Covers: ichef upgrade rules (new + legacy formats), pixel dimension
probing, rule gate (tracking pixel / icon-like / min width), layout tag
mapping, storage fallback + OSS mock, candidate collection, LLM selection
degradation, and the article-level cover orchestration.
"""

from __future__ import annotations

import struct
from pathlib import Path
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
    SELECTION_MODE_FALLBACK_FIRST,
    SELECTION_MODE_LLM,
    _CoverSelectOutput,
    build_image_block,
    layout_for_dimensions,
    select_cover_images,
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
            caption_zh=" 一句图说 ",
            source_caption="Photo: BBC",
        )
        assert block["layout"] == LAYOUT_FULL_BLEED
        assert block["caption_zh"] == "一句图说"
        assert block["source_caption"] == "Photo: BBC"
        assert block["role"] == "cover"


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


# --- LLM selection + degradation ---


def _validated(width: int, height: int, url: str, caption: str = "") -> object:
    from app.services.daily_reader.cover_download import ValidatedCandidate

    return ValidatedCandidate(
        url=url,
        caption=caption,
        position=IMAGE_POSITION_META,
        fetched=_fetched(_jpeg_bytes(width, height), url),
        width=width,
        height=height,
    )


class TestSelectCoverImages:
    async def test_no_candidates_returns_empty_selection(self):
        selection = await select_cover_images(title="t", text_excerpt="x", candidates=[])
        assert selection.cover is None

    async def test_model_unavailable_degrades_to_first_candidate(self):
        with patch(
            "app.llm.router.build_model_for_route", return_value=(None, None)
        ):
            selection = await select_cover_images(
                title="t",
                text_excerpt="x",
                candidates=[_validated(1600, 900, "https://a/1.jpg")],
            )
        assert selection.mode == SELECTION_MODE_FALLBACK_FIRST
        assert selection.cover.index == 0
        assert selection.cover.caption_zh == ""

    async def test_fallback_prefers_widest_candidate(self):
        with patch(
            "app.llm.router.build_model_for_route", return_value=(None, None)
        ):
            selection = await select_cover_images(
                title="t",
                text_excerpt="x",
                candidates=[
                    _validated(1200, 675, "https://a/meta.jpg"),
                    _validated(3840, 2159, "https://a/body.jpg"),
                ],
            )
        assert selection.mode == SELECTION_MODE_FALLBACK_FIRST
        assert selection.cover.index == 1

    async def test_llm_selection_with_caption(self):
        output = _CoverSelectOutput(
            cover_index=1,
            cover_caption_zh="发射现场的清晨",
            inline_index=None,
        )
        with (
            patch(
                "app.llm.router.build_model_for_route",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch(
                "app.services.daily_reader.cover_select._run_cover_select_span",
                new=AsyncMock(return_value=output),
            ),
            patch("app.services.daily_reader.cover_select.assert_real_llm_allowed"),
        ):
            selection = await select_cover_images(
                title="t",
                text_excerpt="x",
                candidates=[
                    _validated(1600, 900, "https://a/1.jpg"),
                    _validated(1600, 900, "https://a/2.jpg"),
                ],
            )
        assert selection.mode == SELECTION_MODE_LLM
        assert selection.cover.index == 1
        assert selection.cover.caption_zh == "发射现场的清晨"
        assert selection.inline is None

    async def test_invalid_index_falls_back(self):
        output = _CoverSelectOutput(cover_index=99, cover_caption_zh="x")
        with (
            patch(
                "app.llm.router.build_model_for_route",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch(
                "app.services.daily_reader.cover_select._run_cover_select_span",
                new=AsyncMock(return_value=output),
            ),
            patch("app.services.daily_reader.cover_select.assert_real_llm_allowed"),
        ):
            selection = await select_cover_images(
                title="t",
                text_excerpt="x",
                candidates=[_validated(1600, 900, "https://a/1.jpg")],
            )
        assert selection.mode == SELECTION_MODE_FALLBACK_FIRST
        assert selection.cover.index == 0

    async def test_llm_exception_falls_back(self):
        with (
            patch(
                "app.llm.router.build_model_for_route",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch(
                "app.services.daily_reader.cover_select._run_cover_select_span",
                new=AsyncMock(side_effect=RuntimeError("provider down")),
            ),
            patch("app.services.daily_reader.cover_select.assert_real_llm_allowed"),
        ):
            selection = await select_cover_images(
                title="t",
                text_excerpt="x",
                candidates=[_validated(1600, 900, "https://a/1.jpg")],
            )
        assert selection.mode == SELECTION_MODE_FALLBACK_FIRST


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
            patch("app.llm.router.build_model_for_route", return_value=(None, None)),
        ):
            mock_storage_factory.return_value = LocalCoverStorage(cover_dir=tmp_path)
            outcome = await process_article_covers(article, tracker=tracker)

        assert outcome.cover_url is not None
        assert outcome.cover_url.endswith(".jpg")
        assert len(outcome.image_blocks) == 1
        block = outcome.image_blocks[0]
        assert block["role"] == "cover"
        assert block["layout"] == LAYOUT_FULL_BLEED  # 2100/900
        assert outcome.meta["selection_mode"] == SELECTION_MODE_FALLBACK_FIRST
        reasons = [c["reason"] for c in outcome.meta["candidates"]]
        assert REASON_TRACKING_PIXEL in reasons
        # silent failures are now visible: tracker got the invalid candidate
        tracker.add_error.assert_awaited()

    async def test_all_candidates_fail_returns_null_cover(self):
        article = self._article(
            [ImageCandidate(url="https://a/small.jpg", position=IMAGE_POSITION_META)]
        )
        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        with (
            patch(
                "app.services.daily_reader.cover_download.fetch_image",
                new=AsyncMock(return_value=_fetched(_png_bytes(240, 134))),
            ),
        ):
            outcome = await process_article_covers(article, tracker=tracker)

        assert outcome.cover_url is None
        assert outcome.image_blocks == []
        assert outcome.meta["selection_mode"] == "none"
        assert outcome.meta["candidates"][0]["reason"] == REASON_BELOW_MIN_WIDTH
        tracker.add_error.assert_awaited()

    async def test_no_candidates_records_error(self):
        article = self._article([])
        tracker = MagicMock()
        tracker.add_error = AsyncMock()
        outcome = await process_article_covers(article, tracker=tracker)
        assert outcome.cover_url is None
        assert outcome.meta["selection_mode"] == "none"
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
