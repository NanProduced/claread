"""T5: Reader Strategy Resolver integration into enhancement job bootstrap.

These tests verify that ``EnhancementJobBootstrapService.bootstrap_missing_jobs``
records strategy metadata (``reading_goal``, ``reading_variant``,
``strategy_version``, ``strategy_hash``, layer ``policy_hash``) on job
input/envelope JSON, and that the ``operation_fingerprint`` covers
``strategy_hash`` so that a policy text change does not silently reuse old
job output.

Scope:
    - Only the enhancement job bootstrap entry point
      (``EnhancementJobBootstrapService.bootstrap_missing_jobs``) and the
      shared display-title bootstrap path are exercised.
    - No real LLM is called; no worker prompt is executed.
    - ``reader_variants.yaml`` is never modified. Policy-hash-change tests
      monkeypatch the resolver in the ``job_bootstrap`` module instead.

Fail-closed contract:
    - ``academic`` / ``academic_general`` and cross-goal pairs are rejected
      by the resolver before any job is written.
    - Bootstrap propagates resolver errors; there is no silent fallback.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration import job_bootstrap
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
    TranslationJobBootstrapService,
    ArticleRoute,
    _LockedActiveBaseState,
    _fingerprint_matches_base,
    _load_article_route,
)
from app.services.reader_orchestration.reading_strategy import (
    READER_VARIANT_POLICY_VERSION,
    ReaderStrategyResolverError,
    ReaderVariantStrategy,
    resolve_reader_variant_strategy,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
)

# Migration 0015 adds ``layer_analysis_plans`` + ``analysis_windows`` tables.
# Required because ``bootstrap_missing_jobs`` now routes grammar bootstrap
# based on Z+ plan existence in ``layer_analysis_plans`` (Task C3).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_0015_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")

# T1.1 short-article batch path: migration 0017 adds the new batch job types
# and worker types to the CHECK constraints (see pipeline runner fixture).
_MIGRATION_0017_SQL = (
    _REPO_ROOT / "infra" / "migrations" / "0017_reader_jobs_batch_path_job_types.sql"
).read_text(encoding="utf-8")

_PLAIN_TEXT = (
    "First sentence for strategy bootstrap.\n\n"
    "Second paragraph for strategy bootstrap.\n\n"
    "Third paragraph for strategy bootstrap."
)

# T3.1: long text (>6000 chars) routes to the grouped/window
# ``translate_article`` path. Each paragraph becomes a unit; with
# ~2300 chars/unit and target=6000, 8 paragraphs yield ~3 windows.
_LONG_TEXT = "\n\n".join(
    [
        " ".join(
            f"Word{i} placeholder sentence for long-form strategy bootstrap."
            for i in range(40)
        )
        for _ in range(8)
    ]
)
assert len(_LONG_TEXT) > 6000

# T3.1 P2: very long text explicitly exceeding safety_max * 2
# (TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT * 2 = 20000) to guarantee
# the planner produces >= 2 windows.
_VERY_LONG_TEXT = "\n\n".join(
    [
        " ".join(
            f"Word{i} placeholder sentence for very long grouped translation test."
            for i in range(40)
        )
        for _ in range(16)
    ]
)
assert len(_VERY_LONG_TEXT) > 20000


@pytest.fixture
async def strategy_env() -> asyncpg.Pool:
    schema_name = f"test_reader_bootstrap_strategy_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.execute(_MIGRATION_0015_SQL)
    await admin.execute(_MIGRATION_0017_SQL)
    await admin.close()

    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


async def _submit_with_strategy(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    reading_goal: str,
    reading_variant: str,
    plain_text: str = _PLAIN_TEXT,
) -> UUID:
    """Submit a plain-text article with explicit strategy fields; return record_id."""
    service = ArticleReadyPersistenceService(pool=pool)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=plain_text,
            title="Strategy Bootstrap Slice",
            language="en",
            reading_goal=reading_goal,  # type: ignore[arg-type]
            reading_variant=reading_variant,  # type: ignore[arg-type]
        )
    )
    return result.record_id


async def _load_jobs(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                job_id,
                job_type,
                target_type,
                operation_fingerprint,
                input_hash,
                input_json,
                run_id
            FROM (
                SELECT
                    j.id AS job_id,
                    j.job_type,
                    j.target_type,
                    j.operation_fingerprint,
                    j.input_hash,
                    j.input_json,
                    j.run_id,
                    j.created_at
                FROM reader_jobs j
                WHERE j.reading_record_id = $1
                ORDER BY j.created_at ASC, j.id ASC
            ) sub
            """,
            record_id,
        )
    return [dict(row) for row in rows]


async def _load_run_envelope(
    pool: asyncpg.Pool,
    run_id: UUID,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT envelope_json FROM reader_runs WHERE id = $1",
            run_id,
        )
    assert row is not None
    return dict(row["envelope_json"])


def _strategy_keys() -> set[str]:
    return {
        "reading_goal",
        "reading_variant",
        "strategy_version",
        "strategy_hash",
        "layer_policy_hash",
    }


# ---------------------------------------------------------------------------#
# Strategy metadata in job input_json
# ---------------------------------------------------------------------------#


async def test_translation_job_input_contains_strategy_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    # T1.1: short articles route to the batch path (translate_article)
    # instead of per-unit (translate_unit). Accept either.
    translation_jobs = [
        j for j in jobs if j["job_type"] in ("translate_unit", "translate_article")
    ]
    assert len(translation_jobs) >= 1

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    expected_layer_hash = strategy.layers["translation"].policy_hash

    for job in translation_jobs:
        input_json = dict(job["input_json"])
        assert _strategy_keys().issubset(input_json.keys())
        assert input_json["reading_goal"] == "daily_reading"
        assert input_json["reading_variant"] == "intermediate_reading"
        assert input_json["strategy_version"] == READER_VARIANT_POLICY_VERSION
        assert input_json["strategy_hash"] == strategy.strategy_hash
        assert input_json["layer_policy_hash"] == expected_layer_hash


async def test_vocabulary_job_input_contains_strategy_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    # T1.1: short articles route to the batch path
    # (build_vocabulary_layer_article) instead of per-unit
    # (build_vocabulary_layer). Accept either.
    vocab_jobs = [
        j
        for j in jobs
        if j["job_type"]
        in ("build_vocabulary_layer", "build_vocabulary_layer_article")
    ]
    assert len(vocab_jobs) >= 1

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    expected_layer_hash = strategy.layers["vocabulary"].policy_hash

    for job in vocab_jobs:
        input_json = dict(job["input_json"])
        assert _strategy_keys().issubset(input_json.keys())
        assert input_json["layer_policy_hash"] == expected_layer_hash


async def test_grammar_job_input_contains_strategy_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    # 该测试校验 legacy ``build_grammar_bundle`` job 的策略元数据契约。
    # P1-1 之后 Z+ 成为默认路径，这里显式走 legacy 路径以保留原始测试意图。
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id, force_legacy_grammar=True
    )

    jobs = await _load_jobs(strategy_env, record_id)
    grammar_jobs = [j for j in jobs if j["job_type"] == "build_grammar_bundle"]
    assert len(grammar_jobs) >= 1

    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    expected_layer_hash = strategy.layers["grammar_bundle"].policy_hash

    for job in grammar_jobs:
        input_json = dict(job["input_json"])
        assert _strategy_keys().issubset(input_json.keys())
        assert input_json["layer_policy_hash"] == expected_layer_hash


