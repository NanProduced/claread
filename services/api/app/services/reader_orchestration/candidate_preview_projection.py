"""Typed preview projection for Candidate Recovery read model.

Pure functions that transform raw ``blocks_json`` + ``quality_json`` +
``canonical_text_preview`` from a ``candidate_reading_documents`` row
into a safe :class:`ReaderCandidateDocumentPreviewDto`.

Security boundary:
    These functions NEVER leak ``blocks_json`` / ``quality_json`` /
    ``source_refs_json`` raw structures to the API boundary. They
    project only the typed fields defined in the DTOs:
    - ``document_outline``: order_index, block_type_label, heading_text,
      char_count (no block_id / parent_block_id / payload /
      interpretation_policy / canonical_text_*_utf16 / source_refs /
      quality).
    - ``risk_items``: risk_kind (controlled enum), user_message
      (backend-generated Chinese copy, no quality_json key names),
      severity.
    - ``preview_text``: assembled from block text_content, truncated
      per preview_mode.

preview_mode thresholds (UTF-16 code units, matching
``utf16_code_unit_length`` from app.contracts.annotation):
    - total_char_count <= FULL_TEXT_THRESHOLD (2000): full_text
    - FULL_TEXT_THRESHOLD < total_char_count <= EXTENDED_PREVIEW_LIMIT
      (8000): truncated_preview
    - total_char_count > EXTENDED_PREVIEW_LIMIT: outline_only
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.contracts.annotation import utf16_code_unit_length
from app.schemas.reader_orchestration import (
    ReaderCandidateDocumentOutlineItemDto,
    ReaderCandidateDocumentPreviewDto,
    ReaderCandidateDocumentPreviewMode,
    ReaderCandidateDocumentRiskItemDto,
    ReaderCandidateDocumentRiskKind,
    ReaderCandidateDocumentRiskSeverity,
    ReaderCandidateDocumentBlockTypeLabel,
)

FULL_TEXT_THRESHOLD = 2000
EXTENDED_PREVIEW_LIMIT = 8000
TRUNCATED_PREVIEW_CHAR_LIMIT = 2000


class CandidatePreviewProjectionError(ValueError):
    """Raised when blocks_json / quality_json cannot be safely projected."""


# ----------------------------------------------------------------------
# block_type -> block_type_label mapping
# ----------------------------------------------------------------------

_BLOCK_TYPE_LABEL_MAP: dict[str, ReaderCandidateDocumentBlockTypeLabel] = {
    "heading": "heading",
    "paragraph": "paragraph",
    "list_item": "list",
    "blockquote": "quote",
    "code_block": "code",
}

# Structural block types that don't have a dedicated label -> "other".
# Includes: table, table_row, table_cell, image, image_ocr, caption,
# footnote, unknown.


# ----------------------------------------------------------------------
# quality_json suitability flag -> risk_kind mapping
# ----------------------------------------------------------------------

# Maps internal suitability flag names to controlled risk_kind enums.
# The user_message is generated per risk_kind (not per flag) so the
# frontend never sees the internal flag name.
_FLAG_TO_RISK_KIND: dict[str, ReaderCandidateDocumentRiskKind] = {
    "ocr_low_confidence": "low_confidence_ocr",
    "layout_order_uncertain": "low_confidence_ocr",
    "image_ocr_uncertain": "low_confidence_ocr",
    "short_content": "short_content",
    "language_mixed": "language_mixed",
    "encoding_warning": "encoding_warning",
    "markdown_complex_structure": "structure_fragmented",
    "table_structure_uncertain": "structure_fragmented",
    "footnote_or_caption_merged": "structure_fragmented",
    "document_block_degraded": "structure_fragmented",
    "code_dominant": "structure_fragmented",
    "link_list_dominant": "structure_fragmented",
}

_RISK_KIND_TO_MESSAGE: dict[ReaderCandidateDocumentRiskKind, str] = {
    "low_confidence_ocr": "识别置信度较低，请仔细核对内容后再确认。",
    "short_content": "内容较短，请确认是否完整。",
    "language_mixed": "内容包含混合语言，请确认主要语言。",
    "encoding_warning": "内容可能存在编码问题，请核对特殊字符。",
    "structure_fragmented": "内容结构较复杂，请查看大纲确认完整性。",
    "other": "内容存在需要注意的情况，请核对后再确认。",
}

_RISK_KIND_TO_SEVERITY: dict[ReaderCandidateDocumentRiskKind, ReaderCandidateDocumentRiskSeverity] = {
    "low_confidence_ocr": "warning",
    "short_content": "info",
    "language_mixed": "info",
    "encoding_warning": "warning",
    "structure_fragmented": "info",
    "other": "info",
}


# ----------------------------------------------------------------------
# JSON coercion helpers
# ----------------------------------------------------------------------


def _coerce_json_object(raw: Any, *, field_name: str) -> dict[str, Any]:
    """Coerce a JSONB-like value into a plain dict. Fail closed on
    non-object values (None, list, scalar, invalid JSON string)."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CandidatePreviewProjectionError(
                f"{field_name} is not valid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise CandidatePreviewProjectionError(
                f"{field_name} parses to a non-object JSON value"
            )
        return dict(parsed)
    if raw is None:
        return {}
    raise CandidatePreviewProjectionError(
        f"{field_name} must be a JSON object"
    )


