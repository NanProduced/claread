"""T-03: Vocabulary review scheduling tests.

Covers:
- Review payload initialization on new word creation
- Due vocabulary query (GET /vocabulary/review/due)
- Known progression (stage 0→1→…→5 = mastered)
- Unfamiliar regression (stage reset to 0)
- Service-level helpers (_ensure_review_payload, _compute_next_review_at)

All DB interactions are mocked.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.user_assets.vocabulary import (
    REVIEW_INTERVALS,
    _compute_next_review_at,
    _ensure_review_payload,
)

client = TestClient(app)

USER_ID = "00000000-0000-0000-0000-000000000001"
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_auth():
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": UUID(USER_ID),
            "session_id": uuid4(),
        })(),
    )


def _make_mock_conn_with_tx():
    """Create a mock connection whose .transaction() works as async ctx mgr."""
    mock_conn = AsyncMock()
    # transaction() must be a regular call returning an async context manager
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=tx_ctx)
    return mock_conn


def _make_mock_pool(mock_conn):
    """Wrap mock_conn in a pool mock with working acquire() async ctx mgr."""
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


# ---------------------------------------------------------------------------
# Unit: helper functions
# ---------------------------------------------------------------------------


class TestComputeNextReviewAt:
    def test_stage_0_returns_1_day_later(self):
        before = datetime.now(UTC)
        result = _compute_next_review_at(0)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt > before + timedelta(hours=23)

    def test_stage_1_returns_3_days_later(self):
        before = datetime.now(UTC)
        dt = datetime.fromisoformat(_compute_next_review_at(1))
        assert dt > before + timedelta(days=3) - timedelta(minutes=1)

    def test_stage_beyond_intervals_returns_none(self):
        assert _compute_next_review_at(len(REVIEW_INTERVALS)) is None

    def test_all_intervals(self):
        for stage, days in enumerate(REVIEW_INTERVALS):
            before = datetime.now(UTC)
            result = _compute_next_review_at(stage)
            assert result is not None
            dt = datetime.fromisoformat(result)
            assert dt > before + timedelta(days=days) - timedelta(minutes=1)


class TestEnsureReviewPayload:
    def test_empty_payload_gets_review(self):
        result = _ensure_review_payload({})
        assert result["review"]["stage"] == 0
        assert result["review"]["next_review_at"] is not None

    def test_existing_review_preserved(self):
        payload = {"review": {"stage": 3, "next_review_at": "2026-05-20T00:00:00+00:00",
                              "last_result": "known", "last_reviewed_at": "2026-05-10T00:00:00+00:00"}}
        assert _ensure_review_payload(payload)["review"]["stage"] == 3

    def test_other_fields_preserved(self):
        payload = {"source_refs": [{"client_record_id": "r1"}], "audio_url": "http://x"}
        result = _ensure_review_payload(payload)
        assert result["audio_url"] == "http://x"
        assert result["review"]["stage"] == 0


# ---------------------------------------------------------------------------
# Route: GET /vocabulary/review/due
# ---------------------------------------------------------------------------


def _make_vocab_row(vocab_id, stage=0, mastery_status="new", review_count=0):
    now = datetime.now(UTC)
    payload = {"review": {"stage": stage, "next_review_at": (now - timedelta(hours=1)).isoformat(),
                          "last_result": None, "last_reviewed_at": None}}
    return {
        "id": vocab_id, "user_id": UUID(USER_ID), "lemma": "test", "display_word": "test",
        "phonetic": None, "part_of_speech": "n.", "short_meaning": "测试",
        "meanings_json": json.dumps([]), "tags": [], "exchange": [],
        "source_provider": "tecd3", "dict_entry_id": None,
        "source_sentence": None, "source_context": None,
        "mastery_status": mastery_status, "review_count": review_count,
        "last_reviewed_at": None, "payload_json": json.dumps(payload),
        "created_at": now, "updated_at": now,
    }


class TestDueVocabularyRoute:
    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_due_returns_items(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [_make_vocab_row(uuid4())]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/vocabulary/review/due", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_due_empty(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/vocabulary/review/due", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Route: POST /vocabulary/{vocab_id}/review
# ---------------------------------------------------------------------------


class TestSubmitReviewRoute:
    def _setup_review_mocks(self, mock_pool_value, select_row, get_row):
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow = AsyncMock(side_effect=[select_row, get_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_conn

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_known_increments_stage(self, mock_pool, mock_auth):
        vid = uuid4()
        row = _make_vocab_row(vid, stage=0)
        updated = {**row, "review_count": 1, "mastery_status": "learning",
                   "payload_json": json.dumps({"review": {"stage": 1,
                   "next_review_at": _compute_next_review_at(1),
                   "last_result": "known", "last_reviewed_at": datetime.now(UTC).isoformat()}})}
        self._setup_review_mocks(mock_pool, row, updated)

        resp = client.post(f"/vocabulary/{vid}/review", json={"result": "known"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == 1
        assert data["mastery_status"] == "learning"
        assert data["review_count"] == 1

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_unfamiliar_resets_stage(self, mock_pool, mock_auth):
        vid = uuid4()
        row = _make_vocab_row(vid, stage=3, mastery_status="learning", review_count=5)
        updated = {**row, "review_count": 6, "mastery_status": "learning",
                   "payload_json": json.dumps({"review": {"stage": 0,
                   "next_review_at": _compute_next_review_at(0),
                   "last_result": "unfamiliar", "last_reviewed_at": datetime.now(UTC).isoformat()}})}
        self._setup_review_mocks(mock_pool, row, updated)

        resp = client.post(f"/vocabulary/{vid}/review", json={"result": "unfamiliar"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == 0
        assert data["mastery_status"] == "learning"

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_known_at_stage4_becomes_mastered(self, mock_pool, mock_auth):
        vid = uuid4()
        row = _make_vocab_row(vid, stage=4, mastery_status="learning", review_count=4)
        updated = {**row, "review_count": 5, "mastery_status": "mastered",
                   "payload_json": json.dumps({"review": {"stage": 5,
                   "next_review_at": None, "last_result": "known",
                   "last_reviewed_at": datetime.now(UTC).isoformat()}})}
        self._setup_review_mocks(mock_pool, row, updated)

        resp = client.post(f"/vocabulary/{vid}/review", json={"result": "known"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == 5
        assert data["next_review_at"] is None
        assert data["mastery_status"] == "mastered"

    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_review_not_found(self, mock_pool, mock_auth):
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.post(f"/vocabulary/{uuid4()}/review", json={"result": "known"}, headers=AUTH_HEADERS)
        assert resp.status_code == 404

    def test_invalid_result_rejected(self):
        from app.schemas.user_assets.vocabulary import ReviewSubmitRequest
        with pytest.raises(Exception):
            ReviewSubmitRequest(result="invalid_value")


# ---------------------------------------------------------------------------
# Integration: POST /vocabulary initializes review payload
# ---------------------------------------------------------------------------


class TestVocabularyCreateInitializesReview:
    @_mock_auth()
    @patch("app.services.user_assets.vocabulary.db_connection.DB_POOL")
    def test_new_word_gets_review_payload(self, mock_pool, mock_auth):
        vid = uuid4()
        now = datetime.now(UTC)
        mock_conn = _make_mock_conn_with_tx()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            None,  # SELECT FOR UPDATE → new word
            {"id": vid, "updated_at": now, "created": True},  # INSERT RETURNING
        ])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.post("/vocabulary", json={
            "lemma": "test", "display_word": "test",
            "short_meaning": "测试", "payload_json": {},
        }, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["created"] is True

        # The INSERT call's payload_json arg ($15) should contain review
        insert_call = mock_conn.fetchrow.call_args_list[1]
        payload = json.loads(insert_call[0][15])
        assert payload["review"]["stage"] == 0
        assert payload["review"]["next_review_at"] is not None
