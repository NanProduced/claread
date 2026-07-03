"""Tests for D6-I3N ExtractedArtifactMaterializationService.

Covers:
- stable txt extraction → stable document/base/units/segments + article_ready
- markdown requiring candidate → candidate row on same record/input
- rejected text → no stable/candidate writes, product_state=action_required
- stale generation / active_base already exists / artifact not bound / empty
  source_text → fail closed
- no new reading_records/original_inputs inserted
- transaction rollback on persistence failure
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.extracted_artifact_materialization_service import (
    ExtractedArtifactMaterializationError,
    ExtractedArtifactMaterializationService,
    MaterializationResult,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0007_reader_source_artifacts.sql"
).read_text(encoding="utf-8")

from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

# 0004 (document_blocks) is now in BASELINE_SQL, so the materialization
# schema is BASELINE_SQL + 0007 (reader_source_artifacts).
MATERIALIZATION_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

# Fixed UUIDs for deterministic seeding
_USER_ID = UUID("00000000-0000-0000-0000-00000000d001")
_RECORD_ID = UUID("00000000-0000-0000-0000-00000000d002")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-00000000d003")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000d004")

# Stable-ready text: ~60 English words, simple structure
_STABLE_TEXT = (
    "The quick brown fox jumps over the lazy dog near the riverbank. "
    "A small bird sings in the tree above them. The morning sun casts "
    "long shadows across the meadow. Children laugh and play in the "
    "distance while a gentle breeze rustles the leaves. This peaceful "
    "scene captures a moment of quiet harmony in nature."
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

# Candidate-requiring text: >8000 words worth of content with markdown tables
# We build a large markdown document with a table to trigger candidate path
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

# Rejected text: too short (< 50 English words)
_REJECTED_TEXT = "Hello world. This is too short."


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
async def mat_env() -> asyncpg.Pool:
    schema_name = f"test_i3n_mat_{uuid4().hex}"
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
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_environment(
    pool: asyncpg.Pool,
    *,
    source_text: str | None = _STABLE_TEXT,
    content_type: str = "text/plain",
    source_filename: str = "notes.txt",
    artifact_status: str = "available",
    artifact_bound: bool = True,
    artifact_kind: str = "original_upload",
    storage_provider: str = "oss",
    artifact_deleted_at: datetime | None = None,
    record_generation: int = 1,
    active_base_id: UUID | None = None,
    lifecycle_status: str = "active",
    deleted_at: datetime | None = None,
) -> None:
    """Seed user, record, original_input, source_artifact."""
    source_sha = (
        hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if source_text
        else None
    )

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, active_base_id, deleted_at
            )
            VALUES ($1, $2, 'text', 'I3N Test', 'en',
                    $3, 'processing', 'submitted',
                    $4, $5, $6)
            """,
            _RECORD_ID,
            _USER_ID,
            lifecycle_status,
            record_generation,
            active_base_id,
            deleted_at,
        )
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": storage_provider,
            "bucket": "claread-dev",
            "object_key": "dev/test/notes.txt",
            "artifact_kind": artifact_kind,
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
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_text,
            source_ref_json,
            source_sha if source_sha else "a" * 64,
        )
        if artifact_bound:
            await conn.execute(
                """
                INSERT INTO source_artifacts (
                    id, reading_record_id, original_input_id, user_id,
                    artifact_kind, storage_provider, bucket, object_key, endpoint,
                    content_type, byte_size, content_sha256, source_filename,
                    status, deleted_at
                )
                VALUES ($1, $2, $3, $4,
                        $5, $6, 'claread-dev',
                        'dev/test/notes.txt',
                        'https://oss-cn-shenzhen.aliyuncs.com',
                        $7, $8, $9, $10, $11, $12)
                """,
                _ARTIFACT_ID,
                _RECORD_ID,
                _ORIGINAL_INPUT_ID,
                _USER_ID,
                artifact_kind,
                storage_provider,
                content_type,
                len(source_text.encode("utf-8")) if source_text else 0,
                source_sha,
                source_filename,
                artifact_status,
                artifact_deleted_at,
            )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def _fetch_record(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM reading_records WHERE id = $1", _RECORD_ID
        )


