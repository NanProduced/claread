from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    GrammarNoteLayerOutput,
    ReaderPlateSnapshot,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    SentenceAnalysisLayerOutput,
    TranslationGenerationGroup,
    TranslationGroup,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
    VocabularyPhraseGlossItem,
    VocabularyPhraseType,
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


def test_translation_layer_generation_output_accepts_group_native_payload() -> None:
    output = TranslationLayerGenerationOutput.model_validate(
        {
            "groups": [
                {
                    "anchor_segment_ids": ["s1", "s2"],
                    "translated_text": "自然流畅的中文译文。",
                }
            ]
        }
    )

    assert len(output.groups) == 1
    assert isinstance(output.groups[0], TranslationGenerationGroup)
    assert output.groups[0].anchor_segment_ids == ["s1", "s2"]
    assert output.groups[0].translated_text == "自然流畅的中文译文。"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("group_id", "unit_1_g1"),
        ("source_text_hash", "1a2b3c4d"),
        ("source_text", "Hello world"),
        ("segment_sources", [{"anchor_segment_id": "s1"}]),
        ("source_language", "en"),
        ("target_language", "zh-CN"),
        ("profile", {"reading_goal": "daily_reading"}),
        ("confidence", "high"),
        ("reason", "semantic_grouping"),
        ("notes", ["note"]),
        ("diagnostics", []),
        ("plate_path", [0, 1]),
        ("slate_path", [0, 1]),
        ("dom_path", "p:nth-child(1)"),
    ],
)
def test_translation_layer_generation_output_rejects_disallowed_group_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerGenerationOutput.model_validate(
            {
                "groups": [
                    {
                        "anchor_segment_ids": ["s1"],
                        "translated_text": "测试",
                        field: value,
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("target_language", "zh-CN"),
        ("profile", {"reading_goal": "daily_reading"}),
        ("coverage_json", {"coverage_status": "complete"}),
        ("quality_json", {"group_count": 1}),
    ],
)
def test_translation_layer_generation_output_rejects_top_level_extras(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerGenerationOutput.model_validate(
            {
                "groups": [
                    {
                        "anchor_segment_ids": ["s1"],
                        "translated_text": "测试",
                    }
                ],
                field: value,
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"groups": []},
        {"groups": [{"anchor_segment_ids": [], "translated_text": "测试"}]},
        {"groups": [{"anchor_segment_ids": ["s1"], "translated_text": ""}]},
    ],
)
def test_translation_layer_generation_output_rejects_invalid_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerGenerationOutput.model_validate(payload)


def test_translation_layer_output_accepts_group_native_payload() -> None:
    output = TranslationLayerOutput.model_validate(
        {
            "groups": [
                {
                    "group_id": "unit_1_g1",
                    "anchor_segment_ids": ["s1", "s2"],
                    "source_text_hash": "1a2b3c4d",
                    "translated_text": "自然流畅的中文译文。",
                }
            ]
        }
    )

    assert len(output.groups) == 1
    assert isinstance(output.groups[0], TranslationGroup)
    assert output.groups[0].group_id == "unit_1_g1"
    assert output.groups[0].anchor_segment_ids == ["s1", "s2"]
    assert output.groups[0].source_text_hash == "1a2b3c4d"
    assert output.groups[0].translated_text == "自然流畅的中文译文。"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_text", "Hello world"),
        ("segment_sources", [{"anchor_segment_id": "s1"}]),
        ("source_language", "en"),
        ("target_language", "zh-CN"),
        ("profile", {"reading_goal": "daily_reading"}),
        ("confidence", "high"),
        ("reason", "semantic_grouping"),
        ("notes", ["note"]),
        ("diagnostics", []),
        ("coverage_json", {"coverage_status": "complete"}),
        ("quality_json", {"group_count": 1}),
        ("plate_path", [0, 1]),
        ("slate_path", [0, 1]),
        ("dom_path", "p:nth-child(1)"),
    ],
)
def test_translation_layer_output_rejects_disallowed_group_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerOutput.model_validate(
            {
                "groups": [
                    {
                        "group_id": "unit_1_g1",
                        "anchor_segment_ids": ["s1"],
                        "source_text_hash": "1a2b3c4d",
                        "translated_text": "测试",
                        field: value,
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("target_language", "zh-CN"),
        ("profile", {"reading_goal": "daily_reading"}),
        ("coverage_json", {"coverage_status": "complete"}),
        ("quality_json", {"group_count": 1}),
    ],
)
def test_translation_layer_output_rejects_top_level_extras(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerOutput.model_validate(
            {
                "groups": [
                    {
                        "group_id": "unit_1_g1",
                        "anchor_segment_ids": ["s1"],
                        "source_text_hash": "1a2b3c4d",
                        "translated_text": "测试",
                    }
                ],
                field: value,
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"groups": []},
        {
            "groups": [
                {
                    "group_id": "",
                    "anchor_segment_ids": ["s1"],
                    "source_text_hash": "1a2b3c4d",
                    "translated_text": "测试",
                }
            ]
        },
        {
            "groups": [
                {
                    "group_id": "unit_1_g1",
                    "anchor_segment_ids": [],
                    "source_text_hash": "1a2b3c4d",
                    "translated_text": "测试",
                }
            ]
        },
        {
            "groups": [
                {
                    "group_id": "unit_1_g1",
                    "anchor_segment_ids": ["s1"],
                    "source_text_hash": "bad-hash",
                    "translated_text": "测试",
                }
            ]
        },
        {
            "groups": [
                {
                    "group_id": "unit_1_g1",
                    "anchor_segment_ids": ["s1"],
                    "source_text_hash": "1a2b3c4d",
                    "translated_text": "",
                }
            ]
        },
    ],
)
def test_translation_layer_output_rejects_invalid_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TranslationLayerOutput.model_validate(payload)


def test_vocabulary_layer_output_accepts_empty_items() -> None:
    output = VocabularyLayerOutput()

    assert output.schema_version == 1
    assert output.items == []


def _phrase_gloss_anchor() -> ReaderTextRangeAnchor:
    selected = "give up"
    return ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id="u1",
        anchor_segment_id="s1",
        sentence_id="s1",
        start_offset=0,
        end_offset=7,
        selected_text=selected,
        text_hash=compute_text_range_hash(selected),
    )


@pytest.mark.parametrize(
    "phrase_type",
    ["verb_expression", "fixed_collocation", "name_or_term", "idiom"],
)
def test_vocabulary_phrase_gloss_accepts_new_phrase_types(
    phrase_type: VocabularyPhraseType,
) -> None:
    item = VocabularyPhraseGlossItem(
        anchor=_phrase_gloss_anchor(),
        phrase="give up",
        phrase_type=phrase_type,
        gloss="放弃",
        learning_note=None,
    )
    assert item.phrase_type == phrase_type
    dumped = item.model_dump()
    assert dumped["learning_note"] is None
    round_trip = VocabularyPhraseGlossItem.model_validate(dumped)
    assert round_trip.phrase_type == phrase_type


def test_vocabulary_phrase_gloss_learning_note_markdown_round_trip() -> None:
    note = "用法：`give up` + 名词。\n- 常见于戒除义"
    item = VocabularyPhraseGlossItem(
        anchor=_phrase_gloss_anchor(),
        phrase="give up",
        phrase_type="verb_expression",
        gloss="放弃；戒掉",
        learning_note=note,
        example="She gave up smoking.",
    )
    dumped = item.model_dump()
    assert dumped["learning_note"] == note
    restored = VocabularyPhraseGlossItem.model_validate(dumped)
    assert restored.learning_note == note
    assert restored.example == "She gave up smoking."

    layer = VocabularyLayerOutput(items=[item])
    layer_restored = VocabularyLayerOutput.model_validate(layer.model_dump())
    assert layer_restored.items[0].learning_note == note  # type: ignore[union-attr]

    # Published + candidate field descriptions carry the Markdown contract.
    published_desc = VocabularyPhraseGlossItem.model_fields["learning_note"].description or ""
    assert "Markdown" in published_desc
    assert "raw HTML" in published_desc
    assert "headings" in published_desc

    from app.services.reader_orchestration.vocabulary_worker import (
        VocabularyPhraseGlossCandidateItem,
    )

    candidate_desc = (
        VocabularyPhraseGlossCandidateItem.model_fields["learning_note"].description or ""
    )
    assert "Markdown" in candidate_desc
    assert "raw HTML" in candidate_desc


@pytest.mark.parametrize(
    "old_type",
    ["collocation", "phrasal_verb", "proper_noun", "compound", "other"],
)
def test_vocabulary_phrase_gloss_rejects_old_phrase_types(old_type: str) -> None:
    with pytest.raises(ValidationError):
        VocabularyPhraseGlossItem.model_validate(
            {
                "item_type": "phrase_gloss",
                "anchor": _phrase_gloss_anchor().model_dump(),
                "phrase": "give up",
                "phrase_type": old_type,
                "gloss": "放弃",
            }
        )


def test_grammar_note_item_requires_spans_from_same_unit() -> None:
    span_one = ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id="u1",
        anchor_segment_id="s1",
        sentence_id="s1",
        start_offset=0,
        end_offset=4,
        selected_text="Only",
        text_hash=compute_text_range_hash("Only"),
    )
    span_two = ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id="u1",
        anchor_segment_id="s2",
        sentence_id="s2",
        start_offset=5,
        end_offset=9,
        selected_text="once",
        text_hash=compute_text_range_hash("once"),
    )
    item = GrammarNoteItem(
        spans=[span_one, span_two],
        grammar_point="paired focus",
        pattern="only once",
        note="同一 unit 内允许多个 grounded spans。",
    )

    assert len(item.spans) == 2

    with pytest.raises(ValidationError, match="same unit"):
        GrammarNoteItem(
            spans=[
                span_one,
                ReaderTextRangeAnchor(
                    base_id="base-1",
                    unit_id="u2",
                    anchor_segment_id="s3",
                    sentence_id="s3",
                    start_offset=0,
                    end_offset=4,
                    selected_text="else",
                    text_hash=compute_text_range_hash("else"),
                ),
            ],
            grammar_point="bad mix",
            note="跨 unit 不允许。",
        )

    with pytest.raises(ValidationError, match="same base"):
        GrammarNoteItem(
            spans=[
                span_one,
                ReaderTextRangeAnchor(
                    base_id="base-2",
                    unit_id="u1",
                    anchor_segment_id="s4",
                    sentence_id="s4",
                    start_offset=0,
                    end_offset=4,
                    selected_text="base",
                    text_hash=compute_text_range_hash("base"),
                ),
            ],
            grammar_point="bad base",
            note="跨 base 不允许。",
        )


