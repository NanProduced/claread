"""Reading Record Identity Projection — DB-backed route-level tests.

These tests exercise the full ``GET /reader/records`` list endpoint with
real database rows, verifying that:

- The response includes the new ``display_title`` and ``source_label``
  fields on every list item.
- The ``display_title`` priority chain is applied correctly at each
  fallback layer (generated_title_zh → record.title → ready candidate
  title → filename → source-type label → final fallback).
- ``title_generation_status != 'succeeded'`` does NOT unlock
  ``generated_title_zh``.
- The search ``query`` matches the computed ``display_title`` (not just
  ``r.title``).
- ``source_label`` is a controlled string and never leaks raw
  ``metadata_json`` keys or JSON.
- The existing sort (``last_opened_at DESC NULLS LAST``, ``created_at
  DESC``, ``id DESC``) is preserved.

The pure projection logic is covered by
``test_reading_record_list_projection.py``; this file covers the
end-to-end SQL + route integration.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.database import connection as db_connection
from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

AUTH_HEADERS = {"Authorization": "Bearer test_token"}


# ---------------------------------------------------------------------------
# Test fixtures (self-contained, mirroring test_reader_orchestration_api.py)
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


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type("SessionInfo", (), {
            "user_id": user_id,
            "session_id": uuid4(),
        })(),
    )


async def _create_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.fixture
async def reader_api_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    schema_name = f"test_reader_list_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        monkeypatch.setattr(db_connection, "DB_POOL", pool)

        app = FastAPI()
        app.include_router(api_router)

        try:
            yield {"pool": pool, "app": app}
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Helpers for creating records with controlled identity fields
# ---------------------------------------------------------------------------


async def _create_reader_input_record(
    client: AsyncClient,
    *,
    title: str | None = None,
    source_metadata: dict[str, object] | None = None,
) -> str:
    """POST the unified reader input route and return the record_id."""
    text = f"Record body {uuid4().hex[:8]}."
    if title is not None:
        text = f"# {title}\n\n{text}"
    payload: dict[str, object] = {
        "source_type": "pasted_text",
        "text": text,
    }
    if source_metadata is not None:
        payload["source_metadata"] = source_metadata
    response = await client.post(
        "/reader/records/input",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["record_id"])


async def _set_generated_title(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    generated_title_zh: str | None,
    status: str,
) -> None:
    """Directly UPDATE reading_records to set the title-generation fields."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET generated_title_zh = $2,
                title_generation_status = $3,
                title_generation_updated_at = NOW()
            WHERE id = $1
            """,
            UUID(record_id),
            generated_title_zh,
            status,
        )


async def _clear_record_title(
    pool: asyncpg.Pool,
    *,
    record_id: str,
) -> None:
    """Set reading_records.title to NULL to test fallback layers."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET title = NULL WHERE id = $1",
            UUID(record_id),
        )


async def _insert_ready_candidate(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    user_id: UUID,
    title: str,
) -> None:
    """Insert a ready candidate_reading_documents row for the current generation."""
    async with pool.acquire() as conn:
        generation = await conn.fetchval(
            "SELECT generation FROM reading_records WHERE id = $1",
            UUID(record_id),
        )
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status
            )
            VALUES ($1, $2, $3, $4, '[]'::jsonb, '', '{}'::jsonb, '{}'::jsonb, 'ready')
            """,
            UUID(record_id),
            user_id,
            generation,
            title,
        )


async def _update_original_input_type(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    input_type: str,
    filename: str | None = None,
) -> None:
    """Update the original_inputs row for a record to control input_type/filename."""
    async with pool.acquire() as conn:
        if filename is not None:
            await conn.execute(
                """
                UPDATE original_inputs
                SET input_type = $2,
                    metadata_json = metadata_json || jsonb_build_object('filename', $3::text)
                WHERE reading_record_id = $1
                """,
                UUID(record_id),
                input_type,
                filename,
            )
        else:
            await conn.execute(
                """
                UPDATE original_inputs
                SET input_type = $2
                WHERE reading_record_id = $1
                """,
                UUID(record_id),
                input_type,
            )


async def _set_last_opened_at(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    last_opened_at: datetime | None,
) -> None:
    """Set last_opened_at on a reading_records row."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET last_opened_at = $2 WHERE id = $1",
            UUID(record_id),
            last_opened_at,
        )


