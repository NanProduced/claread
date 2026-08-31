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

import hashlib
import json
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
    artifact_id: UUID | None = None,
    reading_record_id: UUID | None = None,
    original_input_id: UUID | None = None,
    artifact_kind: str = "original_upload",
    storage_provider: str = "oss",
    status: str = "available",
    content_type: str | None = "application/pdf",
    deleted: bool = False,
    bucket: str = "claread-dev",
    endpoint: str = "https://oss-cn-shenzhen.aliyuncs.com",
    object_key: str | None = None,
    source_filename: str | None = None,
) -> UUID:
    async with pool.acquire() as conn:
        artifact_id = await conn.fetchval(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key,
                endpoint, content_type, source_filename, status, deleted_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13
            )
            RETURNING id
            """,
            artifact_id or uuid4(),
            reading_record_id,
            original_input_id,
            user_id,
            artifact_kind,
            storage_provider,
            bucket,
            object_key or f"original-inputs/{user_id}/{uuid4()}/source.bin",
            endpoint,
            content_type,
            source_filename,
            status,
            datetime.now(UTC) if deleted else None,
        )
    assert isinstance(artifact_id, UUID)
    return artifact_id


async def _insert_record(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    generation: int = 3,
) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (
                user_id, source_type, lifecycle_status, product_state, generation
            )
            VALUES ($1, 'file', 'active', 'needs_confirmation', $2)
            RETURNING id
            """,
            user_id,
            generation,
        )
    assert isinstance(record_id, UUID)
    return record_id


async def _insert_original_input(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    source_ref: dict[str, object],
) -> UUID:
    async with pool.acquire() as conn:
        original_input_id = await conn.fetchval(
            """
            INSERT INTO original_inputs (
                reading_record_id, user_id, input_type, source_ref_json,
                content_sha256
            )
            VALUES ($1, $2, 'file_ref', $3::jsonb, $4)
            RETURNING id
            """,
            record_id,
            user_id,
            json.dumps(source_ref),
            "0" * 64,
        )
    assert isinstance(original_input_id, UUID)
    return original_input_id


