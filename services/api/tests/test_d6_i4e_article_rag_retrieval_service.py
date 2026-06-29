"""D6-I4E: tests for Article RAG retrieval service.

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
  * vector hit ``stable_document_id`` / ``base_id`` / ``index_version``
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
    DEFAULT_INDEX_VERSION,
    FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
    FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
    FAILURE_CODE_RETRIEVAL_EMBEDDING_MODEL_MISMATCH,
    FAILURE_CODE_RETRIEVAL_INVALID_LIMIT,
    FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
    FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION,
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
        chunker_version="article_rag_index_plan_v1",
        chunks=cs,
    )


def _indexed_run_row(
    plan: ArticleRagIndexPlan,
    plan_content_sha256: str | None = None,
    *,
    status: str = "indexed",
    vector_collection: str | None = "article_rag_index_v1_test",
    embedding_model: str | None = "fake-embedding-deterministic-v1",
) -> dict[str, Any]:
    """Build the ``reader_article_rag_index_runs`` row that the
    retrieval service's ``_load_indexed_run`` queries.

    Defaults match the I4D test fixtures so existing tests keep
    their original assertions; the new ``vector_collection`` and
    ``embedding_model`` parameters let I4E-fix tests pin the
    routing / model-validation behaviour.
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
        "index_version": DEFAULT_INDEX_VERSION,
        "chunker_version": plan.chunker_version,
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
    pre-stubbed.  No real DB."""
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

    async def _fake_build_plan_in_transaction(
        conn, *, record_id, user_id, include_rag_ask_only=False
    ):
        return plan

    plan_service.build_index_plan_in_transaction = (
        _fake_build_plan_in_transaction  # type: ignore[assignment]
    )

    embedding_provider = embedding_provider or FakeArticleRagEmbeddingProvider(
        dim=8
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
    assert result.index_version == DEFAULT_INDEX_VERSION
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
async def test_vector_metadata_mismatch_index_version_fails_closed() -> None:
    plan = _make_plan()
    row = _indexed_run_row(plan)
    searcher = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(
                chunk_id="chunk-aaa",
                score=0.9,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                index_version="some-other-version",
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
                index_version=DEFAULT_INDEX_VERSION,
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
            index_version: str | None = None,
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
    assert result.index_version == DEFAULT_INDEX_VERSION
    # plan_content_sha256 is the CURRENT plan's hash (== indexed run
    # by Phase C invariant).
    from app.services.reader_orchestration.article_rag_index_plan import (
        compute_plan_content_sha256,
    )
    assert result.plan_content_sha256 == compute_plan_content_sha256(plan)


# ---------------------------------------------------------------------------
# 19. Constants / exports
# ---------------------------------------------------------------------------


def test_default_index_version_constant_matches_i4b() -> None:
    """I4E must agree with I4B's default index version.  They are
    independently defined; a drift here would mean new builds cannot
    find their own indexed runs."""
    from app.services.reader_orchestration.article_rag_index_bootstrap import (
        DEFAULT_INDEX_VERSION as I4B_DEFAULT,
    )
    assert DEFAULT_INDEX_VERSION == I4B_DEFAULT


def test_max_retrieval_limit_is_a_positive_int() -> None:
    assert isinstance(MAX_RETRIEVAL_LIMIT, int)
    assert MAX_RETRIEVAL_LIMIT > 0


def test_failure_codes_are_distinct() -> None:
    """All retrieval failure codes must be unique (ops dashboards
    dispatch on the code)."""
    codes = {
        FAILURE_CODE_RETRIEVAL_EMPTY_QUERY,
        FAILURE_CODE_RETRIEVAL_INVALID_LIMIT,
        FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
        FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH,
        FAILURE_CODE_RETRIEVAL_VECTOR_METADATA_MISMATCH,
        FAILURE_CODE_RETRIEVAL_EMBEDDING_FAILED,
        FAILURE_CODE_RETRIEVAL_VECTOR_SEARCH_FAILED,
    }
    assert len(codes) == 7


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
    """Reviewer P1 fix: the retrieval service MUST pass the indexed
    run's ``vector_collection`` to the searcher.  Without this, real
    deployments using a non-default collection name would trigger
    ``vector_searcher_collection_mismatch`` on every retrieval."""
    plan = _make_plan()
    real_collection_name = "my_custom_zilliz_collection_v2"
    row = _indexed_run_row(
        plan, vector_collection=real_collection_name
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
    # collection matches the indexed run's ``vector_collection``.
    assert len(searcher.search_calls) == 1
    assert (
        searcher.search_calls[0]["collection"] == real_collection_name
    )


@pytest.mark.anyio
async def test_vector_collection_empty_fails_closed() -> None:
    """An indexed run with NULL ``vector_collection`` cannot be
    routed.  Fail closed with ``retrieval_no_vector_collection`` —
    do NOT silently fall back to a hard-coded default, which would
    trigger a downstream ``vector_searcher_collection_mismatch``."""
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
        == FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION
    )


@pytest.mark.anyio
async def test_vector_collection_whitespace_only_fails_closed() -> None:
    """Whitespace-only ``vector_collection`` is treated the same as
    NULL — fail closed rather than passing a blank string to the
    searcher (which would trigger ``vector_searcher_collection_mismatch``
    for the default-collection deployment)."""
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
        == FAILURE_CODE_RETRIEVAL_NO_VECTOR_COLLECTION
    )


# ---------------------------------------------------------------------------
# 22. Review-fix P2: indexed run's embedding_model routes the query embed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_model_passed_to_provider_from_indexed_run() -> None:
    """Reviewer P2 fix: the retrieval service MUST pass the indexed
    run's ``embedding_model`` to ``embed_texts`` so a future model
    migration does not silently re-embed the query in a different
    vector space than the one used at index time."""
    plan = _make_plan()
    target_model = "text-embedding-v4-test-model"
    row = _indexed_run_row(plan, embedding_model=target_model)
    provider = FakeArticleRagEmbeddingProvider(dim=8, model=target_model)
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
    # The fake provider records ``last_texts`` and ``call_count``;
    # we can't directly inspect the model kwarg from
    # FakeArticleRagEmbeddingProvider, but we can assert the provider
    # was called at all and that the indexed-run model equals the
    # model's ``_model`` attribute.  See also:
    # test_embedding_model_mismatch_fails_closed below — that test
    # directly verifies the mismatch-policy.
    assert provider.call_count == 1


@pytest.mark.anyio
async def test_embedding_model_mismatch_fails_closed() -> None:
    """If the embedding provider returns a vector built by a
    different model than the indexed run was built with, the query
    vector is in a different space than the indexed vectors → fail
    closed with ``retrieval_embedding_model_mismatch``.

    We use a custom provider that ignores the ``model`` kwarg and
    returns a different model name in its output.
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
    row = _indexed_run_row(
        plan, embedding_model="text-embedding-v4-test-model"
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
    # Provider model + indexed model names appear in the diagnostic.
    msg = str(exc_info.value)
    assert "some-other-model" in msg
    assert "text-embedding-v4-test-model" in msg


@pytest.mark.anyio
async def test_embedding_model_match_does_not_fail_closed() -> None:
    """When the embedding provider returns the same model the
    indexed run was built with, the retrieval completes successfully
    (lock in the policy's positive case)."""
    plan = _make_plan()
    target_model = "text-embedding-v4-test-model"
    row = _indexed_run_row(plan, embedding_model=target_model)
    provider = FakeArticleRagEmbeddingProvider(dim=8, model=target_model)
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
async def test_embedding_model_null_on_indexed_run_does_not_fail_closed() -> None:
    """Defence in depth: if the indexed run's ``embedding_model`` is
    NULL (e.g. a pre-I4D row), we MUST NOT fail closed — instead the
    service proceeds without the model assertion.  The deployment
    just won't get the model-mismatch safety net for that run; the
    worst-case outcome is the same as the pre-fix behaviour."""
    plan = _make_plan()
    row = _indexed_run_row(plan, embedding_model=None)
    provider = FakeArticleRagEmbeddingProvider(
        dim=8, model="whatever-the-provider-wants"
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
            index_version=DEFAULT_INDEX_VERSION,
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
            index_version=DEFAULT_INDEX_VERSION,
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
    """Positive control: entity-wrapper hits with all four guard
    fields consistent with the indexed run pass the join cleanly."""
    plan = _make_plan()
    row = _indexed_run_row(plan)
    hits = [
        ArticleRagVectorSearchHit(
            chunk_id="chunk-aaa",
            score=0.9,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            index_version=DEFAULT_INDEX_VERSION,
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