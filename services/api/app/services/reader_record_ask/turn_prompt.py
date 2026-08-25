"""Turn-frame prompt capability — unique metering boundary.

:class:`TurnFramePromptCapability` is the **only** holder of the initial
system instructions + trusted user-frame composition for production Ask.

Request-frame account ownership
-------------------------------
Charged characters are exactly:

- system instructions;
- projection JSON;
- handles block;
- coverage block;
- the **full** user question (never stripped / truncated / rewritten);
- selection / baseline / map **section chrome** (headers/footers only).

Untrusted block bodies charge selection / baseline / map accounts
respectively. Every separator newline, header, and footer is counted
exactly once under request_frame (or inside the untrusted view for the
body). The production equality holds:

    sum(request_frame + selection + baseline + map)
        == len(system) + len(user_prompt)   # first model-surface content

(or with a single ``\\n`` join between system and user when system is
non-empty — that join is part of the request_frame charge).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapPromptCapability,
    validate_article_map_prompt_capability,
)
from app.services.reader_record_ask.baseline_model_view import (
    BaselinePromptCapability,
    validate_baseline_prompt_capability,
)
from app.services.reader_record_ask.model_view_budget import (
    RESERVE_MEMORY,
    BudgetChargeOk,
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
    is_renderer_minted_view,
)
from app.services.reader_record_ask.selection_model_view import (
    SelectionPromptCapability,
    validate_selection_prompt_capability,
)

if TYPE_CHECKING:
    # build thread_memory package; only consumes the render
    # contract. Lazy import at call time avoids a hard dependency on
    # files that may not exist yet during parallel development.
    pass

_TURN_FRAME_ORIGIN: object = object()

_TURN_FRAME_TYPE_ERROR = (
    "turn frame prompt requires TurnFramePromptCapability "
    "from mint_turn_frame_prompt_capability"
)

_CONTEXT_HEADER = (
    "## Current turn context (server projection; not tool arguments)"
)
_QUESTION_HEADER = "## User question"


@dataclass(frozen=True, slots=True)
class TurnFramePromptCapability:
    """Branded production turn-frame: system + full user prompt + charge.

    Only :func:`mint_turn_frame_prompt_capability` may brand a usable
    instance. ``user_prompt`` is the exact first user-message content
    the model must see (selection/baseline/map bodies appear once).
    """

    system_instructions: str
    user_prompt: str
    request_frame_view: RenderedModelView
    request_frame_charge_cost: int
    # Exact untrusted bodies already charged to their accounts (may be "").
    selection_untrusted: str = ""
    baseline_untrusted: str = ""
    map_untrusted: str = ""
    # Supplemental typed context body (verbatim
    # math LaTeX + structural image metadata). Already charged to the
    # shared ``baseline`` account by the caller before minting; excluded
    # from the request_frame trusted surface. Empty when absent.
    typed_untrusted: str = ""
    # R1A: memory block body already charged to the ``memory`` account
    # (may be "" when no snapshot was injected). Excluded from the
    # request_frame trusted surface so it does not double-charge.
    memory_untrusted: str = ""
    # Verbatim recent conversation suffix, charged to ``recent_history``.
    recent_history_untrusted: str = ""
    _origin: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )

    @property
    def first_surface_char_count(self) -> int:
        """Chars of first system + user model-surface content (incl. join)."""
        if self.system_instructions:
            return (
                len(self.system_instructions)
                + 1
                + len(self.user_prompt)
            )
        return len(self.user_prompt)


def validate_turn_frame_prompt_capability(
    capability: object,
) -> TurnFramePromptCapability:
    if not isinstance(capability, TurnFramePromptCapability):
        raise TypeError(_TURN_FRAME_TYPE_ERROR)
    if getattr(capability, "_origin", None) is not _TURN_FRAME_ORIGIN:
        raise TypeError(_TURN_FRAME_TYPE_ERROR)
    if not is_renderer_minted_view(capability.request_frame_view):
        raise TypeError(_TURN_FRAME_TYPE_ERROR)
    return capability


def _coverage_block(*, is_complete: bool) -> str:
    if is_complete:
        return (
            "\n## Baseline coverage\n"
            "Status: complete. The baseline article text injected above "
            "covers the full article. You may make article-level claims "
            "when supported by the cited evidence.\n"
        )
    return (
        "\n## Baseline coverage\n"
        "Status: partial. The baseline article text injected above is "
        "only a subset of the article. Do NOT make exhaustive or "
        "negative claims about the whole article (e.g. \"the article "
        "never mentions...\", \"the author always...\", \"the article "
        "lists all...\") unless you have expanded coverage via "
        "expand_evidence / search_current_article. If coverage remains "
        "partial, scope your claim to \"in the parts I have read...\" "
        "or call a tool first.\n"
    )


def render_handles_listing(handle_ids: list[str] | tuple[str, ...]) -> str:
    """Request-frame-owned handles listing (exact chrome + ids)."""
    if not handle_ids:
        return ""
    listed = ", ".join(handle_ids)
    return (
        "\n## Server-registered evidence handles already available\n"
        f"{listed}\n"
        "You may cite these handles in cited_evidence_handles when relevant.\n"
    )


def compose_production_user_prompt(
    *,
    projection_json: str,
    handles_block: str,
    coverage_block: str,
    user_question: str,
    selection_prompt: SelectionPromptCapability | None,
    baseline_prompt: BaselinePromptCapability | None,
    map_prompt: ArticleMapPromptCapability | None,
    memory_section: str = "",
    recent_history_section: str = "",
    typed_context_section: str = "",
) -> tuple[str, str, str, str, str, str, str]:
    """Compose the production user prompt; return bodies for equality checks.

    Returns ``(user_prompt, selection_untrusted, baseline_untrusted,
    map_untrusted, memory_untrusted, recent_history_untrusted,
    typed_untrusted)``.
    The user question is preserved **exactly** (no strip / truncate / rewrite).

    R1A: ``memory_section`` is the pre-rendered memory block text (already
    charged to the ``memory`` account by the caller). It is injected
    after the handles block and before selection/baseline/map sections.
    The full ``memory_section`` is returned as ``memory_untrusted`` so
    the caller can exclude it from the request_frame trusted surface.

    ``typed_context_section`` is the pre-rendered
    supplemental typed context text (already charged to the shared
    ``baseline`` account by the caller). It is injected after the map
    section and before the coverage block; empty string injects nothing
    and keeps the composed prompt byte-identical to the pre-feature
    assembly.
    """
    if not isinstance(user_question, str):
        raise TypeError("user_question must be str")

    selection_section = ""
    selection_untrusted = ""
    if selection_prompt is not None:
        cap = validate_selection_prompt_capability(selection_prompt)
        selection_section = cap.section_text
        selection_untrusted = cap.untrusted_block_text

    baseline_section = ""
    baseline_untrusted = ""
    if baseline_prompt is not None:
        bcap = validate_baseline_prompt_capability(baseline_prompt)
        baseline_section = bcap.section_text
        baseline_untrusted = bcap.untrusted_block_text

    map_section = ""
    map_untrusted = ""
    if map_prompt is not None:
        mcap = validate_article_map_prompt_capability(map_prompt)
        map_section = mcap.section_text
        map_untrusted = mcap.untrusted_block_text

    user_prompt = (
        f"{_CONTEXT_HEADER}\n"
        f"{projection_json}\n"
        f"{handles_block}"
        f"{memory_section}"
        f"{recent_history_section}"
        f"{selection_section}"
        f"{baseline_section}"
        f"{map_section}"
        f"{typed_context_section}"
        f"{coverage_block}"
        f"{_QUESTION_HEADER}\n"
        f"{user_question}\n"
    )
    return (
        user_prompt,
        selection_untrusted,
        baseline_untrusted,
        map_untrusted,
        memory_section,
        recent_history_section,
        typed_context_section,
    )


def _trusted_user_frame(
    user_prompt: str,
    *,
    selection_untrusted: str,
    baseline_untrusted: str,
    map_untrusted: str,
    memory_untrusted: str = "",
    recent_history_untrusted: str = "",
    typed_untrusted: str = "",
) -> str:
    """User prompt with untrusted bodies removed (chrome retained)."""
    trusted = user_prompt
    # Bodies appear at most once; remove exactly one occurrence each.
    if selection_untrusted:
        trusted = trusted.replace(selection_untrusted, "", 1)
    if baseline_untrusted:
        trusted = trusted.replace(baseline_untrusted, "", 1)
    if map_untrusted:
        trusted = trusted.replace(map_untrusted, "", 1)
    if typed_untrusted:
        trusted = trusted.replace(typed_untrusted, "", 1)
    if memory_untrusted:
        trusted = trusted.replace(memory_untrusted, "", 1)
    if recent_history_untrusted:
        trusted = trusted.replace(recent_history_untrusted, "", 1)
    return trusted


def _build_recent_history_section(
    recent_history_view: RenderedModelView | None,
    budget: ModelVisibleTurnBudget,
) -> RenderedModelView | None:
    """Charge an already-rendered complete-turn recent history suffix."""

    if recent_history_view is None:
        return None
    budget.charge("recent_history", recent_history_view)
    return recent_history_view


def _build_memory_section(
    snapshot: Any,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,  # noqa: ARG001 — kept for API symmetry
) -> RenderedModelView | None:
    """Render + charge the thread-memory block for the ``memory`` account.

    R1A integration seam. The snapshot is a ``ThreadMemorySnapshot`` (
    schema). The actual rendering is delegated to
    ``thread_memory.render.render_memory_block`` (contract) which
    returns a renderer-minted :class:`RenderedModelView` wrapped in
    ``<transcript_data role="data" not_instructions="true">``.

    Returns ``None`` when:
    - ``snapshot`` is ``None`` (no memory loaded — flag off or empty thread);
    - ``render_memory_block`` returns ``None`` (empty snapshot / budget ≤ 0);
    - the memory account cannot absorb the rendered block (denial → raise
      :class:`ModelViewBudgetError` so the host fail-closes before
      ``agent.run`` — same discipline as selection / baseline / map).

    On success the ``memory`` account is charged exactly once and the
    rendered view is returned for inclusion in the user prompt.
    """
    if snapshot is None:
        return None

    # Lazy import — owns thread_memory/render.py. The contract is
    # ``render_memory_block(snapshot, budget_chars=N) -> RenderedModelView | None``.
    # The returned view is renderer-minted and therefore chargeable.
    from app.services.reader_record_ask.thread_memory.render import (
        render_memory_block,
    )

    memory_view = render_memory_block(snapshot, budget_chars=RESERVE_MEMORY)
    if memory_view is None:
        return None

    # Charge to the ``memory`` account. On denial this raises
    # ModelViewBudgetError — the host fail-closes exactly like the
    # selection / baseline / map accounts (F11 discipline).
    budget.charge("memory", memory_view)
    return memory_view


def mint_turn_frame_prompt_capability(
    *,
    system_instructions: str,
    projection_json: str,
    handles_block: str,
    baseline_is_complete: bool,
    user_question: str,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,
    selection_prompt: SelectionPromptCapability | None = None,
    baseline_prompt: BaselinePromptCapability | None = None,
    map_prompt: ArticleMapPromptCapability | None = None,
    charge: bool = True,
    typed_context_section: str = "",
    memory_snapshot: Any = None,
    recent_history_view: RenderedModelView | None = None,
) -> TurnFramePromptCapability:
    """Compose + optionally charge the request_frame account.

    When ``charge=False`` the request frame is rendered and validated for
    size via ``can_charge`` only (pure planning). On deny raises
    :class:`ModelViewBudgetError` without mutating budget when ``charge``
    is True; when ``charge=False`` still raises so the host fail-closes.

    R1A: ``memory_snapshot`` is the optional thread-memory snapshot. When
    non-None, a memory data block is rendered, charged to the ``memory``
    account, and injected into the user prompt after the handles block
    and before selection/baseline/map sections. When ``None`` (flag off
    or empty thread) no memory block is injected — the assembly path
    behaves exactly as today.
    """
    if not isinstance(system_instructions, str):
        raise TypeError("system_instructions must be str")
    if not isinstance(user_question, str):
        raise TypeError("user_question must be str")

    # R1A: render + charge the memory block BEFORE composing the user
    # prompt so its text can be injected as ``memory_section``. The
    # block is excluded from the request_frame trusted surface (it
    # charges the ``memory`` account, not request_frame). When the
    # snapshot is None or rendering returns None, no block is injected.
    memory_view = _build_memory_section(memory_snapshot, budget, renderer)
    memory_section = memory_view.text if memory_view is not None else ""
    recent_view = _build_recent_history_section(recent_history_view, budget)
    recent_section = recent_view.text if recent_view is not None else ""

    coverage_block = _coverage_block(is_complete=baseline_is_complete)
    user_prompt, sel_u, base_u, map_u, mem_u, recent_u, typed_u = (
        compose_production_user_prompt(
            projection_json=projection_json,
            handles_block=handles_block,
            coverage_block=coverage_block,
            user_question=user_question,
            selection_prompt=selection_prompt,
            baseline_prompt=baseline_prompt,
            map_prompt=map_prompt,
            memory_section=memory_section,
            recent_history_section=recent_section,
            typed_context_section=typed_context_section,
        )
    )
    trusted_user = _trusted_user_frame(
        user_prompt,
        selection_untrusted=sel_u,
        baseline_untrusted=base_u,
        map_untrusted=map_u,
        memory_untrusted=mem_u,
        recent_history_untrusted=recent_u,
        typed_untrusted=typed_u,
    )
    if system_instructions:
        request_frame_text = system_instructions + "\n" + trusted_user
    else:
        request_frame_text = trusted_user

    rendered = renderer.render_plain(request_frame_text)
    if not budget.can_charge("request_frame", rendered):
        # Construct a denial via try_charge without leaving a charge when
        # can_charge is False — try_charge is atomic on denial.
        denied = budget.try_charge("request_frame", rendered)
        # try_charge on denial returns BudgetChargeDenied and does not mutate;
        # on success we'd have charged — but can_charge was False so denied.
        from app.services.reader_record_ask.model_view_budget import (
            BudgetChargeDenied,
        )

        if isinstance(denied, BudgetChargeDenied):
            # R1A: refund the memory charge so the outer transaction
            # rollback receipt stays consistent (the host will call
            # _rollback_outer which refunds selection / baseline / map /
            # request_frame; memory is refunded here because it is
            # charged in this function before request_frame denial).
            if mem_u and budget.spent("memory") >= len(mem_u):
                budget._refund_chars("memory", len(mem_u))  # noqa: SLF001
            if (
                recent_u
                and budget.spent("recent_history") >= len(recent_u)
            ):
                budget._refund_chars(  # noqa: SLF001
                    "recent_history",
                    len(recent_u),
                )
            raise ModelViewBudgetError(denied)
        # Extremely defensive: if try_charge somehow succeeded, refund.
        budget._refund_chars("request_frame", denied.cost)  # noqa: SLF001
        if mem_u and budget.spent("memory") >= len(mem_u):
            budget._refund_chars("memory", len(mem_u))  # noqa: SLF001
        if recent_u and budget.spent("recent_history") >= len(recent_u):
            budget._refund_chars(  # noqa: SLF001
                "recent_history",
                len(recent_u),
            )
        raise ModelViewBudgetError(
            BudgetChargeDenied(
                account="request_frame",
                cost=rendered.char_cost,
                reason="account_exhausted",
                spent_account=budget.spent("request_frame"),
                reserve_account=budget.reserve("request_frame"),
                spent_total=budget.total_spent(),
                remaining_account=budget.remaining("request_frame"),
                remaining_total=budget.total_remaining(),
            )
        )

    charge_cost = 0
    if charge:
        ok: BudgetChargeOk = budget.charge("request_frame", rendered)
        charge_cost = ok.cost
    else:
        charge_cost = rendered.char_cost

    cap = TurnFramePromptCapability(
        system_instructions=system_instructions,
        user_prompt=user_prompt,
        request_frame_view=rendered,
        request_frame_charge_cost=charge_cost,
        selection_untrusted=sel_u,
        baseline_untrusted=base_u,
        map_untrusted=map_u,
        typed_untrusted=typed_u,
        memory_untrusted=mem_u,
        recent_history_untrusted=recent_u,
    )
    object.__setattr__(cap, "_origin", _TURN_FRAME_ORIGIN)
    return cap


def account_partition_equals_first_surface(
    turn_frame: TurnFramePromptCapability,
    *,
    selection_spent: int,
    baseline_spent: int,
    map_spent: int,
    request_frame_spent: int | None = None,
    memory_spent: int = 0,
    recent_history_spent: int = 0,
) -> bool:
    """Return True when account spend equals first model-surface chars.

    R1A: ``memory_spent`` accounts for the thread-memory block which is
    part of the user prompt but excluded from the request_frame trusted
    surface (charged to the ``memory`` account instead). Defaults to 0
    so existing callers (no memory) are unaffected.
    """
    rf = (
        request_frame_spent
        if request_frame_spent is not None
        else turn_frame.request_frame_charge_cost
    )
    total = (
        rf
        + selection_spent
        + baseline_spent
        + map_spent
        + memory_spent
        + recent_history_spent
    )
    return total == turn_frame.first_surface_char_count


def build_production_agent_user_prompt(
    *,
    turn_frame: TurnFramePromptCapability,
    selection_prompt: SelectionPromptCapability | None = None,
    baseline_prompt: BaselinePromptCapability | None = None,
    map_prompt: ArticleMapPromptCapability | None = None,
    focus_section: str = "",
) -> str:
    """Return the exact production user prompt (no re-assembly drift).

    Optional capability args are validated for origin consistency with the
    turn frame's embedded untrusted bodies when provided.

    ASK-UX-COT-COMPOSER- ``focus_section`` is the coordinator-
    rendered focus selections block (untrusted article text, charged to
    the selection account, deliberately absent from the request_frame
    trusted surface). When non-empty it is appended to the frame prompt
    (the section carries its own leading separator newline) so the model
    sees the user's additional focus selections.
    """
    frame = validate_turn_frame_prompt_capability(turn_frame)
    if selection_prompt is not None:
        cap = validate_selection_prompt_capability(selection_prompt)
        if cap.untrusted_block_text != frame.selection_untrusted:
            raise ValueError(
                "selection capability body does not match turn frame"
            )
    if baseline_prompt is not None:
        bcap = validate_baseline_prompt_capability(baseline_prompt)
        if bcap.untrusted_block_text != frame.baseline_untrusted:
            raise ValueError(
                "baseline capability body does not match turn frame"
            )
    if map_prompt is not None:
        mcap = validate_article_map_prompt_capability(map_prompt)
        if mcap.untrusted_block_text != frame.map_untrusted:
            raise ValueError("map capability body does not match turn frame")
    if focus_section:
        return frame.user_prompt + focus_section
    return frame.user_prompt


__all__ = [
    "TurnFramePromptCapability",
    "account_partition_equals_first_surface",
    "build_production_agent_user_prompt",
    "compose_production_user_prompt",
    "mint_turn_frame_prompt_capability",
    "render_handles_listing",
    "validate_turn_frame_prompt_capability",
]
