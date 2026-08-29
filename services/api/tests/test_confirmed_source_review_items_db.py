"""R8 review items — persist / read back / read-time compatibility (real PG).

Isolated per-test schema (project convention; skipped when PostgreSQL is
unavailable). Asserts:

- candidate creation persists enriched ``quality_json.suitability.adaptations``
  (issue_id / tier / target_scope / source_anchor / anchor_hash / evidence),
- GET confirmed-source returns the structured ``content_check`` list,
- ``silent`` parser warnings never surface in ``content_check``,
- OLD three-field persisted shapes ({code, message, classification}) are
  deterministically UPGRADED AT READ TIME (never written back), with
  current-namespace issue_ids and exact UTF-16 anchors from the current
  confirmed-source markdown; un-anchorable items degrade to document
  scope; illegal unknown shapes fail closed,
- same-hash PUT (idempotent_noop) returns the existing full review
  surface identical to the following GET, with zero DB changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationService,
)
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceApplicationError,
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


async def _rewrite_candidate_quality_to_old_shape(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> None:
    """Strip the review-item fields so persisted items are exactly the old
    three-field shape ({code, message, classification})."""
    quality = await _fetch_candidate_quality(pool, record_id)
    adaptations = quality["suitability"]["adaptations"]
    quality["suitability"]["adaptations"] = [
        {key: item[key] for key in ("code", "message", "classification")} for item in adaptations
    ]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET quality_json = $2
            WHERE reading_record_id = $1 AND status = 'ready'
            """,
            record_id,
            jsonb_param(quality),
        )


async def _fetch_source_revision(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT revision
                FROM confirmed_source_documents
                WHERE reading_record_id = $1
                """,
                record_id,
            )
        )


async def _fetch_revision_snapshot_count(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM confirmed_source_revisions
                WHERE reading_record_id = $1
                """,
                record_id,
            )
        )


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
    assert fence["evidence"]["excerpt_text"] == "```python"
    assert "source_media_coordinate" in fence

    footnote = by_code.get("footnote_reference")
    assert footnote is not None
    assert footnote["tier"] == "routine"
    # No precisely derivable anchor -> honest document-scope degrade.
    assert footnote["target_scope"] == "document"
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


# ---------------------------------------------------------------------------
# read-time compatibility for old persisted shapes
# ---------------------------------------------------------------------------


async def test_old_three_field_shape_upgraded_on_read_without_write_back(
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
    # Simulate a pre-R8 candidate: strip the review-item fields.
    await _rewrite_candidate_quality_to_old_shape(db_env, result.reading_record_id)

    get_service = ConfirmedSourceApplicationService(pool=db_env)
    loaded = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )

    fence = next(item for item in loaded.content_check if item["code"] == "has_unclosed_fence")
    assert fence["issue_id"] and len(fence["issue_id"]) == 16
    assert fence["tier"] == "attention"
    assert fence["target_scope"] == "range"
    assert fence["source_anchor"] is not None
    assert fence["anchor_hash"] and len(fence["anchor_hash"]) == 64
    normalized = _normalized(_CANDIDATE_MD)
    assert fence["source_anchor"]["start_utf16"] == normalized.index("```python")
    assert fence["evidence"]["excerpt_text"] == "```python"

    footnote = next(item for item in loaded.content_check if item["code"] == "footnote_reference")
    assert footnote["tier"] == "routine"
    assert footnote["target_scope"] == "document"
    assert footnote["source_anchor"] is None
    assert footnote["anchor_hash"] is None

    # Read-only: the persisted shape is NOT rewritten.
    quality = await _fetch_candidate_quality(db_env, result.reading_record_id)
    persisted = quality["suitability"]["adaptations"]
    assert all(set(item) == {"code", "message", "classification"} for item in persisted)


async def test_complete_new_shape_is_never_rewritten_on_read(
    db_env: asyncpg.Pool,
) -> None:
    """Complete persisted items pass through untouched (no re-enrichment):
    their issue_id / anchor / hash are returned verbatim."""
    user_id = await _insert_user(db_env)
    service = CandidateDocumentCreationService(pool=db_env)
    result = await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=_CANDIDATE_MD,
        language="en",
    )
    sentinel_issue_id = "f0e1d2c3b4a59687"
    sentinel_hash = "e" * 64
    quality = await _fetch_candidate_quality(db_env, result.reading_record_id)
    for item in quality["suitability"]["adaptations"]:
        if item["code"] == "has_unclosed_fence":
            item["issue_id"] = sentinel_issue_id
            item["anchor_hash"] = sentinel_hash
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET quality_json = $2
            WHERE reading_record_id = $1 AND status = 'ready'
            """,
            result.reading_record_id,
            jsonb_param(quality),
        )

    get_service = ConfirmedSourceApplicationService(pool=db_env)
    loaded = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    fence = next(item for item in loaded.content_check if item["code"] == "has_unclosed_fence")
    assert fence["issue_id"] == sentinel_issue_id
    assert fence["anchor_hash"] == sentinel_hash
    assert fence["source_anchor"] is not None


async def test_illegal_content_check_shape_fails_closed(
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
    quality = await _fetch_candidate_quality(db_env, result.reading_record_id)
    quality["suitability"]["adaptations"] = [
        {
            "code": "has_unclosed_fence",
            "message": "m",
            "classification": "content_check",
            "fabricated": True,
        }
    ]
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET quality_json = $2
            WHERE reading_record_id = $1 AND status = 'ready'
            """,
            result.reading_record_id,
            jsonb_param(quality),
        )

    get_service = ConfirmedSourceApplicationService(pool=db_env)
    with pytest.raises(ConfirmedSourceApplicationError):
        await get_service.get_confirmed_source(
            record_id=result.reading_record_id,
            user_id=user_id,
        )


# ---------------------------------------------------------------------------
# idempotent_noop returns the existing full review surface
# ---------------------------------------------------------------------------


async def test_idempotent_noop_returns_existing_review_data_with_zero_db_change(
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

    updated = await get_service.update_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_normalized(_CANDIDATE_MD),  # same hash -> no-op
        edit_source="content_check",
    )
    assert updated.outcome == "idempotent_noop"
    assert updated.revision == 1
    assert updated.quality != {}
    assert updated.content_check != []
    assert updated.adaptation_notice == []

    # Identical to the immediately-following GET review surface.
    loaded = await get_service.get_confirmed_source(
        record_id=result.reading_record_id,
        user_id=user_id,
    )
    assert updated.quality == loaded.quality
    assert updated.adaptation_notice == loaded.adaptation_notice
    assert updated.content_check == loaded.content_check

    # Zero DB change: same revision, no new snapshot, candidate not
    # superseded, ready candidate still exactly one.
    assert await _fetch_source_revision(db_env, result.reading_record_id) == 1
    assert await _fetch_revision_snapshot_count(db_env, result.reading_record_id) == 1
    async with db_env.acquire() as conn:
        ready_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM candidate_reading_documents
            WHERE reading_record_id = $1 AND status = 'ready'
            """,
            result.reading_record_id,
        )
        superseded_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM candidate_reading_documents
            WHERE reading_record_id = $1 AND status = 'superseded'
            """,
            result.reading_record_id,
        )
    assert ready_count == 1
    assert superseded_count == 0