async def _insert_extra_original_input(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    user_id: UUID,
    input_type: str = "plain_text",
    source_text: str = "extra input body",
    filename: str | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert a SECOND original_inputs row for a record.

    The existing _create_reader_input_record already inserts one row.
    This helper inserts an additional row with a later created_at so
    we can verify the LATERAL join picks the earliest one.
    """
    metadata: dict[str, object] = {}
    if filename:
        metadata["filename"] = filename
    ts = created_at or datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json,
                content_sha256, created_at
            )
            VALUES ($1, $2, $3, $4, $5, '{}'::jsonb, $6::jsonb, $7, $8)
            """,
            uuid4(),
            UUID(record_id),
            user_id,
            input_type,
            source_text,
            jsonb_param(metadata) if metadata else "{}",
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            ts,
        )


# ---------------------------------------------------------------------------
# Tests: response field presence
# ---------------------------------------------------------------------------


async def test_response_includes_display_title_and_source_label_fields(
    reader_api_env: dict[str, object],
) -> None:
    """Every list item must include display_title and source_label."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            await _create_reader_input_record(client, title="Has Title")
            await _create_reader_input_record(client, title=None)

            response = await client.get(
                "/reader/records?limit=10",
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 2

            for item in items:
                assert "display_title" in item
                assert isinstance(item["display_title"], str)
                assert len(item["display_title"]) >= 1
                assert "source_label" in item
                assert isinstance(item["source_label"], str)
                assert len(item["source_label"]) >= 1


# ---------------------------------------------------------------------------
# Tests: display_title priority matrix (DB-backed)
# ---------------------------------------------------------------------------


async def test_display_title_layer1_generated_title_zh_succeeded(
    reader_api_env: dict[str, object],
) -> None:
    """Layer 1: succeeded generated_title_zh wins over record.title."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(client, title="English Title")
            await _set_generated_title(
                pool,
                record_id=record_id,
                generated_title_zh="生成的中文标题",
                status="succeeded",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["display_title"] == "生成的中文标题"
            # Original title field is still preserved for backward compat
            assert items[0]["title"] == "English Title"


async def test_display_title_layer2_record_title_when_no_succeeded_generated(
    reader_api_env: dict[str, object],
) -> None:
    """Layer 2: record.title is used when generated_title_zh is pending."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(client, title="My Record Title")
            # generated_title_zh is set but status is pending (not succeeded)
            await _set_generated_title(
                pool,
                record_id=record_id,
                generated_title_zh="should not be used",
                status="pending",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["display_title"] == "My Record Title"


async def test_title_generation_status_not_succeeded_ignores_generated_title_zh(
    reader_api_env: dict[str, object],
) -> None:
    """title_generation_status != 'succeeded' must NOT use generated_title_zh."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(client, title="Fallback Title")
            for status in ("pending", "failed_retryable"):
                await _set_generated_title(
                    pool,
                    record_id=record_id,
                    generated_title_zh="生成的标题-不应使用",
                    status=status,
                )

                response = await client.get("/reader/records", headers=AUTH_HEADERS)
                assert response.status_code == 200
                items = response.json()["items"]
                assert len(items) == 1
                assert items[0]["display_title"] == "Fallback Title", status


async def test_display_title_layer3_ready_candidate_title(
    reader_api_env: dict[str, object],
) -> None:
    """Layer 3: ready candidate title is used when record.title is NULL
    and no succeeded generated title."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(client, title="Temp Title")
            await _clear_record_title(pool, record_id=record_id)
            await _insert_ready_candidate(
                pool,
                record_id=record_id,
                user_id=user_id,
                title="Candidate Document Title",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["display_title"] == "Candidate Document Title"


async def test_display_title_layer4_filename_when_no_titles(
    reader_api_env: dict[str, object],
) -> None:
    """Layer 4: original input filename is used when all title layers are empty."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(
                client,
                title="Temp Title",
                source_metadata={"filename": "report.pdf"},
            )
            await _clear_record_title(pool, record_id=record_id)
            # No ready candidate → falls through to filename

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["display_title"] == "report.pdf"


