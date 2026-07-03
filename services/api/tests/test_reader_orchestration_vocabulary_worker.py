from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    ReaderTextRangeAnchor,
    VocabularyContextGlossItem,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
    VocabularyPhraseGlossItem,
)
from app.services.reader_orchestration import vocabulary_worker as vocabulary_worker_module
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    VOCABULARY_OPERATION_FINGERPRINT,
    VocabularyJobBootstrapService,
    _fingerprint_matches_base,
)
from app.services.reader_orchestration.job_runtime import FenceViolationError
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.vocabulary_worker import (
    FakeVocabularyExecutor,
    PydanticAIVocabularyExecutor,
    VocabularyAnchorSegmentContext,
    VocabularyCandidateOutput,
    VocabularyExecutionError,
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
    _build_vocabulary_output_from_candidates,
    _build_vocabulary_prompt,
    _validate_vocabulary_strategy_metadata,
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
VOCABULARY_ARTICLE_TEXT = (
    "The results prompted the team to rethink their approach.\n\n"
    "Second paragraph for vocabulary."
)


class _StaticVocabularyExecutor:
    def __init__(self, output_builder) -> None:
        self.output_builder = output_builder
        self.calls: list[VocabularyJobContext] = []
        self.usage_data = {
            "aggregate": {
                "input_tokens": 9,
                "output_tokens": 14,
                "total_tokens": 23,
            }
        }

    async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
        self.calls.append(context)
        return VocabularyExecutionResult(
            output=self.output_builder(context),
            usage_data=self.usage_data,
            prompt_version="test-vocabulary-worker",
            model_profile="fake-vocabulary-profile",
            model_provider="fake-provider",
            model_name="fake-vocabulary-model",
        )


class _FailingVocabularyExecutor:
    def __init__(self, error: VocabularyExecutionError) -> None:
        self.error = error

    async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
        raise self.error


class _StubAgentResult:
    def __init__(self, output: object) -> None:
        self.output = output


class _StubPydanticAIVocabularyExecutor(PydanticAIVocabularyExecutor):
    def __init__(self, output: object) -> None:
        self._output = output
        super().__init__(
            settings=Settings(reader_vocabulary_model_profile="reader_vocabulary")
        )

    def _build_agent(self, *, model: object):  # type: ignore[override]
        return object()

    async def _run_agent(self, agent: object, prompt: str) -> _StubAgentResult:  # type: ignore[override]
        return _StubAgentResult(self._output)

def test_real_executor_builds_agent_with_non_deprecated_retry_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    instructions = "stub vocabulary instructions"

    class _CapturingAgent:
        def __init__(self, *args, **kwargs) -> None:
            captured["kwargs"] = kwargs

    monkeypatch.setattr(vocabulary_worker_module, "Agent", _CapturingAgent)
    monkeypatch.setattr(
        vocabulary_worker_module,
        "load_agent_instructions",
        lambda name: instructions,
    )

    executor = PydanticAIVocabularyExecutor(
        settings=Settings(reader_vocabulary_model_profile="reader_vocabulary")
    )
    executor._build_agent(model=object())

    agent_kwargs = captured["kwargs"]
    assert isinstance(agent_kwargs, dict)
    assert agent_kwargs["output_type"] is VocabularyCandidateOutput
    assert agent_kwargs["instructions"] == instructions
    assert agent_kwargs["name"] == "reader_layer_vocabulary_agent"
    assert agent_kwargs["retries"] == {"tools": 1, "output": 2}
    assert "output_retries" not in agent_kwargs
    assert "instrument" not in agent_kwargs


@pytest.fixture
async def vocabulary_worker_env() -> asyncpg.Pool:
    schema_name = f"test_reader_vocabulary_worker_{uuid4().hex}"
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


async def _submit_vocabulary_article(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
):
    return await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=VOCABULARY_ARTICLE_TEXT,
        title="Vocabulary Slice",
        language="en",
    )


def _build_anchor(
    context: VocabularyJobContext,
    selected_text: str,
) -> ReaderTextRangeAnchor:
    segment = context.anchor_segments[0]
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


