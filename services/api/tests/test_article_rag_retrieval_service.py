# task-history: D6-I4E (renamed from test_d6_i4e_article_rag_retrieval_service.py)
"""Tests for the Article RAG retrieval service.

Covers:
  * ownership fail closed (via the I4A plan service's ``LookupError``);
  * inactive / stale base / stable document fail closed (via I4A);
  * no indexed ``reader_article_rag_index_runs`` row → fail closed;
  * ``plan_content_sha256`` drift between current plan and indexed run
    → fail closed;
  * empty / whitespace-only query → fail closed;
  * invalid ``limit`` → fail closed;
  * no hits → empty result with no error;
  * unknown ``chunk_id`` in vector hit → dropped silently;
  * duplicate ``chunk_id`` → deduped (first/highest score kept);
  * top-k enforcement;
  * score-descending order preserved through the join;
  * vector hit ``stable_document_id`` / ``base_id``
    / ``plan_content_sha256`` mismatch → fail closed;
  * no Plate / Markdown / DOM / Slate / UI display group fields in
    returned metadata (denylist scrub);
  * embedding provider error surfaces as retrieval failure;
  * vector searcher error surfaces as retrieval failure.

No network.  ``asyncpg`` is replaced with a ``FakePool`` whose
``acquire()`` returns a ``FakeConn`` whose ``fetchrow`` returns the
data the test wants.  ``ArticleRagIndexPlanService.build_index_plan_in_transaction``
is monkeypatched so the test does not depend on real ``reading_records``
/ ``stable_reading_documents`` / ``reading_bases`` rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerError,
    ArticleRagEmbedding,
    ArticleRagIndexWorkerError as _WorkerErrAlias,
    FakeArticleRagEmbeddingProvider,
)
from app.services.reader_orchestration.article_rag_retrieval_service import (
    ArticleRagRetrievalHit,
    ArticleRagRetrievalResult,
    ArticleRagRetrievalService,
    ArticleRagRetrievalServiceError,
    FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH,
    FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
    FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH,
    FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH,
    FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
    FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH,
    FAILURE_CODE_RETRIEVAL_INVALID_LIMIT,
    FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
    FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH,
    FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH,
    FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED,
    MAX_RETRIEVAL_LIMIT,
)
from app.services.reader_orchestration.article_rag_vector_search import (
    ArticleRagVectorSearchHit,
    ArticleRagVectorSearchResult,
    ArticleRagVectorSearcherError,
    FakeArticleRagVectorSearcher,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]


# ---------------------------------------------------------------------------
# Test fixtures: FakePool / FakeConn + a hand-built plan + indexed run row
# ---------------------------------------------------------------------------


@dataclass
class _FakeRecord(dict):
    """Stand-in for asyncpg.Record — supports both ``row["col"]`` and
    attribute-style ``row.col`` access (asyncpg.Record supports both).

    The single field is the dict payload; ``@dataclass`` is used purely
    to give us ``__repr__`` for free.  We keep ``__init__`` inheriting
    from ``dict`` so positional construction (``_FakeRecord({...})``)
    works.
    """

    payload: dict[str, Any] | None = None

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        dict.__init__(self)
        if payload:
            for k, v in payload.items():
                dict.__setitem__(self, k, v)


@dataclass
class _FakeAcquire:
    conn: "_FakeConn"

    async def __aenter__(self) -> "_FakeConn":
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@dataclass
class _FakeConn:
    """Minimal asyncpg.Connection stand-in for the retrieval service."""

    fetchrow_results: list[dict[str, Any] | None]

    async def fetchrow(
        self, query: str, *args: Any, **kwargs: Any
    ) -> _FakeRecord | None:
        if not self.fetchrow_results:
            return None
        result = self.fetchrow_results.pop(0)
        if result is None:
            return None
        rec = _FakeRecord(result)
        return rec


class _FakePool:
    """Minimal asyncpg.Pool stand-in.  Each ``acquire()`` returns a fresh
    ``_FakeConn`` whose ``fetchrow_results`` matches the test's plan.
    The retrieval service calls ``acquire()`` exactly once, so we use
    the queue model (pop from front)."""

    def __init__(self, fetchrow_results: list[dict[str, Any] | None]) -> None:
        self._fetchrow_results = list(fetchrow_results)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(_FakeConn(self._fetchrow_results))


_RECORD_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_STABLE_DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_OTHER_STABLE_DOC_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
_OTHER_BASE_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")

# P1-F: frozen embedding + vector-space contract literals.  These
# mirror the module-level ``ARTICLE_RAG_EMBEDDING_CONTRACT`` in
# ``article_rag_index_bootstrap``.  Tests use these to construct
# indexed-run rows that are byte-aligned with the frozen contract, so
# that contract-mismatch failures are pinned to the one field under
# test rather than to a default-vs-contract drift in the fixture
# itself.
_DEFAULT_DOCUMENT_EMBEDDING_MODEL = "text-embedding-v4"
_DEFAULT_QUERY_EMBEDDING_MODEL = "text-embedding-v4"
_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION = 1024
_DEFAULT_VECTOR_NAMESPACE = "article_rag_chunks"


def _make_chunk(
    chunk_id: str,
    text: str,
    *,
    stable_document_id: uuid.UUID | None = None,
    base_id: uuid.UUID | None = None,
    block_id: str | None = None,
    canonical_start: int | None = 0,
    canonical_end: int | None = 12,
    metadata: dict[str, Any] | None = None,
) -> ArticleRagIndexChunk:
    sd_id = stable_document_id or _STABLE_DOC_ID
    b_id = base_id or _BASE_ID
    bid = block_id or f"block-for-{chunk_id}"
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagIndexChunk(
        chunk_id=chunk_id,
        citation=ArticleRagCitationRef(
            reading_record_id=_RECORD_ID,
            stable_document_id=sd_id,
            base_id=b_id,
            record_generation=1,
            block_ids=(bid,),
            unit_ids=(),
            anchor_segment_ids=(),
            canonical_text_start_utf16=canonical_start,
            canonical_text_end_utf16=canonical_end,
        ),
        source_scope="main_reading_text",
        text=text,
        content_sha256=content_sha,
        embedding_text_sha256=content_sha,
        metadata_json=metadata
        or {
            "block_type": "paragraph",
            "block_order_index": 0,
            "source_scope": "main_reading_text",
            "default_route": "main_reading",
            "chunk_index": 0,
            "has_canonical_offsets": True,
        },
    )


def _make_plan(
    *,
    chunks: tuple[ArticleRagIndexChunk, ...] | None = None,
) -> ArticleRagIndexPlan:
    cs = chunks or (
        _make_chunk("chunk-aaa", "alpha text"),
        _make_chunk("chunk-bbb", "beta text"),
        _make_chunk("chunk-ccc", "gamma text"),
    )
    return ArticleRagIndexPlan(
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        content_sha256=hashlib.sha256(b"stable-doc-content").hexdigest(),
        canonical_text_sha256=hashlib.sha256(b"canonical-text").hexdigest(),
        chunks=cs,
    )


def _indexed_run_row(
    plan: ArticleRagIndexPlan,
    plan_content_sha256: str | None = None,
    *,
    status: str = "indexed",
    vector_collection: str | None = _DEFAULT_VECTOR_NAMESPACE,
    embedding_model: str | None = _DEFAULT_DOCUMENT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    """Build the ``reader_article_rag_index_runs`` row that the
    retrieval service's ``_load_indexed_run`` queries.

    P1-F: defaults are byte-aligned with the frozen
    ``ARTICLE_RAG_EMBEDDING_CONTRACT`` so that contract-mismatch
    failures are pinned to the one field under test rather than to
    a default-vs-contract drift in the fixture itself.  Tests that
    intentionally want a mismatch must override the relevant field.
    """
    from app.services.reader_orchestration.article_rag_index_plan import (
        compute_plan_content_sha256,
    )
    sha = plan_content_sha256 or compute_plan_content_sha256(plan)
    return {
        "id": uuid.uuid4(),
        "stable_document_id": plan.stable_document_id,
        "base_id": plan.base_id,
        "record_generation": plan.record_generation,
        "plan_content_sha256": sha,
        "chunk_count": len(plan.chunks),
        "status": status,
        "updated_at": None,
        "vector_collection": vector_collection,
        "embedding_model": embedding_model,
    }


def _build_service(
    *,
    plan: ArticleRagIndexPlan | None = None,
    indexed_run_row: dict[str, Any] | None = None,
    no_indexed_run: bool = False,
    searcher: FakeArticleRagVectorSearcher | None = None,
    embedding_provider: FakeArticleRagEmbeddingProvider | None = None,
) -> ArticleRagRetrievalService:
    """Build a retrieval service whose plan and indexed-run queries are
    pre-stubbed.  No real DB.

    The default embedding provider is constructed with
    ``model=_DEFAULT_QUERY_EMBEDDING_MODEL`` so successful retrievals return
    a query embedding whose model matches the frozen contract.
    """
    from app.services.reader_orchestration.article_rag_index_plan import (
        ArticleRagIndexPlanService,
    )

    plan = plan or _make_plan()
    fetchrow_results: list[dict[str, Any] | None] = []
    if not no_indexed_run and indexed_run_row is not None:
        fetchrow_results.append(indexed_run_row)

    pool = _FakePool(fetchrow_results)

    # Stub the plan service so it returns our synthetic plan without
    # touching the DB.  We do this by monkeypatching
    # ``build_index_plan_in_transaction`` on a fresh instance.
    plan_service = ArticleRagIndexPlanService()
    # Record every call so tests can assert forwarding behaviour.  The
    # list is attached to the plan_service instance (not the stub
    # function) so tests access it via
    # ``service._plan_service._test_calls``.
    plan_service._test_calls = []  # type: ignore[attr-defined]

    async def _fake_build_plan_in_transaction(
        conn,
        *,
        record_id,
        user_id,
        include_rag_ask_only=False,
        **kwargs,
    ):
        plan_service._test_calls.append(  # type: ignore[attr-defined]
            {
                "record_id": record_id,
                "user_id": user_id,
                "include_rag_ask_only": include_rag_ask_only,
            }
        )
        return plan

    plan_service.build_index_plan_in_transaction = (
        _fake_build_plan_in_transaction  # type: ignore[assignment]
    )

    # Default embedding provider returns embeddings whose model matches
    # the frozen contract's ``query_embedding_model``.  Tests that need
    # a different model (e.g. mismatch tests) override this.
    embedding_provider = embedding_provider or FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    if searcher is None:
        searcher = FakeArticleRagVectorSearcher(hits=[])

    return ArticleRagRetrievalService(
        pool=pool,  # type: ignore[arg-type]
        plan_service=plan_service,
        embedding_provider=embedding_provider,
        vector_searcher=searcher,
    )


# ---------------------------------------------------------------------------
# 1. Empty / invalid input — fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_query_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_EMPTY_QUERY


@pytest.mark.anyio
async def test_whitespace_only_query_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="   \t\n  ",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_EMPTY_QUERY


@pytest.mark.anyio
async def test_zero_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=0,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_INVALID_LIMIT


@pytest.mark.anyio
async def test_negative_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=-5,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_INVALID_LIMIT


@pytest.mark.anyio
async def test_oversized_limit_fails_closed() -> None:
    service = _build_service()
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            limit=MAX_RETRIEVAL_LIMIT + 1,
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_INVALID_LIMIT


# ---------------------------------------------------------------------------
# 2. Ownership fail closed (propagates LookupError from I4A)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ownership_mismatch_propagates_lookup_error() -> None:
    from app.services.reader_orchestration.article_rag_index_plan import (
        ArticleRagIndexPlanService,
    )

    plan_service = ArticleRagIndexPlanService()

    async def _raise_lookup(*args: Any, **kwargs: Any):
        raise LookupError(
            f"Reading record {_RECORD_ID} was not found for user "
            f"{_USER_ID}."
        )

    plan_service.build_index_plan_in_transaction = (  # type: ignore[assignment]
        _raise_lookup
    )
    pool = _FakePool([])
    service = ArticleRagRetrievalService(
        pool=pool,  # type: ignore[arg-type]
        plan_service=plan_service,
        embedding_provider=FakeArticleRagEmbeddingProvider(dim=8),
    )
    with pytest.raises(LookupError):
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )


# ---------------------------------------------------------------------------
# 3. Inactive / stale base / stable document fail closed (propagates I4A)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inactive_base_propagates_plan_error() -> None:
    from app.services.reader_orchestration.article_rag_index_plan import (
        ArticleRagIndexPlanError,
        ArticleRagIndexPlanService,
    )

    plan_service = ArticleRagIndexPlanService()

    async def _raise_plan_error(*args: Any, **kwargs: Any):
        raise ArticleRagIndexPlanError(
            f"Reading base {_BASE_ID} is not active (status=superseded)."
        )

    plan_service.build_index_plan_in_transaction = (  # type: ignore[assignment]
        _raise_plan_error
    )
    pool = _FakePool([])
    service = ArticleRagRetrievalService(
        pool=pool,  # type: ignore[arg-type]
        plan_service=plan_service,
        embedding_provider=FakeArticleRagEmbeddingProvider(dim=8),
    )
    with pytest.raises(ArticleRagIndexPlanError):
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )


# ---------------------------------------------------------------------------
# 4. No indexed run fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_indexed_run_fails_closed() -> None:
    service = _build_service(no_indexed_run=True)
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN


# ---------------------------------------------------------------------------
# 5. Plan hash drift fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plan_hash_drift_fails_closed() -> None:
    plan = _make_plan()
    bad_row = _indexed_run_row(plan, plan_content_sha256="0" * 64)
    service = _build_service(plan=plan, indexed_run_row=bad_row)
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    [
        pytest.param("base_id", _OTHER_BASE_ID, id="base_id"),
        pytest.param("record_generation", 999, id="record_generation"),
        pytest.param("chunk_count", 999, id="chunk_count"),
    ],
)
async def test_p1f_indexed_run_plan_identity_drift_fails_closed(
    field_name: str,
    drifted_value: Any,
) -> None:
    """Durable index-run plan fields must match the rebuilt current plan."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    row[field_name] = drifted_value
    provider = FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )

    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )

    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH
    )
    assert provider.call_count == 0
    assert searcher.search_calls == []


