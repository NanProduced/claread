"""Selection cost-fit + unique untrusted model-view block (R4-A5-2 / A5-2R).

Atomicity (assembler · registry · budget · prompt)
--------------------------------------------------
**Success (status=injected)** is one host transaction:

1. preflight registry fingerprint + build observation (no budget mutation);
2. cost-fit search via ``can_charge`` only (no mutation);
3. single ``charge("selection", fitted_view)`` of the renderer-minted block;
4. single ``registry.register(observation)`` with the same handle/snippet;
5. post-condition checks (handle_ref, snippet equality);
6. mint :class:`SelectionPromptCapability` branded for the prompt builder.

Any failure **after charge and before successful return** runs a single
compensation path:

- ``registry.discard_if_matches(handle_id, expected=this_observation)`` —
  removes **only** this call's observation when still present and equal;
  never deletes pre-existing / foreign entries;
- ``budget._refund_chars("selection", cost)`` — restore selection spend.

If registry compensation itself cannot complete safely (``mismatch`` or
unexpected error), fail closed with a stable rollback error code — do **not**
silently claim a clean rollback. Error text never embeds selection body.

**Failure paths**

- ``absent``: no registry required; no budget / registry mutation.
- ``budget_denied``: registry required for non-empty selection but unused;
  fit search only ``can_charge``; no charge, no observation, no handle.
- ``registry is None`` / fingerprint mismatch / observation build error:
  raise before charge; no mutation.
- register write-then-raise / wrong handle_ref / postcondition / capability
  mint failure after charge: full compensate (budget + this observation).

**Prompt** accepts only assembler-minted :class:`SelectionPromptCapability`
(not raw ``str``, not generic ``RenderedModelView`` / ``render_plain``).

Cost ownership of selection section chrome
------------------------------------------
- Untrusted ``<untrusted_article_text role="selection">`` block →
  **selection** account (charged once via renderer).
- Fixed section header/footer chrome around that block → **request_frame**
  fixed surface (constant strings; A5-7 request_frame metering must include
  them). Never unowned model-visible characters.

Does **not** implement expand/map/RAG/validator/production wiring (A5-3…7).
Does **not** replace live :func:`register_initial_anchor_evidence` behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.reader_record_ask.baseline_context import ModelContextChunk
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
    build_server_evidence_observation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    RESERVE_SELECTION,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
    is_renderer_minted_view,
)
from app.services.reader_record_ask.turn_capability_projection import (
    SelectionCapabilityView,
)

# Registry / evidence DTO hard ceiling on snippet length (codepoints).
EVIDENCE_SNIPPET_HARD_CAP: int = 2000

SELECTION_CHUNK_ORDINAL: int = 0
SELECTION_ROLE: str = "selection"

# Request-frame-owned fixed chrome around the selection untrusted block.
# Included in SelectionPromptCapability.section_text so the prompt builder
# never invents unowned model-visible characters. A5-7 request_frame charge
# must account for these constants.
SELECTION_SECTION_HEADER: str = "\n## Untrusted article context (selection)\n"
SELECTION_SECTION_FOOTER: str = "\n"

SelectionModelViewStatus = Literal["absent", "injected", "budget_denied"]

# Module-private brand for selection prompt capabilities (not a sandbox).
_SELECTION_PROMPT_ORIGIN: object = object()

_SELECTION_PROMPT_TYPE_ERROR = (
    "selection prompt requires SelectionPromptCapability "
    "from assemble_selection_model_view"
)

_REGISTRY_REQUIRED_ERROR = (
    "assemble_selection_model_view requires a non-None EvidenceRegistry "
    "for non-empty selection (injected selection must be registry-backed)"
)

# Stable rollback / inject failure codes — never embed selection body.
_ROLLBACK_FAILED_PREFIX = "selection_inject_rollback_failed code="
_INJECT_FAILED_PREFIX = "selection_inject_failed code="


# ---------------------------------------------------------------------------
# Selection-specific prompt capability (assembler-minted only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionPromptCapability:
    """Assembler-minted selection injection capability for the prompt builder.

    Not a generic :class:`RenderedModelView`. Hand construction yields an
    unusable capability (origin unset). Module-boundary brand only.
    """

    # Full section inserted into the user prompt (request_frame chrome +
    # selection untrusted block). Prompt builder uses this string as-is.
    section_text: str
    # Exact renderer-minted untrusted block body (no section chrome).
    untrusted_block_text: str
    handle_id: str
    # char_cost of the untrusted block already charged to selection.
    selection_block_char_cost: int
    _origin: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )


def _mint_selection_prompt_capability(
    *,
    fitted_view: RenderedModelView,
    handle_id: str,
) -> SelectionPromptCapability:
    if not is_renderer_minted_view(fitted_view):
        raise TypeError(
            "selection prompt capability requires a renderer-minted untrusted block"
        )
    if not handle_id:
        raise ValueError("handle_id must be non-empty")
    # Guard: block must be the selection role surface (not render_plain).
    if 'role="selection"' not in fitted_view.text:
        raise TypeError(
            "selection prompt capability requires a selection-role untrusted block"
        )
    section_text = (
        SELECTION_SECTION_HEADER + fitted_view.text + SELECTION_SECTION_FOOTER
    )
    cap = SelectionPromptCapability(
        section_text=section_text,
        untrusted_block_text=fitted_view.text,
        handle_id=handle_id,
        selection_block_char_cost=fitted_view.char_cost,
    )
    object.__setattr__(cap, "_origin", _SELECTION_PROMPT_ORIGIN)
    return cap


def validate_selection_prompt_capability(
    capability: object,
) -> SelectionPromptCapability:
    """Non-metering origin check for the prompt builder.

    Rejects raw strings, generic RenderedModelView, hand-forged capabilities,
    and anything not minted by :func:`assemble_selection_model_view`.
    """
    if not isinstance(capability, SelectionPromptCapability):
        raise TypeError(_SELECTION_PROMPT_TYPE_ERROR)
    if getattr(capability, "_origin", None) is not _SELECTION_PROMPT_ORIGIN:
        raise TypeError(_SELECTION_PROMPT_TYPE_ERROR)
    return capability


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionModelViewResult:
    """Host-owned selection model-view assembly outcome.

    For ``status="injected"`` the following are guaranteed non-None and
    registry-backed:

    - ``handle_ref``, ``model_chunk``, ``rendered_untrusted_block``,
      ``prompt_capability``
    - binary equality:
      ``model_chunk.text == registry[handle].snippet == visible_prefix``

    ``continuation_start`` is server-side only (A5-3); never model-visible.
    """

    status: SelectionModelViewStatus
    selection: SelectionCapabilityView
    visible_prefix: str
    full_char_count: int
    continuation_start: int
    model_chunk: ModelContextChunk | None = None
    rendered_untrusted_block: RenderedModelView | None = None
    handle_ref: EvidenceHandleRef | None = None
    prompt_capability: SelectionPromptCapability | None = None

    @property
    def is_injected(self) -> bool:
        return self.status == "injected"


# ---------------------------------------------------------------------------
# Pure fit (planning only — never produces citeable handles)
# ---------------------------------------------------------------------------


def fit_selection_prefix(
    *,
    canonical: str,
    handle_id: str,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,
) -> tuple[str, RenderedModelView | None]:
    """Largest codepoint prefix that fits the selection account via renderer cost.

    Pure planning helper: only ``can_charge``; **no** budget mutation, **no**
    registry write, **no** citeable handle minting for injection.

    This is **not** a construct entry for model-visible / citeable selection.
    Use :func:`assemble_selection_model_view` for inject/cite paths.
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


