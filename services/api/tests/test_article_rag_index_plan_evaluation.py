"""P2-B: Article RAG V1/V2a Offline Plan Comparison Harness tests.

Section A: Tracer bullet (two adjacent paragraphs merge in V2a)
Section B: Should-not-merge boundaries
Section C: UTF-16 code unit contract
Section D: Deterministic re-runs
Section E: Safe output and sentinel rejection
Section F: Coverage invariant fail-closed
Section G: Read-only and zero external calls
Section H: Existing boundary regression

This file is read-only — it does not modify any production module.
Helpers are imported from ``tests.test_d6_i4a_article_rag_index_plan`` to
avoid duplicating production seeding logic.  Frozen literals are
independently defined; no production hash algorithm is re-implemented
in the tests.
"""

from __future__ import annotations

import dataclasses
import json
import traceback
from uuid import uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)

# P2-B RED: the evaluation module does not exist yet.  This import is
# the real RED for Phase A — ModuleNotFoundError until Phase B creates
# the production module.
from app.services.reader_orchestration.article_rag_index_plan_evaluation import (  # noqa: E402
    ArticleRagIndexPlanEvaluationError,
    ArticleRagIndexPlanEvaluationService,
    ArticleRagPlanShapeMetrics,
    ArticleRagV1V2aPlanComparison,
    _assert_coverage_invariant,
    _basis_points_reduction,
    _build_shape_metrics,
)
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_evaluation_profile,
    resolve_article_rag_index_profile,
)

# Reuse helpers from the existing plan test module — DO NOT duplicate
# production seeding logic.  These helpers are module-level public
# functions in test_d6_i4a_article_rag_index_plan.py.
from tests.test_d6_i4a_article_rag_index_plan import (  # noqa: E402
    _BASE_ID,
    _P1E_V1_CHUNKER_VERSION,
    _P1E_V1_PLAN_CONTENT_SHA256,
    _P2A_V2A_MERGED_CHUNK_ID,
    _P2A_V2A_MERGED_PLAN_CONTENT_SHA256,
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
    INDEX_PLAN_SCHEMA_SQL,
    _build_base_text_and_offsets,
    _connect_admin,
    _main_reading_policy,
    _main_reading_policy_with_notes,
    _make_pool,
    _p1e_seed_minimal_v1_env,
    _rag_ask_only_policy,
    _seed_block,
    _seed_full_environment,
    _seed_v2a_two_paragraph_env,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# P2-B frozen sentinels (Section E)
# ---------------------------------------------------------------------------

# P2-B: sentinel strings used to verify fail-closed output sanitization.
# Planted in block_id / text_content / policy notes; MUST NOT appear in
# ``str(error)`` / ``repr(error)`` / ``error.args`` /
# ``traceback.format_exception(error)`` / ``repr(result)`` / result
# dict/JSON snapshot.
_P2B_SENTINELS: tuple[str, ...] = (
    "sk-ANT-sentinel123",               # API-key-like
    "https://attacker.example/payload",  # URI
    "DashScopeError[sentinel]",         # raw upstream error
    "<script>\U0001f3af</script>",      # script + Unicode (🎯 = U+1F3AF, 2 UTF-16 units)
)

# P2-B: expected fixed local error message for ALL coverage invariant
# failures.  The production module MUST use this exact literal (no
# interpolation of caller-supplied values).  Independently asserted by
# every Section F test.
_P2B_MSG_COVERAGE_INVARIANT_FAILED = (
    "Article RAG plan coverage invariant failed"
)

# P2-B: expected V2a index version string passed to the evaluation
# builder.  Frozen literal — the comparison service MUST pass this
# exact string, not a fallback or computed value.
_P2B_V2A_INDEX_VERSION = "article_rag_index_v2"


# ---------------------------------------------------------------------------
# Pool / schema fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def p2b_env() -> asyncpg.Pool:
    """Per-test isolated schema + pool, same shape as ``index_env`` in
    the existing plan test module.  Uses a distinct schema-name prefix
    so concurrent runs do not collide.
    """
    schema_name = f"test_p2b_rag_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(INDEX_PLAN_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _build_eval_service(pool: asyncpg.Pool) -> ArticleRagIndexPlanEvaluationService:
    return ArticleRagIndexPlanEvaluationService(pool=pool)


def _build_plan_service(pool: asyncpg.Pool) -> ArticleRagIndexPlanService:
    return ArticleRagIndexPlanService(pool=pool)


async def _build_v1_and_v2a_plans(
    pool: asyncpg.Pool,
    *,
    include_rag_ask_only: bool = False,
) -> tuple[ArticleRagIndexPlan, ArticleRagIndexPlan]:
    """Build V1 and V2a plans on the same pool (separate connections).

    Helper for tests that need the raw plans directly (e.g. Section F
    corruption tests).  Comparison-service tests should use
    ``_build_eval_service`` instead.
    """
    plan_service = _build_plan_service(pool)
    async with pool.acquire() as conn:
        v1_plan = await plan_service.build_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
            include_rag_ask_only=include_rag_ask_only,
        )
        v2a_plan = await plan_service.build_evaluation_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=_P2B_V2A_INDEX_VERSION,
            include_rag_ask_only=include_rag_ask_only,
        )
    return v1_plan, v2a_plan


def _replace_chunk_block_ids(
    plan: ArticleRagIndexPlan,
    *,
    chunk_index: int,
    new_block_ids: tuple[str, ...],
) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with ``chunk_index``'s citation
    ``block_ids`` replaced.  Used by Section F to construct corrupted
    V2a plans via ``dataclasses.replace``.
    """
    chunks = list(plan.chunks)
    target = chunks[chunk_index]
    new_citation = dataclasses.replace(target.citation, block_ids=new_block_ids)
    chunks[chunk_index] = dataclasses.replace(target, citation=new_citation)
    return dataclasses.replace(plan, chunks=tuple(chunks))


def _replace_chunk_canonical_end(
    plan: ArticleRagIndexPlan,
    *,
    chunk_index: int,
    new_end: int,
) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with ``chunk_index``'s citation
    ``canonical_text_end_utf16`` replaced.
    """
    chunks = list(plan.chunks)
    target = chunks[chunk_index]
    new_citation = dataclasses.replace(
        target.citation, canonical_text_end_utf16=new_end
    )
    chunks[chunk_index] = dataclasses.replace(target, citation=new_citation)
    return dataclasses.replace(plan, chunks=tuple(chunks))


def _replace_chunk_source_scope(
    plan: ArticleRagIndexPlan,
    *,
    chunk_index: int,
    new_scope: str,
) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with ``chunk_index``'s ``source_scope``
    replaced.
    """
    chunks = list(plan.chunks)
    target = chunks[chunk_index]
    chunks[chunk_index] = dataclasses.replace(target, source_scope=new_scope)
    return dataclasses.replace(plan, chunks=tuple(chunks))


def _drop_first_chunk(plan: ArticleRagIndexPlan) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with the first chunk removed."""
    return dataclasses.replace(plan, chunks=plan.chunks[1:])