def _sample_vocabulary_output(context: VocabularyJobContext) -> VocabularyLayerOutput:
    return VocabularyLayerOutput(
        items=[
            VocabularyHighlightItem(
                anchor=_build_anchor(context, "prompted"),
                headword="prompted",
                brief_explanation="促使",
                reason="useful_for_current_goal",
            ),
            VocabularyPhraseGlossItem(
                anchor=_build_anchor(context, "prompted the team"),
                phrase="prompt sb to do sth",
                phrase_type="phrasal_verb",
                gloss="促使某人做某事",
                example=None,
            ),
            VocabularyContextGlossItem(
                anchor=_build_anchor(context, "prompted the team to rethink"),
                display="prompt sb to do sth",
                gloss="促使团队重新思考",
                reason="当前语境强调引发后续动作，不是普通词典义",
            ),
        ]
    )


async def test_bootstrap_creates_vocabulary_run_and_job_with_expected_fingerprint(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    bootstrap = VocabularyJobBootstrapService(pool=vocabulary_worker_env)

    result = await bootstrap.bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert result.base_id == article.base_id
    assert result.expected_generation == 1
    assert _fingerprint_matches_base(
        result.operation_fingerprint, VOCABULARY_OPERATION_FINGERPRINT
    )
    assert result.operation_fingerprint != VOCABULARY_OPERATION_FINGERPRINT
    assert result.unit_id == article.snapshot.navigation.units[0].unit_id

    async with vocabulary_worker_env.acquire() as conn:
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
    assert run_row["run_type"] == "vocabulary_layer"
    assert run_row["status"] == "queued"
    assert run_row["record_generation"] == 1
    assert run_row["trigger_kind"] == "system"
    assert isinstance(run_row["policy_version"], str)

    assert job_row is not None
    assert job_row["base_id"] == article.base_id
    assert job_row["job_type"] == "build_vocabulary_layer"
    assert job_row["target_type"] == "unit"
    assert job_row["target_key"] == result.unit_id
    assert job_row["status"] == "queued"
    assert job_row["expected_generation"] == 1
    assert _fingerprint_matches_base(
        job_row["operation_fingerprint"], VOCABULARY_OPERATION_FINGERPRINT
    )
    assert job_row["operation_fingerprint"] != VOCABULARY_OPERATION_FINGERPRINT
    assert job_row["max_attempts"] == 3


async def test_bootstrap_does_not_create_duplicate_active_vocabulary_job(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    bootstrap = VocabularyJobBootstrapService(pool=vocabulary_worker_env)

    first = await bootstrap.bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    second = await bootstrap.bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    assert second.run_id == first.run_id
    assert second.job_id == first.job_id

    async with vocabulary_worker_env.acquire() as conn:
        total_jobs = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer'
              AND target_type = 'unit'
              AND operation_fingerprint = $2
            """,
            article.record_id,
            first.operation_fingerprint,
        )
    assert total_jobs == 1


async def test_worker_process_publishes_vocabulary_layer_and_snapshot_reload_exposes_it(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    executor = _StaticVocabularyExecutor(_sample_vocabulary_output)
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=executor,
    )

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-1",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.published_layer is not None
    assert len(executor.calls) == 1
    assert executor.calls[0].unit_id == article.snapshot.navigation.units[0].unit_id

    async with vocabulary_worker_env.acquire() as conn:
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
    assert layer_row["layer_type"] == "vocabulary"
    assert layer_row["target_scope"] == "unit"
    assert layer_row["target_key"] == result.context.unit_id
    assert layer_row["generation"] == 1
    assert layer_row["status"] == "published"

    output = VocabularyLayerOutput.model_validate(layer_row["output_json"])
    assert [item.item_type for item in output.items] == [
        "vocab_highlight",
        "phrase_gloss",
        "context_gloss",
    ]

    assert event_row is not None
    assert event_row["sequence"] == 2
    assert event_row["event_type"] == "layer_published"
    assert event_row["source_job_id"] == result.claim.job_id
    assert event_row["source_layer_id"] == result.published_layer.layer_id

    assert job_row is not None and job_row["status"] == "succeeded"
    assert run_row is not None and run_row["status"] == "completed"
    assert usage_row is not None
    assert usage_row["status"] == "succeeded"
    assert usage_row["capability_code"] == "reader_vocabulary"
    assert usage_row["usage_scope"] == "system_internal"
    assert usage_row["billing_mode"] == "internal_only"
    assert usage_row["model_route"] == "reader_layer_vocabulary"
    assert usage_row["model_profile_id"] == "fake-vocabulary-profile"
    assert usage_row["model_provider"] == "fake-provider"
    assert usage_row["model_name"] == "fake-vocabulary-model"
    assert usage_row["reader_run_id"] == result.claim.run_id
    assert usage_row["reader_job_id"] == result.claim.job_id
    assert usage_row["enhancement_layer_id"] == result.published_layer.layer_id
    assert usage_row["input_tokens"] == 9
    assert usage_row["output_tokens"] == 14
    assert usage_row["total_tokens"] == 23
    assert usage_row["operation_fingerprint"] == result.context.operation_fingerprint

    snapshot = await ArticleReadyPersistenceService(pool=vocabulary_worker_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert [layer.layer_id for layer in snapshot.enhancement_layers] == [
        str(result.published_layer.layer_id)
    ]
    assert snapshot.enhancement_layers[0].layer_type == "vocabulary"
    marked_snapshot_leaves = [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_vocabulary_marks")
    ]
    snapshot_item_types = {
        mark["item_type"]
        for leaf in marked_snapshot_leaves
        for mark in leaf["reader_vocabulary_marks"]  # type: ignore[index]
    }
    assert snapshot_item_types == {
        "vocab_highlight",
        "phrase_gloss",
        "context_gloss",
    }


async def test_real_executor_path_publishes_vocabulary_layer_and_snapshot_marks(
    vocabulary_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    monkeypatch.setattr(
        vocabulary_worker_module,
        "build_model_for_route",
        lambda settings, route: (
            object(),
            SimpleNamespace(
                profile_name="reader-vocab-profile",
                provider="stub-provider",
                model_name="stub-model",
                api_key="",
            ),
        ),
    )
    monkeypatch.setattr(
        vocabulary_worker_module,
        "extract_run_usage",
        lambda result: {
            "aggregate": {
                "input_tokens": 15,
                "output_tokens": 11,
                "total_tokens": 26,
            }
        },
    )

    executor = _StubPydanticAIVocabularyExecutor(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted",
                    "headword": "prompted",
                    "brief_explanation": "促使",
                    "reason": "useful_for_current_goal",
                },
                {
                    "item_type": "phrase_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team",
                    "phrase": "prompt sb to do sth",
                    "phrase_type": "phrasal_verb",
                    "gloss": "促使某人做某事",
                    "example": None,
                },
                {
                    "item_type": "context_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team to rethink",
                    "display": "prompt sb to do sth",
                    "gloss": "促使团队重新思考",
                    "reason": "当前语境强调引发后续动作，不是普通词典义",
                },
            ],
        }
    )
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=executor,
    )

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-real-executor",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert [item.item_type for item in result.output.items] == [
        "vocab_highlight",
        "phrase_gloss",
        "context_gloss",
    ]

    snapshot = await ArticleReadyPersistenceService(pool=vocabulary_worker_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )
    marked_snapshot_leaves = [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_vocabulary_marks")
    ]
    snapshot_item_types = {
        mark["item_type"]
        for leaf in marked_snapshot_leaves
        for mark in leaf["reader_vocabulary_marks"]  # type: ignore[index]
    }
    assert snapshot_item_types == {
        "vocab_highlight",
        "phrase_gloss",
        "context_gloss",
    }


async def test_worker_without_executor_fails_terminal_and_does_not_publish_vocabulary_layer(
    vocabulary_worker_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    monkeypatch.setattr(
        vocabulary_worker_module,
        "get_settings",
        lambda: Settings(reader_vocabulary_model_profile=""),
    )
    worker = VocabularyWorkerService(pool=vocabulary_worker_env)

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-empty",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    async with vocabulary_worker_env.acquire() as conn:
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
              AND layer_type = 'vocabulary'
            """,
            article.record_id,
        )
    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "configuration"
    assert job_row["failure_code"] == "vocabulary_executor_unconfigured"
    assert job_row["rationale_code"] == "vocabulary_executor_unconfigured"
    assert run_row is not None
    assert run_row["status"] == "failed_terminal"
    assert run_row["failure_class"] == "configuration"
    assert run_row["failure_code"] == "vocabulary_executor_unconfigured"
    assert layer_count == 0

async def test_worker_explicit_fake_executor_can_publish_empty_vocabulary_layer(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=FakeVocabularyExecutor(),
    )

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-empty-explicit",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output.items == []

    async with vocabulary_worker_env.acquire() as conn:
        output_json = await conn.fetchval(
            """
            SELECT output_json
            FROM enhancement_layers
            WHERE id = $1
            """,
            result.published_layer.layer_id,
        )
    validated = VocabularyLayerOutput.model_validate(output_json)
    assert validated.items == []


async def test_worker_retryable_failure_moves_job_to_retry_later_and_records_failed_usage(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=_FailingVocabularyExecutor(
            VocabularyExecutionError(
                "temporary vocabulary timeout",
                retryable=True,
                failure_class="provider",
                failure_code="provider_timeout",
            )
        ),
    )

    started_at = datetime.now(UTC)
    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-retry",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(minutes=3),
    )

    assert result is not None
    assert result.status == "retry_later"

    async with vocabulary_worker_env.acquire() as conn:
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
        usage_row = await conn.fetchrow(
            """
            SELECT status, capability_code, model_route, model_profile_id,
                   model_provider, model_name, enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at DESC
            LIMIT 1
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

    assert usage_row is not None
    assert usage_row["status"] == "failed"
    assert usage_row["capability_code"] == "reader_vocabulary"
    assert usage_row["model_route"] == "reader_layer_vocabulary"
    assert usage_row["model_profile_id"] is None
    assert usage_row["model_provider"] is None
    assert usage_row["model_name"] is None
    assert usage_row["enhancement_layer_id"] is None

    async with vocabulary_worker_env.acquire() as conn:
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'vocabulary'
            """,
            article.record_id,
        )
    assert layer_count == 0


