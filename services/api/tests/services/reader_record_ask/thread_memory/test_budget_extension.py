"""R1A-A3: budget account extension tests (RL2 + H5 + G11).

Verifies the model_view_budget module exposes nine accounts (seven
original + memory + recent_history), the reserves sum still equals
``MODEL_VISIBLE_TURN_PAYLOAD_CAP`` (128,000), and the stale "Six-account"
docstring (G11) has been corrected to "Nine-account".

Scope: model_view_budget.py only. No thread_memory package, no agent,
no LLM, no DB.
"""

from __future__ import annotations

import inspect

from app.services.reader_record_ask.model_view_budget import (
    ACCOUNT_RESERVES,
    MODEL_VISIBLE_TURN_PAYLOAD_CAP,
    RESERVE_BASELINE,
    RESERVE_CONTROL,
    RESERVE_EXPAND,
    RESERVE_MAP,
    RESERVE_MEMORY,
    RESERVE_RAG,
    RESERVE_RECENT_HISTORY,
    RESERVE_REQUEST_FRAME,
    RESERVE_SELECTION,
    BudgetAccountName,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)


def test_nine_accounts_exist_in_reserves() -> None:
    """ACCOUNT_RESERVES must carry all nine accounts (R0.1 RL2/H5)."""
    expected = {
        "request_frame",
        "selection",
        "baseline",
        "map",
        "expand",
        "rag",
        "control",
        "memory",
        "recent_history",
    }
    assert set(ACCOUNT_RESERVES.keys()) == expected


def test_memory_and_recent_history_constants() -> None:
    """RESERVE_MEMORY and RESERVE_RECENT_HISTORY match §5 parameter matrix."""
    assert RESERVE_MEMORY == 8_000
    assert RESERVE_RECENT_HISTORY == 40_000


def test_reserves_sum_equals_cap() -> None:
    """Nine-account reserves must sum to MODEL_VISIBLE_TURN_PAYLOAD_CAP.

    Ask text-only R2: memory/recent_history use one 128K character ledger.
    No parallel token-side ledger — ``assert sum == CAP`` stays valid.
    """
    total = sum(ACCOUNT_RESERVES.values())
    assert total == MODEL_VISIBLE_TURN_PAYLOAD_CAP
    assert total == 128_000


def test_individual_reserve_values() -> None:
    """Verify the reallocated reserve values (§5 unit-unification statement).

    Tool reserves stay fixed; the text-only conversation window expands
    memory + recent history while retaining one character ledger.
    """
    assert RESERVE_REQUEST_FRAME == 12_000  # was 16,000 (let out 4,000)
    assert RESERVE_SELECTION == 6_000  # unchanged
    assert RESERVE_BASELINE == 12_000  # was 14,000 (let out 2,000)
    assert RESERVE_MAP == 6_000  # unchanged
    assert RESERVE_EXPAND == 24_000  # was 30,000 (let out 6,000)
    assert RESERVE_RAG == 16_000  # was 20,000 (let out 4,000)
    assert RESERVE_CONTROL == 4_000  # unchanged
    assert RESERVE_MEMORY == 8_000
    assert RESERVE_RECENT_HISTORY == 40_000
    # Belt-and-suspenders: the sum is already checked above.
    assert (
        RESERVE_REQUEST_FRAME
        + RESERVE_SELECTION
        + RESERVE_BASELINE
        + RESERVE_MAP
        + RESERVE_EXPAND
        + RESERVE_RAG
        + RESERVE_CONTROL
        + RESERVE_MEMORY
        + RESERVE_RECENT_HISTORY
    ) == 128_000


def test_budget_account_name_literal_includes_new_accounts() -> None:
    """BudgetAccountName Literal must include "memory" and "recent_history"."""
    # BudgetAccountName is a typing.Literal; extract its args.
    args = set(BudgetAccountName.__args__)  # type: ignore[attr-defined]
    assert "memory" in args
    assert "recent_history" in args
    assert "request_frame" in args
    assert "control" in args


def test_budget_spent_initializes_all_nine_accounts() -> None:
    """ModelVisibleTurnBudget.snapshot() must show zero for all nine."""
    budget = ModelVisibleTurnBudget()
    snap = budget.snapshot()
    assert set(snap.keys()) == set(ACCOUNT_RESERVES.keys())
    for account, spent in snap.items():
        assert spent == 0, f"account {account} should start at 0"


def test_memory_account_is_chargeable() -> None:
    """The memory account must accept a renderer-minted charge."""
    budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    view = renderer.render_plain("memory block body")
    ok = budget.charge("memory", view)
    assert ok.cost == len("memory block body")
    assert budget.spent("memory") == ok.cost
    assert budget.remaining("memory") == RESERVE_MEMORY - ok.cost


def test_recent_history_account_is_chargeable() -> None:
    """The recent_history account must accept a renderer-minted charge."""
    budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    view = renderer.render_plain("recent history body")
    ok = budget.charge("recent_history", view)
    assert ok.cost == len("recent history body")
    assert budget.spent("recent_history") == ok.cost


def test_budget_docstring_says_nine_not_six() -> None:
    """G11 fix: class docstring must say 'Nine-account', not 'Six-account'."""
    doc = ModelVisibleTurnBudget.__doc__ or ""
    assert "Nine-account" in doc
    assert "Six-account" not in doc


def test_budget_no_stale_six_account_in_module() -> None:
    """No stale 'Six-account' reference in model_view_budget.py source."""
    source = inspect.getsource(
        __import__(
            "app.services.reader_record_ask.model_view_budget",
            fromlist=["__source__"],
        )
    )
    assert "Six-account" not in source
    assert "six-account" not in source.lower()