async def test_display_title_job_input_has_null_layer_policy_hash(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    title_jobs = [j for j in jobs if j["job_type"] == "generate_display_title_zh"]
    assert len(title_jobs) == 1

    input_json = dict(title_jobs[0]["input_json"])
    assert _strategy_keys().issubset(input_json.keys())
    # display_title has no layer policy; layer_policy_hash must be None.
    assert input_json["layer_policy_hash"] is None
    assert input_json["strategy_hash"] is not None


async def test_run_envelope_contains_strategy_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    assert len(jobs) >= 1
    for job in jobs:
        envelope = await _load_run_envelope(strategy_env, job["run_id"])
        assert "strategy" in envelope
        strategy_block = dict(envelope["strategy"])
        assert _strategy_keys().issubset(strategy_block.keys())


# ---------------------------------------------------------------------------#
# exam / cet variant coverage
# ---------------------------------------------------------------------------#


async def test_exam_cet_variant_records_strategy_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="exam",
        reading_variant="cet",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    assert len(jobs) >= 1

    strategy = resolve_reader_variant_strategy("exam", "cet")
    for job in jobs:
        input_json = dict(job["input_json"])
        assert input_json["reading_goal"] == "exam"
        assert input_json["reading_variant"] == "cet"
        assert input_json["strategy_version"] == READER_VARIANT_POLICY_VERSION
        assert input_json["strategy_hash"] == strategy.strategy_hash


# ---------------------------------------------------------------------------#
# Fingerprint differentiation and determinism
# ---------------------------------------------------------------------------#


async def test_different_variants_produce_different_fingerprints(
    strategy_env: asyncpg.Pool,
) -> None:
    # 该测试校验 legacy per-unit grammar job 指纹随 variant 变化。
    # P1-1 之后 Z+ window job 指纹为 ``grammar_bundle_window_v1``（record-scoped，
    # 不含 variant hash），无法体现 variant 差异；这里显式走 legacy 路径以保留
    # 原始 fingerprint differentiation 契约。
    user_id = await insert_user(strategy_env)
    record_daily = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    record_exam = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="exam",
        reading_variant="cet",
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(
        record_id=record_daily, user_id=user_id, force_legacy_grammar=True
    )
    await service.bootstrap_missing_jobs(
        record_id=record_exam, user_id=user_id, force_legacy_grammar=True
    )

    daily_jobs = await _load_jobs(strategy_env, record_daily)
    exam_jobs = await _load_jobs(strategy_env, record_exam)

    daily_fp = {j["operation_fingerprint"] for j in daily_jobs}
    exam_fp = {j["operation_fingerprint"] for j in exam_jobs}
    # The two records must not share any operation_fingerprint value.
    assert daily_fp.isdisjoint(exam_fp)

    # Sanity: each record has at least one job.
    assert len(daily_jobs) >= 1
    assert len(exam_jobs) >= 1


async def test_same_variant_repeated_bootstrap_is_deterministic(
    strategy_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)

    first = await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    # Idempotent re-bootstrap: no new jobs, same fingerprints.
    second = await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    assert second.job_counts.translation == 0
    assert second.job_counts.vocabulary == 0
    assert second.job_counts.grammar_bundle == 0
    assert second.job_counts.display_title == 0

    first_fps = tuple(r.operation_fingerprint for r in first.translation_results)
    # The first bootstrap's fingerprints are the deterministic baseline; the
    # second bootstrap produced no new results so there is nothing to compare
    # against except confirming stability via a fresh record below.

    # Cross-record determinism: a second record with the same variant must
    # produce the same operation_fingerprints.
    record_two = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    third = await service.bootstrap_missing_jobs(
        record_id=record_two, user_id=user_id
    )
    third_fps = tuple(r.operation_fingerprint for r in third.translation_results)
    assert first_fps == third_fps


async def test_policy_hash_change_changes_fingerprint(
    strategy_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a YAML policy text change by monkeypatching the resolver.

    The real ``reader_variants.yaml`` is never modified. The patched resolver
    returns a strategy with a modified ``strategy_hash``, which simulates a
    prompt-line change. The composed operation_fingerprint must differ.
    """
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)

    original_resolver = job_bootstrap.resolve_reader_variant_strategy

    real_strategy = original_resolver("daily_reading", "intermediate_reading")
    real_fingerprint = job_bootstrap._compose_operation_fingerprint(
        job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT, real_strategy
    )

    modified_strategy = dataclasses.replace(
        real_strategy,
        strategy_hash=real_strategy.strategy_hash + "_policy_changed",
    )

    def patched_resolver(
        reading_goal: str,
        reading_variant: str,
        *,
        policy_doc: object = None,
    ) -> ReaderVariantStrategy:
        # Call the original to validate the pair, then return the modified
        # strategy so the hash differs.
        original_resolver(reading_goal, reading_variant, policy_doc=policy_doc)
        return modified_strategy

    monkeypatch.setattr(
        job_bootstrap, "resolve_reader_variant_strategy", patched_resolver
    )

    modified_fingerprint = job_bootstrap._compose_operation_fingerprint(
        job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT, modified_strategy
    )
    assert real_fingerprint != modified_fingerprint

    # Bootstrap under the patched resolver; the jobs must carry the modified
    # fingerprint, which differs from the real one.
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs = await _load_jobs(strategy_env, record_id)
    # T1.1: short articles route to the batch path (translate_article).
    # Accept either job type; both compose fingerprint from the same base.
    translation_jobs = [
        j for j in jobs if j["job_type"] in ("translate_unit", "translate_article")
    ]
    assert len(translation_jobs) >= 1
    # The composed fingerprint uses the batch base for translate_article and
    # the per-unit base for translate_unit. Recompute the expected fingerprint
    # from the job's own base constant so the assertion holds for either path.
    for job in translation_jobs:
        if job["job_type"] == "translate_article":
            expected_base = job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT
        else:
            expected_base = job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT
        expected_fp = job_bootstrap._compose_operation_fingerprint(
            expected_base,
            modified_strategy,
            semantic_token="sem:legacy:legacy_open:mode:enforce",
        )
        input_json = job["input_json"]
        expected_profile_token = (
            ":prompt_profile:"
            f"{input_json['translation_prompt_profile_contract_version']}:"
            f"{input_json['translation_prompt_profile_version']}:"
            f"{input_json['translation_prompt_profile_fingerprint_hash']}"
        )
        assert job["operation_fingerprint"] == expected_fp + expected_profile_token
        assert job["operation_fingerprint"] != real_fingerprint


# ---------------------------------------------------------------------------#
# Fail-closed contract
# ---------------------------------------------------------------------------#


def test_resolver_rejects_academic_academic_general() -> None:
    with pytest.raises(ReaderStrategyResolverError):
        resolve_reader_variant_strategy("academic", "academic_general")


def test_resolver_rejects_cross_goal_pair() -> None:
    # daily_reading + cet is an illegal cross-goal pair.
    with pytest.raises(ReaderStrategyResolverError):
        resolve_reader_variant_strategy("daily_reading", "cet")


def test_resolver_rejects_exam_with_daily_variant() -> None:
    with pytest.raises(ReaderStrategyResolverError):
        resolve_reader_variant_strategy("exam", "intermediate_reading")


async def test_bootstrap_propagates_resolver_error(
    strategy_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap must fail closed when the resolver raises.

    This tests the propagation path: ``_load_locked_active_base_state`` calls
    the resolver, and any ``ReaderStrategyResolverError`` must surface to the
    caller rather than silently falling back.
    """
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    def raising_resolver(
        reading_goal: str,
        reading_variant: str,
        *,
        policy_doc: object = None,
    ) -> ReaderVariantStrategy:
        raise ReaderStrategyResolverError("simulated policy failure")

    monkeypatch.setattr(
        job_bootstrap, "resolve_reader_variant_strategy", raising_resolver
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    with pytest.raises(ReaderStrategyResolverError, match="simulated policy failure"):
        await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    # No jobs should have been written.
    jobs = await _load_jobs(strategy_env, record_id)
    assert jobs == []


# ---------------------------------------------------------------------------#
# Fix #1: plain-text submit main path is strategy-aware
# ---------------------------------------------------------------------------#


async def test_plain_text_submit_bootstrap_creates_strategy_aware_translation_job(
    strategy_env: asyncpg.Pool,
) -> None:
    """The standalone TranslationJobBootstrapService (used by the plain-text
    submit path via ReaderOrchestrator) must produce strategy-aware jobs.

    Verifies that the job's input_json contains reading_goal,
    reading_variant, strategy_version, strategy_hash, layer_policy_hash,
    and that operation_fingerprint is the composed form (base:hash), not
    the bare constant.
    """
    from app.services.reader_orchestration.orchestrator import ReaderOrchestrator

    user_id = await insert_user(strategy_env)
    service = ReaderOrchestrator(pool=strategy_env)
    result = await service.submit_plain_text_and_bootstrap_translation(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="Plain Text Strategy Path",
            language="en",
            reading_goal="daily_reading",  # type: ignore[arg-type]
            reading_variant="intermediate_reading",  # type: ignore[arg-type]
        )
    )

    async with strategy_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT job_type, operation_fingerprint, input_json, run_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            result.record_id,
        )
    assert job_row is not None

    # operation_fingerprint must be composed (base:hash), not bare constant.
    assert _fingerprint_matches_base(
        job_row["operation_fingerprint"],
        job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert (
        job_row["operation_fingerprint"]
        != job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT
    )

    input_json = job_row["input_json"]
    assert input_json["reading_goal"] == "daily_reading"
    assert input_json["reading_variant"] == "intermediate_reading"
    assert "strategy_version" in input_json
    assert "strategy_hash" in input_json
    assert "layer_policy_hash" in input_json
    assert input_json["strategy_hash"]  # non-empty

    # The envelope_json on the run must also carry the strategy block.
    envelope = await _load_run_envelope(strategy_env, job_row["run_id"])
    assert _strategy_keys().issubset(envelope["strategy"].keys())


async def test_plain_text_submit_bootstrap_with_exam_cet_variant(
    strategy_env: asyncpg.Pool,
) -> None:
    """The plain-text submit path must also work for exam/cet variant."""
    from app.services.reader_orchestration.orchestrator import ReaderOrchestrator

    user_id = await insert_user(strategy_env)
    service = ReaderOrchestrator(pool=strategy_env)
    result = await service.submit_plain_text_and_bootstrap_translation(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
            title="Exam CET Strategy Path",
            language="en",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="cet",  # type: ignore[arg-type]
        )
    )

    async with strategy_env.acquire() as conn:
        job_row = await conn.fetchrow(
            """
            SELECT operation_fingerprint, input_json
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            result.record_id,
        )
    assert job_row is not None
    assert job_row["input_json"]["reading_goal"] == "exam"
    assert job_row["input_json"]["reading_variant"] == "cet"
    assert _fingerprint_matches_base(
        job_row["operation_fingerprint"],
        job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT,
    )


# ---------------------------------------------------------------------------#
# Fix #2: stale fingerprint job supersede
# ---------------------------------------------------------------------------#


async def test_stale_fingerprint_jobs_are_superseded_on_strategy_change(
    strategy_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the strategy fingerprint changes, old queued/retry_later/paused
    jobs of the same target must be marked superseded.

    Steps:
        1. Bootstrap with the real resolver → jobs with fingerprint A.
        2. Monkeypatch resolver to return a different strategy_hash.
        3. Bootstrap again → old jobs superseded, new jobs queued.
    """
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)

    # Step 1: bootstrap with real resolver.
    first = await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    assert first.job_counts.translation >= 1
    first_fp = first.translation_results[0].operation_fingerprint

    # Capture old job IDs before re-bootstrap.
    # T1.1: short articles route to batch (translate_article) not per-unit.
    async with strategy_env.acquire() as conn:
        old_jobs = await conn.fetch(
            """
            SELECT id, operation_fingerprint, status, rationale_code
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type IN ('translate_unit', 'translate_article')
            ORDER BY created_at ASC
            """,
            record_id,
        )
    old_job_ids = {row["id"] for row in old_jobs}
    assert len(old_jobs) >= 1

    # Step 2: monkeypatch resolver to simulate a policy text change.
    original_resolver = job_bootstrap.resolve_reader_variant_strategy
    real_strategy = original_resolver("daily_reading", "intermediate_reading")
    modified_strategy = dataclasses.replace(
        real_strategy,
        strategy_hash=real_strategy.strategy_hash + "_policy_changed",
    )

    def patched_resolver(
        reading_goal: str,
        reading_variant: str,
        *,
        policy_doc: object = None,
    ) -> ReaderVariantStrategy:
        original_resolver(reading_goal, reading_variant, policy_doc=policy_doc)
        return modified_strategy

    monkeypatch.setattr(
        job_bootstrap, "resolve_reader_variant_strategy", patched_resolver
    )

    # Step 3: re-bootstrap under patched resolver.
    second = await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    second_fp = second.translation_results[0].operation_fingerprint
    assert second_fp != first_fp

    # Verify old jobs are superseded with the right rationale_code.
    async with strategy_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, operation_fingerprint, status, rationale_code
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type IN ('translate_unit', 'translate_article')
            ORDER BY created_at ASC
            """,
            record_id,
        )

    old_rows = [r for r in rows if r["id"] in old_job_ids]
    new_rows = [r for r in rows if r["id"] not in old_job_ids]
    assert len(old_rows) >= 1
    assert len(new_rows) >= 1

    for old in old_rows:
        assert old["status"] == "superseded"
        assert old["rationale_code"] == "strategy_fingerprint_superseded"

    for new in new_rows:
        assert new["status"] == "queued"
        assert new["operation_fingerprint"] == second_fp


async def test_superseded_stale_jobs_are_not_claimed_by_worker(
    strategy_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After supersede, a worker claim must NOT pick up the old stale job.

    T3.1: the enhancement service now creates ``translate_article`` window
    jobs for non-short articles (not per-unit ``translate_unit``). This
    test bootstraps via the enhancement service with long text, monkeypatches
    the resolver, re-bootstraps, and verifies that a worker claim for
    ``translate_article`` only picks up the new-fingerprint job.
    """
    from datetime import timedelta

    from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        # T3.1: long text routes to translate_article window jobs.
        plain_text=_LONG_TEXT,
    )

    # Step 1: bootstrap via enhancement service (creates translate_article
    # window jobs with the real strategy fingerprint).
    enhancement = EnhancementJobBootstrapService(pool=strategy_env)
    first = await enhancement.bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    assert first.job_counts.translation >= 1
    first_fp = first.translation_results[0].operation_fingerprint

    # Step 2: monkeypatch resolver and re-bootstrap via enhancement service.
    original_resolver = job_bootstrap.resolve_reader_variant_strategy
    real_strategy = original_resolver("daily_reading", "intermediate_reading")
    modified_strategy = dataclasses.replace(
        real_strategy,
        strategy_hash=real_strategy.strategy_hash + "_v2",
    )

    def patched_resolver(
        reading_goal: str,
        reading_variant: str,
        *,
        policy_doc: object = None,
    ) -> ReaderVariantStrategy:
        original_resolver(reading_goal, reading_variant, policy_doc=policy_doc)
        return modified_strategy

    monkeypatch.setattr(
        job_bootstrap, "resolve_reader_variant_strategy", patched_resolver
    )

    await enhancement.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    # Step 3: worker claim must only get the new fingerprint job.
    runtime = ReaderJobRuntime(pool=strategy_env)
    claim = await runtime.claim_next_job(
        lease_owner="test_worker",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.operation_fingerprint != first_fp
    assert claim.operation_fingerprint.startswith(
        job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT + ":"
    )


# ---------------------------------------------------------------------------#
# Fix #3: prefix matching boundary
# ---------------------------------------------------------------------------#


def test_fingerprint_matches_base_rejects_similar_prefixes() -> None:
    """Only the exact base or ``base:hash`` may match."""
    assert not _fingerprint_matches_base(
        "translation_unit_old:hash", "translation_unit"
    )
    assert not _fingerprint_matches_base(
        "translation_unit_experimental:hash", "translation_unit"
    )
    assert not _fingerprint_matches_base(
        "translation_unit_extra:hash", "translation_unit"
    )
    assert not _fingerprint_matches_base("v1abc", "v1")
    assert not _fingerprint_matches_base("v1x:hash", "v1")


def test_fingerprint_matches_base_accepts_exact_and_composed() -> None:
    """Exact match (legacy) and ``base:hash`` (T5 composed) are both accepted."""
    assert _fingerprint_matches_base("translation_unit", "translation_unit")
    assert _fingerprint_matches_base(
        "translation_unit:abc123", "translation_unit"
    )
    assert _fingerprint_matches_base(
        "display_title_zh_v1:def456", "display_title_zh_v1"
    )


# ---------------------------------------------------------------------------#
# T1.1: short-article threshold routing (batch vs per-unit)
# ---------------------------------------------------------------------------#


async def test_t11_short_article_routes_to_batch_path(
    strategy_env: asyncpg.Pool,
) -> None:
    """T1.1: Article ≤ SHORT_ARTICLE_MAX_CHAR_COUNT (6000) creates batch jobs
    (translate_article + build_vocabulary_layer_article), NOT per-unit jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_PLAIN_TEXT,
    )
    assert len(_PLAIN_TEXT) <= job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    job_types = {j["job_type"] for j in jobs}

    # Short article → batch path
    assert "translate_article" in job_types
    assert "build_vocabulary_layer_article" in job_types
    # Must NOT create per-unit jobs for short articles
    assert "translate_unit" not in job_types
    assert "build_vocabulary_layer" not in job_types


async def test_t11_long_article_routes_to_per_unit_path(
    strategy_env: asyncpg.Pool,
) -> None:
    """T1.1: Article > SHORT_ARTICLE_MAX_CHAR_COUNT (6000) creates grouped
    batch jobs. T3.1: translation now uses grouped batch jobs
    (translate_article) instead of per-unit translate_unit. T3.2b:
    vocabulary uses grouped batch jobs (build_vocabulary_layer_article)."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    assert len(_LONG_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    job_types = {j["job_type"] for j in jobs}

    # T3.1: long article → translation uses grouped batch jobs, NOT per-unit
    assert "translate_article" in job_types
    assert "translate_unit" not in job_types
    # T3.2b: vocabulary uses grouped batch jobs, NOT per-unit
    assert "build_vocabulary_layer_article" in job_types
    assert "build_vocabulary_layer" not in job_types


def test_fingerprint_matches_base_rejects_different_base() -> None:
    """A fingerprint with a different base must not match."""
    assert not _fingerprint_matches_base(
        "vocabulary_unit_v1:hash", "translation_unit"
    )
    assert not _fingerprint_matches_base(
        "grammar_bundle_unit_v1:hash", "vocabulary_unit_v1"
    )


# ---------------------------------------------------------------------------#
# T3.2b: Non-short vocabulary grouped execution (window planner + bootstrap)
# ---------------------------------------------------------------------------#


def test_plan_vocabulary_windows_covers_all_units_no_overlap() -> None:
    """T3.2b: window planner covers every unit, consecutive, no overlap."""
    from app.services.reader_orchestration.job_bootstrap import (
        VocabularyWindowUnit,
        plan_vocabulary_windows,
    )

    units = [
        VocabularyWindowUnit(unit_id=f"u{i}", order_index=i, text_length=1500)
        for i in range(6)
    ]
    windows = plan_vocabulary_windows(units)
    # Every unit appears in exactly one window
    all_windowed = [u for w in windows for u in w.units]
    assert len(all_windowed) == len(units)
    assert {u.unit_id for u in all_windowed} == {u.unit_id for u in units}
    # Windows are consecutive and ordered by reading order
    for w in windows:
        orders = [u.order_index for u in w.units]
        assert orders == sorted(orders)
    # No overlap: each unit_id appears in exactly one window
    seen: set[str] = set()
    for w in windows:
        for u in w.units:
            assert u.unit_id not in seen
            seen.add(u.unit_id)
    # Windows are ordered by reading order
    first_orders = [w.units[0].order_index for w in windows]
    assert first_orders == sorted(first_orders)


def test_plan_vocabulary_windows_respects_safety_max() -> None:
    """T3.2b: a single unit larger than safety max becomes its own window."""
    from app.services.reader_orchestration.job_bootstrap import (
        VocabularyWindowUnit,
        plan_vocabulary_windows,
    )

    units = [
        VocabularyWindowUnit(unit_id="small1", order_index=0, text_length=1000),
        VocabularyWindowUnit(unit_id="huge", order_index=1, text_length=6000),
        VocabularyWindowUnit(unit_id="small2", order_index=2, text_length=1000),
    ]
    windows = plan_vocabulary_windows(
        units,
        target_char_count=3000,
        safety_max_char_count=5000,
    )
    # "huge" must be in its own window (6000 > 5000 safety max)
    assert len(windows) == 3
    assert [w.units[0].unit_id for w in windows] == ["small1", "huge", "small2"]


def test_plan_vocabulary_windows_empty_input() -> None:
    """T3.2b: empty unit list produces empty window list."""
    from app.services.reader_orchestration.job_bootstrap import (
        plan_vocabulary_windows,
    )

    assert plan_vocabulary_windows([]) == []


def test_plan_vocabulary_windows_single_unit() -> None:
    """T3.2b: a single unit produces a single window."""
    from app.services.reader_orchestration.job_bootstrap import (
        VocabularyWindowUnit,
        plan_vocabulary_windows,
    )

    units = [VocabularyWindowUnit(unit_id="u1", order_index=0, text_length=500)]
    windows = plan_vocabulary_windows(units)
    assert len(windows) == 1
    assert windows[0].target_unit_ids == ("u1",)


def test_vocabulary_window_plan_window_id_is_stable() -> None:
    """T3.2b: window_id is a stable hash of sorted unit_ids."""
    from app.services.reader_orchestration.job_bootstrap import (
        VocabularyWindowUnit,
        VocabularyWindowPlan,
    )

    w1 = VocabularyWindowPlan(
        units=(
            VocabularyWindowUnit(unit_id="u2", order_index=1, text_length=100),
            VocabularyWindowUnit(unit_id="u1", order_index=0, text_length=100),
        )
    )
    w2 = VocabularyWindowPlan(
        units=(
            VocabularyWindowUnit(unit_id="u1", order_index=0, text_length=100),
            VocabularyWindowUnit(unit_id="u2", order_index=1, text_length=100),
        )
    )
    # Same unit set → same window_id regardless of tuple order
    assert w1.window_id == w2.window_id
    # Different unit set → different window_id
    w3 = VocabularyWindowPlan(
        units=(
            VocabularyWindowUnit(unit_id="u1", order_index=0, text_length=100),
        )
    )
    assert w1.window_id != w3.window_id


async def test_t32b_non_short_article_creates_multiple_vocabulary_window_jobs(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.2b: non-short article creates multiple build_vocabulary_layer_article
    window jobs (not per-unit build_vocabulary_layer)."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    assert len(_LONG_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    vocab_batch_jobs = [
        j for j in jobs if j["job_type"] == "build_vocabulary_layer_article"
    ]
    # Non-short article must create at least 1 window job; with 8 units of
    # ~1600 chars each and target=3000, expect ~4 windows.
    assert len(vocab_batch_jobs) >= 2, (
        f"expected >=2 vocabulary window jobs, got {len(vocab_batch_jobs)}"
    )
    # Must NOT create per-unit vocabulary jobs
    per_unit_vocab = [j for j in jobs if j["job_type"] == "build_vocabulary_layer"]
    assert len(per_unit_vocab) == 0

    # Each window job must have distinct target_key, input_hash, and window_id
    target_keys = [j["input_json"].get("window_id") for j in vocab_batch_jobs]
    assert len(set(target_keys)) == len(vocab_batch_jobs), (
        "window_ids must be distinct across window jobs"
    )
    input_hashes = [j["input_hash"] for j in vocab_batch_jobs]
    assert len(set(input_hashes)) == len(vocab_batch_jobs), (
        "input_hashes must be distinct across window jobs"
    )

    # target_unit_ids across all windows must cover every unit exactly once
    all_target_unit_ids: list[str] = []
    for j in vocab_batch_jobs:
        ids = j["input_json"].get("target_unit_ids") or []
        all_target_unit_ids.extend(ids)
    assert len(set(all_target_unit_ids)) == len(all_target_unit_ids), (
        "units must not overlap across windows"
    )


async def test_t32b_vocabulary_window_jobs_idempotent_rebootstrap(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.2b: re-running bootstrap does not duplicate window jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_after_first = await _load_jobs(strategy_env, record_id)
    vocab_batch_first = [
        j for j in jobs_after_first if j["job_type"] == "build_vocabulary_layer_article"
    ]

    # Re-run bootstrap — should not create duplicate window jobs
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_after_second = await _load_jobs(strategy_env, record_id)
    vocab_batch_second = [
        j for j in jobs_after_second if j["job_type"] == "build_vocabulary_layer_article"
    ]

    assert len(vocab_batch_second) == len(vocab_batch_first)
    first_ids = {j["job_id"] for j in vocab_batch_first}
    second_ids = {j["job_id"] for j in vocab_batch_second}
    assert first_ids == second_ids


async def test_t32b_vocabulary_window_jobs_partial_publish_only_fills_missing(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.2b: when some units already have published vocabulary layers,
    bootstrap only creates windows for the missing units."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_first = await _load_jobs(strategy_env, record_id)
    vocab_batch_first = [
        j for j in jobs_first if j["job_type"] == "build_vocabulary_layer_article"
    ]
    assert len(vocab_batch_first) >= 2

    # Simulate publishing vocabulary layers for the units in the first window
    first_window_unit_ids = vocab_batch_first[0]["input_json"]["target_unit_ids"]
    async with strategy_env.acquire() as conn:
        base_id = await conn.fetchval(
            "SELECT active_base_id FROM reading_records WHERE id = $1",
            record_id,
        )
        generation = await conn.fetchval(
            "SELECT generation FROM reading_records WHERE id = $1",
            record_id,
        )
        for unit_id in first_window_unit_ids:
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id,
                    base_id,
                    layer_type,
                    target_scope,
                    target_key,
                    generation,
                    status,
                    operation_fingerprint,
                    schema_version,
                    output_json,
                    coverage_json,
                    quality_json,
                    source_run_id,
                    source_job_id,
                    published_at
                )
                VALUES ($1, $2, 'vocabulary', 'unit', $3, $4, 'published',
                    $5, 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                    $6, $7, NOW())
                """,
                record_id,
                base_id,
                unit_id,
                generation,
                f"fake_published_vocabulary:{unit_id}",
                vocab_batch_first[0]["run_id"],
                vocab_batch_first[0]["job_id"],
            )
        # Mark the first window job as succeeded so it's not re-created
        await conn.execute(
            "UPDATE reader_jobs SET status = 'succeeded' WHERE id = $1",
            vocab_batch_first[0]["job_id"],
        )

    # Re-run bootstrap — should only create windows for unpublished units
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_second = await _load_jobs(strategy_env, record_id)
    first_job_id = vocab_batch_first[0]["job_id"]
    vocab_batch_second = [
        j for j in jobs_second if j["job_type"] == "build_vocabulary_layer_article"
    ]

    # The succeeded first-window job should still be present
    assert first_job_id in {j["job_id"] for j in vocab_batch_second}

    # NEW window jobs (not the succeeded first window) must NOT include
    # any of the first window's already-published unit_ids.
    new_window_jobs = [j for j in vocab_batch_second if j["job_id"] != first_job_id]
    assert len(new_window_jobs) >= 1
    for j in new_window_jobs:
        ids = set(j["input_json"]["target_unit_ids"])
        assert not ids.intersection(first_window_unit_ids), (
            f"window {j['job_id']} includes already-published units {ids & set(first_window_unit_ids)}"
        )


async def test_t32b_vocabulary_window_jobs_target_key_distinct(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.2b: each window job has a distinct target_key containing the
    window_id, so idempotency checks do not false-positive across windows."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with strategy_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target_key, operation_fingerprint, idempotency_key, input_hash
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
            ORDER BY created_at ASC
            """,
            record_id,
        )

    assert len(rows) >= 2
    target_keys = [r["target_key"] for r in rows]
    idempotency_keys = [r["idempotency_key"] for r in rows]
    input_hashes = [r["input_hash"] for r in rows]
    # All distinct
    assert len(set(target_keys)) == len(rows)
    assert len(set(idempotency_keys)) == len(rows)
    assert len(set(input_hashes)) == len(rows)
    # target_key format: {record_id}:window:{window_id}
    for tk in target_keys:
        assert ":window:" in tk


# ---------------------------------------------------------------------------#
# T3.1: Non-short translation grouped execution (window planner + bootstrap)
# ---------------------------------------------------------------------------#


def test_plan_translation_windows_covers_all_units_no_overlap() -> None:
    """T3.1: translation window planner covers every unit, consecutive,
    no overlap."""
    from app.services.reader_orchestration.job_bootstrap import (
        TranslationWindowUnit,
        plan_translation_windows,
    )

    units = [
        TranslationWindowUnit(unit_id=f"u{i}", order_index=i, text_length=2500)
        for i in range(8)
    ]
    windows = plan_translation_windows(units)
    # Every unit appears in exactly one window
    all_windowed = [u for w in windows for u in w.units]
    assert len(all_windowed) == len(units)
    assert {u.unit_id for u in all_windowed} == {u.unit_id for u in units}
    # Windows are consecutive and ordered by reading order
    for w in windows:
        orders = [u.order_index for u in w.units]
        assert orders == sorted(orders)
    # No overlap: each unit_id appears in exactly one window
    seen: set[str] = set()
    for w in windows:
        for u in w.units:
            assert u.unit_id not in seen
            seen.add(u.unit_id)
    # Windows are ordered by reading order
    first_orders = [w.units[0].order_index for w in windows]
    assert first_orders == sorted(first_orders)


def test_plan_translation_windows_respects_safety_max() -> None:
    """T3.1: a single unit larger than safety max becomes its own window."""
    from app.services.reader_orchestration.job_bootstrap import (
        TranslationWindowUnit,
        plan_translation_windows,
    )

    units = [
        TranslationWindowUnit(unit_id="small1", order_index=0, text_length=2000),
        TranslationWindowUnit(unit_id="huge", order_index=1, text_length=12000),
        TranslationWindowUnit(unit_id="small2", order_index=2, text_length=2000),
    ]
    windows = plan_translation_windows(
        units,
        target_char_count=6000,
        safety_max_char_count=10000,
    )
    # "huge" must be in its own window (12000 > 10000 safety max)
    assert len(windows) == 3
    assert [w.units[0].unit_id for w in windows] == ["small1", "huge", "small2"]


def test_plan_translation_windows_empty_input() -> None:
    """T3.1: empty unit list produces empty window list."""
    from app.services.reader_orchestration.job_bootstrap import (
        plan_translation_windows,
    )

    assert plan_translation_windows([]) == []


def test_plan_translation_windows_single_unit() -> None:
    """T3.1: a single unit produces a single window."""
    from app.services.reader_orchestration.job_bootstrap import (
        TranslationWindowUnit,
        plan_translation_windows,
    )

    units = [TranslationWindowUnit(unit_id="u1", order_index=0, text_length=500)]
    windows = plan_translation_windows(units)
    assert len(windows) == 1
    assert windows[0].target_unit_ids == ("u1",)


def test_translation_window_plan_window_id_is_stable() -> None:
    """T3.1: window_id is a stable hash of sorted unit_ids."""
    from app.services.reader_orchestration.job_bootstrap import (
        TranslationWindowPlan,
        TranslationWindowUnit,
    )

    w1 = TranslationWindowPlan(
        units=(
            TranslationWindowUnit(unit_id="u2", order_index=1, text_length=100),
            TranslationWindowUnit(unit_id="u1", order_index=0, text_length=100),
        )
    )
    w2 = TranslationWindowPlan(
        units=(
            TranslationWindowUnit(unit_id="u1", order_index=0, text_length=100),
            TranslationWindowUnit(unit_id="u2", order_index=1, text_length=100),
        )
    )
    # Same unit set → same window_id regardless of tuple order
    assert w1.window_id == w2.window_id
    # Different unit set → different window_id
    w3 = TranslationWindowPlan(
        units=(
            TranslationWindowUnit(unit_id="u1", order_index=0, text_length=100),
        )
    )
    assert w1.window_id != w3.window_id


async def test_t31_non_short_article_creates_multiple_translation_window_jobs(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1: non-short article creates multiple translate_article window jobs
    (not per-unit translate_unit)."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    assert len(_LONG_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    # Non-short article must create multiple window jobs; with 8 units of
    # ~2300 chars each and target=6000, expect 3 windows.
    assert len(translation_batch_jobs) >= 2, (
        f"expected >=2 translation window jobs, got {len(translation_batch_jobs)}"
    )
    # Must NOT create per-unit translation jobs
    per_unit_translation = [j for j in jobs if j["job_type"] == "translate_unit"]
    assert len(per_unit_translation) == 0

    # Each window job must have distinct window_id, input_hash, target_key
    window_ids = [j["input_json"].get("window_id") for j in translation_batch_jobs]
    assert len(set(window_ids)) == len(translation_batch_jobs), (
        "window_ids must be distinct across translation window jobs"
    )
    input_hashes = [j["input_hash"] for j in translation_batch_jobs]
    assert len(set(input_hashes)) == len(translation_batch_jobs), (
        "input_hashes must be distinct across translation window jobs"
    )

    # target_unit_ids across all windows must cover every unit exactly once
    all_target_unit_ids: list[str] = []
    for j in translation_batch_jobs:
        ids = j["input_json"].get("target_unit_ids") or []
        all_target_unit_ids.extend(ids)
    assert len(set(all_target_unit_ids)) == len(all_target_unit_ids), (
        "units must not overlap across translation windows"
    )

    # Verify distinct target_keys and idempotency_keys at DB level
    async with strategy_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target_key, idempotency_key, input_hash
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
            ORDER BY created_at ASC
            """,
            record_id,
        )
    target_keys = [r["target_key"] for r in rows]
    idempotency_keys = [r["idempotency_key"] for r in rows]
    assert len(set(target_keys)) == len(rows)
    assert len(set(idempotency_keys)) == len(rows)
    for tk in target_keys:
        assert ":window:" in tk


async def test_t31_translation_window_jobs_idempotent_rebootstrap(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1: re-running bootstrap does not duplicate translation window jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_after_first = await _load_jobs(strategy_env, record_id)
    translation_batch_first = [
        j for j in jobs_after_first if j["job_type"] == "translate_article"
    ]

    # Re-run bootstrap — should not create duplicate window jobs
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_after_second = await _load_jobs(strategy_env, record_id)
    translation_batch_second = [
        j for j in jobs_after_second if j["job_type"] == "translate_article"
    ]

    assert len(translation_batch_second) == len(translation_batch_first)
    first_ids = {j["job_id"] for j in translation_batch_first}
    second_ids = {j["job_id"] for j in translation_batch_second}
    assert first_ids == second_ids


async def test_t31_translation_window_jobs_partial_publish_only_fills_missing(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1: when some units already have published translation layers,
    bootstrap only creates windows for the missing units."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_first = await _load_jobs(strategy_env, record_id)
    translation_batch_first = [
        j for j in jobs_first if j["job_type"] == "translate_article"
    ]
    assert len(translation_batch_first) >= 1

    # Simulate publishing translation layers for the units in the first window
    first_window_unit_ids = translation_batch_first[0]["input_json"]["target_unit_ids"]
    async with strategy_env.acquire() as conn:
        base_id = await conn.fetchval(
            "SELECT active_base_id FROM reading_records WHERE id = $1",
            record_id,
        )
        generation = await conn.fetchval(
            "SELECT generation FROM reading_records WHERE id = $1",
            record_id,
        )
        for unit_id in first_window_unit_ids:
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id,
                    base_id,
                    layer_type,
                    target_scope,
                    target_key,
                    generation,
                    status,
                    operation_fingerprint,
                    schema_version,
                    output_json,
                    coverage_json,
                    quality_json,
                    source_run_id,
                    source_job_id,
                    published_at
                )
                VALUES ($1, $2, 'translation', 'unit', $3, $4, 'published',
                    $5, 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                    $6, $7, NOW())
                """,
                record_id,
                base_id,
                unit_id,
                generation,
                f"fake_published_translation:{unit_id}",
                translation_batch_first[0]["run_id"],
                translation_batch_first[0]["job_id"],
            )
        # Mark the first window job as succeeded so it's not re-created
        await conn.execute(
            "UPDATE reader_jobs SET status = 'succeeded' WHERE id = $1",
            translation_batch_first[0]["job_id"],
        )

    # Re-run bootstrap — should only create windows for unpublished units
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    jobs_second = await _load_jobs(strategy_env, record_id)
    first_job_id = translation_batch_first[0]["job_id"]
    translation_batch_second = [
        j for j in jobs_second if j["job_type"] == "translate_article"
    ]

    # The succeeded first-window job should still be present
    assert first_job_id in {j["job_id"] for j in translation_batch_second}

    # NEW window jobs (not the succeeded first window) must NOT include
    # any of the first window's already-published unit_ids.
    new_window_jobs = [j for j in translation_batch_second if j["job_id"] != first_job_id]
    for j in new_window_jobs:
        ids = set(j["input_json"]["target_unit_ids"])
        assert not ids.intersection(first_window_unit_ids), (
            f"window {j['job_id']} includes already-published units {ids & set(first_window_unit_ids)}"
        )


async def test_t31_translation_window_jobs_target_key_distinct(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1: each translation window job has a distinct target_key containing
    the window_id, plus distinct idempotency_key and input_hash."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with strategy_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target_key, operation_fingerprint, idempotency_key, input_hash
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
            ORDER BY created_at ASC
            """,
            record_id,
        )

    # If only one window was created (small article), this test is a no-op;
    # the non-short fixture normally produces >=1 window. Distinctness is
    # meaningful only when there are >=2 windows, but we still verify the
    # single-window case has the correct target_key format.
    assert len(rows) >= 1
    target_keys = [r["target_key"] for r in rows]
    idempotency_keys = [r["idempotency_key"] for r in rows]
    input_hashes = [r["input_hash"] for r in rows]
    # All distinct (multi-window case)
    assert len(set(target_keys)) == len(rows)
    assert len(set(idempotency_keys)) == len(rows)
    assert len(set(input_hashes)) == len(rows)
    # target_key format: {record_id}:window:{window_id}
    for tk in target_keys:
        assert ":window:" in tk


async def test_t31_short_article_still_single_translation_batch(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1: short article still creates exactly 1 translate_article batch
    job (whole-article), NOT per-unit translate_unit and NOT window jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_PLAIN_TEXT,
    )
    assert len(_PLAIN_TEXT) <= job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    # Short article → exactly 1 whole-article batch job
    assert len(translation_batch_jobs) == 1
    # Must NOT have window_id (short path uses whole-article target_key)
    assert "window_id" not in translation_batch_jobs[0]["input_json"]
    # target_key must be the record_id (whole-article), not a window key
    async with strategy_env.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT target_key
            FROM reader_jobs
            WHERE id = $1
            """,
            translation_batch_jobs[0]["job_id"],
        )
    assert ":window:" not in row["target_key"]
    # Must NOT create per-unit translation jobs
    per_unit = [j for j in jobs if j["job_type"] == "translate_unit"]
    assert len(per_unit) == 0


# ---------------------------------------------------------------------------#
# T3.1 review P2: very long fixture (> safety_max * 2) must produce >= 2 windows
# ---------------------------------------------------------------------------#


async def test_t31_very_long_article_creates_at_least_two_non_overlapping_windows(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1 P2: a fixture explicitly exceeding safety_max * 2 must produce
    at least 2 translation window jobs with no unit overlap."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_VERY_LONG_TEXT,
    )
    assert len(_VERY_LONG_TEXT) > (
        job_bootstrap.TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT * 2
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    assert len(translation_batch_jobs) >= 2, (
        f"expected >=2 translation window jobs for very long article, "
        f"got {len(translation_batch_jobs)}"
    )
    per_unit = [j for j in jobs if j["job_type"] == "translate_unit"]
    assert len(per_unit) == 0

    # No overlap: every unit_id appears in exactly one window
    all_target_unit_ids: list[str] = []
    for j in translation_batch_jobs:
        ids = j["input_json"].get("target_unit_ids") or []
        all_target_unit_ids.extend(ids)
    assert len(set(all_target_unit_ids)) == len(all_target_unit_ids), (
        "units must not overlap across translation windows"
    )

    # Windows are consecutive: when sorted by first-unit order_index,
    # each window's first unit is the immediate successor of the previous
    # window's last unit (no gaps, no overlap). The planner guarantees
    # this; we verify against the DB state.
    window_ranges: list[tuple[int, int]] = []
    async with strategy_env.acquire() as conn:
        for j in translation_batch_jobs:
            unit_ids = j["input_json"]["target_unit_ids"]
            orders = []
            for uid in unit_ids:
                o = await conn.fetchval(
                    "SELECT order_index FROM reading_units WHERE unit_id = $1",
                    uid,
                )
                orders.append(o)
            window_ranges.append((min(orders), max(orders)))
    window_ranges.sort()
    # Each subsequent window starts right after the previous one ends
    # (no gaps, no overlap). We don't assert a specific starting index
    # because order_index may start at 0 or 1 depending on the fixture.
    for i in range(1, len(window_ranges)):
        assert window_ranges[i][0] == window_ranges[i - 1][1] + 1, (
            f"gap or overlap between window {i-1} (ends at "
            f"{window_ranges[i-1][1]}) and window {i} (starts at "
            f"{window_ranges[i][0]})"
        )


# ---------------------------------------------------------------------------#
# T3.1 review P1: cutover — legacy translate_unit jobs must be superseded
# ---------------------------------------------------------------------------#


async def test_t31_cutover_supersedes_legacy_translate_unit_jobs(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1 P1: when legacy ``translate_unit`` per-unit jobs exist in
    ``queued`` / ``retry_later`` / ``paused``, the grouped bootstrap must
    supersede them before creating ``translate_article`` window jobs so the
    worker loop does not dispatch both."""
    import json as _json

    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )

    # Fetch base_id, generation, and a couple of unit_ids so we can
    # manually insert legacy translate_unit jobs.
    async with strategy_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active_base_id, generation FROM reading_records WHERE id = $1",
            record_id,
        )
        base_id = row["active_base_id"]
        generation = row["generation"]
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
    assert len(unit_rows) >= 2
    legacy_unit_ids = [str(unit_rows[0]["unit_id"]), str(unit_rows[1]["unit_id"])]

    # Manually insert two legacy translate_unit jobs in 'queued' status.
    legacy_job_ids: list[UUID] = []
    for unit_id, unit_row in zip(legacy_unit_ids, unit_rows[:2]):
        async with strategy_env.acquire() as conn:
            run_id = await conn.fetchval(
                """
                INSERT INTO reader_runs (
                    reading_record_id, user_id, run_type, status,
                    record_generation, envelope_json, policy_version,
                    trigger_kind
                )
                VALUES ($1, $2, 'translation', 'queued', $3, $4, 'legacy', 'manual')
                RETURNING id
                """,
                record_id,
                user_id,
                generation,
                _json.dumps({"unit_id": unit_id, "trace_id": str(uuid4())}),
            )
            job_id = await conn.fetchval(
                """
                INSERT INTO reader_jobs (
                    reading_record_id, base_id, run_id, user_id,
                    job_type, target_type, target_key, status, priority,
                    expected_generation, operation_fingerprint,
                    idempotency_key, input_hash, input_json, max_attempts
                )
                VALUES ($1, $2, $3, $4, 'translate_unit', 'unit', $5, 'queued', 0,
                    $6, 'legacy_per_unit', $7, 'legacy_hash', $8, 3)
                RETURNING id
                """,
                record_id,
                base_id,
                run_id,
                user_id,
                unit_id,
                generation,
                f"legacy_per_unit:{unit_id}",
                _json.dumps({"unit_id": unit_id}),
            )
            legacy_job_ids.append(job_id)

    # Also insert one translate_unit job in 'retry_later' to verify that
    # status is also superseded.
    unit_id_3 = str(unit_rows[2]["unit_id"]) if len(unit_rows) > 2 else legacy_unit_ids[0]
    async with strategy_env.acquire() as conn:
        run_id_3 = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version,
                trigger_kind
            )
            VALUES ($1, $2, 'translation', 'queued', $3, $4, 'legacy', 'manual')
            RETURNING id
            """,
            record_id,
            user_id,
            generation,
            _json.dumps({"unit_id": unit_id_3, "trace_id": str(uuid4())}),
        )
        job_id_3 = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status, priority,
                expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts
            )
            VALUES ($1, $2, $3, $4, 'translate_unit', 'unit', $5, 'retry_later', 0,
                $6, 'legacy_per_unit', $7, 'legacy_hash_3', $8, 3)
            RETURNING id
            """,
            record_id,
            base_id,
            run_id_3,
            user_id,
            unit_id_3,
            generation,
            f"legacy_per_unit:{unit_id_3}",
            _json.dumps({"unit_id": unit_id_3}),
        )
        legacy_job_ids.append(job_id_3)

    # Run grouped bootstrap — should supersede legacy translate_unit jobs
    # and create translate_article window jobs.
    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with strategy_env.acquire() as conn:
        legacy_rows = await conn.fetch(
            """
            SELECT id, status, rationale_code FROM reader_jobs
            WHERE id = ANY($1::uuid[])
            """,
            legacy_job_ids,
        )
    for row in legacy_rows:
        assert row["status"] == "superseded", (
            f"legacy translate_unit job {row['id']} should be superseded, "
            f"got status={row['status']}"
        )
        assert row["rationale_code"] == "legacy_per_unit_translation_superseded", (
            f"legacy translate_unit job {row['id']} rationale_code="
            f"{row['rationale_code']!r}"
        )

    # translate_article window jobs must exist
    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    assert len(translation_batch_jobs) >= 2
    # No active translate_unit jobs remain
    active_per_unit = [
        j for j in jobs
        if j["job_type"] == "translate_unit"
    ]
    # The legacy jobs are still in the jobs list but should not be active
    async with strategy_env.acquire() as conn:
        active_count = await conn.fetchval(
            """
            SELECT count(*) FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
            """,
            record_id,
        )
    assert active_count == 0


async def test_t31_claimed_legacy_translate_unit_is_excluded_from_new_windows(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1 P1: a claimed legacy ``translate_unit`` job is not superseded,
    but its target unit must be excluded from new ``translate_article``
    windows. Otherwise the claimed per-unit job can publish first and make
    the batch window fail when it reaches the same unit."""
    import json as _json

    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )

    async with strategy_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active_base_id, generation FROM reading_records WHERE id = $1",
            record_id,
        )
        base_id = row["active_base_id"]
        generation = row["generation"]
        unit_row = await conn.fetchrow(
            """
            SELECT unit_id
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            LIMIT 1
            """,
            record_id,
            base_id,
        )
        claimed_unit_id = str(unit_row["unit_id"])
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version,
                trigger_kind
            )
            VALUES ($1, $2, 'translation', 'running', $3, $4, 'legacy', 'manual')
            RETURNING id
            """,
            record_id,
            user_id,
            generation,
            _json.dumps({"unit_id": claimed_unit_id, "trace_id": str(uuid4())}),
        )
        claimed_job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status, priority,
                expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts,
                lease_owner, lease_token, lease_expires_at, claimed_at
            )
            VALUES (
                $1, $2, $3, $4, 'translate_unit', 'unit', $5, 'claimed', 0,
                $6, 'legacy_claimed_per_unit', $7, 'legacy_claimed_hash', $8, 3,
                'legacy-worker', $9, NOW() + INTERVAL '5 minutes', NOW()
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            claimed_unit_id,
            generation,
            f"legacy_claimed_per_unit:{claimed_unit_id}",
            _json.dumps({"unit_id": claimed_unit_id}),
            uuid4(),
        )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    async with strategy_env.acquire() as conn:
        claimed_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            claimed_job_id,
        )
        window_rows = await conn.fetch(
            """
            SELECT input_json->'target_unit_ids' AS target_unit_ids
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
            """,
            record_id,
        )
    assert claimed_status == "claimed"
    assert window_rows, "grouped bootstrap should still create window jobs"
    for row in window_rows:
        ids = row["target_unit_ids"]
        if isinstance(ids, str):
            ids = _json.loads(ids)
        assert claimed_unit_id not in ids


# ---------------------------------------------------------------------------#
# T3.1 review P1: active translate_article window prevents overlapping new window
# ---------------------------------------------------------------------------#


async def test_t31_active_window_job_prevents_overlapping_new_window(
    strategy_env: asyncpg.Pool,
) -> None:
    """T3.1 P1: when an active ``translate_article`` window job already
    covers a set of units, a re-bootstrap must NOT create a new overlapping
    window for any of those units."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs_first = await _load_jobs(strategy_env, record_id)
    window_jobs_first = [
        j for j in jobs_first if j["job_type"] == "translate_article"
    ]
    assert len(window_jobs_first) >= 2

    # Collect all unit_ids covered by the first bootstrap's windows
    first_window_unit_ids: set[str] = set()
    for j in window_jobs_first:
        first_window_unit_ids.update(j["input_json"]["target_unit_ids"])

    # Re-bootstrap: all window jobs are still 'queued', so the
    # active-job exclusion clause should prevent any new windows.
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs_second = await _load_jobs(strategy_env, record_id)
    window_jobs_second = [
        j for j in jobs_second if j["job_type"] == "translate_article"
    ]

    # No new translate_article jobs should have been created
    first_ids = {j["job_id"] for j in window_jobs_first}
    second_ids = {j["job_id"] for j in window_jobs_second}
    assert first_ids == second_ids, (
        f"re-bootstrap created new window jobs: "
        f"new ids = {second_ids - first_ids}"
    )

    # Verify no unit is covered by more than one active window job
    async with strategy_env.acquire() as conn:
        active_windows = await conn.fetch(
            """
            SELECT input_json->'target_unit_ids' AS target_unit_ids
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
            """,
            record_id,
        )
    all_units: list[str] = []
    for row in active_windows:
        ids = row["target_unit_ids"]
        if isinstance(ids, str):
            import json as _json
            ids = _json.loads(ids)
        all_units.extend(ids)
    assert len(set(all_units)) == len(all_units), (
        "a unit is covered by more than one active translate_article window job"
    )


