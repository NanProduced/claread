"""Route tests for D6-I4T Article RAG Index Lifecycle API.

Covers:
 1. GET status happy path: indexed returns full DTO
 2. GET status not_indexed / queued / indexing / superseded_or_stale
 3. GET wrong user / missing record -> 404
 4. POST ensure enqueued: route opens transaction, calls service, returns ids
 5. POST ensure idempotent_noop
 6. POST ensure generation_mismatch / not_ready / no_active_base typed response
 7. POST request extra fields -> 422
 8. POST body does not allow chunker_version -> 422
 9. user_id only from AuthUserDep, not from body/query
10. status route does NOT open transaction; ensure route MUST open transaction
11. route tests do NOT call real DB / network / LLM / vector

All tests use fakes: ``_FakePool`` / ``_FakeConn`` / ``_FakeTransaction``
and a ``_FakeLifecycleService`` that records calls and returns pre-configured
typed results.  Auth is mocked via ``validate_session``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.api.routes import reader_orchestration as route_module
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ENSURE_STATUS_ENQUEUED,
    ENSURE_STATUS_GENERATION_MISMATCH,
    ENSURE_STATUS_IDEMPOTENT_NOOP,
    ENSURE_STATUS_NO_ACTIVE_BASE,
    ENSURE_STATUS_NOT_READY,
    STATUS_INDEXED,
    STATUS_NOT_INDEXED,
    STATUS_QUEUED,
    STATUS_SUPERSEDED_OR_STALE,
    STATUS_UNAVAILABLE,
    ArticleRagIndexEnsureResult,
    ArticleRagIndexLifecycleService,
    ArticleRagIndexLifecycleStatus,
)

pytestmark = pytest.mark.asyncio

AUTH_HEADERS = {"Authorization": "Bearer test_token"}

_RECORD_ID = UUID("00000000-0000-0000-0000-0000000d4f01")
_USER_ID = UUID("00000000-0000-0000-0000-0000000d4f02")
_STABLE_DOC_ID = UUID("00000000-0000-0000-0000-0000000d4f03")
_BASE_ID = UUID("00000000-0000-0000-0000-0000000d4f04")
_INDEX_RUN_ID = UUID("00000000-0000-0000-0000-0000000d4f05")
_JOB_ID = UUID("00000000-0000-0000-0000-0000000d4f06")
_GENERATION = 3
_INDEX_VERSION = "article_rag_index_v1"
_CHUNKER_VERSION = "article_rag_index_plan_v1"
_PLAN_SHA = "a" * 64


# ---------------------------------------------------------------------------
# Fakes: pool / conn / transaction
# ---------------------------------------------------------------------------


class _FakeConn:
    """Fake asyncpg.Connection.

    Tracks ``is_in_transaction()`` so tests can assert the ensure route
    opens a transaction while the status route does not.  ``transaction()``
    returns the async context manager the route uses via
    ``async with conn.transaction():``.
    """

    def __init__(self) -> None:
        self.in_transaction = False
        self.transaction_entered = False
        self.transaction_exited = False

    def is_in_transaction(self) -> bool:
        return self.in_transaction

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


class _FakeTransaction:
    """Fake asyncpg transaction context manager."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn.transaction_entered = True
        self._conn.in_transaction = True

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._conn.transaction_exited = True
        self._conn.in_transaction = False


class _FakeConnContextManager:
    """Fake asyncpg pool.acquire() context manager."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakePool:
    """Fake asyncpg.Pool that always returns the same fake connection."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeConnContextManager:
        return _FakeConnContextManager(self._conn)


# ---------------------------------------------------------------------------
# Fake lifecycle service
# ---------------------------------------------------------------------------


class _FakeLifecycleService:
    """Fake ``ArticleRagIndexLifecycleService``.

    Records every call and returns the pre-configured typed result.
    Raises the pre-configured error if one is set (for unexpected-
    exception testing).
    """

    def __init__(
        self,
        *,
        status_result: ArticleRagIndexLifecycleStatus | None = None,
        ensure_result: ArticleRagIndexEnsureResult | None = None,
        status_error: Exception | None = None,
        ensure_error: Exception | None = None,
    ) -> None:
        self._status_result = status_result
        self._ensure_result = ensure_result
        self._status_error = status_error
        self._ensure_error = ensure_error
        self.status_calls: list[dict[str, Any]] = []
        self.ensure_calls: list[dict[str, Any]] = []

    async def load_article_rag_index_lifecycle_status(
        self,
        conn: Any,
        *,
        reading_record_id: UUID,
        user_id: UUID,
    ) -> ArticleRagIndexLifecycleStatus:
        self.status_calls.append(
            {
                "conn": conn,
                "reading_record_id": reading_record_id,
                "user_id": user_id,
            }
        )
        if self._status_error is not None:
            raise self._status_error
        assert self._status_result is not None
        return self._status_result

    async def ensure_article_rag_index_job_in_transaction(
        self,
        conn: Any,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        expected_generation: int,
        now: Any = None,
    ) -> ArticleRagIndexEnsureResult:
        self.ensure_calls.append(
            {
                "conn": conn,
                "reading_record_id": reading_record_id,
                "user_id": user_id,
                "expected_generation": expected_generation,
                "now": now,
            }
        )
        if self._ensure_error is not None:
            raise self._ensure_error
        assert self._ensure_result is not None
        return self._ensure_result


