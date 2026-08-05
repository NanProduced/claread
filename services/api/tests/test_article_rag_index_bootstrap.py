# task-history: D6-I4B (renamed from test_d6_i4b_article_rag_index_bootstrap.py)
"""Tests for Article RAG index job bootstrap + index state foundation.

Covers the 12+ test requirements from the task spec:
 1. happy path creates index state + reader_run + reader_job
 2. transaction guard: not in transaction → fail closed, no writes
 3. ownership fail: wrong user → LookupError, no writes
 4. no active base / no active stable document / stale generation → fail closed
 5. I4A plan returns no chunks → fail closed
 6. same plan idempotent no-op
 7. same stable_document + different plan hash → fail closed
 8. job payload does not include chunk text / Plate JSON / Markdown syntax /
    DOM / Slate / UI fields
 9. job base_id is non-NULL, target scope aligned with migration CHECK
10. active fingerprint / index unique constraint prevent duplicate queued job
11. DB constraint: article_rag_index_build with null base_id is rejected
12. DB constraint: duplicate active index run is rejected
13. migration included in local compose initdb mount (covered by
    test_local_compose_migration_coverage.py)
14. baseline schema test does not regress (covered by
    test_reader_orchestration_schema_baseline.py)
15. focused pytest pass
16. git diff --check pass

Uses real PostgreSQL with a temporary schema (BASELINE_SQL from
infra/migrations/0001_initial.sql).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapError,
    ArticleRagIndexBootstrapResult,
    ArticleRagIndexBootstrapService,
)
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlanError,
    compute_plan_content_sha256,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

# Reuse seed helpers + UUIDs from the I4A test module.
from tests.test_article_rag_index_plan import (  # noqa: E402
    _BASE_ID,
    _OTHER_USER_ID,
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
    _build_base_text_and_offsets,
    _main_reading_policy,
    _metadata_only_policy,
    _seed_base,
    _seed_block,
    _seed_full_environment,
    _seed_record,
    _seed_user,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# Single-path convergence: BASELINE_SQL already contains the full
# Article RAG schema.
INDEX_BOOTSTRAP_SCHEMA_SQL = BASELINE_SQL


# ---------------------------------------------------------------------------
# Pool / schema fixtures
# ---------------------------------------------------------------------------


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
async def index_env() -> asyncpg.Pool:
    schema_name = f"test_i4b_rag_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(INDEX_BOOTSTRAP_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Environment seed helpers (paragraph-only happy path)
# ---------------------------------------------------------------------------


async def _seed_paragraph_environment(
    pool: asyncpg.Pool,
    *,
    paragraph_text: str = "Indexable paragraph for happy path.",
    record_generation: int = 1,
) -> str:
    """Seed user + record + base + stable document + one main_reading paragraph.

    Returns the base content_sha256.
    """
    await _seed_full_environment(
        pool,
        base_text=paragraph_text,
        record_generation=record_generation,
    )
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=paragraph_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(paragraph_text),
        interpretation_policy=_main_reading_policy(),
    )
    return hashlib.sha256(paragraph_text.encode("utf-8")).hexdigest()


def _build_service(pool: asyncpg.Pool) -> ArticleRagIndexBootstrapService:
    return ArticleRagIndexBootstrapService(pool=pool)


# ---------------------------------------------------------------------------
# Row fetch helpers (used across multiple tests)
# ---------------------------------------------------------------------------


async def _fetch_index_run(
    conn: asyncpg.Connection,
    *,
    index_run_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, stable_document_id, base_id,
               record_generation,
               stable_document_content_sha256, canonical_text_sha256,
               plan_content_sha256, chunk_count, status,
               embedding_model, vector_store_provider, vector_collection,
               job_id, reader_run_id,
               error_json, metadata_json
        FROM reader_article_rag_index_runs
        WHERE id = $1
        """,
        index_run_id,
    )


