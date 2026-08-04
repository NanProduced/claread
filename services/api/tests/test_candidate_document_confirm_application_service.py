# task-history: D6-I2D (renamed from test_d6_i2d_candidate_document_confirm_application_service.py)
"""Focused tests for D6-I2D-B Candidate Document Confirm Application
Service.

These tests use fake pool / fake conn / fake repository / fake event
runtime / fake snapshot service — no real DB is required.

Test coverage:
    * Happy path: confirm inside transaction, state update before
      event, event_type="article_ready" with full payload, snapshot
      reload after commit, result field mapping.
    * Confirm error -> application error, rollback, no state/event/
      snapshot.
    * base_id=None -> fail closed, no state/event/snapshot.
    * Repository error -> rollback, no event/snapshot.
    * Event error -> rollback, no snapshot.
    * Snapshot error -> application error, but transaction already
      committed.
    * Version / language / now pass-through to D6-I2D-A confirm.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationError,
    CandidateDocumentConfirmApplicationResult,
    CandidateDocumentConfirmApplicationService,
)
from app.services.reader_orchestration.candidate_document_confirm_service import (
    CandidateDocumentConfirmError,
    CandidateDocumentConfirmResult,
    CandidateDocumentStatusError,
)
from app.services.reader_orchestration.event_runtime import ReaderEventEnvelope

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]


# --------------------------------------------------------------------
# Fake asyncpg connection + pool
# --------------------------------------------------------------------


class _FakeRecord:
    """Mimics an asyncpg.Record for fetchrow results."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def __getitem__(self, key: str) -> Any:
        return self._mapping[key]

    def keys(self) -> list[str]:
        return list(self._mapping.keys())


class _RecordedCall:
    __slots__ = ("kind", "query", "args")

    def __init__(self, kind: str, query: str, args: tuple) -> None:
        self.kind = kind
        self.query = query
        self.args = args


class _FakeTransaction:
    """Fake ``conn.transaction()`` context manager.

    Sets ``conn._in_transaction = True`` on enter and ``False`` on
    exit. Records commit/rollback status and appends to a shared log
    so tests can assert ordering (e.g. commit before snapshot reload).
    """

    def __init__(
        self, conn: _FakeConn, log: list[str] | None = None
    ) -> None:
        self._conn = conn
        self._log = log
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> None:
        self._conn._in_transaction = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        self._conn._in_transaction = False
        if exc_type is None:
            self.committed = True
            if self._log is not None:
                self._log.append("transaction_committed")
        else:
            self.rolled_back = True
            if self._log is not None:
                self._log.append("transaction_rolled_back")
        return False  # don't suppress


class _FakeConn:
    """Recording fake asyncpg.Connection for unit testing.

    Supports ``transaction()``, ``is_in_transaction()``, ``fetchrow``,
    ``fetchval``, and ``execute``. fetchrow/fetchval results are
    queued by the test in consumption order.

    The connection starts NOT in a transaction; the
    ``_FakeTransaction`` context manager flips ``_in_transaction``.
    """

    def __init__(self, *, log: list[str] | None = None) -> None:
        self.calls: list[_RecordedCall] = []
        self._fetchrow_queue: list[_FakeRecord | None] = []
        self._fetchval_queue: list[Any] = []
        self._execute_overrides: list[tuple[str, str]] = []
        self._in_transaction = False
        self._log = log
        self._last_transaction: _FakeTransaction | None = None

    # -- Queuing helpers --

    def queue_fetchrow(self, mapping: dict[str, Any] | None) -> None:
        self._fetchrow_queue.append(_FakeRecord(mapping) if mapping else None)

    def queue_fetchval(self, value: Any) -> None:
        self._fetchval_queue.append(value)

    def set_execute_result(self, sql_substring: str, result: str) -> None:
        self._execute_overrides.append((sql_substring, result))

    # -- asyncpg-compatible interface --

    def transaction(
        self,
        *,
        isolation: str | None = None,
        readonly: bool = False,
    ) -> _FakeTransaction:
        self._last_transaction = _FakeTransaction(self, log=self._log)
        return self._last_transaction

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(_RecordedCall("execute", query, args))
        for i, (substr, result) in enumerate(self._execute_overrides):
            if substr in query:
                self._execute_overrides.pop(i)
                return result
        stripped = query.lstrip().upper()
        if stripped.startswith("UPDATE"):
            return "UPDATE 1"
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        self.calls.append(_RecordedCall("fetchrow", query, args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(_RecordedCall("fetchval", query, args))
        if self._fetchval_queue:
            return self._fetchval_queue.pop(0)
        return None

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    # -- Query helpers for assertions --

    @property
    def execute_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "execute"]

    @property
    def fetchrow_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "fetchrow"]

    def calls_matching(self, sql_substring: str) -> list[_RecordedCall]:
        return [c for c in self.calls if sql_substring in c.query]


class _FakePoolAcquireContext:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


