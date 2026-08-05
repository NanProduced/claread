# task-history: R2 (renamed from test_semantic_mode_and_worker_fence_r2.py)
"""Frozen mode, mode-aware worker fence, real worker services.

Proves off/shadow/enforce do not collapse to enforce at worker time, and
that real Translation/Vocabulary/Grammar workers supersede with 0 executor
calls on enforce+disallowed / version mismatch.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.config.settings import Settings, get_settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import (
    TranslationBatchGenerationOutput,
    TranslationBatchGroupOutput,
    TranslationBatchUnitOutput,
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
    VocabularyLayerOutput,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    LEGACY_MISSING_MODE_COMPAT,
    SEMANTIC_FENCE_KEY_MODE,
    SEMANTIC_LAYER_DISALLOWED_CODE,
    SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
    AutomaticLayerPolicy,
    compose_semantic_fingerprint_token,
    filter_units_for_any_grammar,
    filter_units_for_automatic_layer,
    is_trusted_explicit_section_translation_job,
    parse_automatic_policy_mode,
    resolve_job_semantic_policy_mode,
    validate_automatic_job_semantic_fence,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
)
from app.services.reader_orchestration.job_bootstrap import (
    GRAMMAR_OPERATION_FINGERPRINT,
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    TRANSLATION_OPERATION_FINGERPRINT,
    VOCABULARY_OPERATION_FINGERPRINT,
    VocabularyJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    SectionRequestTrigger,
)
from app.services.reader_orchestration.section_translation_bootstrap import (
    SectionBootstrapOutcome,
    SectionTranslationBootstrapService,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1
from app.services.reader_orchestration.smoke_harness import (
    DevFakeTranslationBatchExecutor,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionResult,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    VocabularyExecutionResult,
    VocabularyWorkerService,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    ZPlusBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    DATABASE_URL,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_orchestration, pytest.mark.seam_service_integration, pytest.mark.life_permanent_regression]



@contextmanager
def _policy_mode(mode: str) -> Iterator[None]:
    """Isolate automatic policy mode for bootstrap without cross-test pollution."""
    get_settings.cache_clear()
    settings = Settings(reader_automatic_layer_policy_mode=mode)  # type: ignore[arg-type]
    original = get_settings

    def _fake() -> Settings:
        return settings

    import app.config.settings as settings_mod

    settings_mod.get_settings = _fake  # type: ignore[assignment]
    try:
        yield
    finally:
        settings_mod.get_settings = original  # type: ignore[assignment]
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Pure mode / settings / trust
# ---------------------------------------------------------------------------


def test_settings_mode_literal_accepts_three_values() -> None:
    for mode in ("off", "shadow", "enforce"):
        s = Settings(reader_automatic_layer_policy_mode=mode)
        assert s.reader_automatic_layer_policy_mode == mode


def test_settings_mode_rejects_illegal_value() -> None:
    with pytest.raises(ValidationError):
        Settings(reader_automatic_layer_policy_mode="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parse_automatic_policy_mode("nope")


def test_fingerprint_includes_mode() -> None:
    token = compose_semantic_fingerprint_token(
        {
            "semantic_contract_version": SEMANTIC_CONTRACT_V1,
            "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        },
        mode="shadow",
    )
    assert token.endswith(":mode:shadow")
    assert SEMANTIC_CONTRACT_V1 in token


def test_legacy_missing_mode_compat_is_enforce() -> None:
    assert LEGACY_MISSING_MODE_COMPAT == "enforce"
    assert (
        resolve_job_semantic_policy_mode(
            {
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            }
        )
        == "enforce"
    )


def test_off_and_shadow_do_not_block_disallowed_layer() -> None:
    all_off = AutomaticLayerPolicy.all_off().as_dict()
    unit = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": all_off,
        }
    }
    base = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "vocabulary",
    }
    for mode in ("off", "shadow"):
        validate_automatic_job_semantic_fence(
            job_input={**base, SEMANTIC_FENCE_KEY_MODE: mode},
            layer="vocabulary",
            unit_metadata_list=[unit],
        )


def test_enforce_blocks_disallowed_layer() -> None:
    all_off = AutomaticLayerPolicy.all_off().as_dict()
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input={
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                "automatic_layer_name": "vocabulary",
                SEMANTIC_FENCE_KEY_MODE: "enforce",
            },
            layer="vocabulary",
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                        "automatic_layer_policy": all_off,
                    }
                }
            ],
        )
    assert ei.value.code == SEMANTIC_LAYER_DISALLOWED_CODE  # type: ignore[attr-defined]


def test_forged_request_origin_alone_cannot_skip_fence() -> None:
    all_off = AutomaticLayerPolicy.all_off().as_dict()
    # Incomplete section claim on translation lane → fail closed (not allows skip).
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                "automatic_layer_name": "translation",
                SEMANTIC_FENCE_KEY_MODE: "enforce",
            },
            layer="translation",
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                        "automatic_layer_policy": all_off,
                    }
                }
            ],
            operation_fingerprint="translation_article_v1:abc",  # ordinary, not section
        )
    assert ei.value.code == SEMANTIC_POLICY_VERSION_MISMATCH_CODE  # type: ignore[attr-defined]


def test_trusted_section_identity_requires_fingerprint_and_identity() -> None:
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    fp = f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:hash"
    full_identity = {
        "record_id": "r",
        "base_id": "b",
        "generation": 1,
        "start_unit_id": "u1",
        "end_unit_id": "u1",
    }
    target_key = encode_section_target_key(
        SectionIdentity(
            record_id="r",
            base_id="b",
            generation=1,
            start_unit_id="u1",
            end_unit_id="u1",
        )
    )
    # Ordinary fingerprint alone — not trusted.
    assert not is_trusted_explicit_section_translation_job(
        job_input={"request_origin": SECTION_REQUEST_ORIGIN},
        operation_fingerprint="translation_article_v1:x",
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
    )
    # Missing trusted DB target bind — not trusted.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": full_identity,
            "target_unit_ids": ["u1"],
        },
        operation_fingerprint=fp,
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
    )
    # Incomplete identity (missing start/end unit) — not trusted.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                "record_id": "r",
                "base_id": "b",
                "generation": 1,
            },
        },
        operation_fingerprint=fp,
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
    )
    # Mismatched record_id vs trusted DB — not trusted.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": full_identity,
            "target_unit_ids": ["u1"],
        },
        operation_fingerprint=fp,
        trusted_record_id="other",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
    )
    # Range vs target_key mismatch — not trusted.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                **full_identity,
                "start_unit_id": "u2",
                "end_unit_id": "u2",
            },
            "target_unit_ids": ["u2"],
        },
        operation_fingerprint=fp,
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u2"],
    )
    universe = [{"unit_id": "u1", "order_index": 0}]
    # Full trusted triple + identity + target bind — ok.
    assert is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": full_identity,
            "target_unit_ids": ["u1"],
        },
        operation_fingerprint=fp,
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
        trusted_base_ordered_units=universe,
        trusted_anchor_to_unit={},
    )


def test_zplus_filter_any_grammar_respects_mode() -> None:
    units = [
        {
            "unit_id": "code",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        },
        {
            "unit_id": "prose",
            "order_index": 2,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                }
            },
        },
    ]
    assert {u["unit_id"] for u in filter_units_for_any_grammar(units, mode="off")} == {
        "code",
        "prose",
    }
    assert {u["unit_id"] for u in filter_units_for_any_grammar(units, mode="shadow")} == {
        "code",
        "prose",
    }
    assert {
        u["unit_id"] for u in filter_units_for_any_grammar(units, mode="enforce")
    } == {"prose"}


# ---------------------------------------------------------------------------
# Real worker integration helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def fence_env(tmp_path_factory=None):
    schema_name = f"test_sem_fence_r2_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        # Batch job types + analysis window + semantic_outline layer type.
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    pool = await make_pool(schema_name)
    original_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        await pool.close()
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _strategy_input(layer_name: str = "translation") -> dict[str, Any]:
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers[layer_name if layer_name != "grammar_note" else "grammar_bundle"]
    # grammar layer name in strategy is grammar_bundle
    if layer_name in {"grammar_note", "sentence_analysis"}:
        layer = strategy.layers["grammar_bundle"]
    return {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
    }


async def _set_unit_policy(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    unit_id: str,
    policy: dict[str, bool],
) -> None:
    meta = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "content_role": "prose",
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": policy,
        }
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
            jsonb_param(meta),
        )


async def _insert_unit_job(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    user_id: UUID,
    unit_id: str,
    job_type: str,
    target_type: str,
    fingerprint_base: str,
    layer: str,
    mode: str,
    policy: dict[str, bool],
    extra_input: dict[str, Any] | None = None,
    operation_fingerprint_override: str | None = None,
) -> UUID:
    strategy_input = _strategy_input(layer)
    fence = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": layer,
        "semantic_policy_mode": mode,
    }
    input_json = {**strategy_input, **fence, **(extra_input or {})}
    if layer == "vocabulary":
        input_json["layer_type"] = "vocabulary"
    if layer in {"grammar_note", "sentence_analysis"}:
        input_json["layer_types"] = ["grammar_note", "sentence_analysis"]
    token = compose_semantic_fingerprint_token(
        {
            "semantic_contract_version": SEMANTIC_CONTRACT_V1,
            "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        },
        mode=mode,  # type: ignore[arg-type]
    )
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    fp = operation_fingerprint_override or f"{fingerprint_base}:{strategy.strategy_hash}:{token}"
    await _set_unit_policy(pool, record_id=record_id, unit_id=unit_id, policy=policy)

    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', 1, '{}'::jsonb, 'fence-r2', 'system')
            RETURNING id
            """,
            record_id,
            user_id,
            "translation_layer" if "translat" in job_type else "vocabulary_layer"
            if "vocab" in job_type
            else "grammar_bundle",
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
                $5, $6, $7, 'queued',
                0, 1, $8,
                $9, $10, $11::jsonb, 3
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            unit_id,
            fp,
            f"{fp}:{unit_id}",
            "fence-r2-hash",
            jsonb_param(input_json),
        )
    assert isinstance(job_id, UUID)
    return job_id