async def _fetch_job(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, base_id, run_id, user_id,
               job_type, target_type, target_key, status, priority,
               expected_generation, operation_fingerprint, idempotency_key,
               input_hash, input_json, max_attempts
        FROM reader_jobs
        WHERE id = $1
        """,
        job_id,
    )


async def _count_index_runs(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM reader_article_rag_index_runs")


async def _count_reader_runs(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM reader_runs")


async def _count_reader_jobs(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM reader_jobs")


# ===================================================================
# Test 1: happy path creates index state + reader_run + reader_job
# ===================================================================


async def test_happy_path_creates_index_state_and_job(index_env: asyncpg.Pool) -> None:
    """Requirement 1: bootstrap creates one index state row, one reader_run,
    one reader_job, all linked by id / job_id / reader_run_id."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Result is a frozen dataclass with the expected fields.
    assert isinstance(result, ArticleRagIndexBootstrapResult)
    assert result.idempotent_noop is False
    assert result.reading_record_id == _RECORD_ID
    assert result.stable_document_id == _STABLE_DOC_ID
    assert result.base_id == _BASE_ID
    assert result.record_generation == 1
    assert result.chunk_count == 1
    assert len(result.plan_content_sha256) == 64
    assert result.job_id is not None
    assert result.job_status == "queued"

    async with index_env.acquire() as conn:
        # Exactly one index state row.
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1

        index_run = await _fetch_index_run(conn, index_run_id=result.index_run_id)
        assert index_run is not None
        assert index_run["status"] == "queued"
        assert index_run["base_id"] == _BASE_ID
        assert index_run["record_generation"] == 1
        assert index_run["chunk_count"] == 1
        assert index_run["embedding_model"] is None
        assert index_run["vector_store_provider"] is None
        assert index_run["vector_collection"] is None
        assert index_run["job_id"] == result.job_id
        assert index_run["reader_run_id"] is not None

        job = await _fetch_job(conn, job_id=result.job_id)
        assert job is not None
        assert job["job_type"] == "article_rag_index_build"
        assert job["status"] == "queued"
        assert job["base_id"] == _BASE_ID
        assert job["target_type"] == "record"
        assert job["target_key"] == str(_STABLE_DOC_ID)
        assert job["expected_generation"] == 1
        assert job["operation_fingerprint"] == "article_rag_index_build_v1"
        assert job["idempotency_key"] == (
            f"article_rag_index_build_v1:{_STABLE_DOC_ID}"
        )

        # The reader_run is base-scoped via run_type and carries the policy version.
        run = await conn.fetchrow(
            "SELECT run_type, status, record_generation, policy_version, "
            "trigger_kind FROM reader_runs WHERE id = $1",
            index_run["reader_run_id"],
        )
        assert run is not None
        assert run["run_type"] == "article_rag_index_build"
        assert run["status"] == "queued"
        assert run["record_generation"] == 1
        assert run["policy_version"] == "article_rag_index_bootstrap_v1"
        assert run["trigger_kind"] == "system"


# ===================================================================
# Test 2: transaction guard — not in transaction → fail closed
# ===================================================================


async def test_transaction_guard_fails_closed_with_no_writes(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 2: calling the in_transaction variant without an active
    transaction raises ArticleRagIndexBootstrapError with
    reason_code='caller_transaction_required' and writes nothing."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    async with index_env.acquire() as conn:
        # No transaction opened on conn.
        with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
            await service.bootstrap_article_rag_index_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )
        assert exc_info.value.reason_code == "caller_transaction_required"

        # No writes should have occurred.
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# Test 3: ownership fail — wrong user → LookupError, no writes
# ===================================================================


async def test_ownership_fail_wrong_user_no_writes(index_env: asyncpg.Pool) -> None:
    """Requirement 3: a record that does not belong to the requesting user
    raises LookupError and writes nothing."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(LookupError):
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_OTHER_USER_ID,
                )

    # The transaction rolled back; no rows should be visible.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# Test 4a: no active base → fail closed
# ===================================================================


async def test_no_active_base_fails_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 4: a record with active_base_id=NULL fails closed via
    the plan service (ArticleRagIndexPlanError)."""
    await _seed_user(index_env)
    # Insert record WITHOUT active_base_id (no base created, no link).
    await _seed_record(index_env, active_base_id=None, generation=1)
    service = _build_service(index_env)

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanError):
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# Test 4b: no active stable document → fail closed
# ===================================================================


