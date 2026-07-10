from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    GrammarNoteLayerOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    SentenceAnalysisLayerOutput,
)
from app.services.reader_orchestration import grammar_worker as grammar_worker_module
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.grammar_worker import (
    FakeGrammarBundleExecutor,
    GrammarAnchorSegmentContext,
    GrammarBatchExecutionResult,
    GrammarBatchJobContext,
    GrammarBatchJobProcessResult,
    GrammarBatchUnitContext,
    GrammarBundleCandidateOutput,
    GrammarBundleWorkerService,
    GrammarExecutionError,
    GrammarExecutionResult,
    GrammarJobContext,
    PydanticAIGrammarBundleExecutor,
    _build_grammar_batch_prompt,
    _build_grammar_prompt,
    _validate_grammar_strategy_metadata,
)
from app.services.reader_orchestration.job_bootstrap import (
    GRAMMAR_OPERATION_FINGERPRINT,
    EnhancementJobBootstrapService,
    GrammarJobBootstrapService,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
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
GRAMMAR_ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline."
)


class _StaticGrammarExecutor:
    def __init__(self, output_builder) -> None:
        self.output_builder = output_builder
        self.calls: list[GrammarJobContext] = []
        self.usage_data = {
            "aggregate": {
                "input_tokens": 12,
                "output_tokens": 18,
                "total_tokens": 30,
            }
        }

    async def generate(self, context: GrammarJobContext) -> GrammarExecutionResult:
        self.calls.append(context)
        return GrammarExecutionResult(
            output=self.output_builder(context),
            usage_data=self.usage_data,
            prompt_version="test-grammar-worker",
            model_profile="fake-grammar-profile",
            model_provider="fake-provider",
            model_name="fake-grammar-model",
        )


class _FailingGrammarExecutor:
    def __init__(self, error: GrammarExecutionError) -> None:
        self.error = error

    async def generate(self, context: GrammarJobContext) -> GrammarExecutionResult:
        raise self.error

def test_real_executor_builds_agent_with_non_deprecated_retry_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    instructions = "stub grammar instructions"

    class _CapturingAgent:
        def __init__(self, *args, **kwargs) -> None:
            captured["kwargs"] = kwargs

    monkeypatch.setattr(grammar_worker_module, "Agent", _CapturingAgent)
    monkeypatch.setattr(
        grammar_worker_module,
        "load_agent_instructions",
        lambda name: instructions,
    )

    executor = PydanticAIGrammarBundleExecutor(
        settings=Settings(reader_grammar_bundle_model_profile="reader_grammar_bundle")
    )
    executor._build_agent(model=object())

    agent_kwargs = captured["kwargs"]
    assert isinstance(agent_kwargs, dict)
    assert agent_kwargs["output_type"] is GrammarBundleCandidateOutput
    assert agent_kwargs["instructions"] == instructions
    assert agent_kwargs["name"] == "reader_layer_grammar_bundle_agent"
    assert agent_kwargs["retries"] == {"tools": 1, "output": 2}
    assert "output_retries" not in agent_kwargs
    assert "instrument" not in agent_kwargs


@pytest.fixture
async def grammar_worker_env() -> asyncpg.Pool:
    schema_name = f"test_reader_grammar_worker_{uuid4().hex}"
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


async def _submit_grammar_article(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
):
    return await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=GRAMMAR_ARTICLE_TEXT,
        title="Grammar Slice",
        language="en",
    )


async def _submit_multisegment_grammar_article(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
):
    return await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "Not only did the team revise the plan, but they also clarified the timeline. "
            "Everyone understood the tradeoff."
        ),
        title="Grammar Multi Segment Slice",
        language="en",
    )


def _build_anchor(
    context: GrammarJobContext,
    selected_text: str,
    *,
    segment_index: int = 0,
) -> ReaderTextRangeAnchor:
    segment = context.anchor_segments[segment_index]
    start_index = segment.text.index(selected_text)
    start_offset = segment.unit_start_utf16 + utf16_code_unit_length(segment.text[:start_index])
    end_offset = start_offset + utf16_code_unit_length(selected_text)
    return ReaderTextRangeAnchor(
        base_id=str(context.base_id),
        unit_id=context.unit_id,
        anchor_segment_id=segment.anchor_segment_id,
        sentence_id=segment.sentence_id,
        segment_type=segment.segment_type,  # type: ignore[arg-type]
        start_offset=start_offset,
        end_offset=end_offset,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


def _sample_grammar_bundle_output(context: GrammarJobContext) -> GrammarBundleOutput:
    sentence_text = context.anchor_segments[0].text
    return GrammarBundleOutput(
        grammar_notes=[
            GrammarNoteItem(
                spans=[
                    _build_anchor(context, "Not only"),
                    _build_anchor(context, "but they also"),
                ],
                grammar_point="paired focus construction",
                pattern="not only ... but also",
                note="前半句和后半句共同强调并列信息。",
            )
        ],
        sentence_analyses=[
            SentenceAnalysisItem(
                anchor=_build_anchor(context, sentence_text),
                label="fronted emphasis with inversion",
                analysis="前置的否定结构触发助动词提前，后半句补充并列结果。",
                chunks=[
                    SentenceAnalysisChunk(order=1, label="fronted cue", text="Not only"),
                    SentenceAnalysisChunk(
                        order=2,
                        label="inverted clause",
                        text="did the team revise the plan",
                    ),
                    SentenceAnalysisChunk(
                        order=3,
                        label="paired result",
                        text="but they also clarified the timeline",
                    ),
                ],
            )
        ],
    )


def _grammar_note_only_output(context: GrammarJobContext) -> GrammarBundleOutput:
    return GrammarBundleOutput(
        grammar_notes=[
            GrammarNoteItem(
                spans=[_build_anchor(context, "Not only")],
                grammar_point="fronted negative cue",
                pattern="not only",
                note="前置否定触发更强的强调语气。",
            )
        ]
    )


def _mixed_segment_grammar_note_output(context: GrammarJobContext) -> GrammarBundleOutput:
    assert len(context.anchor_segments) >= 2
    return GrammarBundleOutput(
        grammar_notes=[
            GrammarNoteItem(
                spans=[
                    _build_anchor(context, "Not only", segment_index=0),
                    _build_anchor(context, "Everyone", segment_index=1),
                ],
                grammar_point="paired grounding that must stay complete",
                pattern="not only ... / follow-up explanation",
                note="任一 grounding span 失效时整条语法注释都应放弃。",
            )
        ]
    )


@pytest.mark.anyio
async def test_bootstrap_creates_grammar_run_and_job_with_expected_fingerprint(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)

    result = await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert result.base_id == article.base_id
    assert result.expected_generation == 1
    assert _fingerprint_matches_base(
        result.operation_fingerprint, GRAMMAR_OPERATION_FINGERPRINT
    )
    assert result.operation_fingerprint != GRAMMAR_OPERATION_FINGERPRINT
    assert result.unit_id == article.snapshot.navigation.units[0].unit_id

    async with grammar_worker_env.acquire() as conn:
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
            SELECT base_id, job_type, target_type, target_key, status, expected_generation,
                   operation_fingerprint, max_attempts
            FROM reader_jobs
            WHERE id = $1
            """,
            result.job_id,
        )

    assert run_row is not None
    assert run_row["run_type"] == "grammar_bundle"
    assert run_row["status"] == "queued"
    assert run_row["record_generation"] == 1
    assert run_row["trigger_kind"] == "system"
    assert run_row["policy_version"] == "reader_grammar_bundle_bootstrap_v1"

    assert job_row is not None
    assert job_row["base_id"] == article.base_id
    assert job_row["job_type"] == "build_grammar_bundle"
    assert job_row["target_type"] == "unit"
    assert job_row["target_key"] == result.unit_id
    assert job_row["status"] == "queued"
    assert job_row["expected_generation"] == 1
    assert _fingerprint_matches_base(
        job_row["operation_fingerprint"], GRAMMAR_OPERATION_FINGERPRINT
    )
    assert job_row["operation_fingerprint"] != GRAMMAR_OPERATION_FINGERPRINT
    assert job_row["max_attempts"] == 3


@pytest.mark.anyio
async def test_bootstrap_does_not_create_duplicate_active_grammar_job(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)

    first = await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    second = await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert second.run_id == first.run_id
    assert second.job_id == first.job_id
    assert second.operation_fingerprint == first.operation_fingerprint

    async with grammar_worker_env.acquire() as conn:
        total_jobs = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
              AND operation_fingerprint = $2
            """,
            article.record_id,
            first.operation_fingerprint,
        )
    assert total_jobs == 1


