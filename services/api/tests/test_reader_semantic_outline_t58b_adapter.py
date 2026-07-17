"""T5.8b — controlled real adapter, policy pre-call, usage/provenance wiring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.database import connection as db_connection
from app.llm.routes import MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
from app.services.ai_usage import CAPABILITY_READER_SEMANTIC_OUTLINE
from app.services.reader_orchestration.job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    EnhancementJobBootstrapService,
    allow_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.semantic_outline_execution_policy import (
    SemanticOutlineExecutionPolicy,
)
from app.services.reader_orchestration.semantic_outline_executor import (
    OutlineCandidatesOutput,
    PydanticAISemanticOutlineGenerator,
)
from app.services.reader_orchestration.semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    FakeSemanticOutlineGenerator,
    SemanticOutlineExecutionResult,
    SemanticOutlineGenerationError,
    SemanticOutlineWorkerService,
    UnconfiguredSemanticOutlineGenerator,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0020_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0020_reader_semantic_outline_layer.sql"
).read_text(encoding="utf-8")
OUTLINE_SCHEMA_SQL = BASELINE_SQL + "\n" + MIGRATION_0020_SQL

_USAGE = {
    "aggregate": {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    schema_name = f"test_reader_semantic_outline_t58b_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(OUTLINE_SCHEMA_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            db_connection.DB_POOL = original_pool
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _bootstrap_outline(pool, *, record_id, user_id):
    boot = await EnhancementJobBootstrapService(
        pool=pool,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(record_id=record_id, user_id=user_id)
    assert boot is not None
    return boot


async def _first_unit_id(pool, record_id) -> str:
    async with pool.acquire() as conn:
        uid = await conn.fetchval(
            """
            SELECT unit_id FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index LIMIT 1
            """,
            record_id,
        )
    return str(uid)


async def _count_usage(pool, *, job_id) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*) FROM ai_usage_events
                WHERE reader_job_id = $1
                  AND capability_code = $2
                """,
                job_id,
                CAPABILITY_READER_SEMANTIC_OUTLINE,
            )
            or 0
        )


async def _fetch_usage_row(pool, *, job_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT capability_code, model_route, model_profile, model_name,
                   total_tokens, status, reader_run_id, reading_record_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            job_id,
        )


def test_t58b_default_worker_still_unconfigured() -> None:
    worker = SemanticOutlineWorkerService(pool=None)
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)


def test_t58b_policy_disabled_raises_without_call() -> None:
    policy = SemanticOutlineExecutionPolicy.for_tests(generation_enabled=False)
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineWorkerInput,
    )

    empty = SemanticOutlineWorkerInput(
        base_id="b", generation=1, units=(), anchors=(), total_preview_chars=0
    )
    with pytest.raises(SemanticOutlineGenerationError) as exc:
        policy.assert_can_call_provider(
            profile_configured=True, worker_input=empty
        )
    assert exc.value.failure_code == "semantic_outline_generation_disabled"
    assert exc.value.provider_call_made is False


def test_t58b_policy_missing_profile_raises() -> None:
    policy = SemanticOutlineExecutionPolicy.for_tests(generation_enabled=True)
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineWorkerInput,
    )

    empty = SemanticOutlineWorkerInput(
        base_id="b", generation=1, units=(), anchors=(), total_preview_chars=0
    )
    with pytest.raises(SemanticOutlineGenerationError) as exc:
        policy.assert_can_call_provider(
            profile_configured=False, worker_input=empty
        )
    assert exc.value.failure_code == "model_route_unavailable"
    assert exc.value.provider_call_made is False


def test_t58b_policy_rejects_nonempty_preview_over_cap() -> None:
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineUnitPreview,
        SemanticOutlineWorkerInput,
    )

    policy = SemanticOutlineExecutionPolicy.for_tests(
        generation_enabled=True,
        max_unit_preview_chars=10,
        max_total_preview_chars=100,
    )
    over = SemanticOutlineWorkerInput(
        base_id="b",
        generation=1,
        units=(
            SemanticOutlineUnitPreview(
                unit_id="u1", order_index=1, unit_type="body", preview="x" * 11
            ),
        ),
        anchors=(),
        total_preview_chars=11,
    )
    with pytest.raises(SemanticOutlineGenerationError) as exc:
        policy.assert_can_call_provider(profile_configured=True, worker_input=over)
    assert exc.value.failure_code == "semantic_outline_input_envelope_exceeded"
    assert exc.value.provider_call_made is False


