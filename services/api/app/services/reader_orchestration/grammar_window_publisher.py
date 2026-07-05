"""GrammarWindowPublisher: multi-unit publish transaction for Z+ windows.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  - §3.3 unit-scoped publish (target_scope='unit', target_key=unit_id)
  - §8.4 publish transaction (manual transition validation + _apply_transition)
  - §8.5 lock coverage (plan → window → reader_jobs, all FOR UPDATE)

Key invariant:
  Window publisher **cannot** call the public ``transition()`` because
  ``transition()`` opens its own ``conn.transaction()`` and writes
  ``_insert_job_event``, which would split the publish transaction. Instead,
  the publisher manually replicates the validation flow (status / job_type /
  target_type / fingerprint / lease / fence) and then calls the private
  ``_apply_transition`` + ``_insert_job_event`` within the same transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    IllegalTransitionError,
    ReaderJobRuntime,
    _assert_lease_valid,
)
from app.services.reader_orchestration.window_selector import (
    CandidateItem,
    SelectorLedger,
    select_candidates,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_JOB_TYPE,
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    ZPLUS_TARGET_TYPE,
)

GRAMMAR_NOTE_LAYER_TYPE = "grammar_note"
SENTENCE_ANALYSIS_LAYER_TYPE = "sentence_analysis"

# Layer operation fingerprints are unit-scoped to satisfy the
# ``uq_enhancement_layers_source_job_fingerprint UNIQUE (source_job_id,
# operation_fingerprint)`` constraint when publishing multiple unit-targeted
# layers from the same window job.
GRAMMAR_NOTE_WINDOW_FP = "grammar_note_window_v1"
SENTENCE_ANALYSIS_WINDOW_FP = "sentence_analysis_window_v1"

LAYER_SCHEMA_VERSION = 1

_ITEM_TYPES = (GRAMMAR_NOTE_LAYER_TYPE, SENTENCE_ANALYSIS_LAYER_TYPE)


@dataclass(frozen=True, slots=True)
class PublishedWindowResult:
    """Result of a window publish transaction."""

    accepted_count: int
    grammar_note_layer_ids: tuple[UUID, ...]
    sentence_analysis_layer_ids: tuple[UUID, ...]
    skipped: bool = False


class GrammarWindowPublisher:
    """Publish multi-unit grammar/sentence layers for a Z+ analysis window.

    Follows the existing ``GrammarBundleLayerPublisher._publish_unit_grammar_bundle_inner``
    pattern but operates on a window scope: multiple unit-targeted layers in a
    single transaction, plus ledger (typed counters) update.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_window_grammar_bundle(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        plan_id: UUID,
        window_id: UUID,
        candidates: list[CandidateItem],
    ) -> PublishedWindowResult:
        """§8.4 publish transaction.

        Manually replicates ``transition()`` validation (status / job_type /
        target_type / fingerprint / lease / fence) and then calls
        ``_apply_transition`` + ``_insert_job_event`` within the same
        transaction as the ledger + layers writes.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # 1. Lock plan ledger (FOR UPDATE)
                plan_row = await conn.fetchrow(
                    "SELECT * FROM layer_analysis_plans WHERE id = $1 FOR UPDATE",
                    plan_id,
                )
                if plan_row is None:
                    raise LookupError(f"plan {plan_id} not found")

                # 2. Lock window (FOR UPDATE, idempotency guard)
                window_row = await conn.fetchrow(
                    "SELECT * FROM analysis_windows WHERE id = $1 FOR UPDATE",
                    window_id,
                )
                if window_row is None:
                    raise LookupError(f"window {window_id} not found")
                if window_row["status"] != "running":
                    return PublishedWindowResult(
                        accepted_count=0,
                        grammar_note_layer_ids=(),
                        sentence_analysis_layer_ids=(),
                        skipped=True,
                    )

                # 3. Lock reader_jobs (FOR UPDATE, same as existing publisher)
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")

                # 4. Manual transition validation (must precede _apply_transition)
                if job_row["status"] != "claimed":
                    raise IllegalTransitionError(
                        f"expected status='claimed', got {job_row['status']!r}"
                    )
                if job_row["job_type"] != ZPLUS_GRAMMAR_JOB_TYPE:
                    raise IllegalTransitionError("job_type mismatch")
                if job_row["target_type"] != ZPLUS_TARGET_TYPE:
                    raise IllegalTransitionError("target_type mismatch")
                if (
                    job_row["operation_fingerprint"]
                    != ZPLUS_GRAMMAR_OPERATION_FINGERPRINT
                ):
                    raise IllegalTransitionError("operation_fingerprint mismatch")

                # 4c. _assert_lease_valid: module-level sync function, no await
                _assert_lease_valid(job_row, job_id, lease_token)

                # 4d. _validate_fence: instance method, await
                fence_error = await self._job_runtime._validate_fence(
                    conn, job_row
                )
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed: {fence_error}"
                    )

                # 5. Load ledger + run selector
                ledger = await self._load_ledger_from_plan(
                    conn, plan_row, job_row["base_id"]
                )
                window_budget = self._parse_window_budget(window_row)
                selection = select_candidates(
                    candidates,
                    ledger=ledger,
                    window_budget=window_budget,
                )

                # 6. Insert accepted layers (per-unit, target_scope='unit')
                grammar_layer_ids: list[UUID] = []
                sentence_layer_ids: list[UUID] = []
                accepted_by_unit: dict[str, dict[str, list[CandidateItem]]] = {}

                for candidate in selection.accepted:
                    unit_id = (
                        candidate.spans[0].get("unit_id", "")
                        if candidate.spans
                        else ""
                    )
                    if not unit_id:
                        continue
                    accepted_by_unit.setdefault(
                        unit_id,
                        {
                            GRAMMAR_NOTE_LAYER_TYPE: [],
                            SENTENCE_ANALYSIS_LAYER_TYPE: [],
                        },
                    )
                    accepted_by_unit[unit_id][candidate.item_type].append(candidate)

                published_at = datetime.now(UTC)
                window_index = int(window_row["window_index"])
                for unit_id, items in accepted_by_unit.items():
                    if items[GRAMMAR_NOTE_LAYER_TYPE]:
                        layer_id = await self._insert_layer(
                            conn,
                            layer_type=GRAMMAR_NOTE_LAYER_TYPE,
                            layer_fp_prefix=GRAMMAR_NOTE_WINDOW_FP,
                            job_row=job_row,
                            unit_id=unit_id,
                            candidates=tuple(items[GRAMMAR_NOTE_LAYER_TYPE]),
                            published_at=published_at,
                            plan_id=plan_id,
                            window_id=window_id,
                            window_index=window_index,
                        )
                        grammar_layer_ids.append(layer_id)
                    if items[SENTENCE_ANALYSIS_LAYER_TYPE]:
                        layer_id = await self._insert_layer(
                            conn,
                            layer_type=SENTENCE_ANALYSIS_LAYER_TYPE,
                            layer_fp_prefix=SENTENCE_ANALYSIS_WINDOW_FP,
                            job_row=job_row,
                            unit_id=unit_id,
                            candidates=tuple(items[SENTENCE_ANALYSIS_LAYER_TYPE]),
                            published_at=published_at,
                            plan_id=plan_id,
                            window_id=window_id,
                            window_index=window_index,
                        )
                        sentence_layer_ids.append(layer_id)

                # 7. Update ledger (JSONB full overwrite for typed counters)
                new_ledger = self._update_ledger(ledger, selection.accepted)
                if selection.accepted:
                    await conn.execute(
                        """
                        UPDATE layer_analysis_plans SET
                            budget_used = $2::jsonb,
                            published_anchor_counts_by_type = $3::jsonb,
                            published_dedup_keys_by_type = $4::jsonb,
                            published_pattern_keys_by_type = $5::jsonb,
                            density_by_record = $6::jsonb,
                            covered_window_ids = covered_window_ids || $7::jsonb,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        plan_id,
                        jsonb_param(new_ledger["budget_used"]),
                        jsonb_param(new_ledger["published_anchor_counts_by_type"]),
                        jsonb_param(new_ledger["published_dedup_keys_by_type"]),
                        jsonb_param(new_ledger["published_pattern_keys_by_type"]),
                        jsonb_param(new_ledger["density_by_record"]),
                        jsonb_param([str(window_id)]),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE layer_analysis_plans SET
                            no_op_windows = no_op_windows || $2::jsonb,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        plan_id,
                        jsonb_param([str(window_id)]),
                    )

                # 8. Update window status + coverage
                new_window_status = "completed" if selection.accepted else "no_op"
                await conn.execute(
                    """
                    UPDATE analysis_windows SET
                        status = $2,
                        coverage = $3::jsonb,
                        completed_at = NOW()
                    WHERE id = $1
                    """,
                    window_id,
                    new_window_status,
                    jsonb_param(
                        {"covered_unit_ids": list(accepted_by_unit.keys())}
                    ),
                )

                # 9. _apply_transition (status field + lease clearing only)
                rationale = (
                    "grammar_bundle_window_published"
                    if selection.accepted
                    else "grammar_bundle_window_no_op"
                )
                output_ref: dict[str, Any] = {
                    "grammar_note_layer_ids": [
                        str(lid) for lid in grammar_layer_ids
                    ],
                    "sentence_analysis_layer_ids": [
                        str(lid) for lid in sentence_layer_ids
                    ],
                    "accepted_count": len(selection.accepted),
                    "no_op": not selection.accepted,
                }
                updated_job = await self._job_runtime._apply_transition(
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref=output_ref,
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code=rationale,
                )

                # 10. Write reader_job_events
                await self._job_runtime._insert_job_event(
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": "claimed",
                        "target_status": "succeeded",
                        "rationale_code": rationale,
                    },
                )

                # 11. Update reader_runs
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    updated_job["run_id"],
                    published_at,
                )

                return PublishedWindowResult(
                    accepted_count=len(selection.accepted),
                    grammar_note_layer_ids=tuple(grammar_layer_ids),
                    sentence_analysis_layer_ids=tuple(sentence_layer_ids),
                )

    # ------------------------------------------------------------------
    # Ledger helpers
    # ------------------------------------------------------------------

    def _parse_window_budget(
        self, window_row: asyncpg.Record
    ) -> dict[str, int]:
        """Convert window_budget JSONB ``{type: {count: N}}`` → ``{type: N}``."""
        raw = window_row["window_budget"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        if raw is None:
            raw = {}
        return {
            item_type: int(raw.get(item_type, {}).get("count", 0))
            for item_type in _ITEM_TYPES
        }

    async def _load_ledger_from_plan(
        self,
        conn: asyncpg.Connection,
        plan_row: asyncpg.Record,
        base_id: UUID | None,
    ) -> SelectorLedger:
        """Load SelectorLedger from plan row + anchor count query.

        ``total_anchors`` and ``annotated_anchors`` are computed at load
        time (not stored in plan JSONB).
        """

        def parse(val: Any) -> Any:
            if val is None:
                return None
            if isinstance(val, str):
                return json.loads(val)
            return val

        budget_used = parse(plan_row["budget_used"]) or {}
        budget_total = parse(plan_row["budget_total"]) or {}
        published_anchor_counts = parse(
            plan_row["published_anchor_counts_by_type"]
        ) or {}
        published_dedup_keys = parse(
            plan_row["published_dedup_keys_by_type"]
        ) or {}
        published_pattern_keys = parse(
            plan_row["published_pattern_keys_by_type"]
        ) or {}
        density_by_record = parse(plan_row["density_by_record"]) or {}

        # Normalize to SelectorLedger defaults (DB defaults may lack "count")
        for item_type in _ITEM_TYPES:
            if not isinstance(budget_used.get(item_type), dict):
                budget_used[item_type] = {"count": 0}
            elif "count" not in budget_used[item_type]:
                budget_used[item_type]["count"] = 0
            if not isinstance(budget_total.get(item_type), dict):
                budget_total[item_type] = {"count": 0}
            elif "count" not in budget_total[item_type]:
                budget_total[item_type]["count"] = 0
            published_anchor_counts.setdefault(item_type, {})
            published_dedup_keys.setdefault(item_type, [])
            published_pattern_keys.setdefault(item_type, [])
            density_by_record.setdefault(item_type, 0)

        # Query total_anchors
        total_anchors = 0
        if base_id is not None:
            total_anchors = await conn.fetchval(
                "SELECT count(DISTINCT anchor_segment_id) "
                "FROM anchor_segments WHERE base_id = $1",
                base_id,
            ) or 0

        # Compute annotated_anchors from published_anchor_counts_by_type
        # (union of all anchor_segment_ids across item_types)
        annotated: set[str] = set()
        for counts in published_anchor_counts.values():
            if isinstance(counts, dict):
                annotated.update(counts.keys())

        return SelectorLedger(
            budget_used=budget_used,
            budget_total=budget_total,
            published_anchor_counts_by_type=published_anchor_counts,
            published_dedup_keys_by_type=published_dedup_keys,
            published_pattern_keys_by_type=published_pattern_keys,
            density_by_record=density_by_record,
            total_anchors=total_anchors,
            annotated_anchors=annotated,
        )

    def _update_ledger(
        self,
        ledger: SelectorLedger,
        accepted: list[CandidateItem],
    ) -> dict[str, Any]:
        """Compute new ledger JSONB values after accepting candidates."""
        # Deep-copy current ledger values (SelectorLedger is frozen)
        budget_used: dict[str, dict[str, int]] = {
            k: dict(v) for k, v in ledger.budget_used.items()
        }
        published_anchor_counts: dict[str, dict[str, int]] = {
            k: dict(v) for k, v in ledger.published_anchor_counts_by_type.items()
        }
        published_dedup_keys: dict[str, list[str]] = {
            k: list(v) for k, v in ledger.published_dedup_keys_by_type.items()
        }
        published_pattern_keys: dict[str, list[str]] = {
            k: list(v) for k, v in ledger.published_pattern_keys_by_type.items()
        }
        density_by_record: dict[str, int] = dict(ledger.density_by_record)

        for candidate in accepted:
            item_type = candidate.item_type

            # budget_used[item_type].count += 1
            budget_used.setdefault(item_type, {"count": 0})
            budget_used[item_type]["count"] = (
                budget_used[item_type].get("count", 0) + 1
            )

            # published_anchor_counts_by_type[item_type][anchor] += 1
            published_anchor_counts.setdefault(item_type, {})
            anchor_id = candidate.anchor_segment_id
            published_anchor_counts[item_type][anchor_id] = (
                published_anchor_counts[item_type].get(anchor_id, 0) + 1
            )

            # published_dedup_keys_by_type[item_type].append(key)
            published_dedup_keys.setdefault(item_type, [])
            published_dedup_keys[item_type].append(candidate.semantic_dedup_key)

            # published_pattern_keys_by_type[item_type].append(key) if key
            if candidate.pattern_key:
                published_pattern_keys.setdefault(item_type, [])
                published_pattern_keys[item_type].append(candidate.pattern_key)

            # density_by_record[item_type] += 1
            density_by_record[item_type] = (
                density_by_record.get(item_type, 0) + 1
            )

        return {
            "budget_used": budget_used,
            "published_anchor_counts_by_type": published_anchor_counts,
            "published_dedup_keys_by_type": published_dedup_keys,
            "published_pattern_keys_by_type": published_pattern_keys,
            "density_by_record": density_by_record,
        }

    # ------------------------------------------------------------------
    # Layer insert helper
    # ------------------------------------------------------------------

    async def _insert_layer(
        self,
        conn: asyncpg.Connection,
        *,
        layer_type: str,
        layer_fp_prefix: str,
        job_row: asyncpg.Record,
        unit_id: str,
        candidates: tuple[CandidateItem, ...],
        published_at: datetime,
        plan_id: UUID,
        window_id: UUID,
        window_index: int,
    ) -> UUID:
        """INSERT one unit-targeted enhancement layer (status='published').

        Uses a unit-scoped operation fingerprint to satisfy the
        ``uq_enhancement_layers_source_job_fingerprint`` unique constraint when
        publishing multiple unit layers from the same window job.
        """
        layer_id = uuid4()
        output_json: dict[str, Any] = {
            "items": [
                {
                    "anchor_segment_id": c.anchor_segment_id,
                    "spans": c.spans,
                    "semantic_dedup_key": c.semantic_dedup_key,
                    "pattern_key": c.pattern_key,
                    "quality_score": c.quality_score,
                }
                for c in candidates
            ],
        }
        quality_json: dict[str, Any] = {
            "plan_id": str(plan_id),
            "window_id": str(window_id),
            "window_index": window_index,
        }
        # Unit-scoped fingerprint ensures uniqueness across multiple units
        layer_operation_fingerprint = f"{layer_fp_prefix}:{unit_id}"
        generation = int(job_row["expected_generation"])

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                id, reading_record_id, base_id, layer_type, layer_subtype,
                target_scope, target_key, generation, status,
                operation_fingerprint, schema_version, output_json,
                coverage_json, quality_json, source_run_id, source_job_id,
                published_at
            )
            VALUES (
                $1, $2, $3, $4, NULL,
                'unit', $5, $6, 'published',
                $7, $8, $9::jsonb,
                '{}'::jsonb, $10::jsonb, $11, $12,
                $13
            )
            """,
            layer_id,
            job_row["reading_record_id"],
            job_row["base_id"],
            layer_type,
            unit_id,
            generation,
            layer_operation_fingerprint,
            LAYER_SCHEMA_VERSION,
            jsonb_param(output_json),
            jsonb_param(quality_json),
            job_row["run_id"],
            job_row["id"],
            published_at,
        )
        return layer_id