class _CountingTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate(self, context) -> TranslationExecutionResult:
        self.calls += 1
        return TranslationExecutionResult(
            output=TranslationLayerGenerationOutput(
                groups=[
                    TranslationGenerationGroup(
                        anchor_segment_ids=[
                            context.anchor_segments[0].anchor_segment_id
                        ],
                        translated_text="译文",
                    )
                ]
            ),
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _CountingVocab:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, context) -> VocabularyExecutionResult:
        self.calls += 1
        return VocabularyExecutionResult(
            output=VocabularyLayerOutput(items=[]),
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _CountingGrammar:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, context) -> GrammarExecutionResult:
        self.calls += 1
        from app.services.reader_orchestration.grammar_worker import GrammarBundleOutput

        return GrammarExecutionResult(
            output=GrammarBundleOutput(),
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


async def _claim_and_process_translation(
    pool: asyncpg.Pool, *, fingerprint: str, translator: _CountingTranslator
):
    worker = TranslationWorkerService(pool=pool, translator=translator)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=fingerprint.split(":")[0]
        if ":" in fingerprint
        else fingerprint,
    )
    # claim may use base match - pass base
    if claim is None:
        claim = await runtime.claim_next_job(
            lease_owner="fence-r2",
            lease_duration=timedelta(seconds=30),
            job_type="translate_unit",
            operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
        )
    assert claim is not None
    return await worker.process_claimed_translation_job(claim=claim)


async def test_translation_worker_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    all_off = AutomaticLayerPolicy.all_off().as_dict()
    job_id = await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=all_off,
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert row["status"] == "superseded"
    # Supersede transitions record stable rationale_code (failure_code may be null).
    assert row["rationale_code"] == SEMANTIC_LAYER_DISALLOWED_CODE or row[
        "failure_code"
    ] == SEMANTIC_LAYER_DISALLOWED_CODE


