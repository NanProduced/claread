"""Cover image pipeline: upgrade, download, pixel validation, storage (B-1).

Fixes P0-4: BBC ichef ``/ace/standard/{w}/`` URLs were never upgraded (the
old regex only matched the legacy ``/{w}_width/`` form), producing 240x134
stretched covers. Downloads are now validated by real pixel dimensions and
every failure surfaces as a tracker error instead of failing silently.

Storage is delegated to ``cover_storage`` (local static for dev, object
storage for prod). The UA+Referer anti-hotlink headers are kept as-is —
images are fetched once from the origin, no hotlinking at render time.
"""

from __future__ import annotations

import hashlib
import logging
import re
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.services.daily_reader.discovery import DiscoveredArticle
    from app.services.daily_reader.pipeline_tracker import PipelineRunTracker

logger = logging.getLogger(__name__)

TARGET_COVER_WIDTH = 1280
MIN_COVER_WIDTH = 1200
MAX_COVER_CANDIDATES = 6
REASON_DOWNLOAD_FAILED = "download_failed"
REASON_UNREADABLE = "unreadable_image"
REASON_TRACKING_PIXEL = "tracking_pixel"
REASON_ICON_LIKE = "icon_like"
REASON_BELOW_MIN_WIDTH = "below_min_width"
REASON_EXTREME_BANNER = "extreme_banner"

# Wider than this ratio means strip/banner art, not a photo (observed on
# BBC: 1600x263 dividers that pass the width gate).
MAX_ASPECT_RATIO = 3.0


# --- URL upgrade rules (best effort per source; unknown patterns pass
# --- through unchanged and are judged by pixel validation after download).


def _bump_width(match: re.Match, target: int) -> str:
    width = int(match.group(1))
    return match.group(0).replace(str(width), str(max(width, target)), 1)


def upgrade_image_url(url: str) -> str:
    """Upgrade known CDN URL patterns to >=1280px width where possible."""
    if not url:
        return url
    # BBC ichef new format: /ace/{product}/{w}/... or /news/{w}/...
    # (only upgrades — never downgrades an already-wider variant)
    url = re.sub(
        r"(ichef\.bbci\.co\.uk/(?:ace/[a-z_]+|news)/)(\d+)(?=/)",
        lambda m: m.group(1) + str(max(int(m.group(2)), TARGET_COVER_WIDTH)),
        url,
    )
    # BBC legacy format: /240_width/ or /240_height/
    url = re.sub(
        r"/(\d+)_(width|height)/",
        lambda m: f"/{max(int(m.group(1)), TARGET_COVER_WIDTH)}_width/",
        url,
    )
    if "bbci.co.uk" in url or "bbc.co" in url:
        # BBC legacy suffix form: name_240.jpg
        url = re.sub(
            r"_(\d{2,4})(?=\.(?:jpe?g|png|webp)$)",
            lambda m: _bump_width(m, TARGET_COVER_WIDTH),
            url,
            flags=re.IGNORECASE,
        )
    if "media.guim.co.uk" in url:
        # Guardian: .../0_0_5472_3648/140.jpg — trailing segment is width.
        url = re.sub(
            r"/(\d+)(?=\.(?:jpe?g|png|webp)$)",
            lambda m: _bump_width(m, TARGET_COVER_WIDTH),
            url,
            flags=re.IGNORECASE,
        )
    if "npr.org" in url:
        # NPR: ..._wide-{hash}s800.jpg — sNNNN suffix is width (cap 1400).
        url = re.sub(
            r"s(\d{2,4})(?=\.(?:jpe?g|png|webp)$)",
            lambda m: _bump_width(m, 1400),
            url,
            flags=re.IGNORECASE,
        )
    return url


# --- Pixel dimension probing (stdlib only; PNG/JPEG/WebP/GIF headers).


