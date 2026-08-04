"""Regression gate for the evals real-LLM fail-closed conftest.

Locks the three fail-closed properties of ``evals/tests/conftest.py``
without any real provider call, subprocess, or extra dependency. The
real conftest module is loaded from disk and its fixture functions are
driven directly with stub request/config objects:

1. A ``real_llm``-marked test is skipped unless ALL three gates hold:
   ``CLAREAD_ALLOW_REAL_LLM_TESTS=1``, non-empty
   ``CLAREAD_REAL_LLM_MODEL``, and a mark expression that is exactly
   ``real_llm``.
2. When the gate is closed, a blocked real-provider attempt recorded
   through ``app.llm.call_guard`` (the exact call every patched
   provider boundary stub makes) fails the test at teardown and the
   failure reports the attempt surface.
3. With all three gates open the fixtures permit entry — and this test
   still never touches a real provider.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "evals_conftest_under_test", _CONFTEST_PATH
)
assert _spec is not None and _spec.loader is not None
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)

_ALLOW_ENV = "CLAREAD_ALLOW_REAL_LLM_TESTS"
_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"
_MARKER = object()

call_guard = conftest._import_call_guard()
if call_guard is None:
    pytest.skip(
        "services/api app.llm.call_guard unavailable",
        allow_module_level=True,
    )


def _request_stub(*, marked: bool, markexpr: str) -> SimpleNamespace:
    """Minimal stand-in for the pytest FixtureRequest used by conftest."""

    class _Node:
        def get_closest_marker(self, name: str) -> object | None:
            return _MARKER if marked and name == "real_llm" else None

    class _Config:
        def getoption(self, name: str, default: object = None) -> object:
            return markexpr if name == "markexpr" else default

    return SimpleNamespace(node=_Node(), config=_Config())


def _set_gate_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow: bool,
    model: bool,
) -> None:
    if allow:
        monkeypatch.setenv(_ALLOW_ENV, "1")
    else:
        monkeypatch.delenv(_ALLOW_ENV, raising=False)
    if model:
        monkeypatch.setenv(_MODEL_ENV, "gate-probe-model")
    else:
        monkeypatch.delenv(_MODEL_ENV, raising=False)


def _expect_skip_reason(
    monkeypatch: pytest.MonkeyPatch, *, markexpr: str
) -> str:
    with pytest.raises(pytest.skip.Exception) as excinfo:
        conftest.skip_real_llm_tests.__wrapped__(
            _request_stub(marked=True, markexpr=markexpr)
        )
    return str(excinfo.value)


class TestTripleGateSkipSemantics:
    """Scenario 1 — each missing gate alone must skip a marked test."""

    def test_unmarked_test_is_never_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=False, model=False)
        # Returns None (no skip) for an unmarked test regardless of env.
        assert (
            conftest.skip_real_llm_tests.__wrapped__(
                _request_stub(marked=False, markexpr="")
            )
            is None
        )

    def test_missing_allow_env_skips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=False, model=True)
        reason = _expect_skip_reason(monkeypatch, markexpr="real_llm")
        assert _ALLOW_ENV in reason

    def test_missing_model_env_skips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=True, model=False)
        reason = _expect_skip_reason(monkeypatch, markexpr="real_llm")
        assert _MODEL_ENV in reason

    def test_inexact_markexpr_skips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=True, model=True)
        for markexpr in ("", "not real_llm", "real_llm and seam_pure_unit"):
            reason = _expect_skip_reason(monkeypatch, markexpr=markexpr)
            assert "-m real_llm" in reason


class TestGateOpenEntry:
    """Scenario 3 — all three gates open permits entry, still no real
    provider call (this test touches no provider at all)."""

    def test_all_three_gates_open_permits_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=True, model=True)
        request = _request_stub(marked=True, markexpr="real_llm")
        # The skip fixture must NOT raise.
        assert conftest.skip_real_llm_tests.__wrapped__(request) is None
        assert conftest._real_llm_gate_open(request, call_guard) is True
        # The blocking fixture yields cleanly and tears down clean.
        mp = pytest.MonkeyPatch()
        try:
            gen = conftest.fail_on_real_llm_attempts.__wrapped__(mp, request)
            next(gen)
            with pytest.raises(StopIteration):
                next(gen)
        finally:
            mp.undo()


class TestBlockedAttemptFailClosed:
    """Scenario 2 — a closed-gate test touching a blocked provider
    boundary fails and reports the attempt. ``block_real_llm_attempt``
    is exactly what every patched boundary stub calls."""

    def test_blocked_attempt_fails_teardown_and_reports_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=False, model=False)
        mp = pytest.MonkeyPatch()
        try:
            gen = conftest.fail_on_real_llm_attempts.__wrapped__(
                mp, _request_stub(marked=False, markexpr="")
            )
            next(gen)
            surface = "evals.fail_closed.regression_probe"
            with pytest.raises(call_guard.RealLLMCallBlockedError):
                call_guard.block_real_llm_attempt(surface)
            with pytest.raises(pytest.fail.Exception) as excinfo:
                next(gen)
            message = str(excinfo.value)
            assert "attempted to call a real LLM provider" in message
            assert surface in message
        finally:
            mp.undo()

    def test_teardown_without_attempts_is_clean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_gate_env(monkeypatch, allow=False, model=False)
        mp = pytest.MonkeyPatch()
        try:
            gen = conftest.fail_on_real_llm_attempts.__wrapped__(
                mp, _request_stub(marked=False, markexpr="")
            )
            next(gen)
            with pytest.raises(StopIteration):
                next(gen)
        finally:
            mp.undo()