async def test_display_title_layer5_source_type_label_when_nothing_else(
    reader_api_env: dict[str, object],
) -> None:
    """Layer 5: source-type friendly label is used when all title/filename
    layers are empty."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(client, title="Temp Title")
            await _clear_record_title(pool, record_id=record_id)
            # No ready candidate, no filename in metadata → layer 5

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            # original_input_type is the pasted-text semantic → "粘贴文本"
            assert items[0]["display_title"] == "粘贴文本"


# ---------------------------------------------------------------------------
# Tests: query alignment with display_title
# ---------------------------------------------------------------------------


async def test_query_matches_display_title_not_just_record_title(
    reader_api_env: dict[str, object],
) -> None:
    """The search query must match the computed display_title, not just r.title.

    Scenario: record.title is "English Title" but generated_title_zh is
    "中文焦点标题" (succeeded). Searching for "焦点" must find the record
    because display_title == "中文焦点标题".
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            # Record 1: display_title will be "中文焦点标题" (generated, succeeded)
            r1 = await _create_reader_input_record(client, title="English Title")
            await _set_generated_title(
                pool,
                record_id=r1,
                generated_title_zh="中文焦点标题",
                status="succeeded",
            )
            # Record 2: display_title will be "Other Record" (plain record.title)
            await _create_reader_input_record(client, title="Other Record")

            # Search for the Chinese generated title
            response = await client.get(
                "/reader/records?query=焦点",
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert len(data["items"]) == 1
            assert data["items"][0]["display_title"] == "中文焦点标题"
            # The raw title field is still "English Title" — proves the query
            # matched display_title, not r.title
            assert data["items"][0]["title"] == "English Title"


# ---------------------------------------------------------------------------
# Tests: source_label safety
# ---------------------------------------------------------------------------


async def test_source_label_is_controlled_no_raw_metadata_leak(
    reader_api_env: dict[str, object],
) -> None:
    """source_label must be a controlled string; raw metadata_json keys/JSON
    must never appear in the response."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            await _create_reader_input_record(
                client,
                title="Test Record",
                source_metadata={
                    "filename": "doc.pdf",
                    "internal_url": "https://internal.example.com/secret",
                    "raw_blob_ref": "blob://abc123",
                    "nested": {"deep": "value"},
                },
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            source_label = items[0]["source_label"]

            # source_label must be a controlled friendly string
            assert isinstance(source_label, str)
            # Must NOT contain raw metadata keys or JSON syntax
            assert "internal_url" not in source_label
            assert "raw_blob_ref" not in source_label
            assert "nested" not in source_label
            assert "blob://" not in source_label
            assert "https://" not in source_label
            assert "{" not in source_label
            assert "}" not in source_label


async def test_source_label_includes_filename_for_file_ref(
    reader_api_env: dict[str, object],
) -> None:
    """source_label should include the filename for file-like input types."""
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(
                client,
                title="File Record",
                source_metadata={"filename": "report.pdf"},
            )
            # Change input_type to file_ref so source_label includes the filename
            await _update_original_input_type(
                pool,
                record_id=record_id,
                input_type="file_ref",
                filename="report.pdf",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["source_label"] == "上传文件 · report.pdf"


# ---------------------------------------------------------------------------
# Tests: sort regression (last_opened_at DESC NULLS LAST)
# ---------------------------------------------------------------------------


async def test_sort_last_opened_at_desc_nulls_last_regression(
    reader_api_env: dict[str, object],
) -> None:
    """Verify the existing sort order is preserved:
    last_opened_at DESC NULLS LAST, then created_at DESC, then id DESC.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            await _create_reader_input_record(client, title="Oldest No Open")
            r2 = await _create_reader_input_record(client, title="With Open")
            await _create_reader_input_record(client, title="Newest No Open")

            # r2 has last_opened_at set; r1 and r3 do not
            await _set_last_opened_at(
                pool,
                record_id=r2,
                last_opened_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 3

            # r2 (has last_opened_at) comes first
            assert items[0]["title"] == "With Open"
            # r3 (created_at newer, no last_opened_at) comes before r1
            assert items[1]["title"] == "Newest No Open"
            assert items[2]["title"] == "Oldest No Open"


# ---------------------------------------------------------------------------
# Regression: bounded server read (LIMIT applied in SQL)
# ---------------------------------------------------------------------------


async def test_query_less_limit_is_bounded_server_side(
    reader_api_env: dict[str, object],
) -> None:
    """When query is empty, SQL LIMIT must be applied server-side.

    Creates 15 records and requests limit=10. The response must contain
    exactly 10 items (not 15), and total must be 15. This proves the
    repository SQL is bounded — the sidebar (limit=10) does not read
    all user records.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            for i in range(15):
                await _create_reader_input_record(client, title=f"Record {i:02d}")

            response = await client.get(
                "/reader/records?limit=10",
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data["items"]) == 10
            assert data["total"] == 15
            assert data["limit"] == 10


async def test_query_with_limit_bounded_and_still_matches_display_title(
    reader_api_env: dict[str, object],
) -> None:
    """When query is non-empty, results are filtered at SQL level
    against display_title and LIMIT is applied server-side.

    Creates 15 records where 5 have a generated display_title containing
    "焦点". Searching for "焦点" with limit=10 must return exactly 5
    items (not 15), proving the SQL CTE filters before LIMIT.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            # 5 records with generated display_title containing "焦点"
            for i in range(5):
                rid = await _create_reader_input_record(
                    client, title=f"English {i}"
                )
                await _set_generated_title(
                    pool,
                    record_id=rid,
                    generated_title_zh=f"焦点报告 {i}",
                    status="succeeded",
                )
            # 10 records without "焦点" in any title layer
            for i in range(10):
                await _create_reader_input_record(client, title=f"Other {i}")

            response = await client.get(
                "/reader/records?query=焦点&limit=10",
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data["items"]) == 5
            assert data["total"] == 5
            for item in data["items"]:
                assert "焦点" in item["display_title"]


# ---------------------------------------------------------------------------
# Regression: candidate title only when exactly 1 ready candidate
# ---------------------------------------------------------------------------


async def test_display_title_skips_candidate_title_when_two_ready_candidates(
    reader_api_env: dict[str, object],
) -> None:
    """When 2+ ready candidates exist, NONE of their titles are used.

    Creates a record with no title and no generated_title_zh, then
    inserts 2 ready candidates. The display_title must NOT be either
    candidate's title — it must fall through to the next layer
    (filename → source-type → fallback).
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(
                client,
                title="Temp Title",
                source_metadata={"filename": "doc.pdf"},
            )
            await _clear_record_title(pool, record_id=record_id)
            # Insert 2 ready candidates
            await _insert_ready_candidate(
                pool,
                record_id=record_id,
                user_id=user_id,
                title="First Candidate Title",
            )
            await _insert_ready_candidate(
                pool,
                record_id=record_id,
                user_id=user_id,
                title="Second Candidate Title",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            display_title = items[0]["display_title"]
            # Must NOT be either candidate's title
            assert display_title != "First Candidate Title"
            assert display_title != "Second Candidate Title"
            # Must fall through to filename layer (source_metadata has filename)
            assert display_title == "doc.pdf"


async def test_display_title_uses_candidate_title_when_exactly_one(
    reader_api_env: dict[str, object],
) -> None:
    """When exactly 1 ready candidate exists, its title IS used.

    This is the positive control for the fix — the candidate title
    layer must still work when the count is exactly 1.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            record_id = await _create_reader_input_record(
                client,
                title="Temp Title",
                source_metadata={"filename": "doc.pdf"},
            )
            await _clear_record_title(pool, record_id=record_id)
            await _insert_ready_candidate(
                pool,
                record_id=record_id,
                user_id=user_id,
                title="Sole Candidate Title",
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["display_title"] == "Sole Candidate Title"


# ---------------------------------------------------------------------------
# Regression: single deterministic original_inputs projection
# ---------------------------------------------------------------------------


async def test_two_original_inputs_picks_earliest_and_no_duplicate_rows(
    reader_api_env: dict[str, object],
) -> None:
    """When a record has 2 original_inputs, the list returns exactly
    one row, total is not doubled, and display_title/source_label come
    from the EARLIEST original_input.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            # Create a record (this inserts original_input #1 with
            # input_type='plain_text' and metadata containing filename
            # "earliest.pdf").
            record_id = await _create_reader_input_record(
                client,
                title="Has Two Inputs",
                source_metadata={"filename": "earliest.pdf"},
            )
            # Clear title so display_title falls to filename layer,
            # making it easy to assert which original_input was picked.
            await _clear_record_title(pool, record_id=record_id)
            # Update the first original_input to file_ref so filename
            # becomes the display_title. Also set its created_at to a
            # known early time so the second input is definitely later.
            await _update_original_input_type(
                pool,
                record_id=record_id,
                input_type="file_ref",
                filename="earliest.pdf",
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE original_inputs
                    SET created_at = $2
                    WHERE reading_record_id = $1
                    """,
                    UUID(record_id),
                    datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
                )
            # Insert a SECOND original_input with a LATER created_at and
            # a different filename. The LATERAL join must pick the
            # earliest one ("earliest.pdf"), not this one.
            await _insert_extra_original_input(
                pool,
                record_id=record_id,
                user_id=user_id,
                input_type="file_ref",
                source_text="second input body",
                filename="latest.pdf",
                created_at=datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC),
            )

            response = await client.get("/reader/records", headers=AUTH_HEADERS)
            assert response.status_code == 200
            data = response.json()

            # Exactly one row (not two from a fan-out JOIN)
            assert len(data["items"]) == 1
            # Total is 1 (not doubled)
            assert data["total"] == 1

            item = data["items"][0]
            # Display_title comes from the EARLIEST original_input
            assert item["display_title"] == "earliest.pdf"
            # Source_label comes from the EARLIEST original_input
            assert item["source_label"] == "上传文件 · earliest.pdf"


# ---------------------------------------------------------------------------
# Regression: query=None items SQL must have LIMIT, no COUNT(*) OVER
# ---------------------------------------------------------------------------


async def test_query_none_items_sql_has_limit_and_no_count_window(
    reader_api_env: dict[str, object],
) -> None:
    """When query is None, the items SQL must contain LIMIT and must
    NOT contain COUNT(*) OVER().

    Patches ``asyncpg.Connection.fetch`` to capture the actual SQL string
    executed in the query=None path, then asserts structural properties.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    captured_sqls: list[str] = []
    original_fetch = asyncpg.Connection.fetch

    async def _capturing_fetch(self, query, *args, **kwargs):
        captured_sqls.append(query)
        return await original_fetch(self, query, *args, **kwargs)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            await _create_reader_input_record(client, title="Test Record")

            asyncpg.Connection.fetch = _capturing_fetch  # type: ignore[method-assign]
            try:
                response = await client.get(
                    "/reader/records?limit=5",
                    headers=AUTH_HEADERS,
                )
            finally:
                asyncpg.Connection.fetch = original_fetch  # type: ignore[method-assign]

            assert response.status_code == 200

            # At least one SQL was captured (the items query)
            assert len(captured_sqls) >= 1

            # The items query (first captured fetch) must:
            # 1. Contain LIMIT (bounded read)
            # 2. NOT contain COUNT(*) OVER() (no window function)
            items_sql = captured_sqls[0]
            assert "LIMIT" in items_sql.upper()
            assert "COUNT(*)OVER" not in items_sql.upper().replace(" ", "")


async def test_query_none_total_always_uses_separate_count(
    reader_api_env: dict[str, object],
) -> None:
    """When query is None, total must always come from a separate
    simple COUNT(*) on reading_records, even when rows > 0.

    Creates 5 records and requests limit=3. total must be 5 (not 3),
    proving the total is NOT derived from the limited items query.
    """
    pool = reader_api_env["pool"]
    app = reader_api_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = await _insert_user(pool)

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            for i in range(5):
                await _create_reader_input_record(client, title=f"Record {i}")

            response = await client.get(
                "/reader/records?limit=3",
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 200
            data = response.json()
            # Items are bounded by limit
            assert len(data["items"]) == 3
            # Total reflects ALL records, not just the limited page
            assert data["total"] == 5
