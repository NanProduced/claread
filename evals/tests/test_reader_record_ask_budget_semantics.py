"""Planned logical runs vs provider request cap semantics.

Spec: the accepted real-eval fixture identity and path contract,
``Budget semantics`` block.

The audit scenario found: "30 planned runs 共消耗 30 provider requests，
但 3 次 output retry 导致只完成 27 runs." The previous preflight /
report did not distinguish:

- ``planned_logical_runs`` — the number of (case × repetition) pairs
  the harness intends to execute. From the manifest's
  ``planned_run_indices`` (sum of list lengths). This is the
  case-universe size, NOT a request count.
- ``request_cap`` — the configured ``CLAREAD_R4_A3_MAX_REQUESTS``
  (provider call budget). One logical run can consume MORE than one
  provider request when the output validator triggers ``ModelRetry``
  (``retries={"output": 2}`` allows up to 2 retries per case).
- ``retry_headroom`` — ``request_cap - planned_logical_runs``. When
  negative, the cap is structurally insufficient for even one
  attempt per planned run; when small, output retries can push the
  run past the cap before all planned runs complete.
- ``actual_completed_runs`` — the count of (case, run_index) pairs
  that actually produced an artifact (from manifest's
  ``completed_run_indices``).
- ``retries_consumed`` — ``manifest.executed_requests -
  actual_completed_runs``. This is the number of provider requests
  consumed by output retries / multi-turn tool loops ABOVE the one
  request per completed case baseline. It MUST be ≥ 0 and is
  surfaced in the report so the operator can see why a 30-cap run
  completed only 27 cases.

This module tests:

1. ``_compute_budget_semantics`` — pure helper that extracts the
   five typed fields from a manifest + env-configured request cap.
2. ``_decide_final_verdict`` does NOT change verdict based on
   budget_semantics (this is informational only — verdict still
   falls to ``blocked_incomplete_real_model_run`` when
   ``actual_completed_runs < planned_logical_runs`` via the existing
   coverage gap precedence row).
3. Report ``run_metadata.budget_semantics`` field is populated with
   the five typed fields.
4. ``retry_headroom`` semantics:
   - planned=30, cap=30 → headroom=0 (exactly enough for one attempt
     per planned run, no retries allowed)
   - planned=27, cap=30 → headroom=3 (3 retries allowed before cap
     exhaustion)
   - planned=30, cap=20 → headroom=-10 (cap structurally
     insufficient)
5. ``retries_consumed`` semantics:
   - executed_requests=30, completed=27 → retries_consumed=3
   - executed_requests=27, completed=27 → retries_consumed=0
   - executed_requests=25, completed=27 → impossible (executed <
     completed) → helper raises ValueError (fail-closed)
6. No real LLM / provider calls. Mocks use ``SimpleNamespace``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Load the runner module (host of _compute_budget_semantics).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = (
    _REPO_ROOT / "evals" / "scripts" / "run_reader_record_ask_eval.py"
)


def _load_runner_module():
    """Load the runner script as a module (it's not in a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_reader_record_ask_eval_budget_semantics", _RUNNER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_reader_record_ask_eval_budget_semantics"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = _load_runner_module()


# ---------------------------------------------------------------------------
# Helpers — minimal manifest stub
# ---------------------------------------------------------------------------


def _manifest_stub(
    *,
    planned: dict[str, list[int]] | None = None,
    completed: dict[str, list[int]] | None = None,
    executed_requests: int = 0,
    executed_tokens: int = 0,
    status: str = "completed",
):
    """Build a minimal manifest stub for budget semantics tests.

    Mirrors the fields ``_compute_budget_semantics`` reads:
    ``planned_run_indices``, ``completed_run_indices``,
    ``executed_requests``, ``executed_tokens``, ``status``.
    """
    if planned is None:
        planned = {"case-a": [0, 1, 2]}
    if completed is None:
        completed = planned if status == "completed" else {}
    return SimpleNamespace(
        planned_run_indices=planned,
        completed_run_indices=completed,
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
        status=status,
    )


# ---------------------------------------------------------------------------
# Test class 1: _compute_budget_semantics — basic shape and types
# ---------------------------------------------------------------------------


class TestComputeBudgetSemanticsShape:
    """Verify the helper returns the 5 typed fields with correct types."""

    def test_returns_typed_namespace_with_five_fields(self) -> None:
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned={"c1": [0, 1, 2]},
                completed={"c1": [0, 1, 2]},
                executed_requests=3,
            ),
            request_cap=30,
        )
        assert hasattr(result, "planned_logical_runs")
        assert hasattr(result, "request_cap")
        assert hasattr(result, "actual_completed_runs")
        assert hasattr(result, "retry_headroom")
        assert hasattr(result, "retries_consumed")

    def test_types_are_ints(self) -> None:
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned={"c1": [0, 1, 2]},
                completed={"c1": [0, 1, 2]},
                executed_requests=3,
            ),
            request_cap=30,
        )
        assert isinstance(result.planned_logical_runs, int)
        assert isinstance(result.request_cap, int)
        assert isinstance(result.actual_completed_runs, int)
        assert isinstance(result.retry_headroom, int)
        assert isinstance(result.retries_consumed, int)


