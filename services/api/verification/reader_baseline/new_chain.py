"""New orchestration chain metrics extraction.

This module turns a ``ReaderEnhancementSmokeHarness`` result (or the
plain ``ReaderPipelineRunSummary`` and ``ReaderPlateSnapshot``) into a
flat ``dict`` of metrics suitable for baseline reporting and
cross-run comparison.

The metrics are computed from the *already-published* chain state.
The module does not call the LLM, does not modify any database row,
and does not re-run any worker. It is observation-only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import UUID

import asyncpg

from app.services.reader_orchestration.pipeline_runner import (
    EnhancementOutcomeCounts,
    EnhancementWorkerTickCounts,
    ReaderPipelineRunSummary,
)
from app.services.reader_orchestration.smoke_harness import (
    ReaderSmokeHarnessResult,
    SmokePublishedLayerCounts,
)

from app.schemas.reader_orchestration import (
    GrammarNoteLayerOutput,
    ReaderPlateSnapshot,
    ReaderSnapshotLayer,
    SentenceAnalysisLayerOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)

logger = logging.getLogger(__name__)

# Capability codes written by the new chain to ai_usage_events. Kept
# here as a constant so the report can group metrics by capability.
NEW_CHAIN_CAPABILITY_CODES: tuple[str, ...] = (
    "reader_translation",
    "reader_vocabulary",
    "reader_grammar_bundle",
    "reader_title_generation",
)

# Job statuses that still need a worker to act on them. Anything
# outside this set is terminal.
NON_TERMINAL_JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "claimed",
    "retry_later",
    "paused",
)

# Stopped reasons emitted by ``ReaderEnhancementPipelineRunner.run``
# that mean the pipeline ran out of budget. Anything outside this
# set (i.e. ``all_workers_no_job``) means the pipeline genuinely
# drained. The taxonomy is owned by
# ``services/api/app/services/reader_orchestration/pipeline_runner.py``.
NON_DRAIN_STOPPED_REASONS: frozenset[str] = frozenset(
    {"max_ticks_reached", "max_jobs_reached", "attention_required"}
)

CompletionStatus = Literal["complete", "incomplete"]


@dataclass(frozen=True, slots=True)
class LayerItemCounts:
    translation_groups: int = 0
    vocabulary_items: int = 0
    grammar_note_items: int = 0
    sentence_analysis_items: int = 0


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    """Aggregated token / latency / event counts.

    All counters are zero when no usage is observed; that is a
    meaningful signal, not a missing field.
    """

    event_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    failed_event_count: int = 0
    by_capability: dict[str, "UsageMetrics"] = field(default_factory=dict)
    source: Literal["ai_usage_events", "usage_summary", "skipped"] = "skipped"

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "failed_event_count": self.failed_event_count,
            "source": self.source,
            "by_capability": {
                cap: metrics.to_jsonable()
                for cap, metrics in sorted(self.by_capability.items())
            },
        }


async def _fetch_record_reading_metadata(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> dict[str, str]:
    """Read the persisted reading_goal / reading_variant for a record.

    Returns an empty dict when ``pool`` is ``None`` or the row is
    not found. The chain stores the metadata on
    ``reading_records``, so the report can show what the chain
    actually used.
    """
    if pool is None:
        return {}
    query = """
        SELECT reading_goal, reading_variant
        FROM reading_records
        WHERE id = $1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, reading_record_id)
    if row is None:
        return {}
    return {
        "reading_goal": str(row["reading_goal"]),
        "reading_variant": str(row["reading_variant"]),
    }