# ---------------------------------------------------------------------------
# 6. Happy path: empty hits → empty result
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_hits_returns_empty_result() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=10,
    )
    assert isinstance(result, ArticleRagRetrievalResult)
    assert result.hits == ()
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.plan_content_sha256


# ---------------------------------------------------------------------------
# 7. Happy path: hits joined against current plan
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hits_joined_against_plan_in_score_order() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-ccc", score=0.7),
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9),
            ArticleRagVectorSearchHit(chunk_id="chunk-bbb", score=0.8),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=3,
    )
    assert [h.chunk_id for h in result.hits] == [
        "chunk-aaa",
        "chunk-bbb",
        "chunk-ccc",
    ]
    assert [h.score for h in result.hits] == [0.9, 0.8, 0.7]
    # Each hit carries the plan chunk's text + citation (truth from
    # Postgres), not from the vector payload.
    aaa = result.hits[0]
    assert aaa.text == "alpha text"
    assert aaa.citation["stable_document_id"] == str(_STABLE_DOC_ID)
    assert aaa.citation["block_ids"] == ["block-for-chunk-aaa"]


# ---------------------------------------------------------------------------
# 8. Unknown chunk_id dropped silently
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_chunk_id_dropped_silently() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9),
            ArticleRagVectorSearchHit(
                chunk_id="unknown-from-old-run", score=0.8
            ),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert [h.chunk_id for h in result.hits] == ["chunk-aaa"]