async def test_no_active_stable_document_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 4: a record with an active base but no active stable
    document fails closed via the plan service."""
    await _seed_user(index_env)
    await _seed_record(index_env, active_base_id=None, generation=1)
    await _seed_base(index_env)
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            _BASE_ID,
        )
    # No stable_document seeded.
    service = _build_service(index_env)

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanError):
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# Test 4c: stale generation → fail closed
# ===================================================================


async def test_stale_generation_fails_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 4: a stable document with a stale record_generation
    fails closed via the plan service."""
    # Seed the record + base + stable document at generation 1.
    await _seed_paragraph_environment(index_env, record_generation=1)

    # Bump the stable document's record_generation to 2 directly.
    # stable_reading_documents has no FK on record_generation (only on
    # reading_record_id → reading_records.id), so this is safe. The
    # record + base remain at generation 1, so the plan service detects
    # the mismatch and fails closed.
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE stable_reading_documents SET record_generation = 2 "
            "WHERE id = $1",
            _STABLE_DOC_ID,
        )

    service = _build_service(index_env)
    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanError):
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0


# ===================================================================
# Test 5: I4A plan returns no chunks → fail closed
# ===================================================================


async def test_plan_returns_no_chunks_fails_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 5: when the plan service returns no eligible chunks
    (e.g. only metadata_only / non-eligible blocks), the bootstrap fails
    closed via ArticleRagIndexPlanError and writes nothing."""
    # Seed an environment with a non-empty base text (the
    # reading_bases_content_utf16_length_check rejects empty text) and a
    # single metadata_only table block (no main_reading blocks → plan
    # has no eligible chunks).
    await _seed_full_environment(index_env, base_text="Placeholder base text.")
    await _seed_block(
        index_env,
        block_id="table-1",
        order_index=0,
        block_type="table",
        text_content=None,
        interpretation_policy=_metadata_only_policy("table_cell"),
    )

    service = _build_service(index_env)
    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexPlanError):
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# Test 6: same plan idempotent no-op
# ===================================================================


async def test_same_plan_idempotent_noop(index_env: asyncpg.Pool) -> None:
    """Requirement 6: a second bootstrap with the same plan_content_sha256
    and chunk_count returns idempotent_noop=True and writes no additional
    rows."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    second = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert second.idempotent_noop is True
    assert second.index_run_id == first.index_run_id
    assert second.job_id == first.job_id
    assert second.job_status == first.job_status
    assert second.plan_content_sha256 == first.plan_content_sha256
    assert second.chunk_count == first.chunk_count

    # Still exactly one of each row.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 6a: same hash + null job_id → fail closed
# ===================================================================