async def _count_stable_documents(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM stable_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_candidates(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM candidate_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_reading_bases(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_reading_units(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reading_units WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_anchor_segments(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM anchor_segments WHERE reading_record_id = $1",
            _RECORD_ID,
        )


async def _count_article_ready_events(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1 AND event_type = 'article_ready'",
            _RECORD_ID,
        )


async def _count_reading_records(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM reading_records")


async def _count_original_inputs(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM original_inputs")


async def _fetch_candidate(pool: asyncpg.Pool) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM candidate_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )


def _build_service(pool: asyncpg.Pool) -> ExtractedArtifactMaterializationService:
    return ExtractedArtifactMaterializationService(pool=pool)


# ===================================================================
# Stable path tests
# ===================================================================


async def test_stable_txt_creates_stable_document_base_and_article_ready(
    mat_env: asyncpg.Pool,
) -> None:
    """Stable-ready txt extraction → stable doc + base + units + segments + article_ready event."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT, content_type="text/plain")

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
        expected_generation=1,
    )

    assert result.outcome == "stable_document_ready"
    assert result.stable_document_id is not None
    assert result.base_id is not None
    assert result.article_ready_event_id is not None
    assert result.article_ready_sequence is not None

    # Verify DB state
    record = await _fetch_record(mat_env)
    assert record["readiness_state"] == "article_ready"
    assert record["product_state"] == "readable_enhancing"
    assert record["active_base_id"] == result.base_id

    assert await _count_stable_documents(mat_env) == 1
    assert await _count_reading_bases(mat_env) == 1
    assert await _count_reading_units(mat_env) > 0
    assert await _count_anchor_segments(mat_env) > 0
    assert await _count_article_ready_events(mat_env) == 1


async def test_stable_markdown_creates_stable_document(mat_env: asyncpg.Pool) -> None:
    """Stable-ready markdown extraction → stable doc with markdown heading as title."""
    await _seed_environment(
        mat_env, source_text=_STABLE_MD, content_type="text/markdown", source_filename="article.md"
    )

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
        expected_generation=1,
    )

    assert result.outcome == "stable_document_ready"
    assert result.stable_document_id is not None
    assert result.base_id is not None


async def test_stable_path_does_not_create_candidate(mat_env: asyncpg.Pool) -> None:
    """Stable path must not create candidate_reading_documents."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    service = _build_service(mat_env)
    await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert await _count_candidates(mat_env) == 0


# ===================================================================
# Candidate path tests
# ===================================================================


async def test_candidate_markdown_creates_candidate_on_same_record(
    mat_env: asyncpg.Pool,
) -> None:
    """Candidate-requiring markdown → candidate row on same record/input."""
    await _seed_environment(
        mat_env,
        source_text=_CANDIDATE_MD,
        content_type="text/markdown",
        source_filename="large.md",
    )

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
        expected_generation=1,
    )

    assert result.outcome == "candidate_document_required"
    assert result.candidate_document_id is not None
    assert result.block_count is not None and result.block_count > 0
    assert result.canonical_text_preview is not None

    # Verify candidate row on same record
    assert await _count_candidates(mat_env) == 1
    candidate = await _fetch_candidate(mat_env)
    assert candidate["reading_record_id"] == _RECORD_ID
    assert candidate["user_id"] == _USER_ID
    assert candidate["record_generation"] == 1
    assert candidate["status"] == "ready"

    # Verify record state advanced to candidate_base_ready
    record = await _fetch_record(mat_env)
    assert record["readiness_state"] == "candidate_base_ready"
    assert record["product_state"] == "needs_confirmation"
    assert record["active_base_id"] is None  # no base created


async def test_candidate_path_does_not_create_stable_or_base(mat_env: asyncpg.Pool) -> None:
    """Candidate path must not create stable_reading_documents or reading_bases."""
    await _seed_environment(
        mat_env, source_text=_CANDIDATE_MD, content_type="text/markdown", source_filename="large.md"
    )

    service = _build_service(mat_env)
    await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert await _count_stable_documents(mat_env) == 0
    assert await _count_reading_bases(mat_env) == 0
    assert await _count_article_ready_events(mat_env) == 0


# ===================================================================
# Rejected path tests
# ===================================================================


async def test_rejected_text_sets_action_required(mat_env: asyncpg.Pool) -> None:
    """Rejected text → product_state=action_required, no stable/candidate writes."""
    await _seed_environment(mat_env, source_text=_REJECTED_TEXT)

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert result.stable_document_id is None
    assert result.candidate_document_id is None

    record = await _fetch_record(mat_env)
    assert record["product_state"] == "action_required"
    assert record["readiness_state"] == "submitted"  # unchanged
    assert record["active_base_id"] is None

    assert await _count_stable_documents(mat_env) == 0
    assert await _count_candidates(mat_env) == 0
    assert await _count_reading_bases(mat_env) == 0
    assert await _count_article_ready_events(mat_env) == 0


# ===================================================================
# Fail-closed validation tests
# ===================================================================


async def test_stale_generation_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Stale generation must fail closed with typed error."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT, record_generation=2)

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "generation" in str(exc_info.value).lower()


async def test_active_base_already_exists_fail_closed(mat_env: asyncpg.Pool) -> None:
    """If active_base_id is already set, materialization must fail closed."""
    # Insert a real reading_bases row first (FK constraint requires it)
    base_text = "Existing base text."
    base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    base_id = uuid4()
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)
    async with mat_env.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id, reading_record_id, base_version, record_generation,
                text, content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                language, title_snapshot, navigation_json, status
            )
            VALUES ($1, $2, 1, 1, $3, $4, $5,
                    'd3-p1-canonicalizer', 'd3-p1-builder', 'd3-p1-segmenter',
                    'en', 'Existing', '{"units":[]}'::jsonb, 'active')
            """,
            base_id,
            _RECORD_ID,
            base_text,
            base_sha,
            len(base_text),
        )
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            base_id,
        )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "active_base_id" in str(exc_info.value).lower() or "base" in str(exc_info.value).lower()


async def test_artifact_not_bound_fail_closed(mat_env: asyncpg.Pool) -> None:
    """If source_artifact is not bound to the record/input, fail closed.

    Seed record + original_input but NO source_artifact → service's FOR UPDATE
    query returns no row.
    """
    await _seed_environment(
        mat_env, source_text=_STABLE_TEXT, artifact_bound=False
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "source_artifact" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


async def test_empty_source_text_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Empty source_text must fail closed."""
    await _seed_environment(mat_env, source_text=None)

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "source_text" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()


async def test_artifact_not_available_fail_closed(mat_env: asyncpg.Pool) -> None:
    """If source_artifact status is not 'available', fail closed."""
    await _seed_environment(
        mat_env, source_text=_STABLE_TEXT, artifact_status="failed"
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "available" in str(exc_info.value).lower()


async def test_wrong_user_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Wrong user_id must fail closed."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    service = _build_service(mat_env)
    wrong_user = uuid4()
    with pytest.raises(ExtractedArtifactMaterializationError):
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=wrong_user, expected_generation=1
        )


async def test_deleted_record_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Deleted record must fail closed."""
    await _seed_environment(
        mat_env, source_text=_STABLE_TEXT, deleted_at=datetime.now(UTC)
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "deleted" in str(exc_info.value).lower()


# ===================================================================
# Caller-managed transaction guard
# ===================================================================


async def test_in_transaction_variant_requires_active_transaction(
    mat_env: asyncpg.Pool,
) -> None:
    """materialize_extracted_artifact_in_transaction must fail closed if the
    caller forgot to open a transaction on ``conn``.

    Mirrors the guard in ``persist_stable_document_freeze_plan`` and
    ``confirm_candidate_document``. Without it, the multi-step materialization
    pipeline could partially commit under autocommit.
    """
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)
    service = _build_service(mat_env)

    async with mat_env.acquire() as conn:
        # NOT inside conn.transaction() — autocommit mode
        with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
            await service.materialize_extracted_artifact_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                original_input_id=_ORIGINAL_INPUT_ID,
                source_artifact_id=_ARTIFACT_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        assert exc_info.value.reason_code == "caller_transaction_required"

    # Verify NO materialization writes happened (no stable doc, no base)
    assert await _count_stable_documents(mat_env) == 0
    assert await _count_reading_bases(mat_env) == 0


# ===================================================================
# No new records/inputs invariant
# ===================================================================


async def test_no_new_reading_records_or_original_inputs_inserted(
    mat_env: asyncpg.Pool,
) -> None:
    """Materialization must not create new reading_records or original_inputs."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    records_before = await _count_reading_records(mat_env)
    inputs_before = await _count_original_inputs(mat_env)

    service = _build_service(mat_env)
    await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert await _count_reading_records(mat_env) == records_before
    assert await _count_original_inputs(mat_env) == inputs_before


# ===================================================================
# Candidate path: no new records/inputs invariant
# ===================================================================


async def test_candidate_path_no_new_records_or_inputs(mat_env: asyncpg.Pool) -> None:
    """Candidate path must also not create new reading_records or original_inputs."""
    await _seed_environment(
        mat_env, source_text=_CANDIDATE_MD, content_type="text/markdown", source_filename="large.md"
    )

    records_before = await _count_reading_records(mat_env)
    inputs_before = await _count_original_inputs(mat_env)

    service = _build_service(mat_env)
    await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert await _count_reading_records(mat_env) == records_before
    assert await _count_original_inputs(mat_env) == inputs_before


# ===================================================================
# Transaction rollback test
# ===================================================================


async def test_transaction_rollback_on_persistence_failure(
    mat_env: asyncpg.Pool,
) -> None:
    """If the freeze plan build fails (e.g. invalid blocks), all writes roll back.

    We simulate this by providing a source_text that passes suitability but
    then causing a failure inside the transaction. The easiest deterministic
    way is to pre-insert a conflicting stable_reading_documents row with the
    same (reading_record_id, record_generation) but a different content_sha256,
    which makes persist_stable_document_freeze_plan fail closed.
    """
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    # Pre-insert a stable_reading_documents row with the same record/generation
    # but different content_sha256 — this will cause the idempotency check to
    # fail closed (different content_sha256 → StableDocumentFreezePersistenceError)
    fake_sha256 = "0" * 64  # 64-char hex string (all zeros)
    async with mat_env.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO stable_reading_documents (
                id, reading_record_id, record_generation, title,
                document_version, source_profile_json, content_sha256,
                status, frozen_at
            )
            VALUES ($1, $2, 1, 'Pre-existing',
                    1, '{}'::jsonb, $3,
                    'active', NOW())
            """,
            uuid4(),
            _RECORD_ID,
            fake_sha256,
        )

    service = _build_service(mat_env)

    # The materialization should fail because the freeze persistence detects
    # a different content_sha256 for the same (record, generation)
    with pytest.raises(Exception):
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )

    # Verify rollback: record state should be unchanged
    record = await _fetch_record(mat_env)
    assert record["readiness_state"] == "submitted"
    assert record["product_state"] == "processing"
    assert record["active_base_id"] is None

    # No new reading_bases, no article_ready events
    assert await _count_reading_bases(mat_env) == 0
    assert await _count_article_ready_events(mat_env) == 0


# ===================================================================
# Double-call idempotency tests (P1: entry state gate)
# ===================================================================


async def test_double_call_candidate_path_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Second call after candidate creation must fail closed (no duplicate candidate).

    The candidate_reading_documents table has no UNIQUE constraint on
    (reading_record_id, record_generation), so without the processing/submitted
    entry gate a second call would insert another ready candidate row.
    """
    await _seed_environment(
        mat_env,
        source_text=_CANDIDATE_MD,
        content_type="text/markdown",
        source_filename="large.md",
    )

    service = _build_service(mat_env)
    first = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )
    assert first.outcome == "candidate_document_required"
    assert await _count_candidates(mat_env) == 1

    # Second call must fail closed — record is now needs_confirmation/candidate_base_ready
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    msg = str(exc_info.value).lower()
    assert "processing/submitted" in msg or "processing" in msg

    # No duplicate candidate was inserted
    assert await _count_candidates(mat_env) == 1


async def test_double_call_stable_path_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Second call after stable materialization must fail closed.

    Record is now readable_enhancing/article_ready with active_base_id set.
    """
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    service = _build_service(mat_env)
    first = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )
    assert first.outcome == "stable_document_ready"

    # Second call must fail closed — active_base_id is set AND state advanced
    with pytest.raises(ExtractedArtifactMaterializationError):
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )

    # No duplicate stable doc / base / event
    assert await _count_stable_documents(mat_env) == 1
    assert await _count_reading_bases(mat_env) == 1
    assert await _count_article_ready_events(mat_env) == 1


async def test_double_call_rejected_path_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Second call after rejection must fail closed.

    Record product_state is now action_required (readiness_state stays submitted).
    """
    await _seed_environment(mat_env, source_text=_REJECTED_TEXT)

    service = _build_service(mat_env)
    first = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )
    assert first.outcome == "input_rejected_or_action_required"

    # Second call must fail closed — product_state is action_required
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    msg = str(exc_info.value).lower()
    assert "processing/submitted" in msg or "processing" in msg

    # No writes happened on the second call
    assert await _count_stable_documents(mat_env) == 0
    assert await _count_candidates(mat_env) == 0


