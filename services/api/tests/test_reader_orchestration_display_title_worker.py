from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.database import connection as db_connection
from app.llm.registry import build_model_registry
from app.llm.routes import MODEL_ROUTE_READER_TITLE_GENERATION
from app.services.model_execution_journal import CaptureEnvelopeConflictError
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.display_title_worker import (
    MAX_GENERATED_TITLE_CHARS,
    MAX_TITLE_SOURCE_CHARS,
    DisplayTitleExecutionResult,
    DisplayTitleGenerationError,
    DisplayTitleStructuredOutput,
    DisplayTitleWorkerService,
    build_display_title_generation_input,
    normalize_generated_title_zh,
)
from app.services.reader_orchestration.job_bootstrap import (
    DISPLAY_TITLE_OPERATION_FINGERPRINT,
    DisplayTitleJobBootstrapService,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
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


class _JournalInspectingTitleGenerator(_StaticTitleGenerator):
    def __init__(self, pool: asyncpg.Pool) -> None:
        super().__init__()
        self._pool = pool

    async def generate(self, context):
        async with self._pool.acquire() as conn:
            journal_row = await conn.fetchrow(
                """
                SELECT capture_state, usage_delivery_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                context.job_id,
            )
        assert journal_row is not None
        assert journal_row["capture_state"] == "started"
        assert journal_row["usage_delivery_state"] == "not_ready"
        assert journal_row["execution_slot"] == 1
        return await super().generate(context)


class _FailingTitleGenerator:
    def __init__(self, error: DisplayTitleGenerationError) -> None:
        self.error = error
        self.calls = []

    async def generate(self, context):
        self.calls.append(context)
        raise self.error


class _FailIfTitleProviderCalled:
    def __init__(self) -> None:
        self.calls = []

    async def generate(self, context):
        self.calls.append(context)
        raise AssertionError("captured resume must not call the title provider")


class _CrashAfterTitleCaptureWorker(DisplayTitleWorkerService):
    async def _complete_title_job_success(self, **kwargs) -> None:
        del kwargs
        raise RuntimeError("crash after title capture before publish")


class _StaleFenceTitleWorker(DisplayTitleWorkerService):
    async def _complete_title_job_success(self, **kwargs) -> None:
        del kwargs
        raise FenceViolationError("stale title publication fence")


class _CaptureFailingJournal:
    def __init__(self, pool: asyncpg.Pool, error: Exception) -> None:
        self._delegate = ModelExecutionJournalService(pool=pool)
        self._error = error
        self.capture_calls = 0

    async def begin_execution(self, **kwargs):
        return await self._delegate.begin_execution(**kwargs)

    async def capture_execution(self, **kwargs):
        del kwargs
        self.capture_calls += 1
        raise self._error


class _MaterializerFailingJournal:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._delegate = ModelExecutionJournalService(pool=pool)

    async def begin_execution(self, **kwargs):
        return await self._delegate.begin_execution(**kwargs)

    async def capture_execution(self, **kwargs):
        return await self._delegate.capture_execution(**kwargs)

    async def materialize_pending(self):
        raise RuntimeError("materializer unavailable")

    async def load_captured_receipt(self, **kwargs):
        return await self._delegate.load_captured_receipt(**kwargs)


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
    assert _fingerprint_matches_base(
        result.operation_fingerprint, DISPLAY_TITLE_OPERATION_FINGERPRINT
    )
    assert result.operation_fingerprint != DISPLAY_TITLE_OPERATION_FINGERPRINT
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
    generator = _JournalInspectingTitleGenerator(display_title_worker_env)
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
        journal_row = await conn.fetchrow(
            """
            SELECT invocation_key, invocation_kind, capture_state,
                   usage_delivery_state, capture_envelope_sha256
            FROM ai_model_execution_journal
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
    assert journal_row["invocation_key"] == (
        f"reader:reader_title_generation:{result.claim.job_id}:1:1"
    )
    assert journal_row["invocation_kind"] == "reader.display_title"
    assert journal_row["capture_state"] == "captured"
    assert journal_row["usage_delivery_state"] == "reconciled"
    assert len(journal_row["capture_envelope_sha256"]) == 64

    snapshot = await ArticleReadyPersistenceService(
        pool=display_title_worker_env
    ).load_snapshot(record_id=article.record_id, user_id=user_id)
    assert snapshot.record.display_title_zh == "城市补贴政策争议"
    assert snapshot.record.title_generation_status == "succeeded"
    assert snapshot.record.title_generation_error_code is None


@pytest.mark.parametrize(
    "delivery_state",
    ["pending", "reconciled", "dead_letter"],
)
async def test_display_title_captured_restart_resumes_without_provider_recall(
    display_title_worker_env: asyncpg.Pool,
    delivery_state: str,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text="A durable title must survive a crash before publication.",
        title="Durable title",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    first_generator = _StaticTitleGenerator("崩溃后仍可恢复的标题")
    first_worker = _CrashAfterTitleCaptureWorker(
        pool=display_title_worker_env,
        generator=first_generator,
    )

    crashed = await first_worker.process_next_display_title_job(
        lease_owner="title-worker-before-crash",
        lease_duration=timedelta(seconds=30),
    )

    assert crashed is not None
    assert crashed.status == "paused"
    assert len(first_generator.calls) == 1

    async with display_title_worker_env.acquire() as conn:
        if delivery_state != "reconciled":
            await conn.execute(
                """
                UPDATE ai_model_execution_journal
                SET usage_delivery_state = $2,
                    ai_usage_event_id = NULL,
                    reconciled_at = NULL,
                    dead_lettered_at = CASE
                        WHEN $2 = 'dead_letter' THEN NOW()
                        ELSE NULL
                    END
                WHERE reader_job_id = $1
                """,
                crashed.claim.job_id,
                delivery_state,
            )

    fail_if_called = _FailIfTitleProviderCalled()
    resumed = await DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=fail_if_called,
    ).process_next_display_title_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="title-worker-recovery",
        lease_duration=timedelta(seconds=30),
    )

    assert resumed is not None
    assert resumed.status == "succeeded"
    assert resumed.title_zh == "崩溃后仍可恢复的标题"
    assert fail_if_called.calls == []
    async with display_title_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, attempt_count FROM reader_jobs WHERE id = $1",
            crashed.claim.job_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            crashed.claim.job_id,
        )
    assert job_row["status"] == "succeeded"
    assert job_row["attempt_count"] == 1
    assert usage_count == 1