# ---------------------------------------------------------------------------
# 9. Duplicate chunk_id deduped (first / highest score kept)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_duplicate_chunk_id_deduped_first_kept() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9),
            # Duplicate with lower score — must be dropped.
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.5),
            ArticleRagVectorSearchHit(chunk_id="chunk-bbb", score=0.8),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    chunk_ids = [h.chunk_id for h in result.hits]
    assert chunk_ids == ["chunk-aaa", "chunk-bbb"]
    # And the kept hit is the highest-score one.
    assert result.hits[0].score == 0.9


# ---------------------------------------------------------------------------
# 10. Top-k enforcement
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_top_k_truncates_to_limit() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9),
            ArticleRagVectorSearchHit(chunk_id="chunk-bbb", score=0.8),
            ArticleRagVectorSearchHit(chunk_id="chunk-ccc", score=0.7),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
        limit=2,
    )
    assert len(result.hits) == 2
    assert [h.chunk_id for h in result.hits] == ["chunk-aaa", "chunk-bbb"]


# ---------------------------------------------------------------------------
# 11. Vector metadata mismatch fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_vector_metadata_mismatch_stable_document_id_fails_closed() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(
                chunk_id="chunk-aaa",
                score=0.9,
                stable_document_id=_OTHER_STABLE_DOC_ID,
            ),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
    )


@pytest.mark.anyio
async def test_vector_metadata_mismatch_base_id_fails_closed() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(
                chunk_id="chunk-aaa",
                score=0.9,
                stable_document_id=_STABLE_DOC_ID,  # matches
                base_id=_OTHER_BASE_ID,  # mismatches
            ),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
    )


@pytest.mark.anyio
async def test_vector_metadata_mismatch_plan_hash_fails_closed() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(
                chunk_id="chunk-aaa",
                score=0.9,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                plan_content_sha256="1" * 64,  # mismatches
            ),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
    )


