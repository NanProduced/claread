# task-history: A5 (renamed from test_a5_stable_block_unit_classification.py)
"""unit 分类与 snapshot 结构透传（TDD）。

Tests that ``build_reading_base_from_canonical_text`` accepts an
optional ``stable_block_annotations`` parameter and, when a unit's
UTF-16 range exactly matches an annotation, projects the stable
``block_type`` into the unit's ``stable_block_type`` field and the
snapshot ``reader_source_block`` payload. The matched annotation's
heading level / inline marks / table role / parent block id are
stored on the built unit and projected into the snapshot payload
so the Web reading surface can render Markdown block structure
(B2/B3/B4 dependency).

``unit_type`` is only overridden when ``block_type == "heading"``
— the legacy DB CHECK constraint on ``reading_units.unit_type``
(migration 0001) allows only ``body`` / ``heading`` / ``list`` /
``quote`` / ``unknown`` / ``fallback``, so new stable block types
(``paragraph`` / ``list_item`` / ``blockquote`` / ``table*`` /
``code_block``) MUST NOT be written to ``unit_type``. The
``heading`` exception exists because downstream consumers (A6
semantic-outline skip decision, feature extractor, B4 outline
projector) key off ``unit_type == "heading"``.

Legacy path (no annotations) MUST keep the heuristic classification
and MUST NOT emit the new payload fields, so existing snapshots stay
byte-for-byte stable.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.contracts.annotation import utf16_code_unit_length
from app.services.reader_orchestration import (
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.base_builder import (
    StableBlockAnnotation,
    build_reading_base_from_canonical_text,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

_RECORD_ID = "rec-a5-0001"
_BASE_ID = "00000000-0000-0000-0000-0000000000a5"


def _join_blocks(*blocks: str) -> tuple[str, list[tuple[int, int]]]:
    """Join block texts with ``\\n\\n`` and return (text, [(start, end)]).

    Offsets are UTF-16 code units. Assumes ASCII text for simplicity
    (UTF-16 == char count).
    """
    text_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for block in blocks:
        if text_parts:
            sep = "\n\n"
            cursor += utf16_code_unit_length(sep)
            text_parts.append(sep)
        start = cursor
        text_parts.append(block)
        cursor += utf16_code_unit_length(block)
        spans.append((start, cursor))
    return "".join(text_parts), spans


def _annotation(
    span: tuple[int, int],
    block_type: str,
    *,
    block_id: str,
    parent_block_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> StableBlockAnnotation:
    return StableBlockAnnotation(
        start_utf16=span[0],
        end_utf16=span[1],
        block_type=block_type,
        block_id=block_id,
        parent_block_id=parent_block_id,
        payload_json=payload or {},
    )


# ---------------------------------------------------------------------------
# A5-1: unit_type derived from stable block_type when annotation matches
# ---------------------------------------------------------------------------


def test_build_with_stable_block_annotations_sets_unit_type_from_block_type() -> None:
    canonical_text, spans = _join_blocks(
        "Heading One",
        "This is a paragraph with enough words to count.",
        "- list item one\n- list item two",
        "> quoted line",
        "code_line_one()\ncode_line_two()",
    )
    annotations = [
        _annotation(spans[0], "heading", block_id="b1"),
        _annotation(spans[1], "paragraph", block_id="b2"),
        _annotation(spans[2], "list_item", block_id="b3"),
        _annotation(spans[3], "blockquote", block_id="b4"),
        _annotation(spans[4], "code_block", block_id="b5"),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )

    # ``unit_type`` mirrors the DB CHECK constraint on
    # ``reading_units`` (migration 0001): only the 6 legacy heuristic
    # values are accepted. Only ``heading`` overrides the heuristic
    # (because downstream A6 skip / B4 outline key off
    # ``unit_type == "heading"``). All other stable block types keep
    # the heuristic ``unit_type``; the authoritative block type lives
    # in ``stable_block_type``.
    assert [unit.unit_type for unit in result.units] == [
        "heading",  # heading annotation overrides heuristic
        "body",     # paragraph annotation — heuristic kept
        "list",     # list_item annotation — heuristic kept
        "quote",    # blockquote annotation — heuristic kept
        "body",     # code_block annotation — heuristic kept
    ]
    # ``stable_block_type`` carries the authoritative stable block type.
    assert [unit.stable_block_type for unit in result.units] == [
        "heading",
        "paragraph",
        "list_item",
        "blockquote",
        "code_block",
    ]


def test_build_without_annotations_keeps_legacy_heuristic_unit_types() -> None:
    """Regression: no annotations -> legacy _classify_unit_type runs."""
    canonical_text, _ = _join_blocks(
        "Heading One",
        "This is a paragraph with enough words to count.",
        "- list item one\n- list item two",
        "> quoted line",
    )

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
    )

    # Legacy heuristic: short single line w/o punctuation -> heading;
    # all-list-lines -> list; all-quote-lines -> quote; else body.
    assert [unit.unit_type for unit in result.units] == [
        "heading",
        "body",
        "list",
        "quote",
    ]
    # Legacy units MUST NOT carry stable block fields.
    for unit in result.units:
        assert unit.stable_block_type is None
        assert unit.heading_level is None
        assert unit.inline_marks == ()
        assert unit.table_role is None
        assert unit.parent_stable_block_id is None


# ---------------------------------------------------------------------------
# A5-2: heading level + inline marks extracted from payload_json
# ---------------------------------------------------------------------------


def test_build_with_annotations_extracts_heading_level_from_payload() -> None:
    canonical_text, spans = _join_blocks("Section Title")
    annotations = [
        _annotation(
            spans[0],
            "heading",
            block_id="b1",
            payload={"level": 3},
        ),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )

    assert result.units[0].stable_block_type == "heading"
    assert result.units[0].heading_level == 3


def test_build_with_annotations_extracts_inline_marks_from_payload() -> None:
    canonical_text, spans = _join_blocks("bold and italic text here")
    inline_marks = [
        {"type": "strong", "start": 0, "end": 4},
        {"type": "em", "start": 10, "end": 16},
    ]
    annotations = [
        _annotation(
            spans[0],
            "paragraph",
            block_id="b1",
            payload={"inline_marks": inline_marks},
        ),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )

    assert result.units[0].stable_block_type == "paragraph"
    assert list(result.units[0].inline_marks) == inline_marks


# ---------------------------------------------------------------------------
# A5-3: table role + parent block id
# ---------------------------------------------------------------------------


def test_build_with_annotations_extracts_table_role_and_parent_for_table_cell() -> None:
    canonical_text, spans = _join_blocks("cell content one", "cell content two")
    annotations = [
        _annotation(
            spans[0],
            "table_cell",
            block_id="cell-1",
            parent_block_id="row-1",
            payload={"column_index": 0, "alignment": None, "is_header": True},
        ),
        _annotation(
            spans[1],
            "table_cell",
            block_id="cell-2",
            parent_block_id="row-1",
            payload={"column_index": 1, "alignment": None, "is_header": True},
        ),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )

    assert [unit.stable_block_type for unit in result.units] == [
        "table_cell",
        "table_cell",
    ]
    assert [unit.table_role for unit in result.units] == ["cell", "cell"]
    assert [unit.parent_stable_block_id for unit in result.units] == [
        "row-1",
        "row-1",
    ]
    assert [unit.stable_block_id for unit in result.units] == [
        "cell-1",
        "cell-2",
    ]


def test_build_with_annotations_assigns_table_role_for_table_and_row_block_types() -> None:
    """Even though table/table_row wrappers carry no text_content in the
    freeze plan, the annotation path must still map their block_type to
    a table_role if a unit ever matches (defensive completeness)."""
    canonical_text, spans = _join_blocks("wrapper text")
    annotations = [
        _annotation(spans[0], "table", block_id="tbl-1"),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )

    assert result.units[0].stable_block_type == "table"
    assert result.units[0].table_role == "table"


# ---------------------------------------------------------------------------
# A5-4: non-matching annotation falls back to heuristic (fail-safe)
# ---------------------------------------------------------------------------


def test_build_with_non_matching_annotation_falls_back_to_heuristic() -> None:
    canonical_text, spans = _join_blocks("Heading One")
    # Annotation whose offsets do NOT match the unit's UTF-16 range.
    mismatched = StableBlockAnnotation(
        start_utf16=spans[0][1] + 100,
        end_utf16=spans[0][1] + 200,
        block_type="code_block",
        block_id="b-missing",
    )

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=[mismatched],
    )

    # No annotation matched -> heuristic ran -> heading (short, no punct).
    assert result.units[0].unit_type == "heading"
    assert result.units[0].stable_block_type is None


# ---------------------------------------------------------------------------
# A5-5: snapshot payload projection
# ---------------------------------------------------------------------------


def test_snapshot_source_block_includes_stable_block_fields_when_annotated() -> None:
    canonical_text, spans = _join_blocks(
        "Document Title",
        "paragraph with **bold** text",
    )
    inline_marks = [{"type": "strong", "start": 16, "end": 22}]
    annotations = [
        _annotation(
            spans[0],
            "heading",
            block_id="b1",
            payload={"level": 1},
        ),
        _annotation(
            spans[1],
            "paragraph",
            block_id="b2",
            payload={"inline_marks": inline_marks},
        ),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        last_event_sequence=3,
    )

    assert len(snapshot.value) == 2
    heading_block = snapshot.value[0]["children"][0]
    para_block = snapshot.value[1]["children"][0]

    assert heading_block["stableBlockType"] == "heading"
    assert heading_block["headingLevel"] == 1
    assert heading_block["tableRole"] is None
    assert heading_block["parentStableBlockId"] is None

    assert para_block["stableBlockType"] == "paragraph"
    assert para_block["headingLevel"] is None
    assert para_block["inlineMarks"] == inline_marks
    assert para_block["tableRole"] is None


def test_snapshot_source_block_omits_stable_block_fields_when_not_annotated() -> None:
    """Regression: legacy snapshots (no annotations) MUST NOT carry
    the new payload fields, so existing snapshot bytes stay stable."""
    canonical_text, _ = _join_blocks("Just one paragraph of text here.")

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
    )
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        last_event_sequence=1,
    )

    source_block = snapshot.value[0]["children"][0]
    assert "stableBlockType" not in source_block
    assert "headingLevel" not in source_block
    assert "inlineMarks" not in source_block
    assert "tableRole" not in source_block
    assert "parentStableBlockId" not in source_block


def test_snapshot_navigation_unit_carries_stable_block_type_for_outline() -> None:
    """B4 dependency: navigation units must expose stable_block_type +
    heading_level so the markdown outline view can derive depth/target
    without re-parsing the canonical text."""
    canonical_text, spans = _join_blocks(
        "Top Section",
        "Content under top section.",
        "Sub Section",
    )
    annotations = [
        _annotation(spans[0], "heading", block_id="b1", payload={"level": 1}),
        _annotation(spans[1], "paragraph", block_id="b2"),
        _annotation(spans[2], "heading", block_id="b3", payload={"level": 2}),
    ]

    result = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=canonical_text,
        stable_block_annotations=annotations,
    )
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        last_event_sequence=5,
    )

    nav = snapshot.navigation.units
    # ``unit_type`` carries only legacy values (DB CHECK constraint).
    # The paragraph annotation does NOT override unit_type — heuristic
    # ``body`` is kept; the authoritative block type lives in
    # ``stable_block_type``.
    assert [n.unit_type for n in nav] == ["heading", "body", "heading"]
    # Navigation units must expose stable block type + heading level
    # for the outline projector (B4). These are optional fields that
    # default to None on legacy units.
    assert nav[0].stable_block_type == "heading"
    assert nav[0].heading_level == 1
    assert nav[1].stable_block_type == "paragraph"
    assert nav[1].heading_level is None
    assert nav[2].stable_block_type == "heading"
    assert nav[2].heading_level == 2
