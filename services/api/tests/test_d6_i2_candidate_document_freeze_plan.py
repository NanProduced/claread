"""Focused tests for D6-I2A Candidate Document -> Stable Document Freeze Plan.

These tests pin the canonical text derivation rules, UTF-16 offset
accounting (including emoji / surrogate pairs), fail-closed behavior,
hash sensitivity and input non-mutation. They do NOT touch the DB, the
API route, or the orchestrator.
"""

from __future__ import annotations

import pytest

from app.contracts.annotation import (
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.schemas.reader_documents import (
    StableDocumentBlock,
    StableDocumentInterpretationPolicy,
)
from app.services.reader_orchestration.document_freeze_plan import (
    CANONICAL_TEXT_BLOCK_SEPARATOR,
    StableDocumentFreezePlan,
    StableDocumentFreezePlanError,
    build_stable_document_freeze_plan,
)


# --------------------------------------------------------------------
# Block factory helpers
# --------------------------------------------------------------------


def _paragraph(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="paragraph",
        text_content=text,
    )


def _heading(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="heading",
        text_content=text,
    )


def _list_item(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="list_item",
        text_content=text,
    )


def _blockquote(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="blockquote",
        text_content=text,
    )


def _caption(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="caption",
        text_content=text,
    )


def _table(block_id: str, order: int, *, parent: str | None = None) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table",
        text_content=None,
        payload_json={"rows": 2, "cols": 2},
        parent_block_id=parent,
    )


def _table_row(block_id: str, order: int, parent: str) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table_row",
        text_content=None,
        payload_json={"row_index": 0},
        parent_block_id=parent,
    )


def _table_cell(
    block_id: str, text: str, order: int, parent: str
) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table_cell",
        text_content=text,
        parent_block_id=parent,
    )


def _image(
    block_id: str, order: int, *, source_url: str = "s3://bucket/key.png"
) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="image",
        text_content=None,
        payload_json={"source_url": source_url},
    )


def _image_ocr(
    block_id: str, text: str, order: int, parent: str
) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="image_ocr",
        text_content=text,
        payload_json={"engine": "ocr-v1"},
        parent_block_id=parent,
    )


def _footnote(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="footnote",
        text_content=text,
    )


def _code_block(
    block_id: str, text: str | None, order: int
) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="code_block",
        text_content=text,
        payload_json={"language": "python"} if text is None else {},
    )


def _main_reading_policy() -> StableDocumentInterpretationPolicy:
    return StableDocumentInterpretationPolicy(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    )


def _build(
    blocks,
    *,
    reading_record_id: str = "rec-1",
    record_generation: int = 1,
    document_version: int = 1,
    title: str | None = "Test title",
    source_profile_json: dict | None = None,
) -> StableDocumentFreezePlan:
    return build_stable_document_freeze_plan(
        reading_record_id=reading_record_id,
        record_generation=record_generation,
        document_version=document_version,
        title=title,
        blocks=blocks,
        source_profile_json=source_profile_json,
    )


# --------------------------------------------------------------------
# paragraph + heading -> canonical text + UTF-16 offsets
# --------------------------------------------------------------------


def test_paragraph_and_heading_generate_canonical_text_and_offsets() -> None:
    plan = _build(
        [
            _heading("h1", "Chapter One", 0),
            _paragraph("p1", "First paragraph.", 1),
            _paragraph("p2", "Second paragraph.", 2),
        ]
    )

    # Canonical text joins main_reading chunks with the pinned separator.
    assert plan.canonical_text == (
        "Chapter One" + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "First paragraph." + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "Second paragraph."
    )

    blocks_by_id = {b.block_id: b for b in plan.blocks}

    # heading h1: starts at 0, ends at UTF-16 length of "Chapter One".
    h1 = blocks_by_id["h1"]
    assert h1.canonical_text_start_utf16 == 0
    assert h1.canonical_text_end_utf16 == utf16_code_unit_length("Chapter One")
    assert h1.canonical_text_end_utf16 == len("Chapter One")  # ASCII sanity

    # paragraph p1: starts after h1 + separator.
    p1 = blocks_by_id["p1"]
    expected_p1_start = (
        utf16_code_unit_length("Chapter One")
        + utf16_code_unit_length(CANONICAL_TEXT_BLOCK_SEPARATOR)
    )
    assert p1.canonical_text_start_utf16 == expected_p1_start
    assert p1.canonical_text_end_utf16 == expected_p1_start + utf16_code_unit_length(
        "First paragraph."
    )

    # paragraph p2: starts after p1 + separator.
    p2 = blocks_by_id["p2"]
    expected_p2_start = (
        p1.canonical_text_end_utf16
        + utf16_code_unit_length(CANONICAL_TEXT_BLOCK_SEPARATOR)
    )
    assert p2.canonical_text_start_utf16 == expected_p2_start
    assert p2.canonical_text_end_utf16 == expected_p2_start + utf16_code_unit_length(
        "Second paragraph."
    )

    # Offsets slice back to the block text.
    for block_id, expected_text in [
        ("h1", "Chapter One"),
        ("p1", "First paragraph."),
        ("p2", "Second paragraph."),
    ]:
        block = blocks_by_id[block_id]
        sliced = slice_by_utf16_offsets(
            plan.canonical_text,
            block.canonical_text_start_utf16,
            block.canonical_text_end_utf16,
        )
        assert sliced == expected_text


