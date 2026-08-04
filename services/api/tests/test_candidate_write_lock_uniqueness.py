"""S1 Write-side candidate uniqueness tests.

Covers the two split helpers ``lock_record_for_candidate_write`` and
``supersede_ready_candidates_for_locked_record`` and their two call paths:

- Write path A: ``CandidateDocumentCreationService`` (unified input).
  Existing ``status='ready'`` candidate must be superseded when a new
  ready candidate is created for the same
  ``(reading_record_id, record_generation)``.
- Write path B: ``ExtractedArtifactMaterializationService`` (retry path).
  Re-running materialization with a pre-existing ready candidate must
  supersede it before inserting a new ready candidate — but ONLY on the
  candidate branch. Stable and rejected branches must NOT touch existing
  ready candidates.

Plus a real dual-transaction / dual-connection concurrency test using
``asyncio.Barrier`` to maximize lock contention: two concurrent writes
to the same ``(record_id, generation)`` must leave exactly one
``status='ready'`` and one ``status='superseded'`` candidate.

These tests use a real PostgreSQL schema (no mocks for the concurrency
case), so they require ``DATABASE_URL`` to point at a reachable
PostgreSQL instance. The schema is created in an isolated per-test
schema and dropped afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationService,
)
from app.services.reader_orchestration.extracted_artifact_materialization_service import (
    ExtractedArtifactMaterializationError,
    ExtractedArtifactMaterializationService,
)
from app.services.reader_orchestration.repository import (
    CandidateWriteLockError,
    lock_record_for_candidate_write,
    supersede_ready_candidates_for_locked_record,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql

from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# 0004 (document_blocks) is in BASELINE_SQL. Materialization needs
# 0007 (source_artifacts) on top.
MATERIALIZATION_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
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
async def helper_env() -> asyncpg.Pool:
    """Schema for helper-direct and concurrency tests (no source_artifacts)."""
    schema_name = f"test_cand_lock_{uuid4().hex}"
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


@pytest.fixture
async def mat_env() -> asyncpg.Pool:
    """Schema for materialization retry tests (includes source_artifacts)."""
    schema_name = f"test_cand_lock_mat_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(MATERIALIZATION_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Seeding helpers (shared)
# ---------------------------------------------------------------------------


async def _seed_user_and_record(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    record_id: UUID,
    generation: int = 1,
    title: str = "Lock Test Record",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation
            )
            VALUES ($1, $2, 'text', $3, 'en',
                    'active', 'processing', 'submitted', $4)
            """,
            record_id,
            user_id,
            title,
            generation,
        )


async def _seed_candidate(
    pool: asyncpg.Pool,
    *,
    candidate_id: UUID,
    record_id: UUID,
    user_id: UUID,
    generation: int = 1,
    status: str = "ready",
    title: str = "Existing Candidate",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5,
                    '[]'::jsonb, '',
                    '{}'::jsonb, '{}'::jsonb, $6, $7, $7)
            """,
            candidate_id,
            record_id,
            user_id,
            generation,
            title,
            status,
            _NOW,
        )


async def _count_ready_candidates(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*) FROM candidate_reading_documents
            WHERE reading_record_id = $1 AND status = 'ready'
            """,
            record_id,
        )


async def _count_superseded_candidates(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*) FROM candidate_reading_documents
            WHERE reading_record_id = $1 AND status = 'superseded'
            """,
            record_id,
        )


async def _fetch_all_candidates(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, status, record_generation
            FROM candidate_reading_documents
            WHERE reading_record_id = $1
            ORDER BY created_at, id
            """,
            record_id,
        )


# ---------------------------------------------------------------------------
# Section 1: Shared helper direct tests
# ---------------------------------------------------------------------------


