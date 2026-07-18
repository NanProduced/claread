"""Tests for D6-I4B Article RAG Index Job Bootstrap + Index State Foundation.

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

Uses real PostgreSQL with a temporary schema (BASELINE_SQL, which now
includes 0004_reader_document_blocks.sql and 0010_reader_article_rag_index_state.sql).
"""

from __future__ import annotations

import hashlib
import traceback
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapError,
    ArticleRagIndexBootstrapResult,
    ArticleRagIndexBootstrapService,
    DEFAULT_INDEX_VERSION,
)
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    resolve_article_rag_index_profile,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]

# Reuse seed helpers + UUIDs from the I4A test module.
from tests.test_d6_i4a_article_rag_index_plan import (  # noqa: E402
    _BASE_ID,
    _OTHER_USER_ID,
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
    _build_base_text_and_offsets,
    _main_reading_policy,
    _metadata_only_policy,
    _rag_ask_only_policy,
    _seed_base,
    _seed_block,
    _seed_full_environment,
    _seed_record,
    _seed_stable_document,
    _seed_unit,
    _seed_segment,
    _seed_user,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# P1-C: migration 0021 adds the durable ``profile_fingerprint`` column
# (NOT NULL, SHA-256 CHECK) to ``reader_article_rag_index_runs``.  The
# bootstrap service now writes this column on every fresh insert, so
# the Article RAG bootstrap / worker / smoke / dry-run test schemas
# must apply migration 0021 on top of BASELINE_SQL.  This is intentionally
# a per-file append rather than a global BASELINE_SQL mutation so the
# rest of the Reader test surface keeps its current schema baseline.
_MIGRATION_0021_PATH = (
    REPO_ROOT / "infra" / "migrations" / "0021_reader_article_rag_profile_fingerprint.sql"
)
_MIGRATION_0021_SQL = _MIGRATION_0021_PATH.read_text(encoding="utf-8")

# 0004 (document_blocks) and 0010 (article_rag_index_state) are now in
# BASELINE_SQL; migration 0021 (profile_fingerprint) is appended per-file.
INDEX_BOOTSTRAP_SCHEMA_SQL = BASELINE_SQL + "\n" + _MIGRATION_0021_SQL


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
               index_version, chunker_version,
               embedding_model, vector_store_provider, vector_collection,
               profile_fingerprint,
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
    assert result.index_version == DEFAULT_INDEX_VERSION
    assert result.chunker_version == "article_rag_index_plan_v1"
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
        assert job["operation_fingerprint"] == (
            f"article_rag_index_build_v1:{DEFAULT_INDEX_VERSION}"
        )
        assert job["idempotency_key"] == (
            f"article_rag_index_build_v1:{_STABLE_DOC_ID}:{DEFAULT_INDEX_VERSION}"
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
    (stable_document_id, index_version) has a different
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

    # A second bootstrap with the same index_version should fail closed
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
        "index_version",
        "chunker_version",
        "profile_fingerprint",
    }
    assert set(job_input.keys()) == expected_keys
    assert set(run_input.keys()) == expected_keys

    assert job_input["source"] == "article_rag_index_bootstrap"
    assert job_input["reading_record_id"] == str(_RECORD_ID)
    assert job_input["stable_document_id"] == str(_STABLE_DOC_ID)
    assert job_input["base_id"] == str(_BASE_ID)
    assert job_input["record_generation"] == 1
    assert job_input["index_run_id"] == str(result.index_run_id)
    assert job_input["index_version"] == DEFAULT_INDEX_VERSION
    assert job_input["chunker_version"] == "article_rag_index_plan_v1"
    # P1-C: profile_fingerprint is the canonical SHA-256 of the V1
    # ArticleRagIndexProfile, frozen into both input_json and the
    # index-run row.  It carries no model settings, API key, URI,
    # token, or chunk / article text.
    assert job_input["profile_fingerprint"] == result.profile_fingerprint
    assert len(job_input["profile_fingerprint"]) == 64
    assert all(
        c in "0123456789abcdef"
        for c in job_input["profile_fingerprint"]
    )

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
    # (stable_document_id, index_version) — it returns idempotent_noop.
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
                    f"article_rag_index_build_v1:{DEFAULT_INDEX_VERSION}",
                    f"article_rag_index_build_v1:{_STABLE_DOC_ID}:{DEFAULT_INDEX_VERSION}",
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
    first = await service.bootstrap_article_rag_index(
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
    two rows with the same (stable_document_id, index_version) where
    status is in ('planned', 'queued', 'indexing', 'indexed')."""
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)
    first = await service.bootstrap_article_rag_index(
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
                        status, index_version, chunker_version,
                        profile_fingerprint
                    )
                    VALUES ($1, $2, $3, 1,
                            $4, $5,
                            $6, 1,
                            'queued', $7, 'article_rag_index_plan_v1',
                            $8)
                    """,
                    _RECORD_ID,
                    _STABLE_DOC_ID,
                    _BASE_ID,
                    plan.content_sha256,
                    plan.canonical_text_sha256,
                    plan_sha,
                    DEFAULT_INDEX_VERSION,
                    first.profile_fingerprint,
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
    (stable_document_id, index_version)."""
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
# Test 14: P1-C — unknown / unregistered index_version fails closed
# ===================================================================


async def test_unknown_index_version_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C: an unregistered ``index_version`` must fail closed with
    ``reason_code='index_profile_unregistered'``, write no index-run /
    job / run row, and must not echo the offending input in the error
    message or traceback.

    This replaces the legacy ``test_custom_index_version_reflected_in_fingerprint``
    contract: P1-C freezes the V1 registry to a single registered
    ``index_version`` (``article_rag_index_v1``), and any other version
    — including the previous ``article_rag_index_v2_test`` sentinel —
    is rejected before plan construction or DB writes.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    malicious_version = "article_rag_index_v2_test"
    with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
        await service.bootstrap_article_rag_index(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=malicious_version,
        )

    err = exc_info.value
    assert err.reason_code == "index_profile_unregistered"
    # The offending input MUST NOT be echoed in the error message or
    # in the string representation of the cause.
    assert malicious_version not in str(err)
    assert malicious_version not in repr(err)
    if err.__cause__ is not None:
        assert malicious_version not in str(err.__cause__)
        assert malicious_version not in repr(err.__cause__)

    # No index-run / job / run row may be written for an unregistered
    # version — fail-closed BEFORE any DB write.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


# ===================================================================
# P1-C: profile_fingerprint freeze matrix (spec §14)
# ===================================================================


# The frozen V1 profile fingerprint from the P1-B resolver.  Every
# bootstrap with the default index_version MUST freeze this exact
# digest into the index-run column, the job input_json, and the
# job input_hash.
_V1_PROFILE_FINGERPRINT = (
    resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ).profile_fingerprint
)


async def test_p1c_index_run_persists_resolved_profile_fingerprint(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: a fresh bootstrap MUST persist the resolved
    ``profile_fingerprint`` into the ``reader_article_rag_index_runs``
    row, and the persisted value MUST equal both
    ``result.profile_fingerprint`` and the canonical V1 golden digest
    from the P1-B resolver.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert result.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    async with index_env.acquire() as conn:
        index_run = await _fetch_index_run(conn, index_run_id=result.index_run_id)
        assert index_run["profile_fingerprint"] == result.profile_fingerprint
        assert index_run["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT


async def test_p1c_job_input_json_freezes_same_fingerprint(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: ``reader_jobs.input_json.profile_fingerprint`` MUST be
    identical to the index-run column and to ``result.profile_fingerprint``.
    The same fingerprint MUST also appear in ``reader_runs.envelope_json``
    because the run envelope mirrors the job input_json.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    async with index_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=result.job_id)
        run_envelope = await conn.fetchval(
            "SELECT envelope_json FROM reader_runs WHERE id = $1",
            job["run_id"],
        )

    assert job["input_json"]["profile_fingerprint"] == result.profile_fingerprint
    assert run_envelope["profile_fingerprint"] == result.profile_fingerprint
    assert (
        job["input_json"]["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT
    )


async def test_p1c_input_hash_covers_profile_fingerprint(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: ``reader_jobs.input_hash`` MUST be the SHA-256 of a
    concatenation that includes ``profile_fingerprint``.  Verifying
    the exact digest proves the fingerprint participates in the hash
    (any drift in the concatenation order or contents would produce a
    different digest).
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Reconstruct the plan to recover stable_document_id / base_id /
    # plan_content_sha256 (these are deterministic for the seeded
    # paragraph environment).
    plan = await service._plan_service.build_index_plan(  # noqa: SLF001
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    plan_sha = compute_plan_content_sha256(plan)

    expected_input_hash = hashlib.sha256(
        (
            f"{plan.stable_document_id}:"
            f"{plan.base_id}:"
            f"{plan_sha}:"
            f"{DEFAULT_INDEX_VERSION}:"
            f"{result.profile_fingerprint}"
        ).encode()
    ).hexdigest()

    async with index_env.acquire() as conn:
        job = await _fetch_job(conn, job_id=result.job_id)
        assert job["input_hash"] == expected_input_hash


async def test_p1c_result_carries_profile_fingerprint_field(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: ``ArticleRagIndexBootstrapResult.profile_fingerprint``
    is a populated 64-char lowercase SHA-256 hex string equal to the
    V1 golden digest.  This is the trust basis for downstream worker /
    retrieval layers that will consume the fingerprint next round.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert isinstance(result.profile_fingerprint, str)
    assert len(result.profile_fingerprint) == 64
    assert all(
        c in "0123456789abcdef" for c in result.profile_fingerprint
    )
    assert result.profile_fingerprint == _V1_PROFILE_FINGERPRINT


async def test_p1c_idempotent_noop_returns_same_fingerprint(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: when an active index-run already exists with the same
    (index_version, profile_fingerprint, plan_content_sha256,
    chunk_count), a second bootstrap returns an idempotent no-op whose
    ``profile_fingerprint`` equals the persisted row's fingerprint.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    second = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert second.idempotent_noop is True
    assert second.profile_fingerprint == first.profile_fingerprint
    assert second.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1


async def test_p1c_existing_run_fingerprint_mismatch_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: if an existing active index-run has a
    ``profile_fingerprint`` different from the current resolution,
    bootstrap MUST fail closed with
    ``reason_code='index_profile_fingerprint_mismatch'``.  The
    existing row MUST NOT be reused, overwritten, or auto-corrected,
    and the fingerprint value MUST NOT be echoed in the error message.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    # First bootstrap creates a row with the correct V1 fingerprint.
    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    # Manually mutate the persisted profile_fingerprint to a different
    # valid SHA-256 (64-char lowercase hex) so the row is now
    # inconsistent with the V1 resolution.  This simulates a legacy
    # row frozen under a different canonical profile.
    fake_fingerprint = "a" * 64
    assert fake_fingerprint != _V1_PROFILE_FINGERPRINT
    async with index_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_article_rag_index_runs
            SET profile_fingerprint = $2
            WHERE id = $1
            """,
            first.index_run_id,
            fake_fingerprint,
        )

    # Second bootstrap under the same (default) index_version must
    # fail closed with the fingerprint mismatch reason code.
    with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
        await service.bootstrap_article_rag_index(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

    err = exc_info.value
    assert err.reason_code == "index_profile_fingerprint_mismatch"
    # The fingerprint value MUST NOT be echoed in the error.
    assert _V1_PROFILE_FINGERPRINT not in str(err)
    assert _V1_PROFILE_FINGERPRINT not in repr(err)
    assert fake_fingerprint not in str(err)
    assert fake_fingerprint not in repr(err)

    # No new index-run / job / run may be written — the existing row
    # is left untouched.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1
        persisted_fp = await conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs "
            "WHERE id = $1",
            first.index_run_id,
        )
        assert persisted_fp == fake_fingerprint


async def test_p1c_plan_chunker_mismatch_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C §14: if the plan builder returns a ``chunker_version``
    that does not match the resolved profile's ``chunker_version``,
    bootstrap MUST fail closed with
    ``reason_code='index_profile_chunker_mismatch'`` and write no
    index-run / job / run row.

    Simulated by injecting a stub plan service whose plan carries a
    divergent chunker_version.  The real V1 plan builder always
    returns ``article_rag_index_plan_v1`` (matching the V1 profile),
    so this test guards against future drift between the plan builder
    and the registry.
    """
    import dataclasses

    await _seed_paragraph_environment(index_env)

    # Build a real plan first so all other fields are valid, then
    # mutate chunker_version to a divergent value via dataclasses.replace
    # (the plan is a frozen dataclass).
    real_plan_service = ArticleRagIndexPlanService(pool=index_env)
    real_plan = await real_plan_service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert real_plan.chunker_version == "article_rag_index_plan_v1"

    divergent_plan = dataclasses.replace(
        real_plan,
        chunker_version="article_rag_index_plan_v2_drift",
    )

    class _DriftPlanService:
        async def build_index_plan_in_transaction(self, conn, *, record_id, user_id):
            return divergent_plan

        async def build_index_plan(self, *, record_id, user_id):
            return divergent_plan

    drift_service = ArticleRagIndexBootstrapService(
        pool=index_env,
        plan_service=_DriftPlanService(),  # type: ignore[arg-type]
    )

    with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
        await drift_service.bootstrap_article_rag_index(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

    err = exc_info.value
    assert err.reason_code == "index_profile_chunker_mismatch"

    # No index-run / job / run may be written.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 0
        assert await _count_reader_runs(conn) == 0
        assert await _count_reader_jobs(conn) == 0


async def test_p1c_bootstrap_has_no_embedding_or_vector_provider_attributes(
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


# ===================================================================
# P1-C rework — bootstrap 3-layer idempotency freeze guard
# (spec §2 tests 5-7)
# ===================================================================


# Sentinel values used to simulate malicious fingerprint / hash payloads.
# These are format-valid (64-char lowercase hex) but MUST NOT be echoed
# in any error message, str, repr, traceback, or __cause__ chain.
_WRONG_FP_SENTINEL = "b" * 64
_WRONG_HASH_SENTINEL = "c" * 64


async def _update_job_input_json(
    conn: asyncpg.Connection,
    job_id: UUID,
    *,
    remove_fp: bool = False,
    fp_value: str | None = None,
) -> None:
    """Mutate ``reader_jobs.input_json`` to simulate a legacy or corrupt
    payload.

    * ``remove_fp=True`` — delete the ``profile_fingerprint`` key (legacy
      pre-P1-C payload).
    * ``fp_value=<str>`` — set ``profile_fingerprint`` to an arbitrary
      value (wrong or sentinel).
    """
    job = await _fetch_job(conn, job_id=job_id)
    input_json = dict(job["input_json"])
    if remove_fp:
        input_json.pop("profile_fingerprint", None)
    elif fp_value is not None:
        input_json["profile_fingerprint"] = fp_value
    await conn.execute(
        "UPDATE reader_jobs SET input_json = $2 WHERE id = $1",
        job_id,
        jsonb_param(input_json),
    )


async def _update_job_input_hash(
    conn: asyncpg.Connection,
    job_id: UUID,
    input_hash: str,
) -> None:
    """Overwrite ``reader_jobs.input_hash`` with an arbitrary value."""
    await conn.execute(
        "UPDATE reader_jobs SET input_hash = $2 WHERE id = $1",
        job_id,
        input_hash,
    )


async def _set_index_run_status(
    conn: asyncpg.Connection,
    index_run_id: UUID,
    status: str,
) -> None:
    """Update ``reader_article_rag_index_runs.status`` to simulate worker
    progression (e.g. ``queued`` → ``indexed``)."""
    await conn.execute(
        "UPDATE reader_article_rag_index_runs SET status = $2 WHERE id = $1",
        index_run_id,
        status,
    )


def _compute_legacy_input_hash_without_fingerprint(
    *,
    stable_document_id: UUID,
    base_id: UUID,
    plan_content_sha256: str,
    index_version: str,
) -> str:
    """Recompute the pre-P1-C ``input_hash`` (no ``profile_fingerprint``
    suffix).  Used to simulate a legacy job payload frozen under the old
    hash algorithm."""
    return hashlib.sha256(
        (
            f"{stable_document_id}:"
            f"{base_id}:"
            f"{plan_content_sha256}:"
            f"{index_version}"
        ).encode()
    ).hexdigest()


# ----- Test 5: bootstrap active idempotency freeze mismatch ----------


@pytest.mark.parametrize(
    "corruption_kind",
    [
        "missing_fp",
        "mismatched_fp",
        "old_algorithm_hash",
        "sentinel_attack",
    ],
)
async def test_p1c_rework_idempotency_freeze_mismatch_fails_closed(
    index_env: asyncpg.Pool,
    corruption_kind: str,
) -> None:
    """P1-C rework Problem B (bootstrap side): if an execution-active
    existing run has a job layer that does not match the index-run's
    frozen ``profile_fingerprint``, bootstrap MUST fail closed with
    ``reason_code='idempotent_run_profile_freeze_mismatch'``.

    Sub-cases:
      * ``missing_fp`` — ``reader_jobs.input_json`` has no
        ``profile_fingerprint`` key (legacy pre-P1-C payload).
      * ``mismatched_fp`` — ``input_json.profile_fingerprint`` is a
        wrong-but-format-valid SHA-256 (``"b" * 64``).
      * ``old_algorithm_hash`` — ``reader_jobs.input_hash`` was computed
        under the pre-P1-C algorithm (no fingerprint suffix).
      * ``sentinel_attack`` — both ``input_json.profile_fingerprint``
        and ``input_hash`` carry malicious sentinel values; neither
        sentinel may appear in str / repr / traceback / __cause__.

    The bootstrap MUST NOT reuse, overwrite, or create any new
    index-run / reader-run / reader-job.  Original persisted data
    (including the corrupted values) MUST remain unchanged.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    # 1. Create a P1-C active run with a fully consistent 3-layer freeze.
    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False
    assert first.profile_fingerprint == _V1_PROFILE_FINGERPRINT
    assert first.job_id is not None

    # 2. Corrupt the job layer per the parameterization.  The index-run
    #    row (Layer 1) is left untouched with the correct V1 golden
    #    fingerprint, so the existing Layer 1 guard does not fire; the
    #    new 3-layer guard must catch the inconsistency.
    async with index_env.acquire() as conn:
        if corruption_kind == "missing_fp":
            await _update_job_input_json(conn, first.job_id, remove_fp=True)
        elif corruption_kind == "mismatched_fp":
            await _update_job_input_json(
                conn, first.job_id, fp_value=_WRONG_FP_SENTINEL,
            )
        elif corruption_kind == "old_algorithm_hash":
            legacy_hash = _compute_legacy_input_hash_without_fingerprint(
                stable_document_id=first.stable_document_id,
                base_id=first.base_id,
                plan_content_sha256=first.plan_content_sha256,
                index_version=first.index_version,
            )
            await _update_job_input_hash(conn, first.job_id, legacy_hash)
        elif corruption_kind == "sentinel_attack":
            await _update_job_input_json(
                conn, first.job_id, fp_value=_WRONG_FP_SENTINEL,
            )
            await _update_job_input_hash(
                conn, first.job_id, _WRONG_HASH_SENTINEL,
            )
        else:  # pragma: no cover — defensive
            raise AssertionError(f"unknown corruption_kind={corruption_kind!r}")

    # 3. Bootstrap must fail closed with the new reason_code.
    with pytest.raises(ArticleRagIndexBootstrapError) as exc_info:
        await service.bootstrap_article_rag_index(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

    err = exc_info.value
    assert err.reason_code == "idempotent_run_profile_freeze_mismatch", (
        f"corruption_kind={corruption_kind!r}: expected "
        f"reason_code='idempotent_run_profile_freeze_mismatch', "
        f"got {err.reason_code!r}"
    )

    # 4. Sentinel values MUST NOT appear in str / repr / traceback /
    #    __cause__.  This holds for all sub-cases (the bootstrap error
    #    message is a fixed local string and never echoes persisted or
    #    expected values).
    err_str = str(err)
    err_repr = repr(err)
    tb_str = "".join(traceback.format_exception(exc_info.value))
    cause_str = (
        str(err.__cause__) if err.__cause__ is not None else ""
    )
    for sentinel in (_WRONG_FP_SENTINEL, _WRONG_HASH_SENTINEL):
        assert sentinel not in err_str, (
            f"corruption_kind={corruption_kind!r}: fingerprint/hash "
            f"sentinel must not appear in str(err)"
        )
        assert sentinel not in err_repr, (
            f"corruption_kind={corruption_kind!r}: fingerprint/hash "
            f"sentinel must not appear in repr(err)"
        )
        assert sentinel not in tb_str, (
            f"corruption_kind={corruption_kind!r}: fingerprint/hash "
            f"sentinel must not appear in traceback"
        )
        assert sentinel not in cause_str, (
            f"corruption_kind={corruption_kind!r}: fingerprint/hash "
            f"sentinel must not appear in __cause__"
        )
    # The V1 golden fingerprint must also not be echoed.
    assert _V1_PROFILE_FINGERPRINT not in err_str
    assert _V1_PROFILE_FINGERPRINT not in err_repr
    assert _V1_PROFILE_FINGERPRINT not in tb_str
    assert _V1_PROFILE_FINGERPRINT not in cause_str

    # 5. No new rows may have been created.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1

    # 6. Original data MUST be unchanged — the bootstrap must not
    #    overwrite the corrupted values (caller must drain / fix
    #    manually).  The index-run fingerprint is still the V1 golden.
    async with index_env.acquire() as conn:
        index_run = await _fetch_index_run(conn, index_run_id=first.index_run_id)
        assert index_run["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT
        job = await _fetch_job(conn, job_id=first.job_id)
        if corruption_kind == "missing_fp":
            assert "profile_fingerprint" not in job["input_json"], (
                "Bootstrap must not re-add profile_fingerprint to legacy "
                "job input_json"
            )
        elif corruption_kind in ("mismatched_fp", "sentinel_attack"):
            assert (
                job["input_json"]["profile_fingerprint"] == _WRONG_FP_SENTINEL
            ), "Bootstrap must not overwrite corrupted job input_json fingerprint"
        if corruption_kind == "old_algorithm_hash":
            expected_legacy = _compute_legacy_input_hash_without_fingerprint(
                stable_document_id=first.stable_document_id,
                base_id=first.base_id,
                plan_content_sha256=first.plan_content_sha256,
                index_version=first.index_version,
            )
            assert job["input_hash"] == expected_legacy, (
                "Bootstrap must not overwrite corrupted job input_hash"
            )
        elif corruption_kind == "sentinel_attack":
            assert job["input_hash"] == _WRONG_HASH_SENTINEL, (
                "Bootstrap must not overwrite corrupted job input_hash"
            )


# ----- Test 6: bootstrap active idempotency happy path ---------------


async def test_p1c_rework_idempotency_happy_path_3layer_consistent(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C rework Problem B (bootstrap side): when an execution-active
    existing run has a fully consistent 3-layer freeze (index-run
    ``profile_fingerprint`` + ``reader_jobs.input_json.profile_fingerprint``
    + ``reader_jobs.input_hash`` computed under the current P1-C
    algorithm), the bootstrap MUST continue to return the same
    idempotent no-op result and create no new rows.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False
    assert first.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    # The second bootstrap sees the same active run with a consistent
    # 3-layer freeze.  It MUST return the same idempotent result and
    # create no new rows.
    second = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert second.idempotent_noop is True
    assert second.index_run_id == first.index_run_id
    assert second.job_id == first.job_id
    assert second.profile_fingerprint == first.profile_fingerprint
    assert second.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1

        # Verify the 3-layer freeze is intact after the second bootstrap.
        index_run = await _fetch_index_run(conn, index_run_id=first.index_run_id)
        assert index_run["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT
        job = await _fetch_job(conn, job_id=first.job_id)
        assert (
            job["input_json"]["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT
        )


# ----- Test 7: indexed historical compatibility ----------------------


async def test_p1c_rework_indexed_historical_run_not_rejected_by_3layer_guard(
    index_env: asyncpg.Pool,
) -> None:
    """P1-C rework Problem B (bootstrap side): a legacy ``indexed`` V1
    run whose old succeeded job payload lacks ``profile_fingerprint``
    MUST NOT be rejected by the new 3-layer guard.  The guard only
    applies to execution-active statuses (``planned`` / ``queued`` /
    ``indexing``); ``indexed`` rows no longer enter worker execution
    and may carry legacy pre-P1-C job payloads.

    Setup:
      * index-run: status=``indexed``, ``profile_fingerprint``=V1 golden
        (simulating post-migration backfill).
      * job: ``input_json`` without ``profile_fingerprint`` (legacy
        pre-P1-C payload), ``input_hash`` from the old algorithm (no
        fingerprint suffix).

    Bootstrap MUST return an idempotent no-op (plan matches), not raise.
    """
    await _seed_paragraph_environment(index_env)
    service = _build_service(index_env)

    # 1. Create a P1-C active run normally (3-layer consistent).
    first = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert first.idempotent_noop is False
    assert first.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    # 2. Simulate a legacy indexed state:
    #    - index-run status: queued → indexed (worker completed)
    #    - linked job status: queued → succeeded
    #    - index-run profile_fingerprint: still V1 golden (unchanged —
    #      simulates a row that was backfilled by migration 0021)
    #    - job input_json: remove profile_fingerprint (legacy payload)
    #    - job input_hash: old algorithm (without fingerprint)
    async with index_env.acquire() as conn:
        await _set_index_run_status(conn, first.index_run_id, "indexed")
        await conn.execute(
            "UPDATE reader_jobs SET status = 'succeeded' WHERE id = $1",
            first.job_id,
        )
        await _update_job_input_json(conn, first.job_id, remove_fp=True)
        legacy_hash = _compute_legacy_input_hash_without_fingerprint(
            stable_document_id=first.stable_document_id,
            base_id=first.base_id,
            plan_content_sha256=first.plan_content_sha256,
            index_version=first.index_version,
        )
        await _update_job_input_hash(conn, first.job_id, legacy_hash)

    # 3. Bootstrap MUST return an idempotent no-op, NOT raise.  The
    #    3-layer guard only applies to execution-active statuses.
    result = await service.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert result.idempotent_noop is True
    assert result.index_run_id == first.index_run_id
    assert result.job_id == first.job_id
    assert result.profile_fingerprint == _V1_PROFILE_FINGERPRINT

    # No new rows created; original (legacy) data unchanged.
    async with index_env.acquire() as conn:
        assert await _count_index_runs(conn) == 1
        assert await _count_reader_runs(conn) == 1
        assert await _count_reader_jobs(conn) == 1

        index_run = await _fetch_index_run(conn, index_run_id=first.index_run_id)
        assert index_run["status"] == "indexed"
        assert index_run["profile_fingerprint"] == _V1_PROFILE_FINGERPRINT
        job = await _fetch_job(conn, job_id=first.job_id)
        assert job["status"] == "succeeded"
        assert "profile_fingerprint" not in job["input_json"], (
            "Bootstrap must not re-add profile_fingerprint to legacy "
            "indexed job payload"
        )
        assert job["input_hash"] == legacy_hash, (
            "Bootstrap must not overwrite legacy indexed job input_hash"
        )
