# task-history: D6-I1 (renamed from test_d6_i1_stable_document_blocks.py)
from __future__ import annotations

import re

import pytest

from app.schemas.reader_documents import (
    CandidateReadingDocument,
    StableDocumentBlock,
    StableDocumentInterpretationPolicy,
    StableReadingDocument,
    default_interpretation_policy_for,
)
from app.services.reader_orchestration.document_blocks import (
    StableDocumentValidationError,
    compose_stable_document_plain_text,
    compute_stable_document_content_sha256,
    validate_stable_document_blocks,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


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


def _table(block_id: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table",
        text_content=None,
        payload_json={"rows": 2, "cols": 3},
    )


# --------------------------------------------------------------------
# StableDocumentBlock pydantic invariants
# --------------------------------------------------------------------


def test_textual_block_requires_text_content() -> None:
    with pytest.raises(ValueError, match="text_content is required"):
        StableDocumentBlock(
            block_id="p1",
            order_index=0,
            block_type="paragraph",
            text_content=None,
        )


def test_structural_block_may_omit_text_content() -> None:
    block = StableDocumentBlock(
        block_id="t1",
        order_index=0,
        block_type="table",
        text_content=None,
        payload_json={"rows": 1},
    )
    assert block.text_content is None


def test_canonical_text_offsets_must_be_together() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        StableDocumentBlock(
            block_id="p1",
            order_index=0,
            block_type="paragraph",
            text_content="hello",
            canonical_text_start_utf16=0,
        )


def test_canonical_text_offsets_must_be_strictly_ordered() -> None:
    with pytest.raises(ValueError, match="must be greater than"):
        StableDocumentBlock(
            block_id="p1",
            order_index=0,
            block_type="paragraph",
            text_content="hello",
            canonical_text_start_utf16=10,
            canonical_text_end_utf16=10,
        )


def test_parent_must_differ_from_self() -> None:
    with pytest.raises(ValueError, match="must differ from block_id"):
        StableDocumentBlock(
            block_id="p1",
            order_index=0,
            block_type="paragraph",
            text_content="x",
            parent_block_id="p1",
        )


def test_interpretation_policy_ignored_requires_rag_disabled() -> None:
    with pytest.raises(ValueError, match="default_route='ignored'"):
        StableDocumentInterpretationPolicy(
            allowed_source_scope=["main_reading_text"],
            default_route="ignored",
            rag_eligible=True,
        )


# --------------------------------------------------------------------
# validate_stable_document_blocks invariants
# --------------------------------------------------------------------


def test_validate_accepts_simple_textual_sequence() -> None:
    validated = validate_stable_document_blocks(
        [
            _heading("h1", "Title", 0),
            _paragraph("p1", "First paragraph.", 1),
            _paragraph("p2", "Second paragraph.", 2),
        ]
    )
    assert [b.block_id for b in validated] == ["h1", "p1", "p2"]


def test_validate_rejects_duplicate_block_id() -> None:
    with pytest.raises(StableDocumentValidationError, match="duplicate block_id"):
        validate_stable_document_blocks(
            [
                _paragraph("dup", "a", 0),
                _paragraph("dup", "b", 1),
            ]
        )


def test_validate_rejects_duplicate_order_index() -> None:
    with pytest.raises(StableDocumentValidationError, match="duplicate order_index"):
        validate_stable_document_blocks(
            [
                _paragraph("p1", "a", 0),
                _paragraph("p2", "b", 0),
            ]
        )


def test_validate_rejects_unknown_parent() -> None:
    orphan = StableDocumentBlock(
        block_id="c1",
        order_index=1,
        block_type="paragraph",
        text_content="child",
        parent_block_id="missing",
    )
    parent = _heading("p1", "Title", 0)
    with pytest.raises(StableDocumentValidationError, match="unknown parent_block_id"):
        validate_stable_document_blocks([parent, orphan])


def test_validate_allows_structural_parent_table_to_table_row_to_table_cell() -> None:
    # A table may parent a table_row, and a table_row may parent a
    # table_cell. None of these carry text_content (the structural
    # payload is in payload_json), but the hierarchy must validate
    # cleanly so the document model can preserve nested table truth.
    table = StableDocumentBlock(
        block_id="tbl1",
        order_index=0,
        block_type="table",
        text_content=None,
        payload_json={"rows": 2, "cols": 2},
    )
    row = StableDocumentBlock(
        block_id="tbl1_row1",
        order_index=1,
        block_type="table_row",
        text_content=None,
        payload_json={"row_index": 0},
        parent_block_id="tbl1",
    )
    cell = StableDocumentBlock(
        block_id="tbl1_row1_c1",
        order_index=2,
        block_type="table_cell",
        text_content="cell value",
        parent_block_id="tbl1_row1",
    )
    validated = validate_stable_document_blocks([table, row, cell])
    assert [b.block_id for b in validated] == ["tbl1", "tbl1_row1", "tbl1_row1_c1"]


def test_validate_allows_image_to_image_ocr() -> None:
    image = StableDocumentBlock(
        block_id="img1",
        order_index=0,
        block_type="image",
        text_content=None,
        payload_json={"source_url": "s3://bucket/key.png"},
    )
    ocr = StableDocumentBlock(
        block_id="img1_ocr",
        order_index=1,
        block_type="image_ocr",
        text_content="extracted text",
        payload_json={"engine": "ocr-v1"},
        parent_block_id="img1",
    )
    validated = validate_stable_document_blocks([image, ocr])
    assert {b.block_id for b in validated} == {"img1", "img1_ocr"}


def test_validate_allows_image_to_caption() -> None:
    image = StableDocumentBlock(
        block_id="img2",
        order_index=0,
        block_type="image",
        text_content=None,
    )
    caption = StableDocumentBlock(
        block_id="img2_cap",
        order_index=1,
        block_type="caption",
        text_content="Figure 1: data flow.",
        parent_block_id="img2",
    )
    validated = validate_stable_document_blocks([image, caption])
    assert [b.parent_block_id for b in validated if b.parent_block_id] == ["img2"]


def test_validate_rejects_parent_cycle() -> None:
    a = StableDocumentBlock(
        block_id="a",
        order_index=0,
        block_type="paragraph",
        text_content="A",
        parent_block_id="b",
    )
    b = StableDocumentBlock(
        block_id="b",
        order_index=1,
        block_type="paragraph",
        text_content="B",
        parent_block_id="a",
    )
    with pytest.raises(StableDocumentValidationError, match="cycle detected"):
        validate_stable_document_blocks([a, b])


def test_validate_rejects_pydantic_invalid_block() -> None:
    with pytest.raises(StableDocumentValidationError, match="failed pydantic"):
        validate_stable_document_blocks(
            [
                {
                    "block_id": "p1",
                    "order_index": 0,
                    "block_type": "paragraph",
                    "text_content": None,
                }
            ]
        )


# --------------------------------------------------------------------
# compose_stable_document_plain_text
# --------------------------------------------------------------------


def test_compose_emits_structural_marker_not_payload() -> None:
    rendered = compose_stable_document_plain_text(
        [
            _heading("h1", "Title", 0),
            _table("t1", 1),
            _paragraph("p1", "Body.", 2),
        ]
    )
    lines = rendered.split("\n")
    assert lines[0] == "Title"
    assert lines[1].startswith("[[structural:")
    assert "block_id=t1" in lines[1]
    assert "type=table" in lines[1]
    assert lines[2] == "Body."
    # Structural payload must NOT leak into the rendered text.
    assert '"rows"' not in rendered
    assert "2" not in lines[1] or "type=table" in lines[1]


def test_compose_handles_only_structural_blocks() -> None:
    rendered = compose_stable_document_plain_text(
        [_table("t1", 0), _table("t2", 1)],
    )
    # Every line is a structural marker; no payload inlined.
    assert all(line.startswith("[[structural:") for line in rendered.split("\n"))
    assert "block_id=t1" in rendered
    assert "block_id=t2" in rendered


def test_compose_uses_order_index_not_input_order() -> None:
    rendered = compose_stable_document_plain_text(
        [
            _paragraph("p1", "B", 2),
            _paragraph("p0", "A", 1),
            _paragraph("p_", "Z", 0),
        ]
    )
    assert rendered == "Z\nA\nB"


# --------------------------------------------------------------------
# compute_stable_document_content_sha256
# --------------------------------------------------------------------


def test_content_sha256_is_stable_across_input_ordering() -> None:
    a = compute_stable_document_content_sha256(
        [_paragraph("p1", "A", 0), _paragraph("p2", "B", 1)]
    )
    b = compute_stable_document_content_sha256(
        [_paragraph("p2", "B", 1), _paragraph("p1", "A", 0)]
    )
    assert a == b
    assert len(a) == 64


def test_content_sha256_changes_when_text_changes() -> None:
    a = compute_stable_document_content_sha256([_paragraph("p1", "A", 0)])
    b = compute_stable_document_content_sha256([_paragraph("p1", "AA", 0)])
    assert a != b


def test_content_sha256_ignores_dict_insertion_order() -> None:
    # Two payloads that are semantically equal but constructed with
    # different key insertion orders must hash identically. This guards
    # the sha256 path against falling back to Python repr (which is
    # order-sensitive) and against accidentally inlining whitespace
    # differences.
    a = compute_stable_document_content_sha256(
        [
            StableDocumentBlock(
                block_id="p1",
                order_index=0,
                block_type="paragraph",
                text_content="hi",
                payload_json={"alpha": 1, "beta": "x", "nested": {"k": 1, "j": 2}},
                source_refs_json={"ref_a": "url-1", "ref_b": "url-2"},
            )
        ]
    )
    b = compute_stable_document_content_sha256(
        [
            StableDocumentBlock(
                block_id="p1",
                order_index=0,
                block_type="paragraph",
                text_content="hi",
                # Same keys, opposite insertion order, and nested dict
                # also reversed.
                payload_json={"nested": {"j": 2, "k": 1}, "beta": "x", "alpha": 1},
                source_refs_json={"ref_b": "url-2", "ref_a": "url-1"},
            )
        ]
    )
    assert a == b
    assert len(a) == 64


# --------------------------------------------------------------------
# StableReadingDocument + CandidateReadingDocument contracts
# --------------------------------------------------------------------


def test_stable_reading_document_rejects_non_sha256_hash() -> None:
    with pytest.raises(ValueError):
        StableReadingDocument(
            reading_record_id="r1",
            record_generation=1,
            document_version=1,
            content_sha256="not-a-hash",
        )


def test_candidate_reading_document_defaults() -> None:
    candidate = CandidateReadingDocument(
        reading_record_id="r1",
        user_id="u1",
        record_generation=1,
    )
    assert candidate.status == "ready"
    assert candidate.blocks_json == []
    assert candidate.canonical_text_preview == ""
    assert candidate.title is None


def test_candidate_status_must_match_schema_literal() -> None:
    # status is a Literal; passing a non-literal value must fail at
    # construction time so callers cannot smuggle unsupported states.
    with pytest.raises(ValueError):
        CandidateReadingDocument(
            reading_record_id="r1",
            user_id="u1",
            record_generation=1,
            status="archived",
        )


# --------------------------------------------------------------------
# DB / Python contract alignment
# --------------------------------------------------------------------


def _migration_sql() -> str:
    """Load the single fresh baseline migration from disk for static checks.

    Tests use this to assert that the on-disk baseline actually
    matches the Python contract — without requiring a live Postgres
    connection. The static guard catches drift early.

    The original 0004_reader_document_blocks.sql was folded into
    infra/migrations/0001_initial.sql (DATA-SCHEMA-BASELINE D2).
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    migration_path = (
        repo_root.parent.parent / "infra" / "migrations" / "0001_initial.sql"
    )
    assert migration_path.is_file(), (
        f"migration file not found at {migration_path}; the static "
        "contract-alignment tests below require the migration to be on disk"
    )
    return migration_path.read_text(encoding="utf-8")


def test_migration_parent_block_id_is_text_not_uuid() -> None:
    """parent_block_id in the DB must be TEXT (the block_id string),
    not UUID. The Python contract references parent by block_id, so the
    DB column must align.
    """
    sql = _migration_sql()
    # The column declaration must declare parent_block_id as TEXT.
    assert re.search(
        r"parent_block_id\s+TEXT",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "migration must declare parent_block_id as TEXT"
    # The previous UUID FK is gone; no REFERENCES against
    # stable_document_blocks(id) using only parent_block_id.
    assert not re.search(
        r"parent_block_id\s+UUID",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "parent_block_id must not be UUID"
    assert not re.search(
        r"parent_block_id\s+UUID\s+REFERENCES\s+stable_document_blocks\(id\)",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "parent_block_id must not UUID-FK into the row id"


def test_migration_has_composite_parent_fk() -> None:
    """The composite FK (stable_document_id, parent_block_id) ->
    (stable_document_id, block_id) must exist on stable_document_blocks.
    """
    sql = _migration_sql()
    assert re.search(
        r"FOREIGN\s+KEY\s*\(\s*stable_document_id\s*,\s*parent_block_id\s*\)",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "missing composite parent FK"
    assert "REFERENCES" in sql and "stable_document_blocks(stable_document_id, block_id)" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql


def test_migration_has_self_parent_check() -> None:
    """parent_block_id = block_id must be rejected at the DB level."""
    sql = _migration_sql()
    assert re.search(
        r"parent_block_id\s+IS\s+NULL\s*\)?\s*OR\s*\(?\s*parent_block_id\s*<>\s*block_id",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "missing self-parent CHECK"


def test_migration_has_unique_order_index() -> None:
    """UNIQUE (stable_document_id, order_index) must exist."""
    sql = _migration_sql()
    assert re.search(
        r"UNIQUE\s*\(\s*stable_document_id\s*,\s*order_index\s*\)",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "missing UNIQUE(stable_document_id, order_index)"


def test_python_parent_block_id_is_string_not_uuid() -> None:
    """StableDocumentBlock.parent_block_id is a string in the Python
    contract (block_id of the parent). Constructing a block with a UUID
    value should succeed and store the value verbatim — the DB layer is
    what enforces the composite FK + document-bound semantics.
    """
    block = StableDocumentBlock(
        block_id="p1",
        order_index=0,
        block_type="paragraph",
        text_content="x",
        parent_block_id="some-string-block-id",
    )
    assert isinstance(block.parent_block_id, str)
    assert block.parent_block_id == "some-string-block-id"


def test_validator_rejects_duplicate_order_index() -> None:
    """The Python validator must reject duplicate order_index even
    when block_ids are distinct, matching the UNIQUE(stable_document_id,
    order_index) DB constraint.
    """
    with pytest.raises(StableDocumentValidationError, match="duplicate order_index"):
        validate_stable_document_blocks(
            [
                _paragraph("p1", "a", 0),
                _paragraph("p2", "b", 0),
            ]
        )


# --------------------------------------------------------------------
# Per-block-type default interpretation policy
# --------------------------------------------------------------------

_DEFAULT_POLICY_MATRIX: dict[str, dict[str, object]] = {
    # Narrative blocks -> main_reading / main_reading_text, RAG-eligible.
    "paragraph": {
        "default_route": "main_reading",
        "allowed_source_scope": ["main_reading_text"],
        "rag_eligible": True,
    },
    "list_item": {
        "default_route": "main_reading",
        "allowed_source_scope": ["main_reading_text"],
        "rag_eligible": True,
    },
    "blockquote": {
        "default_route": "main_reading",
        "allowed_source_scope": ["main_reading_text"],
        "rag_eligible": True,
    },
    "caption": {
        "default_route": "main_reading",
        "allowed_source_scope": ["main_reading_text"],
        "rag_eligible": True,
    },
    # Heading -> main_reading / heading (its own scope), RAG-eligible.
    "heading": {
        "default_route": "main_reading",
        "allowed_source_scope": ["heading"],
        "rag_eligible": True,
    },
    # Table hierarchy -> main_reading (Markdown ecosystem refactor D2 /
    # A1). The table / table_row wrappers carry no text_content and stay
    # rag_eligible=False; RAG targets the table_cell leaves.
    "table_cell": {
        "default_route": "main_reading",
        "allowed_source_scope": ["table_cell"],
        "rag_eligible": True,
    },
    "image_ocr": {
        "default_route": "rag_ask_only",
        "allowed_source_scope": ["image_ocr"],
        "rag_eligible": True,
    },
    "footnote": {
        "default_route": "rag_ask_only",
        "allowed_source_scope": ["footnote"],
        "rag_eligible": True,
    },
    "code_block": {
        "default_route": "main_reading",
        "allowed_source_scope": ["code_block"],
        "rag_eligible": True,
    },
    # Structural / unknown blocks -> main_reading (table wrappers) or
    # metadata_only (image / unknown), NOT RAG-eligible.
    "table": {
        "default_route": "main_reading",
        "rag_eligible": False,
    },
    "table_row": {
        "default_route": "main_reading",
        "rag_eligible": False,
    },
    "image": {
        "default_route": "metadata_only",
        "rag_eligible": False,
    },
    "unknown": {
        "default_route": "metadata_only",
        "rag_eligible": False,
    },
}


@pytest.mark.parametrize("block_type,expected", list(_DEFAULT_POLICY_MATRIX.items()))
def test_default_interpretation_policy_for(block_type: str, expected: dict[str, object]) -> None:
    """The helper returns the documented per-block-type default."""
    policy = default_interpretation_policy_for(block_type)
    assert isinstance(policy, StableDocumentInterpretationPolicy)
    assert policy.default_route == expected["default_route"]
    assert policy.rag_eligible is expected["rag_eligible"]
    if "allowed_source_scope" in expected:
        assert policy.allowed_source_scope == expected["allowed_source_scope"]
    # metadata_only blocks still need a non-empty scope; the exact
    # scope is a conservative pick (table_cell for table/table_row,
    # image_ocr for image, published_layer for unknown).
    assert policy.allowed_source_scope, (
        f"default policy for {block_type!r} must have a non-empty "
        "allowed_source_scope"
    )


@pytest.mark.parametrize("block_type", sorted(_DEFAULT_POLICY_MATRIX.keys()))
def test_stable_document_block_applies_block_type_default(block_type: str) -> None:
    """StableDocumentBlock must use the per-block-type default when the
    caller does not pass interpretation_policy explicitly."""
    expected = _DEFAULT_POLICY_MATRIX[block_type]
    # Structural blocks allow text_content=None; narrative / RAG blocks
    # need a non-empty text_content.
    needs_text = block_type not in _STRUCTURAL_BLOCK_TYPES_NEEDING_NULL_OK
    block_kwargs: dict[str, object] = {
        "block_id": f"{block_type}-1",
        "order_index": 0,
        "block_type": block_type,
    }
    if needs_text:
        block_kwargs["text_content"] = "sample text"
    if block_type in {"table", "table_row", "image", "unknown"}:
        # The image / table / unknown blocks do not require text_content
        # but do need a payload_json marker so the structural truth is
        # visible in the rendered preview.
        block_kwargs["payload_json"] = {"marker": block_type}

    block = StableDocumentBlock(**block_kwargs)

    assert block.interpretation_policy is not None
    assert block.interpretation_policy.default_route == expected["default_route"]
    assert block.interpretation_policy.rag_eligible is expected["rag_eligible"]


_STRUCTURAL_BLOCK_TYPES_NEEDING_NULL_OK = {
    "table",
    "table_row",
    "image",
    "code_block",
    "unknown",
}


def test_default_interpretation_policy_helper_returns_fresh_instance() -> None:
    """Two calls to default_interpretation_policy_for must not share
    mutable state; otherwise callers could accidentally mutate the
    module-level default for every subsequent block.
    """
    a = default_interpretation_policy_for("paragraph")
    b = default_interpretation_policy_for("paragraph")
    assert a is not b
    # Mutating `a.notes` must not bleed into `b.notes`.
    a.notes.append("only on a")
    assert b.notes == []


def test_explicit_interpretation_policy_is_preserved() -> None:
    """A caller-supplied interpretation_policy (e.g. the Candidate
    Document confirm flow promoting an image_ocr into the main
    reading chain) must NOT be overridden by the per-block-type
    default. The helper is the canonical substitution point; this
    test pins the override contract.
    """
    explicit_policy = StableDocumentInterpretationPolicy(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
        notes=["promoted by user via Candidate confirm"],
    )
    block = StableDocumentBlock(
        block_id="ocr-promoted",
        order_index=0,
        block_type="image_ocr",  # default would be rag_ask_only / image_ocr
        text_content="OCR text the user promoted.",
        interpretation_policy=explicit_policy,
    )
    assert block.interpretation_policy is explicit_policy
    assert block.interpretation_policy.default_route == "main_reading"
    assert block.interpretation_policy.allowed_source_scope == ["main_reading_text"]
    assert block.interpretation_policy.notes == [
        "promoted by user via Candidate confirm"
    ]


def test_explicit_policy_via_dict_input_is_preserved() -> None:
    """Same override guarantee, but exercising the dict-input path
    used by the validator / Candidate confirm flow (which constructs
    blocks from dict payloads)."""
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "footnote-promoted",
            "order_index": 0,
            "block_type": "footnote",  # default would be rag_ask_only
            "text_content": "footnote text",
            "interpretation_policy": {
                "allowed_source_scope": ["main_reading_text"],
                "default_route": "main_reading",
                "rag_eligible": True,
            },
        }
    )
    assert block.interpretation_policy.default_route == "main_reading"
    assert block.interpretation_policy.allowed_source_scope == ["main_reading_text"]
    assert block.interpretation_policy.rag_eligible is True


def test_unknown_block_default_has_conservative_scope() -> None:
    """Unknown blocks have no closer matching scope; the default must
    use a conservative scope that does NOT claim the block is part of
    main reading text.
    """
    policy = default_interpretation_policy_for("unknown")
    assert policy.default_route == "metadata_only"
    assert policy.rag_eligible is False
    assert "main_reading_text" not in policy.allowed_source_scope
    assert policy.allowed_source_scope == ["published_layer"]


def test_metadata_only_blocks_are_not_rag_eligible_by_default() -> None:
    """The structural / unknown default policies must all carry
    rag_eligible=False so RAG indexing never silently pulls table /
    image / unknown truth.

    Since the Markdown ecosystem refactor (D2 / A1), table / table_row
    default to ``main_reading`` (they render in the main reading flow)
    but stay ``rag_eligible=False`` (structural wrappers carry no
    text).  image / unknown remain ``metadata_only``.
    """
    for block_type in ("image", "unknown"):
        policy = default_interpretation_policy_for(block_type)
        assert policy.default_route == "metadata_only", block_type
        assert policy.rag_eligible is False, block_type
    for block_type in ("table", "table_row"):
        policy = default_interpretation_policy_for(block_type)
        assert policy.default_route == "main_reading", block_type
        assert policy.rag_eligible is False, block_type


def test_content_sha256_uses_default_policy_when_unspecified() -> None:
    """When the caller omits interpretation_policy, the sha256 input
    must include the per-block-type default policy. This means two
    blocks with the same text/payload but different block_types will
    hash differently — which is the intended invariant: the
    interpretation policy is part of the durable document truth.
    """
    paragraph_sha = compute_stable_document_content_sha256(
        [StableDocumentBlock(
            block_id="x",
            order_index=0,
            block_type="paragraph",
            text_content="shared text",
        )]
    )
    heading_sha = compute_stable_document_content_sha256(
        [StableDocumentBlock(
            block_id="x",
            order_index=0,
            block_type="heading",
            text_content="shared text",
        )]
    )
    assert paragraph_sha != heading_sha


# --------------------------------------------------------------------
# Empty-dict / None / absent policy are all "not provided" -> default
# --------------------------------------------------------------------


@pytest.mark.parametrize("supplied", [{}, None])
def test_empty_dict_policy_is_treated_as_omitted_table_cell(supplied: object) -> None:
    """A storage-placeholder empty dict `{}` (or explicit None) for
    `interpretation_policy` MUST be replaced by the per-block-type
    default. For `table_cell`, that means `main_reading` /
    `table_cell` (Markdown ecosystem refactor D2 / A1), not the
    model-level `main_reading` / `main_reading_text` fallback. This
    prevents the DB `'{}'::jsonb` storage default from silently
    re-scoping table_cell blocks as narrative main-reading text.
    """
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "cell1",
            "order_index": 0,
            "block_type": "table_cell",
            "text_content": "cell value",
            "interpretation_policy": supplied,
        }
    )
    assert block.interpretation_policy.default_route == "main_reading"
    assert block.interpretation_policy.allowed_source_scope == ["table_cell"]
    assert block.interpretation_policy.rag_eligible is True


@pytest.mark.parametrize("supplied", [{}, None])
def test_empty_dict_policy_is_treated_as_omitted_image(supplied: object) -> None:
    """For `image`, the per-block-type default is `metadata_only`
    with `rag_eligible=False`. An empty-dict / None interpretation_policy
    MUST yield that default, not `main_reading` /
    `main_reading_text`.
    """
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "img1",
            "order_index": 0,
            "block_type": "image",
            "text_content": None,
            "payload_json": {"source_url": "s3://bucket/key.png"},
            "interpretation_policy": supplied,
        }
    )
    assert block.interpretation_policy.default_route == "metadata_only"
    assert block.interpretation_policy.rag_eligible is False
    # Must not claim the image is part of main reading text.
    assert "main_reading_text" not in block.interpretation_policy.allowed_source_scope


def test_absent_interpretation_policy_uses_default() -> None:
    """If the field is absent entirely from the input dict (which is
    the path taken by callers that construct via `model_validate`),
    the per-block-type default must still be substituted. Pins the
    "absent is the same as omitted" branch.
    """
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "cell1",
            "order_index": 0,
            "block_type": "table_cell",
            "text_content": "cell value",
        }
    )
    assert block.interpretation_policy.default_route == "main_reading"
    assert block.interpretation_policy.allowed_source_scope == ["table_cell"]


@pytest.mark.parametrize(
    "block_type",
    ["image", "unknown"],
)
def test_empty_dict_policy_never_silently_routes_to_main_reading(
    block_type: str,
) -> None:
    """Cross-block-type safety net: an empty-dict interpretation_policy
    must NEVER yield `main_reading` for any block type whose default is
    metadata_only. This is the core invariant the
    storage-placeholder split exists to enforce.
    """
    block_kwargs: dict[str, object] = {
        "block_id": f"{block_type}-storage-default",
        "order_index": 0,
        "block_type": block_type,
    }
    if block_type != "image":
        block_kwargs["text_content"] = None
    else:
        block_kwargs["text_content"] = None
        block_kwargs["payload_json"] = {"source_url": "s3://x"}
    block_kwargs["interpretation_policy"] = {}

    block = StableDocumentBlock.model_validate(block_kwargs)
    assert block.interpretation_policy.default_route != "main_reading", block_type
    # Metadata-only blocks must never claim rag_eligible=True either;
    # the per-block-type defaults are metadata_only for image / unknown
    # (rag_eligible=False). main_reading is the route that would let
    # the block leak into the main grammar pass; table / table_row /
    # table_cell / code_block legitimately default to main_reading
    # since the Markdown ecosystem refactor (D2 / A1), so they are no
    # longer part of this safety net.


def test_non_empty_dict_explicit_policy_is_preserved() -> None:
    """Non-empty dicts (any key set) MUST be treated as an explicit
    caller policy and preserved verbatim — even when the dict happens
    to specify a default-shaped value. This is the Candidate Document
    confirm path: an editor can promote an image_ocr into the main
    reading chain by passing `{'default_route': 'main_reading', ...}`.
    """
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "ocr-promoted",
            "order_index": 0,
            "block_type": "image_ocr",  # default would be rag_ask_only
            "text_content": "OCR text the user promoted.",
            "interpretation_policy": {
                "default_route": "main_reading",
                "allowed_source_scope": ["main_reading_text"],
                "rag_eligible": True,
                "notes": ["promoted by user via Candidate confirm"],
            },
        }
    )
    assert block.interpretation_policy.default_route == "main_reading"
    assert block.interpretation_policy.allowed_source_scope == ["main_reading_text"]
    assert block.interpretation_policy.rag_eligible is True
    assert block.interpretation_policy.notes == [
        "promoted by user via Candidate confirm"
    ]


def test_explicit_policy_instance_is_preserved_with_empty_dict_storage_default() -> None:
    """A typed `StableDocumentInterpretationPolicy` instance is the
    strongest form of explicit-policy signal and must be preserved
    verbatim, regardless of block_type. Pins the "not a dict, not None,
    not empty -> preserve" branch.
    """
    explicit_policy = StableDocumentInterpretationPolicy(
        allowed_source_scope=["heading"],
        default_route="ignored",
        rag_eligible=False,
    )
    block = StableDocumentBlock.model_validate(
        {
            "block_id": "any-block",
            "order_index": 0,
            "block_type": "footnote",  # default would be rag_ask_only
            "text_content": "footnote body",
            "interpretation_policy": explicit_policy,
        }
    )
    assert block.interpretation_policy is explicit_policy
    assert block.interpretation_policy.default_route == "ignored"
    assert block.interpretation_policy.allowed_source_scope == ["heading"]
    assert block.interpretation_policy.rag_eligible is False


def test_content_sha256_differs_for_empty_dict_vs_explicit_main_reading() -> None:
    """End-to-end check: an empty-dict interpretation_policy (storage
    placeholder) and an explicit main_reading dict MUST hash
    differently for the same block_type. This catches the regression
    where the before-validator accidentally treats `{}` as an explicit
    policy and silently routes the block into main_reading.
    """
    storage_default_sha = compute_stable_document_content_sha256(
        [
            StableDocumentBlock.model_validate(
                {
                    "block_id": "x",
                    "order_index": 0,
                    "block_type": "table_cell",
                    "text_content": "cell",
                    "interpretation_policy": {},
                }
            )
        ]
    )
    explicit_main_reading_sha = compute_stable_document_content_sha256(
        [
            StableDocumentBlock.model_validate(
                {
                    "block_id": "x",
                    "order_index": 0,
                    "block_type": "table_cell",
                    "text_content": "cell",
                    "interpretation_policy": {
                        "default_route": "main_reading",
                        "allowed_source_scope": ["main_reading_text"],
                        "rag_eligible": True,
                    },
                }
            )
        ]
    )
    explicit_default_sha = compute_stable_document_content_sha256(
        [
            StableDocumentBlock.model_validate(
                {
                    "block_id": "x",
                    "order_index": 0,
                    "block_type": "table_cell",
                    "text_content": "cell",
                    # No interpretation_policy key at all -> default path.
                }
            )
        ]
    )
    # Empty-dict storage placeholder must hash like the default,
    # NOT like explicit main_reading.
    assert storage_default_sha == explicit_default_sha
    assert storage_default_sha != explicit_main_reading_sha


# --------------------------------------------------------------------
# DB / Python contract alignment: storage default vs Python default
# --------------------------------------------------------------------


def test_migration_documents_storage_default_split() -> None:
    """The migration's `interpretation_policy_json DEFAULT '{}'::jsonb`
    is a STORAGE placeholder only. The migration comment must explain
    that D6-I2 service code is responsible for writing the
    Python-model-generated per-block-type policy into the column, so
    the DB default is never relied on at runtime. This guards against
    future readers "fixing" the DB default to match the Python
    default and silently coupling storage defaults to projection
    rules.
    """
    sql = _migration_sql()
    assert re.search(
        r"interpretation_policy_json\s+JSONB\s+(?:NOT\s+NULL\s+)?DEFAULT\s+'\{\}'::jsonb\s+NOT\s+NULL",
        sql,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "expected storage-default placeholder on interpretation_policy_json"
    # The migration comment must explicitly call out the split: DB
    # default is storage-only; D6-I2 service must persist the Python
    # model-generated policy.
    lower = sql.lower()
    assert "d6-i2" in lower, (
        "migration comment must name D6-I2 as the layer that persists "
        "the Python-model-generated policy into interpretation_policy_json"
    )
    assert "default_interpretation_policy_for" in lower, (
        "migration comment must reference the Python helper that "
        "produces the per-block-type default policy"
    )
    assert "storage placeholder" in lower or "storage default" in lower, (
        "migration comment must mark the DB DEFAULT as a storage "
        "placeholder rather than a runtime policy source"
    )