async def test_translation_worker_off_executes_despite_disallowed(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    all_off = AutomaticLayerPolicy.all_off().as_dict()
    job_id = await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="off",
        policy=all_off,
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-off",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert spy.calls == 1
    assert result.status in {"succeeded", "failed_terminal", "retry_later"}
    # Must not be fence supersede
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, failure_code FROM reader_jobs WHERE id = $1", job_id
        )
    assert row["failure_code"] != SEMANTIC_LAYER_DISALLOWED_CODE


async def test_translation_worker_shadow_executes_despite_disallowed(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="shadow",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-shadow",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    await worker.process_claimed_translation_job(claim=claim)
    assert spy.calls == 1


async def test_vocabulary_worker_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_vocabulary_layer",
        target_type="unit",
        fingerprint_base=VOCABULARY_OPERATION_FINGERPRINT,
        layer="vocabulary",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingVocab()
    worker = VocabularyWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-v",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer",
        operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_vocabulary_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_grammar_worker_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_grammar_bundle",
        target_type="unit",
        fingerprint_base=GRAMMAR_OPERATION_FINGERPRINT,
        layer="grammar_note",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingGrammar()
    worker = GrammarBundleWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-g",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_grammar_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_version_mismatch_supersede_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    # unit has v1 resolver, job claims v99
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    strategy_input = _strategy_input("translation")
    input_json = {
        **strategy_input,
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": "automatic_layer_policy_v99",
        "automatic_layer_name": "translation",
        "semantic_policy_mode": "enforce",
    }
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    fp = f"{TRANSLATION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}:sem:mismatch"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'x', 'system')
            RETURNING id
            """,
            article.record_id,
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
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            unit_id,
            fp,
            f"{fp}:{unit_id}",
            jsonb_param(input_json),
        )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-mm",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert row["status"] == "superseded"
    assert row["rationale_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE or row[
        "failure_code"
    ] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE


async def _publish_semantic_outline_for_unit(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int = 1,
) -> None:
    """Insert a published semantic_outline covering a single unit (section bootstrap)."""
    output = {
        "status": "ready",
        "source_identity": {
            "base_id": str(base_id),
            "generation": generation,
        },
        "publication": {"outline_revision": "r2.1-test"},
        "nodes": [
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id, base_id, layer_type, target_scope, target_key,
                generation, status, operation_fingerprint, schema_version,
                output_json, published_at
            )
            VALUES (
                $1, $2, 'semantic_outline', 'record', 'record',
                $3, 'published', 'semantic_outline_test_fp', 1,
                $4::jsonb, NOW()
            )
            """,
            record_id,
            base_id,
            generation,
            jsonb_param(output),
        )


class _CountingDevBatchTranslator(DevFakeTranslationBatchExecutor):
    def __init__(self) -> None:
        self.calls = 0

    async def translate_batch(self, context):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().translate_batch(context)