# ===================================================================
# Source type derivation tests (P1: _derive_source_type fail-closed)
# ===================================================================


async def test_pdf_artifact_routes_to_candidate(mat_env: asyncpg.Pool) -> None:
    """application/pdf artifact → pdf_text source_type → candidate (forced by gate).

    The suitability gate forces candidate_document_required for pdf_text
    unless explicit high-confidence metadata is present.
    """
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        content_type="application/pdf",
        source_filename="doc.pdf",
    )

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert result.outcome == "candidate_document_required"
    assert result.candidate_document_id is not None
    assert await _count_candidates(mat_env) == 1
    # No stable doc created
    assert await _count_stable_documents(mat_env) == 0
    assert await _count_reading_bases(mat_env) == 0


async def test_image_artifact_routes_to_candidate(mat_env: asyncpg.Pool) -> None:
    """image/png artifact → ocr_text source_type → candidate (forced by gate).

    The suitability gate unconditionally forces candidate_document_required
    for ocr_text regardless of text content.
    """
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        content_type="image/png",
        source_filename="scan.png",
    )

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
    )

    assert result.outcome == "candidate_document_required"
    assert result.candidate_document_id is not None
    assert await _count_candidates(mat_env) == 1


async def test_unknown_content_type_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Unknown content_type must fail closed instead of defaulting to txt_file."""
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        content_type="application/vnd.unknown.format",
        source_filename="mystery.bin",
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "source_type" in str(exc_info.value).lower() or "content_type" in str(exc_info.value).lower()

    # No writes happened
    assert await _count_stable_documents(mat_env) == 0
    assert await _count_candidates(mat_env) == 0


async def test_octet_stream_without_txt_md_extension_fail_closed(
    mat_env: asyncpg.Pool,
) -> None:
    """application/octet-stream without .md/.txt extension must fail closed."""
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        content_type="application/octet-stream",
        source_filename="report.pdf",
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError):
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )


# ===================================================================
# Source artifact guard tests (P2: deleted/non-original/local-provider)
# ===================================================================


async def test_deleted_artifact_fail_closed(mat_env: asyncpg.Pool) -> None:
    """Soft-deleted source_artifact must fail closed (deleted_at IS NULL filter)."""
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        artifact_deleted_at=datetime.now(UTC),
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "source_artifact" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


async def test_non_original_upload_artifact_fail_closed(
    mat_env: asyncpg.Pool,
) -> None:
    """artifact_kind != 'original_upload' must fail closed."""
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        artifact_kind="extracted_text",
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "artifact_kind" in str(exc_info.value).lower()


async def test_local_storage_provider_fail_closed(mat_env: asyncpg.Pool) -> None:
    """storage_provider != 'oss' must fail closed."""
    await _seed_environment(
        mat_env,
        source_text=_STABLE_TEXT,
        storage_provider="local",
    )

    service = _build_service(mat_env)
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID, original_input_id=_ORIGINAL_INPUT_ID, source_artifact_id=_ARTIFACT_ID, user_id=_USER_ID, expected_generation=1
        )
    assert "storage_provider" in str(exc_info.value).lower()


# ===================================================================
# Multi-input / multi-artifact precision tests
# ===================================================================


_SECOND_INPUT_ID = UUID("00000000-0000-0000-0000-00000000d010")
_SECOND_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000d011")


async def _seed_second_input_artifact(
    pool: asyncpg.Pool,
    *,
    source_text: str,
    content_type: str,
    source_filename: str,
    object_key: str = "dev/test/second.md",
) -> None:
    """Seed a SECOND original_input + source_artifact on the existing record."""
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    source_ref_json = {
        "artifact_id": str(_SECOND_ARTIFACT_ID),
        "storage_provider": "oss",
        "bucket": "claread-dev",
        "object_key": object_key,
        "artifact_kind": "original_upload",
        "content_type": content_type,
        "source_filename": source_filename,
    }
    async with pool.acquire() as conn:
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
            _SECOND_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_text,
            source_ref_json,
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename,
                status, deleted_at
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    $5,
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    $6, $7, $8, $9, 'available', NULL)
            """,
            _SECOND_ARTIFACT_ID,
            _RECORD_ID,
            _SECOND_INPUT_ID,
            _USER_ID,
            object_key,
            content_type,
            len(source_text.encode("utf-8")),
            source_sha,
            source_filename,
        )


