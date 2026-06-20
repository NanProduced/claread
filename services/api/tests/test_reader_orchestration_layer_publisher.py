from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.schemas.reader_orchestration import TranslationLayerOutput
from app.services.reader_orchestration.job_bootstrap import TranslationJobBootstrapService
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.layer_publisher import TranslationLayerPublisher
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def layer_publisher_env() -> asyncpg.Pool:
    schema_name = f"test_reader_layer_publisher_{uuid4().hex}"
    admin_conn = await connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _translation_output() -> TranslationLayerOutput:
    return TranslationLayerOutput(
        target_language="zh-CN",
        translated_text="发布后的译文",
        notes=[],
        confidence="normal",
    )


async def _bootstrap_and_claim(pool: asyncpg.Pool) -> tuple[object, object, object]:
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    await TranslationJobBootstrapService(pool=pool).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    claim = await ReaderJobRuntime(pool=pool).claim_next_job(
        lease_owner="publisher-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None
    return user_id, article, claim


async def test_publish_writes_translation_layer_and_layer_published_event(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, article, claim = await _bootstrap_and_claim(layer_publisher_env)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)

    published = await publisher.publish_unit_translation(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        output=_translation_output(),
    )

    assert published.reading_record_id == article.record_id
    assert published.base_id == article.base_id
    assert published.unit_id == article.snapshot.navigation.units[0].unit_id
    assert published.generation == 1
    assert published.event.sequence == 2

    async with layer_publisher_env.acquire() as conn:
        layer_row = await conn.fetchrow(
            """
            SELECT layer_type, target_scope, target_key, generation, status, source_job_id
            FROM enhancement_layers
            WHERE id = $1
            """,
            published.layer_id,
        )
        event_row = await conn.fetchrow(
            """
            SELECT sequence, event_type, source_job_id, source_layer_id
            FROM reader_events
            WHERE id = $1
            """,
            published.event.event_id,
        )

    assert layer_row is not None
    assert layer_row["layer_type"] == "translation"
    assert layer_row["target_scope"] == "unit"
    assert layer_row["target_key"] == published.unit_id
    assert layer_row["generation"] == 1
    assert layer_row["status"] == "published"
    assert layer_row["source_job_id"] == claim.job_id

    assert event_row is not None
    assert event_row["sequence"] == 2
    assert event_row["event_type"] == "layer_published"
    assert event_row["source_job_id"] == claim.job_id
    assert event_row["source_layer_id"] == published.layer_id


@pytest.mark.parametrize(
    ("case_name", "expected_reason"),
    [
        ("stale_generation", "stale_generation"),
        ("inactive_base", "inactive_base"),
        ("active_base_mismatch", "active_base_mismatch"),
    ],
)
async def test_publish_rejects_fence_failures(
    layer_publisher_env: asyncpg.Pool,
    case_name: str,
    expected_reason: str,
) -> None:
    _user_id, article, claim = await _bootstrap_and_claim(layer_publisher_env)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)

    async with layer_publisher_env.acquire() as conn:
        async with conn.transaction():
            if case_name == "stale_generation":
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
                    article.base_id,
                )
                new_base_id = await conn.fetchval(
                    """
                    INSERT INTO reading_bases (
                        reading_record_id,
                        base_version,
                        record_generation,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        status
                    )
                    SELECT
                        reading_record_id,
                        base_version + 1,
                        2,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        'active'
                    FROM reading_bases
                    WHERE id = $1
                    RETURNING id
                    """,
                    article.base_id,
                )
                assert new_base_id is not None
                await conn.execute(
                    """
                    UPDATE reading_records
                    SET generation = 2,
                        active_base_id = $2
                    WHERE id = $1
                    """,
                    article.record_id,
                    new_base_id,
                )
            elif case_name == "inactive_base":
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
                    article.base_id,
                )
            elif case_name == "active_base_mismatch":
                await conn.execute(
                    "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
                    article.record_id,
                )
            else:
                raise AssertionError(f"unknown fence test case: {case_name}")

    with pytest.raises(FenceViolationError, match=expected_reason):
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=_translation_output(),
        )

    async with layer_publisher_env.acquire() as conn:
        layer_count = await conn.fetchval("SELECT COUNT(*) FROM enhancement_layers")
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
    assert layer_count == 0
    assert job_status == "claimed"


async def test_publish_rejects_lease_token_mismatch(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(layer_publisher_env)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)

    with pytest.raises(LeaseTokenMismatchError):
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=uuid4(),
            output=_translation_output(),
        )


def test_layer_publisher_module_does_not_reference_render_scene_json() -> None:
    path = API_ROOT / "app" / "services" / "reader_orchestration" / "layer_publisher.py"
    assert "render_scene_json" not in path.read_text(encoding="utf-8")
