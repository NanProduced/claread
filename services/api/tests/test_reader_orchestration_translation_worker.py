from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic_ai.agent import AgentRunResult

# Pre-existing environment guard: pydantic_ai._warnings.PydanticAIDeprecationWarning
# only exists in pydantic_ai >=1.107 (per services/api/pyproject.toml). When the
# installed version is older (e.g. 1.75 in CI/local), the symbol is absent and no
# such warning can be emitted, so the deprecation-absence assertion below degrades
# to vacuously true. This guard only unblocks test collection; it does not alter
# T1.1a translation-group semantics.
try:  # pragma: no cover - version-dependent import
    from pydantic_ai._warnings import PydanticAIDeprecationWarning  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - older pydantic_ai without the submodule
    class PydanticAIDeprecationWarning(DeprecationWarning):  # type: ignore[no-redef]
        """Fallback sentinel so ``issubclass`` checks remain valid on old versions."""

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.llm.agent_runner import extract_run_usage
from app.schemas.reader_orchestration import (
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
)
from app.services.model_execution_journal import CaptureEnvelopeConflictError
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.prompting.prompt_loader import load_agent_instructions
from app.services.reader_orchestration import translation_worker as translation_worker_module
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_OPERATION_FINGERPRINT,
    TranslationJobBootstrapService,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.layer_publisher import PublishedTranslationLayer
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.smoke_harness import (
    DevFakeTranslationBatchExecutor,
)
from app.services.reader_orchestration.translation_worker import (
    PydanticAITranslationExecutor,
    TranslationAnchorSegmentTarget,
    TranslationBatchExecutionResult,
    TranslationBatchJobContext,
    TranslationBatchUnitContext,
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationGroupPlan,
    TranslationJobContext,
    TranslationWorkerService,
    _build_translation_batch_prompt,
    _build_translation_prompt,
    _hydrate_translation_groups,
    _validate_translation_group_plan,
    _validate_translation_strategy_metadata,
    build_deterministic_translation_groups,
    hydrate_translation_batch_output,
    hydrate_translation_layer_output,
    plan_translation_groups,
)
from app.services.reader_orchestration.usage_attribution import (
    ReaderUsageAttributionService,
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
    def __init__(self, output) -> None:
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
        output = self.output(context) if callable(self.output) else self.output
        return TranslationExecutionResult(
            output=output,
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


class _CapturingPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def publish_unit_translation(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: TranslationLayerOutput,
        quality_json: dict[str, object] | None = None,
    ) -> PublishedTranslationLayer:
        self.calls.append(
            {
                "job_id": job_id,
                "lease_token": lease_token,
                "output": output,
                "quality_json": quality_json,
            }
        )
        return PublishedTranslationLayer(
            layer_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            reading_record_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            base_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            unit_id="u1",
            generation=1,
            event=SimpleNamespace(
                event_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                sequence=1,
                event_type="layer_published",
            ),
        )


class _JournalOrderTranslationPublisher:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.calls = 0

    async def _assert_captured(self, job_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT capture_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                job_id,
            )
        assert row is not None
        assert row["capture_state"] == "captured"
        assert row["execution_slot"] == 1

    async def publish_unit_translation(self, **kwargs) -> PublishedTranslationLayer:
        self.calls += 1
        await self._assert_captured(kwargs["job_id"])
        raise RuntimeError("stop after durable translation capture")

    async def publish_article_translation_batch(self, **kwargs) -> object:
        self.calls += 1
        await self._assert_captured(kwargs["job_id"])
        raise RuntimeError("stop after durable translation batch capture")


class _JournalOrderTranslator(_StaticTranslator):
    def __init__(self, pool: asyncpg.Pool, output) -> None:
        super().__init__(output)
        self._pool = pool

    async def translate(self, context) -> TranslationExecutionResult:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT capture_state, usage_delivery_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                context.job_id,
            )
        assert row is not None
        assert row["capture_state"] == "started"
        assert row["usage_delivery_state"] == "not_ready"
        assert row["execution_slot"] == 1
        return await super().translate(context)


class _JournalOrderBatchTranslator(DevFakeTranslationBatchExecutor):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT capture_state, usage_delivery_state, execution_slot
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                context.job_id,
            )
        assert row is not None
        assert row["capture_state"] == "started"
        assert row["usage_delivery_state"] == "not_ready"
        assert row["execution_slot"] == 1
        return await super().translate_batch(context)

class _FakeRunUsage:
    def __init__(self, *, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.details: dict[str, int] = {}

class _FakeCallableUsageResult:
    def __init__(self, output: object, usage: _FakeRunUsage) -> None:
        self.output = output
        self._usage = usage

    def usage(self) -> _FakeRunUsage:
        return self._usage


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


def _translation_generation_output(
    text: str = "第一句。\n\n第二段。",
    anchor_segment_ids: list[str] | None = None,
) -> TranslationLayerGenerationOutput:
    return TranslationLayerGenerationOutput(
        groups=[
            TranslationGenerationGroup(
                anchor_segment_ids=list(anchor_segment_ids or ["s1"]),
                translated_text=text,
            )
        ]
    )


def _build_context_with_segments(
    *,
    source_text: str = "Translation source text.",
    segment_specs: list[tuple[str, int, int, str, str, str | None]] | None = None,
    reading_goal: str = "daily_reading",
    reading_variant: str = "intermediate_reading",
) -> TranslationJobContext:
    """Build a TranslationJobContext with valid T6 strategy metadata.

    Uses the real resolver against the default daily_reading /
    intermediate_reading pair so that _build_translation_prompt produces a
    well-formed strategy section. Tests that need a different variant or
    invalid metadata build their own context or call _load_job_context.
    """
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    layer = strategy.layers["translation"]
    if segment_specs is None:
        segment_specs = [
            (
                "s1",
                0,
                utf16_code_unit_length(source_text),
                "sentence",
                "normal",
                "s1",
            )
        ]

    anchor_segments: list[TranslationAnchorSegmentTarget] = []
    for (
        anchor_segment_id,
        start_offset,
        end_offset,
        segment_type,
        boundary_quality,
        sentence_id,
    ) in segment_specs:
        segment_text = slice_by_utf16_offsets(source_text, start_offset, end_offset)
        assert segment_text is not None
        anchor_segments.append(
            TranslationAnchorSegmentTarget(
                anchor_segment_id=anchor_segment_id,
                sentence_id=sentence_id,
                order_index=len(anchor_segments) + 1,
                segment_type=segment_type,
                boundary_quality=boundary_quality,
                unit_start_utf16=start_offset,
                unit_end_utf16=end_offset,
                text_hash=compute_text_range_hash(segment_text),
                source_text=segment_text,
            )
        )

    return TranslationJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
        source_language="en",
        target_language="zh-CN",
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=tuple(anchor_segments),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
    )


def _translation_context(source_text: str = "Translation source text.") -> TranslationJobContext:
    return _build_context_with_segments(source_text=source_text)

def test_extract_run_usage_reads_agent_run_result_property_without_deprecation_warning() -> None:
    result = AgentRunResult(output=_translation_generation_output())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        usage = extract_run_usage(result)

    assert usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert not any(
        issubclass(warning.category, PydanticAIDeprecationWarning)
        and "AgentRunResult.usage" in str(warning.message)
        for warning in caught
    )

def test_extract_run_usage_keeps_legacy_callable_usage_compatibility() -> None:
    usage = extract_run_usage(
        _FakeCallableUsageResult(
            output=_translation_generation_output(),
            usage=_FakeRunUsage(input_tokens=7, output_tokens=5),
        )
    )

    assert usage == {
        "input_tokens": 7,
        "output_tokens": 5,
        "total_tokens": 12,
    }

@pytest.mark.anyio
async def test_real_executor_uses_non_deprecated_agent_retry_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _CapturingAgent:
        def __init__(self, *args, **kwargs) -> None:
            captured["kwargs"] = kwargs

        async def run(self, prompt: str) -> object:
            return SimpleNamespace(
                output=_translation_generation_output("通过捕获 agent 返回的译文")
            )

    monkeypatch.setattr(
        translation_worker_module,
        "build_model_for_route",
        lambda settings, route: (
            object(),
            SimpleNamespace(
                profile_name="reader-translation-profile",
                provider="stub-provider",
                model_name="stub-model",
                api_key="",
            ),
        ),
    )
    monkeypatch.setattr(
        translation_worker_module,
        "assert_real_llm_allowed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        translation_worker_module,
        "load_agent_instructions",
        lambda name: "stub translation instructions",
    )
    monkeypatch.setattr(translation_worker_module, "Agent", _CapturingAgent)

    executor = PydanticAITranslationExecutor()
    result = await executor.translate(_translation_context())

    assert result.output.groups[0].translated_text == "通过捕获 agent 返回的译文"
    assert result.model_profile == "reader-translation-profile"
    assert result.model_provider == "stub-provider"
    assert result.model_name == "stub-model"
    agent_kwargs = captured["kwargs"]
    assert isinstance(agent_kwargs, dict)
    assert agent_kwargs["output_type"] is TranslationLayerGenerationOutput
    assert agent_kwargs["instructions"] == "stub translation instructions"
    assert agent_kwargs["name"] == "reader_layer_translation_agent"
    assert agent_kwargs["retries"] == {"tools": 1, "output": 2}
    assert "output_retries" not in agent_kwargs
    assert "instrument" not in agent_kwargs


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
    assert _fingerprint_matches_base(
        result.operation_fingerprint, TRANSLATION_OPERATION_FINGERPRINT
    )
    assert result.operation_fingerprint != TRANSLATION_OPERATION_FINGERPRINT
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
    assert _fingerprint_matches_base(
        job_row["operation_fingerprint"], TRANSLATION_OPERATION_FINGERPRINT
    )
    assert job_row["operation_fingerprint"] != TRANSLATION_OPERATION_FINGERPRINT
    assert job_row["max_attempts"] == 3