def _swap_first_two_chunks(plan: ArticleRagIndexPlan) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with the first two chunks swapped."""
    chunks = list(plan.chunks)
    if len(chunks) < 2:
        return plan
    chunks[0], chunks[1] = chunks[1], chunks[0]
    return dataclasses.replace(plan, chunks=tuple(chunks))


def _duplicate_first_block_id(plan: ArticleRagIndexPlan) -> ArticleRagIndexPlan:
    """Return a copy of ``plan`` with the first chunk's first block_id
    duplicated in its ``citation.block_ids``.
    """
    if not plan.chunks:
        return plan
    first = plan.chunks[0]
    if not first.citation.block_ids:
        return plan
    duplicated = first.citation.block_ids + (first.citation.block_ids[0],)
    return _replace_chunk_block_ids(plan, chunk_index=0, new_block_ids=duplicated)


def _assert_sentinels_absent_from_error(err: BaseException) -> None:
    """Assert none of the ``_P2B_SENTINELS`` appear in any error
    surface (``str``, ``repr``, ``args``, ``traceback``).
    """
    err_str = str(err)
    err_repr = repr(err)
    err_args = [str(a) for a in err.args]
    err_tb = "".join(traceback.format_exception(err))
    for sentinel in _P2B_SENTINELS:
        assert sentinel not in err_str, (
            f"Sentinel {sentinel!r} leaked into str(error)"
        )
        assert sentinel not in err_repr, (
            f"Sentinel {sentinel!r} leaked into repr(error)"
        )
        assert not any(sentinel in a for a in err_args), (
            f"Sentinel {sentinel!r} leaked into error.args"
        )
        assert sentinel not in err_tb, (
            f"Sentinel {sentinel!r} leaked into traceback"
        )


def _assert_sentinels_absent_from_result(result: ArticleRagV1V2aPlanComparison) -> None:
    """Assert none of the ``_P2B_SENTINELS`` appear in the result's
    ``repr`` or its JSON-serialised snapshot.
    """
    result_repr = repr(result)
    result_dict = result.canonical_payload()
    result_json = json.dumps(
        result_dict, ensure_ascii=False, separators=(",", ":")
    )
    for sentinel in _P2B_SENTINELS:
        assert sentinel not in result_repr, (
            f"Sentinel {sentinel!r} leaked into repr(result)"
        )
        assert sentinel not in result_json, (
            f"Sentinel {sentinel!r} leaked into result JSON snapshot"
        )


# ===================================================================
# Section A — Tracer bullet
# ===================================================================


async def test_p2b_a_tracer_two_adjacent_paragraphs(p2b_env: asyncpg.Pool) -> None:
    """Section A: two canonical-adjacent main_reading paragraphs.

    V1 produces 2 chunks (one per block).  V2a produces 1 merged chunk
    (contiguous merge of both blocks).  The comparison MUST succeed
    with:
      * v1.chunk_count == 2, v2a.chunk_count == 1
      * v2a.merged_chunk_count == 1
      * source_block_count == 2 on both sides
      * chunk_count_delta == -1
      * chunk_count_reduction_basis_points == 5000  (50%)
      * flattened_block_id_order_equal == True
      * citation_coverage_equal == True
    """
    base_text = await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    result = await service.compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert isinstance(result, ArticleRagV1V2aPlanComparison)
    assert result.record_id == _RECORD_ID

    v1 = result.v1_metrics
    v2a = result.v2a_metrics

    # V1 metrics.
    assert v1.index_version == DEFAULT_ARTICLE_RAG_INDEX_VERSION
    assert v1.chunk_count == 2
    assert v1.source_block_count == 2
    assert v1.merged_chunk_count == 0
    assert v1.max_blocks_per_chunk == 1
    assert v1.embedding_input_count == 2
    assert v1.vector_count == 2
    assert v1.canonical_citation_count == 2
    assert v1.noncanonical_citation_count == 0

    # V2a metrics.
    assert v2a.index_version == _P2B_V2A_INDEX_VERSION
    assert v2a.chunk_count == 1
    assert v2a.source_block_count == 2
    assert v2a.merged_chunk_count == 1
    assert v2a.max_blocks_per_chunk == 2
    assert v2a.embedding_input_count == 1
    assert v2a.vector_count == 1
    assert v2a.canonical_citation_count == 1
    assert v2a.noncanonical_citation_count == 0

    # Deltas.
    assert result.chunk_count_delta == -1
    assert result.chunk_count_reduction_basis_points == 5000
    assert result.vector_count_delta == -1
    assert result.embedding_input_count_delta == -1

    # UTF-16 totals: V2a includes the "\n\n" separator (2 code units)
    # that V1 doesn't have (V1 chunks are individual block texts).
    text_a = "First paragraph for V2a merge."
    text_b = "Second paragraph for V2a merge."
    expected_v1_total = utf16_code_unit_length(text_a) + utf16_code_unit_length(text_b)
    expected_v2a_total = utf16_code_unit_length(base_text)
    assert v1.total_embedding_input_utf16_units == expected_v1_total
    assert v2a.total_embedding_input_utf16_units == expected_v2a_total
    assert result.total_utf16_units_delta == expected_v2a_total - expected_v1_total
    assert result.total_utf16_units_delta == 2  # the "\n\n" separator

    # Coverage invariants pass.
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


# ===================================================================
# Section B — Should-not-merge boundaries
# ===================================================================


async def test_p2b_b_heading_boundary_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: an eligible heading is a hard boundary.  V1 and V2a
    both produce 3 standalone chunks; chunk_count_delta == 0.
    """
    text_a = "First paragraph."
    text_heading = "Section Title"
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(
        text_a, text_heading, text_b
    )
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="heading-1",
        order_index=1,
        block_type="heading",
        text_content=text_heading,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=2,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    assert result.v1_metrics.chunk_count == 3
    assert result.v2a_metrics.chunk_count == 3
    assert result.chunk_count_delta == 0
    assert result.chunk_count_reduction_basis_points == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


async def test_p2b_b_canonical_gap_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: a canonical gap (non-adjacent offsets) prevents V2a
    merge.  V1 and V2a both produce 2 chunks.
    """
    text_a = "First block"
    text_b = "Second block"
    gap_text = "X" * 10
    base_text = text_a + gap_text + text_b
    start1 = 0
    end1 = utf16_code_unit_length(text_a)
    start2 = end1 + utf16_code_unit_length(gap_text)
    end2 = start2 + utf16_code_unit_length(text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=start1,
        canonical_text_end_utf16=end1,
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=start2,
        canonical_text_end_utf16=end2,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    assert result.v1_metrics.chunk_count == 2
    assert result.v2a_metrics.chunk_count == 2
    assert result.chunk_count_delta == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


async def test_p2b_b_different_route_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: adjacent blocks with different routes (main_reading
    vs rag_ask_only) do not merge in V2a.  Both V1 and V2a produce 2
    chunks when ``include_rag_ask_only=True``.
    """
    text_a = "First paragraph."
    text_b = "Table cell content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="table-cell-1",
        order_index=1,
        block_type="table_cell",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_rag_ask_only_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    assert result.v1_metrics.chunk_count == 2
    assert result.v2a_metrics.chunk_count == 2
    assert result.chunk_count_delta == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


async def test_p2b_b_different_source_scope_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: adjacent main_reading blocks with different effective
    source scopes do not merge in V2a.  Uses a valid
    ``StableDocumentSourceScope`` Literal value (``"heading"``).
    """
    text_a = "First paragraph."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy("main_reading_text"),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy("heading"),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    assert result.v1_metrics.chunk_count == 2
    assert result.v2a_metrics.chunk_count == 2
    assert result.chunk_count_delta == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


async def test_p2b_b_different_policy_fingerprint_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: adjacent main_reading blocks with the same route /
    scope / rag_eligible but different ``notes`` do not merge in V2a
    (materialized policy fingerprint covers ``notes``).
    """
    text_a = "First paragraph for notes policy test."
    text_b = "Second paragraph for notes policy test."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(["policy-a"]),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy_with_notes(["policy-b"]),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    assert result.v1_metrics.chunk_count == 2
    assert result.v2a_metrics.chunk_count == 2
    assert result.chunk_count_delta == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


async def test_p2b_b_rag_ask_only_boundary_does_not_merge(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section B: a rag_ask_only block cannot merge with an adjacent
    main_reading block in V2a, even when ``include_rag_ask_only=True``
    exposes both as chunks.
    """
    text_a = "Main paragraph content."
    text_b = "Footnote content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="footnote-1",
        order_index=1,
        block_type="footnote",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_rag_ask_only_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    assert result.v1_metrics.chunk_count == 2
    assert result.v2a_metrics.chunk_count == 2
    assert result.chunk_count_delta == 0
    assert result.flattened_block_id_order_equal is True
    assert result.citation_coverage_equal is True


# ===================================================================
# Section C — UTF-16 code unit contract
# ===================================================================


async def test_p2b_c_ascii_chunk_utf16_metrics(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section C: ASCII-only chunks produce UTF-16 metrics equal to
    Python ``len()`` for each chunk.  Frozen literals are independent
    of any production algorithm; p50/p95 use nearest-rank.
    """
    text_a = "Alpha chunk."            # 12 ASCII chars
    text_b = "Beta chunk second."      # 18 ASCII chars
    text_c = "Gamma chunk third entry."  # 24 ASCII chars
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b, text_c)
    await _seed_full_environment(p2b_env, base_text=base_text)
    for i, (text, block_id) in enumerate(
        [
            (text_a, "paragraph-1"),
            (text_b, "paragraph-2"),
            (text_c, "paragraph-3"),
        ]
    ):
        await _seed_block(
            p2b_env,
            block_id=block_id,
            order_index=i,
            block_type="paragraph",
            text_content=text,
            canonical_text_start_utf16=offsets[i][0],
            canonical_text_end_utf16=offsets[i][1],
            interpretation_policy=_main_reading_policy(),
        )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    v1 = result.v1_metrics
    # Independent fixed literals — no production algorithm reuse.
    assert v1.chunk_count == 3
    assert v1.min_chunk_utf16_units == 12
    assert v1.max_chunk_utf16_units == 24
    # nearest-rank p50 on sorted [12, 18, 24]:
    # ceil(50/100 * 3) - 1 = ceil(1.5) - 1 = 2 - 1 = 1 → sorted[1] = 18
    assert v1.p50_chunk_utf16_units == 18
    # nearest-rank p95: ceil(95/100 * 3) - 1 = ceil(2.85) - 1 = 3 - 1 = 2 → sorted[2] = 24
    assert v1.p95_chunk_utf16_units == 24
    assert v1.total_embedding_input_utf16_units == 12 + 18 + 24