def test_narrative_block_types_all_default_to_main_reading() -> None:
    """paragraph / list_item / blockquote / caption / heading all default
    to main_reading and therefore all contribute to canonical text.
    """
    plan = _build(
        [
            _heading("h1", "Title", 0),
            _paragraph("p1", "Paragraph body.", 1),
            _list_item("li1", "List item one.", 2),
            _blockquote("bq1", "Quote body.", 3),
            _caption("cap1", "Figure 1 caption.", 4),
        ]
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}
    # Every narrative block has non-None canonical offsets.
    for block_id in ("h1", "p1", "li1", "bq1", "cap1"):
        assert blocks_by_id[block_id].canonical_text_start_utf16 is not None
        assert blocks_by_id[block_id].canonical_text_end_utf16 is not None

    # The canonical text contains every chunk, joined by the separator.
    chunks = ["Title", "Paragraph body.", "List item one.", "Quote body.", "Figure 1 caption."]
    assert plan.canonical_text == CANONICAL_TEXT_BLOCK_SEPARATOR.join(chunks)


# --------------------------------------------------------------------
# table / table_row / table_cell default excluded
# --------------------------------------------------------------------


def test_table_hierarchy_default_excluded_from_canonical_text() -> None:
    """table / table_row / table_cell default to metadata_only /
    rag_ask_only respectively, so NONE of them enter canonical text by
    default. The table hierarchy (parent / child) is preserved in the
    output blocks.
    """
    plan = _build(
        [
            _heading("h1", "Title", 0),
            _table("tbl1", 1),
            _table_row("tbl1_r1", 2, parent="tbl1"),
            _table_cell("tbl1_r1_c1", "cell value", 3, parent="tbl1_r1"),
            _paragraph("p1", "Body after table.", 4),
        ]
    )

    blocks_by_id = {b.block_id: b for b in plan.blocks}

    # Table / table_row / table_cell must NOT carry canonical offsets.
    assert blocks_by_id["tbl1"].canonical_text_start_utf16 is None
    assert blocks_by_id["tbl1"].canonical_text_end_utf16 is None
    assert blocks_by_id["tbl1_r1"].canonical_text_start_utf16 is None
    assert blocks_by_id["tbl1_r1"].canonical_text_end_utf16 is None
    assert blocks_by_id["tbl1_r1_c1"].canonical_text_start_utf16 is None
    assert blocks_by_id["tbl1_r1_c1"].canonical_text_end_utf16 is None

    # The cell text MUST NOT leak into the canonical text.
    assert "cell value" not in plan.canonical_text

    # Only h1 + p1 contribute to canonical text.
    assert plan.canonical_text == "Title" + CANONICAL_TEXT_BLOCK_SEPARATOR + "Body after table."

    # Table hierarchy is preserved.
    assert blocks_by_id["tbl1_r1"].parent_block_id == "tbl1"
    assert blocks_by_id["tbl1_r1_c1"].parent_block_id == "tbl1_r1"


def test_table_hierarchy_routes_recorded_in_diagnostics() -> None:
    plan = _build(
        [
            _table("tbl1", 0),
            _table_row("tbl1_r1", 1, parent="tbl1"),
            _table_cell("tbl1_r1_c1", "cell value", 2, parent="tbl1_r1"),
            _paragraph("p1", "Body.", 3),
        ]
    )
    routes = plan.diagnostics.block_routes
    assert routes["tbl1"] == "metadata_only"
    assert routes["tbl1_r1"] == "metadata_only"
    assert routes["tbl1_r1_c1"] == "rag_ask_only"
    assert routes["p1"] == "main_reading"


# --------------------------------------------------------------------
# image + image_ocr default excluded, explicit policy promotes
# --------------------------------------------------------------------


def test_image_ocr_default_excluded_but_explicit_main_reading_promotes() -> None:
    """image_ocr defaults to rag_ask_only, so it does NOT enter canonical
    text by default. An explicit interpretation_policy with
    default_route='main_reading' MUST promote the image_ocr into
    canonical text.
    """
    # Default path: image_ocr excluded.
    plan_default = _build(
        [
            _paragraph("p1", "Body.", 0),
            _image("img1", 1),
            _image_ocr("img1_ocr", "OCR text.", 2, parent="img1"),
        ]
    )
    blocks_default = {b.block_id: b for b in plan_default.blocks}
    assert blocks_default["img1"].canonical_text_start_utf16 is None
    assert blocks_default["img1_ocr"].canonical_text_start_utf16 is None
    assert "OCR text." not in plan_default.canonical_text
    # image block keeps image_ocr as child.
    assert blocks_default["img1_ocr"].parent_block_id == "img1"

    # Promoted path: image_ocr with explicit main_reading policy enters
    # canonical text.
    promoted_ocr = StableDocumentBlock(
        block_id="img1_ocr",
        order_index=2,
        block_type="image_ocr",
        text_content="OCR text.",
        payload_json={"engine": "ocr-v1"},
        parent_block_id="img1",
        interpretation_policy=_main_reading_policy(),
    )
    plan_promoted = _build(
        [
            _paragraph("p1", "Body.", 0),
            _image("img1", 1),
            promoted_ocr,
        ]
    )
    blocks_promoted = {b.block_id: b for b in plan_promoted.blocks}
    assert blocks_promoted["img1_ocr"].canonical_text_start_utf16 is not None
    assert blocks_promoted["img1_ocr"].canonical_text_end_utf16 is not None
    assert "OCR text." in plan_promoted.canonical_text
    # image itself is still metadata_only / excluded.
    assert blocks_promoted["img1"].canonical_text_start_utf16 is None


# --------------------------------------------------------------------
# footnote / code_block default excluded
# --------------------------------------------------------------------


def test_footnote_and_code_block_default_excluded() -> None:
    plan = _build(
        [
            _paragraph("p1", "Body.", 0),
            _footnote("fn1", "Footnote body.", 1),
            _code_block("cb1", "print('hi')", 2),
        ]
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}

    # footnote / code_block default to rag_ask_only -> no canonical offsets.
    assert blocks_by_id["fn1"].canonical_text_start_utf16 is None
    assert blocks_by_id["fn1"].canonical_text_end_utf16 is None
    assert blocks_by_id["cb1"].canonical_text_start_utf16 is None
    assert blocks_by_id["cb1"].canonical_text_end_utf16 is None

    # Their text MUST NOT leak into canonical text.
    assert "Footnote body." not in plan.canonical_text
    assert "print('hi')" not in plan.canonical_text

    # Only paragraph contributes.
    assert plan.canonical_text == "Body."


