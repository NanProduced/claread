# task-history: (renamed from test_d6_i4s_article_rag_index_lifecycle_service.py)
"""Tests for the Article RAG index lifecycle coordinator.

All tests are no-network: no real DB, DashScope, Zilliz, or LLM.
Uses ``_FakeConn`` (a dict-backed fake asyncpg.Connection) and a
``_FakeBootstrapService`` that returns pre-configured results.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapError,
    ArticleRagIndexBootstrapResult,
)
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ENSURE_STATUS_BOOTSTRAP_INCONSISTENT,
    ENSURE_STATUS_ENQUEUED,
    ENSURE_STATUS_ERROR,
    ENSURE_STATUS_GENERATION_MISMATCH,
    ENSURE_STATUS_IDEMPOTENT_NOOP,
    ENSURE_STATUS_NO_ACTIVE_BASE,
    ENSURE_STATUS_NOT_READY,
    ENSURE_STATUS_PLAN_HASH_MISMATCH,
    ENSURE_STATUS_RECORD_NOT_FOUND,
    STATUS_FAILED,
    STATUS_INDEXED,
    STATUS_INDEXING,
    STATUS_NOT_INDEXED,
    STATUS_NOT_READY,
    STATUS_QUEUED,
    STATUS_SUPERSEDED_OR_STALE,
    STATUS_UNAVAILABLE,
    ArticleRagIndexEnsureResult,
    ArticleRagIndexLifecycleService,
    ArticleRagIndexLifecycleStatus,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("00000000-0000-0000-0000-000000000001")
_USER_ID = UUID("00000000-0000-0000-0000-000000000002")
_OTHER_USER_ID = UUID("00000000-0000-0000-0000-000000000003")
_BASE_ID = UUID("00000000-0000-0000-0000-000000000010")
_STABLE_DOC_ID = UUID("00000000-0000-0000-0000-000000000020")
_INDEX_RUN_ID = UUID("00000000-0000-0000-0000-000000000030")
_JOB_ID = UUID("00000000-0000-0000-0000-000000000040")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000050")
_GENERATION = 3


# ---------------------------------------------------------------------------
# FakeConn
# ---------------------------------------------------------------------------


class _FakeConn:
    """Dict-backed fake asyncpg.Connection.

    ``fetchrow`` inspects the SQL string to decide which pre-configured
    result to return.  This avoids parsing SQL while keeping tests
    deterministic and network-free.
    """

    def __init__(
        self,
        *,
        in_transaction: bool = True,
        record_row: dict[str, Any] | None = None,
        stable_row: dict[str, Any] | None = None,
        index_row: dict[str, Any] | None = None,
    ) -> None:
        self._in_transaction = in_transaction
        self._record_row = record_row
        self._stable_row = stable_row
        self._index_row = index_row
        self.fetchrow_calls: list[str] = []

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append(sql)
        sql_lower = sql.lower()
        if "reading_records" in sql_lower:
            return self._record_row
        if "stable_reading_documents" in sql_lower:
            return self._stable_row
        if "reader_article_rag_index_runs" in sql_lower:
            return self._index_row
        return None


def _make_record_row(
    *,
    generation: int = _GENERATION,
    active_base_id: UUID | None = _BASE_ID,
    readiness_state: str = "article_ready",
) -> dict[str, Any]:
    return {
        "generation": generation,
        "active_base_id": active_base_id,
        "readiness_state": readiness_state,
    }


def _make_stable_row(
    *,
    stable_id: UUID = _STABLE_DOC_ID,
    record_generation: int = _GENERATION,
) -> dict[str, Any]:
    return {
        "id": stable_id,
        "record_generation": record_generation,
    }


def _make_index_row(
    *,
    status: str = "indexed",
    base_id: UUID | None = _BASE_ID,
    record_generation: int = _GENERATION,
    stable_document_id: UUID = _STABLE_DOC_ID,
    plan_sha: str = "a" * 64,
    chunk_count: int = 5,
    index_run_id: UUID = _INDEX_RUN_ID,
) -> dict[str, Any]:
    return {
        "id": index_run_id,
        "base_id": base_id,
        "record_generation": record_generation,
        "stable_document_id": stable_document_id,
        "plan_content_sha256": plan_sha,
        "chunk_count": chunk_count,
        "status": status,
    }


# ---------------------------------------------------------------------------
# FakeBootstrapService
# ---------------------------------------------------------------------------


class _FakeBootstrapService:
    """Fake bootstrap service that returns a pre-configured result
    or raises a pre-configured error."""

    def __init__(
        self,
        *,
        result: ArticleRagIndexBootstrapResult | None = None,
        error: ArticleRagIndexBootstrapError | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def bootstrap_article_rag_index_in_transaction(
        self,
        conn: Any,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        now: Any = None,
    ) -> ArticleRagIndexBootstrapResult:
        self.calls.append(
            {
                "conn": conn,
                "reading_record_id": reading_record_id,
                "user_id": user_id,
                "now": now,
            }
        )
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _make_bootstrap_result(
    *,
    idempotent_noop: bool = False,
) -> ArticleRagIndexBootstrapResult:
    return ArticleRagIndexBootstrapResult(
        index_run_id=_INDEX_RUN_ID,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=_GENERATION,
        plan_content_sha256="a" * 64,
        chunk_count=5,
        job_id=_JOB_ID,
        job_status="queued",
        idempotent_noop=idempotent_noop,
    )


def _make_service(
    *,
    bootstrap: _FakeBootstrapService,
) -> ArticleRagIndexLifecycleService:
    return ArticleRagIndexLifecycleService(bootstrap_service=bootstrap)  # type: ignore[arg-type]


# ===========================================================================
# ensure: transaction guard
# ===========================================================================


class TestEnsureTransactionGuard:
    async def test_no_transaction_fails_closed(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(in_transaction=False)

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_ERROR
        assert result.reason_code == "caller_transaction_required"
        assert result.idempotent_noop is False
        # Bootstrap must NOT have been called.
        assert len(bootstrap.calls) == 0


# ===========================================================================
# ensure: ownership / deleted / inactive
# ===========================================================================


class TestEnsureOwnership:
    async def test_wrong_user_returns_record_not_found(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=None)  # record not found

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_OTHER_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_RECORD_NOT_FOUND
        assert result.reason_code == "record_not_found"
        assert len(bootstrap.calls) == 0

    async def test_deleted_record_returns_record_not_found(self) -> None:
        # FakeConn returns None for reading_records when record_row is None,
        # which simulates the WHERE deleted_at IS NULL filter excluding the row.
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=None)

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_RECORD_NOT_FOUND
        assert len(bootstrap.calls) == 0

    async def test_inactive_record_returns_record_not_found(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=None)  # lifecycle_status != 'active'

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_RECORD_NOT_FOUND
        assert len(bootstrap.calls) == 0


# ===========================================================================
# ensure: not article_ready
# ===========================================================================


class TestEnsureNotReady:
    async def test_not_article_ready_does_not_enqueue(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(
            record_row=_make_record_row(readiness_state="processing"),
        )

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_NOT_READY
        assert result.reason_code == "record_not_article_ready"
        assert len(bootstrap.calls) == 0

    async def test_missing_active_base_does_not_enqueue(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(
            record_row=_make_record_row(active_base_id=None),
        )

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_NO_ACTIVE_BASE
        assert result.reason_code == "active_base_id_is_null"
        assert len(bootstrap.calls) == 0


# ===========================================================================
# ensure: generation mismatch
# ===========================================================================


class TestEnsureGenerationMismatch:
    async def test_generation_mismatch_does_not_enqueue(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(
            record_row=_make_record_row(generation=2),
        )

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,  # expects 3, actual is 2
        )

        assert result.status == ENSURE_STATUS_GENERATION_MISMATCH
        assert result.reason_code == "generation_mismatch"
        assert result.record_generation == 2
        assert len(bootstrap.calls) == 0


# ===========================================================================
# ensure: happy path
# ===========================================================================


class TestEnsureHappyPath:
    async def test_happy_path_calls_bootstrap_and_returns_enqueued(self) -> None:
        bootstrap = _FakeBootstrapService(result=_make_bootstrap_result())
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_ENQUEUED
        assert result.reason_code == "enqueued"
        assert result.idempotent_noop is False
        assert result.stable_document_id == _STABLE_DOC_ID
        assert result.base_id == _BASE_ID
        assert result.record_generation == _GENERATION
        assert result.index_run_id == _INDEX_RUN_ID
        assert result.job_id == _JOB_ID
        assert not hasattr(result, "index_version")
        assert not hasattr(result, "chunker_version")
        # Bootstrap was called exactly once with fixed DEFAULT identity.
        assert len(bootstrap.calls) == 1
        assert bootstrap.calls[0]["reading_record_id"] == _RECORD_ID
        assert bootstrap.calls[0]["user_id"] == _USER_ID

    async def test_bootstrap_idempotent_noop_is_passed_through(self) -> None:
        bootstrap = _FakeBootstrapService(
            result=_make_bootstrap_result(idempotent_noop=True),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_IDEMPOTENT_NOOP
        assert result.reason_code == "idempotent_noop"
        assert result.idempotent_noop is True
        assert result.index_run_id == _INDEX_RUN_ID


# ===========================================================================
# ensure: bootstrap error translation
# ===========================================================================


class TestEnsureBootstrapError:
    async def test_plan_hash_mismatch_translated(self) -> None:
        bootstrap = _FakeBootstrapService(
            error=ArticleRagIndexBootstrapError(
                "plan hash drift",
                reason_code="plan_hash_mismatch",
            ),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_PLAN_HASH_MISMATCH
        assert result.reason_code == "plan_hash_mismatch"

    async def test_idempotent_run_inconsistent_translated(self) -> None:
        bootstrap = _FakeBootstrapService(
            error=ArticleRagIndexBootstrapError(
                "dead job",
                reason_code="idempotent_run_inconsistent",
            ),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_BOOTSTRAP_INCONSISTENT
        assert result.reason_code == "idempotent_run_inconsistent"

    async def test_embedding_contract_identity_missing_translated(self) -> None:
        """Wave 7 / A3: a legacy run without a persisted contract
        fingerprint must surface as its own typed status + reason code
        (never an idempotent no-op, never a generic error)."""
        bootstrap = _FakeBootstrapService(
            error=ArticleRagIndexBootstrapError(
                "legacy run without contract fingerprint",
                reason_code="embedding_contract_identity_missing",
            ),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == "embedding_contract_identity_missing"
        assert result.reason_code == "embedding_contract_identity_missing"
        assert result.idempotent_noop is False

    async def test_embedding_contract_mismatch_translated(self) -> None:
        """Wave 7 / A3: a run persisted under a different contract must
        surface as its own typed status + reason code."""
        bootstrap = _FakeBootstrapService(
            error=ArticleRagIndexBootstrapError(
                "run built under another contract",
                reason_code="embedding_contract_mismatch",
            ),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == "embedding_contract_mismatch"
        assert result.reason_code == "embedding_contract_mismatch"
        assert result.idempotent_noop is False

    async def test_generic_bootstrap_error_translated(self) -> None:
        bootstrap = _FakeBootstrapService(
            error=ArticleRagIndexBootstrapError(
                "something else",
                reason_code="bootstrap_failed",
            ),
        )
        service = _make_service(bootstrap=bootstrap)
        conn = _FakeConn(record_row=_make_record_row())

        result = await service.ensure_article_rag_index_job_in_transaction(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            expected_generation=_GENERATION,
        )

        assert result.status == ENSURE_STATUS_ERROR
        assert result.reason_code == "bootstrap_failed"


# ===========================================================================
# status: not_ready / unavailable
# ===========================================================================


class TestStatusNotReady:
    async def test_record_not_found_returns_unavailable(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(record_row=None)

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_OTHER_USER_ID,
        )

        assert status.status == STATUS_UNAVAILABLE
        assert status.reason_code == "record_not_found"

    async def test_not_article_ready_returns_not_ready(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(readiness_state="processing"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_NOT_READY
        assert status.reason_code == "record_not_article_ready"

    async def test_no_active_base_returns_not_ready(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(active_base_id=None),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_NOT_READY
        assert status.reason_code == "active_base_id_is_null"

    async def test_no_active_stable_document_returns_not_ready(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=None,
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_NOT_READY
        assert status.reason_code == "no_active_stable_document"

    async def test_stable_generation_mismatch_returns_not_ready(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(generation=3),
            stable_row=_make_stable_row(record_generation=2),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_NOT_READY
        assert status.reason_code == "stable_generation_mismatch"


# ===========================================================================
# status: not_indexed
# ===========================================================================


class TestStatusNotIndexed:
    async def test_ready_but_no_index_run_returns_not_indexed(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=None,
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_NOT_INDEXED
        assert status.reason_code == "no_index_run"
        assert status.stable_document_id == _STABLE_DOC_ID
        assert status.base_id == _BASE_ID
        assert status.record_generation == _GENERATION


# ===========================================================================
# status: queued / indexing
# ===========================================================================


class TestStatusQueuedIndexing:
    async def test_queued_status(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="queued"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_QUEUED
        assert status.reason_code == "index_run_queued"
        assert status.index_run_id == _INDEX_RUN_ID

    async def test_planned_status_maps_to_queued(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="planned"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_QUEUED

    async def test_indexing_status(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="indexing"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_INDEXING
        assert status.reason_code == "index_run_indexing"


# ===========================================================================
# status: indexed (happy + stale)
# ===========================================================================


class TestStatusIndexed:
    async def test_indexed_happy_path(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="indexed"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_INDEXED
        assert status.reason_code == "indexed"
        assert status.index_run_id == _INDEX_RUN_ID
        assert status.plan_content_sha256 == "a" * 64
        assert status.chunk_count == 5

    async def test_indexed_base_mismatch_returns_stale(self) -> None:
        other_base = UUID("00000000-0000-0000-0000-000000000099")
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="indexed", base_id=other_base),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"

    async def test_indexed_generation_mismatch_returns_stale(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(generation=3),
            stable_row=_make_stable_row(record_generation=3),
            index_row=_make_index_row(status="indexed", record_generation=2),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"

    async def test_indexed_stable_doc_mismatch_returns_stale(self) -> None:
        other_stable = UUID("00000000-0000-0000-0000-000000000099")
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(
                status="indexed",
                stable_document_id=other_stable,
            ),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"


# ===========================================================================
# status: stale detection applies to ALL run statuses
# ===========================================================================


class TestStatusStaleDetectionAllStatuses:
    """Contract: stale consistency check must run *before* status-specific
    mapping so a stale queued / indexing / failed / superseded run is reported
    as ``superseded_or_stale`` rather than as the run's own status."""

    async def _assert_stale_status(self, *, run_status: str) -> (
        ArticleRagIndexLifecycleStatus
    ):
        other_base = UUID("00000000-0000-0000-0000-000000000099")
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status=run_status, base_id=other_base),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"
        assert status.index_run_id == _INDEX_RUN_ID
        return status

    async def test_stale_queued_run_returns_superseded(self) -> None:
        await self._assert_stale_status(run_status="queued")

    async def test_stale_planned_run_returns_superseded(self) -> None:
        await self._assert_stale_status(run_status="planned")

    async def test_stale_indexing_run_returns_superseded(self) -> None:
        await self._assert_stale_status(run_status="indexing")

    async def test_stale_failed_run_returns_superseded(self) -> None:
        await self._assert_stale_status(run_status="failed")

    async def test_stale_superseded_run_returns_superseded(self) -> None:
        await self._assert_stale_status(run_status="superseded")

    async def test_stale_queued_generation_mismatch_returns_superseded(
        self,
    ) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(generation=3),
            stable_row=_make_stable_row(record_generation=3),
            index_row=_make_index_row(
                status="queued",
                record_generation=2,
            ),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"

    async def test_stale_indexing_stable_doc_mismatch_returns_superseded(
        self,
    ) -> None:
        other_stable = UUID("00000000-0000-0000-0000-000000000099")
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(
                status="indexing",
                stable_document_id=other_stable,
            ),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_base_or_generation_mismatch"