async def test_worker_process_hydrates_generation_output_and_passes_durable_output_to_publisher(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    publisher = _CapturingPublisher()
    translator = _StaticTranslator(
        lambda context: _translation_generation_output(
            "第一句。\n\n第二段。",
            [context.anchor_segments[0].anchor_segment_id],
        )
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=translator,
        layer_publisher=publisher,
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
    assert len(publisher.calls) == 1
    assert translator.calls[0].unit_id == article.snapshot.navigation.units[0].unit_id
    published_output = publisher.calls[0]["output"]
    assert isinstance(published_output, TranslationLayerOutput)
    assert result.output.model_dump(mode="json") == published_output.model_dump(mode="json")
    assert published_output.groups[0].translated_text == "第一句。\n\n第二段。"
    assert published_output.groups[0].group_id == f"{result.context.unit_id}_g1_1"
    assert published_output.groups[0].source_text_hash == compute_text_range_hash(
        result.context.anchor_segments[0].source_text
    )
    assert set(published_output.model_dump(mode="json")["groups"][0].keys()) == {
        "group_id",
        "anchor_segment_ids",
        "source_text_hash",
        "translated_text",
    }
    quality_json = publisher.calls[0]["quality_json"]
    assert quality_json == {
        "group_count": 1,
        "prompt_version": "test-translation-worker",
        "model_route": "reader_layer_translation",
        "model_profile": "fake-profile",
        "model_provider": "fake-provider",
        "model_name": "fake-model",
        "translation_prompt_profile_contract_version": (
            result.context.translation_prompt_profile_contract_version
        ),
        "translation_prompt_profile_version": (
            result.context.translation_prompt_profile_version
        ),
        "translation_prompt_profile_manifest_hash": (
            result.context.translation_prompt_profile_manifest_hash
        ),
        "translation_prompt_profile_fingerprint_hash": (
            result.context.translation_prompt_profile_fingerprint_hash
        ),
    }


async def test_translation_unit_journals_started_then_captured_before_publish(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    publisher = _JournalOrderTranslationPublisher(translation_worker_env)
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_JournalOrderTranslator(
            translation_worker_env,
            _translation_generation_output(),
        ),
        layer_publisher=publisher,
    )

    result = await worker.process_next_translation_job(
        lease_owner="translation-journal-order",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "paused"
    assert publisher.calls == 1


async def test_translation_batch_journals_started_then_captured_before_publish(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap_result = await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    await _tamper_unit_job_into_batch(
        translation_worker_env,
        job_id=bootstrap_result.job_id,
        unit_id=bootstrap_result.unit_id,
        job_type="translate_article",
    )
    async with translation_worker_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET operation_fingerprint = regexp_replace(
                operation_fingerprint,
                '^translation_unit',
                'translation_article_v1'
            )
            WHERE id = $1
            """,
            bootstrap_result.job_id,
        )
    publisher = _JournalOrderTranslationPublisher(translation_worker_env)
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=_JournalOrderBatchTranslator(translation_worker_env),
        layer_publisher=publisher,
    )

    result = await worker.process_next_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-journal-order",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "paused"
    assert publisher.calls == 1


@pytest.mark.parametrize(
    "delivery_state",
    ["pending", "reconciled", "dead_letter"],
)
async def test_translation_unit_captured_restart_is_provider_free_and_delivery_orthogonal(
    translation_worker_env: asyncpg.Pool,
    delivery_state: str,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    first_translator = _StaticTranslator(
        lambda context: _translation_generation_output(
            "可恢复译文",
            [context.anchor_segments[0].anchor_segment_id],
        )
    )
    first = TranslationWorkerService(
        pool=translation_worker_env,
        translator=first_translator,
        layer_publisher=_JournalOrderTranslationPublisher(translation_worker_env),
    )

    paused = await first.process_next_translation_job(
        lease_owner="translation-before-restart",
        lease_duration=timedelta(seconds=30),
    )
    assert paused is not None and paused.status == "paused"
    async with translation_worker_env.acquire() as conn:
        paused_attempt = await conn.fetchval(
            "SELECT attempt_count FROM reader_jobs WHERE id = $1",
            paused.claim.job_id,
        )
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET usage_delivery_state = $2,
                ai_usage_event_id = CASE WHEN $2 = 'reconciled'
                                         THEN ai_usage_event_id ELSE NULL END,
                delivery_next_attempt_at = NULL,
                reconciled_at = CASE WHEN $2 = 'reconciled' THEN NOW() ELSE NULL END,
                dead_lettered_at = CASE WHEN $2 = 'dead_letter' THEN NOW() ELSE NULL END
            WHERE reader_job_id = $1
            """,
            paused.claim.job_id,
            delivery_state,
        )

    forbidden_translator = _StaticTranslator(_translation_generation_output("不应调用"))
    resumed = await TranslationWorkerService(
        pool=translation_worker_env,
        translator=forbidden_translator,
    ).process_next_translation_job(
        lease_owner="translation-after-restart",
        lease_duration=timedelta(seconds=30),
    )

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, failure_code, failure_message FROM reader_jobs WHERE id = $1",
            paused.claim.job_id,
        )
    assert resumed is not None and resumed.status == "succeeded", job_row
    assert resumed.output is not None
    assert resumed.output.groups[0].translated_text == "可恢复译文"
    assert forbidden_translator.calls == []
    async with translation_worker_env.acquire() as conn:
        final_attempt = await conn.fetchval(
            "SELECT attempt_count FROM reader_jobs WHERE id = $1",
            resumed.claim.job_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            resumed.claim.job_id,
        )
    assert final_attempt == paused_attempt
    assert usage_count == 1


async def test_translation_batch_captured_restart_is_provider_free(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap_result = await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    await _tamper_unit_job_into_batch(
        translation_worker_env,
        job_id=bootstrap_result.job_id,
        unit_id=bootstrap_result.unit_id,
        job_type="translate_article",
    )
    async with translation_worker_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET operation_fingerprint = regexp_replace(
                operation_fingerprint,
                '^translation_unit',
                'translation_article_v1'
            )
            WHERE id = $1
            """,
            bootstrap_result.job_id,
        )
    first = TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=_JournalOrderBatchTranslator(translation_worker_env),
        layer_publisher=_JournalOrderTranslationPublisher(translation_worker_env),
    )
    paused = await first.process_next_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-before-restart",
        lease_duration=timedelta(seconds=30),
    )
    assert paused is not None and paused.status == "paused"
    forbidden = _JournalOrderBatchTranslator(translation_worker_env)

    resumed = await TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=forbidden,
    ).process_next_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-after-restart",
        lease_duration=timedelta(seconds=30),
    )

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, failure_code, failure_message FROM reader_jobs WHERE id = $1",
            paused.claim.job_id,
        )
    assert resumed is not None and resumed.status == "succeeded", job_row
    assert resumed.published_batch is not None


@pytest.mark.parametrize("failure_kind", ["error", "conflict"])
async def test_translation_capture_failure_pauses_without_publish(
    translation_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    publisher = _CapturingPublisher()
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(
            lambda context: _translation_generation_output(
                "捕获前结果",
                [context.anchor_segments[0].anchor_segment_id],
            )
        ),
        layer_publisher=publisher,
    )

    async def _fail_capture(**kwargs) -> None:
        if failure_kind == "conflict":
            raise CaptureEnvelopeConflictError("conflicting translation capture")
        raise RuntimeError("translation capture unavailable")

    monkeypatch.setattr(worker._journal_service, "capture_execution", _fail_capture)
    result = await worker.process_next_translation_job(
        lease_owner="translation-capture-failure",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None and result.status == "paused"
    assert publisher.calls == []
    async with translation_worker_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
    assert row is not None
    assert row["rationale_code"] == (
        "model_execution_capture_conflict"
        if failure_kind == "conflict"
        else "model_execution_ambiguous"
    )


async def test_translation_materializer_failure_does_not_block_publish_and_reconciles_once(
    translation_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    journal = ModelExecutionJournalService(pool=translation_worker_env)

    async def _fail_materializer(**kwargs) -> None:
        raise RuntimeError("usage sink unavailable")

    monkeypatch.setattr(journal, "materialize_pending", _fail_materializer)
    result = await TranslationWorkerService(
        pool=translation_worker_env,
        journal_service=journal,
        translator=_StaticTranslator(
            lambda context: _translation_generation_output(
                "延迟记账译文",
                [context.anchor_segments[0].anchor_segment_id],
            )
        ),
    ).process_next_translation_job(
        lease_owner="translation-materializer-failure",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None and result.status == "succeeded"
    async with translation_worker_env.acquire() as conn:
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_usage_events WHERE reader_job_id = $1",
            result.claim.job_id,
        )
    assert before == 0
    materializer = ModelExecutionJournalService(pool=translation_worker_env)
    attribution = ReaderUsageAttributionService(journal_service=materializer)
    await attribution.materialize_and_reconcile()
    await attribution.materialize_and_reconcile()
    async with translation_worker_env.acquire() as conn:
        usage_row = await conn.fetchrow(
            """
            SELECT id, enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )
    assert usage_row is not None
    assert usage_row["enhancement_layer_id"] == result.published_layer.layer_id


async def test_translation_dead_letter_repair_keeps_published_layer_attribution(
    translation_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    journal = ModelExecutionJournalService(pool=translation_worker_env)
    materialize_pending = journal.materialize_pending

    async def _dead_letter_on_first_failure(**kwargs):
        kwargs["max_attempts"] = 1
        return await materialize_pending(**kwargs)

    monkeypatch.setattr(journal, "materialize_pending", _dead_letter_on_first_failure)
    async with translation_worker_env.acquire() as conn:
        await conn.execute(
            """
            CREATE FUNCTION reject_translation_usage_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'test usage insert failure';
            END;
            $$
            """
        )
        await conn.execute(
            """
            CREATE TRIGGER reject_translation_usage_insert
            BEFORE INSERT ON ai_usage_events
            FOR EACH ROW EXECUTE FUNCTION reject_translation_usage_insert()
            """
        )

    result = await TranslationWorkerService(
        pool=translation_worker_env,
        journal_service=journal,
        translator=_StaticTranslator(
            lambda context: _translation_generation_output(
                "死信修复后归因译文",
                [context.anchor_segments[0].anchor_segment_id],
            )
        ),
    ).process_next_translation_job(
        lease_owner="translation-dead-letter-repair",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None and result.status == "succeeded"
    assert result.published_layer is not None
    async with translation_worker_env.acquire() as conn:
        journal_row = await conn.fetchrow(
            """
            SELECT invocation_key, usage_delivery_state
            FROM ai_model_execution_journal
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )
        await conn.execute(
            "DROP TRIGGER reject_translation_usage_insert ON ai_usage_events"
        )
        await conn.execute("DROP FUNCTION reject_translation_usage_insert()")
    assert journal_row is not None
    assert journal_row["usage_delivery_state"] == "dead_letter"

    repaired_journal = ModelExecutionJournalService(pool=translation_worker_env)
    assert await repaired_journal.repair_dead_letter(
        invocation_key=journal_row["invocation_key"]
    )
    await ReaderUsageAttributionService(
        journal_service=repaired_journal
    ).materialize_and_reconcile()

    async with translation_worker_env.acquire() as conn:
        usage_row = await conn.fetchrow(
            """
            SELECT enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            result.claim.job_id,
        )
        delivery_state = await conn.fetchval(
            """
            SELECT usage_delivery_state
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            journal_row["invocation_key"],
        )
    assert delivery_state == "reconciled"
    assert usage_row is not None
    assert usage_row["enhancement_layer_id"] == result.published_layer.layer_id


async def test_translation_tampered_receipt_fails_closed_without_provider_recall(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(
        pool=translation_worker_env
    ).bootstrap_translation_run(record_id=article.record_id, user_id=user_id)
    first = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(
            lambda context: _translation_generation_output(
                "原始译文",
                [context.anchor_segments[0].anchor_segment_id],
            )
        ),
        layer_publisher=_JournalOrderTranslationPublisher(translation_worker_env),
    )
    paused = await first.process_next_translation_job(
        lease_owner="translation-before-tamper",
        lease_duration=timedelta(seconds=30),
    )
    assert paused is not None and paused.status == "paused"
    async with translation_worker_env.acquire() as conn:
        payload = dict(
            await conn.fetchval(
                """
                SELECT normalized_payload_json
                FROM ai_model_execution_journal
                WHERE reader_job_id = $1
                """,
                paused.claim.job_id,
            )
        )
        payload["output"]["groups"][0]["translated_text"] = "篡改译文"
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET normalized_payload_json = $2::jsonb
            WHERE reader_job_id = $1
            """,
            paused.claim.job_id,
            jsonb_param(payload),
        )
    forbidden = _StaticTranslator(_translation_generation_output("不应调用"))

    resumed = await TranslationWorkerService(
        pool=translation_worker_env,
        translator=forbidden,
    ).process_next_translation_job(
        lease_owner="translation-after-tamper",
        lease_duration=timedelta(seconds=30),
    )

    assert resumed is None
    assert forbidden.calls == []
    async with translation_worker_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            paused.claim.job_id,
        )
    assert row is not None and row["status"] == "paused"
    assert row["rationale_code"] == "model_execution_receipt_invalid"
    assert row["failure_code"] == "receipt_payload_invalid"


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
        translator=_StaticTranslator(_translation_generation_output()),
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


async def test_worker_retry_later_then_success_can_reprocess_same_job(
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

    publisher = _CapturingPublisher()
    success_translator = _StaticTranslator(
        _translation_generation_output("恢复后的译文")
    )
    success_worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=success_translator,
        layer_publisher=publisher,
    )

    success_result = await success_worker.process_next_translation_job(
        lease_owner="worker-retry-then-success",
        lease_duration=timedelta(seconds=30),
    )

    assert success_result is None
    assert success_translator.calls == []
    assert publisher.calls == []

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, rationale_code, failure_code
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
            """,
            article.record_id,
        )

    assert job_row is not None
    assert job_row["status"] == "paused"
    assert job_row["rationale_code"] == "model_execution_ambiguous"
    assert job_row["failure_code"] == "provider_outcome_ambiguous"


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


# ---------------------------------------------------------------------------#
# T6: variant-first strategy integration into translation worker
# ---------------------------------------------------------------------------#


def _build_context_for_variant(
    *,
    reading_goal: str,
    reading_variant: str,
    source_text: str = "Translation source text.",
) -> TranslationJobContext:
    """Build a TranslationJobContext with strategy metadata for a given variant."""
    return _build_context_with_segments(
        source_text=source_text,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )


def test_build_translation_prompt_contains_concrete_policy_lines() -> None:
    """The prompt must include the concrete translation policy lines from
    reader_variants.yaml, not just a placeholder."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_translation_prompt(context)

    # The strategy section must be present with the variant's prompt lines.
    assert "<reader_strategy>" in prompt
    assert "</reader_strategy>" in prompt
    assert "<policy_lines>" in prompt
    assert "</policy_lines>" in prompt
    assert "reading_goal: daily_reading" in prompt
    assert "reading_variant: intermediate_reading" in prompt
    assert "strategy_hash:" in prompt
    assert "layer_policy_hash:" in prompt

    # Every concrete policy line for intermediate_reading translation layer
    # must appear in the prompt.
    for line in context.translation_prompt_lines:
        assert line in prompt


def test_build_translation_prompt_includes_target_segments_and_group_native_output_contract(
) -> None:
    context = _build_context_with_segments(
        source_text="Alpha Beta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "low", "sent-1"),
            ("s2", 6, 10, "clause", "normal", "sent-2"),
        ],
    )
    prompt = _build_translation_prompt(context)

    assert "Return only the structured TranslationLayerGenerationOutput." in prompt
    assert "Only output groups[].anchor_segment_ids and groups[].translated_text." in prompt
    assert "Do not output source_text, source_text_hash, group_id, segment_sources" in prompt
    assert "coverage_json, quality_json" in prompt
    assert 'If boundary_quality="low", treat it only as a hint' in prompt
    assert "<target_segments>" in prompt
    assert "</target_segments>" in prompt
    assert "anchor_segment_id: s1" in prompt
    assert "anchor_segment_id: s2" in prompt
    assert "source_text_hash: " + context.anchor_segments[0].text_hash in prompt
    assert "segment_type: sentence" in prompt
    assert "segment_type: clause" in prompt
    assert "boundary_quality: low" in prompt
    assert "boundary_quality: normal" in prompt
    assert "Alpha" in prompt
    assert "Beta" in prompt
    assert "1-3 segments" not in prompt


def test_build_translation_prompt_requires_semantic_reading_groups_and_forbids_one_to_one() -> None:
    """Prompt must teach the model to produce semantic reading groups, not
    a row-per-anchor-segment fill-in-the-blank table."""
    context = _build_context_with_segments(
        source_text="Alpha Beta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 6, 10, "sentence", "normal", "sent-2"),
        ],
    )
    prompt = _build_translation_prompt(context)

    # Variant-independent per-call grouping guidance must be present.
    assert "<grouping_guidance>" in prompt
    assert "</grouping_guidance>" in prompt

    # The prompt must call out semantic reading groups explicitly.
    assert "semantic reading groups" in prompt

    # The prompt must forbid mechanically one-group-per-anchor-segment.
    assert "one group per anchor segment" in prompt

    # The prompt must reframe target_segments as anchor handles, not a
    # row-by-row output template.
    assert "anchor handle" in prompt
    assert "row-by-row output template" in prompt

    # The prompt must allow groups of varying size (>= 1 segment) without
    # imposing a fixed number-of-groups rule.
    assert "one or more consecutive anchor_segment_ids" in prompt
    assert "no fixed minimum or maximum group size" in prompt
    assert "no fixed number of groups" in prompt


def test_build_translation_prompt_does_not_introduce_group_size_thresholds() -> None:
    """Prompt must NOT prescribe a numeric threshold for group size or count.

    Granularity must be left to the model's semantic judgment; the codebase
    must not encode "groups of 2-3 segments" or similar mechanical cutoffs.

    Note: this test guards against prescriptive thresholds like
    "minimum group size = 2" or "groups of 3-5". It must NOT match the
    prompt's own meta-language about *not* having a threshold (e.g.
    "no minimum or maximum group size").
    """
    context = _build_context_with_segments(
        source_text="Alpha Beta Gamma",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 6, 10, "sentence", "normal", "sent-2"),
            ("s3", 11, 16, "sentence", "normal", "sent-3"),
        ],
    )
    prompt = _build_translation_prompt(context)

    # Strip the prompt's own negation preamble before scanning, so the
    # meta-language about "no fixed minimum/maximum" does not trip the
    # forbidden-substring check. We test that the prompt contains a
    # self-declaration of "no fixed minimum/maximum group size" below in
    # the positive-assertion test.
    lower = prompt.lower()

    # No numeric group-size cutoffs (e.g. "1-3 segments", "2-4 segments").
    for forbidden in (
        "1-3 segments",
        "1-3 anchor",
        "2-4 segments",
        "2-4 anchor",
        "groups of 2",
        "groups of 3",
        "at least 2 segments",
        "at most 4 segments",
        "min group size",
        "max group size",
    ):
        assert forbidden not in lower, f"prompt must not prescribe {forbidden!r}"

    # No "must merge" / "must split" cutoff phrasing that would coerce
    # group counts into a fixed shape.
    for forbidden in (
        "must merge",
        "must split",
        "always merge",
        "always split",
        "exactly",
        "at least one group",
        "at most one group",
    ):
        assert forbidden not in lower, f"prompt must not prescribe {forbidden!r}"

    # Positive self-declaration: the prompt MUST tell the model there is
    # no fixed min/max size and no fixed number of groups.
    assert "no fixed minimum or maximum group size" in lower
    assert "no fixed number of groups" in lower


