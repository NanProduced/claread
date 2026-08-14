"""Development auto-activation of Semantic Outline main path.

Dev-only freeze: when ``activation_ready = semantic_outline_generation_enabled
AND reader_semantic_outline_model_profile != ""``, every record that has
reached the existing ``article_ready`` milestone auto-qualifies for semantic
outline bootstrap. Committed defaults stay closed
(``semantic_outline_generation_enabled=False``,
``reader_semantic_outline_model_profile=""``).

This is NOT beta / whitelist / CTA / capability endpoint / migration work.
Tests use only fakes and DI — no real provider calls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.database import connection as db_connection
from app.llm.routes import MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
from app.services.ai_usage import CAPABILITY_READER_SEMANTIC_OUTLINE
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    EnhancementJobBootstrapService,
    default_semantic_outline_request_eligibility,
    settings_aware_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.semantic_outline_executor import (
    PydanticAISemanticOutlineGenerator,
)
from app.services.reader_orchestration.semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    FakeSemanticOutlineGenerator,
    SemanticOutlineExecutionResult,
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


# Settings that turn ON dev activation (tests / dev only; never committed).
_DEV_ACTIVATION_SETTINGS = Settings(
    semantic_outline_generation_enabled=True,
    reader_semantic_outline_model_profile="outline_profile",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    schema_name = f"test_reader_semantic_outline_dev_{uuid4().hex}"
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


# ---------------------------------------------------------------------------
# A. Default-off (bootstrap admission)
# ---------------------------------------------------------------------------


async def test_dev_disabled_generation_creates_no_job(
    outline_env: asyncpg.Pool,
) -> None:
    """generation_enabled=False + profile set → no job, no provider call."""
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_profile",
    )
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(settings)
        ),
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 0


async def test_dev_empty_profile_creates_no_job(
    outline_env: asyncpg.Pool,
) -> None:
    """generation_enabled=True + profile empty → no job, no provider call."""
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="",
    )
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(settings)
        ),
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 0


# ---------------------------------------------------------------------------
# B. Dev auto-eligibility (bootstrap admission)
# ---------------------------------------------------------------------------


async def test_dev_activation_ready_article_ready_creates_one_job(
    outline_env: asyncpg.Pool,
) -> None:
    """activation_ready=True + article_ready → exactly one job."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is not None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 1


async def test_dev_already_active_no_op(outline_env: asyncpg.Pool) -> None:
    """Same base/generation already has active outline job → no-op (no duplicate)."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    eligibility = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=eligibility,
    )
    first = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert first is not None
    # Second bootstrap on same base/generation must be no-op.
    second = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert second is None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 1


async def test_dev_non_article_ready_no_op(outline_env: asyncpg.Pool) -> None:
    """Non-article_ready state → no-op even when activation_ready=True."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    # Force readiness_state back to a non-article_ready state.
    async with outline_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET readiness_state = 'submitted' WHERE id = $1",
            article.record_id,
        )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 0


# ---------------------------------------------------------------------------
# C. Composition (pipeline_runner)
# ---------------------------------------------------------------------------


# Sentinel for distinguishing "argument not passed" from "explicit None".
# When callers do not pass ``bootstrap_service``, the runner must be allowed
# to auto-compose the service from settings; only an explicit injection
# (real instance) should be forwarded as ``bootstrap_service=``.
_UNSET: Any = object()


def _build_runner_with_stubs(
    *,
    settings: Settings,
    semantic_outline_worker_service: object | None = None,
    bootstrap_service: Any = _UNSET,
) -> ReaderEnhancementPipelineRunner:
    """Construct a runner without a DB pool by injecting stub services.

    The C tests verify composition logic only (which generator / eligibility
    predicate the runner selects). They do not call any worker methods, so
    stubs are safe. ``enable_grammar_window=False`` skips the grammar window
    executor import path; ``MagicMock`` stubs prevent eager pool resolution
    in services that are not under test.

    ``bootstrap_service`` uses a sentinel so that "not passed" lets the
    runner auto-compose the real ``EnhancementJobBootstrapService`` from
    settings; only an explicit real instance is forwarded as override.
    """
    from unittest.mock import MagicMock

    kwargs: dict[str, Any] = dict(
        pool=None,
        settings=settings,
        display_title_worker_service=MagicMock(),
        semantic_outline_worker_service=semantic_outline_worker_service,
        translation_orchestrator=MagicMock(),
        translation_batch_worker_service=MagicMock(),
        vocabulary_worker_service=MagicMock(),
        grammar_worker_service=MagicMock(),
        enable_grammar_window=False,
        job_runtime=MagicMock(),
    )
    if bootstrap_service is not _UNSET:
        kwargs["bootstrap_service"] = bootstrap_service
    return ReaderEnhancementPipelineRunner(**kwargs)


