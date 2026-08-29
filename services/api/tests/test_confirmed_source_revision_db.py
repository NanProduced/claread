"""R8 Commit 2 — immutable confirmed-source revision history (real PG).

Isolated per-test schema (project convention). Asserts:
- every durable write persists an immutable snapshot (initial / save /
  restore),
- list returns metadata only; get returns the full body,
- restore writes a NEW monotonic revision from an immutable snapshot and
  never rewrites history,
- stale expected_revision fails closed (409) without touching the
  current body,
- ownership fail-closed (cross-user 404 collapse).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationService,
)
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceApplicationService,
    ConfirmedSourceConflictError,
)
from app.services.reader_orchestration.confirmed_source_revision_service import (
    ConfirmedSourceRevisionNotFoundError,
    ConfirmedSourceRevisionService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

SCHEMA_SQL = BASELINE_SQL

_CANDIDATE_MD = """## Quarterly Review Notes

The committee reviewed the regional pilot results and recorded every
measured outcome before drafting the summary for the public review
session scheduled next month in the main hall near the river.[^1]

[^1]: The archival note keeps the additional context attached.

The closing paragraph explains how the committee weighed the evidence
and why the combined record supports the final recommendation for all
readers of the public summary document.
"""

_EDITED_MD = """## Quarterly Review Notes ( Revised )

The committee reviewed the regional pilot results and recorded every
measured outcome before drafting the summary for the public review
session scheduled next month in the main hall near the river.[^1]

[^1]: The archival note keeps the additional context attached.

The closing paragraph explains how the committee weighed the edited
evidence and why the combined record supports the revised final
recommendation for all readers of the public summary document.
"""


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


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


@pytest.fixture
async def db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_revisions_{uuid4().hex}"
    admin_conn: asyncpg.Connection | None = None
    try:
        admin_conn = await asyncpg.connect(DATABASE_URL)
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(SCHEMA_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        if admin_conn is not None:
            await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for revision tests: {exc}")
    assert admin_conn is not None
    pool = await _make_pool(schema_name)
    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def _snapshot_rows(pool: asyncpg.Pool, record_id: UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT revision, snapshot_reason, edit_source, markdown_text,
                   content_sha256
            FROM confirmed_source_revisions
            WHERE reading_record_id = $1
            ORDER BY revision ASC
            """,
            record_id,
        )
    return [dict(row) for row in rows]


async def _create_candidate(pool: asyncpg.Pool, user_id: UUID, text: str):
    service = CandidateDocumentCreationService(pool=pool)
    return await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=text,
        language="en",
    )


async def test_initial_snapshot_persisted_on_candidate_creation(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    rows = await _snapshot_rows(db_env, result.reading_record_id)
    assert len(rows) == 1
    assert rows[0]["revision"] == 1
    assert rows[0]["snapshot_reason"] == "initial"
    assert rows[0]["markdown_text"] == _normalized(_CANDIDATE_MD)


async def test_save_snapshot_on_edit_and_list_metadata_without_body(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    service = ConfirmedSourceApplicationService(pool=db_env)
    updated = await service.update_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_EDITED_MD,
        edit_source="content_check",
    )
    assert updated.revision == 2

    rev_service = ConfirmedSourceRevisionService(pool=db_env)
    listing = await rev_service.list_revisions(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    assert [r["revision"] for r in listing.revisions] == [2, 1]
    assert listing.revisions[0]["snapshot_reason"] == "save"
    assert "markdown_text" not in listing.revisions[0]

    first = await rev_service.get_revision(
        record_id=result.reading_record_id,
        user_id=user_id,
        revision=1,
    )
    assert first.revision["markdown_text"] == _normalized(_CANDIDATE_MD)
    second = await rev_service.get_revision(
        record_id=result.reading_record_id,
        user_id=user_id,
        revision=2,
    )
    assert second.revision["markdown_text"] == _normalized(_EDITED_MD)


async def test_restore_creates_new_revision_and_never_rewrites_history(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    service = ConfirmedSourceApplicationService(pool=db_env)
    await service.update_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_EDITED_MD,
        edit_source="content_check",
    )

    rev_service = ConfirmedSourceRevisionService(pool=db_env)
    restored = await rev_service.restore_revision(
        record_id=result.reading_record_id,
        user_id=user_id,
        expected_revision=2,
        target_revision=1,
    )
    assert restored.revision == 3
    assert restored.restored_to == 1
    assert restored.markdown_text == _normalized(_CANDIDATE_MD)

    rows = await _snapshot_rows(db_env, result.reading_record_id)
    assert [r["revision"] for r in rows] == [1, 2, 3]
    assert rows[2]["snapshot_reason"] == "restore"
    # Immutability: history rows are byte-identical to their originals.
    assert rows[0]["markdown_text"] == _normalized(_CANDIDATE_MD)
    assert rows[1]["markdown_text"] == _normalized(_EDITED_MD)
    assert rows[2]["markdown_text"] == _normalized(_CANDIDATE_MD)


async def test_stale_restore_fails_closed_without_touching_current_body(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)
    service = ConfirmedSourceApplicationService(pool=db_env)
    await service.update_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_EDITED_MD,
        edit_source="content_check",
    )

    rev_service = ConfirmedSourceRevisionService(pool=db_env)
    with pytest.raises(ConfirmedSourceConflictError) as exc_info:
        await rev_service.restore_revision(
            record_id=result.reading_record_id,
            user_id=user_id,
            expected_revision=1,  # stale: current revision is 2
            target_revision=1,
        )
    assert exc_info.value.code == "stale_source_revision"
    assert exc_info.value.current_revision == 2

    async with db_env.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT revision, markdown_text
            FROM confirmed_source_documents
            WHERE reading_record_id = $1
            """,
            result.reading_record_id,
        )
    assert row["revision"] == 2
    assert row["markdown_text"] == _normalized(_EDITED_MD)
    rows = await _snapshot_rows(db_env, result.reading_record_id)
    assert len(rows) == 2  # no extra snapshot from the failed restore


async def test_revision_access_is_owner_scoped(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    other_user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    rev_service = ConfirmedSourceRevisionService(pool=db_env)
    with pytest.raises(ConfirmedSourceRevisionNotFoundError):
        await rev_service.list_revisions(
            record_id=result.reading_record_id,
            user_id=other_user_id,
        )
    with pytest.raises(ConfirmedSourceRevisionNotFoundError):
        await rev_service.get_revision(
            record_id=result.reading_record_id,
            user_id=other_user_id,
            revision=1,
        )
    with pytest.raises(ConfirmedSourceRevisionNotFoundError):
        await rev_service.restore_revision(
            record_id=result.reading_record_id,
            user_id=other_user_id,
            expected_revision=1,
            target_revision=1,
        )