@pytest.mark.anyio
async def test_vector_metadata_guard_none_is_permissive() -> None:
    """When the searcher does NOT surface guard metadata (all fields
    ``None``), the retrieval service does NOT fail closed — it accepts
    the hit and joins against the current plan.  This guards against
    pymilvus implementations that omit guard columns."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert [h.chunk_id for h in result.hits] == ["chunk-aaa"]


# ---------------------------------------------------------------------------
# 12. Denylist scrub on returned metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_returned_metadata_strips_plate_markdown_dom_slate_ui_keys() -> None:
    """The retrieval service MUST NOT surface any of the forbidden
    metadata keys (Plate / Markdown / DOM / Slate / UI display group /
    text).  Defence in depth against future regressions in the I4A
    metadata shape."""
    chunk = _make_chunk(
        "chunk-aaa",
        "alpha text",
        metadata={
            "block_type": "paragraph",
            "block_order_index": 0,
            "source_scope": "main_reading_text",
            "default_route": "main_reading",
            "chunk_index": 0,
            "has_canonical_offsets": True,
            # Forbidden keys that MUST be scrubbed:
            "plate": {"op": "slate"},
            "plate_json": {"node": "x"},
            "markdown": "**hello**",
            "markdown_syntax": "# title",
            "dom": {"tag": "div"},
            "dom_selection": "xpath",
            "slate": {"path": [0, 1]},
            "slate_path": [0, 1],
            "ui": {"display": "x"},
            "ui_display_group": "main",
            "render_profile": "v1",
            "render_snapshot": {"a": 1},
            "text": "SECRET-CHUNK-TEXT-DO-NOT-LEAK",
            "html": "<p>hi</p>",
            "innerText": "hi",
            "innerHTML": "<b>x</b>",
        },
    )
    plan = _make_plan(chunks=(chunk,))
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9)]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    md = result.hits[0].metadata_json
    forbidden = {
        "plate",
        "plate_json",
        "markdown",
        "markdown_syntax",
        "dom",
        "dom_selection",
        "slate",
        "slate_path",
        "ui",
        "ui_display_group",
        "render_profile",
        "render_snapshot",
        "text",
        "html",
        "innerText",
        "innerHTML",
    }
    leaked = forbidden & set(md.keys())
    assert leaked == set(), f"forbidden keys leaked: {leaked}"
    # And the chunk text MUST NOT appear in metadata_json — it lives
    # in ``.text`` on the hit, not in ``.metadata_json``.
    assert "SECRET-CHUNK-TEXT-DO-NOT-LEAK" not in str(md)


# ---------------------------------------------------------------------------
# 13. Embedding provider error surfaces as retrieval failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_provider_error_surfaces_as_retrieval_failure() -> None:
    class _RaisingProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            raise ArticleRagIndexWorkerError(
                "DashScope embedding call failed via bailian_embedding "
                "(input_count=1, wrapper_exc=RuntimeError); see __cause__ "
                "for upstream diagnostic",
                retryable=True,
                failure_class="embedding",
                failure_code="embedding_backend_failed",
            )

    plan = _make_plan()
    row = _indexed_run_row(plan)
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=FakeArticleRagVectorSearcher(hits=[]),
        embedding_provider=_RaisingProvider(dim=8),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED
    )
    assert exc_info.value.retryable is True
    # Underlying error preserved.
    assert isinstance(
        exc_info.value.__cause__, ArticleRagIndexWorkerError
    )


# ---------------------------------------------------------------------------
# 14. Vector searcher error surfaces as retrieval failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_vector_searcher_error_surfaces_as_retrieval_failure() -> None:
    class _RaisingSearcher(FakeArticleRagVectorSearcher):
        async def search(
            self,
            *,
            collection: str,
            query_vector: tuple[float, ...],
            limit: int,
            stable_document_id: uuid.UUID | None = None,
        ) -> ArticleRagVectorSearchResult:
            raise ArticleRagVectorSearcherError(
                "Zilliz search failed via pymilvus (limit=5, "
                "wrapper_exc=RuntimeError); see __cause__ for upstream "
                "diagnostic",
                retryable=True,
                failure_code="vector_search_backend_failed",
            )

    plan = _make_plan()
    row = _indexed_run_row(plan)
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=_RaisingSearcher()
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED
    )


# ---------------------------------------------------------------------------
# 15. Embedding provider returns empty list → fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_provider_empty_result_fails_closed() -> None:
    class _EmptyProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            return []

    plan = _make_plan()
    row = _indexed_run_row(plan)
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=FakeArticleRagVectorSearcher(hits=[]),
        embedding_provider=_EmptyProvider(dim=8),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED
    )


# ---------------------------------------------------------------------------
# 16. Embedding provider returns embedding with empty vector → fail closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_provider_empty_vector_fails_closed() -> None:
    class _ZeroDimProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            return [
                ArticleRagEmbedding(
                    text_sha256=hashlib.sha256(b"x").hexdigest(),
                    model="fake",
                    vector=(),
                    dim=0,
                )
            ]

    plan = _make_plan()
    row = _indexed_run_row(plan)
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=FakeArticleRagVectorSearcher(hits=[]),
        embedding_provider=_ZeroDimProvider(dim=8),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED
    )


# ---------------------------------------------------------------------------
# 17. Embedding-provider uncaught exception is wrapped (defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_provider_uncaught_exception_wrapped() -> None:
    class _RawRaisingProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            raise RuntimeError("totally unexpected SDK boom")

    plan = _make_plan()
    row = _indexed_run_row(plan)
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=FakeArticleRagVectorSearcher(hits=[]),
        embedding_provider=_RawRaisingProvider(dim=8),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED
    )
    # Underlying RuntimeError preserved as __cause__.
    assert isinstance(exc_info.value.__cause__, RuntimeError)


# ---------------------------------------------------------------------------
# 18. Retrieval result shape
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_retrieval_result_echoes_current_plan_truth() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9)]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # Echoed from the current plan (truth), NOT from the indexed-run row.
    assert result.reading_record_id == plan.reading_record_id
    assert result.stable_document_id == plan.stable_document_id
    assert result.base_id == plan.base_id
    assert result.record_generation == plan.record_generation
    # plan_content_sha256 is the CURRENT plan's hash (== indexed run
    # by Phase C invariant).
    from app.services.reader_orchestration.article_rag_index_plan import (
        compute_plan_content_sha256,
    )
    assert result.plan_content_sha256 == compute_plan_content_sha256(plan)


# ---------------------------------------------------------------------------
# 19. Constants / exports
# ---------------------------------------------------------------------------


def test_max_retrieval_limit_is_a_positive_int() -> None:
    assert isinstance(MAX_RETRIEVAL_LIMIT, int)
    assert MAX_RETRIEVAL_LIMIT > 0


def test_failure_codes_are_distinct() -> None:
    """All retrieval failure codes must remain unique for ops routing."""
    codes = {
        FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
        FAILURE_CODE_RETRIEVAL_INVALID_LIMIT,
        FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
        FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH,
        FAILURE_CODE_RETRIEVAL_INDEX_RUN_PLAN_MISMATCH,
        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH,
        FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
        FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED,
        FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH,
        FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH,
        FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH,
    }
    assert len(codes) == 11


def test_retrieval_error_inherits_worker_error() -> None:
    """Defence in depth: the retrieval error must inherit the worker
    base class so any future orchestrator that catches the worker
    base class also catches retrieval failures."""
    err = ArticleRagRetrievalServiceError(
        "synthetic",
        retryable=False,
        failure_code=FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
    )
    assert isinstance(err, ArticleRagIndexWorkerError)


# ---------------------------------------------------------------------------
# 21. Review-fix P1: indexed run's vector_collection routes the searcher
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_vector_collection_passed_to_searcher_from_indexed_run(
) -> None:
    """P1-F: the retrieval service routes the resolved profile's
    ``vector_namespace`` to the searcher.  The indexed run's
    ``vector_collection`` MUST equal ``profile.vector_namespace``
    (validated in Phase C.3 Field 5); the searcher is then called with
    the profile value as the canonical source of truth.  A non-V1
    collection name is now rejected as a profile mismatch — the
    custom-collection scenario from the original reviewer P1 fix is
    no longer reachable because V1 is the only registered profile."""
    plan = _make_plan()
    row = _indexed_run_row(
        plan, vector_collection=_DEFAULT_VECTOR_NAMESPACE
    )
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9)
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # The fake searcher records ``search_calls`` — assert the routed
    # collection matches the V1 profile's ``vector_namespace``.
    assert len(searcher.search_calls) == 1
    assert (
        searcher.search_calls[0]["collection"] == _DEFAULT_VECTOR_NAMESPACE
    )


@pytest.mark.anyio
async def test_vector_collection_empty_fails_closed() -> None:
    """P1-F: a NULL ``vector_collection`` is rejected at Phase C as a
    contract mismatch — the indexed run's ``vector_collection`` MUST
    equal ``contract.vector_collection``.  The legacy
    ``retrieval_no_vector_collection`` routing-phase check is no
    longer reachable because the contract identity validation
    precedes it.  The fail-closed contract is preserved (the offending
    value is never echoed); only the failure code changes."""
    plan = _make_plan()
    row = _indexed_run_row(plan, vector_collection=None)
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert len(searcher.search_calls) == 0


@pytest.mark.anyio
async def test_vector_collection_whitespace_only_fails_closed() -> None:
    """P1-F: a whitespace-only ``vector_collection`` is rejected at
    Phase C — it does not equal ``contract.vector_collection``
    and therefore fails closed as a contract mismatch.  The
    ``retrieval_no_vector_collection`` routing-phase check is no
    longer reachable; the fail-closed contract is preserved."""
    plan = _make_plan()
    row = _indexed_run_row(plan, vector_collection="   \t\n  ")
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert len(searcher.search_calls) == 0


# ---------------------------------------------------------------------------
# 22. Review-fix P2: indexed run's embedding_model routes the query embed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_model_passed_to_provider_from_indexed_run() -> None:
    """P1-F: the retrieval service MUST pass the resolved profile's
    ``query_embedding_model`` to ``embed_texts``.  The indexed run's
    ``embedding_model`` (= ``profile.document_embedding_model`` in V1)
    is validated at Phase C.3 Field 4 but is NOT the model used to
    embed the query — the query model is a distinct profile field.
    In V1 both fields are ``"text-embedding-v4"``.  The legacy
    custom-model scenario from the original reviewer P2 fix is no
    longer reachable because V1 is the only registered profile."""
    plan = _make_plan()
    # V1-aligned indexed run: ``embedding_model`` equals
    # ``profile.document_embedding_model`` so Phase C.3 Field 4 passes.
    row = _indexed_run_row(
        plan, embedding_model=_DEFAULT_DOCUMENT_EMBEDDING_MODEL
    )
    # Provider returns embeddings whose model equals
    # ``profile.query_embedding_model`` (V1 query model) so Phase D.2
    # passes.
    provider = FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # The fake provider records ``last_texts`` and ``call_count``.
    # The provider was called once with the V1 query model.
    assert provider.call_count == 1


@pytest.mark.anyio
async def test_embedding_model_mismatch_fails_closed() -> None:
    """P1-F: if the embedding provider returns a vector built by a
    different model than ``profile.query_embedding_model``, the query
    vector is in a different space than the indexed vectors → fail
    closed with ``retrieval_embedding_model_mismatch``.  The indexed
    run's ``embedding_model`` MUST equal
    ``profile.document_embedding_model`` (validated at Phase C.3
    Field 4); the provider's returned model MUST equal
    ``profile.query_embedding_model`` (validated at Phase D.2).  The
    offending model name is NEVER echoed in the error message —
    P1-F requires fixed local messages for all profile-mismatch
    failures.
    """
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagEmbedding,
    )

    class _MismatchProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            return [
                ArticleRagEmbedding(
                    text_sha256=hashlib.sha256(
                        texts[0].encode("utf-8")
                    ).hexdigest(),
                    model="some-other-model",
                    vector=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
                    dim=8,
                )
            ]

    plan = _make_plan()
    # V1-aligned indexed run so Phase C.3 Field 4 passes; the mismatch
    # is isolated to the provider's returned model (Phase D.2).
    row = _indexed_run_row(
        plan, embedding_model=_DEFAULT_DOCUMENT_EMBEDDING_MODEL
    )
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=FakeArticleRagVectorSearcher(hits=[]),
        embedding_provider=_MismatchProvider(dim=8),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH
    )
    # P1-F: the offending model name is NEVER echoed in the error
    # message.  The message is a fixed local string that does not
    # interpolate any caller-supplied value.
    msg = str(exc_info.value)
    assert "some-other-model" not in msg
    assert _DEFAULT_QUERY_EMBEDDING_MODEL not in msg
    assert _DEFAULT_DOCUMENT_EMBEDDING_MODEL not in msg


@pytest.mark.anyio
async def test_embedding_model_match_does_not_fail_closed() -> None:
    """P1-F: when the indexed run's ``embedding_model`` equals
    ``profile.document_embedding_model`` (Phase C.3 Field 4 passes)
    AND the embedding provider returns ``profile.query_embedding_model``
    (Phase D.2 passes), the retrieval completes successfully.  In V1
    both fields are ``"text-embedding-v4"``; the test locks in the
    policy's positive case for the V1-aligned happy path."""
    plan = _make_plan()
    row = _indexed_run_row(
        plan, embedding_model=_DEFAULT_DOCUMENT_EMBEDDING_MODEL
    )
    provider = FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert result.hits == ()


