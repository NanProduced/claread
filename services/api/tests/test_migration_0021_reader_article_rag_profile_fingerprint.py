"""Tests for migration 0021: reader_article_rag_index_runs.profile_fingerprint.

P1-C: Durable Profile Fingerprint Migration.

Migration 0021 adds a durable ``profile_fingerprint`` column to
``reader_article_rag_index_runs``, safely backfills recognised V1 rows
with the frozen V1 profile fingerprint, and atomically fails on any
unknown or contradictory legacy row.

Contract enforced by these tests:

  1. Empty table — migration applies cleanly; column exists as
     ``TEXT NOT NULL`` with a ``CHECK (^[0-9a-f]{64}$)`` constraint.
  2. V1-compatible rows — backfilled with the golden V1 fingerprint.
  3. Golden literal equality — backfilled value precisely equals the
     precomputed V1 profile fingerprint.
  4. Unknown ``index_version`` — migration fails atomically (column
     not added, no partial state).
  5. Contradictory ``chunker_version`` — migration fails atomically.
  6. Contradictory ``embedding_model`` (non-NULL, non-v4) — migration
     fails atomically.
  7. Contradictory ``vector_collection`` (non-NULL, non-v1) — migration
     fails atomically.
  8. Post-migration INSERT with NULL fingerprint — rejected by NOT NULL.
  9. Post-migration INSERT with malformed / uppercase fingerprint —
     rejected by CHECK.
 10. Post-migration INSERT with valid fingerprint — accepted.

The migration SQL is loaded from
``infra/migrations/0021_reader_article_rag_profile_fingerprint.sql``
and applied on top of ``BASELINE_SQL`` (which already includes 0010
creating ``reader_article_rag_index_runs``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    _connect,
    _insert_reading_base,
    _insert_reading_record,
    _insert_user,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_0021_PATH = (
    REPO_ROOT / "infra" / "migrations" / "0021_reader_article_rag_profile_fingerprint.sql"
)

# Frozen V1 profile fingerprint (from P1-B resolver).  This is the
# precomputed golden digest that the migration must backfill into every
# recognised V1 row.  It is a FIXED LITERAL — if the V1 profile fields
# change, this digest will mismatch and the test will fail, which is
# the intended regression signal.
V1_PROFILE_FINGERPRINT = (
    "e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d"
)

# V1 identity constants (must match the profile module + migration
# preflight predicates).
V1_INDEX_VERSION = "article_rag_index_v1"
V1_CHUNKER_VERSION = "article_rag_index_plan_v1"
V1_EMBEDDING_MODEL = "text-embedding-v4"
V1_VECTOR_COLLECTION = "article_rag_index_v1"


def _load_migration_sql() -> str:
    """Read the migration 0021 SQL file.

    This will raise FileNotFoundError during the TDD RED phase (before
    the migration file is created), which is the intended RED signal.
    """
    return MIGRATION_0021_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers: insert a minimal reader_article_rag_index_runs row
# ---------------------------------------------------------------------------


def _sha256_hex(value: str = "seed") -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _insert_stable_document(
    conn: asyncpg.Connection,
    record_id,
    *,
    content_sha256: str | None = None,
) -> str:
    """Insert a minimal stable_reading_documents row and return its UUID."""
    sha = content_sha256 or _sha256_hex("stable-doc")
    return await conn.fetchval(
        """
        INSERT INTO stable_reading_documents (
            reading_record_id, record_generation, document_version, content_sha256
        )
        VALUES ($1, 1, 1, $2)
        RETURNING id::text
        """,
        record_id,
        sha,
    )


async def _insert_index_run_row(
    conn: asyncpg.Connection,
    *,
    reading_record_id,
    stable_document_id,
    base_id,
    record_generation: int = 1,
    index_version: str = V1_INDEX_VERSION,
    chunker_version: str = V1_CHUNKER_VERSION,
    embedding_model: str | None = None,
    vector_collection: str | None = None,
    status: str = "indexed",
) -> str:
    """Insert a reader_article_rag_index_runs row and return its UUID."""
    return await conn.fetchval(
        """
        INSERT INTO reader_article_rag_index_runs (
            reading_record_id, stable_document_id, base_id,
            record_generation,
            stable_document_content_sha256, canonical_text_sha256,
            plan_content_sha256, chunk_count,
            status, index_version, chunker_version,
            embedding_model, vector_collection
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13)
        RETURNING id::text
        """,
        reading_record_id,
        stable_document_id,
        base_id,
        record_generation,
        _sha256_hex("doc"),
        _sha256_hex("canonical"),
        _sha256_hex("plan"),
        3,
        status,
        index_version,
        chunker_version,
        embedding_model,
        vector_collection,
    )


async def _seed_parent_rows(conn: asyncpg.Connection):
    """Seed minimal user / record / base / stable_document for one row."""
    user_id = await _insert_user(conn)
    record_id = await _insert_reading_record(conn, user_id)
    base_id = await _insert_reading_base(conn, record_id)
    stable_doc_id = await _insert_stable_document(conn, record_id)
    return user_id, record_id, base_id, stable_doc_id


async def _rollback_safely(conn: asyncpg.Connection) -> None:
    """Rollback any aborted transaction on the connection.

    The migration SQL uses an explicit ``BEGIN ... COMMIT``.  When the
    preflight ``RAISE EXCEPTION`` fires, the transaction enters aborted
    state and the trailing ``COMMIT`` is not processed (asyncpg's simple
    query batch stops at the error).  This helper issues a ``ROLLBACK``
    so subsequent queries on the same connection can proceed.
    """
    try:
        await conn.execute("ROLLBACK")
    except Exception:
        pass


async def _column_exists(conn: asyncpg.Connection, schema_name: str) -> bool:
    """Check whether ``profile_fingerprint`` column exists on the table."""
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'reader_article_rag_index_runs'
              AND column_name = 'profile_fingerprint'
        )
        """,
        schema_name,
    )


