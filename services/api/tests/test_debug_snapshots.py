from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.analysis.debug_snapshots import upsert_debug_snapshot


class _AcquireCtx:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.anyio
async def test_upsert_debug_snapshot_preserves_created_at_and_updates_updated_at(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = AsyncMock()
    conn = MagicMock()
    conn.execute = execute
    pool = MagicMock()
    pool.acquire.return_value = _AcquireCtx(conn)

    monkeypatch.setattr("app.services.analysis.debug_snapshots.db_connection.DB_POOL", pool)

    await upsert_debug_snapshot(
        {
            "record_id": uuid4(),
            "task_id": uuid4(),
            "workflow_name": "article_analysis",
            "workflow_version": "3.0.0",
            "schema_version": "3.0.0",
            "prompt_version": "test",
            "task_status": "succeeded",
            "user_facing_state": "normal",
            "failure_code": None,
            "failure_message": None,
            "preprocess_summary_json": None,
            "normalize_summary_json": None,
            "drop_log_summary_json": None,
            "runtime_summary_json": {"latency_ms": 1},
            "academic_quality_json": None,
            "few_shot_debug_json": None,
            "rag_debug_json": None,
            "trace_refs_json": {"request_id": "req-1"},
        }
    )

    sql = execute.await_args.args[0]
    assert "updated_at = EXCLUDED.updated_at" in sql
    assert "created_at = EXCLUDED.created_at" not in sql


@pytest.fixture
def anyio_backend():
    return "asyncio"