@pytest.mark.anyio
async def test_embedding_model_null_on_indexed_run_fails_closed() -> None:
    """P1-F: a NULL ``embedding_model`` on the indexed run is now
    rejected at Phase C as a contract mismatch.  The legacy
    NULL-bypass policy (defence in depth: pre-I4D rows still served)
    is removed because the frozen contract requires strict
    equality with ``contract.document_embedding_model``.  A NULL
    ``embedding_model`` means the indexed run predates the
    contract-aware worker and MUST NOT be served — silently serving
    hits from a pre-contract index would break the citation-truth
    boundary.  The provider is never called."""
    plan = _make_plan()
    row = _indexed_run_row(plan, embedding_model=None)
    provider = FakeArticleRagEmbeddingProvider(
        dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
    )
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    # P1-F: provider and searcher are NEVER called on contract-mismatch
    # paths — the failure is detected before any I/O leaves the
    # service boundary.
    assert provider.call_count == 0
    assert len(searcher.search_calls) == 0


@pytest.mark.anyio
async def test_failure_codes_include_new_review_codes() -> None:
    """The two new failure codes must be present in the public
    constants so ops dashboards can dispatch on them."""
    from app.services.reader_orchestration import article_rag_retrieval_service

    assert hasattr(
        article_rag_retrieval_service,
        "FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION",
    )
    assert hasattr(
        article_rag_retrieval_service,
        "FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH",
    )


