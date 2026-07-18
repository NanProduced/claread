"""PhasePlanner — explicit case manifest + fixed repetitions + evaluator-based
failure selection + budget stop result.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirements: PhasePlanner 深模块 + 固定重复（P0-2, P0-5）, evaluator-based
Phase 2/3 失败选择（P0-3）.

Prior to this module, the harness:
- Selected Phase 1 cases by sorting on ``question_category`` and picking
  the first one — implicit, unauditable, missed BBC core question coverage.
- Broke on the first ``finalized_status='ok'`` so repetition was
  effectively 1 (no way to measure hallucination rate).
- Selected Phase 2/3 cases by looking only at terminal failures
  (exception / finalized_status != ok / final_text empty), missing
  content-quality failures (unsupported ``2025`` year token, missing
  cities, region-as-city type confusion, count mismatch, whole-sentence
  English).

This module exposes a small interface (``cases_to_run`` /
``repetitions`` / ``budget_stop_result``) over a robust implementation
that:

- Pulls Phase 1 cases from the dataset's explicit ``phase_tags`` field
  (``real_phase1`` tag), not from an implicit sort.
- Runs each selected case ``repetitions`` times (default 3), with
  ``run_index`` 0..N-1, never breaking early on first success.
- Selects Phase 2 cases from the prior run's *evaluator results*
  (``is_content_failure``), not from terminal status alone. A
  ``finalized_status='ok'`` artifact with an unsupported ``2025`` year
  token is correctly selected for Phase 2.
- Records :class:`BudgetStopResult` when the global request/token budget
  is exhausted, with the remaining cases/run_indices explicitly listed
  so the report does not silently treat missing runs as passes.
- Excludes ``offline_only`` cases (e.g. ``known_bbc`` cases pending
  R4-A4 trusted-source-metadata seam) from real-model runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from claread_eval.reader_record_ask.evaluation import (
    PriorEvalResults,
    any_repetition_content_failure,
)

if TYPE_CHECKING:
    from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
    from claread_eval.reader_record_ask.schema import (
        ReaderRecordAskR4A3Case,
        ReaderRecordAskR4A3Dataset,
    )


# ---------------------------------------------------------------------------
# Recognized phase tags
# ---------------------------------------------------------------------------

# Case is a candidate for Phase 1 real-model runs.
PHASE_TAG_REAL_PHASE1 = "real_phase1"
# Case is evaluator-only — never selected for real-model runs. Used for
# ``known_bbc`` cases until R4-A4 lands the trusted-source-metadata seam.
PHASE_TAG_OFFLINE_ONLY = "offline_only"
# Case is expected to fail in Phase 1 and enter Phase 2 (informational;
# the actual Phase 2 selection is driven by evaluator results, not by
# this tag — but the tag makes the dataset's intent auditable).
PHASE_TAG_PHASE2_CANDIDATE = "targeted_phase2_candidate"

# Default number of independent repetitions per case in Phase 1.
# Spec: "默认 3 次".
DEFAULT_PHASE1_REPETITIONS = 3

# Hard cap on total independent runs in Phase 1.
# Spec: "共最多 10 cases × 3 repetitions = 30 independent runs".
MAX_PHASE1_INDEPENDENT_RUNS = 30


# ---------------------------------------------------------------------------
# Budget stop result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetStopResult:
    """Recorded when the global request/token budget is exhausted.

    Spec: "只有达到全局硬预算时才可停止，停止必须记录：
    budget_exhausted=True、未执行 case/run_index、已执行请求数/token".
    """

    budget_exhausted: bool
    executed_requests: int
    executed_tokens: int | None
    remaining_cases: list[str] = field(default_factory=list)
    remaining_run_indices: dict[str, list[int]] = field(default_factory=dict)
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if self.budget_exhausted and not self.stop_reason:
            object.__setattr__(
                self, "stop_reason", "budget_exhausted"
            )


# ---------------------------------------------------------------------------
# PhasePlanner
# ---------------------------------------------------------------------------


class PhasePlanner:
    """Explicit case manifest + fixed repetitions + evaluator-based
    failure selection + budget stop result.

    Construction:

    - Phase 1: ``PhasePlanner(dataset=..., phase=1, repetitions=3)``.
      ``cases_to_run`` returns all cases with ``phase_tags`` containing
      ``real_phase1`` (excluding ``offline_only``), up to the
      :data:`MAX_PHASE1_INDEPENDENT_RUNS` cap on ``cases * repetitions``.

    - Phase 2: ``PhasePlanner(dataset=..., phase=2, repetitions=1,
      prior_artifacts=p1_arts, prior_eval_results=p1_evals)``.
      ``cases_to_run`` returns the subset of Phase 1 cases whose prior
      evaluator results flagged a content-quality failure
      (``is_content_failure`` returns ``True``).

    - Phase 3: ``PhasePlanner(dataset=..., phase=3, repetitions=1,
      prior_artifacts=p2_arts, prior_eval_results=p2_evals)``. Same
      selection rule as Phase 2 but over Phase 2 results.

    ``budget_stop_result`` is ``None`` until the harness calls
    :meth:`record_budget_stop`; the planner itself does not track live
    usage (that is the :class:`BudgetedUsageModel`'s job).
    """

    def __init__(
        self,
        dataset: ReaderRecordAskR4A3Dataset,
        phase: int,
        *,
        repetitions: int | None = None,
        prior_artifacts: list[RawArtifact] | None = None,
        prior_eval_results: PriorEvalResults | None = None,
        max_independent_runs: int = MAX_PHASE1_INDEPENDENT_RUNS,
    ) -> None:
        if phase not in (1, 2, 3):
            raise ValueError(
                f"phase must be 1, 2, or 3, got {phase!r}"
            )
        # Phase-dependent default: Phase 1 = 3 (measure hallucination
        # rate over independent reps); Phase 2/3 = 1 (a single re-run
        # with the upgraded model is enough to confirm the fix).
        if repetitions is None:
            repetitions = (
                DEFAULT_PHASE1_REPETITIONS if phase == 1 else 1
            )
        if repetitions < 1:
            raise ValueError(
                f"repetitions must be >= 1, got {repetitions}"
            )
        if phase != 1 and prior_eval_results is None:
            raise ValueError(
                f"phase {phase} requires prior_eval_results "
                "(evaluator results from the prior phase)"
            )

        self._dataset = dataset
        self._phase = phase
        self._repetitions = repetitions
        self._prior_artifacts = prior_artifacts or []
        self._prior_eval_results = prior_eval_results or {}
        self._max_independent_runs = (
            max_independent_runs if phase == 1 else len(dataset.cases) * repetitions
        )
        self._budget_stop_result: BudgetStopResult | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def phase(self) -> int:
        return self._phase

    @property
    def repetitions(self) -> int:
        """Fixed repetitions per case (P0-2).

        Phase 1: defaults to 3, never breaks early on first success.
        Phase 2/3: defaults to 1 (a single re-run with the upgraded
        model is usually enough to confirm the fix).
        """
        return self._repetitions

    @property
    def cases_to_run(self) -> list[ReaderRecordAskR4A3Case]:
        """Cases this phase should run, in dataset order.

        Phase 1: cases with ``real_phase1`` tag, excluding ``offline_only``.
        Phase 2/3: cases whose prior evaluator results flagged a
        content-quality failure.
        """
        if self._phase == 1:
            return self._select_phase1_cases()
        return self._select_failure_cases_from_prior()

    @property
    def budget_stop_result(self) -> BudgetStopResult | None:
        """``None`` until :meth:`record_budget_stop` is called."""
        return self._budget_stop_result

    @property
    def prior_artifacts(self) -> list[RawArtifact]:
        return list(self._prior_artifacts)

    @property
    def prior_eval_results(self) -> PriorEvalResults:
        return {k: list(v) for k, v in self._prior_eval_results.items()}

    # ------------------------------------------------------------------
    # Budget stop recording (called by harness, not by the planner itself)
    # ------------------------------------------------------------------

    def record_budget_stop(
        self,
        *,
        executed_requests: int,
        executed_tokens: int | None,
        remaining_cases: list[str],
        remaining_run_indices: dict[str, list[int]],
        stop_reason: str = "budget_exhausted",
    ) -> BudgetStopResult:
        """Record that the global budget was exhausted.

        The harness calls this when the :class:`BudgetedUsageModel` raises
        :class:`BudgetExhaustedError`. The planner records the remaining
        work so the aggregate report can mark missing runs as
        ``budget_exhausted=True`` (NOT as passes).
        """
        self._budget_stop_result = BudgetStopResult(
            budget_exhausted=True,
            executed_requests=executed_requests,
            executed_tokens=executed_tokens,
            remaining_cases=list(remaining_cases),
            remaining_run_indices={
                k: list(v) for k, v in remaining_run_indices.items()
            },
            stop_reason=stop_reason,
        )
        return self._budget_stop_result

    # ------------------------------------------------------------------
    # Internal: Phase 1 selection
    # ------------------------------------------------------------------

    def _select_phase1_cases(self) -> list[ReaderRecordAskR4A3Case]:
        """Phase 1: cases tagged ``real_phase1``, excluding ``offline_only``.

        Applies the global ``max_independent_runs`` cap on
        ``len(cases) * repetitions``. The cap is computed as
        ``allowed_case_count = max_independent_runs // repetitions``
        (integer division). When the eligible case count is exactly
        ``allowed_case_count`` (i.e. ``cases * reps == max``), the cap
        is NOT exceeded and no :class:`BudgetStopResult` is recorded
        (P0-1 exact-cap fix). When the eligible count exceeds
        ``allowed_case_count``, the surplus cases are recorded as
        ``remaining`` in a :class:`BudgetStopResult`.

        ``offline_only`` cases never enter ``selected`` or ``remaining``
        — they are excluded before the cap is applied.
        """
        # Step 1: build eligible cases (real_phase1 AND NOT offline_only),
        # preserving dataset order. The eligible list is the universe
        # from which the cap selects.
        eligible: list[ReaderRecordAskR4A3Case] = [
            case
            for case in self._dataset.cases
            if PHASE_TAG_REAL_PHASE1 in case.phase_tags
            and PHASE_TAG_OFFLINE_ONLY not in case.phase_tags
        ]

        # Step 2: compute the integer case budget. Integer division is
        # correct: if max=30 and reps=3, allowed=10 cases exactly fill
        # the cap (10*3=30) with no remainder — no truncation.
        allowed_case_count = (
            self._max_independent_runs // self._repetitions
            if self._repetitions > 0
            else 0
        )

        # Step 3: selected = eligible[:allowed], remaining = eligible[allowed:].
        selected = eligible[:allowed_case_count]
        remaining = eligible[allowed_case_count:]

        # Step 4: only record a BudgetStopResult when remaining is
        # non-empty. The cap is NOT triggered when eligible count
        # exactly equals allowed_case_count (P0-1 exact-cap fix).
        if remaining:
            remaining_run_indices: dict[str, list[int]] = {
                case.id: list(range(self._repetitions)) for case in remaining
            }
            self._budget_stop_result = BudgetStopResult(
                budget_exhausted=True,
                executed_requests=0,
                executed_tokens=0,
                remaining_cases=[case.id for case in remaining],
                remaining_run_indices=remaining_run_indices,
                stop_reason="phase1_independent_run_cap",
            )
        return selected

    # ------------------------------------------------------------------
    # Internal: Phase 2/3 selection
    # ------------------------------------------------------------------

    def _select_failure_cases_from_prior(self) -> list[ReaderRecordAskR4A3Case]:
        """Phase 2/3: cases whose prior eval results flagged content failure.

        P0-2 multi-repetition fix: a case is selected if ANY repetition
        produced a content-quality failure. Prior results are stored
        per-repetition (``case_id -> list[list[EvalDimensionResult]]``),
        so a fail-then-pass-then-pass sequence still triggers Phase 2
        selection. The prior shape was ``case_id -> list[EvalDimensionResult]``
        which silently kept only the last repetition's result — that
        masked intermittent hallucination failures.

        Uses :func:`any_repetition_content_failure` so a
        ``finalized_status='ok'`` artifact with an unsupported ``2025``
        year token is correctly selected (the temporal evaluator fails
        → content failure → selected for Phase 2). A prior run whose
        only failure is ``usage_observability`` is NOT selected (spec:
        "默认不要仅因 usage 缺失升级模型").
        """
        prior_failed_case_ids = {
            case_id
            for case_id, reps in self._prior_eval_results.items()
            if any_repetition_content_failure(reps)
        }
        # Preserve dataset order for stable execution.
        return [
            case
            for case in self._dataset.cases
            if case.id in prior_failed_case_ids
        ]