def test_build_translation_prompt_reframes_target_segments_as_registry_not_template() -> None:
    """The <target_segments> block must be presented as an anchor handle
    registry the model references back into, not a row-by-row output template."""
    context = _build_context_with_segments(
        source_text="Alpha Beta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 6, 10, "sentence", "normal", "sent-2"),
        ],
    )
    prompt = _build_translation_prompt(context)

    # The grouping guidance block must precede the real target_segments block.
    grouping_block_idx = prompt.index("<grouping_guidance>")
    grouping_close_idx = prompt.index("</grouping_guidance>")
    # The real <target_segments> block sits AFTER </grouping_guidance>. The
    # registry_note body mentions the literal "<target_segments>" string, so
    # we cannot rely on `prompt.index("<target_segments>")` — we search from
    # past the grouping_close marker to find the real registry block.
    target_segments_block_idx = prompt.index("<target_segments>", grouping_close_idx)
    target_segments_close_idx = prompt.index("</target_segments>", grouping_close_idx)
    assert grouping_block_idx < grouping_close_idx
    assert grouping_close_idx < target_segments_block_idx
    assert target_segments_block_idx < target_segments_close_idx

    # The grouping guidance must include a registry_note subsection that
    # reframes target_segments as anchor handles.
    registry_idx = prompt.index("<target_segments_registry_note>")
    registry_close_idx = prompt.index("</target_segments_registry_note>")
    assert grouping_block_idx < registry_idx < registry_close_idx < grouping_close_idx

    # The note explicitly forbids one-row-per-listed-id behavior.
    registry_note = prompt[registry_idx:registry_close_idx]
    assert "row-by-row output template" in registry_note
    assert "one row per listed id" in registry_note


def test_build_translation_prompt_grouping_guidance_sits_between_strategy_and_output_contract(
) -> None:
    """The grouping_guidance block must sit between the strategy section and
    the structured-output contract so it shapes model behavior before the
    schema is named."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_translation_prompt(context)

    strategy_idx = prompt.index("<reader_strategy>")
    grouping_idx = prompt.index("<grouping_guidance>")
    return_idx = prompt.index("Return only the structured TranslationLayerGenerationOutput.")
    source_idx = prompt.index("<source_text>")

    assert strategy_idx < grouping_idx < return_idx < source_idx


def test_translation_agent_instructions_require_semantic_groups_and_drop_legacy_contract() -> None:
    """The agent-level instructions (loaded via load_agent_instructions) must:

    1. Teach semantic reading groups and forbid one-group-per-anchor-segment.
    2. Treat ``target_segments`` as anchor handles, not a row template.
    3. Restrict the generation output to ``TranslationLayerGenerationOutput``
       with only ``groups[].anchor_segment_ids`` and ``groups[].translated_text``.
    4. NOT carry over the legacy ``TranslationLayerOutput`` contract that
       asked for ``target_language`` / ``confidence`` / ``notes`` round-trip
       fields — those belong to the publisher, not the generator.
    """
    instructions = load_agent_instructions("reader_layer_translation")

    # 1) Semantic grouping guidance must be present in the agent instructions.
    assert "semantic reading groups" in instructions
    assert "one group per anchor segment" in instructions
    assert "anchor handles" in instructions or "anchor handle" in instructions
    # The instructions must declare the canonical two-field generation whitelist
    # using the full field-path syntax.
    assert (
        "只输出 `groups[].anchor_segment_ids` 和 `groups[].translated_text`"
        in instructions
    )
    assert "groups[].anchor_segment_ids" in instructions
    assert "groups[].translated_text" in instructions

    # 2) The legacy TranslationLayerOutput contract must not appear.
    #    The instruction must point at the generation-state schema, not the
    #    publisher/persisted schema.
    assert "TranslationLayerOutput" not in instructions

    # 3) The legacy "target_language must be round-tripped" contract must not
    #    appear. Instead, target_language is referenced as task context only,
    #    not as an output field.
    assert "target_language 必须回填" not in instructions
    assert "目标语言由任务上下文" in instructions
    # The instruction must explicitly tell the model not to write
    # target_language back into the generated structure.
    assert "不要在生成结构中回写" in instructions
    assert (
        "`target_language` 字段" in instructions
        or "target_language 字段" in instructions
    )

    # 4) Legacy per-group fields must not be requested as outputs.
    for forbidden in (
        "confidence",  # legacy TranslationLayerOutput.confidence
        "notes",  # legacy TranslationLayerOutput.notes
    ):
        # `confidence` and `notes` may legitimately appear as part of the
        # forbidden-output list ("不要输出 ... confidence ..."), so we
        # require the surrounding negation pattern.
        if forbidden in instructions:
            # If present, it must be inside the "don't output" list, not as
            # an instruction to round-trip the value.
            assert (
                "不要输出" in instructions
                or "do not output" in instructions.lower()
                or "not output" in instructions.lower()
            ), (
                f"instructions mention {forbidden!r} but not in a "
                f"'don't output' context"
            )

    # 5) Generation-state-only field whitelist must be enforced explicitly.
    #    Every legacy field must appear in a "do not output" prohibition so
    #    the model treats them as off-limits.
    for forbidden_field in (
        "group_id",
        "source_text_hash",
        "source_language",
        "diagnostics",
        "coverage_json",
        "quality_json",
        "source_text",
        "segment_sources",
        "profile",
        "reason",
    ):
        assert forbidden_field in instructions, (
            f"instructions must enumerate {forbidden_field!r} as a forbidden "
            f"generation field"
        )


# Shared Chinese quality-contract markers — both the per-unit YAML and the
# batch-path instructions must contain these exact fragments. The two paths
# historically drifted (per-unit Chinese, batch English) which let the
# batch path emit traditional Chinese characters. They now share one
# stable Chinese contract.
_SHARED_QUALITY_MARKERS = (
    "简体中文",
    "禁止输出繁体字",
    "准确完整",
    "自然中文优先",
    "不要机械贴合英语语序",
    "因果",
    "转折",
    "指代",
    "大陆地区通行译名",
    "无可靠通行译名时保留英文",
    "同一输出内译名保持一致",
    "禁止 Markdown",
    "教学点评",
)

# Sample traditional characters enumerated in the ban line. The contract
# must name concrete samples so the model cannot mistake the ban for a
# generic "use Simplified Chinese" reminder.
_TRADITIONAL_CHAR_SAMPLES_IN_BAN = ("英國", "將", "與", "們", "個", "來", "說", "這", "對", "關")


def test_per_unit_translation_instructions_contain_quality_contract() -> None:
    """Per-unit agent YAML carries the shared translation quality contract."""
    instructions = load_agent_instructions("reader_layer_translation")
    for marker in _SHARED_QUALITY_MARKERS:
        assert marker in instructions, f"per-unit missing quality marker: {marker!r}"
    # Traditional-character ban must be explicit and enumerate samples
    assert "禁止输出繁体字" in instructions
    for sample in _TRADITIONAL_CHAR_SAMPLES_IN_BAN:
        assert sample in instructions, (
            f"per-unit ban line must enumerate traditional sample {sample!r}"
        )
    assert "同义替换" in instructions  # forbidden as commentary in quality contract
    # No first-mention state machine requirement
    assert "全文首次出现" not in instructions
    assert "first-mention" not in instructions.lower()


def test_batch_translation_instructions_contain_quality_contract() -> None:
    """Batch path instructions share the same Chinese quality contract.

    The batch path previously used an English-phrased quality contract that
    drifted from the per-unit YAML; it now mirrors the per-unit Chinese
    contract verbatim for the hard simplified-Chinese ban and mainland
    rendering requirement. Structural batch-only rules (PRE-DEFINED
    groups, group_id echo) remain in Chinese phrasing.
    """
    from app.services.reader_orchestration.translation_worker import (
        _TRANSLATION_BATCH_AGENT_INSTRUCTIONS,
    )

    batch = _TRANSLATION_BATCH_AGENT_INSTRUCTIONS
    for marker in _SHARED_QUALITY_MARKERS:
        assert marker in batch, f"batch missing quality marker: {marker!r}"
    # Traditional-character ban must be explicit and enumerate samples
    assert "禁止输出繁体字" in batch
    for sample in _TRADITIONAL_CHAR_SAMPLES_IN_BAN:
        assert sample in batch, (
            f"batch ban line must enumerate traditional sample {sample!r}"
        )
    # Structural batch contract preserved (Chinese phrasing)
    assert "预先定义" in batch
    assert "group_id" in batch
    assert "translated_text" in batch
    assert "不得臆造、合并、拆分" in batch
    assert "first-mention" not in batch.lower()
    assert "full-article" not in batch.lower()


def test_per_unit_and_batch_share_symmetric_simplified_chinese_contract() -> None:
    """Both paths must use identical Chinese phrasing for the hard
    simplified-Chinese ban and mainland-rendering requirement.

    Asymmetric phrasing (per-unit Chinese, batch English) historically let
    the batch path drift and emit traditional characters. The two paths
    must agree verbatim on the hard ban line and the mainland-rendering
    requirement.
    """
    from app.services.reader_orchestration.translation_worker import (
        _TRANSLATION_BATCH_AGENT_INSTRUCTIONS,
    )

    per_unit = load_agent_instructions("reader_layer_translation")
    batch = _TRANSLATION_BATCH_AGENT_INSTRUCTIONS

    # The hard simplified-Chinese ban line must appear in both paths.
    assert "禁止输出繁体字" in per_unit
    assert "禁止输出繁体字" in batch

    # Both must require mainland renderings + English fallback.
    for fragment in ("大陆地区通行译名", "无可靠通行译名时保留英文"):
        assert fragment in per_unit, f"per-unit missing {fragment!r}"
        assert fragment in batch, f"batch missing {fragment!r}"

    # Both must enumerate the same traditional-character samples.
    for sample in _TRADITIONAL_CHAR_SAMPLES_IN_BAN:
        assert sample in per_unit, f"per-unit missing traditional sample {sample!r}"
        assert sample in batch, f"batch missing traditional sample {sample!r}"


def test_translation_schema_documented_simplified_chinese_contract() -> None:
    """Pydantic Field descriptions on ``translated_text`` must document the
    simplified-Chinese-only contract so schema introspection (JSON Schema
    export, OpenAPI) surfaces the same guarantee the prompt enforces.

    This is documentation-only — no pattern validator, to avoid
    false-negative rejections of borderline characters. The real
    enforcement is the prompt + model; the description is a contract
    signal for downstream consumers and an audit anchor for drift
    detection.
    """
    from app.schemas.reader_orchestration import (
        TranslationBatchGroupOutput,
        TranslationGenerationGroup,
        TranslationGroup,
    )

    for model_cls in (
        TranslationGenerationGroup,
        TranslationBatchGroupOutput,
        TranslationGroup,
    ):
        field_info = model_cls.model_fields["translated_text"]
        description = field_info.description or ""
        assert "Simplified-Chinese plain text only" in description, (
            f"{model_cls.__name__}.translated_text missing simplified-Chinese "
            f"contract description"
        )
        assert "Traditional characters" in description, (
            f"{model_cls.__name__}.translated_text missing traditional-character "
            f"ban in description"
        )
        assert "Mainland China renderings" in description, (
            f"{model_cls.__name__}.translated_text missing mainland-rendering "
            f"requirement in description"
        )
        # Description must enumerate at least one concrete traditional
        # sample so the ban is unambiguous.
        assert "英國" in description, (
            f"{model_cls.__name__}.translated_text description must enumerate "
            f"concrete traditional-character samples"
        )


# Soft-lens boundary: these belong only in agent quality contracts, not variants.
_VARIANT_LAYER_FORBIDDEN = (
    # Quality-contract bans / mechanics
    "教学点评",
    "词汇注释",
    "题型提示",
    "题型",
    "拆句",
    "合句",
    "英语语序",
    "英文语序",
    "逐词硬译",
    "增删原意",
    "Markdown",
    "同义替换",
    "语法讲解",
    "词汇讲解",
    "修辞分析",
    "教学说明",
    "教学备注",
    "固定拆合",
    "硬性规定",
    # Legacy hard templates
    "尽量保留原文的逻辑顺序和句子结构",
    "句法映射",
    "适当拆分为短句",
    "此处 contribute to",
    "此处 be attributed to",
    "此处原文用暗喻",
    "此处 XX 指的是",
    "通行中文译名",  # quality contract, not soft lens
    "禁止输出繁体字",  # hard simplified-Chinese ban, not soft lens
    "大陆地区通行译名",  # mainland-rendering requirement, not soft lens
    "无可靠通行译名时保留英文",  # English-fallback rule, not soft lens
)


@pytest.mark.parametrize(
    "goal,variant",
    [
        ("daily_reading", "beginner_reading"),
        ("daily_reading", "intermediate_reading"),
        ("daily_reading", "intensive_reading"),
        ("exam", "gaokao"),
        ("exam", "cet"),
        ("exam", "kaoyan"),
        ("exam", "tem"),
        ("exam", "ielts_toefl"),
    ],
)
def test_translation_variant_soft_lenses_omit_quality_contract_rules(
    goal: str, variant: str
) -> None:
    """Variant translation lines must not restate the stable quality contract
    (including teaching bans and hard sentence-handling rules)."""
    strategy = resolve_reader_variant_strategy(goal, variant)
    text = "\n".join(strategy.layers["translation"].prompt_lines)
    for fragment in _VARIANT_LAYER_FORBIDDEN:
        assert fragment not in text, (
            f"{variant} translation soft lens still contains {fragment!r}"
        )
    assert strategy.layers["translation"].prompt_lines
    assert all(line.strip() for line in strategy.layers["translation"].prompt_lines)
    # Soft lenses stay short: 1–2 lines of user/register orientation.
    assert 1 <= len(strategy.layers["translation"].prompt_lines) <= 2


def test_translation_variant_soft_lenses_remain_distinguishable() -> None:
    """Variants keep light differentiation without becoming templates."""
    beginner = "\n".join(
        resolve_reader_variant_strategy("daily_reading", "beginner_reading")
        .layers["translation"]
        .prompt_lines
    )
    intensive = "\n".join(
        resolve_reader_variant_strategy("daily_reading", "intensive_reading")
        .layers["translation"]
        .prompt_lines
    )
    kaoyan = "\n".join(
        resolve_reader_variant_strategy("exam", "kaoyan")
        .layers["translation"]
        .prompt_lines
    )
    ielts = "\n".join(
        resolve_reader_variant_strategy("exam", "ielts_toefl")
        .layers["translation"]
        .prompt_lines
    )
    tem = "\n".join(
        resolve_reader_variant_strategy("exam", "tem")
        .layers["translation"]
        .prompt_lines
    )

    assert "初学者" in beginner or "通俗" in beginner
    assert "语气" in intensive or "节奏" in intensive or "文体" in intensive
    assert "层次" in kaoyan or "论证" in kaoyan
    assert "学术" in ielts
    assert "语气" in tem or "节奏" in tem or "文体" in tem
    assert len({beginner, intensive, kaoyan, ielts, tem}) == 5


def test_build_translation_prompt_differs_between_daily_intermediate_and_exam_cet() -> None:
    """daily_reading/intermediate_reading and exam/cet must produce
    different strategy sections in the prompt."""
    daily_context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    exam_context = _build_context_for_variant(
        reading_goal="exam",
        reading_variant="cet",
    )

    daily_prompt = _build_translation_prompt(daily_context)
    exam_prompt = _build_translation_prompt(exam_context)

    # The two prompts must differ in the strategy section.
    assert daily_prompt != exam_prompt

    # The daily prompt must carry the daily_reading goal and the
    # intermediate_reading variant's policy lines.
    assert "reading_goal: daily_reading" in daily_prompt
    assert "reading_variant: intermediate_reading" in daily_prompt
    for line in daily_context.translation_prompt_lines:
        assert line in daily_prompt
        # The exam prompt must NOT carry the daily variant's lines.
        assert line not in exam_prompt

    # The exam prompt must carry the exam goal and the cet variant's
    # policy lines.
    assert "reading_goal: exam" in exam_prompt
    assert "reading_variant: cet" in exam_prompt
    for line in exam_context.translation_prompt_lines:
        assert line in exam_prompt
        assert line not in daily_prompt

    # strategy_hash and layer_policy_hash must differ between the two.
    assert daily_context.strategy_hash != exam_context.strategy_hash
    assert daily_context.layer_policy_hash != exam_context.layer_policy_hash


def test_build_translation_prompt_strategy_section_order() -> None:
    """The strategy section must sit between the unit_id line and the
    'Return only...' directive so it does not clobber the source_text block."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_translation_prompt(context)

    unit_id_idx = prompt.index(f"unit_id: {context.unit_id}")
    strategy_idx = prompt.index("<reader_strategy>")
    return_idx = prompt.index(
        "Return only the structured TranslationLayerGenerationOutput."
    )
    source_idx = prompt.index("<source_text>")

    assert unit_id_idx < strategy_idx < return_idx < source_idx


