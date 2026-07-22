"""Semantic article map model-view (R4-A5-4, offline core).

Deep module for the article map: projection metadata + a single
renderer-minted ``<untrusted_article_map>`` block + opaque server-bound
cursors that only ``expand_evidence`` may use (design TMP §18.2).

Contracts
---------
- Projection stays metadata-only: ``article_map = {present, entry_count,
  truncated}`` — never labels, article text, locators, offsets, identity,
  or provenance. The model sees labels **only** inside the untrusted map
  block (XML-escaped via the unified renderer; charged to the map account
  at the block's real serialized cost — never guessed from entry counts or
  label lengths).
- Label derivation is deterministic (no server-side LLM, keyword
  classifier, or free summary): canonical ``heading`` → deterministic
  ``main_reading_text`` first-sentence/prefix → ordinal fallback that
  explicitly states its limited navigation.
- Map entries are **never evidence**: no entry registers a citeable
  handle; only text produced by successfully expanding a map cursor mints
  and registers an evidence handle (``source_tool="map_expand"``).
- Cursors are ``cur_<32 hex>``, bound to ``scope_kind="map"`` plus the
  full envelope identity, issued into the shared
  :class:`ExpansionPointerLedger`. A selection-scope expander answers a
  map cursor with ``stale_evidence`` (binding mismatch) and vice versa.
- Assembly is one host transaction: fit (can_charge only) → charge("map",
  real block) → issue cursors under per-cursor markers; any issuance
  failure revokes exactly this assembly's cursors and refunds the charge,
  fail-closed with stable codes on unproven rollback.
- The map-scope :class:`ArticleMapExpander` reuses the shared expansion
  primitives (renderer / budget / registry / ledger / transaction
  compensation) — no second transaction implementation.

Zero I/O: no document-access seam, RAG port, DB, runtime, SSE, or real
model. The caller supplies server-owned entry sources as data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.services.reader_record_ask.baseline_context import ModelContextChunk
from app.services.reader_record_ask.evidence import (
    build_server_evidence_observation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_expansion import (
    ExpansionEnvelopeIdentity,
    ExpansionOutcome,
    ExpansionPointerLedger,
    PointerBinding,
    fit_expand_segment,
    metered_expand_error_outcome,
    mint_expansion_cursor_id,
    mint_transition_marker,
    render_expand_success_view,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.evidence_transaction import (
    compensate_ledger_transition_and_observation,
    rollback_charged_observation,
)
from app.services.reader_record_ask.model_view_budget import (
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.tool_contracts import (
    is_expand_pointer_shape,
)

MapEntryKind = Literal["heading", "window", "ordinal"]

MapAssembleStatus = Literal["ok", "absent", "budget_denied"]

# Hard cap on one resolved label before cost-fit clipping (codepoints).
MAP_LABEL_HARD_CAP: int = 120

# Ordinal fallback must not pretend to offer semantic navigation.
MAP_ORDINAL_NAVIGATION_NOTE: str = "ordinal only — limited navigation"

# Request-frame-owned fixed chrome around the map untrusted block (mirrors
# the selection section chrome; A5-7 request_frame metering must include
# these constants). Never unowned model-visible characters.
MAP_SECTION_HEADER: str = "\n## Article map (untrusted navigation)\n"
MAP_SECTION_FOOTER: str = "\n"

# Fixed model-visible summaries for map-scope expansion.
_MAP_SUMMARY_MORE = (
    "Article map entry segment expanded. More text remains for this "
    "entry; call expand_evidence with the returned cursor."
)
_MAP_SUMMARY_DONE = (
    "Article map entry segment expanded. The full entry text is now "
    "visible; no cursor remains."
)

_ERROR_SUMMARIES: dict[str, str] = {
    "invalid_cursor": (
        "Unknown, malformed, or already-used expansion pointer. "
        "No text was added."
    ),
    "stale_evidence": (
        "Expansion pointer does not match this turn's verified context. "
        "No text was added."
    ),
}

# Stable failure codes — never embed body, repr, or raw exception text.
_MAP_ROLLBACK_PREFIX = "article_map_rollback_failed code="
_MAP_ASSEMBLY_FAILED_PREFIX = "article_map_assembly_failed code="
_MAP_EXPAND_ROLLBACK_PREFIX = "map_expand_rollback_failed code="
_MAP_EXPAND_FAILED_PREFIX = "map_expand_failed code="

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?.\n]+")

# Fixed-length cursor placeholder for fit candidates (never issued).
_CURSOR_PLACEHOLDER = f"cur_{'0' * 32}"

# Module-private brand for map prompt capabilities (not a sandbox).
_ARTICLE_MAP_PROMPT_ORIGIN: object = object()

_ARTICLE_MAP_PROMPT_TYPE_ERROR = (
    "article map prompt requires ArticleMapPromptCapability "
    "from assemble_article_map"
)


# ---------------------------------------------------------------------------
# Sources / entries / prompt capability
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleMapEntrySource:
    """Server-owned source material for one map entry.

    Carries article-derived text only — never locators, offsets, identity,
    or provenance. ``heading`` is the canonical heading (preferred label);
    ``window_text`` is the deterministic ``main_reading_text`` window the
    cursor expands (and the label fallback source).
    """

    heading: str | None = None
    window_text: str | None = None


@dataclass(frozen=True, slots=True)
class ArticleMapEntry:
    """One resolved map entry (server-side; label is untrusted text)."""

    kind: MapEntryKind
    label: str
    cursor: str
    # Expandable window text; None for entries without a semantic source
    # (ordinal fallback) — their cursors are structurally valid but not
    # expandable (safe invalid_cursor on use).
    window_text: str | None = None


@dataclass(frozen=True, slots=True)
class ArticleMapPromptCapability:
    """Assembler-minted map prompt capability for the prompt builder.

    Hand construction yields an unusable capability (origin unset);
    module-boundary brand only. ``section_text`` already includes the
    request_frame-owned chrome plus the map-account untrusted block.
    """

    section_text: str
    untrusted_block_text: str
    entry_count: int
    truncated: bool
    _origin: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )


def validate_article_map_prompt_capability(
    capability: object,
) -> ArticleMapPromptCapability:
    """Non-metering origin check for the prompt builder."""
    if not isinstance(capability, ArticleMapPromptCapability):
        raise TypeError(_ARTICLE_MAP_PROMPT_TYPE_ERROR)
    if getattr(capability, "_origin", None) is not _ARTICLE_MAP_PROMPT_ORIGIN:
        raise TypeError(_ARTICLE_MAP_PROMPT_TYPE_ERROR)
    return capability


def _mint_article_map_prompt_capability(
    *,
    rendered_block: RenderedModelView,
    entry_count: int,
    truncated: bool,
) -> ArticleMapPromptCapability:
    capability = ArticleMapPromptCapability(
        section_text=MAP_SECTION_HEADER
        + rendered_block.text
        + MAP_SECTION_FOOTER,
        untrusted_block_text=rendered_block.text,
        entry_count=entry_count,
        truncated=truncated,
    )
    object.__setattr__(capability, "_origin", _ARTICLE_MAP_PROMPT_ORIGIN)
    return capability


# ---------------------------------------------------------------------------
# Deterministic label derivation (no LLM / keyword classifier)
# ---------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    """Deterministic first non-empty sentence / line of ``text``."""
    stripped = text.strip()
    for part in _SENTENCE_SPLIT_RE.split(stripped):
        candidate = part.strip()
        if candidate:
            return candidate
    return stripped


def _resolve_full_label(
    source: ArticleMapEntrySource, *, index: int
) -> tuple[MapEntryKind, str]:
    """Label fallback order per §18.2: heading → window prefix → ordinal."""
    heading = (source.heading or "").strip()
    if heading:
        return "heading", heading[:MAP_LABEL_HARD_CAP]
    window = (source.window_text or "").strip()
    if window:
        return "window", _first_sentence(window)[:MAP_LABEL_HARD_CAP]
    return "ordinal", f"Section {index} ({MAP_ORDINAL_NAVIGATION_NOTE})"


# ---------------------------------------------------------------------------
# Result + expander
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleMapResult:
    """Host-owned article map assembly outcome.

    ``entries`` and the expander's cursor→window state are server-only;
    the model-visible surface is exactly ``rendered_block`` (inside the
    prompt capability) plus the metadata projection fields
    ``entry_count`` / ``truncated``.
    """

    status: MapAssembleStatus
    rendered_block: RenderedModelView | None = None
    prompt_capability: ArticleMapPromptCapability | None = None
    entries: tuple[ArticleMapEntry, ...] = ()
    entry_count: int = 0
    truncated: bool = False
    expander: ArticleMapExpander | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class _MapCursorState:
    """Server-only per-cursor continuation state."""

    window_text: str
    next_pos: int
    ordinal: int


class ArticleMapExpander:
    """Map-scope expander: opaque cursor → entry window text → evidence.

    Shares the expansion primitives with the selection-scope session
    (renderer, budget, registry, ledger, shared transaction compensation).
    Cursors map to server-owned window text; callers pass only the opaque
    pointer — never source text, locators, offsets, fingerprints, turn
    ids, or generations.
    """

    def __init__(
        self,
        *,
        binding: PointerBinding,
        ledger: ExpansionPointerLedger,
        registry: EvidenceRegistry,
        budget: ModelVisibleTurnBudget,
        renderer: ModelViewRenderer,
        cursor_state: dict[str, _MapCursorState],
    ) -> None:
        if binding.scope_kind != "map":
            raise ValueError("map expander requires a map-scope binding")
        self._binding = binding
        self._ledger = ledger
        self._registry = registry
        self._budget = budget
        self._renderer = renderer
        self._cursor_state = dict(cursor_state)

    @property
    def binding(self) -> PointerBinding:
        return self._binding

    def _error_outcome(
        self, status: Literal["invalid_cursor", "stale_evidence"]
    ) -> ExpansionOutcome:
        return metered_expand_error_outcome(
            renderer=self._renderer,
            budget=self._budget,
            status=status,
            summary=_ERROR_SUMMARIES[status],
        )

    def expand(self, *, pointer: str) -> ExpansionOutcome:
        """Expand one map entry window by one cost-fit segment."""
        # 1. Resolve: shape → ledger → binding (scope-aware) → consumed →
        #    session-known cursor state.
        if not is_expand_pointer_shape(pointer):
            return self._error_outcome("invalid_cursor")
        record = self._ledger.lookup(pointer)
        if record is None:
            return self._error_outcome("invalid_cursor")
        if record.binding != self._binding:
            return self._error_outcome("stale_evidence")
        if record.consumed:
            return self._error_outcome("invalid_cursor")
        state = self._cursor_state.get(pointer)
        if state is None or state.next_pos >= len(state.window_text):
            # Unknown to this session, or an ordinal entry without an
            # expandable source: safe invalid state.
            return self._error_outcome("invalid_cursor")

        # 2. Full-tool-view cost fit (can_charge only, no mutation).
        handle_id = mint_evidence_handle_id()
        fitted = fit_expand_segment(
            renderer=self._renderer,
            budget=self._budget,
            canonical=state.window_text,
            next_pos=state.next_pos,
            ordinal=state.ordinal,
            handle_id=handle_id,
            summary_more=_MAP_SUMMARY_MORE,
            summary_done=_MAP_SUMMARY_DONE,
        )
        if fitted is None:
            return ExpansionOutcome(kind="budget_exhausted", model_visible=False)
        segment, _candidate_view, end, has_more = fitted

        # 3. Prospective observation / handle / cursor state (pure builds).
        observation = build_server_evidence_observation(
            kind="observation",
            envelope_fingerprint=self._binding.envelope_fingerprint,
            source_tool="map_expand",
            snippet=segment,
            handle_id=handle_id,
        )
        cursor_token = mint_expansion_cursor_id() if has_more else None
        final_view = render_expand_success_view(
            renderer=self._renderer,
            segment=segment,
            handle_id=handle_id,
            ordinal=state.ordinal,
            cursor=cursor_token,
            summary_more=_MAP_SUMMARY_MORE,
            summary_done=_MAP_SUMMARY_DONE,
        )
        if not self._budget.can_charge("expand", final_view):
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
                    f"{_MAP_EXPAND_FAILED_PREFIX}postcondition"
                )
        except Exception:
            # No ledger transition yet: shared registry+budget rollback.
            rollback_charged_observation(
                budget=self._budget,
                account="expand",
                charge_cost=charge_cost,
                registry=self._registry,
                observation=observation,
                failure_domain="map_expand",
            )
            raise

        # 6. Commit: single marker-scoped ledger transition + state advance.
        marker = mint_transition_marker()
        try:
            receipt = self._ledger.transition_pointers(
                consume_token=pointer,
                issue_token=cursor_token,
                binding=self._binding,
                marker=marker,
            )
        except Exception:
            compensate_ledger_transition_and_observation(
                budget=self._budget,
                account="expand",
                charge_cost=charge_cost,
                registry=self._registry,
                observation=observation,
                ledger=self._ledger,
                marker=marker,
                failure_domain="map_expand",
            )
            raise RuntimeError(
                f"{_MAP_EXPAND_ROLLBACK_PREFIX}pointer_transition"
            ) from None

        # State advance only after a proven commit.
        del self._cursor_state[pointer]
        if cursor_token is not None:
            self._cursor_state[cursor_token] = _MapCursorState(
                window_text=state.window_text,
                next_pos=end,
                ordinal=state.ordinal + 1,
            )

        model_chunk = ModelContextChunk(
            handle_id=handle_id,
            chunk_ordinal=state.ordinal,
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


# ---------------------------------------------------------------------------
# Assembly (the map host transaction)
# ---------------------------------------------------------------------------


def assemble_article_map(
    *,
    entry_sources: Sequence[ArticleMapEntrySource],
    envelope_identity: ExpansionEnvelopeIdentity,
    registry: EvidenceRegistry,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer | None = None,
    pointer_ledger: ExpansionPointerLedger | None = None,
) -> ArticleMapResult:
    """Assemble the cost-fit, server-bound article map (offline).

    Fits the **real** serialized block under ``RESERVE_MAP`` (max entries
    first, then max label length), charges it once, and issues one opaque
    cursor per entry into the shared ledger under ``scope_kind="map"``.
    Any cursor-issuance failure revokes exactly this assembly's cursors
    and refunds the charge — fail-closed with stable codes when rollback
    cannot be proven.

    Zero I/O: entry sources are caller-supplied server-owned data.
    """
    active_renderer = renderer if renderer is not None else ModelViewRenderer()

    if not entry_sources:
        return ArticleMapResult(status="absent")
    for source in entry_sources:
        if not isinstance(source, ArticleMapEntrySource):
            raise TypeError(
                "entry_sources must be ArticleMapEntrySource instances"
            )

    if registry.envelope_fingerprint != (
        envelope_identity.envelope_fingerprint
    ):
        raise ValueError(
            "evidence registry fingerprint does not match envelope "
            "fingerprint"
        )

    binding = PointerBinding(
        turn_id=envelope_identity.turn_id,
        envelope_fingerprint=envelope_identity.envelope_fingerprint,
        record_generation=envelope_identity.record_generation,
        base_id=envelope_identity.base_id,
        reading_record_id=envelope_identity.reading_record_id,
        scope_kind="map",
    )

    total = len(entry_sources)
    full_labels: list[tuple[MapEntryKind, str]] = [
        _resolve_full_label(source, index=index + 1)
        for index, source in enumerate(entry_sources)
    ]

    def _render_candidate(entry_count: int, label_len: int) -> RenderedModelView:
        candidate_entries = []
        for index in range(entry_count):
            kind, full_label = full_labels[index]
            candidate_entries.append(
                {
                    "cursor": _CURSOR_PLACEHOLDER,
                    "kind": kind,
                    "label": full_label[:label_len],
                }
            )
        return active_renderer.render_untrusted_article_map(
            entries=candidate_entries
        )

    # Fit: maximize entries first, then label length — both monotonic in
    # real serialized cost. Never a fixed entry count / label length guess.
    max_label_len = max(len(full_label) for _, full_label in full_labels)
    best_count = 0
    lo, hi = 1, total
    while lo <= hi:
        mid = (lo + hi) // 2
        if budget.can_charge("map", _render_candidate(mid, 1)):
            best_count = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best_count == 0:
        return ArticleMapResult(status="budget_denied")

    best_label_len = 0
    lo, hi = 1, max_label_len
    while lo <= hi:
        mid = (lo + hi) // 2
        if budget.can_charge("map", _render_candidate(best_count, mid)):
            best_label_len = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best_label_len == 0:
        return ArticleMapResult(status="budget_denied")

    # Prospective entries with real cursors (same 36-char shape as the
    # fit placeholders, so the fitted cost is exact; the final block is
    # re-checked below).
    cursors = [mint_expansion_cursor_id() for _ in range(best_count)]
    entries: list[ArticleMapEntry] = []
    for index in range(best_count):
        kind, full_label = full_labels[index]
        label = full_label[:best_label_len]
        source = entry_sources[index]
        window = (source.window_text or "").strip() or None
        entries.append(
            ArticleMapEntry(
                kind=kind,
                label=label,
                cursor=cursors[index],
                window_text=window,
            )
        )
    final_block = active_renderer.render_untrusted_article_map(
        entries=[
            {"cursor": entry.cursor, "kind": entry.kind, "label": entry.label}
            for entry in entries
        ]
    )
    if not budget.can_charge("map", final_block):
        return ArticleMapResult(status="budget_denied")

    truncated = best_count < total or any(
        entry.label != full_labels[index][1] for index, entry in enumerate(entries)
    )

    # Commit: charge once → issue cursors under per-cursor markers.
    try:
        charge_ok = budget.charge("map", final_block)
    except ModelViewBudgetError:
        return ArticleMapResult(status="budget_denied")
    charge_cost = charge_ok.cost

    ledger = pointer_ledger if pointer_ledger is not None else (
        ExpansionPointerLedger()
    )
    issued_markers: list[str] = []
    try:
        for cursor in cursors:
            marker = mint_transition_marker()
            # Track BEFORE issuing: a write-then-raise implementation must
            # still leave a marker the compensation loop can roll back.
            issued_markers.append(marker)
            ledger.issue(token=cursor, binding=binding, marker=marker)
    except Exception:
        # Revoke exactly this assembly's issued cursors (marker-scoped),
        # then refund the map charge. Fail closed with stable codes.
        ledger_complete = True
        for issued_marker in issued_markers:
            try:
                if (
                    ledger.rollback_transition_by_marker(issued_marker)
                    != "rolled_back"
                ):
                    ledger_complete = False
            except Exception:
                ledger_complete = False
        try:
            budget._refund_chars("map", charge_cost)
        except Exception:
            if not ledger_complete:
                raise RuntimeError(
                    f"{_MAP_ROLLBACK_PREFIX}ledger_and_budget"
                ) from None
            raise RuntimeError(
                f"{_MAP_ROLLBACK_PREFIX}budget_refund"
            ) from None
        if not ledger_complete:
            raise RuntimeError(f"{_MAP_ROLLBACK_PREFIX}ledger") from None
        raise RuntimeError(
            f"{_MAP_ASSEMBLY_FAILED_PREFIX}cursor_issue"
        ) from None

    cursor_state = {
        entry.cursor: _MapCursorState(
            window_text=entry.window_text,
            next_pos=0,
            ordinal=1,
        )
        for entry in entries
        if entry.window_text is not None
    }
    expander = ArticleMapExpander(
        binding=binding,
        ledger=ledger,
        registry=registry,
        budget=budget,
        renderer=active_renderer,
        cursor_state=cursor_state,
    )
    prompt_capability = _mint_article_map_prompt_capability(
        rendered_block=final_block,
        entry_count=best_count,
        truncated=truncated,
    )

    return ArticleMapResult(
        status="ok",
        rendered_block=final_block,
        prompt_capability=prompt_capability,
        entries=tuple(entries),
        entry_count=best_count,
        truncated=truncated,
        expander=expander,
    )


__all__ = [
    "MAP_LABEL_HARD_CAP",
    "MAP_ORDINAL_NAVIGATION_NOTE",
    "MAP_SECTION_FOOTER",
    "MAP_SECTION_HEADER",
    "ArticleMapEntry",
    "ArticleMapEntrySource",
    "ArticleMapExpander",
    "ArticleMapPromptCapability",
    "ArticleMapResult",
    "MapAssembleStatus",
    "MapEntryKind",
    "assemble_article_map",
    "validate_article_map_prompt_capability",
]