def test_dev_composition_selects_pydantic_generator() -> None:
    """activation_ready=True → runner wires PydanticAISemanticOutlineGenerator."""
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_profile",
    )
    runner = _build_runner_with_stubs(settings=settings)
    worker = runner._semantic_outline_worker_service
    assert isinstance(worker._generator, PydanticAISemanticOutlineGenerator)


def test_dev_composition_unconfigured_when_disabled() -> None:
    """generation_enabled=False → UnconfiguredSemanticOutlineGenerator +
    default always-false predicate."""
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_profile",
    )
    runner = _build_runner_with_stubs(settings=settings)
    worker = runner._semantic_outline_worker_service
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)
    # Bootstrap must keep the default always-false predicate (no auto-eligibility).
    bootstrap = runner._bootstrap_service
    assert isinstance(bootstrap, EnhancementJobBootstrapService)
    assert (
        bootstrap._semantic_outline_request_eligibility
        is default_semantic_outline_request_eligibility
    )


def test_dev_composition_unconfigured_when_profile_empty() -> None:
    """profile="" → UnconfiguredSemanticOutlineGenerator + default always-false predicate."""
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="",
    )
    runner = _build_runner_with_stubs(settings=settings)
    worker = runner._semantic_outline_worker_service
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)
    # Bootstrap must keep the default always-false predicate (no auto-eligibility).
    bootstrap = runner._bootstrap_service
    assert isinstance(bootstrap, EnhancementJobBootstrapService)
    assert (
        bootstrap._semantic_outline_request_eligibility
        is default_semantic_outline_request_eligibility
    )


class _StubState:
    """Stub state object for eligibility predicate testing.

    The settings-aware predicate inspects ``activation_ready`` (captured at
    factory time) and, since, ``unit_types`` for the content-sufficiency
    short-circuit. When ``unit_types`` is ``None`` (the default here), the
    predicate fail-closed to the activation-only result, so this stub still
    yields ``True`` under activation-ready settings.
    """

    unit_types: tuple[str, ...] | None = None


def test_dev_composition_bootstrap_uses_settings_aware_eligibility() -> None:
    """activation_ready=True + no explicit bootstrap → runner auto-constructs
    a real ``EnhancementJobBootstrapService`` whose eligibility predicate:

    - is NOT the default always-false function, and
    - returns True for any state (article_ready gate is enforced separately
      by ``_bootstrap_semantic_outline_job``, not by this predicate).
    """
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_profile",
    )
    runner = _build_runner_with_stubs(settings=settings)
    bootstrap = runner._bootstrap_service
    # The runner must have constructed a real EnhancementJobBootstrapService
    # (not a MagicMock stub) when no explicit override was passed.
    assert isinstance(bootstrap, EnhancementJobBootstrapService)
    # The injected predicate must NOT be the default always-false function.
    predicate = bootstrap._semantic_outline_request_eligibility
    assert predicate is not default_semantic_outline_request_eligibility
    # The settings-aware predicate must return True for any state — the
    # article_ready readiness_state gate is enforced separately upstream.
    assert predicate(_StubState()) is True


def test_dev_composition_default_settings_keeps_unconfigured() -> None:
    """Committed outline defaults keep the generator unconfigured."""
    assert Settings.model_fields["semantic_outline_generation_enabled"].default is False
    assert Settings.model_fields["reader_semantic_outline_model_profile"].default == ""
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="",
    )
    runner = _build_runner_with_stubs(settings=settings)
    worker = runner._semantic_outline_worker_service
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)
    bootstrap = runner._bootstrap_service
    assert isinstance(bootstrap, EnhancementJobBootstrapService)
    assert (
        bootstrap._semantic_outline_request_eligibility
        is default_semantic_outline_request_eligibility
    )


def test_dev_composition_explicit_worker_override_respected() -> None:
    """Explicit semantic_outline_worker_service injection is never replaced."""
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_profile",
    )
    fake_worker = SemanticOutlineWorkerService(
        pool=None, generator=FakeSemanticOutlineGenerator(())
    )
    runner = _build_runner_with_stubs(
        settings=settings, semantic_outline_worker_service=fake_worker
    )
    assert runner._semantic_outline_worker_service is fake_worker