def test_hydrate_translation_layer_output_builds_deterministic_group_id() -> None:
    context = _build_context_with_segments(
        source_text="Alpha Beta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 6, 10, "sentence", "normal", "sent-2"),
        ],
    )
    generation = _translation_generation_output(
        "阿尔法 贝塔",
        ["s1", "s2"],
    )

    hydrated_one = hydrate_translation_layer_output(
        context=context,
        generation=generation,
    )
    hydrated_two = hydrate_translation_layer_output(
        context=context,
        generation=generation,
    )

    assert hydrated_one.groups[0].group_id == "u1_g1_2"
    assert hydrated_two.groups[0].group_id == "u1_g1_2"


def test_hydrate_translation_layer_output_uses_separator_inclusive_hash_for_space() -> None:
    context = _build_context_with_segments(
        source_text="Alpha Beta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 6, 10, "sentence", "normal", "sent-2"),
        ],
    )
    hydrated = hydrate_translation_layer_output(
        context=context,
        generation=_translation_generation_output("阿尔法 贝塔", ["s1", "s2"]),
    )

    assert set(hydrated.model_dump(mode="json")["groups"][0].keys()) == {
        "group_id",
        "anchor_segment_ids",
        "source_text_hash",
        "translated_text",
    }
    assert hydrated.groups[0].source_text_hash == compute_text_range_hash("Alpha Beta")
    assert hydrated.groups[0].source_text_hash != compute_text_range_hash("AlphaBeta")


def test_hydrate_translation_layer_output_uses_separator_inclusive_hash_for_paragraph_break(
) -> None:
    context = _build_context_with_segments(
        source_text="Alpha\n\nBeta",
        segment_specs=[
            ("s1", 0, 5, "sentence", "normal", "sent-1"),
            ("s2", 7, 11, "sentence", "normal", "sent-2"),
        ],
    )
    hydrated = hydrate_translation_layer_output(
        context=context,
        generation=_translation_generation_output("阿尔法\n\n贝塔", ["s1", "s2"]),
    )

    assert hydrated.groups[0].source_text_hash == compute_text_range_hash("Alpha\n\nBeta")
    assert hydrated.groups[0].source_text_hash != compute_text_range_hash("AlphaBeta")


def test_hydrate_translation_layer_output_rejects_unknown_anchor_segment_id() -> None:
    context = _translation_context()
    generation = _translation_generation_output("测试", ["missing"])

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_layer_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_unknown_anchor_segment"


# ---------------------------------------------------------------------------#
# T6: _validate_translation_strategy_metadata fail-closed unit tests
# ---------------------------------------------------------------------------#


def test_validate_strategy_metadata_rejects_non_mapping_input() -> None:
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(None)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_missing_keys() -> None:
    """A legacy bare-fingerprint job whose input_json lacks strategy
    metadata must fail closed, not fall back to a default strategy."""
    incomplete = {
        "unit_id": "u1",
        "base_language": "en",
        "target_language": "zh-CN",
        # No reading_goal / reading_variant / strategy_version / strategy_hash
        # / layer_policy_hash.
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(incomplete)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert "reading_goal" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_empty_string_values() -> None:
    """Empty string values are treated as missing."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    payload = {
        "reading_goal": "",
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_metadata_missing"


def test_validate_strategy_metadata_rejects_strategy_hash_mismatch() -> None:
    """strategy_hash mismatch must fail closed with a dedicated code."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_hash_mismatch"
    assert "strategy_hash" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_layer_policy_hash_mismatch() -> None:
    """layer_policy_hash mismatch must fail closed with a dedicated code."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": strategy.layers["vocabulary"].policy_hash,
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(payload)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_strategy_version_mismatch() -> None:
    """strategy_version mismatch must fail closed."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": "stale_version",
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_version_mismatch"


def test_validate_strategy_metadata_rejects_illegal_goal_variant_pair() -> None:
    """An illegal goal/variant pair (e.g. academic) must fail closed via
    the resolver, not silently fall back."""
    payload = {
        "reading_goal": "academic",
        "reading_variant": "academic_general",
        "strategy_version": "reader_variant_policy_v1",
        "strategy_hash": "irrelevant",
        "layer_policy_hash": "irrelevant",
    }
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_strategy_metadata(payload)
    assert exc_info.value.failure_class == "strategy_resolution"
    assert exc_info.value.failure_code == "strategy_resolver_error"


def test_validate_strategy_metadata_returns_resolved_prompt_lines_on_success() -> None:
    """On success, the helper returns the resolver's concrete prompt_lines
    so the prompt builder can inject them."""
    strategy = resolve_reader_variant_strategy("exam", "cet")
    layer = strategy.layers["translation"]
    payload = {
        "reading_goal": "exam",
        "reading_variant": "cet",
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    result = _validate_translation_strategy_metadata(payload)
    assert result.reading_goal == "exam"
    assert result.reading_variant == "cet"
    assert result.strategy_hash == strategy.strategy_hash
    assert result.layer_policy_hash == layer.policy_hash
    assert result.translation_prompt_lines == layer.prompt_lines
    assert len(result.translation_prompt_lines) >= 1


# ---------------------------------------------------------------------------#
# _load_job_context integration — reads job-bootstrap strategy metadata
# ---------------------------------------------------------------------------#


async def test_load_job_context_reads_bootstrap_strategy_metadata(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must read strategy metadata written by the job bootstrap
    and resolve the concrete translation policy lines from the resolver."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    boot_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    worker = TranslationWorkerService(pool=translation_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    # The context must carry the strategy metadata from input_json.
    assert context.reading_goal == "daily_reading"
    assert context.reading_variant == "intermediate_reading"

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    assert context.strategy_version == strategy.strategy_version
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["translation"].policy_hash

    # The concrete prompt lines must come from the resolver, not from
    # input_json (input_json only stores hashes, not the lines themselves).
    assert context.translation_prompt_lines == strategy.layers["translation"].prompt_lines
    assert len(context.translation_prompt_lines) >= 1

    # The prompt built from this context must include the concrete lines.
    prompt = _build_translation_prompt(context)
    for line in context.translation_prompt_lines:
        assert line in prompt
    assert len(context.anchor_segments) >= 1
    assert [segment.order_index for segment in context.anchor_segments] == sorted(
        segment.order_index for segment in context.anchor_segments
    )
    for segment in context.anchor_segments:
        assert compute_text_range_hash(segment.source_text) == segment.text_hash


async def test_load_job_context_reads_exam_cet_strategy_metadata(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must also work for exam/cet variant."""
    from app.services.reader_orchestration.article_ready_service import (
        PlainTextArticleReadySubmitRequest,
    )

    user_id = await insert_user(translation_worker_env)
    service = ArticleReadyPersistenceService(pool=translation_worker_env)
    submit_result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="First paragraph for exam.\n\nSecond paragraph for exam.",
            title="Exam CET Slice",
            language="en",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="cet",  # type: ignore[arg-type]
        )
    )

    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    boot_result = await bootstrap.bootstrap_translation_run(
        record_id=submit_result.record_id,
        user_id=user_id,
    )

    worker = TranslationWorkerService(pool=translation_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    assert context.reading_goal == "exam"
    assert context.reading_variant == "cet"
    strategy = resolve_reader_variant_strategy("exam", "cet")
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["translation"].policy_hash
    assert context.translation_prompt_lines == strategy.layers["translation"].prompt_lines


async def test_load_job_context_fail_closed_on_missing_anchor_segments(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    boot_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with translation_worker_env.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            """,
            article.record_id,
            article.base_id,
            boot_result.unit_id,
        )

    worker = TranslationWorkerService(pool=translation_worker_env)
    with pytest.raises(TranslationExecutionError) as exc_info:
        await worker._load_job_context(boot_result.job_id)
    assert exc_info.value.failure_code == "anchor_segments_missing"
    assert exc_info.value.retryable is False


async def test_load_job_context_fail_closed_on_anchor_segment_hash_mismatch(
    translation_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    boot_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with translation_worker_env.acquire() as conn:
        first_segment_id = await conn.fetchval(
            """
            SELECT anchor_segment_id
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            ORDER BY order_index ASC
            LIMIT 1
            """,
            article.record_id,
            article.base_id,
            boot_result.unit_id,
        )
        await conn.execute(
            """
            UPDATE anchor_segments
            SET text_hash = 'deadbeef'
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
              AND anchor_segment_id = $4
            """,
            article.record_id,
            article.base_id,
            boot_result.unit_id,
            first_segment_id,
        )

    worker = TranslationWorkerService(pool=translation_worker_env)
    with pytest.raises(TranslationExecutionError) as exc_info:
        await worker._load_job_context(boot_result.job_id)
    assert exc_info.value.failure_code == "anchor_segment_hash_mismatch"
    assert str(first_segment_id) in str(exc_info.value)


async def _insert_legacy_translation_job_without_strategy_metadata(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    user_id: UUID,
    unit_id: str,
    input_json: dict,
) -> UUID:
    """Insert a translation job row with crafted input_json.

    Used to simulate legacy bare-fingerprint jobs or jobs with tampered
    strategy metadata for fail-closed tests.
    """
    from app.database.json_compat import jsonb_param

    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1,
                    '{}'::jsonb, 'legacy-test', 'system')
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
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts
            )
            VALUES (
                $1, $2, $3, $4,
                'translate_unit', 'unit', $5, 'queued',
                0, 1, $6,
                $7, $8, $9::jsonb, 3
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            unit_id,
            TRANSLATION_OPERATION_FINGERPRINT,
            f"{TRANSLATION_OPERATION_FINGERPRINT}:{unit_id}",
            "legacy-input-hash",
            jsonb_param(input_json),
        )
    assert isinstance(job_id, UUID)
    return job_id


async def test_load_job_context_fail_closed_on_missing_strategy_metadata(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """A legacy bare-fingerprint job without strategy metadata in input_json
    must fail closed when _load_job_context tries to load it."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "target_language": "zh-CN",
        # No strategy metadata keys.
    }
    job_id = await _insert_legacy_translation_job_without_strategy_metadata(
        translation_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = TranslationWorkerService(pool=translation_worker_env)
    with pytest.raises(TranslationExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


async def test_load_job_context_fail_closed_on_strategy_hash_mismatch(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json strategy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "target_language": "zh-CN",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    job_id = await _insert_legacy_translation_job_without_strategy_metadata(
        translation_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = TranslationWorkerService(pool=translation_worker_env)
    with pytest.raises(TranslationExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_hash_mismatch"
    assert exc_info.value.retryable is False


async def test_load_job_context_fail_closed_on_layer_policy_hash_mismatch(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json layer_policy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "target_language": "zh-CN",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": strategy.layers["vocabulary"].policy_hash,
    }
    job_id = await _insert_legacy_translation_job_without_strategy_metadata(
        translation_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = TranslationWorkerService(pool=translation_worker_env)
    with pytest.raises(TranslationExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


async def test_worker_fail_closed_on_missing_strategy_metadata_moves_job_to_failed_terminal(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """End-to-end: a legacy job without strategy metadata, when processed
    by the worker, must move to failed_terminal with the right failure code."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "target_language": "zh-CN",
    }
    job_id = await _insert_legacy_translation_job_without_strategy_metadata(
        translation_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_generation_output()),
    )

    # Manually claim the legacy bare-fingerprint job through the runtime so
    # this test can exercise process_claimed_translation_job's fail-closed
    # metadata-validation path directly.
    from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

    runtime = ReaderJobRuntime(pool=translation_worker_env)
    claim = await runtime.claim_next_job(
        lease_owner="legacy-test-worker",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id

    result = await worker.process_claimed_translation_job(claim=claim)

    assert result.status == "failed_terminal"
    assert result.context is None  # context loading failed before assignment

    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, failure_class, failure_code, rationale_code "
            "FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "validation"
    assert job_row["failure_code"] == "strategy_metadata_missing"
    # TranslationExecutionError defaults rationale_code to failure_code when
    # not explicitly set; the worker's TranslationExecutionError branch
    # propagates exc.rationale_code to the transition call.
    assert job_row["rationale_code"] == "strategy_metadata_missing"


# ---------------------------------------------------------------------------#
# T1.1 batch deterministic-grouping alignment fix
#
# Regression coverage for the short-article batch translation misalignment
# bug (record a1812e99...: the s17 translation was anchored to s15 because
# the LLM freely chose anchor_segment_ids). The batch path now pre-defines
# one translation group per anchor segment and the LLM only returns
# group_id + translated_text; the backend binds anchors deterministically.
# ---------------------------------------------------------------------------#


# The actual misaligned sentences from the bug report.
_STAFF_LIVELIHOOD_SOURCE = "Without tips, staff may not earn enough money to live on."
_AUTO_TIP_BILLS_SOURCE = (
    "In many host cities, restaurants have "
    "automatically added tips to their bills."
)
_STAFF_LIVELIHOOD_ZH = "如果没有小费，员工可能挣不到足够的钱维持生活。"
_AUTO_TIP_BILLS_ZH = "在许多主办城市，餐厅已经自动将小费添加到账单中。"


def _build_batch_context_with_segments(
    *,
    unit_id: str = "u2",
    segment_specs: list[tuple[str, str, int]] | None = None,
    joiner: str = "\n\n",
    reading_goal: str = "daily_reading",
    reading_variant: str = "intermediate_reading",
) -> TranslationBatchJobContext:
    """Build a TranslationBatchJobContext with one unit and given segments.

    ``segment_specs`` is a list of ``(anchor_segment_id, source_text,
    order_index)``. The unit source_text is the segments joined by
    ``joiner``; each segment's unit_start_utf16 / unit_end_utf16 are
    computed from the join so slice_by_utf16_offsets can recover each
    segment exactly.

    ``joiner`` controls the gap text between segments but does NOT
    control group boundaries (newlines are a SOFT hint only):

    - ``"\\n\\n"``: each segment is visually its own paragraph. The
      planner still merges consecutive short single-sentence paragraphs
      into 2-3-anchor reading groups (Reuters/BBC news feed scenario).
    - ``" "``: all segments form one paragraph. The planner clusters
      1-3 short sentences into one group, or splits long runs into
      bounded groups.
    """
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    layer = strategy.layers["translation"]
    if segment_specs is None:
        segment_specs = [
            ("s15", _STAFF_LIVELIHOOD_SOURCE, 15),
            ("s17", _AUTO_TIP_BILLS_SOURCE, 17),
        ]
    texts = [spec[1] for spec in segment_specs]
    unit_source_text = joiner.join(texts)
    joiner_utf16_len = utf16_code_unit_length(joiner)
    anchor_segments: list[TranslationAnchorSegmentTarget] = []
    cursor = 0
    for anchor_segment_id, segment_text, order_index in segment_specs:
        start = cursor
        end = cursor + utf16_code_unit_length(segment_text)
        anchor_segments.append(
            TranslationAnchorSegmentTarget(
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                order_index=order_index,
                segment_type="sentence",
                boundary_quality="normal",
                unit_start_utf16=start,
                unit_end_utf16=end,
                text_hash=compute_text_range_hash(segment_text),
                source_text=segment_text,
            )
        )
        cursor = end + joiner_utf16_len
    unit = TranslationBatchUnitContext(
        unit_id=unit_id,
        order_index=1,
        source_text=unit_source_text,
        text_hash=compute_text_range_hash(unit_source_text),
        anchor_segments=tuple(anchor_segments),
    )
    return TranslationBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        expected_generation=1,
        operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        source_language="en",
        target_language="zh-CN",
        target_unit_ids=(unit_id,),
        units=(unit,),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
    )


# Contiguous segments for multi-anchor grouping tests (s10/s11/s12).
# These three sentences form ONE paragraph (joined by a single space) so
# the semantic planner clusters them into a single translation group.
# The first sentence carries the `$2.13 per hour` decimal boundary, which
# must NOT be split by the planner (the planner only groups whole anchor
# segments; it never re-segments sentence text).
_MIN_WAGE_SOURCE = "Workers at restaurants in the US can earn as little as $2.13 per hour."
_RELY_ON_TIPS_SOURCE = "They rely on diners to tip for their service."
_BATCH_STAFF_LIVELIHOOD_SOURCE = "Without tips, staff may not earn enough money to live on."


def _build_contiguous_unit_source_text() -> str:
    return " ".join([_MIN_WAGE_SOURCE, _RELY_ON_TIPS_SOURCE, _BATCH_STAFF_LIVELIHOOD_SOURCE])


def _build_batch_context_with_contiguous_segments(
    *,
    unit_id: str = "u2",
    reading_goal: str = "daily_reading",
    reading_variant: str = "intermediate_reading",
) -> TranslationBatchJobContext:
    """Build a TranslationBatchJobContext with 3 contiguous anchor segments
    forming ONE paragraph (space-joined).

    Segments s10/s11/s12 (order_index 10/11/12) form a single short
    paragraph. The semantic planner clusters 1-3 short sentences into
    ONE translation group (semantic reading group for the paragraph).
    This is the a75d742a regression fixture: a short paragraph with
    multiple sentences must NOT be split into one-sentence-per-group.
    """
    segment_specs = [
        ("s10", _MIN_WAGE_SOURCE, 10),
        ("s11", _RELY_ON_TIPS_SOURCE, 11),
        ("s12", _BATCH_STAFF_LIVELIHOOD_SOURCE, 12),
    ]
    return _build_batch_context_with_segments(
        unit_id=unit_id,
        segment_specs=segment_specs,
        joiner=" ",
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )


def _batch_unit_output(
    unit_id: str,
    groups: list[tuple[str, str]],
) -> TranslationBatchUnitOutput:
    return TranslationBatchUnitOutput(
        unit_id=unit_id,
        groups=[
            TranslationBatchGroupOutput(group_id=gid, translated_text=text)
            for gid, text in groups
        ],
    )


def test_build_deterministic_translation_groups_splits_non_contiguous_anchors() -> None:
    """Non-contiguous anchor segments (gap in order_index) get separate
    groups because the publisher requires contiguous order_index within
    a group. The s15/s17 pair (no s16) produces two single-segment groups."""
    context = _build_batch_context_with_segments()
    unit = context.units[0]

    groups = build_deterministic_translation_groups(unit)

    assert [g.group_id for g in groups] == ["u2_g15_15", "u2_g17_17"]
    assert [g.anchor_segment_ids for g in groups] == [("s15",), ("s17",)]
    assert [g.order_index for g in groups] == [15, 17]
    # source_text_hash must come from the segment, not the LLM.
    assert groups[0].source_text_hash == compute_text_range_hash(_STAFF_LIVELIHOOD_SOURCE)
    assert groups[1].source_text_hash == compute_text_range_hash(_AUTO_TIP_BILLS_SOURCE)
    assert groups[0].source_text == _STAFF_LIVELIHOOD_SOURCE
    assert groups[1].source_text == _AUTO_TIP_BILLS_SOURCE


def test_build_deterministic_translation_groups_one_semantic_group_for_short_paragraph() -> None:
    """A short paragraph with 2-3 contiguous anchor segments produces ONE
    semantic translation group covering the whole paragraph.

    This is the a75d742a regression fixture (one-sentence-per-line display
    degradation): a short paragraph with multiple sentences must NOT be
    split into one-sentence-per-group. The semantic planner clusters 1-3
    short sentences into one group. This is NOT one-unit-one-group: the
    grouping decision is based on paragraph structure + sentence count,
    not on unit boundaries.

    The fixture joins s10/s11/s12 with a single space (one paragraph).
    The first sentence carries the ``$2.13 per hour`` decimal boundary,
    which must NOT be split by the planner (the planner only groups whole
    anchor segments; it never re-segments sentence text)."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    groups = build_deterministic_translation_groups(unit)

    # One semantic group covering all 3 sentences in the paragraph.
    assert len(groups) == 1
    assert groups[0].group_id == "u2_g10_12"
    assert list(groups[0].anchor_segment_ids) == ["s10", "s11", "s12"]
    assert groups[0].order_index == 10
    # source_text is the full paragraph span from s10.start to s12.end.
    assert groups[0].source_text == _build_contiguous_unit_source_text()
    # source_text_hash is the hash of that span, not a single segment hash.
    assert groups[0].source_text_hash == compute_text_range_hash(
        _build_contiguous_unit_source_text()
    )
    # Decimal boundary regression: the $2.13 per hour token must survive
    # intact inside the group's source_text (the planner must not split
    # the sentence at the decimal).
    assert "$2.13 per hour" in groups[0].source_text


def test_build_deterministic_translation_groups_splits_long_paragraph_at_safety_max() -> None:
    """A single paragraph whose total span exceeds
    ``TRANSLATION_GROUP_SAFETY_MAX_CHARS`` is split at anchor-segment
    boundaries into bounded groups (safety-max split within a paragraph)."""
    # Build a unit whose single paragraph exceeds the safety max.
    long_segment_a = "A" * 800 + "."
    long_segment_b = "B" * 800 + "."
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", long_segment_a, 1),
            ("s2", long_segment_b, 2),
        ],
        joiner=" ",  # one paragraph, two long sentences
    )
    unit = context.units[0]

    groups = build_deterministic_translation_groups(unit)

    # One paragraph, but total span > 1400 chars → safety-max split into
    # two single-sentence groups.
    assert len(groups) == 2
    assert groups[0].group_id == "u2_g1_1"
    assert groups[0].anchor_segment_ids == ("s1",)
    assert groups[1].group_id == "u2_g2_2"
    assert groups[1].anchor_segment_ids == ("s2",)


def test_batch_group_output_schema_rejects_anchor_segment_ids() -> None:
    """The misalignment vector (LLM choosing anchor_segment_ids) is removed
    at the schema level: TranslationBatchGroupOutput forbids any field
    other than group_id and translated_text."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TranslationBatchGroupOutput(  # type: ignore[call-arg]
            group_id="u2_g15_15",
            translated_text=_STAFF_LIVELIHOOD_ZH,
            anchor_segment_ids=["s17"],  # must be rejected (extra="forbid")
        )


