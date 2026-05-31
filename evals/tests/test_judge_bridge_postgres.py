from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from claread_eval.judge_bridge.store import AsyncpgJudgeRunRequestStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("EVAL_TEST_DATABASE_URL"),
    reason="Set EVAL_TEST_DATABASE_URL to run judge bridge Postgres integration tests.",
)


@pytest.fixture
async def postgres_store():
    database_url = os.environ["EVAL_TEST_DATABASE_URL"]
    schema = f"eval_judge_bridge_test_{uuid4().hex}"
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(f"CREATE SCHEMA {schema}")
        await conn.execute(_ddl(schema))
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(database_url, server_settings={"search_path": schema})
    store = AsyncpgJudgeRunRequestStore(pool)
    try:
        yield store, pool
    finally:
        await pool.close()
        cleanup_conn = await asyncpg.connect(database_url)
        try:
            await cleanup_conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await cleanup_conn.close()


@pytest.mark.asyncio
async def test_postgres_judge_claim_allows_only_one_worker(postgres_store) -> None:
    store, pool = postgres_store
    request_id = await _insert_request(pool, judge_run_id="pg-judge-claim")

    first, second = await asyncio.gather(
        store.claim_next_request(worker_id="worker-a", lease_seconds=60),
        store.claim_next_request(worker_id="worker-b", lease_seconds=60),
    )

    claimed = [request for request in (first, second) if request is not None]
    assert len(claimed) == 1
    assert claimed[0].id == str(request_id)
    row = await pool.fetchrow(
        "SELECT status, lease_owner FROM eval_judge_run_requests WHERE id = $1",
        request_id,
    )
    assert row["status"] == "running"
    assert row["lease_owner"] in {"worker-a", "worker-b"}


@pytest.mark.asyncio
async def test_postgres_judge_completion_does_not_override_cancelled(postgres_store) -> None:
    store, pool = postgres_store
    request_id = await _insert_request(pool, judge_run_id="pg-judge-cancel")
    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    await pool.execute(
        "UPDATE eval_judge_run_requests SET status = 'cancelled' WHERE id = $1",
        request_id,
    )

    updated = await store.mark_succeeded(
        request_id=claimed.id,
        worker_id="worker-a",
        artifact_path="evals/runs/source-run/judge/should-not-write",
    )

    row = await pool.fetchrow(
        "SELECT status, artifact_path FROM eval_judge_run_requests WHERE id = $1",
        request_id,
    )
    assert updated is False
    assert row["status"] == "cancelled"
    assert row["artifact_path"] is None


@pytest.mark.asyncio
async def test_postgres_judge_stale_recovery_marks_complete_artifact_succeeded(
    postgres_store,
    tmp_path: Path,
) -> None:
    store, pool = postgres_store
    evals_root = tmp_path / "evals"
    artifact_dir = evals_root / "runs" / "source-run" / "judge" / "pg-judge-recovered"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "case-results.json").write_text("{}", encoding="utf-8")
    request_id = await _insert_request(
        pool,
        judge_run_id="pg-judge-recovered",
        status="running",
        lease_owner="stale-worker",
        lease_until=datetime.now(UTC) - timedelta(seconds=5),
    )

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    row = await pool.fetchrow(
        """
        SELECT status, artifact_path
        FROM eval_judge_run_requests
        WHERE id = $1
        """,
        request_id,
    )
    assert len(recovered) == 1
    assert row["status"] == "succeeded"
    assert row["artifact_path"] == "evals/runs/source-run/judge/pg-judge-recovered"


async def _insert_request(
    pool,
    *,
    judge_run_id: str,
    status: str = "queued",
    lease_owner: str | None = None,
    lease_until: datetime | None = None,
) -> UUID:
    request_id = uuid4()
    await pool.execute(
        """
        INSERT INTO eval_judge_run_requests (
            id,
            judge_run_id,
            run_id,
            rubric_id,
            rubric_version,
            status,
            judge_adapter_kind,
            config_json,
            lease_owner,
            lease_until
        )
        VALUES (
            $1, $2, 'source-run', 'language-quality-v1', 'v1',
            $3, 'fake', $4::jsonb, $5, $6
        )
        """,
        request_id,
        judge_run_id,
        status,
        json.dumps({"max_concurrency": 1}),
        lease_owner,
        lease_until,
    )
    return request_id


def _ddl(schema: str) -> str:
    return f"""
    CREATE TABLE {schema}.eval_judge_run_requests (
        id UUID PRIMARY KEY,
        date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
        date_updated TIMESTAMPTZ,
        user_created UUID,
        user_updated UUID,
        judge_run_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        rubric_id TEXT NOT NULL,
        rubric_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        judge_adapter_kind TEXT NOT NULL DEFAULT 'fake',
        config_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        artifact_path TEXT,
        lease_owner TEXT,
        lease_until TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        error_json JSONB,
        notes TEXT,
        tags JSONB NOT NULL DEFAULT '[]'::jsonb,
        UNIQUE (run_id, judge_run_id)
    )
    """
