"""Turn-frame prompt capability — unique metering boundary (R4-A5-7).

:class:`TurnFramePromptCapability` is the **only** holder of the initial
system instructions + trusted user-frame composition for production Ask.

Request-frame account ownership
-------------------------------
Charged characters are exactly:

- system instructions;
- projection JSON;
- structured turn-answer policy JSON;
- handles block;
- coverage block;
- optional legacy correctness block (production omits it);
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

from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapPromptCapability,
    validate_article_map_prompt_capability,
)
from app.services.reader_record_ask.baseline_model_view import (
    BaselinePromptCapability,
    validate_baseline_prompt_capability,
)
from app.services.reader_record_ask.model_view_budget import (
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

_TURN_FRAME_ORIGIN: object = object()

_TURN_FRAME_TYPE_ERROR = (
    "turn frame prompt requires TurnFramePromptCapability "
    "from mint_turn_frame_prompt_capability"
)

_CONTEXT_HEADER = (
    "## Current turn context (server projection; not tool arguments)"
)
_ANSWER_POLICY_HEADER = "## Turn answer policy (server-owned)"
_QUESTION_HEADER = "## User question"
_CORRECTNESS_HEADER = "## Answer correctness (turn-specific rules)"


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
    correctness_block: str | None,
    user_question: str,
    selection_prompt: SelectionPromptCapability | None,
    baseline_prompt: BaselinePromptCapability | None,
    map_prompt: ArticleMapPromptCapability | None,
    answer_policy_json: str = "{}",
) -> tuple[str, str, str, str]:
    """Compose the production user prompt; return bodies for equality checks.

    Returns ``(user_prompt, selection_untrusted, baseline_untrusted, map_untrusted)``.
    The user question is preserved **exactly** (no strip / truncate / rewrite).
    """
    if not isinstance(user_question, str):
        raise TypeError("user_question must be str")
    if not isinstance(answer_policy_json, str):
        raise TypeError("answer_policy_json must be str")

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

    correctness_section = (
        f"\n{_CORRECTNESS_HEADER}\n{correctness_block}\n"
        if correctness_block
        else ""
    )

    user_prompt = (
        f"{_CONTEXT_HEADER}\n"
        f"{projection_json}\n"
        f"{_ANSWER_POLICY_HEADER}\n"
        f"{answer_policy_json}\n"
        f"{handles_block}"
        f"{selection_section}"
        f"{baseline_section}"
        f"{map_section}"
        f"{coverage_block}"
        f"{correctness_section}"
        f"{_QUESTION_HEADER}\n"
        f"{user_question}\n"
    )
    return user_prompt, selection_untrusted, baseline_untrusted, map_untrusted


def _trusted_user_frame(
    user_prompt: str,
    *,
    selection_untrusted: str,
    baseline_untrusted: str,
    map_untrusted: str,
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
    return trusted


def mint_turn_frame_prompt_capability(
    *,
    system_instructions: str,
    projection_json: str,
    handles_block: str,
    baseline_is_complete: bool,
    correctness_block: str | None,
    user_question: str,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,
    selection_prompt: SelectionPromptCapability | None = None,
    baseline_prompt: BaselinePromptCapability | None = None,
    map_prompt: ArticleMapPromptCapability | None = None,
    answer_policy_json: str = "{}",
    charge: bool = True,
) -> TurnFramePromptCapability:
    """Compose + optionally charge the request_frame account.

    When ``charge=False`` the request frame is rendered and validated for
    size via ``can_charge`` only (pure planning). On deny raises
    :class:`ModelViewBudgetError` without mutating budget when ``charge``
    is True; when ``charge=False`` still raises so the host fail-closes.
    """
    if not isinstance(system_instructions, str):
        raise TypeError("system_instructions must be str")
    if not isinstance(user_question, str):
        raise TypeError("user_question must be str")

    coverage_block = _coverage_block(is_complete=baseline_is_complete)
    user_prompt, sel_u, base_u, map_u = compose_production_user_prompt(
        projection_json=projection_json,
        handles_block=handles_block,
        coverage_block=coverage_block,
        correctness_block=correctness_block,
        user_question=user_question,
        selection_prompt=selection_prompt,
        baseline_prompt=baseline_prompt,
        map_prompt=map_prompt,
        answer_policy_json=answer_policy_json,
    )
    trusted_user = _trusted_user_frame(
        user_prompt,
        selection_untrusted=sel_u,
        baseline_untrusted=base_u,
        map_untrusted=map_u,
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
            raise ModelViewBudgetError(denied)
        # Extremely defensive: if try_charge somehow succeeded, refund.
        budget._refund_chars("request_frame", denied.cost)  # noqa: SLF001
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
) -> bool:
    """Return True when four-account spend equals first model-surface chars."""
    rf = (
        request_frame_spent
        if request_frame_spent is not None
        else turn_frame.request_frame_charge_cost
    )
    total = rf + selection_spent + baseline_spent + map_spent
    return total == turn_frame.first_surface_char_count


def build_production_agent_user_prompt(
    *,
    turn_frame: TurnFramePromptCapability,
    selection_prompt: SelectionPromptCapability | None = None,
    baseline_prompt: BaselinePromptCapability | None = None,
    map_prompt: ArticleMapPromptCapability | None = None,
) -> str:
    """Return the exact production user prompt (no re-assembly drift).

    Optional capability args are validated for origin consistency with the
    turn frame's embedded untrusted bodies when provided.
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