# ---------------------------------------------------------------------------
# Inject / citeable assembly (registry required for non-empty selection)
# ---------------------------------------------------------------------------


def _build_locator_summary(
    *,
    unit_id: str | None,
    anchor_segment_id: str | None,
    text_hash: str | None,
    offset_unit: str | None,
    start_offset: int | None,
    end_offset: int | None,
) -> dict[str, Any] | None:
    if unit_id is None and anchor_segment_id is None:
        return None
    locator_summary: dict[str, Any] = {
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
    return locator_summary


def _preflight_observation(
    *,
    registry: EvidenceRegistry,
    envelope_fingerprint: str,
    handle_id: str,
    visible_prefix: str,
    unit_id: str | None,
    anchor_segment_id: str | None,
    text_hash: str | None,
    offset_unit: str | None,
    start_offset: int | None,
    end_offset: int | None,
) -> ServerEvidenceObservation:
    """Build + validate observation before any budget mutation."""
    if registry.envelope_fingerprint != envelope_fingerprint:
        raise ValueError(
            "evidence registry fingerprint does not match envelope fingerprint"
        )
    if registry.get(handle_id) is not None:
        raise ValueError(f"duplicate evidence handle_id: {handle_id}")

    locator_summary = _build_locator_summary(
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        text_hash=text_hash,
        offset_unit=offset_unit,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    # Pydantic validation happens here — before charge.
    return build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=envelope_fingerprint,
        source_tool="initial_anchor",
        snippet=visible_prefix,
        locator_summary=locator_summary,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        handle_id=handle_id,
    )


def _compensate_after_charge(
    *,
    budget: ModelVisibleTurnBudget,
    charge_cost: int,
    registry: EvidenceRegistry,
    observation: ServerEvidenceObservation,
) -> None:
    """Roll back selection charge + this call's registry write (if any).

    Must be called only after a successful ``charge`` for this inject attempt.
    Fail-closed: incomplete compensation raises a stable-code RuntimeError
    that never embeds selection body text.
    """
    handle_id = observation.handle.handle_id
    discard_outcome: str
    try:
        discard_outcome = registry.discard_if_matches(
            handle_id=handle_id,
            expected=observation,
        )
    except Exception:
        # Attempt budget refund anyway; still report dual failure without
        # chaining raw exception text that might carry probe payloads.
        try:
            budget._refund_chars("selection", charge_cost)
        except Exception:
            raise RuntimeError(
                f"{_ROLLBACK_FAILED_PREFIX}registry_and_budget"
            ) from None
        raise RuntimeError(f"{_ROLLBACK_FAILED_PREFIX}registry_discard") from None

    if discard_outcome == "mismatch":
        # Foreign entry under our handle — must not delete; still refund budget.
        try:
            budget._refund_chars("selection", charge_cost)
        except Exception:
            raise RuntimeError(
                f"{_ROLLBACK_FAILED_PREFIX}registry_mismatch_and_budget"
            ) from None
        raise RuntimeError(f"{_ROLLBACK_FAILED_PREFIX}registry_mismatch") from None

    # discarded | absent: no residual for *this* observation under handle_id.
    residual = registry.get(handle_id)
    if residual is not None and residual == observation:
        try:
            budget._refund_chars("selection", charge_cost)
        except Exception:
            raise RuntimeError(
                f"{_ROLLBACK_FAILED_PREFIX}registry_residual_and_budget"
            ) from None
        raise RuntimeError(f"{_ROLLBACK_FAILED_PREFIX}registry_residual") from None

    try:
        budget._refund_chars("selection", charge_cost)
    except Exception:
        raise RuntimeError(f"{_ROLLBACK_FAILED_PREFIX}budget_refund") from None


def assemble_selection_model_view(
    *,
    canonical_selected_text: str | None,
    envelope_fingerprint: str,
    budget: ModelVisibleTurnBudget,
    registry: EvidenceRegistry | None = None,
    renderer: ModelViewRenderer | None = None,
    unit_id: str | None = None,
    anchor_segment_id: str | None = None,
    text_hash: str | None = None,
    offset_unit: str | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> SelectionModelViewResult:
    """Assemble cost-fit, registry-backed selection model-view (inject seam).

    For non-empty ``canonical_selected_text``, ``registry`` is **required**.
    Injected results always carry a registered handle, a charged renderer
    block, and an assembler-minted :class:`SelectionPromptCapability`.

    Pure planning without registry is not this function's job — use
    :func:`fit_selection_prefix` (no handles / no injection).
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
        return SelectionModelViewResult(
            status="absent",
            selection=SelectionCapabilityView(present=False),
            visible_prefix="",
            full_char_count=0,
            continuation_start=0,
        )

    # Fix 1: non-empty selection requires registry before any fit/charge.
    if registry is None:
        raise ValueError(_REGISTRY_REQUIRED_ERROR)

    # Preflight fingerprint before any budget work (no mutation).
    if registry.envelope_fingerprint != envelope_fingerprint:
        raise ValueError(
            "evidence registry fingerprint does not match envelope fingerprint"
        )

    full_len = len(canonical_selected_text)
    handle_id = mint_evidence_handle_id()

    # Cost-fit search: can_charge only.
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

    # Preflight observation construction + duplicate handle check BEFORE charge.
    observation = _preflight_observation(
        registry=registry,
        envelope_fingerprint=envelope_fingerprint,
        handle_id=handle_id,
        visible_prefix=visible_prefix,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        text_hash=text_hash,
        offset_unit=offset_unit,
        start_offset=start_offset,
        end_offset=end_offset,
    )

    model_chunk = ModelContextChunk(
        handle_id=handle_id,
        chunk_ordinal=SELECTION_CHUNK_ORDINAL,
        text=visible_prefix,
    )
    if model_chunk.text != visible_prefix or observation.snippet != visible_prefix:
        raise RuntimeError("selection model-view binary equality broken at preflight")

    # Atomic commit: charge → register → postcondition → mint capability.
    # Any failure after charge uses _compensate_after_charge (budget + this obs).
    charge_ok = budget.charge("selection", fitted_view)
    charge_cost = charge_ok.cost
    handle_ref: EvidenceHandleRef | None = None
    try:
        handle_ref = registry.register(observation)

        registered = registry.get(handle_id)
        if (
            registered is None
            or registered != observation
            or registered.snippet != model_chunk.text
            or handle_ref.handle_id != handle_id
        ):
            raise RuntimeError(f"{_INJECT_FAILED_PREFIX}postcondition")

        prompt_capability = _mint_selection_prompt_capability(
            fitted_view=fitted_view,
            handle_id=handle_id,
        )
    except Exception:
        _compensate_after_charge(
            budget=budget,
            charge_cost=charge_cost,
            registry=registry,
            observation=observation,
        )
        # Re-raise original inject failure so callers see register/mint
        # errors; compensation already restored budget + this observation.
        # Rollback failures raise from _compensate_after_charge instead.
        raise

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
        prompt_capability=prompt_capability,
    )


def assert_selection_binary_equality(
    result: SelectionModelViewResult,
    *,
    registry: EvidenceRegistry,
) -> None:
    """Raise ``AssertionError`` unless inject-path binary equality holds."""
    if result.status != "injected":
        raise AssertionError(
            f"binary equality requires injected status, got {result.status}"
        )
    if result.model_chunk is None:
        raise AssertionError("injected result missing model_chunk")
    if result.handle_ref is None:
        raise AssertionError("injected result missing handle_ref")
    if result.prompt_capability is None:
        raise AssertionError("injected result missing prompt_capability")
    if result.rendered_untrusted_block is None:
        raise AssertionError("injected result missing rendered_untrusted_block")
    if result.model_chunk.text != result.visible_prefix:
        raise AssertionError("model_chunk.text != visible_prefix")
    if result.selection.handle_id is None:
        raise AssertionError("injected result missing handle_id")
    if result.model_chunk.handle_id != result.selection.handle_id:
        raise AssertionError("chunk handle_id != selection metadata handle_id")
    if result.handle_ref.handle_id != result.selection.handle_id:
        raise AssertionError("handle_ref != selection handle_id")
    if result.prompt_capability.handle_id != result.selection.handle_id:
        raise AssertionError("prompt capability handle_id mismatch")
    obs = registry.get(result.selection.handle_id)
    if obs is None:
        raise AssertionError("handle not in registry")
    if obs.snippet != result.visible_prefix:
        raise AssertionError("registry snippet != visible_prefix")
    if obs.snippet != result.model_chunk.text:
        raise AssertionError("registry snippet != model_chunk.text")
    validate_selection_prompt_capability(result.prompt_capability)


__all__ = [
    "EVIDENCE_SNIPPET_HARD_CAP",
    "RESERVE_SELECTION",
    "SELECTION_CHUNK_ORDINAL",
    "SELECTION_ROLE",
    "SELECTION_SECTION_FOOTER",
    "SELECTION_SECTION_HEADER",
    "SelectionModelViewResult",
    "SelectionModelViewStatus",
    "SelectionPromptCapability",
    "assemble_selection_model_view",
    "assert_selection_binary_equality",
    "fit_selection_prefix",
    "validate_selection_prompt_capability",
]