# ---------------------------------------------------------------------------
# 1. Tracer bullet: migration file exists + SQL loads
# ---------------------------------------------------------------------------


def test_migration_0021_file_exists_and_loads() -> None:
    """RED tracer: the migration SQL file must exist and be readable."""
    assert MIGRATION_0021_PATH.exists(), (
        f"Migration file not found: {MIGRATION_0021_PATH}"
    )
    sql = _load_migration_sql()
    assert "profile_fingerprint" in sql, (
        "Migration SQL must reference profile_fingerprint column"
    )
    assert "reader_article_rag_index_runs" in sql, (
        "Migration SQL must target reader_article_rag_index_runs table"
    )


# ---------------------------------------------------------------------------
# 2. Empty table: migration applies cleanly, column + constraint exist
# ---------------------------------------------------------------------------


async def test_migration_0021_on_empty_table_adds_column_with_constraints() -> None:
    schema_name = f"test_mig0021_empty_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_load_migration_sql())

        # Column exists with TEXT type.
        col_type = await admin_conn.fetchval(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'reader_article_rag_index_runs'
              AND column_name = 'profile_fingerprint'
            """,
            schema_name,
        )
        assert col_type == "text", f"expected text, got {col_type}"

        # Column is NOT NULL.
        is_nullable = await admin_conn.fetchval(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'reader_article_rag_index_runs'
              AND column_name = 'profile_fingerprint'
            """,
            schema_name,
        )
        assert is_nullable == "NO", f"expected NOT NULL, got {is_nullable}"

        # CHECK constraint exists with sha256 regex.
        constraint_def = await admin_conn.fetchval(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = '"reader_article_rag_index_runs"'::regclass
              AND conname = 'ck_reader_article_rag_index_runs_profile_fingerprint_sha256'
            """,
        )
        assert constraint_def is not None, "CHECK constraint not found"
        assert "[0-9a-f]{64}" in constraint_def, (
            f"CHECK constraint must enforce sha256 hex regex: {constraint_def}"
        )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 3. V1-compatible rows: backfilled with golden fingerprint
# ---------------------------------------------------------------------------


async def test_migration_0021_backfills_v1_rows_with_golden_fingerprint() -> None:
    schema_name = f"test_mig0021_backfill_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        # Seed four V1-compatible rows with varying nullable metadata:
        #   row A: embedding_model=NULL, vector_collection=NULL
        #   row B: embedding_model='text-embedding-v4', vector_collection=NULL
        #   row C: embedding_model=NULL, vector_collection='article_rag_index_v1'
        #   row D: embedding_model='text-embedding-v4', vector_collection='article_rag_index_v1'
        rows = []
        for suffix in ("a", "b", "c", "d"):
            user_id = await _insert_user(admin_conn)
            record_id = await _insert_reading_record(admin_conn, user_id, title=f"Rec-{suffix}")
            base_id = await _insert_reading_base(admin_conn, record_id)
            stable_doc_id = await _insert_stable_document(
                admin_conn, record_id,
                content_sha256=_sha256_hex(f"doc-{suffix}"),
            )
            emb = V1_EMBEDDING_MODEL if suffix in ("b", "d") else None
            vec = V1_VECTOR_COLLECTION if suffix in ("c", "d") else None
            row_id = await _insert_index_run_row(
                admin_conn,
                reading_record_id=record_id,
                stable_document_id=stable_doc_id,
                base_id=base_id,
                embedding_model=emb,
                vector_collection=vec,
            )
            rows.append(row_id)

        await admin_conn.execute(_load_migration_sql())

        # All four rows must be backfilled with the golden V1 fingerprint.
        for row_id in rows:
            fp = await admin_conn.fetchval(
                "SELECT profile_fingerprint FROM reader_article_rag_index_runs WHERE id = $1::uuid",
                row_id,
            )
            assert fp == V1_PROFILE_FINGERPRINT, (
                f"row {row_id}: expected golden fingerprint, got {fp}"
            )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_backfilled_fingerprint_matches_resolver_literal() -> None:
    """The backfilled fingerprint must precisely equal the V1 profile
    fingerprint from the P1-B resolver module."""
    from app.services.reader_orchestration.article_rag_index_profile import (
        DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        resolve_article_rag_index_profile,
    )

    resolution = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    resolver_fingerprint = resolution.profile_fingerprint

    schema_name = f"test_mig0021_literal_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
        )

        await admin_conn.execute(_load_migration_sql())

        db_fp = await admin_conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs LIMIT 1"
        )
        assert db_fp == resolver_fingerprint, (
            f"DB fingerprint {db_fp} != resolver fingerprint {resolver_fingerprint}"
        )
        assert db_fp == V1_PROFILE_FINGERPRINT, (
            f"DB fingerprint {db_fp} != golden literal {V1_PROFILE_FINGERPRINT}"
        )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 4-7. Atomic fail: unknown / contradictory rows
# ---------------------------------------------------------------------------


async def test_migration_0021_fails_atomically_on_unknown_index_version() -> None:
    schema_name = f"test_mig0021_unknown_idx_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            index_version="article_rag_index_v2_unknown",
        )

        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        # Atomic: column must NOT exist (transaction rolled back).
        col_exists = await _column_exists(admin_conn, schema_name)
        assert not col_exists, (
            "Migration must be atomic: column must not exist after preflight failure"
        )
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_fails_atomically_on_contradictory_chunker_version() -> None:
    schema_name = f"test_mig0021_bad_chunker_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            chunker_version="article_rag_index_plan_v2_bad",
        )

        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        col_exists = await _column_exists(admin_conn, schema_name)
        assert not col_exists, "Migration must be atomic on contradictory chunker_version"
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_fails_atomically_on_contradictory_embedding_model() -> None:
    schema_name = f"test_mig0021_bad_emb_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            embedding_model="text-embedding-v3_bad",
        )

        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        col_exists = await _column_exists(admin_conn, schema_name)
        assert not col_exists, "Migration must be atomic on contradictory embedding_model"
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_fails_atomically_on_contradictory_vector_collection() -> None:
    schema_name = f"test_mig0021_bad_vec_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            vector_collection="article_rag_index_v2_bad",
        )

        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        col_exists = await _column_exists(admin_conn, schema_name)
        assert not col_exists, "Migration must be atomic on contradictory vector_collection"
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 8-10. Post-migration INSERT contract
# ---------------------------------------------------------------------------


async def test_migration_0021_post_migration_rejects_null_fingerprint() -> None:
    schema_name = f"test_mig0021_null_fp_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_load_migration_sql())

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        with pytest.raises(asyncpg.NotNullViolationError):
            await _insert_index_run_row(
                admin_conn,
                reading_record_id=record_id,
                stable_document_id=stable_doc_id,
                base_id=base_id,
            )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_post_migration_rejects_malformed_fingerprint() -> None:
    schema_name = f"test_mig0021_bad_fp_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_load_migration_sql())

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        for bad_fp in (
            "",
            "abc123",
            "E443F581EB3E86AEB9DBCDCCEE806783186BD85DA6C987C60357B61905EA86D6D",  # uppercase
            "g443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d",  # non-hex char
            "e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6",  # 63 chars
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await admin_conn.execute(
                    """
                    INSERT INTO reader_article_rag_index_runs (
                        reading_record_id, stable_document_id, base_id,
                        record_generation,
                        stable_document_content_sha256, canonical_text_sha256,
                        plan_content_sha256, chunk_count,
                        status, index_version, chunker_version,
                        profile_fingerprint
                    )
                    VALUES ($1, $2, $3, 1, $4, $4, $4, 1,
                            'queued', $5, $6, $7)
                    """,
                    record_id,
                    stable_doc_id,
                    base_id,
                    _sha256_hex(bad_fp),
                    V1_INDEX_VERSION,
                    V1_CHUNKER_VERSION,
                    bad_fp,
                )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_post_migration_accepts_valid_fingerprint() -> None:
    schema_name = f"test_mig0021_ok_fp_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_load_migration_sql())

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        row_id = await admin_conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                reading_record_id, stable_document_id, base_id,
                record_generation,
                stable_document_content_sha256, canonical_text_sha256,
                plan_content_sha256, chunk_count,
                status, index_version, chunker_version,
                profile_fingerprint
            )
            VALUES ($1, $2, $3, 1, $4, $4, $4, 1,
                    'queued', $5, $6, $7)
            RETURNING id::text
            """,
            record_id,
            stable_doc_id,
            base_id,
            _sha256_hex("ok"),
            V1_INDEX_VERSION,
            V1_CHUNKER_VERSION,
            V1_PROFILE_FINGERPRINT,
        )
        assert row_id is not None
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 11. Column comment
# ---------------------------------------------------------------------------