def test_hydrate_batch_output_binds_anchors_from_backend_mapping_not_llm() -> None:
    """Regression for the s15/s17 misalignment: even if the LLM returns the
    s17 translation under group_id g15, the hydrated group g15 is anchored
    to s15 by the backend mapping. The LLM can no longer reassign anchors
    to translations; only the translated_text is taken from the LLM.

    Residual risk: this test proves the anchor binding is deterministic, NOT
    that translated_text semantically matches source_text. If the LLM puts
    wrong text under the right group_id, the published layer still carries a
    structurally-legal but textually-misaligned translation. Semantic
    verification requires LLM-as-a-Judge and is out of scope here.
    """
    context = _build_batch_context_with_segments()
    # LLM "misaligns" by putting the s17 translation under g15.
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [
                    ("u2_g15_15", _AUTO_TIP_BILLS_ZH),  # wrong text for this anchor
                    ("u2_g17_17", _STAFF_LIVELIHOOD_ZH),  # wrong text for this anchor
                ],
            )
        ]
    )

    outputs = hydrate_translation_batch_output(
        context=context,
        generation=generation,
    )

    assert len(outputs) == 1
    unit_id, layer = outputs[0]
    assert unit_id == "u2"
    by_group = {g.group_id: g for g in layer.groups}
    # The anchor binding is deterministic: g15 -> s15, g17 -> s17,
    # regardless of which translated_text the LLM attached.
    assert by_group["u2_g15_15"].anchor_segment_ids == ["s15"]
    assert by_group["u2_g15_15"].source_text_hash == compute_text_range_hash(
        _STAFF_LIVELIHOOD_SOURCE
    )
    assert by_group["u2_g15_15"].translated_text == _AUTO_TIP_BILLS_ZH
    assert by_group["u2_g17_17"].anchor_segment_ids == ["s17"]
    assert by_group["u2_g17_17"].source_text_hash == compute_text_range_hash(_AUTO_TIP_BILLS_SOURCE)
    assert by_group["u2_g17_17"].translated_text == _STAFF_LIVELIHOOD_ZH


def test_hydrate_batch_output_correct_alignment_preserves_translations() -> None:
    """When the LLM returns the correct translation per group_id, the
    hydrated layer has the right anchor + translated_text pairing."""
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [
                    ("u2_g15_15", _STAFF_LIVELIHOOD_ZH),
                    ("u2_g17_17", _AUTO_TIP_BILLS_ZH),
                ],
            )
        ]
    )

    outputs = hydrate_translation_batch_output(
        context=context,
        generation=generation,
    )

    by_group = {g.group_id: g for g in outputs[0][1].groups}
    assert by_group["u2_g15_15"].anchor_segment_ids == ["s15"]
    assert by_group["u2_g15_15"].translated_text == _STAFF_LIVELIHOOD_ZH
    assert by_group["u2_g17_17"].anchor_segment_ids == ["s17"]
    assert by_group["u2_g17_17"].translated_text == _AUTO_TIP_BILLS_ZH


def test_hydrate_batch_output_fail_closed_on_missing_group() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[_batch_unit_output("u2", [("u2_g15_15", _STAFF_LIVELIHOOD_ZH)])]  # missing g17
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_batch_missing_group"
    assert "u2_g17_17" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_hydrate_batch_output_fail_closed_on_extra_group() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [
                    ("u2_g15_15", _STAFF_LIVELIHOOD_ZH),
                    ("u2_g17_17", _AUTO_TIP_BILLS_ZH),
                    ("u2_g16_16", "bogus"),  # not a predefined group
                ],
            )
        ]
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_batch_extra_group"
    assert "u2_g16_16" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_hydrate_batch_output_fail_closed_on_duplicate_group_id() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[
            TranslationBatchUnitOutput(
                unit_id="u2",
                groups=[
                    TranslationBatchGroupOutput(
                        group_id="u2_g15_15", translated_text=_STAFF_LIVELIHOOD_ZH
                    ),
                    TranslationBatchGroupOutput(
                        group_id="u2_g15_15", translated_text="duplicate"
                    ),
                    TranslationBatchGroupOutput(
                        group_id="u2_g17_17", translated_text=_AUTO_TIP_BILLS_ZH
                    ),
                ],
            )
        ]
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_batch_duplicate_group_id"
    assert "u2_g15_15" in str(exc_info.value)


def test_hydrate_batch_output_fail_closed_on_unknown_unit() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[_batch_unit_output("u99", [("u2_g15_15", _STAFF_LIVELIHOOD_ZH)])]
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_batch_unknown_unit"
    assert "u99" in str(exc_info.value)


def test_hydrate_batch_output_fail_closed_on_blank_translated_text() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [("u2_g15_15", "   "), ("u2_g17_17", _AUTO_TIP_BILLS_ZH)],
            )
        ]
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    assert exc_info.value.failure_code == "translation_batch_empty_translated_text"
    assert "u2_g15_15" in str(exc_info.value)