async def test_worker_terminal_failure_moves_job_to_failed_terminal_and_records_failed_usage(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=_FailingVocabularyExecutor(
            VocabularyExecutionError(
                "unsupported vocabulary policy",
                retryable=False,
                failure_class="policy",
                failure_code="unsupported_vocabulary_policy",
            )
        ),
    )

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-terminal",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "failed_terminal"

    async with vocabulary_worker_env.acquire() as conn:
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
        usage_row = await conn.fetchrow(
            """
            SELECT status, capability_code, model_route, model_profile_id,
                   model_provider, model_name, enhancement_layer_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            result.claim.job_id,
        )

    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "policy"
    assert job_row["failure_code"] == "unsupported_vocabulary_policy"
    assert "unsupported vocabulary policy" in job_row["failure_message"]
    assert job_row["rationale_code"] == "unsupported_vocabulary_policy"

    assert run_row is not None
    assert run_row["status"] == "failed_terminal"
    assert run_row["failure_class"] == "policy"
    assert run_row["failure_code"] == "unsupported_vocabulary_policy"
    assert run_row["finished_at"] is not None

    assert usage_row is not None
    assert usage_row["status"] == "failed"
    assert usage_row["capability_code"] == "reader_vocabulary"
    assert usage_row["model_route"] == "reader_layer_vocabulary"
    assert usage_row["model_profile_id"] is None
    assert usage_row["model_provider"] is None
    assert usage_row["model_name"] is None
    assert usage_row["enhancement_layer_id"] is None

    async with vocabulary_worker_env.acquire() as conn:
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'vocabulary'
            """,
            article.record_id,
        )
    assert layer_count == 0


