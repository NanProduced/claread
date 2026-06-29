from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.display_title_worker import (
    MAX_TITLE_SOURCE_CHARS,
    DisplayTitleExecutionResult,
    DisplayTitleGenerationError,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.job_bootstrap import (
    DISPLAY_TITLE_OPERATION_FINGERPRINT,
    DisplayTitleJobBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


class _StaticTitleGenerator:
    def __init__(self, title_zh: str = "城市补贴政策争议") -> None:
        self.title_zh = title_zh
        self.calls = []

    async def generate(self, context):
        self.calls.append(context)
        return DisplayTitleExecutionResult(
            title_zh=self.title_zh,
            usage_data={
                "aggregate": {
                    "input_tokens": 24,
                    "output_tokens": 8,
                    "total_tokens": 32,
                }
            },
            prompt_version="test-display-title",
            model_profile="fake-title-profile",
            model_provider="fake-provider",
            model_name="fake-title-model",
        )


class _FailingTitleGenerator:
    def __init__(self, error: DisplayTitleGenerationError) -> None:
        self.error = error
        self.calls = []

    async def generate(self, context):
        self.calls.append(context)
        raise self.error


@pytest.fixture
async def display_title_worker_env() -> asyncpg.Pool:
    schema_name = f"test_reader_display_title_{uuid4().hex}"
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


async def _bootstrap_title_job(pool: asyncpg.Pool, *, record_id, user_id):
    bootstrap = DisplayTitleJobBootstrapService(pool=pool)
    result = await bootstrap.bootstrap_display_title_job(
        record_id=record_id,
        user_id=user_id,
    )
    assert result is not None
    assert result.operation_fingerprint == DISPLAY_TITLE_OPERATION_FINGERPRINT
    return result


async def test_display_title_worker_generates_chinese_title_and_snapshot_field(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text=(
            "City officials debated whether to preserve emergency subsidies "
            "after shop owners warned that headline recovery numbers hid "
            "fragile street-level demand."
        ),
        title="Subsidy hearing",
    )
    assert article.snapshot.record.display_title_zh is None
    assert article.snapshot.record.title_generation_status == "pending"

    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    generator = _StaticTitleGenerator()
    worker = DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=generator,
    )

    result = await worker.process_next_display_title_job(
        lease_owner="title-worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.title_zh == "城市补贴政策争议"
    assert len(generator.calls) == 1

    async with display_title_worker_env.acquire() as conn:
        record_row = await conn.fetchrow(
            """
            SELECT generated_title_zh, title_generation_status,
                   title_generation_error_code, title_generation_attempt_count
            FROM reading_records
            WHERE id = $1
            """,
            article.record_id,
        )
        job_row = await conn.fetchrow(
            "SELECT status, output_ref_json FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        usage_row = await conn.fetchrow(
            """
            SELECT status, capability_code, model_route, model_profile_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )

    assert record_row["generated_title_zh"] == "城市补贴政策争议"
    assert record_row["title_generation_status"] == "succeeded"
    assert record_row["title_generation_error_code"] is None
    assert record_row["title_generation_attempt_count"] == 1
    assert job_row["status"] == "succeeded"
    assert job_row["output_ref_json"]["generated_title_zh"] == "城市补贴政策争议"
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_title_generation"
    assert usage_row["model_route"] == "reader_title_generation"
    assert usage_row["model_profile_id"] == "fake-title-profile"

    snapshot = await ArticleReadyPersistenceService(
        pool=display_title_worker_env
    ).load_snapshot(record_id=article.record_id, user_id=user_id)
    assert snapshot.record.display_title_zh == "城市补贴政策争议"
    assert snapshot.record.title_generation_status == "succeeded"
    assert snapshot.record.title_generation_error_code is None


async def test_display_title_worker_failure_enters_retryable_state(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(display_title_worker_env, user_id=user_id)
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )

    generator = _FailingTitleGenerator(
        DisplayTitleGenerationError(
            "provider temporarily unavailable",
            failure_class="provider",
            failure_code="provider_timeout",
        )
    )
    worker = DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=generator,
    )

    result = await worker.process_next_display_title_job(
        lease_owner="title-worker-1",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=10),
    )

    assert result is not None
    assert result.status == "retry_later"
    assert len(generator.calls) == 1

    async with display_title_worker_env.acquire() as conn:
        record_row = await conn.fetchrow(
            """
            SELECT generated_title_zh, title_generation_status,
                   title_generation_error_code, title_generation_error_message
            FROM reading_records
            WHERE id = $1
            """,
            article.record_id,
        )
        job_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, failure_message,
                   rationale_code, available_at
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )

    assert record_row["generated_title_zh"] is None
    assert record_row["title_generation_status"] == "failed_retryable"
    assert record_row["title_generation_error_code"] == "provider_timeout"
    assert "provider temporarily unavailable" in record_row[
        "title_generation_error_message"
    ]
    assert job_row["status"] == "retry_later"
    assert job_row["failure_class"] == "provider"
    assert job_row["failure_code"] == "provider_timeout"
    assert job_row["rationale_code"] == "display_title_generation_failed"
    assert job_row["available_at"] > result.claim.lease_expires_at

    snapshot = await ArticleReadyPersistenceService(
        pool=display_title_worker_env
    ).load_snapshot(record_id=article.record_id, user_id=user_id)
    assert snapshot.record.display_title_zh is None
    assert snapshot.record.title_generation_status == "failed_retryable"
    assert snapshot.record.title_generation_error_code == "provider_timeout"


async def test_display_title_worker_does_not_send_full_long_text_to_generator(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    sentinel = "DO_NOT_SEND_FULL_TEXT_SENTINEL"
    long_body = (
        "The city recovery committee reviewed export data and shopkeeper testimony. "
        * 220
    ) + sentinel
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text=long_body,
        title="Long policy hearing",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )

    generator = _StaticTitleGenerator("城市复苏听证争议")
    worker = DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=generator,
    )
    result = await worker.process_next_display_title_job(
        lease_owner="title-worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    context = generator.calls[0]
    title_input = context.title_input
    assert title_input.base_char_length > title_input.preview_char_length
    assert title_input.preview_char_length <= MAX_TITLE_SOURCE_CHARS
    assert sentinel not in title_input.content_preview
    assert long_body not in title_input.content_preview
