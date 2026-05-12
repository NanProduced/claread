"""T-03: Feedback route/service tests.

Covers: submit feedback, list feedback, delete feedback, 404 on delete.
All DB interactions are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app

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


MOCK_FEEDBACK_ROW = {
    "id": uuid4(),
    "feedback_scope": "analysis_result",
    "target_id": "target_001",
    "sentiment": "negative",
    "feedback_type": "incorrect_annotation",
    "status": "pending",
    "created_at": datetime.now(UTC),
}


class TestSubmitFeedback:
    @_mock_auth()
    @patch("app.api.routes.feedback.feedback_svc.submit_feedback", new_callable=AsyncMock)
    def test_submit_feedback_success(self, mock_submit, mock_auth):
        mock_submit.return_value = {**MOCK_FEEDBACK_ROW, "sentiment": "positive"}

        response = client.post(
            "/feedback",
            json={
                "feedback_scope": "analysis_result",
                "target_id": "target_001",
                "analysis_record_id": str(uuid4()),
                "sentiment": "positive",
                "feedback_type": "thumbs_up",
                "context_json": {},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["feedback_scope"] == "analysis_result"
        assert data["sentiment"] == "positive"

    @_mock_auth()
    @patch("app.api.routes.feedback.feedback_svc.submit_feedback", new_callable=AsyncMock)
    def test_submit_feedback_with_content(self, mock_submit, mock_auth):
        mock_submit.return_value = {**MOCK_FEEDBACK_ROW, "feedback_type": "feature_request"}

        response = client.post(
            "/feedback",
            json={
                "feedback_scope": "app",
                "target_id": "app_general",
                "sentiment": "neutral",
                "feedback_type": "feature_request",
                "content": "Great app!",
                "context_json": {"page": "home"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200

    @_mock_auth()
    @patch("app.api.routes.feedback.feedback_svc.submit_feedback", new_callable=AsyncMock)
    def test_submit_sentence_feedback(self, mock_submit, mock_auth):
        mock_submit.return_value = {
            **MOCK_FEEDBACK_ROW,
            "feedback_scope": "sentence",
            "feedback_type": "selection_issue",
        }

        response = client.post(
            "/feedback",
            json={
                "feedback_scope": "sentence",
                "target_id": "record:abc:sentence:s1",
                "sentiment": "negative",
                "feedback_type": "selection_issue",
                "annotation_type": "sentence_action",
                "context_json": {"sentence_id": "s1"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["feedback_scope"] == "sentence"


class TestListFeedback:
    @_mock_auth()
    @patch("app.services.feedback.service.db_connection.DB_POOL")
    def test_list_feedback_empty(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/feedback", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["has_more"] is False

    @_mock_auth()
    @patch("app.services.feedback.service.db_connection.DB_POOL")
    def test_list_feedback_with_items(self, mock_pool, mock_auth):
        item_row = {
            "id": uuid4(),
            "feedback_scope": "annotation",
            "feedback_type": "incorrect_annotation",
            "sentiment": "negative",
            "content": "Wrong label",
            "status": "pending",
            "reward_points": 0,
            "created_at": datetime.now(UTC).isoformat(),
        }
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [item_row]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/feedback", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1

    @_mock_auth()
    @patch("app.services.feedback.service.db_connection.DB_POOL")
    def test_list_feedback_with_scope_filter(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/feedback?feedback_scope=annotation", headers=AUTH_HEADERS)
        assert response.status_code == 200


class TestDeleteFeedback:
    @_mock_auth()
    @patch("app.services.feedback.service.db_connection.DB_POOL")
    def test_delete_feedback_success(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 1"
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.delete(f"/feedback/{uuid4()}", headers=AUTH_HEADERS)
        assert response.status_code == 204

    @_mock_auth()
    @patch("app.services.feedback.service.db_connection.DB_POOL")
    def test_delete_feedback_not_found(self, mock_pool, mock_auth):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.delete(f"/feedback/{uuid4()}", headers=AUTH_HEADERS)
        assert response.status_code == 404


class TestFeedbackUnauthorized:
    def test_submit_without_auth(self):
        response = client.post(
            "/feedback",
            json={
                "feedback_scope": "app",
                "target_id": "test",
                "sentiment": "positive",
                "feedback_type": "suggestion",
                "context_json": {},
            },
        )
        assert response.status_code in (401, 403)

    def test_list_without_auth(self):
        response = client.get("/feedback")
        assert response.status_code in (401, 403)