@pytest.mark.anyio
async def test_worker_process_publishes_both_grammar_layers_and_records_single_usage(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    executor = _StaticGrammarExecutor(_sample_grammar_bundle_output)
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=executor,
    )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.published_bundle is not None
    assert len(executor.calls) == 1
    assert executor.calls[0].unit_id == article.snapshot.navigation.units[0].unit_id

    async with grammar_worker_env.acquire() as conn:
        layer_rows = await conn.fetch(
            """
            SELECT id, layer_type, target_scope, target_key, generation, status, output_json
            FROM enhancement_layers
            WHERE reading_record_id = $1
            ORDER BY layer_type ASC
            """,
            article.record_id,
        )
        event_rows = await conn.fetch(
            """
            SELECT e.sequence, e.event_type, l.layer_type
            FROM reader_events e
            LEFT JOIN enhancement_layers l
              ON l.id = e.source_layer_id
            WHERE e.reading_record_id = $1
            ORDER BY e.sequence ASC
            """,
            article.record_id,
        )
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )
        usage_rows = await conn.fetch(
            """
            SELECT status, capability_code, usage_scope, billing_mode, model_route,
                   model_profile_id, model_provider, model_name, reader_run_id,
                   reader_job_id, enhancement_layer_id, input_tokens, output_tokens,
                   total_tokens, operation_fingerprint, metadata_json
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at ASC
            """,
            result.claim.job_id,
        )

    assert [row["layer_type"] for row in layer_rows] == ["grammar_note", "sentence_analysis"]
    assert all(row["target_scope"] == "unit" for row in layer_rows)
    assert all(row["target_key"] == result.context.unit_id for row in layer_rows if result.context)
    assert all(row["generation"] == 1 for row in layer_rows)
    assert all(row["status"] == "published" for row in layer_rows)

    grammar_output = GrammarNoteLayerOutput.model_validate(layer_rows[0]["output_json"])
    sentence_output = SentenceAnalysisLayerOutput.model_validate(layer_rows[1]["output_json"])
    assert len(grammar_output.items) == 1
    assert len(sentence_output.items) == 1

    assert [(row["sequence"], row["event_type"], row["layer_type"]) for row in event_rows] == [
        (1, "article_ready", None),
        (2, "layer_published", "grammar_note"),
        (3, "layer_published", "sentence_analysis"),
    ]

    assert job_row is not None and job_row["status"] == "succeeded"
    assert run_row is not None and run_row["status"] == "completed"
    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_grammar_bundle"
    assert usage_row["usage_scope"] == "system_internal"
    assert usage_row["billing_mode"] == "internal_only"
    assert usage_row["model_route"] == "reader_layer_grammar_bundle"
    assert usage_row["model_profile_id"] == "fake-grammar-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-grammar-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["enhancement_layer_id"] is None
    assert usage_row["input_tokens"] == 12
    assert usage_row["output_tokens"] == 18
    assert usage_row["total_tokens"] == 30
    assert usage_row["operation_fingerprint"] == result.context.operation_fingerprint
    assert usage_row["metadata_json"]["published_layer_types"] == [
        "grammar_note",
        "sentence_analysis",
    ]
    assert usage_row["metadata_json"]["no_op"] is False


@pytest.mark.anyio
async def test_worker_process_single_grammar_layer_blocks_future_bootstrap_for_same_unit(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)
    await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_grammar_note_only_output),
    )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-single-layer",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.published_bundle is not None
    assert result.published_bundle.grammar_note_layer is not None
    assert result.published_bundle.sentence_analysis_layer is None

    async with grammar_worker_env.acquire() as conn:
        layer_rows = await conn.fetch(
            """
            SELECT layer_type
            FROM enhancement_layers
            WHERE reading_record_id = $1
            ORDER BY layer_type ASC
            """,
            article.record_id,
        )
        event_rows = await conn.fetch(
            """
            SELECT event_type
            FROM reader_events
            WHERE reading_record_id = $1
            ORDER BY sequence ASC
            """,
            article.record_id,
        )

    assert [row["layer_type"] for row in layer_rows] == ["grammar_note"]
    assert [row["event_type"] for row in event_rows] == ["article_ready", "layer_published"]

    with pytest.raises(ValueError, match="no unprocessed grammar reading unit is available"):
        await bootstrap.bootstrap_grammar_run(
            record_id=article.record_id,
            user_id=user_id,
        )


