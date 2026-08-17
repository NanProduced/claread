"""Offline route tests for the reader manual recovery endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.router import api_router
from app.schemas.reader_recovery import ReaderRecoveryResponse
from app.services.auth.dependencies import AuthUser, get_current_user
from app.services.reader_orchestration.job_bootstrap import (
    RECOVERY_TRIGGER_MANUAL,
    EnhancementJobBootstrapService,
    EnhancementRecoverySummary,
)

_RECORD_ID = uuid4()
_AUTH_USER_ID = uuid4()
_SENSITIVE_PROBE = "SELECT secret FROM credentials -- probe-7f3a"


def _recovery_summary(*, recovered: bool) -> EnhancementRecoverySummary:
    return EnhancementRecoverySummary(
        record_id=_RECORD_ID,
        base_id=uuid4(),
        expected_generation=2,
        trigger=RECOVERY_TRIGGER_MANUAL,
        previous_product_state="failed",
        next_product_state="readable_enhancing" if recovered else "failed",
        successor_job_ids=(uuid4(), uuid4()) if recovered else (),
        recovered=recovered,
    )


def _build_app(*, authenticated: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router)
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            user_id=str(_AUTH_USER_ID),
            session_id=str(uuid4()),
        )
    return app


@pytest.fixture
def recovery_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=_recovery_summary(recovered=True))
    monkeypatch.setattr(
        EnhancementJobBootstrapService,
        "recover_failed_enhancement_jobs",
        mock,
    )
    return mock


async def _post_recovery(app: FastAPI) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.post(f"/reader/records/{_RECORD_ID}/recovery")


async def test_manual_recovery_route_starts_recovery(
    recovery_mock: AsyncMock,
) -> None:
    response = await _post_recovery(_build_app(authenticated=True))
    assert response.status_code == 200
    payload = ReaderRecoveryResponse.model_validate(response.json())
    assert payload.record_id == str(_RECORD_ID)
    assert payload.outcome == "recovery_started"
    assert payload.previous_product_state == "failed"
    assert payload.next_product_state == "readable_enhancing"
    assert payload.record_generation == 2
    assert payload.successor_job_count == 2
    recovery_mock.assert_awaited_once()
    # Strict kwarg set: identity from auth, path record_id, fixed manual
    # trigger; no client-controlled trace/generation/base inputs.
    assert recovery_mock.await_args.kwargs == {
        "record_id": _RECORD_ID,
        "user_id": _AUTH_USER_ID,
        "trigger": RECOVERY_TRIGGER_MANUAL,
    }


async def test_manual_recovery_route_returns_idempotent_noop(
    recovery_mock: AsyncMock,
) -> None:
    recovery_mock.return_value = _recovery_summary(recovered=False)
    response = await _post_recovery(_build_app(authenticated=True))
    assert response.status_code == 200
    payload = ReaderRecoveryResponse.model_validate(response.json())
    assert payload.outcome == "nothing_to_recover"
    assert payload.successor_job_count == 0


async def test_manual_recovery_route_hides_unowned_or_missing_record(
    recovery_mock: AsyncMock,
) -> None:
    recovery_mock.side_effect = LookupError(
        f"reading record {_RECORD_ID} not found for user {uuid4()}"
    )
    response = await _post_recovery(_build_app(authenticated=True))
    assert response.status_code == 404
    assert response.json() == {"detail": "reader_record_not_found"}
    assert "not found for user" not in response.text


async def test_manual_recovery_route_maps_ineligible_record(
    recovery_mock: AsyncMock,
) -> None:
    recovery_mock.side_effect = ValueError(
        "recovery requires an article-ready record "
        "(readiness_state='submitted')"
    )
    response = await _post_recovery(_build_app(authenticated=True))
    assert response.status_code == 409
    assert response.json() == {"detail": "reader_recovery_not_available"}
    assert "article-ready" not in response.text
    assert "readiness_state" not in response.text


async def test_manual_recovery_route_maps_unexpected_backend_failure(
    recovery_mock: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recovery_mock.side_effect = RuntimeError(_SENSITIVE_PROBE)
    response = await _post_recovery(_build_app(authenticated=True))
    assert response.status_code == 503
    assert response.json() == {
        "detail": "reader_recovery_temporarily_unavailable"
    }
    assert _SENSITIVE_PROBE not in response.text
    assert _SENSITIVE_PROBE not in caplog.text
    assert "reader_manual_recovery_unexpected_failure" in caplog.text
    assert str(_RECORD_ID) in caplog.text


async def test_manual_recovery_route_requires_authentication(
    recovery_mock: AsyncMock,
) -> None:
    response = await _post_recovery(_build_app(authenticated=False))
    assert response.status_code == 401
    recovery_mock.assert_not_awaited()
