from __future__ import annotations

import pytest

from app.contracts.anchor_validation import (
    INVALID_OFFSET_SPAN,
    OFFSETS_DO_NOT_SLICE_UNIT_TEXT,
    OUTSIDE_ANCHOR_SEGMENT_RANGE,
    SELECTED_TEXT_LENGTH_MISMATCH,
    SELECTED_TEXT_MISMATCH,
    TEXT_HASH_MISMATCH,
    UNSUPPORTED_HASH_ALGORITHM,
    UNSUPPORTED_OFFSET_UNIT,
    AnchorSegmentRange,
    AnchorValidationError,
    validate_text_anchor_against_unit,
    validate_text_anchor_payload,
)
from app.contracts.annotation import compute_text_range_hash


def test_validate_text_anchor_payload_accepts_utf16_span() -> None:
    text = "🧠"

    validate_text_anchor_payload(
        offset_unit="utf16",
        start_offset=1,
        end_offset=3,
        selected_text=text,
        text_hash=compute_text_range_hash(text),
    )


def test_validate_text_anchor_payload_rejects_unsupported_offset_unit() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_payload(
            offset_unit="unicode_code_point",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
        )

    assert exc_info.value.code == UNSUPPORTED_OFFSET_UNIT


def test_validate_text_anchor_payload_rejects_unsupported_hash_algorithm() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_payload(
            offset_unit="utf16",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
            hash_algorithm="sha256",
        )

    assert exc_info.value.code == UNSUPPORTED_HASH_ALGORITHM


def test_validate_text_anchor_payload_rejects_invalid_offset_span() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_payload(
            offset_unit="utf16",
            start_offset=5,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
        )

    assert exc_info.value.code == INVALID_OFFSET_SPAN


def test_validate_text_anchor_payload_rejects_utf16_length_mismatch() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_payload(
            offset_unit="utf16",
            start_offset=0,
            end_offset=4,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
        )

    assert exc_info.value.code == SELECTED_TEXT_LENGTH_MISMATCH


def test_validate_text_anchor_payload_rejects_hash_mismatch() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_payload(
            offset_unit="utf16",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("world"),
        )

    assert exc_info.value.code == TEXT_HASH_MISMATCH


def test_validate_text_anchor_against_unit_accepts_anchor_segment_slice() -> None:
    unit_text = "A🧠BC"
    anchor_segment = AnchorSegmentRange(
        anchor_segment_id="s1",
        unit_start_utf16=1,
        unit_end_utf16=3,
    )

    selected_text = validate_text_anchor_against_unit(
        offset_unit="utf16",
        start_offset=1,
        end_offset=3,
        selected_text="🧠",
        text_hash=compute_text_range_hash("🧠"),
        unit_text=unit_text,
        anchor_segment=anchor_segment,
    )

    assert selected_text == "🧠"


def test_validate_text_anchor_against_unit_rejects_offsets_outside_segment_range() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_against_unit(
            offset_unit="utf16",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
            unit_text="hello world",
            anchor_segment=AnchorSegmentRange(
                anchor_segment_id="s1",
                unit_start_utf16=6,
                unit_end_utf16=11,
            ),
        )

    assert exc_info.value.code == OUTSIDE_ANCHOR_SEGMENT_RANGE


def test_validate_text_anchor_against_unit_rejects_offsets_that_do_not_slice_unit_text() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_against_unit(
            offset_unit="utf16",
            start_offset=3,
            end_offset=8,
            selected_text="world",
            text_hash=compute_text_range_hash("world"),
            unit_text="hello",
            anchor_segment=AnchorSegmentRange(
                anchor_segment_id="s1",
                unit_start_utf16=0,
                unit_end_utf16=8,
            ),
        )

    assert exc_info.value.code == OFFSETS_DO_NOT_SLICE_UNIT_TEXT


def test_validate_text_anchor_against_unit_rejects_selected_text_mismatch() -> None:
    with pytest.raises(AnchorValidationError) as exc_info:
        validate_text_anchor_against_unit(
            offset_unit="utf16",
            start_offset=0,
            end_offset=5,
            selected_text="world",
            text_hash=compute_text_range_hash("world"),
            unit_text="hello world",
            anchor_segment=AnchorSegmentRange(
                anchor_segment_id="s1",
                unit_start_utf16=0,
                unit_end_utf16=5,
            ),
        )

    assert exc_info.value.code == SELECTED_TEXT_MISMATCH
