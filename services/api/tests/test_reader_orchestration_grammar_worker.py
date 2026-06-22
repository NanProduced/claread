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
)
from app.services.reader_orchestration.grammar_worker import (
    FakeGrammarBundleExecutor,
    GrammarBundleCandidateOutput,
    GrammarBundleWorkerService,
    GrammarExecutionError,
    GrammarExecutionResult,
    GrammarJobContext,
    PydanticAIGrammarBundleExecutor,
)
from app.services.reader_orchestration.job_bootstrap import (
    GRAMMAR_OPERATION_FINGERPRINT,
    GrammarJobBootstrapService,
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
    assert result.operation_fingerprint == GRAMMAR_OPERATION_FINGERPRINT
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
    assert job_row["operation_fingerprint"] == GRAMMAR_OPERATION_FINGERPRINT
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
            GRAMMAR_OPERATION_FINGERPRINT,
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
    await GrammarJobBootstrapService(pool=grammar_worker_env).bootstrap_grammar_run(
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
            GRAMMAR_OPERATION_FINGERPRINT,
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
