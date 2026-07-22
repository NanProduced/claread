"""Selection cost-fit + unique untrusted model-view block (R4-A5-2).

Impact chain
------------
canonical ``selected_text``
  → cost-fit ``visible_prefix`` (serialized ``selection`` account)
  → registry handle ``snippet`` (when registered)
  → ``ModelContextChunk.text`` / renderer-minted untrusted block
  → ``TurnCapabilityProjection.selection`` metadata only
  → server-side ``continuation_start`` (A5-3 expand; not model-visible)

Contract
--------
On a normal inject success path:

    model_chunk.text
      == registry[handle].snippet
      == visible_prefix

Strict Python ``str`` equality. XML escape happens only when
:class:`~app.services.reader_record_ask.model_view_budget.ModelViewRenderer`
renders the untrusted block. That same :class:`RenderedModelView` is the
sole prompt injection string — no second formatter.

Does **not** implement expand tool, cursor, cross-turn binding, map, RAG
scrub, validator migration, or production runtime wiring (A5-3…A5-7).
Does **not** replace live :func:`register_initial_anchor_evidence` behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.reader_record_ask.baseline_context import ModelContextChunk
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    build_server_evidence_observation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    RESERVE_SELECTION,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.turn_capability_projection import (
    SelectionCapabilityView,
)

# Registry / evidence DTO hard ceiling on snippet length (codepoints).
# Cost-fit may return a shorter prefix; never inject past this cap.
EVIDENCE_SNIPPET_HARD_CAP: int = 2000

# Selection untrusted blocks always use ordinal 0 on the A5-2 model-view path.
SELECTION_CHUNK_ORDINAL: int = 0
SELECTION_ROLE: str = "selection"

SelectionModelViewStatus = Literal["absent", "injected", "budget_denied"]


@dataclass(frozen=True, slots=True)
class SelectionModelViewResult:
    """Host-owned selection model-view assembly outcome (offline seam).

    ``continuation_start`` is **server-side only** — never placed on the
    model-visible projection. A5-3 will bind expand tools to this index.
    """

    status: SelectionModelViewStatus
    selection: SelectionCapabilityView
    visible_prefix: str
    full_char_count: int
    continuation_start: int
    model_chunk: ModelContextChunk | None = None
    rendered_untrusted_block: RenderedModelView | None = None
    handle_ref: EvidenceHandleRef | None = None

    @property
    def is_injected(self) -> bool:
        return self.status == "injected"

    @property
    def prompt_block_text(self) -> str | None:
        """Exact renderer output for :func:`build_agent_user_prompt`."""
        if self.rendered_untrusted_block is None:
            return None
        return self.rendered_untrusted_block.text


def fit_selection_prefix(
    *,
    canonical: str,
    handle_id: str,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,
) -> tuple[str, RenderedModelView | None]:
    """Largest codepoint prefix that fits the selection account via renderer cost.

    Uses binary search over prefix length in
    ``[0, min(len(canonical), EVIDENCE_SNIPPET_HARD_CAP)]``.

    Search only calls :meth:`ModelVisibleTurnBudget.can_charge` on
    renderer-minted views — **no** budget mutation. Returns
    ``("", None)`` when even the empty-body tagged block cannot fit.
    """
    if not isinstance(canonical, str):
        raise TypeError("canonical must be str")
    if not handle_id or not isinstance(handle_id, str):
        raise ValueError("handle_id must be a non-empty string")

    hard_max = min(len(canonical), EVIDENCE_SNIPPET_HARD_CAP)
    lo = 0
    hi = hard_max
    best_prefix = ""
    best_view: RenderedModelView | None = None

    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = canonical[:mid]
        view = renderer.render_untrusted_article_text(
            handle_id=handle_id,
            ordinal=SELECTION_CHUNK_ORDINAL,
            role=SELECTION_ROLE,
            text=candidate,
        )
        if budget.can_charge("selection", view):
            best_prefix = candidate
            best_view = view
            lo = mid + 1
        else:
            hi = mid - 1

    return best_prefix, best_view


def assemble_selection_model_view(
    *,
    canonical_selected_text: str | None,
    envelope_fingerprint: str,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer | None = None,
    registry: EvidenceRegistry | None = None,
    unit_id: str | None = None,
    anchor_segment_id: str | None = None,
    text_hash: str | None = None,
    offset_unit: str | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> SelectionModelViewResult:
    """Assemble cost-fit selection model-view for the offline A5-2 seam.

    Parameters
    ----------
    canonical_selected_text:
        Envelope-validated selection body, or ``None`` when absent.
    envelope_fingerprint:
        Registry binding when ``registry`` is provided.
    budget / renderer:
        Host-owned metering seam. Search uses ``can_charge`` only; the
        winning view is ``charge``d exactly once on inject success.
    registry:
        Optional. When provided and inject succeeds, registers
        ``initial_anchor`` with ``snippet == visible_prefix`` and the
        pre-minted handle id (binary equality).
    unit_id / anchor_segment_id / text_hash / offsets:
        Optional **server-side** locator material for the registry only.
        Never enters the model chunk, rendered block, or projection.

    Returns
    -------
    SelectionModelViewResult
        ``absent`` when no selection; ``injected`` on success; 
        ``budget_denied`` when no non-empty prefix fits (no mutation).
    """
    active_renderer = renderer if renderer is not None else ModelViewRenderer()

    if canonical_selected_text is None:
        return SelectionModelViewResult(
            status="absent",
            selection=SelectionCapabilityView(present=False),
            visible_prefix="",
            full_char_count=0,
            continuation_start=0,
        )

    if not isinstance(canonical_selected_text, str):
        raise TypeError("canonical_selected_text must be str or None")
    if not canonical_selected_text:
        # Envelope contract normally forbids empty selected_text; treat as absent.
        return SelectionModelViewResult(
            status="absent",
            selection=SelectionCapabilityView(present=False),
            visible_prefix="",
            full_char_count=0,
            continuation_start=0,
        )

    full_len = len(canonical_selected_text)
    handle_id = mint_evidence_handle_id()

    # Cost-fit search: can_charge only — budget unchanged on denial paths.
    visible_prefix, fitted_view = fit_selection_prefix(
        canonical=canonical_selected_text,
        handle_id=handle_id,
        budget=budget,
        renderer=active_renderer,
    )

    if not visible_prefix or fitted_view is None:
        return SelectionModelViewResult(
            status="budget_denied",
            selection=SelectionCapabilityView(
                present=True,
                handle_id=None,
                expandable=True,
                visible_char_count=0,
                full_char_count=full_len,
            ),
            visible_prefix="",
            full_char_count=full_len,
            continuation_start=0,
        )

    # Final charge of the **same** renderer-minted view found by search.
    budget.charge("selection", fitted_view)

    model_chunk = ModelContextChunk(
        handle_id=handle_id,
        chunk_ordinal=SELECTION_CHUNK_ORDINAL,
        text=visible_prefix,
    )

    handle_ref: EvidenceHandleRef | None = None
    if registry is not None:
        if registry.envelope_fingerprint != envelope_fingerprint:
            raise ValueError(
                "evidence registry fingerprint does not match envelope fingerprint"
            )
        locator_summary: dict[str, Any] | None = None
        if unit_id is not None or anchor_segment_id is not None:
            locator_summary = {
                "mode": "initial_anchor",
                "untrusted": True,
            }
            if unit_id is not None:
                locator_summary["unit_id"] = unit_id
            if anchor_segment_id is not None:
                locator_summary["anchor_segment_id"] = anchor_segment_id
            if offset_unit is not None:
                locator_summary["offset_unit"] = offset_unit
            if start_offset is not None:
                locator_summary["start_offset"] = start_offset
            if end_offset is not None:
                locator_summary["end_offset"] = end_offset
            if text_hash is not None:
                locator_summary["text_hash"] = text_hash

        observation = build_server_evidence_observation(
            kind="initial_anchor",
            envelope_fingerprint=envelope_fingerprint,
            source_tool="initial_anchor",
            # Binary equality: snippet == model_chunk.text == visible_prefix
            snippet=visible_prefix,
            locator_summary=locator_summary,
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            handle_id=handle_id,
        )
        handle_ref = registry.register(observation)
        registered = registry.get(handle_id)
        if registered is None or registered.snippet != model_chunk.text:
            raise RuntimeError(
                "selection model-view binary equality broken after registry register"
            )

    if model_chunk.text != visible_prefix:
        raise RuntimeError("selection model-view binary equality broken for chunk")

    expandable = full_len > len(visible_prefix)
    continuation_start = len(visible_prefix)

    return SelectionModelViewResult(
        status="injected",
        selection=SelectionCapabilityView(
            present=True,
            handle_id=handle_id,
            expandable=expandable,
            visible_char_count=len(visible_prefix),
            full_char_count=full_len,
        ),
        visible_prefix=visible_prefix,
        full_char_count=full_len,
        continuation_start=continuation_start,
        model_chunk=model_chunk,
        rendered_untrusted_block=fitted_view,
        handle_ref=handle_ref,
    )


def assert_selection_binary_equality(
    result: SelectionModelViewResult,
    *,
    registry: EvidenceRegistry | None = None,
) -> None:
    """Raise ``AssertionError`` unless inject-path binary equality holds."""
    if result.status != "injected":
        raise AssertionError(f"binary equality requires injected status, got {result.status}")
    if result.model_chunk is None:
        raise AssertionError("injected result missing model_chunk")
    if result.model_chunk.text != result.visible_prefix:
        raise AssertionError("model_chunk.text != visible_prefix")
    if result.selection.handle_id is None:
        raise AssertionError("injected result missing handle_id")
    if result.model_chunk.handle_id != result.selection.handle_id:
        raise AssertionError("chunk handle_id != selection metadata handle_id")
    if registry is not None:
        obs = registry.get(result.selection.handle_id)
        if obs is None:
            raise AssertionError("handle not in registry")
        if obs.snippet != result.visible_prefix:
            raise AssertionError("registry snippet != visible_prefix")
        if obs.snippet != result.model_chunk.text:
            raise AssertionError("registry snippet != model_chunk.text")


# Re-export reserve constant for tests / call-sites (single source).
__all__ = [
    "EVIDENCE_SNIPPET_HARD_CAP",
    "RESERVE_SELECTION",
    "SELECTION_CHUNK_ORDINAL",
    "SELECTION_ROLE",
    "SelectionModelViewResult",
    "SelectionModelViewStatus",
    "assemble_selection_model_view",
    "assert_selection_binary_equality",
    "fit_selection_prefix",
]
