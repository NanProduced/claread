from __future__ import annotations

from dataclasses import dataclass

from app.contracts.annotation import (
    TEXT_RANGE_HASH_ALGORITHM,
    TEXT_RANGE_OFFSET_UNIT,
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)

UNSUPPORTED_OFFSET_UNIT = "unsupported_offset_unit"
UNSUPPORTED_HASH_ALGORITHM = "unsupported_hash_algorithm"
INVALID_OFFSET_SPAN = "invalid_offset_span"
SELECTED_TEXT_LENGTH_MISMATCH = "selected_text_length_mismatch"
TEXT_HASH_MISMATCH = "text_hash_mismatch"
OUTSIDE_ANCHOR_SEGMENT_RANGE = "outside_anchor_segment_range"
OFFSETS_DO_NOT_SLICE_UNIT_TEXT = "offsets_do_not_slice_unit_text"
SELECTED_TEXT_MISMATCH = "selected_text_mismatch"


class AnchorValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AnchorSegmentRange:
    anchor_segment_id: str
    unit_start_utf16: int
    unit_end_utf16: int


def validate_text_anchor_payload(
    *,
    offset_unit: str,
    start_offset: int,
    end_offset: int,
    selected_text: str,
    text_hash: str,
    hash_algorithm: str = TEXT_RANGE_HASH_ALGORITHM,
) -> None:
    if offset_unit != TEXT_RANGE_OFFSET_UNIT:
        raise AnchorValidationError(
            UNSUPPORTED_OFFSET_UNIT,
            f"offset_unit must be {TEXT_RANGE_OFFSET_UNIT}",
        )
    if hash_algorithm != TEXT_RANGE_HASH_ALGORITHM:
        raise AnchorValidationError(
            UNSUPPORTED_HASH_ALGORITHM,
            f"hash_algorithm must be {TEXT_RANGE_HASH_ALGORITHM}",
        )
    if end_offset <= start_offset:
        raise AnchorValidationError(
            INVALID_OFFSET_SPAN,
            "end_offset must be greater than start_offset",
        )
    if utf16_code_unit_length(selected_text) != end_offset - start_offset:
        raise AnchorValidationError(
            SELECTED_TEXT_LENGTH_MISMATCH,
            "selected_text UTF-16 length must match offset span",
        )
    if compute_text_range_hash(selected_text) != text_hash:
        raise AnchorValidationError(
            TEXT_HASH_MISMATCH,
            "text_hash must match selected_text",
        )


def validate_text_anchor_against_unit(
    *,
    offset_unit: str,
    start_offset: int,
    end_offset: int,
    selected_text: str,
    text_hash: str,
    unit_text: str,
    anchor_segment: AnchorSegmentRange,
    hash_algorithm: str = TEXT_RANGE_HASH_ALGORITHM,
) -> str:
    validate_text_anchor_payload(
        offset_unit=offset_unit,
        start_offset=start_offset,
        end_offset=end_offset,
        selected_text=selected_text,
        text_hash=text_hash,
        hash_algorithm=hash_algorithm,
    )

    if (
        start_offset < anchor_segment.unit_start_utf16
        or end_offset > anchor_segment.unit_end_utf16
    ):
        raise AnchorValidationError(
            OUTSIDE_ANCHOR_SEGMENT_RANGE,
            f"offsets fall outside anchor segment {anchor_segment.anchor_segment_id}",
        )

    selected_text_at_offsets = slice_by_utf16_offsets(unit_text, start_offset, end_offset)
    if selected_text_at_offsets is None:
        raise AnchorValidationError(
            OFFSETS_DO_NOT_SLICE_UNIT_TEXT,
            "offsets do not slice unit_text",
        )
    if selected_text_at_offsets != selected_text:
        raise AnchorValidationError(
            SELECTED_TEXT_MISMATCH,
            "selected_text does not match unit_text at offsets",
        )
    return selected_text_at_offsets
