from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic_ai._warnings import PydanticAIDeprecationWarning
from pydantic_ai.agent import AgentRunResult

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage
from app.schemas.reader_orchestration import (
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
)
from app.services.reader_orchestration import translation_worker as translation_worker_module
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_OPERATION_FINGERPRINT,
    TranslationJobBootstrapService,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.layer_publisher import PublishedTranslationLayer
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.translation_worker import (
    PydanticAITranslationExecutor,
    TranslationAnchorSegmentTarget,
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
    _build_translation_prompt,
    _validate_translation_strategy_metadata,
    hydrate_translation_layer_output,
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
    monkeypatch: pytest.MonkeyPatch,
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
    async def _noop_record_usage_event(**kwargs) -> None:
        return None

    monkeypatch.setattr(worker, "_record_usage_event", _noop_record_usage_event)

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
    }


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
    monkeypatch: pytest.MonkeyPatch,
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
    success_worker = TranslationWorkerService(
        pool=translation_worker_env,
        translator=_StaticTranslator(_translation_generation_output("恢复后的译文")),
        layer_publisher=publisher,
    )
    async def _noop_record_usage_event(**kwargs) -> None:
        return None

    monkeypatch.setattr(success_worker, "_record_usage_event", _noop_record_usage_event)

    success_result = await success_worker.process_next_translation_job(
        lease_owner="worker-retry-then-success",
        lease_duration=timedelta(seconds=30),
    )

    assert success_result is not None
    assert success_result.status == "succeeded"
    assert len(publisher.calls) == 1
    published_output = publisher.calls[0]["output"]
    assert isinstance(published_output, TranslationLayerOutput)
    assert published_output.groups[0].translated_text == "恢复后的译文"

    async with translation_worker_env.acquire() as conn:
        failed_usage_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM ai_usage_events
            WHERE reader_job_id = $1
              AND status = 'failed'
            """,
            success_result.claim.job_id,
        )

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


def test_build_translation_prompt_includes_target_segments_and_group_native_output_contract() -> None:
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


def test_hydrate_translation_layer_output_uses_separator_inclusive_hash_for_paragraph_break() -> None:
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
# T6: _load_job_context integration — reads T5 bootstrap strategy metadata
# ---------------------------------------------------------------------------#


async def test_load_job_context_reads_t5_bootstrap_strategy_metadata(
    translation_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must read strategy metadata written by T5 bootstrap
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
        ArticleReadyPersistenceService,
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