@pytest.mark.anyio
async def test_worker_explicit_fake_executor_succeeds_noop_without_layers_or_reader_events(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)
    await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=FakeGrammarBundleExecutor(),
    )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-noop",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.published_bundle is not None
    assert result.published_bundle.no_op is True

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, rationale_code, output_ref_json
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
        reader_event_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_events
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
        usage_rows = await conn.fetch(
            """
            SELECT status, enhancement_layer_id, metadata_json
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at ASC
            """,
            result.claim.job_id,
        )

    assert job_row is not None
    assert job_row["status"] == "succeeded"
    assert job_row["rationale_code"] == "grammar_bundle_no_op"
    assert job_row["output_ref_json"]["no_op"] is True
    assert job_row["output_ref_json"]["grammar_note_count"] == 0
    assert job_row["output_ref_json"]["sentence_analysis_count"] == 0
    assert run_row is not None and run_row["status"] == "completed"
    assert layer_count == 0
    assert reader_event_count == 1
    assert len(usage_rows) == 1
    assert usage_rows[0]["status"] == "succeeded"
    assert usage_rows[0]["enhancement_layer_id"] is None
    assert usage_rows[0]["metadata_json"]["no_op"] is True

    with pytest.raises(ValueError, match="no unprocessed grammar reading unit is available"):
        await bootstrap.bootstrap_grammar_run(
            record_id=article.record_id,
            user_id=user_id,
        )


@pytest.mark.anyio
async def test_worker_without_executor_fails_terminal_and_does_not_publish_grammar_layers(
    grammar_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    monkeypatch.setattr(
        grammar_worker_module,
        "get_settings",
        lambda: Settings(reader_grammar_bundle_model_profile=""),
    )
    worker = GrammarBundleWorkerService(pool=grammar_worker_env)

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-empty",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, rationale_code
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code
            FROM reader_runs
            WHERE id = $1
            """,
            result.claim.run_id,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "configuration"
    assert job_row["failure_code"] == "grammar_bundle_executor_unconfigured"
    assert job_row["rationale_code"] == "grammar_bundle_executor_unconfigured"
    assert run_row is not None
    assert run_row["status"] == "failed_terminal"
    assert run_row["failure_class"] == "configuration"
    assert run_row["failure_code"] == "grammar_bundle_executor_unconfigured"
    assert layer_count == 0


@pytest.mark.anyio
async def test_worker_retryable_failure_moves_job_to_retry_later_and_records_failed_usage(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_FailingGrammarExecutor(
            GrammarExecutionError(
                "temporary grammar timeout",
                retryable=True,
                failure_class="provider",
                failure_code="provider_timeout",
            )
        ),
    )

    started_at = datetime.now(UTC)
    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-retry",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=3),
    )

    assert result is not None
    assert result.status == "retry_later"

    async with grammar_worker_env.acquire() as conn:
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
        usage_rows = await conn.fetch(
            """
            SELECT status, capability_code, model_route, model_profile_id,
                   model_provider, model_name, enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at ASC
            """,
            result.claim.job_id,
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

    assert len(usage_rows) == 1
    usage_row = usage_rows[0]
    assert usage_row["status"] == "failed"
    assert usage_row["capability_code"] == "reader_grammar_bundle"
    assert usage_row["model_route"] == "reader_layer_grammar_bundle"
    assert usage_row["model_profile_id"] is None
    assert usage_row["model_provider"] is None
    assert usage_row["model_name"] is None
    assert usage_row["enhancement_layer_id"] is None

    async with grammar_worker_env.acquire() as conn:
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
    assert layer_count == 0


@pytest.mark.anyio
async def test_worker_terminal_failure_moves_job_to_failed_terminal_and_records_failed_usage(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_FailingGrammarExecutor(
            GrammarExecutionError(
                "grammar bundle rejected",
                retryable=False,
                failure_class="validation",
                failure_code="grammar_bundle_output_invalid",
            )
        ),
    )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-terminal",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, rationale_code
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
        usage_rows = await conn.fetch(
            """
            SELECT status, capability_code, error_code, enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at ASC
            """,
            result.claim.job_id,
        )

    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "validation"
    assert job_row["failure_code"] == "grammar_bundle_output_invalid"
    assert job_row["rationale_code"] == "grammar_bundle_output_invalid"

    assert run_row is not None
    assert run_row["status"] == "failed_terminal"
    assert run_row["failure_class"] == "validation"
    assert run_row["failure_code"] == "grammar_bundle_output_invalid"
    assert run_row["finished_at"] is not None

    assert len(usage_rows) == 1
    assert usage_rows[0]["status"] == "failed"
    assert usage_rows[0]["capability_code"] == "reader_grammar_bundle"
    assert usage_rows[0]["error_code"] == "grammar_bundle_output_invalid"
    assert usage_rows[0]["enhancement_layer_id"] is None


@pytest.mark.anyio
async def test_worker_claim_fence_supersedes_job_on_active_base_mismatch(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    boot_result = await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(pool=grammar_worker_env)

    async with grammar_worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            article.record_id,
        )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-stale-claim",
        lease_duration=timedelta(seconds=30),
    )

    assert result is None

    async with grammar_worker_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
              AND operation_fingerprint = $2
            """,
            article.record_id,
            boot_result.operation_fingerprint,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    assert job_status == "superseded"
    assert layer_count == 0


@pytest.mark.anyio
async def test_worker_publish_fence_supersedes_claimed_job_on_stale_generation(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_sample_grammar_bundle_output),
    )
    claim = await worker.claim_grammar_job(
        lease_owner="grammar-worker-stale-publish",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    async with grammar_worker_env.acquire() as conn:
        async with conn.transaction():
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

    with pytest.raises(FenceViolationError, match="stale_generation"):
        await worker.process_claimed_grammar_job(claim=claim)

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT status, rationale_code
            FROM reader_jobs
            WHERE id = $1
            """,
            claim.job_id,
        )
        run_row = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code
            FROM reader_runs
            WHERE id = $1
            """,
            claim.run_id,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    assert job_row is not None
    assert job_row["status"] == "superseded"
    assert job_row["rationale_code"] == "publish_fence_failed"

    assert run_row is not None
    assert run_row["status"] == "superseded"
    assert run_row["failure_class"] == "publish_guard"
    assert run_row["failure_code"] == "publish_fence_failed"
    assert layer_count == 0


@pytest.mark.anyio
async def test_fallback_window_outputs_are_sanitized_to_noop_success(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with grammar_worker_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE anchor_segments
            SET segment_type = 'fallback_window',
                boundary_quality = 'low'
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_sample_grammar_bundle_output),
    )
    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-fallback-window",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.published_bundle is not None
    assert result.published_bundle.no_op is True

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT output_ref_json
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    assert job_row is not None
    assert job_row["output_ref_json"]["no_op"] is True
    assert job_row["output_ref_json"]["diagnostics"]["skipped_item_count"] >= 1
    assert layer_count == 0