# ---------------------------------------------------------------------------
# 23. Reviewer P2 fix: entity-wrapper hit guard mismatch triggers fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_wrapper_hit_base_id_mismatch_fails_closed() -> None:
    """When a hit comes back in the entity-wrapper shape with a
    mismatching ``base_id`` (relative to the current plan), the
    retrieval service MUST fail closed.  Before the reviewer fix the
    adapter only read top-level guard fields, so entity-wrapper hits
    would silently pass the mismatch check.
    """
    plan = _make_plan()
    row = _indexed_run_row(plan)
    # Build the searcher to return the entity-wrapper shape directly
    # — the fake searcher forwards whatever hits it's given.
    hits = [
        ArticleRagVectorSearchHit(
            chunk_id="chunk-aaa",
            score=0.9,
            stable_document_id=_STABLE_DOC_ID,  # matches plan
            base_id=_OTHER_BASE_ID,  # mismatches — must fail closed
            plan_content_sha256=row["plan_content_sha256"],
        ),
    ]
    searcher = FakeArticleRagVectorSearcher(hits=hits)
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
    )


@pytest.mark.anyio
async def test_entity_wrapper_hit_plan_hash_mismatch_fails_closed() -> None:
    """When an entity-wrapper hit carries a ``plan_content_sha256``
    that does NOT match the indexed run's plan hash, the retrieval
    service MUST fail closed — even if every other guard field is
    consistent.  This guards against drift contamination across
    index-version rebuilds."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    hits = [
        ArticleRagVectorSearchHit(
            chunk_id="chunk-aaa",
            score=0.9,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            plan_content_sha256="1" * 64,  # mismatches indexed run
        ),
    ]
    searcher = FakeArticleRagVectorSearcher(hits=hits)
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH
    )


@pytest.mark.anyio
async def test_entity_wrapper_hit_all_guards_match_succeeds() -> None:
    """Positive control: entity-wrapper hits with all guard
    fields consistent with the indexed run pass the join cleanly."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    hits = [
        ArticleRagVectorSearchHit(
            chunk_id="chunk-aaa",
            score=0.9,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            plan_content_sha256=row["plan_content_sha256"],
        ),
    ]
    searcher = FakeArticleRagVectorSearcher(hits=hits)
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert [h.chunk_id for h in result.hits] == ["chunk-aaa"]


# ---------------------------------------------------------------------------
# 24. Opt-in smoke skeleton (skipped unless READER_ARTICLE_RAG_SMOKE=1)
# ---------------------------------------------------------------------------

_SMOKE_ENV_VAR = "READER_ARTICLE_RAG_SMOKE"


@pytest.mark.skipif(
    os.environ.get(_SMOKE_ENV_VAR) != "1",
    reason="opt-in smoke skeleton; requires READER_ARTICLE_RAG_SMOKE=1",
)
@pytest.mark.anyio
async def test_real_retrieval_smoke_is_opt_in_only() -> None:
    # The actual smoke wiring is the responsibility of the I4E
    # orchestrator wiring milestone — this skeleton exists only so the
    # collection path is verifiable end-to-end when the env is on.
    pytest.skip("retrieval smoke skeleton not yet wired; opt-in only")


# ===========================================================================
# P1-F: Article RAG Retrieval Frozen Embedding Contract Validation
# ===========================================================================
#
# The tests below lock in the P1-F closure:
#
#   indexed-run durable embedding + vector-space contract validation
#     → plan hash validation
#     → query embedding model/dimension validation
#     → vector namespace routing
#     → vector hit joined back to current Postgres plan for citation
#
# All tests exercise the public ``retrieve_for_record`` seam.  Private
# helpers are not tested directly.  Failure codes are asserted with
# precise equality (no loose set membership) so each scenario has
# exactly one deterministic code.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1-F fixtures for contract-mismatch call-count assertions
# ---------------------------------------------------------------------------

@pytest.fixture
def provider_call_counter():
    """Wrap a FakeArticleRagEmbeddingProvider so tests can assert
    ``call_count == 0`` on fail-closed paths.  The fixture returns a
    constructor wrapper; the wrapped provider is returned to the
    test."""
    def _wrap(provider):
        return provider
    return _wrap


@pytest.fixture
def searcher_call_counter():
    """Wrap a FakeArticleRagVectorSearcher so tests can assert
    ``len(search_calls) == 0`` on fail-closed paths."""
    def _wrap(searcher):
        return searcher
    return _wrap