async def _insert_confirmed_source(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    original_input_id: UUID,
    generation: int = 3,
) -> UUID:
    markdown = "# Source preview"
    async with pool.acquire() as conn:
        source_id = await conn.fetchval(
            """
            INSERT INTO confirmed_source_documents (
                reading_record_id, user_id, record_generation,
                original_input_id, markdown_text, content_sha256
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            record_id,
            user_id,
            generation,
            original_input_id,
            markdown,
            hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        )
    assert isinstance(source_id, UUID)
    return source_id


async def _insert_record_lineage(
    pool: asyncpg.Pool,
    *,
    object_key: str = "original-inputs/chosen.pdf",
) -> tuple[UUID, UUID, UUID, UUID]:
    user_id = await _insert_user(pool)
    record_id = await _insert_record(pool, user_id=user_id)
    artifact_id = uuid4()
    original_input_id = await _insert_original_input(
        pool,
        record_id=record_id,
        user_id=user_id,
        source_ref={"artifact_id": str(artifact_id)},
    )
    await _insert_confirmed_source(
        pool,
        record_id=record_id,
        user_id=user_id,
        original_input_id=original_input_id,
    )
    await _insert_artifact(
        pool,
        artifact_id=artifact_id,
        reading_record_id=record_id,
        original_input_id=original_input_id,
        user_id=user_id,
        object_key=object_key,
        source_filename="source.pdf",
    )
    return user_id, record_id, original_input_id, artifact_id


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


async def test_record_preview_uses_exact_persisted_artifact_reference_with_decoy(
    db_env: asyncpg.Pool,
) -> None:
    user_id, record_id, original_input_id, _ = await _insert_record_lineage(db_env)
    await _insert_artifact(
        db_env,
        user_id=user_id,
        reading_record_id=record_id,
        original_input_id=original_input_id,
        object_key="original-inputs/newer-decoy.pdf",
        source_filename="source.pdf",
        content_type="application/pdf",
    )

    result = await SourceArtifactPreviewService(
        pool=db_env,
        presigner=FakePresigner(),
    ).create_record_preview(
        record_id=record_id,
        expected_generation=3,
        user_id=user_id,
    )

    assert result.preview_url is not None
    assert "/original-inputs/chosen.pdf?" in result.preview_url
    assert "newer-decoy.pdf" not in result.preview_url


async def test_record_preview_security_failures_collapse(
    db_env: asyncpg.Pool,
) -> None:
    cases = (
        "random_record",
        "cross_account",
        "stale_generation",
        "deleted_record",
        "inactive_record",
        "advanced_product_state",
        "missing_source",
        "frozen_source",
        "missing_original_input",
        "missing_artifact_ref",
        "malformed_artifact_ref",
        "input_owner_mismatch",
        "input_record_mismatch",
        "artifact_owner_mismatch",
        "artifact_record_mismatch",
        "artifact_input_mismatch",
        "non_original_artifact",
        "deleted_artifact",
        "pending_artifact",
        "failed_artifact",
        "local_artifact",
        "wrong_mime",
        "null_mime",
    )

    for case in cases:
        user_id, record_id, original_input_id, artifact_id = await _insert_record_lineage(db_env)
        requested_record_id = record_id
        requested_user_id = user_id
        expected_generation = 3

        other_user_id: UUID | None = None
        if case in {"cross_account", "input_owner_mismatch", "artifact_owner_mismatch"}:
            other_user_id = await _insert_user(db_env)
        other_record_id: UUID | None = None
        if case in {"input_record_mismatch", "artifact_record_mismatch"}:
            other_record_id = await _insert_record(pool=db_env, user_id=user_id)
        other_input_id: UUID | None = None
        if case == "artifact_input_mismatch":
            other_input_id = await _insert_original_input(
                db_env,
                record_id=record_id,
                user_id=user_id,
                source_ref={"artifact_id": str(uuid4())},
            )

        async with db_env.acquire() as conn:
            if case == "random_record":
                requested_record_id = uuid4()
            elif case == "cross_account":
                assert other_user_id is not None
                requested_user_id = other_user_id
            elif case == "stale_generation":
                expected_generation = 2
            elif case == "deleted_record":
                await conn.execute(
                    "UPDATE reading_records SET deleted_at = now() WHERE id = $1",
                    record_id,
                )
            elif case == "inactive_record":
                await conn.execute(
                    "UPDATE reading_records SET lifecycle_status = 'cancelled' WHERE id = $1",
                    record_id,
                )
            elif case == "advanced_product_state":
                await conn.execute(
                    "UPDATE reading_records SET product_state = 'readable_enhancing' WHERE id = $1",
                    record_id,
                )
            elif case == "missing_source":
                await conn.execute(
                    "DELETE FROM confirmed_source_documents WHERE reading_record_id = $1",
                    record_id,
                )
            elif case == "frozen_source":
                await conn.execute(
                    """
                    UPDATE confirmed_source_documents
                    SET status = 'frozen', frozen_at = now()
                    WHERE reading_record_id = $1
                    """,
                    record_id,
                )
            elif case == "missing_original_input":
                await conn.execute(
                    """
                    UPDATE confirmed_source_documents
                    SET original_input_id = NULL
                    WHERE reading_record_id = $1
                    """,
                    record_id,
                )
            elif case == "missing_artifact_ref":
                await conn.execute(
                    """
                    UPDATE original_inputs
                    SET source_ref_json = $2::jsonb
                    WHERE id = $1
                    """,
                    original_input_id,
                    json.dumps({"kind": "upload"}),
                )
            elif case == "malformed_artifact_ref":
                await conn.execute(
                    """
                    UPDATE original_inputs
                    SET source_ref_json = $2::jsonb
                    WHERE id = $1
                    """,
                    original_input_id,
                    json.dumps({"artifact_id": "not-a-uuid"}),
                )
            elif case == "input_owner_mismatch":
                assert other_user_id is not None
                await conn.execute(
                    "UPDATE original_inputs SET user_id = $2 WHERE id = $1",
                    original_input_id,
                    other_user_id,
                )
            elif case == "input_record_mismatch":
                assert other_record_id is not None
                await conn.execute(
                    "UPDATE original_inputs SET reading_record_id = $2 WHERE id = $1",
                    original_input_id,
                    other_record_id,
                )
            elif case == "artifact_owner_mismatch":
                assert other_user_id is not None
                await conn.execute(
                    "UPDATE source_artifacts SET user_id = $2 WHERE id = $1",
                    artifact_id,
                    other_user_id,
                )
            elif case == "artifact_record_mismatch":
                assert other_record_id is not None
                await conn.execute(
                    "UPDATE source_artifacts SET reading_record_id = $2 WHERE id = $1",
                    artifact_id,
                    other_record_id,
                )
            elif case == "artifact_input_mismatch":
                assert other_input_id is not None
                await conn.execute(
                    "UPDATE source_artifacts SET original_input_id = $2 WHERE id = $1",
                    artifact_id,
                    other_input_id,
                )
            elif case == "non_original_artifact":
                await conn.execute(
                    "UPDATE source_artifacts SET artifact_kind = 'derived_preview' WHERE id = $1",
                    artifact_id,
                )
            elif case == "deleted_artifact":
                await conn.execute(
                    "UPDATE source_artifacts SET deleted_at = now() WHERE id = $1",
                    artifact_id,
                )
            elif case in {"pending_artifact", "failed_artifact"}:
                await conn.execute(
                    "UPDATE source_artifacts SET status = $2 WHERE id = $1",
                    artifact_id,
                    case.removesuffix("_artifact"),
                )
            elif case == "local_artifact":
                await conn.execute(
                    "UPDATE source_artifacts SET storage_provider = 'local' WHERE id = $1",
                    artifact_id,
                )
            elif case == "wrong_mime":
                await conn.execute(
                    "UPDATE source_artifacts SET content_type = 'text/plain' WHERE id = $1",
                    artifact_id,
                )
            elif case == "null_mime":
                await conn.execute(
                    "UPDATE source_artifacts SET content_type = NULL WHERE id = $1",
                    artifact_id,
                )

        service = SourceArtifactPreviewService(pool=db_env, presigner=FakePresigner())
        with pytest.raises(SourceArtifactPreviewNotFoundError):
            await service.create_record_preview(
                record_id=requested_record_id,
                expected_generation=expected_generation,
                user_id=requested_user_id,
            )


async def test_record_preview_presigner_unavailable_degrades(
    db_env: asyncpg.Pool,
) -> None:
    user_id, record_id, _, _ = await _insert_record_lineage(db_env)

    result = await SourceArtifactPreviewService(
        pool=db_env,
        presigner=NullPresigner(),
    ).create_record_preview(
        record_id=record_id,
        expected_generation=3,
        user_id=user_id,
    )

    assert result.degraded is True
    assert result.preview_url is None
    assert result.expires_at is None