def test_t58b_policy_allows_identity_only_units_beyond_preview_count() -> None:
    """Many units with empty preview must not be rejected (identity-only)."""
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineUnitPreview,
        SemanticOutlineWorkerInput,
    )

    policy = SemanticOutlineExecutionPolicy.for_tests(
        generation_enabled=True,
        max_units_for_preview=2,
        max_unit_preview_chars=50,
        max_total_preview_chars=100,
    )
    units = tuple(
        SemanticOutlineUnitPreview(
            unit_id=f"u{i}",
            order_index=i,
            unit_type="body",
            preview=("ok" if i <= 2 else ""),
        )
        for i in range(1, 10)
    )
    worker_input = SemanticOutlineWorkerInput(
        base_id="b",
        generation=1,
        units=units,
        anchors=(),
        total_preview_chars=4,  # two non-empty "ok"
    )
    # Must not raise — identity-only extras are allowed.
    policy.assert_can_call_provider(
        profile_configured=True, worker_input=worker_input
    )


def test_t58b_policy_rejects_too_many_nonempty_preview_units() -> None:
    """max_units_for_preview=2 and 3 non-empty previews → envelope exceeded."""
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineUnitPreview,
        SemanticOutlineWorkerInput,
    )

    policy = SemanticOutlineExecutionPolicy.for_tests(
        generation_enabled=True,
        max_units_for_preview=2,
        max_unit_preview_chars=50,
        max_total_preview_chars=200,
    )
    units = tuple(
        SemanticOutlineUnitPreview(
            unit_id=f"u{i}",
            order_index=i,
            unit_type="body",
            preview=f"p{i}",
        )
        for i in range(1, 4)  # 3 non-empty
    )
    worker_input = SemanticOutlineWorkerInput(
        base_id="b",
        generation=1,
        units=units,
        anchors=(),
        total_preview_chars=6,
    )
    with pytest.raises(SemanticOutlineGenerationError) as exc:
        policy.assert_can_call_provider(
            profile_configured=True, worker_input=worker_input
        )
    assert exc.value.failure_code == "semantic_outline_input_envelope_exceeded"
    assert exc.value.provider_call_made is False


def test_t58b_apply_output_token_cap_merges_into_model_settings() -> None:
    from app.llm.types import RunModelSettings
    from app.services.reader_orchestration.semantic_outline_executor import (
        apply_output_token_cap,
    )

    capped = apply_output_token_cap(None, max_output_tokens=512)
    assert capped.max_tokens == 512
    capped2 = apply_output_token_cap(
        RunModelSettings(max_tokens=2048, temperature=0.2),
        max_output_tokens=512,
    )
    assert capped2.max_tokens == 512
    assert capped2.temperature == 0.2
    capped3 = apply_output_token_cap(
        RunModelSettings(max_tokens=100),
        max_output_tokens=512,
    )
    assert capped3.max_tokens == 100  # existing tighter cap wins


async def test_t58b_disabled_claimed_job_permanent_zero_usage(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Disabled path."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_prof",
    )
    gen = PydanticAISemanticOutlineGenerator(
        settings=settings,
        policy=SemanticOutlineExecutionPolicy.for_tests(generation_enabled=False),
    )
    worker = SemanticOutlineWorkerService(pool=outline_env, generator=gen)
    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58b-disabled",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "semantic_outline_generation_disabled"
    async with outline_env.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, failure_code FROM reader_jobs WHERE id = $1", boot.job_id
        )
        run = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1", boot.run_id
        )
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
    assert job["status"] == "failed_terminal"
    assert job["failure_code"] == "semantic_outline_generation_disabled"
    assert run["status"] == "failed_terminal"
    assert run["finished_at"] is not None
    assert int(layers) == 0
    assert await _count_usage(outline_env, job_id=boot.job_id) == 0


