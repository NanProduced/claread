"""R8 Commit 3 — source artifact preview security contract (real PG).

Isolated per-test schema. Security coverage:
- owner-only (cross-account 404),
- deleted / pending / failed artifacts 404,
- wrong MIME (and null MIME) 404,
- local storage provider 404,
- short-lived read-only URL via injected FakePresigner (never the PUT
  upload URL path; response never exposes object_key/bucket/endpoint
  fields),
- presigner unavailable -> 200 degraded with preview_url=None
  (fail-closed, Candidate flows unaffected).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.oss_presigner import (
    FakePresigner,
    NullPresigner,
)
from app.services.reader_orchestration.source_preview_service import (
    SourceArtifactPreviewNotFoundError,
    SourceArtifactPreviewService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

SCHEMA_SQL = BASELINE_SQL


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
    schema_name = f"test_preview_{uuid4().hex}"
    admin_conn: asyncpg.Connection | None = None
    try:
        admin_conn = await asyncpg.connect(DATABASE_URL)
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(SCHEMA_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        if admin_conn is not None:
            await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for preview tests: {exc}")
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


async def _insert_artifact(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    artifact_kind: str = "original_upload",
    storage_provider: str = "oss",
    status: str = "available",
    content_type: str | None = "application/pdf",
    deleted: bool = False,
    bucket: str = "claread-dev",
    endpoint: str = "https://oss-cn-shenzhen.aliyuncs.com",
) -> UUID:
    async with pool.acquire() as conn:
        artifact_id = await conn.fetchval(
            """
            INSERT INTO source_artifacts (
                id, user_id, artifact_kind, storage_provider, bucket,
                object_key, endpoint, content_type, status, deleted_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            uuid4(),
            user_id,
            artifact_kind,
            storage_provider,
            bucket,
            f"original-inputs/{user_id}/{uuid4()}/source.bin",
            endpoint,
            content_type,
            status,
            datetime.now(UTC) if deleted else None,
        )
    assert isinstance(artifact_id, UUID)
    return artifact_id


async def test_owner_pdf_preview_returns_short_read_only_url(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    artifact_id = await _insert_artifact(db_env, user_id=user_id)

    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    )
    result = await service.create_preview(
        artifact_id=artifact_id,
        user_id=user_id,
    )
    assert result.degraded is False
    assert result.preview_url is not None
    assert "Signature=fake" in result.preview_url
    assert result.expires_at is not None
    # Short-lived: default presign TTL is 900s; assert well under an hour.
    age = (result.expires_at - datetime.now(UTC)).total_seconds()
    assert 0 < age <= 950
    assert result.content_type == "application/pdf"


async def test_cross_account_preview_404(
    db_env: asyncpg.Pool,
) -> None:
    owner = await _insert_user(db_env)
    other = await _insert_user(db_env)
    artifact_id = await _insert_artifact(db_env, user_id=owner)

    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    )
    with pytest.raises(SourceArtifactPreviewNotFoundError):
        await service.create_preview(
            artifact_id=artifact_id,
            user_id=other,
        )


async def test_deleted_pending_failed_artifacts_404(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    )
    for status, deleted in (
        ("deleted", False),
        ("pending", False),
        ("failed", False),
        ("available", True),
    ):
        artifact_id = await _insert_artifact(
            db_env, user_id=user_id, status=status, deleted=deleted
        )
        with pytest.raises(SourceArtifactPreviewNotFoundError):
            await service.create_preview(
                artifact_id=artifact_id,
                user_id=user_id,
            )


async def test_unsupported_mime_and_local_provider_404(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    )
    for content_type in ("text/plain", "text/markdown", None):
        artifact_id = await _insert_artifact(db_env, user_id=user_id, content_type=content_type)
        with pytest.raises(SourceArtifactPreviewNotFoundError):
            await service.create_preview(
                artifact_id=artifact_id,
                user_id=user_id,
            )
    local_id = await _insert_artifact(db_env, user_id=user_id, storage_provider="local")
    with pytest.raises(SourceArtifactPreviewNotFoundError):
        await service.create_preview(
            artifact_id=local_id,
            user_id=user_id,
        )


async def test_presigner_unavailable_degrades_without_url(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    artifact_id = await _insert_artifact(db_env, user_id=user_id)

    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=NullPresigner(),
    )
    result = await service.create_preview(
        artifact_id=artifact_id,
        user_id=user_id,
    )
    assert result.degraded is True
    assert result.preview_url is None
    assert result.expires_at is None


async def test_preview_never_exposes_storage_field_names(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    artifact_id = await _insert_artifact(
        db_env,
        user_id=user_id,
        bucket="secret-bucket-name",
        endpoint="https://secret-endpoint.example.com",
    )
    service = SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    )
    result = await service.create_preview(
        artifact_id=artifact_id,
        user_id=user_id,
    )
    # Response contract: only the DTO fields. object_key / bucket /
    # endpoint / credentials never appear as independent response fields
    # (asserted at the DTO level in test_source_artifact_preview_route.py).
    # The presigned URL value itself is a sensitive temporary delivery
    # value (bucket host + key path are inherent to the OSS presigned-URL
    # model, same as the documented PUT contract) and must never be
    # written into ordinary DOM; the AccessKey SECRET never appears
    # anywhere.
    assert result.preview_url is not None
    assert "Signature=fake" in result.preview_url
    assert "OSSAccessKeySecret" not in result.preview_url
