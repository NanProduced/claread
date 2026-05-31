from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from claread_eval.runner_bridge.store import AsyncpgWorkflowRunRequestStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("EVAL_TEST_DATABASE_URL"),
    reason="Set EVAL_TEST_DATABASE_URL to run runner bridge Postgres integration tests.",
)


@pytest.fixture
async def postgres_store():
    database_url = os.environ["EVAL_TEST_DATABASE_URL"]
    schema = f"eval_bridge_test_{uuid4().hex}"
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(f"CREATE SCHEMA {schema}")
        await conn.execute(_ddl(schema))
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(database_url, server_settings={"search_path": schema})
    store = AsyncpgWorkflowRunRequestStore(pool)
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
async def test_postgres_claim_allows_only_one_worker(postgres_store) -> None:
    store, pool = postgres_store
    request_id = await _insert_request(pool, run_id="pg-claim")

    first, second = await asyncio.gather(
        store.claim_next_request(worker_id="worker-a", lease_seconds=60),
        store.claim_next_request(worker_id="worker-b", lease_seconds=60),
    )

    claimed = [request for request in (first, second) if request is not None]
    assert len(claimed) == 1
    assert claimed[0].id == str(request_id)
    row = await pool.fetchrow(
        "SELECT status, lease_owner FROM eval_workflow_run_requests WHERE id = $1",
        request_id,
    )
    assert row["status"] == "running"
    assert row["lease_owner"] in {"worker-a", "worker-b"}


@pytest.mark.asyncio
async def test_postgres_completion_does_not_override_cancelled(postgres_store) -> None:
    store, pool = postgres_store
    request_id = await _insert_request(pool, run_id="pg-cancel-guard")
    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    await pool.execute(
        "UPDATE eval_workflow_run_requests SET status = 'cancelled' WHERE id = $1",
        request_id,
    )

    updated = await store.mark_succeeded(
        request_id=claimed.id,
        worker_id="worker-a",
        artifact_run_id="pg-cancel-guard",
        artifact_path="evals/runs/should-not-write",
    )

    row = await pool.fetchrow(
        "SELECT status, artifact_path FROM eval_workflow_run_requests WHERE id = $1",
        request_id,
    )
    assert updated is False
    assert row["status"] == "cancelled"
    assert row["artifact_path"] == "evals/runs/pg-cancel-guard"


@pytest.mark.asyncio
async def test_postgres_stale_recovery_marks_complete_artifact_succeeded(
    postgres_store,
    tmp_path: Path,
) -> None:
    store, pool = postgres_store
    evals_root = tmp_path / "evals"
    run_dir = evals_root / "runs" / "pg-recovered"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    (run_dir / "case-index.json").write_text("{}", encoding="utf-8")
    request_id = await _insert_request(
        pool,
        run_id="pg-recovered",
        status="running",
        lease_owner="stale-worker",
        lease_until=datetime.now(UTC) - timedelta(seconds=5),
    )

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    row = await pool.fetchrow(
        """
        SELECT status, artifact_run_id, artifact_path
        FROM eval_workflow_run_requests
        WHERE id = $1
        """,
        request_id,
    )
    assert len(recovered) == 1
    assert row["status"] == "succeeded"
    assert row["artifact_run_id"] == "pg-recovered"
    assert row["artifact_path"] == "evals/runs/pg-recovered"


@pytest.mark.asyncio
async def test_postgres_claim_preserves_retry_lineage_fields(postgres_store) -> None:
    store, pool = postgres_store
    source_request_id = uuid4()
    request_id = await _insert_request(
        pool,
        run_id="pg-retry",
        source_request_id=source_request_id,
        attempt_no=2,
        max_attempts=2,
        retry_reason="manual retry",
    )

    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)

    row = await pool.fetchrow(
        """
        SELECT source_request_id, attempt_no, max_attempts, retry_reason
        FROM eval_workflow_run_requests
        WHERE id = $1
        """,
        request_id,
    )
    assert claimed is not None
    assert claimed.id == str(request_id)
    assert row["source_request_id"] == source_request_id
    assert row["attempt_no"] == 2
    assert row["max_attempts"] == 2
    assert row["retry_reason"] == "manual retry"


async def _insert_request(
    pool,
    *,
    run_id: str,
    status: str = "queued",
    lease_owner: str | None = None,
    lease_until: datetime | None = None,
    source_request_id: UUID | None = None,
    attempt_no: int = 1,
    max_attempts: int = 1,
    retry_reason: str | None = None,
) -> UUID:
    request_id = uuid4()
    await pool.execute(
        """
        INSERT INTO eval_workflow_run_requests (
            id,
            run_id,
            status,
            dataset_id,
            mode,
            eval_purpose,
            adapter_kind,
            runner_kind,
            config_json,
            artifact_run_id,
            artifact_path,
            source_request_id,
            attempt_no,
            max_attempts,
            retry_reason,
            max_concurrency,
            lease_owner,
            lease_until
        )
        VALUES (
            $1, $2, $3, 'article-analysis-v1', 'workflow',
            'dataset_regression', 'fake', 'external_worker', $4::jsonb,
            $2, 'evals/runs/' || $2, $5, $6, $7, $8, 1, $9, $10
        )
        """,
        request_id,
        run_id,
        status,
        json.dumps({"adapter_kind": "fake", "rag_mode": "off", "trace_scope": "off"}),
        source_request_id,
        attempt_no,
        max_attempts,
        retry_reason,
        lease_owner,
        lease_until,
    )
    return request_id


def _ddl(schema: str) -> str:
    return f"""
    CREATE TABLE {schema}.eval_workflow_run_requests (
        id UUID PRIMARY KEY,
        date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
        date_updated TIMESTAMPTZ,
        user_created UUID,
        user_updated UUID,
        run_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'queued',
        dataset_id TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'workflow',
        eval_purpose TEXT NOT NULL DEFAULT 'dataset_regression',
        adapter_kind TEXT NOT NULL DEFAULT 'fake',
        runner_kind TEXT NOT NULL DEFAULT 'external_worker',
        config_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        prompt_variant_id TEXT,
        prompt_variant_snapshot_hash TEXT,
        artifact_run_id TEXT,
        artifact_path TEXT,
        source_request_id UUID,
        attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
        max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= attempt_no),
        retry_reason TEXT,
        max_concurrency INTEGER NOT NULL DEFAULT 1,
        lease_owner TEXT,
        lease_until TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        error_json JSONB,
        notes TEXT,
        tags JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """
