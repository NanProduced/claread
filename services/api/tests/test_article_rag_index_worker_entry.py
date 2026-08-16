# task-history: (renamed from test_d6_i4u_article_rag_index_worker_entry.py)
"""Tests for the Article RAG index operational worker entry.

Covers the standalone ``scripts/run_reader_article_rag_index_worker.py``:

1. ``build_worker_service``: fail-closed when DashScope/Zilliz config
   missing — construction does NOT trigger network calls.
2. ``_parse_args``: settings defaults + CLI overrides.
3. ``_run_drain_cycle``: calls ``process_next`` until idle or max_ticks.
4. ``_build_result_payload``: serializes result without sensitive data.
5. ``_run_worker`` ``--once`` mode: drains, prints JSON, exits.
6. ``_run_worker`` loop mode: processes, sleeps, stops on shutdown.
7. Validation: loop mode rejects ``poll_interval_seconds=0``; once mode
   allows it; rejects invalid lease_duration / max_ticks.
8. No real DB / network / LLM / vector by default.

All tests use a ``_FakeWorkerService`` that records ``process_next``
calls and returns pre-configured results.  ``init_db`` / ``close_db`` /
``DB_POOL`` / ``build_worker_service`` are stubbed via monkeypatch so
no real database or network connection is made.
"""

from __future__ import annotations