async def test_worker_claim_fence_supersedes_job_on_active_base_mismatch(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    boot_result = await VocabularyJobBootstrapService(
        pool=vocabulary_worker_env
    ).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    worker = VocabularyWorkerService(pool=vocabulary_worker_env)

    async with vocabulary_worker_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            article.record_id,
        )

    result = await worker.process_next_vocabulary_job(
        lease_owner="vocabulary-worker-stale-claim",
        lease_duration=timedelta(seconds=30),
    )

    assert result is None

    async with vocabulary_worker_env.acquire() as conn:
        job_status = await conn.fetchval(
            """
            SELECT status
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer'
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
              AND layer_type = 'vocabulary'
            """,
            article.record_id,
        )

    assert job_status == "superseded"
    assert layer_count == 0


async def test_worker_publish_fence_supersedes_claimed_job_on_stale_generation(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    await VocabularyJobBootstrapService(pool=vocabulary_worker_env).bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    executor = _StaticVocabularyExecutor(_sample_vocabulary_output)
    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=executor,
    )
    claim = await worker.claim_vocabulary_job(
        lease_owner="vocabulary-worker-stale-publish",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None

    async with vocabulary_worker_env.acquire() as conn:
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
        await worker.process_claimed_vocabulary_job(claim=claim)

    async with vocabulary_worker_env.acquire() as conn:
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
              AND layer_type = 'vocabulary'
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


def test_vocabulary_modules_do_not_reference_render_scene_json() -> None:
    job_bootstrap_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "job_bootstrap.py"
    )
    worker_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "vocabulary_worker.py"
    )
    layer_publisher_path = (
        API_ROOT / "app" / "services" / "reader_orchestration" / "layer_publisher.py"
    )

    assert "render_scene_json" not in job_bootstrap_path.read_text(encoding="utf-8")
    assert "render_scene_json" not in worker_path.read_text(encoding="utf-8")
    assert "render_scene_json" not in layer_publisher_path.read_text(encoding="utf-8")


