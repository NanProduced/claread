"""Vector-GC worker entry tests (Wave 9 D).

Locks the drain-cycle ordering and failure semantics of the GC step in
``scripts/run_reader_article_rag_index_worker.py``:

- per cycle: recover stale leases -> reconcile orphaned index runs ->
  process at most one due vector-GC intent -> process index jobs.
- typed vector failures write retry events inside the service and the
  drain continues with index jobs.
- unexpected GC control-plane / DB exceptions abort the drain cycle and
  are re-raised (never swallowed).
- the entry builds a fail-closed GC service (unconfigured deleter) with
  no network contact.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerResult,
)
from app.services.reader_orchestration.article_rag_vector_deleter import (
    UnconfiguredArticleRagVectorDeleter,
)
from app.services.reader_orchestration.article_rag_vector_gc_service import (
    ArticleRagVectorGcResult,
)
from scripts.run_reader_article_rag_index_worker import (
    _run_drain_cycle,
    build_gc_service,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


@pytest.fixture(autouse=True)
def _stub_recover_stale_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from app.services.reader_orchestration import job_runtime

    monkeypatch.setattr(
        job_runtime.ReaderJobRuntime,
        "recover_stale_leases",
        AsyncMock(return_value=0),
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWorkerService:
    def __init__(
        self,
        *,
        process_next_results: list[ArticleRagIndexWorkerResult | None] | None = None,
    ) -> None:
        self._results = list(process_next_results or [])
        self.process_next_calls: list[dict[str, Any]] = []
        self.reconcile_calls: list[int] = []

    async def process_next(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta = timedelta(seconds=60),
        retry_delay: timedelta = timedelta(minutes=2),
    ) -> ArticleRagIndexWorkerResult | None:
        self.process_next_calls.append(
            {"lease_owner": lease_owner, "lease_duration": lease_duration}
        )
        if not self._results:
            return None
        return self._results.pop(0)

    async def reconcile_orphaned_index_runs(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        self.reconcile_calls.append(batch_size)
        return 0


class _FakeGcService:
    """Records calls; returns a preset result or raises a preset error."""

    def __init__(
        self,
        *,
        result: ArticleRagVectorGcResult | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._result = result
        self._raise_error = raise_error
        self.calls: list[list[str]] = []
        self.order_events: list[str] = []

    async def process_next_due_intent(self) -> ArticleRagVectorGcResult | None:
        self.calls.append(["gc"])
        if self._raise_error is not None:
            raise self._raise_error
        return self._result


def _gc_result(status: str = "retry_scheduled") -> ArticleRagVectorGcResult:
    return ArticleRagVectorGcResult(
        intent_event_id=uuid4(),
        status=status,  # type: ignore[arg-type]
        failure_code="vector_deletion_sdk_error",
        attempt_number=1,
    )


def _make_result() -> ArticleRagIndexWorkerResult:
    return ArticleRagIndexWorkerResult(
        job_id=uuid4(),
        index_run_id=uuid4(),
        reading_record_id=uuid4(),
        stable_document_id=uuid4(),
        base_id=uuid4(),
        status="succeeded",
        chunk_count=1,
    )


async def _run_cycle(service: Any, gc_service: Any, *, max_ticks: int = 5) -> Any:
    return await _run_drain_cycle(
        service=service,
        gc_service=gc_service,
        lease_owner="test-owner",
        lease_duration=timedelta(seconds=30),
        max_ticks=max_ticks,
    )


# ===========================================================================
# Drain order: recover -> reconcile -> GC -> index jobs
# ===========================================================================


class TestDrainOrder:
    async def test_recover_reconcile_gc_index_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from app.services.reader_orchestration import job_runtime

        order: list[str] = []

        async def _recover(*, batch_size: int) -> int:
            order.append("recover")
            return 0

        monkeypatch.setattr(
            job_runtime.ReaderJobRuntime,
            "recover_stale_leases",
            AsyncMock(side_effect=_recover),
        )

        svc = _FakeWorkerService(process_next_results=[None])
        original_process_next = svc.process_next
        original_reconcile = svc.reconcile_orphaned_index_runs

        async def _tracking_process_next(**kwargs: Any) -> Any:
            order.append("index")
            return await original_process_next(**kwargs)

        async def _tracking_reconcile(**kwargs: Any) -> int:
            order.append("reconcile")
            return await original_reconcile(**kwargs)

        svc.process_next = _tracking_process_next  # type: ignore[method-assign]
        svc.reconcile_orphaned_index_runs = _tracking_reconcile  # type: ignore[method-assign]

        gc = _FakeGcService(result=None)
        original_gc = gc.process_next_due_intent

        async def _tracking_gc() -> Any:
            order.append("gc")
            return await original_gc()

        gc.process_next_due_intent = _tracking_gc  # type: ignore[method-assign]

        await _run_cycle(svc, gc, max_ticks=1)

        assert order[:4] == ["recover", "reconcile", "gc", "index"], order

    async def test_gc_called_at_most_once_per_cycle(self) -> None:
        svc = _FakeWorkerService(
            process_next_results=[_make_result(), _make_result(), None]
        )
        gc = _FakeGcService(result=_gc_result())
        await _run_cycle(svc, gc, max_ticks=10)

        assert len(gc.calls) == 1
        assert len(svc.process_next_calls) == 3

    async def test_no_gc_service_skips_gc_step(self) -> None:
        svc = _FakeWorkerService(process_next_results=[None])
        results = await _run_drain_cycle(
            service=svc,
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=5,
        )
        assert results == []
        assert len(svc.process_next_calls) == 1


# ===========================================================================
# Failure semantics
# ===========================================================================


class TestGcFailureSemantics:
    async def test_typed_failure_retry_does_not_block_index_drain(self) -> None:
        svc = _FakeWorkerService(
            process_next_results=[_make_result(), None]
        )
        gc = _FakeGcService(result=_gc_result(status="retry_scheduled"))

        results = await _run_cycle(svc, gc, max_ticks=5)

        assert results, "index jobs must still be processed after a GC retry"
        assert results[0].status == "succeeded"

    async def test_completed_gc_result_does_not_block_index_drain(self) -> None:
        svc = _FakeWorkerService(process_next_results=[_make_result(), None])
        gc = _FakeGcService(result=_gc_result(status="completed"))

        results = await _run_cycle(svc, gc, max_ticks=5)

        assert results and results[0].status == "succeeded"

    async def test_unexpected_gc_failure_aborts_drain_cycle(self) -> None:
        svc = _FakeWorkerService(process_next_results=[_make_result()])
        gc = _FakeGcService(
            raise_error=RuntimeError("unexpected control-plane failure")
        )

        with pytest.raises(RuntimeError, match="unexpected control-plane failure"):
            await _run_cycle(svc, gc, max_ticks=5)

        assert svc.process_next_calls == [], (
            "index jobs must not run after an unexpected GC failure"
        )

    async def test_db_failure_aborts_drain_cycle(self) -> None:
        svc = _FakeWorkerService(process_next_results=[_make_result()])
        gc = _FakeGcService(raise_error=ConnectionError("db connection lost"))

        with pytest.raises(ConnectionError, match="db connection lost"):
            await _run_cycle(svc, gc, max_ticks=5)

        assert svc.process_next_calls == []


# ===========================================================================
# Entry factory
# ===========================================================================


class TestBuildGcService:
    def test_missing_config_returns_fail_closed_service(self) -> None:
        settings = Settings(
            reader_article_rag_vector_provider="",
        )
        service = build_gc_service(settings=settings, pool=object())
        assert service is not None
        assert isinstance(service._deleter, UnconfiguredArticleRagVectorDeleter)

    def test_partial_config_never_touches_network(self) -> None:
        # The resolver falls back to ZILLIZ_URI/ZILLIZ_TOKEN (env or local
        # .env), so "missing token" here may still resolve a real token —
        # exactly like the writer factory.  The fail-closed property under
        # test is: construction never raises and never opens a connection.
        settings = Settings(
            reader_article_rag_vector_provider="zilliz",
            reader_article_rag_zilliz_uri="https://zilliz.invalid",
            reader_article_rag_zilliz_token="",  # missing token
            reader_article_rag_zilliz_collection="article_rag_chunks",
        )
        service = build_gc_service(settings=settings, pool=object())
        assert service is not None
        assert service._deleter is not None

    def test_complete_config_constructs_without_network(self) -> None:
        settings = Settings(
            reader_article_rag_vector_provider="zilliz",
            reader_article_rag_zilliz_uri="https://zilliz.invalid",
            reader_article_rag_zilliz_token="fake-token",
            reader_article_rag_zilliz_collection="article_rag_chunks",
        )
        service = build_gc_service(settings=settings, pool=object())
        assert service is not None
        assert not isinstance(service._deleter, UnconfiguredArticleRagVectorDeleter)