class FakePool:
    """Fake asyncpg.Pool that always yields the same FakeConn."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePoolAcquireContext:
        return _FakePoolAcquireContext(self._conn)


# --------------------------------------------------------------------
# Fake repository / event runtime / snapshot service
# --------------------------------------------------------------------


class FakeRepository:
    """Fake ReaderOrchestrationRepository.

    Records ``set_active_base_and_mark_article_ready`` calls and
    optionally raises.
    """

    def __init__(
        self,
        *,
        log: list[str] | None = None,
        raise_on_set_active_base: Exception | None = None,
    ) -> None:
        self.set_active_base_calls: list[dict[str, Any]] = []
        self._log = log
        self._raise = raise_on_set_active_base

    async def set_active_base_and_mark_article_ready(
        self,
        conn: Any,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        updated_at: datetime,
    ) -> None:
        self.set_active_base_calls.append(
            {
                "record_id": record_id,
                "base_id": base_id,
                "expected_generation": expected_generation,
                "updated_at": updated_at,
            }
        )
        if self._log is not None:
            self._log.append("set_active_base")
        if self._raise is not None:
            raise self._raise

    def get_pool(self) -> Any:
        raise RuntimeError("FakeRepository.get_pool should not be called when pool is injected")


class FakeEventRuntime:
    """Fake ReaderEventRuntime.

    Records ``publish_event_in_transaction`` calls and returns a
    fake :class:`ReaderEventEnvelope`. Optionally raises.
    """

    def __init__(
        self,
        *,
        envelope: ReaderEventEnvelope,
        log: list[str] | None = None,
        raise_on_publish: Exception | None = None,
    ) -> None:
        self.publish_calls: list[dict[str, Any]] = []
        self._envelope = envelope
        self._log = log
        self._raise = raise_on_publish

    async def publish_event_in_transaction(
        self,
        conn: Any,
        *,
        record_id: UUID,
        event_type: str,
        payload_json: Any,
        source_run_id: UUID | None = None,
        source_job_id: UUID | None = None,
        source_layer_id: UUID | None = None,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReaderEventEnvelope:
        self.publish_calls.append(
            {
                "record_id": record_id,
                "event_type": event_type,
                "payload_json": dict(payload_json),
                "source_run_id": source_run_id,
                "source_job_id": source_job_id,
                "source_layer_id": source_layer_id,
                "event_id": event_id,
                "created_at": created_at,
            }
        )
        if self._log is not None:
            self._log.append("publish_event")
        if self._raise is not None:
            raise self._raise
        return self._envelope


class _FakeSnapshot:
    """Minimal stand-in for ReaderPlateSnapshot in tests."""

    pass


class FakeSnapshotService:
    """Fake ArticleReadyPersistenceService.

    Records ``load_snapshot`` calls and returns a fake snapshot.
    Optionally raises.
    """

    def __init__(
        self,
        *,
        snapshot: Any,
        log: list[str] | None = None,
        raise_on_load: Exception | None = None,
    ) -> None:
        self.load_calls: list[dict[str, Any]] = []
        self._snapshot = snapshot
        self._log = log
        self._raise = raise_on_load

    async def load_snapshot(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> Any:
        self.load_calls.append(
            {
                "record_id": record_id,
                "user_id": user_id,
                "expected_base_id": expected_base_id,
                "expected_generation": expected_generation,
            }
        )
        if self._log is not None:
            self._log.append("snapshot_loaded")
        if self._raise is not None:
            raise self._raise
        return self._snapshot


# --------------------------------------------------------------------
# Constants and helpers
# --------------------------------------------------------------------


_RECORD_ID = UUID("00000000-0000-0000-0000-000000000001")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000002")
_USER_ID = UUID("00000000-0000-0000-0000-000000000003")
_EVENT_ID = UUID("aaaaaaaa-0000-0000-0000-0000000000aa")
_STABLE_DOC_ID = UUID("bbbbbbbb-0000-0000-0000-0000000000bb")
_BASE_ID = UUID("cccccccc-0000-0000-0000-0000000000cc")
_FROZEN_AT = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)

_FAKE_SNAPSHOT = _FakeSnapshot()

_FAKE_ENVELOPE = ReaderEventEnvelope(
    event_id=_EVENT_ID,
    reading_record_id=_RECORD_ID,
    sequence=42,
    event_type="article_ready",
    payload_json={},
    source_run_id=None,
    source_job_id=None,
    source_layer_id=None,
    created_at=_FROZEN_AT,
)


def _block(
    block_id: str, text: str, order: int, block_type: str = "paragraph"
) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "order_index": order,
        "block_type": block_type,
        "text_content": text,
    }


def _candidate_row(
    *,
    status: str = "ready",
    blocks: list[dict[str, Any]] | None = None,
    record_generation: int = 1,
    title: str | None = "Test Article",
    source_refs: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if blocks is None:
        blocks = [
            _block("h1", "Title", 0, "heading"),
            _block("p1", "First paragraph.", 1),
            _block("p2", "Second paragraph.", 2),
        ]
    return {
        "id": _CANDIDATE_ID,
        "reading_record_id": _RECORD_ID,
        "user_id": _USER_ID,
        "record_generation": record_generation,
        "title": title,
        "blocks_json": blocks,
        "source_refs_json": source_refs or {},
        "quality_json": quality or {},
        "status": status,
    }


def _queue_happy_path(
    conn: _FakeConn,
    *,
    candidate_row: dict[str, Any] | None = None,
) -> None:
    """Queue fetchrow results for a happy-path D6-I2D-A confirm.

    Queues:
        fetchrow #1: candidate row (service SELECT ... FOR UPDATE)
        fetchrow #2: None (L2 插入点 A — confirmed_source_documents 行
                     不存在 → legacy candidate 分支)
        fetchrow #3: None (existing stable doc, persistence idempotency)
        fetchrow #4: {"status": "ready"} (candidate status lookup,
                     persistence _confirm_candidate_document)

    Also sets the supersede UPDATE to return "UPDATE 0".
    """
    conn.queue_fetchrow(candidate_row or _candidate_row())
    conn.queue_fetchrow(None)  # L2: no confirmed source row (legacy)
    conn.queue_fetchrow(None)
    conn.queue_fetchrow({"status": "ready"})
    conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")


def _build_service(
    conn: _FakeConn,
    *,
    log: list[str] | None = None,
    repository: FakeRepository | None = None,
    event_runtime: FakeEventRuntime | None = None,
    snapshot_service: FakeSnapshotService | None = None,
) -> CandidateDocumentConfirmApplicationService:
    """Build a service with all fakes wired."""
    pool = FakePool(conn)
    repo = repository or FakeRepository(log=log)
    runtime = event_runtime or FakeEventRuntime(
        envelope=_FAKE_ENVELOPE, log=log
    )
    snapshot_svc = snapshot_service or FakeSnapshotService(
        snapshot=_FAKE_SNAPSHOT, log=log
    )
    return CandidateDocumentConfirmApplicationService(
        pool=pool,
        repository=repo,
        event_runtime=runtime,
        snapshot_service=snapshot_svc,
    )


def _confirm(
    service: CandidateDocumentConfirmApplicationService,
    *,
    candidate_document_id: UUID = _CANDIDATE_ID,
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    canonicalizer_version: str = "test_canonicalizer_v1",
    builder_version: str = "test_builder_v1",
    segmenter_version: str = "regex_sentence_clause_window_v1",
    language: str | None = "en",
    now: datetime | None = _FROZEN_AT,
) -> CandidateDocumentConfirmApplicationResult:
    import asyncio

    return asyncio.run(
        service.confirm_candidate_document_and_load_snapshot(
            candidate_document_id=candidate_document_id,
            reading_record_id=reading_record_id,
            user_id=user_id,
            canonicalizer_version=canonicalizer_version,
            builder_version=builder_version,
            segmenter_version=segmenter_version,
            language=language,
            now=now,
        )
    )


def _fake_freeze_result(
    *,
    base_id: UUID | None = _BASE_ID,
    idempotent_noop: bool = False,
    record_generation: int = 1,
) -> CandidateDocumentConfirmResult:
    return CandidateDocumentConfirmResult(
        stable_document_id=_STABLE_DOC_ID,
        base_id=base_id,
        reading_record_id=_RECORD_ID,
        record_generation=record_generation,
        document_version=record_generation,
        content_sha256="abc123content",
        canonical_text_sha256="def456canonical",
        block_count=3,
        candidate_confirmed=True,
        idempotent_noop=idempotent_noop,
    )


def _patch_confirm(
    result: CandidateDocumentConfirmResult | None = None,
) -> Any:
    """Return a patch context manager that replaces
    ``confirm_candidate_document`` with an ``AsyncMock`` returning the
    given fake result (defaults to :func:`_fake_freeze_result`).

    Tests that assert specific UUIDs / hashes use this to avoid
    depending on the real confirm's ``uuid4()`` generation.
    """
    return patch(
        "app.services.reader_orchestration."
        "candidate_document_confirm_application_service."
        "confirm_candidate_document",
        new=AsyncMock(return_value=result or _fake_freeze_result()),
    )


# --------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------


class TestHappyPath:
    def test_result_fields_map_freeze_result_event_and_snapshot(self) -> None:
        conn = _FakeConn()
        service = _build_service(conn)

        with _patch_confirm():
            result = _confirm(service)

        assert isinstance(result, CandidateDocumentConfirmApplicationResult)
        assert result.reading_record_id == _RECORD_ID
        assert result.candidate_document_id == _CANDIDATE_ID
        assert result.stable_document_id == _STABLE_DOC_ID
        assert result.base_id == _BASE_ID
        assert result.record_generation == 1
        assert result.document_version == 1
        assert result.content_sha256 == "abc123content"
        assert result.canonical_text_sha256 == "def456canonical"
        assert result.block_count == 3
        assert result.candidate_confirmed is True
        assert result.freeze_idempotent_noop is False
        assert result.article_ready_event_id == _EVENT_ID
        assert result.article_ready_sequence == 42
        assert result.snapshot is _FAKE_SNAPSHOT

    def test_confirm_called_inside_active_transaction(self) -> None:
        """The D6-I2D-A confirm must be called while the transaction
        is active. We verify this indirectly: the confirm function
        checks ``conn.is_in_transaction()`` and would raise if False.
        Since the happy path completes successfully, the transaction
        was active during confirm."""
        conn = _FakeConn()
        _queue_happy_path(conn)
        service = _build_service(conn)

        result = _confirm(service)

        # If confirm ran successfully, the candidate SELECT fetchrow
        # was issued (proving confirm was called inside the
        # transaction).
        assert len(conn.fetchrow_calls) >= 1
        assert "candidate_reading_documents" in conn.fetchrow_calls[0].query
        # Transaction was committed.
        assert conn._last_transaction is not None
        assert conn._last_transaction.committed is True
        assert conn._last_transaction.rolled_back is False

    def test_state_update_and_event_called(self) -> None:
        conn = _FakeConn()
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with _patch_confirm():
            _confirm(service)

        assert len(repo.set_active_base_calls) == 1
        call = repo.set_active_base_calls[0]
        assert call["record_id"] == _RECORD_ID
        assert call["base_id"] == _BASE_ID
        assert call["expected_generation"] == 1
        assert call["updated_at"] == _FROZEN_AT

        assert len(runtime.publish_calls) == 1
        assert len(snapshot_svc.load_calls) == 1

    def test_snapshot_reload_args(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(conn, snapshot_service=snapshot_svc)

        with _patch_confirm():
            _confirm(service)

        call = snapshot_svc.load_calls[0]
        assert call["record_id"] == _RECORD_ID
        assert call["user_id"] == _USER_ID
        assert call["expected_base_id"] == _BASE_ID
        assert call["expected_generation"] == 1


# --------------------------------------------------------------------
# Ordering: state update before event, commit before snapshot
# --------------------------------------------------------------------


class TestOrdering:
    def test_state_update_happens_before_event(self) -> None:
        log: list[str] = []
        conn = _FakeConn(log=log)
        service = _build_service(conn, log=log)

        with _patch_confirm():
            _confirm(service)

        assert log.index("set_active_base") < log.index("publish_event")

    def test_snapshot_reload_after_transaction_commit(self) -> None:
        log: list[str] = []
        conn = _FakeConn(log=log)
        service = _build_service(conn, log=log)

        with _patch_confirm():
            _confirm(service)

        assert log.index("transaction_committed") < log.index("snapshot_loaded")


# --------------------------------------------------------------------
# Event payload
# --------------------------------------------------------------------


class TestEventPayload:
    def test_event_type_is_article_ready(self) -> None:
        conn = _FakeConn()
        _queue_happy_path(conn)
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        service = _build_service(conn, event_runtime=runtime)

        _confirm(service)

        assert runtime.publish_calls[0]["event_type"] == "article_ready"

    def test_event_payload_contains_all_required_fields(self) -> None:
        conn = _FakeConn()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        service = _build_service(conn, event_runtime=runtime)

        with _patch_confirm():
            _confirm(service)

        payload = runtime.publish_calls[0]["payload_json"]
        assert payload["record_id"] == str(_RECORD_ID)
        assert payload["candidate_document_id"] == str(_CANDIDATE_ID)
        assert payload["stable_document_id"] == str(_STABLE_DOC_ID)
        assert payload["base_id"] == str(_BASE_ID)
        assert payload["generation"] == 1
        assert payload["document_version"] == 1
        assert payload["readiness_state"] == "article_ready"
        assert payload["product_state"] == "readable_enhancing"
        assert payload["content_sha256"] == "abc123content"
        assert payload["canonical_text_sha256"] == "def456canonical"
        assert payload["block_count"] == 3
        assert payload["candidate_confirmed"] is True
        assert payload["freeze_idempotent_noop"] is False
        assert payload["source"] == "candidate_document_confirm"

    def test_event_created_at_uses_frozen_at(self) -> None:
        conn = _FakeConn()
        _queue_happy_path(conn)
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        service = _build_service(conn, event_runtime=runtime)

        _confirm(service)

        assert runtime.publish_calls[0]["created_at"] == _FROZEN_AT


# --------------------------------------------------------------------
# Confirm error
# --------------------------------------------------------------------


class TestConfirmError:
    def test_confirm_error_wrapped_and_no_state_event_snapshot(self) -> None:
        """Candidate not found -> confirm raises
        CandidateDocumentConfirmError -> application wraps it,
        transaction rolls back, no state/event/snapshot."""
        conn = _FakeConn()
        conn.queue_fetchrow(None)  # candidate not found
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with pytest.raises(
            CandidateDocumentConfirmApplicationError,
            match=r"Candidate document confirmation failed",
        ) as exc_info:
            _confirm(service)

        # __cause__ must be CandidateDocumentConfirmError.
        assert isinstance(exc_info.value.__cause__, CandidateDocumentConfirmError)

        # Transaction rolled back.
        assert conn._last_transaction is not None
        assert conn._last_transaction.rolled_back is True
        assert conn._last_transaction.committed is False

        # No state update, event, or snapshot.
        assert len(repo.set_active_base_calls) == 0
        assert len(runtime.publish_calls) == 0
        assert len(snapshot_svc.load_calls) == 0


# --------------------------------------------------------------------
# base_id=None fail closed
# --------------------------------------------------------------------


class TestBaseIdNone:
    def test_base_id_none_fails_closed(self) -> None:
        """If confirm returns base_id=None, the service must fail
        closed — no state update, event, or snapshot."""
        fake_result = _fake_freeze_result(base_id=None)
        conn = _FakeConn()
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "confirm_candidate_document",
            new=AsyncMock(return_value=fake_result),
        ):
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"base_id=None",
            ):
                _confirm(service)

        # Transaction rolled back.
        assert conn._last_transaction is not None
        assert conn._last_transaction.rolled_back is True

        # No state update, event, or snapshot.
        assert len(repo.set_active_base_calls) == 0
        assert len(runtime.publish_calls) == 0
        assert len(snapshot_svc.load_calls) == 0


# --------------------------------------------------------------------
# Repository error
# --------------------------------------------------------------------


class TestRepositoryError:
    def test_repository_error_rolls_back_no_event_no_snapshot(self) -> None:
        conn = _FakeConn()
        _queue_happy_path(conn)
        repo = FakeRepository(
            raise_on_set_active_base=ValueError("generation mismatch"),
        )
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with pytest.raises(
            CandidateDocumentConfirmApplicationError,
            match=r"Failed to mark reading record",
        ) as exc_info:
            _confirm(service)

        assert isinstance(exc_info.value.__cause__, ValueError)

        # Transaction rolled back.
        assert conn._last_transaction is not None
        assert conn._last_transaction.rolled_back is True

        # Repository was called but event and snapshot were not.
        assert len(repo.set_active_base_calls) == 1
        assert len(runtime.publish_calls) == 0
        assert len(snapshot_svc.load_calls) == 0


# --------------------------------------------------------------------
# Event error
# --------------------------------------------------------------------


class TestEventError:
    def test_event_error_rolls_back_no_snapshot(self) -> None:
        conn = _FakeConn()
        _queue_happy_path(conn)
        repo = FakeRepository()
        runtime = FakeEventRuntime(
            envelope=_FAKE_ENVELOPE,
            raise_on_publish=RuntimeError("sequence allocation failed"),
        )
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with pytest.raises(
            CandidateDocumentConfirmApplicationError,
            match=r"Failed to publish article_ready event",
        ) as exc_info:
            _confirm(service)

        assert isinstance(exc_info.value.__cause__, RuntimeError)

        # Transaction rolled back.
        assert conn._last_transaction is not None
        assert conn._last_transaction.rolled_back is True

        # State update happened (before event), but no snapshot.
        assert len(repo.set_active_base_calls) == 1
        assert len(runtime.publish_calls) == 1  # attempted
        assert len(snapshot_svc.load_calls) == 0


# --------------------------------------------------------------------
# Snapshot error (transaction already committed)
# --------------------------------------------------------------------


class TestSnapshotError:
    def test_snapshot_error_wrapped_but_transaction_committed(self) -> None:
        """Snapshot load fails AFTER the transaction commits. The
        service wraps the error, but the transaction cannot be
        rolled back — the state update and event are already
        persisted."""
        conn = _FakeConn()
        _queue_happy_path(conn)
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(
            snapshot=_FAKE_SNAPSHOT,
            raise_on_load=ValueError("reading record not found"),
        )
        service = _build_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )

        with pytest.raises(
            CandidateDocumentConfirmApplicationError,
            match=r"Failed to reload snapshot",
        ) as exc_info:
            _confirm(service)

        assert isinstance(exc_info.value.__cause__, ValueError)

        # Transaction was COMMITTED (not rolled back) — the error
        # happened after commit.
        assert conn._last_transaction is not None
        assert conn._last_transaction.committed is True
        assert conn._last_transaction.rolled_back is False

        # State update and event happened (inside the committed
        # transaction). Snapshot was attempted but failed.
        assert len(repo.set_active_base_calls) == 1
        assert len(runtime.publish_calls) == 1
        assert len(snapshot_svc.load_calls) == 1  # attempted


# --------------------------------------------------------------------
# Pass-through: versions / language / now
# --------------------------------------------------------------------


class TestPassThrough:
    def test_versions_language_now_passed_to_confirm(self) -> None:
        """canonicalizer_version, builder_version, segmenter_version,
        language, and now must all be forwarded to
        confirm_candidate_document."""
        fake_result = _fake_freeze_result()
        conn = _FakeConn()
        service = _build_service(conn)

        custom_canonicalizer = "custom_canonicalizer_v2"
        custom_builder = "custom_builder_v2"
        custom_segmenter = "custom_segmenter_v2"
        custom_language = "zh"
        custom_now = datetime(2026, 7, 1, 9, 30, 0, tzinfo=UTC)

        mock = AsyncMock(return_value=fake_result)
        with patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "confirm_candidate_document",
            new=mock,
        ):
            result = _confirm(
                service,
                canonicalizer_version=custom_canonicalizer,
                builder_version=custom_builder,
                segmenter_version=custom_segmenter,
                language=custom_language,
                now=custom_now,
            )

        # Confirm was called once with the right kwargs.
        mock.assert_awaited_once()
        _, kwargs = mock.call_args
        assert kwargs["candidate_document_id"] == _CANDIDATE_ID
        assert kwargs["reading_record_id"] == _RECORD_ID
        assert kwargs["user_id"] == _USER_ID
        assert kwargs["canonicalizer_version"] == custom_canonicalizer
        assert kwargs["builder_version"] == custom_builder
        assert kwargs["segmenter_version"] == custom_segmenter
        assert kwargs["language"] == custom_language
        assert kwargs["now"] == custom_now

        # Result should reflect the fake freeze result.
        assert result.stable_document_id == _STABLE_DOC_ID
        assert result.base_id == _BASE_ID
        assert result.snapshot is _FAKE_SNAPSHOT

    def test_now_defaults_to_datetime_now_utc(self) -> None:
        """If now=None, the service uses datetime.now(UTC). We verify
        by checking that the confirm mock receives a non-None now."""
        fake_result = _fake_freeze_result()
        conn = _FakeConn()
        service = _build_service(conn)

        mock = AsyncMock(return_value=fake_result)
        with patch(
            "app.services.reader_orchestration."
            "candidate_document_confirm_application_service."
            "confirm_candidate_document",
            new=mock,
        ):
            _confirm(service, now=None)

        _, kwargs = mock.call_args
        assert kwargs["now"] is not None
        assert isinstance(kwargs["now"], datetime)


# --------------------------------------------------------------------
# Confirmed-candidate recovery (D6-I2D-B-H)
# --------------------------------------------------------------------


_RECOVERY_CONTENT_SHA256 = "recovery_content_hash"
_RECOVERY_CANONICAL_SHA256 = "recovery_canonical_hash"
_RECOVERY_BLOCK_COUNT = 3
_RECOVERY_EVENT_SEQUENCE = 99
_RECOVERY_EVENT_ID = UUID("dddddddd-0000-0000-0000-0000000000dd")


def _status_error(
    status: str = "confirmed",
    record_generation: int = 1,
) -> CandidateDocumentStatusError:
    """Build a CandidateDocumentStatusError for patching confirm."""
    return CandidateDocumentStatusError(
        f"Candidate document {_CANDIDATE_ID} has status={status!r} "
        f"(expected 'ready').",
        candidate_document_id=_CANDIDATE_ID,
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        record_generation=record_generation,
        status=status,
    )


def _patch_confirm_status_error(
    status: str = "confirmed",
    record_generation: int = 1,
) -> Any:
    """Patch confirm_candidate_document to raise
    CandidateDocumentStatusError with the given status."""
    error = _status_error(status, record_generation)
    return patch(
        "app.services.reader_orchestration."
        "candidate_document_confirm_application_service."
        "confirm_candidate_document",
        new=AsyncMock(side_effect=error),
    )


def _queue_recovery_happy_path(
    conn: _FakeConn,
    *,
    record_generation: int = 1,
    stable_doc_id: UUID = _STABLE_DOC_ID,
    base_id: UUID = _BASE_ID,
    event_id: UUID = _RECOVERY_EVENT_ID,
    event_sequence: int = _RECOVERY_EVENT_SEQUENCE,
    block_count: int = _RECOVERY_BLOCK_COUNT,
    content_sha256: str = _RECOVERY_CONTENT_SHA256,
    canonical_text_sha256: str = _RECOVERY_CANONICAL_SHA256,
    document_version: int = 1,
    payload: dict[str, Any] | None = None,
    readiness_state: str = "article_ready",
    product_state: str = "readable_enhancing",
) -> None:
    """Queue fetchrow/fetchval results for the recovery queries.

    Order must match _recover_confirmed_candidate:
        1. fetchrow: stable_reading_documents
        2. fetchrow: reading_records (with product_state, readiness_state)
        3. fetchrow: reading_bases
        4. fetchval: stable_document_blocks count
        5. fetchrow: reader_events (with payload_json)
        6. fetchrow: candidate_reading_documents source_refs_json
           (L2 — legacy candidate: 无 source 引用三 key，跳过 source 校验)
    """
    if payload is None:
        payload = {
            "source": "candidate_document_confirm",
            "candidate_document_id": str(_CANDIDATE_ID),
            "stable_document_id": str(stable_doc_id),
            "base_id": str(base_id),
            "generation": record_generation,
            "document_version": document_version,
        }
    # (1) stable_reading_documents
    conn.queue_fetchrow({
        "id": stable_doc_id,
        "document_version": document_version,
        "content_sha256": content_sha256,
    })
    # (2) reading_records
    conn.queue_fetchrow({
        "active_base_id": base_id,
        "generation": record_generation,
        "product_state": product_state,
        "readiness_state": readiness_state,
    })
    # (3) reading_bases
    conn.queue_fetchrow({
        "id": base_id,
        "content_sha256": canonical_text_sha256,
    })
    # (4) stable_document_blocks count
    conn.queue_fetchval(block_count)
    # (5) reader_events
    conn.queue_fetchrow({
        "id": event_id,
        "sequence": event_sequence,
        "payload_json": payload,
    })
    # (6) L2 candidate source_refs_json — legacy（无 source 引用三 key）
    conn.queue_fetchrow({"source_refs_json": {}})


def _build_recovery_service(
    conn: _FakeConn,
    *,
    log: list[str] | None = None,
    repository: FakeRepository | None = None,
    event_runtime: FakeEventRuntime | None = None,
    snapshot_service: FakeSnapshotService | None = None,
) -> CandidateDocumentConfirmApplicationService:
    """Build a service for recovery tests. Same as _build_service but
    named for readability."""
    return _build_service(
        conn,
        log=log,
        repository=repository,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )


# --------------------------------------------------------------------
# Recovery happy path
# --------------------------------------------------------------------


class TestConfirmedRecovery:
    def test_confirmed_candidate_enters_recovery_and_returns_committed_state(self) -> None:
        """confirmed candidate -> recovery path -> result fields from
        stable doc / active base / event / snapshot."""
        conn = _FakeConn()
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            result = _confirm(service)

        assert isinstance(result, CandidateDocumentConfirmApplicationResult)
        assert result.reading_record_id == _RECORD_ID
        assert result.candidate_document_id == _CANDIDATE_ID
        assert result.stable_document_id == _STABLE_DOC_ID
        assert result.base_id == _BASE_ID
        assert result.record_generation == 1
        assert result.document_version == 1
        assert result.content_sha256 == _RECOVERY_CONTENT_SHA256
        assert result.canonical_text_sha256 == _RECOVERY_CANONICAL_SHA256
        assert result.block_count == _RECOVERY_BLOCK_COUNT
        assert result.candidate_confirmed is True
        assert result.freeze_idempotent_noop is True
        assert result.article_ready_event_id == _RECOVERY_EVENT_ID
        assert result.article_ready_sequence == _RECOVERY_EVENT_SEQUENCE
        assert result.snapshot is _FAKE_SNAPSHOT

    def test_recovery_does_not_call_set_active_base(self) -> None:
        """Recovery must NOT call repository.set_active_base_and_mark_article_ready."""
        conn = _FakeConn()
        repo = FakeRepository()
        service = _build_recovery_service(conn, repository=repo)
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            _confirm(service)

        assert len(repo.set_active_base_calls) == 0

    def test_recovery_does_not_call_publish_event(self) -> None:
        """Recovery must NOT call event_runtime.publish_event_in_transaction."""
        conn = _FakeConn()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        service = _build_recovery_service(conn, event_runtime=runtime)
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            _confirm(service)

        assert len(runtime.publish_calls) == 0

    def test_recovery_reloads_snapshot_after_commit(self) -> None:
        """Recovery must reload snapshot after the transaction commits,
        using the recovered base_id and record_generation."""
        log: list[str] = []
        conn = _FakeConn(log=log)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT, log=log)
        service = _build_recovery_service(conn, log=log, snapshot_service=snapshot_svc)
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            _confirm(service)

        # Snapshot loaded after commit.
        assert log.index("transaction_committed") < log.index("snapshot_loaded")
        assert len(snapshot_svc.load_calls) == 1
        call = snapshot_svc.load_calls[0]
        assert call["record_id"] == _RECORD_ID
        assert call["user_id"] == _USER_ID
        assert call["expected_base_id"] == _BASE_ID
        assert call["expected_generation"] == 1


# --------------------------------------------------------------------
# Recovery fail-closed: missing/inconsistent committed state
# --------------------------------------------------------------------


class TestRecoveryMissingState:
    """Each test queues a recovery happy-path EXCEPT one query returns
    missing/inconsistent data. The service must fail closed and NOT
    reload the snapshot."""

    def test_missing_stable_reading_documents_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1) stable_reading_documents -> None
        conn.queue_fetchrow(None)
        # Remaining queries are not consumed but queued for safety.
        conn.queue_fetchrow({
            "active_base_id": _BASE_ID, "generation": 1,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })
        conn.queue_fetchrow({"id": _BASE_ID, "content_sha256": "x"})
        conn.queue_fetchval(3)
        conn.queue_fetchrow({"id": _RECOVERY_EVENT_ID, "sequence": 99, "payload_json": {}})

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"no active stable_reading_documents",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_missing_active_base_id_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1) stable_reading_documents -> OK
        conn.queue_fetchrow({
            "id": _STABLE_DOC_ID,
            "document_version": 1,
            "content_sha256": _RECOVERY_CONTENT_SHA256,
        })
        # (2) reading_records -> active_base_id is None
        conn.queue_fetchrow({
            "active_base_id": None, "generation": 1,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"active_base_id is NULL",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_generation_mismatch_fails_closed(self) -> None:
        """reading_records.generation != record_generation must fail."""
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1) stable_reading_documents -> OK (record_generation=1 from error)
        conn.queue_fetchrow({
            "id": _STABLE_DOC_ID,
            "document_version": 1,
            "content_sha256": _RECOVERY_CONTENT_SHA256,
        })
        # (2) reading_records -> generation=5 (mismatch)
        conn.queue_fetchrow({
            "active_base_id": _BASE_ID, "generation": 5,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })

        with _patch_confirm_status_error(record_generation=1):
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"generation=5.*does not match.*record_generation=1",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_missing_reading_bases_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1) stable_reading_documents -> OK
        conn.queue_fetchrow({
            "id": _STABLE_DOC_ID,
            "document_version": 1,
            "content_sha256": _RECOVERY_CONTENT_SHA256,
        })
        # (2) reading_records -> OK
        conn.queue_fetchrow({
            "active_base_id": _BASE_ID, "generation": 1,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })
        # (3) reading_bases -> None
        conn.queue_fetchrow(None)

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"no active reading_bases row",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_zero_blocks_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1) stable_reading_documents -> OK
        conn.queue_fetchrow({
            "id": _STABLE_DOC_ID,
            "document_version": 1,
            "content_sha256": _RECOVERY_CONTENT_SHA256,
        })
        # (2) reading_records -> OK
        conn.queue_fetchrow({
            "active_base_id": _BASE_ID, "generation": 1,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })
        # (3) reading_bases -> OK
        conn.queue_fetchrow({"id": _BASE_ID, "content_sha256": _RECOVERY_CANONICAL_SHA256})
        # (4) stable_document_blocks count -> 0
        conn.queue_fetchval(0)

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"stable_document_blocks count is 0",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_missing_event_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)

        # (1)-(4) all OK
        conn.queue_fetchrow({
            "id": _STABLE_DOC_ID,
            "document_version": 1,
            "content_sha256": _RECOVERY_CONTENT_SHA256,
        })
        conn.queue_fetchrow({
            "active_base_id": _BASE_ID, "generation": 1,
            "product_state": "readable_enhancing",
            "readiness_state": "article_ready",
        })
        conn.queue_fetchrow({"id": _BASE_ID, "content_sha256": _RECOVERY_CANONICAL_SHA256})
        conn.queue_fetchval(_RECOVERY_BLOCK_COUNT)
        # (5) reader_events -> None
        conn.queue_fetchrow(None)

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"no article_ready event.*source='candidate_document_confirm'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0


# --------------------------------------------------------------------
# Recovery hardening: SQL guards + state mismatches + payload mismatches
# --------------------------------------------------------------------


class TestRecoverySqlGuards:
    """Verify the recovery reading_records SQL carries user_id /
    deleted_at / lifecycle_status guards."""

    def test_reading_records_query_has_user_id_guard(self) -> None:
        conn = _FakeConn()
        service = _build_recovery_service(conn)
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            _confirm(service)

        rr_calls = conn.calls_matching("FROM reading_records")
        assert len(rr_calls) == 1
        query = rr_calls[0].query
        assert "user_id = $2" in query
        # The second bind arg must be the user_id.
        assert rr_calls[0].args[1] == _USER_ID

    def test_reading_records_query_has_deleted_at_and_lifecycle_guards(self) -> None:
        conn = _FakeConn()
        service = _build_recovery_service(conn)
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            _confirm(service)

        rr_calls = conn.calls_matching("FROM reading_records")
        assert len(rr_calls) == 1
        query = rr_calls[0].query
        assert "deleted_at IS NULL" in query
        assert "lifecycle_status = 'active'" in query


class TestRecoveryStateMismatch:
    """product_state / readiness_state mismatch must fail closed."""

    def test_product_state_mismatch_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        _queue_recovery_happy_path(conn, product_state="draft")

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"product_state='draft'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_readiness_state_mismatch_fails_closed(self) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        _queue_recovery_happy_path(conn, readiness_state="processing")

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"readiness_state='processing'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0


class TestRecoveryPayloadMismatch:
    """reader_events payload_json field mismatches must fail closed."""

    def _queue_with_payload_override(
        self, conn: _FakeConn, payload: dict[str, Any]
    ) -> None:
        """Queue the recovery happy path but override the reader_events
        payload_json with the given dict."""
        _queue_recovery_happy_path(conn)
        # Replace the reader_events fetchrow (5th of 6; index -2 since the
        # L2 candidate source_refs_json row is queued last) with the
        # custom payload.
        conn._fetchrow_queue[-2] = _FakeRecord({
            "id": _RECOVERY_EVENT_ID,
            "sequence": _RECOVERY_EVENT_SEQUENCE,
            "payload_json": payload,
        })

    def test_payload_base_id_mismatch_fails_closed(self) -> None:
        wrong_base = UUID("eeeeeeee-0000-0000-0000-0000000000ee")
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        self._queue_with_payload_override(
            conn,
            {
                "source": "candidate_document_confirm",
                "candidate_document_id": str(_CANDIDATE_ID),
                "stable_document_id": str(_STABLE_DOC_ID),
                "base_id": str(wrong_base),  # mismatch
                "generation": 1,
                "document_version": 1,
            },
        )

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"payload_json field 'base_id'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    def test_payload_stable_document_id_mismatch_fails_closed(self) -> None:
        wrong_doc = UUID("ffffffff-0000-0000-0000-0000000000ff")
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        self._queue_with_payload_override(
            conn,
            {
                "source": "candidate_document_confirm",
                "candidate_document_id": str(_CANDIDATE_ID),
                "stable_document_id": str(wrong_doc),  # mismatch
                "base_id": str(_BASE_ID),
                "generation": 1,
                "document_version": 1,
            },
        )

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=r"payload_json field 'stable_document_id'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0

    @pytest.mark.parametrize(
        "field,value",
        [
            ("generation", 999),
            ("document_version", 999),
        ],
    )
    def test_payload_generation_or_version_mismatch_fails_closed(
        self, field: str, value: int
    ) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        payload = {
            "source": "candidate_document_confirm",
            "candidate_document_id": str(_CANDIDATE_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "generation": 1,
            "document_version": 1,
        }
        payload[field] = value  # inject mismatch
        self._queue_with_payload_override(conn, payload)

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=rf"payload_json field '{field}'",
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0


class TestRecoveryPayloadInvalid:
    """Invalid / non-object payload_json must fail closed.

    JSONB may arrive as a dict (asyncpg default) or a JSON string (some
    driver configs). Invalid JSON strings, non-object JSON (arrays), and
    non-dict/non-str raw values must all fail closed.
    """

    @pytest.mark.parametrize(
        "raw,match",
        [
            ("{not valid json", r"payload_json is not valid JSON"),
            ("[1, 2, 3]", r"payload_json parsed to a non-object value"),
            ([1, 2, 3], r"payload_json is not a JSON object"),
            (None, r"payload_json is not a JSON object"),
            (42, r"payload_json is not a JSON object"),
        ],
    )
    def test_invalid_or_non_object_payload_fails_closed(
        self, raw: Any, match: str
    ) -> None:
        conn = _FakeConn()
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(conn, snapshot_service=snapshot_svc)
        _queue_recovery_happy_path(conn)
        # Replace the reader_events fetchrow (5th of 6; index -2 since the
        # L2 candidate source_refs_json row is queued last) with the
        # invalid payload raw value.
        conn._fetchrow_queue[-2] = _FakeRecord({
            "id": _RECOVERY_EVENT_ID,
            "sequence": _RECOVERY_EVENT_SEQUENCE,
            "payload_json": raw,
        })

        with _patch_confirm_status_error():
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=match,
            ):
                _confirm(service)

        assert len(snapshot_svc.load_calls) == 0


class TestRecoveryHappyPathPayloadValidation:
    """Explicit happy-path payload validation passes test."""

    def test_happy_path_payload_validation_passes(self) -> None:
        """Recovery with a fully consistent payload_json succeeds —
        all six payload fields (source / candidate_document_id /
        stable_document_id / base_id / generation / document_version)
        match the recovered committed state."""
        conn = _FakeConn()
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )
        # The helper constructs a default payload that matches all
        # expected fields exactly.
        _queue_recovery_happy_path(conn)

        with _patch_confirm_status_error():
            result = _confirm(service)

        # Recovery completed without error and returned the committed
        # state from the validated event payload.
        assert isinstance(result, CandidateDocumentConfirmApplicationResult)
        assert result.candidate_confirmed is True
        assert result.freeze_idempotent_noop is True
        assert result.article_ready_event_id == _RECOVERY_EVENT_ID
        assert result.article_ready_sequence == _RECOVERY_EVENT_SEQUENCE
        assert result.snapshot is _FAKE_SNAPSHOT
        # No state writes or event publishes in the recovery path.
        assert len(repo.set_active_base_calls) == 0
        assert len(runtime.publish_calls) == 0
        assert len(snapshot_svc.load_calls) == 1


# --------------------------------------------------------------------
# Non-confirmed statuses still fail closed (no recovery)
# --------------------------------------------------------------------


class TestNonConfirmedStatusFailClosed:
    @pytest.mark.parametrize("status", ["rejected", "superseded"])
    def test_non_confirmed_status_does_not_enter_recovery(self, status: str) -> None:
        """rejected / superseded must NOT enter recovery — they fail
        closed with CandidateDocumentConfirmApplicationError."""
        conn = _FakeConn()
        repo = FakeRepository()
        runtime = FakeEventRuntime(envelope=_FAKE_ENVELOPE)
        snapshot_svc = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
        service = _build_recovery_service(
            conn,
            repository=repo,
            event_runtime=runtime,
            snapshot_service=snapshot_svc,
        )
        # No recovery queries queued — they should not be consumed.

        with _patch_confirm_status_error(status=status):
            with pytest.raises(
                CandidateDocumentConfirmApplicationError,
                match=rf"status='{status}'",
            ) as exc_info:
                _confirm(service)

        # __cause__ must be the CandidateDocumentStatusError.
        assert isinstance(exc_info.value.__cause__, CandidateDocumentStatusError)

        # No state update, event, or snapshot.
        assert len(repo.set_active_base_calls) == 0
        assert len(runtime.publish_calls) == 0
        assert len(snapshot_svc.load_calls) == 0

        # Transaction rolled back.
        assert conn._last_transaction is not None
        assert conn._last_transaction.rolled_back is True
