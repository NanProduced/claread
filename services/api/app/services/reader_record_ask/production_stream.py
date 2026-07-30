"""Production SSE + persistence adapter for the agentic Reading Record Ask path.

Flag-gated from ``service.py``.  Does not import legacy reader_ask agent,
planner, ask_runtime stream, or old RAG prompt bridges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal
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
from app.services.reader_record_ask.repository import (
    HEARTBEAT_INTERVAL_SECONDS,
    ReaderRecordAskRepository,
)
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.runtime_deps import RuntimeObservation
from app.services.reader_record_ask.runtime_events import (
    AgenticReasoningDeltaEvent,
    AgenticReasoningStartedEvent,
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    AnswerDeltaEvent,
    AnswerPreviewResetEvent,
    ComposingAnswerEvent,
    FinalAnswerEvent,
    RunFinishedEvent,
    RunStartedEvent,
    RuntimeEvent,
    ToolCallEvent,
    ToolResultEvent,
    ValidatingEvidenceEvent,
    WebSearchCallEvent,
    WebSearchResultEvent,
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
    EVENT_MESSAGE_PREVIEW_RESET,
    EVENT_MESSAGE_STARTED,
    EVENT_THREAD_READY,
    encode_sse,
)
from app.services.reader_record_ask.tool_contracts import (
    TOOL_EXPAND_EVIDENCE,
    TOOL_READ_RANGE,
    TOOL_SEARCH_CURRENT_ARTICLE,
    TOOL_SEARCH_WEB,
)
from app.services.reader_record_ask.turn_coordinator import HostBudgetExhausted
from app.services.reader_record_ask.turn_lifecycle import StreamLifecycleHook
from app.services.reader_record_ask.web_evidence_registry import WebEvidenceRegistry
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
    WebSearchMode,
    WebSearchTurnObservation,
    registrable_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchBackend,
)

RunFn = Callable[..., Any]

logger = logging.getLogger(__name__)

# ASK-TURN-LIFECYCLE R1: SSE chunk prefixes that mark a typed terminal
# event. When the generator yields a chunk starting with one of these
# prefixes, the stream lifecycle hook's ``mark_terminal_emitted`` is
# called so the route's ``finally`` block skips stale-stream
# reconciliation (the row was already terminalized by the generator).
_TERMINAL_SSE_PREFIXES: tuple[str, ...] = (
    "event: message.completed\n",
    "event: agentic.terminal\n",
    "event: message.interrupted\n",
)


def _is_terminal_sse_chunk(chunk: str) -> bool:
    """True iff ``chunk`` is a typed terminal SSE frame."""
    return chunk.startswith(_TERMINAL_SSE_PREFIXES)


SubmissionTerminalStatus = Literal["completed", "failed", "cancelled"]

# R8.1: fixed priority — completed > failed > cancelled.
_SUBMISSION_TERMINAL_RANK: dict[SubmissionTerminalStatus, int] = {
    "completed": 3,
    "failed": 2,
    "cancelled": 1,
}


def merge_known_submission_status(
    current: SubmissionTerminalStatus | None,
    incoming: SubmissionTerminalStatus | None,
) -> SubmissionTerminalStatus | None:
    """Monotonic merge for agentic ``known_submission_status``.

    Late/duplicate terminals must never demote a stronger outcome:
    completed cannot become failed/cancelled; failed cannot become cancelled.
    """
    if incoming is None:
        return current
    if current is None:
        return incoming
    if _SUBMISSION_TERMINAL_RANK[incoming] > _SUBMISSION_TERMINAL_RANK[current]:
        return incoming
    return current


def map_message_row_to_submission_status(
    message_status: str | None,
) -> SubmissionTerminalStatus | None:
    """Trusted message-row statuses only; streaming/None → None (no invent)."""
    if message_status == "completed":
        return "completed"
    if message_status == "failed":
        return "failed"
    if message_status in {"interrupted", "cancelled"}:
        return "cancelled"
    return None


def resolve_agentic_submission_write_status(
    *,
    known: SubmissionTerminalStatus | None,
    message_status: str | None = None,
    message_lookup_ok: bool = True,
) -> SubmissionTerminalStatus | None:
    """Decide what status (if any) to write for agentic submission terminal.

    - ``known`` is authoritative when set (already merged monotonically).
    - Message row is supplementary **only** when known is None.
    - Lookup failure / missing row → None (zero write, never invent cancelled).
    """
    if known is not None:
        return known
    if not message_lookup_ok:
        return None
    return map_message_row_to_submission_status(message_status)


async def apply_agentic_submission_terminal(
    *,
    hook: Any,
    known: SubmissionTerminalStatus | None,
    load_message_status: Any | None = None,
    repo: Any | None = None,
) -> SubmissionTerminalStatus | None:
    """Executable seam: resolve + mark submission terminal.

    Returns the status that was requested for write, or ``None`` when no
    write is attempted (unknown outcome). Does not invent cancelled.
    """
    message_status: str | None = None
    message_lookup_ok = True
    if known is None and load_message_status is not None:
        try:
            message_status = await load_message_status()
        except Exception:  # noqa: BLE001
            message_lookup_ok = False
            message_status = None
    write = resolve_agentic_submission_write_status(
        known=known,
        message_status=message_status,
        message_lookup_ok=message_lookup_ok,
    )
    if write is None:
        return None
    ok = await hook.mark(write, repo=repo)
    if not ok:
        await hook.ensure_synced(repo=repo, fallback=write)
    return write


def _submission_status_from_terminal_chunk(
    chunk: str,
) -> SubmissionTerminalStatus | None:
    """Map a typed terminal SSE frame to durable submission status.

    R8: captures the **known** model outcome at the yield site of a
    trusted terminal event. Does not invent cancelled for non-terminal
    or unparseable frames.
    """
    if chunk.startswith("event: message.completed\n"):
        return "completed"
    if not (
        chunk.startswith("event: agentic.terminal\n")
        or chunk.startswith("event: message.interrupted\n")
    ):
        return None
    # Extract final_status from the data line when present.
    for line in chunk.split("\n"):
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            import json

            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return "failed"
        if not isinstance(payload, dict):
            return "failed"
        final = payload.get("final_status")
        if final == "ok":
            return "completed"
        if final == "cancelled":
            return "cancelled"
        if final in {
            "failed",
            "context_stale",
            "invalid_citations",
            "unavailable",
        }:
            return "failed"
        return "failed"
    return "failed"

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
# ASK-WEB-G1-R1: ``TOOL_SEARCH_WEB`` is public so the projector emits
# ``searching_web`` phase + ``search_web`` tool_name progress events.
_PUBLIC_TOOL_NAMES: frozenset[str] = frozenset(
    {TOOL_READ_RANGE, TOOL_SEARCH_CURRENT_ARTICLE, TOOL_SEARCH_WEB}
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
    # ASK-WEB-G1-R1: surface the turn-level web search summary on the
    # public completed DTO. ``None`` means search was not invoked this
    # turn (capability disabled / agent did not call ``search_web``).
    # The summary counts only message-local web citations actually
    # attached to the answer — never the raw provider result count.
    return ReaderRecordAskCompletedDTO(
        answer_text=run_result.final_text,
        answer_blocks=list(finalized.answer_blocks),
        citations=list(finalized.public_citations),
        knowledge_mode=finalized.knowledge_mode,
        source_status=finalized.source_status,
        web_search=finalized.web_search_summary,
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


class _TurnLifecycleMetrics:
    """R3 observability: per-turn lifecycle timing metrics.

    Records only timestamps and counts — never answer content, reasoning
    text, citations, provider payloads, secrets, or user input. All
    timestamps are ``time.perf_counter`` deltas (ms) from
    ``started_at`` so they are monotonic and clock-skew-immune.

    Lifecycle phases tracked (R3 contract):

    - ``first_reasoning_ms``: first ``AgenticReasoningStartedEvent`` or
      first ``AgenticReasoningDeltaEvent`` (whichever fires first).
      ``None`` when no reasoning was emitted this turn.
    - ``first_answer_delta_ms`` / ``last_answer_delta_ms``: first and
      last ``AnswerDeltaEvent`` arrival times. ``None`` when no answer
      delta was emitted (e.g. early validation failure). The gap
      ``last - first`` is the answer streaming duration.
    - ``validation_done_ms``: ``ValidatingEvidenceEvent`` projection or
      ``FinalAnswerEvent`` arrival (whichever fires first), marking the
      end of the agent run loop and the start of host-side validation.
      ``None`` when the run failed before reaching validation.
    - ``persistence_done_ms``: successful ``complete_agentic_turn_run``
      commit timestamp. ``None`` on failure paths (no canonical answer
      was persisted).
    - ``terminal_sent_ms``: first typed terminal SSE frame yielded
      (``message.completed`` / ``agentic.terminal`` /
      ``message.interrupted``). Marks the moment the client should be
      able to receive the terminal signal.
    """

    def __init__(self, *, started_at: float) -> None:
        self._started_at = started_at
        self.first_reasoning_ms: int | None = None
        self.first_answer_delta_ms: int | None = None
        self.last_answer_delta_ms: int | None = None
        self.validation_done_ms: int | None = None
        self.persistence_done_ms: int | None = None
        self.terminal_sent_ms: int | None = None

    def _elapsed_ms(self) -> int:
        return max(0, int((time.perf_counter() - self._started_at) * 1000))

    def mark_first_reasoning(self) -> None:
        if self.first_reasoning_ms is None:
            self.first_reasoning_ms = self._elapsed_ms()

    def mark_answer_delta(self) -> None:
        if self.first_answer_delta_ms is None:
            self.first_answer_delta_ms = self._elapsed_ms()
        self.last_answer_delta_ms = self._elapsed_ms()

    def mark_validation_done(self) -> None:
        if self.validation_done_ms is None:
            self.validation_done_ms = self._elapsed_ms()

    def mark_persistence_done(self) -> None:
        self.persistence_done_ms = self._elapsed_ms()

    def mark_terminal_sent(self) -> None:
        if self.terminal_sent_ms is None:
            self.terminal_sent_ms = self._elapsed_ms()

    def to_log_dict(self) -> dict[str, Any]:
        """Return metrics as a log-safe dict — no content/secrets."""
        return {
            "first_reasoning_ms": self.first_reasoning_ms,
            "first_answer_delta_ms": self.first_answer_delta_ms,
            "last_answer_delta_ms": self.last_answer_delta_ms,
            "validation_done_ms": self.validation_done_ms,
            "persistence_done_ms": self.persistence_done_ms,
            "terminal_sent_ms": self.terminal_sent_ms,
        }


def _log_web_search_turn_observation(
    *,
    run_result: ReadingRecordAskRunResult | None,
    model_route: str,
    provider: str | None,
) -> None:
    """Write exactly one content-free terminal Web Search observation.

    The observation is deliberately a logger-only aggregate. It is not added
    to the completed DTO, history, SSE, or persistence payload, and neither
    this function nor its format string accepts query/URL/title/raw-provider
    data.
    """
    observation = (
        run_result.web_search_turn_observation
        if run_result is not None
        else None
    )
    if observation is None:
        finalized = run_result.finalized if run_result is not None else None
        web_summary = (
            finalized.web_search_summary if finalized is not None else None
        )
        citations = (
            finalized.public_citations if finalized is not None else ()
        )
        web_citations = [
            citation
            for citation in citations
            if citation.source_kind == "web" and citation.url is not None
        ]
        observation = WebSearchTurnObservation(
            attempt_count=(run_result.web_search_calls if run_result is not None else 0),
            final_outcome=(web_summary.outcome if web_summary is not None else None),
            # Without the coordinator-owned observation, the Web Search
            # lifecycle duration is unknown. The enclosing Ask turn duration
            # includes model, validation, and persistence work and must never
            # be mislabeled as search latency.
            total_duration_ms=None,
            cited_source_count=len(web_citations),
            distinct_domain_count=len(
                {
                    registrable_domain_from_canonical_url(citation.url)
                    for citation in web_citations
                    if citation.url is not None
                }
                - {None}
            ),
            deadline_exhausted=False,
            second_query_changed=None,
            final_detail_code=None,
        )
    logger.info(
        "reader_record_ask web search turn: model_route=%s provider=%s "
        "attempt_count=%s final_outcome=%s total_duration_ms=%s "
        "cited_source_count=%s distinct_domain_count=%s "
        "deadline_exhausted=%s second_query_changed=%s final_detail_code=%s",
        model_route,
        provider,
        observation.attempt_count,
        observation.final_outcome,
        observation.total_duration_ms,
        observation.cited_source_count,
        observation.distinct_domain_count,
        observation.deadline_exhausted,
        observation.second_query_changed,
        observation.final_detail_code,
    )


class _ProgressProjector:
    """Map internal RuntimeEvent → privacy-safe ProgressDTO with monotonic clock."""

    def __init__(
        self,
        *,
        started_at: float,
        turn_run_id: str = "",
        model_route: str = "",
    ) -> None:
        self._started_at = started_at
        self._sequence = 0
        self.progress_event_count = 0
        self.time_to_first_activity_ms: int | None = None
        self.read_range_calls = 0
        self.search_current_article_calls = 0
        # ASK-WEB-G1-R1: per-turn web search call counter (host-owned).
        # Mirrors ``read_range_calls`` / ``search_current_article_calls``
        # so observers can audit the per-turn web search budget without
        # touching the agent's tool surface.
        self.web_search_calls = 0
        self._agent_started_emitted = False
        # ASK-WEB-R4: per-attempt telemetry context. ``turn_run_id`` and
        # ``model_route`` are server-owned, non-sensitive identifiers
        # logged with each WebSearchResultEvent for per-attempt
        # observability. Never includes query / URL / provider payload.
        self._turn_run_id = turn_run_id
        self._model_route = model_route

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
        activity_id: str | None = None,
        attempt_count: int | None = None,
        call_sequence: int | None = None,
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
            activity_id=activity_id,
            attempt_count=attempt_count,
            call_sequence=call_sequence,
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
                if activity == "unavailable":
                    # Evidence expansion is an internal, fail-soft refinement.
                    # When the pointer is stale or yields no additional text,
                    # keep the user-facing activity neutral: the agent may
                    # still answer from baseline/article/Web evidence. Raw
                    # status remains available in the runtime event stream and
                    # telemetry; only the public progress copy is de-noised.
                    out.append(
                        self._next(
                            phase="agent_running",
                            activity="completed",
                            summary="已检查文章证据",
                            status="ok",
                            duration_ms=duration,
                        )
                    )
                    return out
                summary = {
                    "completed": "已扩展证据",
                    "failed": "证据扩展失败",
                }.get(activity, "证据扩展失败")
                status: ProgressStatus = {
                    "completed": "ok",
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

        # ASK-WEB-G1-R1: Web Search call/result projection. The agent
        # emits ``WebSearchCallEvent`` when it invokes ``search_web``
        # and ``WebSearchResultEvent`` when the host returns. Neither
        # carries the query text, URLs, or provider payload — only the
        # call sequence + typed outcome. The projector maps them to the
        # ``searching_web`` phase with ``search_web`` tool_name.
        if isinstance(event, WebSearchCallEvent):
            started = self.ensure_agent_started()
            if started is not None:
                out.append(started)
            out.append(
                self._next(
                    phase="searching_web",
                    activity="started",
                    summary="正在搜索网页",
                    tool_name="search_web",
                    status="running",
                    activity_id="web_search",
                    attempt_count=event.attempt_count,
                    call_sequence=event.call_sequence,
                )
            )
            return out

        if isinstance(event, WebSearchResultEvent):
            # Result events carry the only authoritative provider count. A
            # host-rejected invocation may have emitted a started event but
            # must never increase this real-attempt counter.
            self.web_search_calls = max(self.web_search_calls, event.attempt_count)
            # ASK-WEB-R4: per-attempt telemetry. Logs only non-sensitive
            # identifiers and typed outcomes — never query / URL / provider
            # payload / reasoning / API key. ``turn_run_id`` and
            # ``model_route`` are server-owned. ``detail_code`` is a short
            # safe reason code (e.g. ``"ok"``, ``"call_limit"``).
            logger.info(
                "reader_record_ask web search attempt: turn_run_id=%s "
                "model_route=%s tool=search_web call_sequence=%s attempt_count=%s "
                "outcome=%s turn_outcome=%s detail_code=%s "
                "registered_evidence_count=%s duration_ms=%s",
                self._turn_run_id,
                self._model_route,
                event.call_sequence,
                event.attempt_count,
                event.outcome,
                event.turn_outcome,
                event.detail_code,
                event.registered_evidence_count,
                event.duration_ms,
            )
            # ASK-WEB-R4: use ``turn_outcome`` (turn-level aggregated)
            # instead of ``outcome`` (per-attempt) for UI activity so a
            # ``call_limit`` attempt after a successful search does NOT
            # degrade the turn to ``unavailable``. ``outcome`` and
            # ``detail_code`` remain available for telemetry.
            #
            # Translate turn_outcome → public activity / status / summary.
            # ``completed`` / ``no_results`` → completed activity, ok status.
            # ``unavailable`` → unavailable activity, unavailable status.
            # ``failed`` → failed activity, failed status.
            turn_outcome = event.turn_outcome
            web_activity: ProgressActivity = (
                "completed"
                if turn_outcome in ("completed", "no_results")
                else (
                    "unavailable" if turn_outcome == "timeout" else turn_outcome
                )
            )
            web_summary = {
                "completed": "已完成网页搜索",
                "no_results": "未找到相关网页结果",
                "unavailable": "网页搜索暂不可用",
                "failed": "网页搜索失败",
                "timeout": "网页搜索超时，未能验证最新信息",
            }.get(turn_outcome, "网页搜索未知状态")
            web_status: ProgressStatus = {
                "completed": "ok",
                "no_results": "ok",
                "unavailable": "unavailable",
                "failed": "failed",
                "timeout": "unavailable",
            }.get(turn_outcome, "failed")
            out.append(
                self._next(
                    phase="searching_web",
                    activity=web_activity,
                    summary=web_summary,
                    tool_name="search_web",
                    status=web_status,
                    duration_ms=event.duration_ms,
                    activity_id="web_search",
                    attempt_count=event.attempt_count,
                    call_sequence=event.call_sequence,
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
    # ASK-WEB-G1-R1: web search capability + port + registry. The
    # capability is the server-owned execution truth — when ``None`` the
    # runtime must NOT mount the ``search_web`` tool. The backend port
    # is provider-neutral; ``None`` means fail-soft even when
    # ``enabled_for_turn=True``. The registry is bound to the same
    # envelope fingerprint as the article evidence registry; when
    # ``None`` the coordinator builds a fresh one bound to the envelope.
    web_search_capability: ResolvedWebSearchCapability | None = None,
    web_search_backend: WebSearchBackend | None = None,
    web_evidence_registry: WebEvidenceRegistry | None = None,
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

    ASK-WEB-G1-R1: ``web_search_capability`` / ``web_search_backend`` /
    ``web_evidence_registry`` forward the resolved execution truth into
    ``run_reading_record_ask`` so the runtime can mount the
    ``search_web`` tool and inject the :class:`WebSearchBackend` port.
    All three default to ``None`` (capability not granted).
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
    # ASK-WEB-R4: pass turn_run_id and model_route to the projector for
    # per-attempt web search telemetry. Both are server-owned, non-sensitive.
    projector = _ProgressProjector(
        started_at=started_at,
        turn_run_id=str(turn["id"]),
        model_route=_safe_model_route(active_model),
    )
    # ASK-TURN-LIFECYCLE R3: per-turn lifecycle timing metrics. Records
    # only timestamps and counts — never content/secrets. Logged on the
    # final info line so operators can observe the
    # first-delta → validation → persistence → terminal sequence.
    metrics = _TurnLifecycleMetrics(started_at=started_at)
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
    # ASK-WEB-R4: create a RuntimeObservation so the runtime tracks
    # output_validation_final_attempts / output_validation_retry_requests
    # for per-turn observability. Never serialised; never on any public
    # DTO / SSE / DB surface. Logged only as aggregate counts on the
    # terminal/completed info line.
    runtime_observation = RuntimeObservation()
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
            observation=runtime_observation,
            thinking_observer=reasoning_projector,
            web_search_capability=web_search_capability,
            web_search_backend=web_search_backend,
            web_evidence_registry=web_evidence_registry,
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
                # R3: mark first-reasoning timestamp (idempotent).
                metrics.mark_first_reasoning()
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_STARTED,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(item, AgenticReasoningDeltaEvent):
                # ASK-REASONING-R1: projected reasoning increment
                # (redaction + quota already applied server-side).
                # R3: mark first-reasoning timestamp (idempotent; covers
                # the no-started-event edge case where the projector
                # emits a delta without a preceding started event).
                metrics.mark_first_reasoning()
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_DELTA,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(item, AnswerPreviewResetEvent):
                # R4-2: canonical preview-reset SSE event. Carries
                # generation_id, reason, execution_version, and full
                # turn identity so the client can validate trust before
                # mutating UI state. The client MUST clear
                # provisional_content_md but MUST NOT touch canonical
                # content_md.
                yield encode_sse(
                    EVENT_MESSAGE_PREVIEW_RESET,
                    {
                        "generation_id": item.generation_id,
                        "reason": item.reason,
                        "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                        "message_id": assistant_msg["id"],
                        "thread_id": str(thread_id),
                        "turn_run_id": turn["id"],
                    },
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
                | RunFinishedEvent
                | WebSearchCallEvent
                | WebSearchResultEvent,
            ):
                # Only project known runtime events; never dump raw objects.
                continue
            if isinstance(item, AnswerDeltaEvent):
                # R4-A6: token-level answer_text increment — user-visible
                # answer content, never reasoning. Maps 1:1 to
                # message.delta; never projected as agentic progress.
                # R4-2: include generation_id so the client can discard
                # deltas from a stale generation after a preview_reset.
                # R3: track first/last answer delta timestamps.
                metrics.mark_answer_delta()
                yield encode_sse(
                    EVENT_MESSAGE_DELTA,
                    {
                        "delta": item.delta,
                        "generation_id": item.generation_id,
                    },
                )
                continue
            # R3: validation_done marks the end of the agent run loop
            # (ValidatingEvidenceEvent fires when finalizer starts;
            # FinalAnswerEvent fires when finalizer completes). Whichever
            # arrives first closes the validation phase.
            if isinstance(item, ValidatingEvidenceEvent | FinalAnswerEvent):
                metrics.mark_validation_done()
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
                # R3: mark first-reasoning timestamp (idempotent).
                metrics.mark_first_reasoning()
                yield encode_sse(
                    EVENT_AGENTIC_REASONING_STARTED,
                    item.model_dump(mode="json"),
                )
                continue
            if isinstance(item, AgenticReasoningDeltaEvent):
                # ASK-REASONING-R1 (late drain path).
                # R3: mark first-reasoning timestamp (idempotent).
                metrics.mark_first_reasoning()
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
                | RunFinishedEvent
                | WebSearchCallEvent
                | WebSearchResultEvent,
            ):
                if isinstance(item, AnswerDeltaEvent):
                    # R4-A6: token-level answer_text increment (drain path).
                    # R3: track first/last answer delta timestamps.
                    metrics.mark_answer_delta()
                    yield encode_sse(
                        EVENT_MESSAGE_DELTA, {"delta": item.delta}
                    )
                    continue
                # R3: validation_done on drain path too.
                if isinstance(item, ValidatingEvidenceEvent | FinalAnswerEvent):
                    metrics.mark_validation_done()
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
                "message_id=%s model_route=%s envelope_fp=%s total_ms=%s "
                "lifecycle=%s",
                getattr(exc, "account", "unknown"),
                turn["id"],
                assistant_msg["id"],
                _safe_model_route(active_model),
                envelope.envelope_fingerprint[:12],
                max(0, int((time.perf_counter() - started_at) * 1000)),
                metrics.to_log_dict(),
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
            # R3: terminal_sent marks the moment we yield the terminal SSE.
            metrics.mark_terminal_sent()
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
                    "turn_run_id=%s message_id=%s lifecycle=%s",
                    budget_exc.account,
                    turn["id"],
                    assistant_msg["id"],
                    metrics.to_log_dict(),
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
                # R3: terminal_sent marks the moment we yield the terminal SSE.
                metrics.mark_terminal_sent()
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
                "progress_events=%s ttfa_ms=%s read_range_calls=%s search_calls=%s "
                "web_search_calls=%s output_validation_final_attempts=%s "
                "output_validation_retry_requests=%s lifecycle=%s",
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
                projector.web_search_calls,
                runtime_observation.output_validation_final_attempts,
                runtime_observation.output_validation_retry_requests,
                metrics.to_log_dict(),
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
            # R3: terminal_sent marks the moment we yield the terminal SSE.
            metrics.mark_terminal_sent()
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
                "progress_events=%s ttfa_ms=%s read_range_calls=%s search_calls=%s "
                "web_search_calls=%s output_validation_final_attempts=%s "
                "output_validation_retry_requests=%s lifecycle=%s",
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
                projector.web_search_calls,
                runtime_observation.output_validation_final_attempts,
                runtime_observation.output_validation_retry_requests,
                metrics.to_log_dict(),
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
            # R3: terminal_sent marks the moment we yield the terminal SSE.
            metrics.mark_terminal_sent()
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
            # R3: terminal_sent marks the moment we yield the terminal SSE.
            # On cancel, persistence_done is intentionally skipped — no
            # canonical answer was persisted on this path.
            metrics.mark_terminal_sent()
            logger.info(
                "reader_record_ask turn cancelled: turn_run_id=%s message_id=%s "
                "model_route=%s total_ms=%s lifecycle=%s",
                turn["id"],
                assistant_msg["id"],
                _safe_model_route(active_model),
                max(0, int((time.perf_counter() - started_at) * 1000)),
                metrics.to_log_dict(),
            )
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
        _log_web_search_turn_observation(
            run_result=run_result,
            model_route=_safe_model_route(active_model),
            provider=(
                web_search_capability.provider
                if web_search_capability is not None
                else None
            ),
        )

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
            "progress_events=%s read_range_calls=%s search_calls=%s "
            "web_search_calls=%s output_validation_final_attempts=%s "
            "output_validation_retry_requests=%s lifecycle=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            terminal.final_status,
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
            run_result.web_search_calls,
            runtime_observation.output_validation_final_attempts,
            runtime_observation.output_validation_retry_requests,
            metrics.to_log_dict(),
        )
        # R3: terminal_sent marks the moment we yield the terminal SSE.
        metrics.mark_terminal_sent()
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
            "web_search_calls=%s output_validation_final_attempts=%s "
            "output_validation_retry_requests=%s reason=%s lifecycle=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
            run_result.web_search_calls,
            runtime_observation.output_validation_final_attempts,
            runtime_observation.output_validation_retry_requests,
            TERMINAL_REASON_EVIDENCE_SCOPE_INVARIANT,
            metrics.to_log_dict(),
        )
        # R3: terminal_sent marks the moment we yield the terminal SSE.
        metrics.mark_terminal_sent()
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
        # R3: terminal_sent marks the moment we yield the terminal SSE.
        metrics.mark_terminal_sent()
        logger.info(
            "reader_record_ask turn terminal: turn_run_id=%s message_id=%s "
            "model_route=%s final_status=failed total_ms=%s ttfa_ms=%s "
            "progress_events=%s read_range_calls=%s search_calls=%s "
            "web_search_calls=%s output_validation_final_attempts=%s "
            "output_validation_retry_requests=%s reason=%s lifecycle=%s",
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            total_ms,
            projector.time_to_first_activity_ms,
            projector.progress_event_count,
            run_result.read_range_calls,
            run_result.search_current_article_calls,
            run_result.web_search_calls,
            runtime_observation.output_validation_final_attempts,
            runtime_observation.output_validation_retry_requests,
            TERMINAL_REASON_PERSIST_FAILED,
            metrics.to_log_dict(),
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return
    # R3: persistence_done marks the successful commit timestamp. From
    # this point on, the canonical answer is durable and any reload
    # returns the same content.
    # R4-3: CAS outcome check. Only the CAS WINNER (the call that
    # actually flipped the row from streaming → completed with
    # final_status=ok) may emit reasoning.completed and message.completed.
    # The CAS loser (status == "already_terminal") must NOT emit
    # completed — the winning writer owns the terminal. If the winning
    # terminal was a non-ok state (cancelled / failed / context_stale),
    # project the real persisted terminal so the client sees a typed
    # terminal instead of an unexplained EOF. If the winning terminal
    # was ok (duplicate completed), end silently — the first completed
    # already emitted the canonical terminal.
    if persisted.get("status") == "already_terminal":
        winning_final_status = persisted.get("winning_final_status")
        winning_terminal_reason = persisted.get("winning_terminal_reason")
        winning_output_json = persisted.get("winning_user_visible_output_json")
        logger.info(
            "reader_record_ask CAS lost: turn_run_id=%s message_id=%s "
            "winning_final_status=%s winning_terminal_reason=%s "
            "model_route=%s total_ms=%s lifecycle=%s",
            turn["id"],
            assistant_msg["id"],
            winning_final_status,
            winning_terminal_reason,
            _safe_model_route(active_model),
            total_ms,
            metrics.to_log_dict(),
        )
        # R4-3: mark terminal_sent so the lifecycle hook does not
        # double-reconcile. We are about to either project the winning
        # terminal or end silently — both are terminal outcomes.
        metrics.mark_terminal_sent()
        if winning_final_status in ("failed", "cancelled", "context_stale"):
            # Project the real persisted terminal. Prefer the winning
            # terminal DTO if the winner persisted one; otherwise build
            # a minimal typed terminal from the persisted fields.
            if isinstance(winning_output_json, dict):
                terminal_json = winning_output_json
            else:
                terminal = build_terminal_dto(
                    finalized=None,
                    message_id=assistant_msg["id"],
                    thread_id=str(thread_id),
                    turn_run_id=turn["id"],
                    envelope_fingerprint=envelope.envelope_fingerprint,
                    final_status=winning_final_status,
                    terminal_reason=winning_terminal_reason
                    or TERMINAL_REASON_AGENT_RUN_FAILED,
                )
                terminal_json = terminal.model_dump(mode="json")
            yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
            yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        # If winning_final_status == "ok" or unknown, end silently —
        # the winning completed writer owns the message.completed
        # terminal. The client will either see the winner's
        # message.completed (same request) or run stale-stream
        # reconciliation (different request / reconnect).
        return
    metrics.mark_persistence_done()
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
        "web_search_calls=%s output_validation_final_attempts=%s "
        "output_validation_retry_requests=%s reasoning_projected=%s "
        "lifecycle=%s",
        turn["id"],
        assistant_msg["id"],
        _safe_model_route(active_model),
        total_ms,
        projector.time_to_first_activity_ms,
        projector.progress_event_count,
        run_result.read_range_calls,
        run_result.search_current_article_calls,
        run_result.web_search_calls,
        runtime_observation.output_validation_final_attempts,
        runtime_observation.output_validation_retry_requests,
        reasoning_completed is not None,
        metrics.to_log_dict(),
    )
    if reasoning_completed is not None:
        yield encode_sse(
            EVENT_AGENTIC_REASONING_COMPLETED,
            reasoning_completed.model_dump(mode="json"),
        )
    # R3: terminal_sent marks the moment we yield message.completed —
    # the client should be able to receive the terminal signal now.
    metrics.mark_terminal_sent()
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
    # ASK-WEB-G1-R1: web search capability resolved from the request
    # toggle (``web_search_mode``). When ``None`` (capability not
    # granted / ``web_search_mode="disabled"``) the runtime must NOT
    # mount the ``search_web`` tool. When non-None and
    # ``enabled_for_turn=True``, the helper auto-wires a
    # :class:`FakeWebSearchBackend` (G1 vertical slice) and a fresh
    # :class:`WebEvidenceRegistry` bound to the envelope fingerprint
    # unless explicit overrides are supplied. Real provider transports
    # land in G2+ and replace the auto-wired fake.
    web_search_capability: ResolvedWebSearchCapability | None = None,
    web_search_backend: WebSearchBackend | None = None,
    web_evidence_registry: WebEvidenceRegistry | None = None,
    # ASK-TURN-LIFECYCLE R1: route-owned lifecycle hook. The generator
    # registers the active turn (turn_run_id + message_id) as soon as
    # the rows are persisted, and marks terminal-emitted after yielding
    # a typed terminal event. The route's ``finally`` block uses this
    # to reconcile any still-streaming row on generator close.
    lifecycle: StreamLifecycleHook | None = None,
    # ASK-RETRY-CONTRACT-R2/R5: client-generated submission identity.
    client_submission_id: UUID | None = None,
    # R5: pair already durable-created by facade gateway.
    existing_user_message: dict[str, Any] | None = None,
    existing_assistant_message: dict[str, Any] | None = None,
    claim_generation: int | None = None,
    model_option_key: str | None = None,
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

    effective_web_search_mode: WebSearchMode = (
        "allowed"
        if (
            web_search_capability is not None
            and web_search_capability.enabled_for_turn
            and web_search_backend is not None
        )
        else "disabled"
    )
    envelope = build_envelope_from_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
        facts=facts,
        request_anchor=request_anchor,
        validated_anchor=validated_anchor,
        stable_document_id=resolved_stable_id,
        web_search_mode=effective_web_search_mode,
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

    # ASK-WEB-G1-R2: NEVER auto-inject FakeWebSearchBackend on the
    # production path. The fake backend is test-only; production code
    # must never import, construct, or default-select it. When the
    # capability is granted (``enabled_for_turn=True``) but no explicit
    # backend was injected, the runtime mounts the ``search_web`` tool
    # but the tool returns ``unavailable`` on every call — fail-closed
    # by construction. Tests inject scripted fakes directly via the
    # ``web_search_backend`` parameter.
    #
    # The WebEvidenceRegistry is always safe to auto-wire because it
    # is a pure host-side data structure (no provider I/O). It is bound
    # to the envelope fingerprint so web evidence cannot be reused
    # across envelopes (defense-in-depth against cross-turn leakage).
    wired_web_search_backend: WebSearchBackend | None = web_search_backend
    wired_web_evidence_registry: WebEvidenceRegistry | None = web_evidence_registry
    if (
        web_search_capability is not None
        and web_search_capability.enabled_for_turn
        and wired_web_evidence_registry is None
    ):
        wired_web_evidence_registry = WebEvidenceRegistry(
            envelope_fingerprint=envelope.envelope_fingerprint,
        )
    effective_web_search_capability = (
        web_search_capability
        if (
            web_search_capability is not None
            and web_search_capability.enabled_for_turn
            and wired_web_search_backend is not None
        )
        else None
    )

    supersedes_run_id: UUID | None = None
    run_attempt = 1
    if retry_message_id is not None:
        # Retry mode: reset the existing assistant message and reuse the
        # preceding user message's content.  No new user message is created.
        # ASK-RETRY-CONTRACT-R3: content_md is preserved until the new run
        # commits; supersedes_run_id + run_attempt link the regenerate chain.
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
        prior_run_id = existing_assistant.get("turn_run_id")
        prior_attempt = existing_assistant.get("turn_run_attempt")
        if prior_run_id:
            try:
                supersedes_run_id = UUID(str(prior_run_id))
            except ValueError:
                supersedes_run_id = None
        if isinstance(prior_attempt, int) and prior_attempt >= 1:
            run_attempt = prior_attempt + 1
        else:
            run_attempt = 2
        try:
            assistant_msg = await repo.reset_assistant_message_for_retry(
                message_id=retry_message_id,
            )
        except Exception:
            yield encode_sse(
                "error",
                {
                    "code": "409",
                    "detail": "A regenerate is already in progress for this answer.",
                },
            )
            return
        user_msg = existing_user
        # Use the original user message text as agent input.
        content = existing_user["content_md"] or ""
    else:
        # ASK-WEB-G1-R2: persist the resolved web search capability mode
        # (``allowed`` / ``disabled``) on the user message so the retry
        # path can replay the original turn's capability without re-
        # deciding it from the current UI toggle. The persisted value
        # reflects the **actual** capability granted by the server, not
        # the raw request value — when the resolver returns ``None`` or
        # ``enabled_for_turn=False`` (e.g. provider not wired), the
        # persisted mode is ``disabled`` even if the user toggled the
        # UI to "allowed". This is the single source of truth for retry
        # replay; the runtime must NOT fall back to the current UI
        # toggle when this field is present.
        #
        # ASK-WEB-G1-R3: ``allowed`` is only persisted when a real
        # backend was actually wired this turn. Without a backend, the
        # capability cannot execute, so retry replay must NOT inherit a
        # "假可用" (fake-available) mode — fail-closed to ``disabled``.
        #
        # ASK-RETRY-CONTRACT-R5: prefer pair already created by the facade
        # gateway (atomic claim+pair+bind). Fall back to create only when
        # no client_submission_id (pre-R2 clients) or tests inject messages.
        from app.services.reader_record_ask.submission_gateway import (
            build_retry_snapshot,
        )

        if existing_user_message is not None and existing_assistant_message is not None:
            user_msg = existing_user_message
            assistant_msg = existing_assistant_message
        else:
            persisted_web_search_mode = effective_web_search_mode
            retry_snapshot = build_retry_snapshot(
                lane="agentic",
                model_option_key=model_option_key,
                web_search_mode=persisted_web_search_mode,
            )
            if model_option_key is None and client_submission_id is not None:
                # Incomplete snapshot for a submission-bound turn is fail-closed.
                yield encode_sse(
                    "error",
                    {
                        "code": "retry_snapshot_incomplete",
                        "detail": "Agentic turn requires a resolved model_option_key.",
                    },
                )
                return
            user_msg = await repo.create_message(
                thread_id=thread_id,
                role="user",
                status="completed",
                content_md=content,
                metadata={
                    "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                    "retry_lane": "agentic",
                    "retry_contract_version": retry_snapshot[
                        "retry_contract_version"
                    ],
                    "web_search_mode": persisted_web_search_mode,
                    "model_option_key": model_option_key,
                    "retry_snapshot": retry_snapshot,
                },
            )
            assistant_msg = await repo.create_message(
                thread_id=thread_id,
                role="assistant",
                status="streaming",
                content_md="",
                metadata={
                    "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                    "retry_lane": "agentic",
                    "retry_contract_version": retry_snapshot[
                        "retry_contract_version"
                    ],
                    "model_option_key": model_option_key,
                    "retry_snapshot": retry_snapshot,
                },
            )

    # R8/R8.1: known model terminal; monotonic via merge_known_submission_status.
    known_submission_status: SubmissionTerminalStatus | None = None

    async def _sync_submission_terminal_r6(
        *,
        known: SubmissionTerminalStatus | None = None,
    ) -> None:
        """R6–R8.1: CAS terminal sync for client_submission_id.

        Uses :func:`apply_agentic_submission_terminal` seam. Never invents
        cancelled when outcome is unknown.
        """
        if client_submission_id is None or assistant_msg is None:
            return
        from app.services.reader_ask import repository as ask_msg_repo
        from app.services.reader_record_ask.submission_gateway import (
            SubmissionTerminalHook,
        )

        asst_uuid: UUID | None
        try:
            asst_uuid = UUID(str(assistant_msg["id"]))
        except (ValueError, TypeError, KeyError):
            asst_uuid = None
        hook = SubmissionTerminalHook(
            thread_id=thread_id,
            client_submission_id=client_submission_id,
            claim_generation=claim_generation,
            assistant_message_id=asst_uuid,
        )
        effective_known = (
            known if known is not None else known_submission_status
        )

        async def _load_msg_status() -> str | None:
            if asst_uuid is None:
                return None
            row = await ask_msg_repo.get_message(asst_uuid)
            if row is None:
                return None
            return str(row.get("status") or "") or None

        try:
            await apply_agentic_submission_terminal(
                hook=hook,
                known=effective_known,
                load_message_status=(
                    _load_msg_status if effective_known is None else None
                ),
                repo=repo,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "agentic submission terminal sync failed "
                "client_submission_id=%s assistant_id=%s",
                client_submission_id,
                asst_uuid,
                exc_info=True,
            )

    try:
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
            run_attempt=run_attempt,
            supersedes_run_id=supersedes_run_id,
        )
    except RuntimeError:
        yield encode_sse(
            "error",
            {
                "code": "409",
                "detail": "A regenerate is already in progress for this answer.",
            },
        )
        # Conflict is a known failure, not cancel.
        await _sync_submission_terminal_r6(known="failed")
        return

    # ASK-TURN-LIFECYCLE R1: register the active turn identity with the
    # route-owned lifecycle hook. From this point on, any generator close
    # (client disconnect, BFF disconnect, ASGI cancellation) will trigger
    # the route's ``finally`` block to reconcile this turn_run/message to
    # a terminal state via ``reconcile_stale_streaming_turn_run``.
    if lifecycle is not None:
        lifecycle.register_active_turn(
            turn_run_id=UUID(turn["id"]),
            message_id=UUID(assistant_msg["id"]),
        )

    # ASK-TURN-LIFECYCLE R4-5c: heartbeat task. During streaming, a
    # background coroutine updates ``updated_at`` on the turn_run row at
    # ``HEARTBEAT_INTERVAL_SECONDS`` intervals. The stale-stream
    # reconciler (startup sweep + periodic sweeper) treats rows whose
    # ``updated_at`` is older than ``HEARTBEAT_STALE_THRESHOLD_SECONDS``
    # as heartbeat-dead — i.e. the owner process is gone or stuck. This
    # is the heartbeat half of the owner/lease proof: the route
    # ``finally`` is the lease release; this loop is the heartbeat that
    # proves the lease is still alive during long-running turns.
    #
    # The task is best-effort: heartbeat failures are logged and do not
    # tear down the stream. The ``WHERE status = 'streaming'`` guard in
    # ``heartbeat_turn_run`` makes the write a no-op once the row
    # transitions to terminal, so a late heartbeat after the row has
    # already been reconciled cannot resurrect it.
    heartbeat_turn_run_id = UUID(turn["id"])
    heartbeat_task: asyncio.Task[None] | None = None

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await repo.heartbeat_turn_run(turn_run_id=heartbeat_turn_run_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "heartbeat_turn_run failed turn_run_id=%s",
                    heartbeat_turn_run_id,
                    exc_info=True,
                )

    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

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
            # ASK-WEB-G1-R1: echo the resolved capability mode so the
            # frontend can render the Search toggle in the correct state.
            # ``allowed`` only means the capability is mounted; the agent
            # may still choose not to search.
            #
            # ASK-WEB-G1-R3: ``allowed`` must only be echoed when a real
            # executable ``WebSearchBackend`` is wired this turn. Even when
            # the capability resolver returned ``enabled_for_turn=True``,
            # missing backend means the ``search_web`` tool would be a
            # "假可用" (fake-available) capability — the runtime must
            # fail-closed to ``disabled`` so the UI/RunStarted never
            # advertises a capability that cannot execute.
            web_search_mode=effective_web_search_mode,
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
            web_search_capability=effective_web_search_capability,
            web_search_backend=wired_web_search_backend,
            web_evidence_registry=wired_web_evidence_registry,
        ):
            yield chunk
            # ASK-TURN-LIFECYCLE R1: mark terminal-emitted as soon as the
            # generator yields a typed terminal event. This tells the
            # route's ``finally`` block that the row was already
            # terminalized by the generator and stale-stream reconciliation
            # must be skipped (idempotent guard).
            if _is_terminal_sse_chunk(chunk):
                if lifecycle is not None:
                    lifecycle.mark_terminal_emitted()
                # R8.1: single monotonic merge for known terminal status.
                noted = _submission_status_from_terminal_chunk(chunk)
                known_submission_status = merge_known_submission_status(
                    known_submission_status,
                    noted,
                )
    finally:
        # R4-5c: cancel the heartbeat task on stream end (normal,
        # exception, or ASGI cancellation). The ``CancelledError`` is
        # expected and swallowed; any other exception surfaced from the
        # heartbeat loop is logged but never re-raised — the stream's
        # own terminal state must win.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.warning(
                    "heartbeat_task teardown raised turn_run_id=%s",
                    heartbeat_turn_run_id,
                    exc_info=True,
                )
        # R8: sync with known outcome; never invent cancelled if unknown.
        await _sync_submission_terminal_r6(known=known_submission_status)


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
    # ASK-WEB-G1-R1: retry must receive the same resolved web search
    # capability as the original send (callers resolve via
    # ``resolve_reader_record_ask_execution`` before calling). When
    # ``None`` the runtime must NOT mount the ``search_web`` tool —
    # retry must never silently grant a capability the user did not
    # enable on the original turn.
    web_search_capability: ResolvedWebSearchCapability | None = None,
    web_search_backend: WebSearchBackend | None = None,
    web_evidence_registry: WebEvidenceRegistry | None = None,
    # ASK-TURN-LIFECYCLE R1: forwarded to ``stream_agentic_thread_message``.
    lifecycle: StreamLifecycleHook | None = None,
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

    ASK-WEB-G1-R1: ``web_search_capability`` / ``web_search_backend`` /
    ``web_evidence_registry`` forward the resolved web search truth. Retry
    must not silently grant a capability the user did not enable on the
    original turn — callers must pass the same capability (or ``None``)
    resolved from the persisted execution snapshot.
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
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        web_evidence_registry=web_evidence_registry,
        lifecycle=lifecycle,
    ):
        yield chunk
    return