def _fetch_record_reading_metadata_sync(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> dict[str, str]:
    if pool is None:
        return {}
    import asyncio as _asyncio
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(
            _fetch_record_reading_metadata(pool, reading_record_id=reading_record_id)
        )
    raise RuntimeError(
        "summarise() cannot drive asyncpg from a running event loop; "
        "call _fetch_record_reading_metadata directly instead"
    )


@dataclass(frozen=True, slots=True)
class NewChainMetrics:
    executor_mode: str
    executor_note: str | None
    record_id: str
    base_id: str
    last_event_sequence: int
    total_ticks: int
    total_jobs: int
    worker_tick_counts: dict[str, int]
    outcome_counts: dict[str, int]
    bootstrap_job_counts: dict[str, int]
    stopped_reason: str
    stopped_worker_type: str | None
    stopped_outcome: str | None
    attention_code: str | None
    snapshot_reload_recommended: bool
    layer_counts: dict[str, int]
    layer_item_counts: dict[str, int]
    no_op_windows: int
    failed_windows: int
    attempts: tuple[dict[str, Any], ...]
    attempt_attention_codes: tuple[str, ...]
    completion_status: CompletionStatus
    outstanding_jobs: dict[str, int]
    completion_reasons: tuple[str, ...]
    usage: UsageMetrics
    record_reading_goal: str | None = None
    record_reading_variant: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "executor_mode": self.executor_mode,
            "executor_note": self.executor_note,
            "record_id": self.record_id,
            "base_id": self.base_id,
            "last_event_sequence": self.last_event_sequence,
            "total_ticks": self.total_ticks,
            "total_jobs": self.total_jobs,
            "worker_tick_counts": dict(self.worker_tick_counts),
            "outcome_counts": dict(self.outcome_counts),
            "bootstrap_job_counts": dict(self.bootstrap_job_counts),
            "stopped_reason": self.stopped_reason,
            "stopped_worker_type": self.stopped_worker_type,
            "stopped_outcome": self.stopped_outcome,
            "attention_code": self.attention_code,
            "snapshot_reload_recommended": self.snapshot_reload_recommended,
            "layer_counts": dict(self.layer_counts),
            "layer_item_counts": dict(self.layer_item_counts),
            "no_op_windows": self.no_op_windows,
            "failed_windows": self.failed_windows,
            "attempts": list(self.attempts),
            "attempt_attention_codes": list(self.attempt_attention_codes),
            "completion_status": self.completion_status,
            "outstanding_jobs": dict(self.outstanding_jobs),
            "completion_reasons": list(self.completion_reasons),
            "usage": self.usage.to_jsonable(),
            "record_reading_goal": self.record_reading_goal,
            "record_reading_variant": self.record_reading_variant,
        }


def _count_layer_items(layer: ReaderSnapshotLayer) -> int:
    """Count items inside a single published layer's output.

    Different ``layer_type`` values store items under different
    fields. This helper knows the four canonical types and falls
    back to ``0`` for anything else.
    """
    output = layer.output
    if output is None:
        return 0
    try:
        if layer.layer_type == "translation":
            # Acceptance: the published layer stores
            # ``TranslationLayerOutput`` (with ``group_id`` /
            # ``source_text_hash``), not the LLM generation schema
            # ``TranslationLayerGenerationOutput`` (``extra="forbid"``).
            # Parsing with the generation schema silently fails and
            # returns 0 groups, masking real coverage.
            parsed = TranslationLayerOutput.model_validate(output)
            return sum(len(group.anchor_segment_ids) for group in parsed.groups)
        if layer.layer_type == "vocabulary":
            parsed = VocabularyLayerOutput.model_validate(output)
            return len(parsed.items)
        if layer.layer_type == "grammar_note":
            parsed = GrammarNoteLayerOutput.model_validate(output)
            return len(parsed.items)
        if layer.layer_type == "sentence_analysis":
            parsed = SentenceAnalysisLayerOutput.model_validate(output)
            return len(parsed.items)
    except Exception:
        # Defensive: a layer that does not parse still contributes
        # to layer_counts but not to item counts. The caller will
        # surface parse failures via other channels.
        return 0
    return 0


def _summarise_layer_items(layers: list[ReaderSnapshotLayer]) -> LayerItemCounts:
    by_type: dict[str, int] = {
        "translation_groups": 0,
        "vocabulary_items": 0,
        "grammar_note_items": 0,
        "sentence_analysis_items": 0,
    }
    for layer in layers:
        if layer.layer_type == "translation":
            by_type["translation_groups"] += _count_layer_items(layer)
        elif layer.layer_type == "vocabulary":
            by_type["vocabulary_items"] += _count_layer_items(layer)
        elif layer.layer_type == "grammar_note":
            by_type["grammar_note_items"] += _count_layer_items(layer)
        elif layer.layer_type == "sentence_analysis":
            by_type["sentence_analysis_items"] += _count_layer_items(layer)
    return LayerItemCounts(
        translation_groups=by_type["translation_groups"],
        vocabulary_items=by_type["vocabulary_items"],
        grammar_note_items=by_type["grammar_note_items"],
        sentence_analysis_items=by_type["sentence_analysis_items"],
    )


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "__dataclass_fields__"):
        return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    return dict(obj)


