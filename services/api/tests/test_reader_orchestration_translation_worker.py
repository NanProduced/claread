from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.schemas.reader_orchestration import TranslationLayerOutput
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_OPERATION_FINGERPRINT,
    TranslationJobBootstrapService,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationWorkerService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


class _StaticTranslator:
    def __init__(self, output: TranslationLayerOutput) -> None:
        self.output = output
        self.calls = []
        self.usage_data = {
            "aggregate": {
                "input_tokens": 12,
                "output_tokens": 18,
                "total_tokens": 30,
            }
        }

    async def translate(self, context) -> TranslationExecutionResult:
        self.calls.append(context)
        return TranslationExecutionResult(
            output=self.output,
            usage_data=self.usage_data,
            prompt_version="test-translation-worker",
            model_profile="fake-profile",
            model_provider="fake-provider",
            model_name="fake-model",
        )


class _FailingTranslator:
    def __init__(self, error: TranslationExecutionError) -> None:
        self.error = error

    async def translate(self, context) -> TranslationExecutionResult:
        raise self.error


@pytest.fixture
async def translation_worker_env() -> asyncpg.Pool:
    schema_name = f"test_reader_translation_worker_{uuid4().hex}"
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


def _translation_output(text: str = "第一句。\n\n第二段。") -> TranslationLayerOutput:
    return TranslationLayerOutput(
        target_language="zh-CN",
        translated_text=text,
        notes=[],
        confidence="normal",
    )


def _translation_nodes(snapshot) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    for unit_node in snapshot.value:
        for child in unit_node["children"]:  # type: ignore[index]
            if isinstance(child, dict) and child.get("type") == "reader_translation":
                nodes.append(child)
    return nodes


async def _insert_build_base_job(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    priority: int,
) -> tuple[UUID, UUID, UUID]:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title, language, generation)
            VALUES ($1, 'text', 'Build Base Record', 'en', 1)
            RETURNING id
            """,
            user_id,
        )
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'initial_build', 'queued', 1, '{}'::jsonb, 'd4-p1-test', 'system')
            RETURNING id
            """,
            record_id,
            user_id,
        )
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint, idempotency_key
            )
            VALUES (
                $1, NULL, $2, $3,
                'build_base', 'record', $4, 'queued',
                $5, 1, 'build-base-v1', $6
            )
            RETURNING id
            """,
            record_id,
            run_id,
            user_id,
            str(record_id),
            priority,
            f"build-base:{record_id}",
        )
    assert isinstance(record_id, UUID)
    assert isinstance(run_id, UUID)
    assert isinstance(job_id, UUID)
    return record_id, run_id, job_id


async def test_bootstrap_creates_translation_run_and_job_with_expected_fingerprint(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)

    result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert result.base_id == article.base_id
    assert result.expected_generation == 1
    assert result.operation_fingerprint == TRANSLATION_OPERATION_FINGERPRINT
    assert result.unit_id == article.snapshot.navigation.units[0].unit_id

    async with translation_worker_env.acquire() as conn:
        run_row = await conn.fetchrow(
            """
            SELECT run_type, status, record_generation, trigger_kind, policy_version
            FROM reader_runs
            WHERE id = $1
            """,
            result.run_id,
        )
        job_row = await conn.fetchrow(
            """
            SELECT base_id, target_type, target_key, status, expected_generation,
                   operation_fingerprint, max_attempts
            FROM reader_jobs
            WHERE id = $1
            """,
            result.job_id,
        )

    assert run_row is not None
    assert run_row["run_type"] == "translation_layer"
    assert run_row["status"] == "queued"
    assert run_row["record_generation"] == 1
    assert run_row["trigger_kind"] == "system"
    assert isinstance(run_row["policy_version"], str)

    assert job_row is not None
    assert job_row["base_id"] == article.base_id
    assert job_row["target_type"] == "unit"
    assert job_row["target_key"] == result.unit_id
    assert job_row["status"] == "queued"
    assert job_row["expected_generation"] == 1
    assert job_row["operation_fingerprint"] == TRANSLATION_OPERATION_FINGERPRINT
    assert job_row["max_attempts"] == 3


async def test_worker_process_publishes_translation_and_snapshot_reload_projects_it(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    translator = _StaticTranslator(_translation_output())
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=translator,
    )

    result = await worker.process_next_translation_job(
        lease_owner="worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.published_layer is not None
    assert len(translator.calls) == 1
    assert translator.calls[0].unit_id == article.snapshot.navigation.units[0].unit_id

    async with translation_worker_env.acquire() as conn:
        layer_row = await conn.fetchrow(
            """
            SELECT layer_type, target_scope, target_key, generation, status, output_json
            FROM enhancement_layers
            WHERE id = $1
            """,
            result.published_layer.layer_id,
        )
        event_row = await conn.fetchrow(
            """
            SELECT sequence, event_type, source_job_id, source_layer_id
            FROM reader_events
            WHERE id = $1
            """,
            result.published_layer.event.event_id,
        )
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )
        usage_row = await conn.fetchrow(
            """
            SELECT status, capability_code, usage_scope, billing_mode,
                   model_route, model_profile_id, model_provider, model_name,
                   reader_run_id, reader_job_id, enhancement_layer_id,
                   input_tokens, output_tokens, total_tokens, operation_fingerprint
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )

    assert layer_row is not None
    assert layer_row["layer_type"] == "translation"
    assert layer_row["target_scope"] == "unit"
    assert layer_row["target_key"] == result.context.unit_id
    assert layer_row["generation"] == 1
    assert layer_row["status"] == "published"
    assert layer_row["output_json"]["translated_text"] == "第一句。\n\n第二段。"

    assert event_row is not None
    assert event_row["sequence"] == 2
    assert event_row["event_type"] == "layer_published"
    assert event_row["source_job_id"] == result.claim.job_id
    assert event_row["source_layer_id"] == result.published_layer.layer_id

    assert job_row is not None and job_row["status"] == "succeeded"
    assert run_row is not None and run_row["status"] == "completed"
    assert usage_row is not None
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["usage_scope"] == "system_internal"
    assert usage_row["billing_mode"] == "internal_only"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["enhancement_layer_id"] == result.published_layer.layer_id
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["operation_fingerprint"] == result.context.operation_fingerprint

    snapshot = await ArticleReadyPersistenceService(pool=translation_worker_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )
    translation_nodes = _translation_nodes(snapshot)

    assert [layer.layer_id for layer in snapshot.enhancement_layers] == [
        str(result.published_layer.layer_id)
    ]
    assert [node["layer_id"] for node in translation_nodes] == [
        str(result.published_layer.layer_id)
    ]
    assert translation_nodes[0]["base_id"] == str(article.base_id)
    assert translation_nodes[0]["unit_id"] == result.context.unit_id
    assert translation_nodes[0]["children"][0]["text"] == "第一句。\n\n第二段。"


