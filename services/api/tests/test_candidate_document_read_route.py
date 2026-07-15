"""S2 P1-1: Route-level body contract tests for
``GET /reader/records/{record_id}/candidate-document``.

These tests verify the **actual HTTP status code and the full top-level
JSON body** returned by FastAPI. They explicitly assert that:

- 404 responses return the root-level shape
  ``{"ok": false, "code": "not_found", "message": ...}`` and are NOT
  wrapped into FastAPI's default ``{"detail": ...}`` envelope.
- 409 responses return the root-level shape
  ``{"ok": false, "code": ..., "resolution": ..., "message": ...}``.
- The 200 response still returns the typed projection DTO.

The tests use a real PostgreSQL schema (per-test isolated) and a real
FastAPI app with the production router wired in. Auth is mocked via
``app.services.auth.dependencies.validate_session``.

Coverage:
- 404 (record not found — collapsed)
- 409 open_reader (readable_enhancing + article_ready + active_base_id)
- 409 open_reader via coverage_complete (P1-2 regression)
- 409 return_to_library (failed state)
- 409 multiple_ready_candidates
- 200 happy path (one ready candidate + needs_confirmation)
- Body shape invariants (no ``detail`` wrapper, root-level ``ok``/``code``)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.database import connection as db_connection
from app.database.connection import init_connection
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
AUTH_HEADERS = {"Authorization": "Bearer test_token"}
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
async def route_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    schema_name = f"test_cand_route_{uuid4().hex}"
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


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {
                "user_id": user_id,
                "session_id": uuid4(),
            },
        )(),
    )


async def _create_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


# ---------------------------------------------------------------------------
# Seeding helpers (mirror of test_candidate_document_read_service.py)
# ---------------------------------------------------------------------------


async def _seed_user(pool: asyncpg.Pool, *, user_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )


async def _seed_record(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int = 1,
    product_state: str = "needs_confirmation",
    readiness_state: str = "candidate_base_ready",
    active_base_id: UUID | None = None,
    deleted_at: datetime | None = None,
    title: str = "Test Record",
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO reading_records (
                    id, user_id, source_type, title, language,
                    lifecycle_status, product_state, readiness_state,
                    generation, active_base_id, deleted_at
                )
                VALUES ($1, $2, 'text', $3, 'en',
                        'active', $4, $5, $6, NULL, $7)
                """,
                record_id,
                user_id,
                title,
                product_state,
                readiness_state,
                generation,
                deleted_at,
            )
            if active_base_id is not None:
                base_text = "test base text"
                base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
                await conn.execute(
                    """
                    INSERT INTO reading_bases (
                        id, reading_record_id, base_version, record_generation,
                        text, content_sha256, content_utf16_length,
                        canonicalizer_version, builder_version, segmenter_version,
                        status, frozen_at, created_at
                    )
                    VALUES ($1, $2, 1, $3,
                            $4, $5, utf16_code_unit_length($4),
                            'test_v1', 'test_v1', 'test_v1',
                            'active', $6, $6)
                    """,
                    active_base_id,
                    record_id,
                    generation,
                    base_text,
                    base_sha,
                    _NOW,
                )
                await conn.execute(
                    "UPDATE reading_records SET active_base_id = $1 WHERE id = $2",
                    active_base_id,
                    record_id,
                )


async def _seed_original_input(
    pool: asyncpg.Pool,
    *,
    original_input_id: UUID,
    record_id: UUID,
    user_id: UUID,
    input_type: str = "plain_text",
    source_text: str = "test text",
    metadata_json: dict | None = None,
) -> None:
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, $4,
                    $5, '{}'::jsonb, $6::jsonb, $7)
            """,
            original_input_id,
            record_id,
            user_id,
            input_type,
            source_text,
            metadata_json or {},
            source_sha,
        )


async def _seed_candidate(
    pool: asyncpg.Pool,
    *,
    candidate_id: UUID,
    record_id: UUID,
    user_id: UUID,
    generation: int = 1,
    status: str = "ready",
    title: str = "Test Candidate",
    blocks_json: list | None = None,
    quality_json: dict | None = None,
    source_refs_json: dict | None = None,
    canonical_text_preview: str = "",
    created_at: datetime = _NOW,
    confirmed_at: datetime | None = None,
    original_input_id: UUID | None = None,
) -> None:
    blocks = blocks_json or [
        {
            "block_id": "paragraph-0000",
            "order_index": 0,
            "block_type": "paragraph",
            "text_content": "Short test content.",
        }
    ]
    quality = quality_json or {
        "candidate_creation_version": "candidate_creation_v1",
        "suitability": {
            "outcome": "candidate_document_required",
            "flags": [],
            "reasons": [],
            "word_count": 5,
            "english_word_ratio": 1.0,
            "natural_language_score": 0.95,
        },
    }
    refs_payload: dict = {"source_type": "pasted_text"}
    if source_refs_json is not None:
        refs_payload = dict(source_refs_json)
    if original_input_id is not None and "original_input_id" not in refs_payload:
        refs_payload["original_input_id"] = str(original_input_id)
    effective_confirmed_at = confirmed_at if status == "confirmed" else None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status, created_at, updated_at,
                confirmed_at
            )
            VALUES ($1, $2, $3, $4, $5,
                    $6::jsonb, $7, $8::jsonb, $9::jsonb, $10, $11, $11, $12)
            """,
            candidate_id,
            record_id,
            user_id,
            generation,
            title,
            blocks,
            canonical_text_preview,
            refs_payload,
            quality,
            status,
            created_at,
            effective_confirmed_at,
        )


# ---------------------------------------------------------------------------
# Section 1: 404 body contract
# ---------------------------------------------------------------------------