async def test_p2b_c_emoji_astral_unicode_utf16(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section C: a chunk containing an astral-plane emoji (🎯 U+1F3AF)
    has UTF-16 length 2 for that single character — NOT Python
    ``len()`` of 1.  ``total_embedding_input_utf16_units`` reflects
    surrogate pair counting.
    """
    # "🎯 Alpha" — 🎯 = U+1F3AF (2 UTF-16 code units), space=1, "Alpha"=5
    # Total UTF-16 = 2 + 1 + 5 = 8
    text_a = "🎯 Alpha"
    text_b = "Plain beta chunk."  # 17 ASCII chars (P-l-a-i-n-_-b-e-t-a-_-c-h-u-n-k-.)
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    v1 = result.v1_metrics
    assert v1.chunk_count == 2
    # Verify surrogate pair is counted as 2, not Python len() of 1.
    assert utf16_code_unit_length("🎯") == 2
    assert len("🎯") == 1  # Python len() — explicitly different
    assert v1.min_chunk_utf16_units == 8   # "🎯 Alpha"
    assert v1.max_chunk_utf16_units == 17   # "Plain beta chunk." (17 ASCII chars)
    # sorted: [8, 17]; p50 = sorted[ceil(50/100*2)-1] = sorted[0] = 8
    # nearest-rank with n=2 and p=50: rank = ceil(0.50*2) = 1 → index 0
    assert v1.p50_chunk_utf16_units == 8
    # p95 = sorted[ceil(95/100*2)-1] = sorted[1] = 17
    # nearest-rank with n=2 and p=95: rank = ceil(0.95*2) = 2 → index 1
    assert v1.p95_chunk_utf16_units == 17
    assert v1.total_embedding_input_utf16_units == 8 + 17


async def test_p2b_c_empty_plan_metrics(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section C: empty plan (no eligible chunks) yields zero metrics
    across the board.

    The comparison service cannot produce an empty plan end-to-end
    because the underlying plan service raises
    ``ArticleRagIndexPlanError`` when no RAG-eligible blocks are
    present (a fail-closed contract from P1-E that P2-B MUST NOT
    modify).  This test exercises the spec's "Empty plan" scenario
    by constructing an empty :class:`ArticleRagIndexPlan` directly
    and calling :func:`_build_shape_metrics` to verify the metrics
    builder's empty-plan behaviour.

    The single-chunk case (n=1, p50=p95=that value) is covered by
    Section A's V2a metrics (chunk_count=1).
    """
    empty_plan = ArticleRagIndexPlan(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        content_sha256="0" * 64,
        canonical_text_sha256="0" * 64,
        chunker_version=_P1E_V1_CHUNKER_VERSION,
        chunks=(),
    )

    metrics = _build_shape_metrics(
        empty_plan,
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        profile_fingerprint="0" * 64,
    )

    assert metrics.chunk_count == 0
    assert metrics.source_block_count == 0
    assert metrics.merged_chunk_count == 0
    assert metrics.max_blocks_per_chunk == 0
    assert metrics.embedding_input_count == 0
    assert metrics.vector_count == 0
    assert metrics.total_embedding_input_utf16_units == 0
    assert metrics.min_chunk_utf16_units == 0
    assert metrics.max_chunk_utf16_units == 0
    assert metrics.p50_chunk_utf16_units == 0
    assert metrics.p95_chunk_utf16_units == 0
    assert metrics.canonical_citation_count == 0
    assert metrics.noncanonical_citation_count == 0
    assert metrics.unit_reference_count == 0
    assert metrics.anchor_segment_reference_count == 0
    # source_scope_counts: empty mapping for empty plan.
    assert dict(metrics.source_scope_counts) == {}


# ===================================================================
# Section D — Deterministic re-runs
# ===================================================================


async def test_p2b_d_repeat_comparison_byte_equal(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section D: comparing the same fixture N times produces
    byte-equal ``ArticleRagV1V2aPlanComparison`` results.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    results: list[ArticleRagV1V2aPlanComparison] = []
    for _ in range(3):
        result = await service.compare_for_record(
            record_id=_RECORD_ID, user_id=_USER_ID,
        )
        results.append(result)

    # All results byte-equal: compare via canonical_payload() +
    # JSON dump (deterministic key order frozen by canonical_payload()).
    snapshots = [
        json.dumps(r.canonical_payload(), ensure_ascii=False, separators=(",", ":"))
        for r in results
    ]
    first = snapshots[0]
    for i, snap in enumerate(snapshots[1:], start=1):
        assert snap == first, f"Run {i} diverged from run 0"


async def test_p2b_d_plan_hash_and_profile_fingerprint_stable(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section D: ``plan_content_sha256`` and ``profile_fingerprint`` are
    stable across repeated comparisons on the same fixture.

    This test focuses on stability across runs (r1 == r2).  The
    cross-check against the P1-E V1 golden literal is performed by
    ``test_p2b_h_v1_golden_chunk_id_and_plan_hash_unchanged`` (which
    seeds the P1-E minimal V1 environment) and the cross-check
    against the P2-A V2a golden literal is performed by
    ``test_p2b_h_v2a_golden_chunk_id_and_plan_hash_unchanged``.
    Here, V2a hash stability is also asserted against the P2-A golden
    because the V2a two-paragraph fixture IS the P2-A fixture.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    r1 = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )
    r2 = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    # V1 plan hash stable across runs (not checked against P1-E golden
    # here because the V2a two-paragraph fixture produces a different
    # V1 plan hash than the P1-E minimal single-paragraph fixture).
    assert r1.v1_metrics.plan_content_sha256 == r2.v1_metrics.plan_content_sha256
    # V2a plan hash stable + matches P2-A golden literal (V2a fixture
    # is the same fixture used to capture the P2-A golden).
    assert r1.v2a_metrics.plan_content_sha256 == _P2A_V2A_MERGED_PLAN_CONTENT_SHA256
    assert r2.v2a_metrics.plan_content_sha256 == _P2A_V2A_MERGED_PLAN_CONTENT_SHA256
    # Profile fingerprints stable across runs.
    assert r1.v1_metrics.profile_fingerprint == r2.v1_metrics.profile_fingerprint
    assert r1.v2a_metrics.profile_fingerprint == r2.v2a_metrics.profile_fingerprint
    # V1 and V2a have distinct fingerprints (different index_version).
    assert r1.v1_metrics.profile_fingerprint != r1.v2a_metrics.profile_fingerprint


async def test_p2b_d_source_scope_counts_order_stable(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section D: ``source_scope_counts`` keys are sorted ascending and
    stable across runs.  Multiple distinct scopes are seeded.
    """
    # Mix main_reading_text + heading scope + table_cell (rag_ask_only
    # when include_rag_ask_only=True).
    text_a = "Paragraph one."
    text_h = "Heading title"
    text_b = "Table cell content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_h, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy("main_reading_text"),
    )
    await _seed_block(
        p2b_env,
        block_id="heading-1",
        order_index=1,
        block_type="heading",
        text_content=text_h,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy("heading"),
    )
    await _seed_block(
        p2b_env,
        block_id="table-cell-1",
        order_index=2,
        block_type="table_cell",
        text_content=text_b,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_rag_ask_only_policy("table_cell"),
    )

    service = _build_eval_service(p2b_env)
    r1 = await service.compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )
    r2 = await service.compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    for metrics in (r1.v1_metrics, r1.v2a_metrics, r2.v1_metrics, r2.v2a_metrics):
        keys = list(metrics.source_scope_counts.keys())
        # Keys are sorted ascending — frozen contract.
        assert keys == sorted(keys), f"source_scope_counts keys not sorted: {keys}"
        # Stable across runs.
    assert list(r1.v1_metrics.source_scope_counts.keys()) == list(
        r2.v1_metrics.source_scope_counts.keys()
    )
    assert list(r1.v2a_metrics.source_scope_counts.keys()) == list(
        r2.v2a_metrics.source_scope_counts.keys()
    )


async def test_p2b_d_json_snapshot_key_order_contract(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section D: JSON / dict snapshot key set and order is stable.
    The ``ArticleRagV1V2aPlanComparison`` dataclass field order is
    the canonical contract; ``canonical_payload()`` preserves it.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)
    r1 = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )
    r2 = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    # Field order contract — frozen dataclass field declaration order.
    expected_field_order = (
        "record_id",
        "v1_metrics",
        "v2a_metrics",
        "chunk_count_delta",
        "chunk_count_reduction_basis_points",
        "vector_count_delta",
        "embedding_input_count_delta",
        "total_utf16_units_delta",
        "flattened_block_id_order_equal",
        "citation_coverage_equal",
    )
    for r in (r1, r2):
        actual_fields = tuple(f.name for f in dataclasses.fields(r))
        assert actual_fields == expected_field_order, (
            f"Comparison field order drift: {actual_fields}"
        )

    # Metrics field order contract.
    expected_metrics_fields = (
        "index_version",
        "profile_fingerprint",
        "chunker_version",
        "plan_content_sha256",
        "chunk_count",
        "source_block_count",
        "merged_chunk_count",
        "max_blocks_per_chunk",
        "embedding_input_count",
        "vector_count",
        "total_embedding_input_utf16_units",
        "min_chunk_utf16_units",
        "max_chunk_utf16_units",
        "p50_chunk_utf16_units",
        "p95_chunk_utf16_units",
        "canonical_citation_count",
        "noncanonical_citation_count",
        "unit_reference_count",
        "anchor_segment_reference_count",
        "source_scope_counts",
    )
    for metrics in (r1.v1_metrics, r1.v2a_metrics):
        actual_metrics_fields = tuple(f.name for f in dataclasses.fields(metrics))
        assert actual_metrics_fields == expected_metrics_fields, (
            f"Metrics field order drift: {actual_metrics_fields}"
        )

    # JSON snapshot deterministic.
    s1 = json.dumps(r1.canonical_payload(), ensure_ascii=False, separators=(",", ":"))
    s2 = json.dumps(r2.canonical_payload(), ensure_ascii=False, separators=(",", ":"))
    assert s1 == s2


# ===================================================================
# Section E — Safe output and sentinel rejection
# ===================================================================


async def test_p2b_e_sentinel_not_in_error_surface(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section E: plant API-key-like / URI / raw upstream error / script
    +Unicode sentinels in chunk text, block_id, and policy notes; when
    an error is triggered (via coverage invariant corruption), the
    sentinel MUST NOT appear in any error surface.
    """
    # Plant sentinels in chunk text via a real build, then construct a
    # corrupted V2a plan that triggers the coverage invariant.
    text_a = "First paragraph sk-ANT-sentinel123."
    text_b = "Second paragraph https://attacker.example/payload."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1-sk-ANT-sentinel123",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(
            ["DashScopeError[sentinel]", "<script>\U0001f3af</script>"],
        ),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    # Build a real V1 plan (it will contain sentinels in text).
    plan_service = _build_plan_service(p2b_env)
    async with p2b_env.acquire() as conn:
        v1_plan = await plan_service.build_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        )
        v2a_plan = await plan_service.build_evaluation_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=_P2B_V2A_INDEX_VERSION,
        )

    # Corrupt V2a plan: drop a block_id from the first chunk's citation
    # so the coverage invariant fails.
    corrupted_v2a = _drop_first_chunk(v2a_plan)

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(v1_plan, corrupted_v2a)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None
    _assert_sentinels_absent_from_error(err)