def _make_segment(
    *,
    anchor_segment_id: str,
    text: str,
    segment_type: str = "sentence",
    boundary_quality: str = "normal",
) -> VocabularyAnchorSegmentContext:
    return VocabularyAnchorSegmentContext(
        anchor_segment_id=anchor_segment_id,
        sentence_id=anchor_segment_id,
        segment_type=segment_type,
        unit_start_utf16=0,
        unit_end_utf16=len(text.encode("utf-16-le", "surrogatepass")) // 2,
        text_hash=compute_text_range_hash(text),
        text=text,
        boundary_quality=boundary_quality,
    )


def _build_fallback_context(
    *,
    unit_text: str,
    segments: list[VocabularyAnchorSegmentContext],
) -> VocabularyJobContext:
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    return VocabularyJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        unit_id="u_fb",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="vocabulary_unit_v1",
        source_language="en",
        source_text=unit_text,
        text_hash=compute_text_range_hash(unit_text),
        anchor_segments=tuple(segments),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        vocabulary_prompt_lines=layer.prompt_lines,
    )


def test_real_executor_skips_fallback_window_segment_with_boundary_reason() -> None:
    fallback_text = "longword " * 24
    context = _build_fallback_context(
        unit_text=fallback_text,
        segments=[
            _make_segment(
                anchor_segment_id="fb1",
                text=fallback_text,
                segment_type="fallback_window",
                boundary_quality="low",
            )
        ],
    )
    candidate = VocabularyCandidateOutput.model_validate(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "fb1",
                    "selected_text": "longword",
                    "headword": "longword",
                    "brief_explanation": "long",
                    "reason": "common",
                }
            ],
        }
    )

    output, diagnostics = _build_vocabulary_output_from_candidates(context, candidate)

    assert output.items == []
    reason_codes = [
        entry.get("reason_code")
        for entry in diagnostics.get("skipped_items", [])
        if isinstance(entry, dict)
    ]
    assert reason_codes == ["boundary_low_fallback_window"]
    assert diagnostics["resolved_item_count"] == 0
    assert diagnostics["skipped_item_count"] == 1


def test_real_executor_resolves_normal_segment_when_other_segment_is_fallback() -> None:
    normal_text = "She bought a brand-new notebook."
    fallback_text = "longword " * 24
    unit_text = f"{normal_text} {fallback_text.strip()}"
    context = _build_fallback_context(
        unit_text=unit_text,
        segments=[
            _make_segment(
                anchor_segment_id="s1",
                text=normal_text,
                segment_type="sentence",
                boundary_quality="normal",
            ),
            _make_segment(
                anchor_segment_id="fb1",
                text=fallback_text,
                segment_type="fallback_window",
                boundary_quality="low",
            ),
        ],
    )
    candidate = VocabularyCandidateOutput.model_validate(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": "bought",
                    "headword": "bought",
                    "brief_explanation": "买",
                    "reason": "common",
                },
                {
                    "item_type": "phrase_gloss",
                    "anchor_segment_id": "fb1",
                    "selected_text": "longword",
                    "phrase": "longword",
                    "phrase_type": "compound",
                    "gloss": "长词",
                },
            ],
        }
    )

    output, diagnostics = _build_vocabulary_output_from_candidates(context, candidate)

    assert len(output.items) == 1
    surviving = output.items[0]
    assert surviving.item_type == "vocab_highlight"
    assert surviving.anchor.anchor_segment_id == "s1"
    reason_codes = [
        entry.get("reason_code")
        for entry in diagnostics.get("skipped_items", [])
        if isinstance(entry, dict)
    ]
    assert reason_codes == ["boundary_low_fallback_window"]


def test_vocabulary_anchor_segment_context_defaults_boundary_quality_to_normal() -> None:
    seg = VocabularyAnchorSegmentContext(
        anchor_segment_id="s1",
        sentence_id="s1",
        segment_type="sentence",
        unit_start_utf16=0,
        unit_end_utf16=10,
        text_hash="abcd1234",
        text="hello",
    )
    assert seg.boundary_quality == "normal"


