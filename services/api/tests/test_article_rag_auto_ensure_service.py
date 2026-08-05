# task-history: D6-I4V (renamed from test_d6_i4v_article_rag_auto_ensure_service.py)
"""Tests for the Article RAG index auto-ensure hook.

Covers:
1. ``ArticleRagAutoEnsureService`` unit tests (disabled, success, typed
   error, unexpected error, no sensitive data leaked, no network calls).
2. Wiring test: ``StableReadyInputApplicationService`` calls auto-ensure
   and enriches the ``article_ready`` event payload with RAG status.
3. Fail-soft test: main flow succeeds even when auto-ensure raises.
4. Constructor injection tests for all three application services.
5. Non-stable path (materialization candidate) does NOT trigger auto-ensure.

All tests use fakes — no real DB / network / LLM / vector.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from app.services.reader_orchestration.article_rag_auto_ensure_service import (
    AUTO_ENSURE_STATUS_DISABLED,
    AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR,
    ArticleRagAutoEnsureResult,
    ArticleRagAutoEnsureService,
)
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ENSURE_STATUS_ENQUEUED,
    ENSURE_STATUS_IDEMPOTENT_NOOP,
    ENSURE_STATUS_NOT_READY,
    ArticleRagIndexEnsureResult,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationService,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceResult,
)
from app.services.reader_orchestration.event_runtime import ReaderEventEnvelope
from app.services.reader_orchestration.extracted_artifact_materialization_service import (
    ExtractedArtifactMaterializationService,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

# ===========================================================================
# Fakes
# ===========================================================================


class _FakeLifecycleService:
    """Fake :class:`ArticleRagIndexLifecycleService` for unit tests."""

    def __init__(
        self,
        *,
        result: ArticleRagIndexEnsureResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._raise = raise_exc
        self.ensure_calls: list[dict[str, Any]] = []

    async def ensure_article_rag_index_job_in_transaction(
        self,
        conn: Any,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        expected_generation: int,
        now: datetime | None = None,
    ) -> ArticleRagIndexEnsureResult:
        self.ensure_calls.append(
            {
                "reading_record_id": reading_record_id,
                "user_id": user_id,
                "expected_generation": expected_generation,
                "now": now,
            }
        )
        if self._raise is not None:
            raise self._raise
        if self._result is not None:
            return self._result
        return ArticleRagIndexEnsureResult(
            reading_record_id=reading_record_id,
            status=ENSURE_STATUS_ENQUEUED,
            reason_code="enqueued",
            idempotent_noop=False,
        )


class _FakeConn:
    """Minimal fake asyncpg.Connection for auto-ensure tests."""

    def __init__(self) -> None:
        self._in_transaction = True

    def is_in_transaction(self) -> bool:
        return self._in_transaction


# ---------------------------------------------------------------------------
# Fakes for wiring test (reusing patterns from test_stable_ready_input_application_service)
# ---------------------------------------------------------------------------


class _WiringFakeConn:
    def __init__(self) -> None:
        self._in_transaction = False

    def transaction(self) -> _WiringFakeTransaction:
        return _WiringFakeTransaction(self)

    async def execute(self, query: str, *args: Any) -> str:
        return "INSERT 0 1"

    def is_in_transaction(self) -> bool:
        return self._in_transaction


class _WiringFakeTransaction:
    def __init__(self, conn: _WiringFakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn._in_transaction = True

    async def __aexit__(self, *args: object) -> bool:
        self._conn._in_transaction = False
        return False


class _WiringFakePoolAcquire:
    def __init__(self, conn: _WiringFakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _WiringFakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        pass


class _WiringFakePool:
    def __init__(self, conn: _WiringFakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _WiringFakePoolAcquire:
        return _WiringFakePoolAcquire(self._conn)


class _WiringFakeRepository:
    def __init__(self) -> None:
        self.set_active_base_calls: list[dict[str, Any]] = []

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
            }
        )

    def get_pool(self) -> Any:
        raise RuntimeError("should not be called")


class _WiringFakeEventRuntime:
    def __init__(self) -> None:
        self.publish_calls: list[dict[str, Any]] = []

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
            }
        )
        return ReaderEventEnvelope(
            event_id=event_id or uuid4(),
            reading_record_id=record_id,
            sequence=1,
            event_type=event_type,
            payload_json=dict(payload_json),
            source_run_id=source_run_id,
            source_job_id=source_job_id,
            source_layer_id=source_layer_id,
            created_at=created_at or datetime.now(UTC),
        )


class _WiringFakeSnapshotService:
    async def load_snapshot(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> Any:
        return object()


class _RecordingAutoEnsureService:
    """Fake auto-ensure service that records calls and returns preset result."""

    def __init__(
        self,
        *,
        result: ArticleRagAutoEnsureResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result or ArticleRagAutoEnsureResult(
            status="enqueued",
            reason_code="enqueued",
        )
        self._raise = raise_exc
        self.ensure_calls: list[dict[str, Any]] = []

    async def ensure_in_transaction(
        self,
        conn: Any,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        expected_generation: int,
        now: datetime | None = None,
    ) -> ArticleRagAutoEnsureResult:
        self.ensure_calls.append(
            {
                "reading_record_id": reading_record_id,
                "user_id": user_id,
                "expected_generation": expected_generation,
                "now": now,
            }
        )
        if self._raise is not None:
            raise self._raise
        return self._result


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
_RECORD_ID = UUID("00000000-0000-0000-0000-000000000002")
_BASE_ID = UUID("00000000-0000-0000-0000-000000000003")
_FROZEN_AT = datetime(2026, 6, 26, 9, 0, 0, tzinfo=UTC)


def _make_ensure_result(
    *,
    status: str = ENSURE_STATUS_ENQUEUED,
    reason_code: str = "enqueued",
    idempotent_noop: bool = False,
) -> ArticleRagIndexEnsureResult:
    return ArticleRagIndexEnsureResult(
        reading_record_id=_RECORD_ID,
        status=status,
        reason_code=reason_code,
        idempotent_noop=idempotent_noop,
        index_run_id=uuid4(),
        job_id=uuid4(),
    )


# ===========================================================================
# ArticleRagAutoEnsureService — unit tests
# ===========================================================================


class TestAutoEnsureServiceDisabled:
    def test_disabled_returns_disabled_status(self) -> None:
        """When ``enabled=False``, returns disabled/rag_disabled immediately."""
        fake_lifecycle = _FakeLifecycleService()
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=False,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
                now=_FROZEN_AT,
            )
        )
        assert result.status == AUTO_ENSURE_STATUS_DISABLED
        assert result.reason_code == "rag_disabled"
        assert result.index_run_id is None
        assert result.job_id is None
        # Lifecycle was NOT called.
        assert len(fake_lifecycle.ensure_calls) == 0


class TestAutoEnsureServiceEnabled:
    def test_enqueued_result_forwarded(self) -> None:
        """Lifecycle returns enqueued → auto-ensure forwards status + ids."""
        fake_lifecycle = _FakeLifecycleService(
            result=_make_ensure_result(status=ENSURE_STATUS_ENQUEUED),
        )
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
                now=_FROZEN_AT,
            )
        )
        assert result.status == ENSURE_STATUS_ENQUEUED
        assert result.reason_code == "enqueued"
        assert result.index_run_id is not None
        assert result.job_id is not None
        assert len(fake_lifecycle.ensure_calls) == 1

    def test_idempotent_noop_forwarded(self) -> None:
        fake_lifecycle = _FakeLifecycleService(
            result=_make_ensure_result(
                status=ENSURE_STATUS_IDEMPOTENT_NOOP,
                reason_code="idempotent_noop",
                idempotent_noop=True,
            ),
        )
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        )
        assert result.status == ENSURE_STATUS_IDEMPOTENT_NOOP

    def test_typed_non_success_forwarded(self) -> None:
        """Lifecycle returns not_ready → auto-ensure forwards it (not error)."""
        fake_lifecycle = _FakeLifecycleService(
            result=_make_ensure_result(
                status=ENSURE_STATUS_NOT_READY,
                reason_code="record_not_article_ready",
            ),
        )
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        )
        assert result.status == ENSURE_STATUS_NOT_READY
        assert result.reason_code == "record_not_article_ready"


class TestAutoEnsureServiceFailSoft:
    def test_typed_exception_returns_fail_soft(self) -> None:
        """Lifecycle raises typed error → fail_soft_error, not raised."""
        fake_lifecycle = _FakeLifecycleService(
            raise_exc=RuntimeError("bootstrap plan error"),
        )
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        )
        assert result.status == AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR
        assert result.reason_code == "auto_ensure_unexpected_error"
        assert result.index_run_id is None
        assert result.job_id is None

    def test_unexpected_exception_no_sensitive_data_leaked(self) -> None:
        """Lifecycle raises with sensitive message → reason_code is fixed,
        raw message is NOT in the result."""
        sensitive_msg = "api_key=sk-secret123 uri=https://zilliz.example.com"
        fake_lifecycle = _FakeLifecycleService(
            raise_exc=RuntimeError(sensitive_msg),
        )
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        result = asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        )
        assert result.status == AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR
        assert result.reason_code == "auto_ensure_unexpected_error"
        # The sensitive message must NOT appear anywhere in the result.
        assert "sk-secret123" not in result.reason_code
        assert "zilliz.example.com" not in result.reason_code
        assert "api_key" not in result.reason_code

    def test_no_network_calls(self) -> None:
        """Auto-ensure service does NOT call embedding/vector/LLM/network.

        The fake lifecycle service records calls — if the auto-ensure
        service tried to call embedding/vector, those would not go through
        the lifecycle service and would fail.  Since the test passes with
        only the fake lifecycle, no network calls were made.
        """
        fake_lifecycle = _FakeLifecycleService()
        service = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        asyncio.run(
            service.ensure_in_transaction(
                _FakeConn(),
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        )
        # Only one lifecycle.ensure call — no embedding/vector/network.
        assert len(fake_lifecycle.ensure_calls) == 1


# ===========================================================================
# Wiring test: StableReadyInputApplicationService
# ===========================================================================


def _freeze_result(
    *,
    base_id: UUID = _BASE_ID,
) -> StableDocumentFreezePersistenceResult:
    text = "Test content for stable document freeze."
    return StableDocumentFreezePersistenceResult(
        stable_document_id=UUID("00000000-0000-0000-0000-000000000010"),
        base_id=base_id,
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        canonical_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        block_count=1,
        candidate_confirmed=False,
        idempotent_noop=False,
    )


def _build_wiring_service(
    conn: _WiringFakeConn,
    *,
    auto_ensure_service: Any,
    repository: _WiringFakeRepository | None = None,
    event_runtime: _WiringFakeEventRuntime | None = None,
) -> StableReadyInputApplicationService:
    return StableReadyInputApplicationService(
        pool=_WiringFakePool(conn),
        repository=repository or _WiringFakeRepository(),
        event_runtime=event_runtime or _WiringFakeEventRuntime(),
        snapshot_service=_WiringFakeSnapshotService(),
        auto_ensure_service=auto_ensure_service,
    )


def _english_text() -> str:
    return (
        "This article explains how communities compare evidence, revise plans, "
        "and discuss tradeoffs before making a decision about public projects. "
        "Each paragraph stays focused on natural language reading, includes "
        "complete sentences, and keeps enough context for vocabulary, grammar, "
        "and sentence analysis to be genuinely useful for an English learner."
    )


class TestStableReadyWiring:
    """Verify auto-ensure is called and payload is enriched."""

    def test_auto_ensure_called_and_payload_enriched(self) -> None:
        """Stable-ready path calls auto-ensure and adds RAG status to payload."""
        conn = _WiringFakeConn()
        fake_auto = _RecordingAutoEnsureService(
            result=ArticleRagAutoEnsureResult(
                status="enqueued",
                reason_code="enqueued",
            ),
        )
        repo = _WiringFakeRepository()
        event_rt = _WiringFakeEventRuntime()
        service = _build_wiring_service(
            conn,
            auto_ensure_service=fake_auto,
            repository=repo,
            event_runtime=event_rt,
        )

        with (
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".normalize_input_document"
            ) as mock_norm,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".build_stable_document_freeze_plan"
            ) as mock_plan,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".persist_stable_document_freeze_plan"
            ) as mock_persist,
        ):
            mock_norm.return_value = type(
                "NormalizedDoc",
                (),
                {
                    "title": "Test",
                    "source_type": "pasted_text",
                    "suitability": type(
                        "Suitability",
                        (),
                        {
                            "outcome": "stable_document_ready",
                            "flags": [],
                            "reasons": [],
                        },
                    )(),
                    "normalized_text": _english_text(),
                    "content_sha256": "a" * 64,
                    "blocks": [],
                    # M1 收尾（c13e9eb29）给 NormalizedInputDocument
                    # 加了 parser_identity 字段；stable_ready 服务现在
                    # 会读取 normalized.parser_identity。mock 需同步，
                    # pasted_text 路径为 None。
                    "parser_identity": None,
                },
            )()
            mock_plan.return_value = object()
            mock_persist.return_value = _freeze_result()

            result = asyncio.run(
                service.freeze_stable_ready_input_and_load_snapshot(
                    user_id=_USER_ID,
                    source_type="pasted_text",
                    text=_english_text(),
                    now=_FROZEN_AT,
                )
            )

        # Main flow succeeded.
        assert result is not None
        # Auto-ensure was called.
        assert len(fake_auto.ensure_calls) == 1
        assert fake_auto.ensure_calls[0]["user_id"] == _USER_ID
        # Event payload contains RAG status.
        assert len(event_rt.publish_calls) == 1
        payload = event_rt.publish_calls[0]["payload_json"]
        assert "article_rag_index" in payload
        assert payload["article_rag_index"]["status"] == "enqueued"
        assert payload["article_rag_index"]["reason_code"] == "enqueued"

    def test_main_flow_succeeds_when_auto_ensure_raises(self) -> None:
        """Fail-soft: lifecycle raises → real auto-ensure swallows → main flow succeeds.

        Uses the REAL ``ArticleRagAutoEnsureService`` with a fake lifecycle
        that raises, proving the full fail-soft chain through the wiring:
        lifecycle raises → auto-ensure catches → main flow commits →
        event payload records ``fail_soft_error``.
        """
        conn = _WiringFakeConn()
        fake_lifecycle = _FakeLifecycleService(
            raise_exc=RuntimeError("unexpected Zilliz connection error"),
        )
        real_auto = ArticleRagAutoEnsureService(
            lifecycle_service=fake_lifecycle,
            enabled=True,
        )
        repo = _WiringFakeRepository()
        event_rt = _WiringFakeEventRuntime()
        service = _build_wiring_service(
            conn,
            auto_ensure_service=real_auto,
            repository=repo,
            event_runtime=event_rt,
        )

        with (
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".normalize_input_document"
            ) as mock_norm,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".build_stable_document_freeze_plan"
            ) as mock_plan,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".persist_stable_document_freeze_plan"
            ) as mock_persist,
        ):
            mock_norm.return_value = type(
                "NormalizedDoc",
                (),
                {
                    "title": "Test",
                    "source_type": "pasted_text",
                    "suitability": type(
                        "Suitability",
                        (),
                        {
                            "outcome": "stable_document_ready",
                            "flags": [],
                            "reasons": [],
                        },
                    )(),
                    "normalized_text": _english_text(),
                    "content_sha256": "a" * 64,
                    "blocks": [],
                    # M1 收尾（c13e9eb29）给 NormalizedInputDocument
                    # 加了 parser_identity 字段；stable_ready 服务现在
                    # 会读取 normalized.parser_identity。mock 需同步，
                    # pasted_text 路径为 None。
                    "parser_identity": None,
                },
            )()
            mock_plan.return_value = object()
            mock_persist.return_value = _freeze_result()

            result = asyncio.run(
                service.freeze_stable_ready_input_and_load_snapshot(
                    user_id=_USER_ID,
                    source_type="pasted_text",
                    text=_english_text(),
                    now=_FROZEN_AT,
                )
            )

        # Main flow succeeded — no exception propagated.
        assert result is not None
        # Lifecycle was called (it raised, but auto-ensure swallowed it).
        assert len(fake_lifecycle.ensure_calls) == 1
        # Event was still published.
        assert len(event_rt.publish_calls) == 1
        # Payload records fail-soft error, not the raw exception message.
        payload = event_rt.publish_calls[0]["payload_json"]
        assert payload["article_rag_index"]["status"] == AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR
        assert payload["article_rag_index"]["reason_code"] == "auto_ensure_unexpected_error"
        # Sensitive message must NOT leak into the payload.
        assert "Zilliz" not in str(payload)

    def test_disabled_auto_ensure_still_succeeds(self) -> None:
        """When auto-ensure returns disabled, main flow still succeeds."""
        conn = _WiringFakeConn()
        fake_auto = _RecordingAutoEnsureService(
            result=ArticleRagAutoEnsureResult(
                status=AUTO_ENSURE_STATUS_DISABLED,
                reason_code="rag_disabled",
            ),
        )
        repo = _WiringFakeRepository()
        event_rt = _WiringFakeEventRuntime()
        service = _build_wiring_service(
            conn,
            auto_ensure_service=fake_auto,
            repository=repo,
            event_runtime=event_rt,
        )

        with (
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".normalize_input_document"
            ) as mock_norm,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".build_stable_document_freeze_plan"
            ) as mock_plan,
            patch(
                "app.services.reader_orchestration.stable_ready_input_application_service"
                ".persist_stable_document_freeze_plan"
            ) as mock_persist,
        ):
            mock_norm.return_value = type(
                "NormalizedDoc",
                (),
                {
                    "title": "Test",
                    "source_type": "pasted_text",
                    "suitability": type(
                        "Suitability",
                        (),
                        {
                            "outcome": "stable_document_ready",
                            "flags": [],
                            "reasons": [],
                        },
                    )(),
                    "normalized_text": _english_text(),
                    "content_sha256": "a" * 64,
                    "blocks": [],
                    # M1 收尾（c13e9eb29）给 NormalizedInputDocument
                    # 加了 parser_identity 字段；stable_ready 服务现在
                    # 会读取 normalized.parser_identity。mock 需同步，
                    # pasted_text 路径为 None。
                    "parser_identity": None,
                },
            )()
            mock_plan.return_value = object()
            mock_persist.return_value = _freeze_result()

            result = asyncio.run(
                service.freeze_stable_ready_input_and_load_snapshot(
                    user_id=_USER_ID,
                    source_type="pasted_text",
                    text=_english_text(),
                    now=_FROZEN_AT,
                )
            )

        assert result is not None
        assert len(fake_auto.ensure_calls) == 1
        payload = event_rt.publish_calls[0]["payload_json"]
        assert payload["article_rag_index"]["status"] == AUTO_ENSURE_STATUS_DISABLED


# ===========================================================================
# Constructor injection tests
# ===========================================================================


class TestConstructorInjection:
    """Verify all three application services accept auto_ensure_service."""

    def test_stable_ready_accepts_auto_ensure_service(self) -> None:
        fake = _RecordingAutoEnsureService()
        service = StableReadyInputApplicationService(
            pool=object(),
            auto_ensure_service=fake,
        )
        assert service._get_auto_ensure_service() is fake

    def test_candidate_confirm_accepts_auto_ensure_service(self) -> None:
        fake = _RecordingAutoEnsureService()
        service = CandidateDocumentConfirmApplicationService(
            pool=object(),
            auto_ensure_service=fake,
        )
        assert service._get_auto_ensure_service() is fake

    def test_materialization_accepts_auto_ensure_service(self) -> None:
        fake = _RecordingAutoEnsureService()
        service = ExtractedArtifactMaterializationService(auto_ensure_service=fake)
        assert service._get_auto_ensure_service() is fake

    def test_stable_ready_lazy_init_when_not_injected(self) -> None:
        """When not injected, _get_auto_ensure_service creates a default."""
        service = StableReadyInputApplicationService(pool=object())
        # The default service should be created (enabled=False by default
        # since reader_article_rag_enabled defaults to False).
        auto = service._get_auto_ensure_service()
        assert isinstance(auto, ArticleRagAutoEnsureService)

    def test_candidate_confirm_lazy_init_when_not_injected(self) -> None:
        service = CandidateDocumentConfirmApplicationService(pool=object())
        auto = service._get_auto_ensure_service()
        assert isinstance(auto, ArticleRagAutoEnsureService)

    def test_materialization_lazy_init_when_not_injected(self) -> None:
        service = ExtractedArtifactMaterializationService()
        auto = service._get_auto_ensure_service()
        assert isinstance(auto, ArticleRagAutoEnsureService)


# ===========================================================================
# Non-stable path: auto-ensure must NOT be triggered
# ===========================================================================


class TestNonStablePathNoAutoEnsure:
    """RAG ensure must NOT be triggered on candidate/rejected/non-stable paths.

    The auto-ensure hook is only in ``_materialize_stable`` (stable path).
    The candidate path (``_materialize_candidate``) and the action_required
    path do NOT call ``_get_auto_ensure_service()``.

    This is verified by code structure: the auto-ensure call is only
    present in ``_materialize_stable``, not in ``_materialize_candidate``
    or the action_required branch.
    """

    def test_materialization_candidate_path_has_no_auto_ensure_call(self) -> None:
        """Verify _materialize_candidate does not reference auto_ensure_service."""
        import inspect

        from app.services.reader_orchestration import (
            extracted_artifact_materialization_service as mod,
        )

        source = inspect.getsource(mod.ExtractedArtifactMaterializationService)
        # Find _materialize_stable and _materialize_candidate method bodies
        stable_start = source.index("async def _materialize_stable")
        candidate_start = source.index("async def _materialize_candidate")
        # Get the text between _materialize_stable and _materialize_candidate
        stable_body = source[stable_start:candidate_start]
        # Get the text from _materialize_candidate to the next method
        # (or end of class)
        remaining = source[candidate_start:]
        # Find the next method after _materialize_candidate
        next_method = remaining.find("async def ", len("async def _materialize_candidate"))
        if next_method == -1:
            candidate_body = remaining
        else:
            candidate_body = remaining[:next_method]

        # _materialize_stable MUST reference auto_ensure
        assert "auto_ensure" in stable_body, (
            "_materialize_stable should call auto_ensure_service"
        )
        # _materialize_candidate must NOT reference auto_ensure
        assert "auto_ensure" not in candidate_body, (
            "_materialize_candidate must NOT trigger auto-ensure"
        )