# --------------------------------------------------------------------
# explicit main_reading policy promotes table_cell / footnote / code_block
# --------------------------------------------------------------------


def test_explicit_main_reading_promotes_table_cell_footnote_code_block() -> None:
    """A caller-supplied interpretation_policy with default_route=
    'main_reading' MUST promote table_cell / footnote / code_block into
    canonical text, regardless of their per-block-type default route.
    """
    promoted_table_cell = StableDocumentBlock(
        block_id="cell_promoted",
        order_index=0,
        block_type="table_cell",
        text_content="cell text promoted",
        interpretation_policy=_main_reading_policy(),
    )
    promoted_footnote = StableDocumentBlock(
        block_id="fn_promoted",
        order_index=1,
        block_type="footnote",
        text_content="footnote promoted",
        interpretation_policy=_main_reading_policy(),
    )
    promoted_code_block = StableDocumentBlock(
        block_id="cb_promoted",
        order_index=2,
        block_type="code_block",
        text_content="code = 'promoted'",
        interpretation_policy=_main_reading_policy(),
    )

    plan = _build([promoted_table_cell, promoted_footnote, promoted_code_block])
    blocks_by_id = {b.block_id: b for b in plan.blocks}

    for block_id in ("cell_promoted", "fn_promoted", "cb_promoted"):
        assert blocks_by_id[block_id].canonical_text_start_utf16 is not None
        assert blocks_by_id[block_id].canonical_text_end_utf16 is not None

    # Canonical text contains all three promoted chunks in order.
    assert plan.canonical_text == (
        "cell text promoted"
        + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "footnote promoted"
        + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "code = 'promoted'"
    )

    # Routes recorded as main_reading in diagnostics.
    for block_id in ("cell_promoted", "fn_promoted", "cb_promoted"):
        assert plan.diagnostics.block_routes[block_id] == "main_reading"


def test_explicit_demote_paragraph_to_rag_ask_only_excludes_from_canonical() -> None:
    """A caller may also DEMOTE a normally-main_reading block (e.g.
    paragraph) to rag_ask_only / metadata_only / ignored; in that case
    the block MUST NOT enter canonical text.
    """
    demoted_policy = StableDocumentInterpretationPolicy(
        allowed_source_scope=["main_reading_text"],
        default_route="rag_ask_only",
        rag_eligible=True,
    )
    demoted_paragraph = StableDocumentBlock(
        block_id="p_demoted",
        order_index=0,
        block_type="paragraph",
        text_content="Should not appear in canonical text.",
        interpretation_policy=demoted_policy,
    )
    plan = _build(
        [
            demoted_paragraph,
            _paragraph("p1", "Should appear.", 1),
        ]
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}

    assert blocks_by_id["p_demoted"].canonical_text_start_utf16 is None
    assert blocks_by_id["p_demoted"].canonical_text_end_utf16 is None
    assert "Should not appear in canonical text." not in plan.canonical_text
    assert plan.canonical_text == "Should appear."
    assert plan.diagnostics.block_routes["p_demoted"] == "rag_ask_only"


# --------------------------------------------------------------------
# emoji / surrogate pair UTF-16 offsets
# --------------------------------------------------------------------


def test_emoji_utf16_offsets_correct() -> None:
    """UTF-16 offsets must be computed in JavaScript UTF-16 code units,
    not Python ``len``. An emoji (surrogate pair) counts as 2 code units.
    """
    emoji_text = "Hello 😀 world"  # 13 Python chars, 14 UTF-16 code units
    assert utf16_code_unit_length(emoji_text) == 14
    assert utf16_code_unit_length(emoji_text) != len(emoji_text)

    plan = _build(
        [
            _paragraph("p1", "Intro.", 0),
            _paragraph("p2", emoji_text, 1),
            _paragraph("p3", "Outro.", 2),
        ]
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}

    # p2 starts after p1 + separator.
    p2 = blocks_by_id["p2"]
    expected_p2_start = (
        utf16_code_unit_length("Intro.")
        + utf16_code_unit_length(CANONICAL_TEXT_BLOCK_SEPARATOR)
    )
    assert p2.canonical_text_start_utf16 == expected_p2_start
    assert p2.canonical_text_end_utf16 == expected_p2_start + 14  # emoji = 2 code units

    # p3 starts after p2 + separator. The emoji must have advanced the
    # cursor by 2 (not 1) code units, otherwise the offset would be off
    # by one.
    p3 = blocks_by_id["p3"]
    expected_p3_start = p2.canonical_text_end_utf16 + utf16_code_unit_length(
        CANONICAL_TEXT_BLOCK_SEPARATOR
    )
    assert p3.canonical_text_start_utf16 == expected_p3_start

    # Slice back to verify the emoji round-trips correctly.
    sliced_p2 = slice_by_utf16_offsets(
        plan.canonical_text,
        p2.canonical_text_start_utf16,
        p2.canonical_text_end_utf16,
    )
    assert sliced_p2 == emoji_text