# ---------------------------------------------------------------------------#
# T7: variant-first strategy integration into vocabulary worker
# ---------------------------------------------------------------------------#


def _build_context_for_variant(
    *,
    reading_goal: str,
    reading_variant: str,
    source_text: str = "Vocabulary source text for variant strategy.",
) -> VocabularyJobContext:
    """Build a VocabularyJobContext with strategy metadata for a given variant."""
    strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    layer = strategy.layers["vocabulary"]
    return VocabularyJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="vocabulary_unit_v1",
        source_language="en",
        source_text=source_text,
        text_hash="vocabulary-text-hash",
        anchor_segments=(
            VocabularyAnchorSegmentContext(
                anchor_segment_id="s1",
                sentence_id="s1",
                segment_type="sentence",
                unit_start_utf16=0,
                unit_end_utf16=len(
                    source_text.encode("utf-16-le", "surrogatepass")
                )
                // 2,
                text_hash=compute_text_range_hash(source_text),
                text=source_text,
            ),
        ),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        vocabulary_prompt_lines=layer.prompt_lines,
    )


def test_build_vocabulary_prompt_contains_concrete_policy_lines() -> None:
    """The prompt must include the concrete vocabulary policy lines from
    reader_variants.yaml, not just a goal/variant label."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_vocabulary_prompt(context)

    # The strategy section must be present with the variant's prompt lines.
    assert "<reader_strategy>" in prompt
    assert "</reader_strategy>" in prompt
    assert "<policy_lines>" in prompt
    assert "</policy_lines>" in prompt
    assert "reading_goal: daily_reading" in prompt
    assert "reading_variant: intermediate_reading" in prompt
    assert "strategy_hash:" in prompt
    assert "layer_policy_hash:" in prompt

    # Every concrete policy line for intermediate_reading vocabulary layer
    # must appear in the prompt.
    for line in context.vocabulary_prompt_lines:
        assert line in prompt


def test_build_vocabulary_prompt_differs_between_daily_intermediate_and_exam_cet() -> None:
    """daily_reading/intermediate_reading and exam/cet must produce
    different strategy sections in the vocabulary prompt."""
    daily_context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    exam_context = _build_context_for_variant(
        reading_goal="exam",
        reading_variant="cet",
    )

    daily_prompt = _build_vocabulary_prompt(daily_context)
    exam_prompt = _build_vocabulary_prompt(exam_context)

    # The two prompts must differ in the strategy section.
    assert daily_prompt != exam_prompt

    # The daily prompt must carry the daily_reading goal and the
    # intermediate_reading variant's vocabulary policy lines.
    assert "reading_goal: daily_reading" in daily_prompt
    assert "reading_variant: intermediate_reading" in daily_prompt
    for line in daily_context.vocabulary_prompt_lines:
        assert line in daily_prompt

    # The exam prompt must carry the exam goal and the cet variant's
    # vocabulary policy lines.
    assert "reading_goal: exam" in exam_prompt
    assert "reading_variant: cet" in exam_prompt
    for line in exam_context.vocabulary_prompt_lines:
        assert line in exam_prompt

    # The two variants' vocabulary policy lines must actually differ (this
    # guards against accidentally identical policy text).
    assert (
        daily_context.vocabulary_prompt_lines
        != exam_context.vocabulary_prompt_lines
    )


def test_build_vocabulary_prompt_strategy_section_order() -> None:
    """The strategy section must sit before the 'Return only...' directive
    so it does not clobber the source_text block."""
    context = _build_context_for_variant(
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    prompt = _build_vocabulary_prompt(context)

    unit_id_idx = prompt.index(f"unit_id: {context.unit_id}")
    strategy_idx = prompt.index("<reader_strategy>")
    return_idx = prompt.index("Return only the structured candidate output.")
    source_idx = prompt.index("<source_text>")

    assert unit_id_idx < strategy_idx < return_idx < source_idx


# ---------------------------------------------------------------------------#
# T7: _validate_vocabulary_strategy_metadata fail-closed unit tests
# ---------------------------------------------------------------------------#


def test_validate_strategy_metadata_rejects_non_mapping_input() -> None:
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(None)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_missing_keys() -> None:
    """A legacy bare-fingerprint job whose input_json lacks strategy
    metadata must fail closed, not fall back to a default strategy."""
    incomplete = {
        "unit_id": "u1",
        "base_language": "en",
        # No reading_goal / reading_variant / strategy_version / strategy_hash
        # / layer_policy_hash.
    }
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(incomplete)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert "reading_goal" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_empty_string_values() -> None:
    """Empty string values are treated as missing."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    payload = {
        "reading_goal": "",
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(payload)
    assert exc_info.value.failure_code == "strategy_metadata_missing"


