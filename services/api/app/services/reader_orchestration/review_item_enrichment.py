"""R8 — Structured Review Item enrichment for confirmed-source responses.

The suitability gate (``input_suitability_gate``) and the Markdown parser emit
closed three-level classifications (``silent`` / ``adaptation_notice`` /
``content_check``). This module enriches the ``content_check`` review surface
with the structured review-item contract frozen in the Read Intake &
Content Check surface specification (``apps/web/docs/design/
surface-read-intake-content-check.md`` §13.1 Open Contract):

- ``issue_id`` — deterministic, stable within the issue namespace (one
  ``(record, generation)`` scope); **distinct per occurrence** for same-code
  items (never an array index, never merged).
- ``tier`` — ``attention`` / ``routine`` product tier inside
  ``content_check`` (deterministic code mapping, fail closed to
  ``attention`` for unknown codes). ``silent`` / ``adaptation_notice``
  records never carry review metadata.
- ``target_scope`` — ``document`` for full-document items,
  ``range`` otherwise.
- ``source_anchor`` / ``anchor_hash`` / ``evidence`` — computed ONLY from
  exact, deterministic positions (currently the unclosed-fence opening
  line); **no text-similarity or fuzzy guessing** is ever used. Missing
  evidence degrades to ``null``.
- ``source_media_coordinate`` — always present as a nullable field;
  page/bbox data is not currently derivable and stays ``null``.

Callers persist the enriched records inside
``candidate_reading_documents.quality_json.suitability.adaptations``
and/or return them in confirmed-source responses; the response DTOs
validate them via ``StructuredReviewItem``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from app.schemas.reader_input_adapter import AdaptationRecord

# Product tier inside content_check per surface spec §7 (closed code table).
# Unknown content_check codes stay fail-closed at ``attention``.
_ROUTINE_CODES = frozenset(
    {
        "source_type_review_default",
        "ocr_low_confidence",
        "image_ocr_uncertain",
        "document_block_degraded",
        "footnote_reference",
        "task_list_unsupported",
    }
)

_FENCE_MARKERS = ("```", "~~~")
_ISSUE_ID_DIGEST_LENGTH = 16


def _utf16_code_unit_length(text: str) -> int:
    """UTF-16LE code unit length.

    Python ``len()`` counts code points: astral characters (emoji etc.)
    are a surrogate PAIR = **2** UTF-16 code units while ``len()`` says 1.
    All offsets in the review-item contract are UTF-16 code units, so
    ``len()`` must never be used for offsets.
    """
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _issue_id(namespace: str, code: str, occurrence_index: int) -> str:
    material = f"{namespace}\x00{code}\x00{occurrence_index}".encode()
    return hashlib.sha256(material).hexdigest()[:_ISSUE_ID_DIGEST_LENGTH]


def _tier_for(code: str) -> str:
    if code in _ROUTINE_CODES:
        return "routine"
    return "attention"


def _locate_unclosed_fence_openings(text: str) -> list[dict[str, Any]]:
    """Exact, deterministic positions of unclosed fenced-code openings.

    Line-based scan: a fence opens on a line whose content starts with a
    fence marker (after up to three leading spaces, per GFM); a closing
    fence is a line containing the same marker with nothing but optional
    trailing whitespace after it. Anything else while inside a fence is
    body content. Never guesses: positions are literal **UTF-16 code
    unit** offsets of the opening marker line (see
    ``_utf16_code_unit_length``).
    """
    openings: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        leading = len(content) - len(content.lstrip(" "))
        if leading > 3:
            leading = 0  # more than three spaces = indented code, not a fence
        trimmed = content[leading:]
        marker = next((m for m in _FENCE_MARKERS if trimmed.startswith(m)), None)
        if marker is None:
            offset += _utf16_code_unit_length(line)
            continue
        if active is None:
            active = {
                "start": offset + leading,
                "marker": marker,
                "line_text": content,
            }
        elif marker == active["marker"] and trimmed[len(marker) :].strip() == "":
            # Closing fence with nothing but the marker on the line.
            active = None
        # Any other fenced line (different marker, or marker + info string)
        # while inside a fence is body content — no state change.
        offset += _utf16_code_unit_length(line)
    if active is not None:
        end = active["start"] + _utf16_code_unit_length(active["line_text"])
        openings.append(
            {
                "start_utf16": active["start"],
                "end_utf16": end,
                "excerpt": active["line_text"],
            }
        )
    return openings


def enrich_review_items(
    *,
    adaptations: Sequence[AdaptationRecord],
    issue_namespace: str,
    document_text: str | None = None,
) -> list[dict[str, Any]]:
    """Enrich adaptation records into structured review-item dicts.

    ``silent`` / ``adaptation_notice`` records pass through with their
    original fields only (they are not review items). ``content_check``
    records get the structured contract fields; evidence that cannot be
    derived exactly is ``null`` (degrade, never guessed).
    """
    fence_openings = (
        _locate_unclosed_fence_openings(document_text) if document_text is not None else []
    )
    fence_cursor = 0
    occurrence_by_code: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for record in adaptations:
        item: dict[str, Any] = {
            "code": record.code,
            "message": record.message,
            "classification": record.classification,
        }
        if record.classification != "content_check":
            items.append(item)
            continue

        occurrence_index = occurrence_by_code.get(record.code, 0)
        occurrence_by_code[record.code] = occurrence_index + 1
        issue_id = _issue_id(issue_namespace, record.code, occurrence_index)

        source_anchor: dict[str, Any] | None = None
        anchor_hash: str | None = None
        excerpt: str | None = None
        if record.code == "has_unclosed_fence" and fence_cursor < len(fence_openings):
            opening = fence_openings[fence_cursor]
            fence_cursor += 1
            source_anchor = {
                "block_id": None,
                "start_utf16": opening["start_utf16"],
                "end_utf16": opening["end_utf16"],
            }
            excerpt = opening["excerpt"]
            anchor_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()

        # target_scope: range ONLY when a valid anchor is emitted; a local
        # item without a precisely derivable anchor degrades honestly to
        # document scope (a range must never be fabricated).
        target_scope = "range" if source_anchor is not None else "document"
        item.update(
            {
                "issue_id": issue_id,
                "tier": _tier_for(record.code),
                "target_scope": target_scope,
                "source_anchor": source_anchor,
                "anchor_hash": anchor_hash,
                "evidence": {
                    "excerpt_text": excerpt,
                    "proposed_patch": None,
                },
                "source_media_coordinate": None,
            }
        )
        items.append(item)
    return items


def matches_issue_id_shape(issue_id: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{16}", issue_id))
