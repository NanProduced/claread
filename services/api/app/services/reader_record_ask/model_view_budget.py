"""Host-owned model-visible turn budget and renderer (R4-A5-1 foundation).

Authority (design TMP §18.1)
----------------------------
``MODEL_VISIBLE_TURN_PAYLOAD_CAP`` is the hard cap on the **host-controlled,
model-visible turn payload** measured in Python ``str`` characters. It is
**not** a provider wire-request size, tokenizer count, or full context-window
claim.

The single enforcement seam is :class:`ModelViewRenderer` +
:class:`ModelVisibleTurnBudget`. Provider encoders, tokenizers, and shadow
ledgers must not decide whether article context may be injected.

A5-1 scope
----------
Foundation modules + unit tests only. Does **not**:

- replace ``build_agent_user_prompt``;
- switch selection injection (A5-2);
- wire expand / map / RAG model-view into the agent loop;
- call embedding / vector I/O;
- raise ``ModelRetry`` on budget exhaustion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from xml.sax.saxutils import escape as _xml_escape

# ---------------------------------------------------------------------------
# Cap + six-account reserves (sum == MODEL_VISIBLE_TURN_PAYLOAD_CAP)
# ---------------------------------------------------------------------------

MODEL_VISIBLE_TURN_PAYLOAD_CAP: int = 24_000

RESERVE_REQUEST_FRAME: int = 4_000
RESERVE_SELECTION: int = 2_500
RESERVE_BASELINE: int = 9_000
RESERVE_MAP: int = 1_500
RESERVE_EXPAND: int = 4_000
RESERVE_RAG: int = 3_000

BudgetAccountName = Literal[
    "request_frame",
    "selection",
    "baseline",
    "map",
    "expand",
    "rag",
]

ACCOUNT_RESERVES: dict[BudgetAccountName, int] = {
    "request_frame": RESERVE_REQUEST_FRAME,
    "selection": RESERVE_SELECTION,
    "baseline": RESERVE_BASELINE,
    "map": RESERVE_MAP,
    "expand": RESERVE_EXPAND,
    "rag": RESERVE_RAG,
}

assert sum(ACCOUNT_RESERVES.values()) == MODEL_VISIBLE_TURN_PAYLOAD_CAP

BudgetDenyReason = Literal["account_exhausted", "total_exhausted"]


# ---------------------------------------------------------------------------
# Typed results / errors (never ModelRetry)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedModelView:
    """One host-owned rendered surface plus its serialized char cost."""

    text: str
    char_cost: int

    def __post_init__(self) -> None:
        if self.char_cost != len(self.text):
            raise ValueError(
                "char_cost must equal len(text); "
                f"got char_cost={self.char_cost}, len={len(self.text)}"
            )


@dataclass(frozen=True, slots=True)
class BudgetChargeOk:
    """Successful charge against one budget account."""

    account: BudgetAccountName
    cost: int
    spent_after: int
    remaining_account: int
    remaining_total: int


@dataclass(frozen=True, slots=True)
class BudgetChargeDenied:
    """Typed budget denial — callers must not truncate the user question."""

    account: BudgetAccountName
    cost: int
    reason: BudgetDenyReason
    spent_account: int
    reserve_account: int
    spent_total: int
    remaining_account: int
    remaining_total: int


class ModelViewBudgetError(Exception):
    """Budget hard-failure for host pre-flight (never ModelRetry).

    Carries a structured :class:`BudgetChargeDenied` so production stream /
    runtime can map to a typed input-too-large / budget-exhausted terminal
    without parsing exception text.
    """

    def __init__(self, denial: BudgetChargeDenied) -> None:
        self.denial = denial
        super().__init__(
            f"model_view_budget_exhausted account={denial.account} "
            f"reason={denial.reason} cost={denial.cost} "
            f"remaining_account={denial.remaining_account} "
            f"remaining_total={denial.remaining_total}"
        )


@dataclass(frozen=True, slots=True)
class RequestFrameParts:
    """Breakdown of the request_frame account (system + turn frame).

    Untrusted selection / baseline / map blocks are **not** part of this
    account — they charge selection / baseline / map respectively.
    """

    system_instructions: str
    user_question: str
    projection_json: str
    handles_block: str = ""
    coverage_block: str = ""
    correctness_block: str = ""


# ---------------------------------------------------------------------------
# Budget ledger
# ---------------------------------------------------------------------------


class ModelVisibleTurnBudget:
    """Six-account char budget for one Ask turn's model-visible payload.

    Spill across accounts is forbidden. Each charge is checked against the
    account reserve **and** the turn total cap.
    """

    __slots__ = ("_spent",)

    def __init__(self) -> None:
        self._spent: dict[BudgetAccountName, int] = {
            name: 0 for name in ACCOUNT_RESERVES
        }

    def reserve(self, account: BudgetAccountName) -> int:
        return ACCOUNT_RESERVES[account]

    def spent(self, account: BudgetAccountName) -> int:
        return self._spent[account]

    def remaining(self, account: BudgetAccountName) -> int:
        return ACCOUNT_RESERVES[account] - self._spent[account]

    def total_spent(self) -> int:
        return sum(self._spent.values())

    def total_remaining(self) -> int:
        return MODEL_VISIBLE_TURN_PAYLOAD_CAP - self.total_spent()

    def snapshot(self) -> dict[str, int]:
        """Diagnostic copy of spent-by-account (not model-visible)."""
        return dict(self._spent)

    def can_charge(self, account: BudgetAccountName, cost: int) -> bool:
        if cost < 0:
            raise ValueError("cost must be non-negative")
        if self._spent[account] + cost > ACCOUNT_RESERVES[account]:
            return False
        if self.total_spent() + cost > MODEL_VISIBLE_TURN_PAYLOAD_CAP:
            return False
        return True

    def try_charge(
        self, account: BudgetAccountName, cost: int
    ) -> BudgetChargeOk | BudgetChargeDenied:
        """Attempt a charge; never mutates on denial."""
        if cost < 0:
            raise ValueError("cost must be non-negative")
        spent_account = self._spent[account]
        reserve = ACCOUNT_RESERVES[account]
        spent_total = self.total_spent()
        remaining_account = reserve - spent_account
        remaining_total = MODEL_VISIBLE_TURN_PAYLOAD_CAP - spent_total

        if spent_account + cost > reserve:
            return BudgetChargeDenied(
                account=account,
                cost=cost,
                reason="account_exhausted",
                spent_account=spent_account,
                reserve_account=reserve,
                spent_total=spent_total,
                remaining_account=remaining_account,
                remaining_total=remaining_total,
            )
        if spent_total + cost > MODEL_VISIBLE_TURN_PAYLOAD_CAP:
            return BudgetChargeDenied(
                account=account,
                cost=cost,
                reason="total_exhausted",
                spent_account=spent_account,
                reserve_account=reserve,
                spent_total=spent_total,
                remaining_account=remaining_account,
                remaining_total=remaining_total,
            )

        self._spent[account] = spent_account + cost
        return BudgetChargeOk(
            account=account,
            cost=cost,
            spent_after=self._spent[account],
            remaining_account=reserve - self._spent[account],
            remaining_total=MODEL_VISIBLE_TURN_PAYLOAD_CAP - self.total_spent(),
        )

    def charge(self, account: BudgetAccountName, cost: int) -> BudgetChargeOk:
        """Charge or raise :class:`ModelViewBudgetError` (typed, not ModelRetry)."""
        result = self.try_charge(account, cost)
        if isinstance(result, BudgetChargeDenied):
            raise ModelViewBudgetError(result)
        return result


# ---------------------------------------------------------------------------
# Host-owned renderer (unique metering entry)
# ---------------------------------------------------------------------------


class ModelViewRenderer:
    """Deterministic host-owned serializer for model-visible Ask surfaces.

    Every charged surface returns :class:`RenderedModelView` whose
    ``char_cost`` equals ``len(text)``. Callers charge that cost into
    :class:`ModelVisibleTurnBudget`. No provider encoder or tokenizer is
    consulted.
    """

    __slots__ = ()

    def render_plain(self, text: str) -> RenderedModelView:
        """Render a trusted plain-text surface (system / question / headers)."""
        if not isinstance(text, str):
            raise TypeError("text must be str")
        return RenderedModelView(text=text, char_cost=len(text))

    def render_json(self, payload: Mapping[str, Any]) -> RenderedModelView:
        """Canonical JSON for projections and tool model-views.

        Sorted keys, compact separators, ``ensure_ascii=False`` so non-ASCII
        content has a stable, re-computable char cost.
        """
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return RenderedModelView(text=text, char_cost=len(text))

    def render_tool_view(self, model_view: Mapping[str, Any]) -> RenderedModelView:
        """Canonical JSON ModelToolView for expand / RAG tool returns.

        Same encoding as :meth:`render_json` — tool-return cost never assumes
        XML wrapping.
        """
        return self.render_json(model_view)

    def render_untrusted_article_text(
        self,
        *,
        handle_id: str,
        ordinal: int,
        role: str,
        text: str,
    ) -> RenderedModelView:
        """XML-escape + tag-wrap untrusted article text (selection/baseline/…).

        Serialized cost includes open/close tags and attribute escaping.
        Text is untrusted data, never instructions.
        """
        if not handle_id or not isinstance(handle_id, str):
            raise ValueError("handle_id must be a non-empty string")
        if not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("ordinal must be a non-negative int")
        if not role or not isinstance(role, str):
            raise ValueError("role must be a non-empty string")
        if not isinstance(text, str):
            raise TypeError("text must be str")

        escaped_text = _xml_escape(text)
        escaped_handle = _xml_escape(handle_id, {'"': "&quot;"})
        escaped_role = _xml_escape(role, {'"': "&quot;"})
        open_tag = (
            f'<untrusted_article_text handle="{escaped_handle}" '
            f'ordinal="{ordinal}" role="{escaped_role}">'
        )
        close_tag = "</untrusted_article_text>"
        rendered = f"{open_tag}{escaped_text}{close_tag}"
        return RenderedModelView(text=rendered, char_cost=len(rendered))

    def render_request_frame(self, parts: RequestFrameParts) -> RenderedModelView:
        """Compose the request_frame account content and measure its char cost.

        Includes canonical system instructions, projection JSON, handles,
        coverage, correctness, and the **full** user question. Does **not**
        truncate the question; if the result exceeds
        ``RESERVE_REQUEST_FRAME``, the caller must deny via the budget
        (typed result / :class:`ModelViewBudgetError`).
        """
        user_question = parts.user_question
        if not isinstance(user_question, str):
            raise TypeError("user_question must be str")

        # Section assembly mirrors the trusted turn-frame surfaces that will
        # later replace ad-hoc prompt assembly. Untrusted article blocks are
        # deliberately excluded (other accounts).
        turn_sections: list[str] = [
            "## Current turn context (server projection; not tool arguments)",
            parts.projection_json,
        ]
        if parts.handles_block:
            turn_sections.append(parts.handles_block.rstrip("\n"))
        if parts.coverage_block:
            turn_sections.append(parts.coverage_block.rstrip("\n"))
        if parts.correctness_block:
            turn_sections.append(
                "## Answer correctness (turn-specific rules)\n"
                + parts.correctness_block.rstrip("\n")
            )
        turn_sections.append("## User question")
        # Never strip / truncate the user question for budget fit.
        turn_sections.append(user_question)

        system = parts.system_instructions
        if not isinstance(system, str):
            raise TypeError("system_instructions must be str")

        turn_frame = "\n".join(turn_sections) + "\n"
        # System and turn frame are separate ModelRequest messages; cost is
        # the sum of their host-owned serialized lengths.
        combined = system + "\n" + turn_frame if system else turn_frame
        return RenderedModelView(text=combined, char_cost=len(combined))

    def charge_request_frame(
        self,
        budget: ModelVisibleTurnBudget,
        parts: RequestFrameParts,
    ) -> tuple[RenderedModelView, BudgetChargeOk]:
        """Render request_frame and charge it; raise on exhaustion.

        The user question is never truncated to fit — oversized questions
        surface as :class:`ModelViewBudgetError`.
        """
        rendered = self.render_request_frame(parts)
        ok = budget.charge("request_frame", rendered.char_cost)
        return rendered, ok
