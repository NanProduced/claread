"""Cover image downloader for Daily Reader pipeline.

Downloads external cover images to local storage to avoid
hotlink protection (403) from CDNs like BBC's ichef.bbci.co.uk.

TODO: 上线前迁移至微信云开发 COS + CDN，替换本地落盘方案。
当前本地 static/covers 仅适用于开发/调试环境。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_COVER_DIR: Path | None = None


def _get_cover_dir() -> Path:
    global _COVER_DIR
    if _COVER_DIR is not None:
        return _COVER_DIR
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    _COVER_DIR = project_root / "static" / "covers"
    _COVER_DIR.mkdir(parents=True, exist_ok=True)
    return _COVER_DIR


async def download_cover_image(url: str) -> str | None:
    if not url:
        return None

    urls_to_try = _build_url_fallbacks(url)

    headers_list = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": _guess_referer(url),
        },
        {
            "User-Agent": "Claread/1.0 (https://claread.app)",
            "Accept": "image/*,*/*;q=0.8",
        },
    ]

    for try_url in urls_to_try:
        for headers in headers_list:
            try:
                async with httpx.AsyncClient(
                    timeout=15.0,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    resp = await client.get(try_url)
                    resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type and not try_url.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    logger.warning("URL does not appear to be an image: %s (content-type: %s)", try_url[:80], content_type)
                    continue

                ext = _guess_extension(try_url, content_type)
                url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
                filename = f"{url_hash}{ext}"

                cover_dir = _get_cover_dir()
                filepath = cover_dir / filename
                filepath.write_bytes(resp.content)

                settings = get_settings()
                base_url = settings.server_base_url.rstrip("/") if settings.server_base_url else "http://127.0.0.1:8000"
                local_url = f"{base_url}/static/covers/{filename}"

                logger.info("Cover image downloaded: %s -> %s (%d bytes)", try_url[:60], local_url, len(resp.content))
                return local_url

            except httpx.HTTPStatusError as e:
                logger.warning("Cover image download failed (HTTP %d): %s", e.response.status_code, try_url[:80])
                continue
            except Exception as e:
                logger.warning("Cover image download failed: %s - %s", try_url[:80], e)
                continue

    logger.warning("All download attempts failed for: %s", url[:80])
    return None


def _build_url_fallbacks(url: str) -> list[str]:
    fallbacks = [url]
    stripped = re.sub(r"_(\d+)(\.\w+)$", r"\2", url)
    if stripped != url:
        fallbacks.append(stripped)
    return fallbacks


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