async def test_helper_lock_then_supersede_existing_ready_candidate(
    helper_env: asyncpg.Pool,
) -> None:
    """lock + supersede directly supersedes an existing ready candidate.

    Pre-seed one ready candidate, call lock_record_for_candidate_write
    then supersede_ready_candidates_for_locked_record inside a
    transaction, then verify the old candidate is now 'superseded' and
    no ready candidates remain (caller would INSERT a new one after).
    """
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()
    old_candidate_id = uuid4()

    await _seed_user_and_record(pool, user_id=user_id, record_id=record_id)
    await _seed_candidate(
        pool,
        candidate_id=old_candidate_id,
        record_id=record_id,
        user_id=user_id,
        status="ready",
    )

    assert await _count_ready_candidates(pool, record_id=record_id) == 1

    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await lock_record_for_candidate_write(
                conn,
                record_id=record_id,
                user_id=user_id,
                expected_generation=1,
            )
            await supersede_ready_candidates_for_locked_record(
                conn,
                record_id=record_id,
                user_id=user_id,
                generation=result.generation,
                now=_NOW,
            )

    assert result.record_id == record_id
    assert result.generation == 1

    # The old ready candidate should now be superseded.
    assert await _count_ready_candidates(pool, record_id=record_id) == 0
    assert (
        await _count_superseded_candidates(pool, record_id=record_id) == 1
    )

    rows = await _fetch_all_candidates(pool, record_id=record_id)
    assert len(rows) == 1
    assert rows[0]["id"] == old_candidate_id
    assert rows[0]["status"] == "superseded"


async def test_lock_raises_on_generation_mismatch(
    helper_env: asyncpg.Pool,
) -> None:
    """lock raises CandidateWriteLockError with reason_code='generation_mismatch'."""
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()

    await _seed_user_and_record(
        pool, user_id=user_id, record_id=record_id, generation=2
    )

    with pytest.raises(CandidateWriteLockError) as exc_info:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await lock_record_for_candidate_write(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    expected_generation=1,
                )

    assert exc_info.value.reason_code == "generation_mismatch"


async def test_lock_raises_on_record_not_found(
    helper_env: asyncpg.Pool,
) -> None:
    """lock raises CandidateWriteLockError with reason_code='record_not_found'
    when the record does not exist / does not belong to the user.
    """
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()

    # Seed a user but no record (so the record_id does not exist).
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )

    with pytest.raises(CandidateWriteLockError) as exc_info:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await lock_record_for_candidate_write(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    expected_generation=1,
                )

    assert exc_info.value.reason_code == "record_not_found"


async def test_lock_raises_transaction_required_outside_transaction(
    helper_env: asyncpg.Pool,
) -> None:
    """lock_record_for_candidate_write fails closed with
    reason_code='transaction_required' when called outside a transaction.
    """
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()

    await _seed_user_and_record(pool, user_id=user_id, record_id=record_id)

    # Acquire a connection but do NOT open a transaction.
    async with pool.acquire() as conn:
        with pytest.raises(CandidateWriteLockError) as exc_info:
            await lock_record_for_candidate_write(
                conn,
                record_id=record_id,
                user_id=user_id,
                expected_generation=1,
            )

    assert exc_info.value.reason_code == "transaction_required"


async def test_supersede_raises_transaction_required_outside_transaction(
    helper_env: asyncpg.Pool,
) -> None:
    """supersede_ready_candidates_for_locked_record also fails closed with
    reason_code='transaction_required' when called outside a transaction.
    """
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()

    await _seed_user_and_record(pool, user_id=user_id, record_id=record_id)

    async with pool.acquire() as conn:
        with pytest.raises(CandidateWriteLockError) as exc_info:
            await supersede_ready_candidates_for_locked_record(
                conn,
                record_id=record_id,
                user_id=user_id,
                generation=1,
                now=_NOW,
            )

    assert exc_info.value.reason_code == "transaction_required"


# ---------------------------------------------------------------------------
# Section 2: Write path B — materialization retry supersedes existing ready
# ---------------------------------------------------------------------------