async def test_multi_input_materializes_only_specified_input_artifact(
    mat_env: asyncpg.Pool,
) -> None:
    """When a record has multiple inputs/artifacts, only the specified pair is materialized.

    Seeds two original_inputs + two source_artifacts on the same record.
    Calls materialize with input_A/artifact_A. Verifies:
    - stable doc is created from input_A's text (not input_B's)
    - input_B / artifact_B are untouched
    - exactly one stable doc / base / event exists
    """
    # Input A: txt, stable text
    await _seed_environment(mat_env, source_text=_STABLE_TEXT, content_type="text/plain")
    # Input B: markdown, different stable text
    await _seed_second_input_artifact(
        mat_env,
        source_text=_STABLE_MD,
        content_type="text/markdown",
        source_filename="second.md",
    )

    service = _build_service(mat_env)
    result = await service.materialize_extracted_artifact(
        reading_record_id=_RECORD_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        user_id=_USER_ID,
        expected_generation=1,
    )

    assert result.outcome == "stable_document_ready"
    assert result.original_input_id == _ORIGINAL_INPUT_ID
    assert result.source_artifact_id == _ARTIFACT_ID

    # Exactly one stable doc / base / event — no leakage from input_B
    assert await _count_stable_documents(mat_env) == 1
    assert await _count_reading_bases(mat_env) == 1
    assert await _count_article_ready_events(mat_env) == 1

    # Both original_inputs still exist (input_B untouched)
    async with mat_env.acquire() as conn:
        input_count = await conn.fetchval(
            "SELECT COUNT(*) FROM original_inputs WHERE reading_record_id = $1",
            _RECORD_ID,
        )
    assert input_count == 2