def test_multiple_emojis_and_mixed_text_offsets() -> None:
    """Stress test: multiple surrogate pairs in a single block, plus
    mixed ASCII / emoji / CJK / surrogate pair content across blocks.
    """
    block_a = "🚀🎉 ascii"  # 2 emojis + space + 5 ascii = 2+2+1+5 = 10 utf16
    block_b = "中文"  # 2 CJK chars, each 1 UTF-16 code unit = 2 utf16
    block_c = "😀"  # 1 emoji = 2 utf16

    plan = _build(
        [
            _paragraph("a", block_a, 0),
            _paragraph("b", block_b, 1),
            _paragraph("c", block_c, 2),
        ]
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}

    a = blocks_by_id["a"]
    b = blocks_by_id["b"]
    c = blocks_by_id["c"]

    assert a.canonical_text_start_utf16 == 0
    assert a.canonical_text_end_utf16 == utf16_code_unit_length(block_a)

    sep_len = utf16_code_unit_length(CANONICAL_TEXT_BLOCK_SEPARATOR)
    assert b.canonical_text_start_utf16 == a.canonical_text_end_utf16 + sep_len
    assert b.canonical_text_end_utf16 == b.canonical_text_start_utf16 + utf16_code_unit_length(block_b)

    assert c.canonical_text_start_utf16 == b.canonical_text_end_utf16 + sep_len
    assert c.canonical_text_end_utf16 == c.canonical_text_start_utf16 + utf16_code_unit_length(block_c)

    # Each block slices back to its own text.
    for block_id, expected in [("a", block_a), ("b", block_b), ("c", block_c)]:
        block = blocks_by_id[block_id]
        sliced = slice_by_utf16_offsets(
            plan.canonical_text,
            block.canonical_text_start_utf16,
            block.canonical_text_end_utf16,
        )
        assert sliced == expected


# --------------------------------------------------------------------
# no main-reading blocks -> fail closed
# --------------------------------------------------------------------


def test_no_main_reading_blocks_fails_closed() -> None:
    """When no block contributes to canonical text, the builder MUST
    raise StableDocumentFreezePlanError instead of returning an empty
    canonical text.
    """
    with pytest.raises(StableDocumentFreezePlanError, match="no main-reading"):
        _build(
            [
                _table("t1", 0),
                _table_row("t1_r1", 1, parent="t1"),
                _table_cell("t1_r1_c1", "cell", 2, parent="t1_r1"),
                _image("img1", 3),
            ]
        )


def test_only_demoted_paragraphs_fails_closed() -> None:
    """If every paragraph is explicitly demoted to rag_ask_only, no
    main_reading text remains and the builder must fail closed.
    """
    demoted = StableDocumentInterpretationPolicy(
        allowed_source_scope=["main_reading_text"],
        default_route="rag_ask_only",
        rag_eligible=True,
    )
    with pytest.raises(StableDocumentFreezePlanError, match="no main-reading"):
        _build(
            [
                StableDocumentBlock(
                    block_id="p1",
                    order_index=0,
                    block_type="paragraph",
                    text_content="demoted",
                    interpretation_policy=demoted,
                ),
            ]
        )


# --------------------------------------------------------------------
# stable_document.content_sha256 == output.content_sha256
# --------------------------------------------------------------------


def test_stable_document_content_sha256_matches_output_content_sha256() -> None:
    plan = _build(
        [
            _heading("h1", "Title", 0),
            _paragraph("p1", "Body.", 1),
        ]
    )
    assert plan.stable_document.content_sha256 == plan.content_sha256
    # 64-char lowercase hex SHA-256.
    assert len(plan.content_sha256) == 64
    assert all(c in "0123456789abcdef" for c in plan.content_sha256)


# --------------------------------------------------------------------
# hash sensitivity to policy / offset changes
# --------------------------------------------------------------------


def test_hash_sensitive_to_policy_change() -> None:
    """Promoting an image_ocr from rag_ask_only (default) to
    main_reading changes both the interpretation_policy AND the
    canonical offsets, so the hash MUST change.
    """
    # Default: image_ocr excluded.
    plan_default = _build(
        [
            _paragraph("p1", "Body.", 0),
            _image("img1", 1),
            _image_ocr("img1_ocr", "OCR text.", 2, parent="img1"),
        ]
    )

    # Promoted: image_ocr enters canonical text.
    promoted_ocr = StableDocumentBlock(
        block_id="img1_ocr",
        order_index=2,
        block_type="image_ocr",
        text_content="OCR text.",
        parent_block_id="img1",
        interpretation_policy=_main_reading_policy(),
    )
    plan_promoted = _build(
        [
            _paragraph("p1", "Body.", 0),
            _image("img1", 1),
            promoted_ocr,
        ]
    )

    assert plan_default.content_sha256 != plan_promoted.content_sha256
    # The promoted plan has more canonical text.
    assert "OCR text." not in plan_default.canonical_text
    assert "OCR text." in plan_promoted.canonical_text


def test_hash_sensitive_to_offset_change() -> None:
    """Adding a new main_reading block BEFORE an existing one shifts the
    existing block's canonical offsets; the hash MUST change even though
    the existing block's text_content is unchanged.
    """
    plan_a = _build([_paragraph("p1", "Body.", 0)])
    plan_b = _build(
        [
            _paragraph("p0", "Intro.", 0),
            _paragraph("p1", "Body.", 1),
        ]
    )

    # p1's text_content is identical in both plans, but its canonical
    # offsets differ (plan_a: 0..5; plan_b: after "Intro.\n\n").
    p1_a = {b.block_id: b for b in plan_a.blocks}["p1"]
    p1_b = {b.block_id: b for b in plan_b.blocks}["p1"]
    assert p1_a.canonical_text_start_utf16 == 0
    assert p1_b.canonical_text_start_utf16 != 0
    assert p1_a.canonical_text_end_utf16 != p1_b.canonical_text_end_utf16

    # Hash must differ.
    assert plan_a.content_sha256 != plan_b.content_sha256


def test_hash_sensitive_to_separator_would_change() -> None:
    """Sanity: if the separator changed, the offsets would shift and the
    hash would change. We cannot change the separator at runtime, but we
    can verify two plans with the same blocks produce the same hash
    (i.e. the hash is deterministic).
    """
    plan_a = _build(
        [_paragraph("p1", "Body.", 0), _paragraph("p2", "More.", 1)]
    )
    plan_b = _build(
        [_paragraph("p1", "Body.", 0), _paragraph("p2", "More.", 1)]
    )
    assert plan_a.content_sha256 == plan_b.content_sha256