def test_hydrate_batch_output_preserves_reading_order_regardless_of_llm_order() -> None:
    """Even if the LLM returns groups out of order, the hydrated layer is
    assembled in deterministic reading order (by anchor order_index)."""
    context = _build_batch_context_with_segments()
    # LLM returns g17 before g15.
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [("u2_g17_17", _AUTO_TIP_BILLS_ZH), ("u2_g15_15", _STAFF_LIVELIHOOD_ZH)],
            )
        ]
    )

    outputs = hydrate_translation_batch_output(context=context, generation=generation)
    layer = outputs[0][1]

    assert [g.group_id for g in layer.groups] == ["u2_g15_15", "u2_g17_17"]
    assert [g.anchor_segment_ids for g in layer.groups] == [["s15"], ["s17"]]


def test_hydrate_batch_output_covers_multiple_units_in_reading_order() -> None:
    """A batch with multiple units hydrates each unit's groups in reading
    order and the result list keeps the LLM's unit order (the publisher
    reorders to target_unit_ids; the hydrate just splits per unit)."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer_policy = strategy.layers["translation"]

    def _unit(uid: str, order: int) -> TranslationBatchUnitContext:
        seg_text = f"{uid} sentence."
        return TranslationBatchUnitContext(
            unit_id=uid,
            order_index=order,
            source_text=seg_text,
            text_hash=compute_text_range_hash(seg_text),
            anchor_segments=(
                TranslationAnchorSegmentTarget(
                    anchor_segment_id=f"{uid}_s1",
                    sentence_id=f"{uid}_s1",
                    order_index=1,
                    segment_type="sentence",
                    boundary_quality="normal",
                    unit_start_utf16=0,
                    unit_end_utf16=utf16_code_unit_length(seg_text),
                    text_hash=compute_text_range_hash(seg_text),
                    source_text=seg_text,
                ),
            ),
        )

    context = TranslationBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        expected_generation=1,
        operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        source_language="en",
        target_language="zh-CN",
        target_unit_ids=("u1", "u2"),
        units=(_unit("u1", 1), _unit("u2", 2)),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer_policy.policy_hash,
        translation_prompt_lines=layer_policy.prompt_lines,
    )
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output("u2", [("u2_g1_1", "译文u2")]),
            _batch_unit_output("u1", [("u1_g1_1", "译文u1")]),
        ]
    )

    outputs = hydrate_translation_batch_output(context=context, generation=generation)

    assert [uid for uid, _ in outputs] == ["u2", "u1"]
    assert outputs[0][1].groups[0].anchor_segment_ids == ["u2_s1"]
    assert outputs[1][1].groups[0].anchor_segment_ids == ["u1_s1"]


def test_build_translation_batch_prompt_emits_predefined_groups_and_forbids_llm_anchor_choice(
) -> None:
    """The batch prompt must hand the LLM pre-defined groups (group_id +
    source_text) and forbid it from choosing anchor_segment_ids."""
    context = _build_batch_context_with_segments()
    prompt = _build_translation_batch_prompt(context)

    # The prompt must NOT ask the LLM to output anchor_segment_ids.
    assert "Do not output anchor_segment_ids" in prompt
    # The predefined groups are emitted with their group_id + source_text_hash.
    assert '<translation_group group_id="u2_g15_15"' in prompt
    assert '<translation_group group_id="u2_g17_17"' in prompt
    assert "anchor_segment_ids=\"s15\"" in prompt
    assert "anchor_segment_ids=\"s17\"" in prompt
    # Each group carries its source_text inline so the LLM translates the
    # right span per group_id.
    assert _STAFF_LIVELIHOOD_SOURCE in prompt
    assert _AUTO_TIP_BILLS_SOURCE in prompt
    # The grouping contract forbids adding/merging/splitting/reordering.
    assert "<grouping_contract>" in prompt
    assert "PRE-DEFINED" in prompt
    assert "MUST NOT add new groups" in prompt or "never invent" in prompt
    # The prompt must not overpromise semantic validation. The backend
    # prevents LLM-selected anchor remapping, but cannot prove the returned
    # translated_text semantically matches the group's source_text.
    assert "rejected before publish" not in prompt
    assert "semantically-misaligned" not in prompt
    assert "translation-quality failure" in prompt


def test_build_translation_batch_prompt_does_not_ask_for_semantic_grouping() -> None:
    """The batch prompt must NOT carry the per-unit semantic-grouping
    guidance (which would contradict the deterministic grouping contract)."""
    context = _build_batch_context_with_segments()
    prompt = _build_translation_batch_prompt(context)

    # The per-unit grouping_guidance block must not appear in the batch
    # prompt; the batch uses grouping_contract instead.
    assert "<grouping_guidance>" not in prompt
    assert "<grouping_contract>" in prompt
    # The batch prompt must not invite the LLM to choose anchor handles.
    assert "anchor handle" not in prompt


# ---------------------------------------------------------------------------#
# Multi-anchor display group regression tests (a75d742a display degradation)
#
# Verifies that a short reading unit with contiguous anchor segments
# produces ONE translation group (not per-sentence), so the page displays
# one paragraph-level translation instead of one-sentence-per-line.
# ---------------------------------------------------------------------------#


def test_hydrate_batch_output_multi_anchor_group_binds_all_anchors() -> None:
    """A multi-anchor deterministic group (s10/s11/s12) hydrates with all
    three anchor_segment_ids and the correct span source_text_hash."""
    context = _build_batch_context_with_contiguous_segments()
    # LLM returns one group_id for the whole unit.
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [("u2_g10_12", "整段译文。")],
            )
        ]
    )

    outputs = hydrate_translation_batch_output(context=context, generation=generation)

    assert len(outputs) == 1
    unit_id, layer = outputs[0]
    assert unit_id == "u2"
    assert len(layer.groups) == 1
    group = layer.groups[0]
    assert group.group_id == "u2_g10_12"
    assert list(group.anchor_segment_ids) == ["s10", "s11", "s12"]
    assert group.source_text_hash == compute_text_range_hash(
        _build_contiguous_unit_source_text()
    )
    assert group.translated_text == "整段译文。"


def test_hydrate_batch_output_multi_anchor_group_fail_closed_on_wrong_group_id() -> None:
    """If the LLM returns a per-segment group_id for a unit that has a
    single multi-anchor group, hydrate must fail closed."""
    context = _build_batch_context_with_contiguous_segments()
    # LLM wrongly splits the unit into per-segment groups.
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [
                    ("u2_g10_10", "译文1"),
                    ("u2_g11_11", "译文2"),
                    ("u2_g12_12", "译文3"),
                ],
            )
        ]
    )

    with pytest.raises(TranslationExecutionError) as exc_info:
        hydrate_translation_batch_output(context=context, generation=generation)
    # The predefined group is u2_g10_12, so the per-segment ids are "extra".
    assert exc_info.value.failure_code == "translation_batch_extra_group"


def test_build_translation_batch_prompt_emits_multi_anchor_group() -> None:
    """The batch prompt emits a single <translation_group> with multiple
    anchor_segment_ids for a short paragraph (3 sentences clustered into
    one semantic group)."""
    context = _build_batch_context_with_contiguous_segments()
    prompt = _build_translation_batch_prompt(context)

    # One group covering s10/s11/s12 (one semantic paragraph).
    assert 'group_id="u2_g10_12"' in prompt
    assert 'anchor_segment_ids="s10,s11,s12"' in prompt
    # The full span source_text is included.
    assert _MIN_WAGE_SOURCE in prompt
    assert _RELY_ON_TIPS_SOURCE in prompt
    assert _BATCH_STAFF_LIVELIHOOD_SOURCE in prompt
    # The prompt does not emit per-segment groups for this paragraph.
    assert 'group_id="u2_g10_10"' not in prompt
    assert 'group_id="u2_g11_11"' not in prompt
    assert 'group_id="u2_g12_12"' not in prompt


# ---------------------------------------------------------------------------#
# T1.1a semantic translation group planner tests.
#
# The batch path must NOT mechanically collapse to one-anchor-one-group,
# one-sentence-one-group, or one-unit-one-group. The planner produces
# semantic groups by splitting at paragraph boundaries and clustering
# 1-3 short sentences within a paragraph.
# ---------------------------------------------------------------------------#


def test_plan_translation_groups_returns_only_anchor_segment_ids() -> None:
    """Planner/translator boundary: ``plan_translation_groups`` returns ONLY
    ``anchor_segment_ids`` ranges. It does NOT return ``group_id``,
    ``source_text``, ``source_text_hash``, or ``translated_text`` — those
    are hydrated by the backend after validation. This keeps the planner
    replaceable (deterministic heuristic today, LLM-based planner later)
    without changing the translator contract."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    assert len(plans) == 1
    plan = plans[0]
    assert isinstance(plan, TranslationGroupPlan)
    # The plan ONLY carries anchor_segment_ids.
    assert plan.anchor_segment_ids == ("s10", "s11", "s12")
    # The plan does NOT carry hydrated fields.
    assert not hasattr(plan, "group_id")
    assert not hasattr(plan, "source_text")
    assert not hasattr(plan, "source_text_hash")
    assert not hasattr(plan, "translated_text")


def test_plan_translation_groups_merges_short_single_sentence_paragraphs() -> None:
    """A unit with multiple short single-sentence "paragraphs" (``\\n\\n``
    gaps between segments) must NOT produce one group per paragraph.
    Newlines are a SOFT hint only. Consecutive short single-sentence
    paragraphs (common in Reuters/BBC news feeds) are merged into 2-3-anchor
    reading groups so the page does not regress to per-sentence fragmented
    translation."""
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s10", _MIN_WAGE_SOURCE, 10),
            ("s11", _RELY_ON_TIPS_SOURCE, 11),
            ("s12", _BATCH_STAFF_LIVELIHOOD_SOURCE, 12),
        ],
        joiner="\n\n",  # 3 single-sentence paragraphs
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    # 3 short single-sentence "paragraphs" → 1 semantic group (NOT 3
    # per-paragraph groups). Newlines do NOT force a group boundary.
    assert len(plans) == 1
    assert plans[0].anchor_segment_ids == ("s10", "s11", "s12")
    # Explicitly forbid the one-paragraph-one-group regression.
    assert len(plans) != len(unit.anchor_segments)


def test_plan_translation_groups_long_paragraph_cluster() -> None:
    """A single paragraph with 4+ sentences is clustered into 2-3 bounded
    semantic groups (NOT one-unit-one-group, NOT one-sentence-one-group)."""
    # 6 short sentences in one paragraph (space-joined).
    sentences = [
        ("s1", "First sentence here.", 1),
        ("s2", "Second sentence here.", 2),
        ("s3", "Third sentence here.", 3),
        ("s4", "Fourth sentence here.", 4),
        ("s5", "Fifth sentence here.", 5),
        ("s6", "Sixth sentence here.", 6),
    ]
    context = _build_batch_context_with_segments(
        segment_specs=sentences,
        joiner=" ",  # one paragraph
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    # 6 sentences, MAX_SENTENCES_PER_GROUP=3 → 2 groups (3+3).
    assert len(plans) == 2
    assert plans[0].anchor_segment_ids == ("s1", "s2", "s3")
    assert plans[1].anchor_segment_ids == ("s4", "s5", "s6")


def test_plan_translation_groups_decimal_boundary_preserved() -> None:
    """The ``$2.13 per hour`` decimal boundary must NOT be split by the
    planner. The planner only groups whole anchor segments; it never
    re-segments sentence text. The segment containing the decimal stays
    intact inside one group."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    plans = plan_translation_groups(unit)
    groups = _hydrate_translation_groups(unit, plans)

    # The decimal-bearing segment s10 is fully inside one group's
    # source_text (not split across groups).
    assert any(
        "$2.13 per hour" in group.source_text for group in groups
    )
    # No group's source_text starts or ends mid-decimal: each group's
    # source_text boundaries align with anchor-segment boundaries.
    for group in groups:
        assert not group.source_text.startswith("13")
        assert not group.source_text.endswith("$2")


def test_plan_translation_groups_never_one_unit_one_group_for_multi_paragraph() -> None:
    """Regression assertion: a multi-paragraph unit must NOT collapse to
    one-unit-one-group. Newlines are a SOFT hint only, so 4 short
    single-sentence "paragraphs" are merged into bounded reading groups
    (2 groups of 2-3 anchors), NOT 1 group for the whole unit and NOT
    4 per-paragraph groups."""
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First paragraph sentence.", 1),
            ("s2", "Second paragraph sentence.", 2),
            ("s3", "Third paragraph sentence.", 3),
            ("s4", "Fourth paragraph sentence.", 4),
        ],
        joiner="\n\n",  # 4 single-sentence paragraphs
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    # 4 short single-sentence "paragraphs" → 2 groups (3+1), NOT 4
    # per-paragraph groups and NOT 1 whole-unit group.
    assert len(plans) == 2
    assert plans[0].anchor_segment_ids == ("s1", "s2", "s3")
    assert plans[1].anchor_segment_ids == ("s4",)
    # Explicitly forbid the one-unit-one-group regression.
    assert len(plans) != 1 or len(unit.anchor_segments) == 1
    # Explicitly forbid the one-paragraph-one-group regression.
    assert len(plans) != len(unit.anchor_segments)


def test_plan_translation_groups_merges_six_single_sentence_paragraphs() -> None:
    """Regression assertion (P1 fix): 6 short single-sentence "paragraphs"
    joined by ``\\n\\n`` must NOT produce 6 per-sentence groups. Newlines
    are a SOFT hint only. The planner merges consecutive short
    single-sentence paragraphs into 2-3-anchor reading groups so
    Reuters/BBC-style news feeds (where each sentence is its own line)
    do not regress to per-sentence fragmented translation."""
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First sentence here.", 1),
            ("s2", "Second sentence here.", 2),
            ("s3", "Third sentence here.", 3),
            ("s4", "Fourth sentence here.", 4),
            ("s5", "Fifth sentence here.", 5),
            ("s6", "Sixth sentence here.", 6),
        ],
        joiner="\n\n",  # 6 single-sentence paragraphs
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    # 6 short single-sentence "paragraphs" → 2 groups (3+3), NOT 6
    # per-sentence groups. Newlines do NOT force a group boundary.
    assert len(plans) == 2
    assert plans[0].anchor_segment_ids == ("s1", "s2", "s3")
    assert plans[1].anchor_segment_ids == ("s4", "s5", "s6")
    # Explicitly forbid the one-paragraph-one-group regression.
    assert len(plans) != len(unit.anchor_segments)
    # Explicitly forbid the one-unit-one-group regression.
    assert len(plans) != 1


def test_plan_translation_groups_never_one_anchor_one_group_for_multi_sentence_paragraph() -> None:
    """Regression assertion: a single paragraph with 2-3 short sentences
    must NOT be split into one-anchor-one-group. The planner clusters them
    into ONE semantic group."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    plans = plan_translation_groups(unit)

    # 3 short sentences in one paragraph → 1 group (NOT 3 per-sentence
    # groups).
    assert len(plans) == 1
    assert plans[0].anchor_segment_ids == ("s10", "s11", "s12")
    # Explicitly forbid the one-anchor-one-group regression for this
    # multi-sentence paragraph.
    assert len(plans) != len(unit.anchor_segments)


def test_hydrate_translation_groups_assigns_stable_group_ids_and_hashes() -> None:
    """Hydration produces stable ``group_id`` / ``source_text_hash`` /
    ``source_text`` from the plan's anchor ranges. The group_id follows
    ``{unit_id}_g{first_order}_{last_order}`` so the hydrate step in
    ``hydrate_translation_batch_output`` can re-derive the same mapping
    and reject any LLM output whose group_id set does not exactly match."""
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First sentence here.", 1),
            ("s2", "Second sentence here.", 2),
            ("s3", "Third sentence here.", 3),
            ("s4", "Fourth sentence here.", 4),
            ("s5", "Fifth sentence here.", 5),
            ("s6", "Sixth sentence here.", 6),
        ],
        joiner=" ",  # one paragraph → clustered into 2 groups
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)
    groups = _hydrate_translation_groups(unit, plans)

    assert len(groups) == 2
    # Stable group_ids derived from anchor order_index ranges.
    assert groups[0].group_id == "u2_g1_3"
    assert groups[1].group_id == "u2_g4_6"
    # source_text_hash is the fnv1a32 hash of each group's span.
    assert groups[0].source_text_hash == compute_text_range_hash(
        groups[0].source_text
    )
    assert groups[1].source_text_hash == compute_text_range_hash(
        groups[1].source_text
    )
    # source_text is the slice from first anchor start to last anchor end.
    assert "First sentence here." in groups[0].source_text
    assert "Third sentence here." in groups[0].source_text
    assert "Fourth sentence here." in groups[1].source_text
    assert "Sixth sentence here." in groups[1].source_text
    # Reading order is stable.
    assert groups[0].order_index < groups[1].order_index