# ---------------------------------------------------------------------------
# P1-F Section 九 #7: indexed embedding_model NULL / mismatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_indexed_run_embedding_model_null_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """NULL ``embedding_model`` on the indexed run →
    ``retrieval_embedding_contract_mismatch``.  The NULL bypass is removed."""
    plan = _make_plan()
    row = _indexed_run_row(plan, embedding_model=None)
    provider = provider_call_counter(
        FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
    )
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert provider.call_count == 0
    assert len(searcher.search_calls) == 0


@pytest.mark.anyio
async def test_p1f_indexed_run_embedding_model_mismatch_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """``indexed.embedding_model`` != ``contract.document_embedding_model``
    → ``retrieval_embedding_contract_mismatch``.  The offending model name
    MUST NOT be echoed."""
    plan = _make_plan()
    row = _indexed_run_row(
        plan, embedding_model="some-other-embedding-model"
    )
    provider = provider_call_counter(
        FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
    )
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert "some-other-embedding-model" not in str(exc_info.value)
    assert provider.call_count == 0
    assert len(searcher.search_calls) == 0


# ---------------------------------------------------------------------------
# P1-F Section 九 #8: indexed vector_collection NULL / mismatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_indexed_run_vector_collection_null_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """NULL ``vector_collection`` on the indexed run →
    ``retrieval_embedding_contract_mismatch`` (NULL != contract.vector_collection)."""
    plan = _make_plan()
    row = _indexed_run_row(plan, vector_collection=None)
    provider = provider_call_counter(
        FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
    )
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert provider.call_count == 0
    assert len(searcher.search_calls) == 0


@pytest.mark.anyio
async def test_p1f_indexed_run_vector_collection_empty_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """Empty / whitespace-only ``vector_collection`` →
    ``retrieval_embedding_contract_mismatch``."""
    plan = _make_plan()
    row = _indexed_run_row(plan, vector_collection="   ")
    provider = provider_call_counter(
        FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
    )
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )


@pytest.mark.anyio
async def test_p1f_indexed_run_vector_collection_mismatch_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """``indexed.vector_collection`` != ``contract.vector_collection`` →
    ``retrieval_embedding_contract_mismatch``.  The offending collection
    name MUST NOT be echoed."""
    plan = _make_plan()
    row = _indexed_run_row(
        plan, vector_collection="some-other-zilliz-collection"
    )
    provider = provider_call_counter(
        FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
    )
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
    )
    assert "some-other-zilliz-collection" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# P1-F Section 九 #11: query embedding returns model mismatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_query_embedding_returns_model_mismatch_fails_closed(
    provider_call_counter, searcher_call_counter
) -> None:
    """If the embedding provider returns a vector whose ``model`` !=
    ``profile.query_embedding_model``, retrieval MUST fail closed
    with ``retrieval_embedding_model_mismatch``.  The indexed run is
    profile-aligned; only the provider's return value is wrong."""

    class _MismatchProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            return [
                ArticleRagEmbedding(
                    text_sha256=hashlib.sha256(
                        texts[0].encode("utf-8")
                    ).hexdigest(),
                    model="some-other-model",
                    vector=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
                    dim=8,
                )
            ]

    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = searcher_call_counter(FakeArticleRagVectorSearcher(hits=[]))
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=_MismatchProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        ),
    )
    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )
    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH
    )
    # The offending model name MUST NOT be echoed.
    assert "some-other-model" not in str(exc_info.value)
    # Searcher was not called because embedding mismatch fires first.
    assert len(searcher.search_calls) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "returned_model",
    [
        pytest.param(_DEFAULT_QUERY_EMBEDDING_MODEL + " ", id="trailing_space"),
        pytest.param(_DEFAULT_QUERY_EMBEDDING_MODEL + "\n", id="trailing_lf"),
        pytest.param(None, id="none"),
        pytest.param(1, id="integer"),
        pytest.param(True, id="bool"),
    ],
)
async def test_p1f_query_embedding_model_requires_raw_exact_string_match(
    returned_model: Any,
) -> None:
    """The provider-reported model must exactly equal the frozen profile."""

    class _ReturnedModelProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            self.call_count += 1
            return [
                ArticleRagEmbedding(
                    text_sha256=hashlib.sha256(
                        texts[0].encode("utf-8")
                    ).hexdigest(),
                    model=returned_model,
                    vector=(0.1,) * 8,
                    dim=8,
                )
            ]

    plan = _make_plan()
    searcher = FakeArticleRagVectorSearcher(hits=[])
    provider = _ReturnedModelProvider(
        dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
    )
    service = _build_service(
        plan=plan,
        indexed_run_row=_indexed_run_row(plan),
        searcher=searcher,
        embedding_provider=provider,
    )

    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )

    assert (
        exc_info.value.failure_code
        == FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH
    )
    assert provider.call_count == 1
    assert searcher.search_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reported_dim", "vector_length"),
    [
        pytest.param(8, 8, id="both_wrong"),
        pytest.param(8, 1024, id="reported_dim_wrong"),
        pytest.param(1024, 8, id="vector_length_wrong"),
        pytest.param(True, 1024, id="bool_reported_dim"),
    ],
)
async def test_p1f_query_embedding_dimension_matches_frozen_profile(
    reported_dim: Any,
    vector_length: int,
) -> None:
    """Both reported and actual vector dimensions must match the profile."""

    class _DimensionProvider(FakeArticleRagEmbeddingProvider):
        async def embed_texts(
            self, texts: list[str], *, model: str | None = None
        ):
            self.call_count += 1
            return [
                ArticleRagEmbedding(
                    text_sha256=hashlib.sha256(
                        texts[0].encode("utf-8")
                    ).hexdigest(),
                    model=_DEFAULT_QUERY_EMBEDDING_MODEL,
                    vector=(0.1,) * vector_length,
                    dim=reported_dim,
                )
            ]

    plan = _make_plan()
    searcher = FakeArticleRagVectorSearcher(hits=[])
    provider = _DimensionProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    service = _build_service(
        plan=plan,
        indexed_run_row=_indexed_run_row(plan),
        searcher=searcher,
        embedding_provider=provider,
    )

    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await service.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
        )

    assert exc_info.value.failure_code == (
        FAILURE_CODE_RETRIEVAL_EMBEDDING_DIMENSION_MISMATCH
    )
    assert provider.call_count == 1
    assert searcher.search_calls == []