async def test_t58b_injected_success_publish_one_usage(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Section one.\n\nSection two.",
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    uid = await _first_unit_id(outline_env, article.record_id)

    class _SuccessGen:
        async def generate(self, context):
            return SemanticOutlineExecutionResult(
                candidates=(
                    SemanticOutlineCandidateNode(
                        candidate_ref="c1",
                        parent_candidate_ref=None,
                        depth=1,
                        title="Intro",
                        start_unit_id=uid,
                        end_unit_id=uid,
                    ),
                ),
                model="mock-outline-model",
                usage_data=_USAGE,
                prompt_version="0.0.6",
                model_route=MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
                model_profile="outline_prof",
                model_provider="mock",
                model_name="mock-outline-model",
                provider_call_made=True,
            )

    worker = SemanticOutlineWorkerService(
        pool=outline_env, generator=_SuccessGen()  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58b-ok",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    assert await _count_usage(outline_env, job_id=boot.job_id) == 1
    row = await _fetch_usage_row(outline_env, job_id=boot.job_id)
    assert row is not None
    assert row["capability_code"] == CAPABILITY_READER_SEMANTIC_OUTLINE
    assert row["model_route"] == MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
    assert row["model_profile"] == "outline_prof"
    assert row["model_name"] == "mock-outline-model"
    assert row["reader_run_id"] == boot.run_id
    assert row["reading_record_id"] == article.record_id
    assert row["status"] == "succeeded"


async def test_t58b_invalid_structured_output_permanent_one_usage(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Invalid schema path."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )

    class _InvalidAfterCall:
        async def generate(self, context):
            raise SemanticOutlineGenerationError(
                "bad candidates",
                failure_class="validation",
                failure_code="model_output_invalid",
                retryable=False,
                provider_call_made=True,
                usage_data=_USAGE,
                prompt_version="0.0.6",
                model_route=MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
                model_profile="outline_prof",
                model_provider="mock",
                model_name="mock-outline-model",
            )

    worker = SemanticOutlineWorkerService(
        pool=outline_env, generator=_InvalidAfterCall()  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58b-invalid",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "model_output_invalid"
    async with outline_env.acquire() as conn:
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
        run = await conn.fetchrow(
            "SELECT status FROM reader_runs WHERE id = $1", boot.run_id
        )
    assert int(layers) == 0
    assert run["status"] == "failed_terminal"
    assert await _count_usage(outline_env, job_id=boot.job_id) == 1


async def test_t58b_timeout_without_usage_zero_events(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Timeout path."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )

    class _Timeout:
        async def generate(self, context):
            raise SemanticOutlineGenerationError(
                "timeout",
                failure_class="provider",
                failure_code="TimeoutError",
                retryable=True,
                provider_call_made=True,
                usage_data=None,
                model_route=MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
            )

    worker = SemanticOutlineWorkerService(
        pool=outline_env, generator=_Timeout()  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58b-timeout",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "retry_later"
    async with outline_env.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, failure_code FROM reader_jobs WHERE id = $1", boot.job_id
        )
        run = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1", boot.run_id
        )
    assert job["status"] == "retry_later"
    assert job["failure_code"] == "TimeoutError"
    assert run["status"] == "running"
    assert run["finished_at"] is None
    assert await _count_usage(outline_env, job_id=boot.job_id) == 0


async def test_t58b_fake_default_no_provider_call_flag_no_usage(
    outline_env: asyncpg.Pool,
) -> None:
    """Fake generator does not set provider_call_made → zero usage events."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Fake path.\n\nSecond."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    uid = await _first_unit_id(outline_env, article.record_id)
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(
            (
                SemanticOutlineCandidateNode(
                    candidate_ref="c1",
                    parent_candidate_ref=None,
                    depth=1,
                    title="A",
                    start_unit_id=uid,
                    end_unit_id=uid,
                ),
            )
        ),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58b-fake",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    assert await _count_usage(outline_env, job_id=boot.job_id) == 0


async def test_t58b_adapter_invalid_output_via_mock_run_terminal_one_usage(
    outline_env: asyncpg.Pool,
) -> None:
    """Structured-output failure after real adapter call → terminal + 1 failed usage."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Adapter invalid path."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_prof",
    )
    policy = SemanticOutlineExecutionPolicy.for_tests(
        generation_enabled=True, max_output_tokens=777
    )
    gen = PydanticAISemanticOutlineGenerator(settings=settings, policy=policy)

    fake_model_config = SimpleNamespace(
        profile_name="outline_prof",
        provider="mock",
        model_name="mock-outline-model",
        model_settings=None,
        api_key="",  # avoid real-llm block requiring key
    )
    bad_result = SimpleNamespace(output={"not": "candidates"})

    captured_run_kwargs: dict = {}

    async def _fake_run(agent, prompt, *, model_settings=None, **kwargs):
        # Prove output cap reached the agent-run seam.
        captured_run_kwargs["model_settings"] = model_settings
        captured_run_kwargs["kwargs"] = kwargs
        return bad_result

    with (
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.build_model_for_route",
            return_value=("fake-model", fake_model_config),
        ),
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.assert_real_llm_allowed"
        ),
        patch.object(gen, "_build_agent", return_value=MagicMock()),
        patch.object(gen, "_run_agent", side_effect=_fake_run),
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.extract_run_usage",
            return_value=_USAGE,
        ),
    ):
        worker = SemanticOutlineWorkerService(pool=outline_env, generator=gen)
        result = await worker.process_next_semantic_outline_job(
            lease_owner="t58b-adapter-invalid",
            lease_duration=timedelta(seconds=30),
        )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "model_output_invalid"
    assert gen.last_model_settings is not None
    assert gen.last_model_settings.max_tokens == 777
    # Cap must be present on the agent-run call (dict form of ModelSettings).
    ms = captured_run_kwargs.get("model_settings")
    assert ms is not None
    if isinstance(ms, dict):
        assert ms.get("max_tokens") == 777
    else:
        assert getattr(ms, "max_tokens", None) == 777
    async with outline_env.acquire() as conn:
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
        job = await conn.fetchrow(
            "SELECT status FROM reader_jobs WHERE id = $1", boot.job_id
        )
        run = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1", boot.run_id
        )
    assert int(layers) == 0
    assert job["status"] == "failed_terminal"
    assert run["status"] == "failed_terminal"
    assert run["finished_at"] is not None
    assert await _count_usage(outline_env, job_id=boot.job_id) == 1
    row = await _fetch_usage_row(outline_env, job_id=boot.job_id)
    assert row is not None
    assert row["status"] == "failed"