def _attempt_to_dict(attempt: Any) -> dict[str, Any]:
    return {
        "worker_type": str(getattr(attempt, "worker_type", "")),
        "outcome": str(getattr(attempt, "outcome", "")),
        "processed_job": bool(getattr(attempt, "processed_job", False)),
        "job_id": str(getattr(attempt, "job_id", "")) if getattr(attempt, "job_id", None) else None,
        "run_id": str(getattr(attempt, "run_id", "")) if getattr(attempt, "run_id", None) else None,
        "attention_code": getattr(attempt, "attention_code", None),
        "superseded_jobs": int(getattr(attempt, "superseded_jobs", 0)),
    }


def _no_op_and_failed_from_attempts(
    summary: ReaderPipelineRunSummary,
) -> tuple[int, int]:
    """Derive window-level no-op and failed counts.

    ``outcome_counts`` already exposes ``failed_terminal`` at the
    job level. ``no_op`` is signalled by per-attempt ``attention_code``
    values (e.g. ``publish_fence_skipped``) or by the pipeline
    ``attention_code`` summary. We surface both to keep the report
    self-describing.
    """
    no_op = 0
    failed = 0
    for attempt in summary.attempts:
        if str(attempt.outcome) == "PipelineAttemptOutcome.failed_terminal":
            failed += 1
        if attempt.attention_code and "no_op" in str(attempt.attention_code).lower():
            no_op += 1
    return no_op, failed


def _empty_usage(source: UsageMetrics.__dataclass_fields__["source"].type) -> UsageMetrics:  # type: ignore[attr-defined]
    return UsageMetrics(source=source)