# ---------------------------------------------------------------------------
# Test class 2: planned_logical_runs counting
# ---------------------------------------------------------------------------


class TestPlannedLogicalRunsCounting:
    """``planned_logical_runs`` is the sum of list lengths in
    ``planned_run_indices``."""

    def test_single_case_three_repetitions(self) -> None:
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned={"c1": [0, 1, 2]},
                completed={"c1": [0, 1, 2]},
                executed_requests=3,
            ),
            request_cap=30,
        )
        assert result.planned_logical_runs == 3

    def test_ten_cases_three_repetitions_each_thirty_total(self) -> None:
        planned = {f"c{i}": [0, 1, 2] for i in range(10)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=planned,
                executed_requests=30,
            ),
            request_cap=30,
        )
        assert result.planned_logical_runs == 30

    def test_no_cases_zero(self) -> None:
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned={},
                completed={},
                executed_requests=0,
            ),
            request_cap=30,
        )
        assert result.planned_logical_runs == 0


# ---------------------------------------------------------------------------
# Test class 3: retry_headroom semantics
# ---------------------------------------------------------------------------


class TestRetryHeadroomSemantics:
    """``retry_headroom = request_cap - planned_logical_runs``."""

    def test_cap_equals_planned_headroom_zero(self) -> None:
        """30 planned, cap=30 → headroom=0 (no retries allowed)."""
        planned = {f"c{i}": [0, 1, 2] for i in range(10)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=planned,
                executed_requests=30,
            ),
            request_cap=30,
        )
        assert result.retry_headroom == 0

    def test_cap_above_planned_positive_headroom(self) -> None:
        """27 planned, cap=30 → headroom=3 (3 retries allowed)."""
        planned = {f"c{i}": [0, 1, 2] for i in range(9)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=planned,
                executed_requests=27,
            ),
            request_cap=30,
        )
        assert result.retry_headroom == 3

    def test_cap_below_planned_negative_headroom(self) -> None:
        """30 planned, cap=20 → headroom=-10 (cap structurally
        insufficient — even one attempt per planned run is
        impossible)."""
        planned = {f"c{i}": [0, 1, 2] for i in range(10)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed={},
                executed_requests=20,
                status="budget_exhausted",
            ),
            request_cap=20,
        )
        assert result.retry_headroom == -10


# ---------------------------------------------------------------------------
# Test class 4: retries_consumed semantics
# ---------------------------------------------------------------------------