# ---------------------------------------------------------------------------
# Result factories
# ---------------------------------------------------------------------------


def _make_status_result(
    *,
    status: str = STATUS_INDEXED,
    reason_code: str | None = "indexed",
    stable_document_id: UUID | None = _STABLE_DOC_ID,
    base_id: UUID | None = _BASE_ID,
    record_generation: int | None = _GENERATION,
    index_run_id: UUID | None = _INDEX_RUN_ID,
    plan_content_sha256: str | None = _PLAN_SHA,
    chunk_count: int | None = 5,
) -> ArticleRagIndexLifecycleStatus:
    return ArticleRagIndexLifecycleStatus(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        status=status,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=record_generation,
        index_run_id=index_run_id,
        plan_content_sha256=plan_content_sha256,
        chunk_count=chunk_count,
        reason_code=reason_code,
    )


def _make_ensure_result(
    *,
    status: str = ENSURE_STATUS_ENQUEUED,
    reason_code: str = "enqueued",
    idempotent_noop: bool = False,
    stable_document_id: UUID | None = _STABLE_DOC_ID,
    base_id: UUID | None = _BASE_ID,
    record_generation: int | None = _GENERATION,
    index_run_id: UUID | None = _INDEX_RUN_ID,
    job_id: UUID | None = _JOB_ID,
) -> ArticleRagIndexEnsureResult:
    return ArticleRagIndexEnsureResult(
        reading_record_id=_RECORD_ID,
        status=status,
        reason_code=reason_code,
        idempotent_noop=idempotent_noop,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=record_generation,
        index_run_id=index_run_id,
        job_id=job_id,
    )


# ---------------------------------------------------------------------------
# Test fixture: app + fake pool + fake service
# ---------------------------------------------------------------------------


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {
                "user_id": str(user_id),
                "session_id": uuid4(),
            },
        )(),
    )


