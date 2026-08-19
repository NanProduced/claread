"""OBS-01A: AgentRun usage snapshot on success and failure paths.

FunctionModel only — no real provider calls. Covers:
- Final structured-output failure keeps accumulated provider usage (Experiment A).
- Partial responses then transport unknown keeps confirmed usage only (Experiment B).
- Zero-response transport failure is unavailable (never fabricated).
- CancelledError propagates unwrapped.
- Success path and non-Reader scope stay compatible.
- invocation-keyed failure usage events: idempotent replay + conflict detection.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from app.database.connection import init_connection
from app.llm.agent_runner import run_reader_scoped_agent
from app.services.ai_usage.execution_diagnostics import (
    USAGE_COMPLETENESS_COMPLETE,
    USAGE_COMPLETENESS_PARTIAL,
    USAGE_COMPLETENESS_UNAVAILABLE,
    begin_execution_from_claim,
    current_agent_run_usage_snapshot,
    execution_scope,
)
from app.services.ai_usage.service import (
    AIUsageEventCreate,
    record_reader_failed_usage_event,
)

pytestmark = pytest.mark.anyio


class _FinalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


def _final_call(*, tool_call_id: str = "call-1") -> ToolCallPart:
    return ToolCallPart(
        tool_name="final_result",
        args={"answer": "ok"},
        tool_call_id=tool_call_id,
    )


def _invalid_call(*, ordinal: int) -> ToolCallPart:
    return ToolCallPart(
        tool_name="final_result",
        args="definitely-not-json",
        tool_call_id=f"bad-{ordinal}",
    )


def _response(
    *,
    ordinal: int,
    part: ToolCallPart,
    usage: RequestUsage,
) -> ModelResponse:
    return ModelResponse(
        parts=[part],
        usage=usage,
        provider_response_id=f"resp-{ordinal}",
        finish_reason="stop",
        model_name="function-model",
    )


def _claim() -> SimpleNamespace:
    return SimpleNamespace(
        job_id=uuid4(),
        run_id=uuid4(),
        attempt_count=1,
        reading_record_id=uuid4(),
        operation_fingerprint="test_fingerprint",
    )


def _correlation():
    return begin_execution_from_claim(
        _claim(), capability_code="reader_grammar_bundle"
    )


def _agent(model_fn) -> Agent:
    return Agent(
        model=FunctionModel(model_fn),
        output_type=_FinalOutput,
        name="test_usage_snapshot_agent",
        retries={"tools": 1, "output": 2},
    )


# ---------------------------------------------------------------------------
# Experiment A: final structured-output failure keeps accumulated usage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_final_structured_output_failure_keeps_accumulated_usage() -> None:
    calls = {"i": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        ordinal = calls["i"] + 1
        calls["i"] = ordinal
        return _response(
            ordinal=ordinal,
            part=_invalid_call(ordinal=ordinal),
            usage=RequestUsage(
                input_tokens=20,
                output_tokens=2,
                cache_read_tokens=4,
            ),
        )

    correlation = _correlation()
    with execution_scope(correlation):
        with pytest.raises(Exception) as excinfo:
            await run_reader_scoped_agent(_agent(model_fn), "go")
        # Original pydantic-ai exception type, unwrapped.
        assert type(excinfo.value).__name__ == "UnexpectedModelBehavior"

        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        assert snapshot.execution_id == correlation.execution_id
        assert snapshot.agent_run_id is not None
        assert snapshot.run_completed is False
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_COMPLETE
        assert snapshot.usage_data is not None
        assert snapshot.usage_data["input_tokens"] == 60
        assert snapshot.usage_data["output_tokens"] == 6
        assert snapshot.usage_data["cache_read_tokens"] == 12
        assert snapshot.provider_response_count == 3
        assert [r.provider_response_id for r in snapshot.provider_responses] == [
            "resp-1",
            "resp-2",
            "resp-3",
        ]
        assert snapshot.provider_responses_truncated_count == 0
        assert snapshot.retry_prompt_count == 2


# ---------------------------------------------------------------------------
# Experiment B: partial response then transport unknown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_partial_response_then_transport_unknown_is_partial() -> None:
    calls = {"i": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        calls["i"] += 1
        if calls["i"] == 1:
            return _response(
                ordinal=1,
                part=_invalid_call(ordinal=1),
                usage=RequestUsage(
                    input_tokens=100,
                    output_tokens=9,
                    cache_read_tokens=80,
                ),
            )
        raise TimeoutError("transport unknown")

    correlation = _correlation()
    with execution_scope(correlation):
        with pytest.raises(TimeoutError):
            await run_reader_scoped_agent(_agent(model_fn), "go")

        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_PARTIAL
        assert snapshot.usage_data is not None
        assert snapshot.usage_data["input_tokens"] == 100
        assert snapshot.usage_data["output_tokens"] == 9
        assert snapshot.usage_data["cache_read_tokens"] == 80
        assert snapshot.provider_response_count == 1
        assert snapshot.provider_responses[0].provider_response_id == "resp-1"
        assert snapshot.retry_prompt_count == 1
        metadata = snapshot.to_metadata()
        # Console metadata must mark in-flight request billing as unknown.
        assert metadata["usage_completeness"] == USAGE_COMPLETENESS_PARTIAL
        assert metadata["provider_response_count"] == 1


# ---------------------------------------------------------------------------
# Zero-response transport failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zero_response_transport_failure_is_unavailable() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        raise TimeoutError("no response")

    correlation = _correlation()
    with execution_scope(correlation):
        with pytest.raises(TimeoutError):
            await run_reader_scoped_agent(_agent(model_fn), "go")

        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_UNAVAILABLE
        assert snapshot.usage_data is None
        assert snapshot.provider_response_count == 0
        assert snapshot.provider_responses == ()


# ---------------------------------------------------------------------------
# CancelledError propagates unwrapped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancelled_error_propagates_unwrapped() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        raise asyncio.CancelledError()

    correlation = _correlation()
    with execution_scope(correlation):
        with pytest.raises(asyncio.CancelledError):
            await run_reader_scoped_agent(_agent(model_fn), "go")

        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_UNAVAILABLE


# ---------------------------------------------------------------------------
# Success path compatibility
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_success_path_returns_result_and_complete_snapshot() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_final_call(),
            usage=RequestUsage(input_tokens=11, output_tokens=3),
        )

    correlation = _correlation()
    with execution_scope(correlation):
        result = await run_reader_scoped_agent(_agent(model_fn), "go")

        assert isinstance(result.output, _FinalOutput)
        assert result.output.answer == "ok"
        assert result.usage.input_tokens == 11
        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        assert snapshot.run_completed is True
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_COMPLETE
        assert snapshot.usage_data is not None
        assert snapshot.usage_data["input_tokens"] == 11
        assert snapshot.provider_response_count == 1
        # Existing attach contract preserved.
        assert result._claread_agent_run_id == snapshot.agent_run_id


# ---------------------------------------------------------------------------
# Non-Reader scope unchanged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_reader_scope_runs_plain_and_writes_no_snapshot() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_final_call(),
            usage=RequestUsage(input_tokens=5, output_tokens=1),
        )

    result = await run_reader_scoped_agent(_agent(model_fn), "go")
    assert isinstance(result.output, _FinalOutput)
    assert current_agent_run_usage_snapshot() is None


# ---------------------------------------------------------------------------
# Stale snapshot must not leak into the next execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execution_scope_resets_snapshot_and_runner_clears_entry() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_final_call(),
            usage=RequestUsage(input_tokens=7, output_tokens=2),
        )

    with execution_scope(_correlation()):
        await run_reader_scoped_agent(_agent(model_fn), "go")
        assert current_agent_run_usage_snapshot() is not None

    # Fresh scope: stale snapshot from the previous execution is gone.
    with execution_scope(_correlation()):
        assert current_agent_run_usage_snapshot() is None


# ---------------------------------------------------------------------------
# PostgreSQL-backed failure usage persistence (isolated schema per test)
# ---------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = re.sub(
    r"^\s*SET search_path = public, pg_catalog;\s*$",
    "",
    (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    ),
    flags=re.MULTILINE,
)


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://claread:claread_dev@127.0.0.1:5432/claread",
    )


@pytest.fixture
async def usage_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_agent_usage_snapshot_{uuid4().hex}"
    try:
        admin = await asyncpg.connect(_database_url())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for usage snapshot tests: {exc}")

    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            _database_url(),
            min_size=1,
            max_size=4,
            init=init_connection,
            server_settings={"search_path": f'"{schema_name}", public'},
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


def _failed_event(
    *,
    error_code: str = "UnexpectedModelBehavior",
    usage_data: dict | None = None,
) -> AIUsageEventCreate:
    return AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="reader_grammar_bundle",
        billing_mode="internal_only",
        status="failed",
        error_code=error_code,
        error_message="raw exception text must not persist",
        usage_data=usage_data,
        model_route="reader_layer_grammar_bundle",
        model_provider="deepseek",
        model_name="deepseek-v4-flash",
        metadata_json={},
    )


@pytest.mark.anyio
async def test_failed_usage_event_persists_once_with_snapshot(
    usage_pool: asyncpg.Pool,
) -> None:
    from app.services.ai_usage.execution_diagnostics import (
        build_snapshot_metadata_fragment,
    )

    correlation = _correlation()
    with execution_scope(correlation):
        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is None

        fragment = build_snapshot_metadata_fragment(
            usage_completeness=USAGE_COMPLETENESS_COMPLETE,
            usage_source="agent_run_state",
            provider_responses=(
                {
                    "ordinal": 1,
                    "provider_response_id": "resp-1",
                    "input_tokens": 60,
                    "output_tokens": 6,
                    "cache_read_tokens": 12,
                    "cache_write_tokens": 0,
                    "finish_reason": "stop",
                },
            ),
            provider_responses_truncated_count=0,
            retry_prompt_count=2,
        )
        event_id, disposition = await record_reader_failed_usage_event(
            _failed_event(usage_data={"input_tokens": 60, "output_tokens": 6}),
            pool=usage_pool,
            snapshot_fragment=fragment,
        )
        assert event_id is not None
        assert disposition == "inserted"

        row = await usage_pool.fetchrow(
            "SELECT status, error_code, error_message, input_tokens,"
            " output_tokens, metadata_json FROM ai_usage_events WHERE id = $1",
            event_id,
        )
        assert row["status"] == "failed"
        assert row["error_code"] == "unexpected_model_behavior"
        assert row["error_message"] is None
        assert row["input_tokens"] == 60
        assert row["output_tokens"] == 6
        metadata = row["metadata_json"]
        assert metadata["usage_completeness"] == "complete"
        assert metadata["provider_response_count"] == 1
        assert metadata["retry_prompt_count"] == 2
        assert "output_retry_count" not in metadata
        assert "output_retry_exhausted" not in metadata
        assert metadata["usage_invocation_observation"]["sha256"]

        invocation_key = row["invocation_key"] if "invocation_key" in row.keys() else (
            await usage_pool.fetchval(
                "SELECT invocation_key FROM ai_usage_events WHERE id = $1",
                event_id,
            )
        )
        assert invocation_key == (
            f"reader:reader_grammar_bundle:{correlation.reader_job_id}:1:1"
        )


@pytest.mark.anyio
async def test_failed_usage_replay_same_observation_returns_same_row(
    usage_pool: asyncpg.Pool,
) -> None:
    from app.services.ai_usage.execution_diagnostics import (
        build_snapshot_metadata_fragment,
    )

    fragment = build_snapshot_metadata_fragment(
        usage_completeness=USAGE_COMPLETENESS_PARTIAL,
        usage_source="agent_run_state",
        provider_responses=(),
        provider_responses_truncated_count=0,
        retry_prompt_count=0,
    )
    correlation = _correlation()
    with execution_scope(correlation):
        first_id, first_disposition = await record_reader_failed_usage_event(
            _failed_event(usage_data={"input_tokens": 100, "output_tokens": 9}),
            pool=usage_pool,
            snapshot_fragment=fragment,
        )
        assert first_disposition == "inserted"

        replay_id, replay_disposition = await record_reader_failed_usage_event(
            _failed_event(usage_data={"input_tokens": 100, "output_tokens": 9}),
            pool=usage_pool,
            snapshot_fragment=fragment,
        )
        assert replay_disposition == "replayed"
        assert replay_id == first_id

        count = await usage_pool.fetchval(
            "SELECT count(*) FROM ai_usage_events"
        )
        assert count == 1


@pytest.mark.anyio
async def test_failed_usage_conflict_does_not_overwrite(
    usage_pool: asyncpg.Pool,
) -> None:
    from app.services.ai_usage.execution_diagnostics import (
        build_snapshot_metadata_fragment,
    )

    fragment = build_snapshot_metadata_fragment(
        usage_completeness=USAGE_COMPLETENESS_COMPLETE,
        usage_source="agent_run_state",
        provider_responses=(),
        provider_responses_truncated_count=0,
        retry_prompt_count=0,
    )
    correlation = _correlation()
    with execution_scope(correlation):
        first_id, _ = await record_reader_failed_usage_event(
            _failed_event(usage_data={"input_tokens": 60, "output_tokens": 6}),
            pool=usage_pool,
            snapshot_fragment=fragment,
        )
        conflicting_id, disposition = await record_reader_failed_usage_event(
            _failed_event(usage_data={"input_tokens": 999, "output_tokens": 1}),
            pool=usage_pool,
            snapshot_fragment=fragment,
        )
        assert disposition == "conflict"

        row = await usage_pool.fetchrow(
            "SELECT input_tokens, metadata_json FROM ai_usage_events"
            " WHERE id = $1",
            first_id,
        )
        # Old row untouched.
        assert row["input_tokens"] == 60
        count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
        assert count == 1
        assert isinstance(conflicting_id, UUID)


@pytest.mark.anyio
async def test_failed_usage_without_correlation_falls_back_plain(
    usage_pool: asyncpg.Pool,
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", usage_pool)
    event_id, disposition = await record_reader_failed_usage_event(
        _failed_event(error_code="model_output_invalid"),
    )
    assert event_id is not None
    assert disposition == "recorded_plain"
    row = await usage_pool.fetchrow(
        "SELECT error_code, invocation_key FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["error_code"] == "model_output_invalid"
    assert row["invocation_key"] is None


# ---------------------------------------------------------------------------
# R1: end-to-end keyed failure persistence without manual snapshot fragment
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_keyed_failure_end_to_end_without_manual_fragment(
    usage_pool: asyncpg.Pool,
) -> None:
    from app.services.ai_usage.execution_diagnostics import (
        current_execution,
    )

    calls = {"i": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        ordinal = calls["i"] + 1
        calls["i"] = ordinal
        return _response(
            ordinal=ordinal,
            part=_invalid_call(ordinal=ordinal),
            usage=RequestUsage(
                input_tokens=20,
                output_tokens=2,
                cache_read_tokens=4,
            ),
        )

    claim = _claim()
    correlation = begin_execution_from_claim(
        claim, capability_code="reader_grammar_bundle"
    )
    with execution_scope(correlation):
        from pydantic_ai.exceptions import UnexpectedModelBehavior

        with pytest.raises(UnexpectedModelBehavior):
            await run_reader_scoped_agent(_agent(model_fn), "go")

        active = current_execution()
        assert active is not None
        assert active.agent_run_id is not None

        event = _failed_event()
        # Caller-forged identity fields must be overridden by the
        # authoritative correlation fragment.
        event.metadata_json = {
            "execution_id": "forged-execution-id",
            "attempt_ordinal": 999,
            "agent_run_id": "forged-agent-run-id",
        }
        event_id, disposition = await record_reader_failed_usage_event(
            event,
            pool=usage_pool,
        )
        assert disposition == "inserted"
        assert event_id is not None

    row = await usage_pool.fetchrow(
        "SELECT invocation_key, input_tokens, output_tokens, cache_read_tokens,"
        " metadata_json FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["invocation_key"] == (
        f"reader:reader_grammar_bundle:{claim.job_id}:1:1"
    )
    assert row["input_tokens"] == 60
    assert row["output_tokens"] == 6
    assert row["cache_read_tokens"] == 12
    metadata = row["metadata_json"]
    assert metadata["execution_id"] == str(active.execution_id)
    assert metadata["agent_run_id"] == str(active.agent_run_id)
    assert metadata["attempt_ordinal"] == 1
    assert metadata["correlation_reader_job_id"] == str(claim.job_id)
    assert metadata["correlation_reader_run_id"] == str(claim.run_id)
    assert metadata["correlation_capability_code"] == "reader_grammar_bundle"
    assert metadata["correlation_operation_fingerprint"] == "test_fingerprint"
    assert metadata["usage_completeness"] == "complete"
    assert metadata["provider_response_count"] == 3
    observation = metadata["usage_invocation_observation"]
    assert observation["schema_version"] == 1
    assert observation["sha256"]


# ---------------------------------------------------------------------------
# R1: snapshot double identity gate (execution_id AND agent_run_id)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_second_mint_invalidates_first_snapshot() -> None:
    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_final_call(),
            usage=RequestUsage(input_tokens=7, output_tokens=2),
        )

    from app.services.ai_usage.execution_diagnostics import (
        current_agent_run_usage_snapshot,
        current_execution,
        mint_agent_run_id,
        valid_agent_run_usage_snapshot,
    )

    with execution_scope(_correlation()):
        await run_reader_scoped_agent(_agent(model_fn), "go")
        first = current_agent_run_usage_snapshot()
        assert first is not None

        # A second agent run mints a fresh agent_run_id on the correlation.
        mint_agent_run_id()
        active = current_execution()
        assert active is not None
        assert active.agent_run_id != first.agent_run_id

        # The first snapshot is no longer usable for THIS execution.
        assert valid_agent_run_usage_snapshot(active) is None
        # Only a fresh run re-populates a matching snapshot.
        assert current_agent_run_usage_snapshot() is first  # stale value kept
        assert valid_agent_run_usage_snapshot(active) is None


@pytest.mark.anyio
async def test_snapshot_gate_requires_correlation_agent_run_id() -> None:
    from app.services.ai_usage.execution_diagnostics import (
        valid_agent_run_usage_snapshot,
    )

    correlation = _correlation()
    assert correlation.agent_run_id is None

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_final_call(),
            usage=RequestUsage(input_tokens=3, output_tokens=1),
        )

    with execution_scope(correlation):
        await run_reader_scoped_agent(_agent(model_fn), "go")
        # mint_agent_run_id updated the ContextVar correlation; the frozen
        # local still has agent_run_id=None and must not consume snapshots.
        assert valid_agent_run_usage_snapshot(correlation) is None


# ---------------------------------------------------------------------------
# R1: concurrent first-write serialization (real PostgreSQL, barrier)
# ---------------------------------------------------------------------------


class _BarrierAcquire:
    """Async-context-manager proxy that waits on a barrier before entering."""

    def __init__(self, inner: Any, barrier: asyncio.Barrier) -> None:
        self._inner = inner
        self._barrier = barrier

    async def __aenter__(self) -> Any:
        await self._barrier.wait()
        return await self._inner.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._inner.__aexit__(*exc_info)


class _BarrierPool:
    """Pool wrapper forcing both callers past acquire() before either txn starts."""

    def __init__(self, pool: asyncpg.Pool, barrier: asyncio.Barrier) -> None:
        self._pool = pool
        self._barrier = barrier

    def acquire(self) -> _BarrierAcquire:
        return _BarrierAcquire(self._pool.acquire(), self._barrier)


@pytest.mark.anyio
async def test_concurrent_same_observation_exactly_one_row(
    usage_pool: asyncpg.Pool,
) -> None:
    barrier = asyncio.Barrier(2)
    pool = _BarrierPool(usage_pool, barrier)
    claim = _claim()
    correlation = begin_execution_from_claim(
        claim, capability_code="reader_grammar_bundle"
    )
    event = _failed_event(usage_data={"input_tokens": 60, "output_tokens": 6})

    with execution_scope(correlation):
        results = await asyncio.gather(
            record_reader_failed_usage_event(event, pool=pool),
            record_reader_failed_usage_event(event, pool=pool),
        )

    dispositions = sorted(d for _, d in results)
    assert dispositions == ["inserted", "replayed"]
    assert results[0][0] == results[1][0]
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_concurrent_conflicting_observation_keeps_first_row(
    usage_pool: asyncpg.Pool,
) -> None:
    barrier = asyncio.Barrier(2)
    pool = _BarrierPool(usage_pool, barrier)
    claim = _claim()
    correlation = begin_execution_from_claim(
        claim, capability_code="reader_grammar_bundle"
    )
    first_event = _failed_event(usage_data={"input_tokens": 60, "output_tokens": 6})
    conflicting_event = _failed_event(
        usage_data={"input_tokens": 999, "output_tokens": 1}
    )

    with execution_scope(correlation):
        results = await asyncio.gather(
            record_reader_failed_usage_event(first_event, pool=pool),
            record_reader_failed_usage_event(conflicting_event, pool=pool),
        )

    dispositions = sorted(d for _, d in results)
    assert dispositions == ["conflict", "inserted"]
    inserted_id = next(i for i, d in results if d == "inserted")
    conflicting_id = next(i for i, d in results if d == "conflict")
    assert conflicting_id == inserted_id
    row = await usage_pool.fetchrow(
        "SELECT input_tokens FROM ai_usage_events WHERE id = $1", inserted_id
    )
    # Which observation wins the race is nondeterministic; the invariant is
    # that the FIRST inserted row survives untouched (60 or 999, not a mix).
    assert row["input_tokens"] in {60, 999}
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


# ---------------------------------------------------------------------------
# R1: capability recovery via wrap_run must return a valid result
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_capability_recovery_returns_result_not_failure() -> None:
    from pydantic_ai.capabilities.abstract import AbstractCapability
    from pydantic_ai.run import AgentRunResult

    class _RecoveringCapability(AbstractCapability):  # type: ignore[type-arg]
        async def wrap_run(self, ctx, *, handler):  # noqa: ANN001
            try:
                return await handler()
            except Exception:
                return AgentRunResult(_FinalOutput(answer="recovered"))

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        return _response(
            ordinal=1,
            part=_invalid_call(ordinal=1),
            usage=RequestUsage(input_tokens=5, output_tokens=1),
        )

    agent = Agent(
        model=FunctionModel(model_fn),
        output_type=_FinalOutput,
        name="test_recovery_agent",
        retries={"tools": 0, "output": 0},
        capabilities=[_RecoveringCapability()],
    )

    correlation = _correlation()
    with execution_scope(correlation):
        result = await run_reader_scoped_agent(agent, "go")

        assert result is not None
        assert result.output.answer == "recovered"
        snapshot = current_agent_run_usage_snapshot()
        assert snapshot is not None
        # Recovered runs are NOT failure snapshots.
        assert snapshot.run_completed is True
        assert snapshot.usage_completeness == USAGE_COMPLETENESS_COMPLETE


# ---------------------------------------------------------------------------
# R1: semantic outline zero-response transport failure still records keyed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_semantic_outline_zero_response_records_unavailable(
    usage_pool: asyncpg.Pool,
    monkeypatch,
) -> None:
    from app.services.reader_orchestration.semantic_outline_worker import (
        SemanticOutlineGenerationError,
        SemanticOutlineJobContext,
        SemanticOutlineWorkerService,
    )

    async def timeout_model(messages, info: AgentInfo):
        del messages, info
        raise TimeoutError("transport unknown")

    captured: dict = {}

    async def capture_recorder(event, **kwargs):
        captured["event"] = event
        captured["kwargs"] = kwargs
        return await record_reader_failed_usage_event(event, pool=usage_pool)

    monkeypatch.setattr(
        "app.services.reader_orchestration.semantic_outline_worker."
        "record_reader_failed_usage_event",
        capture_recorder,
    )

    worker = SemanticOutlineWorkerService.__new__(SemanticOutlineWorkerService)
    context = SemanticOutlineJobContext(
        job_id=None,  # type: ignore[arg-type]  # avoid reader_jobs FK
        run_id=None,  # type: ignore[arg-type]  # avoid reader_runs FK
        reading_record_id=None,  # type: ignore[arg-type]
        user_id=None,  # avoid users FK in the isolated schema
        base_id=uuid4(),
        expected_generation=1,
        operation_fingerprint="outline_fingerprint",
        attempt_count=1,
        max_attempts=3,
        worker_input=None,  # type: ignore[arg-type]
    )
    error = SemanticOutlineGenerationError(
        "semantic outline agent execution failed",
        failure_class="provider",
        failure_code="TimeoutError",
        retryable=True,
        provider_call_made=True,
        usage_data=None,
    )

    claim = _claim()
    correlation = begin_execution_from_claim(
        claim, capability_code="reader_semantic_outline"
    )
    with execution_scope(correlation):
        with pytest.raises(TimeoutError):
            await run_reader_scoped_agent(_agent(timeout_model), "go")

        event_id = await worker._maybe_record_error_usage(
            context=context,
            error=error,
        )

    assert event_id is not None
    event = captured["event"]
    assert event.usage_data is None  # recorder must not fabricate tokens
    row = await usage_pool.fetchrow(
        "SELECT input_tokens, output_tokens, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["metadata_json"]["usage_completeness"] == "unavailable"


@pytest.mark.anyio
async def test_plain_fallback_normalizes_and_nulls_error_message(
    usage_pool: asyncpg.Pool,
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", usage_pool)
    event = _failed_event(
        error_code="UnexpectedModelBehavior",
        usage_data=None,
    )
    event.error_message = "raw secret failure text"
    event_id, disposition = await record_reader_failed_usage_event(event)
    assert disposition == "recorded_plain"
    assert event_id is not None
    row = await usage_pool.fetchrow(
        "SELECT error_code, error_message FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["error_code"] == "unexpected_model_behavior"
    assert row["error_message"] is None
