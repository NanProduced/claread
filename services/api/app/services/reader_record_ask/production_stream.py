"""Production SSE + persistence adapter for the agentic Reading Record Ask path.

Flag-gated from ``service.py``.  Does not import legacy reader_ask agent,
planner, ask_runtime stream, or old RAG prompt bridges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from app.config.settings import get_settings
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V2,
    ProgressActivity,
    ProgressPhase,
    ProgressStatus,
    ProgressToolName,
    ReaderRecordAskCompletedDTO,
    ReaderRecordAskEvidenceItem,
    ReaderRecordAskEvidenceScope,
    ReaderRecordAskProgressDTO,
    ReaderRecordAskRunStartedDTO,
    ReaderRecordAskTerminalDTO,
    evidence_item_from_observation,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import DocumentAccess
from app.services.reader_record_ask.envelope_builder import (
    build_envelope_from_facts,
    document_access_from_facts,
)
from app.services.reader_record_ask.evidence_expansion import ExpansionPointerLedger
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.pointer_ledger_owner import (
    get_process_pointer_ledger,
)
from app.services.reader_record_ask.production_wiring import (
    build_production_article_rag_port,
    load_active_stable_document_id,
    resolve_agentic_model,
)

# M3 C2 wiring: map-source material provider for B3 heading enrichment (§4.2).
# Imported lazily inside stream_agentic_thread_message to avoid module-load
# cycles that surface under uvicorn --reload (reader_record_ask.__init__ →
# runtime → turn_coordinator → article_map_model_view ← map_source_material_provider).
from app.services.reader_record_ask.reasoning_projection import (
    ReasoningProjectorObserver,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.runtime_events import (
    AgenticReasoningDeltaEvent,
    AgenticReasoningStartedEvent,
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    AnswerDeltaEvent,
    ComposingAnswerEvent,
    FinalAnswerEvent,
    RunFinishedEvent,
    RunStartedEvent,
    RuntimeEvent,
    ToolCallEvent,
    ToolResultEvent,
    ValidatingEvidenceEvent,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_PROGRESS,
    EVENT_AGENTIC_REASONING_COMPLETED,
    EVENT_AGENTIC_REASONING_DELTA,
    EVENT_AGENTIC_REASONING_STARTED,
    EVENT_AGENTIC_RUN_STARTED,
    EVENT_AGENTIC_TERMINAL,
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_DELTA,
    EVENT_MESSAGE_INTERRUPTED,
    EVENT_MESSAGE_STARTED,
    EVENT_THREAD_READY,
    encode_sse,
)
from app.services.reader_record_ask.tool_contracts import (
    TOOL_EXPAND_EVIDENCE,
    TOOL_READ_RANGE,
    TOOL_SEARCH_CURRENT_ARTICLE,
)
from app.services.reader_record_ask.turn_coordinator import HostBudgetExhausted

RunFn = Callable[..., Any]

logger = logging.getLogger(__name__)

# Stable external terminal reasons. Must not leak pydantic-ai / provider
# internals (exception text, schema bodies, raw responses, thinking).
TERMINAL_REASON_AGENT_OUTPUT_INVALID = "agent_output_invalid"
TERMINAL_REASON_AGENT_RUN_FAILED = "agent_run_failed"
# Host-only model-view budget terminal (R4-A5-7). Typed reason only —
# never embed budget denial text, account dumps, or body.
TERMINAL_REASON_BUDGET_EXHAUSTED = "budget_exhausted"

# Baseline / document unavailable terminal reasons. Mapped from internal
# FinalizeStatus="unavailable" produced when baseline assembly fails or
# when the agent emits response_kind="unavailable" (defense in depth).
# Wire FinalStatus is a 5-value Literal that does NOT include "unavailable";
# production_stream always maps internal "unavailable" to wire "failed" +
# one of these typed terminal_reasons.
TERMINAL_REASON_DOCUMENT_UNAVAILABLE = "document_unavailable"
TERMINAL_REASON_BASELINE_UNAVAILABLE = "baseline_unavailable"

# DB persistence failure on the success path. Typed reason only — never
# embed the underlying DB error text, constraint name, or SQL fragment.
TERMINAL_REASON_PERSIST_FAILED = "persist_failed"

# Sentinel placed on the progress queue when the agent task finishes
# (success or failure). Not a RuntimeEvent.
_AGENT_DONE = object()

# Public tools that may project named progress. expand_evidence is
# deliberately **not** public — it maps to generic agent_running only.
_PUBLIC_TOOL_NAMES: frozenset[str] = frozenset(
    {TOOL_READ_RANGE, TOOL_SEARCH_CURRENT_ARTICLE}
)

_TOOL_RESULT_ACTIVITY: dict[str, ProgressActivity] = {
    "ok": "completed",
    "ready": "completed",
    "loaded": "completed",
    "unavailable": "unavailable",
    "not_ready": "unavailable",
    "disabled": "unavailable",
    "budget_exhausted": "failed",
    "invalid": "failed",
    "invalid_cursor": "unavailable",
    "stale_evidence": "unavailable",
    "failed": "failed",
    "error": "failed",
    "stale": "failed",
    "context_stale": "failed",
    "empty": "completed",
    "not_indexed": "unavailable",
    "indexing": "unavailable",
}


def _safe_model_route(model: Model | str | None) -> str:
    """Return a short, non-sensitive model route/name for diagnostics."""
    if model is None:
        return "none"
    if isinstance(model, str):
        return model[:64]
    name = getattr(model, "model_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()[:64]
    return type(model).__name__[:64]


def _find_host_budget_exhausted(
    exc: BaseException,
) -> HostBudgetExhausted | None:
    """Walk ExceptionGroup / cause chain for a typed HostBudgetExhausted."""
    if isinstance(exc, HostBudgetExhausted):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_host_budget_exhausted(sub)
            if found is not None:
                return found
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return _find_host_budget_exhausted(cause)
    return None


# Stable terminal reason when search_hit evidence conflicts with envelope scope.
# Do not embed raw ids, hashes, or provider detail in this string.
TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT = "evidence_scope_invariant_violation"


class EvidenceScopeInvariantError(ValueError):
    """Raised when production cannot emit a fail-closed ok completed DTO.

    Callers must not catch-and-repair by dropping evidence; they must
    terminal the turn without a displayable answer.
    """


def _safe_envelope_snapshot(envelope: ReadingRecordAskContextEnvelope) -> dict[str, Any]:
    """Persistable snapshot — includes server identity for fence/replay."""
    return envelope.model_dump(mode="json")


def evidence_scope_from_envelope(
    envelope: ReadingRecordAskContextEnvelope,
) -> ReaderRecordAskEvidenceScope:
    """Project message-level evidence scope solely from the turn envelope.

    Never derive identity from evidence items, rag_citation, DOM, snippet,
    or envelope_fingerprint. UUID fields serialize as strings for wire/JSON.
    """
    stable = envelope.stable_document_id
    return ReaderRecordAskEvidenceScope(
        reading_record_id=str(envelope.reading_record_id),
        base_id=str(envelope.base_id),
        record_generation=envelope.record_generation,
        stable_document_id=str(stable) if stable is not None else None,
    )


def assert_evidence_scope_matches_items(
    scope: ReaderRecordAskEvidenceScope,
    evidence: list[ReaderRecordAskEvidenceItem],
) -> None:
    """Central production invariant: search_hit identity must match scope.

    Rules
    -----
    - No search_hit: scope may have ``stable_document_id=None`` (RAG off / no doc).
    - Any search_hit: scope.stable_document_id must be non-null; every hit must
      carry a complete rag_citation whose stable_document_id, base_id, and
      record_generation equal the message-level scope exactly.
    - Mismatch or missing citation → :class:`EvidenceScopeInvariantError`.
      Callers must not emit ``final_status=ok`` or silently drop hits.

    Historical rows with ``evidence_scope=None`` are a separate cold-load
    concern: navigation must return ``unavailable.legacy_scope_missing`` and
    must not use current page identity or rag_citation-only shortcuts.
    """
    search_hits = [item for item in evidence if item.kind == "search_hit"]
    if not search_hits:
        return

    if scope.stable_document_id is None or not scope.stable_document_id:
        raise EvidenceScopeInvariantError(
            "search_hit evidence requires non-null evidence_scope.stable_document_id"
        )

    for item in search_hits:
        citation = item.rag_citation
        if citation is None:
            raise EvidenceScopeInvariantError(
                "search_hit evidence item is missing rag_citation"
            )
        if citation.stable_document_id != scope.stable_document_id:
            raise EvidenceScopeInvariantError(
                "search_hit rag_citation.stable_document_id mismatches evidence_scope"
            )
        if citation.base_id != scope.base_id:
            raise EvidenceScopeInvariantError(
                "search_hit rag_citation.base_id mismatches evidence_scope"
            )
        if citation.record_generation != scope.record_generation:
            raise EvidenceScopeInvariantError(
                "search_hit rag_citation.record_generation mismatches evidence_scope"
            )


def build_restricted_evidence_json(
    *,
    run_result: ReadingRecordAskRunResult,
    envelope: ReadingRecordAskContextEnvelope,
) -> list[dict[str, Any]]:
    """Build restricted evidence for ``resolved_evidence_json`` only.

    Includes citation bindings + server observations needed for secure
    navigation. Never attached to public completed DTOs.
    """
    assert run_result.finalized is not None
    finalized = run_result.finalized
    scope = evidence_scope_from_envelope(envelope)
    # Defense-in-depth: search_hit identity must still match envelope scope.
    evidence_items = [
        evidence_item_from_observation(obs) for obs in finalized.resolved_evidence
    ]
    assert_evidence_scope_matches_items(scope, evidence_items)

    bindings = list(finalized.citation_bindings)
    if not bindings and not evidence_items:
        return []

    if bindings:
        return [
            {
                "citation_id": binding.citation_id,
                "handle_id": binding.handle_id,
                "source_kind": binding.source_kind,
                "snippet": binding.snippet,
                "unit_id": binding.unit_id,
                "anchor_segment_id": binding.anchor_segment_id,
                "kind": binding.kind,
                "source_tool": binding.source_tool,
                "rag_citation": binding.rag_citation,
                "evidence_scope": scope.model_dump(mode="json"),
            }
            for binding in bindings
        ]

    # Clarification / empty-citation ok paths: no public citations, nothing
    # to navigate. Scope is still recorded for audit consistency.
    return [
        {
            **item.model_dump(mode="json"),
            "evidence_scope": scope.model_dump(mode="json"),
        }
        for item in evidence_items
    ]


def build_completed_dto(
    *,
    run_result: ReadingRecordAskRunResult,
    message_id: str,
    thread_id: str,
    turn_run_id: str,
    envelope: ReadingRecordAskContextEnvelope,
) -> ReaderRecordAskCompletedDTO:
    """Build the single public completed truth object for SSE + persistence.

    Public surface is no-evh. Restricted evidence is written separately via
    :func:`build_restricted_evidence_json`. Raises
    :class:`EvidenceScopeInvariantError` when search_hit identity does not
    match scope — callers must terminal fail-closed (no ok completed).
    """
    assert run_result.finalized is not None
    assert run_result.finalized.status == "ok"
    assert run_result.final_text is not None
    # Validate restricted evidence invariants before emitting public ok.
    build_restricted_evidence_json(run_result=run_result, envelope=envelope)
    finalized = run_result.finalized
    return ReaderRecordAskCompletedDTO(
        answer_text=run_result.final_text,
        answer_blocks=list(finalized.answer_blocks),
        citations=list(finalized.public_citations),
        knowledge_mode=finalized.knowledge_mode,
        source_status=finalized.source_status,
        message_id=message_id,
        thread_id=thread_id,
        turn_run_id=turn_run_id,
    )


def build_terminal_dto(
    *,
    finalized: FinalizedAskResult | None,
    message_id: str | None,
    thread_id: str | None,
    turn_run_id: str | None,
    envelope_fingerprint: str | None = None,
    final_status: str,
    terminal_reason: str | None,
) -> ReaderRecordAskTerminalDTO:
    del envelope_fingerprint  # internal only; never enter public terminal DTO
    if finalized is not None:
        terminal_reason = terminal_reason or finalized.reason
        # Only override final_status for wire-compatible internal statuses.
        # ``"unavailable"`` is internal-only and must NEVER leak to wire —
        # the caller maps it to ``"failed"`` + a typed terminal_reason
        # before calling this function. Letting it override here would
        # trip the wire FinalStatus Literal validator (5 values, no
        # ``"unavailable"``).
        if finalized.status not in ("ok", "unavailable"):
            final_status = finalized.status
    return ReaderRecordAskTerminalDTO(
        final_status=final_status,  # type: ignore[arg-type]
        message_id=message_id,
        thread_id=thread_id,
        turn_run_id=turn_run_id,
        terminal_reason=terminal_reason,
    )


class _ProgressProjector:
    """Map internal RuntimeEvent → privacy-safe ProgressDTO with monotonic clock."""

    def __init__(self, *, started_at: float) -> None:
        self._started_at = started_at
        self._sequence = 0
        self.progress_event_count = 0
        self.time_to_first_activity_ms: int | None = None
        self.read_range_calls = 0
        self.search_current_article_calls = 0
        self._agent_started_emitted = False

    def _elapsed_ms(self) -> int:
        return max(0, int((time.perf_counter() - self._started_at) * 1000))

    def _next(
        self,
        *,
        phase: ProgressPhase,
        activity: ProgressActivity,
        summary: str,
        tool_name: ProgressToolName | None = None,
        status: ProgressStatus | None = None,
        duration_ms: int | None = None,
    ) -> ReaderRecordAskProgressDTO:
        self._sequence += 1
        elapsed = self._elapsed_ms()
        if self.time_to_first_activity_ms is None:
            self.time_to_first_activity_ms = elapsed
        self.progress_event_count += 1
        return ReaderRecordAskProgressDTO(
            sequence=self._sequence,
            phase=phase,
            activity=activity,
            summary=summary,
            elapsed_ms=elapsed,
            tool_name=tool_name,
            status=status,
            duration_ms=duration_ms,
        )

    def ensure_agent_started(self) -> ReaderRecordAskProgressDTO | None:
        if self._agent_started_emitted:
            return None
        self._agent_started_emitted = True
        return self._next(
            phase="agent_running",
            activity="started",
            summary="正在分析当前文章",
            status="running",
        )

    def project(self, event: RuntimeEvent) -> list[ReaderRecordAskProgressDTO]:
        """Whitelist-project an internal event. Never dumps event payloads."""
        out: list[ReaderRecordAskProgressDTO] = []

        if isinstance(event, RunStartedEvent):
            started = self.ensure_agent_started()
            if started is not None:
                out.append(started)
            return out

        if isinstance(event, AnalysisStartedEvent):
            # Safe phase only: generic activity, no reasoning text/length.
            started = self.ensure_agent_started()
            if started is not None:
                out.append(started)
            out.append(
                self._next(
                    phase="agent_running",
                    activity="started",
                    summary="开始分析",
                    status="running",
                )
            )
            return out

        if isinstance(event, AnalysisFinishedEvent):
            out.append(
                self._next(
                    phase="agent_running",
                    activity="completed",
                    summary="分析完成",
                    status="ok",
                )
            )
            return out

        if isinstance(event, ToolCallEvent):
            started = self.ensure_agent_started()
            if started is not None:
                out.append(started)
            tool = event.tool_name
            # R4-A5-7: expand_evidence → generic agent_running only (no
            # tool_name, no pointer/body/handle in progress).
            if tool == TOOL_EXPAND_EVIDENCE:
                out.append(
                    self._next(
                        phase="agent_running",
                        activity="started",
                        summary="正在扩展证据",
                        status="running",
                    )
                )
                return out
            if tool not in _PUBLIC_TOOL_NAMES:
                # Unknown tools: stay on generic agent activity, no dynamic names
                # and no repeated agent_running/started spam.
                return out
            if tool == TOOL_READ_RANGE:
                self.read_range_calls += 1
                out.append(
                    self._next(
                        phase="reading_context",
                        activity="started",
                        summary="正在读取文章上下文",
                        tool_name="read_range",
                        status="running",
                    )
                )
            else:
                self.search_current_article_calls += 1
                out.append(
                    self._next(
                        phase="searching_article",
                        activity="started",
                        summary="正在检索当前文章",
                        tool_name="search_current_article",
                        status="running",
                    )
                )
            return out

        if isinstance(event, ToolResultEvent):
            tool = event.tool_name
            # Fail-closed: unknown/future statuses never project as completed/ok.
            raw_status = str(event.status or "").lower()
            activity = _TOOL_RESULT_ACTIVITY.get(raw_status, "failed")
            duration = event.duration_ms if event.duration_ms is not None else None
            if tool == TOOL_EXPAND_EVIDENCE:
                summary = {
                    "completed": "已扩展证据",
                    "unavailable": "证据扩展暂不可用",
                    "failed": "证据扩展失败",
                }.get(activity, "证据扩展失败")
                status: ProgressStatus = {
                    "completed": "ok",
                    "unavailable": "unavailable",
                    "failed": "failed",
                }.get(activity, "failed")  # type: ignore[assignment]
                out.append(
                    self._next(
                        phase="agent_running",
                        activity=activity,
                        summary=summary,
                        status=status,
                        duration_ms=duration,
                    )
                )
                return out
            if tool == TOOL_READ_RANGE:
                summary = {
                    "completed": "已读取相关上下文",
                    "unavailable": "文章上下文暂不可用",
                    "failed": "读取文章上下文失败",
                }.get(activity, "读取文章上下文失败")
                status: ProgressStatus = {
                    "completed": "ok",
                    "unavailable": "unavailable",
                    "failed": "failed",
                }.get(activity, "failed")  # type: ignore[assignment]
                out.append(
                    self._next(
                        phase="reading_context",
                        activity=activity,
                        summary=summary,
                        tool_name="read_range",
                        status=status,
                        duration_ms=duration,
                    )
                )
            elif tool == TOOL_SEARCH_CURRENT_ARTICLE:
                summary = {
                    "completed": "已检索当前文章",
                    "unavailable": "当前文章检索暂不可用",
                    "failed": "当前文章检索失败",
                }.get(activity, "当前文章检索失败")
                status = {
                    "completed": "ok",
                    "unavailable": "unavailable",
                    "failed": "failed",
                }.get(activity, "failed")  # type: ignore[assignment]
                out.append(
                    self._next(
                        phase="searching_article",
                        activity=activity,
                        summary=summary,
                        tool_name="search_current_article",
                        status=status,
                        duration_ms=duration,
                    )
                )
            else:
                # Unknown tool result: fail-closed generic failure, no tool_name.
                out.append(
                    self._next(
                        phase="agent_running",
                        activity="failed",
                        summary="分析步骤失败",
                        status="failed",
                        duration_ms=duration,
                    )
                )
            return out

        if isinstance(event, ComposingAnswerEvent):
            started = self.ensure_agent_started()
            if started is not None:
                out.append(started)
            out.append(
                self._next(
                    phase="composing_answer",
                    activity="started",
                    summary="正在组织回答",
                    status="running",
                )
            )
            return out

        if isinstance(event, ValidatingEvidenceEvent):
            out.append(
                self._next(
                    phase="validating_evidence",
                    activity="started",
                    summary="正在核对回答依据",
                    status="running",
                )
            )
            return out

        if isinstance(event, FinalAnswerEvent):
            # Final answer is post-finalizer. Do not claim validation is starting.
            return out

        if isinstance(event, RunFinishedEvent):
            # No extra public progress after validation — completed/terminal follows.
            return out

        return out


def _make_queue_sink(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[Any],
) -> Callable[[RuntimeEvent], None]:
    """Thread/task-safe sink that never blocks the agent on a full queue."""

    def _sink(event: RuntimeEvent) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            # Loop closed / different thread after cancellation — drop silently.
            return
        except asyncio.QueueFull:
            return

    return _sink


async def _run_agentic_turn(
    *,
    repo: ReaderRecordAskRepository,
    thread_id: UUID,
    assistant_msg: dict[str, Any],
    turn: dict[str, Any],
    envelope: ReadingRecordAskContextEnvelope,
    access: DocumentAccess,
    active_model: Model | str | None,
    wired_rag: ArticleRagSearchPort | None,
    wired_map_source_provider: Any,
    user_message: str,
    run_fn: RunFn | None,
    pointer_ledger: ExpansionPointerLedger | None,
    model_settings: ModelSettings | None = None,
    usage_limits: UsageLimits | None = None,
) -> AsyncIterator[str]:
    """Run the agent task and stream SSE events to terminal/completed.

    Shared core between ``stream_agentic_thread_message`` (new message) and
    ``retry_agentic_thread_message`` (regenerate existing assistant message).
    Both callers prepare thread / envelope / model / messages / turn_run
    state and delegate the streaming + terminal handling here.

    Caller invariants:
    - ``assistant_msg`` has been persisted (or reset) with status='streaming'.
    - ``turn`` is a freshly-created ``reader_ask_turn_runs`` row.
    - ``envelope`` and ``access`` are fully resolved (stable document id,
      anchor, facts) — the helper does not re-resolve them.
    - ``active_model`` may be None — helper emits a typed terminal.

    ASK-M1: ``model_settings`` / ``usage_limits`` forward the resolved
    product budget into ``run_reading_record_ask`` (and from there into
    PydanticAI ``agent.run``). Both default to ``None``.
    """
    run_agent = run_fn or run_reading_record_ask
    turn_run_id = UUID(turn["id"])
    message_id = UUID(assistant_msg["id"])

    if active_model is None:
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=(
                "agentic_model_unconfigured: no validated model for reader_ask route; "
                "refusing pseudo-completed answer"
            ),
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return

    started_at = time.perf_counter()
    projector = _ProgressProjector(started_at=started_at)
    # Unbounded: tool fan-out is small; avoid blocking the agent on progress.
    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    sink = _make_queue_sink(loop, event_queue)

    # ASK-REASONING-R1: the single approved reasoning projection
    # chokepoint for this turn. Receives raw provider reasoning via the
    # ThinkingObserver injection, publishes only the deterministic
    # redacted + quota-bounded projection as agentic.reasoning.* events.
    # Raw reasoning never enters SSE/DTO/DB/logs. When the provider
    # returns no non-empty reasoning it stays silent (no events, no
    # persistence) and the UI renders no reasoning element.
    reasoning_projector = ReasoningProjectorObserver(
        emit=sink,
        message_id=assistant_msg["id"],
        thread_id=str(thread_id),
        turn_run_id=turn["id"],
    )

    active_ledger = (
        pointer_ledger
        if pointer_ledger is not None
        else get_process_pointer_ledger()
    )
    agent_task = asyncio.create_task(
        run_agent(
            user_message=user_message,
            envelope=envelope,
            document_access=access,
            model=active_model,
            article_rag=wired_rag,
            event_sink=sink,
            pointer_ledger=active_ledger,
            map_source_material_provider=wired_map_source_provider,
            model_settings=model_settings,
            usage_limits=usage_limits,
            thinking_observer=reasoning_projector,
        )
    )

    def _on_agent_done(task: asyncio.Task[Any]) -> None:
        try:
            event_queue.put_nowait(_AGENT_DONE)
        except Exception:  # noqa: BLE001
            return

    agent_task.add_done_callback(_on_agent_done)

    run_result: ReadingRecordAskRunResult | None = None
    terminal_emitted = False

    try:
        while True:
            item = await event_queue.get()
            if item is _AGENT_DONE:
                break
            if isinstance(item, AgenticReasoningStartedEvent):
                # ASK-REASONING-R1: safe projection only — emitted by the
                # reasoning projector on the first non-empty projected
                # chunk. 1:1 wire mapping; never progress, never phase.
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_STARTED,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(item, AgenticReasoningDeltaEvent):
                # ASK-REASONING-R1: projected reasoning increment
                # (redaction + quota already applied server-side).
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_DELTA,
                    item.model_dump(mode="json"),
                )
                continue
            if not isinstance(
                item,
                RunStartedEvent
                | AnalysisStartedEvent
                | AnalysisFinishedEvent
                | AnswerDeltaEvent
                | ToolCallEvent
                | ToolResultEvent
                | ComposingAnswerEvent
                | ValidatingEvidenceEvent
                | FinalAnswerEvent
                | RunFinishedEvent,
            ):
                # Only project known runtime events; never dump raw objects.
                continue
            if isinstance(item, AnswerDeltaEvent):
                # R4-A6: token-level answer_text increment — user-visible
                # answer content, never reasoning. Maps 1:1 to
                # message.delta; never projected as agentic progress.
                yield encode_sse(EVENT_MESSAGE_DELTA, {"delta": item.delta})
                continue
            for progress in projector.project(item):
                yield encode_sse(
                    EVENT_AGENTIC_PROGRESS,
                    progress.model_dump(mode="json"),
                )

        # Drain any late events that arrived with/after DONE.
        while not event_queue.empty():
            try:
                item = event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _AGENT_DONE:
                continue
            if isinstance(item, AgenticReasoningStartedEvent):
                # ASK-REASONING-R1 (late drain path).
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_STARTED,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(item, AgenticReasoningDeltaEvent):
                # ASK-REASONING-R1 (late drain path).
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_DELTA,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(
                item,
                RunStartedEvent
                | AnalysisStartedEvent
                | AnalysisFinishedEvent
                | AnswerDeltaEvent
                | ToolCallEvent
                | ToolResultEvent
                | ComposingAnswerEvent
                | ValidatingEvidenceEvent
                | FinalAnswerEvent
                | RunFinishedEvent,
            ):
                if isinstance(item, AnswerDeltaEvent):
                    # R4-A6: token-level answer_text increment (drain path).
                    yield encode_sse(
                        EVENT_MESSAGE_DELTA, {"delta": item.delta}
                    )
                    continue
                for progress in projector.project(item):
                    yield encode_sse(
                        EVENT_AGENTIC_PROGRESS,
                        progress.model_dump(mode="json"),
                    )

        try:
            run_result = agent_task.result()
        except asyncio.CancelledError:
            raise
        except HostBudgetExhausted as exc:
            # Typed host budget terminal — before generic exception handling.
            # Never surface account dumps, body, or denial text on the wire.
            logger.warning(
                "reader_record_ask budget exhausted: account=%s turn_run_id=%s "
                "message_id=%s model_route=%s envelope_fp=%s total_ms=%s",
                getattr(exc, "account", "unknown"),
                turn["id"],
                assistant_msg["id"],
                _safe_model_route(active_model),
                envelope.envelope_fingerprint[:12],
                max(0, int((time.perf_counter() - started_at) * 1000)),
            )
            terminal = build_terminal_dto(
                finalized=None,
                message_id=assistant_msg["id"],
                thread_id=str(thread_id),
                turn_run_id=turn["id"],
                envelope_fingerprint=envelope.envelope_fingerprint,
                final_status="failed",
                terminal_reason=TERMINAL_REASON_BUDGET_EXHAUSTED,
            )
            await repo.terminal_agentic_turn_run(
                turn_run_id=turn_run_id,
                message_id=message_id,
                run_status="failed",
                final_status="failed",
                terminal_reason=terminal.terminal_reason,
                terminal_dto=terminal.model_dump(mode="json"),
            )
            yield encode_sse(
                EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json")
            )
            yield encode_sse(
                EVENT_MESSAGE_INTERRUPTED,
                terminal.model_dump(mode="json"),
            )
            terminal_emitted = True
            return
        except BaseExceptionGroup as exc_group:
            # Tool-path HostBudgetExhausted may surface inside ExceptionGroup.
            budget_exc = _find_host_budget_exhausted(exc_group)
            if budget_exc is not None:
                logger.warning(
                    "reader_record_ask budget exhausted (group): account=%s "
                    "turn_run_id=%s message_id=%s",
                    budget_exc.account,
                    turn["id"],
                    assistant_msg["id"],
                )
                terminal = build_terminal_dto(
                    finalized=None,
                    message_id=assistant_msg["id"],
                    thread_id=str(thread_id),
                    turn_run_id=turn["id"],
                    envelope_fingerprint=envelope.envelope_fingerprint,
                    final_status="failed",
                    terminal_reason=TERMINAL_REASON_BUDGET_EXHAUSTED,
                )
                await repo.terminal_agentic_turn_run(
                    turn_run_id=turn_run_id,
                    message_id=message_id,
                    run_status="failed",
                    final_status="failed",
                    terminal_reason=terminal.terminal_reason,
                    terminal_dto=terminal.model_dump(mode="json"),
                )
                yield encode_sse(
                    EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json")
                )
                yield encode_sse(
                    EVENT_MESSAGE_INTERRUPTED,
                    terminal.model_dump(mode="json"),
                )
                terminal_emitted = True
                return
            # Fall through to UnexpectedModelBehavior / generic handling.
            if isinstance(exc_group, ExceptionGroup):
                # Re-raise first non-budget sub-exception path via generic.
                raise exc_group.exceptions[0] from None
            raise
        except UnexpectedModelBehavior as exc:
            logger.warning(
                "reader_record_ask structured output invalid: type=%s turn_run_id=%s "
                "message_id=%s model_route=%s envelope_fp=%s total_ms=%s "
                "progress_events=%s ttfa_ms=%s read_range_calls=%s search_calls=%s",
                type(exc).__name__,
                turn["id"],
                assistant_msg["id"],
                _safe_model_route(active_model),
                envelope.envelope_fingerprint[:12],
                max(0, int((time.perf_counter() - started_at) * 1000)),
                projector.progress_event_count,
                projector.time_to_first_activity_ms,
                projector.read_range_calls,
                projector.search_current_article_calls,
            )
            terminal = build_terminal_dto(
                finalized=None,
                message_id=assistant_msg["id"],
                thread_id=str(thread_id),
                turn_run_id=turn["id"],
                envelope_fingerprint=envelope.envelope_fingerprint,
                final_status="failed",
                terminal_reason=TERMINAL_REASON_AGENT_OUTPUT_INVALID,
            )
            await repo.terminal_agentic_turn_run(
                turn_run_id=turn_run_id,
                message_id=message_id,
                run_status="failed",
                final_status="failed",
                terminal_reason=terminal.terminal_reason,
                terminal_dto=terminal.model_dump(mode="json"),
            )
            yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
            yield encode_sse(
                EVENT_MESSAGE_INTERRUPTED,
                terminal.model_dump(mode="json"),
            )
            terminal_emitted = True
            return
        except Exception as exc:
            logger.warning(
                "reader_record_ask agent run failed: type=%s turn_run_id=%s "
                "message_id=%s model_route=%s envelope_fp=%s total_ms=%s "
                "progress_events=%s ttfa_ms=%s read_range_calls=%s search_calls=%s",
                type(exc).__name__,
                turn["id"],
                assistant_msg["id"],
                _safe_model_route(active_model),
                envelope.envelope_fingerprint[:12],
                max(0, int((time.perf_counter() - started_at) * 1000)),
                projector.progress_event_count,
                projector.time_to_first_activity_ms,
                projector.read_range_calls,
                projector.search_current_article_calls,
            )
            terminal = build_terminal_dto(
                finalized=None,
                message_id=assistant_msg["id"],
                thread_id=str(thread_id),
                turn_run_id=turn["id"],
                envelope_fingerprint=envelope.envelope_fingerprint,
                final_status="failed",
                terminal_reason=TERMINAL_REASON_AGENT_RUN_FAILED,
            )
            await repo.terminal_agentic_turn_run(
                turn_run_id=turn_run_id,
                message_id=message_id,
                run_status="failed",
                final_status="failed",
                terminal_reason=terminal.terminal_reason,
                terminal_dto=terminal.model_dump(mode="json"),
            )
            yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
            yield encode_sse(
                EVENT_MESSAGE_INTERRUPTED,
                terminal.model_dump(mode="json"),
            )
            terminal_emitted = True
            return

    except asyncio.CancelledError:
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if not terminal_emitted:
            terminal = build_terminal_dto(
                finalized=None,
                message_id=assistant_msg["id"],
                thread_id=str(thread_id),
                turn_run_id=turn["id"],
                envelope_fingerprint=envelope.envelope_fingerprint,
                final_status="cancelled",
                terminal_reason="client disconnect or cancellation",
            )
            persisted = await repo.terminal_agentic_turn_run(
                turn_run_id=turn_run_id,
                message_id=message_id,
                run_status="cancelled",
                final_status="cancelled",
                terminal_reason=terminal.terminal_reason,
                terminal_dto=terminal.model_dump(mode="json"),
            )
            assert persisted.get("resolved_evidence_json") in (None, [], "[]")
            yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
            yield encode_sse(
                EVENT_MESSAGE_INTERRUPTED,
                terminal.model_dump(mode="json"),
            )
        raise
    finally:
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    if terminal_emitted or run_result is None:
        return

    total_ms = max(0, int((time.perf_counter() - started_at) * 1000))
    finalized = run_result.finalized
    if finalized is None or finalized.status != "ok" or run_result.final_text is None:
        status = finalized.status if finalized is not None else "failed"
        run_status = "stale" if status == "context_stale" else "failed"

        # Map internal "unavailable" to wire "failed" + typed terminal_reason.
        # Wire FinalStatus is a 5-value Literal that does NOT include
        # "unavailable"; production must never emit it on the wire. The
        # caller (runtime) sets finalized.reason to one of the two typed
        # values when producing status="unavailable"; we defence-in-depth
        # validate that here and fall back to a safe typed value.
        if status == "unavailable":
            wire_final_status = "failed"
            typed_reason = finalized.reason if finalized is not None else None
            if typed_reason not in (
                TERMINAL_REASON_DOCUMENT_UNAVAILABLE,
                TERMINAL_REASON_BASELINE_UNAVAILABLE,
            ):
                typed_reason = TERMINAL_REASON_BASELINE_UNAVAILABLE
        else:
            wire_final_status = status if status != "ok" else "failed"
            typed_reason = (
                finalized.reason if finalized is not None else "missing_finalizer_result"
            )

        terminal = build_terminal_dto(
            finalized=finalized,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status=wire_final_status,
            terminal_reason=typed_reason,
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status=run_status,
            final_status=terminal.final_status,
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        logger.info(
            "reader_record_ask turn terminal: turn_run_id=%s message_id=%s "
            "model_route=%s final_status=%s total_ms=%s ttfa_ms=%s "
            "progress_events=%s read_range_calls=%s search_calls=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            terminal.final_status,
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return

    try:
        completed = build_completed_dto(
            run_result=run_result,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope=envelope,
        )
    except EvidenceScopeInvariantError:
        # Fail-closed: never emit ok completed with conflicting / incomplete scope.
        # Do not drop conflicting search_hit evidence and retry success.
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT,
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        logger.info(
            "reader_record_ask turn terminal: turn_run_id=%s message_id=%s "
            "model_route=%s final_status=failed total_ms=%s ttfa_ms=%s "
            "progress_events=%s read_range_calls=%s search_calls=%s "
            "reason=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
            TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return

    completed_json = completed.model_dump(mode="json")
    try:
        restricted_evidence = build_restricted_evidence_json(
            run_result=run_result,
            envelope=envelope,
        )
    except EvidenceScopeInvariantError:
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT,
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return
    try:
        persisted = await repo.complete_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            answer_text=completed.answer_text,
            completed_dto=completed_json,
            resolved_evidence=restricted_evidence,
            final_status="ok",
            # ASK-REASONING-R1: the visible reasoning projection commits
            # in the SAME transaction as the answer (or NULL when the
            # provider returned no reasoning). Persist failure below
            # leaves no cold-history reasoning — fail-closed.
            reasoning_projection=reasoning_projector.persistence_payload(),
        )
    except Exception:
        # Success-path DB persistence failed (connection drop, constraint,
        # JSONB encoding, etc.).  Emit a typed terminal so the frontend
        # receives a terminal signal instead of hanging on a stream that
        # ended without message.completed / message.interrupted.
        # The typed reason never embeds the underlying DB error text.
        logger.exception(
            "reader_record_ask persist failed: turn_run_id=%s message_id=%s",
            turn_run_id,
            message_id,
        )
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=TERMINAL_REASON_PERSIST_FAILED,
        )
        terminal_json = terminal.model_dump(mode="json")
        try:
            await repo.terminal_agentic_turn_run(
                turn_run_id=turn_run_id,
                message_id=message_id,
                run_status="failed",
                final_status="failed",
                terminal_reason=TERMINAL_REASON_PERSIST_FAILED,
                terminal_dto=terminal_json,
            )
        except Exception:
            logger.exception(
                "reader_record_ask terminal persist also failed: turn_run_id=%s",
                turn_run_id,
            )
        logger.info(
            "reader_record_ask turn terminal: turn_run_id=%s message_id=%s "
            "model_route=%s final_status=failed total_ms=%s ttfa_ms=%s "
            "progress_events=%s read_range_calls=%s search_calls=%s "
            "reason=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
            TERMINAL_REASON_PERSIST_FAILED,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return
    stored = persisted.get("user_visible_output_json")
    emit_payload = stored if isinstance(stored, dict) else completed_json
    # ASK-REASONING-R1: persist-first ordering contract. The projection
    # and the answer are now committed in one transaction, so from this
    # point on any reload returns the same visible reasoning text. Only
    # now may the completion promise be emitted — and it must precede
    # message.completed. None when the provider returned no reasoning
    # (nothing started, nothing to complete, no cold content).
    reasoning_completed = reasoning_projector.build_completed_event()
    logger.info(
        "reader_record_ask turn completed: turn_run_id=%s message_id=%s "
        "model_route=%s final_status=ok total_ms=%s ttfa_ms=%s "
        "progress_events=%s read_range_calls=%s search_calls=%s "
        "reasoning_projected=%s",
        turn["id"],
        assistant_msg["id"],
        _safe_model_route(active_model),
        total_ms,
        projector.time_to_first_activity_ms,
        projector.progress_event_count,
        run_result.read_range_calls,
        run_result.search_current_article_calls,
        reasoning_completed is not None,
    )
    if reasoning_completed is not None:
        yield encode_sse(
            EVENT_AGENTIC_REASONING_COMPLETED,
            reasoning_completed.model_dump(mode="json"),
        )
    yield encode_sse(EVENT_MESSAGE_COMPLETED, emit_payload)


async def stream_agentic_thread_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    content: str,
    facts: Any,
    request_anchor: Any | None,
    validated_anchor: Any | None = None,
    stable_document_id: UUID | None = None,
    repository: ReaderRecordAskRepository | None = None,
    document_access: DocumentAccess | None = None,
    article_rag: ArticleRagSearchPort | None = None,
    model: Model | str | None = None,
    run_fn: RunFn | None = None,
    auto_wire_dependencies: bool = True,
    pointer_ledger: ExpansionPointerLedger | None = None,
    retry_message_id: UUID | None = None,
    model_settings: ModelSettings | None = None,
    usage_limits: UsageLimits | None = None,
) -> AsyncIterator[str]:
    """Run the agentic path: persist + SSE with a single completed DTO truth.

    When ``auto_wire_dependencies`` is True (production default):
    - resolve a real model via the ``reader_ask`` route (no stub success);
    - load active stable document identity for the envelope;
    - build Article RAG port when ``reader_article_rag_enabled``.

    Explicit ``model`` / ``article_rag`` / ``stable_document_id`` overrides
    always win (tests).  Missing model → typed terminal failed, never
    ``message.completed``.

    Live ``agentic.progress`` events are projected from runtime events via a
    concurrent queue while the agent task is still running.

    When ``retry_message_id`` is set, the function operates in retry mode:
    instead of creating a new user message + assistant message, it resets the
    existing assistant message (identified by ``retry_message_id``) to
    ``streaming`` and reuses the preceding user message's content.  A new
    ``turn_run`` is created linking the existing messages.  ``content`` is
    ignored in retry mode — the original user message text is loaded from DB.
    """
    repo = repository or ReaderRecordAskRepository()
    settings = get_settings()

    thread = await repo.get_thread(
        user_id=user_id,
        thread_id=thread_id,
        reading_record_id=reading_record_id,
    )
    if thread is None:
        yield encode_sse(
            "error",
            {"code": "404", "detail": "Reader ask thread not found for this Reading Record"},
        )
        return

    # Resolve base/generation from facts first so stable-document lookup
    # can fence against the active base.
    base = facts.build_result.base
    base_id = UUID(str(base.base_id))
    generation = int(facts.record.generation)

    resolved_stable_id = stable_document_id
    if resolved_stable_id is None and auto_wire_dependencies:
        resolved_stable_id = await load_active_stable_document_id(
            user_id=user_id,
            reading_record_id=reading_record_id,
            expected_generation=generation,
            expected_base_id=base_id,
        )

    envelope = build_envelope_from_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
        facts=facts,
        request_anchor=request_anchor,
        validated_anchor=validated_anchor,
        stable_document_id=resolved_stable_id,
    )
    access = document_access or document_access_from_facts(
        reading_record_id=reading_record_id,
        facts=facts,
        stable_document_id=resolved_stable_id,
    )

    # Model resolution — never invent a stub completed answer.
    # Explicit model always wins. Production auto-wire resolves reader_ask
    # route; test callers with auto_wire=False and model=None stay unconfigured.
    if model is not None:
        active_model: Model | str | None = model
    elif auto_wire_dependencies:
        active_model = resolve_agentic_model(settings, explicit=None)
    else:
        active_model = None
    wired_rag = article_rag
    if wired_rag is None and auto_wire_dependencies:
        wired_rag = build_production_article_rag_port(settings)

    # M3 C2 wiring: construct the map-source material provider so B3 heading
    # enrichment (§4.2) takes effect on the production path. Only wired when
    # auto_wire_dependencies=True (tests pass auto_wire=False or inject their
    # own run_fn). The provider is a thin preflight adapter — no DB writes,
    # no embedding/Zilliz calls (read-only plan service).
    #
    # Lazy import inside the function to avoid module-load cycles under
    # uvicorn --reload (map_source_material_provider → source_evidence_descriptor
    # → article_map_model_view → __init__ → runtime → turn_coordinator ← cycle).
    wired_map_source_provider: Any = None
    if auto_wire_dependencies:
        from app.services.reader_orchestration.article_rag_index_plan import (
            ArticleRagIndexPlanService,
        )
        from app.services.reader_orchestration.map_source_material_provider import (
            MapSourceMaterialProvider,
        )

        wired_map_source_provider = MapSourceMaterialProvider(
            plan_service=ArticleRagIndexPlanService()
        )

    if retry_message_id is not None:
        # Retry mode: reset the existing assistant message and reuse the
        # preceding user message's content.  No new user message is created.
        existing_assistant, existing_user = (
            await repo.get_assistant_message_with_preceding_user_message(
                thread_id=thread_id,
                message_id=retry_message_id,
            )
        )
        if existing_assistant is None or existing_user is None:
            yield encode_sse(
                "error",
                {
                    "code": "404",
                    "detail": (
                        "Retried assistant message or its preceding user "
                        "message was not found in this thread"
                    ),
                },
            )
            return
        assistant_msg = await repo.reset_assistant_message_for_retry(
            message_id=retry_message_id,
        )
        user_msg = existing_user
        # Use the original user message text as agent input.
        content = existing_user["content_md"] or ""
    else:
        user_msg = await repo.create_message(
            thread_id=thread_id,
            role="user",
            status="completed",
            content_md=content,
            metadata={"execution_version": EXECUTION_VERSION_AGENTIC_V2},
        )
        assistant_msg = await repo.create_message(
            thread_id=thread_id,
            role="assistant",
            status="streaming",
            content_md="",
            metadata={"execution_version": EXECUTION_VERSION_AGENTIC_V2},
        )
    turn = await repo.create_agentic_turn_run(
        message_id=UUID(assistant_msg["id"]),
        thread_id=thread_id,
        user_id=user_id,
        reading_record_id=reading_record_id,
        base_id=envelope.base_id,
        generation=envelope.record_generation,
        turn_id=UUID(user_msg["id"]),
        envelope_fingerprint=envelope.envelope_fingerprint,
        envelope_snapshot=_safe_envelope_snapshot(envelope),
    )

    yield encode_sse(
        EVENT_THREAD_READY,
        {"thread_id": str(thread_id), "execution_version": EXECUTION_VERSION_AGENTIC_V2},
    )
    yield encode_sse(
        EVENT_MESSAGE_STARTED,
        {
            "message_id": assistant_msg["id"],
            "thread_id": str(thread_id),
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        },
    )
    run_started = ReaderRecordAskRunStartedDTO(
        message_id=assistant_msg["id"],
        thread_id=str(thread_id),
        turn_run_id=turn["id"],
        has_initial_selection=envelope.initial_anchor is not None,
    )
    yield encode_sse(EVENT_AGENTIC_RUN_STARTED, run_started.model_dump(mode="json"))

    async for chunk in _run_agentic_turn(
        repo=repo,
        thread_id=thread_id,
        assistant_msg=assistant_msg,
        turn=turn,
        envelope=envelope,
        access=access,
        active_model=active_model,
        wired_rag=wired_rag,
        wired_map_source_provider=wired_map_source_provider,
        user_message=content,
        run_fn=run_fn,
        pointer_ledger=pointer_ledger,
        model_settings=model_settings,
        usage_limits=usage_limits,
    ):
        yield chunk


async def retry_agentic_thread_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    facts: Any,
    model: Model | str | None = None,
    repository: ReaderRecordAskRepository | None = None,
    document_access: DocumentAccess | None = None,
    article_rag: ArticleRagSearchPort | None = None,
    run_fn: RunFn | None = None,
    auto_wire_dependencies: bool = True,
    pointer_ledger: ExpansionPointerLedger | None = None,
    model_settings: ModelSettings | None = None,
    usage_limits: UsageLimits | None = None,
) -> AsyncIterator[str]:
    """Retry an existing assistant message via the agentic path.

    Resets the existing assistant message (``message_id``) to ``streaming``
    and re-runs the agent using the preceding user message's content.  No new
    user message is created.  A new ``turn_run`` is created linking the
    existing messages.

    Delegates to :func:`stream_agentic_thread_message` with
    ``retry_message_id`` set; see that function for the retry-mode contract
    (existing assistant message is reset, preceding user message content is
    reused, ``content`` argument is ignored).

    ASK-M1: ``model_settings`` / ``usage_limits`` forward the resolved
    product budget. Retry must receive the same execution config as the
    original send — callers resolve via
    :func:`resolve_reader_record_ask_execution` before calling.
    """
    async for chunk in stream_agentic_thread_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        content="",  # ignored in retry mode — loaded from existing user msg
        facts=facts,
        request_anchor=None,  # retry uses general document context
        validated_anchor=None,
        stable_document_id=None,
        repository=repository,
        document_access=document_access,
        article_rag=article_rag,
        model=model,
        run_fn=run_fn,
        auto_wire_dependencies=auto_wire_dependencies,
        pointer_ledger=pointer_ledger,
        retry_message_id=message_id,
        model_settings=model_settings,
        usage_limits=usage_limits,
    ):
        yield chunk
    return
