"""P2B-R1.1 — Z+ Strict Fence Rollback Closure.

Proves ``ZPlusBootstrapService.bootstrap_grammar_window_plan()`` delegates
semantic fence construction to the single shared strict builder
(``generation_semantic_fence_from_targets``). When target units inside the
same Z+ window carry mixed contract or resolver versions, the shared
builder raises ``SemanticFenceConstructionError`` and the outer PostgreSQL
transaction rolls back completely — no ``layer_analysis_plans``,
``analysis_windows``, ``reader_runs``, or ``reader_jobs`` half-row
survives.

These are real PostgreSQL regression tests using the public
``bootstrap_grammar_window_plan`` entry point (no mock transaction, no
direct ``_create_window_reader_job`` call). No real LLM is invoked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

import app.config.settings as settings_mod
from app.config.settings import Settings, get_settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    SEMANTIC_FENCE_INCONSISTENT_CODE,
    AutomaticLayerPolicy,
    SemanticFenceConstructionError,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1
from app.services.reader_orchestration.zplus_bootstrap import ZPlusBootstrapService
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


# Two-paragraph prose so the article produces at least one grammar-eligible
# unit that the Z+ window planner can pack into a window. Both paragraphs
# stay grammar-on under enforce so the unit survives the
# ``filter_units_for_any_grammar`` pre-filter and reaches the shared fence
# builder inside ``_create_window_reader_job``.
_ZPLUS_ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the "
    "timeline so that everyone understood the tradeoff before the next "
    "quarterly review began.\n\n"
    "The committee, which had spent six months reviewing export data, "
    "labor surveys, and municipal tax receipts that rarely lined up neatly, "
    "claimed that the recovery was broad enough to justify ending the "
    "emergency grant program without further extension."
)


@contextmanager
def _policy_mode(mode: str) -> Iterator[None]:
    """Temporarily set ``reader_automatic_layer_policy_mode``.

    Z+ bootstrap reads mode via ``get_automatic_layer_policy_mode()`` which
    consults the live settings object; tests must freeze the mode for the
    duration of the bootstrap call so the grammar-allowed units are not
    dropped by the pre-filter under ``enforce``.
    """
    get_settings.cache_clear()
    settings = Settings(reader_automatic_layer_policy_mode=mode)  # type: ignore[arg-type]
    original = settings_mod.get_settings

    def _fake() -> Settings:
        return settings

    settings_mod.get_settings = _fake  # type: ignore[assignment]
    try:
        yield
    finally:
        settings_mod.get_settings = original  # type: ignore[assignment]
        get_settings.cache_clear()


@pytest.fixture
async def zplus_fence_env() -> AsyncIterator[asyncpg.Pool]:
    """Schema with baseline + 0015/0017/0020 migrations for Z+ bootstrap."""
    schema_name = f"test_p2b_r1_1_zplus_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        await pool.close()
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _table_count(
    conn: asyncpg.Connection,
    table: str,
    *,
    record_id: UUID,
) -> int:
    """Count rows for a record across the four Z+ persistence tables."""
    if table == "layer_analysis_plans":
        row = await conn.fetchval(
            "SELECT count(*)::int FROM layer_analysis_plans "
            "WHERE reading_record_id = $1",
            record_id,
        )
    elif table == "analysis_windows":
        row = await conn.fetchval(
            "SELECT count(*)::int FROM analysis_windows aw "
            "JOIN layer_analysis_plans p ON p.id = aw.plan_id "
            "WHERE p.reading_record_id = $1",
            record_id,
        )
    elif table == "reader_runs":
        row = await conn.fetchval(
            "SELECT count(*)::int FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )
    elif table == "reader_jobs":
        row = await conn.fetchval(
            "SELECT count(*)::int FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unknown table {table!r}")
    assert isinstance(row, int)
    return row


async def _assert_zero_delta_on_all_four_tables(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    before: dict[str, int],
) -> None:
    """Assert zero delta on plan / window / run / job tables."""
    after: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table in ("layer_analysis_plans", "analysis_windows",
                      "reader_runs", "reader_jobs"):
            after[table] = await _table_count(conn, table, record_id=record_id)
    for table in before:
        delta = after[table] - before[table]
        assert delta == 0, (
            f"expected zero new {table} rows after Z+ mixed-fence rollback, "
            f"before={before[table]} after={after[table]} delta={delta}"
        )


async def _record_four_table_counts(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> dict[str, int]:
    """Snapshot the four Z+ persistence tables before a bootstrap call."""
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table in ("layer_analysis_plans", "analysis_windows",
                      "reader_runs", "reader_jobs"):
            counts[table] = await _table_count(conn, table, record_id=record_id)
    return counts


async def _set_unit_semantic(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    unit_id: str,
    contract_version: str = SEMANTIC_CONTRACT_V1,
    resolver_version: str = AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
) -> None:
    """Stamp a unit with full semantic metadata (grammar-on).

    Used to bring legacy units onto the semantic contract before tampering
    one of them, so the shared fence builder observes a real mixed-version
    target set rather than a legacy+semantic mix.
    """
    semantic: dict[str, Any] = {
        "contract_version": contract_version,
        "content_role": "prose",
        "resolver_version": resolver_version,
        "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_units
            SET metadata_json = COALESCE(metadata_json, '{}'::jsonb) || $3::jsonb
            WHERE reading_record_id = $1 AND unit_id = $2
            """,
            record_id,
            unit_id,
            jsonb_param({"semantic": semantic}),
        )


