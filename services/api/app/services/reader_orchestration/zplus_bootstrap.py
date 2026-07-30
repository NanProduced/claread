"""ZPlusBootstrapService: bootstrap layer_analysis_plans + analysis_windows + reader_jobs.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  - §3.2 Window Job contract (job_type / target_type / target_key / input_json)
  - §4.1 layer_analysis_plans table (status='active' at creation, no planning phase)
  - §7.3 budget caps (grammar_note / sentence_analysis formulas)

Idempotency: same record/base/layer with existing active plan is reused, including
its windows and reader_jobs. The partial unique index
``uq_layer_analysis_plans_active`` fences concurrent active plans.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

from .analysis_anchor_view import AnalysisAnchorView, load_analysis_anchor_views
from .automatic_layer_policy import (
    AutomaticLayerTargetUnit,
    build_semantic_fence_input_fields,
    compose_semantic_fingerprint_token,
    filter_units_for_any_grammar,
    generation_semantic_fence_from_targets,
    get_automatic_layer_policy_mode,
    policy_from_unit_metadata,
)
from .job_bootstrap import (
    _build_strategy_metadata,
    _compose_operation_fingerprint,
    _load_locked_active_base_state,
    _LockedActiveBaseState,
)
from .window_planner import PlannedWindow, WindowFormationConfig, plan_windows

ZPLUS_GRAMMAR_JOB_TYPE = "build_grammar_bundle_window"
ZPLUS_GRAMMAR_OPERATION_FINGERPRINT = "grammar_bundle_window_v1"
ZPLUS_TARGET_TYPE = "unit_range"
ZPLUS_POLICY_VERSION = "zplus_grammar_bundle_v1"
ZPLUS_LAYER_TYPE = "grammar_bundle"
ZPLUS_GRAMMAR_RUN_TYPE = "grammar_bundle_window"
ZPLUS_TRIGGER_KIND = "system"
ZPLUS_DEFAULT_MAX_ATTEMPTS = 3
ZPLUS_STRATEGY_LAYER_NAME = "grammar_bundle"

# §7.3 per-record budget caps
_GRAMMAR_NOTE_BUDGET_CAP = 18
_SENTENCE_ANALYSIS_BUDGET_CAP = 5
# §7.3 per-window caps
_WINDOW_GRAMMAR_NOTE_COUNT = 2
_WINDOW_SENTENCE_ANALYSIS_COUNT = 1


@dataclass(frozen=True, slots=True)
class ZPlusBootstrapResult:
    plan_id: UUID
    windows: tuple[PlannedWindow, ...]
    job_ids: tuple[UUID, ...]


def _compute_budget_total(
    anchor_views: tuple[AnalysisAnchorView, ...],
) -> dict[str, dict[str, int]]:
    """§7.3 per-record budget caps.

    - grammar_note = min(ceil(content_chars / 1000) * 2, 18)
    - sentence_analysis = min(max(round(content_chars / 2000), 1), 5)

    ``content_chars`` is the sum of ``anchor_char_count`` across all anchors,
    which represents the actual content being analyzed (not the unit length).
    """
    content_chars = sum(a.anchor_char_count for a in anchor_views)
    grammar_note_count = min(math.ceil(content_chars / 1000) * 2, _GRAMMAR_NOTE_BUDGET_CAP)
    sentence_analysis_count = min(
        max(round(content_chars / 2000), 1), _SENTENCE_ANALYSIS_BUDGET_CAP
    )
    return {
        "grammar_note": {"count": grammar_note_count},
        "sentence_analysis": {"count": sentence_analysis_count},
    }


def _compute_window_budget() -> dict[str, dict[str, int]]:
    """§7.3 per-window caps: grammar_note=2, sentence_analysis=1."""
    return {
        "grammar_note": {"count": _WINDOW_GRAMMAR_NOTE_COUNT},
        "sentence_analysis": {"count": _WINDOW_SENTENCE_ANALYSIS_COUNT},
    }


class ZPlusBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_grammar_window_plan(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        trace_id: UUID | None = None,
    ) -> ZPlusBootstrapResult:
        """Create plan + windows + reader_jobs (idempotent).

        If an active plan already exists for the same record/base/layer, the
        existing plan, windows, and reader_jobs are returned without
        re-creating anything.

        ``trace_id`` is the shared observability trace root for the record.
        When provided, it is written into every window ``reader_runs.envelope_json``
        so downstream workers can propagate it into ``reader_runtime_spans``
        (requirement 5). When ``None``, a fresh UUID is generated so the
        window runs always carry a trace_id even when the caller did not
        supply one (e.g. direct ZPlusBootstrapService callers).
        """
        pool = self.get_pool()

        # 1. Load anchor views outside the transaction (read-only).
        # ``load_analysis_anchor_views`` acquires its own connection internally,
        # so it cannot share the transaction connection.
        anchor_views = await load_analysis_anchor_views(pool, base_id=base_id)

        # Mode-aware automatic grammar policy (same seam as compact/grouped).
        # off/shadow keep all units; enforce drops grammar-disallowed units.
        unit_ids = {v.unit_id for v in anchor_views}
        if unit_ids:
            async with pool.acquire() as meta_conn:
                meta_rows = await meta_conn.fetch(
                    """
                    SELECT unit_id, order_index, metadata_json
                    FROM reading_units
                    WHERE base_id = $1
                      AND unit_id = ANY($2::text[])
                    ORDER BY order_index ASC
                    """,
                    base_id,
                    list(unit_ids),
                )
            unit_maps: list[dict[str, object]] = []
            for row in meta_rows:
                meta = row["metadata_json"]
                if hasattr(meta, "keys"):
                    meta = dict(meta)
                elif not isinstance(meta, dict):
                    meta = {}
                unit_maps.append(
                    {
                        "unit_id": str(row["unit_id"]),
                        "order_index": int(row["order_index"] or 0),
                        "metadata_json": meta,
                    }
                )
            kept = filter_units_for_any_grammar(
                unit_maps,
                mode=get_automatic_layer_policy_mode(),
                record_id=str(record_id),
            )
            allowed_unit_ids = {str(u["unit_id"]) for u in kept}
            anchor_views = tuple(
                v for v in anchor_views if v.unit_id in allowed_unit_ids
            )

        # 2. Plan windows (pure algorithm, no IO).
        config = WindowFormationConfig()
        windows = plan_windows(anchor_views, config=config)

        # 3. Compute per-record budget_total (§7.3).
        budget_total = _compute_budget_total(anchor_views)

        # 4. Open a transaction for all writes.
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Resolve user_id for the record (required by
                # ``_load_locked_active_base_state``).
                user_id = await conn.fetchval(
                    """
                    SELECT user_id FROM reading_records
                    WHERE id = $1 AND deleted_at IS NULL
                    """,
                    record_id,
                )
                if user_id is None:
                    raise LookupError(f"reading record {record_id} not found")

                # Load + lock the active base state (reuses the existing
                # helper so product_state / lifecycle / generation checks
                # are consistent with the rest of the bootstrap family).
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                # Guard: the caller-supplied base_id must match the active base.
                if state.base_id != base_id:
                    raise ValueError(
                        f"base_id {base_id} does not match active base "
                        f"{state.base_id}"
                    )

                # 5. Idempotency: reuse existing active plan if present.
                existing_plan_id = await conn.fetchval(
                    """
                    SELECT id FROM layer_analysis_plans
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND layer_type = $3
                      AND status IN ('planning', 'active')
                    """,
                    record_id,
                    base_id,
                    ZPLUS_LAYER_TYPE,
                )
                if existing_plan_id is not None:
                    return await self._load_existing_plan(conn, existing_plan_id)

                # 6. INSERT plan (status='active' — Z+ skips planning phase).
                plan_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO layer_analysis_plans (
                        id, reading_record_id, base_id, layer_type,
                        policy_version, generation, budget_total, status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'active')
                    """,
                    plan_id,
                    record_id,
                    base_id,
                    ZPLUS_LAYER_TYPE,
                    ZPLUS_POLICY_VERSION,
                    state.expected_generation,
                    jsonb_param(budget_total),
                )

                # 7. INSERT windows + reader_runs + reader_jobs.
                # Resolve trace_id: caller-supplied (shared with display/
                # translation/vocab runs) or a fresh UUID so window runs
                # always carry a trace_id for span propagation.
                effective_trace_id = trace_id if trace_id is not None else uuid4()

                job_ids: list[UUID] = []
                for window in windows:
                    window_id = uuid4()
                    job_id = await self._create_window_reader_job(
                        conn,
                        state=state,
                        plan_id=plan_id,
                        window_id=window_id,
                        window=window,
                        trace_id=effective_trace_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO analysis_windows (
                            id, plan_id, window_index,
                            target_anchor_ids,
                            context_anchor_prev, context_anchor_next,
                            target_unit_ids, target_block_ids,
                            char_count, anchor_count,
                            window_budget, status, job_id
                        )
                        VALUES (
                            $1, $2, $3,
                            $4::jsonb,
                            $5::jsonb, $6::jsonb,
                            $7::jsonb, $8::jsonb,
                            $9, $10,
                            $11::jsonb, 'pending', $12
                        )
                        """,
                        window_id,
                        plan_id,
                        window.window_index,
                        jsonb_param(window.target_anchor_ids),
                        jsonb_param(
                            [a.anchor_segment_id for a in window.context_anchor_prev]
                        ),
                        jsonb_param(
                            [a.anchor_segment_id for a in window.context_anchor_next]
                        ),
                        jsonb_param(window.target_unit_ids),
                        jsonb_param(window.target_block_ids),
                        window.char_count,
                        window.anchor_count,
                        jsonb_param(_compute_window_budget()),
                        job_id,
                    )
                    job_ids.append(job_id)

                return ZPlusBootstrapResult(
                    plan_id=plan_id,
                    windows=tuple(windows),
                    job_ids=tuple(job_ids),
                )

    async def _create_window_reader_job(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        plan_id: UUID,
        window_id: UUID,
        window: PlannedWindow,
        trace_id: UUID,
    ) -> UUID:
        """Create one reader_runs row + one reader_jobs row for a window job.

        Follows the per-unit-run pattern from ``_insert_unit_job``: each window
        gets its own reader_runs row so it can be tracked independently.

        ``trace_id`` is written into ``reader_runs.envelope_json`` so the
        pipeline runner's worker_tick span can propagate the same trace root
        shared by display / translation / vocabulary runs (requirement 5).
        """
        strategy_metadata = _build_strategy_metadata(
            state.strategy, ZPLUS_STRATEGY_LAYER_NAME
        )
        window_budget = _compute_window_budget()

        # Semantic fence from target unit metadata (recorded versions).
        unit_meta_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, metadata_json
            FROM reading_units
            WHERE base_id = $1
              AND unit_id = ANY($2::text[])
            ORDER BY order_index ASC
            """,
            state.base_id,
            list(window.target_unit_ids),
        )
        typed_targets: list[AutomaticLayerTargetUnit] = []
        for ur in unit_meta_rows:
            meta = ur["metadata_json"]
            if hasattr(meta, "keys"):
                meta = dict(meta)
            elif not isinstance(meta, dict):
                meta = {}
            resolved = policy_from_unit_metadata(meta)
            typed_targets.append(
                AutomaticLayerTargetUnit(
                    unit_id=str(ur["unit_id"]),
                    order_index=int(ur["order_index"] or 0),
                    metadata_json=meta,
                    contract_version=resolved.contract_version,
                    resolver_version=(
                        "legacy_open" if resolved.is_legacy else resolved.resolver_version
                    ),
                    content_role=resolved.content_role,
                    policy=resolved.policy,
                )
            )
        semantic_fence = generation_semantic_fence_from_targets(typed_targets)
        frozen_mode = get_automatic_layer_policy_mode()
        semantic_token = compose_semantic_fingerprint_token(
            semantic_fence, mode=frozen_mode
        )
        operation_fingerprint = _compose_operation_fingerprint(
            ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
            state.strategy,
            semantic_token=semantic_token,
        )
        fence_fields = build_semantic_fence_input_fields(
            semantic_fence, layer="grammar_note", mode=frozen_mode
        )

        # 1. reader_runs row (per-window-run).
        run_row = await conn.fetchrow(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'queued', $4, $5::jsonb, $6, $7)
            RETURNING id
            """,
            state.record_id,
            state.user_id,
            ZPLUS_GRAMMAR_RUN_TYPE,
            state.expected_generation,
            jsonb_param(
                {
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": ZPLUS_TARGET_TYPE,
                    "window_id": str(window_id),
                    "plan_id": str(plan_id),
                    "layer_types": ["grammar_note", "sentence_analysis"],
                    "strategy": strategy_metadata,
                    "trace_id": str(trace_id),
                    **fence_fields,
                }
            ),
            ZPLUS_POLICY_VERSION,
            ZPLUS_TRIGGER_KIND,
        )
        if run_row is None:
            raise RuntimeError("reader_runs insert did not return a row")
        run_id: UUID = run_row["id"]

        # 2. reader_jobs row (window-scoped).
        input_json: dict[str, Any] = {
            **strategy_metadata,
            "plan_id": str(plan_id),
            "window_id": str(window_id),
            "window_index": window.window_index,
            "target_unit_ids": window.target_unit_ids,
            "target_anchor_ids": window.target_anchor_ids,
            "context_anchor_prev": [
                a.anchor_segment_id for a in window.context_anchor_prev
            ],
            "context_anchor_next": [
                a.anchor_segment_id for a in window.context_anchor_next
            ],
            "window_budget": window_budget,
            "record_id": str(state.record_id),
            "base_id": str(state.base_id),
            "expected_generation": state.expected_generation,
            **fence_fields,
        }

        input_signature = (
            f"{state.base_id}:{state.record_id}:{state.expected_generation}:"
            f"{operation_fingerprint}:{window_id}:"
            f"{state.strategy.strategy_hash}:{semantic_token}"
        )
        input_hash = hashlib.sha256(input_signature.encode("utf-8")).hexdigest()

        job_row = await conn.fetchrow(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key,
                status, priority, expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                'queued', 0, $8, $9,
                $10, $11, $12::jsonb, $13
            )
            RETURNING id
            """,
            state.record_id,
            state.base_id,
            run_id,
            state.user_id,
            ZPLUS_GRAMMAR_JOB_TYPE,
            ZPLUS_TARGET_TYPE,
            str(window_id),
            state.expected_generation,
            operation_fingerprint,
            f"{operation_fingerprint}:{window_id}",
            input_hash,
            jsonb_param(input_json),
            ZPLUS_DEFAULT_MAX_ATTEMPTS,
        )
        if job_row is None:
            raise RuntimeError("reader_jobs insert did not return a row")
        job_id: UUID = job_row["id"]
        return job_id

    async def _load_existing_plan(
        self,
        conn: asyncpg.Connection,
        plan_id: UUID,
    ) -> ZPlusBootstrapResult:
        """Load existing plan + windows + job_ids (idempotent path)."""
        window_rows = await conn.fetch(
            """
            SELECT window_index, target_anchor_ids, target_unit_ids,
                   target_block_ids, char_count, anchor_count, job_id
            FROM analysis_windows
            WHERE plan_id = $1
            ORDER BY window_index
            """,
            plan_id,
        )
        job_ids: list[UUID] = []
        windows: list[PlannedWindow] = []
        for row in window_rows:
            if row["job_id"] is not None:
                job_ids.append(row["job_id"])
            windows.append(
                PlannedWindow(
                    window_index=row["window_index"],
                    target_anchor_ids=list(row["target_anchor_ids"]),
                    target_unit_ids=list(row["target_unit_ids"]),
                    target_block_ids=list(row["target_block_ids"]),
                    char_count=row["char_count"],
                    anchor_count=row["anchor_count"],
                )
            )
        return ZPlusBootstrapResult(
            plan_id=plan_id,
            windows=tuple(windows),
            job_ids=tuple(job_ids),
        )
