"""Turn-bound opaque evidence expansion (R4-A5-3, offline core slice).

Deep module for ``selection continuation → opaque pointer → expand tool
model-view → new evidence handle``. Narrow public surface, deep state:
callers never pass locators, offsets, body text, fingerprints, generations,
or turn ids into :meth:`EvidenceExpansionSession.expand` — only an opaque
pointer string.

Contracts (design TMP §18)
--------------------------
- A5-2's ``continuation_start`` stays server-only: it never enters the
  projection, tool arguments, error messages, or any model-visible sidecar.
- The **initial** expand pointer is the injected selection's ``evh_*``
  handle; **subsequent** pointers are server-minted ``cur_*`` cursors.
- Every pointer is bound to and validated against:
  ``turn_id, envelope_fingerprint, record_generation, base_id,
  reading_record_id, scope_kind``.
- Known pointer with any binding mismatch → model-visible safe state
  ``stale_evidence``. Unknown / malformed / already-consumed pointer →
  model-visible safe state ``invalid_cursor``.
- The model never supplies or influences the real turn_id; this module
  accepts only server-owned turn context. :class:`ExpandEvidenceToolInput`
  fail-closes on model-supplied turn context (``extra="forbid"``).
- A new citeable ``evh_*`` handle is minted **only after** a successful
  expand. Map / RAG paths are deliberately not implemented here (A5-4/A5-5).

Metering
--------
The model surface is a narrow :class:`ExpandEvidenceToolView` (never the
legacy free-form ``payloads`` shape). The logical text appears exactly
once, inside one ``render_untrusted_article_text(..., role="expand")``
block (XML escaping preserved). Final cost is metered on the **complete**
serialized tool-view via ``ModelViewRenderer.render_tool_view`` and charged
to the ``expand`` account — never estimated from body length alone. Every
success or model-visible safe-error tool-view is rendered then charged; an
unmetered error JSON is never produced. If even the minimal safe view
cannot be charged, the outcome is a typed non-model-visible
``budget_exhausted`` host outcome with zero mutation.

Transaction (success order)
---------------------------
resolve + binding preflight
→ full-tool-view cost fit (``can_charge`` only, no mutation)
→ build prospective observation / handle / cursor state
→ ``charge("expand", renderer-minted tool view)`` once
→ ``registry.register`` + postcondition
→ commit cursor/binding state (issue new cursor, consume old pointer,
  advance codepoint position)
→ return

Any failure after charge and before commit runs the shared host-only
compensation (:func:`evidence_transaction.rollback_charged_observation`,
``failure_domain="expand_evidence"``): conditional discard of **only this**
observation + refund of this charge. New-cursor issuance is rolled back via
:meth:`ExpansionPointerLedger.revoke_if_matches` (provably this call's
artifact). The old pointer is never consumed on failure. Incomplete
compensation fails closed with stable
``expand_evidence_rollback_failed code=...`` messages — no body, repr, or
raw exception text. No model-retry control flow is ever raised.

Pointer state lives in :class:`ExpansionPointerLedger`, a host-owned
turn-spanning store shared across same-host sessions so cross-turn stale
pointers resolve to ``stale_evidence`` instead of silently succeeding.
Offline slice: deterministic in-memory ledger; no TTL/eviction policy yet.

Zero I/O: no document-access seam, RAG port, DB, runtime, SSE, or real
model.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.services.reader_record_ask.baseline_context import ModelContextChunk
from app.services.reader_record_ask.evidence import (
    ServerEvidenceObservation,
    build_server_evidence_observation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.evidence_transaction import (
    rollback_charged_observation,
)
from app.services.reader_record_ask.model_view_budget import (
    BudgetChargeOk,
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.selection_model_view import (
    EVIDENCE_SNIPPET_HARD_CAP,
    SelectionModelViewResult,
)
from app.services.reader_record_ask.tool_contracts import (
    EXPANSION_CURSOR_PREFIX,
    ExpandEvidenceToolView,
    is_expand_pointer_shape,
)

EXPAND_ROLE: str = "expand"

ExpandScopeKind = Literal["selection"]

ExpansionOutcomeKind = Literal[
    "ok",
    "invalid_cursor",
    "stale_evidence",
    "budget_exhausted",
]

LedgerRevokeResult = Literal["revoked", "absent", "mismatch"]

# Fixed model-visible summaries (no counts/offsets/identity — safe phrases).
_EXPAND_SUMMARY_MORE = (
    "Selection segment expanded. More selected text remains; call "
    "expand_evidence with the returned cursor."
)
_EXPAND_SUMMARY_DONE = (
    "Selection segment expanded. The full selection is now visible; no "
    "cursor remains."
)
_INVALID_CURSOR_SUMMARY = (
    "Unknown, malformed, or already-used expansion pointer. No text was added."
)
_STALE_EVIDENCE_SUMMARY = (
    "Expansion pointer does not match this turn's verified context. "
    "No text was added."
)

_ERROR_SUMMARIES: dict[str, str] = {
    "invalid_cursor": _INVALID_CURSOR_SUMMARY,
    "stale_evidence": _STALE_EVIDENCE_SUMMARY,
}

# Stable failure codes — never embed body, repr, or raw exception text.
_EXPAND_ROLLBACK_PREFIX = "expand_evidence_rollback_failed code="
_EXPAND_COMMIT_FAILED_PREFIX = "expand_evidence_failed code="

_TURN_ID_PATTERN = re.compile(r"^turn_[0-9a-f]{32}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Same length as any real server-minted cursor (36 chars); never registered
# in the ledger. Used only to fit candidate tool-views at real shape.
_CURSOR_PLACEHOLDER = f"{EXPANSION_CURSOR_PREFIX}{'0' * 32}"


def _mint_cursor_id() -> str:
    """Server-only opaque continuation cursor mint."""
    return f"{EXPANSION_CURSOR_PREFIX}{secrets.token_hex(16)}"


# ---------------------------------------------------------------------------
# Pointer binding + ledger (turn-bound state store seam)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PointerBinding:
    """Server-owned identity a pointer is bound to and validated against.

    Any mismatch between a known pointer's binding and the session's
    binding is ``stale_evidence`` — never silently accepted.
    """

    turn_id: str
    envelope_fingerprint: str
    record_generation: int
    base_id: UUID
    reading_record_id: UUID
    scope_kind: ExpandScopeKind

    def __post_init__(self) -> None:
        if not isinstance(self.turn_id, str) or not _TURN_ID_PATTERN.match(
            self.turn_id
        ):
            raise ValueError(
                "turn_id must be a server-minted token matching "
                "turn_<32 hex chars>"
            )
        if not isinstance(
            self.envelope_fingerprint, str
        ) or not _FINGERPRINT_PATTERN.match(self.envelope_fingerprint):
            raise ValueError(
                "envelope_fingerprint must be a 64-char lowercase hex "
                "SHA-256 digest"
            )
        if (
            not isinstance(self.record_generation, int)
            or isinstance(self.record_generation, bool)
            or self.record_generation < 1
        ):
            raise ValueError("record_generation must be an int >= 1")
        if not isinstance(self.base_id, UUID):
            raise TypeError("base_id must be a UUID")
        if not isinstance(self.reading_record_id, UUID):
            raise TypeError("reading_record_id must be a UUID")
        if self.scope_kind != "selection":
            # A5-3 supports selection scope only; map paths land in A5-4.
            raise ValueError("scope_kind must be 'selection' in R4-A5-3")


@dataclass(frozen=True, slots=True)
class PointerRecord:
    """Ledger entry: which binding minted a pointer, and whether it is spent."""

    binding: PointerBinding
    consumed: bool


class ExpansionPointerLedger:
    """Host-owned pointer knowledge shared across turn-bound sessions.

    Shared so a pointer minted under an old turn identity is *recognized*
    by a later session and answered with ``stale_evidence`` (binding
    mismatch) instead of being misclassified as unknown. Per-session
    private state alone cannot express cross-turn staleness.

    Narrow host-only API: ``issue`` / ``revoke_if_matches`` (compensation)
    / ``mark_consumed`` / ``lookup``. Not a general delete-by-token surface.
    """

    def __init__(self) -> None:
        self._records: dict[str, PointerRecord] = {}

    def lookup(self, token: str) -> PointerRecord | None:
        """Return the record for ``token``, or None when unknown.

        Never raises on malformed tokens — unknown is unknown.
        """
        return self._records.get(token)

    def issue(self, *, token: str, binding: PointerBinding) -> None:
        """Record a freshly minted pointer under its binding.

        Idempotent when the same token is already known with an equal
        binding and unconsumed (session rebuild). Different binding or a
        consumed record is a fail-closed error.
        """
        if not is_expand_pointer_shape(token):
            raise ValueError(
                "pointer token must match evh_<32 hex> or cur_<32 hex>"
            )
        existing = self._records.get(token)
        if existing is not None:
            if existing.consumed:
                raise ValueError(
                    "cannot re-issue a consumed pointer token"
                )
            if existing.binding != binding:
                raise ValueError(
                    "pointer token already bound to a different identity"
                )
            return
        self._records[token] = PointerRecord(binding=binding, consumed=False)

    def revoke_if_matches(
        self,
        *,
        token: str,
        expected_binding: PointerBinding,
    ) -> LedgerRevokeResult:
        """Compensation seam: revoke an unconsumed pointer issued by this call.

        Deletes **only** an unconsumed record whose binding equals
        ``expected_binding`` — provably the caller's own artifact. Foreign
        or consumed records are left untouched (``"mismatch"``).
        """
        record = self._records.get(token)
        if record is None:
            return "absent"
        if record.consumed or record.binding != expected_binding:
            return "mismatch"
        del self._records[token]
        return "revoked"

    def mark_consumed(self, *, token: str) -> PointerRecord:
        """Spend a known, unconsumed pointer. Raises when absent/consumed."""
        record = self._records.get(token)
        if record is None:
            raise ValueError("pointer token is not known to this ledger")
        if record.consumed:
            raise ValueError("pointer token is already consumed")
        consumed_record = PointerRecord(
            binding=record.binding, consumed=True
        )
        self._records[token] = consumed_record
        return consumed_record

    def __len__(self) -> int:
        return len(self._records)


@dataclass(frozen=True, slots=True)
class ExpansionEnvelopeIdentity:
    """Server-owned turn/envelope identity for one expansion session.

    Built by the host from the verified envelope plus the single
    server-minted ``turn_id`` shared with ``TurnCapabilityProjection``.
    Never derived from model input.
    """

    turn_id: str
    envelope_fingerprint: str
    record_generation: int
    base_id: UUID
    reading_record_id: UUID


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpansionOutcome:
    """Result of one :meth:`EvidenceExpansionSession.expand` call.

    ``model_visible=True`` outcomes carry the renderer-minted,
    already-charged tool-view in ``rendered_tool_view``. Hosts must not
    send a ``model_visible=False`` outcome's content to the model;
    ``budget_exhausted`` is a typed host terminal (map it to the typed
    budget-exhausted stream outcome, never to an unmetered JSON error).

    Host-only fields (``segment_text``, ``evidence_handle_id``,
    ``next_cursor``, ``model_chunk``) are populated only on ``ok``.
    """

    kind: ExpansionOutcomeKind
    model_visible: bool
    rendered_tool_view: RenderedModelView | None = None
    charge: BudgetChargeOk | None = None
    segment_text: str | None = None
    evidence_handle_id: str | None = None
    next_cursor: str | None = None
    model_chunk: ModelContextChunk | None = None


# ---------------------------------------------------------------------------
# Expansion session (the deep module)
# ---------------------------------------------------------------------------


class EvidenceExpansionSession:
    """Turn-bound controller expanding one injected selection by codepoints.

    Hidden state (never exposed to callers or the model): canonical text,
    next codepoint position, segment ordinal, the binding, and — via the
    shared :class:`ExpansionPointerLedger` — issued/consumed pointers.

    Construction validates the A5-2 inject facts (fail-closed before any
    state): injected + registry-backed, ``visible_prefix ==
    canonical[:continuation_start]``, registry snippet binary equality,
    fingerprint match, and expandability. Only an ``expandable=True``
    selection yields a usable :attr:`initial_pointer`.
    """

    def __init__(
        self,
        *,
        canonical_selected_text: str,
        selection_result: SelectionModelViewResult,
        envelope_identity: ExpansionEnvelopeIdentity,
        registry: EvidenceRegistry,
        budget: ModelVisibleTurnBudget,
        renderer: ModelViewRenderer | None = None,
        pointer_ledger: ExpansionPointerLedger | None = None,
    ) -> None:
        if not isinstance(canonical_selected_text, str):
            raise TypeError("canonical_selected_text must be str")
        if not canonical_selected_text:
            raise ValueError(
                "expansion requires a non-empty canonical selection"
            )
        if selection_result.status != "injected":
            raise ValueError(
                "expansion requires an injected, registry-backed selection "
                f"(got status={selection_result.status})"
            )
        handle_id = selection_result.selection.handle_id
        if handle_id is None or selection_result.handle_ref is None:
            raise ValueError(
                "expansion requires a registered selection handle"
            )
        if not selection_result.selection.expandable:
            raise ValueError(
                "only an expandable selection produces a usable initial "
                "expansion pointer"
            )
        if registry.envelope_fingerprint != (
            envelope_identity.envelope_fingerprint
        ):
            raise ValueError(
                "evidence registry fingerprint does not match envelope "
                "fingerprint"
            )

        full_len = len(canonical_selected_text)
        continuation_start = selection_result.continuation_start
        if (
            continuation_start < 0
            or continuation_start >= full_len
            or len(selection_result.visible_prefix) != continuation_start
        ):
            raise ValueError(
                "continuation_start is inconsistent with the canonical "
                "selection length"
            )
        if (
            selection_result.visible_prefix
            != canonical_selected_text[:continuation_start]
        ):
            raise ValueError(
                "visible_prefix must equal canonical[:continuation_start]"
            )
        registered = registry.get(handle_id)
        if registered is None:
            raise ValueError(
                "selection handle is not registry-backed"
            )
        if registered.snippet != selection_result.visible_prefix:
            raise ValueError(
                "registry snippet != selection visible_prefix "
                "(binary equality broken)"
            )

        # Validates all identity shapes (fail-closed before any state).
        binding = PointerBinding(
            turn_id=envelope_identity.turn_id,
            envelope_fingerprint=envelope_identity.envelope_fingerprint,
            record_generation=envelope_identity.record_generation,
            base_id=envelope_identity.base_id,
            reading_record_id=envelope_identity.reading_record_id,
            scope_kind="selection",
        )

        ledger = pointer_ledger if pointer_ledger is not None else (
            ExpansionPointerLedger()
        )
        # Register the initial pointer under this binding (idempotent for
        # same-binding rebuilds; fail-closed on consumed / foreign binding).
        ledger.issue(token=handle_id, binding=binding)

        self._canonical = canonical_selected_text
        self._next_pos = continuation_start
        self._segment_ordinal = 1
        self._binding = binding
        self._initial_pointer = handle_id
        self._registry = registry
        self._budget = budget
        self._renderer = (
            renderer if renderer is not None else ModelViewRenderer()
        )
        self._ledger = ledger

    # -- host-only introspection (not model-visible) ------------------------

    @property
    def initial_pointer(self) -> str:
        """The injected selection's opaque ``evh_*`` handle."""
        return self._initial_pointer

    @property
    def turn_id(self) -> str:
        return self._binding.turn_id

    @property
    def binding(self) -> PointerBinding:
        return self._binding

    @property
    def next_codepoint_position(self) -> int:
        """Python-codepoint index where the next segment starts."""
        return self._next_pos

    # -- rendering (pure; no mutation) --------------------------------------

    def _render_success_view(
        self,
        *,
        handle_id: str,
        segment: str,
        cursor: str | None,
    ) -> RenderedModelView:
        """Full expand tool-view: fixed fields + one untrusted text block.

        The logical segment text appears exactly once, inside the
        renderer-minted ``role="expand"`` untrusted block. ``cursor`` may
        be the fixed-length placeholder during fit search.
        """
        untrusted = self._renderer.render_untrusted_article_text(
            handle_id=handle_id,
            ordinal=self._segment_ordinal,
            role=EXPAND_ROLE,
            text=segment,
        )
        view = ExpandEvidenceToolView(
            status="ok",
            summary=(
                _EXPAND_SUMMARY_MORE if cursor is not None else _EXPAND_SUMMARY_DONE
            ),
            next_actions=("expand_evidence",) if cursor is not None else (),
            evidence_handle={"handle_id": handle_id},
            next_cursor=cursor,
            article_text_block=untrusted.text,
        )
        return self._renderer.render_tool_view(view.model_dump(mode="json"))

    def _error_outcome(
        self, status: Literal["invalid_cursor", "stale_evidence"]
    ) -> ExpansionOutcome:
        """Render + charge a minimal safe error tool-view (no body/identity).

        If even the minimal error view cannot be charged, fall back to a
        typed non-model-visible budget-exhausted outcome with zero mutation.
        """
        view = ExpandEvidenceToolView(
            status=status,
            summary=_ERROR_SUMMARIES[status],
        )
        rendered = self._renderer.render_tool_view(view.model_dump(mode="json"))
        if not self._budget.can_charge("expand", rendered):
            return ExpansionOutcome(
                kind="budget_exhausted", model_visible=False
            )
        try:
            ok = self._budget.charge("expand", rendered)
        except ModelViewBudgetError:
            return ExpansionOutcome(
                kind="budget_exhausted", model_visible=False
            )
        return ExpansionOutcome(
            kind=status,
            model_visible=True,
            rendered_tool_view=rendered,
            charge=ok,
        )

    # -- fit (planning only; can_charge, never mutates) ----------------------

    def _fit_segment(
        self, *, handle_id: str
    ) -> tuple[str, RenderedModelView, int, bool] | None:
        """Largest codepoint segment whose *complete* tool-view fits expand.

        Evaluates real candidate views: the terminal candidate (all
        remaining text, ``next_cursor=null``) is checked separately from
        the with-cursor region because the JSON shape — and therefore the
        serialized cost — differs. Never assumes a fixed ``[:2000]`` fits.
        Returns ``(segment, candidate_view, end, has_more)`` or None.
        """
        total = len(self._canonical)
        remaining = self._canonical[self._next_pos :]
        hard_max = min(len(remaining), EVIDENCE_SNIPPET_HARD_CAP)

        # Terminal candidate: everything remaining, cursor null (only when
        # the remaining text is within the snippet hard cap).
        if len(remaining) <= EVIDENCE_SNIPPET_HARD_CAP:
            terminal_view = self._render_success_view(
                handle_id=handle_id, segment=remaining, cursor=None
            )
            if self._budget.can_charge("expand", terminal_view):
                return remaining, terminal_view, total, False
            hi = hard_max - 1
        else:
            hi = hard_max

        lo = 1
        best: tuple[str, RenderedModelView, int, bool] | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            segment = remaining[:mid]
            end = self._next_pos + mid  # strictly < total in this region
            view = self._render_success_view(
                handle_id=handle_id, segment=segment, cursor=_CURSOR_PLACEHOLDER
            )
            if self._budget.can_charge("expand", view):
                best = (segment, view, end, True)
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    # -- compensation --------------------------------------------------------

    def _compensate_after_charge(
        self,
        *,
        charge_cost: int,
        observation: ServerEvidenceObservation,
        cursor_revoke_failed: bool,
    ) -> None:
        """Shared registry+budget rollback plus this call's cursor revoke.

        ``cursor_revoke_failed`` marks a ledger that could not be proven
        clean of this call's cursor. Incomplete compensation fails closed
        with stable ``expand_evidence_rollback_failed code=...`` messages.
        """
        try:
            rollback_charged_observation(
                budget=self._budget,
                account="expand",
                charge_cost=charge_cost,
                registry=self._registry,
                observation=observation,
                failure_domain="expand_evidence",
            )
        except RuntimeError:
            if cursor_revoke_failed:
                raise RuntimeError(
                    f"{_EXPAND_ROLLBACK_PREFIX}cursor_and_registry"
                ) from None
            raise
        if cursor_revoke_failed:
            raise RuntimeError(
                f"{_EXPAND_ROLLBACK_PREFIX}cursor_revoke"
            ) from None

    # -- public entry ---------------------------------------------------------

    def expand(self, *, pointer: str) -> ExpansionOutcome:
        """Expand the selection by one cost-fit segment.

        Takes only an opaque pointer. Returns a charged model-visible
        outcome (``ok`` / ``invalid_cursor`` / ``stale_evidence``) or a
        typed non-model-visible ``budget_exhausted`` host outcome. Never
        raises for model-visible safe states; raises stable-code
        ``RuntimeError`` only on incomplete host-side compensation.
        """
        # 1. Resolve: shape gate → ledger lookup → binding preflight.
        # Binding precedence: a KNOWN pointer with any binding mismatch is
        # stale_evidence even if also consumed; consumed-with-matching-
        # binding (same-turn replay) is invalid_cursor.
        if not is_expand_pointer_shape(pointer):
            return self._error_outcome("invalid_cursor")
        record = self._ledger.lookup(pointer)
        if record is None:
            return self._error_outcome("invalid_cursor")
        if record.binding != self._binding:
            return self._error_outcome("stale_evidence")
        if record.consumed:
            return self._error_outcome("invalid_cursor")
        if self._next_pos >= len(self._canonical):
            # Defensive: a live pointer should never outlive the text.
            return self._error_outcome("invalid_cursor")

        # 2. Full-tool-view cost fit (can_charge only, no mutation).
        handle_id = mint_evidence_handle_id()
        fitted = self._fit_segment(handle_id=handle_id)
        if fitted is None:
            return ExpansionOutcome(kind="budget_exhausted", model_visible=False)
        segment, _candidate_view, end, has_more = fitted

        # 3. Prospective observation / handle / cursor state (pure builds).
        observation = build_server_evidence_observation(
            kind="observation",
            envelope_fingerprint=self._binding.envelope_fingerprint,
            source_tool="selection_expand",
            snippet=segment,
            handle_id=handle_id,
        )
        cursor_token = _mint_cursor_id() if has_more else None
        final_view = self._render_success_view(
            handle_id=handle_id, segment=segment, cursor=cursor_token
        )
        if not self._budget.can_charge("expand", final_view):
            # Real final view does not fit: typed host terminal, no mutation.
            return ExpansionOutcome(kind="budget_exhausted", model_visible=False)

        # 4. Charge the complete renderer-minted tool-view exactly once.
        try:
            charge_ok = self._budget.charge("expand", final_view)
        except ModelViewBudgetError:
            return ExpansionOutcome(kind="budget_exhausted", model_visible=False)
        charge_cost = charge_ok.cost

        # 5. Registry register + postcondition.
        try:
            handle_ref = self._registry.register(observation)
            registered = self._registry.get(handle_id)
            if (
                registered is None
                or registered != observation
                or registered.snippet != segment
                or handle_ref.handle_id != handle_id
            ):
                raise RuntimeError(
                    f"{_EXPAND_COMMIT_FAILED_PREFIX}postcondition"
                )
        except Exception:
            # Cursor not yet issued; old pointer not yet consumed:
            # shared registry+budget rollback is the full compensation.
            rollback_charged_observation(
                budget=self._budget,
                account="expand",
                charge_cost=charge_cost,
                registry=self._registry,
                observation=observation,
                failure_domain="expand_evidence",
            )
            raise

        # 6. Commit cursor/binding state (issue new → consume old → advance).
        if cursor_token is not None:
            try:
                self._ledger.issue(token=cursor_token, binding=self._binding)
            except Exception:
                # Ledger otherwise untouched; shared rollback suffices.
                rollback_charged_observation(
                    budget=self._budget,
                    account="expand",
                    charge_cost=charge_cost,
                    registry=self._registry,
                    observation=observation,
                    failure_domain="expand_evidence",
                )
                raise RuntimeError(
                    f"{_EXPAND_ROLLBACK_PREFIX}cursor_issue"
                ) from None
        try:
            self._ledger.mark_consumed(token=pointer)
        except Exception:
            # Revoke this call's cursor (provably ours), then shared rollback.
            cursor_revoke_failed = False
            if cursor_token is not None:
                try:
                    revoke_outcome = self._ledger.revoke_if_matches(
                        token=cursor_token,
                        expected_binding=self._binding,
                    )
                except Exception:
                    cursor_revoke_failed = True
                else:
                    cursor_revoke_failed = revoke_outcome == "mismatch"
            self._compensate_after_charge(
                charge_cost=charge_cost,
                observation=observation,
                cursor_revoke_failed=cursor_revoke_failed,
            )
            raise RuntimeError(
                f"{_EXPAND_ROLLBACK_PREFIX}consume_old_pointer"
            ) from None

        self._next_pos = end
        ordinal = self._segment_ordinal
        self._segment_ordinal = ordinal + 1

        # 7. Return (binary equality guaranteed by postcondition above).
        model_chunk = ModelContextChunk(
            handle_id=handle_id,
            chunk_ordinal=ordinal,
            text=segment,
        )
        return ExpansionOutcome(
            kind="ok",
            model_visible=True,
            rendered_tool_view=final_view,
            charge=charge_ok,
            segment_text=segment,
            evidence_handle_id=handle_id,
            next_cursor=cursor_token,
            model_chunk=model_chunk,
        )


__all__ = [
    "EXPAND_ROLE",
    "ExpandScopeKind",
    "ExpansionEnvelopeIdentity",
    "ExpansionOutcome",
    "ExpansionOutcomeKind",
    "ExpansionPointerLedger",
    "EvidenceExpansionSession",
    "PointerBinding",
    "PointerRecord",
]
