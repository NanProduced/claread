from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
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

# Migration 0015 adds ``layer_analysis_plans`` + ``analysis_windows`` tables.
# Required because ``bootstrap_missing_jobs`` now routes grammar bootstrap
# based on Z+ plan existence in ``layer_analysis_plans`` (Task C3).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")

# T1.1 short-article batch path: migration 0017 adds the new batch job types
# and worker types to the CHECK constraints (see pipeline runner fixture).
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")


@pytest.fixture
async def smoke_harness_env() -> asyncpg.Pool:
    schema_name = f"test_reader_smoke_harness_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.execute(_MIGRATION_0017_SQL)
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


async def _poll_events_after(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    after_sequence: int,
):
    result = await ReaderEventRuntime(pool=pool).poll_events(
        record_id=record_id,
        user_id=user_id,
        after_sequence=after_sequence,
        limit=50,
    )
    return result.events


def _find_progress_layer(
    snapshot,
    *,
    capability: str,
    status: str | None = None,
    layer_type: str | None = None,
):
    for layer in snapshot.enhancement_progress.layers:
        if layer.capability != capability:
            continue
        if status is not None and layer.status != status:
            continue
        if layer_type is not None and layer.layer_type != layer_type:
            continue
        return layer
    raise AssertionError(
        "progress layer not found for "
        f"capability={capability!r}, status={status!r}, layer_type={layer_type!r}"
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
    assert result.snapshot.last_event_sequence > 1
    assert result.snapshot.enhancement_progress.overall_status == "ready"
    assert _find_progress_layer(
        result.snapshot,
        capability="translation",
        status="succeeded",
        layer_type="translation",
    )
    assert _find_progress_layer(
        result.snapshot,
        capability="vocabulary",
        status="succeeded",
        layer_type="vocabulary",
    )
    assert _find_progress_layer(
        result.snapshot,
        capability="grammar",
        status="succeeded",
    )
    events = await _poll_events_after(
        smoke_harness_env,
        record_id=result.record_id,
        user_id=user_id,
        after_sequence=1,
    )
    assert any(event.event_type == "layer_published" for event in events)
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
    await ReaderEnhancementPipelineRunner(
        pool=smoke_harness_env,
        enable_zplus_grammar=False,
    ).bootstrap_missing_jobs(
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
    ) == 4
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
