"""Deterministic, source-priority cover selection (P-0).

Selects exactly one cover image from the pixel-qualified candidates using a
single fixed rule set — no LLM resolver, no multimodal model, no generated
caption. Source captions/credits are passed through unchanged (or null when
missing); the AI Chinese caption key is kept for compatibility but fixed to
null on new output. This module never makes a provider call.

Layout tags map dimensions to the three fixed rendering slots of the daily
reader surface brief (full-bleed / two-third / half-float); Track C renders.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.daily_reader.cover_download import ValidatedCandidate
from app.services.daily_reader.discovery import IMAGE_POSITION_META

LAYOUT_FULL_BLEED = "full-bleed"
LAYOUT_TWO_THIRD = "two-third"
LAYOUT_HALF_FLOAT = "half-float"

# P-0 fixed machine selection mode (no candidates still yields "none").
SELECTION_MODE_DETERMINISTIC_SOURCE = "deterministic_source"
SELECTION_MODE_NONE = "none"

# Fixed reason recorded when the cover pool is ambiguous and uncaptioned;
# flags future visual-fallback *eligibility* only — never triggers a call.
VISUAL_FALLBACK_REASON_CAPTION_MISSING = "caption_missing_multi_image_ambiguity"


def layout_for_dimensions(width: int, height: int) -> str:
    """Map aspect ratio to the three fixed layout slots (surface brief \u00a74)."""
    if height <= 0:
        return LAYOUT_TWO_THIRD
    ratio = width / height
    if ratio >= 1.9:
        return LAYOUT_FULL_BLEED
    if ratio >= 1.25:
        return LAYOUT_TWO_THIRD
    return LAYOUT_HALF_FLOAT


def build_image_block(
    *,
    block_id: str,
    role: str,
    url: str,
    width: int,
    height: int,
    source_caption: str = "",
) -> dict:
    """body_json image block contract (additive; rendering is Track C).

    P-0: ``caption_zh`` key remains for compatibility but is fixed to null;
    ``source_caption`` carries the selected candidate's caption/credit or null.
    """
    return {
        "id": block_id,
        "role": role,  # cover for new output; inline only in legacy data
        "url": url,
        "width": width,
        "height": height,
        "layout": layout_for_dimensions(width, height),
        "caption_zh": None,
        "source_caption": (source_caption or "").strip() or None,
    }


@dataclass(frozen=True)
class CoverSelection:
    """Deterministic selection result: machine mode + the selected cover index."""

    mode: str
    cover_index: int | None


def cover_pool_indices(candidates: list[ValidatedCandidate]) -> list[int]:
    """Pool = qualified meta candidates when any exist, else all qualified."""
    if not candidates:
        return []
    meta = [i for i, c in enumerate(candidates) if c.position == IMAGE_POSITION_META]
    return meta if meta else list(range(len(candidates)))


def visual_fallback_eligible(candidates: list[ValidatedCandidate]) -> bool:
    """True only when the pool is ambiguous (2+) and every pool caption is empty."""
    pool = cover_pool_indices(candidates)
    return len(pool) > 1 and all(not candidates[i].caption.strip() for i in pool)


def select_cover_images(candidates: list[ValidatedCandidate]) -> CoverSelection:
    """Deterministic source-priority selection; never calls a model.

    Within the downloaded, pixel-qualified candidates (already capped at
    MAX_COVER_CANDIDATES):
    1. a meta pool is preferred over a body pool;
    2. pool members with a non-empty source caption win;
    3. ties break by larger width;
    4. remaining ties keep the original input order (stable; no random/hash).

    New output selects exactly one cover and never an inline image.
    """
    if not candidates:
        return CoverSelection(mode=SELECTION_MODE_NONE, cover_index=None)

    pool = cover_pool_indices(candidates)

    def _key(index: int) -> tuple:
        cand = candidates[index]
        return (not (cand.caption or "").strip(), -cand.width)

    selected_index = min(pool, key=_key)  # min keeps original order on ties
    return CoverSelection(
        mode=SELECTION_MODE_DETERMINISTIC_SOURCE,
        cover_index=selected_index,
    )


__all__ = [
    "LAYOUT_FULL_BLEED",
    "LAYOUT_TWO_THIRD",
    "LAYOUT_HALF_FLOAT",
    "SELECTION_MODE_DETERMINISTIC_SOURCE",
    "SELECTION_MODE_NONE",
    "VISUAL_FALLBACK_REASON_CAPTION_MISSING",
    "CoverSelection",
    "build_image_block",
    "cover_pool_indices",
    "layout_for_dimensions",
    "select_cover_images",
    "visual_fallback_eligible",
]
