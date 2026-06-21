from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.schemas.reader_orchestration import (
    ReaderPlateSnapshot,
    ReaderTextRangeAnchor,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)


def test_reader_text_range_anchor_accepts_utf16_offsets() -> None:
    selected_text = "A🙂B"
    anchor = ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id="u1",
        anchor_segment_id="s1",
        sentence_id="s1",
        start_offset=0,
        end_offset=4,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )

    assert anchor.segment_type == "sentence"
    assert anchor.offset_unit == "utf16"


def test_reader_text_range_anchor_rejects_utf16_length_mismatch() -> None:
    with pytest.raises(ValidationError, match="UTF-16 length"):
        selected_text = "A🙂B"
        ReaderTextRangeAnchor(
            base_id="base-1",
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=3,
            selected_text=selected_text,
            text_hash=compute_text_range_hash(selected_text),
        )


def test_reader_text_range_anchor_rejects_text_hash_mismatch() -> None:
    with pytest.raises(ValidationError, match="text_hash must match selected_text"):
        ReaderTextRangeAnchor(
            base_id="base-1",
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=4,
            selected_text="A🙂B",
            text_hash="1a2b3c4d",
        )


def test_translation_layer_output_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TranslationLayerOutput(
            target_language="zh-CN",
            translated_text="测试",
            extra_field=True,
        )


def test_vocabulary_layer_output_accepts_empty_items() -> None:
    output = VocabularyLayerOutput()

    assert output.schema_version == 1
    assert output.items == []


def test_reader_plate_snapshot_rejects_projection_version() -> None:
    with pytest.raises(ValidationError):
        ReaderPlateSnapshot.model_validate(
            {
                "snapshot_id": "snap-1",
                "snapshot_taken_at": datetime.now(UTC),
                "last_event_sequence": 1,
                "record_id": "record-1",
                "base": {
                    "base_id": "base-1",
                    "content_sha256": "a" * 64,
                    "canonicalizer_version": "canon-v1",
                    "builder_version": "builder-v1",
                    "segmenter_version": "segmenter-v1",
                    "text_length_utf16": 4,
                },
                "navigation": {"units": []},
                "enhancement_layers": [],
                "ask_supplements": [],
                "user_assets": [],
                "parsed_decisions": [],
                "value": [],
                "projection_version": 3,
            }
        )