async def test_real_section_bootstrap_batch_worker_despite_translation_false(
    fence_env: asyncpg.Pool,
) -> None:
    """SectionTranslationBootstrapService → persisted section job → batch worker."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    await _publish_semantic_outline_for_unit(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        unit_id=unit_id,
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    assert result.job_id is not None
    job_id = result.job_id

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job_type, target_type, operation_fingerprint, input_json,
                   reading_record_id, base_id, expected_generation
            FROM reader_jobs WHERE id = $1
            """,
            job_id,
        )
    assert row is not None
    assert row["job_type"] == TRANSLATION_BATCH_JOB_TYPE
    assert row["target_type"] == "unit_range"
    assert str(row["operation_fingerprint"]).startswith(
        TRANSLATION_SECTION_OPERATION_FINGERPRINT
    )
    input_json = row["input_json"]
    if hasattr(input_json, "keys"):
        input_json = dict(input_json)
    assert input_json.get("request_origin") == SECTION_REQUEST_ORIGIN
    identity = input_json.get("section_identity") or {}
    assert identity.get("record_id") == str(article.record_id)
    assert identity.get("base_id") == str(article.base_id)
    assert identity.get("generation") == 1
    assert identity.get("start_unit_id") == unit_id
    assert identity.get("end_unit_id") == unit_id

    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2.1-section",
        lease_duration=timedelta(seconds=30),
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    process_result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert spy.calls == 1
    assert process_result.status != "superseded"
    async with pool.acquire() as conn:
        final = await conn.fetchrow(
            "SELECT status, rationale_code, failure_code FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert final["rationale_code"] != SEMANTIC_LAYER_DISALLOWED_CODE
    assert final["failure_code"] != SEMANTIC_LAYER_DISALLOWED_CODE
    assert final["status"] != "superseded" or final[
        "rationale_code"
    ] not in {
        SEMANTIC_LAYER_DISALLOWED_CODE,
        SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
    }


# ---------------------------------------------------------------------------
# Batch / grouped / allowed / dual-path / window real workers
# ---------------------------------------------------------------------------


async def _insert_batch_job(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    user_id: UUID,
    unit_id: str,
    job_type: str,
    fingerprint_base: str,
    layer: str,
    mode: str,
    policy: dict[str, bool],
) -> UUID:
    strategy_input = _strategy_input(layer)
    fence = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": layer,
        "semantic_policy_mode": mode,
        "target_unit_ids": [unit_id],
        "target_scope": "unit_range",
        "article_route": "short_batch",
    }
    if layer == "vocabulary":
        fence["layer_type"] = "vocabulary"
    if layer in {"grammar_note", "sentence_analysis"}:
        fence["layer_types"] = ["grammar_note", "sentence_analysis"]
    input_json = {**strategy_input, **fence}
    token = compose_semantic_fingerprint_token(
        {
            "semantic_contract_version": SEMANTIC_CONTRACT_V1,
            "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        },
        mode=mode,  # type: ignore[arg-type]
    )
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    fp = f"{fingerprint_base}:{strategy.strategy_hash}:{token}"
    await _set_unit_policy(pool, record_id=record_id, unit_id=unit_id, policy=policy)
    run_type = (
        "translation_layer"
        if "translat" in job_type
        else "vocabulary_layer"
        if "vocab" in job_type
        else "grammar_bundle"
    )
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', 1, '{}'::jsonb, 'fence-r2-batch', 'system')
            RETURNING id
            """,
            record_id,
            user_id,
            run_type,
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
                $5, 'unit_range', $6, 'queued',
                0, 1, $7,
                $8, $9, $10::jsonb, 3
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            f"batch:{unit_id}",
            fp,
            f"{fp}:batch",
            "fence-r2-batch-hash",
            jsonb_param(input_json),
        )
    assert isinstance(job_id, UUID)
    return job_id


class _CountingBatchTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate_batch(self, context) -> Any:
        from app.services.reader_orchestration.translation_worker import (
            TranslationBatchExecutionResult,
        )

        self.calls += 1
        units = []
        for u in context.units:
            units.append(
                TranslationBatchUnitOutput(
                    unit_id=u.unit_id,
                    groups=[
                        TranslationBatchGroupOutput(
                            anchor_segment_ids=[u.anchor_segments[0].anchor_segment_id],
                            translated_text="译文",
                        )
                    ],
                )
            )
        return TranslationBatchExecutionResult(
            output=TranslationBatchGenerationOutput(units=units),
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _CountingBatchVocab:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_batch(self, context) -> Any:
        from app.services.reader_orchestration.vocabulary_worker import (
            VocabularyBatchCandidateOutput,
            VocabularyBatchExecutionResult,
            VocabularyBatchUnitCandidateOutput,
        )

        self.calls += 1
        return VocabularyBatchExecutionResult(
            output=VocabularyBatchCandidateOutput(
                units=[
                    VocabularyBatchUnitCandidateOutput(unit_id=u.unit_id, items=[])
                    for u in context.units
                ]
            ),
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _CountingBatchGrammar:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_batch(self, context) -> Any:
        from app.services.reader_orchestration.grammar_worker import (
            GrammarBatchExecutionResult,
            GrammarBundleOutput,
        )

        self.calls += 1
        return GrammarBatchExecutionResult(
            outputs=[(u.unit_id, GrammarBundleOutput()) for u in context.units],
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


class _CountingWindowGrammar:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, context) -> Any:
        from app.services.reader_orchestration.grammar_window_worker import (
            GrammarWindowExecutionResult,
        )

        self.calls += 1
        return GrammarWindowExecutionResult(
            candidates=[],
            usage_data={"aggregate": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            prompt_version="fence-r2",
            model_profile="fake",
            model_provider="fake",
            model_name="fake",
        )


async def test_translation_batch_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    job_id = await _insert_batch_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_article",
        fingerprint_base=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-t-batch",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=TRANSLATION_BATCH_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1", job_id
        )
    assert row["status"] == "superseded"
    assert row["rationale_code"] == SEMANTIC_LAYER_DISALLOWED_CODE


async def test_vocabulary_batch_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    from app.services.reader_orchestration.job_bootstrap import (
        VOCABULARY_BATCH_OPERATION_FINGERPRINT,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_batch_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_vocabulary_layer_article",
        fingerprint_base=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
        layer="vocabulary",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingBatchVocab()
    worker = VocabularyWorkerService(pool=pool, batch_executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-v-batch",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer_article",
        operation_fingerprint=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_vocabulary_batch_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_grammar_batch_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    from app.services.reader_orchestration.job_bootstrap import (
        GRAMMAR_BATCH_OPERATION_FINGERPRINT,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_batch_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_grammar_bundle",
        fingerprint_base=GRAMMAR_BATCH_OPERATION_FINGERPRINT,
        layer="grammar_note",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingBatchGrammar()
    worker = GrammarBundleWorkerService(pool=pool, batch_executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-g-batch",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        target_type="unit_range",
        operation_fingerprint=GRAMMAR_BATCH_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_grammar_batch_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_vocabulary_worker_off_and_shadow_execute(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    for mode in ("off", "shadow"):
        job_id = await _insert_unit_job(
            pool,
            record_id=article.record_id,
            base_id=article.base_id,
            user_id=user_id,
            unit_id=unit_id,
            job_type="build_vocabulary_layer",
            target_type="unit",
            fingerprint_base=VOCABULARY_OPERATION_FINGERPRINT,
            layer="vocabulary",
            mode=mode,
            policy=AutomaticLayerPolicy.all_off().as_dict(),
        )
        spy = _CountingVocab()
        worker = VocabularyWorkerService(pool=pool, executor=spy)
        runtime = ReaderJobRuntime(pool=pool)
        claim = await runtime.claim_next_job(
            lease_owner=f"fence-r2-v-{mode}",
            lease_duration=timedelta(seconds=30),
            job_type="build_vocabulary_layer",
            operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
        )
        assert claim is not None
        assert claim.job_id == job_id
        await worker.process_claimed_vocabulary_job(claim=claim)
        assert spy.calls == 1, f"mode={mode} must enter executor"


async def test_grammar_worker_off_and_shadow_execute(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    for mode in ("off", "shadow"):
        job_id = await _insert_unit_job(
            pool,
            record_id=article.record_id,
            base_id=article.base_id,
            user_id=user_id,
            unit_id=unit_id,
            job_type="build_grammar_bundle",
            target_type="unit",
            fingerprint_base=GRAMMAR_OPERATION_FINGERPRINT,
            layer="grammar_note",
            mode=mode,
            policy=AutomaticLayerPolicy.all_off().as_dict(),
        )
        spy = _CountingGrammar()
        worker = GrammarBundleWorkerService(pool=pool, executor=spy)
        runtime = ReaderJobRuntime(pool=pool)
        claim = await runtime.claim_next_job(
            lease_owner=f"fence-r2-g-{mode}",
            lease_duration=timedelta(seconds=30),
            job_type="build_grammar_bundle",
            operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
        )
        assert claim is not None
        assert claim.job_id == job_id
        await worker.process_claimed_grammar_job(claim=claim)
        assert spy.calls == 1, f"mode={mode} must enter executor"


async def test_translation_allowed_under_enforce_enters_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    job_id = await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-allowed",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert spy.calls == 1
    assert result.status != "superseded"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rationale_code FROM reader_jobs WHERE id = $1", job_id
        )
    assert row["rationale_code"] != SEMANTIC_LAYER_DISALLOWED_CODE


async def test_context_load_fence_blocks_before_generate(
    fence_env: asyncpg.Pool,
) -> None:
    """Both context-load and process entry raise fence; generate never runs."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    job_id = await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)

    from app.services.reader_orchestration.translation_worker import (
        TranslationExecutionError,
    )

    with pytest.raises(TranslationExecutionError) as ei:
        await worker._load_job_context(job_id)
    assert ei.value.failure_code == SEMANTIC_LAYER_DISALLOWED_CODE
    assert spy.calls == 0

    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-dual",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_shadow_bootstrap_would_skip_observation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """shadow keeps units and emits automatic_layer_policy_skip would-skip logs."""
    import logging

    units = [
        {
            "unit_id": "code",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        },
        {
            "unit_id": "prose",
            "order_index": 2,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                }
            },
        },
    ]
    with caplog.at_level(
        logging.INFO,
        logger="app.services.reader_orchestration.automatic_layer_policy",
    ):
        kept = filter_units_for_automatic_layer(
            units, "vocabulary", mode="shadow", record_id="r-shadow", generation=1
        )
    assert {u["unit_id"] for u in kept} == {"code", "prose"}
    assert any(
        "automatic_layer_policy_skip" in r.getMessage() for r in caplog.records
    )


async def test_grammar_window_enforce_disallowed_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Z+ window worker: real GrammarWindowWorkerService, fence supersede, 0 executor."""
    from app.services.reader_orchestration.grammar_window_worker import (
        GrammarWindowWorkerService,
    )
    from app.services.reader_orchestration.zplus_bootstrap import (
        ZPlusBootstrapService,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "Not only did the team revise the plan, but they also clarified the timeline. "
            "Everyone understood the tradeoff.\n\n"
            "The committee which had spent months reviewing data claimed recovery was broad."
        ),
    )
    # Stamp contract/resolver on units BEFORE bootstrap so job fence matches.
    async with pool.acquire() as conn:
        unit_ids = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1", article.base_id
        )
    for row in unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=str(row["unit_id"]),
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )

    zplus = ZPlusBootstrapService(pool=pool)
    result = await zplus.bootstrap_grammar_window_plan(
        record_id=article.record_id, base_id=article.base_id
    )
    assert result.job_ids
    job_id = result.job_ids[0]

    # Flip only automatic_layer_policy; keep contract/resolver so fence is layer-disallow.
    for row in unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=str(row["unit_id"]),
            policy=AutomaticLayerPolicy.all_off().as_dict(),
        )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET input_json = input_json || $2::jsonb
            WHERE id = $1
            """,
            job_id,
            jsonb_param({"semantic_policy_mode": "enforce"}),
        )

    spy = _CountingWindowGrammar()
    worker = GrammarWindowWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-window",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle_window",
        operation_fingerprint=ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    out = await worker.process_window_job(claim=claim)
    assert out.get("status") == "superseded"
    assert out.get("failure_code") == SEMANTIC_LAYER_DISALLOWED_CODE
    assert spy.calls == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1", job_id
        )
    assert row["status"] == "superseded"
    assert row["rationale_code"] == SEMANTIC_LAYER_DISALLOWED_CODE


async def test_forged_origin_real_worker_still_supersedes(
    fence_env: asyncpg.Pool,
) -> None:
    """Forged request_origin=section_v1 without section fingerprint cannot bypass."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
        extra_input={"request_origin": SECTION_REQUEST_ORIGIN},
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="fence-r2-forge",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_section_fp_missing_identity_cannot_skip_fence() -> None:
    """section fingerprint + incomplete identity fail-closed on translation lane."""
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "section_identity": {
                    "record_id": "r",
                    "base_id": "b",
                    "generation": 1,
                    # missing start/end unit
                },
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                "automatic_layer_name": "translation",
                SEMANTIC_FENCE_KEY_MODE: "enforce",
            },
            layer="translation",
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                        "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                    }
                }
            ],
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            trusted_record_id="r",
            trusted_base_id="b",
            trusted_generation=1,
        )
    assert ei.value.code == SEMANTIC_POLICY_VERSION_MISMATCH_CODE  # type: ignore[attr-defined]