# --------------------------------------------------------------------
# input non-mutation
# --------------------------------------------------------------------


def test_input_blocks_not_mutated() -> None:
    """The builder MUST NOT mutate caller-owned StableDocumentBlock
    instances. The returned blocks are NEW instances (different identity)
    with canonical offsets populated.
    """
    p1 = _paragraph("p1", "Body.", 0)
    p2 = _paragraph("p2", "More.", 1)
    # Pre-populate caller-side canonical offsets that the builder should
    # IGNORE (it derives offsets from the policy decision).
    p1_with_offsets = p1.model_copy(
        update={"canonical_text_start_utf16": 999, "canonical_text_end_utf16": 1000}
    )
    inputs = [p1_with_offsets, p2]

    # Snapshot the inputs before the call.
    p1_before = p1_with_offsets.model_copy()
    p2_before = p2.model_copy()

    plan = _build(inputs)

    # Inputs unchanged.
    assert p1_with_offsets == p1_before
    assert p2 == p2_before
    # Specifically, the caller's pre-populated offsets are preserved on
    # the input (the builder does not reach in and clear them).
    assert p1_with_offsets.canonical_text_start_utf16 == 999
    assert p1_with_offsets.canonical_text_end_utf16 == 1000

    # The output blocks are NEW instances with derived offsets.
    out_p1 = next(b for b in plan.blocks if b.block_id == "p1")
    out_p2 = next(b for b in plan.blocks if b.block_id == "p2")
    assert out_p1 is not p1_with_offsets
    assert out_p2 is not p2
    # p1's output offsets are derived (0..5), NOT the caller's 999..1000.
    assert out_p1.canonical_text_start_utf16 == 0
    assert out_p1.canonical_text_end_utf16 == utf16_code_unit_length("Body.")


def test_input_blocks_not_mutated_when_non_main_reading_has_caller_offsets() -> None:
    """A non-main_reading block (e.g. table_cell) with caller-supplied
    canonical offsets MUST have those offsets cleared in the OUTPUT (the
    freeze plan only honors main_reading canonical mappings), but the
    INPUT instance MUST be left untouched.
    """
    cell_with_offsets = StableDocumentBlock(
        block_id="cell1",
        order_index=1,
        block_type="table_cell",
        text_content="cell",
        canonical_text_start_utf16=42,
        canonical_text_end_utf16=46,
    )
    p1 = _paragraph("p1", "Body.", 0)
    inputs = [p1, cell_with_offsets]
    cell_before = cell_with_offsets.model_copy()

    plan = _build(inputs)

    # Input unchanged.
    assert cell_with_offsets == cell_before
    assert cell_with_offsets.canonical_text_start_utf16 == 42

    # Output cell has cleared canonical offsets (table_cell defaults to
    # rag_ask_only, so it does not contribute to canonical text).
    out_cell = next(b for b in plan.blocks if b.block_id == "cell1")
    assert out_cell.canonical_text_start_utf16 is None
    assert out_cell.canonical_text_end_utf16 is None
    assert out_cell is not cell_with_offsets


def test_input_list_not_mutated() -> None:
    """The input list itself is not mutated (no reordering, no
    replacement, no append).
    """
    inputs = [
        _paragraph("p2", "B", 1),
        _paragraph("p1", "A", 0),
    ]
    inputs_snapshot = [b.model_copy() for b in inputs]

    plan = _build(inputs)

    # Same length, same identities, same content.
    assert len(inputs) == 2
    for original, snapshot in zip(inputs, inputs_snapshot):
        assert original == snapshot
    # Output is sorted by order_index (p1 first, p2 second), independent
    # of the input list order.
    assert [b.block_id for b in plan.blocks] == ["p1", "p2"]


# --------------------------------------------------------------------
# D6-I1 validator failure -> freeze plan error
# --------------------------------------------------------------------


def test_validator_failure_raises_freeze_plan_error() -> None:
    """When the D6-I1 validator rejects the input (e.g. duplicate
    block_id), the freeze plan builder MUST wrap the failure in
    StableDocumentFreezePlanError.
    """
    with pytest.raises(
        StableDocumentFreezePlanError, match="D6-I1 block validation failed"
    ):
        _build(
            [
                _paragraph("dup", "a", 0),
                _paragraph("dup", "b", 1),
            ]
        )


def test_validator_failure_for_unknown_parent_raises_freeze_plan_error() -> None:
    with pytest.raises(
        StableDocumentFreezePlanError, match="D6-I1 block validation failed"
    ):
        _build(
            [
                _paragraph("p1", "a", 0),
                StableDocumentBlock(
                    block_id="orphan",
                    order_index=1,
                    block_type="paragraph",
                    text_content="b",
                    parent_block_id="missing",
                ),
            ]
        )


# --------------------------------------------------------------------
# non-main_reading canonical offsets forced to None
# --------------------------------------------------------------------


def test_non_main_reading_blocks_have_none_canonical_offsets_in_output() -> None:
    """Even if the caller passes a non-main_reading block with
    pre-populated canonical offsets, the OUTPUT must have None offsets.
    The canonical mapping is derived solely from the policy decision.
    """
    cell = StableDocumentBlock(
        block_id="cell1",
        order_index=1,
        block_type="table_cell",
        text_content="cell",
        canonical_text_start_utf16=10,
        canonical_text_end_utf16=14,
    )
    plan = _build([_paragraph("p1", "Body.", 0), cell])
    out_cell = next(b for b in plan.blocks if b.block_id == "cell1")
    assert out_cell.canonical_text_start_utf16 is None
    assert out_cell.canonical_text_end_utf16 is None


