"""Structured result contract for the 11-dimension evaluators.

Every evaluator returns :class:`EvalDimensionResult`. The aggregator
(:mod:`evaluators.aggregator`) consumes these to build per-dimension /
per-config / failure-cluster reports.

Key invariant (spec Requirement: 11 维确定性 evaluator): an LLM judge may
only *supplement* the ``entity_precision`` dimension and may NOT flip a
deterministic ``passed=False`` to ``True``. ``llm_judge_used`` /
``llm_judge_note`` are recording fields only; the aggregator ignores them
when counting pass/fail.

The optional
``classification`` field carries a typed reason tag (e.g.
``"fact_not_supported"``, ``"instrumentation_incomplete"``,
``"baseline_unavailable"``). The aggregator / readiness audit consults
this field to distinguish instrumentation blockers from real model
failures WITHOUT parsing free-form ``details`` text. Dimensions other
than ``context_support`` leave this field ``None`` — the typed signal
is currently scoped to that dimension only.

The field is typed as :data:`ContextSupportClassification` (a closed
``Literal``) so any classification string not in the contract
vocabulary is rejected at the Pydantic model boundary. The Literal
and the three routing frozensets
(:data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` /
:data:`LEGACY_BLOCKER_CLASSIFICATIONS` /
:data:`MODEL_FAILURE_CLASSIFICATIONS`) live in
:mod:`evaluators.context_support_contract` — the SINGLE source of
truth shared by the evaluator, aggregator, and runner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    ContextSupportClassification,
)

Severity = Literal["high", "medium", "low", "none"]


class EvalDimensionResult(BaseModel):
    """One dimension's verdict for one (case, run) pair."""

    model_config = {"extra": "forbid"}

    dimension: str  # answer_success / context_support / ...
    passed: bool
    severity: Severity = "none"
    details: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    # Typed
    # classification tag from the closed
    # :data:`ContextSupportClassification` vocabulary. Currently
    # populated only by the ``context_support`` evaluator. Other
    # evaluators leave this as ``None``. The aggregator / readiness
    # audit uses this field to distinguish instrumentation blockers
    # (e.g. ``"instrumentation_incomplete"`` /
    # ``"baseline_unavailable"`` / ``"runtime_exception"``) from
    # real model failures (``"fact_not_supported"`` /
    # ``"fact_not_cited"``) WITHOUT parsing ``details`` strings.
    # ``None`` means "no typed classification — treat as ordinary
    # pass/fail". The closed Literal enforces that any string
    # outside the contract vocabulary is rejected at the Pydantic
    # model boundary — see :mod:`evaluators.context_support_contract`.
    classification: ContextSupportClassification | None = None
    # Optional LLM judge signal (entity_precision only). The aggregator
    # MUST treat ``passed`` as authoritative even when
    # ``llm_judge_used=True`` and ``llm_judge_note`` is positive — a
    # deterministic failure is never overridden.
    llm_judge_used: bool = False
    llm_judge_note: str | None = None
