"""ASK-TURN-LIFECYCLE — Turn stream lifecycle typed contract.

Freezes the unified turn lifecycle state machine that the SSE producer
(``production_stream``), the SSE consumer (Web ``consumeReaderAskSse``),
the persistence seam (``repository``) and the UI send/retry handlers
must agree on.

State machine
-------------

    idle → running → finalizing → committed
                       │
                       ├──→ failed
                       ├──→ cancelled
                       └──→ (finalizing may also return to running on
                            server-owned retry/tool boundary)

* ``idle``       — no turn_run / assistant message created yet.
* ``running``    — assistant message + turn_run are persisted as
                   ``streaming``; provisional answer preview may be
                   emitted. HTTP body is open.
* ``finalizing`` — last answer delta already emitted; host is running
                   output validation, finalizer, restricted-evidence
                   build and the success transaction. No new provisional
                   delta is allowed in this state.
* ``committed``  — ``message.completed`` sent; canonical answer_text /
                   answer_blocks / citations / web_search persisted
                   atomically. Terminal.
* ``failed``     — ``agentic.terminal`` with ``final_status="failed"`` /
                   ``"context_stale"``; canonical answer is empty.
                   Terminal.
* ``cancelled``  — ``agentic.terminal`` with ``final_status="cancelled"``
                   (client abort, user stop, stale-stream reconciliation).
                   Terminal.

Critical invariants
-------------------

* HTTP EOF is **transport cleanup**, not a business terminal. Only a
  trusted typed terminal event (``message.completed`` /
  ``agentic.terminal`` / parse-error / abort) may move the lifecycle into a
  terminal state.
* A terminal is **trusted** only when its ``message_id`` /
  ``thread_id`` / ``turn_run_id`` match the active turn identity
  captured at ``agentic.run_started``. Foreign / stale terminals are
  ignored and never unlock the composer.
* Provisional answer deltas never write to the canonical answer slots
  (``answer_text`` / ``answer_blocks`` / ``citations`` /
  ``knowledge_mode`` / ``web_search``). Only ``message.completed``
  atomically replaces those.
* Any terminal other than ``committed`` discards the provisional
  preview. Failure / cancel / retry never preserves half answers as
  canonical content.
* Terminal writes are idempotent: a ``cancelled`` arriving after
  ``committed`` (or vice versa) must not flip the row back.

This module is intentionally dependency-free (no Pydantic, no FastAPI,
no React) so it can be imported by both the API and the Web client
contract tests. The Web client mirrors these types in
``apps/web/src/components/reader/ask/turn-lifecycle.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

# ---------------------------------------------------------------------------
# State + terminal kinds
# ---------------------------------------------------------------------------

TurnLifecycleState = Literal[
    "idle",
    "running",
    "finalizing",
    "committed",
    "failed",
    "cancelled",
]

# Terminal states — once entered, no further transition is allowed.
TERMINAL_STATES: frozenset[TurnLifecycleState] = frozenset(
    {"committed", "failed", "cancelled"}
)

# Trusted typed terminal event names.
TRUSTED_TERMINAL_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "message.completed",
        "agentic.terminal",
    }
)

# Final status values carried by ``agentic.terminal``. ``ok`` only appears
# on ``message.completed``.
TerminalFinalStatus = Literal[
    "ok",
    "failed",
    "cancelled",
    "context_stale",
]

# Mapping from a trusted terminal final_status to the resulting
# lifecycle state. ``ok`` maps to ``committed`` because it only arrives
# via ``message.completed``.
_FINAL_STATUS_TO_STATE: dict[str, TurnLifecycleState] = {
    "ok": "committed",
    "failed": "failed",
    "cancelled": "cancelled",
    "context_stale": "failed",
}

# Terminal reason kind for stale-stream reconciliation. Used when the
# host detects a streaming run/message whose owner has gone away.
STALE_STREAM_TERMINAL_REASON = "stale_stream_reconciled"


# ---------------------------------------------------------------------------
# Turn identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnIdentity:
    """Identity of the active turn captured at ``agentic.run_started``.

    All three fields must match for a later terminal frame to be
    considered **trusted** for this turn. Any mismatch (foreign turn)
    or arrival after a terminal was already observed (stale frame) means
    the terminal is ignored and never unlocks the composer.
    """

    message_id: str
    thread_id: str
    turn_run_id: str

    def matches(
        self,
        *,
        message_id: str | None,
        thread_id: str | None,
        turn_run_id: str | None,
    ) -> bool:
        """Return True iff all three identifiers match this identity.

        None / empty values in the candidate fields never match — they
        indicate an untrusted payload and must not unlock the turn.
        """
        return (
            bool(message_id)
            and bool(thread_id)
            and bool(turn_run_id)
            and message_id == self.message_id
            and thread_id == self.thread_id
            and turn_run_id == self.turn_run_id
        )


# ---------------------------------------------------------------------------
# Logical terminal result
# ---------------------------------------------------------------------------


LogicalTerminalKind = Literal[
    "completed",      # message.completed with valid v2 payload
    "terminal",       # agentic.terminal with non-ok final_status
    "abort",          # client abort / network failure / BFF disconnect
    "parse_error",    # SSE_PARSE_ERROR — stream corrupted
    "eof",            # HTTP body closed without a typed terminal
]


@dataclass
class LogicalTerminalResult:
    """Result returned by the SSE consumer to the send/retry caller.

    The consumer must stop reading and release the composer as soon as
    a ``trusted`` terminal is observed. ``eof`` is **not** trusted —
    when ``eof`` is the only signal and no trusted terminal arrived,
    the host must still reconcile the run/message to a terminal state
    via the stale-stream reconciliation path.
    """

    kind: LogicalTerminalKind
    identity: TurnIdentity | None = None
    final_status: TerminalFinalStatus | None = None
    terminal_reason: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_trusted_terminal(self) -> bool:
        """True iff this result should immediately unlock the composer.

        ``eof`` alone is not trusted — the host must run stale-stream
        reconciliation. ``abort`` is trusted for composer unlock but
        the host must still persist a ``cancelled`` terminal.
        """
        return self.kind in {"completed", "terminal", "abort", "parse_error"}

    @property
    def resulting_state(self) -> TurnLifecycleState:
        """Lifecycle state the host should record for this terminal."""
        if self.kind == "completed":
            return "committed"
        if self.kind == "terminal":
            if self.final_status == "cancelled":
                return "cancelled"
            return "failed"
        if self.kind in {"abort", "parse_error"}:
            return "cancelled" if self.kind == "abort" else "failed"
        return "failed"  # eof without terminal — treated as failed


def state_for_final_status(final_status: str | None) -> TurnLifecycleState:
    """Map a typed final_status to the canonical lifecycle state.

    Returns ``"failed"`` for unknown / None — fail-closed. Callers that
    receive a ``message.completed`` with ``final_status="ok"`` should
    use ``"committed"`` directly.
    """
    if final_status is None:
        return "failed"
    return _FINAL_STATUS_TO_STATE.get(final_status, "failed")


def is_terminal_state(state: TurnLifecycleState) -> bool:
    """True iff ``state`` cannot transition any further."""
    return state in TERMINAL_STATES


def is_trusted_terminal_event(event_name: str) -> bool:
    """True iff ``event_name`` is a typed terminal the host trusts."""
    return event_name in TRUSTED_TERMINAL_EVENT_NAMES


# ---------------------------------------------------------------------------
# Stream lifecycle hook (route ↔ generator bridge)
# ---------------------------------------------------------------------------


@runtime_checkable
class StreamLifecycleHook(Protocol):
    """Minimal bridge between the route's ``finally`` and the generator.

    ASK-TURN-LIFECYCLE the generator (``stream_agentic_thread_message``)
    calls ``register_active_turn`` as soon as the assistant message +
    turn_run rows are persisted, and ``mark_terminal_emitted`` immediately
    after yielding a typed terminal event (``message.completed`` /
    ``agentic.terminal``).

    The route's ``finally`` block (inside ``_streaming_response``) calls
    ``reconcile_if_streaming`` to terminalize any still-streaming row
    when the FastAPI generator is closed — cleanly, via cancellation,
    or via ASGI cancellation — without a typed terminal.

    The hook is intentionally minimal: it does NOT carry answer text,
    reasoning, citations, or any user-visible payload. Only the two
    identifiers needed for the idempotent reconciliation write plus a
    single boolean tracking whether the generator already wrote a
    typed terminal.
    """

    def register_active_turn(
        self,
        *,
        turn_run_id: UUID,
        message_id: UUID,
    ) -> None: ...

    def mark_terminal_emitted(self) -> None: ...

    async def reconcile_if_streaming(self) -> None: ...


__all__ = [
    "LogicalTerminalKind",
    "LogicalTerminalResult",
    "STALE_STREAM_TERMINAL_REASON",
    "StreamLifecycleHook",
    "TERMINAL_STATES",
    "TRUSTED_TERMINAL_EVENT_NAMES",
    "TerminalFinalStatus",
    "TurnIdentity",
    "TurnLifecycleState",
    "is_terminal_state",
    "is_trusted_terminal_event",
    "state_for_final_status",
]