# --------------------------------------------------------------------
# promoted block with empty text -> fail closed
# --------------------------------------------------------------------


def test_promoted_table_cell_with_empty_text_raises() -> None:
    """A promoted table_cell with text_content=None and
    default_route='main_reading' MUST fail closed. A main_reading route
    with no text would otherwise produce an inconsistent frozen block
    (main_reading policy but None canonical offsets).
    """
    promoted_empty_cell = StableDocumentBlock(
        block_id="cell_empty",
        order_index=1,
        block_type="table_cell",
        text_content=None,  # structural type allows None
        interpretation_policy=_main_reading_policy(),
    )
    with pytest.raises(
        StableDocumentFreezePlanError,
        match=r"block_id='cell_empty'.*main_reading requires non-empty text_content",
    ):
        _build(
            [
                _paragraph("p1", "Body.", 0),
                promoted_empty_cell,
            ]
        )


def test_promoted_code_block_with_empty_text_raises() -> None:
    """A promoted code_block with text_content=None and
    default_route='main_reading' MUST fail closed for the same reason
    as table_cell: main_reading requires non-empty text_content.
    """
    promoted_empty_code = StableDocumentBlock(
        block_id="code_empty",
        order_index=0,
        block_type="code_block",
        text_content=None,  # structural type allows None
        payload_json={"language": "python"},
        interpretation_policy=_main_reading_policy(),
    )
    with pytest.raises(
        StableDocumentFreezePlanError,
        match=r"block_id='code_empty'.*block_type='code_block'.*main_reading requires non-empty text_content",
    ):
        _build([promoted_empty_code])


def test_all_promoted_blocks_empty_text_fails_closed() -> None:
    """If every main_reading block has empty text_content (only possible
    for promoted structural blocks), the builder MUST fail closed on the
    first such block rather than producing an empty canonical text.
    """
    promoted_empty_cell = StableDocumentBlock(
        block_id="cell_empty",
        order_index=0,
        block_type="table_cell",
        text_content=None,
        interpretation_policy=_main_reading_policy(),
    )
    with pytest.raises(
        StableDocumentFreezePlanError,
        match=r"block_id='cell_empty'.*main_reading requires non-empty text_content",
    ):
        _build([promoted_empty_cell])


def test_promoted_block_with_non_empty_text_still_passes() -> None:
    """Sanity: a promoted table_cell / code_block / image_ocr / footnote
    with NON-empty text_content and default_route='main_reading' still
    enters canonical text successfully. The fail-closed rule only fires
    when text_content is empty.
    """
    plan = _build(
        [
            StableDocumentBlock(
                block_id="cell_ok",
                order_index=0,
                block_type="table_cell",
                text_content="cell text",
                interpretation_policy=_main_reading_policy(),
            ),
            StableDocumentBlock(
                block_id="code_ok",
                order_index=1,
                block_type="code_block",
                text_content="x = 1",
                interpretation_policy=_main_reading_policy(),
            ),
            StableDocumentBlock(
                block_id="ocr_ok",
                order_index=2,
                block_type="image_ocr",
                text_content="OCR text.",
                interpretation_policy=_main_reading_policy(),
            ),
            StableDocumentBlock(
                block_id="fn_ok",
                order_index=3,
                block_type="footnote",
                text_content="Footnote.",
                interpretation_policy=_main_reading_policy(),
            ),
        ]
    )
    assert plan.canonical_text == (
        "cell text"
        + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "x = 1"
        + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "OCR text."
        + CANONICAL_TEXT_BLOCK_SEPARATOR
        + "Footnote."
    )
    blocks_by_id = {b.block_id: b for b in plan.blocks}
    for block_id in ("cell_ok", "code_ok", "ocr_ok", "fn_ok"):
        assert blocks_by_id[block_id].canonical_text_start_utf16 is not None
        assert blocks_by_id[block_id].canonical_text_end_utf16 is not None


# --------------------------------------------------------------------
# stable document fields and source_profile_json
# --------------------------------------------------------------------


def test_stable_document_carries_metadata() -> None:
    """The frozen StableReadingDocument carries the caller-supplied
    reading_record_id / record_generation / document_version / title /
    source_profile_json, plus the derived content_sha256.
    """
    source_profile = {"input_type": "markdown", "artifact_id": "art-1"}
    plan = build_stable_document_freeze_plan(
        reading_record_id="rec-abc",
        record_generation=3,
        document_version=2,
        title="Custom Title",
        blocks=[_paragraph("p1", "Body.", 0)],
        source_profile_json=source_profile,
    )
    assert plan.stable_document.reading_record_id == "rec-abc"
    assert plan.stable_document.record_generation == 3
    assert plan.stable_document.document_version == 2
    assert plan.stable_document.title == "Custom Title"
    assert plan.stable_document.source_profile_json == source_profile
    assert plan.stable_document.status == "active"
    assert plan.stable_document.content_sha256 == plan.content_sha256


def test_source_profile_json_defaults_to_empty_dict() -> None:
    plan = _build([_paragraph("p1", "Body.", 0)])
    assert plan.stable_document.source_profile_json == {}


# --------------------------------------------------------------------
# canonical text does not use compose_stable_document_plain_text
# --------------------------------------------------------------------


def test_canonical_text_does_not_contain_structural_placeholders() -> None:
    """The freeze plan's canonical text MUST NOT contain the
    `[[structural:...]]` placeholders emitted by
    compose_stable_document_plain_text(). Those are preview-only; the
    freeze plan derives canonical text directly from main_reading
    blocks' text_content.
    """
    plan = _build(
        [
            _heading("h1", "Title", 0),
            _table("t1", 1),
            _table_row("t1_r1", 2, parent="t1"),
            _table_cell("t1_r1_c1", "cell", 3, parent="t1_r1"),
            _paragraph("p1", "Body.", 4),
        ]
    )
    assert "[[structural:" not in plan.canonical_text
    assert "block_id=" not in plan.canonical_text
    assert "type=table" not in plan.canonical_text


