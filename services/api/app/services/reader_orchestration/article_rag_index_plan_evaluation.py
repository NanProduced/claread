"""P2-B: Article RAG V1/V2a Offline Plan Comparison Harness.

This module is a pure, read-only comparison service that builds both a
V1 production plan and a V2a evaluation-only plan for the same record
and produces deterministic shape metrics + comparison deltas.

Strict contracts enforced by tests in
``tests/test_article_rag_index_plan_evaluation.py``:

  * **Read-only**: never writes to the database, never creates
    index-run / job / reader_run / vector / event rows, never publishes
    reader representation events.
  * **Zero external calls**: never calls embedding providers, vector
    writers/searchers, rerankers, or any external network service.
  * **No Settings/env/provider key reads**: the service is fully
    deterministic given the same record and same DB state.
  * **Shared consistent snapshot**: ``compare_for_record`` acquires a
    single connection and wraps both V1 + V2a builds in one read-only
    repeatable-read transaction; ``compare_for_record_in_transaction``
    requires an active caller-owned transaction and never acquires a pool.
  * **V1 via production seam**:
    ``ArticleRagIndexPlanService.build_index_plan_in_transaction``
    with explicit ``index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION``.
  * **V2a via evaluation seam**:
    ``ArticleRagIndexPlanService.build_evaluation_index_plan_in_transaction``
    with explicit ``index_version="article_rag_index_v2"``.
  * **Coverage invariant fail-closed**: V1/V2a citation
    ``block_ids`` flatten + ``citation_coverage`` tuple + ``source_scope_counts``
    key set MUST be equal; any drift raises
    :class:`ArticleRagIndexPlanEvaluationError` with a fixed local
    message and a clean exception chain (``__cause__ is None``,
    ``__context__ is None``).
  * **Sentinel rejection**: caller-supplied block id / text / policy
    notes content MUST NOT appear in error surfaces or result surfaces.
  * **Integer basis points**: all ratios are ``int`` basis points
    (1bp = 0.01%); divide-by-zero returns 0.
  * **UTF-16 via shared helper**: ``app.contracts.annotation.utf16_code_unit_length``
    is reused; ``len(text.encode("utf-16-le")) // 2`` is NOT
    re-implemented; Python ``len(text)`` is NOT used.
  * **Nearest-rank percentile frozen**: ``sorted_values[ceil(p/100 * n) - 1]``;
    ``n=0`` returns 0; ``n=1`` returns the single value.

This module does NOT:

  * register a production V2 profile
  * modify bootstrap / worker / retrieval / embedding adapter / vector
    store / migration
  * call embedding providers, Zilliz / Milvus, rerankers
  * compute pseudo-token / cost / recall / MRR / nDCG / rerank / latency
  * introduce a tokenizer
  * compute provider batch counts (explicitly deferred — no new magic
    numbers)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

import asyncpg

from app.contracts.annotation import utf16_code_unit_length
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfileResolution,
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_evaluation_profile,
    resolve_article_rag_index_profile,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fixed local error message used for ALL coverage invariant failures
# (block loss / reorder / duplicate / canonical range drift / source
# scope drift).  The message is a fixed local literal — caller-supplied
# values (block id / text / policy / URI / hash sentinel) are NEVER
# interpolated, echoed, truncated, or persisted in the error's ``str``,
# ``repr``, ``args``, or traceback.
_MSG_COVERAGE_INVARIANT_FAILED = "Article RAG plan coverage invariant failed"

# Fixed local error message used when wrapping lower-level errors
# (``ArticleRagIndexPlanError`` /
# ``ArticleRagIndexProfileResolutionError``) into
# :class:`ArticleRagIndexPlanEvaluationError`.  The wrapped error's
# ``args`` are NOT copied; ``__cause__`` and ``__context__`` are
# explicitly set to ``None`` to scrub the chain.
_MSG_PLAN_BUILD_FAILED = "Article RAG plan comparison failed to build plans"

_MSG_CALLER_TRANSACTION_REQUIRED = (
    "Article RAG plan evaluation requires an active caller-owned transaction"
)

# Explicit V2a evaluation index version string.  The comparison
# service passes this exact string to the evaluation builder — not a
# fallback, not a computed value, not a Settings/env read.
_V2A_INDEX_VERSION = "article_rag_index_v2"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArticleRagIndexPlanEvaluationError(ValueError):
    """Raised when the Article RAG V1/V2a plan comparison fails safely.

    All coverage-invariant drift (block loss / reorder / duplicate /
    canonical range drift / source scope drift) and lower-level error
    wrapping (``ArticleRagIndexPlanError`` /
    ``ArticleRagIndexProfileResolutionError``) raises this exception
    with a fixed local message and a clean exception chain.

    Caller-supplied values (block id / text / policy / URI / hash
    sentinel) are NEVER echoed in ``str(error)``, ``repr(error)``,
    ``error.args``, or ``traceback.format_exception(error)``.
    """


def _raise_clean(
    message: str,
) -> ArticleRagIndexPlanEvaluationError:
    """Construct a fixed-message evaluation error without a chain.

    Raising with ``from None`` suppresses implicit-context rendering,
    but a replacement for a caught lower-level error must additionally
    be raised only after leaving the ``except`` block so its
    ``__context__`` is actually ``None``.
    """
    err = ArticleRagIndexPlanEvaluationError(message)
    err.__cause__ = None
    err.__context__ = None
    return err


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagPlanShapeMetrics:
    """Per-version deterministic shape metrics for one Article RAG plan.

    All 20 fields are deterministic and contain NO chunk text, NO
    block id, NO URI, NO raw policy, NO SDK object, and NO secret.
    Only aggregate counts, frozen identity strings, hashes, and a
    read-only ``source_scope_counts`` mapping are exposed.

    Fields (canonical order, frozen via ``dataclass`` declaration):

      * ``index_version``: ``"article_rag_index_v1"`` or
        ``"article_rag_index_v2"``.
      * ``profile_fingerprint``: 64-char lowercase hex from
        :class:`ArticleRagIndexProfileResolution.profile_fingerprint`.
      * ``chunker_version``: ``plan.chunker_version``.
      * ``plan_content_sha256``: 64-char lowercase hex from
        :func:`compute_plan_content_sha256`.
      * ``chunk_count``: ``len(plan.chunks)``.
      * ``source_block_count``: sum of ``len(citation.block_ids)``
        across chunks (allows duplicates — merge does not dedupe).
      * ``merged_chunk_count``: count of chunks whose
        ``metadata_json["merged_block_count"] > 1``.
      * ``max_blocks_per_chunk``: max of ``merged_block_count``;
        0 for empty plan.
      * ``embedding_input_count``: equals ``chunk_count``; represents
        input entries, NOT provider request count.
      * ``vector_count``: equals ``chunk_count``; represents expected
        vector count, NOT written vector count.
      * ``total_embedding_input_utf16_units``: sum of chunk text
        UTF-16 code unit lengths (via ``utf16_code_unit_length``).
      * ``min_chunk_utf16_units``: min chunk UTF-16 length; 0 for
        empty plan.
      * ``max_chunk_utf16_units``: max chunk UTF-16 length; 0 for
        empty plan.
      * ``p50_chunk_utf16_units``: nearest-rank p50; 0 for empty plan.
      * ``p95_chunk_utf16_units``: nearest-rank p95; 0 for empty plan.
      * ``canonical_citation_count``: count of chunks with both
        ``canonical_text_start_utf16`` and ``canonical_text_end_utf16``
        non-None.
      * ``noncanonical_citation_count``: remaining chunks.
      * ``unit_reference_count``: sum of ``len(citation.unit_ids)``.
      * ``anchor_segment_reference_count``: sum of
        ``len(citation.anchor_segment_ids)``.
      * ``source_scope_counts``: ``MappingProxyType`` mapping
        ``chunk.source_scope`` to aggregate chunk count, sorted by key
        ascending.  Key set and order are frozen by this docstring.
    """

    index_version: str
    profile_fingerprint: str
    chunker_version: str
    plan_content_sha256: str
    chunk_count: int
    source_block_count: int
    merged_chunk_count: int
    max_blocks_per_chunk: int
    embedding_input_count: int
    vector_count: int
    total_embedding_input_utf16_units: int
    min_chunk_utf16_units: int
    max_chunk_utf16_units: int
    p50_chunk_utf16_units: int
    p95_chunk_utf16_units: int
    canonical_citation_count: int
    noncanonical_citation_count: int
    unit_reference_count: int
    anchor_segment_reference_count: int
    source_scope_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ArticleRagV1V2aPlanComparison:
    """Full deterministic comparison of V1 and V2a Article RAG plans
    for one record.

    All 10 fields are deterministic.  ``record_id`` is the only
    user-visible identifier exposed in the result; chunk text / block
    id / URI / policy notes MUST NOT appear in any field.

    Fields (canonical order, frozen via ``dataclass`` declaration):

      * ``record_id``: the compared record's UUID.
      * ``v1_metrics`` / ``v2a_metrics``:
        :class:`ArticleRagPlanShapeMetrics` for each version.
      * ``chunk_count_delta``: ``v2a.chunk_count - v1.chunk_count``.
      * ``chunk_count_reduction_basis_points``: integer basis points
        (1bp = 0.01%); ``round((v1 - v2a) / v1 * 10000)``; 0 when
        ``v1.chunk_count == 0``.
      * ``vector_count_delta``: ``v2a.vector_count - v1.vector_count``.
      * ``embedding_input_count_delta``:
        ``v2a.embedding_input_count - v1.embedding_input_count``.
      * ``total_utf16_units_delta``:
        ``v2a.total_embedding_input_utf16_units
           - v1.total_embedding_input_utf16_units``.
      * ``flattened_block_id_order_equal``: True iff V1/V2a citation
        ``block_ids`` flatten sequence is identical (length / order /
        values).  Drift raises
        :class:`ArticleRagIndexPlanEvaluationError` before this field
        is set, so a True value means the invariant held.
      * ``citation_coverage_equal``: True iff every V2a chunk is an
        exact projection of the next contiguous V1 per-block citation
        slice, including canonical boundaries, adjacency, and source
        scope.  Drift raises before this field is set.
    """

    record_id: UUID
    v1_metrics: ArticleRagPlanShapeMetrics
    v2a_metrics: ArticleRagPlanShapeMetrics
    chunk_count_delta: int
    chunk_count_reduction_basis_points: int
    vector_count_delta: int
    embedding_input_count_delta: int
    total_utf16_units_delta: int
    flattened_block_id_order_equal: bool
    citation_coverage_equal: bool


# ---------------------------------------------------------------------------
# Helpers (module-private, exposed for direct unit testing)
# ---------------------------------------------------------------------------


def _nearest_rank_percentile(values: list[int], percentile: int) -> int:
    """Nearest-rank percentile algorithm (frozen).

    Algorithm::

        if n == 0:
            return 0
        rank = ceil(percentile / 100 * n)   # 1-indexed rank
        index = rank - 1                    # 0-indexed
        return sorted_values[index]

    Frozen contract:

      * ``n == 0`` returns ``0`` (no values → no percentile).
      * ``n == 1`` returns the single value for any percentile.
      * ``percentile`` MUST be an ``int`` in ``[1, 100]``; behaviour
        outside this range is undefined and not tested.

    The test suite uses independent fixed literals (NOT a re-implementation
    of this algorithm) to assert expected results for ``n=0``, ``n=1``,
    ``n=2``, and ``n=3``.
    """
    n = len(values)
    if n == 0:
        return 0
    sorted_values = sorted(values)
    rank = math.ceil(percentile / 100 * n)
    if rank < 1:
        rank = 1
    if rank > n:
        rank = n
    return sorted_values[rank - 1]


def _flatten_block_ids(plan: ArticleRagIndexPlan) -> tuple[str, ...]:
    """Flatten all chunk citation ``block_ids`` in order.

    Returns a tuple of block ids in the order chunks appear in the
    plan, with duplicates preserved (no deduplication).  The returned
    tuple is the canonical sequence used for the
    ``flattened_block_id_order_equal`` comparison — set comparison
    MUST NOT be used because it would mask duplicates and reorder.
    """
    flat: list[str] = []
    for chunk in plan.chunks:
        flat.extend(chunk.citation.block_ids)
    return tuple(flat)








def _source_scope_counts(plan: ArticleRagIndexPlan) -> Mapping[str, int]:
    """Build a deterministic ``MappingProxyType`` of source scope counts.

    Keys are sorted ascending.  The returned mapping is a
    ``MappingProxyType`` wrapping a fresh dict — callers cannot mutate
    the underlying counts, and the iteration order is deterministic.
    """
    counts: dict[str, int] = {}
    for chunk in plan.chunks:
        counts[chunk.source_scope] = counts.get(chunk.source_scope, 0) + 1
    # Sort by key ascending — frozen contract.
    sorted_counts = {k: counts[k] for k in sorted(counts)}
    return MappingProxyType(sorted_counts)





def _build_shape_metrics(
    plan: ArticleRagIndexPlan,
    *,
    index_version: str,
    profile_fingerprint: str,
) -> ArticleRagPlanShapeMetrics:
    """Build :class:`ArticleRagPlanShapeMetrics` from a plan.

    The metrics are pure aggregates — chunk text, block id, URI,
    policy notes, and SDK objects are NOT retained.  UTF-16 lengths
    are computed via :func:`app.contracts.annotation.utf16_code_unit_length`
    (NOT Python ``len()`` and NOT a re-implementation).
    """
    chunk_count = len(plan.chunks)
    source_block_count = sum(
        len(c.citation.block_ids) for c in plan.chunks
    )

    merged_chunk_count = 0
    max_blocks_per_chunk = 0
    for c in plan.chunks:
        mbc = c.metadata_json.get("merged_block_count", 1)
        if isinstance(mbc, bool) or not isinstance(mbc, int):
            # Treat malformed / missing merged_block_count as 1
            # (single-block chunk).  This is a defensive fallback for
            # the metrics only — citation truth comes from
            # ``ArticleRagCitationRef``, never from metadata.
            mbc = 1
        if mbc > 1:
            merged_chunk_count += 1
        if mbc > max_blocks_per_chunk:
            max_blocks_per_chunk = mbc

    chunk_utf16_lengths = [
        utf16_code_unit_length(c.text) for c in plan.chunks
    ]
    total_utf16 = sum(chunk_utf16_lengths)
    min_utf16 = min(chunk_utf16_lengths) if chunk_utf16_lengths else 0
    max_utf16 = max(chunk_utf16_lengths) if chunk_utf16_lengths else 0
    p50 = _nearest_rank_percentile(chunk_utf16_lengths, 50)
    p95 = _nearest_rank_percentile(chunk_utf16_lengths, 95)

    canonical_count = 0
    noncanonical_count = 0
    for c in plan.chunks:
        if (
            c.citation.canonical_text_start_utf16 is not None
            and c.citation.canonical_text_end_utf16 is not None
        ):
            canonical_count += 1
        else:
            noncanonical_count += 1

    unit_reference_count = sum(
        len(c.citation.unit_ids) for c in plan.chunks
    )
    anchor_segment_reference_count = sum(
        len(c.citation.anchor_segment_ids) for c in plan.chunks
    )

    scope_counts = _source_scope_counts(plan)

    return ArticleRagPlanShapeMetrics(
        index_version=index_version,
        profile_fingerprint=profile_fingerprint,
        chunker_version=plan.chunker_version,
        plan_content_sha256=compute_plan_content_sha256(plan),
        chunk_count=chunk_count,
        source_block_count=source_block_count,
        merged_chunk_count=merged_chunk_count,
        max_blocks_per_chunk=max_blocks_per_chunk,
        embedding_input_count=chunk_count,
        vector_count=chunk_count,
        total_embedding_input_utf16_units=total_utf16,
        min_chunk_utf16_units=min_utf16,
        max_chunk_utf16_units=max_utf16,
        p50_chunk_utf16_units=p50,
        p95_chunk_utf16_units=p95,
        canonical_citation_count=canonical_count,
        noncanonical_citation_count=noncanonical_count,
        unit_reference_count=unit_reference_count,
        anchor_segment_reference_count=anchor_segment_reference_count,
        source_scope_counts=scope_counts,
    )


def _assert_coverage_invariant(
    v1_plan: ArticleRagIndexPlan,
    v2a_plan: ArticleRagIndexPlan,
) -> None:
    """Validate V2a chunks against ordered per-block V1 citation truth.

    V1 contributes the canonical span and source scope for each block.
    Every V2a chunk must consume the next contiguous slice of that
    ordered truth, preserve its exact block ids and source scope, and
    project the slice from its first canonical start through its last
    canonical end.  Multi-block slices must also retain the frozen
    two-UTF-16-unit canonical separator adjacency.

    Duplicate block ids, missing/reordered blocks, internal span drift,
    canonical gaps hidden by unchanged global extents, and per-block
    source-scope reassignment all fail closed with a fixed safe error.
    """
    v1_truth: list[tuple[str, int | None, int | None, str]] = []
    for chunk in v1_plan.chunks:
        for block_id in chunk.citation.block_ids:
            v1_truth.append(
                (
                    block_id,
                    chunk.citation.canonical_text_start_utf16,
                    chunk.citation.canonical_text_end_utf16,
                    chunk.source_scope,
                )
            )

    v1_block_ids = tuple(item[0] for item in v1_truth)
    v2a_block_ids = _flatten_block_ids(v2a_plan)
    if (
        v1_block_ids != v2a_block_ids
        or len(v1_block_ids) != len(set(v1_block_ids))
        or len(v2a_block_ids) != len(set(v2a_block_ids))
    ):
        raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None

    cursor = 0
    for chunk in v2a_plan.chunks:
        block_ids = tuple(chunk.citation.block_ids)
        if not block_ids:
            raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None

        expected = v1_truth[cursor : cursor + len(block_ids)]
        if (
            len(expected) != len(block_ids)
            or tuple(item[0] for item in expected) != block_ids
            or any(item[3] != chunk.source_scope for item in expected)
        ):
            raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None

        starts = tuple(item[1] for item in expected)
        ends = tuple(item[2] for item in expected)
        all_noncanonical = all(
            start is None and end is None
            for start, end in zip(starts, ends, strict=True)
        )
        all_canonical = all(
            start is not None and end is not None
            for start, end in zip(starts, ends, strict=True)
        )
        if not (all_noncanonical or all_canonical):
            raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None

        if all_noncanonical:
            expected_start = None
            expected_end = None
        else:
            expected_start = starts[0]
            expected_end = ends[-1]
            if len(expected) > 1:
                for previous_end, next_start in zip(
                    ends[:-1],
                    starts[1:],
                    strict=True,
                ):
                    if (
                        previous_end is None
                        or next_start is None
                        or next_start != previous_end + 2
                    ):
                        raise _raise_clean(
                            _MSG_COVERAGE_INVARIANT_FAILED
                        ) from None

        if (
            chunk.citation.canonical_text_start_utf16 != expected_start
            or chunk.citation.canonical_text_end_utf16 != expected_end
        ):
            raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None

        cursor += len(block_ids)

    if cursor != len(v1_truth):
        raise _raise_clean(_MSG_COVERAGE_INVARIANT_FAILED) from None


def _basis_points_reduction(numerator: int, denominator: int) -> int:
    """Compute integer basis points reduction
    ``round(numerator / denominator * 10000)``.

    Returns 0 on divide-by-zero (``denominator == 0``).  Result is
    always an ``int`` — no ``float`` or unfrozen ``Decimal`` context.
    """
    if denominator == 0:
        return 0
    return round(numerator * 10000 / denominator)


def _wrap_plan_error(
    err: BaseException,
) -> ArticleRagIndexPlanEvaluationError:
    """Wrap a lower-level plan/profile error into
    :class:`ArticleRagIndexPlanEvaluationError` with a scrubbed chain.

    The wrapped error's ``args`` are NOT copied.  ``__cause__`` and
    ``__context__`` are explicitly set to ``None`` to prevent any
    caller-supplied value (block id / text / policy / URI / hash
    sentinel) from leaking into the wrapped error's traceback.
    """
    wrapped = ArticleRagIndexPlanEvaluationError(_MSG_PLAN_BUILD_FAILED)
    wrapped.__cause__ = None
    wrapped.__context__ = None
    # Discard the original error — do NOT retain a reference.
    del err
    return wrapped


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagIndexPlanEvaluationService:
    """Read-only V1/V2a Article RAG plan comparison service.

    The service is pure / read-only: it never writes to the database,
    never calls embedding providers, never calls Zilliz / Milvus, and
    never publishes reader representation events.  Both V1 and V2a
    plans are built on the same acquired connection within a single
    read-only transaction, ensuring a consistent DB view.

    Usage:

        service = ArticleRagIndexPlanEvaluationService(pool=pool)
        result = await service.compare_for_record(
            record_id=record_id, user_id=user_id,
        )

    Or with a caller-owned connection (no pool acquisition, no new
    transaction):

        async with pool.acquire() as conn:
            async with conn.transaction(
                isolation="repeatable_read",
                readonly=True,
            ):
                result = await service.compare_for_record_in_transaction(
                    conn, record_id=record_id, user_id=user_id,
                )
    """

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        raise RuntimeError(
            "ArticleRagIndexPlanEvaluationService requires a pool to "
            "call compare_for_record; pass pool=... or use "
            "compare_for_record_in_transaction with a caller-owned conn."
        )

    async def compare_for_record(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
    ) -> ArticleRagV1V2aPlanComparison:
        """Compare V1 and V2a plans for one record.

        Acquires a single connection from the pool and wraps both V1
        and V2a builds in one read-only repeatable-read transaction,
        ensuring both plans see the same consistent DB snapshot.  No
        DB writes, provider calls, or vector calls are performed.

        Raises :class:`ArticleRagIndexPlanEvaluationError` on any
        coverage-invariant drift or wrapped lower-level plan/profile
        error (with a scrubbed exception chain — caller-supplied
        values are never echoed).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction(
                isolation="repeatable_read",
                readonly=True,
            ):
                return await self.compare_for_record_in_transaction(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    include_rag_ask_only=include_rag_ask_only,
                )

    async def compare_for_record_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
    ) -> ArticleRagV1V2aPlanComparison:
        """Compare V1 and V2a plans on a caller-owned connection.

        Requires an active caller-owned transaction and fails closed
        before any plan build when none is active.  Does NOT acquire a
        pool, open a transaction, or commit / rollback the caller's
        transaction.  Both plans use the same caller-owned connection.

        Raises :class:`ArticleRagIndexPlanEvaluationError` on any
        coverage-invariant drift or wrapped lower-level plan/profile
        error (with a scrubbed exception chain — caller-supplied
        values are never echoed).
        """
        if not conn.is_in_transaction():
            raise _raise_clean(_MSG_CALLER_TRANSACTION_REQUIRED) from None

        # Reuse a plan service bound to no pool — we always pass an
        # explicit ``conn``.  No new pool is acquired.
        plan_service = ArticleRagIndexPlanService(pool=None)

        # Resolve V1 + V2a profile fingerprints via public resolvers.
        # We do NOT read private ``_REGISTRY``.
        resolution_error: ArticleRagIndexPlanEvaluationError | None = None
        v1_resolution: ArticleRagIndexProfileResolution | None = None
        v2a_resolution: ArticleRagIndexProfileResolution | None = None
        try:
            v1_resolution = (
                resolve_article_rag_index_profile(
                    DEFAULT_ARTICLE_RAG_INDEX_VERSION
                )
            )
            v2a_resolution = (
                resolve_article_rag_index_evaluation_profile(
                    _V2A_INDEX_VERSION
                )
            )
        except ArticleRagIndexProfileResolutionError as exc:
            resolution_error = _wrap_plan_error(exc)
        if resolution_error is not None:
            raise resolution_error
        if v1_resolution is None or v2a_resolution is None:
            raise _raise_clean(_MSG_PLAN_BUILD_FAILED) from None

        # Build V1 plan via production seam.
        v1_plan_error: ArticleRagIndexPlanEvaluationError | None = None
        v1_plan: ArticleRagIndexPlan | None = None
        try:
            v1_plan = await plan_service.build_index_plan_in_transaction(
                conn,
                record_id=record_id,
                user_id=user_id,
                index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
                include_rag_ask_only=include_rag_ask_only,
            )
        except (ArticleRagIndexPlanError, ArticleRagIndexProfileResolutionError) as exc:
            v1_plan_error = _wrap_plan_error(exc)
        if v1_plan_error is not None:
            raise v1_plan_error
        if v1_plan is None:
            raise _raise_clean(_MSG_PLAN_BUILD_FAILED) from None

        # Build V2a plan via evaluation seam.
        v2a_plan_error: ArticleRagIndexPlanEvaluationError | None = None
        v2a_plan: ArticleRagIndexPlan | None = None
        try:
            v2a_plan = await plan_service.build_evaluation_index_plan_in_transaction(
                conn,
                record_id=record_id,
                user_id=user_id,
                index_version=_V2A_INDEX_VERSION,
                include_rag_ask_only=include_rag_ask_only,
            )
        except (ArticleRagIndexPlanError, ArticleRagIndexProfileResolutionError) as exc:
            v2a_plan_error = _wrap_plan_error(exc)
        if v2a_plan_error is not None:
            raise v2a_plan_error
        if v2a_plan is None:
            raise _raise_clean(_MSG_PLAN_BUILD_FAILED) from None

        # Build per-version shape metrics.
        v1_metrics = _build_shape_metrics(
            v1_plan,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
            profile_fingerprint=v1_resolution.profile_fingerprint,
        )
        v2a_metrics = _build_shape_metrics(
            v2a_plan,
            index_version=_V2A_INDEX_VERSION,
            profile_fingerprint=v2a_resolution.profile_fingerprint,
        )

        # Coverage invariant — fail-closed BEFORE returning any result.
        _assert_coverage_invariant(v1_plan, v2a_plan)

        # Compute deltas and basis points.
        chunk_count_delta = v2a_metrics.chunk_count - v1_metrics.chunk_count
        chunk_count_reduction_basis_points = _basis_points_reduction(
            v1_metrics.chunk_count - v2a_metrics.chunk_count,
            v1_metrics.chunk_count,
        )
        vector_count_delta = v2a_metrics.vector_count - v1_metrics.vector_count
        embedding_input_count_delta = (
            v2a_metrics.embedding_input_count - v1_metrics.embedding_input_count
        )
        total_utf16_units_delta = (
            v2a_metrics.total_embedding_input_utf16_units
            - v1_metrics.total_embedding_input_utf16_units
        )

        return ArticleRagV1V2aPlanComparison(
            record_id=record_id,
            v1_metrics=v1_metrics,
            v2a_metrics=v2a_metrics,
            chunk_count_delta=chunk_count_delta,
            chunk_count_reduction_basis_points=chunk_count_reduction_basis_points,
            vector_count_delta=vector_count_delta,
            embedding_input_count_delta=embedding_input_count_delta,
            total_utf16_units_delta=total_utf16_units_delta,
            flattened_block_id_order_equal=True,
            citation_coverage_equal=True,
        )