import asyncio
import json
import sys
from argparse import Namespace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerResult,
)
from scripts.run_reader_article_rag_index_worker import (
    _build_result_payload,
    _parse_args,
    _run_drain_cycle,
    _run_worker,
    build_worker_service,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


# ---------------------------------------------------------------------------
# Module-level autouse fixture: stub stale-lease recovery at the runtime
# class boundary so tests that drive ``_run_drain_cycle`` / ``_run_worker``
# directly do NOT touch a real DB / network.
#
# This is a global safety net added in backend hardening. Tests
# that need to assert ordering / batch-size propagation (e.g.
# ``TestStaleLeaseRecovery``) override this stub with their own
# ``monkeypatch.setattr(...)`` — monkeypatch teardown is function-scoped, so
# the override only applies to that test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_recover_stale_leases_for_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub ``ReaderJobRuntime.recover_stale_leases`` at the runtime class
    boundary so tests that drive ``_run_drain_cycle`` / ``_run_worker``
    directly do NOT touch a real DB / network.

    This is a global safety net added in backend hardening. Tests
    that need to assert ordering / batch-size propagation (e.g.
    ``TestStaleLeaseRecovery``) override this stub with their own
    ``monkeypatch.setattr(...)`` — monkeypatch teardown is function-scoped, so
    the override only applies to that test.
    """
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
    """Fake :class:`ArticleRagIndexWorkerService` for script tests.

    ``process_next_results`` is a list of results (or None for idle).
    Each ``process_next`` call pops one entry.  When the list is empty,
    returns ``None`` (idle).
    """

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
            {
                "lease_owner": lease_owner,
                "lease_duration": lease_duration,
                "retry_delay": retry_delay,
            }
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


def _make_result(
    *,
    job_id: UUID | None = None,
    status: str = "succeeded",
    chunk_count: int = 5,
    embedding_model: str | None = "text-embedding-v4",
    vector_store_provider: str | None = "zilliz",
    vector_collection: str | None = "article_rag_chunks",
    retryable: bool | None = None,
    failure_code: str | None = None,
    idempotent_noop: bool = False,
) -> ArticleRagIndexWorkerResult:
    return ArticleRagIndexWorkerResult(
        job_id=job_id or uuid4(),
        index_run_id=uuid4(),
        reading_record_id=uuid4(),
        stable_document_id=uuid4(),
        base_id=uuid4(),
        status=status,
        chunk_count=chunk_count,
        embedding_model=embedding_model,
        vector_store_provider=vector_store_provider,
        vector_collection=vector_collection,
        retryable=retryable,
        failure_code=failure_code,
        idempotent_noop=idempotent_noop,
    )


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


async def _noop_init_db(*args: object, **kwargs: object) -> None:
    return None


async def _noop_close_db() -> None:
    return None


def _stub_infra(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_service: _FakeWorkerService | None = None,
) -> _FakeWorkerService:
    """Stub init_db / close_db / DB_POOL / build_worker_service.

    Stale-lease recovery is stubbed at the module level by an autouse fixture
    so this helper does NOT need to repeat that work.

    ``build_gc_service`` is stubbed with an idle fake so the vector-GC
    drain step never touches a real DB in these unit tests (the GC drain
    step itself is covered by tests/test_article_rag_vector_gc_entry.py).
    """
    monkeypatch.setattr(
        "scripts.run_reader_article_rag_index_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_article_rag_index_worker.close_db", _noop_close_db
    )
    monkeypatch.setattr("app.database.connection.DB_POOL", object())

    service = fake_service or _FakeWorkerService()
    monkeypatch.setattr(
        "scripts.run_reader_article_rag_index_worker.build_worker_service",
        lambda **kwargs: service,
    )
    monkeypatch.setattr(
        "scripts.run_reader_article_rag_index_worker.build_gc_service",
        lambda **kwargs: _FakeGcService(),
    )
    return service


class _FakeGcService:
    """Idle GC service fake: never returns an intent, never touches DB."""

    async def process_next_due_intent(self) -> None:
        return None


# ===========================================================================
# build_worker_service — factory wiring
# ===========================================================================


class TestBuildWorkerService:
    """Factory must construct without network, fail-closed on missing config."""

    def test_missing_config_returns_service_with_unconfigured_providers(self) -> None:
        """Missing DashScope/Zilliz config → service with Unconfigured* providers.

        Construction must NOT trigger network calls or raise.
        """
        settings = Settings(
            reader_article_rag_embedding_provider="",
            reader_article_rag_vector_provider="",
        )
        # Use a fake pool to avoid real DB.
        service = build_worker_service(settings=settings, pool=object())
        # The service is constructed; providers are Unconfigured* (fail closed
        # on first job).  We don't assert the type directly to avoid coupling
        # to the internal provider class names — the key contract is that
        # construction succeeds without network.
        assert service is not None
        assert service._embedding_provider is not None
        assert service._vector_writer is not None

    def test_partial_zilliz_config_does_not_raise(self) -> None:
        """Partial Zilliz config (missing token) → no crash, fail-closed."""
        settings = Settings(
            reader_article_rag_vector_provider="zilliz",
            reader_article_rag_zilliz_uri="https://zilliz.example.com",
            reader_article_rag_zilliz_token="",  # missing token
            reader_article_rag_zilliz_collection="article_rag_chunks",
            reader_article_rag_vector_dim=1024,
        )
        service = build_worker_service(settings=settings, pool=object())
        # Construction succeeded; no network call was made.
        assert service is not None

    def test_complete_config_does_not_connect_at_construction(self) -> None:
        """Complete Zilliz config → writer constructed but NOT connected.

        The ZillizArticleRagVectorWriter lazily creates the MilvusClient
        inside the first ``upsert_chunks`` call, not at construction.
        This test verifies construction does NOT trigger a connection.
        """
        settings = Settings(
            reader_article_rag_vector_provider="zilliz",
            reader_article_rag_zilliz_uri="https://zilliz.example.com",
            reader_article_rag_zilliz_token="fake-token-for-construction-test",
            reader_article_rag_zilliz_collection="custom_article_rag_dev",
            reader_article_rag_vector_dim=1024,
        )
        service = build_worker_service(settings=settings, pool=object())
        # If construction had tried to connect, this would have raised or
        # hung on a network call to a non-existent Zilliz instance.
        assert service is not None
        assert service._vector_writer is not None
        assert service._default_vector_collection == "custom_article_rag_dev"


# ===========================================================================
# _parse_args
# ===========================================================================


class TestParseArgs:
    def test_uses_settings_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = Settings(
            reader_article_rag_worker_poll_interval_seconds=7,
            reader_article_rag_worker_lease_owner_prefix="rag-worker-test",
            reader_article_rag_worker_lease_duration_seconds=95,
            reader_article_rag_worker_max_ticks=42,
        )
        monkeypatch.setattr(
            sys, "argv", ["run_reader_article_rag_index_worker.py", "--once"]
        )
        args = _parse_args(settings)
        assert args.once is True
        assert args.poll_interval_seconds == 7
        assert args.lease_owner_prefix == "rag-worker-test"
        assert args.lease_duration_seconds == 95
        assert args.max_ticks == 42

    def test_accepts_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = Settings()
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_reader_article_rag_index_worker.py",
                "--poll-interval-seconds", "3",
                "--lease-duration-seconds", "60",
                "--lease-owner-prefix", "custom-owner",
                "--max-ticks", "10",
            ],
        )
        args = _parse_args(settings)
        assert args.once is False
        assert args.poll_interval_seconds == 3
        assert args.lease_duration_seconds == 60
        assert args.lease_owner_prefix == "custom-owner"
        assert args.max_ticks == 10


# ===========================================================================
# _run_drain_cycle
# ===========================================================================


class TestRunDrainCycle:
    async def test_calls_process_next_until_idle(self) -> None:
        """Drain cycle calls process_next until None (idle) is returned."""
        service = _FakeWorkerService(
            process_next_results=[
                _make_result(status="succeeded"),
                _make_result(status="succeeded"),
                None,  # idle
            ]
        )
        results = await _run_drain_cycle(
            service=service,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=10,
        )
        assert len(results) == 2
        assert len(service.process_next_calls) == 3  # 2 results + 1 idle

    async def test_respects_max_ticks(self) -> None:
        """Drain cycle stops at max_ticks even if jobs are still available."""
        service = _FakeWorkerService(
            process_next_results=[
                _make_result(status="succeeded"),
                _make_result(status="succeeded"),
                _make_result(status="succeeded"),
                _make_result(status="succeeded"),
            ]
        )
        results = await _run_drain_cycle(
            service=service,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=2,
        )
        assert len(results) == 2
        assert len(service.process_next_calls) == 2

    async def test_idle_returns_empty_list(self) -> None:
        """No job available → empty results list."""
        service = _FakeWorkerService(process_next_results=[None])
        results = await _run_drain_cycle(
            service=service,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=5,
        )
        assert results == []
        assert len(service.process_next_calls) == 1

    async def test_passes_lease_owner_and_duration(self) -> None:
        """Lease owner and duration are forwarded to process_next."""
        service = _FakeWorkerService(process_next_results=[None])
        await _run_drain_cycle(
            service=service,  # type: ignore[arg-type]
            lease_owner="my-owner",
            lease_duration=timedelta(seconds=45),
            max_ticks=5,
        )
        assert service.process_next_calls[0]["lease_owner"] == "my-owner"
        assert service.process_next_calls[0]["lease_duration"] == timedelta(seconds=45)


# ===========================================================================
# _build_result_payload
# ===========================================================================


class TestBuildResultPayload:
    def test_success_result_serialized(self) -> None:
        job_id = uuid4()
        result = _make_result(
            job_id=job_id,
            status="succeeded",
            chunk_count=7,
            embedding_model="text-embedding-v4",
            vector_store_provider="zilliz",
            vector_collection="article_rag_chunks",
        )
        payload = _build_result_payload(result)
        assert payload["job_id"] == str(job_id)
        assert payload["status"] == "succeeded"
        assert payload["chunk_count"] == 7
        assert payload["embedding_model"] == "text-embedding-v4"
        assert payload["vector_store_provider"] == "zilliz"
        assert payload["vector_collection"] == "article_rag_chunks"
        assert "idempotent_noop" not in payload  # False → omitted

    def test_failure_result_serialized(self) -> None:
        result = _make_result(
            status="failed_terminal",
            retryable=False,
            failure_code="embedding_provider_unconfigured",
        )
        payload = _build_result_payload(result)
        assert payload["status"] == "failed_terminal"
        assert payload["retryable"] is False
        assert payload["failure_code"] == "embedding_provider_unconfigured"

    def test_idempotent_noop_serialized(self) -> None:
        result = _make_result(
            status="succeeded",
            idempotent_noop=True,
        )
        payload = _build_result_payload(result)
        assert payload["idempotent_noop"] is True

    def test_no_sensitive_data_in_payload(self) -> None:
        """Payload must NOT contain chunk text, vectors, tokens, or URIs."""
        result = _make_result()
        payload = _build_result_payload(result)
        payload_str = json.dumps(payload)
        # No chunk text, embedding vectors, tokens, URIs, or raw SDK messages.
        forbidden_keys = {"chunk_text", "vector", "token", "uri", "api_key", "error_message"}
        for key in forbidden_keys:
            assert key not in payload, f"payload contains sensitive key: {key}"
        assert "chunk_text" not in payload_str.lower()


# ===========================================================================
# _run_worker --once mode
# ===========================================================================


class TestRunWorkerOnceMode:
    async def test_drains_and_prints_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--once mode: runs one drain cycle, prints JSON, exits."""
        fake_service = _FakeWorkerService(
            process_next_results=[
                _make_result(status="succeeded"),
                None,  # idle → end drain cycle
            ]
        )
        _stub_infra(monkeypatch, fake_service=fake_service)

        args = Namespace(
            once=True,
            poll_interval_seconds=5,
            lease_duration_seconds=120,
            lease_owner_prefix="test-once",
            max_ticks=100,
        )
        await _run_worker(args, Settings())

        assert len(fake_service.process_next_calls) == 2  # 1 result + 1 idle
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert len(parsed) == 1
        assert parsed[0]["status"] == "succeeded"

    async def test_idle_prints_empty_array(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--once mode with no jobs: prints [] and exits."""
        fake_service = _FakeWorkerService(process_next_results=[None])
        _stub_infra(monkeypatch, fake_service=fake_service)

        args = Namespace(
            once=True,
            poll_interval_seconds=5,
            lease_duration_seconds=120,
            lease_owner_prefix="test-idle",
            max_ticks=100,
        )
        await _run_worker(args, Settings())

        captured = capsys.readouterr()
        assert json.loads(captured.out) == []

    async def test_allows_zero_poll_interval(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--once mode allows poll_interval_seconds=0 (no idle sleep)."""
        fake_service = _FakeWorkerService(process_next_results=[None])
        _stub_infra(monkeypatch, fake_service=fake_service)

        args = Namespace(
            once=True,
            poll_interval_seconds=0,
            lease_duration_seconds=120,
            lease_owner_prefix="test",
            max_ticks=100,
        )
        await _run_worker(args, Settings())
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []


# ===========================================================================
# _run_worker loop mode
# ===========================================================================


class TestRunWorkerLoopMode:
    async def test_processes_then_sleeps_then_stops(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loop mode: processes jobs, sleeps when idle, stops on cancel."""
        fake_service = _FakeWorkerService(
            process_next_results=[
                _make_result(status="succeeded"),
                None,  # idle → triggers sleep
            ]
        )
        _stub_infra(monkeypatch, fake_service=fake_service)

        args = Namespace(
            once=False,
            poll_interval_seconds=1,
            lease_duration_seconds=120,
            lease_owner_prefix="test-loop",
            max_ticks=100,
        )

        task = asyncio.create_task(_run_worker(args, Settings()))

        # Wait until at least 2 process_next calls (1 result + 1 idle).
        deadline = asyncio.get_event_loop().time() + 5.0
        while (
            len(fake_service.process_next_calls) < 2
            and asyncio.get_event_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)

        assert len(fake_service.process_next_calls) >= 2

        # Cancel the task (simulates graceful shutdown / cleanup).
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ===========================================================================
# Validation
# ===========================================================================


class TestValidation:
    async def test_loop_mode_rejects_zero_poll_interval(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loop mode must reject poll_interval_seconds < 1 (no busy-spin)."""
        _stub_infra(monkeypatch)

        args = Namespace(
            once=False,
            poll_interval_seconds=0,
            lease_duration_seconds=120,
            lease_owner_prefix="test",
            max_ticks=100,
        )
        with pytest.raises(ValueError, match="poll_interval_seconds must be >= 1 in loop mode"):
            await _run_worker(args, Settings())

    async def test_rejects_invalid_lease_duration(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_infra(monkeypatch)
        args = Namespace(
            once=True,
            poll_interval_seconds=5,
            lease_duration_seconds=0,
            lease_owner_prefix="test",
            max_ticks=100,
        )
        with pytest.raises(ValueError, match="lease_duration_seconds"):
            await _run_worker(args, Settings())

    async def test_rejects_invalid_max_ticks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_infra(monkeypatch)
        args = Namespace(
            once=True,
            poll_interval_seconds=5,
            lease_duration_seconds=120,
            lease_owner_prefix="test",
            max_ticks=0,
        )
        with pytest.raises(ValueError, match="max_ticks"):
            await _run_worker(args, Settings())

    async def test_rejects_negative_poll_interval(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_infra(monkeypatch)
        args = Namespace(
            once=True,
            poll_interval_seconds=-1,
            lease_duration_seconds=120,
            lease_owner_prefix="test",
            max_ticks=100,
        )
        with pytest.raises(ValueError, match="poll_interval_seconds must be >= 0"):
            await _run_worker(args, Settings())


# ===========================================================================
# No real backend calls
# ===========================================================================


class TestNoRealBackendCalls:
    """All entry tests must NOT call real DB / network / LLM / vector."""

    async def test_once_mode_does_not_call_real_db(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """init_db and close_db are stubbed; no real DB connection."""
        init_call_count = 0
        close_call_count = 0

        async def _counting_init_db(*args: object, **kwargs: object) -> None:
            nonlocal init_call_count
            init_call_count += 1

        async def _counting_close_db() -> None:
            nonlocal close_call_count
            close_call_count += 1

        monkeypatch.setattr(
            "scripts.run_reader_article_rag_index_worker.init_db", _counting_init_db
        )
        monkeypatch.setattr(
            "scripts.run_reader_article_rag_index_worker.close_db", _counting_close_db
        )
        monkeypatch.setattr("app.database.connection.DB_POOL", object())

        fake_service = _FakeWorkerService(process_next_results=[None])
        monkeypatch.setattr(
            "scripts.run_reader_article_rag_index_worker.build_worker_service",
            lambda **kwargs: fake_service,
        )
        monkeypatch.setattr(
            "scripts.run_reader_article_rag_index_worker.build_gc_service",
            lambda **kwargs: _FakeGcService(),
        )

        args = Namespace(
            once=True,
            poll_interval_seconds=5,
            lease_duration_seconds=120,
            lease_owner_prefix="test-no-backend",
            max_ticks=100,
        )
        await _run_worker(args, Settings())

        # init_db called once (stubbed, no real connection).
        assert init_call_count == 1
        assert close_call_count == 1
        # No real process_next calls (idle).
        assert len(fake_service.process_next_calls) == 1


# ===========================================================================
# Stale-lease recovery (backend hardening:)
# ===========================================================================


class TestStaleLeaseRecovery:
    async def test_drain_cycle_calls_recover_before_process_next(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Drain cycle must call ``ReaderJobRuntime.recover_stale_leases`` once
        before any ``process_next`` calls, and must use the independent
        ``recover_batch_size`` (not throttled by ``max_ticks``).
        """
        from unittest.mock import AsyncMock

        recover_calls: list[int] = []
        call_order: list[str] = []

        async def _side_effect(*, batch_size: int) -> int:
            recover_calls.append(batch_size)
            call_order.append("recover")
            return 0

        # ``AsyncMock`` is required (not a plain async function) so the
        # bound-method ``self`` does not break the keyword-only call.
        mock = AsyncMock(side_effect=_side_effect)

        from app.services.reader_orchestration import job_runtime

        monkeypatch.setattr(
            job_runtime.ReaderJobRuntime,
            "recover_stale_leases",
            mock,
        )

        svc = _FakeWorkerService(process_next_results=[None])
        # Subclass to record process_next invocations in the shared order list.
        original_process_next = svc.process_next

        async def _tracking_process_next(**kwargs: Any) -> Any:
            call_order.append("process_next")
            return await original_process_next(**kwargs)

        svc.process_next = _tracking_process_next  # type: ignore[method-assign]
        _stub_infra(monkeypatch, fake_service=svc)  # type: ignore[arg-type]

        # Drive the drain cycle directly with our (overridden) service injected.
        from scripts.run_reader_article_rag_index_worker import _run_drain_cycle

        results = await _run_drain_cycle(
            service=svc,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=3,
            recover_batch_size=200,
        )
        assert results == []
        # ``process_next`` runs AFTER the recover stub.
        assert svc.process_next_calls, "process_next should have run at least once"
        # Recover ran first, with the independent batch size 200.
        assert recover_calls == [200], (
            f"recover must use the independent batch size; got {recover_calls}"
        )
        # Plan-mandated: recover MUST run before process_next (one ordering
        # assertion that would fail if a regression swapped the order).
        assert call_order and call_order[0] == "recover", (
            f"recover_stale_leases must run before process_next; order was {call_order}"
        )

    async def test_once_mode_invokes_recover_before_draining(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--once mode: runtime-level recover runs before any process_next
        call. Recovery must use the independent batch size.
        """
        from unittest.mock import AsyncMock

        recover_batch_used: list[int] = []
        call_order: list[str] = []

        async def _side_effect(*, batch_size: int) -> int:
            recover_batch_used.append(batch_size)
            call_order.append("recover")
            return 7

        mock = AsyncMock(side_effect=_side_effect)

        from app.services.reader_orchestration import job_runtime

        monkeypatch.setattr(
            job_runtime.ReaderJobRuntime,
            "recover_stale_leases",
            mock,
        )

        svc = _FakeWorkerService(process_next_results=[None])
        # Track process_next invocations in the shared order list.
        original_process_next = svc.process_next

        async def _tracking_process_next(**kwargs: Any) -> Any:
            call_order.append("process_next")
            return await original_process_next(**kwargs)

        svc.process_next = _tracking_process_next  # type: ignore[method-assign]
        _stub_infra(monkeypatch, fake_service=svc)  # type: ignore[arg-type]

        args = Namespace(
            once=True, poll_interval_seconds=0, lease_duration_seconds=120,
            lease_owner_prefix="test-once-recover", max_ticks=100,
            recover_batch_size=200,
        )
        await _run_worker(args, Settings())
        # The runtime-level recover must have used the independent batch 200.
        assert recover_batch_used == [200], (
            f"runtime-level recover must use recover_batch_size=200; got {recover_batch_used}"
        )
        # Plan-mandated: drain cycle MUST have run (process_next invoked at
        # least once). Without this, an early-exit regression would pass.
        assert len(svc.process_next_calls) >= 1, (
            "process_next must run at least once after recover (drain cycle "
            f"invoked); got {len(svc.process_next_calls)} calls"
        )
        # Plan-mandated: recover MUST run before process_next.
        assert call_order and call_order[0] == "recover", (
            f"recover_stale_leases must run before process_next; order was {call_order}"
        )

    async def test_drain_cycle_reconciles_after_recover_before_process_next(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Drain cycle must run the orphan reconciliation pass after
        ``recover_stale_leases`` (so job-level recovery is already
        converged) and before any ``process_next`` claim, with the
        independent ``recover_batch_size``.
        """
        from unittest.mock import AsyncMock

        from app.services.reader_orchestration import job_runtime

        call_order: list[str] = []

        async def _recover_side_effect(*, batch_size: int) -> int:
            call_order.append("recover")
            return 0

        monkeypatch.setattr(
            job_runtime.ReaderJobRuntime,
            "recover_stale_leases",
            AsyncMock(side_effect=_recover_side_effect),
        )

        svc = _FakeWorkerService(process_next_results=[None])
        original_process_next = svc.process_next
        original_reconcile = svc.reconcile_orphaned_index_runs

        async def _tracking_process_next(**kwargs: Any) -> Any:
            call_order.append("process_next")
            return await original_process_next(**kwargs)

        async def _tracking_reconcile(**kwargs: Any) -> int:
            call_order.append("reconcile")
            return await original_reconcile(**kwargs)

        svc.process_next = _tracking_process_next  # type: ignore[method-assign]
        svc.reconcile_orphaned_index_runs = _tracking_reconcile  # type: ignore[method-assign]
        _stub_infra(monkeypatch, fake_service=svc)  # type: ignore[arg-type]

        from scripts.run_reader_article_rag_index_worker import _run_drain_cycle

        results = await _run_drain_cycle(
            service=svc,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=3,
            recover_batch_size=200,
        )
        assert results == []
        # Reconcile ran exactly once, with the independent batch size.
        assert svc.reconcile_calls == [200], (
            f"reconcile must use recover_batch_size; got {svc.reconcile_calls}"
        )
        # Ordering: recover -> reconcile -> process_next.
        assert call_order[:3] == ["recover", "reconcile", "process_next"], (
            f"expected recover -> reconcile -> process_next; order was {call_order}"
        )
