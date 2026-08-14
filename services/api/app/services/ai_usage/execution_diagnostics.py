"""Usage presence diagnostics and durable execution correlation.

This module is the **single** constructor for execution correlation keys and
usage-presence diagnostic codes used by Reader orchestration workers.

Design (no migration required for Slice 1):

- ``attempt_ordinal``: durable claim order from ``reader_jobs.attempt_count``
  after a successful claim (incremented atomically at claim time).
- ``execution_id``: UUID minted once per worker execution of a claimed job;
  shared by usage events, worker spans, and structured logs for that attempt.
- ``agent_run_id``: UUID minted once per ``agent.run()`` inside an execution;
  never used as a provider HTTP request id.
- Correlation is persisted under stable metadata keys (schema versioned) on
  ``ai_usage_events.metadata_json`` and ``reader_runtime_spans.metadata_json``.

Query/index note: correlation lives in JSONB metadata for this slice; durable
lookups are by ``reader_job_id`` + ``metadata_json->>'execution_id'`` (or
``attempt_ordinal``). Prefer job-scoped filters. A future migration may promote
columns if cross-record indexing becomes required.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Final, ParamSpec, TypeVar
from uuid import UUID, uuid4

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema / metadata contract
# ---------------------------------------------------------------------------

CORRELATION_SCHEMA_KIND: Final = "usage_execution_correlation"
CORRELATION_SCHEMA_VERSION: Final = "1"

META_SCHEMA_KIND: Final = "correlation_schema_kind"
META_SCHEMA_VERSION: Final = "correlation_schema_version"
META_EXECUTION_ID: Final = "execution_id"
META_ATTEMPT_ORDINAL: Final = "attempt_ordinal"
META_AGENT_RUN_ID: Final = "agent_run_id"
META_SPAN_ID: Final = "span_id"
META_READER_JOB_ID: Final = "correlation_reader_job_id"
META_READER_RUN_ID: Final = "correlation_reader_run_id"
META_OPERATION_FINGERPRINT: Final = "correlation_operation_fingerprint"
META_CAPABILITY_CODE: Final = "correlation_capability_code"
META_DIAGNOSTIC_CODES: Final = "usage_diagnostic_codes"
META_USAGE_PRESENCE: Final = "usage_presence"
META_USAGE_KEY_LIST: Final = "usage_key_list"
META_NORMALIZED_TOTALS: Final = "normalized_token_totals"
META_SPAN_TOTALS: Final = "span_token_totals"
META_EVENT_TOTALS: Final = "event_token_totals"

# ---------------------------------------------------------------------------
# Duration provenance — never alias worker/agent duration as
# provider latency. Does not write ai_usage_events.latency_ms.
# ---------------------------------------------------------------------------

DURATION_SCHEMA_KIND: Final = "duration_provenance"
DURATION_SCHEMA_VERSION: Final = "1"

META_DURATION_SCHEMA_KIND: Final = "duration_schema_kind"
META_DURATION_SCHEMA_VERSION: Final = "duration_schema_version"
META_AGENT_RUN_DURATION_MS: Final = "agent_run_duration_ms"
META_AGENT_RUN_DURATION_SOURCE: Final = "agent_run_duration_source"
META_AGENT_RUN_DURATION_BOUNDARY: Final = "agent_run_duration_boundary"
META_PROVIDER_REQUEST_DURATION_MS: Final = "provider_request_duration_ms"
META_PROVIDER_REQUEST_DURATION_STATUS: Final = "provider_request_duration_status"
META_PROVIDER_REQUEST_DURATION_SOURCE: Final = "provider_request_duration_source"
META_PROVIDER_REQUEST_DURATION_FIELD: Final = "provider_request_duration_field"

# Local monotonic clock around agent.run only — NOT provider HTTP RTT.
AGENT_RUN_DURATION_SOURCE_LOCAL_MONOTONIC: Final = "local_monotonic"
AGENT_RUN_DURATION_BOUNDARY_AGENT_RUN: Final = "agent.run"

# Provider timing: ONLY via a dedicated adapter envelope attached by a
# trusted provider/SDK adapter. Generic usage maps, result.timing, or
# same-named keys in local/agent payloads must NEVER flip status to
# available (O3 fail-closed provenance).
PROVIDER_DURATION_STATUS_AVAILABLE: Final = "available"
PROVIDER_DURATION_STATUS_UNAVAILABLE: Final = "unavailable"
PROVIDER_DURATION_SOURCE_ADAPTER_ENVELOPE: Final = "provider_adapter_envelope"
PROVIDER_DURATION_SOURCE_NONE: Final = "none"

# Envelope contract (set only by provider adapters, never by workers/local
# timers). Kind+version are required; duration field is required and must
# coerce to non-negative ms.
PROVIDER_RESPONSE_TIMING_ATTR: Final = "_claread_provider_response_timing"
PROVIDER_RESPONSE_TIMING_KIND: Final = "claread_provider_response_timing"
PROVIDER_RESPONSE_TIMING_VERSION: Final = "1"
PROVIDER_RESPONSE_TIMING_MS_FIELD: Final = "provider_request_duration_ms"

# ---------------------------------------------------------------------------
# Diagnostic codes (central constants — no magic strings at call sites)
# ---------------------------------------------------------------------------

USAGE_MISSING_AT_ADAPTER: Final = "usage_missing_at_adapter"
USAGE_EMPTY_AT_ADAPTER: Final = "usage_empty_at_adapter"
USAGE_PRESENT_AT_ADAPTER: Final = "usage_present_at_adapter"
USAGE_MISSING_BEFORE_EVENT: Final = "usage_missing_before_event"
USAGE_ZERO_AFTER_NORMALIZATION: Final = "usage_zero_after_normalization"
USAGE_EVENT_PERSISTED_ZERO: Final = "usage_event_persisted_zero"
USAGE_SPAN_EVENT_MISMATCH: Final = "usage_span_event_mismatch"
USAGE_EVENT_PERSIST_FAILED: Final = "usage_event_persist_failed"
USAGE_PRESENT_BEFORE_EVENT: Final = "usage_present_before_event"
USAGE_PRESENT_AFTER_NORMALIZE: Final = "usage_present_after_normalize"
USAGE_EVENT_PERSISTED: Final = "usage_event_persisted"
USAGE_SPAN_WRITTEN: Final = "usage_span_written"
PROVIDER_TIMING_AVAILABLE: Final = "provider_timing_available"
PROVIDER_TIMING_UNAVAILABLE: Final = "provider_timing_unavailable"
AGENT_RUN_DURATION_RECORDED: Final = "agent_run_duration_recorded"

USAGE_DIAGNOSTIC_CODES: Final[frozenset[str]] = frozenset(
    {
        USAGE_MISSING_AT_ADAPTER,
        USAGE_EMPTY_AT_ADAPTER,
        USAGE_PRESENT_AT_ADAPTER,
        USAGE_MISSING_BEFORE_EVENT,
        USAGE_ZERO_AFTER_NORMALIZATION,
        USAGE_EVENT_PERSISTED_ZERO,
        USAGE_SPAN_EVENT_MISMATCH,
        USAGE_EVENT_PERSIST_FAILED,
        USAGE_PRESENT_BEFORE_EVENT,
        USAGE_PRESENT_AFTER_NORMALIZE,
        USAGE_EVENT_PERSISTED,
        USAGE_SPAN_WRITTEN,
        PROVIDER_TIMING_AVAILABLE,
        PROVIDER_TIMING_UNAVAILABLE,
        AGENT_RUN_DURATION_RECORDED,
    }
)

# Stages for structured diagnostics (not durable identity)
STAGE_ADAPTER: Final = "adapter"
STAGE_EXECUTION_RESULT: Final = "execution_result"
STAGE_EVENT_DTO: Final = "event_dto"
STAGE_NORMALIZE: Final = "normalize"
STAGE_EVENT_PERSIST: Final = "event_persist"
STAGE_SPAN_WRITE: Final = "span_write"


@dataclass(frozen=True, slots=True)
class ExecutionCorrelation:
    """Durable correlation for one worker execution of one claimed job attempt."""

    reader_job_id: UUID
    reader_run_id: UUID
    attempt_ordinal: int
    execution_id: UUID
    operation_fingerprint: str | None = None
    capability_code: str | None = None
    agent_run_id: UUID | None = None
    span_id: UUID | None = None
    reading_record_id: UUID | None = None

    def with_agent_run_id(self, agent_run_id: UUID) -> ExecutionCorrelation:
        return replace(self, agent_run_id=agent_run_id)

    def with_span_id(self, span_id: UUID) -> ExecutionCorrelation:
        return replace(self, span_id=span_id)

    def with_capability(self, capability_code: str) -> ExecutionCorrelation:
        return replace(self, capability_code=capability_code)

    def to_metadata(self) -> dict[str, Any]:
        """Stable metadata fragment for usage events and span rows."""
        payload: dict[str, Any] = {
            META_SCHEMA_KIND: CORRELATION_SCHEMA_KIND,
            META_SCHEMA_VERSION: CORRELATION_SCHEMA_VERSION,
            META_EXECUTION_ID: str(self.execution_id),
            META_ATTEMPT_ORDINAL: int(self.attempt_ordinal),
            META_READER_JOB_ID: str(self.reader_job_id),
            META_READER_RUN_ID: str(self.reader_run_id),
        }
        if self.operation_fingerprint is not None:
            payload[META_OPERATION_FINGERPRINT] = self.operation_fingerprint
        if self.capability_code is not None:
            payload[META_CAPABILITY_CODE] = self.capability_code
        if self.agent_run_id is not None:
            payload[META_AGENT_RUN_ID] = str(self.agent_run_id)
        if self.span_id is not None:
            payload[META_SPAN_ID] = str(self.span_id)
        if self.reading_record_id is not None:
            payload["reading_record_id"] = str(self.reading_record_id)
        return payload


@dataclass(frozen=True, slots=True)
class UsagePresenceSnapshot:
    """Stage-local classification of usage_data without prompt/content."""

    stage: str
    diagnostic_code: str
    usage_is_none: bool
    usage_is_empty_mapping: bool
    usage_key_list: tuple[str, ...]
    normalized_totals: dict[str, int]
    provider: str | None = None
    model: str | None = None
    capability_code: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRecordOutcome:
    """Result of best-effort usage event persistence with diagnostics."""

    event_id: UUID | None
    recorded_totals: dict[str, int]
    diagnostic_codes: tuple[str, ...]
    usage_presence: UsagePresenceSnapshot


@dataclass(frozen=True, slots=True)
class DurationProvenance:
    """Lineage for agent-run vs provider-request timing.

    Field dictionary (stable metadata keys):

    | Field | Meaning | Clock / source | Represents provider duration? |
    |-------|---------|----------------|-------------------------------|
    | agent_run_duration_ms | Wall around local ``agent.run`` | ``time.perf_counter`` (local monotonic) | **No** |
    | agent_run_duration_source | Always ``local_monotonic`` when measured | fixed label | No |
    | agent_run_duration_boundary | ``agent.run`` | fixed label | No |
    | provider_request_duration_ms | Provider-attributed request time if any | dedicated adapter envelope only | **Yes** only when status=available |
    | provider_request_duration_status | ``available`` / ``unavailable`` | derived | — |
    | provider_request_duration_source | ``provider_adapter_envelope`` / ``none`` | derived | — |
    | provider_request_duration_field | Envelope duration key when available | string | — |

    Explicit non-claims:
    - Does **not** write or redefine ``ai_usage_events.latency_ms``.
    - Does **not** equal worker_tick ``duration_ms`` (PG wall of whole tick).
    - Does **not** equal pipeline-root or claim-wait duration.
    """

    agent_run_duration_ms: int | None
    agent_run_duration_source: str
    agent_run_duration_boundary: str
    provider_request_duration_ms: int | None
    provider_request_duration_status: str
    provider_request_duration_source: str
    provider_request_duration_field: str | None = None
    agent_run_id: UUID | None = None

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            META_DURATION_SCHEMA_KIND: DURATION_SCHEMA_KIND,
            META_DURATION_SCHEMA_VERSION: DURATION_SCHEMA_VERSION,
            META_AGENT_RUN_DURATION_MS: self.agent_run_duration_ms,
            META_AGENT_RUN_DURATION_SOURCE: self.agent_run_duration_source,
            META_AGENT_RUN_DURATION_BOUNDARY: self.agent_run_duration_boundary,
            META_PROVIDER_REQUEST_DURATION_MS: self.provider_request_duration_ms,
            META_PROVIDER_REQUEST_DURATION_STATUS: self.provider_request_duration_status,
            META_PROVIDER_REQUEST_DURATION_SOURCE: self.provider_request_duration_source,
            META_PROVIDER_REQUEST_DURATION_FIELD: self.provider_request_duration_field,
        }
        if self.agent_run_id is not None:
            payload[META_AGENT_RUN_ID] = str(self.agent_run_id)
        return payload

    def diagnostic_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        if self.agent_run_duration_ms is not None:
            codes.append(AGENT_RUN_DURATION_RECORDED)
        if self.provider_request_duration_status == PROVIDER_DURATION_STATUS_AVAILABLE:
            codes.append(PROVIDER_TIMING_AVAILABLE)
        else:
            codes.append(PROVIDER_TIMING_UNAVAILABLE)
        return tuple(codes)


_CURRENT_EXECUTION: ContextVar[ExecutionCorrelation | None] = ContextVar(
    "claread_reader_execution_correlation",
    default=None,
)

_LAST_USAGE_OUTCOME: ContextVar[UsageRecordOutcome | None] = ContextVar(
    "claread_reader_last_usage_outcome",
    default=None,
)

_LAST_DURATION_PROVENANCE: ContextVar[DurationProvenance | None] = ContextVar(
    "claread_reader_last_duration_provenance",
    default=None,
)


def current_execution() -> ExecutionCorrelation | None:
    return _CURRENT_EXECUTION.get()


def set_current_execution(correlation: ExecutionCorrelation | None) -> None:
    _CURRENT_EXECUTION.set(correlation)


def current_usage_outcome() -> UsageRecordOutcome | None:
    return _LAST_USAGE_OUTCOME.get()


def set_last_usage_outcome(outcome: UsageRecordOutcome | None) -> None:
    _LAST_USAGE_OUTCOME.set(outcome)


def current_duration_provenance() -> DurationProvenance | None:
    return _LAST_DURATION_PROVENANCE.get()


def set_last_duration_provenance(value: DurationProvenance | None) -> None:
    _LAST_DURATION_PROVENANCE.set(value)


@contextmanager
def execution_scope(correlation: ExecutionCorrelation) -> Iterator[ExecutionCorrelation]:
    """Bind correlation for the duration of one claimed-job execution."""
    token = _CURRENT_EXECUTION.set(correlation)
    outcome_token = _LAST_USAGE_OUTCOME.set(None)
    duration_token = _LAST_DURATION_PROVENANCE.set(None)
    try:
        yield correlation
    finally:
        _CURRENT_EXECUTION.reset(token)
        _LAST_USAGE_OUTCOME.reset(outcome_token)
        _LAST_DURATION_PROVENANCE.reset(duration_token)


@contextmanager
def bind_execution_from_claim(
    claim: Any,
    *,
    capability_code: str,
) -> Iterator[ExecutionCorrelation]:
    """Begin + bind execution correlation for a claimed job process path.

    Pulls ``span_id`` from the active worker_tick span when present so usage
    events and span rows share the same correlation fragment.
    """
    span_id: UUID | None = None
    try:
        from app.services.reader_orchestration.span_recorder import current_span

        span = current_span()
        if span is not None:
            span_id = span.span_id
    except Exception:  # pragma: no cover - import/context edge
        span_id = None
    correlation = begin_execution_from_claim(
        claim,
        capability_code=capability_code,
        span_id=span_id,
    )
    with execution_scope(correlation) as bound:
        yield bound


def with_execution_correlation(
    capability_code: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator for ``async def process_*(self, *, claim, ...)`` methods.

    Ensures every usage event / span end for the method body shares one
    ``execution_id`` and the claim's durable ``attempt_ordinal``.
    """

    def decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            claim = kwargs.get("claim")
            if claim is None and args:
                claim = args[0]
            if claim is None:
                raise TypeError(
                    f"{fn.__name__} requires claim= for execution correlation"
                )
            with bind_execution_from_claim(claim, capability_code=capability_code):
                return await fn(self, *args, **kwargs)

        return wrapper

    return decorator