def test_grammar_bundle_output_accepts_empty_lists() -> None:
    output = GrammarBundleOutput()

    assert output.schema_version == 1
    assert output.grammar_notes == []
    assert output.sentence_analyses == []


def test_persisted_grammar_layer_outputs_validate() -> None:
    anchor = ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id="u1",
        anchor_segment_id="s1",
        sentence_id="s1",
        start_offset=0,
        end_offset=4,
        selected_text="Only",
        text_hash=compute_text_range_hash("Only"),
    )

    grammar_output = GrammarNoteLayerOutput(
        items=[
            GrammarNoteItem(
                spans=[anchor],
                grammar_point="fronted focus",
                pattern="only",
                note="强调语气。",
            )
        ]
    )
    sentence_output = SentenceAnalysisLayerOutput(
        items=[
            SentenceAnalysisItem(
                anchor=anchor,
                label="focus cue",
                analysis="前置副词起强调作用。",
                chunks=[SentenceAnalysisChunk(order=1, label="cue", text="Only")],
            )
        ]
    )

    assert grammar_output.schema_version == 1
    assert sentence_output.schema_version == 1


def test_reader_plate_snapshot_rejects_projection_version() -> None:
    with pytest.raises(ValidationError):
        ReaderPlateSnapshot.model_validate(
            {
                "snapshot_id": "snap-1",
                "snapshot_taken_at": datetime.now(UTC),
                "last_event_sequence": 1,
                "record_id": "record-1",
                "record": {
                    "title": "Snapshot Example",
                    "created_at": datetime.now(UTC),
                    "source_type": "text",
                    "source_metadata": {},
                    "product_state": "readable_enhancing",
                },
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