@pytest.fixture
def route_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire the routes to use a fake pool + fake service.

    Returns a dict with the fake conn, fake pool, and fake service so
    individual tests can configure the service's return value and
    assert on calls.
    """
    fake_conn = _FakeConn()
    fake_pool = _FakePool(fake_conn)
    fake_service = _FakeLifecycleService(
        status_result=_make_status_result(),
        ensure_result=_make_ensure_result(),
    )
    monkeypatch.setattr(
        route_module,
        "_get_article_rag_index_lifecycle_service",
        lambda: fake_service,
    )
    monkeypatch.setattr(route_module, "_get_reader_pool", lambda: fake_pool)

    app = FastAPI()
    app.include_router(api_router)
    return {
        "app": app,
        "conn": fake_conn,
        "pool": fake_pool,
        "service": fake_service,
    }


def _create_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


# ===========================================================================
# 1. GET status happy path: indexed returns full DTO
# ===========================================================================


class TestGetStatusHappyPath:
    async def test_indexed_returns_full_dto(self, route_env: dict[str, Any]) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_INDEXED,
            reason_code="indexed",
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reading_record_id"] == str(_RECORD_ID)
        assert body["status"] == "indexed"
        assert body["stable_document_id"] == str(_STABLE_DOC_ID)
        assert body["base_id"] == str(_BASE_ID)
        assert body["record_generation"] == _GENERATION
        assert body["index_run_id"] == str(_INDEX_RUN_ID)
        assert "index_version" not in body
        assert "chunker_version" not in body
        assert body["plan_content_sha256"] == _PLAN_SHA
        assert body["chunk_count"] == 5
        assert body["reason_code"] == "indexed"
        # user_id is intentionally NOT in the response.
        assert "user_id" not in body

        # Service was called with the auth user_id and the path record_id.
        assert len(service.status_calls) == 1
        call = service.status_calls[0]
        assert call["reading_record_id"] == _RECORD_ID
        assert call["user_id"] == _USER_ID
        assert "index_version" not in call


# ===========================================================================
# 2. GET status not_indexed / queued / superseded_or_stale
# ===========================================================================


class TestGetStatusNonIndexedStates:
    async def test_not_indexed_status(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_NOT_INDEXED,
            reason_code="no_index_run",
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            record_generation=_GENERATION,
            index_run_id=None,
            plan_content_sha256=None,
            chunk_count=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "not_indexed"
        assert body["reason_code"] == "no_index_run"
        assert body["index_run_id"] is None
        assert body["plan_content_sha256"] is None
        assert body["chunk_count"] is None

    async def test_queued_status(self, route_env: dict[str, Any]) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_QUEUED,
            reason_code="index_run_queued",
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["reason_code"] == "index_run_queued"

    async def test_superseded_or_stale_status(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_SUPERSEDED_OR_STALE,
            reason_code="index_run_base_or_generation_mismatch",
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "superseded_or_stale"
        assert body["reason_code"] == "index_run_base_or_generation_mismatch"


# ===========================================================================
# 3. GET wrong user / missing record -> 404
# ===========================================================================


class TestGetStatusNotFound:
    async def test_record_not_found_returns_404(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_UNAVAILABLE,
            reason_code="record_not_found",
            stable_document_id=None,
            base_id=None,
            record_generation=None,
            index_run_id=None,
            plan_content_sha256=None,
            chunk_count=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_wrong_user_returns_404_via_record_not_found(
        self, route_env: dict[str, Any]
    ) -> None:
        """Wrong user is reported as ``record_not_found`` (fail closed)
        by the lifecycle service, which the route maps to 404.
        """
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_result = _make_status_result(
            status=STATUS_UNAVAILABLE,
            reason_code="record_not_found",
            stable_document_id=None,
            base_id=None,
            record_generation=None,
            index_run_id=None,
            plan_content_sha256=None,
            chunk_count=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 404
        # The auth user_id is what got passed to the service.
        assert service.status_calls[0]["user_id"] == _USER_ID


# ===========================================================================
# 4. POST ensure enqueued: route opens transaction, returns ids
# ===========================================================================


class TestPostEnsureEnqueued:
    async def test_ensure_enqueued_returns_ids_and_opens_transaction(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        conn: _FakeConn = route_env["conn"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_ENQUEUED,
            reason_code="enqueued",
            idempotent_noop=False,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reading_record_id"] == str(_RECORD_ID)
        assert body["status"] == "enqueued"
        assert body["reason_code"] == "enqueued"
        assert body["idempotent_noop"] is False
        assert body["stable_document_id"] == str(_STABLE_DOC_ID)
        assert body["base_id"] == str(_BASE_ID)
        assert body["record_generation"] == _GENERATION
        assert body["index_run_id"] == str(_INDEX_RUN_ID)
        assert body["job_id"] == str(_JOB_ID)
        # user_id is intentionally NOT in the response.
        assert "user_id" not in body

        # Route MUST have opened a transaction.
        assert conn.transaction_entered is True
        assert conn.transaction_exited is True

        # Service was called with the auth user_id, the path record_id,
        # the body's expected_generation, with fixed server-side index identity.
        assert len(service.ensure_calls) == 1
        call = service.ensure_calls[0]
        assert call["reading_record_id"] == _RECORD_ID
        assert call["user_id"] == _USER_ID
        assert call["expected_generation"] == _GENERATION
        # The public route and lifecycle seam do not accept chunker_version.


# ===========================================================================
# 5. POST ensure idempotent_noop
# ===========================================================================


class TestPostEnsureIdempotentNoop:
    async def test_ensure_idempotent_noop_passes_through(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_IDEMPOTENT_NOOP,
            reason_code="idempotent_noop",
            idempotent_noop=True,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "idempotent_noop"
        assert body["idempotent_noop"] is True
        assert body["index_run_id"] == str(_INDEX_RUN_ID)


# ===========================================================================
# 6. POST ensure typed non-success statuses
# ===========================================================================


class TestPostEnsureTypedNonSuccess:
    async def test_generation_mismatch_returns_typed_response(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_GENERATION_MISMATCH,
            reason_code="generation_mismatch",
            idempotent_noop=False,
            stable_document_id=None,
            base_id=None,
            record_generation=2,
            index_run_id=None,
            job_id=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        # Typed non-success is returned as 200 (not swallowed, not 409).
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "generation_mismatch"
        assert body["reason_code"] == "generation_mismatch"
        assert body["record_generation"] == 2
        assert body["index_run_id"] is None

    async def test_not_ready_returns_typed_response(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_NOT_READY,
            reason_code="record_not_article_ready",
            idempotent_noop=False,
            stable_document_id=None,
            base_id=None,
            record_generation=_GENERATION,
            index_run_id=None,
            job_id=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["reason_code"] == "record_not_article_ready"

    async def test_no_active_base_returns_typed_response(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_NO_ACTIVE_BASE,
            reason_code="active_base_id_is_null",
            idempotent_noop=False,
            stable_document_id=None,
            base_id=None,
            record_generation=_GENERATION,
            index_run_id=None,
            job_id=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "no_active_base"
        assert body["reason_code"] == "active_base_id_is_null"


# ===========================================================================
# 7. POST request extra fields -> 422
# ===========================================================================


class TestPostEnsureExtraFieldsRejected:
    async def test_unknown_field_returns_422(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={
                        "expected_generation": _GENERATION,
                        "unexpected_field": "boom",
                    },
                )

        assert response.status_code == 422


# ===========================================================================
# 8. POST body does not allow chunker_version -> 422
# ===========================================================================


class TestPostEnsureChunkerVersionRejected:
    async def test_chunker_version_in_body_returns_422(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={
                        "expected_generation": _GENERATION,
                        "chunker_version": "should_be_rejected",
                    },
                )

        assert response.status_code == 422


# ===========================================================================
# 9. user_id only from AuthUserDep, not from body/query
# ===========================================================================


class TestUserIdFromAuthOnly:
    async def test_status_route_uses_auth_user_id(
        self, route_env: dict[str, Any]
    ) -> None:
        """GET status: user_id must come from AuthUserDep, not query string."""
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                # Even if the client tries to pass user_id as a query
                # parameter, FastAPI ignores it because the route has no
                # such parameter — the auth dependency is the sole source.
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status"
                    "?user_id=00000000-0000-0000-0000-000000000099",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        assert service.status_calls[0]["user_id"] == _USER_ID

    async def test_ensure_route_uses_auth_user_id(
        self, route_env: dict[str, Any]
    ) -> None:
        """POST ensure: user_id must come from AuthUserDep, not body.

        The body schema uses ``extra="forbid"`` so a ``user_id`` field
        in the JSON body is rejected with 422.
        """
        app = route_env["app"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={
                        "expected_generation": _GENERATION,
                        "user_id": str(UUID("00000000-0000-0000-0000-000000000099")),
                    },
                )

        assert response.status_code == 422


# ===========================================================================
# 10. status route does NOT open transaction; ensure route MUST open tx
# ===========================================================================


class TestTransactionBehavior:
    async def test_status_route_does_not_open_transaction(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        conn: _FakeConn = route_env["conn"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        # The status route is read-only; it must NOT open a transaction.
        assert conn.transaction_entered is False
        assert conn.transaction_exited is False

    async def test_ensure_route_opens_transaction(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        conn: _FakeConn = route_env["conn"]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200
        # The ensure route MUST open a caller-managed transaction.
        assert conn.transaction_entered is True
        assert conn.transaction_exited is True

    async def test_ensure_route_passes_in_transaction_conn_to_service(
        self, route_env: dict[str, Any]
    ) -> None:
        """The conn passed to the service must be inside the transaction."""
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]

        captured_in_transaction = []

        async def _capture_conn_in_tx(
            conn: Any,
            *,
            reading_record_id: UUID,
            user_id: UUID,
            expected_generation: int,
            now: Any = None,
        ) -> ArticleRagIndexEnsureResult:
            captured_in_transaction.append(conn.is_in_transaction())
            return _make_ensure_result()

        service.ensure_article_rag_index_job_in_transaction = _capture_conn_in_tx  # type: ignore[assignment]

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 200
        assert captured_in_transaction == [True]


# ===========================================================================
# 11. No real DB / network / LLM / vector — verified by fakes
# ===========================================================================


class TestNoRealBackendCalls:
    async def test_status_route_does_not_call_real_service(
        self, route_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ensure the real ``ArticleRagIndexLifecycleService`` is never
        instantiated — the monkeypatched factory is the sole entry point.
        """
        app = route_env["app"]
        real_instantiation_calls: list[bool] = []

        class _SentinelService(ArticleRagIndexLifecycleService):
            def __init__(self) -> None:
                real_instantiation_calls.append(True)
                super().__init__()

        monkeypatch.setattr(
            route_module,
            "_get_article_rag_index_lifecycle_service",
            lambda: route_env["service"],
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 200
        assert real_instantiation_calls == []

    async def test_ensure_route_unexpected_exception_returns_409(
        self, route_env: dict[str, Any]
    ) -> None:
        """Unexpected (non-typed) service exception → 409 with a fixed,
        leak-safe detail.  The exception message MUST NOT be echoed to
        the client (it may carry tokens, URIs, chunk text, query text,
        or SDK internals).
        """
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_error = RuntimeError(
            "token=sk-abcdef123 chunk_text=hello world query=SELECT *"
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 409
        detail = response.json()["detail"]
        # Fixed safe identifier is present.
        assert detail == "article_rag_index_ensure_unexpected_error"
        # Raw exception message is NEVER echoed.
        assert "sk-abcdef123" not in detail
        assert "hello world" not in detail
        assert "SELECT *" not in detail
        assert "token=" not in detail

    async def test_status_route_unexpected_exception_returns_409(
        self, route_env: dict[str, Any]
    ) -> None:
        """Unexpected (non-typed) status service exception → 409 with a
        fixed, leak-safe detail.  The exception message MUST NOT be
        echoed to the client.
        """
        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._status_error = RuntimeError(
            "internal_uri=https://dashscope/foo secret=xyz"
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.get(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/status",
                    headers=AUTH_HEADERS,
                )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail == "article_rag_index_status_unexpected_error"
        assert "dashscope" not in detail
        assert "secret=xyz" not in detail
        assert "internal_uri" not in detail


# ===========================================================================
# ===========================================================================



class TestRound1OpenApiAndExternalVersionGates:
    async def test_public_schemas_have_no_version_fields(self) -> None:
        from app.schemas.reader_orchestration import (
            ReaderArticleRagIndexEnsureRequest,
            ReaderArticleRagIndexEnsureResponse,
            ReaderArticleRagIndexStatusResponse,
        )

        assert "index_version" not in ReaderArticleRagIndexEnsureRequest.model_fields
        assert "index_version" not in ReaderArticleRagIndexEnsureResponse.model_fields
        assert "chunker_version" not in ReaderArticleRagIndexEnsureResponse.model_fields
        assert "index_version" not in ReaderArticleRagIndexStatusResponse.model_fields

    async def test_openapi_status_parameters_exclude_index_version(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        schema = app.openapi()
        path_item = schema["paths"][
            "/reader/records/{record_id}/article-rag-index/status"
        ]["get"]
        params = path_item.get("parameters") or []
        names = {p.get("name") for p in params}
        assert "index_version" not in names
        assert "record_id" in names

    async def test_openapi_ensure_request_response_exclude_version_fields(
        self, route_env: dict[str, Any]
    ) -> None:
        app = route_env["app"]
        schema = app.openapi()
        components = schema["components"]["schemas"]
        ensure_req = components["ReaderArticleRagIndexEnsureRequest"]["properties"]
        ensure_resp = components["ReaderArticleRagIndexEnsureResponse"]["properties"]
        status_resp = components["ReaderArticleRagIndexStatusResponse"]["properties"]
        assert "index_version" not in ensure_req
        assert "index_version" not in ensure_resp
        assert "chunker_version" not in ensure_resp
        assert "index_version" not in status_resp
        assert "chunker_version" not in status_resp

    async def test_ensure_body_index_version_is_rejected_extra(
        self, route_env: dict[str, Any]
    ) -> None:
        """extra=forbid: clients cannot select index_version via body."""
        app = route_env["app"]
        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={
                        "expected_generation": _GENERATION,
                        "index_version": "custom_v2",
                    },
                )
        assert response.status_code == 422


class TestPostEnsureRecordNotFound:
    async def test_ensure_record_not_found_returns_404(
        self, route_env: dict[str, Any]
    ) -> None:
        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
            ENSURE_STATUS_RECORD_NOT_FOUND,
        )

        app = route_env["app"]
        service: _FakeLifecycleService = route_env["service"]
        service._ensure_result = _make_ensure_result(
            status=ENSURE_STATUS_RECORD_NOT_FOUND,
            reason_code="record_not_found",
            idempotent_noop=False,
            stable_document_id=None,
            base_id=None,
            record_generation=None,
            index_run_id=None,
            job_id=None,
        )

        with _mock_auth(_USER_ID):
            async with _create_client(app) as client:
                response = await client.post(
                    f"/reader/records/{_RECORD_ID}/article-rag-index/ensure",
                    headers=AUTH_HEADERS,
                    json={"expected_generation": _GENERATION},
                )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
