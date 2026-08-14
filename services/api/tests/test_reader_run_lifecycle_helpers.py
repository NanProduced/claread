# task-history: ARCH-OPT--WORKER-LOCALITY
"""Unit tests for shared reader_runs lifecycle helpers (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.reader_orchestration.job_runtime import (
    mark_reader_run_running,
    mark_reader_run_status,
)

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


@pytest.mark.anyio
async def test_mark_reader_run_running_sql_shape() -> None:
    conn = AsyncMock()
    run_id = uuid4()
    await mark_reader_run_running(conn, run_id)
    conn.execute.assert_awaited_once()
    sql, arg = conn.execute.await_args.args[0], conn.execute.await_args.args[1]
    assert "UPDATE reader_runs" in sql
    assert "status = 'running'" in sql
    assert "failure_class = NULL" in sql
    assert "started_at = COALESCE(started_at, NOW())" in sql
    assert arg == run_id


@pytest.mark.anyio
async def test_mark_reader_run_status_sql_shape() -> None:
    conn = AsyncMock()
    run_id = uuid4()
    finished = datetime.now(UTC)
    await mark_reader_run_status(
        conn,
        run_id,
        status="completed",
        failure_class=None,
        failure_code=None,
        finished_at=finished,
    )
    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    sql = args[0]
    assert "UPDATE reader_runs" in sql
    assert "status = $2" in sql
    assert "finished_at = $5" in sql
    assert args[1:] == (run_id, "completed", None, None, finished)
