"""T4.2a-R2-R1 durable execution budget guard for the reader enhancement pipeline.

Provides a deterministic, **cross-pipeline-run** budget tracker that limits
the number of effective LLM calls per layer (translation, vocabulary,
grammar).

T4.2a-R2-R1 fix: the budget is no longer an in-memory counter recreated
every ``runner.run()``. It is loaded from durable DB state
(``reader_jobs.attempt_count`` / ``reader_jobs.max_attempts``) so that
multiple ``run()`` calls within the same WorkerLoop cycle cannot exceed
the hard cost ceiling.

Design:
- ``planned_calls``: number of jobs created at bootstrap for each layer
  (all jobs for the active route/fingerprint, including terminal).
- ``max_effective_calls``: ``SUM(max_attempts)`` across all layer jobs.
  Aligns with the actual retry semantics (``max_attempts=3`` → 3 calls
  per job), replacing the previous ``planned * 2`` which was too low.
- ``consumed_calls``: ``SUM(attempt_count)`` across all layer jobs.
  ``attempt_count`` is incremented atomically at claim time, before the
  LLM call. This is the durable, authoritative consumed count.

Budget check/reservation happens before the executor/LLM call. When a
layer's budget is exhausted, the pipeline runner reports
``budget_denied`` (not ``no_job``) so the event is observable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import asyncpg

# Layer names aligned with worker_type mapping in pipeline_runner.
BudgetLayer = Literal["translation", "vocabulary", "grammar"]

# Worker type → budget layer mapping. ``display_title`` is excluded
# because it is a single-call metadata job, not a per-layer enhancement
# with retry risk.
WORKER_TYPE_TO_BUDGET_LAYER: dict[str, BudgetLayer] = {
    "translation": "translation",
    "translation_batch": "translation",
    "vocabulary": "vocabulary",
    "vocabulary_batch": "vocabulary",
    "grammar_bundle": "grammar",
    "grammar_bundle_window": "grammar",
}

# Outcomes that consume budget (LLM call was made or attempted).
BUDGET_CONSUMING_OUTCOMES: frozenset[str] = frozenset({
    "succeeded",
    "retry_later",
    "failed_terminal",
})

# Outcomes that do NOT consume budget (no LLM call was made).
NON_BUDGET_CONSUMING_OUTCOMES: frozenset[str] = frozenset({
    "no_job",
    "superseded",
    "budget_denied",
})

# Job type → budget layer mapping for DB queries.
_JOB_TYPE_TO_BUDGET_LAYER: dict[str, BudgetLayer] = {
    "translate_unit": "translation",
    "translate_article": "translation",
    "build_vocabulary_layer": "vocabulary",
    "build_vocabulary_layer_article": "vocabulary",
    "build_grammar_bundle": "grammar",
    "build_grammar_bundle_window": "grammar",
}


@dataclass(frozen=True, slots=True)
class ExecutionBudgetSnapshot:
    """Immutable snapshot of budget state for a single layer."""

    planned_calls: int
    max_effective_calls: int
    consumed_calls: int
    remaining_calls: int
    exhausted: bool


@dataclass(frozen=True, slots=True)
class DurableBudgetLoadResult:
    """Result of loading durable budget state from the database.

    ``layer_snapshots`` maps each budget layer to its durable snapshot.
    ``non_superseded_fingerprints`` records the sorted tuple of
    operation_fingerprint values that were included in the budget
    calculation, for observability. T4.2a-R2-R2: this is a *set*, not
    a single "active" fingerprint — the budget conservatively
    aggregates across all non-superseded fingerprints because the
    existing schema cannot reliably determine a single active
    fingerprint during a route cutover. The tuple is sorted for
    determinism (no last-wins dependency on DB row order).
    """

    layer_snapshots: dict[str, ExecutionBudgetSnapshot]
    non_superseded_fingerprints: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class ExecutionBudget:
    """Cross-pipeline-run durable budget tracker.

    T4.2a-R2-R1: the budget is loaded from ``reader_jobs.attempt_count``
    and ``reader_jobs.max_attempts`` at the start of each ``run()``.
    The in-memory ``_consumed`` counter is only used for intra-run
    tracking (so a single run that dispatches multiple workers sees
    the budget decrement within the same run). The authoritative
    consumed count is always re-read from the DB on the next ``run()``.

    ``max_effective_calls = SUM(max_attempts)`` across all layer jobs
    for the active operation_fingerprint, replacing the previous
    ``planned * 2`` formula that was lower than ``max_attempts=3``.
    """

    _planned: dict[BudgetLayer, int] = field(default_factory=dict)
    _max: dict[BudgetLayer, int] = field(default_factory=dict)
    _consumed: dict[BudgetLayer, int] = field(default_factory=dict)
    # T4.2a-R2-R2: sorted tuple of non-superseded fingerprints per layer.
    # Not a single "active" fingerprint — see DurableBudgetLoadResult docs.
    _non_superseded_fingerprints: dict[BudgetLayer, tuple[str, ...]] = field(
        default_factory=dict
    )

    @classmethod
    def from_planned_calls(
        cls,
        planned_calls: dict[BudgetLayer, int],
        *,
        max_multiplier: int = 3,
    ) -> ExecutionBudget:
        """Create a budget from per-layer planned call counts (legacy/test path).

        ``max_effective_calls = planned_calls * max_multiplier``.
        The default ``max_multiplier=3`` aligns with ``max_attempts=3``
        in production. Tests that need the old ``*2`` behavior can pass
        ``max_multiplier=2`` explicitly.
        """
        budget = cls()
        for layer, count in planned_calls.items():
            budget._planned[layer] = count
            budget._max[layer] = count * max_multiplier
            budget._consumed[layer] = 0
        return budget

    @classmethod
    async def load_durable(
        cls,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
    ) -> DurableBudgetLoadResult:
        """Load durable budget state from ``reader_jobs``.

        Queries all enhancement jobs for the given record / base /
        generation, grouped by budget layer. For each layer:

        - ``planned_calls`` = COUNT(*) of all jobs (any status) for the
          non-superseded operation_fingerprint(s).
        - ``max_effective_calls`` = SUM(max_attempts).
        - ``consumed_calls`` = SUM(attempt_count).

        T4.2a-R2-R2: "active fingerprint" is now a **conservative
        sorted set** of all non-superseded fingerprints per layer, not
        a single last-wins value. The budget aggregates across all
        non-superseded fingerprints because the existing schema cannot
        reliably determine a single active fingerprint during a route
        cutover. Superseded jobs (stale route) are excluded.

        Returns a ``DurableBudgetLoadResult`` with per-layer snapshots
        and the sorted fingerprint tuples for observability.
        """
        rows = await conn.fetch(
            """
            SELECT
                job_type,
                operation_fingerprint,
                COUNT(*) AS planned,
                COALESCE(SUM(max_attempts), 0) AS max_calls,
                COALESCE(SUM(attempt_count), 0) AS consumed_calls
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND status != 'superseded'
              AND job_type = ANY($4::text[])
            GROUP BY job_type, operation_fingerprint
            ORDER BY operation_fingerprint ASC
            """,
            record_id,
            base_id,
            expected_generation,
            list(_JOB_TYPE_TO_BUDGET_LAYER.keys()),
        )

        # Aggregate per layer. Multiple fingerprints per layer are summed
        # (conservative: the budget covers all non-superseded
        # fingerprints, which is safe during a route cutover).
        layer_planned: dict[BudgetLayer, int] = {
            "translation": 0, "vocabulary": 0, "grammar": 0}
        layer_max: dict[BudgetLayer, int] = {
            "translation": 0, "vocabulary": 0, "grammar": 0}
        layer_consumed: dict[BudgetLayer, int] = {
            "translation": 0, "vocabulary": 0, "grammar": 0}
        layer_fingerprints: dict[BudgetLayer, set[str]] = {
            "translation": set(), "vocabulary": set(), "grammar": set(),
        }

        for row in rows:
            job_type = str(row["job_type"])
            layer = _JOB_TYPE_TO_BUDGET_LAYER.get(job_type)
            if layer is None:
                continue
            layer_planned[layer] += int(row["planned"])
            layer_max[layer] += int(row["max_calls"])
            layer_consumed[layer] += int(row["consumed_calls"])
            fp = str(row["operation_fingerprint"])
            if fp:
                layer_fingerprints[layer].add(fp)

        snapshots: dict[str, ExecutionBudgetSnapshot] = {}
        fingerprint_sets: dict[str, tuple[str, ...]] = {}
        for layer in ("translation", "vocabulary", "grammar"):
            planned = layer_planned[layer]
            maximum = layer_max[layer]
            consumed = layer_consumed[layer]
            snapshots[layer] = ExecutionBudgetSnapshot(
                planned_calls=planned,
                max_effective_calls=maximum,
                consumed_calls=consumed,
                remaining_calls=max(0, maximum - consumed),
                exhausted=consumed >= maximum if maximum > 0 else False,
            )
            # Sorted tuple for determinism — no DB row order dependency.
            fingerprint_sets[layer] = tuple(sorted(layer_fingerprints[layer]))

        return DurableBudgetLoadResult(
            layer_snapshots=snapshots,
            non_superseded_fingerprints=fingerprint_sets,
        )

    def load_from_durable(self, result: DurableBudgetLoadResult) -> None:
        """Populate this in-memory budget from a durable load result.

        Called at the start of each ``run()`` after
        ``load_durable`` fetches the DB state. The in-memory
        ``_consumed`` is set to the durable consumed count so that
        intra-run decrements start from the correct baseline.
        """
        self._planned.clear()
        self._max.clear()
        self._consumed.clear()
        self._non_superseded_fingerprints.clear()
        for layer_str, snap in result.layer_snapshots.items():
            layer: BudgetLayer = layer_str  # type: ignore[assignment]
            if snap.max_effective_calls > 0 or snap.planned_calls > 0:
                self._planned[layer] = snap.planned_calls
                self._max[layer] = snap.max_effective_calls
                self._consumed[layer] = snap.consumed_calls
        for layer_str, fps in result.non_superseded_fingerprints.items():
            if fps:
                self._non_superseded_fingerprints[layer_str] = fps  # type: ignore[index]

    def can_consume(self, layer: BudgetLayer) -> bool:
        """Check if the layer has remaining budget."""
        if layer not in self._max:
            return False
        return self._consumed[layer] < self._max[layer]

    def consume(self, layer: BudgetLayer) -> bool:
        """Consume one call for the layer. Returns True if consumed.

        Returns False if the budget is already exhausted (no-op).
        """
        if not self.can_consume(layer):
            return False
        self._consumed[layer] += 1
        return True

    def is_exhausted(self, layer: BudgetLayer) -> bool:
        """Check if the layer's budget is exhausted.

        A layer with no budget (``max == 0``) is NOT exhausted — it
        has no jobs, so there is nothing to exhaust. This prevents
        a layer with zero planned calls from triggering
        ``budget_exhausted``.
        """
        if layer not in self._max:
            return False
        if self._max[layer] == 0:
            return False
        return self._consumed[layer] >= self._max[layer]

    def any_exhausted(self) -> bool:
        """Check if any layer with jobs has an exhausted budget."""
        return any(
            self.is_exhausted(layer) for layer in self._max
        )

    def exhausted_layers(self) -> tuple[BudgetLayer, ...]:
        """Return the layers whose budget is exhausted (and had jobs)."""
        return tuple(
            layer for layer in self._max if self.is_exhausted(layer)
        )

    def has_active_jobs_for_layer(self, layer: BudgetLayer) -> bool:
        """Check if the layer has any planned calls (i.e. had/has jobs)."""
        return self._planned.get(layer, 0) > 0

    def snapshot(self, layer: BudgetLayer) -> ExecutionBudgetSnapshot:
        """Get an immutable snapshot of the layer's budget state."""
        planned = self._planned.get(layer, 0)
        maximum = self._max.get(layer, 0)
        consumed = self._consumed.get(layer, 0)
        return ExecutionBudgetSnapshot(
            planned_calls=planned,
            max_effective_calls=maximum,
            consumed_calls=consumed,
            remaining_calls=max(0, maximum - consumed),
            exhausted=consumed >= maximum if maximum > 0 else False,
        )

    def to_diagnostics(self) -> dict[str, dict[str, int | list[str]]]:
        """Serialize to a diagnostics-friendly dict for span metadata.

        T4.2a-R2-R2: includes ``non_superseded_fingerprints`` as a
        sorted list per layer for observability.
        """
        result: dict[str, dict[str, int | list[str]]] = {}
        for layer in self._max:
            snap = self.snapshot(layer)
            result[layer] = {
                "planned": snap.planned_calls,
                "max": snap.max_effective_calls,
                "consumed": snap.consumed_calls,
                "remaining": snap.remaining_calls,
                "non_superseded_fingerprints": list(
                    self._non_superseded_fingerprints.get(layer, ())
                ),
            }
        return result

    def non_superseded_fingerprints(
        self, layer: BudgetLayer
    ) -> tuple[str, ...]:
        """Return the sorted non-superseded fingerprints for the layer.

        T4.2a-R2-R2: this is a *set*, not a single active fingerprint.
        The budget conservatively aggregates across all non-superseded
        fingerprints. Returns an empty tuple if the layer has no jobs.
        """
        return self._non_superseded_fingerprints.get(layer, ())