# ---------------------------------------------------------------------------#
# T4.1 / T4.1a: deterministic document feature routing
# ---------------------------------------------------------------------------#
#
# The fixtures below are built from a realistic BBC-style English sentence
# pool (~6.1 chars/word) so the char/word boundary is meaningful. They pin
# the three-mode routing contract:
#
#   - BBC near-threshold (>6000 chars, ~1000 words) -> SHORT_BATCH
#     (regression fix: legacy raw-char router sent it to grouped/windowed).
#   - Medium (>6000 chars, ~1450 words) -> STRUCTURED_BATCH
#     (single whole-article batch job, NOT grouped/windowed).
#   - Long (existing _LONG_TEXT, ~2240 words) -> GROUPED_WINDOWED
#     (preserved; multiple window jobs with :window: target keys).
#
# All three fixtures exceed the legacy 6000-char threshold, so under the
# old raw-``content_utf16_length`` router all three would have entered the
# heavy grouped/windowed path. The new word-based router splits them.
_T41A_BBC_SENTENCES = (
    "The findings were published in a peer-reviewed journal on Tuesday morning.",
    "Researchers said the experimental battery can hold more energy per unit of volume than common lithium-ion cells.",
    "The design uses abundant materials rather than rare metals that have constrained supply chains.",
    "Independent experts described the work as technically sound but cautioned that commercial deployment remains years away.",
    "Energy storage is widely seen as a central challenge in the transition away from fossil fuels.",
    "Solar and wind generation fluctuates with the weather so utilities need reliable ways to store surplus electricity.",
    "The team said the battery maintained its performance over thousands of charge and discharge cycles without significant degradation.",
    "Officials in several countries have indicated that storage technology will receive a growing share of public research funding.",
    "The next phase will involve building a larger prototype and partnering with industry manufacturers to test mass production.",
)
_T41A_BBC_PARAGRAPH = " ".join(_T41A_BBC_SENTENCES)


