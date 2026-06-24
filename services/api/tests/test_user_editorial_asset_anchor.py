from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.schemas.user_editorial_assets import (
    UserEditorialAssetAnchor,
    UserEditorialAssetAnchorSet,
)


def test_user_editorial_asset_anchor_accepts_utf16_text_range() -> None:
    text = "🧠"
    anchor = UserEditorialAssetAnchor(
        record_id="rec_123",
        base_id="base_123",
        generation=2,
        unit_id="u1",
        anchor_segment_id="s1",
        start_offset=10,
        end_offset=12,
        selected_text=text,
        text_hash=compute_text_range_hash(text),
    )

    assert anchor.scope == "stable_source"
    assert anchor.offset_unit == "utf16"
    assert anchor.hash_algorithm == "fnv1a32-utf16"


def test_user_editorial_asset_anchor_accepts_future_scope_values() -> None:
    anchor = UserEditorialAssetAnchor(
        record_id="rec_123",
        base_id="base_123",
        generation=1,
        unit_id="u1",
        anchor_segment_id="s1",
        scope="ask_supplement",
        start_offset=0,
        end_offset=5,
        selected_text="hello",
        text_hash=compute_text_range_hash("hello"),
    )

    assert anchor.scope == "ask_supplement"


def test_user_editorial_asset_anchor_rejects_mismatched_hash() -> None:
    with pytest.raises(ValidationError, match="text_hash must match selected_text"):
        UserEditorialAssetAnchor(
            record_id="rec_123",
            base_id="base_123",
            generation=1,
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("world"),
        )


def test_user_editorial_asset_anchor_forbids_plate_or_slate_paths() -> None:
    with pytest.raises(ValidationError):
        UserEditorialAssetAnchor(
            record_id="rec_123",
            base_id="base_123",
            generation=1,
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
            plate_path=[0, 1],
        )


def test_user_editorial_asset_anchor_remains_single_range_only() -> None:
    range_payload = {
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "start_offset": 0,
        "end_offset": 5,
        "selected_text": "hello",
        "text_hash": compute_text_range_hash("hello"),
    }

    with pytest.raises(ValidationError):
        UserEditorialAssetAnchor(
            record_id="rec_123",
            base_id="base_123",
            generation=1,
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash=compute_text_range_hash("hello"),
            anchor_mode="multi_text",
            ranges=[range_payload, range_payload],
        )


def test_user_editorial_asset_anchor_set_accepts_multi_range_draft() -> None:
    anchor_set = UserEditorialAssetAnchorSet(
        record_id="rec_123",
        base_id="base_123",
        generation=1,
        ranges=[
            {
                "unit_id": "u1",
                "anchor_segment_id": "s1",
                "start_offset": 0,
                "end_offset": 5,
                "selected_text": "hello",
                "text_hash": compute_text_range_hash("hello"),
            },
            {
                "unit_id": "u1",
                "anchor_segment_id": "s2",
                "start_offset": 6,
                "end_offset": 11,
                "selected_text": "world",
                "text_hash": compute_text_range_hash("world"),
            },
        ],
    )

    assert anchor_set.anchor_mode == "multi_text"
    assert len(anchor_set.ranges) == 2
    assert anchor_set.ranges[0].offset_unit == "utf16"


def test_user_editorial_asset_anchor_set_requires_at_least_two_ranges() -> None:
    with pytest.raises(ValidationError):
        UserEditorialAssetAnchorSet(
            record_id="rec_123",
            base_id="base_123",
            generation=1,
            ranges=[
                {
                    "unit_id": "u1",
                    "anchor_segment_id": "s1",
                    "start_offset": 0,
                    "end_offset": 5,
                    "selected_text": "hello",
                    "text_hash": compute_text_range_hash("hello"),
                },
            ],
        )


def test_user_editorial_asset_anchor_set_validates_each_range_hash() -> None:
    with pytest.raises(ValidationError, match="text_hash must match selected_text"):
        UserEditorialAssetAnchorSet(
            record_id="rec_123",
            base_id="base_123",
            generation=1,
            ranges=[
                {
                    "unit_id": "u1",
                    "anchor_segment_id": "s1",
                    "start_offset": 0,
                    "end_offset": 5,
                    "selected_text": "hello",
                    "text_hash": compute_text_range_hash("hello"),
                },
                {
                    "unit_id": "u1",
                    "anchor_segment_id": "s2",
                    "start_offset": 6,
                    "end_offset": 11,
                    "selected_text": "world",
                    "text_hash": compute_text_range_hash("wrong"),
                },
            ],
        )