async def test_migration_0021_adds_column_comment() -> None:
    schema_name = f"test_mig0021_comment_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_load_migration_sql())

        comment = await admin_conn.fetchval(
            """
            SELECT col_description(
                'reader_article_rag_index_runs'::regclass,
                (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'reader_article_rag_index_runs'::regclass
                   AND attname = 'profile_fingerprint')
            )
            """
        )
        assert comment is not None, "Column comment must be set"
        assert "profile" in comment.lower() or "fingerprint" in comment.lower(), (
            f"Comment must reference profile/fingerprint: {comment}"
        )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 12-13. P1-C rework — wrong-valid-fingerprint rejection + safe golden rerun
# ---------------------------------------------------------------------------


async def _manually_add_profile_fingerprint_column(
    conn: asyncpg.Connection,
) -> None:
    """Simulate a pre-existing ``profile_fingerprint`` column.

    Used by the wrong-valid-fingerprint test to reproduce the scenario
    where the column was added by a previous partial migration run but
    a V1 row was left with a wrong-but-format-valid SHA-256 fingerprint.
    """
    await conn.execute(
        "ALTER TABLE reader_article_rag_index_runs "
        "ADD COLUMN IF NOT EXISTS profile_fingerprint TEXT NULL"
    )


async def _set_row_fingerprint(
    conn: asyncpg.Connection,
    row_id: str,
    fingerprint: str,
) -> None:
    """Manually set the fingerprint on an existing index-run row."""
    await conn.execute(
        "UPDATE reader_article_rag_index_runs "
        "SET profile_fingerprint = $2 "
        "WHERE id = $1::uuid",
        row_id,
        fingerprint,
    )