async def test_p2b_e_sentinel_not_in_result_surface(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section E: when sentinels are planted in chunk text / policy notes
    but the comparison SUCCEEDS (no coverage drift), sentinels MUST NOT
    appear in ``repr(result)`` or result's dict/JSON snapshot.
    """
    # Plant sentinels in chunk text only (not in block_id — block_ids
    # DO appear in flattened_block_id comparison but are not echoed in
    # the result, so this is fine for the success case).
    text_a = "First paragraph sk-ANT-sentinel123 contents."
    text_b = "Second paragraph https://attacker.example/payload end."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(
            ["DashScopeError[sentinel]", "<script>\U0001f3af</script>"],
        ),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    _assert_sentinels_absent_from_result(result)


async def test_p2b_e_public_identity_only_allowed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section E: the only allowed public identity fields in the result
    are ``record_id``, ``index_version``, ``profile_fingerprint``,
    ``plan_content_sha256``, and aggregate counts.  Chunk text / block
    id / URI / policy notes / SDK objects MUST NOT appear.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    # Allowed public identity — whitelist of safe substrings.
    allowed_public_identity = {
        "record_id",
        "index_version",
        "profile_fingerprint",
        "plan_content_sha256",
        "chunker_version",
        # Field names (structural, not values).
        "v1_metrics", "v2a_metrics",
        "chunk_count", "source_block_count", "merged_chunk_count",
        "max_blocks_per_chunk", "embedding_input_count", "vector_count",
        "total_embedding_input_utf16_units", "min_chunk_utf16_units",
        "max_chunk_utf16_units", "p50_chunk_utf16_units",
        "p95_chunk_utf16_units", "canonical_citation_count",
        "noncanonical_citation_count", "unit_reference_count",
        "anchor_segment_reference_count", "source_scope_counts",
        "chunk_count_delta", "chunk_count_reduction_basis_points",
        "vector_count_delta", "embedding_input_count_delta",
        "total_utf16_units_delta",
        "flattened_block_id_order_equal", "citation_coverage_equal",
        # Frozen enum-like string identities (not user content).
        "article_rag_index_v1", "article_rag_index_v2",
        "article_rag_index_plan_v1", "article_rag_index_plan_v2a",
        # Source scope enum values.
        "main_reading_text", "heading", "table_cell",
        # JSON structural punctuation.
        "{", "}", "[", "]", "(", ")", ",", ":", '"', ' ', '=',
        "True", "False", "UUID", "MappingProxyType",
        # Hex / numeric literal characters.
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "a", "b", "c", "d", "e", "f",
        "A", "B", "C", "D", "E", "F",
        "-", "_",
    }

    result_repr = repr(result)
    result_json = json.dumps(
        result.canonical_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    result_str = str(result)

    # Build the set of characters/substrings actually present.
    # We check that the result surfaces do not contain forbidden
    # substrings like leaked block id values, text content, etc.
    #
    # NOTE: ``block_id`` as a bare substring is intentionally NOT in
    # this list because the comparison result has a public field named
    # ``flattened_block_id_order_equal`` whose repr legitimately contains
    # the substring "block_id" as part of the field name (not as a
    # leaked value).  The same applies to ``citation_coverage_equal``
    # (contains "citation") and ``canonical_*`` field names.  We check
    # for leaked VALUES (e.g., "paragraph-1") rather than field-name
    # fragments.
    forbidden_substrings = (
        "paragraph-1", "paragraph-2", "text_content",
        "First paragraph", "Second paragraph",
        # Sentinels from Section E.
        "sk-ANT", "attacker.example", "DashScopeError", "<script>",
    )
    for s in forbidden_substrings:
        assert s not in result_repr, f"Forbidden substring {s!r} in repr(result)"
        assert s not in result_json, f"Forbidden substring {s!r} in result JSON"
        assert s not in result_str, f"Forbidden substring {s!r} in str(result)"

    # Sanity: allowed identity is non-empty.
    assert allowed_public_identity


# ===================================================================
# Section F — Coverage invariant fail-closed
# ===================================================================


async def test_p2b_f_block_loss_fail_closed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section F: V2a losing a chunk (and thus a block_id) MUST trigger
    fail-closed ``ArticleRagIndexPlanEvaluationError`` with the fixed
    local message and a clean exception chain.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    corrupted = _drop_first_chunk(v2a_plan)

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(v1_plan, corrupted)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None
    # Repr / args / traceback must not echo block ids.
    for surface in (str(err), repr(err), *[str(a) for a in err.args]):
        assert "paragraph-1" not in surface
        assert "paragraph-2" not in surface


async def test_p2b_f_block_reorder_fail_closed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section F: V2a chunks swapped so flattened_block_id_order differs
    from V1 MUST trigger fail-closed.  Set comparison would mask this;
    the production invariant MUST use ordered tuple comparison.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    # V1 has 2 chunks (paragraph-1, paragraph-2); V2a has 1 merged
    # chunk containing both.  Flattened order in V1 is (paragraph-1,
    # paragraph-2); in V2a it is also (paragraph-1, paragraph-2).  To
    # trigger reorder on V1, swap V1's first two chunks (V1 chunk
    # order is reversed).
    corrupted_v1 = _swap_first_two_chunks(v1_plan)

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(corrupted_v1, v2a_plan)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2b_f_block_duplicate_fail_closed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section F: V2a chunk's citation.block_ids containing a duplicate
    MUST trigger fail-closed.  Set comparison would mask this;
    production invariant MUST use ordered tuple comparison.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    corrupted = _duplicate_first_block_id(v2a_plan)

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(v1_plan, corrupted)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2b_f_canonical_range_drift_fail_closed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section F: V2a chunk's ``canonical_text_end_utf16`` differs from
    V1's (for the same block_id) MUST trigger fail-closed via citation
    coverage tuple comparison.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    # V2a's merged chunk has citation end = combined end of both blocks.
    # Replace it with a clearly wrong value to trigger drift.
    v2a_first = v2a_plan.chunks[0]
    wrong_end = v2a_first.citation.canonical_text_end_utf16 + 100  # type: ignore[operator]
    corrupted = _replace_chunk_canonical_end(
        v2a_plan, chunk_index=0, new_end=wrong_end,  # type: ignore[arg-type]
    )

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(v1_plan, corrupted)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2b_f_source_scope_drift_fail_closed(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section F: V2a chunk's ``source_scope`` differing from V1's
    source_scope_counts key set MUST trigger fail-closed.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    # Replace V2a's source_scope with a different value than V1's.
    # V1 uses "main_reading_text"; replace V2a with "heading".
    corrupted = _replace_chunk_source_scope(
        v2a_plan, chunk_index=0, new_scope="heading",
    )

    with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
        _assert_coverage_invariant(v1_plan, corrupted)

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


# ===================================================================
# Section G — Read-only and zero external calls
# ===================================================================


async def test_p2b_g_v1_builder_called_once(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section G: V1 production builder ``build_index_plan_in_transaction``
    is called exactly once per comparison.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    calls: list[dict[str, object]] = []
    original = ArticleRagIndexPlanService.build_index_plan_in_transaction

    async def spy(self, conn, *, record_id, user_id, **kwargs):
        calls.append({
            "record_id": record_id,
            "user_id": user_id,
            "index_version": kwargs.get("index_version"),
            "include_rag_ask_only": kwargs.get("include_rag_ask_only", False),
        })
        return await original(self, conn, record_id=record_id, user_id=user_id, **kwargs)

    monkeypatch.setattr(
        ArticleRagIndexPlanService, "build_index_plan_in_transaction", spy,
    )

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(calls) == 1
    assert calls[0]["index_version"] == DEFAULT_ARTICLE_RAG_INDEX_VERSION


async def test_p2b_g_v2a_evaluation_builder_called_once(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section G: V2a evaluation builder
    ``build_evaluation_index_plan_in_transaction`` is called exactly
    once per comparison.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    calls: list[dict[str, object]] = []
    original = ArticleRagIndexPlanService.build_evaluation_index_plan_in_transaction

    async def spy(self, conn, *, record_id, user_id, index_version, **kwargs):
        calls.append({
            "record_id": record_id,
            "user_id": user_id,
            "index_version": index_version,
            "include_rag_ask_only": kwargs.get("include_rag_ask_only", False),
        })
        return await original(
            self, conn, record_id=record_id, user_id=user_id,
            index_version=index_version, **kwargs,
        )

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        spy,
    )

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(calls) == 1
    assert calls[0]["index_version"] == _P2B_V2A_INDEX_VERSION


async def test_p2b_g_same_record_user_include_rag_ask_only(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section G: V1 and V2a builders receive the same ``record_id``,
    ``user_id``, and ``include_rag_ask_only`` arguments.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    v1_calls: list[dict[str, object]] = []
    v2a_calls: list[dict[str, object]] = []
    v1_orig = ArticleRagIndexPlanService.build_index_plan_in_transaction
    v2a_orig = ArticleRagIndexPlanService.build_evaluation_index_plan_in_transaction

    async def v1_spy(self, conn, *, record_id, user_id, **kwargs):
        v1_calls.append({
            "record_id": record_id, "user_id": user_id,
            "include_rag_ask_only": kwargs.get("include_rag_ask_only", False),
        })
        return await v1_orig(self, conn, record_id=record_id, user_id=user_id, **kwargs)

    async def v2a_spy(self, conn, *, record_id, user_id, index_version, **kwargs):
        v2a_calls.append({
            "record_id": record_id, "user_id": user_id,
            "include_rag_ask_only": kwargs.get("include_rag_ask_only", False),
        })
        return await v2a_orig(
            self, conn, record_id=record_id, user_id=user_id,
            index_version=index_version, **kwargs,
        )

    monkeypatch.setattr(
        ArticleRagIndexPlanService, "build_index_plan_in_transaction", v1_spy,
    )
    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        v2a_spy,
    )

    await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID, include_rag_ask_only=True,
    )

    assert len(v1_calls) == 1
    assert len(v2a_calls) == 1
    assert v1_calls[0]["record_id"] == v2a_calls[0]["record_id"] == _RECORD_ID
    assert v1_calls[0]["user_id"] == v2a_calls[0]["user_id"] == _USER_ID
    assert v1_calls[0]["include_rag_ask_only"] is True
    assert v2a_calls[0]["include_rag_ask_only"] is True


async def test_p2b_g_v1_uses_v1_identity_v2a_uses_v2_identity(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section G: V1 builder receives ``index_version=V1``; V2a builder
    receives ``index_version="article_rag_index_v2"`` explicitly.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    v1_versions: list[object] = []
    v2a_versions: list[object] = []
    v1_orig = ArticleRagIndexPlanService.build_index_plan_in_transaction
    v2a_orig = ArticleRagIndexPlanService.build_evaluation_index_plan_in_transaction

    async def v1_spy(self, conn, *, record_id, user_id, **kwargs):
        v1_versions.append(kwargs.get("index_version"))
        return await v1_orig(self, conn, record_id=record_id, user_id=user_id, **kwargs)

    async def v2a_spy(self, conn, *, record_id, user_id, index_version, **kwargs):
        v2a_versions.append(index_version)
        return await v2a_orig(
            self, conn, record_id=record_id, user_id=user_id,
            index_version=index_version, **kwargs,
        )

    monkeypatch.setattr(
        ArticleRagIndexPlanService, "build_index_plan_in_transaction", v1_spy,
    )
    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        v2a_spy,
    )

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)

    assert v1_versions == [DEFAULT_ARTICLE_RAG_INDEX_VERSION]
    assert v2a_versions == [_P2B_V2A_INDEX_VERSION]


async def test_p2b_g_zero_embedding_provider_calls(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section G: comparison service MUST NOT call any embedding
    provider.  Verified by the production module never importing an
    embedding module and by running a comparison without exception.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    import app.services.reader_orchestration.article_rag_index_plan_evaluation as eval_mod  # noqa: E402

    module_source = open(eval_mod.__file__, encoding="utf-8").read()
    forbidden_imports = (
        "dashscope", "pymilvus", "embedding_adapter",
        "vector_writer", "vector_searcher", "reranker",
    )
    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in module_source, (
            f"Forbidden import '{forbidden}' in evaluation module source"
        )
        assert f"from {forbidden}" not in module_source, (
            f"Forbidden from-import '{forbidden}' in evaluation module source"
        )

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_p2b_g_zero_vector_writer_searcher_calls(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section G: comparison service MUST NOT call any vector writer or
    searcher.  Verified by inspecting the production module source for
    forbidden calls and by running a comparison without exception.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    import app.services.reader_orchestration.article_rag_index_plan_evaluation as eval_mod  # noqa: E402

    module_source = open(eval_mod.__file__, encoding="utf-8").read()
    forbidden_calls = (
        "writer.upsert", "searcher.search", "milvus_client",
        "insert_vectors", "search_vectors", "upsert_vectors",
    )
    for forbidden in forbidden_calls:
        assert forbidden not in module_source, (
            f"Forbidden vector call '{forbidden}' in evaluation module source"
        )

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_p2b_g_no_db_side_effects(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section G: comparison MUST NOT insert any row into
    ``reader_jobs``, ``reader_runs``, ``reader_article_rag_index_runs``,
    or ``reader_job_events``.  No reader representation event is published.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)

    async with p2b_env.acquire() as conn:
        async def _safe_count(table_name: str) -> int:
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", table_name,
            )
            if not exists:
                return 0
            return await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608

        tables = (
            "reader_jobs",
            "reader_runs",
            "reader_article_rag_index_runs",
            "reader_job_events",
        )
        before = {t: await _safe_count(t) for t in tables}

    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)

    async with p2b_env.acquire() as conn:
        after = {t: await _safe_count(t) for t in tables}

    for table_name in tables:
        assert after[table_name] == before[table_name], (
            f"DB side effect detected: {table_name} row count changed "
            f"from {before[table_name]} to {after[table_name]}"
        )


# ===================================================================
# Section H — Existing boundary regression
# ===================================================================


async def test_p2b_h_production_resolver_rejects_v2() -> None:
    """Section H: production resolver ``resolve_article_rag_index_profile``
    MUST continue to reject ``article_rag_index_v2`` — V2 is evaluation-only.
    """
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_profile(_P2B_V2A_INDEX_VERSION)


async def test_p2b_h_evaluation_resolver_rejects_v1_unknown_malformed() -> None:
    """Section H: evaluation resolver ``resolve_article_rag_index_evaluation_profile``
    rejects V1 / unknown / malformed versions — only the explicit V2a
    evaluation identity is accepted.
    """
    bad_inputs = (
        DEFAULT_ARTICLE_RAG_INDEX_VERSION,    # V1 identity
        "article_rag_index_v3",              # unknown future
        "",                                  # empty
        "   ",                               # whitespace
        "article_rag_index_v2\x00malformed", # null byte
    )
    for bad in bad_inputs:
        with pytest.raises(ArticleRagIndexProfileResolutionError):
            resolve_article_rag_index_evaluation_profile(bad)

    # The explicit V2a identity MUST resolve successfully.
    resolution = resolve_article_rag_index_evaluation_profile(_P2B_V2A_INDEX_VERSION)
    assert resolution.profile.index_version == _P2B_V2A_INDEX_VERSION


async def test_p2b_h_v1_golden_chunk_id_and_plan_hash_unchanged(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section H: V1 golden ``chunk_id`` and ``plan_content_sha256``
    MUST remain unchanged from the P1-E baseline.

    Uses the P1-E minimal V1 environment (single ``paragraph-1`` block
    seeded with ``_P1E_V1_BASE_TEXT = "Hello article RAG world."``) so
    the captured ``_P1E_V1_PLAN_CONTENT_SHA256`` golden applies
    directly.  Seeding with the V2a two-paragraph fixture would
    produce a different V1 plan hash because the block text and
    chunk count differ.
    """
    await _p1e_seed_minimal_v1_env(p2b_env)
    plan_service = _build_plan_service(p2b_env)
    async with p2b_env.acquire() as conn:
        plan = await plan_service.build_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        )

    # V1 plan_content_sha256 matches P1-E golden.
    assert compute_plan_content_sha256(plan) == _P1E_V1_PLAN_CONTENT_SHA256
    # V1 chunker_version unchanged.
    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION
    assert plan.chunker_version == "article_rag_index_plan_v1"