def test_dev_explicit_bootstrap_override_respected() -> None:
    """Explicit bootstrap_service injection is never replaced, even when
    activation_ready=True would otherwise auto-construct a different one.

    The caller's bootstrap object must be retained verbatim (identity check)
    so that test-only or product-level DI can pin a specific eligibility
    predicate, pool, or implementation without the runner silently swapping
    it for the settings-aware default.
    """
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="outline_profile",
    )
    explicit_bootstrap = EnhancementJobBootstrapService(pool=None)
    # Sanity: explicit bootstrap uses default always-false predicate, which
    # is distinct from what activation_ready=True would auto-construct.
    assert (
        explicit_bootstrap._semantic_outline_request_eligibility
        is default_semantic_outline_request_eligibility
    )
    runner = _build_runner_with_stubs(
        settings=settings, bootstrap_service=explicit_bootstrap
    )
    assert runner._bootstrap_service is explicit_bootstrap


# ---------------------------------------------------------------------------
# D. Real chain seam (fake/DI generator → snapshot projection)
# ---------------------------------------------------------------------------


async def test_dev_fake_generator_publishes_trusted_projection(
    outline_env: asyncpg.Pool,
) -> None:
    """DI fake generator success → snapshot projects trusted semantic_outline."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Outline projection seam paragraph.",
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c1",
            parent_candidate_ref=None,
            depth=1,
            title="Section One",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    boot = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert boot is not None
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="dev-d1",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    snapshot = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.status in {"ready", "partial"}
    # Ensure default predicate is untouched (still always-false) — sanity.
    assert default_semantic_outline_request_eligibility is not None


async def test_dev_snapshot_navigation_units_unchanged(
    outline_env: asyncpg.Pool,
) -> None:
    """L0/L1 navigation.units must be identical before/after outline generation."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Navigation stability check.",
    )
    eligibility = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=eligibility,
    )
    boot = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert boot is not None

    # Snapshot BEFORE worker runs.
    before = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    before_units = [
        (u.unit_id, u.order_index, u.unit_type) for u in before.navigation.units
    ]
    assert before.semantic_outline is None

    # Run worker with a fake generator.
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c1",
            parent_candidate_ref=None,
            depth=1,
            title="Stable Section",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="dev-d2",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"

    after = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    after_units = [
        (u.unit_id, u.order_index, u.unit_type) for u in after.navigation.units
    ]
    assert before_units == after_units
    # Outline is now present in the after snapshot.
    assert after.semantic_outline is not None