@pytest.mark.anyio
async def test_mixed_normal_and_fallback_spans_skip_entire_grammar_note(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_multisegment_grammar_article(grammar_worker_env, user_id=user_id)
    assert len(article.snapshot.navigation.units) == 1
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with grammar_worker_env.acquire() as conn:
        anchor_segments = await conn.fetch(
            """
            SELECT anchor_segment_id
            FROM anchor_segments
            WHERE reading_record_id = $1
            ORDER BY order_index ASC
            """,
            article.record_id,
        )
        assert len(anchor_segments) >= 2
        await conn.execute(
            """
            UPDATE anchor_segments
            SET segment_type = 'fallback_window',
                boundary_quality = 'low'
            WHERE reading_record_id = $1
              AND anchor_segment_id = $2
            """,
            article.record_id,
            anchor_segments[1]["anchor_segment_id"],
        )

    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_mixed_segment_grammar_note_output),
    )
    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-mixed-fallback",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.published_bundle is not None
    assert result.published_bundle.no_op is True

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT output_ref_json
            FROM reader_jobs
            WHERE id = $1
            """,
            result.claim.job_id,
        )
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
        reader_event_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_events
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    assert job_row is not None
    assert job_row["output_ref_json"]["no_op"] is True
    assert job_row["output_ref_json"]["diagnostics"]["grammar_note_count"] == 0
    assert job_row["output_ref_json"]["diagnostics"]["skipped_item_count"] == 1
    assert (
        job_row["output_ref_json"]["diagnostics"]["skipped_items"][0]["reason_code"]
        == "boundary_low_fallback_window"
    )
    assert layer_count == 0
    assert reader_event_count == 1


@pytest.mark.anyio
async def test_snapshot_reload_exposes_grammar_layer_metadata_and_value_projections_without_writes(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_sample_grammar_bundle_output),
    )
    result = await worker.process_next_grammar_job(
        lease_owner="grammar-worker-snapshot",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"

    async with grammar_worker_env.acquire() as conn:
        before_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    snapshot = await ArticleReadyPersistenceService(pool=grammar_worker_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )

    async with grammar_worker_env.acquire() as conn:
        after_counts = {
            "reader_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "reader_job_events": await conn.fetchval(
                "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
                article.record_id,
            ),
            "enhancement_layers": await conn.fetchval(
                "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1",
                article.record_id,
            ),
        }

    assert before_counts == after_counts
    assert [layer.layer_type for layer in snapshot.enhancement_layers] == [
        "grammar_note",
        "sentence_analysis",
    ]
    grammar_marked_leaves = [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_grammar_note_marks")
    ]
    sentence_analysis_nodes = [
        child
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_sentence_analysis"
    ]
    assert grammar_marked_leaves
    assert any(
        mark["item_type"] == "grammar_note"
        for leaf in grammar_marked_leaves
        for mark in leaf["reader_grammar_note_marks"]  # type: ignore[index]
    )
    assert len(sentence_analysis_nodes) == 1
    assert sentence_analysis_nodes[0]["owner"] == "system_ai"
    assert sentence_analysis_nodes[0]["label"] == "fronted emphasis with inversion"
    assert "render_scene_json" not in json.dumps(snapshot.value, ensure_ascii=False)


def test_grammar_modules_do_not_reference_render_scene_json() -> None:
    job_bootstrap_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "job_bootstrap.py"
    )
    worker_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "grammar_worker.py"
    )
    layer_publisher_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "layer_publisher.py"
    )

    assert "render_scene_json" not in job_bootstrap_path.read_text(encoding="utf-8")
    assert "render_scene_json" not in worker_path.read_text(encoding="utf-8")
    assert "render_scene_json" not in layer_publisher_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------#
# T8: variant-first grammar_bundle strategy prompt + metadata validation
# ---------------------------------------------------------------------------#


def _build_context_for_variant(
    *,
    reading_goal: str,
    reading_variant: str,
    source_text: str = (
        "Not only did the team revise the plan, "
        "but they also clarified the timeline."
    ),
) -> GrammarJobContext:
    """Build a GrammarJobContext with strategy metadata for a given variant."""
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    layer = strategy.layers["grammar_bundle"]
    return GrammarJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="grammar_bundle_unit_v1",
        source_language="en",
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(
            GrammarAnchorSegmentContext(
                anchor_segment_id="s1",
                sentence_id="s1",
                segment_type="sentence",
                unit_start_utf16=0,
                unit_end_utf16=utf16_code_unit_length(source_text),
                text_hash=compute_text_range_hash(source_text),
                text=source_text,
            ),
        ),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        grammar_prompt_lines=layer.prompt_lines,
    )


def _build_batch_context_for_variant(
    *,
    reading_goal: str,
    reading_variant: str,
    article_route: str = "structured_batch",
    document_features: dict[str, object] | None = None,
    source_text: str = (
        "Not only did the team revise the plan, "
        "but they also clarified the timeline."
    ),
) -> GrammarBatchJobContext:
    """Build a GrammarBatchJobContext for compact-prompt unit tests."""
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    layer = strategy.layers["grammar_bundle"]
    unit = GrammarBatchUnitContext(
        unit_id="u1",
        order_index=1,
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(
            GrammarAnchorSegmentContext(
                anchor_segment_id="s1",
                sentence_id="s1",
                segment_type="sentence",
                unit_start_utf16=0,
                unit_end_utf16=utf16_code_unit_length(source_text),
                text_hash=compute_text_range_hash(source_text),
                text=source_text,
            ),
        ),
    )
    return GrammarBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        expected_generation=1,
        operation_fingerprint="grammar_bundle_article_structured_v1:test",
        source_language="en",
        units=(unit,),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        grammar_prompt_lines=layer.prompt_lines,
        article_route=article_route,
        document_features=document_features,
    )


def test_build_grammar_prompt_contains_concrete_policy_lines() -> None:
    """The prompt must include the concrete grammar_bundle policy lines from
    reader_variants.yaml, not just a goal/variant label."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_grammar_prompt(context)

    assert "<reader_strategy>" in prompt
    assert "</reader_strategy>" in prompt
    assert "<policy_lines>" in prompt
    assert "</policy_lines>" in prompt
    assert "reading_goal: daily_reading" in prompt
    assert "reading_variant: intermediate_reading" in prompt
    assert "strategy_hash:" in prompt
    assert "layer_policy_hash:" in prompt

    # Every concrete policy line for intermediate_reading grammar_bundle
    # layer must appear in the prompt.
    for line in context.grammar_prompt_lines:
        assert line in prompt