async def _tamper_unit_semantic(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    unit_id: str,
    contract_version: str | None = None,
    resolver_version: str | None = None,
) -> None:
    """Overwrite a unit's semantic contract_version or resolver_version.

    Keeps ``automatic_layer_policy`` grammar-on so the unit still passes the
    ``filter_units_for_any_grammar`` pre-filter and reaches the shared fence
    builder inside ``_create_window_reader_job``. This is what forces the
    shared builder to observe a real mixed-version target set.
    """
    semantic: dict[str, Any] = {
        "contract_version": (
            contract_version if contract_version is not None else SEMANTIC_CONTRACT_V1
        ),
        "content_role": "prose",
        "resolver_version": (
            resolver_version
            if resolver_version is not None
            else AUTOMATIC_LAYER_POLICY_RESOLVER_V1
        ),
        "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_units
            SET metadata_json = COALESCE(metadata_json, '{}'::jsonb) || $3::jsonb
            WHERE reading_record_id = $1 AND unit_id = $2
            """,
            record_id,
            unit_id,
            jsonb_param({"semantic": semantic}),
        )


# ---------------------------------------------------------------------------
# Real PostgreSQL rollback tests
# ---------------------------------------------------------------------------


async def test_zplus_bootstrap_mixed_contract_rolls_back_all_four_tables(
    zplus_fence_env: asyncpg.Pool,
) -> None:
    """Mixed contract_version across window target units → typed error + rollback.

    The shared strict fence builder raises ``SemanticFenceConstructionError``
    inside ``_create_window_reader_job`` after the ``layer_analysis_plans``
    INSERT has already executed inside the transaction. The outer
    ``async with conn.transaction()`` block must roll back every row: plan,
    window, run, job.
    """
    pool = zplus_fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_ZPLUS_ARTICLE_TEXT,
        title="Z+ Mixed Contract Rollback",
        language="en",
    )

    # Load unit ids so we can tamper one to a bogus contract version while
    # keeping grammar policy on (so it still reaches the fence builder).
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 "
            "ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(r["unit_id"]) for r in unit_rows]
    assert len(unit_ids) >= 2, (
        "test article must produce at least 2 units to create a mixed-fence "
        "window; got fewer"
    )

    # Bring both units onto the semantic contract first (uniform), then
    # tamper the second unit's contract_version to a different value.
    # Grammar policy stays all_on so both units are still kept by
    # ``filter_units_for_any_grammar`` and reach the shared fence
    # builder — the mismatch is observed there, not at the pre-filter.
    for uid in unit_ids:
        await _set_unit_semantic(
            pool, record_id=article.record_id, unit_id=uid
        )
    await _tamper_unit_semantic(
        pool,
        record_id=article.record_id,
        unit_id=unit_ids[1],
        contract_version="semantic_contract_v999_bogus",
    )

    before = await _record_four_table_counts(
        pool, record_id=article.record_id
    )

    service = ZPlusBootstrapService(pool=pool)
    with _policy_mode("enforce"):
        with pytest.raises(SemanticFenceConstructionError) as exc_info:
            await service.bootstrap_grammar_window_plan(
                record_id=article.record_id, base_id=article.base_id
            )
    assert exc_info.value.code == SEMANTIC_FENCE_INCONSISTENT_CODE

    await _assert_zero_delta_on_all_four_tables(
        pool, record_id=article.record_id, before=before
    )


async def test_zplus_bootstrap_mixed_resolver_rolls_back_all_four_tables(
    zplus_fence_env: asyncpg.Pool,
) -> None:
    """Mixed resolver_version across window target units → typed error + rollback.

    Same rollback contract as the mixed-contract case: the shared builder
    raises before any reader_runs / reader_jobs row can be committed, and
    the already-INSERTed ``layer_analysis_plans`` row is rolled back by the
    outer transaction.
    """
    pool = zplus_fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_ZPLUS_ARTICLE_TEXT,
        title="Z+ Mixed Resolver Rollback",
        language="en",
    )

    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 "
            "ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(r["unit_id"]) for r in unit_rows]
    assert len(unit_ids) >= 2

    # Bring both units onto the semantic contract first (uniform), then
    # tamper the second unit's resolver_version to a different value.
    # Grammar policy stays all_on so both units still pass the pre-filter
    # and reach the shared fence builder.
    for uid in unit_ids:
        await _set_unit_semantic(
            pool, record_id=article.record_id, unit_id=uid
        )
    await _tamper_unit_semantic(
        pool,
        record_id=article.record_id,
        unit_id=unit_ids[1],
        resolver_version="automatic_layer_policy_v999_bogus",
    )

    before = await _record_four_table_counts(
        pool, record_id=article.record_id
    )

    service = ZPlusBootstrapService(pool=pool)
    with _policy_mode("enforce"):
        with pytest.raises(SemanticFenceConstructionError) as exc_info:
            await service.bootstrap_grammar_window_plan(
                record_id=article.record_id, base_id=article.base_id
            )
    assert exc_info.value.code == SEMANTIC_FENCE_INCONSISTENT_CODE

    await _assert_zero_delta_on_all_four_tables(
        pool, record_id=article.record_id, before=before
    )


# ---------------------------------------------------------------------------
# Positive non-regression: uniform semantic Z+ bootstrap still succeeds
# ---------------------------------------------------------------------------


async def test_zplus_bootstrap_uniform_semantic_succeeds_non_regression(
    zplus_fence_env: asyncpg.Pool,
) -> None:
    """Uniform semantic contract/resolver on all units → bootstrap succeeds.

    Confirms the strict fence builder does not false-positive on uniform
    semantic data: the plan + windows + reader_jobs are committed and the
    fence fields are frozen on the persisted rows. This guards against a
    regression where the convergence accidentally rejects legitimate
    uniform-semantic Z+ windows.
    """
    pool = zplus_fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=_ZPLUS_ARTICLE_TEXT,
        title="Z+ Uniform Semantic Non-Regression",
        language="en",
    )

    # Bring all units onto the uniform semantic contract so the shared
    # fence builder produces a real semantic fence (not a legacy one) and
    # the bootstrap commits plan + windows + jobs.
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 "
            "ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(r["unit_id"]) for r in unit_rows]
    assert unit_ids
    for uid in unit_ids:
        await _set_unit_semantic(
            pool, record_id=article.record_id, unit_id=uid
        )

    before = await _record_four_table_counts(
        pool, record_id=article.record_id
    )

    service = ZPlusBootstrapService(pool=pool)
    with _policy_mode("enforce"):
        result = await service.bootstrap_grammar_window_plan(
            record_id=article.record_id, base_id=article.base_id
        )

    assert result.plan_id is not None
    assert isinstance(result.plan_id, UUID)
    assert len(result.windows) >= 1
    assert len(result.job_ids) == len(result.windows)
    assert len(result.job_ids) >= 1

    # The four persistence tables must have grown by the bootstrap output.
    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            result.plan_id,
        )
        assert plan is not None
        assert plan["status"] == "active"

        windows = await conn.fetch(
            "SELECT * FROM analysis_windows WHERE plan_id = $1 "
            "ORDER BY window_index",
            result.plan_id,
        )
        assert len(windows) == len(result.windows)

        for job_id in result.job_ids:
            job = await conn.fetchrow(
                "SELECT input_json FROM reader_jobs WHERE id = $1",
                job_id,
            )
            assert job is not None
            input_json = job["input_json"]
            if hasattr(input_json, "keys"):
                input_json = dict(input_json)
            # Fence fields frozen by the shared builder on the persisted row.
            assert (
                input_json.get("semantic_contract_version") == SEMANTIC_CONTRACT_V1
            )
            assert (
                input_json.get("automatic_layer_policy_resolver_version")
                == AUTOMATIC_LAYER_POLICY_RESOLVER_V1
            )
            assert input_json.get("automatic_layer_name") == "grammar_note"
            assert input_json.get("semantic_policy_mode") == "enforce"

    # Sanity: at least one new plan row and matching job rows were committed.
    after = await _record_four_table_counts(pool, record_id=article.record_id)
    assert after["layer_analysis_plans"] > before["layer_analysis_plans"]
    assert after["reader_jobs"] > before["reader_jobs"]
