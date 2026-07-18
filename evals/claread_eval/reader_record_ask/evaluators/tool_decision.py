"""Dimension 10/11 — tool_decision.

Spec (``expect_tool_calls``):
- ``"forbidden"``: baseline sufficient → ``read_range_calls == 0`` AND
  ``search_current_article_calls == 0`` is correct; any call ⇒
  medium-severity failure.
- ``"optional"``: always pass; record the actual call counts in details.
- ``"required"``: baseline incomplete → model SHOULD have called
  ``read_range`` or ``search_current_article``; zero calls ⇒
  medium-severity failure (the model failed to expand coverage).

``details`` always carries both call counters so the report can show
the actual tool-decision telemetry next to the verdict.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskR4A3Case

DIMENSION = "tool_decision"


def evaluate_tool_decision(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    expect = case.expected.expect_tool_calls
    rr = artifact.read_range_calls
    sc = artifact.search_current_article_calls
    base = (
        f"read_range_calls={rr}, search_current_article_calls={sc}, "
        f"expect_tool_calls={expect!r}"
    )

    if expect == "forbidden":
        if rr == 0 and sc == 0:
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=True,
                severity="none",
                details=f"tool_decision: no calls as expected; {base}",
            )
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=False,
            severity="medium",
            details=f"tool_decision: expected no tool calls but got; {base}",
        )

    if expect == "optional":
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=True,
            severity="none",
            details=f"tool_decision: tool calls optional; {base}",
        )

    if expect == "required":
        if rr > 0 or sc > 0:
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=True,
                severity="none",
                details=f"tool_decision: tool calls made as required; {base}",
            )
        baseline_note = ""
        if artifact.baseline_is_complete is False:
            baseline_note = (
                "; baseline_is_complete=False (model should have expanded)"
            )
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=False,
            severity="medium",
            details=(
                f"tool_decision: expected tool calls but none made; "
                f"{base}{baseline_note}"
            ),
        )

    # Unknown policy — fail safe.
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=False,
        severity="medium",
        details=f"tool_decision: unknown expect_tool_calls={expect!r}; {base}",
    )
