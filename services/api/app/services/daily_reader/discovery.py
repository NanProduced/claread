"""Discovery layer for Daily Reader article pipeline.

Fetches candidate articles from Guardian API and RSS feeds (BBC, NPR).
"""

from __future__ import annotations

import logging
import re
from html import unescape
from dataclasses import dataclass, field
from datetime import datetime

import feedparser
import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredArticle:
    url: str
    title: str
    description: str = ""
    text: str = ""
    author: str = ""
    published_at: datetime | None = None
    cover_image_url: str | None = None
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    source: str = ""
    needs_extraction: bool = True


ARTICLE_SOURCES = {
    "guardian": {
        "type": "api",
        "base_url": "https://content.guardianapis.com",
        "sections": ["science", "technology", "culture"],
        "show_fields": "headline,standfirst,thumbnail,wordcount,body,byline",
        "wordcount_range": (500, 2000),
        "page_size": 5,
    },
    "bbc": {
        "type": "rss",
        "feeds": {
            "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
        },
        "image_width_upgrade": {"from": 240, "to": 640},
    },
    "npr": {
        "type": "rss",
        "feeds": {
            "science": "https://feeds.npr.org/1007/rss.xml",
            "technology": "https://feeds.npr.org/1019/rss.xml",
        },
    },
}

DISCOVERY_MAX_PER_SOURCE = 5


async def discover_guardian() -> list[DiscoveredArticle]:
    settings = get_settings()
    api_key = settings.guardian_api_key
    if not api_key:
        logger.warning("Guardian API key not configured, skipping Guardian discovery")
        return []

    source_config = ARTICLE_SOURCES["guardian"]
    base_url = source_config["base_url"]
    articles: list[DiscoveredArticle] = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for section in source_config["sections"]:
            try:
                params = {
                    "api-key": api_key,
                    "section": section,
                    "show-fields": source_config["show_fields"],
                    "page-size": source_config["page_size"],
                    "order-by": "newest",
                    "wordcount": f"{source_config['wordcount_range'][0]}-{source_config['wordcount_range'][1]}",
                }
                resp = await client.get(f"{base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("Guardian API error for section %s: %s", section, e)
                continue

            results = data.get("response", {}).get("results", [])
            for item in results:
                fields = item.get("fields", {})
                body_html = fields.get("body", "")
                text = _strip_html(body_html)
                wc = int(fields.get("wordcount", 0) or 0)

                if not text or wc < 400:
                    continue

                articles.append(
                    DiscoveredArticle(
                        url=item.get("webUrl", ""),
                        title=fields.get("headline", item.get("webTitle", "")),
                        description=_strip_html(fields.get("standfirst", "")),
                        text=text,
                        author=fields.get("byline", ""),
                        cover_image_url=fields.get("thumbnail"),
                        tags=[section],
                        word_count=wc,
                        source="guardian",
                        needs_extraction=False,
                    )
                )

    logger.info("Guardian discovery: found %d articles", len(articles))
    return articles


async def discover_rss_sources() -> list[DiscoveredArticle]:
    articles: list[DiscoveredArticle] = []

    for source_name in ("bbc", "npr"):
        source_config = ARTICLE_SOURCES[source_name]
        feeds = source_config.get("feeds", {})

        for section, feed_url in feeds.items():
            try:
                feed_articles = _parse_rss_feed(source_name, section, feed_url)
                articles.extend(feed_articles)
            except Exception as e:
                logger.warning("RSS parse error for %s/%s: %s", source_name, section, e)

    logger.info("RSS discovery: found %d articles", len(articles))
    return articles


def _parse_rss_feed(
    source_name: str, section: str, feed_url: str, max_entries: int = DISCOVERY_MAX_PER_SOURCE,
) -> list[DiscoveredArticle]:
    feed = feedparser.parse(feed_url)
    articles: list[DiscoveredArticle] = []

    for entry in feed.entries[:max_entries]:
        url = entry.get("link", "")
        title = entry.get("title", "")
        if not url or not title:
            continue

        description = _strip_html(entry.get("summary", ""))
        cover_image_url = _extract_rss_thumbnail(entry)

        published_at = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published_at = datetime(*entry.published_parsed[:6])
            except (TypeError, ValueError):
                pass

        articles.append(
            DiscoveredArticle(
                url=url,
                title=title,
                description=description,
                cover_image_url=cover_image_url,
                tags=[section],
                source=source_name,
                needs_extraction=True,
                published_at=published_at,
            )
        )

    return articles


def _extract_rss_thumbnail(entry: object) -> str | None:
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail:
        for thumb in media_thumbnail:
            url = thumb.get("url", "")
            if url:
                return _upgrade_image_url(url, entry)

    media_content = getattr(entry, "media_content", None)
    if media_content:
        for media in media_content:
            url = media.get("url", "")
            if url and ("image" in media.get("type", "image")):
                return _upgrade_image_url(url, entry)

    enclosures = getattr(entry, "enclosures", [])
    for enc in enclosures:
        if "image" in enc.get("type", ""):
            return enc.get("href", "")

    return None


def _upgrade_image_url(url: str, entry: object) -> str:
    source_name = ""
    if hasattr(entry, "source") and hasattr(entry.source, "title"):
        source_name = entry.source.title

    if "bbc" in source_name.lower() or "bbci" in url:
        url = re.sub(r"/\d+_(width|height)/", "/640_width/", url)
        if "_width" not in url and "_height" not in url:
            url = re.sub(r"\.jpg$", "_640.jpg", url, flags=re.IGNORECASE)
            url = re.sub(r"\.png$", "_640.png", url, flags=re.IGNORECASE)

    return url


def _strip_html(html: str) -> str:
    import re

    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</(p|h[1-6]|li|div|blockquote|section|article)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text