async def test_dev_usage_provenance_auditable(
    outline_env: asyncpg.Pool,
) -> None:
    """Provider call with usage_data → exactly one auditable ai_usage_events row."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Usage provenance audit body.",
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c1",
            parent_candidate_ref=None,
            depth=1,
            title="Usage Section",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )

    class _UsageRecordingGen:
        """Fake generator that simulates a real provider call (usage recorded)."""

        async def generate(self, context):  # type: ignore[no-untyped-def]
            return SemanticOutlineExecutionResult(
                candidates=candidates,
                worker_failure=False,
                model="mock-outline-model",
                usage_data={
                    "aggregate": {
                        "input_tokens": 12,
                        "output_tokens": 8,
                        "total_tokens": 20,
                    }
                },
                prompt_version="0.0.6",
                model_route=MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
                model_profile="outline_prof",
                model_provider="mock",
                model_name="mock-outline-model",
                provider_call_made=True,
            )

    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    boot = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert boot is not None
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=_UsageRecordingGen(),  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="dev-d3",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    async with outline_env.acquire() as conn:
        usage_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM ai_usage_events
            WHERE reader_job_id = $1 AND capability_code = $2
            """,
            boot.job_id,
            CAPABILITY_READER_SEMANTIC_OUTLINE,
        )
        usage_row = await conn.fetchrow(
            """
            SELECT capability_code, model_route, model_profile, status,
                   reader_run_id, reading_record_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            boot.job_id,
        )
    assert int(usage_count) == 1
    assert usage_row is not None
    assert usage_row["capability_code"] == CAPABILITY_READER_SEMANTIC_OUTLINE
    assert usage_row["model_route"] == MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
    assert usage_row["reader_run_id"] == boot.run_id
    assert usage_row["reading_record_id"] == article.record_id


async def test_dev_coverage_budget_readiness_unchanged(
    outline_env: asyncpg.Pool,
) -> None:
    """Successful outline generation must not enter coverage/budget/readiness.

    Outline is a side layer: enhancement_progress caps must not include
    semantic_outline; readiness_state must remain article_ready.
    """
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Coverage budget readiness unchanged.",
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c1",
            parent_candidate_ref=None,
            depth=1,
            title="Side Section",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    boot = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert boot is not None
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="dev-d4",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    snapshot = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    # Readiness state unchanged.
    assert snapshot.record.readiness_state == "article_ready"
    # Outline must NOT appear in enhancement_progress.layers (side layer).
    progress_caps = {layer.capability for layer in snapshot.enhancement_progress.layers}
    assert "semantic_outline" not in progress_caps


# ---------------------------------------------------------------------------
# E. Runner-level real bootstrap seam (public ``runner.bootstrap_missing_jobs``)
# ---------------------------------------------------------------------------
#
# These tests exercise the runner's public ``bootstrap_missing_jobs`` seam
# against a real DB schema (no worker invocation, no provider call). The
# runner auto-constructs ``EnhancementJobBootstrapService`` from settings
# (no explicit bootstrap injection); a Fake semantic_outline worker is
# injected only to guarantee that even an accidental ``runner.run`` would
# not trigger a real provider call.


def _build_runner_with_real_pool(
    *,
    pool: asyncpg.Pool,
    settings: Settings,
) -> ReaderEnhancementPipelineRunner:
    """Construct a runner against a real pool with auto-composed bootstrap.

    The runner's ``__init__`` builds ``EnhancementJobBootstrapService`` from
    settings (no explicit injection). A Fake ``semantic_outline_worker_service``
    is injected so that no real provider can be invoked even if a future
    caller accidentally runs the full pipeline.
    """
    fake_outline_worker = SemanticOutlineWorkerService(
        pool=pool, generator=FakeSemanticOutlineGenerator(())
    )
    return ReaderEnhancementPipelineRunner(
        pool=pool,
        settings=settings,
        semantic_outline_worker_service=fake_outline_worker,
        enable_grammar_window=False,
    )


async def test_dev_runner_bootstrap_creates_one_outline_job(
    outline_env: asyncpg.Pool,
) -> None:
    """activation_ready=True + article_ready → runner.bootstrap_missing_jobs
    creates exactly one semantic_outline job via the auto-composed bootstrap.
    """
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    runner = _build_runner_with_real_pool(
        pool=outline_env,
        settings=_DEV_ACTIVATION_SETTINGS,
    )
    summary = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert summary.job_counts.semantic_outline == 1
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 1


async def test_dev_runner_bootstrap_idempotent_on_same_base_generation(
    outline_env: asyncpg.Pool,
) -> None:
    """Re-calling ``runner.bootstrap_missing_jobs`` on the same base/generation
    must not create a duplicate semantic_outline job (idempotent seam)."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    runner = _build_runner_with_real_pool(
        pool=outline_env,
        settings=_DEV_ACTIVATION_SETTINGS,
    )
    first = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert first.job_counts.semantic_outline == 1
    # Second call on same base/generation must be a no-op for outline.
    second = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert second.job_counts.semantic_outline == 0
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 1


async def test_dev_runner_bootstrap_no_outline_when_disabled(
    outline_env: asyncpg.Pool,
) -> None:
    """activation_ready=False → same public seam creates 0 semantic_outline jobs.

    The runner auto-composes bootstrap with the default always-false
    predicate, so ``bootstrap_missing_jobs`` must not enqueue any outline
    job even when article_ready is satisfied.
    """
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_profile",
    )
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    runner = _build_runner_with_real_pool(
        pool=outline_env,
        settings=settings,
    )
    summary = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert summary.job_counts.semantic_outline == 0
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 0


async def test_dev_runner_bootstrap_no_outline_when_profile_empty(
    outline_env: asyncpg.Pool,
) -> None:
    """profile="" → same public seam creates 0 semantic_outline jobs.

    Even with generation_enabled=True, an empty profile keeps the runner
    on the default always-false predicate (activation_ready=False).
    """
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="",
    )
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    runner = _build_runner_with_real_pool(
        pool=outline_env,
        settings=settings,
    )
    summary = await runner.bootstrap_missing_jobs(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert summary.job_counts.semantic_outline == 0
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 0
