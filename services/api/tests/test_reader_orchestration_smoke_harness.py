from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.smoke_harness import (
    DEV_FAKE_EXECUTOR_NOTE,
    ReaderEnhancementSmokeHarness,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
)


@pytest.fixture
async def smoke_harness_env() -> asyncpg.Pool:
    schema_name = f"test_reader_smoke_harness_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


def _plain_text(unit_count: int) -> str:
    paragraphs = [
        "First sentence for smoke harness.",
        "Second sentence for smoke harness.",
        "Third sentence for smoke harness.",
    ]
    return "\n\n".join(paragraphs[:unit_count])


async def _count_jobs_by_status(
    pool: asyncpg.Pool,
    record_id: UUID,
    status: str,
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND status = $2
                """,
                record_id,
                status,
            )
        )


async def _count_layers(pool: asyncpg.Pool, record_id: UUID, layer_type: str) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM enhancement_layers
                WHERE reading_record_id = $1
                  AND layer_type = $2
                  AND status = 'published'
                """,
                record_id,
                layer_type,
            )
        )


@pytest.mark.anyio
async def test_prepare_record_with_fake_executors_reloads_snapshot_without_render_scene_json(
    smoke_harness_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(smoke_harness_env)
    harness = ReaderEnhancementSmokeHarness(pool=smoke_harness_env)

    result = await harness.prepare_record(
        user_id=user_id,
        plain_text=_plain_text(2),
        title="Smoke Harness",
        executor_mode="fake",
        allow_fake_executors=True,
    )

    assert result.executor_mode == "fake"
    assert result.executor_note == DEV_FAKE_EXECUTOR_NOTE
    assert result.pipeline_summary.record_id == result.record_id
    assert result.pipeline_summary.base_id == result.base_id
    assert result.layer_counts.translation == 2
    assert result.layer_counts.vocabulary == 2
    assert result.layer_counts.grammar_note == 2
    assert result.layer_counts.sentence_analysis == 2
    assert result.snapshot.record_id == str(result.record_id)
    assert result.snapshot.last_event_sequence == result.pipeline_summary.last_event_sequence
    assert "render_scene_json" not in json.dumps(result.snapshot.value, ensure_ascii=False)


@pytest.mark.anyio
async def test_prepare_record_keeps_other_record_jobs_queued(
    smoke_harness_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(smoke_harness_env)
    article_service = ArticleReadyPersistenceService(pool=smoke_harness_env)
    older_record = await article_service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=_plain_text(1),
            title="Older queued record",
        )
    )
    await ReaderEnhancementPipelineRunner(pool=smoke_harness_env).bootstrap_missing_jobs(
        record_id=older_record.record_id,
        user_id=user_id,
    )

    result = await ReaderEnhancementSmokeHarness(pool=smoke_harness_env).prepare_record(
        user_id=user_id,
        plain_text=_plain_text(1),
        title="Target record",
        executor_mode="fake",
        allow_fake_executors=True,
    )

    assert result.record_id != older_record.record_id
    assert result.layer_counts.translation == 1
    assert result.layer_counts.vocabulary == 1
    assert result.layer_counts.grammar_note == 1
    assert result.layer_counts.sentence_analysis == 1

    assert await _count_jobs_by_status(
        smoke_harness_env,
        older_record.record_id,
        "queued",
    ) == 3
    assert await _count_layers(
        smoke_harness_env,
        older_record.record_id,
        "translation",
    ) == 0
    assert await _count_layers(
        smoke_harness_env,
        older_record.record_id,
        "vocabulary",
    ) == 0
    assert await _count_layers(
        smoke_harness_env,
        older_record.record_id,
        "grammar_note",
    ) == 0
    assert await _count_layers(
        smoke_harness_env,
        older_record.record_id,
        "sentence_analysis",
    ) == 0


@pytest.mark.anyio
async def test_fake_executors_require_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    smoke_harness_env: asyncpg.Pool,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="explicit opt-in"):
            await ReaderEnhancementSmokeHarness(pool=smoke_harness_env).prepare_record(
                user_id=uuid4(),
                plain_text=_plain_text(1),
                title="Fake not allowed",
                executor_mode="fake",
            )
    finally:
        get_settings.cache_clear()