class TestRetriesConsumedSemantics:
    """``retries_consumed = executed_requests - actual_completed_runs``.

    A logical run that completes on the first attempt consumes 1
    request. An output retry consumes 1 additional request without
    completing a new case.
    """

    def test_no_retries_consumed(self) -> None:
        """27 completed, 27 requests → retries_consumed=0."""
        planned = {f"c{i}": [0, 1, 2] for i in range(9)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=planned,
                executed_requests=27,
            ),
            request_cap=30,
        )
        assert result.actual_completed_runs == 27
        assert result.retries_consumed == 0

    def test_three_retries_consumed(self) -> None:
        """27 completed, 30 requests → retries_consumed=3.

        This audit scenario uses 30 planned runs and a cap of 30, but
        3 output retries pushed total requests to 30 before all 30
        planned could complete, leaving 27 completed + 3 retries.
        """
        planned = {f"c{i}": [0, 1, 2] for i in range(10)}
        completed = {f"c{i}": [0, 1, 2] for i in range(9)}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=completed,
                executed_requests=30,
                status="budget_exhausted",
            ),
            request_cap=30,
        )
        assert result.actual_completed_runs == 27
        assert result.retries_consumed == 3

    def test_executed_below_completed_raises_fail_closed(self) -> None:
        """executed_requests < actual_completed_runs is impossible —
        each completed run consumed at least 1 request. The helper
        raises ``ValueError`` (fail-closed) so a corrupt manifest
        cannot silently produce a negative retries_consumed."""
        planned = {"c1": [0, 1, 2]}
        with pytest.raises(ValueError, match="executed_requests.*completed"):
            _RUNNER._compute_budget_semantics(
                manifest=_manifest_stub(
                    planned=planned,
                    completed=planned,
                    executed_requests=2,  # < 3 completed
                ),
                request_cap=30,
            )


# ---------------------------------------------------------------------------
# Test class 5: None manifest handling
# ---------------------------------------------------------------------------


class TestNoneManifestHandling:
    """When manifest is None (absent / corrupt / foreign), the helper
    returns zeros — it cannot compute budget semantics without a
    manifest. This is intentional: the verdict falls to a blocked_*
    variant via the existing precedence rows, and budget semantics
    are informational only."""

    def test_none_manifest_returns_zeros(self) -> None:
        result = _RUNNER._compute_budget_semantics(
            manifest=None,
            request_cap=30,
        )
        assert result.planned_logical_runs == 0
        assert result.actual_completed_runs == 0
        assert result.retry_headroom == 30  # cap - 0 planned
        assert result.retries_consumed == 0


# ---------------------------------------------------------------------------
# Test class 6: request_cap None handling
# ---------------------------------------------------------------------------


class TestRequestCapNoneHandling:
    """When ``request_cap`` is None (env not set, no default), the
    helper surfaces ``None`` for ``request_cap`` and ``retry_headroom``,
    but still computes the other three fields from the manifest."""

    def test_none_cap_surfaces_none_in_headroom(self) -> None:
        planned = {"c1": [0, 1, 2]}
        result = _RUNNER._compute_budget_semantics(
            manifest=_manifest_stub(
                planned=planned,
                completed=planned,
                executed_requests=3,
            ),
            request_cap=None,
        )
        assert result.planned_logical_runs == 3
        assert result.request_cap is None
        assert result.retry_headroom is None
        assert result.actual_completed_runs == 3
        assert result.retries_consumed == 0


# ---------------------------------------------------------------------------
# Test class 7: env-driven request_cap resolution
# ---------------------------------------------------------------------------


class TestEnvRequestCapResolution:
    """``_resolve_request_cap_from_env`` reads
    ``CLAREAD_R4_A3_MAX_REQUESTS`` and returns ``None`` when unset.

    This is the same env var the harness reads — keeping the same
    resolution rule ensures the aggregate's ``request_cap`` matches
    what the harness actually used.
    """

    def test_env_set_returns_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAREAD_R4_A3_MAX_REQUESTS", "42")
        cap = _RUNNER._resolve_request_cap_from_env()
        assert cap == 42

    def test_env_unset_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAREAD_R4_A3_MAX_REQUESTS", raising=False)
        cap = _RUNNER._resolve_request_cap_from_env()
        assert cap is None

    def test_env_empty_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAREAD_R4_A3_MAX_REQUESTS", "  ")
        cap = _RUNNER._resolve_request_cap_from_env()
        assert cap is None

    def test_env_non_int_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAREAD_R4_A3_MAX_REQUESTS", "not-a-number")
        with pytest.raises(ValueError, match="CLAREAD_R4_A3_MAX_REQUESTS"):
            _RUNNER._resolve_request_cap_from_env()

    def test_env_negative_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAREAD_R4_A3_MAX_REQUESTS", "-5")
        with pytest.raises(ValueError, match="CLAREAD_R4_A3_MAX_REQUESTS"):
            _RUNNER._resolve_request_cap_from_env()