def begin_execution_from_claim(
    claim: Any,
    *,
    capability_code: str,
    span_id: UUID | None = None,
) -> ExecutionCorrelation:
    """Mint a new ``execution_id`` for this claim; ``attempt_ordinal`` is claim.attempt_count.

    ``claim`` is duck-typed (``ClaimResult`` or any object with the required fields)
    so unit tests do not need to import the full job runtime graph.

    Fail-closed: ``attempt_count`` must be >= 1 (production claim always
    increments before process). Values of 0 or negative raise ``ValueError``;
    never clamp 0→1.
    """
    raw_attempt = int(claim.attempt_count)
    if raw_attempt < 1:
        raise ValueError(
            f"attempt_ordinal must be >= 1 (claim.attempt_count after claim); "
            f"got {raw_attempt}"
        )
    correlation = ExecutionCorrelation(
        reader_job_id=claim.job_id,
        reader_run_id=claim.run_id,
        attempt_ordinal=raw_attempt,
        execution_id=uuid4(),
        operation_fingerprint=getattr(claim, "operation_fingerprint", None),
        capability_code=capability_code,
        span_id=span_id,
        reading_record_id=getattr(claim, "reading_record_id", None),
    )
    return correlation


def mint_agent_run_id(
    correlation: ExecutionCorrelation | None = None,
) -> tuple[UUID, ExecutionCorrelation | None]:
    """Mint a new agent_run_id and optionally update the active correlation."""
    agent_run_id = uuid4()
    active = correlation if correlation is not None else current_execution()
    if active is None:
        return agent_run_id, None
    updated = active.with_agent_run_id(agent_run_id)
    if correlation is None:
        set_current_execution(updated)
    return agent_run_id, updated