def _coerce_blocks_list(raw: Any) -> list[dict[str, Any]]:
    """Coerce blocks_json into a list of dict. Fail closed on non-list
    or empty list."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CandidatePreviewProjectionError(
                f"blocks_json is not valid JSON: {exc}"
            ) from exc
    if not isinstance(raw, list):
        raise CandidatePreviewProjectionError(
            f"blocks_json must be a list (got {type(raw).__name__})"
        )
    if len(raw) == 0:
        raise CandidatePreviewProjectionError(
            "blocks_json is empty; cannot project preview"
        )
    return [dict(b) if isinstance(b, Mapping) else b for b in raw]


# ----------------------------------------------------------------------
# Outline projection
# ----------------------------------------------------------------------


def _block_type_label(block_type: str | None) -> ReaderCandidateDocumentBlockTypeLabel:
    if block_type is None:
        return "other"
    return _BLOCK_TYPE_LABEL_MAP.get(block_type, "other")


def _heading_text(block: dict[str, Any]) -> str | None:
    """Extract heading text from a block. Only heading blocks have
    heading_text; all other types return None."""
    block_type = block.get("block_type")
    if block_type != "heading":
        return None
    text_content = block.get("text_content")
    if isinstance(text_content, str) and text_content.strip():
        return text_content.strip()
    # Fallback: heading text may live in payload_json
    payload = block.get("payload_json")
    if isinstance(payload, Mapping):
        heading = payload.get("heading_text") or payload.get("text")
        if isinstance(heading, str) and heading.strip():
            return heading.strip()
    return None


def _block_char_count(block: dict[str, Any]) -> int:
    """UTF-16 code unit length of a block's text_content. Returns 0 for
    blocks with no text_content (e.g. structural blocks like tables)."""
    text_content = block.get("text_content")
    if not isinstance(text_content, str) or not text_content:
        return 0
    return utf16_code_unit_length(text_content)


def _project_document_outline(
    blocks: list[dict[str, Any]],
) -> list[ReaderCandidateDocumentOutlineItemDto]:
    """Project blocks_json into a safe outline list.

    Each block becomes one outline item with:
    - order_index (from block.order_index, or list index as fallback)
    - block_type_label (controlled enum, never the raw block_type)
    - heading_text (only for heading blocks; None otherwise)
    - char_count (UTF-16 length of text_content)

    Does NOT expose: block_id, parent_block_id, payload_json,
    source_refs_json, quality_json, interpretation_policy,
    canonical_text_start_utf16, canonical_text_end_utf16.
    """
    outline: list[ReaderCandidateDocumentOutlineItemDto] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            continue
        order_index = block.get("order_index")
        if not isinstance(order_index, int) or order_index < 0:
            order_index = index
        block_type = block.get("block_type")
        outline.append(
            ReaderCandidateDocumentOutlineItemDto(
                order_index=order_index,
                block_type_label=_block_type_label(
                    block_type if isinstance(block_type, str) else None
                ),
                heading_text=_heading_text(dict(block)),
                char_count=_block_char_count(dict(block)),
            )
        )
    return outline


# ----------------------------------------------------------------------
# Risk items projection
# ----------------------------------------------------------------------


def _project_risk_items(
    quality_json: dict[str, Any],
) -> list[ReaderCandidateDocumentRiskItemDto]:
    """Project quality_json suitability flags into typed risk items.

    Maps internal flag names to controlled risk_kind enums. Generates
    backend Chinese user_message per risk_kind (never per flag). The
    frontend never sees the internal flag names.

    Deduplicates by risk_kind so multiple flags mapping to the same
    risk_kind produce one risk item.
    """
    suitability = quality_json.get("suitability")
    if not isinstance(suitability, Mapping):
        return []

    flags = suitability.get("flags")
    if not isinstance(flags, list):
        return []

    seen_kinds: set[ReaderCandidateDocumentRiskKind] = set()
    risk_items: list[ReaderCandidateDocumentRiskItemDto] = []

    for flag in flags:
        if not isinstance(flag, str):
            continue
        risk_kind = _FLAG_TO_RISK_KIND.get(flag)
        if risk_kind is None:
            # Unknown flag -> "other" risk_kind (defense-in-depth).
            # Logged but not exposed with the raw flag name.
            risk_kind = "other"
        if risk_kind in seen_kinds:
            continue
        seen_kinds.add(risk_kind)
        risk_items.append(
            ReaderCandidateDocumentRiskItemDto(
                risk_kind=risk_kind,
                user_message=_RISK_KIND_TO_MESSAGE[risk_kind],
                severity=_RISK_KIND_TO_SEVERITY[risk_kind],
            )
        )

    return risk_items


# ----------------------------------------------------------------------
# Preview text assembly
# ----------------------------------------------------------------------


def _assemble_full_text(blocks: list[dict[str, Any]]) -> str:
    """Assemble the complete document text from block text_content,
    joined by double newlines. Only includes blocks with non-empty
    text_content."""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        text_content = block.get("text_content")
        if isinstance(text_content, str) and text_content.strip():
            parts.append(text_content.strip())
    return "\n\n".join(parts)


def _total_char_count(blocks: list[dict[str, Any]]) -> int:
    """Sum of UTF-16 code unit lengths across all block text_content."""
    total = 0
    for block in blocks:
        if isinstance(block, Mapping):
            total += _block_char_count(dict(block))
    return total


def _decide_preview_mode(total_char_count: int) -> ReaderCandidateDocumentPreviewMode:
    if total_char_count <= FULL_TEXT_THRESHOLD:
        return "full_text"
    if total_char_count <= EXTENDED_PREVIEW_LIMIT:
        return "truncated_preview"
    return "outline_only"


# ----------------------------------------------------------------------
# Main projection entry point
# ----------------------------------------------------------------------


def build_candidate_preview_projection(
    *,
    blocks_json: Any,
    quality_json: Any,
    canonical_text_preview: str,
) -> ReaderCandidateDocumentPreviewDto:
    """Build a safe typed preview projection from raw candidate fields.

    This is the single entry point used by the read service. It:
    1. Parses blocks_json into a list of dict (fail closed on invalid).
    2. Parses quality_json into a dict (fail closed on invalid).
    3. Computes total_char_count (UTF-16 sum of block text_content).
    4. Decides preview_mode based on total_char_count thresholds.
    5. Assembles preview_text per preview_mode:
       - full_text: complete text from blocks
       - truncated_preview: first TRUNCATED_PREVIEW_CHAR_LIMIT chars
       - outline_only: empty string
    6. Projects document_outline (safe fields only).
    7. Projects risk_items from quality_json flags (typed, no raw keys).

    Args:
        blocks_json: Raw JSONB value (list, dict, or JSON string) from
            candidate_reading_documents.blocks_json.
        quality_json: Raw JSONB value from quality_json.
        canonical_text_preview: The candidate's canonical_text_preview
            (used as a fallback for preview_text when blocks are
            unparseable; normally blocks_json is the truth source).

    Returns:
        A :class:`ReaderCandidateDocumentPreviewDto` safe for API
        response.

    Raises:
        CandidatePreviewProjectionError: If blocks_json is missing,
            empty, or unparseable. The read service treats this as a
            500 (the candidate should not have been persisted with
            invalid blocks).
    """
    blocks = _coerce_blocks_list(blocks_json)
    quality = _coerce_json_object(quality_json, field_name="quality_json")

    total = _total_char_count(blocks)
    preview_mode = _decide_preview_mode(total)

    if preview_mode == "full_text":
        preview_text = _assemble_full_text(blocks)
        is_truncated = False
    elif preview_mode == "truncated_preview":
        full_text = _assemble_full_text(blocks)
        if len(full_text) > TRUNCATED_PREVIEW_CHAR_LIMIT:
            preview_text = full_text[:TRUNCATED_PREVIEW_CHAR_LIMIT]
            is_truncated = True
        else:
            preview_text = full_text
            is_truncated = total > 0 and len(preview_text) < total
    else:
        # outline_only: no preview text
        preview_text = ""
        is_truncated = True

    outline = _project_document_outline(blocks)
    risk_items = _project_risk_items(quality)

    return ReaderCandidateDocumentPreviewDto(
        preview_mode=preview_mode,
        preview_text=preview_text,
        is_truncated=is_truncated,
        total_char_count=total,
        document_outline=outline,
        risk_items=risk_items,
    )


# ----------------------------------------------------------------------
# source_label projection
# ----------------------------------------------------------------------


_SOURCE_TYPE_LABEL_MAP: dict[str, str] = {
    "plain_text": "粘贴文本",
    "markdown": "Markdown 文档",
    "file_ref": "上传文件",
    "url": "网页链接",
    "image_ref": "图片 OCR",
}


def build_source_label(
    *,
    source_type: str,
    filename: str | None,
) -> str:
    """Generate a user-friendly source label from source_type + filename.

    The label does NOT expose the raw source_type value; it maps to a
    Chinese friendly string. If filename is present, it is appended in
    parentheses for file_ref / markdown / image_ref types.
    """
    base_label = _SOURCE_TYPE_LABEL_MAP.get(source_type, "上传内容")
    if filename and source_type in ("file_ref", "markdown", "image_ref"):
        return f"{base_label}（{filename}）"
    return base_label