async def _fetch_outstanding_job_counts(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> dict[str, int]:
    """Count ``reader_jobs`` rows that are still in a non-terminal state.

    Returns an empty dict if ``pool`` is ``None``; callers should
    fall back to ``stopped_reason``-only heuristics in that case.
    """
    if pool is None:
        return {}
    query = """
        SELECT status, COUNT(*)::int AS n
        FROM reader_jobs
        WHERE reading_record_id = $1
          AND status = ANY($2::text[])
        GROUP BY status
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, reading_record_id, list(NON_TERMINAL_JOB_STATUSES))
    return {str(row["status"]): int(row["n"]) for row in rows}


async def _fetch_ai_usage_aggregates(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> UsageMetrics:
    """Aggregate ``ai_usage_events`` for one reading record.

    Returns an empty ``UsageMetrics`` with ``source="skipped"`` when
    ``pool`` is ``None``.
    """
    if pool is None:
        return _empty_usage("skipped")
    query = """
        SELECT
            capability_code,
            COUNT(*)::int AS event_count,
            COALESCE(SUM(input_tokens), 0)::int AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::int AS output_tokens,
            COALESCE(SUM(total_tokens), 0)::int AS total_tokens,
            COALESCE(SUM(latency_ms), 0)::int AS latency_ms,
            COUNT(*) FILTER (WHERE status <> 'succeeded')::int AS failed_event_count
        FROM ai_usage_events
        WHERE reading_record_id = $1
          AND capability_code = ANY($2::text[])
        GROUP BY capability_code
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            query, reading_record_id, list(NEW_CHAIN_CAPABILITY_CODES)
        )
    by_capability: dict[str, UsageMetrics] = {}
    total = UsageMetrics(source="ai_usage_events")
    for row in rows:
        cap = str(row["capability_code"])
        per = UsageMetrics(
            event_count=int(row["event_count"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            total_tokens=int(row["total_tokens"]),
            latency_ms=int(row["latency_ms"]),
            failed_event_count=int(row["failed_event_count"]),
            source="ai_usage_events",
        )
        by_capability[cap] = per
        total = UsageMetrics(
            event_count=total.event_count + per.event_count,
            input_tokens=total.input_tokens + per.input_tokens,
            output_tokens=total.output_tokens + per.output_tokens,
            total_tokens=total.total_tokens + per.total_tokens,
            latency_ms=total.latency_ms + per.latency_ms,
            failed_event_count=total.failed_event_count + per.failed_event_count,
            by_capability=dict(total.by_capability),
            source="ai_usage_events",
        )
    total = UsageMetrics(
        event_count=total.event_count,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        total_tokens=total.total_tokens,
        latency_ms=total.latency_ms,
        failed_event_count=total.failed_event_count,
        by_capability=by_capability,
        source="ai_usage_events",
    )
    return total


def _classify_completion(
    *,
    stopped_reason: str,
    outcome_counts: dict[str, int],
    outstanding_jobs: dict[str, int],
) -> tuple[CompletionStatus, tuple[str, ...]]:
    """Decide whether a run is ``complete`` or ``incomplete``.

    ``complete`` requires all three:

    - The pipeline stopped with ``all_workers_no_job`` (drained,
      not budget-exhausted).
    - No reader_jobs are still in a non-terminal state. When
      ``outstanding_jobs`` is empty we treat it as "unknown" and
      fall back to the first two checks.
    - No ``failed_terminal`` jobs were produced (a worker can
      succeed on the drain tick but still leave behind
      ``failed_terminal`` from earlier ticks).
    """
    reasons: list[str] = []
    if stopped_reason not in {"all_workers_no_job"}:
        reasons.append(
            f"stopped_reason={stopped_reason!r} is not 'all_workers_no_job'"
        )
    if outstanding_jobs:
        reasons.append(
            f"outstanding jobs still open: {sorted(outstanding_jobs.items())}"
        )
    if outcome_counts.get("failed_terminal", 0) > 0:
        reasons.append(
            f"failed_terminal jobs: {outcome_counts.get('failed_terminal')}"
        )
    if reasons:
        return "incomplete", tuple(reasons)
    return "complete", ()


def summarise(
    *,
    result: ReaderSmokeHarnessResult,
    pool: asyncpg.Pool | None = None,
) -> NewChainMetrics:
    """Build a flat metrics record from a smoke harness result.

    Pass ``pool`` to enable ``ai_usage_events`` aggregation and the
    ``reader_jobs`` outstanding-job check. Without it the
    ``UsageMetrics.source`` is ``"skipped"`` and completion
    classification falls back to ``stopped_reason`` + ``outcome_counts``
    alone.
    """
    summary: ReaderPipelineRunSummary = result.pipeline_summary
    layer_counts_obj: SmokePublishedLayerCounts = result.layer_counts
    item_counts = _summarise_layer_items(result.snapshot.enhancement_layers)
    no_op, failed = _no_op_and_failed_from_attempts(summary)
    outstanding_jobs = _fetch_outstanding_job_counts_sync(
        pool, reading_record_id=result.record_id
    )
    usage = _fetch_ai_usage_aggregates_sync(
        pool, reading_record_id=result.record_id
    )
    record_meta = _fetch_record_reading_metadata_sync(
        pool, reading_record_id=result.record_id
    )
    completion_status, completion_reasons = _classify_completion(
        stopped_reason=str(summary.stopped_reason),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        outstanding_jobs=outstanding_jobs,
    )
    return NewChainMetrics(
        executor_mode=str(result.executor_mode),
        executor_note=result.executor_note,
        record_id=str(result.record_id),
        base_id=str(result.base_id),
        last_event_sequence=int(summary.last_event_sequence),
        total_ticks=int(summary.total_ticks),
        total_jobs=int(summary.total_jobs),
        worker_tick_counts=_dataclass_to_dict(summary.worker_tick_counts),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        bootstrap_job_counts=_dataclass_to_dict(summary.bootstrapped_job_counts),
        stopped_reason=str(summary.stopped_reason),
        stopped_worker_type=(
            str(summary.stopped_worker_type) if summary.stopped_worker_type else None
        ),
        stopped_outcome=(
            str(summary.stopped_outcome) if summary.stopped_outcome else None
        ),
        attention_code=summary.attention_code,
        snapshot_reload_recommended=bool(summary.snapshot_reload_recommended),
        layer_counts=_dataclass_to_dict(layer_counts_obj),
        layer_item_counts=_dataclass_to_dict(item_counts),
        no_op_windows=no_op,
        failed_windows=failed,
        attempts=tuple(_attempt_to_dict(a) for a in summary.attempts),
        attempt_attention_codes=tuple(
            a.attention_code for a in summary.attempts if a.attention_code
        ),
        completion_status=completion_status,
        outstanding_jobs=outstanding_jobs,
        completion_reasons=completion_reasons,
        usage=usage,
        record_reading_goal=record_meta.get("reading_goal"),
        record_reading_variant=record_meta.get("reading_variant"),
    )


def _fetch_outstanding_job_counts_sync(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> dict[str, int]:
    """Sync wrapper around :func:`_fetch_outstanding_job_counts`.

    The reader-orchestration smoke harness returns a fully-realised
    result by the time ``summarise`` is called, so the SQL queries
    can be driven from sync code. We use ``asyncio.run`` only when
    ``pool`` is not ``None`` and there is no running event loop.
    """
    if pool is None:
        return {}
    import asyncio as _asyncio
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(
            _fetch_outstanding_job_counts(pool, reading_record_id=reading_record_id)
        )
    raise RuntimeError(
        "summarise() cannot drive asyncpg from a running event loop; "
        "call _fetch_outstanding_job_counts directly instead"
    )


def _fetch_ai_usage_aggregates_sync(
    pool: asyncpg.Pool | None,
    *,
    reading_record_id: UUID,
) -> UsageMetrics:
    """Sync wrapper around :func:`_fetch_ai_usage_aggregates`."""
    if pool is None:
        return _empty_usage("skipped")
    import asyncio as _asyncio
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(
            _fetch_ai_usage_aggregates(pool, reading_record_id=reading_record_id)
        )
    raise RuntimeError(
        "summarise() cannot drive asyncpg from a running event loop; "
        "call _fetch_ai_usage_aggregates directly instead"
    )


async def summarise_async(
    *,
    result: ReaderSmokeHarnessResult,
    pool: asyncpg.Pool | None = None,
) -> NewChainMetrics:
    """Async variant of :func:`summarise`.

    The CLI runs ``summarise`` from sync code so it can use the
    sync wrappers. Tests that already have an event loop can use
    this entry instead.
    """
    summary: ReaderPipelineRunSummary = result.pipeline_summary
    layer_counts_obj: SmokePublishedLayerCounts = result.layer_counts
    item_counts = _summarise_layer_items(result.snapshot.enhancement_layers)
    no_op, failed = _no_op_and_failed_from_attempts(summary)
    outstanding_jobs = await _fetch_outstanding_job_counts(
        pool, reading_record_id=result.record_id
    )
    usage = await _fetch_ai_usage_aggregates(pool, reading_record_id=result.record_id)
    record_meta = await _fetch_record_reading_metadata(
        pool, reading_record_id=result.record_id
    )
    completion_status, completion_reasons = _classify_completion(
        stopped_reason=str(summary.stopped_reason),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        outstanding_jobs=outstanding_jobs,
    )
    return NewChainMetrics(
        executor_mode=str(result.executor_mode),
        executor_note=result.executor_note,
        record_id=str(result.record_id),
        base_id=str(result.base_id),
        last_event_sequence=int(summary.last_event_sequence),
        total_ticks=int(summary.total_ticks),
        total_jobs=int(summary.total_jobs),
        worker_tick_counts=_dataclass_to_dict(summary.worker_tick_counts),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        bootstrap_job_counts=_dataclass_to_dict(summary.bootstrapped_job_counts),
        stopped_reason=str(summary.stopped_reason),
        stopped_worker_type=(
            str(summary.stopped_worker_type) if summary.stopped_worker_type else None
        ),
        stopped_outcome=(
            str(summary.stopped_outcome) if summary.stopped_outcome else None
        ),
        attention_code=summary.attention_code,
        snapshot_reload_recommended=bool(summary.snapshot_reload_recommended),
        layer_counts=_dataclass_to_dict(layer_counts_obj),
        layer_item_counts=_dataclass_to_dict(item_counts),
        no_op_windows=no_op,
        failed_windows=failed,
        attempts=tuple(_attempt_to_dict(a) for a in summary.attempts),
        attempt_attention_codes=tuple(
            a.attention_code for a in summary.attempts if a.attention_code
        ),
        completion_status=completion_status,
        outstanding_jobs=outstanding_jobs,
        completion_reasons=completion_reasons,
        usage=usage,
        record_reading_goal=record_meta.get("reading_goal"),
        record_reading_variant=record_meta.get("reading_variant"),
    )


def summarise_pipeline_summary(
    *,
    summary: ReaderPipelineRunSummary,
    record_id: UUID,
    base_id: UUID,
    snapshot: ReaderPlateSnapshot,
    executor_mode: str,
    executor_note: str | None,
    pool: asyncpg.Pool | None = None,
) -> NewChainMetrics:
    """Build a metrics record from a ``ReaderPipelineRunSummary`` directly.

    Useful for tests that have a synthetic pipeline summary and do
    not need the full smoke harness. Without ``pool`` the
    ``UsageMetrics.source`` is ``"skipped"`` and the completion
    classification ignores outstanding jobs.
    """
    layer_counts = SmokePublishedLayerCounts(
        translation=sum(
            1 for layer in snapshot.enhancement_layers if layer.layer_type == "translation"
        ),
        vocabulary=sum(
            1 for layer in snapshot.enhancement_layers if layer.layer_type == "vocabulary"
        ),
        grammar_note=sum(
            1 for layer in snapshot.enhancement_layers if layer.layer_type == "grammar_note"
        ),
        sentence_analysis=sum(
            1
            for layer in snapshot.enhancement_layers
            if layer.layer_type == "sentence_analysis"
        ),
    )
    item_counts = _summarise_layer_items(snapshot.enhancement_layers)
    no_op, failed = _no_op_and_failed_from_attempts(summary)
    outstanding_jobs = _fetch_outstanding_job_counts_sync(pool, reading_record_id=record_id)
    usage = _fetch_ai_usage_aggregates_sync(pool, reading_record_id=record_id)
    record_meta = _fetch_record_reading_metadata_sync(pool, reading_record_id=record_id)
    completion_status, completion_reasons = _classify_completion(
        stopped_reason=str(summary.stopped_reason),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        outstanding_jobs=outstanding_jobs,
    )
    return NewChainMetrics(
        executor_mode=executor_mode,
        executor_note=executor_note,
        record_id=str(record_id),
        base_id=str(base_id),
        last_event_sequence=int(summary.last_event_sequence),
        total_ticks=int(summary.total_ticks),
        total_jobs=int(summary.total_jobs),
        worker_tick_counts=_dataclass_to_dict(summary.worker_tick_counts),
        outcome_counts=_dataclass_to_dict(summary.outcome_counts),
        bootstrap_job_counts=_dataclass_to_dict(summary.bootstrapped_job_counts),
        stopped_reason=str(summary.stopped_reason),
        stopped_worker_type=(
            str(summary.stopped_worker_type) if summary.stopped_worker_type else None
        ),
        stopped_outcome=str(summary.stopped_outcome) if summary.stopped_outcome else None,
        attention_code=summary.attention_code,
        snapshot_reload_recommended=bool(summary.snapshot_reload_recommended),
        layer_counts=_dataclass_to_dict(layer_counts),
        layer_item_counts=_dataclass_to_dict(item_counts),
        no_op_windows=no_op,
        failed_windows=failed,
        attempts=tuple(_attempt_to_dict(a) for a in summary.attempts),
        attempt_attention_codes=tuple(
            a.attention_code for a in summary.attempts if a.attention_code
        ),
        completion_status=completion_status,
        outstanding_jobs=outstanding_jobs,
        completion_reasons=completion_reasons,
        usage=usage,
        record_reading_goal=record_meta.get("reading_goal"),
        record_reading_variant=record_meta.get("reading_variant"),
    )


__all__ = [
    "LayerItemCounts",
    "NewChainMetrics",
    "UsageMetrics",
    "CompletionStatus",
    "NEW_CHAIN_CAPABILITY_CODES",
    "NON_TERMINAL_JOB_STATUSES",
    "NON_DRAIN_STOPPED_REASONS",
    "summarise",
    "summarise_async",
    "summarise_pipeline_summary",
]