async def test_multi_input_artifact_mismatch_fail_closed(
    mat_env: asyncpg.Pool,
) -> None:
    """Passing input_A with artifact_B (bound to input_B) must fail closed.

    The precise WHERE clause (id + reading_record_id + original_input_id +
    user_id + deleted_at IS NULL) returns no row because artifact_B is bound
    to input_B, not input_A.
    """
    await _seed_environment(mat_env, source_text=_STABLE_TEXT, content_type="text/plain")
    await _seed_second_input_artifact(
        mat_env,
        source_text=_STABLE_MD,
        content_type="text/markdown",
        source_filename="second.md",
    )

    service = _build_service(mat_env)
    # input_A + artifact_B (mismatch) → fail closed
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID,
            original_input_id=_ORIGINAL_INPUT_ID,
            source_artifact_id=_SECOND_ARTIFACT_ID,
            user_id=_USER_ID,
            expected_generation=1,
        )
    assert "source_artifact" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()

    # No writes happened
    assert await _count_stable_documents(mat_env) == 0
    assert await _count_candidates(mat_env) == 0
    assert await _count_reading_bases(mat_env) == 0
    assert await _count_article_ready_events(mat_env) == 0

    # Record state unchanged
    record = await _fetch_record(mat_env)
    assert record["product_state"] == "processing"
    assert record["readiness_state"] == "submitted"


async def test_nonexistent_original_input_id_fail_closed(
    mat_env: asyncpg.Pool,
) -> None:
    """Passing a non-existent original_input_id must fail closed."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    service = _build_service(mat_env)
    fake_input_id = uuid4()
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID,
            original_input_id=fake_input_id,
            source_artifact_id=_ARTIFACT_ID,
            user_id=_USER_ID,
            expected_generation=1,
        )
    assert "original_input" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


async def test_nonexistent_source_artifact_id_fail_closed(
    mat_env: asyncpg.Pool,
) -> None:
    """Passing a non-existent source_artifact_id must fail closed."""
    await _seed_environment(mat_env, source_text=_STABLE_TEXT)

    service = _build_service(mat_env)
    fake_artifact_id = uuid4()
    with pytest.raises(ExtractedArtifactMaterializationError) as exc_info:
        await service.materialize_extracted_artifact(
            reading_record_id=_RECORD_ID,
            original_input_id=_ORIGINAL_INPUT_ID,
            source_artifact_id=fake_artifact_id,
            user_id=_USER_ID,
            expected_generation=1,
        )
    assert "source_artifact" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()
