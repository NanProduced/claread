"""R8 Commit 1 — structured review items persist & read back (real PG).

Isolated per-test schema (project convention; skipped when PostgreSQL is
unavailable). Asserts the R8 review-item contract end to end:

- candidate creation persists enriched ``quality_json.suitability.adaptations``
  (issue_id / tier / target_scope / source_anchor / anchor_hash / evidence),
- GET confirmed-source returns the structured ``content_check`` list,
- ``silent`` parser warnings never surface in ``content_check``.
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
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

SCHEMA_SQL = BASELINE_SQL

# Candidate trigger (fence) + a routine-tier content_check signal
# (footnote) + a silent parser warning (strikethrough). The unclosed
# fence must be the LAST block: the parser only flags fences that run
# to EOF.
_CANDIDATE_MD = """## Quarterly Review Notes

The committee reviewed the regional pilot results and recorded every
measured outcome before drafting the summary for the public review
session scheduled next month in the main hall near the river.[^1]

[^1]: The archival note keeps the additional context attached.

The closing paragraph explains how the committee weighed the evidence
and why the combined record supports the final recommendation for all
readers of the public summary document.

```python
print("unclosed fence body")
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
    schema_name = f"test_review_items_{uuid4().hex}"
    admin_conn: asyncpg.Connection | None = None
    try:
        admin_conn = await asyncpg.connect(DATABASE_URL)
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(SCHEMA_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        if admin_conn is not None:
            await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for review-items tests: {exc}")
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


async def _fetch_candidate_quality(pool: asyncpg.Pool, record_id: UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT quality_json
            FROM candidate_reading_documents
            WHERE reading_record_id = $1 AND status = 'ready'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            record_id,
        )
    # asyncpg decodes jsonb to a Python object already.
    assert isinstance(value, dict)
    return value


async def test_candidate_creation_persists_structured_review_items(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    service = CandidateDocumentCreationService(pool=db_env)
    result = await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=_CANDIDATE_MD,
        language="en",
    )
    assert result.status == "ready"

    quality = await _fetch_candidate_quality(db_env, result.reading_record_id)
    adaptations = quality["suitability"]["adaptations"]
    by_code = {item["code"]: item for item in adaptations}

    fence = by_code.get("has_unclosed_fence")
    assert fence is not None
    assert fence["classification"] == "content_check"
    assert fence["issue_id"] and len(fence["issue_id"]) == 16
    assert fence["tier"] == "attention"
    assert fence["target_scope"] == "range"
    assert fence["source_anchor"] is not None
    assert fence["anchor_hash"] and len(fence["anchor_hash"]) == 64
    normalized = _normalized(_CANDIDATE_MD)
    assert fence["source_anchor"]["start_utf16"] == normalized.index("```python")
    assert fence["evidence"]["excerpt"] == "```python"
    assert "source_media_coordinate" in fence

    footnote = by_code.get("footnote_reference")
    assert footnote is not None
    assert footnote["tier"] == "routine"
    assert footnote["target_scope"] == "range"
    assert footnote["issue_id"] and len(footnote["issue_id"]) == 16


async def test_review_items_are_stable_across_get_reads(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    service = CandidateDocumentCreationService(pool=db_env)
    result = await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=_CANDIDATE_MD,
        language="en",
    )

    get_service = ConfirmedSourceApplicationService(pool=db_env)
    first = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    second = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    fence_first = next(item for item in first.content_check if item["code"] == "has_unclosed_fence")
    fence_second = next(
        item for item in second.content_check if item["code"] == "has_unclosed_fence"
    )
    assert fence_first["issue_id"] == fence_second["issue_id"]
    assert fence_first["source_anchor"] == fence_second["source_anchor"]


async def test_silent_classification_never_surfaces_in_content_check(
    db_env: asyncpg.Pool,
) -> None:
    md_with_strikethrough = _CANDIDATE_MD.replace(
        "The committee reviewed the regional pilot results",
        "The committee ~~reviewed~~ the regional pilot results",
    )
    user_id = await _insert_user(db_env)
    service = CandidateDocumentCreationService(pool=db_env)
    result = await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=md_with_strikethrough,
        language="en",
    )

    get_service = ConfirmedSourceApplicationService(pool=db_env)
    loaded = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    assert all(item["classification"] != "silent" for item in loaded.content_check)
    assert all(
        item["classification"] in ("adaptation_notice",) for item in loaded.adaptation_notice
    )