def test_validate_strategy_metadata_rejects_strategy_hash_mismatch() -> None:
    """strategy_hash mismatch must fail closed with a dedicated code."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(payload)
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
        # Use a different layer's policy_hash to trigger mismatch.
        "layer_policy_hash": strategy.layers["translation"].policy_hash,
    }
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(payload)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


def test_validate_strategy_metadata_rejects_strategy_version_mismatch() -> None:
    """strategy_version mismatch must fail closed."""
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    payload = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": "stale_version",
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(payload)
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
    with pytest.raises(VocabularyExecutionError) as exc_info:
        _validate_vocabulary_strategy_metadata(payload)
    assert exc_info.value.failure_class == "strategy_resolution"
    assert exc_info.value.failure_code == "strategy_resolver_error"


def test_validate_strategy_metadata_returns_resolved_prompt_lines_on_success() -> None:
    """On success, the helper returns the resolver's concrete prompt_lines
    so the prompt builder can inject them."""
    strategy = resolve_reader_variant_strategy("exam", "cet")
    layer = strategy.layers["vocabulary"]
    payload = {
        "reading_goal": "exam",
        "reading_variant": "cet",
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
    }
    result = _validate_vocabulary_strategy_metadata(payload)
    assert result.reading_goal == "exam"
    assert result.reading_variant == "cet"
    assert result.strategy_hash == strategy.strategy_hash
    assert result.layer_policy_hash == layer.policy_hash
    assert result.vocabulary_prompt_lines == layer.prompt_lines
    assert len(result.vocabulary_prompt_lines) >= 1


# ---------------------------------------------------------------------------#
# T7: _load_job_context integration — reads T5 bootstrap strategy metadata
# ---------------------------------------------------------------------------#


async def test_load_job_context_reads_t5_bootstrap_strategy_metadata(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must read strategy metadata written by T5 bootstrap
    and resolve the concrete vocabulary policy lines from the resolver."""
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    bootstrap = VocabularyJobBootstrapService(pool=vocabulary_worker_env)
    boot_result = await bootstrap.bootstrap_vocabulary_run(
        record_id=article.record_id,
        user_id=user_id,
    )

    worker = VocabularyWorkerService(pool=vocabulary_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    # The context must carry the strategy metadata from input_json.
    assert context.reading_goal == "daily_reading"
    assert context.reading_variant == "intermediate_reading"

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    assert context.strategy_version == strategy.strategy_version
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["vocabulary"].policy_hash

    # The concrete prompt lines must come from the resolver, not from
    # input_json (input_json only stores hashes, not the lines themselves).
    assert context.vocabulary_prompt_lines == strategy.layers["vocabulary"].prompt_lines
    assert len(context.vocabulary_prompt_lines) >= 1

    # The prompt built from this context must include the concrete lines.
    prompt = _build_vocabulary_prompt(context)
    for line in context.vocabulary_prompt_lines:
        assert line in prompt


async def test_load_job_context_reads_exam_cet_strategy_metadata(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """_load_job_context must also work for exam/cet variant."""
    from app.services.reader_orchestration.article_ready_service import (
        ArticleReadyPersistenceService,
        PlainTextArticleReadySubmitRequest,
    )

    user_id = await insert_user(vocabulary_worker_env)
    service = ArticleReadyPersistenceService(pool=vocabulary_worker_env)
    submit_result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="First paragraph for exam vocab.\n\nSecond paragraph for exam vocab.",
            title="Exam CET Vocabulary Slice",
            language="en",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="cet",  # type: ignore[arg-type]
        )
    )

    bootstrap = VocabularyJobBootstrapService(pool=vocabulary_worker_env)
    boot_result = await bootstrap.bootstrap_vocabulary_run(
        record_id=submit_result.record_id,
        user_id=user_id,
    )

    worker = VocabularyWorkerService(pool=vocabulary_worker_env)
    context = await worker._load_job_context(boot_result.job_id)

    assert context.reading_goal == "exam"
    assert context.reading_variant == "cet"
    strategy = resolve_reader_variant_strategy("exam", "cet")
    assert context.strategy_hash == strategy.strategy_hash
    assert context.layer_policy_hash == strategy.layers["vocabulary"].policy_hash
    assert context.vocabulary_prompt_lines == strategy.layers["vocabulary"].prompt_lines


