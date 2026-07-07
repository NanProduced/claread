"""Isolated schema setup for the reader baseline CLI.

Running the new orchestration chain against the shared ``public``
schema of a development database would pollute it with new rows
and risk collisions with other test runs. The smoke harness tests
already solve this by creating a per-test schema, loading the
reader baseline migration SQL into it, and pointing a fresh pool
at it via ``SET search_path``. This module exposes the same
primitive so the baseline CLI can run end-to-end without touching
the shared schema.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

import asyncpg

from app.database.connection import init_connection

REPO_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "infra" / "migrations"

# These are the migrations the reader-orchestration test suite
# applies to bring a fresh schema up to the same state the dev DB
# has. We deliberately exclude 0001+ utility migrations (such as
# grammar seed data) and only load the DDL we actually need.
REQUIRED_MIGRATION_NAMES: tuple[str, ...] = (
    "0001_initial_schema.sql",
    "0002_reader_record_anchor_columns.sql",
    "0003_reader_ask_dual_scope.sql",
    "0004_reader_document_blocks.sql",
    "0006_reader_ask_supplements_nullable_analysis_record_id.sql",
    "0008_reader_jobs_input_artifact_extraction.sql",
    "0009_reader_jobs_extracted_artifact_materialization.sql",
    "0010_reader_article_rag_index_state.sql",
    "0011_reader_display_title_generation.sql",
    "0012_reader_record_reading_strategy.sql",
    "0013_user_annotation_color_palette.sql",
    "0014_reader_runtime_spans.sql",
    "0015_layer_analysis_plans.sql",
    "0016_reader_runtime_spans_grammar_bundle_window.sql",
    "0017_reader_jobs_batch_path_job_types.sql",
)

# Whitelist pattern for the isolated schema name. We intentionally
# restrict to lowercase ASCII alphanumerics plus ``_`` to avoid any
# SQL-injection-shaped identifier (quotes, semicolons, dashes,
# unicode). The required ``reader_baseline_`` prefix makes the
# schema easy to spot in ``\\dn`` and prevents accidental
# destruction of an unrelated business schema that happened to
# share a similar name.
SCHEMA_NAME_PATTERN = re.compile(r"^reader_baseline_[a-z0-9_]{1,40}$")

# Schemas we never drop. The exact spelling matters: Postgres is
# case-folding for unquoted identifiers, so ``public`` matches
# ``Public`` after a fold.
RESERVED_SCHEMA_NAMES: frozenset[str] = frozenset(
    {
        "public",
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "pg_temp_1",
        "pg_temp_2",
        "pg_temp_3",
        "pg_temp_4",
        "pg_temp_5",
        "pg_temp_6",
        "pg_temp_7",
        "pg_temp_8",
        "pg_temp_9",
        "pg_temp_10",
    }
)


class UnsafeSchemaNameError(ValueError):
    """Raised when a requested schema name fails the safety check."""


def validate_schema_name(name: str) -> str:
    """Validate an isolated-schema name and return it unchanged.

    The check is purely lexical: it does not look at the live
    database, and it raises before any DDL is attempted. This is
    what protects callers from accidentally passing
    ``"public"`` / ``"pg_catalog"`` / ``"information_schema"`` or a
    SQL-injection-shaped string to ``isolated_schema``.

    The accepted form is ``reader_baseline_`` followed by 1-40
    lowercase ASCII alphanumerics or underscores. Anything else
    raises :class:`UnsafeSchemaNameError`.
    """
    if not isinstance(name, str):
        raise UnsafeSchemaNameError(
            f"schema name must be a string, got {type(name).__name__}"
        )
    if not name:
        raise UnsafeSchemaNameError("schema name must not be empty")
    # Reject before regex to keep the error message specific.
    if name.lower() in RESERVED_SCHEMA_NAMES:
        raise UnsafeSchemaNameError(
            f"refusing to drop or recreate reserved schema {name!r}"
        )
    if name.startswith("pg_") or name.startswith("reader_") and not name.startswith(
        "reader_baseline_"
    ):
        raise UnsafeSchemaNameError(
            f"schema name {name!r} is in a reserved namespace"
        )
    if not SCHEMA_NAME_PATTERN.match(name):
        raise UnsafeSchemaNameError(
            f"schema name {name!r} does not match the required pattern "
            f"{SCHEMA_NAME_PATTERN.pattern!r}"
        )
    return name


def auto_generated_schema_name() -> str:
    """Return a fresh schema name that always passes validation.

    The 12-hex-char suffix is what the test suite uses for transient
    schemas; it is always lowercase + digits, so the whitelist
    accepts it.
    """
    name = f"reader_baseline_{uuid4().hex[:12]}"
    validate_schema_name(name)
    return name


def _load_database_url() -> str:
    """Load the Postgres DSN the same way the smoke harness tests do.

    Looks first at ``DATABASE_URL`` in the environment, then at
    ``services/api/.env`` next to the API root, then falls back to
    the well-known dev default.
    """
    env = os.getenv("DATABASE_URL")
    if env:
        return env
    env_path = API_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip().lstrip("﻿")
            if not line or line.startswith("#") or not line.startswith("DATABASE_URL="):
                continue
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://claread:claread_dev@127.0.0.1:5432/claread"


def _build_baseline_sql() -> str:
    parts: list[str] = []
    for name in REQUIRED_MIGRATION_NAMES:
        path = MIGRATIONS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"required migration missing: {path}")
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def get_database_url() -> str:
    return _load_database_url()


@asynccontextmanager
async def isolated_schema(
    *,
    schema_name: str | None = None,
    keep: bool = False,
) -> AsyncIterator[tuple[asyncpg.Pool, UUID]]:
    """Yield a ``(pool, user_id)`` pair scoped to a fresh schema.

    The schema is dropped on exit unless ``keep=True``. The pool
    uses ``SET search_path TO <schema>, public`` so cross-schema
    references (such as the ``pg_catalog`` JSONB codec) keep
    working. A new user is inserted into ``users`` so the smoke
    harness has a non-null ``user_id`` to attach the record to.

    Safety: the resolved schema name is run through
    :func:`validate_schema_name` *before* any DDL is executed, so a
    caller mistake (such as ``--schema-name public``) cannot reach
    a ``DROP SCHEMA ... CASCADE`` statement.
    """
    db_url = _load_database_url()
    schema = validate_schema_name(schema_name or auto_generated_schema_name())
    admin = await asyncpg.connect(db_url)
    try:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}", public')
        baseline_sql = _build_baseline_sql()
        await admin.execute(baseline_sql)
        user_id = await admin.fetchval(
            f'INSERT INTO "{schema}".users DEFAULT VALUES RETURNING id'
        )
    finally:
        await admin.close()

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema}", public')

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )
    try:
        yield pool, UUID(str(user_id))
    finally:
        await pool.close()
        if not keep:
            # Re-validate the name on the cleanup path too. This
            # guards against a future refactor that might somehow
            # mutate ``schema`` between enter and exit; today it
            # is the same string we validated up front.
            validate_schema_name(schema)
            cleanup = await asyncpg.connect(db_url)
            try:
                await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            finally:
                await cleanup.close()


__all__ = [
    "isolated_schema",
    "get_database_url",
    "REQUIRED_MIGRATION_NAMES",
    "SCHEMA_NAME_PATTERN",
    "RESERVED_SCHEMA_NAMES",
    "UnsafeSchemaNameError",
    "validate_schema_name",
    "auto_generated_schema_name",
]
