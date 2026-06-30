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
    _fingerprint_matches_base,
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

_PLAIN_TEXT = (
    "First sentence for strategy bootstrap.\n\n"
    "Second paragraph for strategy bootstrap.\n\n"
    "Third paragraph for strategy bootstrap."
)


@pytest.fixture
async def strategy_env() -> asyncpg.Pool:
    schema_name = f"test_reader_bootstrap_strategy_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
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
) -> UUID:
    """Submit a plain-text article with explicit strategy fields; return record_id."""
    service = ArticleReadyPersistenceService(pool=pool)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=_PLAIN_TEXT,
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
                operation_fingerprint,
                input_hash,
                input_json,
                run_id
            FROM (
                SELECT
                    j.id AS job_id,
                    j.job_type,
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
    translation_jobs = [j for j in jobs if j["job_type"] == "translate_unit"]
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
    vocab_jobs = [j for j in jobs if j["job_type"] == "build_vocabulary_layer"]
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
    await service.bootstrap_missing_jobs(record_id=record_daily, user_id=user_id)
    await service.bootstrap_missing_jobs(record_id=record_exam, user_id=user_id)

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
    translation_jobs = [j for j in jobs if j["job_type"] == "translate_unit"]
    assert len(translation_jobs) >= 1
    for job in translation_jobs:
        assert job["operation_fingerprint"] == modified_fingerprint
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
    async with strategy_env.acquire() as conn:
        old_jobs = await conn.fetch(
            """
            SELECT id, operation_fingerprint, status, rationale_code
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'translate_unit'
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
              AND job_type = 'translate_unit'
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

    Uses the standalone TranslationJobBootstrapService to enqueue a job,
    then monkeypatches the resolver and re-bootstraps via the enhancement
    service. The old job is superseded; a worker claim for translation
    must only see the new queued job.
    """
    from datetime import timedelta

    from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

    user_id = await insert_user(strategy_env)
    record_id = await _submit_with_strategy(
        strategy_env,
        user_id=user_id,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    # Step 1: enqueue via standalone service (plain-text submit path).
    standalone = TranslationJobBootstrapService(pool=strategy_env)
    first = await standalone.bootstrap_translation_run(
        record_id=record_id, user_id=user_id
    )
    first_fp = first.operation_fingerprint

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

    enhancement = EnhancementJobBootstrapService(pool=strategy_env)
    await enhancement.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)

    # Step 3: worker claim must only get the new fingerprint job.
    runtime = ReaderJobRuntime(pool=strategy_env)
    claim = await runtime.claim_next_job(
        lease_owner="test_worker",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.operation_fingerprint != first_fp
    assert claim.operation_fingerprint.startswith(
        job_bootstrap.TRANSLATION_OPERATION_FINGERPRINT + ":"
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


def test_fingerprint_matches_base_rejects_different_base() -> None:
    """A fingerprint with a different base must not match."""
    assert not _fingerprint_matches_base(
        "vocabulary_unit_v1:hash", "translation_unit"
    )
    assert not _fingerprint_matches_base(
        "grammar_bundle_unit_v1:hash", "vocabulary_unit_v1"
    )