async def test_404_record_not_found_body_contract(
    route_env: dict[str, object],
) -> None:
    """404 must return root-level {ok, code, message} and NOT {"detail": ...}."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    await _seed_user(pool, user_id=user_id)

    nonexistent_record_id = uuid4()
    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{nonexistent_record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 404
    body = response.json()
    # Root-level contract — must NOT be wrapped in {"detail": ...}.
    assert "detail" not in body
    assert body["ok"] is False
    assert body["code"] == "not_found"
    assert isinstance(body["message"], str) and body["message"]
    # Must not leak internal terminology.
    body_str = str(body)
    for forbidden in ("candidate", "generation", "blocks_json", "quality_json"):
        assert forbidden not in body_str


# ---------------------------------------------------------------------------
# Section 2: 409 body contracts
# ---------------------------------------------------------------------------


async def test_409_open_reader_body_contract(
    route_env: dict[str, object],
) -> None:
    """409 open_reader: root-level body with code/resolution/message."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        active_base_id=base_id,
    )

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 409
    body = response.json()
    assert "detail" not in body
    assert body["ok"] is False
    assert body["code"] == "record_state_advanced"
    assert body["resolution"] == "open_reader"
    assert isinstance(body["message"], str) and body["message"]


async def test_409_open_reader_coverage_complete_body_contract(
    route_env: dict[str, object],
) -> None:
    """P1-2: coverage_complete + active_base_id also yields open_reader."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="readable_enhancing",
        readiness_state="coverage_complete",
        active_base_id=base_id,
    )

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 409
    body = response.json()
    assert "detail" not in body
    assert body["ok"] is False
    assert body["code"] == "record_state_advanced"
    assert body["resolution"] == "open_reader"
    assert isinstance(body["message"], str) and body["message"]


async def test_409_return_to_library_body_contract(
    route_env: dict[str, object],
) -> None:
    """409 return_to_library (failed state): root-level body."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    record_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="failed",
        readiness_state="submitted",
    )

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 409
    body = response.json()
    assert "detail" not in body
    assert body["ok"] is False
    assert body["code"] == "record_state_advanced"
    assert body["resolution"] == "return_to_library"
    assert isinstance(body["message"], str) and body["message"]


async def test_409_multiple_ready_candidates_body_contract(
    route_env: dict[str, object],
) -> None:
    """409 multiple_ready_candidates: root-level body."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    record_id = uuid4()
    candidate_a = uuid4()
    candidate_b = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_candidate(
        pool,
        candidate_id=candidate_a,
        record_id=record_id,
        user_id=user_id,
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_b,
        record_id=record_id,
        user_id=user_id,
    )

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 409
    body = response.json()
    assert "detail" not in body
    assert body["ok"] is False
    assert body["code"] == "multiple_ready_candidates"
    assert body["resolution"] == "return_to_library"
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Section 3: 200 happy path body contract
# ---------------------------------------------------------------------------


async def test_200_happy_path_body_contract(
    route_env: dict[str, object],
) -> None:
    """200 returns the typed projection DTO, not wrapped in {detail}."""
    pool = route_env["pool"]
    app = route_env["app"]
    assert isinstance(pool, asyncpg.Pool)
    assert isinstance(app, FastAPI)
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    original_input_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_original_input(
        pool,
        original_input_id=original_input_id,
        record_id=record_id,
        user_id=user_id,
        input_type="plain_text",
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        original_input_id=original_input_id,
    )

    with _mock_auth(user_id):
        async with await _create_client(app) as client:
            response = await client.get(
                f"/reader/records/{record_id}/candidate-document",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    assert "detail" not in body
    # Typed projection fields.
    assert body["record_id"] == str(record_id)
    assert body["candidate_document_id"] == str(candidate_id)
    assert body["record_generation"] == 1
    assert body["status"] == "ready"
    assert isinstance(body["title"], str)
    preview = body["preview"]
    assert preview["preview_mode"] in (
        "full_text",
        "truncated_preview",
        "outline_only",
    )
    assert isinstance(preview["preview_text"], str)
    assert isinstance(preview["is_truncated"], bool)
    assert isinstance(preview["total_char_count"], int)
    assert isinstance(preview["document_outline"], list)
    assert isinstance(preview["risk_items"], list)
    assert body["source_type"] in (
        "plain_text",
        "markdown",
        "file_ref",
        "url",
        "image_ref",
    )
    assert isinstance(body["source_label"], str)
    assert isinstance(body["created_at"], str)
    assert isinstance(body["updated_at"], str)
    # Field whitelist — no internal leak.
    body_str = str(body)
    for forbidden in (
        "blocks_json",
        "quality_json",
        "source_refs_json",
        "source_text",
        "canonical_text_preview",
        "original_input_id",
    ):
        assert forbidden not in body_str


# ---------------------------------------------------------------------------
# Section 4: 401 unauthenticated body contract
# ---------------------------------------------------------------------------


async def test_401_unauthenticated_body_contract(
    route_env: dict[str, object],
) -> None:
    """401 (no auth header) returns the existing auth mechanism's 401.

    The candidate-document endpoint inherits AuthUserDep, so a missing
    Authorization header yields 401 before the service is ever called.
    The body shape here is FastAPI's default for HTTPBearer
    auto_error=True; we only assert the status code so we do not
    over-couple to the auth envelope (which is owned by the auth
    module, not S2).
    """
    app = route_env["app"]
    assert isinstance(app, FastAPI)
    record_id = uuid4()

    async with await _create_client(app) as client:
        response = await client.get(
            f"/reader/records/{record_id}/candidate-document",
            # No Authorization header.
        )

    assert response.status_code == 401
