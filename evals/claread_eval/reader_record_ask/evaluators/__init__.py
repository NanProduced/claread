"""R4-A3 reader-record-ask 11-dimension deterministic evaluators.

Public API:
- :class:`RawArtifact` (+ :class:`RawEvidenceObservation`, :class:`RawUsage`)
  — evaluator input, a serializable projection of
  :class:`ReadingRecordAskRunResult`.
- :class:`EvalDimensionResult` — evaluator output contract.
- 11 ``evaluate_<dimension>`` functions, each with the unified signature
  ``def evaluate_<dim>(case, artifact) -> EvalDimensionResult``.
  :func:`evaluate_entity_precision` additionally accepts an optional
  ``llm_judge`` callable that may only supplement (never override a
  deterministic failure).
- :func:`aggregate_results` — build an :class:`AggregatedReport` from
  per-(case, run) :class:`CaseEvalResult` values.

Key invariant: a deterministic ``passed=False`` is never overridden by
an LLM judge. The aggregator uses ``passed`` as the single source of
truth; ``llm_judge_used`` / ``llm_judge_note`` are recording-only.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.aggregator import (
    AggregatedReport,
    CaseEvalResult,
    FailureCluster,
    aggregate_results,
)
from claread_eval.reader_record_ask.evaluators.answer_success import (
    evaluate_answer_success,
)
from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawEvidenceObservation,
    RawUsage,
)
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

__all__ = [
    # Artifact
    "RawArtifact",
    "RawEvidenceObservation",
    "RawUsage",
    # Result
    "EvalDimensionResult",
    # 11 evaluators
    "evaluate_answer_success",
    "evaluate_context_support",
    "evaluate_unsupported_temporal_claims",
    "evaluate_numeric_grounding",
    "evaluate_entity_precision",
    "evaluate_exhaustive_completeness",
    "evaluate_instruction_following",
    "evaluate_language_consistency",
    "evaluate_evidence_minimality",
    "evaluate_tool_decision",
    "evaluate_usage_observability",
    # Aggregator
    "CaseEvalResult",
    "AggregatedReport",
    "FailureCluster",
    "aggregate_results",
]
