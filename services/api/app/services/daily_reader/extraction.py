"""Extraction layer for Daily Reader article pipeline.

Uses trafilatura to extract full text from article URLs.

Also hosts the A-1 dirty-data gate: deterministic cleaning for the three
exposed scrape artefacts (BBC "- Published" head residue, BBC
"external"/"internal" link badges, copyright/subscribe/transcript footer
lines) plus NPR transcript detection (transcripts are rejected, not
cleaned). Only the exposed forms are handled — no general boilerplate
framework (decision-record §9).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape

from app.services.daily_reader.discovery import (
    IMAGE_POSITION_META,
    DiscoveredArticle,
    ImageCandidate,
    collect_image_candidates_from_html,
)

logger = logging.getLogger(__name__)

# --- A-1 dirty-data gate: deterministic patterns for exposed artefacts ---

# BBC head residue: a bare "- Published" / "(Published …)" line, or the
# same prefix glued to the first content line. The dash/paren forms are the
# scrape artefact; a bare "Published …" sentence start is left untouched.
_PUBLISHED_LINE_RE = re.compile(
    r"^\s*(?:-\s*Published\b|\(\s*Published[^)]*\)).*$",
    re.IGNORECASE,
)

# BBC link badges flattened into text: "figures, external,", "platforms,
# external.", "open letter, external entitled". Only the comma-adjacent
# lowercase token is stripped so ordinary uses ("external auditors", sentence
# starts) survive; known ceiling — a real appositive like "the committee,
# external experts, …" would also lose the token.
_LINK_BADGE_RE = re.compile(r",\s*\b(?:external|internal)\b", re.IGNORECASE)

# Footer boilerplate lines: copyright block, subscribe CTA, syndication
# notice, NPR transcript accuracy disclaimer.
_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"copyright\b"
    r"|subscribe to\b"
    r"|this article originally appeared on\b"
    r"|accuracy and availability of\b"
    r")",
    re.IGNORECASE,
)

# NPR transcript cues: all-caps speaker lines and soundbite markers.
_TRANSCRIPT_CUE_RE = re.compile(
    r"^[A-Z][A-Z .\-'\u2019]*(?:\s+[A-Z][A-Z .\-'\u2019]*)*,\s*"
    r"(?:HOST|BYLINE|CORRESPONDENT|ANCHOR|GUEST):",
    re.MULTILINE,
)
_TRANSCRIPT_SOUNDBITE_RE = re.compile(r"\(SOUNDBITE OF\b", re.IGNORECASE)

# Surface scan fragments for the workflow review gate (artefact-level).
_SURFACE_BOILERPLATE_RES = (
    _LINK_BADGE_RE,
    re.compile(r"copyright\b.{0,80}all rights reserved", re.IGNORECASE),
    re.compile(r"\bthis article originally appeared on\b", re.IGNORECASE),
    re.compile(r"accuracy and availability of\b", re.IGNORECASE),
    _TRANSCRIPT_CUE_RE,
    _TRANSCRIPT_SOUNDBITE_RE,
)


@dataclass
class ExtractionResult:
    text: str
    author: str = ""
    description: str = ""
    cover_image_url: str | None = None
    # B-1: multi-image candidates (og:image meta + body figure/img) with
    # original caption and position; the cover pipeline validates/selects.
    image_candidates: list[ImageCandidate] = field(default_factory=list)
    word_count: int = 0
    rejection_reason: str | None = None


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

        # B-1: multi-candidate collection reuses the already-downloaded page.
        image_candidates = collect_image_candidates_from_html(downloaded, url)
        if cover_image_url and cover_image_url not in {c.url for c in image_candidates}:
            image_candidates.insert(
                0, ImageCandidate(url=cover_image_url, position=IMAGE_POSITION_META)
            )
        if image_candidates and not cover_image_url:
            cover_image_url = image_candidates[0].url

        cleaned = _clean_extracted_text(result)

        markers = detect_transcript_markers(cleaned)
        if markers:
            reason = (
                "npr_transcript: transcript markers detected "
                f"({', '.join(markers)}); transcripts are not articles"
            )
            logger.warning("trafilatura: rejected transcript %s (%s)", url, reason)
            return ExtractionResult(text="", rejection_reason=reason)

        clean_result = clean_extracted_article(cleaned)
        word_count = len(clean_result.split())

        return ExtractionResult(
            text=clean_result,
            author=_clean_extracted_text(author),
            description=_clean_extracted_text(description),
            cover_image_url=cover_image_url,
            image_candidates=image_candidates,
            word_count=word_count,
        )
    except Exception as e:
        logger.warning("trafilatura extraction failed for %s: %s", url, e)
        return None


def _clean_extracted_text(text: str) -> str:
    return unescape(text or "").replace("\u00A0", " ").strip()


def clean_extracted_article(text: str) -> str:
    """Deterministic A-1 cleaning for the three exposed dirty-data forms."""
    if not text:
        return ""
    kept_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
            continue
        if _PUBLISHED_LINE_RE.match(stripped) or _BOILERPLATE_LINE_RE.match(stripped):
            continue
        kept_lines.append(_LINK_BADGE_RE.sub("", line))
    return "\n".join(kept_lines).strip()


def detect_transcript_markers(text: str) -> list[str]:
    """Return detected transcript marker kinds; non-empty means reject.

    Threshold: >=2 speaker cues, or >=1 cue plus a SOUNDBITE marker. A
    single stray cue in a normal article does not trigger rejection.
    """
    if not text:
        return []
    cue_count = len(_TRANSCRIPT_CUE_RE.findall(text))
    soundbite_count = len(_TRANSCRIPT_SOUNDBITE_RE.findall(text))
    markers: list[str] = []
    if cue_count >= 2 or (cue_count >= 1 and soundbite_count >= 1):
        markers.append(f"speaker_cue_x{cue_count}" if cue_count else "speaker_cue")
    if soundbite_count and (cue_count >= 1 or soundbite_count >= 2):
        markers.append(f"soundbite_x{soundbite_count}")
    return markers


def find_boilerplate_hits(texts: list[str]) -> list[str]:
    """Scan artefact surfaces for leaked dirty fragments (review gate)."""
    hits: list[str] = []
    for text in texts:
        if not text:
            continue
        for pattern in _SURFACE_BOILERPLATE_RES:
            match = pattern.search(text)
            if match:
                hits.append(match.group(0).strip())
    return hits


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
    if extraction.image_candidates and not article.image_candidates:
        article.image_candidates = extraction.image_candidates