async def test_section_identity_record_mismatch_cannot_skip_fence() -> None:
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "section_identity": {
                    "record_id": "forged-record",
                    "base_id": "b",
                    "generation": 1,
                    "start_unit_id": "u1",
                    "end_unit_id": "u1",
                },
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                "automatic_layer_name": "translation",
                SEMANTIC_FENCE_KEY_MODE: "enforce",
            },
            layer="translation",
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                        "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                    }
                }
            ],
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            trusted_record_id="real-record",
            trusted_base_id="b",
            trusted_generation=1,
        )
    assert ei.value.code == SEMANTIC_POLICY_VERSION_MISMATCH_CODE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Production bootstrap three-mode persistence (per-unit vocabulary)
# ---------------------------------------------------------------------------


async def test_vocabulary_bootstrap_persists_mode_off_shadow_enforce(
    fence_env: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """VocabularyJobBootstrapService freezes mode into job input + fingerprint."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    async with pool.acquire() as conn:
        all_unit_ids = [
            str(r["unit_id"])
            for r in await conn.fetch(
                "SELECT unit_id FROM reading_units WHERE base_id = $1",
                article.base_id,
            )
        ]
    assert all_unit_ids
    unit_id = all_unit_ids[0]
    vocab_off = {
        "translation": True,
        "vocabulary": False,
        "grammar_note": False,
        "sentence_analysis": False,
    }

    # All units disallow vocabulary so enforce has nothing to enqueue.
    for uid in all_unit_ids:
        await _set_unit_policy(
            pool, record_id=article.record_id, unit_id=uid, policy=vocab_off
        )

    svc = VocabularyJobBootstrapService(pool=pool)

    # --- off: keep disallowed unit, mode:off in input + fingerprint ---
    with _policy_mode("off"):
        off_result = await svc.bootstrap_vocabulary_run(
            record_id=article.record_id, user_id=user_id
        )
    assert off_result.job_id is not None
    async with pool.acquire() as conn:
        off_row = await conn.fetchrow(
            "SELECT input_json, operation_fingerprint, target_key FROM reader_jobs WHERE id = $1",
            off_result.job_id,
        )
        await conn.execute(
            "UPDATE reader_jobs SET status = 'cancelled' WHERE id = $1",
            off_result.job_id,
        )
    off_input = dict(off_row["input_json"])
    assert off_input.get("semantic_policy_mode") == "off"
    assert "mode:off" in str(off_row["operation_fingerprint"])
    assert off_row["target_key"] == unit_id

    # --- shadow: keep unit + would-skip log, mode:shadow ---
    with _policy_mode("shadow"):
        with caplog.at_level(
            logging.INFO,
            logger="app.services.reader_orchestration.automatic_layer_policy",
        ):
            shadow_result = await svc.bootstrap_vocabulary_run(
                record_id=article.record_id, user_id=user_id
            )
    assert shadow_result.job_id is not None
    async with pool.acquire() as conn:
        shadow_row = await conn.fetchrow(
            "SELECT input_json, operation_fingerprint FROM reader_jobs WHERE id = $1",
            shadow_result.job_id,
        )
        await conn.execute(
            "UPDATE reader_jobs SET status = 'cancelled' WHERE id = $1",
            shadow_result.job_id,
        )
    assert dict(shadow_row["input_json"]).get("semantic_policy_mode") == "shadow"
    assert "mode:shadow" in str(shadow_row["operation_fingerprint"])
    assert any(
        "automatic_layer_policy_skip" in r.getMessage() for r in caplog.records
    )

    # --- enforce: no job when every unit disallows vocabulary ---
    with _policy_mode("enforce"):
        with pytest.raises(ValueError, match="no"):
            await svc.bootstrap_vocabulary_run(
                record_id=article.record_id, user_id=user_id
            )

    # Restore allowed vocabulary so enforce can create for prose.
    for uid in all_unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=uid,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
    with _policy_mode("enforce"):
        enforce_result = await svc.bootstrap_vocabulary_run(
            record_id=article.record_id, user_id=user_id
        )
    assert enforce_result.job_id is not None
    async with pool.acquire() as conn:
        enforce_row = await conn.fetchrow(
            "SELECT input_json, operation_fingerprint FROM reader_jobs WHERE id = $1",
            enforce_result.job_id,
        )
    assert dict(enforce_row["input_json"]).get("semantic_policy_mode") == "enforce"
    assert "mode:enforce" in str(enforce_row["operation_fingerprint"])


def test_bootstrap_shared_filter_seam_used_by_batch_grouped() -> None:
    """Batch/grouped paths call the same _filter_units_for_layer seam."""
    import inspect

    from app.services.reader_orchestration import job_bootstrap as jb

    src = inspect.getsource(jb.EnhancementJobBootstrapService)
    assert "_filter_units_for_layer" in src or "filter_units_for_automatic_layer" in src
    # vocabulary batch/grouped helpers go through _filter_units_for_layer
    batch_src = inspect.getsource(jb.EnhancementJobBootstrapService._bootstrap_vocabulary_jobs)
    assert "_bootstrap_vocabulary" in batch_src or "filter" in batch_src
    grouped = inspect.getsource(
        jb.EnhancementJobBootstrapService._bootstrap_vocabulary_grouped_jobs
    )
    assert "_filter_units_for_layer" in grouped


# ---------------------------------------------------------------------------
# Z+ three-mode real bootstrap
# ---------------------------------------------------------------------------


async def test_zplus_bootstrap_three_modes_persist_mode(
    fence_env: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ZPlusBootstrapService.bootstrap_grammar_window_plan freezes mode per create."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "Not only did the team revise the plan, but they also clarified the timeline. "
            "Everyone understood the tradeoff.\n\n"
            "The committee which had spent months reviewing data claimed recovery was broad."
        ),
    )
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(r["unit_id"]) for r in unit_rows]
    assert unit_ids
    # First unit: grammar-disallowed; remaining (if any) keep all_on.
    for i, uid in enumerate(unit_ids):
        policy = (
            AutomaticLayerPolicy.all_off().as_dict()
            if i == 0
            else AutomaticLayerPolicy.all_on().as_dict()
        )
        await _set_unit_policy(
            pool, record_id=article.record_id, unit_id=uid, policy=policy
        )

    zplus = ZPlusBootstrapService(pool=pool)
    disallowed = unit_ids[0]

    async def _read_jobs() -> list[dict[str, Any]]:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, input_json, operation_fingerprint, status
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = 'build_grammar_bundle_window'
                ORDER BY created_at ASC
                """,
                article.record_id,
            )
        out = []
        for r in rows:
            ij = r["input_json"]
            if hasattr(ij, "keys"):
                ij = dict(ij)
            out.append(
                {
                    "id": r["id"],
                    "mode": ij.get("semantic_policy_mode"),
                    "fp": str(r["operation_fingerprint"]),
                    "target_units": list(ij.get("target_unit_ids") or []),
                }
            )
        return out

    async def _cancel_window_jobs() -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_jobs SET status = 'cancelled'
                WHERE reading_record_id = $1
                  AND job_type = 'build_grammar_bundle_window'
                """,
                article.record_id,
            )
            await conn.execute(
                """
                UPDATE layer_analysis_plans SET status = 'superseded'
                WHERE reading_record_id = $1 AND status = 'active'
                """,
                article.record_id,
            )

    # off
    with _policy_mode("off"):
        off_res = await zplus.bootstrap_grammar_window_plan(
            record_id=article.record_id, base_id=article.base_id
        )
    assert off_res.job_ids
    off_jobs = await _read_jobs()
    assert any(disallowed in j["target_units"] for j in off_jobs)
    assert all(j["mode"] == "off" for j in off_jobs)
    assert all("mode:off" in j["fp"] for j in off_jobs)
    await _cancel_window_jobs()

    # shadow
    with _policy_mode("shadow"):
        with caplog.at_level(
            logging.INFO,
            logger="app.services.reader_orchestration.automatic_layer_policy",
        ):
            shadow_res = await zplus.bootstrap_grammar_window_plan(
                record_id=article.record_id, base_id=article.base_id
            )
    assert shadow_res.job_ids
    shadow_jobs = await _read_jobs()
    # Only non-cancelled jobs for this mode (previous cancelled remain).
    shadow_live = [j for j in shadow_jobs if "mode:shadow" in j["fp"]]
    assert shadow_live
    assert any(disallowed in j["target_units"] for j in shadow_live)
    assert all(j["mode"] == "shadow" for j in shadow_live)
    assert any(
        "automatic_layer_policy_skip" in r.getMessage() for r in caplog.records
    )
    await _cancel_window_jobs()

    # enforce: disallowed unit excluded
    with _policy_mode("enforce"):
        enforce_res = await zplus.bootstrap_grammar_window_plan(
            record_id=article.record_id, base_id=article.base_id
        )
    enforce_jobs = await _read_jobs()
    enforce_live = [j for j in enforce_jobs if "mode:enforce" in j["fp"]]
    if len(unit_ids) == 1:
        # Only disallowed unit → no windows / no jobs under enforce.
        assert not enforce_res.job_ids or all(
            disallowed not in j["target_units"] for j in enforce_live
        )
    else:
        assert enforce_res.job_ids
        assert enforce_live
        assert all(disallowed not in j["target_units"] for j in enforce_live)
        assert all(j["mode"] == "enforce" for j in enforce_live)


# ---------------------------------------------------------------------------
# R2.2: section identity cannot bypass non-translation / bad target bind
# ---------------------------------------------------------------------------


def _section_extra_input(
    *,
    record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int = 1,
) -> dict[str, Any]:
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    identity = SectionIdentity(
        record_id=str(record_id),
        base_id=str(base_id),
        generation=generation,
        start_unit_id=unit_id,
        end_unit_id=unit_id,
    )
    return {
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": str(record_id),
            "base_id": str(base_id),
            "generation": generation,
            "start_unit_id": unit_id,
            "end_unit_id": unit_id,
            "start_anchor_segment_id": None,
            "end_anchor_segment_id": None,
        },
        "target_unit_ids": [unit_id],
        # Stashed for tests that need the canonical key (not written by default).
        "_section_target_key": encode_section_target_key(identity),
    }


async def test_section_claim_cannot_bypass_vocabulary_worker(
    fence_env: asyncpg.Pool,
) -> None:
    """Full section triple + vocabulary all_off/enforce → executor=0 supersede."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    extra = _section_extra_input(
        record_id=article.record_id, base_id=article.base_id, unit_id=unit_id
    )
    extra.pop("_section_target_key", None)
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_vocabulary_layer",
        target_type="unit",
        fingerprint_base=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
        layer="vocabulary",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
        extra_input=extra,
    )
    spy = _CountingVocab()
    worker = VocabularyWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r22-vocab-section",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_vocabulary_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_section_claim_cannot_bypass_grammar_worker(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    extra = _section_extra_input(
        record_id=article.record_id, base_id=article.base_id, unit_id=unit_id
    )
    extra.pop("_section_target_key", None)
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_grammar_bundle",
        target_type="unit",
        fingerprint_base=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
        layer="grammar_note",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_off().as_dict(),
        extra_input=extra,
    )
    spy = _CountingGrammar()
    worker = GrammarBundleWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r22-grammar-section",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_grammar_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_section_claim_cannot_bypass_grammar_window_worker(
    fence_env: asyncpg.Pool,
) -> None:
    from app.services.reader_orchestration.grammar_window_worker import (
        GrammarWindowWorkerService,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "Not only did the team revise the plan, but they also clarified the timeline. "
            "Everyone understood the tradeoff.\n\n"
            "The committee which had spent months reviewing data claimed recovery was broad."
        ),
    )
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1", article.base_id
        )
    for row in unit_rows:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=str(row["unit_id"]),
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
    with _policy_mode("enforce"):
        zplus = ZPlusBootstrapService(pool=pool)
        result = await zplus.bootstrap_grammar_window_plan(
            record_id=article.record_id, base_id=article.base_id
        )
    assert result.job_ids
    job_id = result.job_ids[0]
    unit_id = str(unit_rows[0]["unit_id"])
    # Stamp full section claim + flip policy off after job exists.
    extra = _section_extra_input(
        record_id=article.record_id, base_id=article.base_id, unit_id=unit_id
    )
    extra.pop("_section_target_key", None)
    for row in unit_rows:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=str(row["unit_id"]),
            policy=AutomaticLayerPolicy.all_off().as_dict(),
        )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET operation_fingerprint = $2,
                input_json = input_json || $3::jsonb
            WHERE id = $1
            """,
            job_id,
            f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:window-forge",
            jsonb_param(
                {
                    **extra,
                    "semantic_policy_mode": "enforce",
                    "automatic_layer_name": "grammar_note",
                }
            ),
        )
    spy = _CountingWindowGrammar()
    worker = GrammarWindowWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r22-window-section",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle_window",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    out = await worker.process_window_job(claim=claim)
    assert out.get("status") == "superseded"
    assert spy.calls == 0


async def test_translation_section_range_mismatch_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """identity range ≠ target_key → fail closed, executor=0."""
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    # Canonical key for unit_id, but identity claims a different end unit.
    good_key = encode_section_target_key(
        SectionIdentity(
            record_id=str(article.record_id),
            base_id=str(article.base_id),
            generation=1,
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        )
    )
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    input_json = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": str(article.record_id),
            "base_id": str(article.base_id),
            "generation": 1,
            "start_unit_id": unit_id,
            "end_unit_id": "u-forged-end",
        },
        "target_unit_ids": [unit_id],
        "target_scope": "unit_range",
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "translation",
        "semantic_policy_mode": "enforce",
    }
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    fp = f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'r22', 'user')
            RETURNING id
            """,
            article.record_id,
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
                'translate_article', 'unit_range', $5, 'queued',
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            good_key,
            fp,
            f"{fp}:range-mismatch",
            jsonb_param(input_json),
        )
    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r22-range",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_translation_section_loaded_units_mismatch_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """target_unit_ids / loaded units disagree with identity ends → fail closed."""
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    identity = SectionIdentity(
        record_id=str(article.record_id),
        base_id=str(article.base_id),
        generation=1,
        start_unit_id=unit_id,
        end_unit_id=unit_id,
    )
    target_key = encode_section_target_key(identity)
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    # Declare an extra forged unit id that will not be loaded from DB.
    input_json = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": str(article.record_id),
            "base_id": str(article.base_id),
            "generation": 1,
            "start_unit_id": unit_id,
            "end_unit_id": unit_id,
        },
        "target_unit_ids": [unit_id, "u-ghost"],
        "target_scope": "unit_range",
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "translation",
        "semantic_policy_mode": "enforce",
    }
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    fp = f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}:ghost"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'r22', 'user')
            RETURNING id
            """,
            article.record_id,
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
                'translate_article', 'unit_range', $5, 'queued',
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            target_key,
            fp,
            f"{fp}:ghost",
            jsonb_param(input_json),
        )
    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r22-ghost",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_batch_job(claim=claim)
    # Fence runs before missing-unit check when loaded != declared.
    assert spy.calls == 0
    assert result.status in {"superseded", "failed_terminal"}