def _coerce_duration_ms(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    # Guard absurd values (days of ms) that are not request timings.
    if number > 24 * 60 * 60 * 1000:
        return None
    return int(round(number))


def make_provider_response_timing_envelope(
    *,
    provider_request_duration_ms: int,
    source_adapter: str | None = None,
) -> dict[str, Any]:
    """Build the only envelope that can mark provider timing available.

    Intended for provider/SDK adapters. Workers and local timers must not
    forge this object from generic usage or agent-side clocks.
    """
    payload: dict[str, Any] = {
        "kind": PROVIDER_RESPONSE_TIMING_KIND,
        "version": PROVIDER_RESPONSE_TIMING_VERSION,
        PROVIDER_RESPONSE_TIMING_MS_FIELD: int(provider_request_duration_ms),
    }
    if source_adapter is not None:
        payload["source_adapter"] = source_adapter
    return payload


def extract_provider_request_timing(
    result: Any = None,
    usage_data: Mapping[str, Any] | None = None,
) -> tuple[int | None, str, str | None]:
    """Return (ms, status, field_name) for provider-attributed request timing.

    Fail-closed: only the dedicated ``_claread_provider_response_timing``
    adapter envelope on ``result`` can make status ``available``.

    Explicitly **ignored** (even if they contain ``request_duration_ms`` /
    ``llm_latency_ms``):
    - arbitrary ``usage_data`` / usage.details / aggregate
    - generic ``result.timing`` / ``result.response_timing`` / ``provider_timing``
    - local monotonic agent-run duration
    - worker span duration

    ``usage_data`` is accepted for API compatibility but never consulted.
    """
    del usage_data  # never a provenance source for provider timing
    if result is None:
        return (
            None,
            PROVIDER_DURATION_STATUS_UNAVAILABLE,
            None,
        )

    envelope = getattr(result, PROVIDER_RESPONSE_TIMING_ATTR, None)
    if not isinstance(envelope, Mapping):
        return (
            None,
            PROVIDER_DURATION_STATUS_UNAVAILABLE,
            None,
        )
    if envelope.get("kind") != PROVIDER_RESPONSE_TIMING_KIND:
        return (
            None,
            PROVIDER_DURATION_STATUS_UNAVAILABLE,
            None,
        )
    if str(envelope.get("version") or "") != PROVIDER_RESPONSE_TIMING_VERSION:
        return (
            None,
            PROVIDER_DURATION_STATUS_UNAVAILABLE,
            None,
        )
    ms = _coerce_duration_ms(envelope.get(PROVIDER_RESPONSE_TIMING_MS_FIELD))
    if ms is None:
        return (
            None,
            PROVIDER_DURATION_STATUS_UNAVAILABLE,
            None,
        )
    return (
        ms,
        PROVIDER_DURATION_STATUS_AVAILABLE,
        PROVIDER_RESPONSE_TIMING_MS_FIELD,
    )


def build_duration_provenance(
    *,
    agent_run_duration_ms: int | None,
    agent_run_id: UUID | None = None,
    result: Any = None,
    usage_data: Mapping[str, Any] | None = None,
) -> DurationProvenance:
    """Construct duration provenance for one agent.run boundary.

    ``usage_data`` is ignored for provider timing (fail-closed). Kept as a
    keyword for call-site compatibility only.
    """
    provider_ms, provider_status, provider_field = extract_provider_request_timing(
        result,
        usage_data=None,
    )
    return DurationProvenance(
        agent_run_duration_ms=agent_run_duration_ms,
        agent_run_duration_source=AGENT_RUN_DURATION_SOURCE_LOCAL_MONOTONIC,
        agent_run_duration_boundary=AGENT_RUN_DURATION_BOUNDARY_AGENT_RUN,
        provider_request_duration_ms=provider_ms,
        provider_request_duration_status=provider_status,
        provider_request_duration_source=(
            PROVIDER_DURATION_SOURCE_ADAPTER_ENVELOPE
            if provider_status == PROVIDER_DURATION_STATUS_AVAILABLE
            else PROVIDER_DURATION_SOURCE_NONE
        ),
        provider_request_duration_field=provider_field,
        agent_run_id=agent_run_id,
    )


def merge_duration_provenance_metadata(
    metadata: Mapping[str, Any] | None,
    provenance: DurationProvenance | None = None,
) -> dict[str, Any]:
    """Merge duration provenance keys into metadata (authoritative overwrite)."""
    merged: dict[str, Any] = dict(metadata or {})
    active = (
        provenance if provenance is not None else current_duration_provenance()
    )
    if active is None:
        return merged
    merged.update(active.to_metadata())
    return merged


def normalize_token_totals(usage_data: Mapping[str, Any] | None) -> dict[str, int]:
    """Normalize usage_data to integer token totals (mirrors ai_usage service rules)."""
    if not usage_data:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    aggregate: Mapping[str, Any]
    if isinstance(usage_data.get("aggregate"), Mapping):
        aggregate = usage_data["aggregate"]  # type: ignore[assignment]
    else:
        aggregate = usage_data

    input_tokens = int(aggregate.get("input_tokens") or 0)
    output_tokens = int(aggregate.get("output_tokens") or 0)
    total_tokens = int(aggregate.get("total_tokens") or 0) or (
        input_tokens + output_tokens
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": int(aggregate.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(aggregate.get("cache_write_tokens") or 0),
    }


def usage_key_list(usage_data: Mapping[str, Any] | None) -> tuple[str, ...]:
    if usage_data is None:
        return ()
    if not isinstance(usage_data, Mapping):
        return ()
    return tuple(sorted(str(k) for k in usage_data.keys()))


def classify_usage_presence(
    usage_data: Mapping[str, Any] | None,
    *,
    stage: str,
    capability_code: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> UsagePresenceSnapshot:
    """Classify usage_data at a pipeline stage.

    Distinguishes:
    - ``None`` → missing
    - ``{}`` (or mapping with no keys) → empty
    - populated mapping → present (even if all token values normalize to 0)
    """
    keys = usage_key_list(usage_data)
    totals = normalize_token_totals(usage_data if isinstance(usage_data, Mapping) else None)

    if usage_data is None:
        code = (
            USAGE_MISSING_AT_ADAPTER
            if stage == STAGE_ADAPTER
            else USAGE_MISSING_BEFORE_EVENT
        )
        return UsagePresenceSnapshot(
            stage=stage,
            diagnostic_code=code,
            usage_is_none=True,
            usage_is_empty_mapping=False,
            usage_key_list=keys,
            normalized_totals=totals,
            provider=provider,
            model=model,
            capability_code=capability_code,
        )

    if isinstance(usage_data, Mapping) and len(usage_data) == 0:
        code = (
            USAGE_EMPTY_AT_ADAPTER
            if stage == STAGE_ADAPTER
            else USAGE_MISSING_BEFORE_EVENT
        )
        return UsagePresenceSnapshot(
            stage=stage,
            diagnostic_code=code,
            usage_is_none=False,
            usage_is_empty_mapping=True,
            usage_key_list=keys,
            normalized_totals=totals,
            provider=provider,
            model=model,
            capability_code=capability_code,
        )

    if stage == STAGE_ADAPTER:
        code = USAGE_PRESENT_AT_ADAPTER
    elif stage == STAGE_EVENT_DTO:
        code = USAGE_PRESENT_BEFORE_EVENT
    elif stage == STAGE_NORMALIZE:
        code = (
            USAGE_ZERO_AFTER_NORMALIZATION
            if totals["total_tokens"] == 0
            and totals["input_tokens"] == 0
            and totals["output_tokens"] == 0
            else USAGE_PRESENT_AFTER_NORMALIZE
        )
    else:
        code = USAGE_PRESENT_BEFORE_EVENT

    return UsagePresenceSnapshot(
        stage=stage,
        diagnostic_code=code,
        usage_is_none=False,
        usage_is_empty_mapping=False,
        usage_key_list=keys,
        normalized_totals=totals,
        provider=provider,
        model=model,
        capability_code=capability_code,
    )


def _safe_correlation_fields(
    correlation: ExecutionCorrelation | None,
) -> dict[str, Any]:
    if correlation is None:
        return {}
    return {
        "execution_id": str(correlation.execution_id),
        "attempt_ordinal": correlation.attempt_ordinal,
        "reader_job_id": str(correlation.reader_job_id),
        "reader_run_id": str(correlation.reader_run_id),
        "agent_run_id": (
            str(correlation.agent_run_id) if correlation.agent_run_id else None
        ),
        "span_id": str(correlation.span_id) if correlation.span_id else None,
        "capability_code": correlation.capability_code,
        "operation_fingerprint": correlation.operation_fingerprint,
    }


def log_usage_diagnostic(
    *,
    diagnostic_code: str,
    stage: str,
    correlation: ExecutionCorrelation | None = None,
    usage_key_list: tuple[str, ...] | list[str] = (),
    normalized_totals: Mapping[str, int] | None = None,
    provider: str | None = None,
    model: str | None = None,
    capability_code: str | None = None,
    usage_event_id: UUID | None = None,
    status: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit structured diagnostic via LogRecord.extra (no payload stringification).

    Stable extra fields are attachable on the LogRecord for tests and log
    shippers. Never logs prompt, article text, model raw response, secrets,
    or session tokens.
    """
    if diagnostic_code not in USAGE_DIAGNOSTIC_CODES:
        # Still log, but mark as unknown so typos are visible in tests.
        diagnostic_code = f"unknown:{diagnostic_code}"

    # LogRecord.extra keys must not collide with reserved LogRecord attributes.
    record_extra: dict[str, Any] = {
        "diagnostic_code": diagnostic_code,
        "diagnostic_stage": stage,
        "correlation_schema_kind": CORRELATION_SCHEMA_KIND,
        "correlation_schema_version": CORRELATION_SCHEMA_VERSION,
        "usage_key_list": list(usage_key_list),
        "normalized_token_totals": dict(normalized_totals or {}),
        "provider": provider,
        "model": model,
        "capability_code": capability_code
        or (correlation.capability_code if correlation else None),
        "usage_event_id": str(usage_event_id) if usage_event_id else None,
        "diagnostic_status": status,
        **_safe_correlation_fields(correlation or current_execution()),
    }
    if extra:
        # Only allow a small non-sensitive extra surface.
        for key in (
            "event_totals",
            "span_totals",
            "mismatch",
            "persist_failed",
            "usage_is_none",
            "usage_is_empty_mapping",
        ):
            if key in extra:
                record_extra[key] = extra[key]

    logger.info(
        "reader_usage_diagnostic code=%s stage=%s",
        diagnostic_code,
        stage,
        extra=record_extra,
    )


def merge_correlation_metadata(
    metadata: Mapping[str, Any] | None,
    correlation: ExecutionCorrelation | None = None,
    *,
    diagnostic_codes: list[str] | tuple[str, ...] | None = None,
    presence: UsagePresenceSnapshot | None = None,
    duration: DurationProvenance | None = None,
) -> dict[str, Any]:
    """Merge correlation + diagnostics + duration into usage/span metadata_json.

    Correlation authority always wins over caller-forged
    ``execution_id`` / ``attempt_ordinal`` / schema / agent_run / span keys.
    Duration provenance is merged when present and never renames itself to
    ``latency_ms``.
    """
    merged: dict[str, Any] = dict(metadata or {})
    active = correlation if correlation is not None else current_execution()
    if active is not None:
        # Force authoritative correlation fragment over any caller forgery.
        merged.update(active.to_metadata())
    duration_codes: tuple[str, ...] = ()
    active_duration = (
        duration if duration is not None else current_duration_provenance()
    )
    if active_duration is not None:
        merged = merge_duration_provenance_metadata(merged, active_duration)
        duration_codes = active_duration.diagnostic_codes()
    if diagnostic_codes or duration_codes:
        existing = merged.get(META_DIAGNOSTIC_CODES)
        codes: list[str] = []
        if isinstance(existing, list):
            codes.extend(str(c) for c in existing)
        if diagnostic_codes:
            codes.extend(str(c) for c in diagnostic_codes)
        codes.extend(duration_codes)
        # de-dupe preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for code in codes:
            if code not in seen:
                seen.add(code)
                ordered.append(code)
        merged[META_DIAGNOSTIC_CODES] = ordered
    if presence is not None:
        merged[META_USAGE_PRESENCE] = presence.diagnostic_code
        merged[META_USAGE_KEY_LIST] = list(presence.usage_key_list)
        merged[META_NORMALIZED_TOTALS] = dict(presence.normalized_totals)
    return merged


def span_totals_from_usage_data(
    usage_data: Mapping[str, Any] | None,
) -> dict[str, int | None]:
    """Mirror span_recorder top-level .get() semantics for comparison."""
    if usage_data is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    usage = dict(usage_data)
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
    }


def detect_event_span_token_mismatch(
    *,
    event_totals: Mapping[str, int],
    span_totals: Mapping[str, Any],
) -> bool:
    """Return True when event normalized totals disagree with span-written tokens.

    Span fields may be None (missing); treat None as 0 for comparison only when
    the event side recorded zeros and span has non-zero numbers, or vice versa
    for total_tokens / input / output.
    """

    def _as_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if _as_int(event_totals.get(key)) != _as_int(span_totals.get(key)):
            return True
    return False


def log_event_span_mismatch(
    *,
    correlation: ExecutionCorrelation | None,
    event_totals: Mapping[str, int],
    span_totals: Mapping[str, Any],
    usage_event_id: UUID | None,
    capability_code: str | None = None,
) -> None:
    log_usage_diagnostic(
        diagnostic_code=USAGE_SPAN_EVENT_MISMATCH,
        stage=STAGE_SPAN_WRITE,
        correlation=correlation,
        normalized_totals=dict(event_totals),
        capability_code=capability_code,
        usage_event_id=usage_event_id,
        status="mismatch",
        extra={
            "event_totals": dict(event_totals),
            "span_totals": {
                k: (int(v) if isinstance(v, int | float) else v)
                for k, v in span_totals.items()
            },
            "mismatch": True,
        },
    )


__all__ = [
    "CORRELATION_SCHEMA_KIND",
    "CORRELATION_SCHEMA_VERSION",
    "DURATION_SCHEMA_KIND",
    "DURATION_SCHEMA_VERSION",
    "DurationProvenance",
    "ExecutionCorrelation",
    "UsagePresenceSnapshot",
    "UsageRecordOutcome",
    "USAGE_MISSING_AT_ADAPTER",
    "USAGE_EMPTY_AT_ADAPTER",
    "USAGE_PRESENT_AT_ADAPTER",
    "USAGE_MISSING_BEFORE_EVENT",
    "USAGE_ZERO_AFTER_NORMALIZATION",
    "USAGE_EVENT_PERSISTED_ZERO",
    "USAGE_SPAN_EVENT_MISMATCH",
    "USAGE_EVENT_PERSIST_FAILED",
    "PROVIDER_TIMING_AVAILABLE",
    "PROVIDER_TIMING_UNAVAILABLE",
    "AGENT_RUN_DURATION_RECORDED",
    "PROVIDER_DURATION_STATUS_AVAILABLE",
    "PROVIDER_DURATION_STATUS_UNAVAILABLE",
    "PROVIDER_DURATION_SOURCE_ADAPTER_ENVELOPE",
    "PROVIDER_DURATION_SOURCE_NONE",
    "PROVIDER_RESPONSE_TIMING_ATTR",
    "PROVIDER_RESPONSE_TIMING_KIND",
    "PROVIDER_RESPONSE_TIMING_VERSION",
    "STAGE_ADAPTER",
    "STAGE_EXECUTION_RESULT",
    "STAGE_EVENT_DTO",
    "STAGE_NORMALIZE",
    "STAGE_EVENT_PERSIST",
    "STAGE_SPAN_WRITE",
    "begin_execution_from_claim",
    "bind_execution_from_claim",
    "build_duration_provenance",
    "classify_usage_presence",
    "current_duration_provenance",
    "current_execution",
    "current_usage_outcome",
    "detect_event_span_token_mismatch",
    "execution_scope",
    "extract_provider_request_timing",
    "log_event_span_mismatch",
    "log_usage_diagnostic",
    "make_provider_response_timing_envelope",
    "merge_correlation_metadata",
    "merge_duration_provenance_metadata",
    "mint_agent_run_id",
    "normalize_token_totals",
    "set_current_execution",
    "set_last_duration_provenance",
    "set_last_usage_outcome",
    "span_totals_from_usage_data",
    "usage_key_list",
]
