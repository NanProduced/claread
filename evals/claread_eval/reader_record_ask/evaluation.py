"""evaluate_artifact — single 11-dimension evaluator entrypoint.

Requirement: evaluator-based 失败选择.

Prior to this module, the harness and the aggregate script each duplicated
the list of 11 evaluators and applied them with slightly different
filtering. Worse, staged case selection only checked *terminal*
failures (exception / finalized_status != ok / final_text empty), missing
content-quality failures such as:

- ``2025`` unsupported claim (caught by ``unsupported_temporal_claims``)
- city enumeration missing (caught by ``exhaustive_completeness``)
- region leaked as city (caught by ``entity_precision``)
- one question became five (caught by ``instruction_following``)
- whole-sentence English (caught by ``language_consistency``)

This module exposes one deep module with a small interface:

- :func:`evaluate_artifact` — run all 11 evaluators on one (case, artifact)
  and return a list of :class:`EvalDimensionResult`. Used by both the
  harness and the aggregate script.
- :func:`is_content_failure` — True if any content-quality dimension
  failed. Used by :class:`PhasePlanner` to select follow-up cases. Returns
  ``False`` when only ``usage_observability`` failed.
- :func:`has_usage_gap_only` — True when ``usage_observability`` is the
  *only* failing dimension. The planner uses this to record an
  observability gap without triggering a model upgrade.

The 11 evaluators themselves live in :mod:`evaluators.*` — this module
is the *only* place that knows the canonical list and order.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from claread_eval.reader_record_ask.evaluators.answer_success import (
    evaluate_answer_success,
)
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.context_support import (
    evaluate_context_support,
)
from claread_eval.reader_record_ask.evaluators.entity_precision import (
    evaluate_entity_precision,
)
from claread_eval.reader_record_ask.evaluators.evidence_minimality import (
    evaluate_evidence_minimality,
)
from claread_eval.reader_record_ask.evaluators.exhaustive_completeness import (
    evaluate_exhaustive_completeness,
)
from claread_eval.reader_record_ask.evaluators.instruction_following import (
    evaluate_instruction_following,
)
from claread_eval.reader_record_ask.evaluators.language_consistency import (
    evaluate_language_consistency,
)
from claread_eval.reader_record_ask.evaluators.numeric_grounding import (
    evaluate_numeric_grounding,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.evaluators.tool_decision import (
    evaluate_tool_decision,
)
from claread_eval.reader_record_ask.evaluators.unsupported_temporal_claims import (
    evaluate_unsupported_temporal_claims,
)
from claread_eval.reader_record_ask.evaluators.usage_observability import (
    evaluate_usage_observability,
)

if TYPE_CHECKING:
    from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

# ---------------------------------------------------------------------------
# Canonical dimension set
# ---------------------------------------------------------------------------

# Order matters for stable report rendering. The spec lists the 11
# dimensions in this exact order.
DIMENSION_ORDER: tuple[str, ...] = (
    "answer_success",
    "context_support",
    "unsupported_temporal_claims",
    "numeric_grounding",
    "entity_precision",
    "exhaustive_completeness",
    "instruction_following",
    "language_consistency",
    "evidence_minimality",
    "tool_decision",
    "usage_observability",
)

# Dimensions whose failure indicates a content-quality problem and should
# trigger model-upgrade selection (spec: "default 不要仅因 usage 缺失升级模型").
CONTENT_QUALITY_DIMENSIONS: frozenset[str] = frozenset(
    d for d in DIMENSION_ORDER if d != "usage_observability"
)

# The observability-only dimension — its failure does NOT trigger model
# upgrade on its own, but is recorded in the report as a gap.
OBSERVABILITY_DIMENSIONS: frozenset[str] = frozenset({"usage_observability"})

# Type alias for per-repetition prior eval results (multi-repetition
# fix). Outer dict: case_id. Outer list: one entry per repetition
# (sorted by run_index). Inner list: 11 EvalDimensionResult for that
# repetition. Replaces the prior ``dict[str, list[EvalDimensionResult]]``
# shape which silently kept only the last repetition's result.
PriorEvalResults = dict[str, list[list[EvalDimensionResult]]]


# ---------------------------------------------------------------------------
# Single evaluator entrypoint
# ---------------------------------------------------------------------------

# Type alias for the optional LLM judge hook (entity_precision only).
# Kept here so callers don't need to import the evaluator module just for
# the signature.
LlmJudgeHook = Callable[[str, dict], dict]


def evaluate_artifact(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
    *,
    llm_judge: LlmJudgeHook | None = None,
) -> list[EvalDimensionResult]:
    """Run all 11 deterministic evaluators on one (case, artifact) pair.

    Returns a list of :class:`EvalDimensionResult` in :data:`DIMENSION_ORDER`.
    Both the harness and the aggregate script MUST call this
    entrypoint — they MUST NOT duplicate the evaluator list, because
    divergence between the two was the root cause of the bug
    (terminal-ok artifacts hiding content-quality failures).

    ``llm_judge`` is reserved for the ``entity_precision`` dimension and
    may only *supplement* (record a note). It can never flip a
    deterministic ``passed=False`` to ``True`` — see
    :mod:`evaluators.entity_precision` for the contract.
    """
    # Build a list of (dimension_name, result) pairs so we can assert the
    # evaluator returned the expected dimension (defensive — catches
    # silent renames inside an evaluator module).
    pairs: list[tuple[str, EvalDimensionResult]] = [
        (
            "answer_success",
            evaluate_answer_success(case, artifact),
        ),
        (
            "context_support",
            evaluate_context_support(case, artifact),
        ),
        (
            "unsupported_temporal_claims",
            evaluate_unsupported_temporal_claims(case, artifact),
        ),
        (
            "numeric_grounding",
            evaluate_numeric_grounding(case, artifact),
        ),
        (
            "entity_precision",
            evaluate_entity_precision(case, artifact, llm_judge=llm_judge),
        ),
        (
            "exhaustive_completeness",
            evaluate_exhaustive_completeness(case, artifact),
        ),
        (
            "instruction_following",
            evaluate_instruction_following(case, artifact),
        ),
        (
            "language_consistency",
            evaluate_language_consistency(case, artifact),
        ),
        (
            "evidence_minimality",
            evaluate_evidence_minimality(case, artifact),
        ),
        (
            "tool_decision",
            evaluate_tool_decision(case, artifact),
        ),
        (
            "usage_observability",
            evaluate_usage_observability(case, artifact),
        ),
    ]

    # Defensive: each evaluator must return its own dimension name. If an
    # evaluator is refactored and accidentally renames its dimension, we
    # want a loud failure here rather than a silent mismatch between the
    # harness's view and the aggregate's view.
    for expected_name, result in pairs:
        if result.dimension != expected_name:
            raise ValueError(
                f"evaluator for {expected_name!r} returned dimension="
                f"{result.dimension!r} — canonical list is out of sync"
            )

    return [result for _, result in pairs]


# ---------------------------------------------------------------------------
# Failure selection helpers (used by PhasePlanner)
# ---------------------------------------------------------------------------


def is_content_failure(dimensions: list[EvalDimensionResult]) -> bool:
    """True if any content-quality dimension failed.

    Returns ``False`` when *only* ``usage_observability`` failed — the
    spec explicitly says "默认不要仅因 usage 缺失升级模型".

    A terminal failure (exception, finalized_status != ok, final_text
    empty) is captured by ``answer_success`` returning ``passed=False``,
    so this function covers both terminal and content-quality failures
    in one check.
    """
    for dim in dimensions:
        if dim.dimension in CONTENT_QUALITY_DIMENSIONS and not dim.passed:
            return True
    return False


def has_usage_gap_only(dimensions: list[EvalDimensionResult]) -> bool:
    """True when ``usage_observability`` is the *only* failing dimension.

    The planner records this as an observability gap in the report
    without selecting the case for model upgrade.
    """
    has_content_failure = False
    has_usage_failure = False
    for dim in dimensions:
        if dim.dimension in OBSERVABILITY_DIMENSIONS:
            if not dim.passed:
                has_usage_failure = True
        else:
            if not dim.passed:
                has_content_failure = True
    return has_usage_failure and not has_content_failure


def any_repetition_content_failure(
    repetitions: list[list[EvalDimensionResult]],
) -> bool:
    """True if ANY repetition produced a content-quality failure.

    Spec: "任意一个 repetition 出现 content failure，则该 case 进入
    后续升级". This replaces the prior shape where only the last
    repetition's results were kept (silently masking intermittent
    hallucination failures like fail→pass→pass).

    ``repetitions`` is a list of per-repetition ``list[EvalDimensionResult]``
    (one inner list per run_index, sorted by run_index). An empty list
    returns ``False`` (no repetitions → no failure to select on).

    A repetition whose only failure is ``usage_observability`` does NOT
    count as a content failure (spec: "默认不要仅因 usage 缺失升级模型").
    """
    return any(is_content_failure(rep) for rep in repetitions)


def failed_dimensions(
    dimensions: list[EvalDimensionResult],
) -> list[EvalDimensionResult]:
    """Return only the failing dimensions (preserves order)."""
    return [dim for dim in dimensions if not dim.passed]


def dimension_by_name(
    dimensions: list[EvalDimensionResult],
    name: str,
) -> EvalDimensionResult | None:
    """Look up a single dimension result by name. None if not found."""
    for dim in dimensions:
        if dim.dimension == name:
            return dim
    return None
