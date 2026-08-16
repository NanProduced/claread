"""Tests for the explicit operator reindex flow (Wave 7 / F1c phase B).

Covers ``ArticleRagIndexLifecycleService.reindex_article_rag_index_in_transaction``:

  * happy path: old ``indexed`` run -> ``superseded`` (with audit
    metadata) + new ``queued`` run + reader run + job, all in ONE
    caller-owned transaction;
  * the new run persists the CURRENT embedding contract fingerprint;
  * the old run's succeeded job / completed reader run are NOT
    rewritten;
  * in-flight runs (planned / queued / indexing) are NEVER superseded
    -> typed ``reindex_in_progress`` with zero writes;
  * no active run -> typed ``no_indexed_run``;
  * ownership / readiness / active-base typed failures;
  * mid-transaction failure rolls back EVERYTHING (old run stays
    indexed);
  * concurrent reindex calls produce exactly ONE new active candidate.

Uses real PostgreSQL with a temporary schema (same pattern as
test_article_rag_index_bootstrap.py).  No network, no embedding /
vector provider.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.article_rag_contract import (
    ARTICLE_RAG_EMBEDDING_CONTRACT,
    compute_embedding_contract_fingerprint,
)
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapService,
)
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ArticleRagIndexLifecycleService,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

from app.contracts.annotation import utf16_code_unit_length  # noqa: E402
from tests.test_article_rag_index_plan import (  # noqa: E402
    _RECORD_ID,
    _USER_ID,
    _main_reading_policy,
    _seed_block,
    _seed_full_environment,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def reindex_env() -> asyncpg.Pool:
    schema_name = f"test_w7_reindex_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


_PARAGRAPH_TEXT = "Indexable paragraph for the reindex flow."


async def _seed_ready_environment(pool: asyncpg.Pool) -> None:
    await _seed_full_environment(pool, base_text=_PARAGRAPH_TEXT)
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=_PARAGRAPH_TEXT,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(_PARAGRAPH_TEXT),
        interpretation_policy=_main_reading_policy(),
    )


async def _bootstrap_indexed_run(
    pool: asyncpg.Pool,
) -> UUID:
    """Bootstrap + mark the index run ``indexed`` (simulating a
    completed build) and return its id."""
    bootstrap = ArticleRagIndexBootstrapService(pool=pool)
    result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with pool.acquire() as conn:
        reader_run_id = await conn.fetchval(
            "SELECT reader_run_id FROM reader_article_rag_index_runs "
            "WHERE id = $1",
            result.index_run_id,
        )
        await conn.execute(
            """
            UPDATE reader_article_rag_index_runs
            SET status = 'indexed',
                embedding_model = $2,
                vector_store_provider = $3,
                vector_collection = $4,
                completed_at = NOW()
            WHERE id = $1
            """,
            result.index_run_id,
            ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_model,
            "zilliz",
            ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection,
        )
        # Mark the job succeeded + run completed like the worker would.
        await conn.execute(
            "UPDATE reader_jobs SET status = 'succeeded' WHERE id = $1",
            result.job_id,
        )
        await conn.execute(
            "UPDATE reader_runs SET status = 'completed' WHERE id = $1",
            reader_run_id,
        )
    return result.index_run_id


def _make_service(pool: asyncpg.Pool) -> ArticleRagIndexLifecycleService:
    return ArticleRagIndexLifecycleService()


# ===========================================================================
# Happy path: indexed -> superseded + new queued run in ONE transaction
# ===========================================================================


async def test_reindex_supersedes_indexed_and_enqueues_new_run(
    reindex_env: asyncpg.Pool,
) -> None:
    await _seed_ready_environment(reindex_env)
    old_run_id = await _bootstrap_indexed_run(reindex_env)

    service = _make_service(reindex_env)
    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            result = await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    assert result.status == "reindex_enqueued"
    assert result.superseded_index_run_id == old_run_id
    assert result.new_index_run_id is not None
    assert result.new_index_run_id != old_run_id

    async with reindex_env.acquire() as conn:
        # Old run: superseded with audit metadata.
        old_row = await conn.fetchrow(
            """
            SELECT status, metadata_json, completed_at
            FROM reader_article_rag_index_runs WHERE id = $1
            """,
            old_run_id,
        )
        assert old_row["status"] == "superseded"
        metadata = old_row["metadata_json"]
        assert metadata["supersede_reason"] == "operator_reindex"
        assert metadata["trigger_kind"] == "operator_reindex"
        assert metadata["superseded_at"] is not None
        assert isinstance(metadata["previous_plan_content_sha256"], str)
        # The superseded row keeps its contract fingerprint identity.
        assert metadata["embedding_contract_fingerprint"] == (
            compute_embedding_contract_fingerprint(
                ARTICLE_RAG_EMBEDDING_CONTRACT
            )
        )

        # New run: queued with the CURRENT contract fingerprint.
        new_row = await conn.fetchrow(
            """
            SELECT status, metadata_json, job_id, reader_run_id
            FROM reader_article_rag_index_runs WHERE id = $1
            """,
            result.new_index_run_id,
        )
        assert new_row["status"] == "queued"
        assert new_row["metadata_json"]["embedding_contract_fingerprint"] == (
            compute_embedding_contract_fingerprint(
                ARTICLE_RAG_EMBEDDING_CONTRACT
            )
        )
        # New run has proper job/run linkage.
        assert new_row["job_id"] is not None
        assert new_row["reader_run_id"] is not None


async def test_reindex_preserves_old_job_and_reader_run(
    reindex_env: asyncpg.Pool,
) -> None:
    """B2: the old indexed run's succeeded job and completed reader run
    must NOT be rewritten to superseded."""
    await _seed_ready_environment(reindex_env)
    bootstrap = ArticleRagIndexBootstrapService(pool=reindex_env)
    boot = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with reindex_env.acquire() as conn:
        boot_reader_run_id = await conn.fetchval(
            "SELECT reader_run_id FROM reader_article_rag_index_runs "
            "WHERE id = $1",
            boot.index_run_id,
        )
        await conn.execute(
            "UPDATE reader_article_rag_index_runs SET status='indexed' "
            "WHERE id = $1",
            boot.index_run_id,
        )
        await conn.execute(
            "UPDATE reader_jobs SET status='succeeded' WHERE id = $1",
            boot.job_id,
        )
        await conn.execute(
            "UPDATE reader_runs SET status='completed' WHERE id = $1",
            boot_reader_run_id,
        )

    service = _make_service(reindex_env)
    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    async with reindex_env.acquire() as conn:
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", boot.job_id
        )
        run_status = await conn.fetchval(
            "SELECT status FROM reader_runs WHERE id = $1",
            boot_reader_run_id,
        )
    assert job_status == "succeeded"
    assert run_status == "completed"


# ===========================================================================
# B1: in-flight runs are never superseded
# ===========================================================================


@pytest.mark.parametrize("in_flight_status", ["planned", "queued", "indexing"])
async def test_reindex_in_flight_returns_in_progress_zero_writes(
    reindex_env: asyncpg.Pool, in_flight_status: str
) -> None:
    await _seed_ready_environment(reindex_env)
    bootstrap = ArticleRagIndexBootstrapService(pool=reindex_env)
    boot = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with reindex_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs SET status = $2 "
            "WHERE id = $1",
            boot.index_run_id,
            in_flight_status,
        )

    service = _make_service(reindex_env)
    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            result = await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    assert result.status == "reindex_in_progress"
    async with reindex_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM reader_article_rag_index_runs "
            "WHERE stable_document_id = $1",
            boot.stable_document_id,
        )
        assert row["status"] == in_flight_status
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs"
        )
        assert count == 1


# ===========================================================================
# Typed failures
# ===========================================================================


async def test_reindex_no_indexed_run(reindex_env: asyncpg.Pool) -> None:
    """No active index run at all -> typed no_indexed_run, zero writes."""
    await _seed_ready_environment(reindex_env)
    service = _make_service(reindex_env)

    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            result = await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    assert result.status == "no_indexed_run"


async def test_reindex_record_not_found(reindex_env: asyncpg.Pool) -> None:
    await _seed_ready_environment(reindex_env)
    service = _make_service(reindex_env)

    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            result = await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=uuid4(),
                user_id=_USER_ID,
            )

    assert result.status == "record_not_found"


async def test_reindex_not_ready_record(reindex_env: asyncpg.Pool) -> None:
    await _seed_full_environment(reindex_env, base_text=_PARAGRAPH_TEXT)
    async with reindex_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET readiness_state = 'submitted' "
            "WHERE id = $1",
            _RECORD_ID,
        )
    service = _make_service(reindex_env)

    async with reindex_env.acquire() as conn:
        async with conn.transaction():
            result = await service.reindex_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )

    assert result.status == "not_ready"


# ===========================================================================
# Atomicity: mid-transaction failure rolls everything back
# ===========================================================================


async def test_reindex_mid_transaction_failure_rolls_back(
    reindex_env: asyncpg.Pool,
) -> None:
    """B2: if the bootstrap step raises inside the reindex transaction,
    the supersede MUST roll back — the old run stays ``indexed``."""
    await _seed_ready_environment(reindex_env)
    old_run_id = await _bootstrap_indexed_run(reindex_env)

    service = _make_service(reindex_env)

    # Sabotage the bootstrap step: after the supersede UPDATE has run
    # inside the transaction, the bootstrap must raise so the whole
    # caller-owned transaction rolls back.
    from app.services.reader_orchestration.article_rag_index_plan import (
        ArticleRagIndexPlanError,
    )

    async def _failing_bootstrap(conn, **kwargs):
        raise ArticleRagIndexPlanError("plan exploded mid-reindex")

    bootstrap_instance = service._bootstrap_service
    original = type(bootstrap_instance).bootstrap_article_rag_index_in_transaction
    bootstrap_instance.bootstrap_article_rag_index_in_transaction = (  # type: ignore[method-assign]
        _failing_bootstrap
    )
    try:
        async with reindex_env.acquire() as conn:
            with pytest.raises(ArticleRagIndexPlanError):
                async with conn.transaction():
                    await service.reindex_article_rag_index_in_transaction(
                        conn,
                        reading_record_id=_RECORD_ID,
                        user_id=_USER_ID,
                    )
    finally:
        del bootstrap_instance.bootstrap_article_rag_index_in_transaction  # type: ignore[attr-defined]
        assert type(bootstrap_instance).bootstrap_article_rag_index_in_transaction is original

    async with reindex_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM reader_article_rag_index_runs WHERE id = $1",
            old_run_id,
        )
        assert row["status"] == "indexed"
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs"
        )
        assert count == 1


# ===========================================================================
# B3: concurrency — exactly one new active candidate
# ===========================================================================


async def test_concurrent_reindex_produces_single_candidate(
    reindex_env: asyncpg.Pool,
) -> None:
    """Two concurrent reindex calls on the same record: exactly one
    returns ``reindex_enqueued``; the other sees the new queued run and
    returns ``reindex_in_progress``.  Exactly ONE new active run exists."""
    await _seed_ready_environment(reindex_env)
    await _bootstrap_indexed_run(reindex_env)

    service = _make_service(reindex_env)

    async def _one_call() -> str:
        async with reindex_env.acquire() as conn:
            async with conn.transaction():
                result = (
                    await service.reindex_article_rag_index_in_transaction(
                        conn,
                        reading_record_id=_RECORD_ID,
                        user_id=_USER_ID,
                    )
                )
                return result.status

    statuses = await asyncio.gather(_one_call(), _one_call())

    assert sorted(statuses) == [
        "reindex_enqueued",
        "reindex_in_progress",
    ]
    async with reindex_env.acquire() as conn:
        runs = await conn.fetch(
            """
            SELECT status FROM reader_article_rag_index_runs
            WHERE status IN ('planned', 'queued', 'indexing', 'indexed')
            """
        )
        assert len(runs) == 1
        assert runs[0]["status"] == "queued"