async def _insert_legacy_vocabulary_job_without_strategy_metadata(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    user_id: UUID,
    unit_id: str,
    input_json: dict,
) -> UUID:
    """Insert a vocabulary job row with crafted input_json.

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
            VALUES ($1, $2, 'vocabulary_layer', 'queued', 1,
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
                'build_vocabulary_layer', 'unit', $5, 'queued',
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
            "vocabulary_unit_v1",
            f"vocabulary_unit_v1:{unit_id}",
            "legacy-input-hash",
            jsonb_param(input_json),
        )
    assert isinstance(job_id, UUID)
    return job_id


async def test_load_job_context_fail_closed_on_missing_strategy_metadata(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """A legacy bare-fingerprint job without strategy metadata in input_json
    must fail closed when _load_job_context tries to load it."""
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        # No strategy metadata keys.
    }
    job_id = await _insert_legacy_vocabulary_job_without_strategy_metadata(
        vocabulary_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = VocabularyWorkerService(pool=vocabulary_worker_env)
    with pytest.raises(VocabularyExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_metadata_missing"
    assert exc_info.value.retryable is False


async def test_load_job_context_fail_closed_on_strategy_hash_mismatch(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json strategy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["vocabulary"]
    tampered_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash + "_tampered",
        "layer_policy_hash": layer.policy_hash,
    }
    job_id = await _insert_legacy_vocabulary_job_without_strategy_metadata(
        vocabulary_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = VocabularyWorkerService(pool=vocabulary_worker_env)
    with pytest.raises(VocabularyExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "strategy_hash_mismatch"
    assert exc_info.value.retryable is False


async def test_load_job_context_fail_closed_on_layer_policy_hash_mismatch(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """A job whose input_json layer_policy_hash doesn't match the resolver
    output must fail closed."""
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
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
    job_id = await _insert_legacy_vocabulary_job_without_strategy_metadata(
        vocabulary_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=tampered_input_json,
    )

    worker = VocabularyWorkerService(pool=vocabulary_worker_env)
    with pytest.raises(VocabularyExecutionError) as exc_info:
        await worker._load_job_context(job_id)
    assert exc_info.value.failure_code == "layer_policy_hash_mismatch"
    assert exc_info.value.retryable is False


async def test_worker_fail_closed_on_missing_strategy_metadata_moves_job_to_failed_terminal(
    vocabulary_worker_env: asyncpg.Pool,
) -> None:
    """End-to-end: a legacy job without strategy metadata, when processed
    by the worker, must move to failed_terminal with the right failure code."""
    user_id = await insert_user(vocabulary_worker_env)
    article = await _submit_vocabulary_article(vocabulary_worker_env, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    legacy_input_json = {
        "unit_id": unit_id,
        "base_language": "en",
    }
    job_id = await _insert_legacy_vocabulary_job_without_strategy_metadata(
        vocabulary_worker_env,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        input_json=legacy_input_json,
    )

    worker = VocabularyWorkerService(
        pool=vocabulary_worker_env,
        executor=_StaticVocabularyExecutor(_sample_vocabulary_output),
    )

    # Manually claim the legacy bare-fingerprint job through the runtime so
    # this test can exercise process_claimed_vocabulary_job's fail-closed
    # metadata-validation path directly.
    from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

    runtime = ReaderJobRuntime(pool=vocabulary_worker_env)
    claim = await runtime.claim_next_job(
        lease_owner="legacy-test-worker",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer",
        operation_fingerprint="vocabulary_unit_v1",
    )
    assert claim is not None
    assert claim.job_id == job_id

    result = await worker.process_claimed_vocabulary_job(claim=claim)

    assert result.status == "failed_terminal"
    assert result.context is None  # context loading failed before assignment

    async with vocabulary_worker_env.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, failure_class, failure_code, rationale_code "
            "FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert job_row is not None
    assert job_row["status"] == "failed_terminal"
    assert job_row["failure_class"] == "validation"
    assert job_row["failure_code"] == "strategy_metadata_missing"
    # VocabularyExecutionError defaults rationale_code to failure_code when
    # not explicitly set; the worker's VocabularyExecutionError branch
    # propagates exc.rationale_code to the transition call.
    assert job_row["rationale_code"] == "strategy_metadata_missing"
