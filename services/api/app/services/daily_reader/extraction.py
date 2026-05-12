"""Extraction layer for Daily Reader article pipeline.

Uses trafilatura to extract full text from article URLs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html import unescape

from app.services.daily_reader.discovery import DiscoveredArticle

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    text: str
    author: str = ""
    description: str = ""
    cover_image_url: str | None = None
    word_count: int = 0


async def extract_with_trafilatura(url: str) -> ExtractionResult | None:
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("trafilatura: failed to download %s", url)
            return None

        result = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            url=url,
        )
        if not result or len(result.strip()) < 200:
            logger.warning("trafilatura: extracted text too short for %s", url)
            return None

        metadata = trafilatura.extract(
            downloaded,
            output_format="json",
            include_comments=False,
            url=url,
        )

        author = ""
        description = ""
        cover_image_url = None

        if metadata:
            try:
                import orjson

                meta = orjson.loads(metadata)
                author = meta.get("author", "")
                description = meta.get("description", "")
                cover_image_url = meta.get("image")
            except (orjson.JSONDecodeError, TypeError):
                pass

        clean_result = _clean_extracted_text(result)
        word_count = len(clean_result.split())

        return ExtractionResult(
            text=clean_result,
            author=_clean_extracted_text(author),
            description=_clean_extracted_text(description),
            cover_image_url=cover_image_url,
            word_count=word_count,
        )
    except Exception as e:
        logger.warning("trafilatura extraction failed for %s: %s", url, e)
        return None


def _clean_extracted_text(text: str) -> str:
    return unescape(text or "").replace("\u00A0", " ").strip()


def apply_extraction_to_article(
    article: DiscoveredArticle, extraction: ExtractionResult
) -> None:
    article.text = extraction.text
    article.word_count = extraction.word_count
    article.needs_extraction = False

    if extraction.author and not article.author:
        article.author = extraction.author
    if extraction.description and not article.description:
        article.description = extraction.description
    if extraction.cover_image_url and not article.cover_image_url:
        article.cover_image_url = extraction.cover_image_url
