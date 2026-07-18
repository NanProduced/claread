"""Structured evaluator result contract for the R4-A3 11-dimension evaluators.

Every evaluator returns :class:`EvalDimensionResult`. The aggregator
(:mod:`evaluators.aggregator`) consumes these to build per-dimension /
per-config / failure-cluster reports.

Key invariant (spec Requirement: 11 维确定性 evaluator): an LLM judge may
only *supplement* the ``entity_precision`` dimension and may NOT flip a
deterministic ``passed=False`` to ``True``. ``llm_judge_used`` /
``llm_judge_note`` are recording fields only; the aggregator ignores them
when counting pass/fail.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low", "none"]


class EvalDimensionResult(BaseModel):
    """One dimension's verdict for one (case, run) pair."""

    model_config = {"extra": "forbid"}

    dimension: str  # answer_success / context_support / ...
    passed: bool
    severity: Severity = "none"
    details: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    # Optional LLM judge signal (entity_precision only). The aggregator
    # MUST treat ``passed`` as authoritative even when
    # ``llm_judge_used=True`` and ``llm_judge_note`` is positive — a
    # deterministic failure is never overridden.
    llm_judge_used: bool = False
    llm_judge_note: str | None = None
