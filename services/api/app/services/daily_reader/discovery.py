"""Discovery layer for Daily Reader article pipeline.

Fetches candidate articles from Guardian API and RSS feeds (BBC, NPR).

Also owns the image-candidate data model (B-1): each article carries a list
of ``ImageCandidate`` entries (og:image / body figure+img) that the cover
pipeline validates, selects from, and stores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import feedparser
import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

IMAGE_POSITION_META = "meta"
IMAGE_POSITION_BODY = "body"

# Cheap URL-level noise filter; the authoritative gate is pixel validation
# after download (cover_download.validate_image_dimensions).
_IMG_URL_NOISE_RE = re.compile(
    r"(?:icon|logo|sprite|badge|avatar|favicon|tracking|1x1)", re.IGNORECASE
)


@dataclass
class ImageCandidate:
    url: str
    caption: str = ""
    position: str = IMAGE_POSITION_META  # meta (og:image) | body (figure/img)


def upsert_image_candidate(
    candidates: list[ImageCandidate], cand: ImageCandidate
) -> None:
    """P-0 source-caption dedup/merge: same resolved URL keeps a single candidate.

    First-saved position and source text win. An empty stored caption is
    filled by a later non-empty source caption/credit; empty values never
    overwrite non-empty ones; whitespace-only is treated as empty. Only
    whitespace normalization is applied — no translation, rewriting or
    inference (nested-tag figcaption text is preserved as collected).
    """
    caption = (cand.caption or "").strip()
    for existing in candidates:
        if existing.url == cand.url:
            if not existing.caption and caption:
                existing.caption = caption
            return
    candidates.append(ImageCandidate(url=cand.url, caption=caption, position=cand.position))


@dataclass
class DiscoveredArticle:
    url: str
    title: str
    description: str = ""
    text: str = ""
    author: str = ""
    published_at: datetime | None = None
    cover_image_url: str | None = None
    image_candidates: list[ImageCandidate] = field(default_factory=list)
    # B-1: real pixel-validated cover eligibility (feeds the has_cover
    # signal); distinct from merely "has a cover URL".
    has_qualified_cover: bool = False
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    source: str = ""
    needs_extraction: bool = True


def collect_image_candidates_from_html(
    html: str, base_url: str, max_body_images: int = 6
) -> list[ImageCandidate]:
    """Collect image candidates (og:image + body figure/img) from raw HTML.

    Records URL, original caption (figcaption) and position. Relative URLs
    are resolved against ``base_url``; data:/svg/noise URLs are skipped.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("image candidate collection failed for %s: %s", base_url[:80], exc)
        return []

    candidates: list[ImageCandidate] = []

    def _add(url: str, caption: str, position: str) -> None:
        url = (url or "").strip()
        if not url or url.startswith("data:") or url.lower().endswith(".svg"):
            return
        if _IMG_URL_NOISE_RE.search(url):
            return
        resolved = urljoin(base_url, url)
        # P-0: same resolved URL merges in place (first-saved position kept;
        # empty caption filled by the first later non-empty source caption).
        upsert_image_candidate(
            candidates,
            ImageCandidate(url=resolved, caption=caption, position=position),
        )

    for meta in soup.find_all(
        "meta", attrs={"property": re.compile(r"^(?:og|twitter):image$")}
    ):
        _add(meta.get("content", ""), "", IMAGE_POSITION_META)

    body_count = 0
    for figure in soup.find_all("figure"):
        if body_count >= max_body_images:
            break
        img = figure.find("img")
        if img is None:
            continue
        figcaption = figure.find("figcaption")
        _add(
            img.get("src") or img.get("data-src", ""),
            figcaption.get_text(" ", strip=True) if figcaption else "",
            IMAGE_POSITION_BODY,
        )
        body_count += 1

    return candidates


ARTICLE_SOURCES = {
    "guardian": {
        "type": "api",
        "base_url": "https://content.guardianapis.com",
        # B-2: society carries Guardian health/NHS coverage (their content
        # API has no top-level "health" section); artanddesign/lifeandstyle
        # widen the topic mix beyond science/tech/culture.
        "sections": ["science", "technology", "culture", "society", "artanddesign",
                     "lifeandstyle"],
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
            "health": "https://feeds.bbci.co.uk/news/health/rss.xml",
            "entertainment_and_arts": (
                "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"
            ),
        },
        # B-1: width upgrade is centralized in cover_download.upgrade_image_url.
        "image_width_upgrade": {"from": 240, "to": 1280},
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

                web_url = item.get("webUrl", "")
                thumbnail = fields.get("thumbnail")
                image_candidates: list[ImageCandidate] = []
                if thumbnail:
                    image_candidates.append(
                        ImageCandidate(url=thumbnail, position=IMAGE_POSITION_META)
                    )
                # Guardian skips trafilatura (API body), so collect body
                # figure/img candidates here (B-1). P-0: same URL as the
                # thumbnail merges in place, so its figcaption populates the
                # empty meta thumbnail caption while the meta position is kept.
                for cand in collect_image_candidates_from_html(body_html, web_url):
                    upsert_image_candidate(image_candidates, cand)

                articles.append(
                    DiscoveredArticle(
                        url=web_url,
                        title=fields.get("headline", item.get("webTitle", "")),
                        description=_strip_html(fields.get("standfirst", "")),
                        text=text,
                        author=fields.get("byline", ""),
                        cover_image_url=thumbnail,
                        image_candidates=image_candidates,
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
    # B-1: centralized upgrade rules live in cover_download so extraction
    # candidates and RSS thumbnails share the same patterns (target 1280).
    from app.services.daily_reader.cover_download import upgrade_image_url

    return upgrade_image_url(url)


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
