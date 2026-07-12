"""T4.2a-O2 / O2-R1: execution correlation + usage presence diagnostics."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.llm import agent_runner
from app.llm.routes import MODEL_ROUTE_READER_LAYER_TRANSLATION
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    TranslationLayerGenerationOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)
from app.services.ai_usage import execution_diagnostics as ued
from app.services.ai_usage.execution_diagnostics import (
    AGENT_RUN_DURATION_RECORDED,
    CORRELATION_SCHEMA_KIND,
    CORRELATION_SCHEMA_VERSION,
    DURATION_SCHEMA_KIND,
    DURATION_SCHEMA_VERSION,
    META_AGENT_RUN_DURATION_MS,
    META_PROVIDER_REQUEST_DURATION_MS,
    META_PROVIDER_REQUEST_DURATION_STATUS,
    PROVIDER_DURATION_STATUS_AVAILABLE,
    PROVIDER_DURATION_STATUS_UNAVAILABLE,
    PROVIDER_TIMING_AVAILABLE,
    PROVIDER_TIMING_UNAVAILABLE,
    STAGE_ADAPTER,
    STAGE_EVENT_DTO,
    STAGE_NORMALIZE,
    USAGE_EMPTY_AT_ADAPTER,
    USAGE_EVENT_PERSIST_FAILED,
    USAGE_EVENT_PERSISTED_ZERO,
    USAGE_MISSING_AT_ADAPTER,
    USAGE_MISSING_BEFORE_EVENT,
    USAGE_PRESENT_AT_ADAPTER,
    USAGE_SPAN_EVENT_MISMATCH,
    USAGE_ZERO_AFTER_NORMALIZATION,
    begin_execution_from_claim,
    classify_usage_presence,
    current_duration_provenance,
    current_execution,
    current_usage_outcome,
    detect_event_span_token_mismatch,
    execution_scope,
    extract_provider_request_timing,
    log_usage_diagnostic,
    merge_correlation_metadata,
    mint_agent_run_id,
    normalize_token_totals,
    set_last_usage_outcome,
    span_totals_from_usage_data,
    with_execution_correlation,
)
from app.services.ai_usage.service import AIUsageEventCreate, record_ai_usage_event
from app.services.reader_orchestration.display_title_worker import (
    DisplayTitleExecutionResult,
    DisplayTitleGenerationInput,
    DisplayTitleJobContext,
    DisplayTitleWorkerService,
)
from app.services.reader_orchestration.grammar_window_publisher import (
    PublishedWindowResult,
)
from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionError,
    GrammarWindowExecutionResult,
    GrammarWindowWorkerService,
    PreflightResult,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBundleWorkerService,
    GrammarExecutionResult,
    GrammarJobContext,
)
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    FenceViolationError,
)
from app.services.reader_orchestration.layer_publisher import (
    PublishedGrammarBatch,
    PublishedGrammarBundle,
    PublishedTranslationBatch,
    PublishedTranslationLayer,
    PublishedVocabularyBatch,
    PublishedVocabularyLayer,
)
from app.services.reader_orchestration.pipeline_runner import (
    ReaderEnhancementPipelineRunner,
)
from app.services.reader_orchestration.span_recorder import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    SpanContext,
    end_worker_span_execution_error,
    end_worker_span_fence_violation,
    end_worker_span_generic_exception,
    end_worker_span_success,
    set_default_recorder,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationExecutionResult,
    TranslationJobContext,
    TranslationWorkerService,
)
from app.services.reader_orchestration.vocabulary_worker import (
    FakeVocabularyExecutor,
    VocabularyExecutionResult,
    VocabularyJobContext,
    VocabularyWorkerService,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_execution_and_span_globals() -> Any:
    """Prevent O2 ContextVar / default-recorder leakage across tests."""
    from app.services.ai_usage.execution_diagnostics import set_current_execution

    set_default_recorder(None)
    set_last_usage_outcome(None)
    set_current_execution(None)
    yield
    set_default_recorder(None)
    set_last_usage_outcome(None)
    set_current_execution(None)


def _claim(*, attempt_count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        attempt_count=attempt_count,
        operation_fingerprint="fp:test:v1",
        lease_token=uuid4(),
    )


def _claim_result(*, attempt_count: int = 1) -> ClaimResult:
    return ClaimResult(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        job_type="test_job",
        target_type="unit",
        target_key=str(uuid4()),
        expected_generation=1,
        operation_fingerprint="fp:test:v1",
        attempt_count=attempt_count,
        lease_owner="test-worker",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )


def _fake_pool_returning(event_id: UUID) -> MagicMock:
    fake_conn = AsyncMock()
    fake_conn.fetchval = AsyncMock(return_value=event_id)
    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=fake_conn),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    return fake_pool


def _metadata_from_record_call(fetchval_mock: AsyncMock) -> dict[str, Any]:
    for arg in fetchval_mock.await_args.args:
        if isinstance(arg, dict) and "execution_id" in arg:
            return arg
    raise AssertionError("expected metadata_json with execution_id in INSERT args")


def _assert_correlation_metadata(
    meta: dict[str, Any],
    *,
    claim: ClaimResult | SimpleNamespace,
    execution_id: UUID | None = None,
    attempt_ordinal: int | None = None,
    capability: str | None = None,
) -> None:
    assert meta["correlation_schema_kind"] == CORRELATION_SCHEMA_KIND
    assert meta["correlation_schema_version"] == CORRELATION_SCHEMA_VERSION
    assert meta["correlation_reader_job_id"] == str(claim.job_id)
    assert meta["correlation_reader_run_id"] == str(claim.run_id)
    if attempt_ordinal is not None:
        assert meta["attempt_ordinal"] == attempt_ordinal
    else:
        assert meta["attempt_ordinal"] == claim.attempt_count
    if execution_id is not None:
        assert meta["execution_id"] == str(execution_id)
    else:
        UUID(meta["execution_id"])  # valid UUID
    if capability is not None:
        assert meta["correlation_capability_code"] == capability


def _diagnostic_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if getattr(r, "diagnostic_code", None) is not None
        or "reader_usage_diagnostic" in r.getMessage()
    ]


# ---------------------------------------------------------------------------
# Correlation identity
# ---------------------------------------------------------------------------


def test_begin_execution_uses_claim_attempt_count_as_attempt_ordinal() -> None:
    claim = _claim(attempt_count=2)
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")
    assert corr.attempt_ordinal == 2
    assert corr.reader_job_id == claim.job_id
    assert corr.reader_run_id == claim.run_id
    assert corr.capability_code == "reader_grammar_bundle"
    assert isinstance(corr.execution_id, UUID)


def test_attempt_count_fail_closed_rejects_zero_and_negative() -> None:
    with pytest.raises(ValueError, match="attempt_ordinal must be >= 1"):
        begin_execution_from_claim(_claim(attempt_count=0), capability_code="reader_translation")
    with pytest.raises(ValueError, match="attempt_ordinal must be >= 1"):
        begin_execution_from_claim(_claim(attempt_count=-3), capability_code="reader_translation")


def test_attempt_count_one_and_two_accepted() -> None:
    c1 = begin_execution_from_claim(_claim(attempt_count=1), capability_code="reader_vocabulary")
    c2 = begin_execution_from_claim(_claim(attempt_count=2), capability_code="reader_vocabulary")
    assert c1.attempt_ordinal == 1
    assert c2.attempt_ordinal == 2


def test_same_job_two_retries_have_distinct_execution_ids_and_ordinals() -> None:
    job_id = uuid4()
    run_id = uuid4()
    claim1 = SimpleNamespace(
        job_id=job_id,
        run_id=run_id,
        reading_record_id=uuid4(),
        attempt_count=1,
        operation_fingerprint="fp:same",
    )
    claim2 = SimpleNamespace(
        job_id=job_id,
        run_id=run_id,
        reading_record_id=claim1.reading_record_id,
        attempt_count=2,
        operation_fingerprint="fp:same",
    )
    c1 = begin_execution_from_claim(claim1, capability_code="reader_translation")
    c2 = begin_execution_from_claim(claim2, capability_code="reader_translation")
    assert c1.reader_job_id == c2.reader_job_id == job_id
    assert c1.attempt_ordinal == 1
    assert c2.attempt_ordinal == 2
    assert c1.execution_id != c2.execution_id
    assert c1.to_metadata()["execution_id"] != c2.to_metadata()["execution_id"]


def test_execution_scope_binds_and_resets_context() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")
    assert current_execution() is None
    with execution_scope(corr):
        assert current_execution() is corr
    assert current_execution() is None


def test_mint_agent_run_id_updates_active_correlation() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_title_generation")
    with execution_scope(corr):
        agent_run_id, updated = mint_agent_run_id()
        assert updated is not None
        assert updated.agent_run_id == agent_run_id
        assert current_execution().agent_run_id == agent_run_id
        meta = current_execution().to_metadata()
        assert meta["agent_run_id"] == str(agent_run_id)
        assert "provider_request_id" not in meta


def test_last_usage_outcome_does_not_leak_across_scopes() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    with execution_scope(corr):
        set_last_usage_outcome(
            ued.UsageRecordOutcome(
                event_id=uuid4(),
                recorded_totals={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                diagnostic_codes=("usage_event_persisted",),
                usage_presence=classify_usage_presence(
                    {"input_tokens": 1}, stage=STAGE_EVENT_DTO
                ),
            )
        )
        assert current_usage_outcome() is not None
    assert current_usage_outcome() is None
    # Non-Reader scope: outcome stays unset
    assert current_execution() is None
    assert current_usage_outcome() is None


# ---------------------------------------------------------------------------
# Usage presence classification
# ---------------------------------------------------------------------------


def test_classify_usage_none_at_adapter() -> None:
    snap = classify_usage_presence(None, stage=STAGE_ADAPTER)
    assert snap.diagnostic_code == USAGE_MISSING_AT_ADAPTER
    assert snap.usage_is_none is True


def test_classify_usage_empty_dict_distinct_from_none() -> None:
    snap = classify_usage_presence({}, stage=STAGE_ADAPTER)
    assert snap.diagnostic_code == USAGE_EMPTY_AT_ADAPTER
    assert snap.usage_is_empty_mapping is True


def test_classify_usage_populated_nonzero() -> None:
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    snap = classify_usage_presence(usage, stage=STAGE_ADAPTER)
    assert snap.diagnostic_code == USAGE_PRESENT_AT_ADAPTER
    assert snap.normalized_totals["total_tokens"] == 15


def test_classify_usage_zero_after_normalization_stage() -> None:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "foo": 1}
    snap = classify_usage_presence(usage, stage=STAGE_NORMALIZE)
    assert snap.diagnostic_code == USAGE_ZERO_AFTER_NORMALIZATION


def test_none_before_event_uses_missing_before_event_code() -> None:
    snap = classify_usage_presence(None, stage=STAGE_EVENT_DTO)
    assert snap.diagnostic_code == USAGE_MISSING_BEFORE_EVENT


# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------


def test_no_mismatch_when_event_and_span_agree() -> None:
    totals = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    assert (
        detect_event_span_token_mismatch(event_totals=totals, span_totals=totals)
        is False
    )


def test_mismatch_when_event_zero_span_nonzero() -> None:
    event = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    span = {"input_tokens": 11885, "output_tokens": 3074, "total_tokens": 14959}
    assert detect_event_span_token_mismatch(event_totals=event, span_totals=span) is True


def test_span_totals_from_usage_none_are_none_not_zero() -> None:
    totals = span_totals_from_usage_data(None)
    assert totals["input_tokens"] is None


# ---------------------------------------------------------------------------
# Metadata merge / forgery resistance
# ---------------------------------------------------------------------------


def test_merge_correlation_metadata_is_stable_and_versioned() -> None:
    claim = _claim(attempt_count=3)
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    merged = merge_correlation_metadata({"base_id": "abc"}, corr)
    assert merged["base_id"] == "abc"
    assert merged["correlation_schema_kind"] == CORRELATION_SCHEMA_KIND
    assert merged["execution_id"] == str(corr.execution_id)
    assert merged["attempt_ordinal"] == 3


def test_caller_cannot_forge_execution_id_or_attempt_ordinal() -> None:
    claim = _claim(attempt_count=2)
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")
    forged = {
        "execution_id": "00000000-0000-0000-0000-000000000099",
        "attempt_ordinal": 99,
        "correlation_schema_kind": "forged",
        "caller_field": "kept",
    }
    merged = merge_correlation_metadata(forged, corr)
    assert merged["execution_id"] == str(corr.execution_id)
    assert merged["attempt_ordinal"] == 2
    assert merged["correlation_schema_kind"] == CORRELATION_SCHEMA_KIND
    assert merged["caller_field"] == "kept"


# ---------------------------------------------------------------------------
# Structured LogRecord.extra
# ---------------------------------------------------------------------------


def test_log_usage_diagnostic_uses_logrecord_extra_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    with execution_scope(corr):
        with caplog.at_level(logging.INFO, logger=ued.logger.name):
            log_usage_diagnostic(
                diagnostic_code=USAGE_PRESENT_AT_ADAPTER,
                stage=STAGE_ADAPTER,
                correlation=corr,
                usage_key_list=("input_tokens",),
                normalized_totals={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            )
    records = _diagnostic_records(caplog)
    assert records
    rec = records[-1]
    assert rec.diagnostic_code == USAGE_PRESENT_AT_ADAPTER
    assert rec.diagnostic_stage == STAGE_ADAPTER
    assert rec.execution_id == str(corr.execution_id)
    assert rec.attempt_ordinal == 1
    assert rec.correlation_schema_kind == CORRELATION_SCHEMA_KIND
    assert rec.correlation_schema_version == CORRELATION_SCHEMA_VERSION
    # Message is not a stringified full payload dict
    assert "SECRET" not in rec.getMessage()
    assert rec.getMessage().startswith("reader_usage_diagnostic")


def test_diagnostic_logs_never_include_prompt_or_article_or_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    secrets = {
        "prompt": "SECRET_PROMPT_TEXT_SHOULD_NOT_APPEAR",
        "article": "SECRET_ARTICLE_BODY",
        "api_key": "sk-secret-key",
        "raw_response": '{"choices":[]}',
        "session": "sess-secret",
    }
    with execution_scope(corr):
        with caplog.at_level(logging.INFO, logger=ued.logger.name):
            log_usage_diagnostic(
                diagnostic_code=USAGE_PRESENT_AT_ADAPTER,
                stage=STAGE_ADAPTER,
                correlation=corr,
                usage_key_list=("input_tokens",),
                normalized_totals={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                extra={
                    "mismatch": False,
                    "prompt": secrets["prompt"],
                    "article": secrets["article"],
                    "api_key": secrets["api_key"],
                    "raw_response": secrets["raw_response"],
                    "session": secrets["session"],
                },
            )
    rec = _diagnostic_records(caplog)[-1]
    joined = f"{rec.getMessage()} {rec.__dict__}"
    for secret in secrets.values():
        assert secret not in joined
    assert rec.execution_id == str(corr.execution_id)
    assert not hasattr(rec, "prompt")
    assert not hasattr(rec, "api_key")


# ---------------------------------------------------------------------------
# extract_run_usage diagnostics (Reader scope only)
# ---------------------------------------------------------------------------


def test_extract_run_usage_none_emits_missing_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")
    with execution_scope(corr):
        with caplog.at_level(logging.INFO, logger=ued.logger.name):
            result = agent_runner.extract_run_usage(SimpleNamespace(usage=None))
    assert result is None
    codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
    assert USAGE_MISSING_AT_ADAPTER in codes


def test_extract_run_usage_populated_emits_present_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pydantic_ai.usage import RunUsage

    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")
    usage = RunUsage(input_tokens=11, output_tokens=2)
    with execution_scope(corr):
        with caplog.at_level(logging.INFO, logger=ued.logger.name):
            result = agent_runner.extract_run_usage(SimpleNamespace(usage=usage))
    assert result is not None
    assert result["input_tokens"] == 11
    codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
    assert USAGE_PRESENT_AT_ADAPTER in codes


def test_extract_run_usage_without_reader_scope_emits_no_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pydantic_ai.usage import RunUsage

    assert current_execution() is None
    with caplog.at_level(logging.INFO, logger=ued.logger.name):
        result = agent_runner.extract_run_usage(
            SimpleNamespace(usage=RunUsage(input_tokens=3, output_tokens=1))
        )
    assert result is not None
    assert result["input_tokens"] == 3
    assert not _diagnostic_records(caplog)


# ---------------------------------------------------------------------------
# record_ai_usage_event
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_record_ai_usage_event_merges_correlation_and_zero_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)

    with execution_scope(corr):
        with patch("app.services.ai_usage.service.db_connection") as db:
            db.DB_POOL = fake_pool
            with caplog.at_level(logging.INFO, logger=ued.logger.name):
                result = await record_ai_usage_event(
                    AIUsageEventCreate(
                        usage_scope="system_internal",
                        capability_code="reader_grammar_bundle",
                        billing_mode="internal_only",
                        status="succeeded",
                        reader_job_id=claim.job_id,
                        reader_run_id=claim.run_id,
                        usage_data=None,
                        metadata_json={"base_id": "x"},
                    )
                )
    assert result == event_id
    fetchval = fake_pool.acquire.return_value.__aenter__.return_value.fetchval
    meta = _metadata_from_record_call(fetchval)
    assert meta["execution_id"] == str(corr.execution_id)
    codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
    assert USAGE_EVENT_PERSISTED_ZERO in codes
    assert USAGE_MISSING_BEFORE_EVENT in codes


@pytest.mark.anyio
async def test_record_ai_usage_event_persist_failure_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    fake_conn = AsyncMock()
    fake_conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=fake_conn),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    with execution_scope(corr):
        with patch("app.services.ai_usage.service.db_connection") as db:
            db.DB_POOL = fake_pool
            with caplog.at_level(logging.INFO, logger=ued.logger.name):
                result = await record_ai_usage_event(
                    AIUsageEventCreate(
                        usage_scope="system_internal",
                        capability_code="reader_translation",
                        billing_mode="internal_only",
                        status="succeeded",
                        reader_job_id=claim.job_id,
                        reader_run_id=claim.run_id,
                        usage_data={
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                    )
                )
        assert current_execution() is corr
    assert result is None
    codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
    assert USAGE_EVENT_PERSIST_FAILED in codes


@pytest.mark.anyio
async def test_record_without_reader_scope_skips_reader_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    assert current_execution() is None
    with patch("app.services.ai_usage.service.db_connection") as db:
        db.DB_POOL = fake_pool
        with caplog.at_level(logging.INFO, logger=ued.logger.name):
            result = await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope="system_internal",
                    capability_code="dictionary_lookup",
                    billing_mode="internal_only",
                    status="succeeded",
                    usage_data={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                    metadata_json={"caller": "dictionary"},
                )
            )
    assert result == event_id
    assert not _diagnostic_records(caplog)
    # metadata should not gain correlation keys
    args = fake_pool.acquire.return_value.__aenter__.return_value.fetchval.await_args.args
    meta_args = [a for a in args if isinstance(a, dict)]
    assert meta_args
    assert "execution_id" not in meta_args[-1]


@pytest.mark.anyio
async def test_record_nonzero_usage_no_mismatch_on_span_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")
    usage = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    try:
        with execution_scope(corr):
            with patch("app.services.ai_usage.service.db_connection") as db:
                db.DB_POOL = fake_pool
                await record_ai_usage_event(
                    AIUsageEventCreate(
                        usage_scope="system_internal",
                        capability_code="reader_vocabulary",
                        billing_mode="internal_only",
                        status="succeeded",
                        reader_job_id=claim.job_id,
                        reader_run_id=claim.run_id,
                        usage_data=usage,
                    )
                )
            span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
            with patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                with caplog.at_level(logging.INFO, logger=ued.logger.name):
                    await end_worker_span_success(
                        ai_usage_event_id=event_id,
                        usage_data=usage,
                        model_route="r",
                        model_name="m",
                        model_provider="p",
                        capability_code="reader_vocabulary",
                    )
        codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
        assert USAGE_SPAN_EVENT_MISMATCH not in codes
        kwargs = recorder.end_span.await_args.kwargs
        extra = kwargs.get("extra_metadata") or {}
        assert extra.get("execution_id") == str(corr.execution_id)
        assert kwargs.get("status") == STATUS_SUCCEEDED
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_event_zero_span_nonzero_emits_mismatch_without_mutating(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    span_usage = {
        "input_tokens": 11885,
        "output_tokens": 3074,
        "total_tokens": 14959,
    }
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    try:
        with execution_scope(corr):
            with patch("app.services.ai_usage.service.db_connection") as db:
                db.DB_POOL = fake_pool
                await record_ai_usage_event(
                    AIUsageEventCreate(
                        usage_scope="system_internal",
                        capability_code="reader_grammar_bundle",
                        billing_mode="internal_only",
                        status="succeeded",
                        reader_job_id=claim.job_id,
                        reader_run_id=claim.run_id,
                        usage_data=None,
                    )
                )
            span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
            with patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                with caplog.at_level(logging.INFO, logger=ued.logger.name):
                    await end_worker_span_success(
                        ai_usage_event_id=event_id,
                        usage_data=span_usage,
                        model_route="r",
                        model_name="m",
                        model_provider="p",
                        capability_code="reader_grammar_bundle",
                    )
        codes = [getattr(r, "diagnostic_code", None) for r in caplog.records]
        assert USAGE_SPAN_EVENT_MISMATCH in codes
        kwargs = recorder.end_span.await_args.kwargs
        assert kwargs["input_tokens"] == 11885
        assert kwargs["total_tokens"] == 14959
    finally:
        set_default_recorder(None)


# ---------------------------------------------------------------------------
# Failure / superseded span correlation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ender,expected_status,failure_class,failure_code",
    [
        (
            lambda: end_worker_span_fence_violation(),
            STATUS_SUPERSEDED,
            "publish_fence",
            "publish_fence_failed",
        ),
        (
            lambda: end_worker_span_execution_error(
                failure_class="provider", failure_code="TimeoutError"
            ),
            STATUS_FAILED,
            "provider",
            "TimeoutError",
        ),
        (
            lambda: end_worker_span_generic_exception(
                layer="translation", exc=RuntimeError("boom")
            ),
            STATUS_FAILED,
            "translation_execution",
            "RuntimeError",
        ),
    ],
)
async def test_failure_span_paths_persist_full_correlation(
    ender,
    expected_status: str,
    failure_class: str,
    failure_code: str,
) -> None:
    claim = _claim(attempt_count=2)
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    agent_run_id = uuid4()
    corr = corr.with_agent_run_id(agent_run_id)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    try:
        with execution_scope(corr):
            with patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                await ender()
        kwargs = recorder.end_span.await_args.kwargs
        assert kwargs["status"] == expected_status
        assert kwargs["failure_class"] == failure_class
        assert kwargs["failure_code"] == failure_code
        extra = kwargs["extra_metadata"]
        _assert_correlation_metadata(
            extra,
            claim=claim,
            execution_id=corr.execution_id,
            attempt_ordinal=2,
            capability="reader_translation",
        )
        assert extra["agent_run_id"] == str(agent_run_id)
        assert extra["span_id"] == str(span.span_id)
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_agent_run_exception_retains_agent_run_id_on_correlation() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")

    class BoomAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("llm down")

    with execution_scope(corr):
        model_cfg = SimpleNamespace(
            provider="p", model_name="m", profile_name="pr"
        )
        with patch(
            "app.llm.agent_runner.build_model_for_route",
            return_value=(object(), model_cfg),
        ), patch("app.llm.agent_runner.assert_real_llm_allowed"):
            with pytest.raises(RuntimeError, match="llm down"):
                await agent_runner.run_agent_with_route(
                    agent=BoomAgent(),
                    prompt="hi",
                    deps=None,
                    route=MODEL_ROUTE_READER_LAYER_TRANSLATION,
                )
        # agent_run_id minted before run; retained on ContextVar after exception
        active = current_execution()
        assert active is not None
        assert active.agent_run_id is not None
        assert active.execution_id == corr.execution_id


@pytest.mark.anyio
async def test_run_agent_with_route_without_scope_skips_agent_run_id() -> None:
    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(output="ok")

    assert current_execution() is None
    with patch(
        "app.llm.agent_runner.build_model_for_route",
        return_value=(
            object(),
            SimpleNamespace(provider="p", model_name="m", profile_name="pr"),
        ),
    ), patch("app.llm.agent_runner.assert_real_llm_allowed"):
        result = await agent_runner.run_agent_with_route(
            agent=OkAgent(),
            prompt="hi",
            deps=None,
            route=MODEL_ROUTE_READER_LAYER_TRANSLATION,
        )
    assert not hasattr(result, "_claread_agent_run_id") or getattr(
        result, "_claread_agent_run_id", None
    ) is None


@pytest.mark.anyio
async def test_run_reader_scoped_agent_mints_and_attaches_agent_run_id() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")

    class OkAgent:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            return SimpleNamespace(output="ok", prompt=prompt)

    with execution_scope(corr):
        result = await agent_runner.run_reader_scoped_agent(OkAgent(), "p")
        active = current_execution()
        assert active is not None
        assert active.agent_run_id is not None
        assert result._claread_agent_run_id == active.agent_run_id


@pytest.mark.anyio
async def test_run_reader_scoped_agent_exception_retains_agent_run_id() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")

    class BoomAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("provider failed")

    with execution_scope(corr):
        with pytest.raises(RuntimeError, match="provider failed"):
            await agent_runner.run_reader_scoped_agent(BoomAgent(), "p")
        active = current_execution()
        assert active is not None
        assert active.agent_run_id is not None


@pytest.mark.anyio
async def test_run_reader_scoped_agent_multiple_calls_mint_distinct_ids() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    ids: list[UUID] = []

    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            active = current_execution()
            assert active is not None and active.agent_run_id is not None
            ids.append(active.agent_run_id)
            return SimpleNamespace(output="ok")

    with execution_scope(corr):
        await agent_runner.run_reader_scoped_agent(OkAgent(), "one")
        await agent_runner.run_reader_scoped_agent(OkAgent(), "two")
    assert len(ids) == 2
    assert ids[0] != ids[1]


@pytest.mark.anyio
async def test_run_reader_scoped_agent_without_scope_is_plain_run() -> None:
    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(output="plain")

    assert current_execution() is None
    result = await agent_runner.run_reader_scoped_agent(OkAgent(), "p")
    assert result.output == "plain"
    assert getattr(result, "_claread_agent_run_id", None) is None


# ---------------------------------------------------------------------------
# T4.2a-O3 duration provenance
# ---------------------------------------------------------------------------


def test_extract_provider_request_timing_unavailable_by_default() -> None:
    ms, status, field = extract_provider_request_timing(
        SimpleNamespace(output="x"),
        {"input_tokens": 1, "output_tokens": 2},
    )
    assert ms is None
    assert status == PROVIDER_DURATION_STATUS_UNAVAILABLE
    assert field is None


def test_generic_usage_or_timing_maps_cannot_mark_provider_timing_available() -> None:
    """P1 negative: same-named fields without adapter envelope stay unavailable."""
    from app.services.ai_usage.execution_diagnostics import (
        make_provider_response_timing_envelope,
    )

    # Arbitrary usage mapping with look-alike keys.
    ms, status, field = extract_provider_request_timing(
        None,
        {
            "input_tokens": 1,
            "request_duration_ms": 123.4,
            "llm_latency_ms": 99,
            "provider_request_duration_ms": 50,
        },
    )
    assert ms is None
    assert status == PROVIDER_DURATION_STATUS_UNAVAILABLE
    assert field is None

    # Generic result.timing / response_timing blobs are not trusted.
    ms2, status2, field2 = extract_provider_request_timing(
        SimpleNamespace(
            timing={"request_duration_ms": 200},
            response_timing={"llm_latency_ms": 300},
            provider_timing={"provider_request_duration_ms": 400},
            usage=SimpleNamespace(details={"request_duration_ms": 500}),
        ),
        {"request_duration_ms": 600},
    )
    assert ms2 is None
    assert status2 == PROVIDER_DURATION_STATUS_UNAVAILABLE
    assert field2 is None

    # Control: dedicated adapter envelope is the only available path.
    envelope = make_provider_response_timing_envelope(
        provider_request_duration_ms=88,
        source_adapter="test_adapter",
    )
    ms3, status3, field3 = extract_provider_request_timing(
        SimpleNamespace(
            output="ok",
            _claread_provider_response_timing=envelope,
            timing={"request_duration_ms": 999},  # still ignored
        ),
        {"request_duration_ms": 999},
    )
    assert ms3 == 88
    assert status3 == PROVIDER_DURATION_STATUS_AVAILABLE
    assert field3 == "provider_request_duration_ms"


@pytest.mark.anyio
async def test_agent_run_duration_local_monotonic_not_provider_latency() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    times = iter([100.0, 100.250])  # 250ms

    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(output="ok", usage=None)

    with execution_scope(corr):
        with patch("time.perf_counter", side_effect=lambda: next(times)):
            result = await agent_runner.run_reader_scoped_agent(OkAgent(), "p")
        provenance = current_duration_provenance()
        assert provenance is not None
        assert provenance.agent_run_duration_ms == 250
        assert provenance.agent_run_duration_source == "local_monotonic"
        assert provenance.agent_run_duration_boundary == "agent.run"
        assert (
            provenance.provider_request_duration_status
            == PROVIDER_DURATION_STATUS_UNAVAILABLE
        )
        assert provenance.provider_request_duration_ms is None
        assert result._claread_agent_run_duration_ms == 250
        # Must not be framed as provider latency column
        meta = merge_correlation_metadata({}, corr, duration=provenance)
        assert meta[META_AGENT_RUN_DURATION_MS] == 250
        assert meta[META_PROVIDER_REQUEST_DURATION_STATUS] == (
            PROVIDER_DURATION_STATUS_UNAVAILABLE
        )
        assert "latency_ms" not in meta
        assert meta["duration_schema_kind"] == DURATION_SCHEMA_KIND
        assert meta["duration_schema_version"] == DURATION_SCHEMA_VERSION
        codes = meta.get("usage_diagnostic_codes") or []
        assert AGENT_RUN_DURATION_RECORDED in codes
        assert PROVIDER_TIMING_UNAVAILABLE in codes


@pytest.mark.anyio
async def test_provider_timing_available_only_with_adapter_envelope() -> None:
    from app.services.ai_usage.execution_diagnostics import (
        make_provider_response_timing_envelope,
    )

    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")

    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                output="ok",
                usage=SimpleNamespace(
                    details={"request_duration_ms": 999},  # must NOT count
                    input_tokens=1,
                    output_tokens=1,
                ),
                _claread_provider_response_timing=make_provider_response_timing_envelope(
                    provider_request_duration_ms=88,
                    source_adapter="unit_test",
                ),
            )

    with execution_scope(corr):
        await agent_runner.run_reader_scoped_agent(OkAgent(), "p")
        provenance = current_duration_provenance()
        assert provenance is not None
        assert provenance.provider_request_duration_ms == 88
        assert (
            provenance.provider_request_duration_status
            == PROVIDER_DURATION_STATUS_AVAILABLE
        )
        assert provenance.provider_request_duration_field == (
            "provider_request_duration_ms"
        )
        assert provenance.provider_request_duration_source == (
            "provider_adapter_envelope"
        )
        meta = merge_correlation_metadata({}, corr, duration=provenance)
        assert meta[META_PROVIDER_REQUEST_DURATION_MS] == 88
        assert PROVIDER_TIMING_AVAILABLE in (meta.get("usage_diagnostic_codes") or [])


@pytest.mark.anyio
async def test_agent_run_exception_retains_duration_provenance() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_grammar_bundle")
    times = iter([10.0, 10.1])

    class BoomAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    with execution_scope(corr):
        with patch("time.perf_counter", side_effect=lambda: next(times)):
            with pytest.raises(RuntimeError, match="boom"):
                await agent_runner.run_reader_scoped_agent(BoomAgent(), "p")
        provenance = current_duration_provenance()
        assert provenance is not None
        assert provenance.agent_run_duration_ms == 100
        assert (
            provenance.provider_request_duration_status
            == PROVIDER_DURATION_STATUS_UNAVAILABLE
        )
        assert current_execution() is not None
        assert current_execution().agent_run_id is not None


@pytest.mark.anyio
async def test_duration_merged_into_usage_event_without_setting_latency_ms() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_title_generation")
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    times = iter([1.0, 1.05])

    class OkAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(output="ok", usage=None)

    with execution_scope(corr):
        with patch("time.perf_counter", side_effect=lambda: next(times)):
            await agent_runner.run_reader_scoped_agent(OkAgent(), "p")
        with patch("app.services.ai_usage.service.db_connection") as db:
            db.DB_POOL = fake_pool
            result = await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope="system_internal",
                    capability_code="reader_title_generation",
                    billing_mode="internal_only",
                    status="succeeded",
                    reader_job_id=claim.job_id,
                    reader_run_id=claim.run_id,
                    usage_data={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                    # deliberately leave latency_ms unset
                )
            )
    assert result == event_id
    fetchval = fake_pool.acquire.return_value.__aenter__.return_value.fetchval
    # latency_ms positional arg in INSERT must remain None
    # Find metadata dict with duration keys
    meta = None
    latency_args = []
    for arg in fetchval.await_args.args:
        if isinstance(arg, dict) and "agent_run_duration_ms" in arg:
            meta = arg
        if arg is None or isinstance(arg, int):
            latency_args.append(arg)
    assert meta is not None
    assert meta[META_AGENT_RUN_DURATION_MS] == 50
    assert meta[META_PROVIDER_REQUEST_DURATION_STATUS] == (
        PROVIDER_DURATION_STATUS_UNAVAILABLE
    )
    assert "latency_ms" not in meta


@pytest.mark.anyio
async def test_failure_span_includes_duration_provenance() -> None:
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_translation")
    times = iter([5.0, 5.2])
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)

    class BoomAgent:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("down")

    try:
        with execution_scope(corr):
            with patch("time.perf_counter", side_effect=lambda: next(times)):
                with pytest.raises(RuntimeError):
                    await agent_runner.run_reader_scoped_agent(BoomAgent(), "p")
            with patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                await end_worker_span_execution_error(
                    failure_class="provider",
                    failure_code="RuntimeError",
                )
        extra = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert extra[META_AGENT_RUN_DURATION_MS] == 200
        assert extra[META_PROVIDER_REQUEST_DURATION_STATUS] == (
            PROVIDER_DURATION_STATUS_UNAVAILABLE
        )
        assert extra.get("execution_id") == str(corr.execution_id)
        assert "latency_ms" not in extra
    finally:
        set_default_recorder(None)


def test_assert_lease_owners_belong_to_harness_rejects_foreign_worker() -> None:
    from app.services.reader_orchestration.validation_isolation import (
        assert_lease_owners_belong_to_harness,
    )

    with pytest.raises(RuntimeError, match="foreign lease_owner"):
        assert_lease_owners_belong_to_harness(
            ["t42a-o2-v1-diagnostic", "reader-enhancement-worker:host:1"],
            harness_lease_owner="t42a-o2-v1-diagnostic",
        )


def test_assert_lease_owners_belong_to_harness_accepts_harness_only() -> None:
    from app.services.reader_orchestration.validation_isolation import (
        assert_lease_owners_belong_to_harness,
    )

    assert_lease_owners_belong_to_harness(
        ["t42a-o2-v1-diagnostic", "t42a-o2-v1-diagnostic"],
        harness_lease_owner="t42a-o2-v1-diagnostic",
    )


def test_assert_no_external_enhancement_workers_raises_when_found() -> None:
    from app.services.reader_orchestration.validation_isolation import (
        ExternalWorkerProcess,
        assert_no_external_enhancement_workers,
    )

    fake = [
        ExternalWorkerProcess(
            pid=99999,
            name="python",
            cmdline="python scripts/run_reader_enhancement_worker.py --once",
        )
    ]
    with patch(
        "app.services.reader_orchestration.validation_isolation.list_enhancement_worker_processes",
        return_value=fake,
    ):
        with pytest.raises(RuntimeError, match="External reader enhancement worker"):
            assert_no_external_enhancement_workers()


def test_worker_isolation_fails_closed_when_process_inspection_unavailable() -> None:
    from app.services.reader_orchestration.validation_isolation import (
        ProcessInspectionUnavailable,
        assert_no_external_enhancement_workers,
    )

    with patch(
        "app.services.reader_orchestration.validation_isolation._load_psutil",
        side_effect=ProcessInspectionUnavailable("inspection unavailable"),
    ):
        with pytest.raises(ProcessInspectionUnavailable, match="inspection unavailable"):
            assert_no_external_enhancement_workers()


def test_workspace_code_fingerprint_contains_hashes_and_git_identity() -> None:
    from app.services.reader_orchestration.validation_isolation import (
        workspace_code_fingerprint,
    )

    fingerprint = workspace_code_fingerprint()
    assert len(fingerprint["git_head"]) == 40
    assert fingerprint["dirty_target_slice"] in {"true", "false"}
    assert len(fingerprint["agent_runner_sha256"]) == 64
    assert len(fingerprint["execution_diagnostics_sha256"]) == 64
    assert fingerprint["has_run_reader_scoped_agent"] == "true"


# ---------------------------------------------------------------------------
# Real worker paths with deterministic fake executors
# ---------------------------------------------------------------------------


def _translation_context(claim: ClaimResult) -> TranslationJobContext:
    return TranslationJobContext(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        order_index=0,
        expected_generation=1,
        operation_fingerprint=claim.operation_fingerprint,
        source_language="en",
        target_language="zh",
        source_text="Hello world",
        text_hash="hash",
        anchor_segments=(),
        reading_goal="general",
        reading_variant="default",
        strategy_version="v1",
        strategy_hash="sh",
        layer_policy_hash="ph",
        translation_prompt_lines=(),
    )


def _vocabulary_context(claim: ClaimResult) -> VocabularyJobContext:
    return VocabularyJobContext(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        order_index=0,
        expected_generation=1,
        operation_fingerprint=claim.operation_fingerprint,
        source_language="en",
        source_text="Hello world",
        text_hash="hash",
        anchor_segments=(),
        reading_goal="general",
        reading_variant="default",
        strategy_version="v1",
        strategy_hash="sh",
        layer_policy_hash="ph",
        vocabulary_prompt_lines=(),
    )


def _grammar_context(claim: ClaimResult) -> GrammarJobContext:
    return GrammarJobContext(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        order_index=0,
        expected_generation=1,
        operation_fingerprint=claim.operation_fingerprint,
        source_language="en",
        source_text="Hello world",
        text_hash="hash",
        anchor_segments=(),
        reading_goal="general",
        reading_variant="default",
        strategy_version="v1",
        strategy_hash="sh",
        layer_policy_hash="ph",
        grammar_prompt_lines=(),
    )


def _published_translation(claim: ClaimResult) -> PublishedTranslationLayer:
    return PublishedTranslationLayer(
        layer_id=uuid4(),
        reading_record_id=claim.reading_record_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        generation=1,
        event=MagicMock(),
    )


def _published_vocabulary(claim: ClaimResult) -> PublishedVocabularyLayer:
    return PublishedVocabularyLayer(
        layer_id=uuid4(),
        reading_record_id=claim.reading_record_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        generation=1,
        event=MagicMock(),
    )


def _published_grammar(claim: ClaimResult) -> PublishedGrammarBundle:
    return PublishedGrammarBundle(
        reading_record_id=claim.reading_record_id,
        base_id=claim.base_id or uuid4(),
        unit_id="u1",
        generation=1,
        grammar_note_layer=None,
        sentence_analysis_layer=None,
        events=(),
        no_op=True,
    )


class _CorrCaptureExecutor:
    """Fake executor that records active correlation during generate/translate."""

    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        self.usage = usage or {
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
        }
        self.seen_execution_ids: list[UUID] = []
        self.agent_run_ids: list[UUID | None] = []
        self.calls = 0

    def _capture(self) -> None:
        self.calls += 1
        active = current_execution()
        assert active is not None, "executor must run inside execution scope"
        self.seen_execution_ids.append(active.execution_id)
        self.agent_run_ids.append(active.agent_run_id)


@pytest.mark.anyio
async def test_real_translation_unit_worker_shares_execution_id() -> None:
    claim = _claim_result(attempt_count=1)
    usage = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeTranslator:
        async def translate(self, context: TranslationJobContext) -> TranslationExecutionResult:
            capturer._capture()
            return TranslationExecutionResult(
                output=TranslationLayerGenerationOutput.model_construct(groups=[]),
                usage_data=usage,
                prompt_version="test",
                model_profile="p",
                model_provider="prov",
                model_name="m",
            )

    worker = TranslationWorkerService(pool=MagicMock(), translator=_FakeTranslator())
    worker._load_job_context = AsyncMock(return_value=_translation_context(claim))  # type: ignore[method-assign]
    worker._layer_publisher.publish_unit_translation = AsyncMock(  # type: ignore[method-assign]
        return_value=_published_translation(claim)
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ), patch(
            "app.services.reader_orchestration.translation_worker.hydrate_translation_layer_output",
            return_value=TranslationLayerOutput.model_construct(groups=[]),
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_translation_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        assert capturer.seen_execution_ids
        exec_id = capturer.seen_execution_ids[0]
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == str(exec_id)
        assert span_meta["execution_id"] == str(exec_id)
        assert event_meta["attempt_ordinal"] == 1
        assert span_meta["attempt_ordinal"] == 1
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_real_translation_batch_worker_shares_execution_id() -> None:
    from app.services.reader_orchestration.translation_worker import (
        TranslationBatchExecutionResult,
        TranslationBatchJobContext,
    )

    claim = _claim_result(attempt_count=1)
    usage = {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeBatch:
        async def translate_batch(self, context: Any) -> TranslationBatchExecutionResult:
            capturer._capture()
            return TranslationBatchExecutionResult(
                output=MagicMock(),
                usage_data=usage,
                prompt_version="test",
                model_profile="p",
                model_provider="prov",
                model_name="m",
            )

    batch_ctx = MagicMock(spec=TranslationBatchJobContext)
    batch_ctx.job_id = claim.job_id
    batch_ctx.run_id = claim.run_id
    batch_ctx.reading_record_id = claim.reading_record_id
    batch_ctx.user_id = claim.user_id
    batch_ctx.base_id = claim.base_id
    batch_ctx.operation_fingerprint = claim.operation_fingerprint
    batch_ctx.units = (MagicMock(),)
    batch_ctx.target_language = "zh"
    batch_ctx.source_language = "en"

    worker = TranslationWorkerService(pool=MagicMock(), batch_translator=_FakeBatch())
    worker._load_batch_job_context = AsyncMock(return_value=batch_ctx)  # type: ignore[method-assign]
    worker._layer_publisher.publish_article_translation_batch = AsyncMock(  # type: ignore[method-assign]
        return_value=PublishedTranslationBatch(
            reading_record_id=claim.reading_record_id,
            base_id=claim.base_id or uuid4(),
            generation=1,
            layers=(),
        )
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ), patch(
            "app.services.reader_orchestration.translation_worker.hydrate_translation_batch_output",
            return_value=[],
        ), patch(
            "app.services.reader_orchestration.translation_worker._build_batch_quality_json",
            return_value={},
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_translation_batch_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"]
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_real_vocabulary_unit_worker_shares_execution_id() -> None:
    claim = _claim_result(attempt_count=1)
    usage = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeVocab:
        async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
            capturer._capture()
            return VocabularyExecutionResult(
                output=VocabularyLayerOutput(),
                usage_data=usage,
                prompt_version="test",
            )

    worker = VocabularyWorkerService(pool=MagicMock(), executor=_FakeVocab())
    worker._load_job_context = AsyncMock(return_value=_vocabulary_context(claim))  # type: ignore[method-assign]
    worker._layer_publisher.publish_unit_vocabulary = AsyncMock(  # type: ignore[method-assign]
        return_value=_published_vocabulary(claim)
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_vocabulary_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"] == str(
            capturer.seen_execution_ids[0]
        )
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_real_vocabulary_batch_worker_shares_execution_id() -> None:
    from app.services.reader_orchestration.vocabulary_worker import (
        VocabularyBatchCandidateOutput,
        VocabularyBatchExecutionResult,
        VocabularyBatchJobContext,
        VocabularyBatchUnitCandidateOutput,
    )

    claim = _claim_result(attempt_count=2)
    usage = {"input_tokens": 11, "output_tokens": 6, "total_tokens": 17}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeBatch:
        async def generate_batch(self, context: Any) -> VocabularyBatchExecutionResult:
            capturer._capture()
            return VocabularyBatchExecutionResult(
                output=VocabularyBatchCandidateOutput(
                    units=[
                        VocabularyBatchUnitCandidateOutput(unit_id="u1", items=[]),
                    ]
                ),
                usage_data=usage,
                prompt_version="test",
            )

    batch_ctx = MagicMock(spec=VocabularyBatchJobContext)
    batch_ctx.job_id = claim.job_id
    batch_ctx.run_id = claim.run_id
    batch_ctx.reading_record_id = claim.reading_record_id
    batch_ctx.user_id = claim.user_id
    batch_ctx.base_id = claim.base_id
    batch_ctx.operation_fingerprint = claim.operation_fingerprint
    batch_ctx.units = (MagicMock(unit_id="u1"),)
    batch_ctx.source_language = "en"
    batch_ctx.target_unit_ids = ("u1",)

    worker = VocabularyWorkerService(pool=MagicMock(), batch_executor=_FakeBatch())
    worker._load_batch_job_context = AsyncMock(return_value=batch_ctx)  # type: ignore[method-assign]
    worker._layer_publisher.publish_article_vocabulary_batch = AsyncMock(  # type: ignore[method-assign]
        return_value=PublishedVocabularyBatch(
            reading_record_id=claim.reading_record_id,
            base_id=claim.base_id or uuid4(),
            generation=1,
            layers=(),
        )
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ), patch(
            "app.services.reader_orchestration.vocabulary_worker._build_vocabulary_batch_outputs",
            return_value=([], {}),
        ), patch(
            "app.services.reader_orchestration.vocabulary_worker._build_vocabulary_batch_quality_json",
            return_value={},
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_vocabulary_batch_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        assert capturer.seen_execution_ids
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"]
        assert event_meta["attempt_ordinal"] == 2
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_real_grammar_unit_worker_shares_execution_id() -> None:
    claim = _claim_result(attempt_count=1)
    usage = {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeGrammar:
        async def generate(self, context: GrammarJobContext) -> GrammarExecutionResult:
            capturer._capture()
            return GrammarExecutionResult(
                output=GrammarBundleOutput(),
                usage_data=usage,
                prompt_version="test",
            )

    worker = GrammarBundleWorkerService(pool=MagicMock(), executor=_FakeGrammar())
    worker._load_job_context = AsyncMock(return_value=_grammar_context(claim))  # type: ignore[method-assign]
    worker._layer_publisher.publish_unit_grammar_bundle = AsyncMock(  # type: ignore[method-assign]
        return_value=_published_grammar(claim)
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_grammar_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"]
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_real_grammar_batch_worker_shares_execution_id() -> None:
    from app.services.reader_orchestration.grammar_worker import (
        GrammarBatchExecutionResult,
    )

    claim = _claim_result(attempt_count=1)
    capturer = _CorrCaptureExecutor()
    usage = {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8}

    class _FakeBatch:
        async def generate_batch(self, context: Any) -> GrammarBatchExecutionResult:
            capturer._capture()
            return GrammarBatchExecutionResult(
                outputs=[("u1", GrammarBundleOutput())],
                usage_data=usage,
            )

    worker = GrammarBundleWorkerService(pool=MagicMock(), batch_executor=_FakeBatch())
    batch_ctx = MagicMock()
    batch_ctx.job_id = claim.job_id
    batch_ctx.run_id = claim.run_id
    batch_ctx.reading_record_id = claim.reading_record_id
    batch_ctx.user_id = claim.user_id
    batch_ctx.base_id = claim.base_id
    batch_ctx.operation_fingerprint = claim.operation_fingerprint
    batch_ctx.units = (MagicMock(unit_id="u1"),)
    batch_ctx.source_language = "en"
    worker._load_batch_job_context = AsyncMock(return_value=batch_ctx)  # type: ignore[method-assign]
    worker._layer_publisher.publish_article_grammar_batch = AsyncMock(  # type: ignore[method-assign]
        return_value=PublishedGrammarBatch(
            reading_record_id=claim.reading_record_id,
            base_id=claim.base_id or uuid4(),
            generation=1,
            layers=(),
            layer_ids=(),
            layer_types=(),
            no_op=True,
        )
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ), patch(
            "app.services.reader_orchestration.grammar_worker._build_batch_quality_json",
            return_value={},
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_grammar_batch_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"]
        assert current_execution() is None
    finally:
        set_default_recorder(None)


# ---------------------------------------------------------------------------
# Grammar-window pipeline correlation (outer scope covers process+publish)
# ---------------------------------------------------------------------------


def _window_usage() -> dict[str, int]:
    return {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}


def _window_ready_result(
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "status": "candidates_ready",
        "candidates": [],
        "usage_data": usage if usage is not None else _window_usage(),
        "prompt_version": "test-window",
        "model_route": "reader_layer_grammar_bundle",
        "model_profile": "fake-profile",
        "model_provider": "fake-provider",
        "model_name": "fake-model",
    }


def _published_window() -> PublishedWindowResult:
    return PublishedWindowResult(
        accepted_count=0,
        grammar_note_layer_ids=(),
        sentence_analysis_layer_ids=(),
        skipped=True,
    )


async def _run_window_attempt(
    *,
    claim: ClaimResult,
    process_side_effect: Any,
    publisher: Any | None = None,
    record_usage_side_effect: Any | None = None,
) -> Any:
    """Drive ``_run_grammar_window_attempt`` with mocked claim/process/publish."""
    runner = ReaderEnhancementPipelineRunner(
        pool=MagicMock(),
        enable_zplus_grammar=False,
        grammar_window_worker_service=MagicMock(),
        grammar_window_publisher=MagicMock(),
        job_runtime=MagicMock(),
    )
    runner._job_runtime.claim_next_job = AsyncMock(return_value=claim)  # type: ignore[method-assign]
    runner._load_window_ids_from_job = AsyncMock(  # type: ignore[method-assign]
        return_value=(uuid4(), uuid4())
    )
    runner._mark_window_run_running = AsyncMock()  # type: ignore[method-assign]
    runner._mark_window_run_status = AsyncMock()  # type: ignore[method-assign]
    runner._mark_window_run_failed = AsyncMock()  # type: ignore[method-assign]
    runner._mark_analysis_window_failed = AsyncMock()  # type: ignore[method-assign]
    runner._build_failure_diagnostics = AsyncMock(return_value={})  # type: ignore[method-assign]
    runner._load_window_publish_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "window_index": 0,
            "target_unit_ids": [],
            "target_anchor_ids": [],
        }
    )
    runner._job_runtime.transition = AsyncMock()  # type: ignore[method-assign]

    process = process_side_effect
    if not callable(process) or isinstance(process, Exception):
        process_mock = AsyncMock(side_effect=process)
    else:
        process_mock = AsyncMock(side_effect=process)
    runner._grammar_window_worker = MagicMock()
    runner._grammar_window_worker.process_window_job = process_mock

    pub = publisher if publisher is not None else AsyncMock(return_value=_published_window())
    runner._grammar_window_publisher = MagicMock()
    runner._grammar_window_publisher.publish_window_grammar_bundle = pub

    if record_usage_side_effect is not None:
        runner._record_window_success_usage = AsyncMock(  # type: ignore[method-assign]
            side_effect=record_usage_side_effect
        )
        runner._record_window_failure_usage = AsyncMock(  # type: ignore[method-assign]
            side_effect=record_usage_side_effect
        )

    return await runner._run_grammar_window_attempt(
        record_id=claim.reading_record_id,
        base_id=claim.base_id or uuid4(),
        expected_generation=1,
        lease_owner="test-window",
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(seconds=1),
    )


@pytest.mark.anyio
async def test_pipeline_grammar_window_success_shares_execution_id() -> None:
    """candidates_ready → publish → usage → span share one execution_id."""
    claim = _claim_result(attempt_count=2)
    seen: list[UUID] = []
    usage = _window_usage()

    async def process(*, claim: ClaimResult) -> dict[str, Any]:
        active = current_execution()
        assert active is not None
        assert active.attempt_ordinal == 2
        seen.append(active.execution_id)
        return _window_ready_result(usage=usage)

    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            db.DB_POOL = fake_pool
            result = await _run_window_attempt(claim=claim, process_side_effect=process)
        assert result.outcome == "succeeded"
        assert result.processed_job is True
        assert len(seen) == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"] == str(seen[0])
        assert event_meta["attempt_ordinal"] == 2
        assert span_meta["attempt_ordinal"] == 2
        assert event_meta["correlation_reader_job_id"] == str(claim.job_id)
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_pipeline_grammar_window_execution_error_keeps_correlation() -> None:
    claim = _claim_result(attempt_count=1)
    seen: list[UUID] = []

    async def process(*, claim: ClaimResult) -> dict[str, Any]:
        active = current_execution()
        assert active is not None
        seen.append(active.execution_id)
        raise GrammarWindowExecutionError(
            "provider timeout",
            retryable=True,
            failure_class="provider",
            failure_code="TimeoutError",
            prompt_version="test-window",
            model_route="reader_layer_grammar_bundle",
            model_profile="p",
            model_provider="prov",
            model_name="m",
        )

    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            db.DB_POOL = fake_pool
            result = await _run_window_attempt(claim=claim, process_side_effect=process)
        assert result.outcome == "retry_later"
        assert seen
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"] == str(seen[0])
        assert recorder.end_span.await_args.kwargs["status"] == STATUS_FAILED
        assert recorder.end_span.await_args.kwargs["failure_code"] == "TimeoutError"
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_pipeline_grammar_window_generic_exception_keeps_correlation() -> None:
    claim = _claim_result(attempt_count=1)
    seen: list[UUID] = []

    async def process(*, claim: ClaimResult) -> dict[str, Any]:
        active = current_execution()
        assert active is not None
        seen.append(active.execution_id)
        raise RuntimeError("unexpected boom")

    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            result = await _run_window_attempt(claim=claim, process_side_effect=process)
        assert result.outcome == "failed_terminal"
        assert seen
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert span_meta["execution_id"] == str(seen[0])
        assert span_meta["attempt_ordinal"] == 1
        assert recorder.end_span.await_args.kwargs["status"] == STATUS_FAILED
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_pipeline_grammar_window_fence_superseded_keeps_correlation() -> None:
    claim = _claim_result(attempt_count=1)
    seen: list[UUID] = []

    async def process(*, claim: ClaimResult) -> dict[str, Any]:
        active = current_execution()
        assert active is not None
        seen.append(active.execution_id)
        return _window_ready_result()

    async def publish_fail(**kwargs: Any) -> PublishedWindowResult:
        active = current_execution()
        assert active is not None
        assert active.execution_id == seen[0]
        raise FenceViolationError("stale generation")

    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            result = await _run_window_attempt(
                claim=claim,
                process_side_effect=process,
                publisher=AsyncMock(side_effect=publish_fail),
            )
        assert result.outcome == "superseded"
        assert seen
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert span_meta["execution_id"] == str(seen[0])
        assert recorder.end_span.await_args.kwargs["status"] == STATUS_SUPERSEDED
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_pipeline_grammar_window_usage_persist_failure_does_not_change_outcome() -> None:
    claim = _claim_result(attempt_count=1)
    seen: list[UUID] = []

    async def process(*, claim: ClaimResult) -> dict[str, Any]:
        active = current_execution()
        assert active is not None
        seen.append(active.execution_id)
        return _window_ready_result()

    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            # pool None → persist failure path returns None without raising
            db.DB_POOL = None
            result = await _run_window_attempt(claim=claim, process_side_effect=process)
        assert result.outcome == "succeeded"
        assert result.processed_job is True
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert span_meta["execution_id"] == str(seen[0])
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_pipeline_grammar_window_retries_do_not_collapse_execution_ids() -> None:
    job_id = uuid4()
    run_id = uuid4()
    seen: list[UUID] = []

    for attempt in (1, 2):
        claim = ClaimResult(
            job_id=job_id,
            run_id=run_id,
            reading_record_id=uuid4(),
            user_id=uuid4(),
            base_id=uuid4(),
            job_type="build_grammar_bundle_window",
            target_type="unit_range",
            target_key="w1",
            expected_generation=1,
            operation_fingerprint="fp:window",
            attempt_count=attempt,
            lease_owner="w",
            lease_token=uuid4(),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )

        async def process(*, claim: ClaimResult, _attempt: int = attempt) -> dict[str, Any]:
            active = current_execution()
            assert active is not None
            assert active.attempt_ordinal == _attempt
            seen.append(active.execution_id)
            return _window_ready_result()

        event_id = uuid4()
        fake_pool = _fake_pool_returning(event_id)
        recorder = MagicMock()
        recorder.end_span = AsyncMock()
        set_default_recorder(recorder)
        span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
        try:
            with patch("app.services.ai_usage.service.db_connection") as db, patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                db.DB_POOL = fake_pool
                await _run_window_attempt(claim=claim, process_side_effect=process)
            meta = _metadata_from_record_call(
                fake_pool.acquire.return_value.__aenter__.return_value.fetchval
            )
            assert meta["attempt_ordinal"] == attempt
        finally:
            set_default_recorder(None)
        assert current_execution() is None

    assert len(seen) == 2
    assert seen[0] != seen[1]


@pytest.mark.anyio
async def test_process_window_job_alone_does_not_bind_execution_scope() -> None:
    """Inner process_window_job must not mint its own execution_id."""
    claim = _claim_result(attempt_count=1)

    class _FakeWindow:
        async def generate(self, context: dict[str, Any]) -> GrammarWindowExecutionResult:
            assert current_execution() is None
            return GrammarWindowExecutionResult(
                candidates=[],
                usage_data=_window_usage(),
                prompt_version="test",
            )

    service = GrammarWindowWorkerService(pool=MagicMock(), executor=_FakeWindow())
    service.preflight_window_job = AsyncMock(return_value=PreflightResult.PROCEED)  # type: ignore[method-assign]
    service._load_window_context = AsyncMock(  # type: ignore[method-assign]
        return_value={"target_anchors": [{"anchor_segment_id": "a1"}]}
    )
    service._heartbeat_interval = timedelta(hours=1)

    result = await service.process_window_job(claim=claim)
    assert result["status"] == "candidates_ready"
    assert current_execution() is None


@pytest.mark.anyio
async def test_real_display_title_worker_shares_execution_id() -> None:
    claim = _claim_result(attempt_count=1)
    usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    capturer = _CorrCaptureExecutor(usage)

    class _FakeTitle:
        async def generate(self, context: DisplayTitleJobContext) -> DisplayTitleExecutionResult:
            capturer._capture()
            return DisplayTitleExecutionResult(
                title_zh="测试标题",
                usage_data=usage,
                prompt_version="test",
                model_profile="p",
                model_provider="prov",
                model_name="m",
            )

    title_input = DisplayTitleGenerationInput(
        source_title="Hello",
        source_type="paste",
        source_language="en",
        input_strategy="default",
        section_headings=(),
        content_preview="Hello world",
        preview_char_length=11,
        base_char_length=11,
        source_metadata={},
    )
    context = DisplayTitleJobContext(
        job_id=claim.job_id,
        run_id=claim.run_id,
        reading_record_id=claim.reading_record_id,
        user_id=claim.user_id,
        base_id=claim.base_id or uuid4(),
        expected_generation=1,
        operation_fingerprint=claim.operation_fingerprint,
        attempt_count=1,
        title_input=title_input,
    )
    worker = DisplayTitleWorkerService(pool=MagicMock(), generator=_FakeTitle())
    worker._load_job_context = AsyncMock(return_value=context)  # type: ignore[method-assign]
    worker._complete_title_job_success = AsyncMock()  # type: ignore[method-assign]
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ), patch(
            "app.services.reader_orchestration.display_title_worker.normalize_generated_title_zh",
            side_effect=lambda x: x,
        ):
            db.DB_POOL = fake_pool
            result = await worker.process_claimed_display_title_job(claim=claim)
        assert result.status == "succeeded"
        assert capturer.calls == 1
        event_meta = _metadata_from_record_call(
            fake_pool.acquire.return_value.__aenter__.return_value.fetchval
        )
        span_meta = recorder.end_span.await_args.kwargs["extra_metadata"]
        assert event_meta["execution_id"] == span_meta["execution_id"]
        assert current_execution() is None
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_retry_attempts_do_not_collapse_execution_ids() -> None:
    """Two claims (attempt 1 then 2) must mint distinct execution_ids."""
    job_id = uuid4()
    run_id = uuid4()
    seen: list[UUID] = []

    class _FakeVocab:
        async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
            active = current_execution()
            assert active is not None
            seen.append(active.execution_id)
            return VocabularyExecutionResult(
                output=VocabularyLayerOutput(),
                usage_data={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

    for attempt in (1, 2):
        claim = ClaimResult(
            job_id=job_id,
            run_id=run_id,
            reading_record_id=uuid4(),
            user_id=uuid4(),
            base_id=uuid4(),
            job_type="build_vocabulary",
            target_type="unit",
            target_key="u1",
            expected_generation=1,
            operation_fingerprint="fp:same",
            attempt_count=attempt,
            lease_owner="w",
            lease_token=uuid4(),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        worker = VocabularyWorkerService(pool=MagicMock(), executor=_FakeVocab())
        worker._load_job_context = AsyncMock(return_value=_vocabulary_context(claim))  # type: ignore[method-assign]
        worker._layer_publisher.publish_unit_vocabulary = AsyncMock(  # type: ignore[method-assign]
            return_value=_published_vocabulary(claim)
        )
        event_id = uuid4()
        fake_pool = _fake_pool_returning(event_id)
        recorder = MagicMock()
        recorder.end_span = AsyncMock()
        set_default_recorder(recorder)
        span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
        try:
            with patch("app.services.ai_usage.service.db_connection") as db, patch(
                "app.services.reader_orchestration.span_recorder.current_span",
                return_value=span,
            ):
                db.DB_POOL = fake_pool
                await worker.process_claimed_vocabulary_job(claim=claim)
            meta = _metadata_from_record_call(
                fake_pool.acquire.return_value.__aenter__.return_value.fetchval
            )
            assert meta["attempt_ordinal"] == attempt
        finally:
            set_default_recorder(None)
        assert current_execution() is None

    assert len(seen) == 2
    assert seen[0] != seen[1]


@pytest.mark.anyio
async def test_worker_exception_clears_execution_contextvar() -> None:
    claim = _claim_result(attempt_count=1)

    class _Boom:
        async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
            raise RuntimeError("executor boom")

    worker = VocabularyWorkerService(pool=MagicMock(), executor=_Boom())
    worker._load_job_context = AsyncMock(return_value=_vocabulary_context(claim))  # type: ignore[method-assign]
    worker._job_runtime.transition = AsyncMock()  # type: ignore[method-assign]
    worker._mark_run_status = AsyncMock()  # type: ignore[method-assign]
    worker._record_failed_usage_event = AsyncMock(return_value=None)  # type: ignore[method-assign]
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    try:
        with patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            result = await worker.process_claimed_vocabulary_job(claim=claim)
        assert result.status in {"failed_terminal", "retry_later"}
        # ContextVar cleaned even after exception path
        assert current_execution() is None
        # Failure span still got correlation
        extra = recorder.end_span.await_args.kwargs.get("extra_metadata") or {}
        assert "execution_id" in extra
        assert extra["attempt_ordinal"] == 1
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_single_agent_run_invariant_per_worker_execution() -> None:
    """Current workers call the executor once per process; pin the invariant."""
    claim = _claim_result(attempt_count=1)
    calls = {"n": 0}

    class _Once:
        async def generate(self, context: VocabularyJobContext) -> VocabularyExecutionResult:
            calls["n"] += 1
            return VocabularyExecutionResult(
                output=VocabularyLayerOutput(),
                usage_data={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            )

    worker = VocabularyWorkerService(pool=MagicMock(), executor=_Once())
    worker._load_job_context = AsyncMock(return_value=_vocabulary_context(claim))  # type: ignore[method-assign]
    worker._layer_publisher.publish_unit_vocabulary = AsyncMock(  # type: ignore[method-assign]
        return_value=_published_vocabulary(claim)
    )
    event_id = uuid4()
    fake_pool = _fake_pool_returning(event_id)
    recorder = MagicMock()
    recorder.end_span = AsyncMock()
    set_default_recorder(recorder)
    span = SpanContext(span_id=uuid4(), trace_id=uuid4(), parent_span_id=None)
    try:
        with patch("app.services.ai_usage.service.db_connection") as db, patch(
            "app.services.reader_orchestration.span_recorder.current_span",
            return_value=span,
        ):
            db.DB_POOL = fake_pool
            await worker.process_claimed_vocabulary_job(claim=claim)
        assert calls["n"] == 1
    finally:
        set_default_recorder(None)


@pytest.mark.anyio
async def test_fake_vocabulary_executor_callable_under_correlation() -> None:
    """Smoke: built-in FakeVocabularyExecutor works under execution scope."""
    claim = _claim()
    corr = begin_execution_from_claim(claim, capability_code="reader_vocabulary")
    ctx = _vocabulary_context(_claim_result())
    with execution_scope(corr):
        result = await FakeVocabularyExecutor().generate(ctx)
        assert result.usage_data is not None
        assert current_execution() is corr
    assert current_execution() is None


# ---------------------------------------------------------------------------
# Non-Reader regression (analysis / dictionary / daily-reader style)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_reader_analysis_dictionary_daily_reader_no_reader_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shared extract_run_usage / record path without Reader scope.

    Mirrors analysis / dictionary / daily-reader callers that use
    ``run_agent_with_route`` + ``extract_run_usage`` + ``record_ai_usage_event``
    outside ``@with_execution_correlation``.
    """
    from pydantic_ai.usage import RunUsage

    assert current_execution() is None
    with caplog.at_level(logging.INFO, logger=ued.logger.name):
        # dictionary-style extract
        usage = agent_runner.extract_run_usage(
            SimpleNamespace(usage=RunUsage(input_tokens=4, output_tokens=2))
        )
        assert usage is not None
        # analysis-style agent route (no agent_run_id)
        class OkAgent:
            async def run(self, *args: Any, **kwargs: Any) -> Any:
                return SimpleNamespace(
                    output="ok",
                    usage=RunUsage(input_tokens=1, output_tokens=1),
                )

        with patch(
            "app.llm.agent_runner.build_model_for_route",
            return_value=(
                object(),
                SimpleNamespace(provider="p", model_name="m", profile_name="pr"),
            ),
        ), patch("app.llm.agent_runner.assert_real_llm_allowed"):
            result = await agent_runner.run_agent_with_route(
                agent=OkAgent(),
                prompt="analyze",
                deps=None,
                route=MODEL_ROUTE_READER_LAYER_TRANSLATION,
            )
        assert getattr(result, "_claread_agent_run_id", None) is None
        # daily-reader style persist
        event_id = uuid4()
        fake_pool = _fake_pool_returning(event_id)
        with patch("app.services.ai_usage.service.db_connection") as db:
            db.DB_POOL = fake_pool
            persisted = await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope="system_internal",
                    capability_code="daily_reader_highlight",
                    billing_mode="internal_only",
                    status="succeeded",
                    usage_data=usage,
                    metadata_json={"source": "daily_reader"},
                )
            )
        assert persisted == event_id

    assert not _diagnostic_records(caplog)
    assert current_execution() is None
    assert current_usage_outcome() is None


def test_normalize_token_totals_empty_and_aggregate() -> None:
    assert normalize_token_totals(None)["total_tokens"] == 0
    assert normalize_token_totals({})["total_tokens"] == 0
    nested = {"aggregate": {"input_tokens": 3, "output_tokens": 4}}
    assert normalize_token_totals(nested)["total_tokens"] == 7


@pytest.mark.anyio
async def test_with_execution_correlation_decorator_requires_claim() -> None:
    @with_execution_correlation("reader_translation")
    async def _fn(self: Any, *, claim: Any = None) -> str:
        return "ok"

    with pytest.raises(TypeError, match="requires claim"):
        await _fn(object())
