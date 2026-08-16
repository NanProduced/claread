"""Regression tests: vocabulary_highlight single-lexical-item contract.

Covers:
- ACCEPT matrix: deadline, missed, well-known, don't (curly), students',
  U.K. (hyphens / apostrophes / possessives / internal-period
  abbreviations are single lexical items);
- REJECT matrix with the exact diagnostic reason_code
  ``vocab_highlight_not_single_lexical_item``: missed a deadline,
  was due to, police protection, take place, padded multiword input;
- publish chain: mixed batches skip ONLY the illegal highlights, keep
  legal highlights / phrase_gloss / context_gloss, never auto-convert
  a highlight into phrase_gloss, and no illegal multiword highlight
  reaches the snapshot marks;
- per-unit and batch paths share the same guard and behave identically;
- context_gloss / phrase_gloss guards do not regress.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.contracts.annotation import (
    compute_text_range_hash,
    utf16_code_unit_length,
)
from app.schemas.reader_orchestration import ReaderSnapshotLayer
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.vocabulary_worker import (
    _VOCAB_HIGHLIGHT_NOT_SINGLE_LEXICAL_ITEM,
    VocabularyAnchorSegmentContext,
    VocabularyBatchCandidateOutput,
    VocabularyBatchJobContext,
    VocabularyBatchUnitCandidateOutput,
    VocabularyBatchUnitContext,
    VocabularyCandidateOutput,
    VocabularyContextGlossCandidateItem,
    VocabularyHighlightCandidateItem,
    VocabularyHighlightItem,
    VocabularyJobContext,
    VocabularyPhraseGlossCandidateItem,
    _build_vocabulary_batch_outputs,
    _build_vocabulary_output_from_candidates,
    _vocab_highlight_guard_reason_code,
)
from tests.reader_orchestration_test_support import fixture_analysis_progress

MIXED_SEGMENT_TEXT = (
    "The deadline for police protection was due to missed signals."
)

RECORD_ID = UUID("33333333-3333-3333-3333-333333333333")
USER_ID = UUID("44444444-4444-4444-4444-444444444444")
BASE_ID = UUID("55555555-5555-5555-5555-555555555555")


def _unit_context(text: str, *, unit_id: str = "u1") -> VocabularyJobContext:
    return VocabularyJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=RECORD_ID,
        user_id=USER_ID,
        base_id=BASE_ID,
        unit_id=unit_id,
        order_index=1,
        expected_generation=1,
        operation_fingerprint="r7-2-test",
        source_language="en",
        source_text=text,
        text_hash=compute_text_range_hash(text),
        anchor_segments=(
            VocabularyAnchorSegmentContext(
                anchor_segment_id="s1",
                sentence_id="s1",
                segment_type="sentence",
                unit_start_utf16=0,
                unit_end_utf16=utf16_code_unit_length(text),
                text_hash=compute_text_range_hash(text),
                text=text,
            ),
        ),
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        strategy_version="test-strategy",
        strategy_hash="test-strategy-hash",
        layer_policy_hash="test-layer-policy",
        vocabulary_prompt_lines=(),
    )


def _hl(selected_text: str, headword: str = "word") -> VocabularyHighlightCandidateItem:
    return VocabularyHighlightCandidateItem(
        anchor_segment_id="s1",
        selected_text=selected_text,
        headword=headword,
    )


def _phrase(
    selected_text: str,
    phrase: str,
    phrase_type: str = "fixed_collocation",
) -> VocabularyPhraseGlossCandidateItem:
    return VocabularyPhraseGlossCandidateItem(
        anchor_segment_id="s1",
        selected_text=selected_text,
        phrase=phrase,
        phrase_type=phrase_type,  # type: ignore[arg-type]
        gloss="整体释义",
    )


def _context_gloss(selected_text: str) -> VocabularyContextGlossCandidateItem:
    return VocabularyContextGlossCandidateItem(
        anchor_segment_id="s1",
        selected_text=selected_text,
        display=selected_text,
        gloss="语境释义",
        reason="此处含义依赖上下文",
    )


def _build(items: list, *, text: str = MIXED_SEGMENT_TEXT):
    return _build_vocabulary_output_from_candidates(
        _unit_context(text),
        VocabularyCandidateOutput(items=list(items)),
    )


def _skip_reasons(diagnostics: dict) -> list[str]:
    return [item["reason_code"] for item in diagnostics.get("skipped_items", [])]


# ---------------------------------------------------------------------------
# Guard unit matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selected_text",
    [
        pytest.param("deadline", id="plain-word"),
        pytest.param("missed", id="inflected-form"),
        pytest.param("well-known", id="hyphenated"),
        pytest.param("don't", id="ascii-apostrophe"),
        pytest.param("don’t", id="curly-apostrophe"),
        pytest.param("students'", id="possessive"),
        pytest.param("students’", id="curly-possessive"),
        pytest.param("U.K.", id="orthographic-abbreviation"),
    ],
)
def test_guard_accepts_single_lexical_items(selected_text: str) -> None:
    assert _vocab_highlight_guard_reason_code(_hl(selected_text)) is None


@pytest.mark.parametrize(
    "selected_text",
    [
        pytest.param("missed a deadline", id="verb-object-span"),
        pytest.param("was due to", id="multiword-predicate"),
        pytest.param("police protection", id="noun-noun-span"),
        pytest.param("take place", id="verb-expression-span"),
        pytest.param("  missed a deadline  ", id="padded-multiword"),
        pytest.param("the U.K. government", id="abbreviation-inside-multiword"),
    ],
)
def test_guard_rejects_multiword_selected_text(selected_text: str) -> None:
    assert (
        _vocab_highlight_guard_reason_code(_hl(selected_text))
        == _VOCAB_HIGHLIGHT_NOT_SINGLE_LEXICAL_ITEM
    )


# ---------------------------------------------------------------------------
# Publish chain: accept / reject through the full candidate pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selected_text", "headword"),
    [
        ("deadline", "deadline"),
        ("missed", "miss"),
        ("well-known", "well-known"),
        ("U.K.", "U.K."),
    ],
)
def test_legal_highlights_are_published(selected_text: str, headword: str) -> None:
    output, diagnostics = _build(
        [_hl(selected_text, headword)],
        text=f"The term {selected_text} appeared once here.",
    )

    assert len(output.items) == 1
    item = output.items[0]
    assert isinstance(item, VocabularyHighlightItem)
    assert item.headword == headword
    assert item.anchor.selected_text == selected_text
    assert _skip_reasons(diagnostics) == []


@pytest.mark.parametrize(
    "selected_text",
    ["missed a deadline", "was due to", "police protection", "take place"],
)
def test_illegal_highlights_are_skipped_with_exact_diagnostic(
    selected_text: str,
) -> None:
    output, diagnostics = _build([_hl(selected_text, "word")])

    # Not published, not converted, batch/unit output still succeeds.
    assert output.items == []
    skipped = diagnostics["skipped_items"]
    assert len(skipped) == 1
    assert skipped[0]["reason_code"] == "vocab_highlight_not_single_lexical_item"
    assert skipped[0]["item_type"] == "vocab_highlight"
    assert skipped[0]["anchor_segment_id"] == "s1"


def test_padded_multiword_highlight_is_trimmed_and_rejected_before_grounding() -> None:
    output, diagnostics = _build([_hl("  police protection  ", "protection")])
    assert output.items == []
    assert _skip_reasons(diagnostics) == ["vocab_highlight_not_single_lexical_item"]


def test_mixed_batch_skips_only_illegal_highlights_and_never_auto_converts() -> None:
    output, diagnostics = _build(
        [
            _hl("police protection", "protection"),  # illegal highlight
            _hl("deadline", "deadline"),  # legal highlight
            _phrase("due to", "due to"),  # LLM-emitted phrase_gloss
            _context_gloss("missed"),  # legal context_gloss
        ]
    )

    published_by_type = {item.item_type for item in output.items}
    assert published_by_type == {"vocab_highlight", "phrase_gloss", "context_gloss"}

    highlights = [i for i in output.items if i.item_type == "vocab_highlight"]
    assert [h.anchor.selected_text for h in highlights] == ["deadline"]
    assert [h.headword for h in highlights] == ["deadline"]

    # The only published phrase_gloss is the LLM-emitted one; the illegal
    # highlight was NOT converted into a phrase_gloss.
    phrases = [i for i in output.items if i.item_type == "phrase_gloss"]
    assert [p.anchor.selected_text for p in phrases] == ["due to"]
    assert [p.phrase for p in phrases] == ["due to"]

    context_glosses = [i for i in output.items if i.item_type == "context_gloss"]
    assert [c.anchor.selected_text for c in context_glosses] == ["missed"]

    # Exactly one skip: the illegal highlight, with the exact reason.
    assert _skip_reasons(diagnostics) == ["vocab_highlight_not_single_lexical_item"]


# ---------------------------------------------------------------------------
# per-unit / batch shared-guard equivalence
# ---------------------------------------------------------------------------


def _batch_context(*units: VocabularyJobContext) -> VocabularyBatchJobContext:
    batch_units = tuple(
        VocabularyBatchUnitContext(
            unit_id=unit.unit_id,
            order_index=unit.order_index,
            source_text=unit.source_text,
            text_hash=unit.text_hash,
            anchor_segments=unit.anchor_segments,
        )
        for unit in units
    )
    return VocabularyBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=RECORD_ID,
        user_id=USER_ID,
        base_id=BASE_ID,
        expected_generation=1,
        operation_fingerprint="r7-2-test",
        source_language="en",
        target_unit_ids=tuple(unit.unit_id for unit in batch_units),
        units=batch_units,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        strategy_version="test-strategy",
        strategy_hash="test-strategy-hash",
        layer_policy_hash="test-layer-policy",
        vocabulary_prompt_lines=(),
    )


def test_batch_path_applies_the_same_guard_as_per_unit() -> None:
    context = _unit_context(MIXED_SEGMENT_TEXT)
    candidates = [
        _hl("police protection", "protection"),  # illegal
        _hl("deadline", "deadline"),  # legal
    ]

    per_unit_output, per_unit_diagnostics = _build(list(candidates))

    batch_outputs, batch_diagnostics = _build_vocabulary_batch_outputs(
        context=_batch_context(context),
        candidate_output=VocabularyBatchCandidateOutput(
            units=[
                VocabularyBatchUnitCandidateOutput(
                    unit_id="u1", items=list(candidates)
                )
            ]
        ),
    )

    assert len(batch_outputs) == 1
    unit_id, batch_unit_output = batch_outputs[0]
    assert unit_id == "u1"

    # Same published items as the per-unit path.
    assert [item.item_type for item in batch_unit_output.items] == [
        item.item_type for item in per_unit_output.items
    ]
    assert [
        item.anchor.selected_text for item in batch_unit_output.items
    ] == ["deadline"]

    # Same skip diagnostic, enriched with unit_id on the batch path.
    assert _skip_reasons(per_unit_diagnostics) == [
        "vocab_highlight_not_single_lexical_item"
    ]
    assert [d["reason_code"] for d in batch_diagnostics] == [
        "vocab_highlight_not_single_lexical_item"
    ]
    assert [d["unit_id"] for d in batch_diagnostics] == ["u1"]
    assert [d["item_type"] for d in batch_diagnostics] == ["vocab_highlight"]


def test_batch_illegal_highlight_in_one_unit_does_not_block_other_units() -> None:
    unit_a = _unit_context("The deadline arrived early.", unit_id="u1")
    unit_b = _unit_context("The police protection failed.", unit_id="u2")

    batch_outputs, batch_diagnostics = _build_vocabulary_batch_outputs(
        context=_batch_context(unit_a, unit_b),
        candidate_output=VocabularyBatchCandidateOutput(
            units=[
                VocabularyBatchUnitCandidateOutput(
                    unit_id="u1", items=[_hl("deadline", "deadline")]
                ),
                VocabularyBatchUnitCandidateOutput(
                    unit_id="u2", items=[_hl("police protection", "protection")]
                ),
            ]
        ),
    )

    outputs_by_unit = dict(batch_outputs)
    assert [i.anchor.selected_text for i in outputs_by_unit["u1"].items] == [
        "deadline"
    ]
    assert outputs_by_unit["u2"].items == []
    assert [d["unit_id"] for d in batch_diagnostics] == ["u2"]
    assert [d["reason_code"] for d in batch_diagnostics] == [
        "vocab_highlight_not_single_lexical_item"
    ]


# ---------------------------------------------------------------------------
# Snapshot projection: no illegal multiword highlight reaches the marks
# ---------------------------------------------------------------------------


def test_snapshot_contains_no_illegal_multiword_vocabulary_highlight() -> None:
    result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=str(RECORD_ID),
            base_id=str(BASE_ID),
            source_text=MIXED_SEGMENT_TEXT,
            title="R7-2",
            language="en",
        )
    )
    output, diagnostics = _build(
        [
            _hl("police protection", "protection"),  # illegal
            _hl("deadline", "deadline"),
            _phrase("due to", "due to"),
        ],
        text=result.units[0].text,
    )
    assert _skip_reasons(diagnostics) == ["vocab_highlight_not_single_lexical_item"]

    layer = ReaderSnapshotLayer(
        layer_id="vocab-layer-r72",
        layer_type="vocabulary",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=result.units[0].unit_id,
        schema_version=1,
        output={
            "schema_version": 1,
            "items": [item.model_dump(mode="json") for item in output.items],
        },
        published_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )
    snapshot = build_reader_plate_snapshot(result,
        analysis_progress=fixture_analysis_progress(),
snapshot_taken_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        last_event_sequence=9,
        enhancement_layers=[layer],
    )

    marks = [
        mark
        for unit_node in snapshot.value
        for source_block in unit_node["children"]  # type: ignore[index]
        if isinstance(source_block, dict)
        for anchor_node in source_block.get("children", [])  # type: ignore[union-attr]
        if isinstance(anchor_node, dict)
        for leaf in anchor_node.get("children", [])  # type: ignore[union-attr]
        if isinstance(leaf, dict)
        for mark in leaf.get("reader_vocabulary_marks", [])
        if isinstance(mark, dict)
    ]
    marked_selected = {mark["selected_text"] for mark in marks}
    assert marked_selected == {"deadline", "due to"}
    assert "police protection" not in marked_selected

    highlight_marks = [m for m in marks if m["item_type"] == "vocab_highlight"]
    assert [m["selected_text"] for m in highlight_marks] == ["deadline"]
    for mark in highlight_marks:
        assert not any(
            char.isspace() for char in str(mark["selected_text"])
        ), mark


# ---------------------------------------------------------------------------
# Existing guards do not regress
# ---------------------------------------------------------------------------


def test_context_gloss_multiword_guard_still_skips() -> None:
    output, diagnostics = _build([_context_gloss("due to")])
    assert output.items == []
    assert _skip_reasons(diagnostics) == ["context_gloss_not_single_lexical_item"]


def test_context_gloss_single_word_still_publishes() -> None:
    output, diagnostics = _build([_context_gloss("missed")])
    assert [item.item_type for item in output.items] == ["context_gloss"]
    assert _skip_reasons(diagnostics) == []


def test_phrase_gloss_sentence_like_guard_still_skips() -> None:
    output, diagnostics = _build(
        [_phrase("Are we to have nothing tonight?", "nothing tonight")]
    )
    assert output.items == []
    assert _skip_reasons(diagnostics) == ["phrase_gloss_sentence_like"]