async def test_idempotent_null_job_id_fails_closed(index_env: asyncpg.Pool) -> None:
    """P1 fix: an existing active index run with a null job_id means the
    index is silently stuck. The bootstrap service must fail closed with
    reason_code='idempotent_run_inconsistent' rather than returning a
    no-op that misleads the caller into thinking the index is queued."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # Corrupt the index run: null out job_id and reader_run_id.
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs "
            "SET job_id = NULL, reader_run_id = NULL WHERE id = $1",
            first.index_run_id,
        )

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
            assert exc_info.value.reason_code == "idempotent_run_inconsistent"

    # No new writes (the failed bootstrap rolled back).
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 6b: same hash + missing job row → fail closed
# ===================================================================


async def test_idempotent_missing_job_row_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1 fix: an existing active index run whose job_id points to a
    non-existent job row means the index is silently stuck. Fail closed."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # Corrupt the index run: point job_id to a random non-existent UUID.
    bogus_job_id = uuid4()
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_article_rag_index_runs "
            "SET job_id = $2 WHERE id = $1",
            first.index_run_id,
            bogus_job_id,
        )

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
            assert exc_info.value.reason_code == "idempotent_run_inconsistent"

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 6c: same hash + mismatched job row → fail closed
# ===================================================================


async def test_idempotent_mismatched_job_row_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1 fix: an existing active index run whose job row has mismatched
    fields (e.g. target_key changed) means the index is silently stuck.
    Fail closed."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # Corrupt the job row: change target_key to a different value.
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET target_key = 'mismatched' WHERE id = $1",
            first.job_id,
        )

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
            assert exc_info.value.reason_code == "idempotent_run_inconsistent"

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 6d: same hash + dead job status → fail closed
# ===================================================================


async def test_idempotent_dead_job_status_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1 fix: an existing active index run whose job has a dead status
    (failed_terminal / cancelled / superseded) means the index is
    silently stuck. Fail closed."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # Mark the job as cancelled (dead status). The index run is still
    # 'queued' (active), so the idempotency check will find it.
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET status = 'cancelled' WHERE id = $1",
            first.job_id,
        )

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
            assert exc_info.value.reason_code == "idempotent_run_inconsistent"

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 7: same stable_document + different plan hash → fail closed
# ===================================================================


async def test_different_plan_hash_fails_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 7: when an existing active index run for the same
    stable_document_id has a different
    plan_content_sha256, bootstrap fails closed with
    reason_code='plan_hash_mismatch'."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # Mutate the plan content by adding a second main_reading paragraph.
    # This changes the chunk count and the plan_content_sha256.
    base_text = "Indexable paragraph for happy path.\n\nSecond paragraph added."
    _, offsets = _build_base_text_and_offsets(
        "Indexable paragraph for happy path.",
        "Second paragraph added.",
    )
    # Update the base text + content hash + utf16 length.
    new_base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    async with index_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_bases
            SET text = $2,
                content_sha256 = $3,
                content_utf16_length = $4
            WHERE id = $1
            """,
            _BASE_ID,
            base_text,
            new_base_sha,
            utf16_code_unit_length(base_text),
        )

    # Add a second paragraph block at the new offsets.
    second_start, second_end = offsets[1]
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content="Second paragraph added.",
        canonical_text_start_utf16=second_start,
        canonical_text_end_utf16=second_end,
        interpretation_policy=_main_reading_policy(),
    )

    # A second bootstrap should fail closed
    # because the plan now has 2 chunks (vs. 1 in the existing run) and a
    # different plan_content_sha256.
    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
                await service.bootstrap_article_rag_index_in_transaction(
                    conn,
                    reading_record_id=_RECORD_ID,
                    user_id=_USER_ID,
                )
            assert exc_info.value.reason_code == "plan_hash_mismatch"

    # No new rows should be visible after rollback.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 8: job payload does not include chunk text / Plate / Markdown / DOM
# ===================================================================


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "chunks",
        "chunk_text",
        "chunk_texts",
        "plate",
        "plate_json",
        "markdown",
        "markdown_syntax",
        "dom",
        "dom_selection",
        "slate",
        "slate_path",
        "ui",
        "ui_display_group",
        "render_profile",
        "render_snapshot",
        "citation_refs",
    }
)