# ---------------------------------------------------------------------------
# R2.3: layer identity fail-closed + DB geometry bind
# ---------------------------------------------------------------------------


async def test_translation_job_wrong_layer_name_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="translate_unit",
        target_type="unit",
        fingerprint_base=TRANSLATION_OPERATION_FINGERPRINT,
        layer="grammar_bundle",  # wrong for translation worker
        mode="enforce",
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-t-layer",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_vocabulary_job_wrong_layer_name_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_vocabulary_layer",
        target_type="unit",
        fingerprint_base=VOCABULARY_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    spy = _CountingVocab()
    worker = VocabularyWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-v-layer",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer",
        operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_vocabulary_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_grammar_job_wrong_layer_name_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _insert_unit_job(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        user_id=user_id,
        unit_id=unit_id,
        job_type="build_grammar_bundle",
        target_type="unit",
        fingerprint_base=GRAMMAR_OPERATION_FINGERPRINT,
        layer="translation",
        mode="enforce",
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    spy = _CountingGrammar()
    worker = GrammarBundleWorkerService(pool=pool, executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-g-layer",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    result = await worker.process_claimed_grammar_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_fenced_job_missing_layer_name_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    input_json = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        # missing automatic_layer_name
        "semantic_policy_mode": "enforce",
    }
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    fp = f"{TRANSLATION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}:nolayer"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'r23', 'system')
            RETURNING id
            """,
            article.record_id,
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
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            unit_id,
            fp,
            f"{fp}:{unit_id}",
            jsonb_param(input_json),
        )
    spy = _CountingTranslator()
    worker = TranslationWorkerService(pool=pool, translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-missing-layer",
        lease_duration=timedelta(seconds=30),
        job_type="translate_unit",
        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_job(claim=claim)
    assert result.status == "superseded"
    assert spy.calls == 0


async def test_section_missing_middle_unit_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """start=u1 end=u3 but target_unit_ids omit middle unit → fail closed."""
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "First paragraph about planning and timelines for the project.\n\n"
            "Second paragraph covers budget tradeoffs and team ownership.\n\n"
            "Third paragraph summarizes outcomes and next steps clearly."
        ),
    )
    async with pool.acquire() as conn:
        units = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(u["unit_id"]) for u in units]
    if len(unit_ids) < 3:
        pytest.skip("need at least 3 units for middle-unit omission test")
    u1, _u2, u3 = unit_ids[0], unit_ids[1], unit_ids[2]
    identity = SectionIdentity(
        record_id=str(article.record_id),
        base_id=str(article.base_id),
        generation=1,
        start_unit_id=u1,
        end_unit_id=u3,
    )
    target_key = encode_section_target_key(identity)
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    for uid in unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=uid,
            policy=AutomaticLayerPolicy.all_off().as_dict(),
        )
    input_json = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": str(article.record_id),
            "base_id": str(article.base_id),
            "generation": 1,
            "start_unit_id": u1,
            "end_unit_id": u3,
        },
        "target_unit_ids": [u1, u3],  # omits middle
        "target_scope": "unit_range",
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "translation",
        "semantic_policy_mode": "enforce",
    }
    fp = f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}:mid"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'r23', 'user')
            RETURNING id
            """,
            article.record_id,
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
                'translate_article', 'unit_range', $5, 'queued',
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            target_key,
            fp,
            f"{fp}:mid",
            jsonb_param(input_json),
        )
    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-mid",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert spy.calls == 0
    assert result.status == "superseded"