def test_validate_translation_group_plan_rejects_incomplete_coverage() -> None:
    """A plan that does not cover all unit anchors fails closed."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    # Plan covers only s10/s11, missing s12.
    incomplete_plan = [TranslationGroupPlan(anchor_segment_ids=("s10", "s11"))]

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_group_plan(unit, incomplete_plan)
    assert exc_info.value.failure_code == "translation_group_plan_incomplete_coverage"
    assert "s12" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_translation_group_plan_rejects_overlap() -> None:
    """A plan where two groups cover the same anchor fails closed."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    overlapping_plans = [
        TranslationGroupPlan(anchor_segment_ids=("s10", "s11")),
        TranslationGroupPlan(anchor_segment_ids=("s11", "s12")),  # s11 overlap
    ]

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_group_plan(unit, overlapping_plans)
    assert exc_info.value.failure_code == "translation_group_plan_overlap"
    assert "s11" in str(exc_info.value)


def test_validate_translation_group_plan_rejects_unknown_anchor() -> None:
    """A plan referencing an anchor not in the unit fails closed."""
    context = _build_batch_context_with_contiguous_segments()
    unit = context.units[0]

    bad_plan = [TranslationGroupPlan(anchor_segment_ids=("s10", "s99"))]

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_group_plan(unit, bad_plan)
    assert exc_info.value.failure_code == "translation_group_plan_unknown_anchor"
    assert "s99" in str(exc_info.value)


def test_validate_translation_group_plan_rejects_non_contiguous_group() -> None:
    """A plan whose group has non-consecutive order_index fails closed
    (the publisher requires contiguity within a group)."""
    context = _build_batch_context_with_segments()  # s15/s17 (order 15/17)
    unit = context.units[0]

    # Forbid a single group spanning s15+s17 (order 15 then 17, no 16).
    non_contiguous_plan = [
        TranslationGroupPlan(anchor_segment_ids=("s15", "s17"))
    ]

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_group_plan(unit, non_contiguous_plan)
    assert exc_info.value.failure_code == "translation_group_plan_non_contiguous"


def test_validate_translation_group_plan_rejects_unstable_order() -> None:
    """A plan whose groups are not in ascending reading order fails closed."""
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First sentence here.", 1),
            ("s2", "Second sentence here.", 2),
            ("s3", "Third sentence here.", 3),
            ("s4", "Fourth sentence here.", 4),
        ],
        joiner="\n\n",  # gap text does not affect manual invalid plan
    )
    unit = context.units[0]

    # Reversed order: s4 group before s1 group.
    reversed_plans = [
        TranslationGroupPlan(anchor_segment_ids=("s4",)),
        TranslationGroupPlan(anchor_segment_ids=("s3",)),
        TranslationGroupPlan(anchor_segment_ids=("s2",)),
        TranslationGroupPlan(anchor_segment_ids=("s1",)),
    ]

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_group_plan(unit, reversed_plans)
    assert exc_info.value.failure_code == "translation_group_plan_unstable_order"


def test_hydrate_translation_groups_full_coverage_contiguous_no_overlap() -> None:
    """End-to-end planner + hydrate assertion: published groups have full
    anchor coverage, contiguous anchor_segment_ids within each group, no
    overlap across groups, stable reading order, and correct
    source_text_hash per group."""
    # 6 sentences in one paragraph → 2 clustered groups (3+3).
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First sentence here.", 1),
            ("s2", "Second sentence here.", 2),
            ("s3", "Third sentence here.", 3),
            ("s4", "Fourth sentence here.", 4),
            ("s5", "Fifth sentence here.", 5),
            ("s6", "Sixth sentence here.", 6),
        ],
        joiner=" ",
    )
    unit = context.units[0]

    plans = plan_translation_groups(unit)
    groups = _hydrate_translation_groups(unit, plans)

    # 2-4 semantic groups for a 6-sentence paragraph.
    assert 2 <= len(groups) <= 4

    all_anchor_ids = [seg.anchor_segment_id for seg in unit.anchor_segments]
    covered: list[str] = []
    previous_order: int | None = None
    seen: set[str] = set()
    for group in groups:
        # No overlap.
        for aid in group.anchor_segment_ids:
            assert aid not in seen
            seen.add(aid)
            covered.append(aid)
        # Contiguity within a group (order_index consecutive).
        orders = [
            next(
                seg.order_index
                for seg in unit.anchor_segments
                if seg.anchor_segment_id == aid
            )
            for aid in group.anchor_segment_ids
        ]
        for prev_order, curr_order in zip(orders, orders[1:], strict=False):
            assert curr_order == prev_order + 1
        # Stable reading order across groups.
        if previous_order is not None:
            assert orders[0] > previous_order
        previous_order = orders[-1]
        # source_text_hash is correct for the group's span.
        assert group.source_text_hash == compute_text_range_hash(
            group.source_text
        )

    # Full coverage.
    assert covered == all_anchor_ids


class _FakePlannerTranslator:
    """Fake planner + translator for the batch path. Mirrors the real
    planner/translator boundary: the planner decides anchor ranges (via
    ``plan_translation_groups``), the backend hydrates group_id /
    source_text / source_text_hash, and the translator only echoes
    group_id + a per-group translated_text. No real LLM is invoked."""

    def __init__(self) -> None:
        self.translate_calls: list[TranslationBatchJobContext] = []

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        self.translate_calls.append(context)
        units_output = []
        for unit in context.units:
            groups = build_deterministic_translation_groups(unit)
            units_output.append(
                TranslationBatchUnitOutput(
                    unit_id=unit.unit_id,
                    groups=[
                        TranslationBatchGroupOutput(
                            group_id=group.group_id,
                            translated_text=f"译文：{group.source_text}",
                        )
                        for group in groups
                    ],
                )
            )
        return TranslationBatchExecutionResult(
            output=TranslationBatchGenerationOutput(units=units_output),
            usage_data={"input_tokens": 1, "output_tokens": 1},
            prompt_version="test-fake-planner-translator",
            model_profile="fake_planner_translator",
            model_provider="fake",
            model_name="fake-planner-translator",
        )


@pytest.mark.anyio
async def test_batch_path_with_fake_planner_translator_produces_semantic_groups() -> None:
    """End-to-end batch path test using a fake planner/translator. The
    fake translator consumes the backend-hydrated groups (group_id +
    source_text) and returns group_id + translated_text. The hydrated
    output must have semantic groups (NOT one-unit-one-group, NOT
    one-paragraph-one-group) with full coverage, correct anchor binding,
    and correct source_text_hash."""
    # 6 short single-sentence "paragraphs" (Reuters/BBC news feed scenario)
    # → 2 semantic groups (3+3), NOT 6 per-paragraph groups.
    context = _build_batch_context_with_segments(
        segment_specs=[
            ("s1", "First sentence here.", 1),
            ("s2", "Second sentence here.", 2),
            ("s3", "Third sentence here.", 3),
            ("s4", "Fourth sentence here.", 4),
            ("s5", "Fifth sentence here.", 5),
            ("s6", "Sixth sentence here.", 6),
        ],
        joiner="\n\n",
    )

    fake = _FakePlannerTranslator()
    result = await fake.translate_batch(context)

    outputs = hydrate_translation_batch_output(
        context=context, generation=result.output
    )

    assert len(outputs) == 1
    unit_id, layer = outputs[0]
    assert unit_id == "u2"
    # 6 short single-sentence "paragraphs" → 2 semantic groups (3+3),
    # NOT 6 per-paragraph groups and NOT 1 whole-unit group.
    assert len(layer.groups) == 2
    # Full coverage, no overlap, stable order.
    all_anchors = [seg.anchor_segment_id for seg in context.units[0].anchor_segments]
    covered = [aid for group in layer.groups for aid in group.anchor_segment_ids]
    assert covered == all_anchors
    # group_ids are the stable hydrated ids derived from anchor order ranges.
    assert [group.group_id for group in layer.groups] == [
        "u2_g1_3",
        "u2_g4_6",
    ]
    # source_text_hash matches the fnv1a32 hash of each group's full span
    # (from the first anchor's unit_start_utf16 to the last anchor's
    # unit_end_utf16, including gap text between segments).
    segments_by_id = {
        seg.anchor_segment_id: seg for seg in context.units[0].anchor_segments
    }
    for group in layer.groups:
        first_seg = segments_by_id[group.anchor_segment_ids[0]]
        last_seg = segments_by_id[group.anchor_segment_ids[-1]]
        span_text = slice_by_utf16_offsets(
            context.units[0].source_text,
            first_seg.unit_start_utf16,
            last_seg.unit_end_utf16,
        )
        assert span_text is not None
        assert group.source_text_hash == compute_text_range_hash(span_text)


@pytest.mark.anyio
async def test_batch_window_output_preserves_translation_group_contract() -> None:
    """T3.1 Translation Group contract regression: a multi-unit batch window
    (the shape a non-short ``translate_article`` window job produces) must
    NOT degrade to one-unit-one-group, one-anchor-one-group, or
    one-sentence-one-group. The ``$2.13 per hour`` decimal boundary must
    stay intact inside one group's anchor span.

    This test builds a 2-unit batch context simulating a translation window:
    - Unit w1: 4 short single-sentence paragraphs → 2 semantic groups (3+1)
    - Unit w2: 3 contiguous sentences (one carries ``$2.13 per hour``) → 1
      group with all 3 anchors

    The fake planner/translator echoes the backend-predefined group_ids.
    ``hydrate_translation_batch_output`` must produce per-unit
    ``TranslationLayerOutput`` whose groups match the semantic plan.
    """
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer_policy = strategy.layers["translation"]

    # Unit w1: 4 short single-sentence "paragraphs" (\\n\\n-joined).
    # Planner merges into 2 groups (3+1), NOT 4 per-paragraph and NOT 1
    # whole-unit (one-unit-one-group regression).
    w1_segments = [
        ("w1_s1", "First paragraph for window contract test.", 1),
        ("w1_s2", "Second paragraph for window contract test.", 2),
        ("w1_s3", "Third paragraph for window contract test.", 3),
        ("w1_s4", "Fourth paragraph for window contract test.", 4),
    ]
    w1_texts = [spec[1] for spec in w1_segments]
    w1_joiner = "\n\n"
    w1_source = w1_joiner.join(w1_texts)
    w1_joiner_len = utf16_code_unit_length(w1_joiner)
    w1_anchors: list[TranslationAnchorSegmentTarget] = []
    cursor = 0
    for aid, text, order in w1_segments:
        start = cursor
        end = cursor + utf16_code_unit_length(text)
        w1_anchors.append(
            TranslationAnchorSegmentTarget(
                anchor_segment_id=aid,
                sentence_id=aid,
                order_index=order,
                segment_type="sentence",
                boundary_quality="normal",
                unit_start_utf16=start,
                unit_end_utf16=end,
                text_hash=compute_text_range_hash(text),
                source_text=text,
            )
        )
        cursor = end + w1_joiner_len

    # Unit w2: 3 contiguous sentences in ONE paragraph (space-joined).
    # The first carries ``$2.13 per hour``. Planner clusters into 1 group
    # with all 3 anchors; the decimal stays inside one anchor segment.
    w2_segments = [
        ("w2_s10", _MIN_WAGE_SOURCE, 10),
        ("w2_s11", _RELY_ON_TIPS_SOURCE, 11),
        ("w2_s12", _BATCH_STAFF_LIVELIHOOD_SOURCE, 12),
    ]
    w2_texts = [spec[1] for spec in w2_segments]
    w2_joiner = " "
    w2_source = w2_joiner.join(w2_texts)
    w2_joiner_len = utf16_code_unit_length(w2_joiner)
    w2_anchors: list[TranslationAnchorSegmentTarget] = []
    cursor = 0
    for aid, text, order in w2_segments:
        start = cursor
        end = cursor + utf16_code_unit_length(text)
        w2_anchors.append(
            TranslationAnchorSegmentTarget(
                anchor_segment_id=aid,
                sentence_id=aid,
                order_index=order,
                segment_type="sentence",
                boundary_quality="normal",
                unit_start_utf16=start,
                unit_end_utf16=end,
                text_hash=compute_text_range_hash(text),
                source_text=text,
            )
        )
        cursor = end + w2_joiner_len

    context = TranslationBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        expected_generation=1,
        operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        source_language="en",
        target_language="zh-CN",
        target_unit_ids=("w1", "w2"),
        units=(
            TranslationBatchUnitContext(
                unit_id="w1",
                order_index=1,
                source_text=w1_source,
                text_hash=compute_text_range_hash(w1_source),
                anchor_segments=tuple(w1_anchors),
            ),
            TranslationBatchUnitContext(
                unit_id="w2",
                order_index=2,
                source_text=w2_source,
                text_hash=compute_text_range_hash(w2_source),
                anchor_segments=tuple(w2_anchors),
            ),
        ),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer_policy.policy_hash,
        translation_prompt_lines=layer_policy.prompt_lines,
    )

    fake = _FakePlannerTranslator()
    result = await fake.translate_batch(context)
    outputs = hydrate_translation_batch_output(context=context, generation=result.output)

    # The window covers 2 units → 2 per-unit outputs.
    assert len(outputs) == 2
    assert [uid for uid, _ in outputs] == ["w1", "w2"]

    w1_uid, w1_layer = outputs[0]
    w2_uid, w2_layer = outputs[1]

    # Unit w1: 4 single-sentence paragraphs → 2 semantic groups (3+1).
    # NOT 1 (one-unit-one-group) and NOT 4 (one-anchor-one-group /
    # one-sentence-one-group).
    assert len(w1_layer.groups) == 2, (
        f"w1 expected 2 semantic groups, got {len(w1_layer.groups)} "
        f"(one-unit-one-group={len(w1_layer.groups) == 1}, "
        f"one-anchor-one-group={len(w1_layer.groups) == len(w1_anchors)})"
    )
    # Full coverage, no overlap, stable order for w1.
    w1_all_anchors = [seg.anchor_segment_id for seg in w1_anchors]
    w1_covered = [aid for group in w1_layer.groups for aid in group.anchor_segment_ids]
    assert w1_covered == w1_all_anchors
    # Each group's anchors are contiguous.
    for group in w1_layer.groups:
        orders = [
            next(seg.order_index for seg in w1_anchors if seg.anchor_segment_id == aid)
            for aid in group.anchor_segment_ids
        ]
        assert orders == sorted(orders)

    # Unit w2: 3 contiguous sentences → 1 semantic group with all 3 anchors.
    # NOT 3 (one-anchor-one-group) and NOT 1 group with only 1 anchor.
    assert len(w2_layer.groups) == 1, (
        f"w2 expected 1 semantic group, got {len(w2_layer.groups)}"
    )
    w2_group = w2_layer.groups[0]
    w2_all_anchors = [seg.anchor_segment_id for seg in w2_anchors]
    assert list(w2_group.anchor_segment_ids) == w2_all_anchors, (
        "w2 group must cover all 3 anchors (decimal-bearing s10 + s11 + s12)"
    )

    # The ``$2.13 per hour`` decimal boundary is fully inside one group's
    # anchor span (the planner only groups whole anchor segments; it never
    # re-segments sentence text). The decimal-bearing anchor w2_s10 is in
    # the group, and the group's source_text_hash covers the full span
    # from w2_s10's start to w2_s12's end.
    assert "w2_s10" in w2_group.anchor_segment_ids
    w2_segments_by_id = {seg.anchor_segment_id: seg for seg in w2_anchors}
    w2_first = w2_segments_by_id["w2_s10"]
    w2_last = w2_segments_by_id["w2_s12"]
    w2_span = slice_by_utf16_offsets(
        w2_source,
        w2_first.unit_start_utf16,
        w2_last.unit_end_utf16,
    )
    assert w2_span is not None
    assert "$2.13 per hour" in w2_span
    assert w2_group.source_text_hash == compute_text_range_hash(w2_span)