async def test_job_payload_excludes_truth_projections(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 8: input_json on both reader_runs and reader_jobs only
    contains IDs and run params. No chunk text, Plate JSON, Markdown
    syntax, DOM/Slate/UI fields."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    async with index_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=result.job_id)
        run_input = await conn.fetchval(
            "SELECT envelope_json FROM reader_runs WHERE id = $1",
            job["run_id"],
        )

        job_input = dict(job["input_json"])
        run_input = dict(run_input)

    expected_keys = {
        "source",
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "index_run_id",
    }
    assert set(job_input.keys()) == expected_keys
    assert set(run_input.keys()) == expected_keys

    assert job_input["source"] == "article_rag_index_bootstrap"
    assert job_input["reading_record_id"] == str(_RECORD_ID)
    assert job_input["stable_document_id"] == str(_STABLE_DOC_ID)
    assert job_input["base_id"] == str(_BASE_ID)
    assert job_input["record_generation"] == 1
    assert job_input["index_run_id"] == str(result.index_run_id)

    # Sanity: none of the forbidden projection keys appear anywhere.
    for forbidden in _FORBIDDEN_PAYLOAD_KEYS:
        assert forbidden not in job_input
        assert forbidden not in run_input


# ===================================================================
# Test 9: job base_id non-NULL, target scope aligned with migration CHECK
# ===================================================================


async def test_job_base_id_non_null_and_target_scope_aligned(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 9: reader_jobs.base_id IS NOT NULL for an
    article_rag_index_build job, target_type is one of the CHECK values,
    and target_key is the stable_document_id."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    async with index_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=result.job_id)
        assert job["base_id"] == _BASE_ID
        assert job["base_id"] is not None
        assert job["target_type"] == "record"
        assert job["target_key"] == str(_STABLE_DOC_ID)
        assert job["job_type"] == "article_rag_index_build"


# ===================================================================
# Test 10: active fingerprint unique constraint prevents duplicate job
# ===================================================================


async def test_active_fingerprint_prevents_duplicate_job(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 10: the partial unique index
    uq_reader_jobs_active_fingerprint prevents two active jobs with the
    same (operation_fingerprint, idempotency_key) from coexisting.

    The bootstrap service's idempotency check should return a no-op
    BEFORE attempting to insert a duplicate job. To exercise the DB
    constraint directly, we insert a duplicate job row manually and
    assert it raises.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)
    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False

    # The service should not let us enqueue a second job for the same
    # stable_document_id — it returns idempotent_noop.
    second = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert second.idempotent_noop is True
    assert second.job_id == first.job_id

    # Now exercise the DB-level constraint directly: insert a second
    # reader_run + reader_job with the SAME operation_fingerprint and
    # idempotency_key. The unique index should reject it.
    async with index_env.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                """
                INSERT INTO reader_runs (
                    reading_record_id, user_id, run_type, status,
                    record_generation, envelope_json, policy_version,
                    trigger_kind
                )
                VALUES ($1, $2, 'article_rag_index_build', 'queued',
                        1, '{}'::jsonb,
                        'article_rag_index_bootstrap_v1', 'system')
                RETURNING id
                """,
                _RECORD_ID,
                _USER_ID,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO reader_jobs (
                        reading_record_id, base_id, run_id, user_id,
                        job_type, target_type, target_key,
                        status, priority, expected_generation,
                        operation_fingerprint, idempotency_key,
                        input_hash, input_json, max_attempts
                    )
                    VALUES ($1, $2, $3, $4, 'article_rag_index_build',
                            'record', $5,
                            'queued', 0, 1,
                            $6, $7,
                            'deadbeef', '{}'::jsonb, 3)
                    """,
                    _RECORD_ID,
                    _BASE_ID,
                    run_id,
                    _USER_ID,
                    str(_STABLE_DOC_ID),
                    "article_rag_index_build_v1",
                    f"article_rag_index_build_v1:{_STABLE_DOC_ID}",
                )

    # Confirm no extra rows snuck in (the duplicate insert rolled back).
    async with index_env.acquire() as conn:
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 11: DB constraint — article_rag_index_build with null base_id rejected
# ===================================================================


async def test_db_rejects_article_rag_index_build_with_null_base_id(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 11: ck_reader_jobs_base_scope rejects
    article_rag_index_build with base_id IS NULL because the catch-all
    clause requires base_id IS NOT NULL for any job_type not in the
    build_base / input_artifact_extraction / extracted_artifact_materialization
    allow-list."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)
    # Create a real run first so we have a valid run_id to reference.
    _ = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    async with index_env.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                """
                INSERT INTO reader_runs (
                    reading_record_id, user_id, run_type, status,
                    record_generation, envelope_json, policy_version,
                    trigger_kind
                )
                VALUES ($1, $2, 'article_rag_index_build', 'cancelled',
                        1, '{}'::jsonb,
                        'article_rag_index_bootstrap_v1', 'system')
                RETURNING id
                """,
                _RECORD_ID,
                _USER_ID,
            )
            # Use a different idempotency_key so the unique constraint
            # does not fire first; we want the CHECK to fire.
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO reader_jobs (
                        reading_record_id, base_id, run_id, user_id,
                        job_type, target_type, target_key,
                        status, priority, expected_generation,
                        operation_fingerprint, idempotency_key,
                        input_hash, input_json, max_attempts
                    )
                    VALUES ($1, NULL, $2, $3, 'article_rag_index_build',
                            'record', $4,
                            'cancelled', 0, 1,
                            'fp_v1', 'ik_v1_unique',
                            'deadbeef', '{}'::jsonb, 3)
                    """,
                    _RECORD_ID,
                    run_id,
                    _USER_ID,
                    str(_STABLE_DOC_ID),
                )

    # Sanity: no second job row was inserted.
    async with index_env.acquire() as conn:
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Test 12: DB constraint — duplicate active index run rejected
# ===================================================================


async def test_db_rejects_duplicate_active_index_run(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 12: uq_reader_article_rag_index_runs_active prevents
    two rows with the same stable_document_id where
    status is in ('planned', 'queued', 'indexing', 'indexed')."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)
    _ = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    plan = await service._plan_service.build_index_plan(  # noqa: SLF001
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    plan_sha = compute_plan_content_sha256(plan)

    async with index_env.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO reader_article_rag_index_runs (
                        reading_record_id, stable_document_id, base_id,
                        record_generation,
                        stable_document_content_sha256, canonical_text_sha256,
                        plan_content_sha256, chunk_count,
                        status
                    )
                    VALUES ($1, $2, $3, 1,
                            $4, $5,
                            $6, 1,
                            'queued')
                    """,
                    _RECORD_ID,
                    _STABLE_DOC_ID,
                    _BASE_ID,
                    plan.content_sha256,
                    plan.canonical_text_sha256,
                    plan_sha,
                )

    # Sanity: only the first row exists.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1


# ===================================================================
# Test 12b: DB constraint — superseded / failed rows do NOT conflict
# ===================================================================


async def test_db_allows_new_active_row_after_superseded(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 12 (complement): a 'superseded' or 'failed' row does
    not collide with a new 'queued' row for the same
    stable_document_id."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)
    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Mark the first run as superseded.
    async with index_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_article_rag_index_runs
            SET status = 'superseded', completed_at = $2, updated_at = $2
            WHERE id = $1
            """,
            first.index_run_id,
            datetime.now(UTC),
        )
        # Mark the linked job as superseded too so the active fingerprint
        # index does not block a future re-enqueue.
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'superseded'
            WHERE id = $1
            """,
            first.job_id,
        )

    # A new bootstrap should succeed and create a fresh active row.
    second = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert second.idempotent_noop is False
    assert second.index_run_id != first.index_run_id

    async with index_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status FROM reader_article_rag_index_runs
            WHERE stable_document_id = $1
            ORDER BY created_at ASC
            """,
            _STABLE_DOC_ID,
        )
        assert len(rows) == 2
        assert rows[0]["status"] == "superseded"
        assert rows[1]["status"] == "queued"
        assert rows[1]["id"] == second.index_run_id


# ===================================================================
# Test 13: convenience wrapper opens its own transaction
# ===================================================================


async def test_convenience_wrapper_opens_own_transaction(
    index_env: asyncpg.Pool,
) -> None:
    """The convenience wrapper ``bootstrap_article_rag_index`` opens its
    own connection + transaction so callers don't need to manage one."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        now=datetime.now(UTC),
    )

    assert result.idempotent_noop is False
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


# ===================================================================
# Bootstrap must not depend on embedding / vector providers
# ===================================================================


async def test_bootstrap_has_no_embedding_or_vector_provider_attributes(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: bootstrap MUST NOT depend on embedding or vector
    providers.  Verified structurally: the bootstrap service carries
    no ``embedding_provider`` / ``vector_writer`` / ``vector_searcher``
    attributes, and its constructor accepts no such parameters.  This
    is a regression guard against any future change that might sneak
    network-call dependencies into the bootstrap path.
    """
    service = _build_service(index_env)
    for attr in (
        "embedding_provider",
        "vector_writer",
        "vector_searcher",
        "_embedding_provider",
        "_vector_writer",
        "_vector_searcher",
    ):
        assert not hasattr(service, attr), (
            f"bootstrap service must not carry attribute {attr!r}"
        )