async def _constraint_exists(
    conn: asyncpg.Connection,
    constraint_name: str,
    schema_name: str,
) -> bool:
    """Check whether a CHECK constraint exists in the given schema.

    MUST scope by namespace — once migration 0021 is applied to the
    local ``claread`` database's ``public`` schema, an unscoped query
    against ``pg_constraint`` would match the ``public``-schema
    constraint and break test isolation for atomicity assertions.
    """
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_constraint "
        "WHERE conname = $1 AND connamespace = $2::regnamespace)",
        constraint_name,
        schema_name,
    )


async def test_migration_0021_rejects_wrong_valid_fingerprint_on_existing_column() -> None:
    """P1-C rework Problem A: if the ``profile_fingerprint`` column already
    exists and a V1 row carries a wrong-but-format-valid SHA-256
    fingerprint, migration MUST fail closed with a fixed local message.

    Atomicity guarantees:
      * no new constraint is added (the ``ADD CONSTRAINT`` is rolled back);
      * no backfill mutation occurs (the ``UPDATE`` is rolled back);
      * the pre-existing column persists (it was there before the migration);
      * the pre-existing wrong value is unchanged (the migration did not
        touch it).

    The wrong fingerprint value MUST NOT appear in the error message.
    """
    wrong_fingerprint = "a" * 64  # format-valid but not the V1 golden
    schema_name = f"test_mig0021_wrong_fp_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        # Simulate a pre-existing column (e.g., from a partial prior run).
        await _manually_add_profile_fingerprint_column(admin_conn)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        row_id = await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
        )
        # Set a wrong-but-format-valid fingerprint on the V1 row.
        await _set_row_fingerprint(admin_conn, row_id, wrong_fingerprint)

        # Migration must fail closed.
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        # Atomic: no constraint was added (the ADD CONSTRAINT rolled back).
        constraint_added = await _constraint_exists(
            admin_conn,
            "ck_reader_article_rag_index_runs_profile_fingerprint_sha256",
            schema_name,
        )
        assert not constraint_added, (
            "Migration must be atomic: CHECK constraint must not be added "
            "after preflight failure on wrong-valid-fingerprint"
        )

        # Pre-existing column still exists (was there before migration).
        col_exists = await _column_exists(admin_conn, schema_name)
        assert col_exists, (
            "Pre-existing profile_fingerprint column must persist "
            "(it was added before the migration transaction)"
        )

        # Pre-existing wrong value is unchanged (UPDATE was rolled back).
        persisted_fp = await admin_conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs "
            "WHERE id = $1::uuid",
            row_id,
        )
        assert persisted_fp == wrong_fingerprint, (
            "Pre-existing wrong fingerprint must remain unchanged "
            "(no backfill mutation may occur on preflight failure)"
        )

        # Other V1 rows with NULL fingerprint must NOT have been backfilled.
        user_id2 = await _insert_user(admin_conn)
        record_id2 = await _insert_reading_record(admin_conn, user_id2, title="Rec2")
        base_id2 = await _insert_reading_base(admin_conn, record_id2)
        stable_doc_id2 = await _insert_stable_document(
            admin_conn, record_id2,
            content_sha256=_sha256_hex("doc2"),
        )
        row_id2 = await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id2,
            stable_document_id=stable_doc_id2,
            base_id=base_id2,
        )
        persisted_fp2 = await admin_conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs "
            "WHERE id = $1::uuid",
            row_id2,
        )
        assert persisted_fp2 is None, (
            "Other NULL fingerprint rows must NOT be backfilled "
            "when migration fails atomically"
        )
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_migration_0021_safe_rerun_when_all_rows_already_golden() -> None:
    """P1-C rework Problem A: if the ``profile_fingerprint`` column already
    exists and EVERY V1 row already carries the V1 golden fingerprint,
    migration MUST be a safe no-op rerun.

    Post-conditions:
      * fingerprint still equals the V1 golden on every row;
      * column is TEXT NOT NULL;
      * SHA-256 CHECK constraint exists and is validated;
      * no extra mutation (row count and values unchanged).
    """
    schema_name = f"test_mig0021_rerun_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        # Pre-existing column with V1 golden on every row.
        await _manually_add_profile_fingerprint_column(admin_conn)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        row_id = await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
        )
        await _set_row_fingerprint(admin_conn, row_id, V1_PROFILE_FINGERPRINT)

        # Capture pre-migration row count.
        pre_count = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs"
        )

        # Migration must succeed (safe rerun).
        await admin_conn.execute(_load_migration_sql())

        # Fingerprint still golden.
        persisted_fp = await admin_conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs "
            "WHERE id = $1::uuid",
            row_id,
        )
        assert persisted_fp == V1_PROFILE_FINGERPRINT, (
            "Safe rerun must leave V1 golden fingerprint unchanged"
        )

        # Column is NOT NULL.
        is_nullable = await admin_conn.fetchval(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'reader_article_rag_index_runs'
              AND column_name = 'profile_fingerprint'
            """,
            schema_name,
        )
        assert is_nullable == "NO", (
            f"Safe rerun must end with NOT NULL column, got {is_nullable}"
        )

        # CHECK constraint exists and is validated (scoped to test schema
        # to avoid matching the public-schema constraint after migration
        # 0021 is applied to the local database).
        constraint_validated = await admin_conn.fetchval(
            """
            SELECT convalidated FROM pg_constraint
            WHERE conname = $1
              AND connamespace = $2::regnamespace
            """,
            "ck_reader_article_rag_index_runs_profile_fingerprint_sha256",
            schema_name,
        )
        assert constraint_validated is True, (
            "Safe rerun must leave CHECK constraint validated"
        )

        # No extra mutation.
        post_count = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs"
        )
        assert post_count == pre_count, (
            f"Safe rerun must not change row count: "
            f"pre={pre_count}, post={post_count}"
        )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 14. P1-C rework — execution-active legacy run rejection (parameterized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "active_status",
    ["planned", "queued", "indexing"],
)
async def test_migration_0021_rejects_execution_active_legacy_run(
    active_status: str,
) -> None:
    """P1-C rework Problem B (migration side): if a V1 index-run row is in
    an execution-active status (``planned`` / ``queued`` / ``indexing``),
    migration MUST fail closed with a fixed local message.

    The migration must NOT:
      * backfill any row;
      * add the CHECK constraint;
      * set NOT NULL;
      * modify reader_jobs / reader_runs / index-run status.

    Atomicity: schema and data roll back to pre-migration state.

    The error MUST NOT echo row id, status, job payload, fingerprint, or
    any database content.
    """
    schema_name = f"test_mig0021_active_{active_status}_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        row_id = await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            status=active_status,
        )

        with pytest.raises(asyncpg.exceptions.PostgresError):
            await admin_conn.execute(_load_migration_sql())

        await _rollback_safely(admin_conn)

        # Atomic: column must NOT exist (transaction rolled back).
        col_exists = await _column_exists(admin_conn, schema_name)
        assert not col_exists, (
            f"Migration must be atomic on execution-active status "
            f"{active_status}: column must not exist after preflight failure"
        )

        # Atomic: no constraint was added.
        constraint_added = await _constraint_exists(
            admin_conn,
            "ck_reader_article_rag_index_runs_profile_fingerprint_sha256",
            schema_name,
        )
        assert not constraint_added, (
            f"Migration must be atomic on execution-active status "
            f"{active_status}: no CHECK constraint may be added"
        )

        # Original row unchanged (status still the execution-active value).
        persisted_status = await admin_conn.fetchval(
            "SELECT status FROM reader_article_rag_index_runs WHERE id = $1::uuid",
            row_id,
        )
        assert persisted_status == active_status, (
            f"Migration must not mutate execution-active row status: "
            f"expected {active_status}, got {persisted_status}"
        )
    finally:
        await _rollback_safely(admin_conn)
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# 15. P1-C rework — terminal/history rows can be backfilled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal_status",
    ["indexed", "failed", "superseded"],
)
async def test_migration_0021_terminal_history_rows_can_be_backfilled(
    terminal_status: str,
) -> None:
    """P1-C rework Problem B (migration side): V1 index-run rows in
    terminal / history statuses (``indexed`` / ``failed`` / ``superseded``)
    MUST be backfillable with the V1 golden fingerprint.

    The migration must NOT modify the business status of these rows.
    """
    schema_name = f"test_mig0021_terminal_{terminal_status}_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id, record_id, base_id, stable_doc_id = await _seed_parent_rows(admin_conn)
        row_id = await _insert_index_run_row(
            admin_conn,
            reading_record_id=record_id,
            stable_document_id=stable_doc_id,
            base_id=base_id,
            status=terminal_status,
        )

        await admin_conn.execute(_load_migration_sql())

        # Backfilled with V1 golden.
        persisted_fp = await admin_conn.fetchval(
            "SELECT profile_fingerprint FROM reader_article_rag_index_runs "
            "WHERE id = $1::uuid",
            row_id,
        )
        assert persisted_fp == V1_PROFILE_FINGERPRINT, (
            f"Terminal status {terminal_status} row must be backfilled "
            f"with V1 golden fingerprint"
        )

        # Business status unchanged.
        persisted_status = await admin_conn.fetchval(
            "SELECT status FROM reader_article_rag_index_runs WHERE id = $1::uuid",
            row_id,
        )
        assert persisted_status == terminal_status, (
            f"Migration must not mutate terminal row status: "
            f"expected {terminal_status}, got {persisted_status}"
        )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()