@pytest.mark.parametrize(
    ("capture_error", "expected_rationale", "expected_failure"),
    [
        (
            RuntimeError("capture unavailable"),
            "model_execution_ambiguous",
            "provider_outcome_ambiguous",
        ),
        (
            CaptureEnvelopeConflictError("capture conflict"),
            "model_execution_capture_conflict",
            "capture_envelope_conflict",
        ),
    ],
)
async def test_display_title_capture_failure_pauses_without_publish(
    display_title_worker_env: asyncpg.Pool,
    capture_error: Exception,
    expected_rationale: str,
    expected_failure: str,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text="Capture must complete before title publication.",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    journal = _CaptureFailingJournal(display_title_worker_env, capture_error)
    result = await DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=_StaticTitleGenerator(),
        journal_service=journal,
    ).process_next_display_title_job(
        lease_owner="title-capture-failure",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "paused"
    assert journal.capture_calls == 1
    async with display_title_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, pause_owner, rationale_code, failure_code
            FROM reader_jobs WHERE id = $1
            """,
            result.claim.job_id,
        )
        record_title = await conn.fetchval(
            "SELECT generated_title_zh FROM reading_records WHERE id = $1",
            article.record_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            result.claim.job_id,
        )
    assert dict(job_row) == {
        "status": "paused",
        "pause_owner": "system",
        "rationale_code": expected_rationale,
        "failure_code": expected_failure,
    }
    assert record_title is None
    assert usage_count == 0


async def test_display_title_materializer_failure_does_not_block_publish(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text="Usage delivery is independent from title publication.",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    result = await DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=_StaticTitleGenerator(),
        journal_service=_MaterializerFailingJournal(display_title_worker_env),
    ).process_next_display_title_job(
        lease_owner="title-materializer-failure",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    journal_service = ModelExecutionJournalService(pool=display_title_worker_env)
    await journal_service.materialize_pending()
    await journal_service.materialize_pending()
    async with display_title_worker_env.acquire() as conn:
        journal_state = await conn.fetchval(
            """
            SELECT usage_delivery_state FROM ai_model_execution_journal
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            result.claim.job_id,
        )
    assert journal_state == "reconciled"
    assert usage_count == 1


