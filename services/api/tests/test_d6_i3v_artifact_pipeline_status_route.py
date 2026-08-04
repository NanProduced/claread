"""Route tests for D6-I3V GET /reader/source-artifacts/{artifact_id}/pipeline-status.

Covers:
- auth user_id only from AuthUserDep (mocked validate_session)
- unknown artifact → 404
- wrong user → 404 (fail closed)
- response extra forbid / stable shape
- happy path returns expected outcome
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from app.api.router import api_router
from app.database import connection as db_connection
from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio


# The single baseline includes document blocks and source artifacts.

AUTH_HEADERS = {"Authorization": "Bearer test_token"}

_USER_ID = UUID("00000000-0000-0000-0000-0000000d3f01")
_OTHER_USER_ID = UUID("00000000-0000-0000-0000-0000000d3f02")
_RECORD_ID = UUID("00000000-0000-0000-0000-0000000d3f03")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-0000000d3f04")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-0000000d3f05")
_RUN_ID = UUID("00000000-0000-0000-0000-0000000d3f06")

_DEFAULT_CONTENT_SHA256 = "a" * 64
_EXTRACTION_JOB_TYPE = "input_artifact_extraction"


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
async def route_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    schema_name = f"test_i3v_route_{uuid4().hex}"
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
                "user_id": str(user_id),
                "session_id": uuid4(),
            },
        )(),
    )


def _create_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


# ---------------------------------------------------------------------------
# Seed helpers (minimal — only what route tests need)
# ---------------------------------------------------------------------------


async def _seed_pending_artifact(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, NULL, NULL, $2,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/test/test.pdf', 'https://oss-cn-shenzhen.aliyuncs.com',
                    'application/pdf', 1024, $3, 'test.pdf', 'pending')
            """,
            _ARTIFACT_ID,
            _USER_ID,
            _DEFAULT_CONTENT_SHA256,
        )


async def _seed_other_user_artifact(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _OTHER_USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, NULL, NULL, $2,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/test/test.pdf', 'https://oss-cn-shenzhen.aliyuncs.com',
                    'application/pdf', 1024, $3, 'test.pdf', 'pending')
            """,
            _ARTIFACT_ID,
            _OTHER_USER_ID,
            _DEFAULT_CONTENT_SHA256,
        )


async def _seed_extraction_queued_environment(pool: asyncpg.Pool) -> None:
    """Seed a bound artifact with an extraction job in 'queued' status."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state, generation
            )
            VALUES ($1, $2, 'pdf', 'Route Test', 'en',
                    'active', 'processing', 'submitted', 1)
            """,
            _RECORD_ID,
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    NULL, $4::jsonb,
                    '{"source_artifact_status": "available"}'::jsonb,
                    $5)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            jsonb_param({"artifact_id": str(_ARTIFACT_ID)}),
            _DEFAULT_CONTENT_SHA256,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/test/test.pdf', 'https://oss-cn-shenzhen.aliyuncs.com',
                    'application/pdf', 1024, $5, 'test.pdf', 'available')
            """,
            _ARTIFACT_ID,
            _RECORD_ID,
            _ORIGINAL_INPUT_ID,
            _USER_ID,
            _DEFAULT_CONTENT_SHA256,
        )
        await conn.execute(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind, id
            )
            VALUES ($1, $2, 'input_artifact_extraction', 'queued', 1,
                    '{}'::jsonb, 'test_policy_v1', 'system', $3)
            """,
            _RECORD_ID,
            _USER_ID,
            _RUN_ID,
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_json, max_attempts
            )
            VALUES ($1, NULL, $2, $3,
                    $4, 'record', $5, 'queued',
                    0, 1, 'input_artifact_extraction_v1',
                    $6, '{}'::jsonb, 3)
            """,
            _RECORD_ID,
            _RUN_ID,
            _USER_ID,
            _EXTRACTION_JOB_TYPE,
            str(_ARTIFACT_ID),
            f"route-test-{uuid4().hex}",
        )


# ---------------------------------------------------------------------------
# Tests
# ===================================================================