def test_build_grammar_prompt_differs_between_daily_intermediate_and_exam_cet() -> None:
    """daily_reading/intermediate_reading and exam/cet must produce
    different strategy sections in the grammar prompt."""
    daily_context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    exam_context = _build_context_for_variant(
        reading_goal="exam",
        reading_variant="cet",
    )

    daily_prompt = _build_grammar_prompt(daily_context)
    exam_prompt = _build_grammar_prompt(exam_context)

    assert daily_prompt != exam_prompt

    assert "reading_goal: daily_reading" in daily_prompt
    assert "reading_variant: intermediate_reading" in daily_prompt
    for line in daily_context.grammar_prompt_lines:
        assert line in daily_prompt

    assert "reading_goal: exam" in exam_prompt
    assert "reading_variant: cet" in exam_prompt
    for line in exam_context.grammar_prompt_lines:
        assert line in exam_prompt

    # The two variants' grammar_bundle policy lines must actually differ
    # (guards against accidentally identical policy text).
    assert (
        daily_context.grammar_prompt_lines
        != exam_context.grammar_prompt_lines
    )


def test_build_grammar_prompt_strategy_section_order() -> None:
    """The strategy section must sit before the 'Return only...' directive
    so it does not clobber the source_text block."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_grammar_prompt(context)

    unit_id_idx = prompt.index(f"unit_id: {context.unit_id}")
    strategy_idx = prompt.index("<reader_strategy>")
    return_idx = prompt.index("Return only the structured candidate output.")
    source_idx = prompt.index("<source_text>")

    assert unit_id_idx < strategy_idx < return_idx < source_idx


def test_build_grammar_batch_prompt_includes_route_and_compact_document_features() -> None:
    """Compact grammar batch prompts must expose route identity and only the
    small document_features subset intended for prompt-time adaptation."""
    context = _build_batch_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        article_route="structured_batch",
        document_features={
            "estimated_word_count": 1450,
            "estimated_token_count": 1980,
            "unit_count": 5,
            "paragraph_count": 6,
            "heading_count": 2,
            "structural_noise_ratio": 0.18,
            "extractor_version": "document_feature_v1",
            "ignored_signal": "should_not_be_in_prompt",
        },
    )

    prompt = _build_grammar_batch_prompt(context)

    assert "article_route: structured_batch" in prompt
    assert "<document_features>" in prompt
    assert "estimated_word_count: 1450" in prompt
    assert "estimated_token_count: 1980" in prompt
    assert "unit_count: 5" in prompt
    assert "paragraph_count: 6" in prompt
    assert "heading_count: 2" in prompt
    assert "structural_noise_ratio: 0.18" in prompt
    assert "extractor_version" not in prompt
    assert "ignored_signal" not in prompt


def test_legacy_agent_instructions_contain_markdown_contract() -> None:
    """The legacy path agent instructions (reader_layer_grammar_bundle.yaml)
    must declare the Markdown output contract: note/analysis are Simplified
    Chinese Markdown, allow bold/inline-code/short bullets, forbid raw HTML
    and headings (hard forbid, no "除非确有需要"). Must declare unified
    language requirements (grammar_point can be Chinese/mixed, pattern/
    dedup_hint stay English).
    """
    from app.services.analysis.prompting.prompt_loader import (
        load_agent_instructions,
    )

    instructions = load_agent_instructions(
        grammar_worker_module.GRAMMAR_PROMPT_AGENT_NAME
    )

    assert "Markdown" in instructions
    assert "**加粗**" in instructions
    assert "`inline code`" in instructions
    assert "raw HTML" in instructions
    # The instructions must explicitly forbid HTML tags (mentioning them in
    # backticks as forbidden examples is the correct form).
    assert "禁止的 Markdown" in instructions or "Forbidden" in instructions
    # Heading forbid must be hard — no soft "除非确有需要" escape
    assert "除非确有需要" not in instructions
    # Language requirements: grammar_point can be Chinese or mixed
    assert "中英混合" in instructions
    # Frontend deserialization (not backend)
    assert "前端" in instructions


def test_legacy_field_descriptions_forbid_raw_html() -> None:
    """Legacy candidate Field descriptions for note/analysis must declare the
    Markdown contract and forbid raw HTML, matching the window path. Must say
    "前端" (not "后端") for deserialization, and hard-forbid headings.
    """
    from app.services.reader_orchestration.grammar_worker import (
        GrammarNoteCandidateItem,
        SentenceAnalysisCandidateItem,
    )

    note_desc = GrammarNoteCandidateItem.model_fields["note"].description or ""
    analysis_desc = (
        SentenceAnalysisCandidateItem.model_fields["analysis"].description or ""
    )

    assert "Markdown" in note_desc
    assert "raw HTML" in note_desc
    assert "Markdown" in analysis_desc
    assert "raw HTML" in analysis_desc
    # Deserialization is done by the frontend, not the backend
    assert "前端" in note_desc
    assert "前端" in analysis_desc
    assert "后端" not in note_desc
    assert "后端" not in analysis_desc
    # Heading forbid must be hard
    assert "除非确有需要" not in note_desc
    assert "除非确有需要" not in analysis_desc


def test_legacy_prompt_injects_gaokao_policy_without_stale_field_names() -> None:
    """Gaokao grammar_bundle policy lines must enter the legacy prompt, and
    must not carry stale field names (note_zh / analysis_zh / teach).
    """
    context = _build_context_for_variant(
        reading_goal="exam",
        reading_variant="gaokao",
    )
    prompt = _build_grammar_prompt(context)

    assert "reading_goal: exam" in prompt
    assert "reading_variant: gaokao" in prompt
    assert "高考" in prompt
    assert "显性教学" in prompt
    # Stale field names from old grammar.yaml must NOT appear
    assert "note_zh" not in prompt
    assert "analysis_zh" not in prompt


# ---------------------------------------------------------------------------#
# T8: _validate_grammar_strategy_metadata fail-closed unit tests
# ---------------------------------------------------------------------------#


def test_validate_grammar_strategy_metadata_rejects_non_mapping_input() -> None:
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(None)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


def test_validate_grammar_strategy_metadata_rejects_missing_keys() -> None:
    """A legacy bare-fingerprint job whose input_json lacks strategy
    metadata must fail closed, not fall back to a default strategy."""
    incomplete = {
        "unit_id": "u1",
        "base_language": "en",
        # No reading_goal / reading_variant / strategy_version / strategy_hash
        # / layer_policy_hash.
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(incomplete)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert "reading_goal" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_grammar_strategy_metadata_rejects_empty_string_values() -> None:
    """Empty string values are treated as missing."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["grammar_bundle"]
    payload = {
        "reading_goal": "",
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_metadata_missing"


def test_validate_grammar_strategy_metadata_rejects_strategy_hash_mismatch() -> None:
    """strategy_hash mismatch must fail closed with a dedicated code."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["grammar_bundle"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_hash_mismatch"
    assert "strategy_hash" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_grammar_strategy_metadata_rejects_layer_policy_hash_mismatch() -> None:
    """layer_policy_hash mismatch must fail closed with a dedicated code."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        # Use a different layer's policy_hash to trigger mismatch.
        "layer_policy_hash": strategy.layers["translation"].policy_hash,
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(payload)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


def test_validate_grammar_strategy_metadata_rejects_strategy_version_mismatch() -> None:
    """strategy_version mismatch must fail closed."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["grammar_bundle"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": "stale_version",
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_version_mismatch"


def test_validate_grammar_strategy_metadata_rejects_illegal_goal_variant_pair() -> None:
    """An illegal goal/variant pair (e.g. academic) must fail closed via
    the resolver, not silently fall back."""
    payload = {
        "reading_goal": "academic",
        "reading_variant": "academic_general",
        "strategy_version": "reader_variant_policy_v1",
        "strategy_hash": "irrelevant",
        "layer_policy_hash": "irrelevant",
    }
    with pytest.raises(GrammarExecutionError) as exc_info:
        _validate_grammar_strategy_metadata(payload)
    assert exc_info.value.failure_class == "strategy_resolution"
    assert exc_info.value.failure_code == "strategy_resolver_error"


def test_validate_grammar_strategy_metadata_returns_resolved_prompt_lines_on_success() -> None:
    """On success, the helper returns the resolver's concrete prompt_lines
    so the prompt builder can inject them."""
    strategy = resolve_reader_variant_strategy("exam", "cet")
    layer = strategy.layers["grammar_bundle"]
    payload = {
        "reading_goal": "exam",
        "reading_variant": "cet",
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    result = _validate_grammar_strategy_metadata(payload)
    assert result.reading_goal == "exam"
    assert result.reading_variant == "cet"
    assert result.strategy_hash == strategy.strategy_hash
    assert result.layer_policy_hash == layer.policy_hash
    assert result.grammar_prompt_lines == layer.prompt_lines
    assert len(result.grammar_prompt_lines) >= 1


# ---------------------------------------------------------------------------#
# T8: _load_job_context integration — reads T5/T8 bootstrap strategy metadata
# ---------------------------------------------------------------------------#


@pytest.mark.anyio
async def test_load_job_context_reads_t5_bootstrap_strategy_metadata(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must read strategy metadata written by T5/T8 bootstrap
    and resolve the concrete grammar_bundle policy lines from the resolver."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)
    boot_result = await bootstrap.bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    assert context.reading_goal == "daily_reading"
    assert context.reading_variant == "intermediate_reading"

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    assert context.strategy_version == strategy.strategy_version
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["grammar_bundle"].policy_hash

    assert context.grammar_prompt_lines == strategy.layers["grammar_bundle"].prompt_lines
    assert len(context.grammar_prompt_lines) >= 1

    prompt = _build_grammar_prompt(context)
    for line in context.grammar_prompt_lines:
        assert line in prompt


@pytest.mark.anyio
async def test_load_job_context_reads_exam_cet_strategy_metadata(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must also work for exam/cet variant."""
    user_id = await insert_user(grammar_worker_env)
    service = ArticleReadyPersistenceService(pool=grammar_worker_env)
    submit_result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=(
                "Not only did the team revise the plan, "
                "but they also clarified the timeline."
            ),
            title="Exam CET Grammar Slice",
            language="en",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="cet",  # type: ignore[arg-type]
        )
    )

    bootstrap = GrammarJobBootstrapService(pool=grammar_worker_env)
    boot_result = await bootstrap.bootstrap_grammar_run(
        record_id=submit_result.record_id,
        user_id=user_id,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    assert context.reading_goal == "exam"
    assert context.reading_variant == "cet"
    strategy = resolve_reader_variant_strategy("exam", "cet")
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["grammar_bundle"].policy_hash
    assert context.grammar_prompt_lines == strategy.layers["grammar_bundle"].prompt_lines


async def _insert_legacy_grammar_job_without_strategy_metadata(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    user_id: UUID,
    unit_id: str,
    input_json: dict,
) -> UUID:
    """Insert a grammar job row with crafted input_json.

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
            VALUES ($1, $2, 'grammar_bundle', 'queued', 1,
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
                'build_grammar_bundle', 'unit', $5, 'queued',
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
            GRAMMAR_OPERATION_FINGERPRINT,
            f"{GRAMMAR_OPERATION_FINGERPRINT}:{unit_id}",
            "legacy-input-hash",
            jsonb_param(input_json),
        )
    assert isinstance(job_id, UUID)
    return job_id


@pytest.mark.anyio
async def test_load_job_context_fail_closed_on_missing_strategy_metadata(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """A legacy bare-fingerprint job without strategy metadata in input_json
    must fail closed when _load_job_context tries to load it."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        # No strategy metadata keys.
    }
    job_id = await _insert_legacy_grammar_job_without_strategy_metadata(
        grammar_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    with pytest.raises(GrammarExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_load_job_context_fail_closed_on_strategy_hash_mismatch(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json strategy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["grammar_bundle"]
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    job_id = await _insert_legacy_grammar_job_without_strategy_metadata(
        grammar_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    with pytest.raises(GrammarExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_hash_mismatch"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_load_job_context_fail_closed_on_layer_policy_hash_mismatch(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json layer_policy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        # Use a different layer's policy_hash to trigger mismatch.
        "layer_policy_hash": strategy.layers["translation"].policy_hash,
    }
    job_id = await _insert_legacy_grammar_job_without_strategy_metadata(
        grammar_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    with pytest.raises(GrammarExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_load_job_context_fail_closed_on_strategy_version_mismatch(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json strategy_version doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["grammar_bundle"]
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": "stale_version",
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    job_id = await _insert_legacy_grammar_job_without_strategy_metadata(
        grammar_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = GrammarBundleWorkerService(pool=grammar_worker_env)
    with pytest.raises(GrammarExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_version_mismatch"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_worker_fail_closed_on_missing_strategy_metadata_moves_job_to_failed_terminal(
    grammar_worker_env: asyncpg.Pool,
) -> None:
    """End-to-end: a legacy job without strategy metadata, when processed
    by the worker, must move to failed_terminal with the right failure code."""
    user_id = await insert_user(grammar_worker_env)
    article = await _submit_grammar_article(grammar_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
    }
    job_id = await _insert_legacy_grammar_job_without_strategy_metadata(
        grammar_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = GrammarBundleWorkerService(
        pool=grammar_worker_env,
        executor=_StaticGrammarExecutor(_sample_grammar_bundle_output),
    )

    # Manually claim the legacy bare-fingerprint job through the runtime so
    # this test can exercise process_claimed_grammar_job's fail-closed
    # metadata-validation path directly.
    from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

    runtime = ReaderJobRuntime(pool=grammar_worker_env)
    claim = await runtime.claim_next_job(
        lease_owner="legacy-test-worker",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id

    result = await worker.process_claimed_grammar_job(claim=claim)

    assert result.status == "failed_terminal"
    assert result.context is None  # context loading failed before assignment

    async with grammar_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, failure_class, failure_code, rationale_code "
            "FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "validation"
    assert job_row["failure_code"] == "strategy_metadata_missing"
    # GrammarExecutionError defaults rationale_code to failure_code when
    # not explicitly set; the worker's GrammarExecutionError branch
    # propagates exc.rationale_code to the transition call.
    assert job_row["rationale_code"] == "strategy_metadata_missing"


# ---------------------------------------------------------------------------#
# T4.1c: compact grammar batch path — publish contract tests
# ---------------------------------------------------------------------------#
#
# These tests verify that the compact grammar batch worker (SHORT_BATCH /
# STRUCTURED_BATCH route) publishes per-unit grammar_note / sentence_analysis
# layers from a single batch LLM call, without per-unit fan-out.
#
# The fixture loads migrations 0015 + 0017 so that
# ``EnhancementJobBootstrapService.bootstrap_missing_jobs`` can route
# short articles to the grammar batch path.

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")

_T41C_BATCH_ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline.\n\n"
    "The committee approved the revised schedule after a thorough review."
)


class _StaticGrammarBatchExecutor:
    """T4.1c fake batch executor: produces valid grammar_note /
    sentence_analysis candidates for each unit in the batch context."""

    def __init__(self) -> None:
        self.calls: list[GrammarBatchJobContext] = []

    async def generate_batch(
        self,
        context: GrammarBatchJobContext,
    ) -> GrammarBatchExecutionResult:
        self.calls.append(context)
        outputs: list[tuple[str, GrammarBundleOutput]] = []
        for unit in context.units:
            if not unit.anchor_segments:
                outputs.append((unit.unit_id, GrammarBundleOutput()))
                continue
            anchor_segment = unit.anchor_segments[0]
            word_match = __import__("re").search(r"\b\w+\b", anchor_segment.text)
            assert word_match is not None
            word = word_match.group(0)
            word_start = anchor_segment.unit_start_utf16 + utf16_code_unit_length(
                anchor_segment.text[: word_match.start()]
            )
            word_anchor = ReaderTextRangeAnchor(
                base_id=str(context.base_id),
                unit_id=unit.unit_id,
                anchor_segment_id=anchor_segment.anchor_segment_id,
                sentence_id=anchor_segment.sentence_id,
                segment_type=anchor_segment.segment_type,
                start_offset=word_start,
                end_offset=word_start + utf16_code_unit_length(word),
                selected_text=word,
                text_hash=compute_text_range_hash(word),
            )
            sentence_anchor = ReaderTextRangeAnchor(
                base_id=str(context.base_id),
                unit_id=unit.unit_id,
                anchor_segment_id=anchor_segment.anchor_segment_id,
                sentence_id=anchor_segment.sentence_id,
                segment_type=anchor_segment.segment_type,
                start_offset=anchor_segment.unit_start_utf16,
                end_offset=anchor_segment.unit_end_utf16,
                selected_text=anchor_segment.text,
                text_hash=compute_text_range_hash(anchor_segment.text),
            )
            outputs.append(
                (
                    unit.unit_id,
                    GrammarBundleOutput(
                        grammar_notes=[
                            GrammarNoteItem(
                                spans=[word_anchor],
                                grammar_point="core verb",
                                pattern="SVO",
                                note="Batch grammar note for T4.1c.",
                            )
                        ],
                        sentence_analyses=[
                            SentenceAnalysisItem(
                                anchor=sentence_anchor,
                                label="main clause",
                                analysis="Simple clause for batch grammar test.",
                                chunks=[
                                    SentenceAnalysisChunk(
                                        order=1,
                                        label="clause",
                                        text=anchor_segment.text,
                                    )
                                ],
                            )
                        ],
                    ),
                )
            )
        return GrammarBatchExecutionResult(
            outputs=outputs,
            usage_data={"aggregate": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50}},
            prompt_version="test-grammar-batch",
            model_profile="fake-grammar-batch-profile",
            model_provider="fake-provider",
            model_name="fake-grammar-batch-model",
        )


@pytest.fixture
async def grammar_batch_env() -> asyncpg.Pool:
    schema_name = f"test_reader_grammar_batch_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_MIGRATION_0015_SQL)
        await admin_conn.execute(_MIGRATION_0017_SQL)
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


@pytest.mark.anyio
async def test_t41c_batch_worker_publishes_grammar_and_sentence_layers(
    grammar_batch_env: asyncpg.Pool,
) -> None:
    """T4.1c publish contract: the compact grammar batch worker publishes
    per-unit ``grammar_note`` and ``sentence_analysis`` layers from a
    single batch LLM call. No per-unit ``build_grammar_bundle`` / ``unit``
    jobs are created."""
    user_id = await insert_user(grammar_batch_env)
    submit_service = ArticleReadyPersistenceService(pool=grammar_batch_env)
    submit_result = await submit_service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=_T41C_BATCH_ARTICLE_TEXT,
            title="T4.1c Grammar Batch",
            language="en",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )
    record_id = submit_result.record_id
    base_id = submit_result.base_id

    # Bootstrap creates 1 grammar batch job (SHORT_BATCH route)
    service = EnhancementJobBootstrapService(pool=grammar_batch_env)
    await service.bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )

    # Verify batch job was created (build_grammar_bundle / unit_range)
    async with grammar_batch_env.acquire() as conn:
        batch_job_row = await conn.fetchrow(
            """
            SELECT id, run_id, job_type, target_type, operation_fingerprint
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit_range'
            """,
            record_id,
        )
    assert batch_job_row is not None, "SHORT_BATCH: expected a grammar batch job"
    assert batch_job_row["target_type"] == "unit_range"

    # Process the batch job with a fake executor
    executor = _StaticGrammarBatchExecutor()
    worker = GrammarBundleWorkerService(
        pool=grammar_batch_env,
        batch_executor=executor,
    )
    result = await worker.process_next_grammar_batch_job_for_record(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        lease_owner="grammar-batch-worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.published_batch is not None
    assert len(executor.calls) == 1
    batch_context = executor.calls[0]
    assert len(batch_context.units) >= 2, "expected at least 2 units in batch"

    # Verify per-unit grammar_note + sentence_analysis layers published
    async with grammar_batch_env.acquire() as conn:
        layer_rows = await conn.fetch(
            """
            SELECT layer_type, target_scope, target_key, status, output_json
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type IN ('grammar_note', 'sentence_analysis')
            ORDER BY layer_type ASC, target_key ASC
            """,
            record_id,
        )
        job_row = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1",
            result.claim.job_id,
        )
        run_row = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1",
            result.claim.run_id,
        )

    # Each unit should have both grammar_note and sentence_analysis
    unit_count = len(batch_context.units)
    grammar_notes = [r for r in layer_rows if r["layer_type"] == "grammar_note"]
    sentence_analyses = [r for r in layer_rows if r["layer_type"] == "sentence_analysis"]
    assert len(grammar_notes) == unit_count, (
        f"expected {unit_count} grammar_note layers, got {len(grammar_notes)}"
    )
    assert len(sentence_analyses) == unit_count, (
        f"expected {unit_count} sentence_analysis layers, got {len(sentence_analyses)}"
    )
    assert all(r["target_scope"] == "unit" for r in layer_rows)
    assert all(r["status"] == "published" for r in layer_rows)

    # Validate output_json schemas
    for row in grammar_notes:
        GrammarNoteLayerOutput.model_validate(row["output_json"])
    for row in sentence_analyses:
        SentenceAnalysisLayerOutput.model_validate(row["output_json"])

    # Job and run transitions
    assert job_row is not None and job_row["status"] == "succeeded"
    assert run_row is not None and run_row["status"] == "completed"


@pytest.mark.anyio
async def test_t41c_batch_worker_no_job_for_long_article(
    grammar_batch_env: asyncpg.Pool,
) -> None:
    """T4.1c: the batch worker returns ``None`` when no grammar batch job
    exists (GROUPED_WINDOWED route creates Z+ window jobs, not batch jobs).
    The per-unit fallback then finds no per-unit jobs either, so the
    pipeline runner's grammar dispatch returns ``no_job``."""
    user_id = await insert_user(grammar_batch_env)
    # Long text → GROUPED_WINDOWED → Z+ path, no grammar batch job
    long_text = "\n\n".join(
        " ".join(f"Word{i} placeholder sentence for long grammar batch test." for i in range(40))
        for _ in range(8)
    )
    submit_service = ArticleReadyPersistenceService(pool=grammar_batch_env)
    submit_result = await submit_service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=long_text,
            title="T4.1c Long Article",
            language="en",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )

    service = EnhancementJobBootstrapService(pool=grammar_batch_env)
    await service.bootstrap_missing_jobs(
        record_id=submit_result.record_id, user_id=user_id
    )

    worker = GrammarBundleWorkerService(
        pool=grammar_batch_env,
        batch_executor=_StaticGrammarBatchExecutor(),
    )
    result = await worker.process_next_grammar_batch_job_for_record(
        record_id=submit_result.record_id,
        base_id=submit_result.base_id,
        expected_generation=1,
        lease_owner="grammar-batch-worker-long",
        lease_duration=timedelta(seconds=30),
    )
    # No batch job for GROUPED_WINDOWED
    assert result is None
