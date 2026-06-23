from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor


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
