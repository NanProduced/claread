"""A6 — Semantic Outline content-sufficiency short-circuit.

D3 decision: when the stable document already carries enough Markdown
headings, the backend MUST skip semantic outline job creation. The
existing ``activation_ready = generation_enabled AND profile_configured``
predicate is unchanged; this adds a content-sufficiency short-circuit
ON TOP of activation:

- ``activation_ready`` False → return False (no change).
- ``activation_ready`` True + heading count ≥ threshold → return False
  (skip job, record ``skipped_markdown_headings_sufficient`` diagnostic).
- ``activation_ready`` True + heading count < threshold → return True
  (existing behavior).
- ``state.unit_types`` None (not loaded) → fail-closed to existing
  behavior (return ``activation_ready`` only); no skip.

Tests use only fakes and DI — no real provider calls. Integration tests
use the existing ``outline_env`` Postgres harness (no LLM).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.database import connection as db_connection
from app.services.reader_orchestration.job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    EnhancementJobBootstrapService,
    settings_aware_semantic_outline_request_eligibility,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


_DEV_ACTIVATION_SETTINGS = Settings(
    semantic_outline_generation_enabled=True,
    reader_semantic_outline_model_profile="outline_profile",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    schema_name = f"test_a6_outline_{uuid4().hex}"
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
# Stub state for unit tests (no DB)
# ---------------------------------------------------------------------------


@dataclass
class _StubState:
    """Minimal stub for the eligibility predicate.

    The settings-aware predicate inspects ``activation_ready`` (captured at
    factory time), ``unit_types`` (A6 content-sufficiency short-circuit),
    and ``record_id`` / ``base_id`` (for the structured diagnostic log).
    """

    unit_types: tuple[str, ...] | None = None
    record_id: UUID | None = None
    base_id: UUID | None = None


# ---------------------------------------------------------------------------
# A. Unit tests — predicate logic (no DB)
# ---------------------------------------------------------------------------


def test_a6_unit_a1_activation_disabled_returns_false() -> None:
    """generation_enabled=False → predicate returns False regardless of headings."""
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_profile",
    )
    predicate = settings_aware_semantic_outline_request_eligibility(settings)
    # Plenty of headings, but activation is off → False.
    assert predicate(_StubState(unit_types=("heading",) * 5)) is False


def test_a6_unit_a2_empty_profile_returns_false() -> None:
    """profile="" → predicate returns False regardless of headings."""
    settings = Settings(
        semantic_outline_generation_enabled=True,
        reader_semantic_outline_model_profile="",
    )
    predicate = settings_aware_semantic_outline_request_eligibility(settings)
    assert predicate(_StubState(unit_types=("heading",) * 5)) is False


def test_a6_unit_types_none_fail_closed_returns_true() -> None:
    """activation_ready=True + unit_types=None → fail-closed to True (no skip).

    When unit_types is not loaded (e.g., a code path that did not pre-load
    units), the predicate MUST NOT skip. This preserves existing behavior.
    """
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert predicate(_StubState(unit_types=None)) is True


def test_a6_empty_unit_types_returns_true() -> None:
    """activation_ready=True + zero units → True (no skip; no headings)."""
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert predicate(_StubState(unit_types=())) is True


def test_a6_fewer_than_threshold_headings_returns_true() -> None:
    """activation_ready=True + 1 heading (< threshold 2) → True (no skip)."""
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert predicate(_StubState(unit_types=("heading", "body", "body"))) is True


def test_a6_unit_a6_threshold_headings_returns_false() -> None:
    """activation_ready=True + exactly 2 headings (≥ threshold) → False (skip)."""
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert (
        predicate(_StubState(unit_types=("heading", "body", "heading"))) is False
    )


def test_a6_unit_a7_more_than_threshold_headings_returns_false() -> None:
    """activation_ready=True + >2 headings → False (skip)."""
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert (
        predicate(
            _StubState(
                unit_types=("heading", "heading", "body", "heading", "list")
            )
        )
        is False
    )


def test_a6_unit_a8_non_heading_units_do_not_trigger_skip() -> None:
    """activation_ready=True + many body/list/quote units + 1 heading → True."""
    predicate = settings_aware_semantic_outline_request_eligibility(
        _DEV_ACTIVATION_SETTINGS
    )
    assert (
        predicate(
            _StubState(
                unit_types=("body", "list", "quote", "heading", "body", "body")
            )
        )
        is True
    )


# ---------------------------------------------------------------------------
# B. Integration tests — DB-backed bootstrap (no LLM)
# ---------------------------------------------------------------------------


async def test_a6_int_b1_article_with_sufficient_headings_skips_job(
    outline_env: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Article with ≥2 heading-classified units → no outline job + diagnostic log.

    The plain text ``"Heading One\\n\\nHeading Two\\n\\nBody content here."``
    produces two short, punctuation-free lines that the heuristic
    ``_classify_unit_type`` classifies as ``heading``. The third paragraph
    ends with a period → ``body``. With ≥2 headings, A6 must short-circuit
    semantic outline job creation.
    """
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Heading One\n\nHeading Two\n\nBody content here.",
    )
    # Sanity: confirm the article produced ≥2 heading units.
    async with outline_env.acquire() as conn:
        heading_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reading_units
            WHERE reading_record_id = $1 AND unit_type = 'heading'
            """,
            article.record_id,
        )
    assert heading_count >= 2, (
        f"expected ≥2 heading units for A6 skip, got {heading_count}"
    )

    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(_DEV_ACTIVATION_SETTINGS)
        ),
    )
    caplog.set_level(logging.INFO, logger="app.services.reader_orchestration.job_bootstrap")
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
    # Diagnostic must be recorded.
    diagnostic_messages = [
        r.getMessage()
        for r in caplog.records
        if "skipped_markdown_headings_sufficient" in r.getMessage()
    ]
    assert diagnostic_messages, (
        "expected skipped_markdown_headings_sufficient diagnostic log; "
        f"got records: {[r.getMessage() for r in caplog.records]}"
    )


async def test_a6_integration_article_without_sufficient_headings_creates_job(
    outline_env: asyncpg.Pool,
) -> None:
    """Article with <2 heading units → outline job created (regression).

    The default ``submit_article_ready`` plain text produces body units
    (sentences ending with periods), so heading count < 2. A6 must NOT
    short-circuit; the existing activation path creates exactly one job.
    """
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    # Sanity: confirm <2 heading units.
    async with outline_env.acquire() as conn:
        heading_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reading_units
            WHERE reading_record_id = $1 AND unit_type = 'heading'
            """,
            article.record_id,
        )
    assert heading_count < 2, (
        f"expected <2 heading units for regression, got {heading_count}"
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
    assert result is not None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert job_count == 1


async def test_a6_int_b3_skip_does_not_create_diagnostic_when_activation_off(
    outline_env: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """activation_ready=False + ≥2 headings → no job, no A6 diagnostic.

    The skip must ONLY record ``skipped_markdown_headings_sufficient`` when
    activation is ready but content is sufficient. When activation itself
    is off, the existing always-false path applies — no A6 diagnostic.
    """
    settings = Settings(
        semantic_outline_generation_enabled=False,
        reader_semantic_outline_model_profile="outline_profile",
    )
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Heading One\n\nHeading Two\n\nBody content here.",
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=(
            settings_aware_semantic_outline_request_eligibility(settings)
        ),
    )
    caplog.set_level(logging.INFO, logger="app.services.reader_orchestration.job_bootstrap")
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
    # No A6 diagnostic when activation is off.
    diagnostic_messages = [
        r.getMessage()
        for r in caplog.records
        if "skipped_markdown_headings_sufficient" in r.getMessage()
    ]
    assert not diagnostic_messages, (
        "A6 diagnostic must not fire when activation_ready=False; got: "
        f"{diagnostic_messages}"
    )
