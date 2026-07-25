"""Host-owned model-visible turn budget and renderer (R4-A5-1 / A5-1R).

Authority (design TMP §18.1)
----------------------------
``MODEL_VISIBLE_TURN_PAYLOAD_CAP`` is the hard cap on the **host-controlled,
model-visible turn payload** measured in Python ``str`` characters. It is
**not** a provider wire-request size, tokenizer count, or full context-window
claim.

The single enforcement seam is :class:`ModelViewRenderer` +
:class:`ModelVisibleTurnBudget`. Provider encoders, tokenizers, and shadow
ledgers must not decide whether article context may be injected.

Public budget charges accept only :class:`RenderedModelView` instances minted
by :class:`ModelViewRenderer` (renderer-origin brand). Hand-constructed views
are rejected before any budget mutation. Raw integer debit is private to this
module. This is a **module boundary** constraint, not a hostile in-process
sandbox.

JSON surfaces (``render_json`` / ``render_tool_view``) are type-level
fail-closed: only JSON-native values and finite floats. Serialization errors
are sanitized typed codes — never payload dumps, object repr, or raw
exception text. Mapping/container traversal exceptions are converted the same
way. Content safety of projections / ModelToolView remains the responsibility
of their typed schemas (no field-name or semantic scanning here).

A5-1 scope
----------
Foundation modules + unit tests only. Does **not**:

- replace ``build_production_agent_user_prompt``;
- switch selection injection (A5-2);
- wire expand / map / RAG model-view into the agent loop;
- call embedding / vector I/O;
- raise ``ModelRetry`` on budget exhaustion.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from xml.sax.saxutils import escape as _xml_escape

# ---------------------------------------------------------------------------
# Cap + six-account reserves (sum == MODEL_VISIBLE_TURN_PAYLOAD_CAP)
# ---------------------------------------------------------------------------

MODEL_VISIBLE_TURN_PAYLOAD_CAP: int = 24_000

# R4-A5-7: request_frame must absorb full system instructions (~6.3k) plus
# projection / handles / coverage / question / section chrome.
# Rebalanced from the A5-1 placeholder 4k so production turns fit without
# truncating the user question. Selection/expand/map stay at their A5-2/3/4
# sizes so existing cost-fit tests remain valid. Sum remains 24_000.
RESERVE_REQUEST_FRAME: int = 9_500
RESERVE_SELECTION: int = 2_500
RESERVE_BASELINE: int = 3_500
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

# Sanitized serialization failure codes (no payload / type name / value).
SerializationErrorCode = Literal[
    "non_json_native",
    "non_finite_float",
    "non_string_key",
    "not_object",
]

# Module-private brand for renderer-minted views. Not exported in public API
# surface docs; identity-checked only. Not a hostile-code sandbox.
_RENDERER_ORIGIN: object = object()

# Map-entry cursor shape (R4-A5-4): server-minted opaque continuation token.
_MAP_CURSOR_PATTERN = re.compile(r"^cur_[0-9a-f]{32}$")

_CHARGE_REQUIRES_RENDERER_VIEW = (
    "budget charge requires RenderedModelView from ModelViewRenderer"
)


# ---------------------------------------------------------------------------
# Typed results / errors (never ModelRetry)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedModelView:
    """One host-owned rendered surface plus its serialized char cost.

    Public annotation type. Only instances minted via the private renderer
    factory carry a valid origin brand and may be charged. Hand construction
    yields an unchargeable view (module boundary, not a security sandbox).
    """

    text: str
    char_cost: int
    # init=False: public constructor cannot brand a view. Factory sets via
    # object.__setattr__. Excluded from repr / equality / hash so the brand
    # never leaks into diagnostics or model-visible comparisons.
    _origin: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )

    def __post_init__(self) -> None:
        if self.char_cost != len(self.text):
            raise ValueError(
                "char_cost must equal len(text); "
                f"got char_cost={self.char_cost}, len={len(self.text)}"
            )


def _mint_rendered_view(text: str) -> RenderedModelView:
    """Private factory: only path that brands a chargeable RenderedModelView."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    view = RenderedModelView(text=text, char_cost=len(text))
    object.__setattr__(view, "_origin", _RENDERER_ORIGIN)
    return view