async def test_t58b_adapter_unexpected_model_behavior_maps_to_model_output_invalid(
    outline_env: asyncpg.Pool,
) -> None:
    """PydanticAI raises UnexpectedModelBehavior inside agent.run for bad output.

    Must map to model_output_invalid (not generic provider), terminal job/run,
    zero layer, exactly one failed usage event even without token payload.
    """
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="UnexpectedModelBehavior path."
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_prof",
    )
    policy = SemanticOutlineExecutionPolicy.for_tests(
        generation_enabled=True, max_output_tokens=256
    )
    gen = PydanticAISemanticOutlineGenerator(settings=settings, policy=policy)
    fake_model_config = SimpleNamespace(
        profile_name="outline_prof",
        provider="mock",
        model_name="mock-outline-model",
        model_settings=None,
        api_key="",
    )

    async def _raise_structured_failure(agent, prompt, *, model_settings=None, **kwargs):
        raise UnexpectedModelBehavior("Exceeded maximum output retries (0)")

    with (
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.build_model_for_route",
            return_value=("fake-model", fake_model_config),
        ),
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.assert_real_llm_allowed"
        ),
        patch.object(gen, "_build_agent", return_value=MagicMock()),
        patch.object(gen, "_run_agent", side_effect=_raise_structured_failure),
        patch(
            "app.services.reader_orchestration.semantic_outline_executor.extract_run_usage",
            return_value=None,  # no token payload
        ),
    ):
        worker = SemanticOutlineWorkerService(pool=outline_env, generator=gen)
        result = await worker.process_next_semantic_outline_job(
            lease_owner="t58b-umb",
            lease_duration=timedelta(seconds=30),
        )

    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "model_output_invalid"
    async with outline_env.acquire() as conn:
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
        job = await conn.fetchrow(
            "SELECT status, failure_class, failure_code FROM reader_jobs WHERE id = $1",
            boot.job_id,
        )
        run = await conn.fetchrow(
            "SELECT status, finished_at FROM reader_runs WHERE id = $1",
            boot.run_id,
        )
    assert int(layers) == 0
    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "validation"
    assert job["failure_code"] == "model_output_invalid"
    assert run["status"] == "failed_terminal"
    assert run["finished_at"] is not None
    assert await _count_usage(outline_env, job_id=boot.job_id) == 1
    row = await _fetch_usage_row(outline_env, job_id=boot.job_id)
    assert row is not None
    assert row["status"] == "failed"


async def test_t58b_real_adapter_policy_blocks_before_model() -> None:
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="x",
    )
    gen = PydanticAISemanticOutlineGenerator(settings=settings)
    ctx = MagicMock()
    ctx.worker_input = SimpleNamespace(
        base_id="b",
        generation=1,
        units=(),
        anchors=(),
        total_preview_chars=0,
    )
    # Minimal real JobContext-like object
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineJobContext,
        SemanticOutlineWorkerInput,
    )

    context = SemanticOutlineJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        expected_generation=1,
        operation_fingerprint="fp",
        attempt_count=1,
        max_attempts=3,
        worker_input=SemanticOutlineWorkerInput(
            base_id="b",
            generation=1,
            units=(),
            anchors=(),
            total_preview_chars=0,
        ),
    )
    with patch(
        "app.services.reader_orchestration.semantic_outline_executor.build_model_for_route"
    ) as build:
        with pytest.raises(SemanticOutlineGenerationError) as exc:
            await gen.generate(context)
        assert exc.value.failure_code == "semantic_outline_generation_disabled"
        build.assert_not_called()


async def test_t58b_outline_candidates_output_forbids_node_id() -> None:
    with pytest.raises(ValidationError):
        OutlineCandidatesOutput.model_validate(
            {
                "candidates": [
                    {
                        "candidate_ref": "c1",
                        "parent_candidate_ref": None,
                        "depth": 1,
                        "title": "T",
                        "start_unit_id": "u1",
                        "end_unit_id": "u1",
                        "node_id": "must-fail",
                    }
                ]
            }
        )
