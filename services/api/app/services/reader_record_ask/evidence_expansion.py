"""Turn-bound opaque evidence expansion (offline core).

Deep module for ``selection continuation → opaque pointer → expand tool
model-view → new evidence handle``. Narrow public surface, deep state:
callers never pass locators, offsets, body text, fingerprints, generations,
or turn ids into :meth:`EvidenceExpansionSession.expand` — only an opaque
pointer string.

Full canonical source integrity
------------------------------------------
Construction verifies the **entire** canonical selection, not just the
visible prefix, via the assembler-minted server-only
:class:`~app.services.reader_record_ask.selection_model_view.SelectionExpansionSeed`:

- full char count == ``len(canonical)``;
- full-content SHA-256 digest == ``seed.canonical_digest`` (minted by the
   assembler when the canonical selection is known — never the
  semantically unclear, possibly absent locator ``text_hash``);
- seed/handle_ref/model-chunk/selection metadata handle consistency;
- ``visible_prefix == canonical[:continuation_start]``;
- registry snippet binary equality.

Same-prefix forgeries (different length, or same length with a replaced
suffix) fail closed at construction with zero budget / registry / ledger
mutation. Digest and full source never enter projection, prompt,
tool-view, error messages, or registry sidecars.

Pointer / binding contracts (design TMP §18)
--------------------------------------------
- ``continuation_start`` stays server-only.
- The **initial** expand pointer is the injected selection's ``evh_*``
  handle; **subsequent** pointers are server-minted ``cur_*`` cursors.
- Every pointer is bound to and validated against:
  ``turn_id, envelope_fingerprint, record_generation, base_id,
  reading_record_id, scope_kind``.
- Known pointer with any binding mismatch → ``stale_evidence`` (takes
  precedence over consumed). Consumed with matching binding (same-turn
  replay), unknown, or malformed → ``invalid_cursor``.
- The model never supplies or influences the real turn_id; this module
  accepts only server-owned turn context.

Ledger transition
----------------------------
Cursor issuance + old-pointer consumption happen as **one** marker-scoped
ledger transition (:meth:`ExpansionPointerLedger.transition_pointers`):

- every attempt mints a unique server-only ``txn_<32 hex>`` marker;
- each write is recorded under that marker's claim (``issue_marker`` /
  ``consume_marker`` on ledger records);
- :meth:`ExpansionPointerLedger.rollback_transition_by_marker` restores
  the old pointer **only** when this marker consumed it and deletes the
  new cursor **only** when this marker issued it — never based on binding
  equality, never touching foreign state;
- compensation does not assume "no write happened before the raise":
  write-then-raise implementations leave a claim behind and the marker
  rollback still restores a clean state;
- the initial selection pointer issuance uses the same marker claim so a
  failed construction leaves no orphan ledger record.

Metering
--------
The model surface is a narrow :class:`ExpandEvidenceToolView` (never the
legacy free-form ``payloads`` shape). The logical text appears exactly
once, inside one ``render_untrusted_article_text(..., role="expand")``
block (XML escaping preserved). Final cost is metered on the **complete**
serialized tool-view via ``ModelViewRenderer.render_tool_view`` and charged
to the ``expand`` account. Every success or model-visible safe-error
tool-view is rendered then charged; if even the minimal safe view cannot
be charged, the outcome is a typed non-model-visible ``budget_exhausted``
host outcome with zero mutation.

Transaction (success order)
---------------------------
resolve + binding preflight
→ full-tool-view cost fit (``can_charge`` only, no mutation)
→ build prospective observation / handle / cursor state
→ ``charge("expand", renderer-minted tool view)`` once
→ ``registry.register`` + postcondition
→ single marker-scoped ledger transition (issue new cursor + consume old
  pointer) → advance codepoint position
→ return

Any failure after charge runs the shared host-only compensation
(:func:`evidence_transaction.rollback_charged_observation`,
``failure_domain="expand_evidence"``) plus marker-scoped ledger rollback.
Incomplete compensation fails closed with stable
``expand_evidence_rollback_failed code=...`` messages — no body, repr, or
raw exception text. No model-retry control flow is ever raised.

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
    compensate_ledger_transition_and_observation,
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
    canonical_selection_digest,
    validate_selection_expansion_seed,
)
from app.services.reader_record_ask.tool_contracts import (
    EXPANSION_CURSOR_PREFIX,
    ExpandEvidenceToolView,
    is_expand_pointer_shape,
)

EXPAND_ROLE: str = "expand"

# Selection-scope expansion. Map-scope expansion (map cursors
# expand entry window text — entries themselves are never evidence).
ExpandScopeKind = Literal["selection", "map"]

ExpansionOutcomeKind = Literal[
    "ok",
    "invalid_cursor",
    "stale_evidence",
    "budget_exhausted",
]

TransitionRollbackStatus = Literal["rolled_back", "incomplete"]

# Capacity / retention forget verdict (public seam; not transaction rollback).
CapacityDiscardResult = Literal["discarded", "absent", "mismatch"]

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
_TRANSITION_MARKER_PATTERN = re.compile(r"^txn_[0-9a-f]{32}$")

# Same length as any real server-minted cursor (36 chars); never registered
# in the ledger. Used only to fit candidate tool-views at real shape.
_CURSOR_PLACEHOLDER = f"{EXPANSION_CURSOR_PREFIX}{'0' * 32}"


def _mint_cursor_id() -> str:
    """Server-only opaque continuation cursor mint."""
    return f"{EXPANSION_CURSOR_PREFIX}{secrets.token_hex(16)}"


def mint_expansion_cursor_id() -> str:
    """Server-only opaque expansion cursor mint (``cur_<32 hex>``).

    Shared by selection-scope and map-scope
    expanders. Cursors are continuation pointers, never evidence handles.
    """
    return _mint_cursor_id()


def mint_transition_marker() -> str:
    """Unique server-only marker for one ledger transition attempt."""
    return f"txn_{secrets.token_hex(16)}"


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
        if self.scope_kind not in ("selection", "map"):
            # support selection + map scopes only.
            raise ValueError("scope_kind must be 'selection' or 'map'")


@dataclass(frozen=True, slots=True)
class PointerRecord:
    """Ledger entry for one pointer.

    ``issue_marker`` / ``consume_marker`` record **which transition**
    wrote each state change so rollback can be scoped to exactly one
    attempt — never based on binding equality.
    """

    binding: PointerBinding
    consumed: bool
    issue_marker: str
    consume_marker: str | None = None


@dataclass(frozen=True, slots=True)
class PointerIssueReceipt:
    """Receipt for one marker-scoped pointer issuance."""

    marker: str
    token: str
    # False on idempotent rebind of an already-known, same-binding,
    # unconsumed pointer (rollback must not delete records this marker
    # did not create).
    newly_issued: bool


@dataclass(frozen=True, slots=True)
class PointerTransitionReceipt:
    """Receipt for one successful atomic pointer transition."""

    marker: str
    consumed_token: str
    issued_token: str | None


class _TransitionClaim:
    """Private mutable per-attempt write claim (ledger storage only)."""

    __slots__ = ("marker", "issued_token", "consumed_token")

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.issued_token: str | None = None
        self.consumed_token: str | None = None


class ExpansionPointerLedger:
    """Host-owned pointer knowledge shared across turn-bound sessions.

    Shared so a pointer minted under an old turn identity is *recognized*
    by a later session and answered with ``stale_evidence`` (binding
    mismatch) instead of being misclassified as unknown.

    Public behavior seams: ``lookup``, ``issue``, ``mark_consumed``,
    ``transition_pointers`` (atomic issue-new + consume-old under one
    marker), and ``rollback_transition_by_marker`` (marker-scoped undo).
    Rollback restores/deletes **only** state written by the same marker —
    foreign pointers are never touched, regardless of binding equality.
    """

    def __init__(self) -> None:
        self._records: dict[str, PointerRecord] = {}
        self._claims: dict[str, _TransitionClaim] = {}

    def lookup(self, token: str) -> PointerRecord | None:
        """Return the record for ``token``, or None when unknown.

        Never raises on malformed tokens — unknown is unknown.
        """
        return self._records.get(token)

    def _claim(self, marker: str) -> _TransitionClaim:
        if not isinstance(marker, str) or not _TRANSITION_MARKER_PATTERN.match(
            marker
        ):
            raise ValueError(
                "transition marker must match txn_<32 hex chars>"
            )
        claim = self._claims.get(marker)
        if claim is None:
            claim = _TransitionClaim(marker)
            self._claims[marker] = claim
        return claim

    def issue(
        self,
        *,
        token: str,
        binding: PointerBinding,
        marker: str,
    ) -> PointerIssueReceipt:
        """Record a freshly minted pointer under its binding + marker.

        Idempotent when the same token is already known with an equal
        binding and unconsumed (session rebuild): the receipt reports
        ``newly_issued=False`` and marker rollback will not delete the
        pre-existing record. Different binding / consumed record is a
        fail-closed error. The claim records the issuance so a later
        marker rollback deletes **only** this marker's write.
        """
        if not is_expand_pointer_shape(token):
            raise ValueError(
                "pointer token must match evh_<32 hex> or cur_<32 hex>"
            )
        claim = self._claim(marker)
        if claim.issued_token is not None:
            raise ValueError("transition marker already issued a pointer")
        existing = self._records.get(token)
        if existing is not None:
            if existing.consumed:
                raise ValueError("cannot re-issue a consumed pointer token")
            if existing.binding != binding:
                raise ValueError(
                    "pointer token already bound to a different identity"
                )
            return PointerIssueReceipt(
                marker=marker, token=token, newly_issued=False
            )
        self._records[token] = PointerRecord(
            binding=binding,
            consumed=False,
            issue_marker=marker,
        )
        claim.issued_token = token
        return PointerIssueReceipt(marker=marker, token=token, newly_issued=True)

    def mark_consumed(self, *, token: str, marker: str) -> PointerRecord:
        """Spend a known, unconsumed pointer under this marker's claim.

        Raises when absent or already consumed. The written record carries
        ``consume_marker`` so a marker rollback restores **only** this
        attempt's consumption.
        """
        claim = self._claim(marker)
        record = self._records.get(token)
        if record is None:
            raise ValueError("pointer token is not known to this ledger")
        if record.consumed:
            raise ValueError("pointer token is already consumed")
        consumed_record = PointerRecord(
            binding=record.binding,
            consumed=True,
            issue_marker=record.issue_marker,
            consume_marker=marker,
        )
        self._records[token] = consumed_record
        claim.consumed_token = token
        return consumed_record

    def transition_pointers(
        self,
        *,
        consume_token: str,
        issue_token: str | None,
        binding: PointerBinding,
        marker: str,
    ) -> PointerTransitionReceipt:
        """Atomic pointer transition: issue new cursor + consume old pointer.

        Preflights the consumed pointer (known, unconsumed, binding equal),
        then writes the new cursor (when any) before consuming the old
        pointer — all under one marker claim. If any step raises
        (including a write-then-raise implementation), the claim stays
        registered and :meth:`rollback_transition_by_marker` restores a
        clean state; no write is assumed absent.
        """
        if not isinstance(marker, str) or not _TRANSITION_MARKER_PATTERN.match(
            marker
        ):
            raise ValueError(
                "transition marker must match txn_<32 hex chars>"
            )
        if marker in self._claims:
            raise ValueError("transition marker is already used")
        claim = _TransitionClaim(marker)
        self._claims[marker] = claim

        # Preflight (defense in depth; the session already answered
        # stale/invalid before charging). Never mutates.
        record = self._records.get(consume_token)
        if record is None:
            raise ValueError(
                "transition consume target is not known to this ledger"
            )
        if record.consumed:
            raise ValueError("transition consume target is already consumed")
        if record.binding != binding:
            raise ValueError(
                "transition consume target binding does not match"
            )

        # Writes under the claim. self.issue / self.mark_consumed record
        # their tokens on this claim as each write lands.
        if issue_token is not None:
            self.issue(token=issue_token, binding=binding, marker=marker)
        self.mark_consumed(token=consume_token, marker=marker)

        return PointerTransitionReceipt(
            marker=marker,
            consumed_token=consume_token,
            issued_token=claim.issued_token,
        )

    def rollback_transition_by_marker(
        self, marker: str
    ) -> TransitionRollbackStatus:
        """Marker-scoped undo of one transition attempt.

        - deletes the issued pointer **only** when its record still carries
          this marker's ``issue_marker`` (provably this attempt's write);
        - restores the consumed pointer **only** when its record still
          carries this marker's ``consume_marker``;
        - never deletes or restores foreign state (different marker),
          regardless of binding equality;
        - idempotent: the claim is dropped after one rollback pass.

        Returns ``"incomplete"`` when the ledger cannot be proven clean of
        this attempt's writes — callers must fail closed.
        """
        claim = self._claims.pop(marker, None)
        if claim is None:
            return "rolled_back"
        complete = True

        issued_token = claim.issued_token
        if issued_token is not None:
            current = self._records.get(issued_token)
            if current is not None:
                if current.issue_marker == marker:
                    del self._records[issued_token]
                else:
                    # Foreign issuance under this token — never touch it.
                    complete = False

        consumed_token = claim.consumed_token
        if consumed_token is not None:
            current = self._records.get(consumed_token)
            if current is None:
                complete = False
            elif not current.consumed:
                # Already restored (or never consumed): safe end state.
                pass
            elif current.consume_marker == marker:
                self._records[consumed_token] = PointerRecord(
                    binding=current.binding,
                    consumed=False,
                    issue_marker=current.issue_marker,
                    consume_marker=None,
                )
            else:
                # Consumed by another attempt — never touch it.
                complete = False

        return "rolled_back" if complete else "incomplete"

    def discard_token_for_capacity(
        self,
        token: str,
        expected_issue_marker: str,
    ) -> CapacityDiscardResult:
        """Retention / capacity forget gated by expected issue marker.

        Public seam used by the process-scoped ledger owner when soft
        capacity is exceeded. Deletes the token **only when** the current
        record's ``issue_marker`` equals ``expected_issue_marker`` — the
        marker the capacity queue remembered at issuance time.

        - ``discarded``: token present under the expected marker; removed.
        - ``absent``: token unknown; no mutation.
        - ``mismatch``: token present under a **different** issue marker
          (foreign owner); **never** deleted. Caller must drop its local
          capacity queue entry only.

        This is **not** a host-transaction rollback. Transaction undo must
        use :meth:`rollback_transition_by_marker`. Capacity drop of a
        discarded token may later surface as ``invalid_cursor`` (accepted
        retention degradation — not a cross-process stale guarantee).
        """
        if not isinstance(expected_issue_marker, str) or not expected_issue_marker:
            raise ValueError("expected_issue_marker must be a non-empty str")
        current = self._records.get(token)
        if current is None:
            return "absent"
        if current.issue_marker != expected_issue_marker:
            return "mismatch"
        del self._records[token]
        return "discarded"

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
# Shared expand tool-view rendering / fit / metering (selection + map scope)
# ---------------------------------------------------------------------------


def render_expand_success_view(
    *,
    renderer: ModelViewRenderer,
    segment: str,
    handle_id: str,
    ordinal: int,
    cursor: str | None,
    summary_more: str,
    summary_done: str,
) -> RenderedModelView:
    """Full expand tool-view for one segment (single untrusted text block).

    Shared by selection-scope and map-scope expanders
    so the model-visible success shape cannot drift between scopes. The
    logical segment text appears exactly once, inside the renderer-minted
    ``role="expand"`` untrusted block. ``cursor`` may be the fixed-length
    placeholder during fit search.
    """
    untrusted = renderer.render_untrusted_article_text(
        handle_id=handle_id,
        ordinal=ordinal,
        role=EXPAND_ROLE,
        text=segment,
    )
    view = ExpandEvidenceToolView(
        status="ok",
        summary=(summary_more if cursor is not None else summary_done),
        next_actions=("expand_evidence",) if cursor is not None else (),
        evidence_handle={"handle_id": handle_id},
        next_cursor=cursor,
        article_text_block=untrusted.text,
    )
    return renderer.render_tool_view(view.model_dump(mode="json"))


def metered_expand_error_outcome(
    *,
    renderer: ModelViewRenderer,
    budget: ModelVisibleTurnBudget,
    status: Literal["invalid_cursor", "stale_evidence"],
    summary: str,
) -> ExpansionOutcome:
    """Render + charge a minimal safe expand error tool-view.

    Shared by both expander scopes. If even the minimal error view cannot
    be charged, falls back to a typed non-model-visible budget-exhausted
    outcome with zero mutation.
    """
    view = ExpandEvidenceToolView(status=status, summary=summary)
    rendered = renderer.render_tool_view(view.model_dump(mode="json"))
    if not budget.can_charge("expand", rendered):
        return ExpansionOutcome(kind="budget_exhausted", model_visible=False)
    try:
        ok = budget.charge("expand", rendered)
    except ModelViewBudgetError:
        return ExpansionOutcome(kind="budget_exhausted", model_visible=False)
    return ExpansionOutcome(
        kind=status,
        model_visible=True,
        rendered_tool_view=rendered,
        charge=ok,
    )


def fit_expand_segment(
    *,
    renderer: ModelViewRenderer,
    budget: ModelVisibleTurnBudget,
    canonical: str,
    next_pos: int,
    ordinal: int,
    handle_id: str,
    summary_more: str,
    summary_done: str,
) -> tuple[str, RenderedModelView, int, bool] | None:
    """Largest codepoint segment whose *complete* tool-view fits expand.

    Shared fit primitive for selection-scope and map-scope expansion.
    Evaluates real candidate views: the terminal candidate (all remaining
    text, ``next_cursor=null``) is checked separately from the with-cursor
    region because the JSON shape — and therefore the serialized cost —
    differs. Never assumes a fixed ``[:2000]`` fits. Returns
    ``(segment, candidate_view, end, has_more)`` or None.
    """
    total = len(canonical)
    remaining = canonical[next_pos:]
    hard_max = min(len(remaining), EVIDENCE_SNIPPET_HARD_CAP)

    # Terminal candidate: everything remaining, cursor null (only when the
    # remaining text is within the snippet hard cap).
    if len(remaining) <= EVIDENCE_SNIPPET_HARD_CAP:
        terminal_view = render_expand_success_view(
            renderer=renderer,
            segment=remaining,
            handle_id=handle_id,
            ordinal=ordinal,
            cursor=None,
            summary_more=summary_more,
            summary_done=summary_done,
        )
        if budget.can_charge("expand", terminal_view):
            return remaining, terminal_view, total, False
        hi = hard_max - 1
    else:
        hi = hard_max

    lo = 1
    best: tuple[str, RenderedModelView, int, bool] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        segment = remaining[:mid]
        end = next_pos + mid  # strictly < total in this region
        view = render_expand_success_view(
            renderer=renderer,
            segment=segment,
            handle_id=handle_id,
            ordinal=ordinal,
            cursor=_CURSOR_PLACEHOLDER,
            summary_more=summary_more,
            summary_done=summary_done,
        )
        if budget.can_charge("expand", view):
            best = (segment, view, end, True)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# ---------------------------------------------------------------------------
# Expansion session (the deep module)
# ---------------------------------------------------------------------------


class EvidenceExpansionSession:
    """Turn-bound controller expanding one injected selection by codepoints.

    Hidden state (never exposed to callers or the model): canonical text,
    next codepoint position, segment ordinal, the binding, and — via the
    shared :class:`ExpansionPointerLedger` — issued/consumed pointers.

    Construction validates the full canonical source against the
    assembler-minted expansion seed (fail-closed, zero mutation before any
    ledger write): injected + registry-backed, handle consistency,
    ``visible_prefix == canonical[:continuation_start]``, registry snippet
    binary equality, fingerprint match, expandability, full char count,
    and full-content digest. Only an ``expandable=True`` selection yields
    a usable :attr:`initial_pointer`.
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
        if selection_result.handle_ref.handle_id != handle_id:
            raise ValueError(
                "selection handle_ref is inconsistent with selection metadata"
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
            raise ValueError("selection handle is not registry-backed")
        if registered.snippet != selection_result.visible_prefix:
            raise ValueError(
                "registry snippet != selection visible_prefix "
                "(binary equality broken)"
            )
        model_chunk = selection_result.model_chunk
        if model_chunk is not None:
            if model_chunk.handle_id != handle_id:
                raise ValueError(
                    "selection model_chunk handle is inconsistent"
                )
            if model_chunk.text != selection_result.visible_prefix:
                raise ValueError(
                    "selection model_chunk text != visible_prefix"
                )

        # Full-source integrity: assembler-minted server-only seed.
        # Same-prefix forgeries (longer, or same length with a replaced
        # suffix) fail closed here — before any ledger write. Error text
        # carries no digest value and no body.
        seed = validate_selection_expansion_seed(selection_result.expansion_seed)
        if seed.handle_id != handle_id:
            raise ValueError("selection expansion seed handle_id mismatch")
        if seed.envelope_fingerprint != envelope_identity.envelope_fingerprint:
            raise ValueError(
                "selection expansion seed envelope_fingerprint mismatch"
            )
        if seed.full_char_count != full_len:
            raise ValueError(
                "selection expansion seed full_char_count mismatch"
            )
        if seed.continuation_start != continuation_start:
            raise ValueError(
                "selection expansion seed continuation_start mismatch"
            )
        if (
            canonical_selection_digest(canonical_selected_text)
            != seed.canonical_digest
        ):
            raise ValueError("selection expansion seed digest mismatch")

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
        # Register the initial pointer under this binding with a marker
        # claim, so a failed construction leaves no orphan ledger record
        # (idempotent rebind for same-binding rebuilds; fail-closed on
        # consumed / foreign binding).
        marker = mint_transition_marker()
        try:
            ledger.issue(token=handle_id, binding=binding, marker=marker)
        except Exception:
            # A rollback that returns incomplete **or raises** is treated
            # as unproven — never propagate raw ledger exception text —
            # and fails closed with a stable initial-pointer code.
            try:
                rollback_status = ledger.rollback_transition_by_marker(marker)
            except Exception:
                rollback_status = "incomplete"
            if rollback_status == "rolled_back":
                raise ValueError(
                    "selection expansion pointer initialization failed"
                ) from None
            raise RuntimeError(
                f"{_EXPAND_ROLLBACK_PREFIX}initial_pointer_issue"
            ) from None

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
        """Delegate to the shared expand success-view renderer."""
        return render_expand_success_view(
            renderer=self._renderer,
            segment=segment,
            handle_id=handle_id,
            ordinal=self._segment_ordinal,
            cursor=cursor,
            summary_more=_EXPAND_SUMMARY_MORE,
            summary_done=_EXPAND_SUMMARY_DONE,
        )

    def _error_outcome(
        self, status: Literal["invalid_cursor", "stale_evidence"]
    ) -> ExpansionOutcome:
        """Delegate to the shared metered expand error outcome."""
        return metered_expand_error_outcome(
            renderer=self._renderer,
            budget=self._budget,
            status=status,
            summary=_ERROR_SUMMARIES[status],
        )

    # -- fit (planning only; can_charge, never mutates) ----------------------

    def _fit_segment(
        self, *, handle_id: str
    ) -> tuple[str, RenderedModelView, int, bool] | None:
        """Delegate to the shared expand segment fit primitive."""
        return fit_expand_segment(
            renderer=self._renderer,
            budget=self._budget,
            canonical=self._canonical,
            next_pos=self._next_pos,
            ordinal=self._segment_ordinal,
            handle_id=handle_id,
            summary_more=_EXPAND_SUMMARY_MORE,
            summary_done=_EXPAND_SUMMARY_DONE,
        )

    # -- compensation --------------------------------------------------------

    def _compensate_after_charge(
        self,
        *,
        charge_cost: int,
        observation: ServerEvidenceObservation,
        marker: str,
    ) -> None:
        """Shared ledger-transition + registry/budget compensation.

        Delegates to
        :func:`evidence_transaction.compensate_ledger_transition_and_observation`
        (``failure_domain="expand_evidence"``) — the same primitive the
        map-scope expander uses, so transition-failure semantics cannot
        drift between seams. Returns only when the ledger rollback is
        proven complete and registry+budget were compensated; otherwise
        raises stable ``expand_evidence_rollback_failed code=...``.
        """
        compensate_ledger_transition_and_observation(
            budget=self._budget,
            account="expand",
            charge_cost=charge_cost,
            registry=self._registry,
            observation=observation,
            ledger=self._ledger,
            marker=marker,
            failure_domain="expand_evidence",
        )

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
            # No ledger transition happened yet: shared registry+budget
            # rollback is the full compensation.
            rollback_charged_observation(
                budget=self._budget,
                account="expand",
                charge_cost=charge_cost,
                registry=self._registry,
                observation=observation,
                failure_domain="expand_evidence",
            )
            raise

        # 6. Commit pointer state as ONE marker-scoped ledger transition
        #    (issue new cursor + consume old pointer), then advance.
        marker = mint_transition_marker()
        try:
            receipt = self._ledger.transition_pointers(
                consume_token=pointer,
                issue_token=cursor_token,
                binding=self._binding,
                marker=marker,
            )
        except Exception:
            # Shared marker-scoped ledger + registry/budget compensation
            # (guards a raising rollback; never skips compensation).
            self._compensate_after_charge(
                charge_cost=charge_cost,
                observation=observation,
                marker=marker,
            )
            raise RuntimeError(
                f"{_EXPAND_ROLLBACK_PREFIX}pointer_transition"
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
            next_cursor=receipt.issued_token,
            model_chunk=model_chunk,
        )


__all__ = [
    "CapacityDiscardResult",
    "EXPAND_ROLE",
    "ExpandScopeKind",
    "ExpansionEnvelopeIdentity",
    "ExpansionOutcome",
    "ExpansionOutcomeKind",
    "ExpansionPointerLedger",
    "EvidenceExpansionSession",
    "PointerBinding",
    "PointerIssueReceipt",
    "PointerRecord",
    "PointerTransitionReceipt",
    "TransitionRollbackStatus",
    "fit_expand_segment",
    "metered_expand_error_outcome",
    "mint_expansion_cursor_id",
    "mint_transition_marker",
    "render_expand_success_view",
]
