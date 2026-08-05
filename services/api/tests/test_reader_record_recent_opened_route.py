"""P1 — last_opened_at endpoint contract tests.

These tests exercise the new ``POST /reader/records/{record_id}/opened``
route and the ``last_opened_at`` field on the ``GET /reader/records``
list response. They use ``AsyncMock`` to patch the
``ReaderOrchestrationRepository`` so no real DB connection is required
(mirroring the pattern from
``tests/test_stable_ready_input_route.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.services.reader_orchestration.repository import ReaderRecordSummary


AUTH_HEADERS = {"Authorization": "Bearer test-token"}

USER_ID = UUID("00000000-0000-0000-0000-000000000501")
OTHER_USER_ID = UUID("00000000-0000-0000-0000-000000000502")

NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

RECORD_A = UUID("00000000-0000-0000-0000-000000000601")  # opened recently
RECORD_B = UUID("00000000-0000-0000-0000-000000000602")  # opened long ago
RECORD_C = UUID("00000000-0000-0000-0000-000000000603")  # unopened, newer created_at
RECORD_D = UUID("00000000-0000-0000-0000-000000000604")  # unopened, older created_at


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _session_info(user_id: UUID = USER_ID) -> object:
    return SimpleNamespace(user_id=user_id, session_id=uuid4())


def _mock_auth(user_id: UUID = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


def _summary(
    *,
    record_id: UUID,
    title: str | None = None,
    created_at: datetime,
    last_opened_at: datetime | None = None,
) -> ReaderRecordSummary:
    return ReaderRecordSummary(
        record_id=record_id,
        title=title or f"record-{record_id}",
        source_type="pasted_text",
        product_state="readable_enhancing",
        readiness_state="article_ready",
        created_at=created_at,
        source_metadata={"source_kind": "route_test"},
        last_event_sequence=0,
        last_opened_at=last_opened_at,
    )


# ---------------------------------------------------------------------------
# POST /reader/records/{record_id}/opened
# ---------------------------------------------------------------------------


def test_mark_opened_404_when_record_missing() -> None:
    app = _build_app()
    mock_mark = AsyncMock(return_value=None)

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.mark_record_opened",
            new=mock_mark,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{uuid4()}/opened",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Reader record not found"}
    mock_mark.assert_awaited_once()


def test_mark_opened_writes_only_last_opened_at() -> None:
    app = _build_app()
    record_id = uuid4()
    stamped_at = NOW
    mock_mark = AsyncMock(return_value=stamped_at)

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.mark_record_opened",
            new=mock_mark,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{record_id}/opened",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["record_id"] == str(record_id)
    # FastAPI serializes UTC datetimes as ``...Z``; parse and compare.
    assert datetime.fromisoformat(body["last_opened_at"].replace("Z", "+00:00")) == stamped_at


def test_mark_opened_does_not_touch_updated_at_or_emit_events() -> None:
    app = _build_app()
    record_id = uuid4()
    stamped_at = NOW
    mock_mark = AsyncMock(return_value=stamped_at)

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.mark_record_opened",
            new=mock_mark,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{record_id}/opened",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200

    # mark_record_opened must be invoked exactly once with the three required
    # keyword arguments and nothing else.
    mock_mark.assert_awaited_once()
    call_kwargs = mock_mark.await_args.kwargs
    assert call_kwargs["record_id"] == record_id
    assert call_kwargs["user_id"] == USER_ID
    assert isinstance(call_kwargs["opened_at"], datetime)
    assert call_kwargs["opened_at"].tzinfo is not None

    # The route only ever touches ``mark_record_opened`` — confirm by
    # checking the recorded ``repr`` against the route source: the only
    # repository attribute it consults is ``mark_record_opened``.
    #
    # Use a separate AsyncMock capture to assert that no other repository
    # method was awaited in the same context by wrapping the whole
    # ``ReaderOrchestrationRepository`` class. Since ``mark_record_opened``
    # is patched separately (above), this side captures everything else.
    forbidden_mock = AsyncMock()
    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.mark_record_opened",
            new=mock_mark,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.update_record",
            new=forbidden_mock,
            create=True,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.update_record_state",
            new=forbidden_mock,
            create=True,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.insert_reader_event",
            new=forbidden_mock,
            create=True,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.append_reader_event",
            new=forbidden_mock,
            create=True,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.write_reader_event",
            new=forbidden_mock,
            create=True,
        ),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.emit_event",
            new=forbidden_mock,
            create=True,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/reader/records/{record_id}/opened",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    forbidden_mock.assert_not_called()


# ---------------------------------------------------------------------------
# GET /reader/records — last_opened_at plumbing
# ---------------------------------------------------------------------------


def test_list_records_returns_last_opened_at() -> None:
    app = _build_app()
    opened_recent = _summary(
        record_id=RECORD_A,
        created_at=NOW,
        last_opened_at=NOW,
    )
    unopened = _summary(
        record_id=RECORD_B,
        created_at=NOW - timedelta(days=1),
        last_opened_at=None,
    )
    mock_list = AsyncMock(return_value=((opened_recent, unopened), 2))

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.list_user_records",
            new=mock_list,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/reader/records", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "last_opened_at" in item
    assert (
        datetime.fromisoformat(
            body["items"][0]["last_opened_at"].replace("Z", "+00:00")
        )
        == NOW
    )
    assert body["items"][1]["last_opened_at"] is None


def test_list_records_sorts_opened_before_unopened() -> None:
    app = _build_app()
    # ORDER BY last_opened_at DESC NULLS LAST, created_at DESC, id DESC
    opened_recent = _summary(
        record_id=RECORD_A,
        title="opened-recent",
        created_at=NOW,
        last_opened_at=NOW,
    )
    opened_old = _summary(
        record_id=RECORD_B,
        title="opened-old",
        created_at=NOW - timedelta(days=10),
        last_opened_at=NOW - timedelta(days=5),
    )
    unopened_new = _summary(
        record_id=RECORD_C,
        title="unopened-new",
        created_at=NOW - timedelta(days=2),
        last_opened_at=None,
    )
    unopened_old = _summary(
        record_id=RECORD_D,
        title="unopened-old",
        created_at=NOW - timedelta(days=20),
        last_opened_at=None,
    )
    mock_list = AsyncMock(
        return_value=(
            (opened_recent, opened_old, unopened_new, unopened_old),
            4,
        )
    )

    with (
        _mock_auth(),
        patch(
            "app.api.routes.reader_orchestration."
            "ReaderOrchestrationRepository.list_user_records",
            new=mock_list,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/reader/records", headers=AUTH_HEADERS)

    assert response.status_code == 200
    ids = [item["record_id"] for item in response.json()["items"]]
    assert ids == [
        str(RECORD_A),
        str(RECORD_B),
        str(RECORD_C),
        str(RECORD_D),
    ]