async def _seed_materialization_environment(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    record_id: UUID,
    original_input_id: UUID,
    artifact_id: UUID,
    source_text: str,
    generation: int = 1,
    content_type: str = "text/markdown",
    source_filename: str = "notes.md",
) -> None:
    """Seed user, record, original_input, source_artifact for materialization."""
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation
            )
            VALUES ($1, $2, 'text', 'Mat Retry Test', 'en',
                    'active', 'processing', 'submitted', $3)
            """,
            record_id,
            user_id,
            generation,
        )
        source_ref_json = {
            "artifact_id": str(artifact_id),
            "storage_provider": "oss",
            "bucket": "claread-dev",
            "object_key": f"dev/test/{source_filename}",
            "artifact_kind": "original_upload",
            "content_type": content_type,
            "source_filename": source_filename,
        }
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    $4, $5,
                    '{}'::jsonb,
                    $6)
            """,
            original_input_id,
            record_id,
            user_id,
            source_text,
            source_ref_json,
            source_sha,
        )
        # L2：模拟 extraction 完成态——confirmed_source_documents 行是
        # 正文唯一载体，materialization 从该行读取。
        await conn.execute(
            """
            INSERT INTO confirmed_source_documents (
                id, reading_record_id, user_id, record_generation,
                original_input_id, markdown_text, revision,
                content_sha256, status, edit_source
            )
            VALUES ($1, $2, $3, $4, $5, $6, 1, $7, 'draft', 'extraction')
            """,
            uuid4(),
            record_id,
            user_id,
            generation,
            original_input_id,
            source_text,
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename,
                status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    $5,
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    $6, $7, $8, $9,
                    'available')
            """,
            artifact_id,
            record_id,
            original_input_id,
            user_id,
            f"dev/test/{source_filename}",
            content_type,
            len(source_text.encode("utf-8")),
            source_sha,
            source_filename,
        )


# Candidate-requiring markdown: large enough to trigger candidate path
_CANDIDATE_MD = (
    "# Large Document Requiring Candidate Review\n\n"
    + "This is a paragraph with enough content to exceed the word limit "
    "for stable document ready path and trigger candidate creation.\n\n"
    + "| Column A | Column B |\n|----------|----------|\n"
    + "| cell1 | cell2 |\n\n"
    + "\n\n".join(
        f"Section {i}: " + ("word " * 200)
        for i in range(50)
    )
)

# Stable-ready markdown: heading + enough English prose (≥ 50 words)
_STABLE_MD = (
    "# A Short Article About Nature\n\n"
    "The quick brown fox jumps over the lazy dog near the riverbank. "
    "A small bird sings in the tree above them while the morning sun "
    "casts long shadows across the green meadow. Children laugh and "
    "play in the distance as a gentle breeze rustles the autumn leaves. "
    "This peaceful scene captures a quiet moment of harmony in the "
    "natural world around us today."
)

# Rejected text: too short (< 50 English words)
_REJECTED_TEXT = "Hello world. This is too short."


async def test_materialization_retry_supersedes_existing_ready_candidate(
    mat_env: asyncpg.Pool,
) -> None:
    """Re-running materialization with a pre-existing ready candidate
    supersedes the old one before inserting a new ready candidate.
    """
    pool = mat_env
    user_id = uuid4()
    record_id = uuid4()
    original_input_id = uuid4()
    artifact_id = uuid4()
    old_candidate_id = uuid4()

    await _seed_materialization_environment(
        pool,
        user_id=user_id,
        record_id=record_id,
        original_input_id=original_input_id,
        artifact_id=artifact_id,
        source_text=_CANDIDATE_MD,
    )
    # Pre-seed an existing ready candidate (simulating a previous
    # materialization run that has since been retried).
    await _seed_candidate(
        pool,
        candidate_id=old_candidate_id,
        record_id=record_id,
        user_id=user_id,
        status="ready",
        title="Old Candidate",
    )

    assert await _count_ready_candidates(pool, record_id=record_id) == 1

    service = ExtractedArtifactMaterializationService(pool=pool)
    result = await service.materialize_extracted_artifact(
        reading_record_id=record_id,
        original_input_id=original_input_id,
        source_artifact_id=artifact_id,
        user_id=user_id,
        expected_generation=1,
    )

    # Materialization should produce a candidate (not a stable document),
    # because the input is candidate-requiring markdown.
    assert result.outcome == "candidate_document_required"
    assert result.candidate_document_id is not None

    # Exactly one ready candidate (the new one), and one superseded
    # candidate (the old one).
    assert await _count_ready_candidates(pool, record_id=record_id) == 1
    assert (
        await _count_superseded_candidates(pool, record_id=record_id) == 1
    )

    rows = await _fetch_all_candidates(pool, record_id=record_id)
    statuses = {row["status"] for row in rows}
    assert statuses == {"ready", "superseded"}

    # The old candidate should be the superseded one.
    old_row = next(row for row in rows if row["id"] == old_candidate_id)
    assert old_row["status"] == "superseded"


async def test_materialization_stable_branch_does_not_supersede_existing_ready(
    mat_env: asyncpg.Pool,
) -> None:
    """Stable branch must NOT touch existing ready candidates.

    Pre-seed a ready candidate, run materialization with stable-ready
    text. The stable branch creates a stable document + base and sets
    active_base_id, but must NOT supersede the existing ready candidate
    (only the candidate branch calls supersede).
    """
    pool = mat_env
    user_id = uuid4()
    record_id = uuid4()
    original_input_id = uuid4()
    artifact_id = uuid4()
    old_candidate_id = uuid4()

    await _seed_materialization_environment(
        pool,
        user_id=user_id,
        record_id=record_id,
        original_input_id=original_input_id,
        artifact_id=artifact_id,
        source_text=_STABLE_MD,
        content_type="text/markdown",
        source_filename="notes.md",
    )
    # Pre-seed an existing ready candidate from a prior candidate-path run.
    await _seed_candidate(
        pool,
        candidate_id=old_candidate_id,
        record_id=record_id,
        user_id=user_id,
        status="ready",
        title="Old Candidate",
    )

    assert await _count_ready_candidates(pool, record_id=record_id) == 1

    service = ExtractedArtifactMaterializationService(pool=pool)
    result = await service.materialize_extracted_artifact(
        reading_record_id=record_id,
        original_input_id=original_input_id,
        source_artifact_id=artifact_id,
        user_id=user_id,
        expected_generation=1,
    )

    # Stable path should have been taken.
    assert result.outcome == "stable_document_ready"
    assert result.stable_document_id is not None
    assert result.base_id is not None

    # The old ready candidate must remain 'ready' — stable branch does
    # NOT call supersede_ready_candidates_for_locked_record.
    assert await _count_ready_candidates(pool, record_id=record_id) == 1
    assert (
        await _count_superseded_candidates(pool, record_id=record_id) == 0
    )

    rows = await _fetch_all_candidates(pool, record_id=record_id)
    assert len(rows) == 1
    assert rows[0]["id"] == old_candidate_id
    assert rows[0]["status"] == "ready"


async def test_materialization_rejected_branch_does_not_supersede_existing_ready(
    mat_env: asyncpg.Pool,
) -> None:
    """Rejected branch must NOT touch existing ready candidates.

    Pre-seed a ready candidate, run materialization with rejected text
    (too short). The rejected branch sets product_state='action_required'
    but must NOT supersede the existing ready candidate.
    """
    pool = mat_env
    user_id = uuid4()
    record_id = uuid4()
    original_input_id = uuid4()
    artifact_id = uuid4()
    old_candidate_id = uuid4()

    await _seed_materialization_environment(
        pool,
        user_id=user_id,
        record_id=record_id,
        original_input_id=original_input_id,
        artifact_id=artifact_id,
        source_text=_REJECTED_TEXT,
        content_type="text/plain",
        source_filename="notes.txt",
    )
    # Pre-seed an existing ready candidate from a prior candidate-path run.
    await _seed_candidate(
        pool,
        candidate_id=old_candidate_id,
        record_id=record_id,
        user_id=user_id,
        status="ready",
        title="Old Candidate",
    )

    assert await _count_ready_candidates(pool, record_id=record_id) == 1

    service = ExtractedArtifactMaterializationService(pool=pool)
    result = await service.materialize_extracted_artifact(
        reading_record_id=record_id,
        original_input_id=original_input_id,
        source_artifact_id=artifact_id,
        user_id=user_id,
        expected_generation=1,
    )

    # Rejected path should have been taken.
    assert result.outcome == "input_rejected_or_action_required"

    # The old ready candidate must remain 'ready' — rejected branch does
    # NOT call supersede_ready_candidates_for_locked_record.
    assert await _count_ready_candidates(pool, record_id=record_id) == 1
    assert (
        await _count_superseded_candidates(pool, record_id=record_id) == 0
    )

    rows = await _fetch_all_candidates(pool, record_id=record_id)
    assert len(rows) == 1
    assert rows[0]["id"] == old_candidate_id
    assert rows[0]["status"] == "ready"


# ---------------------------------------------------------------------------
# Section 3: Real dual-transaction / dual-connection concurrency test
# ---------------------------------------------------------------------------


async def test_concurrent_candidate_writes_leave_exactly_one_ready(
    helper_env: asyncpg.Pool,
) -> None:
    """Two concurrent transactions writing a candidate for the same
    (record_id, generation) must leave exactly one ready and one
    superseded candidate.

    Uses ``asyncio.Barrier`` to maximize lock contention: both
    transactions enter the barrier before either calls the helper, so
    both attempt ``SELECT ... FOR UPDATE`` as close to simultaneously as
    possible. PostgreSQL serializes them; whichever commits first
    supersedes nothing (no existing ready), the second caller's helper
    supersedes the first caller's ready candidate before inserting its
    own.

    This is a real dual-connection test — no sequential mocks.
    """
    pool = helper_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_a = uuid4()
    candidate_b = uuid4()

    await _seed_user_and_record(pool, user_id=user_id, record_id=record_id)

    barrier = asyncio.Barrier(2)

    async def _write_candidate(candidate_id: UUID) -> str:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Wait until both transactions are inside their
                # transaction block and ready to race for the lock.
                await barrier.wait()
                lock_result = await lock_record_for_candidate_write(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    expected_generation=1,
                )
                await supersede_ready_candidates_for_locked_record(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=lock_result.generation,
                    now=_NOW,
                )
                await conn.execute(
                    """
                    INSERT INTO candidate_reading_documents (
                        id, reading_record_id, user_id, record_generation,
                        title, blocks_json, canonical_text_preview,
                        source_refs_json, quality_json, status,
                        created_at, updated_at
                    )
                    VALUES ($1, $2, $3, 1,
                            'Concurrent Candidate', '[]'::jsonb, '',
                            '{}'::jsonb, '{}'::jsonb, 'ready',
                            $4, $4)
                    """,
                    candidate_id,
                    record_id,
                    user_id,
                    _NOW,
                )
                return str(candidate_id)

    # Run both writes concurrently. The barrier synchronizes them so
    # they hit the FOR UPDATE lock at the same time. PostgreSQL will
    # serialize: the first to acquire the lock inserts its candidate
    # (no existing ready to supersede), the second acquires the lock
    # after the first commits, supersedes the first's candidate, then
    # inserts its own.
    results = await asyncio.gather(
        _write_candidate(candidate_a),
        _write_candidate(candidate_b),
    )

    assert len(results) == 2
    assert set(results) == {str(candidate_a), str(candidate_b)}

    # Exactly one ready, one superseded.
    assert await _count_ready_candidates(pool, record_id=record_id) == 1
    assert (
        await _count_superseded_candidates(pool, record_id=record_id) == 1
    )

    rows = await _fetch_all_candidates(pool, record_id=record_id)
    assert len(rows) == 2
    statuses = [row["status"] for row in rows]
    assert sorted(statuses) == ["ready", "superseded"]

    # The ready candidate's id must be one of the two we inserted.
    ready_rows = [row for row in rows if row["status"] == "ready"]
    assert len(ready_rows) == 1
    assert ready_rows[0]["id"] in {candidate_a, candidate_b}
