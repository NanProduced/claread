from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.schemas.reader_orchestration import TranslationLayerOutput
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.orchestrator import (
    TRANSLATION_PARSED_POLICY_CODE,
    TRANSLATION_PARSED_RATIONALE_CODE,
    ReaderOrchestrator,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionResult,
    TranslationWorkerService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
)

pytestmark = pytest.mark.anyio


class _StaticTranslator:
    """Fake translator that returns a fixed translation output."""

    def __init__(self, output: TranslationLayerOutput) -> None:
        self.output = output
        self.calls: list = []

    async def translate(self, context) -> TranslationExecutionResult:
        self.calls.append(context)
        return TranslationExecutionResult(
            output=self.output,
            usage_data={
                "aggregate": {
                    "input_tokens": 10,
                    "output_tokens": 15,
                    "total_tokens": 25,
                }
            },
            prompt_version="test-orchestrator",
            model_profile="fake-profile",
            model_provider="fake-provider",
            model_name="fake-model",
        )


def _translation_output(text: str = "第一句。\n\n第二段。") -> TranslationLayerOutput:
    return TranslationLayerOutput(
        target_language="zh-CN",
        translated_text=text,
        notes=[],
        confidence="normal",
    )


@pytest.fixture
async def orchestrator_env() -> asyncpg.Pool:
    schema_name = f"test_reader_orchestrator_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _make_orchestrator(
    pool: asyncpg.Pool,
    *,
    translator: _StaticTranslator | None = None,
) -> ReaderOrchestrator:
    worker = TranslationWorkerService(pool=pool, translator=translator) if translator else None
    return ReaderOrchestrator(pool=pool, worker_service=worker)


async def _count_translation_jobs(pool: asyncpg.Pool, record_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
            """,
            record_id,
        )


async def _count_active_translation_jobs(pool: asyncpg.Pool, record_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
            """,
            record_id,
        )