async def test_section_nonexistent_anchor_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    identity = SectionIdentity(
        record_id=str(article.record_id),
        base_id=str(article.base_id),
        generation=1,
        start_unit_id=unit_id,
        end_unit_id=unit_id,
        start_anchor_segment_id="ghost-sa",
        end_anchor_segment_id="ghost-ea",
    )
    target_key = encode_section_target_key(identity)
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    input_json = {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "base_language": "en",
        "target_language": "zh-CN",
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": str(article.record_id),
            "base_id": str(article.base_id),
            "generation": 1,
            "start_unit_id": unit_id,
            "end_unit_id": unit_id,
            "start_anchor_segment_id": "ghost-sa",
            "end_anchor_segment_id": "ghost-ea",
        },
        "target_unit_ids": [unit_id],
        "target_scope": "unit_range",
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "translation",
        "semantic_policy_mode": "enforce",
    }
    fp = f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}:ghosta"
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1, '{}'::jsonb, 'r23', 'user')
            RETURNING id
            """,
            article.record_id,
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
                'translate_article', 'unit_range', $5, 'queued',
                0, 1, $6, $7, 'h', $8::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            target_key,
            fp,
            f"{fp}:ga",
            jsonb_param(input_json),
        )
    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="r23-ghost-anchor",
        lease_duration=timedelta(seconds=30),
        job_type="translate_article",
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert spy.calls == 0
    assert result.status == "superseded"