def _t41a_bbc_article(paragraph_count: int) -> str:
    return "\n\n".join(
        f"Section {idx}. {_T41A_BBC_PARAGRAPH}"
        for idx in range(1, paragraph_count + 1)
    )


# ~1029 words / ~6300 chars: crosses legacy 6000-char threshold but stays
# under the 1100-word short cap.
_T41A_BBC_NEAR_THRESHOLD_TEXT = _t41a_bbc_article(7)
assert len(_T41A_BBC_NEAR_THRESHOLD_TEXT) > 6000

# ~1450 words / ~8900 chars: beyond the short word cap, under the structured
# word cap (2000) and the char guardrail (12000).
_T41A_MEDIUM_TEXT = _t41a_bbc_article(10)
assert len(_T41A_MEDIUM_TEXT) > 6000


async def _load_translation_target_keys(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target_key
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_article'
            ORDER BY created_at ASC
            """,
            record_id,
        )
    return [r["target_key"] for r in rows]


async def test_t41a_bbc_near_threshold_routes_to_short_batch(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1a regression fix: a BBC-style ~1000-word article whose raw char
    length exceeds the legacy 6000 threshold routes to SHORT_BATCH (a single
    whole-article batch job, no windows), NOT to the grouped/windowed path."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_BBC_NEAR_THRESHOLD_TEXT,
    )
    # Sanity: the fixture crosses the legacy raw-char threshold, which is
    # exactly the condition under which the old router mis-routed it.
    assert len(_T41A_BBC_NEAR_THRESHOLD_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    # Short batch -> exactly 1 whole-article batch job, no window_id
    assert len(translation_batch_jobs) == 1
    assert "window_id" not in translation_batch_jobs[0]["input_json"]
    target_keys = await _load_translation_target_keys(strategy_env, record_id)
    assert all(":window:" not in tk for tk in target_keys)
    # No per-unit jobs
    assert not any(j["job_type"] == "translate_unit" for j in jobs)


async def test_t41a_medium_article_routes_to_structured_batch(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1a: a medium article (~1450 words / ~8900 chars) routes to
    STRUCTURED_BATCH -- a single whole-article batch job -- NOT to the
    grouped/windowed heavy path. This is the missing middle tier the
    implementation plan called out."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_MEDIUM_TEXT,
    )
    assert len(_T41A_MEDIUM_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    # Structured batch -> exactly 1 whole-article batch job, no window_id,
    # no :window: target key. Execution reuses the short-batch path this
    # round; the route label is the deliverable.
    assert len(translation_batch_jobs) == 1, (
        f"medium article should produce exactly 1 structured batch job, "
        f"got {len(translation_batch_jobs)} (grouped/windowed would be >=1 "
        f"window jobs with :window: target keys)"
    )
    assert "window_id" not in translation_batch_jobs[0]["input_json"]
    target_keys = await _load_translation_target_keys(strategy_env, record_id)
    assert all(":window:" not in tk for tk in target_keys), (
        f"medium article must NOT use windowed target keys: {target_keys}"
    )
    # No per-unit jobs
    assert not any(j["job_type"] == "translate_unit" for j in jobs)
    # Vocabulary also routes to a single structured batch job (not windowed)
    vocab_batch_jobs = [
        j for j in jobs if j["job_type"] == "build_vocabulary_layer_article"
    ]
    assert len(vocab_batch_jobs) == 1
    assert not any(j["job_type"] == "build_vocabulary_layer" for j in jobs)


async def test_t41a_long_article_still_routes_to_grouped_windowed(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1a: a clearly long article (existing _LONG_TEXT, ~2240 words)
    still routes to GROUPED_WINDOWED so the T3.1 / T3.2b windowed execution
    contract is preserved. Window jobs must carry :window: target keys."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )
    assert len(_LONG_TEXT) > job_bootstrap.SHORT_ARTICLE_MAX_CHAR_COUNT

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    # Long article -> multiple window jobs with :window: target keys
    assert len(translation_batch_jobs) >= 2
    target_keys = await _load_translation_target_keys(strategy_env, record_id)
    assert all(":window:" in tk for tk in target_keys), (
        f"long article window jobs must carry :window: target keys: {target_keys}"
    )
    assert not any(j["job_type"] == "translate_unit" for j in jobs)


# ---------------------------------------------------------------------------#
# P2: missing-base defensive route stability across two _load_article_route
# calls (translation then vocabulary share one state). Pure unit test -- no
# database needed; uses a fake connection.
# ---------------------------------------------------------------------------#


class _FakeMissingBaseConn:
    """Fake asyncpg.Connection whose ``reading_bases`` row is always missing.

    Simulates the base row being deleted between the lock and the route
    check. ``fetchrow`` returns ``None``; ``fetch`` returns an empty list
    (never reached when the base is missing, but provided for safety).
    """

    async def fetchrow(self, *args, **kwargs):
        return None

    async def fetch(self, *args, **kwargs):
        return []


async def test_t41a_missing_base_route_is_stable_across_two_calls() -> None:
    """P2 regression: ``_load_article_route`` is called twice on the same
    state (translation, then vocabulary). When the base row is missing,
    the first call caches ``base_text=""`` / ``unit_types=()`` and returns
    GROUPED_WINDOWED. Without caching the route decision, the second call
    would see non-None cached fields, re-evaluate an empty profile, and
    misclassify it as SHORT_BATCH -- routing vocabulary to the batch path
    on a missing base. The ``cached_route`` field stabilizes the decision."""
    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    state = _LockedActiveBaseState(
        record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        expected_generation=1,
        base_language="en",
        last_event_sequence=0,
        strategy=strategy,
    )
    conn = _FakeMissingBaseConn()

    first_route = await _load_article_route(conn, state=state)
    assert first_route is ArticleRoute.GROUPED_WINDOWED
    # The state now caches empty base_text/unit_types -- the trap that
    # previously caused the second call to fall through to profiling.
    assert state.base_text == ""
    assert state.unit_types == ()

    second_route = await _load_article_route(conn, state=state)
    assert second_route is ArticleRoute.GROUPED_WINDOWED, (
        "second call (vocabulary) must not re-evaluate the empty profile "
        "and misclassify as SHORT_BATCH"
    )


# ---------------------------------------------------------------------------#
# T4.1b: structured article batch -- route identity in job/run metadata
# ---------------------------------------------------------------------------#
#
# T4.1a only proved the *route label* was correct (job count, window_id
# presence). T4.1b makes STRUCTURED_BATCH an auditable runtime mode:
#
#   - ``input_json.article_route`` records the route on every batch/window job
#   - ``envelope_json.article_route`` + ``envelope_json.document_features``
#     record the route + the deterministic profile signals that drove it
#   - ``reader_runs.policy_version`` records the route-specific policy version
#   - ``operation_fingerprint`` base differs for STRUCTURED_BATCH so a route
#     change (short -> structured on a rebuilt base) triggers
#     ``_supersede_stale_fingerprint_jobs``.
#
# SHORT_BATCH and GROUPED_WINDOWED keep their existing ``*_v1`` fingerprint
# bases (shared) so their idempotency contracts are preserved; the three-way
# distinction is completed by ``article_route`` in ``input_json``.
#
# These tests extend the T4.1a fixtures (same _T41A_BBC_NEAR_THRESHOLD_TEXT,
# _T41A_MEDIUM_TEXT, _LONG_TEXT) so the route classification is identical to
# the T4.1a tests -- only the metadata assertions are new.


async def _load_run_metadata(
    pool: asyncpg.Pool,
    run_id: UUID,
) -> dict:
    """Load ``envelope_json`` AND ``policy_version`` for a run.

    T4.1b asserts on both: ``envelope_json.article_route`` /
    ``envelope_json.document_features`` (route identity + profile signals)
    and ``policy_version`` (route-specific policy version column).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT envelope_json, policy_version FROM reader_runs WHERE id = $1",
            run_id,
        )
    assert row is not None
    return {
        "envelope": dict(row["envelope_json"]),
        "policy_version": row["policy_version"],
    }