def probe_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) parsed from image bytes, or None if unknown."""
    if not data or len(data) < 12:
        return None
    if data.startswith(b"\x89PNG"):
        if len(data) < 24:
            return None
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return width, height
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return width, height
        if chunk == b"VP8 " and len(data) >= 30:
            width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return width, height
        if chunk == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return width, height
        return None
    if data.startswith(b"\xff\xd8"):
        # JPEG: walk segments until a Start-Of-Frame marker.
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xD9:
                break
            segment_length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return width, height
            i += 2 + segment_length
        return None
    return None


@dataclass
class CoverValidation:
    ok: bool
    width: int = 0
    height: int = 0
    reason: str | None = None


def validate_image_dimensions(width: int, height: int) -> CoverValidation:
    """Rule gate: tracking pixel / icon-like / extreme banner / min width."""
    if width <= 2 or height <= 2:
        return CoverValidation(False, width, height, REASON_TRACKING_PIXEL)
    ratio = width / height if height else 0.0
    # Icon/logo shape: near-square and small.
    if 0.8 <= ratio <= 1.25 and min(width, height) < 400:
        return CoverValidation(False, width, height, REASON_ICON_LIKE)
    if ratio > MAX_ASPECT_RATIO:
        return CoverValidation(False, width, height, REASON_EXTREME_BANNER)
    if width < MIN_COVER_WIDTH:
        return CoverValidation(False, width, height, REASON_BELOW_MIN_WIDTH)
    return CoverValidation(True, width, height)


# --- Download (structured result; UA+Referer anti-hotlink headers kept).


@dataclass
class FetchedImage:
    data: bytes
    content_type: str
    source_url: str  # URL variant that actually succeeded


_HEADERS_LIST = (
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    },
    {
        "User-Agent": "Claread/1.0 (https://claread.app)",
        "Accept": "image/*,*/*;q=0.8",
    },
)


def _guess_referer(url: str) -> str:
    if "bbci.co.uk" in url or "bbc.co.uk" in url or "bbc.com" in url:
        return "https://www.bbc.com/"
    if "guardian" in url:
        return "https://www.theguardian.com/"
    if "npr.org" in url:
        return "https://www.npr.org/"
    return ""


def _guess_extension(url: str, content_type: str) -> str:
    if "image/png" in content_type or url.endswith(".png"):
        return ".png"
    if "image/webp" in content_type or url.endswith(".webp"):
        return ".webp"
    if "image/gif" in content_type or url.endswith(".gif"):
        return ".gif"
    return ".jpg"


async def fetch_image(url: str) -> FetchedImage | None:
    """Download an image, trying the upgraded URL first then the original."""
    if not url:
        return None
    upgraded = upgrade_image_url(url)
    urls_to_try = [upgraded] if upgraded != url else []
    urls_to_try.append(url)

    for try_url in urls_to_try:
        headers_variants = []
        referer = _guess_referer(try_url)
        base = dict(_HEADERS_LIST[0])
        if referer:
            base["Referer"] = referer
        headers_variants.append(base)
        headers_variants.append(dict(_HEADERS_LIST[1]))

        for headers in headers_variants:
            try:
                async with httpx.AsyncClient(
                    timeout=15.0,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    resp = await client.get(try_url)
                    resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type and not try_url.endswith(
                    (".jpg", ".jpeg", ".png", ".webp", ".gif")
                ):
                    logger.warning(
                        "URL does not appear to be an image: %s (content-type: %s)",
                        try_url[:80],
                        content_type,
                    )
                    continue

                return FetchedImage(
                    data=resp.content,
                    content_type=content_type or "image/jpeg",
                    source_url=try_url,
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Cover image download failed (HTTP %d): %s",
                    e.response.status_code,
                    try_url[:80],
                )
                continue
            except Exception as e:
                logger.warning("Cover image download failed: %s - %s", try_url[:80], e)
                continue

    logger.warning("All download attempts failed for: %s", url[:80])
    return None


# --- Article-level cover orchestration.


@dataclass
class ValidatedCandidate:
    url: str  # original candidate URL
    caption: str
    position: str
    fetched: FetchedImage
    width: int
    height: int


@dataclass
class CoverOutcome:
    cover_url: str | None = None
    image_blocks: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


async def probe_cover_eligible(article: DiscoveredArticle) -> bool:
    """Cheap pre-sort probe: is the primary candidate pixel-qualified?

    Feeds the has_cover signal with real validation instead of URL presence.
    """
    primary = None
    if article.image_candidates:
        primary = article.image_candidates[0].url
    elif article.cover_image_url:
        primary = article.cover_image_url
    if not primary:
        return False

    fetched = await fetch_image(primary)
    if fetched is None:
        return False
    dims = probe_image_dimensions(fetched.data)
    if dims is None:
        return False
    return validate_image_dimensions(*dims).ok


async def process_article_covers(
    article: DiscoveredArticle,
    tracker: PipelineRunTracker | None = None,
) -> CoverOutcome:
    """Validate candidates, deterministically select one cover, store (P-0).

    No qualified candidate → cover_url=None (editorial fallback) with every
    failure recorded as tracker error + pipeline_meta (no silent failures).
    Never calls a model: selection is a fixed source-priority rule.
    """
    from app.services.daily_reader.cover_select import (
        SELECTION_MODE_NONE,
        VISUAL_FALLBACK_REASON_CAPTION_MISSING,
        build_image_block,
        select_cover_images,
        visual_fallback_eligible,
    )
    from app.services.daily_reader.cover_storage import get_cover_storage
    from app.services.daily_reader.discovery import upsert_image_candidate

    candidates = list(article.image_candidates)
    if not candidates and article.cover_image_url:
        from app.services.daily_reader.discovery import ImageCandidate

        candidates = [ImageCandidate(url=article.cover_image_url)]

    # P-0: same resolved URL keeps one candidate; empty captions are filled by
    # the first later non-empty source caption, first position is preserved.
    unique_candidates: list = []
    for cand in candidates:
        upsert_image_candidate(unique_candidates, cand)
    unique_candidates = unique_candidates[:MAX_COVER_CANDIDATES]

    if not unique_candidates:
        error = f"no image candidates collected for '{article.title[:60]}'"
        logger.warning(error)
        if tracker:
            await tracker.add_error("cover", error)
        return CoverOutcome(
            meta={"selection_mode": "none", "candidates": [], "errors": [error]},
            errors=[error],
        )

    candidate_meta: list[dict] = []
    errors: list[str] = []
    validated: list[ValidatedCandidate] = []

    for cand in unique_candidates:
        upgraded = upgrade_image_url(cand.url)
        fetched = await fetch_image(cand.url)
        entry: dict = {
            "url": cand.url,
            "upgraded_url": upgraded if upgraded != cand.url else None,
            "position": cand.position,
            "source_caption": cand.caption.strip() or None,
            "valid": False,
            "reason": None,
            "width": None,
            "height": None,
        }
        if fetched is None:
            entry["reason"] = REASON_DOWNLOAD_FAILED
            errors.append(f"{REASON_DOWNLOAD_FAILED}: {cand.url[:120]}")
            candidate_meta.append(entry)
            continue
        dims = probe_image_dimensions(fetched.data)
        if dims is None:
            entry["reason"] = REASON_UNREADABLE
            errors.append(f"{REASON_UNREADABLE}: {cand.url[:120]}")
            candidate_meta.append(entry)
            continue
        validation = validate_image_dimensions(*dims)
        entry["width"], entry["height"] = dims
        if not validation.ok:
            entry["reason"] = validation.reason
            errors.append(f"{validation.reason}: {cand.url[:120]} ({dims[0]}x{dims[1]})")
            candidate_meta.append(entry)
            continue
        entry["valid"] = True
        candidate_meta.append(entry)
        validated.append(
            ValidatedCandidate(
                url=cand.url,
                caption=cand.caption,
                position=cand.position,
                fetched=fetched,
                width=dims[0],
                height=dims[1],
            )
        )

    for error in errors:
        if tracker:
            await tracker.add_error("cover", error)

    if not validated:
        meta = {
            "selection_mode": SELECTION_MODE_NONE,
            "visual_fallback_eligible": False,
            "visual_fallback_reason": None,
            "candidates": candidate_meta,
            "errors": errors,
        }
        logger.warning(
            "No qualified cover candidate for '%s' (%d tried)",
            article.title[:60],
            len(unique_candidates),
        )
        return CoverOutcome(meta=meta, errors=errors)

    # P-0: deterministic selection over ALL qualified candidates (no LLM
    # 4-candidate truncation); exactly one cover, never an inline image.
    selection = select_cover_images(validated)
    eligible = visual_fallback_eligible(validated)
    fallback_reason = VISUAL_FALLBACK_REASON_CAPTION_MISSING if eligible else None

    storage = get_cover_storage()
    image_blocks: list[dict] = []
    stored_cover_url: str | None = None
    selected_meta: dict = {"mode": selection.mode}

    candidate = validated[selection.cover_index]
    ext = _guess_extension(candidate.fetched.source_url, candidate.fetched.content_type)
    filename = f"{hashlib.sha256(candidate.url.encode()).hexdigest()[:16]}{ext}"
    try:
        stored_url = await storage.store(
            candidate.fetched.data,
            filename=filename,
            content_type=candidate.fetched.content_type,
        )
    except Exception as exc:
        error = f"cover_storage_failed ({storage.backend}): {exc}"
        logger.error(error)
        errors.append(error)
        if tracker:
            await tracker.add_error("cover", error)
    else:
        stored_cover_url = stored_url
        image_blocks.append(
            build_image_block(
                block_id="img_cover",
                role="cover",
                url=stored_url,
                width=candidate.width,
                height=candidate.height,
                source_caption=candidate.caption,
            )
        )
        selected_meta["cover"] = {
            "url": stored_url,
            "source_url": candidate.url,
            "width": candidate.width,
            "height": candidate.height,
            "source_caption": candidate.caption.strip() or None,
        }

    meta = {
        "selection_mode": selection.mode,
        "visual_fallback_eligible": eligible,
        "visual_fallback_reason": fallback_reason,
        "candidates": candidate_meta,
        "selected": selected_meta,
        "storage_backend": storage.backend,
        "errors": errors,
    }
    logger.info(
        "Cover processed for '%s': mode=%s cover=%s eligible=%s",
        article.title[:50],
        selection.mode,
        bool(stored_cover_url),
        eligible,
    )
    return CoverOutcome(
        cover_url=stored_cover_url,
        image_blocks=image_blocks,
        meta=meta,
        errors=errors,
    )
