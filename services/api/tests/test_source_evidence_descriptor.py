"""Tests for SourceEvidenceDescriptor adapter (M3 stage C, C3).

Contract: docs/initiatives/reader-agentic-orchestration/modules/
ask-claread-agentic-product-runtime-contract.md (accepted, 2026-07-25).

Covers:
  * §3.5.1.2 — chunk_qualifies_for_descriptor 4 AND conditions +
    frozen field-read source (default_route / block_type MUST come from
    ArticleRagIndexChunk.metadata_json; missing / wrong-type /
    out-of-allowlist → fail-closed, no document re-query).
  * §3.2 — SourceEvidenceDescriptor / DescriptorParentContext field
    shape; parent_context defaults to all-None (no re-query path).
  * §3.3 — expansion_text assembly rules + fail-closed fallback
    (table_cell column_name path + neutral prefix; code_block raw text;
    footnote without structured footnote_id → None).
  * §5.4.4 — display label rules (table_cell: column_name or 表格单元格;
    code_block: 代码 or 代码: {language}; footnote: always 脚注;
    footnote_id never enters label).
  * §3.5.1.3 + §5.4 — descriptor_to_candidate_source conversion
    (heading per §5.4.4, window_text = expansion_text; parent_context
    digested — does not appear on ArticleMapEntrySource).
  * §5.4.1 / §5.4.2 — build_descriptor_candidates sort key
    (source_kind_rank=1, canonical_order_index, stable_block_id) +
    hard cap 8 (drop tail; no replacement from main_reading).
  * §3.4 preflight — chunk with no block_ids fail-closed.
  * §5.1 4 — adapter does not splice locator / hash / range / block_id /
    chunk_id / plan_hash / utf16 / generation / record_id into
    expansion_text (only original content + neutral prefix allowed).
  * §5.1 5 — parent_context is server-only; not present on
    ArticleMapEntrySource after conversion.

No DB, no asyncpg, no embedding, no Zilliz. In-memory plan / chunk
helpers mirror test_d6_i4e_article_rag_retrieval_service.py.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
)
from app.services.reader_orchestration.source_evidence_descriptor import (
    ALLOWED_DESCRIPTOR_BLOCK_TYPES,
    DESCRIPTOR_DEFAULT_ROUTE,
    DESCRIPTOR_HARD_CAP,
    DescriptorParentContext,
    SourceEvidenceDescriptor,
    build_descriptor_candidates,
    build_descriptor_from_chunk,
    build_descriptor_label,
    build_expansion_text,
    chunk_qualifies_for_descriptor,
    descriptor_to_candidate_source,
)
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("11111111-1111-1111-1111-111111111111")
_STABLE_DOC_ID = UUID("22222222-2222-2222-2222-222222222222")
_BASE_ID = UUID("33333333-3333-3333-3333-333333333333")
_OTHER_STABLE_DOC_ID = UUID("44444444-4444-4444-4444-444444444444")
_PLAN_CONTENT_SHA = hashlib.sha256(b"plan-content-stable").hexdigest()
_CANON_TEXT_SHA = hashlib.sha256(b"canonical-text").hexdigest()


# ---------------------------------------------------------------------------
# In-memory chunk / plan helpers (no DB, no asyncpg, no embedding)
# ---------------------------------------------------------------------------


def _make_citation(
    *,
    stable_document_id: UUID = _STABLE_DOC_ID,
    base_id: UUID = _BASE_ID,
    block_ids: tuple[str, ...] = ("block-1",),
    canonical_start: int | None = None,
    canonical_end: int | None = None,
) -> ArticleRagCitationRef:
    return ArticleRagCitationRef(
        reading_record_id=_RECORD_ID,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=1,
        block_ids=block_ids,
        unit_ids=(),
        anchor_segment_ids=(),
        canonical_text_start_utf16=canonical_start,
        canonical_text_end_utf16=canonical_end,
    )


def _make_chunk(
    chunk_id: str,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    block_ids: tuple[str, ...] = ("block-1",),
    canonical_start: int | None = None,
    canonical_end: int | None = None,
) -> ArticleRagIndexChunk:
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagIndexChunk(
        chunk_id=chunk_id,
        citation=_make_citation(
            block_ids=block_ids,
            canonical_start=canonical_start,
            canonical_end=canonical_end,
        ),
        source_scope="main_reading_text",
        text=text,
        content_sha256=content_sha,
        embedding_text_sha256=content_sha,
        metadata_json=(
            metadata
            if metadata is not None
            else {
                "block_type": "paragraph",
                "block_order_index": 0,
                "source_scope": "main_reading_text",
                "default_route": "main_reading",
                "chunk_index": 0,
                "has_canonical_offsets": True,
            }
        ),
    )


def _make_plan(
    *,
    chunks: tuple[ArticleRagIndexChunk, ...] | None = None,
    stable_document_id: UUID = _STABLE_DOC_ID,
    content_sha256: str = _PLAN_CONTENT_SHA,
) -> ArticleRagIndexPlan:
    cs = chunks or ()
    return ArticleRagIndexPlan(
        reading_record_id=_RECORD_ID,
        stable_document_id=stable_document_id,
        base_id=_BASE_ID,
        record_generation=1,
        content_sha256=content_sha256,
        canonical_text_sha256=_CANON_TEXT_SHA,
        chunks=cs,
    )


def _rag_ask_metadata(
    *,
    block_type: str,
    block_order_index: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """metadata_json that qualifies a chunk for descriptor generation."""
    md: dict[str, Any] = {
        "block_type": block_type,
        "block_order_index": block_order_index,
        "source_scope": "main_reading_text",
        "default_route": "rag_ask_only",
        "chunk_index": 0,
        "has_canonical_offsets": False,
    }
    if extra:
        md.update(extra)
    return md


# ---------------------------------------------------------------------------
# §3.5.1.2 — chunk_qualifies_for_descriptor (4 AND conditions)
# ---------------------------------------------------------------------------


class TestChunkQualifiesForDescriptor:
    """§3.5.1.2 — 4 AND conditions, all read from chunk.metadata_json."""

    def test_table_cell_qualifies(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "cell text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is True

    def test_code_block_qualifies(self) -> None:
        chunk = _make_chunk(
            "c-cb",
            "print('hi')",
            metadata=_rag_ask_metadata(block_type="code_block"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is True

    def test_footnote_qualifies_at_filter_stage(self) -> None:
        # footnote passes the filter; the §3.3 expansion_text stage
        # fail-closes when footnote_id is None (covered separately).
        chunk = _make_chunk(
            "c-fn",
            "footnote body",
            metadata=_rag_ask_metadata(block_type="footnote"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is True

    def test_main_reading_default_route_does_not_qualify(self) -> None:
        chunk = _make_chunk(
            "c-para",
            "paragraph text",
            metadata={
                "block_type": "paragraph",
                "block_order_index": 0,
                "default_route": "main_reading",
            },
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_image_ocr_block_type_not_in_allowlist(self) -> None:
        # §3.5.1.2: image_ocr may have default_route="rag_ask_only" but
        # block_type is not in allowlist → must not qualify.
        chunk = _make_chunk(
            "c-img",
            "ocr text",
            metadata=_rag_ask_metadata(block_type="image_ocr"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_unknown_block_type_does_not_qualify(self) -> None:
        chunk = _make_chunk(
            "c-unk",
            "unknown",
            metadata=_rag_ask_metadata(block_type="some_future_type"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_canonical_start_non_none_fail_closed(self) -> None:
        # §3.5.1.2 condition 3: rag_ask_only must have canonical range
        # both None. Non-None indicates data inconsistency.
        chunk = _make_chunk(
            "c-bad",
            "text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
            canonical_start=0,
            canonical_end=None,
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_canonical_end_non_none_fail_closed(self) -> None:
        chunk = _make_chunk(
            "c-bad",
            "text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
            canonical_start=None,
            canonical_end=10,
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_empty_text_does_not_qualify(self) -> None:
        chunk = _make_chunk(
            "c-empty",
            "",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_whitespace_only_text_does_not_qualify(self) -> None:
        chunk = _make_chunk(
            "c-ws",
            "   \n  ",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        assert chunk_qualifies_for_descriptor(chunk) is False


# ---------------------------------------------------------------------------
# §3.5.1.2 — frozen field-read source (metadata_json only)
# ---------------------------------------------------------------------------


class TestFrozenFieldReadSource:
    """§3.5.1.2 — default_route / block_type MUST come from metadata_json.

    Missing key, wrong type, or value outside allowlist → fail-closed.
    The adapter MUST NOT re-query the document to fill the gap.
    """

    def test_missing_default_route_key_fail_closed(self) -> None:
        chunk = _make_chunk(
            "c-missing-route",
            "text",
            metadata={
                "block_type": "table_cell",
                "block_order_index": 0,
            },
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_missing_block_type_key_fail_closed(self) -> None:
        chunk = _make_chunk(
            "c-missing-bt",
            "text",
            metadata={
                "default_route": "rag_ask_only",
                "block_order_index": 0,
            },
        )
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_default_route_wrong_type_fail_closed(self) -> None:
        # default_route must be str; int / list / None → fail-closed.
        for bad in (123, ["rag_ask_only"], None, True):
            chunk = _make_chunk(
                f"c-bad-route-{bad!r}",
                "text",
                metadata={
                    "block_type": "table_cell",
                    "block_order_index": 0,
                    "default_route": bad,
                },
            )
            assert chunk_qualifies_for_descriptor(chunk) is False, (
                f"default_route={bad!r} should fail-closed"
            )

    def test_block_type_wrong_type_fail_closed(self) -> None:
        for bad in (123, ["table_cell"], None, True):
            chunk = _make_chunk(
                f"c-bad-bt-{bad!r}",
                "text",
                metadata={
                    "block_type": bad,
                    "block_order_index": 0,
                    "default_route": "rag_ask_only",
                },
            )
            assert chunk_qualifies_for_descriptor(chunk) is False, (
                f"block_type={bad!r} should fail-closed"
            )

    def test_metadata_not_dict_fail_closed(self) -> None:
        # Defensive: if metadata_json is somehow not a dict, fail-closed.
        chunk = _make_chunk(
            "c-bad-md",
            "text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        # Replace metadata_json with a non-dict to simulate corruption.
        object.__setattr__(chunk, "metadata_json", "not-a-dict")  # type: ignore[misc]
        assert chunk_qualifies_for_descriptor(chunk) is False

    def test_allowlist_constants_frozen(self) -> None:
        # §3.5.1.2: allowlist is exactly {table_cell, code_block, footnote}.
        assert ALLOWED_DESCRIPTOR_BLOCK_TYPES == frozenset(
            {"table_cell", "code_block", "footnote"}
        )
        assert DESCRIPTOR_DEFAULT_ROUTE == "rag_ask_only"

    def test_no_document_requery_method_exists(self) -> None:
        # §3.5.1.2 forbids re-querying the document. The module must
        # not expose any helper that takes a stable_document_id / base_id
        # and re-loads block metadata. Verify by inspecting public API.
        from app.services.reader_orchestration import source_evidence_descriptor as mod

        public_names = set(mod.__all__)
        # No function name suggests document re-query.
        for name in public_names:
            assert "query" not in name.lower(), name
            assert "fetch" not in name.lower(), name
            assert "load" not in name.lower(), name
            assert "reload" not in name.lower(), name


# ---------------------------------------------------------------------------
# §3.3 — expansion_text assembly rules
# ---------------------------------------------------------------------------


class TestExpansionText:
    """§3.3 — assembly rules + fail-closed fallback."""

    def test_table_cell_with_column_name(self) -> None:
        text = build_expansion_text(
            block_type="table_cell",
            chunk_text="42",
            parent_context=DescriptorParentContext(column_name="Revenue"),
        )
        assert text == "Revenue: 42"

    def test_table_cell_column_name_whitespace_only_falls_back(self) -> None:
        # §3.3: column_name present but empty/whitespace → neutral prefix.
        text = build_expansion_text(
            block_type="table_cell",
            chunk_text="42",
            parent_context=DescriptorParentContext(column_name="   "),
        )
        assert text == "表格单元格: 42"

    def test_table_cell_without_column_name_uses_neutral_prefix(self) -> None:
        text = build_expansion_text(
            block_type="table_cell",
            chunk_text="42",
            parent_context=DescriptorParentContext(),
        )
        assert text == "表格单元格: 42"

    def test_code_block_returns_raw_text(self) -> None:
        code = "def f():\n    return 1"
        text = build_expansion_text(
            block_type="code_block",
            chunk_text=code,
            parent_context=DescriptorParentContext(),
        )
        assert text == code

    def test_code_block_language_does_not_affect_expansion_text(self) -> None:
        # §3.3: language only affects label, not expansion_text.
        code = "SELECT 1"
        text_with_lang = build_expansion_text(
            block_type="code_block",
            chunk_text=code,
            parent_context=DescriptorParentContext(language="sql"),
        )
        text_without_lang = build_expansion_text(
            block_type="code_block",
            chunk_text=code,
            parent_context=DescriptorParentContext(),
        )
        assert text_with_lang == text_without_lang == code

    def test_footnote_without_footnote_id_returns_none(self) -> None:
        # §3.3 fail-closed: footnote requires structured footnote relation.
        # parent_context.footnote_id is None by default (no re-query).
        text = build_expansion_text(
            block_type="footnote",
            chunk_text="footnote body",
            parent_context=DescriptorParentContext(),
        )
        assert text is None

    def test_footnote_with_footnote_id_returns_body_text(self) -> None:
        # Hypothetical future: parser preserved structured relation.
        # The adapter still does NOT regex-strip the marker (§3.3).
        text = build_expansion_text(
            block_type="footnote",
            chunk_text="footnote body",
            parent_context=DescriptorParentContext(footnote_id="fn-1"),
        )
        assert text == "footnote body"

    def test_original_content_preserved_in_expansion_text(self) -> None:
        # §3.3 / §5.1 4: strings naturally present in user content
        # (UUIDs, years, numbers) must be preserved.
        for original in (
            "Record id 550e8400-e29b-41d4-a716-446655440000 here",
            "Year 2026 revenue",
            "Coordinates 40.7128, -74.0060",
        ):
            text = build_expansion_text(
                block_type="code_block",
                chunk_text=original,
                parent_context=DescriptorParentContext(),
            )
            assert original in text


# ---------------------------------------------------------------------------
# §5.4.4 — display label rules
# ---------------------------------------------------------------------------


class TestDescriptorLabel:
    """§5.4.4 — label rules; footnote_id never enters label."""

    def test_table_cell_with_column_name_label(self) -> None:
        label = build_descriptor_label(
            block_type="table_cell",
            parent_context=DescriptorParentContext(column_name="Revenue"),
        )
        assert label == "Revenue"

    def test_table_cell_column_name_whitespace_falls_back(self) -> None:
        label = build_descriptor_label(
            block_type="table_cell",
            parent_context=DescriptorParentContext(column_name="  "),
        )
        assert label == "表格单元格"

    def test_table_cell_without_column_name_neutral_label(self) -> None:
        label = build_descriptor_label(
            block_type="table_cell",
            parent_context=DescriptorParentContext(),
        )
        assert label == "表格单元格"

    def test_code_block_without_language_label(self) -> None:
        label = build_descriptor_label(
            block_type="code_block",
            parent_context=DescriptorParentContext(),
        )
        assert label == "代码"

    def test_code_block_with_language_label(self) -> None:
        label = build_descriptor_label(
            block_type="code_block",
            parent_context=DescriptorParentContext(language="python"),
        )
        assert label == "代码: python"

    def test_code_block_language_whitespace_falls_back(self) -> None:
        label = build_descriptor_label(
            block_type="code_block",
            parent_context=DescriptorParentContext(language="  "),
        )
        assert label == "代码"

    def test_footnote_label_always_literal(self) -> None:
        # §5.4.4 v4 frozen: footnote label is always "脚注",
        # footnote_id never enters label.
        label_with_id = build_descriptor_label(
            block_type="footnote",
            parent_context=DescriptorParentContext(footnote_id="fn-internal-1"),
        )
        label_without_id = build_descriptor_label(
            block_type="footnote",
            parent_context=DescriptorParentContext(),
        )
        assert label_with_id == label_without_id == "脚注"

    def test_label_never_contains_server_side_metadata(self) -> None:
        # §5.1 23: label must not contain block_id / chunk_id / plan_hash /
        # utf16 / record_id / footnote_id.
        for bt in ("table_cell", "code_block", "footnote"):
            label = build_descriptor_label(
                block_type=bt,  # type: ignore[arg-type]
                parent_context=DescriptorParentContext(
                    column_name="col",
                    language="py",
                    footnote_id="fn-1",
                    row_index=3,
                ),
            )
            assert "block_id" not in label
            assert "chunk_id" not in label
            assert "plan_hash" not in label
            assert "utf16" not in label.lower()
            assert "record_id" not in label
            assert "fn-1" not in label
            assert "fn-internal" not in label


# ---------------------------------------------------------------------------
# §3.2 / §3.5.1.2 — build_descriptor_from_chunk (fail-closed paths)
# ---------------------------------------------------------------------------


class TestBuildDescriptorFromChunk:
    """§3.2 / §3.5.1.2 — descriptor construction with fail-closed."""

    def test_table_cell_descriptor_built(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        assert desc.block_type == "table_cell"
        assert desc.expansion_text == "表格单元格: 42"
        assert desc.block_id == "block-1"
        assert desc.source_content_sha256 == _PLAN_CONTENT_SHA
        assert desc.reading_record_id == str(_RECORD_ID)
        assert desc.stable_document_id == str(_STABLE_DOC_ID)
        assert desc.base_id == str(_BASE_ID)
        assert desc.record_generation == 1
        # parent_context defaults: all None (no re-query).
        assert desc.parent_context.column_name is None
        assert desc.parent_context.row_index is None
        assert desc.parent_context.language is None
        assert desc.parent_context.footnote_id is None

    def test_code_block_descriptor_built(self) -> None:
        chunk = _make_chunk(
            "c-cb",
            "print('hi')",
            metadata=_rag_ask_metadata(block_type="code_block"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        assert desc.block_type == "code_block"
        assert desc.expansion_text == "print('hi')"

    def test_footnote_fail_closed_because_no_footnote_id(self) -> None:
        # §3.3: footnote requires structured footnote_id; provider does
        # not re-query document, so parent_context.footnote_id is None
        # → build_expansion_text returns None → no descriptor.
        chunk = _make_chunk(
            "c-fn",
            "footnote body",
            metadata=_rag_ask_metadata(block_type="footnote"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is None

    def test_missing_block_order_index_fail_closed(self) -> None:
        # §5.4.1 sort key needs block_order_index; missing → fail-closed.
        chunk = _make_chunk(
            "c-no-order",
            "text",
            metadata={
                "block_type": "table_cell",
                "default_route": "rag_ask_only",
                # block_order_index intentionally absent
            },
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is None

    def test_block_order_index_wrong_type_fail_closed(self) -> None:
        for bad in ("0", 0.0, True, None, [0]):
            chunk = _make_chunk(
                f"c-bad-oi-{bad!r}",
                "text",
                metadata={
                    "block_type": "table_cell",
                    "default_route": "rag_ask_only",
                    "block_order_index": bad,
                },
            )
            plan = _make_plan(chunks=(chunk,))
            desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
            assert desc is None, f"block_order_index={bad!r} should fail-closed"

    def test_block_order_index_bool_rejected(self) -> None:
        # bool is a subclass of int in Python but is not a valid order_index.
        chunk = _make_chunk(
            "c-bool-oi",
            "text",
            metadata={
                "block_type": "table_cell",
                "default_route": "rag_ask_only",
                "block_order_index": True,
            },
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is None

    def test_empty_block_ids_fail_closed(self) -> None:
        # §3.4 preflight check 2: block locator must be valid.
        chunk = _make_chunk(
            "c-no-blocks",
            "text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
            block_ids=(),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is None

    def test_empty_block_id_string_fail_closed(self) -> None:
        chunk = _make_chunk(
            "c-empty-block-id",
            "text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
            block_ids=("",),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is None

    def test_descriptor_is_frozen(self) -> None:
        # §3.2: frozen dataclass — immutable.
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        with pytest.raises((AttributeError, Exception)):
            desc.block_type = "code_block"  # type: ignore[misc]

    def test_descriptor_parent_context_is_frozen(self) -> None:
        ctx = DescriptorParentContext(column_name="col")
        with pytest.raises((AttributeError, Exception)):
            ctx.column_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §3.5.1.3 / §5.4.4 — descriptor_to_candidate_source conversion
# ---------------------------------------------------------------------------


class TestDescriptorToCandidateSource:
    """§3.5.1.3 / §5.4.4 — convert descriptor to ArticleMapEntrySource."""

    def test_table_cell_candidate_has_neutral_label_and_expansion_text(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        candidate = descriptor_to_candidate_source(desc)
        assert isinstance(candidate, ArticleMapEntrySource)
        assert candidate.heading == "表格单元格"
        assert candidate.window_text == "表格单元格: 42"

    def test_code_block_candidate_has_code_label(self) -> None:
        chunk = _make_chunk(
            "c-cb",
            "print('hi')",
            metadata=_rag_ask_metadata(block_type="code_block"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        candidate = descriptor_to_candidate_source(desc)
        assert candidate.heading == "代码"
        assert candidate.window_text == "print('hi')"

    def test_candidate_does_not_carry_parent_context(self) -> None:
        # §5.1 5: parent_context is digested after conversion. The
        # ArticleMapEntrySource has only heading + window_text fields.
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        candidate = descriptor_to_candidate_source(desc)
        # ArticleMapEntrySource only has heading + window_text — verify
        # no parent_context-like field leaks.
        fields = {f.name for f in candidate.__dataclass_fields__.values()}
        assert fields == {"heading", "window_text"}

    def test_candidate_does_not_carry_server_side_metadata(self) -> None:
        # §5.1 23: candidate must not carry block_id / chunk_id /
        # plan_hash / utf16 / record_id / footnote_id / source_content_sha256.
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        candidate = descriptor_to_candidate_source(desc)
        # Verify heading + window_text do not leak server-side ids.
        leaked = [
            str(desc.block_id),
            str(desc.source_content_sha256),
            str(desc.reading_record_id),
            str(desc.stable_document_id),
            str(desc.base_id),
        ]
        # footnote_id never appears because footnote is fail-closed
        # in this round; still verify defensively.
        if desc.parent_context.footnote_id is not None:
            leaked.append(desc.parent_context.footnote_id)
        for value in leaked:
            assert value not in (candidate.heading or "")
            assert value not in (candidate.window_text or "")


# ---------------------------------------------------------------------------
# §5.4.1 / §5.4.2 — build_descriptor_candidates (sort + hard cap 8)
# ---------------------------------------------------------------------------


class TestBuildDescriptorCandidates:
    """§5.4.1 deterministic sort + §5.4.2 hard cap 8."""

    def test_empty_plan_returns_empty_tuple(self) -> None:
        plan = _make_plan(chunks=())
        candidates = build_descriptor_candidates(plan=plan)
        assert candidates == ()

    def test_no_qualifying_chunks_returns_empty_tuple(self) -> None:
        # All chunks are main_reading → no descriptors.
        chunks = (
            _make_chunk(
                "c-1",
                "para 1",
                metadata={
                    "block_type": "paragraph",
                    "block_order_index": 0,
                    "default_route": "main_reading",
                },
            ),
            _make_chunk(
                "c-2",
                "para 2",
                metadata={
                    "block_type": "paragraph",
                    "block_order_index": 1,
                    "default_route": "main_reading",
                },
            ),
        )
        plan = _make_plan(chunks=chunks)
        candidates = build_descriptor_candidates(plan=plan)
        assert candidates == ()

    def test_single_table_cell_candidate(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        assert candidates[0].heading == "表格单元格"
        assert candidates[0].window_text == "表格单元格: 42"

    def test_sort_by_canonical_order_index(self) -> None:
        # Two table_cell chunks with different block_order_index.
        # Even though inserted out-of-order, output must be sorted.
        chunk_b = _make_chunk(
            "c-tc-b",
            "B",
            metadata=_rag_ask_metadata(block_type="table_cell", block_order_index=5),
            block_ids=("block-b",),
        )
        chunk_a = _make_chunk(
            "c-tc-a",
            "A",
            metadata=_rag_ask_metadata(block_type="table_cell", block_order_index=1),
            block_ids=("block-a",),
        )
        plan = _make_plan(chunks=(chunk_b, chunk_a))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 2
        # §5.4.1: lower canonical_order_index comes first.
        assert candidates[0].window_text == "表格单元格: A"
        assert candidates[1].window_text == "表格单元格: B"

    def test_sort_tie_break_by_stable_block_id(self) -> None:
        # Same block_order_index → tie-break by stable_block_id dict order.
        chunk_z = _make_chunk(
            "c-tc-z",
            "Z",
            metadata=_rag_ask_metadata(block_type="table_cell", block_order_index=2),
            block_ids=("block-z",),
        )
        chunk_a = _make_chunk(
            "c-tc-a",
            "A",
            metadata=_rag_ask_metadata(block_type="table_cell", block_order_index=2),
            block_ids=("block-a",),
        )
        plan = _make_plan(chunks=(chunk_z, chunk_a))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 2
        # block-a < block-z lexicographically.
        assert candidates[0].window_text == "表格单元格: A"
        assert candidates[1].window_text == "表格单元格: Z"

    def test_hard_cap_8_drops_tail(self) -> None:
        # §5.4.2: descriptor source hard cap 8; overflow drops tail.
        chunks = tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"text-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-{i:02d}",),
            )
            for i in range(15)  # 15 > 8
        )
        plan = _make_plan(chunks=chunks)
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == DESCRIPTOR_HARD_CAP == 8
        # First 8 by (block_order_index, block_id) — indices 0..7.
        for i, candidate in enumerate(candidates):
            assert candidate.window_text == f"表格单元格: text-{i}"

    def test_hard_cap_does_not_replace_from_main_reading(self) -> None:
        # §5.4.3: when descriptor overflow, main_reading chunks must NOT
        # fill the gap. Main_reading chunks are never converted anyway,
        # but verify the contract: 0 main_reading chunks → still cap 8.
        main_chunks = tuple(
            _make_chunk(
                f"c-para-{i}",
                f"para-{i}",
                metadata={
                    "block_type": "paragraph",
                    "block_order_index": i,
                    "default_route": "main_reading",
                },
            )
            for i in range(20)
        )
        desc_chunks = tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"tc-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-tc-{i:02d}",),
            )
            for i in range(10)
        )
        plan = _make_plan(chunks=main_chunks + desc_chunks)
        candidates = build_descriptor_candidates(plan=plan)
        # Only descriptors, capped at 8; main_reading not in output.
        assert len(candidates) == 8
        for c in candidates:
            assert c.window_text is not None
            assert c.window_text.startswith("表格单元格: tc-")

    def test_exactly_eight_descriptors_not_truncated(self) -> None:
        chunks = tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"text-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-{i:02d}",),
            )
            for i in range(8)
        )
        plan = _make_plan(chunks=chunks)
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 8

    def test_returns_frozen_tuple(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert isinstance(candidates, tuple)


# ---------------------------------------------------------------------------
# §5.1 4 — adapter does not splice server-side metadata
# ---------------------------------------------------------------------------


class TestNoMetadataSplicing:
    """§5.1 4 / §3.3 — adapter must not splice locator / hash / range /
    block_id / chunk_id / plan_hash / utf16 / generation / record_id
    into expansion_text."""

    def test_expansion_text_does_not_contain_block_id(self) -> None:
        chunk = _make_chunk(
            "c-tc-secret-block",
            "cell text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
            block_ids=("block-leak-me",),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        # block_id must not appear in heading or window_text.
        assert "block-leak-me" not in (candidates[0].heading or "")
        assert "block-leak-me" not in (candidates[0].window_text or "")

    def test_expansion_text_does_not_contain_record_id(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "cell text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        assert str(_RECORD_ID) not in (candidates[0].heading or "")
        assert str(_RECORD_ID) not in (candidates[0].window_text or "")

    def test_expansion_text_does_not_contain_plan_hash(self) -> None:
        chunk = _make_chunk(
            "c-tc",
            "cell text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        # plan.content_sha256 is the source_content_sha256 of descriptor.
        assert _PLAN_CONTENT_SHA not in (candidates[0].heading or "")
        assert _PLAN_CONTENT_SHA not in (candidates[0].window_text or "")

    def test_expansion_text_does_not_contain_utf16_offsets(self) -> None:
        # rag_ask_only chunks have canonical range = None, but verify
        # the literal string "utf16" never appears.
        chunk = _make_chunk(
            "c-tc",
            "cell text",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        assert "utf16" not in (candidates[0].heading or "").lower()
        assert "utf16" not in (candidates[0].window_text or "").lower()


# ---------------------------------------------------------------------------
# §3.2 — SourceEvidenceDescriptor / DescriptorParentContext shape
# ---------------------------------------------------------------------------


class TestDescriptorShape:
    """§3.2 — field shape frozen by contract."""

    def test_descriptor_parent_context_defaults_all_none(self) -> None:
        ctx = DescriptorParentContext()
        assert ctx.column_name is None
        assert ctx.row_index is None
        assert ctx.language is None
        assert ctx.footnote_id is None

    def test_descriptor_parent_context_explicit_construction(self) -> None:
        ctx = DescriptorParentContext(
            column_name="Revenue",
            row_index=3,
            language="python",
            footnote_id="fn-1",
        )
        assert ctx.column_name == "Revenue"
        assert ctx.row_index == 3
        assert ctx.language == "python"
        assert ctx.footnote_id == "fn-1"

    def test_descriptor_fields_frozen_by_contract(self) -> None:
        # §3.2 — exact field set frozen.
        fields = {
            f.name for f in SourceEvidenceDescriptor.__dataclass_fields__.values()
        }
        assert fields == {
            "reading_record_id",
            "stable_document_id",
            "base_id",
            "record_generation",
            "source_content_sha256",
            "block_id",
            "block_type",
            "expansion_text",
            "parent_context",
        }

    def test_descriptor_parent_context_fields_frozen_by_contract(self) -> None:
        fields = {
            f.name for f in DescriptorParentContext.__dataclass_fields__.values()
        }
        assert fields == {
            "column_name",
            "row_index",
            "language",
            "footnote_id",
        }


# ---------------------------------------------------------------------------
# §3.5.1.3 / §5.1 25 — candidate semantics (no visibility guarantee)
# ---------------------------------------------------------------------------


class TestCandidateSemantics:
    """§3.5.1.3 / §5.1 25 — descriptor is a candidate; no visibility
    guarantee; cost-fit may silently drop."""

    def test_candidate_is_plain_article_map_entry_source(self) -> None:
        # The candidate carries only heading + window_text — no
        # visibility flag, no cursor, no ledger marker. The Ask owner's
        # assemble_article_map decides visibility via cost-fit.
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,))
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert isinstance(candidate, ArticleMapEntrySource)
        # No visibility / cursor / marker fields on ArticleMapEntrySource.
        fields = {f.name for f in candidate.__dataclass_fields__.values()}
        assert "visible" not in fields
        assert "cursor" not in fields
        assert "marker" not in fields
        assert "ledger" not in fields

    def test_candidates_are_independent_objects(self) -> None:
        # Each candidate is a fresh ArticleMapEntrySource; no shared
        # mutable state between them.
        chunks = tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"text-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-{i:02d}",),
            )
            for i in range(3)
        )
        plan = _make_plan(chunks=chunks)
        candidates = build_descriptor_candidates(plan=plan)
        assert len(candidates) == 3
        # Mutating one must not affect others (defensive; frozen anyway).
        ids = [id(c) for c in candidates]
        assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# §5.1 9 / §3.2 — descriptor does not carry RAG citation provenance
# ---------------------------------------------------------------------------


class TestNoRagProvenance:
    """§5.1 9 — descriptor does not carry RAG citation provenance to
    final evidence. index_run_id / plan_content_sha256 intentionally
    excluded (rag_ask_only does not go through generic RAG index)."""

    def test_descriptor_has_no_index_run_id_field(self) -> None:
        fields = {
            f.name for f in SourceEvidenceDescriptor.__dataclass_fields__.values()
        }
        assert "index_run_id" not in fields
        assert "plan_content_sha256" not in fields
        assert "rag_substrate_id" not in fields

    def test_source_content_sha256_uses_plan_content_sha256(self) -> None:
        # §3.2: source_content_sha256 anchors to stable-document content,
        # NOT to RAG index/run/plan provenance.
        chunk = _make_chunk(
            "c-tc",
            "42",
            metadata=_rag_ask_metadata(block_type="table_cell"),
        )
        plan = _make_plan(chunks=(chunk,), content_sha256=_PLAN_CONTENT_SHA)
        desc = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        assert desc is not None
        assert desc.source_content_sha256 == _PLAN_CONTENT_SHA


# ---------------------------------------------------------------------------
# Module API smoke — public __all__ contract
# ---------------------------------------------------------------------------


def test_module_all_exports_match_contract() -> None:
    """The module's __all__ must export exactly the contract surface."""
    from app.services.reader_orchestration import source_evidence_descriptor as mod

    expected = {
        "ALLOWED_DESCRIPTOR_BLOCK_TYPES",
        "DESCRIPTOR_DEFAULT_ROUTE",
        "DESCRIPTOR_HARD_CAP",
        "DescriptorParentContext",
        "SourceEvidenceDescriptor",
        "build_descriptor_candidates",
        "build_descriptor_from_chunk",
        "build_descriptor_label",
        "build_expansion_text",
        "chunk_qualifies_for_descriptor",
        "descriptor_to_candidate_source",
    }
    assert set(mod.__all__) == expected


def test_module_does_not_import_ledger_or_assemble() -> None:
    """§3.5.1.3: adapter does not call ledger.issue or assemble_article_map.

    Verify by inspecting imports — the module must not import
    ExpansionPointerLedger / EvidenceRegistry / assemble_article_map.
    """
    from app.services.reader_orchestration import source_evidence_descriptor as mod

    src = inspect.getsource(mod)
    # The module imports ArticleMapEntrySource (for type only) but must
    # not import the assembly / ledger machinery.
    assert "ExpansionPointerLedger" not in src
    assert "EvidenceRegistry" not in src
    assert "assemble_article_map" not in src
    assert "ledger.issue" not in src