def _expected_fingerprint(base: str, strategy: ReaderVariantStrategy) -> str:
    # Plain-text strategy fixtures produce legacy units (no semantic
    # contract_version). Fingerprint includes frozen policy mode.
    return job_bootstrap._compose_operation_fingerprint(
        base,
        strategy,
        semantic_token="sem:legacy:legacy_open:mode:enforce",
    )


def _expected_fingerprint_with_profile(
    base: str,
    strategy: ReaderVariantStrategy,
    input_json: dict,
) -> str:
    return (
        _expected_fingerprint(base, strategy)
        + ":prompt_profile:"
        + str(input_json["translation_prompt_profile_contract_version"])
        + ":"
        + str(input_json["translation_prompt_profile_version"])
        + ":"
        + str(input_json["translation_prompt_profile_fingerprint_hash"])
    )


async def test_t41b_short_batch_route_identity_in_job_and_run_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1b: a SHORT_BATCH article records ``article_route="short_batch"``
    in ``input_json`` and ``envelope_json``, carries
    ``document_features`` in ``envelope_json``, and uses the short-batch
    ``operation_fingerprint`` base + ``policy_version``."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_BBC_NEAR_THRESHOLD_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    assert len(translation_batch_jobs) == 1
    job = translation_batch_jobs[0]

    # Job-level: input_json.article_route
    assert job["input_json"]["article_route"] == "short_batch"
    # Job-level: operation_fingerprint uses the SHORT_BATCH base
    expected_fp = _expected_fingerprint_with_profile(
        job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        strategy,
        job["input_json"],
    )
    assert job["operation_fingerprint"] == expected_fp

    # Run-level: envelope_json.article_route + document_features, and
    # policy_version is the SHORT_BATCH policy.
    run_meta = await _load_run_metadata(strategy_env, job["run_id"])
    assert run_meta["envelope"]["article_route"] == "short_batch"
    assert run_meta["envelope"]["document_features"] is not None
    assert run_meta["envelope"]["document_features"]["extractor_version"]
    assert (
        run_meta["policy_version"]
        == job_bootstrap.TRANSLATION_BATCH_POLICY_VERSION
    )


