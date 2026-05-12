"""T-04: Quota/credit ledger tests.

Covers: quota info, anonymous quota, credit ledger, quota check route.
All DB interactions are mocked at the service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.analysis.credit_service import (
    InsufficientCredits,
    DEFAULT_DAILY_FREE_POINTS,
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


class TestInsufficientCredits:
    def test_exception_message(self):
        exc = InsufficientCredits(remaining=5, required=10)
        assert "5" in str(exc)
        assert exc.remaining == 5
        assert exc.required == 10


class TestQuotaRoute:
    @_mock_auth()
    @patch("app.api.routes.quota.get_quota_info", new_callable=AsyncMock)
    @patch("app.api.routes.quota.ensure_credit_account", new_callable=AsyncMock)
    def test_get_user_quota(self, mock_ensure, mock_info, mock_auth):
        mock_info.return_value = {
            "daily_free_points": 1000,
            "daily_used_points": 200,
            "bonus_points": 50,
            "remaining_points": 850,
        }

        response = client.get("/me/quota", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["daily_free_points"] == 1000
        assert data["remaining_points"] == 850


class TestAnonymousQuotaRoute:
    @patch("app.api.routes.quota.get_anonymous_quota_info", new_callable=AsyncMock)
    def test_get_anonymous_quota(self, mock_info):
        mock_info.return_value = {
            "anonymous_id": "test_anon_001",
            "remaining_trials": 3,
            "max_trials_per_day": 3,
            "reset_at": "2026-04-29T00:00:00+00:00",
        }

        response = client.get("/me/quota/anonymous?anonymous_id=test_anon_001")
        assert response.status_code == 200
        data = response.json()
        assert data["remaining_trials"] == 3


class TestQuotaCheckRoute:
    @_mock_auth()
    @patch("app.api.routes.quota.get_quota_info", new_callable=AsyncMock)
    @patch("app.api.routes.quota.ensure_credit_account", new_callable=AsyncMock)
    def test_check_quota_authenticated_allowed(self, mock_ensure, mock_info, mock_auth):
        mock_info.return_value = {
            "daily_free_points": 1000,
            "daily_used_points": 200,
            "bonus_points": 0,
            "remaining_points": 800,
        }

        response = client.post(
            "/me/quota/check",
            json={},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["remaining"] == 800
        assert data["quota_type"] == "authenticated"

    @_mock_auth()
    @patch("app.api.routes.quota.get_quota_info", new_callable=AsyncMock)
    @patch("app.api.routes.quota.ensure_credit_account", new_callable=AsyncMock)
    def test_check_quota_authenticated_denied(self, mock_ensure, mock_info, mock_auth):
        mock_info.return_value = {
            "daily_free_points": 1000,
            "daily_used_points": 1000,
            "bonus_points": 0,
            "remaining_points": 0,
        }

        response = client.post(
            "/me/quota/check",
            json={},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is False
        assert data["remaining"] == 0

    @patch("app.api.routes.quota.check_and_consume_anonymous_trial", new_callable=AsyncMock)
    def test_check_quota_anonymous(self, mock_check):
        mock_check.return_value = (2, "2026-04-29T00:00:00+00:00")

        response = client.post(
            "/me/quota/check",
            json={"anonymous_id": "test_anon_001"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["quota_type"] == "anonymous"

    def test_check_quota_no_auth_no_anonymous_id(self):
        response = client.post(
            "/me/quota/check",
            json={},
        )
        assert response.status_code in (400, 401, 403)


class TestCreditLedgerRoute:
    @_mock_auth()
    @patch("app.api.routes.quota.get_credit_ledger", new_callable=AsyncMock)
    def test_get_credit_ledger_empty(self, mock_ledger, mock_auth):
        mock_ledger.return_value = {
            "items": [],
            "cursor": None,
            "has_more": False,
        }

        response = client.get("/me/credit/ledger", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["has_more"] is False

    @_mock_auth()
    @patch("app.api.routes.quota.get_credit_ledger", new_callable=AsyncMock)
    def test_get_credit_ledger_with_items(self, mock_ledger, mock_auth):
        mock_ledger.return_value = {
            "items": [
                {
                    "id": str(uuid4()),
                    "entry_type": "analysis_deduct",
                    "points": -100,
                    "bucket_type": "daily_free",
                    "balance_after": 900,
                    "description": "分析扣减",
                    "article_title": None,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ],
            "cursor": None,
            "has_more": False,
        }

        response = client.get("/me/credit/ledger", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["entry_type"] == "analysis_deduct"

    @_mock_auth()
    @patch("app.api.routes.quota.get_credit_ledger", new_callable=AsyncMock)
    def test_get_credit_ledger_with_cursor(self, mock_ledger, mock_auth):
        mock_ledger.return_value = {
            "items": [],
            "cursor": None,
            "has_more": False,
        }

        cursor_id = uuid4()
        response = client.get(
            f"/me/credit/ledger?cursor={cursor_id}&limit=10",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200


class TestCreditServicePureLogic:
    def test_insufficient_credits_exception(self):
        exc = InsufficientCredits(0, 1)
        assert exc.remaining == 0
        assert exc.required == 1

    def test_default_daily_free_points(self):
        assert DEFAULT_DAILY_FREE_POINTS == 1000