async def test_display_title_tampered_receipt_fails_closed_without_provider_recall(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text="Tampered title receipts must never be published.",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    crashed = await _CrashAfterTitleCaptureWorker(
        pool=display_title_worker_env,
        generator=_StaticTitleGenerator(),
    ).process_next_display_title_job(
        lease_owner="title-before-tamper",
        lease_duration=timedelta(seconds=30),
    )
    assert crashed is not None
    assert crashed.status == "paused"
    async with display_title_worker_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET normalized_payload_json = jsonb_build_object(
                    'title_zh', '被篡改的标题'
                )
            WHERE reader_job_id = $1
            """,
            crashed.claim.job_id,
        )

    fail_if_called = _FailIfTitleProviderCalled()
    resumed = await DisplayTitleWorkerService(
        pool=display_title_worker_env,
        generator=fail_if_called,
    ).process_next_display_title_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="title-after-tamper",
        lease_duration=timedelta(seconds=30),
    )

    assert resumed is None
    assert fail_if_called.calls == []
    async with display_title_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, pause_owner, rationale_code, failure_code
            FROM reader_jobs WHERE id = $1
            """,
            crashed.claim.job_id,
        )
        record_title = await conn.fetchval(
            "SELECT generated_title_zh FROM reading_records WHERE id = $1",
            article.record_id,
        )
    assert dict(job_row) == {
        "status": "paused",
        "pause_owner": "system",
        "rationale_code": "model_execution_receipt_invalid",
        "failure_code": "receipt_payload_invalid",
    }
    assert record_title is None


async def test_display_title_stale_fence_does_not_publish_captured_result(
    display_title_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(display_title_worker_env)
    article = await submit_article_ready(
        display_title_worker_env,
        user_id=user_id,
        plain_text="A stale generation must reject title publication.",
    )
    await _bootstrap_title_job(
        display_title_worker_env,
        record_id=article.record_id,
        user_id=user_id,
    )
    with pytest.raises(FenceViolationError):
        await _StaleFenceTitleWorker(
            pool=display_title_worker_env,
            generator=_StaticTitleGenerator(),
        ).process_next_display_title_job(
            lease_owner="title-stale-fence",
            lease_duration=timedelta(seconds=30),
        )

    async with display_title_worker_env.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job.status, record.generated_title_zh, journal.capture_state,
                   journal.usage_delivery_state
            FROM reader_jobs job
            JOIN reading_records record ON record.id = job.reading_record_id
            JOIN ai_model_execution_journal journal ON journal.reader_job_id = job.id
            WHERE job.job_type = 'generate_display_title_zh'
            ORDER BY job.created_at DESC
            LIMIT 1
            """
        )
        usage_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM ai_usage_events usage
            JOIN reader_jobs job ON job.id = usage.reader_job_id
            WHERE job.job_type = 'generate_display_title_zh'
            """
        )
    assert row["status"] == "superseded"
    assert row["generated_title_zh"] is None
    assert row["capture_state"] == "captured"
    assert row["usage_delivery_state"] == "reconciled"
    assert usage_count == 1


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
        journal_state = await conn.fetchval(
            """
            SELECT capture_state FROM ai_model_execution_journal
            WHERE reader_job_id = $1
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
    assert journal_state == "started"

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


def test_display_title_output_contract_uses_32_character_hard_limit() -> None:
    valid_title = "中" * MAX_GENERATED_TITLE_CHARS
    too_long_title = "中" * (MAX_GENERATED_TITLE_CHARS + 1)

    assert DisplayTitleStructuredOutput(title_zh=valid_title).title_zh == valid_title
    assert normalize_generated_title_zh(valid_title) == valid_title

    with pytest.raises(ValidationError):
        DisplayTitleStructuredOutput(title_zh=too_long_title)
    with pytest.raises(ValueError, match="too long"):
        normalize_generated_title_zh(too_long_title)


def test_display_title_base_text_fallback_preview_is_explicitly_bounded() -> None:
    sentinel = "DO_NOT_SEND_FULL_TEXT_SENTINEL"
    base_text = ("Alpha beta gamma delta. " * 260) + sentinel

    title_input = build_display_title_generation_input(
        record_title="Fallback Source Title",
        base_title_snapshot=None,
        source_type="plain_text",
        source_language="en",
        source_metadata={},
        base_text=base_text,
        base_char_length=len(base_text),
        stable_rows=(),
        unit_rows=(),
    )

    assert title_input.input_strategy == "base_text_preview"
    assert title_input.preview_char_length <= MAX_TITLE_SOURCE_CHARS
    assert sentinel not in title_input.content_preview
    assert base_text not in title_input.content_preview


def test_reader_title_route_requires_explicit_model_profile() -> None:
    registry_without_title = build_model_registry(
        Settings(
            annotation_model_profile="annotation",
            reader_translation_model_profile="translation",
            reader_title_model_profile="",
        )
    )
    registry_with_title = build_model_registry(
        Settings(
            annotation_model_profile="annotation",
            reader_translation_model_profile="translation",
            reader_title_model_profile="reader_title",
        )
    )

    assert MODEL_ROUTE_READER_TITLE_GENERATION not in registry_without_title.route_defaults
    assert (
        registry_with_title.route_defaults[MODEL_ROUTE_READER_TITLE_GENERATION]
        == "reader_title"
    )