async def test_worker_retryable_failure_moves_job_to_retry_later_and_run_failed_retryable(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_FailingTranslator(
            TranslationExecutionError(
                "temporary provider timeout",
                retryable=True,
                failure_class="provider",
                failure_code="provider_timeout",
            )
        ),
    )

    started_at = datetime.now(UTC)
    result = await worker.process_next_translation_job(
        lease_owner="worker-retry",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=3),
    )

    assert result is not None
    assert result.status == "retry_later"

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, rationale_code, available_at
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs
            WHERE id = $1
            """,
            result.claim.run_id,
        )

    assert job_row is not None
    assert job_row["status"] == "retry_later"
    assert job_row["rationale_code"] == "provider_timeout"
    assert job_row["available_at"] > started_at

    assert run_row is not None
    assert run_row["status"] == "failed_retryable"
    assert run_row["failure_class"] == "provider"
    assert run_row["failure_code"] == "provider_timeout"
    assert run_row["finished_at"] is None


async def test_worker_claim_filters_out_non_translation_jobs_in_mixed_queue(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    _build_record_id, _build_run_id, build_job_id = await _insert_build_base_job(
        translation_worker_env,
        user_id=user_id,
        priority=99,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_output()),
    )

    claim = await worker.claim_translation_job(
        lease_owner="translation-only",
        lease_duration=timedelta(seconds=30),
    )

    assert claim is not None
    assert claim.job_type == "translate_unit"
    assert claim.target_type == "unit"

    async with translation_worker_env.acquire() as conn:
        build_job_row = await conn.fetchrow(
            "SELECT status, lease_token FROM reader_jobs WHERE id = $1",
            build_job_id,
        )
    assert build_job_row is not None
    assert build_job_row["status"] == "queued"
    assert build_job_row["lease_token"] is None


async def test_worker_retry_later_then_success_clears_run_failure_fields(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    retry_worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_FailingTranslator(
            TranslationExecutionError(
                "temporary provider timeout",
                retryable=True,
                failure_class="provider",
                failure_code="provider_timeout",
            )
        ),
    )

    retry_result = await retry_worker.process_next_translation_job(
        lease_owner="worker-retry-then-success",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(seconds=0),
    )
    assert retry_result is not None
    assert retry_result.status == "retry_later"

    success_worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_output("恢复后的译文")),
    )
    success_result = await success_worker.process_next_translation_job(
        lease_owner="worker-retry-then-success",
        lease_duration=timedelta(seconds=30),
    )

    assert success_result is not None
    assert success_result.status == "succeeded"

    async with translation_worker_env.acquire() as conn:
        run_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs
            WHERE id = $1
            """,
            success_result.claim.run_id,
        )
        failed_usage_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM ai_usage_events
            WHERE reader_job_id = $1
              AND status = 'failed'
            """,
            success_result.claim.job_id,
        )

    assert run_row is not None
    assert run_row["status"] == "completed"
    assert run_row["failure_class"] is None
    assert run_row["failure_code"] is None
    assert run_row["finished_at"] is not None
    assert failed_usage_count == 1


async def test_worker_terminal_failure_moves_job_to_failed_terminal_and_run_failed_terminal(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_FailingTranslator(
            TranslationExecutionError(
                "unsupported language pair",
                retryable=False,
                failure_class="policy",
                failure_code="unsupported_language_pair",
            )
        ),
    )

    result = await worker.process_next_translation_job(
        lease_owner="worker-terminal",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, failure_message, rationale_code
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs
            WHERE id = $1
            """,
            result.claim.run_id,
        )

    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "policy"
    assert job_row["failure_code"] == "unsupported_language_pair"
    assert "unsupported language pair" in job_row["failure_message"]
    assert job_row["rationale_code"] == "unsupported_language_pair"

    assert run_row is not None
    assert run_row["status"] == "failed_terminal"
    assert run_row["failure_class"] == "policy"
    assert run_row["failure_code"] == "unsupported_language_pair"
    assert run_row["finished_at"] is not None


def test_translation_worker_module_does_not_reference_render_scene_json() -> None:
    path = API_ROOT / "app" / "services" / "reader_orchestration" / "translation_worker.py"
    assert "render_scene_json" not in path.read_text(encoding="utf-8")