# --------------------------------------------------------------------
# canonical text separator is pinned
# --------------------------------------------------------------------


def test_canonical_text_separator_is_double_newline() -> None:
    """Pin the separator value. Changing it invalidates already-frozen
    documents because UTF-16 offsets shift.
    """
    assert CANONICAL_TEXT_BLOCK_SEPARATOR == "\n\n"

    plan = _build(
        [_paragraph("p1", "A", 0), _paragraph("p2", "B", 1)]
    )
    assert plan.canonical_text == "A\n\nB"


# --------------------------------------------------------------------
# deep copy / model_copy non-aliasing sanity
# --------------------------------------------------------------------


def test_input_blocks_deepcopy_independent_of_output() -> None:
    """Mutating the caller's input list / blocks after build() returns
    MUST NOT affect the returned plan, and vice versa.
    """
    p1 = _paragraph("p1", "Body.", 0)
    plan = _build([p1])

    # Mutate the input after build.
    p1.text_content = "MUTATED"
    out_p1 = next(b for b in plan.blocks if b.block_id == "p1")
    assert out_p1.text_content == "Body."  # output unchanged

    # Mutate the output's input payload (via model_copy) — input list
    # stays untouched.
    out_p1_copy = out_p1.model_copy(update={"text_content": "OTHER"})
    assert p1.text_content == "MUTATED"
    assert out_p1.text_content == "Body."
    assert out_p1_copy.text_content == "OTHER"


# --------------------------------------------------------------------
# deep copy: nested mutable fields must not alias between input and output
# --------------------------------------------------------------------


def test_main_reading_output_blocks_deep_copy_nested_dicts() -> None:
    """Mutating nested payload_json / source_refs_json / quality_json on
    a main_reading output block MUST NOT affect the input block, and
    vice versa. ``model_copy(deep=True, ...)`` guarantees this.
    """
    p1 = StableDocumentBlock(
        block_id="p1",
        order_index=0,
        block_type="paragraph",
        text_content="Body.",
        payload_json={"key": ["original"]},
        source_refs_json={"ref": "url-1"},
        quality_json={"score": 0.9},
    )
    plan = _build([p1])
    out_p1 = next(b for b in plan.blocks if b.block_id == "p1")

    # Mutate output nested dicts/lists.
    out_p1.payload_json["key"].append("mutated-from-output")
    out_p1.source_refs_json["ref"] = "mutated-from-output"
    out_p1.quality_json["score"] = 0.1

    # Input unaffected.
    assert p1.payload_json == {"key": ["original"]}
    assert p1.source_refs_json == {"ref": "url-1"}
    assert p1.quality_json == {"score": 0.9}

    # Mutate input nested dicts/lists.
    p1.payload_json["key"].append("mutated-from-input")
    p1.source_refs_json["ref"] = "mutated-from-input"
    p1.quality_json["score"] = 0.5

    # Output unaffected.
    assert out_p1.payload_json == {"key": ["original", "mutated-from-output"]}
    assert out_p1.source_refs_json == {"ref": "mutated-from-output"}
    assert out_p1.quality_json == {"score": 0.1}


def test_non_main_reading_output_blocks_deep_copy_nested_dicts() -> None:
    """Deep copy must also apply to non-main_reading blocks (table /
    table_cell / image / etc.), not just main_reading blocks. A
    non-main_reading block has its canonical offsets cleared in the
    output, but its nested payload_json / source_refs_json /
    quality_json must still be independent copies.
    """
    table = StableDocumentBlock(
        block_id="tbl1",
        order_index=1,
        block_type="table",
        text_content=None,
        payload_json={"rows": 2, "cols": 2, "meta": ["original"]},
        source_refs_json={"ref": "url-table"},
        quality_json={"score": 0.8},
    )
    p1 = _paragraph("p1", "Body.", 0)
    plan = _build([p1, table])
    out_table = next(b for b in plan.blocks if b.block_id == "tbl1")

    # Mutate output nested dicts/lists.
    out_table.payload_json["meta"].append("mutated-from-output")
    out_table.source_refs_json["ref"] = "mutated-from-output"
    out_table.quality_json["score"] = 0.1

    # Input unaffected.
    assert table.payload_json == {"rows": 2, "cols": 2, "meta": ["original"]}
    assert table.source_refs_json == {"ref": "url-table"}
    assert table.quality_json == {"score": 0.8}

    # Mutate input nested dicts/lists.
    table.payload_json["meta"].append("mutated-from-input")
    table.source_refs_json["ref"] = "mutated-from-input"
    table.quality_json["score"] = 0.5

    # Output unaffected.
    assert out_table.payload_json == {
        "rows": 2,
        "cols": 2,
        "meta": ["original", "mutated-from-output"],
    }
    assert out_table.source_refs_json == {"ref": "mutated-from-output"}
    assert out_table.quality_json == {"score": 0.1}


def test_interpretation_policy_lists_not_aliased_between_input_and_output() -> None:
    """The interpretation_policy.allowed_source_scope and notes lists
    MUST NOT be aliased between input and output blocks. Both are
    mutable lists; a shallow model_copy would share them.
    """
    policy = StableDocumentInterpretationPolicy(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
        notes=["original-note"],
    )
    p1 = StableDocumentBlock(
        block_id="p1",
        order_index=0,
        block_type="paragraph",
        text_content="Body.",
        interpretation_policy=policy,
    )
    plan = _build([p1])
    out_p1 = next(b for b in plan.blocks if b.block_id == "p1")
    out_policy = out_p1.interpretation_policy

    # They are different instances.
    assert out_policy is not policy

    # Mutate output policy lists.
    out_policy.allowed_source_scope.append("heading")
    out_policy.notes.append("mutated-from-output")

    # Input policy unaffected.
    assert policy.allowed_source_scope == ["main_reading_text"]
    assert policy.notes == ["original-note"]

    # Mutate input policy lists.
    policy.allowed_source_scope.append("footnote")
    policy.notes.append("mutated-from-input")

    # Output policy unaffected.
    assert out_policy.allowed_source_scope == ["main_reading_text", "heading"]
    assert out_policy.notes == ["original-note", "mutated-from-output"]