async def _count_translation_layers(pool: asyncpg.Pool, record_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'translation'
              AND status = 'published'
            """,
            record_id,
        )


async def _count_parsed_decisions(pool: asyncpg.Pool, record_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM parsed_decisions WHERE reading_record_id = $1",
            record_id,
        )


async def test_submit_and_bootstrap_creates_translation_job(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    orchestrator = _make_orchestrator(orchestrator_env)

    result = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    assert result.record_id is not None
    assert result.base_id is not None
    assert result.article_ready_sequence == 1

    job_count = await _count_translation_jobs(orchestrator_env, result.record_id)
    assert job_count == 1

    active_count = await _count_active_translation_jobs(orchestrator_env, result.record_id)
    assert active_count == 1


async def test_tick_publishes_translation_layer(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    tick_result = await orchestrator.tick_translation_worker(
        lease_owner="tick-test",
        lease_duration=timedelta(seconds=30),
    )

    assert tick_result.worker_result is not None
    assert tick_result.worker_result.status == "succeeded"
    assert tick_result.worker_result.published_layer is not None
    assert tick_result.worker_result.published_layer.reading_record_id == article.record_id
    assert len(translator.calls) == 1

    layer_count = await _count_translation_layers(orchestrator_env, article.record_id)
    assert layer_count == 1


async def test_tick_writes_parsed_decision_after_publish(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    tick_result = await orchestrator.tick_translation_worker(
        lease_owner="tick-parsed",
        lease_duration=timedelta(seconds=30),
    )

    assert tick_result.parsed_decision_written is True

    async with orchestrator_env.acquire() as conn:
        decision_row = await conn.fetchrow(
            """
            SELECT unit_id, policy_code, parsed_state, rationale_code,
                   source_layer_id, source_job_id
            FROM parsed_decisions
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    assert decision_row is not None
    assert decision_row["policy_code"] == TRANSLATION_PARSED_POLICY_CODE
    assert decision_row["parsed_state"] == "parsed"
    assert decision_row["rationale_code"] == TRANSLATION_PARSED_RATIONALE_CODE
    assert decision_row["source_layer_id"] == tick_result.worker_result.published_layer.layer_id
    assert decision_row["source_job_id"] == tick_result.worker_result.context.job_id


async def test_snapshot_reload_contains_translation_layer_and_parsed_decision(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    await orchestrator.tick_translation_worker(
        lease_owner="tick-snapshot",
        lease_duration=timedelta(seconds=30),
    )

    snapshot = await ArticleReadyPersistenceService(
        pool=orchestrator_env
    ).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert len(snapshot.enhancement_layers) == 1
    assert snapshot.enhancement_layers[0].layer_type == "translation"
    assert snapshot.enhancement_layers[0].status == "published"

    assert len(snapshot.parsed_decisions) == 1
    decision = snapshot.parsed_decisions[0]
    assert decision.policy_code == TRANSLATION_PARSED_POLICY_CODE
    assert decision.parsed_state == "parsed"
    assert decision.rationale_code == TRANSLATION_PARSED_RATIONALE_CODE


async def test_polling_after_article_ready_returns_layer_published_event(
    orchestrator_env: asyncpg.Pool,
) -> None:
    from app.services.reader_orchestration.event_runtime import ReaderEventRuntime

    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    await orchestrator.tick_translation_worker(
        lease_owner="tick-poll",
        lease_duration=timedelta(seconds=30),
    )

    runtime = ReaderEventRuntime(pool=orchestrator_env)
    poll_result = await runtime.poll_events(
        record_id=article.record_id,
        user_id=user_id,
        after_sequence=article.article_ready_sequence,
        limit=50,
    )

    event_types = [event.event_type for event in poll_result.events]
    assert "layer_published" in event_types

    layer_published_events = [
        event for event in poll_result.events if event.event_type == "layer_published"
    ]
    assert len(layer_published_events) == 1
    assert layer_published_events[0].sequence > article.article_ready_sequence

    parsed_decision_events = [
        event for event in poll_result.events if event.event_type == "parsed_decision_updated"
    ]
    assert len(parsed_decision_events) == 1
    assert (
        parsed_decision_events[0].sequence > layer_published_events[0].sequence
    )


async def test_repeated_bootstrap_does_not_create_duplicate_active_job_or_layer(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    active_after_first = await _count_active_translation_jobs(
        orchestrator_env, article.record_id
    )
    assert active_after_first == 1

    from app.services.reader_orchestration.job_bootstrap import (
        TranslationJobBootstrapService,
    )

    bootstrap = TranslationJobBootstrapService(pool=orchestrator_env)
    await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    active_after_second = await _count_active_translation_jobs(
        orchestrator_env, article.record_id
    )
    assert active_after_second == 1

    total_jobs = await _count_translation_jobs(orchestrator_env, article.record_id)
    assert total_jobs == 1

    await orchestrator.tick_translation_worker(
        lease_owner="tick-idempotent",
        lease_duration=timedelta(seconds=30),
    )

    layer_count = await _count_translation_layers(orchestrator_env, article.record_id)
    assert layer_count == 1

    second_tick = await orchestrator.tick_translation_worker(
        lease_owner="tick-idempotent-2",
        lease_duration=timedelta(seconds=30),
    )
    assert second_tick.worker_result is None
    assert second_tick.parsed_decision_written is False

    layer_count_after_second_tick = await _count_translation_layers(
        orchestrator_env, article.record_id
    )
    assert layer_count_after_second_tick == 1


async def test_tick_rejects_active_base_mismatch(
    orchestrator_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(orchestrator_env)
    translator = _StaticTranslator(_translation_output())
    orchestrator = _make_orchestrator(orchestrator_env, translator=translator)

    article = await orchestrator.submit_plain_text_and_bootstrap_translation(
        _plain_text_request(user_id),
    )

    async with orchestrator_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            article.record_id,
        )

    tick_result = await orchestrator.tick_translation_worker(
        lease_owner="tick-stale",
        lease_duration=timedelta(seconds=30),
    )

    assert tick_result.worker_result is None
    assert tick_result.parsed_decision_written is False

    async with orchestrator_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
            """,
            article.record_id,
        )

    assert job_status == "superseded"

    layer_count = await _count_translation_layers(orchestrator_env, article.record_id)
    assert layer_count == 0

    decision_count = await _count_parsed_decisions(orchestrator_env, article.record_id)
    assert decision_count == 0


async def test_no_render_scene_json_references_in_orchestration_module(
    orchestrator_env: asyncpg.Pool,
) -> None:
    from pathlib import Path

    from app.services.reader_orchestration import orchestrator as orchestrator_module

    module_path = Path(orchestrator_module.__file__)
    module_text = module_path.read_text(encoding="utf-8")
    assert "render_scene_json" not in module_text
    assert "render_scene" not in module_text


def _plain_text_request(user_id):
    from app.services.reader_orchestration.article_ready_service import (
        PlainTextArticleReadySubmitRequest,
    )

    return PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="First sentence.\n\nSecond paragraph for translation.",
        title="Orchestrator Slice",
        language="en",
    )