async def test_p2b_h_v2a_golden_chunk_id_and_plan_hash_unchanged(
    p2b_env: asyncpg.Pool,
) -> None:
    """Section H: V2a golden ``chunk_id`` and ``plan_content_sha256``
    MUST remain unchanged from the P2-A baseline.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    plan_service = _build_plan_service(p2b_env)
    async with p2b_env.acquire() as conn:
        plan = await plan_service.build_evaluation_index_plan_in_transaction(
            conn,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=_P2B_V2A_INDEX_VERSION,
        )

    # V2a chunker_version.
    assert plan.chunker_version == "article_rag_index_plan_v2a"
    # V2a single merged chunk.
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    # Golden chunk_id.
    assert chunk.chunk_id == _P2A_V2A_MERGED_CHUNK_ID
    # Golden plan_content_sha256.
    assert compute_plan_content_sha256(plan) == _P2A_V2A_MERGED_PLAN_CONTENT_SHA256
    # V2a merged metadata.
    assert chunk.metadata_json["merged_block_count"] == 2


async def test_p2b_h_default_article_rag_index_version_still_v1() -> None:
    """Section H: ``DEFAULT_ARTICLE_RAG_INDEX_VERSION`` is unchanged
    and equals ``"article_rag_index_v1"`` — V2 is NOT registered as
    production.
    """
    assert DEFAULT_ARTICLE_RAG_INDEX_VERSION == "article_rag_index_v1"
    assert DEFAULT_ARTICLE_RAG_INDEX_VERSION != _P2B_V2A_INDEX_VERSION


async def test_p2b_r1_public_seam_discards_lower_plan_error_context(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrapped plan error must not retain its sensitive context."""
    sentinel = "P2B-R1-LOWER-PLAN-ERROR-SENTINEL"

    async def fail_v1_plan(*args: object, **kwargs: object) -> None:
        raise ArticleRagIndexPlanError(f"plan failure {sentinel}")

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_index_plan_in_transaction",
        fail_v1_plan,
    )

    service = _build_eval_service(p2b_env)
    async with p2b_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
                await service.compare_for_record_in_transaction(
                    conn,
                    record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    for surface in (
        str(err),
        repr(err),
        repr(err.args),
        "".join(traceback.format_exception(err)),
    ):
        assert sentinel not in surface


async def test_p2b_r1_wrapper_uses_repeatable_read_read_only_snapshot(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both plan builds must share one read-only repeatable-read snapshot."""
    await _seed_v2a_two_paragraph_env(p2b_env)
    observed: dict[str, str] = {}
    original = ArticleRagIndexPlanService.build_index_plan_in_transaction

    async def capture_transaction(self, conn, **kwargs):
        observed["isolation"] = await conn.fetchval(
            "SHOW transaction_isolation"
        )
        observed["read_only"] = await conn.fetchval(
            "SHOW transaction_read_only"
        )
        return await original(self, conn, **kwargs)

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_index_plan_in_transaction",
        capture_transaction,
    )

    service = _build_eval_service(p2b_env)
    await service.compare_for_record(record_id=_RECORD_ID, user_id=_USER_ID)

    assert observed == {
        "isolation": "repeatable read",
        "read_only": "on",
    }


async def test_p2b_r1_in_transaction_seam_requires_active_transaction(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller-owned seam must reject use outside a transaction."""
    builder_calls = 0

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal builder_calls
        builder_calls += 1
        raise AssertionError("plan builder must not run without a transaction")

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_index_plan_in_transaction",
        fail_if_called,
    )
    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        fail_if_called,
    )

    service = _build_eval_service(p2b_env)
    async with p2b_env.acquire() as conn:
        with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
            await service.compare_for_record_in_transaction(
                conn,
                record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    err = exc_info.value
    assert str(err) == (
        "Article RAG plan evaluation requires an active caller-owned transaction"
    )
    assert err.__cause__ is None
    assert err.__context__ is None
    assert builder_calls == 0


async def test_p2b_r1_internal_span_drift_with_same_extents_fails_closed(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal V1 span drift must fail even when global extents match."""
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)
    first_end = v1_plan.chunks[0].citation.canonical_text_end_utf16
    assert first_end is not None
    corrupted_v1 = _replace_chunk_canonical_end(
        v1_plan,
        chunk_index=0,
        new_end=first_end - 1,
    )

    async def return_v1(*args: object, **kwargs: object) -> ArticleRagIndexPlan:
        return corrupted_v1

    async def return_v2a(*args: object, **kwargs: object) -> ArticleRagIndexPlan:
        return v2a_plan

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_index_plan_in_transaction",
        return_v1,
    )
    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        return_v2a,
    )

    service = _build_eval_service(p2b_env)
    async with p2b_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
                await service.compare_for_record_in_transaction(
                    conn,
                    record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2b_r1_per_block_scope_reassignment_fails_closed(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope reassignment must fail even when the scope key set matches."""
    text_a = "First paragraph."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy("main_reading_text"),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy("heading"),
    )
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)
    first_scope = v2a_plan.chunks[0].source_scope
    second_scope = v2a_plan.chunks[1].source_scope
    assert first_scope != second_scope
    corrupted_v2a = _replace_chunk_source_scope(
        v2a_plan,
        chunk_index=0,
        new_scope=second_scope,
    )
    corrupted_v2a = _replace_chunk_source_scope(
        corrupted_v2a,
        chunk_index=1,
        new_scope=first_scope,
    )

    async def return_v1(*args: object, **kwargs: object) -> ArticleRagIndexPlan:
        return v1_plan

    async def return_v2a(*args: object, **kwargs: object) -> ArticleRagIndexPlan:
        return corrupted_v2a

    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_index_plan_in_transaction",
        return_v1,
    )
    monkeypatch.setattr(
        ArticleRagIndexPlanService,
        "build_evaluation_index_plan_in_transaction",
        return_v2a,
    )

    service = _build_eval_service(p2b_env)
    async with p2b_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanEvaluationError) as exc_info:
                await service.compare_for_record_in_transaction(
                    conn,
                    record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    err = exc_info.value
    assert str(err) == _P2B_MSG_COVERAGE_INVARIANT_FAILED
    assert err.__cause__ is None
    assert err.__context__ is None


# ===================================================================
# P2-B-R2 — Output Contract Closure
#   (1) merge metrics citation-truth source
#   (2) basis-points no-float contract
#   (3) canonical_payload() public API
# ===================================================================


# ---------------------------------------------------------------------------
# Sentinel used in P2-B-R2 metadata-poisoning matrix.
# ---------------------------------------------------------------------------

_P2B_R2_METADATA_SENTINEL = "P2B-R2-METADATA-SENTINEL"


# ===================================================================
# P2-B-R2 (1) — Merge metrics MUST use citation.block_ids, not metadata
# ===================================================================


async def test_p2b_r2_merge_metrics_use_citation_truth_not_metadata(
    p2b_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-B-R2: ``merged_chunk_count`` and ``max_blocks_per_chunk`` MUST
    be derived from ``citation.block_ids`` (the same truth source used
    by the coverage invariant), NOT from
    ``metadata_json["merged_block_count"]``.

    The V2a plan from ``_seed_v2a_two_paragraph_env`` produces one
    chunk whose ``citation.block_ids = ("paragraph-1", "paragraph-2")``
    (length 2).  We construct six V2a plan variants that share the
    same citation but carry different ``metadata_json`` values:
      * missing ``merged_block_count``
      * ``True`` (bool — old code falls back to 1)
      * ``0``
      * ``-1``
      * ``999``
      * ``"P2B-R2-METADATA-SENTINEL"`` (str — old code falls back to 1)

    For every variant, the comparison result MUST produce identical:
      * ``v2a_metrics.merged_chunk_count`` == 1   (one chunk with 2 blocks)
      * ``v2a_metrics.max_blocks_per_chunk`` == 2 (largest citation slice)
      * ``v2a_metrics.source_block_count`` == 2  (sum of citation lengths)
      * ``chunk_count_reduction_basis_points`` == 5000  (V1=2, V2a=1)

    The metadata sentinel MUST NOT leak into ``repr(result)``,
    ``repr(v2a_metrics)``, ``canonical_payload()`` JSON, or a fresh
    ``canonical_payload()`` snapshot.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    v1_plan, v2a_plan = await _build_v1_and_v2a_plans(p2b_env)

    # Sanity: V2a has one chunk whose citation spans two block ids.
    assert len(v2a_plan.chunks) == 1
    assert len(v2a_plan.chunks[0].citation.block_ids) == 2
    # Sanity: V1 has two single-block chunks.
    assert len(v1_plan.chunks) == 2
    for c in v1_plan.chunks:
        assert len(c.citation.block_ids) == 1

    metadata_variants: list[dict[str, object]] = [
        {},                                       # missing
        {"merged_block_count": True},             # bool -> old fallback
        {"merged_block_count": 0},
        {"merged_block_count": -1},
        {"merged_block_count": 999},
        {"merged_block_count": _P2B_R2_METADATA_SENTINEL},  # str -> old fallback
    ]

    results: list[ArticleRagV1V2aPlanComparison] = []
    for metadata in metadata_variants:
        custom_chunks = tuple(
            dataclasses.replace(c, metadata_json=dict(metadata))
            for c in v2a_plan.chunks
        )
        custom_v2a = dataclasses.replace(v2a_plan, chunks=custom_chunks)

        # Bind loop variables as default args to avoid B023 late-binding
        # closure issues — the functions are called synchronously within
        # the same iteration, but default-arg binding makes the captured
        # value explicit and ruff-clean.
        async def return_v1(
            *args: object,
            _v1: ArticleRagIndexPlan = v1_plan,
            **kwargs: object,
        ) -> ArticleRagIndexPlan:
            return _v1

        async def return_v2a(
            *args: object,
            _v2a: ArticleRagIndexPlan = custom_v2a,
            **kwargs: object,
        ) -> ArticleRagIndexPlan:
            return _v2a

        monkeypatch.setattr(
            ArticleRagIndexPlanService,
            "build_index_plan_in_transaction",
            return_v1,
        )
        monkeypatch.setattr(
            ArticleRagIndexPlanService,
            "build_evaluation_index_plan_in_transaction",
            return_v2a,
        )

        service = _build_eval_service(p2b_env)
        async with p2b_env.acquire() as conn:
            async with conn.transaction():
                result = await service.compare_for_record_in_transaction(
                    conn,
                    record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
        results.append(result)

    # All six results MUST agree on the four citation-derived metrics.
    base = results[0]
    for i, r in enumerate(results[1:], start=1):
        assert r.v2a_metrics.merged_chunk_count == base.v2a_metrics.merged_chunk_count, (
            f"variant {i} merged_chunk_count diverged: "
            f"{r.v2a_metrics.merged_chunk_count} != {base.v2a_metrics.merged_chunk_count}"
        )
        assert r.v2a_metrics.max_blocks_per_chunk == base.v2a_metrics.max_blocks_per_chunk, (
            f"variant {i} max_blocks_per_chunk diverged: "
            f"{r.v2a_metrics.max_blocks_per_chunk} != {base.v2a_metrics.max_blocks_per_chunk}"
        )
        assert r.v2a_metrics.source_block_count == base.v2a_metrics.source_block_count, (
            f"variant {i} source_block_count diverged"
        )
        assert r.chunk_count_reduction_basis_points == base.chunk_count_reduction_basis_points, (
            f"variant {i} chunk_count_reduction_basis_points diverged"
        )

    # Citation-truth expected values:
    #   * V2a has one chunk with 2 citation block_ids -> merged_chunk_count = 1
    #   * max blocks per chunk = 2
    #   * source_block_count = 2
    #   * V1=2, V2a=1, reduction = (2-1)/2 * 10000 = 5000 bp
    for i, r in enumerate(results):
        assert r.v2a_metrics.merged_chunk_count == 1, (
            f"variant {i} merged_chunk_count = {r.v2a_metrics.merged_chunk_count}, expected 1"
        )
        assert r.v2a_metrics.max_blocks_per_chunk == 2, (
            f"variant {i} max_blocks_per_chunk = {r.v2a_metrics.max_blocks_per_chunk}, expected 2"
        )
        assert r.v2a_metrics.source_block_count == 2, (
            f"variant {i} source_block_count = {r.v2a_metrics.source_block_count}, expected 2"
        )
        assert r.chunk_count_reduction_basis_points == 5000, (
            f"variant {i} chunk_count_reduction_basis_points = "
            f"{r.chunk_count_reduction_basis_points}, expected 5000"
        )

    # Metadata sentinel MUST NOT leak into any surface, including the
    # canonical payload (public API) and a fresh payload snapshot.
    for r in results:
        assert _P2B_R2_METADATA_SENTINEL not in repr(r)
        assert _P2B_R2_METADATA_SENTINEL not in repr(r.v2a_metrics)
        payload = r.canonical_payload()
        payload_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        assert _P2B_R2_METADATA_SENTINEL not in payload_json
        assert _P2B_R2_METADATA_SENTINEL not in repr(payload)
        fresh_payload = r.canonical_payload()
        fresh_json = json.dumps(
            fresh_payload, ensure_ascii=False, separators=(",", ":")
        )
        assert _P2B_R2_METADATA_SENTINEL not in fresh_json


# ===================================================================
# P2-B-R2 (2) — Basis-points no-float contract
# ===================================================================


def test_p2b_r2_basis_points_no_float_fixed_literals() -> None:
    """P2-B-R2: integer basis-point rounding matches fixed literals.

    This narrow numerical contract intentionally exercises the private pure
    helper because the public comparison seam cannot construct arbitrary
    signed denominators without manufacturing millions of plan chunks.

    Frozen literals (NOT recomputed via the production algorithm) cover
    half-even rounding symmetry for positive and negative numerators and
    denominators, divide-by-zero, and the canonical 1/3 and 2/3 cases.
    """
    assert _basis_points_reduction(1, 2) == 5000
    assert _basis_points_reduction(1, 3) == 3333
    assert _basis_points_reduction(2, 3) == 6667
    assert _basis_points_reduction(1, 32) == 312
    assert _basis_points_reduction(3, 32) == 938
    assert _basis_points_reduction(-1, 32) == -312
    assert _basis_points_reduction(-3, 32) == -938

    # Negative denominators are mathematically equivalent to negating the
    # numerator; these literals expose floor-division sign errors.
    assert _basis_points_reduction(1, -3) == -3333
    assert _basis_points_reduction(-1, -3) == 3333
    assert _basis_points_reduction(1, -32) == -312
    assert _basis_points_reduction(3, -32) == -938

    assert _basis_points_reduction(1, 0) == 0
    assert _basis_points_reduction(0, 7) == 0


def test_p2b_r2_basis_points_big_integer_precision_vs_float() -> None:
    """P2-B-R2: a big-integer case that proves the legacy float path
    loses precision.

    ``numerator = 2**53 + 1 = 9007199254740993`` cannot be represented
    exactly as a float (float64 mantissa is 52 bits + implicit leading
    1, so 2**53 is the largest contiguous integer).  When multiplied
    by 10000 and divided by 1, the legacy float path produces
    ``90071992547409936384`` (float64 representation error compounds
    across the multiply), whereas the correct integer result is
    ``90071992547409930000`` (``numerator * 10000`` exactly).

    This and the fixed-literal rounding matrix are intentionally narrow
    tests of the private pure helper. The public comparison seam covers the
    normal 5000-bp integration case, but cannot reasonably manufacture the
    signed denominators or enormous plan sizes needed for these boundaries.
    """
    numerator = 2**53 + 1  # 9007199254740993
    expected = 90071992547409930000  # numerator * 10000, exact integer
    # Sanity: prove the float path diverges from the integer path.
    float_path = round(numerator * 10000 / 1)
    assert float_path != expected, (
        "Float path unexpectedly matched integer path — test premise invalid"
    )
    # The exact float-path value is a fixed literal observed on CPython
    # 3.x / IEEE 754 float64.  It is NOT recomputed from the production
    # algorithm — it independently certifies that the legacy float path
    # loses precision and that the production seam MUST NOT follow it.
    assert float_path == 90071992547409936384, (
        f"Float path produced {float_path!r}, "
        f"expected 90071992547409936384"
    )
    # The production seam MUST return the integer path.
    assert _basis_points_reduction(numerator, 1) == expected


# ===================================================================
# P2-B-R2 (3) — canonical_payload() public API
# ===================================================================


async def test_p2b_r2_canonical_payload_tracer_and_schema(
    p2b_env: asyncpg.Pool,
) -> None:
    """P2-B-R2: ``ArticleRagV1V2aPlanComparison.canonical_payload()``
    returns a JSON-compatible detached dict with the exact frozen
    schema and key order.

    The top-level payload MUST start with ``$schema`` (discriminator)
    and then strictly follow the ``ArticleRagV1V2aPlanComparison``
    dataclass field declaration order.  Nested ``v1_metrics`` /
    ``v2a_metrics`` payloads MUST strictly follow the
    ``ArticleRagPlanShapeMetrics`` field declaration order.

    ``record_id`` MUST be a ``str`` (UUID stringified).
    ``source_scope_counts`` MUST be a plain ``dict`` with keys sorted
    ascending.

    Standard ``json.dumps(payload, ensure_ascii=False,
    separators=(",", ":"))`` MUST succeed without any custom
    ``default`` hook.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    payload = result.canonical_payload()

    # --- Top-level key order ---------------------------------------
    expected_top_keys = (
        "$schema",
        "record_id",
        "v1_metrics",
        "v2a_metrics",
        "chunk_count_delta",
        "chunk_count_reduction_basis_points",
        "vector_count_delta",
        "embedding_input_count_delta",
        "total_utf16_units_delta",
        "flattened_block_id_order_equal",
        "citation_coverage_equal",
    )
    assert tuple(payload.keys()) == expected_top_keys, (
        f"Top-level key order drift: {tuple(payload.keys())}"
    )
    assert payload["$schema"] == "article_rag_v1_v2a_plan_comparison_v1"
    assert payload["record_id"] == str(_RECORD_ID)
    assert isinstance(payload["record_id"], str)

    # --- Metrics key order -----------------------------------------
    expected_metrics_keys = (
        "index_version",
        "profile_fingerprint",
        "chunker_version",
        "plan_content_sha256",
        "chunk_count",
        "source_block_count",
        "merged_chunk_count",
        "max_blocks_per_chunk",
        "embedding_input_count",
        "vector_count",
        "total_embedding_input_utf16_units",
        "min_chunk_utf16_units",
        "max_chunk_utf16_units",
        "p50_chunk_utf16_units",
        "p95_chunk_utf16_units",
        "canonical_citation_count",
        "noncanonical_citation_count",
        "unit_reference_count",
        "anchor_segment_reference_count",
        "source_scope_counts",
    )
    assert tuple(payload["v1_metrics"].keys()) == expected_metrics_keys, (
        f"v1_metrics key order drift: {tuple(payload['v1_metrics'].keys())}"
    )
    assert tuple(payload["v2a_metrics"].keys()) == expected_metrics_keys, (
        f"v2a_metrics key order drift: {tuple(payload['v2a_metrics'].keys())}"
    )

    # --- source_scope_counts: plain dict, keys sorted ascending ----
    for metrics_key in ("v1_metrics", "v2a_metrics"):
        scope_counts = payload[metrics_key]["source_scope_counts"]
        assert isinstance(scope_counts, dict), (
            f"{metrics_key}.source_scope_counts must be a plain dict, "
            f"got {type(scope_counts).__name__}"
        )
        scope_keys = list(scope_counts.keys())
        assert scope_keys == sorted(scope_keys), (
            f"{metrics_key}.source_scope_counts keys not sorted: {scope_keys}"
        )

    # --- All leaf values MUST be JSON primitives -------------------
    def assert_jsonable(obj: object, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert_jsonable(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                assert_jsonable(v, f"{path}[{i}]")
        elif isinstance(obj, str | int | bool) or obj is None:
            pass
        else:
            raise AssertionError(
                f"Non-JSON value at {path}: {type(obj).__name__}={obj!r}"
            )

    assert_jsonable(payload, "payload")

    # --- Standard json.dumps MUST succeed with no custom default ---
    serialized = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    assert isinstance(serialized, str)
    # Round-trip back through json.loads must produce an equal dict.
    assert json.loads(serialized) == payload


async def test_p2b_r2_canonical_payload_fields_track_dataclasses(
    p2b_env: asyncpg.Pool,
) -> None:
    """Every dataclass field is represented once and in declaration order."""
    await _seed_v2a_two_paragraph_env(p2b_env)
    result = await _build_eval_service(p2b_env).compare_for_record(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    payload = result.canonical_payload()

    comparison_fields = tuple(
        field.name
        for field in dataclasses.fields(ArticleRagV1V2aPlanComparison)
    )
    assert tuple(key for key in payload if key != "$schema") == comparison_fields

    metrics_fields = tuple(
        field.name for field in dataclasses.fields(ArticleRagPlanShapeMetrics)
    )
    assert tuple(payload["v1_metrics"]) == metrics_fields
    assert tuple(payload["v2a_metrics"]) == metrics_fields


async def test_p2b_r2_canonical_payload_detached_mutation(
    p2b_env: asyncpg.Pool,
) -> None:
    """P2-B-R2: ``canonical_payload()`` MUST return a fresh detached
    snapshot on every call.

    Mutating the returned payload's top-level fields, nested metrics
    dict, or nested ``source_scope_counts`` dict MUST NOT affect:
      * the original ``ArticleRagV1V2aPlanComparison`` dataclass
      * the next ``canonical_payload()`` call
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    payload1 = result.canonical_payload()
    original_v1_chunk_count = payload1["v1_metrics"]["chunk_count"]
    original_delta = payload1["chunk_count_delta"]
    original_scope_keys = list(
        payload1["v1_metrics"]["source_scope_counts"].keys()
    )

    # Mutate three levels of nesting.
    payload1["chunk_count_delta"] = 999999
    payload1["v1_metrics"]["chunk_count"] = 999999
    payload1["v1_metrics"]["source_scope_counts"]["fake-scope"] = 999999
    if original_scope_keys:
        original_scope = original_scope_keys[0]
        payload1["v1_metrics"]["source_scope_counts"][original_scope] = -1

    # Second call MUST return a fresh, unmutated snapshot.
    payload2 = result.canonical_payload()
    assert payload2 is not payload1
    assert payload2["chunk_count_delta"] == original_delta
    assert payload2["v1_metrics"]["chunk_count"] == original_v1_chunk_count
    assert "fake-scope" not in payload2["v1_metrics"]["source_scope_counts"]
    if original_scope_keys:
        assert payload2["v1_metrics"]["source_scope_counts"][original_scope] != -1

    # Original dataclass field MUST remain unchanged.
    assert result.chunk_count_delta == original_delta
    assert result.v1_metrics.chunk_count == original_v1_chunk_count

    # Third call MUST also be fresh and equal to payload2.
    payload3 = result.canonical_payload()
    assert payload3 == payload2
    assert payload3 is not payload2


async def test_p2b_r2_canonical_payload_sentinel_safety(
    p2b_env: asyncpg.Pool,
) -> None:
    """P2-B-R2: sentinels planted in chunk text / block id / policy
    notes MUST NOT leak into ``canonical_payload()`` JSON,
    ``repr(payload)``, ``repr(result)``, or a fresh payload snapshot.

    The canonical payload is the public contract that downstream
    runners consume — it MUST NOT contain chunk text, block IDs, policy
    notes, URIs, API keys, SDK objects, or raw exceptions.
    """
    # Plant independent sentinels in chunk text, policy notes, and a
    # valid citation block ID. The comparison result must expose only
    # aggregate coverage booleans, never raw citation identifiers.
    block_id_sentinel = "P2B-R2-BLOCK-ID-SENTINEL"
    sentinels = (*_P2B_SENTINELS, block_id_sentinel)
    text_a = "First paragraph sk-ANT-sentinel123 contents."
    text_b = "Second paragraph https://attacker.example/payload end."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(p2b_env, base_text=base_text)
    await _seed_block(
        p2b_env,
        block_id=f"paragraph-1-{block_id_sentinel}",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(
            ["DashScopeError[sentinel]", "<script>\U0001f3af</script>"],
        ),
    )
    await _seed_block(
        p2b_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    payload = result.canonical_payload()
    payload_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    payload_repr = repr(payload)
    result_repr = repr(result)

    for sentinel in sentinels:
        assert sentinel not in payload_json, (
            f"Sentinel {sentinel!r} leaked into canonical_payload() JSON"
        )
        assert sentinel not in payload_repr, (
            f"Sentinel {sentinel!r} leaked into repr(payload)"
        )
        assert sentinel not in result_repr, (
            f"Sentinel {sentinel!r} leaked into repr(result)"
        )

    # Fresh payload snapshot MUST also be clean.
    fresh_payload = result.canonical_payload()
    fresh_json = json.dumps(
        fresh_payload, ensure_ascii=False, separators=(",", ":")
    )
    for sentinel in sentinels:
        assert sentinel not in fresh_json, (
            f"Sentinel {sentinel!r} leaked into fresh canonical_payload() JSON"
        )


async def test_p2b_r2_metrics_canonical_payload_matches_top_level(
    p2b_env: asyncpg.Pool,
) -> None:
    """P2-B-R2: ``ArticleRagPlanShapeMetrics.canonical_payload()`` MUST
    return a JSON-compatible dict whose key order matches the
    dataclass field declaration order, and whose values are detached
    JSON primitives.

    Both ``v1_metrics`` and ``v2a_metrics`` of the comparison's
    ``canonical_payload()`` MUST equal the corresponding
    ``ArticleRagPlanShapeMetrics.canonical_payload()`` snapshot.
    """
    await _seed_v2a_two_paragraph_env(p2b_env)
    service = _build_eval_service(p2b_env)
    result = await service.compare_for_record(
        record_id=_RECORD_ID, user_id=_USER_ID,
    )

    v1_payload = result.v1_metrics.canonical_payload()
    v2a_payload = result.v2a_metrics.canonical_payload()

    expected_metrics_keys = (
        "index_version",
        "profile_fingerprint",
        "chunker_version",
        "plan_content_sha256",
        "chunk_count",
        "source_block_count",
        "merged_chunk_count",
        "max_blocks_per_chunk",
        "embedding_input_count",
        "vector_count",
        "total_embedding_input_utf16_units",
        "min_chunk_utf16_units",
        "max_chunk_utf16_units",
        "p50_chunk_utf16_units",
        "p95_chunk_utf16_units",
        "canonical_citation_count",
        "noncanonical_citation_count",
        "unit_reference_count",
        "anchor_segment_reference_count",
        "source_scope_counts",
    )
    assert tuple(v1_payload.keys()) == expected_metrics_keys
    assert tuple(v2a_payload.keys()) == expected_metrics_keys

    # Standard json.dumps MUST succeed.
    json.dumps(v1_payload, ensure_ascii=False, separators=(",", ":"))
    json.dumps(v2a_payload, ensure_ascii=False, separators=(",", ":"))

    # The nested metrics inside the comparison's canonical_payload()
    # MUST equal the per-metrics canonical_payload().
    comparison_payload = result.canonical_payload()
    assert comparison_payload["v1_metrics"] == v1_payload
    assert comparison_payload["v2a_metrics"] == v2a_payload

    # Detached: mutating per-metrics payload MUST NOT affect the
    # comparison-level payload.
    v1_payload["chunk_count"] = -999
    fresh_comparison = result.canonical_payload()
    assert fresh_comparison["v1_metrics"]["chunk_count"] != -999