async def test_t41b_structured_batch_route_identity_distinct_from_short(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1b: a STRUCTURED_BATCH article records
    ``article_route="structured_batch"`` AND uses a DISTINCT
    ``operation_fingerprint`` base + ``policy_version`` from SHORT_BATCH.
    This is the core T4.1b deliverable: STRUCTURED_BATCH is no longer just
    a route label -- it is an auditable runtime mode whose fingerprint
    change triggers ``_supersede_stale_fingerprint_jobs`` on a route
    change."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_MEDIUM_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    jobs = await _load_jobs(strategy_env, record_id)
    translation_batch_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    assert len(translation_batch_jobs) == 1
    job = translation_batch_jobs[0]

    # Job-level: input_json.article_route is structured_batch (NOT short_batch)
    assert job["input_json"]["article_route"] == "structured_batch"
    # Job-level: operation_fingerprint uses the STRUCTURED_BATCH base --
    # DISTINCT from SHORT_BATCH. This is what makes a route change auditable
    # and triggers supersede.
    expected_fp = _expected_fingerprint_with_profile(
        job_bootstrap.TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
        strategy,
        job["input_json"],
    )
    assert job["operation_fingerprint"] == expected_fp
    short_fp = _expected_fingerprint(
        job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT, strategy
    )
    assert job["operation_fingerprint"] != short_fp, (
        "STRUCTURED_BATCH must have a distinct fingerprint base from "
        "SHORT_BATCH so a route change triggers _supersede_stale_fingerprint_jobs"
    )

    # Run-level: envelope + policy_version reflect the structured route.
    run_meta = await _load_run_metadata(strategy_env, job["run_id"])
    assert run_meta["envelope"]["article_route"] == "structured_batch"
    assert run_meta["envelope"]["document_features"] is not None
    assert (
        run_meta["envelope"]["document_features"]["estimated_word_count"] > 1100
    ), "medium fixture must exceed the short word cap (route decision signal)"
    assert (
        run_meta["policy_version"]
        == job_bootstrap.TRANSLATION_STRUCTURED_BATCH_POLICY_VERSION
    )
    assert (
        run_meta["policy_version"]
        != job_bootstrap.TRANSLATION_BATCH_POLICY_VERSION
    )

    # Vocabulary layer also carries the structured route identity.
    vocab_batch_jobs = [
        j for j in jobs if j["job_type"] == "build_vocabulary_layer_article"
    ]
    assert len(vocab_batch_jobs) == 1
    vjob = vocab_batch_jobs[0]
    assert vjob["input_json"]["article_route"] == "structured_batch"
    expected_vfp = _expected_fingerprint(
        job_bootstrap.VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
        strategy,
    )
    assert vjob["operation_fingerprint"] == expected_vfp
    vrun_meta = await _load_run_metadata(strategy_env, vjob["run_id"])
    assert vrun_meta["envelope"]["article_route"] == "structured_batch"
    assert (
        vrun_meta["policy_version"]
        == job_bootstrap.VOCABULARY_STRUCTURED_BATCH_POLICY_VERSION
    )


async def test_t41b_grouped_windowed_route_identity_in_job_and_run_metadata(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1b: a GROUPED_WINDOWED article records
    ``article_route="grouped_windowed"`` on every window job, carries
    ``document_features`` in ``envelope_json``, and keeps the shared
    ``*_v1`` fingerprint base + ``policy_version`` so the T3.1/T3.2b
    idempotency contract is preserved."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    jobs = await _load_jobs(strategy_env, record_id)
    translation_window_jobs = [
        j for j in jobs if j["job_type"] == "translate_article"
    ]
    assert len(translation_window_jobs) >= 2

    expected_fp = _expected_fingerprint_with_profile(
        job_bootstrap.TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        strategy,
        translation_window_jobs[0]["input_json"],
    )
    for job in translation_window_jobs:
        # Every window job carries the grouped_windowed route identity.
        assert job["input_json"]["article_route"] == "grouped_windowed"
        # Grouped/windowed keeps the shared *_v1 base (NOT the structured
        # base) so its idempotency contract is preserved.
        assert job["operation_fingerprint"] == expected_fp
        run_meta = await _load_run_metadata(strategy_env, job["run_id"])
        assert run_meta["envelope"]["article_route"] == "grouped_windowed"
        assert run_meta["envelope"]["document_features"] is not None
        assert (
            run_meta["policy_version"]
            == job_bootstrap.TRANSLATION_BATCH_POLICY_VERSION
        )


async def test_t41b_no_per_unit_fanout_regression_across_three_routes(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1b no-regression guard: none of the three routes (short /
    structured / grouped) may produce per-unit ``translate_unit`` or
    ``build_vocabulary_layer`` jobs. T4.1b must not reintroduce the
    legacy per-unit fan-out that T1.1 / T3.1 / T3.2b eliminated."""
    fixtures = [
        (_T41A_BBC_NEAR_THRESHOLD_TEXT, "short_batch"),
        (_T41A_MEDIUM_TEXT, "structured_batch"),
        (_LONG_TEXT, "grouped_windowed"),
    ]
    for plain_text, expected_route in fixtures:
        user_id = await insert_user(strategy_env)
        record_id = await _submit_with_strategy(
            strategy_env,
            user_id=user_id,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            plain_text=plain_text,
        )
        service = EnhancementJobBootstrapService(pool=strategy_env)
        await service.bootstrap_missing_jobs(
            record_id=record_id, user_id=user_id
        )

        jobs = await _load_jobs(strategy_env, record_id)
        # No per-unit translation fan-out
        assert not any(j["job_type"] == "translate_unit" for j in jobs), (
            f"{expected_route}: per-unit translate_unit jobs must not exist"
        )
        # No per-unit vocabulary fan-out
        assert not any(
            j["job_type"] == "build_vocabulary_layer" for j in jobs
        ), (
            f"{expected_route}: per-unit build_vocabulary_layer jobs must not exist"
        )
        # Every batch/window job carries the expected route identity
        batch_jobs = [
            j
            for j in jobs
            if j["job_type"]
            in ("translate_article", "build_vocabulary_layer_article")
        ]
        assert batch_jobs, f"{expected_route}: expected batch jobs, got none"
        for j in batch_jobs:
            assert j["input_json"]["article_route"] == expected_route, (
                f"{expected_route}: job {j['job_type']} has "
                f"article_route={j['input_json']['article_route']!r}"
            )


# ---------------------------------------------------------------------------#
# T4.1c: compact grammar batch path (SHORT_BATCH / STRUCTURED_BATCH)
# ---------------------------------------------------------------------------#
#
# Requirement: SHORT_BATCH and STRUCTURED_BATCH grammar no longer defaults to
# the heavy Z+ analysis-window path. Instead, a single
# ``build_grammar_bundle`` / ``unit_range`` batch job covers all unpublished
# units in one LLM call. GROUPED_WINDOWED keeps the Z+ path
# (``build_grammar_bundle_window`` jobs + ``analysis_windows`` rows).
# No route produces per-unit ``build_grammar_bundle`` / ``unit`` jobs.
#
# T4.1c reuses the existing ``build_grammar_bundle`` job_type (already in
# the reader_jobs.job_type CHECK constraint from migration 0017) for both
# batch (target_type='unit_range') and per-unit (target_type='unit') paths.
# They are distinguished by target_type and operation_fingerprint base.


async def _count_grammar_jobs_by_type(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> dict[str, int]:
    """Count grammar jobs by job_type for a record.

    Note: ``build_grammar_bundle`` covers both batch (target_type='unit_range')
    and per-unit (target_type='unit') jobs. Use
    ``_count_grammar_jobs_by_target_type`` to distinguish them.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT job_type, COUNT(*) AS cnt
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type IN (
                  'build_grammar_bundle',
                  'build_grammar_bundle_window'
              )
            GROUP BY job_type
            """,
            record_id,
        )
    return {str(row["job_type"]): int(row["cnt"]) for row in rows}


async def _count_grammar_jobs_by_target_type(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> dict[str, int]:
    """Count grammar jobs by ``job_type:target_type`` for a record.

    Returns keys like ``'build_grammar_bundle:unit_range'`` (batch) and
    ``'build_grammar_bundle:unit'`` (per-unit) so callers can distinguish
    the compact batch path from the legacy per-unit fan-out.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT job_type, target_type, COUNT(*) AS cnt
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type IN (
                  'build_grammar_bundle',
                  'build_grammar_bundle_window'
              )
            GROUP BY job_type, target_type
            """,
            record_id,
        )
    return {
        f"{row['job_type']}:{row['target_type']}": int(row["cnt"])
        for row in rows
    }


async def _count_analysis_windows(
    pool: asyncpg.Pool,
    record_id: UUID,
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM analysis_windows aw
                JOIN layer_analysis_plans lap ON aw.plan_id = lap.id
                WHERE lap.reading_record_id = $1
                """,
                record_id,
            )
        )


async def test_t41c_short_article_routes_to_compact_grammar_batch(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1c: a SHORT_BATCH article creates a single
    ``build_grammar_bundle`` / ``unit_range`` batch job and does NOT
    create Z+ ``analysis_windows`` or per-unit ``build_grammar_bundle``
    / ``unit`` jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_BBC_NEAR_THRESHOLD_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    jobs = await _load_jobs(strategy_env, record_id)
    grammar_by_target = await _count_grammar_jobs_by_target_type(
        strategy_env, record_id
    )
    analysis_window_count = await _count_analysis_windows(
        strategy_env, record_id
    )

    # Compact batch job created (build_grammar_bundle / unit_range)
    assert grammar_by_target.get("build_grammar_bundle:unit_range") == 1, (
        f"SHORT_BATCH: expected 1 build_grammar_bundle:unit_range job, "
        f"got {grammar_by_target}"
    )
    # No Z+ analysis windows
    assert analysis_window_count == 0, (
        f"SHORT_BATCH: expected 0 analysis_windows, got {analysis_window_count}"
    )
    # No per-unit grammar fan-out
    assert grammar_by_target.get("build_grammar_bundle:unit", 0) == 0, (
        "SHORT_BATCH: per-unit build_grammar_bundle:unit jobs must not exist"
    )
    # No Z+ window jobs
    assert grammar_by_target.get("build_grammar_bundle_window:unit_range", 0) == 0, (
        "SHORT_BATCH: build_grammar_bundle_window jobs must not exist"
    )

    # The batch job carries the short_batch route identity and the
    # SHORT_BATCH fingerprint base.
    batch_jobs = [
        j
        for j in jobs
        if j["job_type"] == "build_grammar_bundle"
        and j["target_type"] == "unit_range"
    ]
    assert len(batch_jobs) == 1
    job = batch_jobs[0]
    assert job["input_json"]["article_route"] == "short_batch"
    expected_fp = _expected_fingerprint(
        job_bootstrap.GRAMMAR_BATCH_OPERATION_FINGERPRINT, strategy
    )
    assert job["operation_fingerprint"] == expected_fp
    # Structured fingerprint must NOT match
    structured_fp = _expected_fingerprint(
        job_bootstrap.GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT, strategy
    )
    assert job["operation_fingerprint"] != structured_fp

    # Run-level: envelope carries the route identity + policy_version.
    run_meta = await _load_run_metadata(strategy_env, job["run_id"])
    assert run_meta["envelope"]["article_route"] == "short_batch"
    assert run_meta["envelope"]["document_features"] is not None
    assert (
        run_meta["policy_version"]
        == job_bootstrap.GRAMMAR_BATCH_POLICY_VERSION
    )


async def test_t41c_structured_article_routes_to_compact_grammar_batch(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1c: a STRUCTURED_BATCH article creates a single
    ``build_grammar_bundle`` / ``unit_range`` batch job with the
    STRUCTURED_BATCH fingerprint base and does NOT create Z+
    ``analysis_windows`` or per-unit ``build_grammar_bundle`` / ``unit``
    jobs."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_T41A_MEDIUM_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    strategy = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    jobs = await _load_jobs(strategy_env, record_id)
    grammar_by_target = await _count_grammar_jobs_by_target_type(
        strategy_env, record_id
    )
    analysis_window_count = await _count_analysis_windows(
        strategy_env, record_id
    )

    assert grammar_by_target.get("build_grammar_bundle:unit_range") == 1, (
        f"STRUCTURED_BATCH: expected 1 build_grammar_bundle:unit_range job, "
        f"got {grammar_by_target}"
    )
    assert analysis_window_count == 0, (
        f"STRUCTURED_BATCH: expected 0 analysis_windows, "
        f"got {analysis_window_count}"
    )
    assert grammar_by_target.get("build_grammar_bundle:unit", 0) == 0
    assert grammar_by_target.get("build_grammar_bundle_window:unit_range", 0) == 0

    batch_jobs = [
        j
        for j in jobs
        if j["job_type"] == "build_grammar_bundle"
        and j["target_type"] == "unit_range"
    ]
    assert len(batch_jobs) == 1
    job = batch_jobs[0]
    assert job["input_json"]["article_route"] == "structured_batch"
    # STRUCTURED_BATCH gets a DISTINCT fingerprint base from SHORT_BATCH.
    expected_fp = _expected_fingerprint(
        job_bootstrap.GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT, strategy
    )
    assert job["operation_fingerprint"] == expected_fp
    short_fp = _expected_fingerprint(
        job_bootstrap.GRAMMAR_BATCH_OPERATION_FINGERPRINT, strategy
    )
    assert job["operation_fingerprint"] != short_fp, (
        "STRUCTURED_BATCH grammar must have a distinct fingerprint base "
        "from SHORT_BATCH so a route change triggers supersede"
    )

    run_meta = await _load_run_metadata(strategy_env, job["run_id"])
    assert run_meta["envelope"]["article_route"] == "structured_batch"
    assert run_meta["envelope"]["document_features"] is not None
    assert (
        run_meta["policy_version"]
        == job_bootstrap.GRAMMAR_STRUCTURED_BATCH_POLICY_VERSION
    )
    assert (
        run_meta["policy_version"]
        != job_bootstrap.GRAMMAR_BATCH_POLICY_VERSION
    )


async def test_t41c_long_article_keeps_zplus_grammar_window_path(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1c: a GROUPED_WINDOWED article keeps the Z+ analysis-window path.
    ``build_grammar_bundle_window`` jobs + ``analysis_windows`` rows are
    created. No ``build_grammar_bundle`` / ``unit_range`` batch job or
    per-unit ``build_grammar_bundle`` / ``unit`` job is created."""
    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        plain_text=_LONG_TEXT,
    )

    service = EnhancementJobBootstrapService(pool=strategy_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    grammar_by_target = await _count_grammar_jobs_by_target_type(
        strategy_env, record_id
    )
    analysis_window_count = await _count_analysis_windows(
        strategy_env, record_id
    )

    # Z+ window jobs created
    assert grammar_by_target.get("build_grammar_bundle_window:unit_range", 0) > 0, (
        "GROUPED_WINDOWED: expected build_grammar_bundle_window jobs"
    )
    # Z+ analysis windows created
    assert analysis_window_count > 0, (
        "GROUPED_WINDOWED: expected analysis_windows rows"
    )
    # No compact grammar batch job (build_grammar_bundle / unit_range)
    assert grammar_by_target.get("build_grammar_bundle:unit_range", 0) == 0, (
        "GROUPED_WINDOWED: build_grammar_bundle:unit_range must not exist"
    )
    # No per-unit grammar fan-out
    assert grammar_by_target.get("build_grammar_bundle:unit", 0) == 0, (
        "GROUPED_WINDOWED: per-unit build_grammar_bundle:unit must not exist"
    )


async def test_t41c_no_per_unit_grammar_fanout_across_three_routes(
    strategy_env: asyncpg.Pool,
) -> None:
    """T4.1c no-regression guard: none of the three routes (short /
    structured / grouped) may produce per-unit ``build_grammar_bundle``
    / ``unit`` jobs. T4.1c must not reintroduce the legacy per-unit
    grammar fan-out."""
    fixtures = [
        (_T41A_BBC_NEAR_THRESHOLD_TEXT, "short_batch"),
        (_T41A_MEDIUM_TEXT, "structured_batch"),
        (_LONG_TEXT, "grouped_windowed"),
    ]
    for plain_text, expected_route in fixtures:
        user_id = await insert_user(strategy_env)
        record_id = await _submit_with_strategy(
            strategy_env,
            user_id=user_id,
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            plain_text=plain_text,
        )
        service = EnhancementJobBootstrapService(pool=strategy_env)
        await service.bootstrap_missing_jobs(
            record_id=record_id, user_id=user_id
        )

        grammar_by_target = await _count_grammar_jobs_by_target_type(
            strategy_env, record_id
        )
        assert grammar_by_target.get("build_grammar_bundle:unit", 0) == 0, (
            f"{expected_route}: per-unit build_grammar_bundle:unit jobs "
            f"must not exist"
        )