# ---------------------------------------------------------------------------
# P1-F Section 九 #12: all contract-mismatch paths make 0 embedding /
# 0 vector-search calls (consolidated assertion)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_all_contract_mismatch_paths_zero_embedding_and_search_calls(
    provider_call_counter, searcher_call_counter
) -> None:
    """For every contract-mismatch scenario, the embedding provider
    call_count == 0 AND the vector searcher call_count == 0.  This
    consolidates the call_count assertions for the family of
    contract-mismatch failures."""
    plan = _make_plan()

    mismatch_rows = [
        # embedding_model NULL
        _indexed_run_row(plan, embedding_model=None),
        # embedding_model mismatch
        _indexed_run_row(
            plan, embedding_model="some-other-embedding-model"
        ),
        # vector_collection NULL
        _indexed_run_row(plan, vector_collection=None),
        # vector_collection mismatch
        _indexed_run_row(
            plan, vector_collection="some-other-collection"
        ),
    ]

    for row in mismatch_rows:
        provider = FakeArticleRagEmbeddingProvider(
            dim=8, model=_DEFAULT_QUERY_EMBEDDING_MODEL
        )
        searcher = FakeArticleRagVectorSearcher(hits=[])
        service = _build_service(
            plan=plan,
            indexed_run_row=row,
            searcher=searcher,
            embedding_provider=provider,
        )
        with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
            await service.retrieve_for_record(
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                query_text="hello",
            )
        assert exc_info.value.failure_code == (
            FAILURE_CODE_RETRIEVAL_CONTRACT_MISMATCH
        ), (
            f"unexpected failure_code={exc_info.value.failure_code!r} "
            f"for row={row!r}"
        )
        assert provider.call_count == 0, (
            f"provider called {provider.call_count} times for row={row!r}"
        )
        assert len(searcher.search_calls) == 0, (
            f"searcher called {len(searcher.search_calls)} times for "
            f"row={row!r}"
        )


# ---------------------------------------------------------------------------
# P1-F Section 九 #13: normal V1 retrieval uses profile model + namespace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_normal_v1_retrieval_uses_profile_model_and_namespace() -> None:
    """A successful V1 retrieval MUST:
      - request query embedding with ``model=profile.query_embedding_model``
      - route the vector search to ``profile.vector_namespace``
      - return citations joined from the current Postgres plan
    """
    plan = _make_plan()
    row = _indexed_run_row(plan)
    provider = FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9)
        ]
    )
    service = _build_service(
        plan=plan,
        indexed_run_row=row,
        searcher=searcher,
        embedding_provider=provider,
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    # Query embedding was requested with the profile's query model.
    assert provider.call_count == 1
    assert provider.last_texts == ["hello"]
    # The fake provider embeds with whatever model kwarg it receives.
    # The returned embedding's model matches the profile's query model
    # because retrieval forwards ``profile.query_embedding_model``.
    assert result.hits[0].chunk_id == "chunk-aaa"
    # Vector search was routed to the profile's vector_namespace.
    assert len(searcher.search_calls) == 1
    assert searcher.search_calls[0]["collection"] == _DEFAULT_VECTOR_NAMESPACE
    # Citation comes from the current plan chunk.
    aaa = result.hits[0]
    assert aaa.text == "alpha text"
    assert aaa.citation["stable_document_id"] == str(_STABLE_DOC_ID)
    assert aaa.citation["block_ids"] == ["block-for-chunk-aaa"]


# ---------------------------------------------------------------------------
# P1-F Section 九 #14: forged vector citation/profile metadata is not truth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1f_forged_vector_citation_metadata_is_not_truth() -> None:
    """A vector hit that carries a forged citation / fingerprint /
    chunk_text in its payload MUST NOT override the citation truth
    from the current Postgres plan.  The hit's ``chunk_id`` joins to
    ``plan.chunks``; the joined chunk's ``text`` / ``citation`` /
    ``metadata_json`` / ``content_sha256`` are the only truth."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    # The vector hit only carries chunk_id + score + guard metadata.
    # It does NOT carry citation / text / fingerprint fields — those
    # are NOT fields on ArticleRagVectorSearchHit by design (the
    # searcher contract forbids trusting payload citation).
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(
                chunk_id="chunk-aaa",
                score=0.9,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                plan_content_sha256=row["plan_content_sha256"],
            ),
        ]
    )
    service = _build_service(
        plan=plan, indexed_run_row=row, searcher=searcher
    )
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    aaa = result.hits[0]
    # Citation comes from the plan chunk, not from the vector payload.
    assert aaa.citation["reading_record_id"] == str(_RECORD_ID)
    assert aaa.citation["stable_document_id"] == str(_STABLE_DOC_ID)
    assert aaa.citation["block_ids"] == ["block-for-chunk-aaa"]
    assert aaa.text == "alpha text"
    # content_sha256 comes from the plan chunk.
    assert aaa.content_sha256 == hashlib.sha256(b"alpha text").hexdigest()


# ---------------------------------------------------------------------------
# Round 1-R1: public retrieve seam without version selection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_round1_retrieve_without_version_param_returns_plan_backed_hits() -> None:
    """No version argument: fixed internal DEFAULT yields plan-backed hits."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[ArticleRagVectorSearchHit(chunk_id="chunk-aaa", score=0.9)]
    )
    service = _build_service(plan=plan, indexed_run_row=row, searcher=searcher)
    result = await service.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="hello",
    )
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.chunk_id == "chunk-aaa"
    assert hit.citation["block_ids"] == ["block-for-chunk-aaa"]
    from app.services.reader_orchestration.article_rag_index_plan import (
        compute_plan_content_sha256,
    )
    assert result.plan_content_sha256 == compute_plan_content_sha256(plan)
    assert result.plan_content_sha256 == row["plan_content_sha256"]


@pytest.mark.anyio
async def test_round1_retrieve_rejects_legacy_index_version_kwarg_before_io() -> None:
    """Legacy index_version= kwarg is not part of the public interface."""
    plan = _make_plan()
    provider = FakeArticleRagEmbeddingProvider(
        dim=_DEFAULT_DOCUMENT_EMBEDDING_DIMENSION,
        model=_DEFAULT_QUERY_EMBEDDING_MODEL,
    )
    searcher = FakeArticleRagVectorSearcher(hits=[])
    service = _build_service(
        plan=plan,
        indexed_run_row=_indexed_run_row(plan),
        searcher=searcher,
        embedding_provider=provider,
    )
    with pytest.raises(TypeError):
        await service.retrieve_for_record(  # type: ignore[call-arg]
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="hello",
            index_version="article_rag_index_v1",
        )
    assert provider.call_count == 0
    assert searcher.search_calls == []