def is_renderer_minted_view(view: object) -> bool:
    """Return True when ``view`` is a renderer-branded :class:`RenderedModelView`.

    Non-metering origin check for co-located Ask seams (selection prompt
    capability, etc.). Does **not** charge budget.
    """
    return (
        isinstance(view, RenderedModelView)
        and getattr(view, "_origin", None) is _RENDERER_ORIGIN
        and view.char_cost == len(view.text)
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


class ModelViewSerializationError(Exception):
    """Fail-closed JSON serialization error with a sanitized typed code.

    Messages contain only the stable ``code`` string. They must never embed
    payload fragments, object reprs, type names of rejected values, or the
    original exception text.
    """

    def __init__(self, code: SerializationErrorCode) -> None:
        self.code: SerializationErrorCode = code
        super().__init__(f"model_view_serialization_error code={code}")


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


# ---------------------------------------------------------------------------
# JSON-native fail-closed walk (no str-coercion default; NaN disallowed)
# ---------------------------------------------------------------------------


def _to_json_native(value: object) -> Any:
    """Return a plain JSON-native structure or raise ModelViewSerializationError.

    Allowed leaves: ``None``, ``bool``, ``int``, finite ``float``, ``str``.
    Allowed containers: ``list``, ``Mapping`` with ``str`` keys only.
    Rejects UUID, custom objects, bytes, tuples, sets, NaN/±Inf, non-str keys.

    Any ordinary ``Exception`` raised while reading containers (e.g. a
    hostile ``Mapping.items()``) is converted to a sanitized
    :class:`ModelViewSerializationError` with ``from None``. Existing
    :class:`ModelViewSerializationError` instances propagate unchanged.
    ``BaseException`` is not caught.
    """
    try:
        return _to_json_native_body(value)
    except ModelViewSerializationError:
        raise
    except Exception:
        raise ModelViewSerializationError("non_json_native") from None


def _to_json_native_body(value: object) -> Any:
    if value is None:
        return None
    # bool is a subclass of int — check before int.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelViewSerializationError("non_finite_float")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_to_json_native(item) for item in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ModelViewSerializationError("non_string_key")
            out[key] = _to_json_native(item)
        return out
    raise ModelViewSerializationError("non_json_native")


def _dump_canonical_json(payload: Mapping[str, Any]) -> str:
    """Canonical JSON dump; fail-closed (no str coercion default; NaN off)."""
    if not isinstance(payload, Mapping):
        raise ModelViewSerializationError("not_object")
    try:
        native = _to_json_native(payload)
    except ModelViewSerializationError:
        raise
    except Exception:
        raise ModelViewSerializationError("non_json_native") from None
    # allow_nan=False is belt-and-suspenders after the finite-float walk.
    try:
        return json.dumps(
            native,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        # Sanitized — never chain or embed the raw exception text.
        raise ModelViewSerializationError("non_json_native") from None


# ---------------------------------------------------------------------------
# Budget ledger
# ---------------------------------------------------------------------------


class ModelVisibleTurnBudget:
    """Six-account char budget for one Ask turn's model-visible payload.

    Spill across accounts is forbidden. Each public charge is checked against
    the account reserve **and** the turn total cap, and must carry a
    renderer-minted :class:`RenderedModelView`.
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
        return cast("dict[str, int]", dict(self._spent))

    def can_charge(
        self, account: BudgetAccountName, rendered: RenderedModelView
    ) -> bool:
        cost = self._cost_from_rendered(rendered)
        return self._can_charge_chars(account, cost)

    def try_charge(
        self, account: BudgetAccountName, rendered: RenderedModelView
    ) -> BudgetChargeOk | BudgetChargeDenied:
        """Attempt a charge; never mutates on denial."""
        cost = self._cost_from_rendered(rendered)
        return self._try_charge_chars(account, cost)

    def charge(
        self, account: BudgetAccountName, rendered: RenderedModelView
    ) -> BudgetChargeOk:
        """Charge or raise :class:`ModelViewBudgetError` (typed, not ModelRetry)."""
        result = self.try_charge(account, rendered)
        if isinstance(result, BudgetChargeDenied):
            raise ModelViewBudgetError(result)
        return result

    # -- private integer debit (not part of the public metering seam) --------

    def _cost_from_rendered(self, rendered: RenderedModelView) -> int:
        """Validate type + renderer origin before any budget mutation."""
        if not isinstance(rendered, RenderedModelView):
            raise TypeError(_CHARGE_REQUIRES_RENDERER_VIEW)
        # Identity check only — brand token never appears in the message.
        if getattr(rendered, "_origin", None) is not _RENDERER_ORIGIN:
            raise TypeError(_CHARGE_REQUIRES_RENDERER_VIEW)
        if rendered.char_cost != len(rendered.text):
            raise ValueError("RenderedModelView char_cost must equal len(text)")
        if rendered.char_cost < 0:
            raise ValueError("char_cost must be non-negative")
        return rendered.char_cost

    def _refund_chars(self, account: BudgetAccountName, cost: int) -> None:
        """Private rollback for failed multi-step host transactions.

        Used only by co-located Ask seams (e.g. selection inject) when a
        charge must be undone because a later registry step failed after
        preflight. Not part of the public metering API.
        """
        if cost < 0:
            raise ValueError("cost must be non-negative")
        spent = self._spent[account]
        if cost > spent:
            raise ValueError("refund exceeds account spent")
        self._spent[account] = spent - cost

    def _can_charge_chars(self, account: BudgetAccountName, cost: int) -> bool:
        if cost < 0:
            raise ValueError("cost must be non-negative")
        if self._spent[account] + cost > ACCOUNT_RESERVES[account]:
            return False
        if self.total_spent() + cost > MODEL_VISIBLE_TURN_PAYLOAD_CAP:
            return False
        return True

    def _try_charge_chars(
        self, account: BudgetAccountName, cost: int
    ) -> BudgetChargeOk | BudgetChargeDenied:
        """Private raw-char charge. Public callers must use RenderedModelView."""
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


# ---------------------------------------------------------------------------
# Host-owned renderer (unique metering entry)
# ---------------------------------------------------------------------------


class ModelViewRenderer:
    """Deterministic host-owned serializer for model-visible Ask surfaces.

    Every charged surface returns a renderer-minted :class:`RenderedModelView`
    whose ``char_cost`` equals ``len(text)``. Callers charge that object into
    :class:`ModelVisibleTurnBudget`. No provider encoder or tokenizer is
    consulted.
    """

    __slots__ = ()

    def render_plain(self, text: str) -> RenderedModelView:
        """Render a trusted plain-text surface (system / question / headers)."""
        if not isinstance(text, str):
            raise TypeError("text must be str")
        return _mint_rendered_view(text)

    def render_json(self, payload: Mapping[str, Any]) -> RenderedModelView:
        """Canonical JSON for projections and tool model-views.

        Sorted keys, compact separators, ``ensure_ascii=False``. Rejects
        non-JSON-native values and non-finite floats with
        :class:`ModelViewSerializationError` (sanitized code only).
        """
        text = _dump_canonical_json(payload)
        return _mint_rendered_view(text)

    def render_tool_view(self, model_view: Mapping[str, Any]) -> RenderedModelView:
        """Canonical JSON ModelToolView for expand / RAG tool returns.

        Same encoding as :meth:`render_json` — tool-return cost never assumes
        XML wrapping.
        """
        return self.render_json(model_view)

    def render_untrusted_article_map(
        self,
        *,
        entries: Sequence[Mapping[str, str]],
    ) -> RenderedModelView:
        """Render the untrusted article-map block (R4-A5-4, design §18.2).

        Each entry carries exactly ``cursor`` (``cur_<32 hex>``), ``kind``
        (``heading`` | ``window`` | ``ordinal``) and ``label``
        (article-derived **untrusted** text). Labels and attribute values
        are XML-escaped so hostile label text cannot escape the data
        region. No entry field may carry identity, locator, offset, or
        provenance — the caller's typed schema enforces that; this method
        enforces the closed key set and shape. Serialized cost includes
        every tag and attribute (the single metering source for the map
        account).
        """
        lines: list[str] = ["<untrusted_article_map>"]
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise TypeError("map entry must be a Mapping")
            if set(entry.keys()) != {"cursor", "kind", "label"}:
                raise ValueError(
                    "map entry must carry exactly cursor/kind/label"
                )
            cursor = entry["cursor"]
            kind = entry["kind"]
            label = entry["label"]
            if not isinstance(cursor, str) or not _MAP_CURSOR_PATTERN.match(
                cursor
            ):
                raise ValueError(
                    "map entry cursor must match cur_<32 hex chars>"
                )
            if not isinstance(kind, str) or kind not in (
                "heading",
                "window",
                "ordinal",
            ):
                raise ValueError(
                    "map entry kind must be heading|window|ordinal"
                )
            if not isinstance(label, str):
                raise TypeError("map entry label must be str")
            escaped_label = _xml_escape(label)
            escaped_cursor = _xml_escape(cursor, {'"': "&quot;"})
            escaped_kind = _xml_escape(kind, {'"': "&quot;"})
            lines.append(
                f'<entry cursor="{escaped_cursor}" kind="{escaped_kind}">'
                f"{escaped_label}</entry>"
            )
        lines.append("</untrusted_article_map>")
        return _mint_rendered_view("\n".join(lines))

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
        return _mint_rendered_view(rendered)

    def render_request_frame(self, parts: RequestFrameParts) -> RenderedModelView:
        """Compose the request_frame account content and measure its char cost.

        Includes canonical system instructions, projection JSON, handles,
        coverage, and the **full** user question. Does **not**
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
        return _mint_rendered_view(combined)

    def charge_request_frame(
        self,
        budget: ModelVisibleTurnBudget,
        parts: RequestFrameParts,
    ) -> tuple[RenderedModelView, BudgetChargeOk]:
        """Render request_frame and charge it; raise on exhaustion.

        The user question is never truncated to fit — oversized questions
        surface as :class:`ModelViewBudgetError`. Metering always flows
        through the renderer-minted :class:`RenderedModelView`.
        """
        rendered = self.render_request_frame(parts)
        ok = budget.charge("request_frame", rendered)
        return rendered, ok