async def test_route_returns_upload_pending_for_pending_artifact(
    route_env: dict[str, object],
) -> None:
    """Happy path: pending artifact → 200 with upload_pending outcome."""
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_pending_artifact(pool)

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "upload_pending"
    assert body["next_action"] == "complete_upload"
    assert body["artifact"]["artifact_id"] == str(_ARTIFACT_ID)
    assert body["artifact"]["status"] == "pending"
    assert body["record"] is None
    assert body["original_input"] is None
    assert body["extraction_job"] is None
    assert body["materialization_job"] is None
    assert body["candidate_document"] is None
    assert body["stable_document"] is None


async def test_route_returns_extraction_queued_for_bound_artifact(
    route_env: dict[str, object],
) -> None:
    """Happy path: bound artifact with queued extraction → 200 with extraction_queued."""
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_extraction_queued_environment(pool)

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "extraction_queued"
    assert body["next_action"] == "wait_for_worker"
    assert body["artifact"]["status"] == "available"
    assert body["record"] is not None
    assert body["record"]["reading_record_id"] == str(_RECORD_ID)
    assert body["original_input"] is not None
    assert body["original_input"]["has_source_text"] is False
    assert body["extraction_job"] is not None
    assert body["extraction_job"]["status"] == "queued"
    assert body["materialization_job"] is None


async def test_route_returns_404_for_unknown_artifact(
    route_env: dict[str, object],
) -> None:
    """Unknown artifact_id → 404."""
    app = route_env["app"]  # type: ignore[assignment]
    unknown_id = UUID("00000000-0000-0000-0000-00000000dead")

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{unknown_id}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_route_returns_404_for_wrong_user(
    route_env: dict[str, object],
) -> None:
    """Wrong user → 404 (fail closed, do not leak existence)."""
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_other_user_artifact(pool)

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_route_response_rejects_extra_fields(
    route_env: dict[str, object],
) -> None:
    """Response model uses extra=forbid; client cannot inject extra query params
    that change the response shape. Verify the response has exactly the
    expected top-level keys.
    """
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_pending_artifact(pool)

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    expected_keys = {
        "artifact",
        "record",
        "original_input",
        "extraction_job",
        "materialization_job",
        "candidate_document",
        "stable_document",
        "outcome",
        "next_action",
    }
    assert set(body.keys()) == expected_keys


async def test_route_auth_user_id_only_from_auth_dep(
    route_env: dict[str, object],
) -> None:
    """user_id must come from AuthUserDep, not from any client-supplied source.

    The route has no request body. We verify that the mock auth's user_id is
    the one used: seed an artifact for _USER_ID, mock auth to _USER_ID,
    and confirm 200. The route signature accepts only artifact_id (path) and
    current_user (dependency) — there is no body parameter.
    """
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_pending_artifact(pool)

    # Mock auth returns _USER_ID — the route must use this, not anything else.
    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact"]["artifact_id"] == str(_ARTIFACT_ID)


async def test_route_artifact_summary_has_expected_fields(
    route_env: dict[str, object],
) -> None:
    """Verify the artifact summary contains all expected fields with correct types."""
    pool = route_env["pool"]  # type: ignore[assignment]
    app = route_env["app"]  # type: ignore[assignment]
    await _seed_pending_artifact(pool)

    with _mock_auth(_USER_ID):
        async with _create_client(app) as client:
            response = await client.get(
                f"/reader/source-artifacts/{_ARTIFACT_ID}/pipeline-status",
                headers=AUTH_HEADERS,
            )

    assert response.status_code == 200
    artifact = response.json()["artifact"]
    expected_artifact_keys = {
        "artifact_id",
        "status",
        "artifact_kind",
        "storage_provider",
        "bucket",
        "endpoint",
        "object_key",
        "content_type",
        "byte_size",
        "content_sha256",
        "source_filename",
        "reading_record_id",
        "original_input_id",
    }
    assert set(artifact.keys()) == expected_artifact_keys
    assert artifact["artifact_kind"] == "original_upload"
    assert artifact["storage_provider"] == "oss"
    assert artifact["byte_size"] == 1024
    assert artifact["content_sha256"] == _DEFAULT_CONTENT_SHA256
    assert artifact["reading_record_id"] is None
    assert artifact["original_input_id"] is None