# ---------------------------------------------------------------------------#
# Failure-side usage completeness
# ---------------------------------------------------------------------------#
# Every failure path below proves that a real model invocation that returned
# usage_data persists EXACTLY ONE usage event (tokens + model identity +
# run/job/record identity), while an executor that returns without a usage
# payload never fabricates tokens.


class _PostExecutionFailTranslationPublisher(_CapturingPublisher):
    """Injected publisher that raises the given error after the model call
    (publish fence violation, typed worker error, or generic failure)."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def publish_unit_translation(self, **kwargs) -> PublishedTranslationLayer:
        raise self.error

    async def publish_article_translation_batch(self, **kwargs) -> object:
        raise self.error


async def _tamper_unit_job_into_batch(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    unit_id: str,
    job_type: str,
) -> None:
    """Tamper a per-unit job into a batch job: the batch context loader
    resolves units from input_json.target_unit_ids."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
        input_json = dict(row["input_json"])
        input_json["target_unit_ids"] = [unit_id]
        await conn.execute(
            """
            UPDATE reader_jobs
            SET job_type = $2,
                target_type = 'unit_range',
                input_json = $3::jsonb
            WHERE id = $1
            """,
            job_id,
            job_type,
            jsonb_param(input_json),
        )


class _PostExecutionFailBatchTranslator:
    """Fake batch translator whose output fails post-execution hydration
    (an unknown group id → typed terminal validation failure)."""

    async def translate_batch(
        self,
        context: TranslationBatchJobContext,
    ) -> TranslationBatchExecutionResult:
        return TranslationBatchExecutionResult(
            output=TranslationBatchGenerationOutput(
                units=[
                    TranslationBatchUnitOutput(
                        unit_id=context.units[0].unit_id,
                        groups=[
                            TranslationBatchGroupOutput(
                                group_id="bogus-group",
                                translated_text="x",
                            )
                        ],
                    )
                ]
            ),
            usage_data={
                "aggregate": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                }
            },
            prompt_version="test-batch-typed-fail",
            model_profile="fake-batch-profile",
            model_provider="fake-provider",
            model_name="fake-batch-model",
        )


async def _fetch_translation_usage_rows(pool: asyncpg.Pool, job_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT status, capability_code, usage_scope, billing_mode, model_route,
                   model_profile_id, model_provider, model_name, reader_run_id,
                   reader_job_id, enhancement_layer_id, input_tokens, output_tokens,
                   total_tokens, error_code, metadata_json
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at ASC
            """,
            job_id,
        )


async def test_worker_publish_fence_records_failed_usage_with_consumed_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Provider returned usage_data and the publish fence fails after the
    model execution. Exactly one failed usage event must carry the consumed
    tokens, the model identity and the run/job identity; the job still ends
    superseded."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_generation_output()),
        layer_publisher=_PostExecutionFailTranslationPublisher(
            FenceViolationError("translation publish fence failed for usage test")
        ),
    )
    claim = await worker.claim_translation_job(
        lease_owner="translation-worker-fence-usage",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    with pytest.raises(FenceViolationError):
        await worker.process_claimed_translation_job(claim=claim)

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status, failure_class, failure_code FROM reader_runs WHERE id = $1",
            claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "superseded"
    assert job_row["rationale_code"] == "publish_fence_failed"
    assert run_row is not None and run_row["status"] == "superseded"
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["usage_scope"] == "system_internal"
    assert usage_row["billing_mode"] == "internal_only"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-model"
    assert usage_row["reader_run_id"] == claim.run_id
    assert usage_row["reader_job_id"] == claim.job_id
    assert usage_row["enhancement_layer_id"] is None
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["error_code"] is None


async def test_worker_retryable_failure_after_provider_call_records_failed_usage_with_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Retryable post-execution failure: provider returned usage_data, a
    retryable failure follows. The failed usage event must keep the consumed
    tokens and the model identity; the job still ends retry_later."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_generation_output()),
        layer_publisher=_PostExecutionFailTranslationPublisher(
            TranslationExecutionError(
                "post-provider retryable failure",
                retryable=True,
                failure_class="provider",
                failure_code="post_provider_retryable",
            )
        ),
    )

    result = await worker.process_next_translation_job(
        lease_owner="translation-worker-retry-after-provider",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=3),
    )

    assert result is not None
    assert result.status == "paused"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, result.claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "paused"
    assert run_row is not None and run_row["status"] == "running"
    assert run_row["finished_at"] is None
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["error_code"] is None


async def test_worker_terminal_failure_after_provider_call_records_failed_usage_with_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Terminal post-execution failure: provider returned usage_data,
    hydration fails terminally. The failed usage event must keep the consumed
    tokens and the model identity; the job still ends failed_terminal."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(
            _translation_generation_output("译文", ["ghost-anchor"])
        ),
    )

    result = await worker.process_next_translation_job(
        lease_owner="translation-worker-terminal-after-provider",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, result.claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "failed_terminal"
    assert run_row is not None and run_row["status"] == "failed_terminal"
    assert run_row["finished_at"] is not None
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "failed"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["error_code"] == "translation_unknown_anchor_segment"


async def test_batch_worker_publish_fence_records_failed_usage_with_consumed_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Batch publish fence after model execution: provider returned
    usage_data, the batch publish fence fails. Exactly one failed usage
    event must carry the consumed tokens and the model identity; the job
    still ends superseded and the fence still propagates."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    bootstrap_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    # Tamper the per-unit job into a batch job: the batch context loader
    # resolves units from input_json.target_unit_ids.
    await _tamper_unit_job_into_batch(
        translation_worker_env,
        job_id=bootstrap_result.job_id,
        unit_id=bootstrap_result.unit_id,
        job_type="translate_article",
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=DevFakeTranslationBatchExecutor(),
        layer_publisher=_PostExecutionFailTranslationPublisher(
            FenceViolationError("translation batch publish fence failed for usage test")
        ),
    )
    claim = await worker.claim_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-worker-fence-usage",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    with pytest.raises(FenceViolationError):
        await worker.process_claimed_translation_batch_job(claim=claim)

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "superseded"
    assert job_row["rationale_code"] == "publish_fence_failed"
    assert run_row is not None and run_row["status"] == "superseded"
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["usage_scope"] == "system_internal"
    assert usage_row["billing_mode"] == "internal_only"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "reader_smoke_fake_translation_batch"
    assert usage_row["model_provider"] == "fake"
    assert usage_row["model_name"] == "reader-smoke-fake-translation-batch"
    assert usage_row["reader_run_id"] == claim.run_id
    assert usage_row["reader_job_id"] == claim.job_id
    assert usage_row["enhancement_layer_id"] is None
    assert usage_row["input_tokens"] == 1
    assert usage_row["output_tokens"] == 1
    assert usage_row["total_tokens"] == 2
    assert usage_row["error_code"] is None


async def test_worker_failure_without_usage_payload_does_not_fabricate_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Executor returned without a reliable usage payload. The failed event
    keeps the confirmed no-token semantics: tokens stay 0 and no zero
    snapshot is fabricated into metadata."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    translator = _StaticTranslator(_translation_generation_output())
    translator.usage_data = None
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=translator,
        layer_publisher=_PostExecutionFailTranslationPublisher(
            TranslationExecutionError(
                "post-provider retryable failure",
                retryable=True,
                failure_class="provider",
                failure_code="post_provider_retryable",
            )
        ),
    )

    result = await worker.process_next_translation_job(
        lease_owner="translation-worker-no-usage-payload",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=3),
    )

    assert result is not None
    assert result.status == "paused"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, result.claim.job_id
    )
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["input_tokens"] == 0
    assert usage_row["output_tokens"] == 0
    assert usage_row["total_tokens"] == 0
    metadata_json = dict(usage_row["metadata_json"])
    assert "usage_snapshot" not in metadata_json

async def test_worker_generic_failure_after_provider_call_records_failed_usage_with_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Generic post-execution failure: provider returned usage_data, an
    untyped exception follows. The failed usage event must keep the consumed
    tokens and the model identity; the job still ends failed_terminal."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    await TranslationJobBootstrapService(pool=translation_worker_env).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_generation_output()),
        layer_publisher=_PostExecutionFailTranslationPublisher(
            RuntimeError("post-provider generic failure")
        ),
    )

    result = await worker.process_next_translation_job(
        lease_owner="translation-worker-generic-after-provider",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "paused"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, result.claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "paused"
    assert run_row is not None and run_row["status"] == "running"
    assert run_row["finished_at"] is None
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["error_code"] is None


async def test_batch_worker_typed_failure_after_provider_call_records_failed_usage_with_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Typed post-execution failure on the batch path: the provider returned
    usage_data but hydration rejects the output terminally. The failed usage
    event keeps the consumed tokens and the model identity; the job still
    ends failed_terminal."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    bootstrap_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    await _tamper_unit_job_into_batch(
        translation_worker_env,
        job_id=bootstrap_result.job_id,
        unit_id=bootstrap_result.unit_id,
        job_type="translate_article",
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=_PostExecutionFailBatchTranslator(),
    )
    claim = await worker.claim_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-worker-typed-failure",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    result = await worker.process_claimed_translation_batch_job(claim=claim)

    assert result is not None
    assert result.status == "failed_terminal"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "failed_terminal"
    assert run_row is not None and run_row["status"] == "failed_terminal"
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "failed"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "fake-batch-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-batch-model"
    assert usage_row["reader_run_id"] == claim.run_id
    assert usage_row["reader_job_id"] == claim.job_id
    assert usage_row["input_tokens"] == 1
    assert usage_row["output_tokens"] == 1
    assert usage_row["total_tokens"] == 2
    assert usage_row["error_code"] == "translation_batch_extra_group"


async def test_batch_worker_generic_failure_after_provider_call_records_failed_usage_with_tokens(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """Generic post-execution failure on the batch path: the provider
    returned usage_data, an untyped exception follows. The failed usage
    event keeps the consumed tokens and the model identity; the job still
    ends failed_terminal."""
    user_id = await insert_user(translation_worker_env)
    article = await submit_article_ready(translation_worker_env, user_id=user_id)
    bootstrap = TranslationJobBootstrapService(pool=translation_worker_env)
    bootstrap_result = await bootstrap.bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    await _tamper_unit_job_into_batch(
        translation_worker_env,
        job_id=bootstrap_result.job_id,
        unit_id=bootstrap_result.unit_id,
        job_type="translate_article",
    )
    worker = TranslationWorkerService(
        pool=translation_worker_env,
        batch_translator=DevFakeTranslationBatchExecutor(),
        layer_publisher=_PostExecutionFailTranslationPublisher(
            RuntimeError("post-provider batch generic failure")
        ),
    )
    claim = await worker.claim_translation_batch_job_for_record(
        record_id=article.record_id,
        base_id=article.base_id,
        expected_generation=1,
        lease_owner="translation-batch-worker-generic-failure",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    result = await worker.process_claimed_translation_batch_job(claim=claim)

    assert result is not None
    assert result.status == "paused"

    usage_rows = await _fetch_translation_usage_rows(
        translation_worker_env, claim.job_id
    )
    async with translation_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            claim.run_id,
        )

    assert job_row is not None and job_row["status"] == "paused"
    assert run_row is not None and run_row["status"] == "running"
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_translation"
    assert usage_row["model_route"] == "reader_layer_translation"
    assert usage_row["model_profile_id"] == "reader_smoke_fake_translation_batch"
    assert usage_row["model_provider"] == "fake"
    assert usage_row["model_name"] == "reader-smoke-fake-translation-batch"
    assert usage_row["reader_run_id"] == claim.run_id
    assert usage_row["reader_job_id"] == claim.job_id
    assert usage_row["input_tokens"] == 1
    assert usage_row["output_tokens"] == 1
    assert usage_row["total_tokens"] == 2
    assert usage_row["error_code"] is None