# ===========================================================================
# status: failed / superseded (current — base/stable/generation match)
# ===========================================================================


class TestStatusFailedSuperseded:
    async def test_failed_status(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="failed"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_FAILED
        assert status.reason_code == "index_run_failed"

    async def test_superseded_status(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="superseded"),
        )

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        assert status.status == STATUS_SUPERSEDED_OR_STALE
        assert status.reason_code == "index_run_superseded"


# ===========================================================================
# status: no chunk text / vector payload / Plate / DOM / Slate fields
# ===========================================================================


class TestStatusNoProjectionFields:
    """The status response must NOT contain chunk text, vector payload,
    Plate / Markdown / DOM / Slate / UI fields."""

    _FORBIDDEN_FIELDS = frozenset(
        {
            "text",
            "chunk_text",
            "embedding",
            "embedding_vector",
            "vector_payload",
            "plate_json",
            "markdown",
            "dom",
            "slate",
            "slate_path",
            "content_md",
            "render_snapshot",
            "ui_state",
        }
    )

    async def test_status_dataclass_has_no_projection_fields(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ArticleRagIndexLifecycleStatus)}
        forbidden = self._FORBIDDEN_FIELDS & field_names
        assert forbidden == set(), (
            f"ArticleRagIndexLifecycleStatus must not have projection fields; "
            f"found: {sorted(forbidden)}"
        )

    async def test_ensure_dataclass_has_no_projection_fields(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ArticleRagIndexEnsureResult)}
        forbidden = self._FORBIDDEN_FIELDS & field_names
        assert forbidden == set(), (
            f"ArticleRagIndexEnsureResult must not have projection fields; "
            f"found: {sorted(forbidden)}"
        )

    async def test_status_query_does_not_select_chunk_text(self) -> None:
        """The SQL for reader_article_rag_index_runs must NOT select
        chunk text / embedding / vector columns."""
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="indexed"),
        )

        await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        # Find the index_runs SQL.
        index_sql = None
        for sql in conn.fetchrow_calls:
            if "reader_article_rag_index_runs" in sql.lower():
                index_sql = sql.lower()
                break

        assert index_sql is not None, "index_runs query was not issued"
        # The SELECT must only pick truth-layer identifiers / hashes / counts.
        forbidden_columns = [
            "chunk_text",
            "embedding",
            "vector",
            "plate",
            "markdown",
            "dom",
            "slate",
            "text_content",
        ]
        for col in forbidden_columns:
            assert col not in index_sql, (
                f"index_runs SQL must not select '{col}'; SQL: {index_sql}"
            )

    async def test_status_query_uses_no_lock(self) -> None:
        """The status query must NOT use FOR UPDATE / FOR SHARE."""
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(
            record_row=_make_record_row(),
            stable_row=_make_stable_row(),
            index_row=_make_index_row(status="indexed"),
        )

        await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

        for sql in conn.fetchrow_calls:
            sql_lower = sql.lower()
            assert "for update" not in sql_lower, (
                f"status query must not lock rows; found FOR UPDATE in: {sql}"
            )
            assert "for share" not in sql_lower, (
                f"status query must not lock rows; found FOR SHARE in: {sql}"
            )


# ===========================================================================
# status: ownership check
# ===========================================================================


class TestStatusOwnership:
    async def test_wrong_user_returns_unavailable(self) -> None:
        service = ArticleRagIndexLifecycleService(bootstrap_service=_FakeBootstrapService())  # type: ignore[arg-type]
        conn = _FakeConn(record_row=None)

        status = await service.load_article_rag_index_lifecycle_status(
            conn,
            reading_record_id=_RECORD_ID,
            user_id=_OTHER_USER_ID,
        )

        assert status.status == STATUS_UNAVAILABLE
        assert status.reason_code == "record_not_found"