def test_non_main_reading_block_with_caller_offsets_deep_copy_nested_dicts() -> None:
    """Deep copy must also apply when the builder clears caller-supplied
    canonical offsets on a non-main_reading block (the code path that
    uses ``model_copy(deep=True, update={...})`` with non-None update).
    """
    cell = StableDocumentBlock(
        block_id="cell1",
        order_index=1,
        block_type="table_cell",
        text_content="cell",
        canonical_text_start_utf16=42,
        canonical_text_end_utf16=46,
        payload_json={"meta": ["original"]},
        source_refs_json={"ref": "url-cell"},
        quality_json={"score": 0.7},
    )
    p1 = _paragraph("p1", "Body.", 0)
    plan = _build([p1, cell])
    out_cell = next(b for b in plan.blocks if b.block_id == "cell1")

    # Offsets cleared in output (table_cell defaults to rag_ask_only).
    assert out_cell.canonical_text_start_utf16 is None
    assert out_cell.canonical_text_end_utf16 is None

    # Mutate output nested dicts/lists.
    out_cell.payload_json["meta"].append("mutated-from-output")
    out_cell.source_refs_json["ref"] = "mutated-from-output"
    out_cell.quality_json["score"] = 0.1

    # Input unaffected (including original offsets).
    assert cell.canonical_text_start_utf16 == 42
    assert cell.canonical_text_end_utf16 == 46
    assert cell.payload_json == {"meta": ["original"]}
    assert cell.source_refs_json == {"ref": "url-cell"}
    assert cell.quality_json == {"score": 0.7}

    # Mutate input nested dicts/lists.
    cell.payload_json["meta"].append("mutated-from-input")
    cell.source_refs_json["ref"] = "mutated-from-input"
    cell.quality_json["score"] = 0.5

    # Output unaffected.
    assert out_cell.payload_json == {"meta": ["original", "mutated-from-output"]}
    assert out_cell.source_refs_json == {"ref": "mutated-from-output"}
    assert out_cell.quality_json == {"score": 0.1}


# --------------------------------------------------------------------
# full document freeze end-to-end sanity
# --------------------------------------------------------------------


def test_full_document_freeze_with_mixed_block_types() -> None:
    """End-to-end sanity: a document with heading, paragraphs, a table
    hierarchy, an image + OCR (promoted), a footnote (default), and a
    code block (default) produces a coherent freeze plan.
    """
    plan = _build(
        [
            _heading("h1", "Document Title", 0),
            _paragraph("p1", "Intro paragraph.", 1),
            _table("tbl1", 2),
            _table_row("tbl1_r1", 3, parent="tbl1"),
            _table_cell("tbl1_r1_c1", "cell A", 4, parent="tbl1_r1"),
            _table_cell("tbl1_r1_c2", "cell B", 5, parent="tbl1_r1"),
            _paragraph("p2", "After table.", 6),
            _image("img1", 7),
            # Promoted OCR text enters canonical text.
            StableDocumentBlock(
                block_id="img1_ocr",
                order_index=8,
                block_type="image_ocr",
                text_content="OCR captured text.",
                parent_block_id="img1",
                interpretation_policy=_main_reading_policy(),
            ),
            _footnote("fn1", "Footnote text.", 9),
            _code_block("cb1", "x = 1", 10),
        ]
    )

    # Canonical text contains: heading, p1, p2, promoted OCR.
    expected_chunks = [
        "Document Title",
        "Intro paragraph.",
        "After table.",
        "OCR captured text.",
    ]
    assert plan.canonical_text == CANONICAL_TEXT_BLOCK_SEPARATOR.join(expected_chunks)

    # Table / image / footnote / code blocks have no canonical offsets.
    blocks_by_id = {b.block_id: b for b in plan.blocks}
    for excluded_id in (
        "tbl1", "tbl1_r1", "tbl1_r1_c1", "tbl1_r1_c2",
        "img1", "fn1", "cb1",
    ):
        assert blocks_by_id[excluded_id].canonical_text_start_utf16 is None
        assert blocks_by_id[excluded_id].canonical_text_end_utf16 is None

    # main_reading blocks (including promoted OCR) have offsets.
    for included_id in ("h1", "p1", "p2", "img1_ocr"):
        assert blocks_by_id[included_id].canonical_text_start_utf16 is not None
        assert blocks_by_id[included_id].canonical_text_end_utf16 is not None

    # Routes recorded.
    routes = plan.diagnostics.block_routes
    assert routes["h1"] == "main_reading"
    assert routes["p1"] == "main_reading"
    assert routes["p2"] == "main_reading"
    assert routes["tbl1"] == "metadata_only"
    assert routes["tbl1_r1"] == "metadata_only"
    assert routes["tbl1_r1_c1"] == "rag_ask_only"
    assert routes["img1"] == "metadata_only"
    assert routes["img1_ocr"] == "main_reading"  # promoted
    assert routes["fn1"] == "rag_ask_only"
    assert routes["cb1"] == "rag_ask_only"

    # content_sha256 matches.
    assert plan.stable_document.content_sha256 == plan.content_sha256
    assert len(plan.content_sha256) == 64

    # No warnings (all main_reading blocks had text).
    assert plan.diagnostics.warnings == []
