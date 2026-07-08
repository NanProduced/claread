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
from app.llm.agent_runner import extract_run_usage
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.schemas.reader_orchestration import (
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
)
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
from app.services.reader_orchestration.layer_publisher import PublishedTranslationLayer
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
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
    assert grouping_block_idx < grouping_close_idx < target_segments_block_idx < target_segments_close_idx

    # The grouping guidance must include a registry_note subsection that
    # reframes target_segments as anchor handles.
    registry_idx = prompt.index("<target_segments_registry_note>")
    registry_close_idx = prompt.index("</target_segments_registry_note>")
    assert grouping_block_idx < registry_idx < registry_close_idx < grouping_close_idx

    # The note explicitly forbids one-row-per-listed-id behavior.
    registry_note = prompt[registry_idx:registry_close_idx]
    assert "row-by-row output template" in registry_note
    assert "one row per listed id" in registry_note


def test_build_translation_prompt_grouping_guidance_sits_between_strategy_and_output_contract() -> None:
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
                f"不要输出" in instructions
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
_S15_SOURCE = "Without tips, staff may not earn enough money to live on."
_S17_SOURCE = "In many host cities, restaurants have automatically added tips to their bills."
_S15_ZH = "如果没有小费，员工可能挣不到足够的钱维持生活。"
_S17_ZH = "在许多主办城市，餐厅已经自动将小费添加到账单中。"


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
            ("s15", _S15_SOURCE, 15),
            ("s17", _S17_SOURCE, 17),
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
_S10_SOURCE = "Workers at restaurants in the US can earn as little as $2.13 per hour."
_S11_SOURCE = "They rely on diners to tip for their service."
_S12_SOURCE = "Without tips, staff may not earn enough money to live on."


def _build_contiguous_unit_source_text() -> str:
    return " ".join([_S10_SOURCE, _S11_SOURCE, _S12_SOURCE])


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
        ("s10", _S10_SOURCE, 10),
        ("s11", _S11_SOURCE, 11),
        ("s12", _S12_SOURCE, 12),
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
    assert groups[0].source_text_hash == compute_text_range_hash(_S15_SOURCE)
    assert groups[1].source_text_hash == compute_text_range_hash(_S17_SOURCE)
    assert groups[0].source_text == _S15_SOURCE
    assert groups[1].source_text == _S17_SOURCE


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
            translated_text=_S15_ZH,
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
                    ("u2_g15_15", _S17_ZH),  # wrong text for this anchor
                    ("u2_g17_17", _S15_ZH),  # wrong text for this anchor
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
    assert by_group["u2_g15_15"].source_text_hash == compute_text_range_hash(_S15_SOURCE)
    assert by_group["u2_g15_15"].translated_text == _S17_ZH
    assert by_group["u2_g17_17"].anchor_segment_ids == ["s17"]
    assert by_group["u2_g17_17"].source_text_hash == compute_text_range_hash(_S17_SOURCE)
    assert by_group["u2_g17_17"].translated_text == _S15_ZH


def test_hydrate_batch_output_correct_alignment_preserves_translations() -> None:
    """When the LLM returns the correct translation per group_id, the
    hydrated layer has the right anchor + translated_text pairing."""
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[
            _batch_unit_output(
                "u2",
                [
                    ("u2_g15_15", _S15_ZH),
                    ("u2_g17_17", _S17_ZH),
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
    assert by_group["u2_g15_15"].translated_text == _S15_ZH
    assert by_group["u2_g17_17"].anchor_segment_ids == ["s17"]
    assert by_group["u2_g17_17"].translated_text == _S17_ZH


def test_hydrate_batch_output_fail_closed_on_missing_group() -> None:
    context = _build_batch_context_with_segments()
    generation = TranslationBatchGenerationOutput(
        units=[_batch_unit_output("u2", [("u2_g15_15", _S15_ZH)])]  # missing g17
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
                    ("u2_g15_15", _S15_ZH),
                    ("u2_g17_17", _S17_ZH),
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
                        group_id="u2_g15_15", translated_text=_S15_ZH
                    ),
                    TranslationBatchGroupOutput(
                        group_id="u2_g15_15", translated_text="duplicate"
                    ),
                    TranslationBatchGroupOutput(
                        group_id="u2_g17_17", translated_text=_S17_ZH
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
        units=[_batch_unit_output("u99", [("u2_g15_15", _S15_ZH)])]
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
                [("u2_g15_15", "   "), ("u2_g17_17", _S17_ZH)],
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
                [("u2_g17_17", _S17_ZH), ("u2_g15_15", _S15_ZH)],
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


def test_build_translation_batch_prompt_emits_predefined_groups_and_forbids_llm_anchor_choice() -> None:
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
    assert _S15_SOURCE in prompt
    assert _S17_SOURCE in prompt
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
    assert _S10_SOURCE in prompt
    assert _S11_SOURCE in prompt
    assert _S12_SOURCE in prompt
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
            ("s10", _S10_SOURCE, 10),
            ("s11", _S11_SOURCE, 11),
            ("s12", _S12_SOURCE, 12),
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
