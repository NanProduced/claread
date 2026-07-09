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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import (
    GrammarNoteItem,
    GrammarNoteLayerOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    SentenceAnalysisLayerOutput,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    IllegalTransitionError,
    ReaderJobRuntime,
    _assert_lease_valid,
)
from app.services.reader_orchestration.window_selector import (
    CandidateItem,
    RejectedCandidate,
    SelectionResult,
    SelectorLedger,
    select_candidates,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_JOB_TYPE,
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    ZPLUS_TARGET_TYPE,
)

# T3.4a: window diagnostics no_op_cause values (success path).
# Failure path uses NO_OP_CAUSE_EXECUTION_FAILED (written by pipeline_runner).
NO_OP_CAUSE_LLM_EMPTY = "llm_empty"
NO_OP_CAUSE_SELECTOR_REJECTED_ALL = "selector_rejected_all"
NO_OP_CAUSE_PUBLISHER_NO_ACCEPTED = "publisher_no_accepted"
NO_OP_CAUSE_EXECUTION_FAILED = "execution_failed"
NO_OP_CAUSE_UNKNOWN = "unknown"

# Truncation limits for diagnostics samples (avoid storing full LLM output).
_DIAGNOSTICS_REASON_MAX_LEN = 240

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


@dataclass(frozen=True, slots=True)
class WindowCandidateContent:
    """Content for layer output (P1-4 fix).

    Carries the actual grammar_note/sentence_analysis content needed to
    build proper GrammarNoteLayerOutput / SentenceAnalysisLayerOutput.
    Matched to CandidateItem by ``semantic_dedup_key``.

    Design source: §8.3 contract — ``output_json`` must contain the layer
    output model (schema_version + items with grammar_point/pattern/note
    for grammar_note, or anchor/label/analysis/chunks for sentence_analysis).
    Provenance (dedup_key/pattern_key/quality_score) goes to ``quality_json``.
    """

    semantic_dedup_key: str
    # grammar_note content
    grammar_point: str = ""
    pattern: str | None = None
    note: str = ""
    spans: list[ReaderTextRangeAnchor] = field(default_factory=list)
    # sentence_analysis content
    label: str = ""
    analysis: str = ""
    chunks: list[SentenceAnalysisChunk] = field(default_factory=list)
    anchor: ReaderTextRangeAnchor | None = None


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
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime

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
        candidate_contents: list[WindowCandidateContent] | None = None,
    ) -> PublishedWindowResult:
        """§8.4 publish transaction wrapped in a ``publish_fence`` span.

        Requirement 7: mirrors the legacy
        ``GrammarBundleLayerPublisher.publish_unit_grammar_bundle`` pattern —
        starts a ``publish_fence`` span, delegates to
        ``_publish_window_grammar_bundle_inner`` for the actual transaction,
        and ends the span with success / failure metadata.

        - Success: ``status='succeeded'`` + extra_metadata with
          ``layer_type`` / ``plan_id`` / ``window_id`` / ``accepted_count``
          / ``no_op`` / ``layer_ids``.
        - ``FenceViolationError``: ``status='failed'`` +
          ``failure_class='fence_violation'``.
        - Other ``Exception``: ``status='failed'`` +
          ``failure_class='publish_exception'`` + ``failure_code=type(exc).__name__``.
        """
        from app.services.reader_orchestration.span_recorder import (
            SPAN_KIND_PUBLISH_FENCE,
            STATUS_FAILED,
            STATUS_SUCCEEDED,
            current_span,
            get_default_recorder,
        )

        parent = current_span()
        recorder = get_default_recorder()
        publish_span = await recorder.start_span(
            trace_id=parent.trace_id if parent is not None else uuid4(),
            span_kind=SPAN_KIND_PUBLISH_FENCE,
            parent_span_id=parent.span_id if parent is not None else None,
            reader_job_id=job_id,
            metadata={
                "layer_type": "grammar_bundle_window",
                "plan_id": str(plan_id),
                "window_id": str(window_id),
            },
        )
        try:
            result = await self._publish_window_grammar_bundle_inner(
                job_id=job_id,
                lease_token=lease_token,
                plan_id=plan_id,
                window_id=window_id,
                candidates=candidates,
                candidate_contents=candidate_contents,
            )
            await recorder.end_span(
                publish_span,
                status=STATUS_SUCCEEDED,
                extra_metadata={
                    "layer_type": "grammar_bundle_window",
                    "plan_id": str(plan_id),
                    "window_id": str(window_id),
                    "accepted_count": result.accepted_count,
                    "no_op": result.skipped or result.accepted_count == 0,
                    "grammar_note_layer_ids": [
                        str(lid) for lid in result.grammar_note_layer_ids
                    ],
                    "sentence_analysis_layer_ids": [
                        str(lid) for lid in result.sentence_analysis_layer_ids
                    ],
                },
            )
            return result
        except FenceViolationError:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="fence_violation",
                failure_code="fence_failed",
            )
            raise
        except Exception as exc:
            await recorder.end_span(
                publish_span,
                status=STATUS_FAILED,
                failure_class="publish_exception",
                failure_code=type(exc).__name__,
            )
            raise

    async def _publish_window_grammar_bundle_inner(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        plan_id: UUID,
        window_id: UUID,
        candidates: list[CandidateItem],
        candidate_contents: list[WindowCandidateContent] | None = None,
    ) -> PublishedWindowResult:
        """§8.4 publish transaction (inner — no span wrapping).

        Manually replicates ``transition()`` validation (status / job_type /
        target_type / fingerprint / lease / fence) and then calls
        ``_apply_transition`` + ``_insert_job_event`` within the same
        transaction as the ledger + layers writes.

        When ``candidate_contents`` is provided, ``output_json`` is built as a
        proper ``GrammarNoteLayerOutput`` / ``SentenceAnalysisLayerOutput``
        (§8.3 contract) and provenance (dedup_key/pattern_key/quality_score)
        is stored in ``quality_json``. When ``candidate_contents`` is ``None``,
        falls back to the legacy selector-sidecar ``output_json`` shape so
        existing callers (e.g. pipeline runner) remain backward compatible.
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
                # P1-3: pass target_anchor_ids from window_row so selector
                # can reject candidates whose anchor_segment_id is outside
                # the window's target anchor set (§7.2 step 2 pre-filter).
                target_anchor_ids = self._parse_target_anchor_ids(window_row)
                selection = select_candidates(
                    candidates,
                    ledger=ledger,
                    window_budget=window_budget,
                    target_anchor_ids=target_anchor_ids,
                )

                # 6. Insert accepted layers (per-unit, target_scope='unit')
                grammar_layer_ids: list[UUID] = []
                sentence_layer_ids: list[UUID] = []
                accepted_by_unit: dict[str, dict[str, list[CandidateItem]]] = {}

                # P1-4: build contents lookup by semantic_dedup_key so _insert_layer
                # can produce proper GrammarNoteLayerOutput / SentenceAnalysisLayerOutput.
                contents_by_dedup: dict[str, WindowCandidateContent] | None = (
                    {c.semantic_dedup_key: c for c in candidate_contents}
                    if candidate_contents is not None
                    else None
                )

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
                            contents_by_dedup=contents_by_dedup,
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
                            contents_by_dedup=contents_by_dedup,
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
                # T3.4a: build window diagnostics for observability. Persisted
                # to BOTH reader_jobs.output_ref_json (full diagnostics, primary)
                # and analysis_windows.coverage.diagnostics (subset, queryable
                # without joining reader_jobs). This makes no-op windows
                # diagnosable even when they have no published layer.
                diagnostics = self._build_window_diagnostics(
                    candidates=candidates,
                    selection=selection,
                    accepted_by_unit=accepted_by_unit,
                    window_row=window_row,
                    plan_row=plan_row,
                    job_row=job_row,
                )
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
                        {
                            "covered_unit_ids": list(accepted_by_unit.keys()),
                            "diagnostics": diagnostics,
                        }
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
                    # T3.4a: full diagnostics (window_meta / strategy / budgets /
                    # raw_candidate_count_by_type / accepted_count_by_type /
                    # rejected_count_by_type / rejected_breakdown / no_op_cause).
                    "diagnostics": diagnostics,
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

    @staticmethod
    def _parse_target_anchor_ids(
        window_row: asyncpg.Record,
    ) -> set[str] | None:
        """Parse ``target_anchor_ids`` JSONB from window row (P1-3).

        Returns ``set[str]`` of valid anchor_segment_ids for the window,
        or ``None`` if the column is missing/empty (defensive: allows
        callers that don't set target_anchor_ids to skip the pre-filter).
        """
        raw = window_row["target_anchor_ids"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        ids = {str(a) for a in raw}
        return ids if ids else None

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

        # Query total_anchors + base_text_length_utf16 in one round-trip.
        # base_text_length_utf16 feeds the RECORD_DENSITY gate (§7.3 P2-6):
        #   density = total_published_count / max(base_text_length_utf16 / 1000, 1.0)
        # Bug fix: previously this was never set, so it defaulted to 0 and
        # the density denominator collapsed to 1.0 — turning the per-1000-chars
        # ratio cap into a raw absolute cap (grammar_note <= 3, sentence_analysis
        # <= 1 across the whole record).
        total_anchors = 0
        base_text_length_utf16 = 0
        if base_id is not None:
            row = await conn.fetchrow(
                """
                SELECT
                    rb.content_utf16_length,
                    char_length(rb.text) AS text_char_length
                FROM reading_bases rb
                WHERE rb.id = $1
                """,
                base_id,
            )
            if row is not None:
                # content_utf16_length has CHECK (>= 1), so it should always
                # be positive. Use NULLIF(..., 0) + COALESCE as a defensive
                # fallback so density never collapses to raw count even if a
                # legacy row violated the constraint.
                content_len = row["content_utf16_length"]
                if content_len and content_len > 0:
                    base_text_length_utf16 = int(content_len)
                else:
                    # char_length returns character count (≈ UTF-16 code
                    # units for BMP-only text). Sufficient as a density
                    # denominator; supplementary-plane chars are rare in
                    # reading bases.
                    base_text_length_utf16 = int(row["text_char_length"] or 0)
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
            base_text_length_utf16=base_text_length_utf16,
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
    # T3.4a: window diagnostics builder
    # ------------------------------------------------------------------

    def _build_window_diagnostics(
        self,
        *,
        candidates: list[CandidateItem],
        selection: SelectionResult,
        accepted_by_unit: dict[str, dict[str, list[CandidateItem]]],
        window_row: asyncpg.Record,
        plan_row: asyncpg.Record,
        job_row: asyncpg.Record,
    ) -> dict[str, Any]:
        """Build window diagnostics summary for observability (T3.4a).

        Persisted to ``reader_jobs.output_ref_json.diagnostics`` (primary)
        and ``analysis_windows.coverage.diagnostics`` (subset, queryable
        without joining reader_jobs). Makes no-op windows diagnosable:
        records whether no-op is due to LLM empty output, selector rejecting
        all candidates, or publisher having no acceptable unit-targeted item.

        Does NOT store full LLM raw output — only counts, gates, reasons,
        and small metadata (window_id / window_index / strategy hash /
        budget snapshot). The selector already returns structured
        ``RejectedCandidate(candidate, gate, reason)`` so no selector
        accept/reject semantics are changed.

        Schema:
          - ``window_meta``: window_id / window_index / plan_id /
            target_unit_ids / target_anchor_count
          - ``strategy``: reading_goal / reading_variant / strategy_hash /
            layer_policy_hash (read from job input_json; the worker already
            cross-validated them against the live resolver)
          - ``budgets``: window_budget + record budget used/total snapshot
          - ``raw_candidate_count_by_type``: grammar_note / sentence_analysis
          - ``accepted_count_by_type``: same shape
          - ``rejected_count_by_type``: same shape
          - ``rejected_breakdown``: list of {item_type, gate, reason, count}
            aggregated by (item_type, gate, reason); reason truncated
          - ``no_op_cause``: llm_empty / selector_rejected_all /
            publisher_no_accepted / unknown (failure path is set by
            pipeline_runner as execution_failed)
        """
        # --- window_meta ---
        window_id_value = (
            str(window_row["id"]) if window_row.get("id") is not None else None
        )
        window_index = int(window_row["window_index"])
        target_unit_ids = self._parse_jsonb_list(window_row, "target_unit_ids")
        target_anchor_ids = self._parse_target_anchor_ids(window_row) or []
        target_anchor_count = len(target_anchor_ids)

        # --- strategy (read from job input_json; worker validated) ---
        strategy_meta = self._read_strategy_metadata_from_input(job_row)

        # --- budgets ---
        window_budget = self._parse_window_budget(window_row)
        budget_used = self._parse_jsonb(plan_row["budget_used"]) or {}
        budget_total = self._parse_jsonb(plan_row["budget_total"]) or {}
        budgets_snapshot = {
            "window_budget": {
                item_type: window_budget.get(item_type, 0)
                for item_type in _ITEM_TYPES
            },
            "record_budget_used": {
                item_type: {
                    "count": int(
                        (budget_used.get(item_type, {}) or {}).get("count", 0)
                    )
                }
                for item_type in _ITEM_TYPES
            },
            "record_budget_total": {
                item_type: {
                    "count": int(
                        (budget_total.get(item_type, {}) or {}).get("count", 0)
                    )
                }
                for item_type in _ITEM_TYPES
            },
        }

        # --- counts by type ---
        raw_count_by_type: dict[str, int] = {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
        for c in candidates:
            if c.item_type in raw_count_by_type:
                raw_count_by_type[c.item_type] += 1

        # Use selection.accepted as the authoritative accepted count. This
        # is what the selector returned post-gates; ``accepted_by_unit`` may
        # drop items with no unit_id in spans (publisher-side drop, tracked
        # separately by no_op_cause=publisher_no_accepted when applicable).
        accepted_count_by_type: dict[str, int] = {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
        for c in selection.accepted:
            if c.item_type in accepted_count_by_type:
                accepted_count_by_type[c.item_type] += 1

        rejected_count_by_type: dict[str, int] = {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
        for r in selection.rejected:
            if r.candidate.item_type in rejected_count_by_type:
                rejected_count_by_type[r.candidate.item_type] += 1

        # --- rejected_breakdown aggregated by (item_type, gate, reason) ---
        rejected_breakdown = self._aggregate_rejected(selection.rejected)

        # --- no_op_cause ---
        # Aligns with existing window status logic:
        #   selection.accepted empty → window status='no_op'
        #   selection.accepted non-empty → window status='completed'
        #   but if accepted_by_unit is empty (all accepted dropped at publish
        #   due to no unit_id in spans), no layers are actually published —
        #   this is the publisher_no_accepted edge case worth surfacing.
        raw_total = sum(raw_count_by_type.values())
        if not selection.accepted:
            # window marked no_op by existing publish logic
            if raw_total == 0:
                no_op_cause: str | None = NO_OP_CAUSE_LLM_EMPTY
            else:
                no_op_cause = NO_OP_CAUSE_SELECTOR_REJECTED_ALL
        elif not accepted_by_unit:
            # selection.accepted non-empty but all dropped at publish
            # (no unit_id in spans). Window is marked completed but no
            # layers published — surface as publisher_no_accepted so the
            # cause is diagnosable instead of silent.
            no_op_cause = NO_OP_CAUSE_PUBLISHER_NO_ACCEPTED
        else:
            no_op_cause = None  # successful publish, not a no-op window

        return {
            "window_meta": {
                "window_id": window_id_value,
                "window_index": window_index,
                "plan_id": str(plan_row["id"]),
                "target_unit_ids": target_unit_ids,
                "target_anchor_count": target_anchor_count,
            },
            "strategy": strategy_meta,
            "budgets": budgets_snapshot,
            "raw_candidate_count_by_type": raw_count_by_type,
            "accepted_count_by_type": accepted_count_by_type,
            "rejected_count_by_type": rejected_count_by_type,
            "rejected_breakdown": rejected_breakdown,
            "no_op_cause": no_op_cause,
        }

    @staticmethod
    def _aggregate_rejected(
        rejected: list[RejectedCandidate],
    ) -> list[dict[str, Any]]:
        """Aggregate rejected candidates by (item_type, gate, reason).

        Returns a sorted list of dicts with ``item_type`` / ``gate`` /
        ``reason`` (truncated) / ``count``. Sorted by count desc then
        item_type / gate for stable output.
        """
        buckets: dict[tuple[str, str, str], int] = {}
        for r in rejected:
            reason = (r.reason or "")[:_DIAGNOSTICS_REASON_MAX_LEN]
            key = (r.candidate.item_type, r.gate.value, reason)
            buckets[key] = buckets.get(key, 0) + 1
        result = [
            {
                "item_type": key[0],
                "gate": key[1],
                "reason": key[2],
                "count": count,
            }
            for key, count in buckets.items()
        ]
        result.sort(key=lambda d: (-d["count"], d["item_type"], d["gate"]))
        return result

    @staticmethod
    def _parse_jsonb(value: Any) -> Any:
        """Parse a JSONB column value (str → json.loads, else pass-through)."""
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value

    @staticmethod
    def _parse_jsonb_list(
        record: asyncpg.Record, column: str
    ) -> list[str]:
        """Parse a JSONB list column from a record (defensive)."""
        raw = record.get(column) if hasattr(record, "get") else None
        if isinstance(raw, str):
            raw = json.loads(raw)
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(v) for v in raw]
        return []

    @staticmethod
    def _read_strategy_metadata_from_input(
        job_row: asyncpg.Record,
    ) -> dict[str, Any]:
        """Read strategy metadata from ``reader_jobs.input_json``.

        The worker (``_resolve_window_strategy``) already cross-validated
        these fields against the live resolver, so the publisher can trust
        them and avoid re-running the resolver. Missing fields fall back
        to ``None`` so diagnostics remain queryable for legacy / malformed
        jobs.
        """
        input_data: Any = job_row["input_json"] if "input_json" in job_row.keys() else None
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        if not isinstance(input_data, dict):
            return {
                "reading_goal": None,
                "reading_variant": None,
                "strategy_hash": None,
                "layer_policy_hash": None,
            }
        return {
            "reading_goal": input_data.get("reading_goal"),
            "reading_variant": input_data.get("reading_variant"),
            "strategy_hash": input_data.get("strategy_hash"),
            "layer_policy_hash": input_data.get("layer_policy_hash"),
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
        contents_by_dedup: dict[str, WindowCandidateContent] | None = None,
    ) -> UUID:
        """INSERT one unit-targeted enhancement layer (status='published').

        Uses a unit-scoped operation fingerprint to satisfy the
        ``uq_enhancement_layers_source_job_fingerprint`` unique constraint when
        publishing multiple unit layers from the same window job.

        P1-4: When ``contents_by_dedup`` is provided, ``output_json`` is built
        as a proper ``GrammarNoteLayerOutput`` / ``SentenceAnalysisLayerOutput``
        (§8.3 contract) and provenance goes to ``quality_json``. When
        ``contents_by_dedup`` is ``None``, falls back to the legacy
        selector-sidecar ``output_json`` shape for backward compatibility.

        P2-7: When ``self._event_runtime`` is not None, emits a
        ``layer_published`` reader_event so frontend polling can detect the
        new layer without snapshot reload.
        """
        layer_id = uuid4()
        generation = int(job_row["expected_generation"])
        # Unit-scoped fingerprint ensures uniqueness across multiple units
        layer_operation_fingerprint = f"{layer_fp_prefix}:{unit_id}"

        output_json, quality_json = self._build_layer_payload(
            layer_type=layer_type,
            candidates=candidates,
            plan_id=plan_id,
            window_id=window_id,
            window_index=window_index,
            contents_by_dedup=contents_by_dedup,
        )

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

        # P2-7: emit layer_published reader_event for progressive publish
        if self._event_runtime is not None:
            await self._event_runtime.publish_event_in_transaction(
                conn,
                record_id=UUID(str(job_row["reading_record_id"])),
                event_type="layer_published",
                payload_json={
                    "record_id": str(job_row["reading_record_id"]),
                    "base_id": str(job_row["base_id"]),
                    "layer_id": str(layer_id),
                    "layer_type": layer_type,
                    "target_scope": "unit",
                    "target_key": unit_id,
                    "generation": generation,
                    "source": "grammar_bundle_window",
                    "plan_id": str(plan_id),
                    "window_id": str(window_id),
                },
                source_run_id=UUID(str(job_row["run_id"])),
                source_job_id=UUID(str(job_row["id"])),
                source_layer_id=layer_id,
                created_at=published_at,
            )
        return layer_id

    def _build_layer_payload(
        self,
        *,
        layer_type: str,
        candidates: tuple[CandidateItem, ...],
        plan_id: UUID,
        window_id: UUID,
        window_index: int,
        contents_by_dedup: dict[str, WindowCandidateContent] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build ``output_json`` + ``quality_json`` for the layer INSERT.

        P1-4 + P2-1: always produces a proper ``GrammarNoteLayerOutput`` /
        ``SentenceAnalysisLayerOutput`` for ``output_json`` and stores
        provenance (dedup_key/pattern_key/quality_score) in ``quality_json``.

        P2-1 (fail closed): when ``contents_by_dedup`` is None but candidates
        exist, raises ValueError instead of falling back to sidecar shape.
        Production path must always produce contract-compliant output_json.
        """
        if contents_by_dedup is None:
            if candidates:
                raise ValueError(
                    "candidate_contents is required when candidates exist "
                    "(P2-1 fail closed: sidecar fallback removed)"
                )
            # No candidates → empty output (no-op window)
            return {"schema_version": 1, "items": []}, {
                "plan_id": str(plan_id),
                "window_id": str(window_id),
                "window_index": window_index,
            }
        return self._build_layer_payload_contract(
            layer_type=layer_type,
            candidates=candidates,
            plan_id=plan_id,
            window_id=window_id,
            window_index=window_index,
            contents_by_dedup=contents_by_dedup,
        )

    def _build_layer_payload_contract(
        self,
        *,
        layer_type: str,
        candidates: tuple[CandidateItem, ...],
        plan_id: UUID,
        window_id: UUID,
        window_index: int,
        contents_by_dedup: dict[str, WindowCandidateContent],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """§8.3 contract: proper layer output model + provenance in quality_json."""
        if layer_type == GRAMMAR_NOTE_LAYER_TYPE:
            items: list[GrammarNoteItem] = []
            for c in candidates:
                content = contents_by_dedup.get(c.semantic_dedup_key)
                if content is None:
                    continue
                items.append(
                    GrammarNoteItem(
                        spans=content.spans,
                        grammar_point=content.grammar_point,
                        pattern=content.pattern,
                        note=content.note,
                    )
                )
            output_model = GrammarNoteLayerOutput(items=items)
        elif layer_type == SENTENCE_ANALYSIS_LAYER_TYPE:
            sentence_items: list[SentenceAnalysisItem] = []
            for c in candidates:
                content = contents_by_dedup.get(c.semantic_dedup_key)
                if content is None or content.anchor is None:
                    continue
                sentence_items.append(
                    SentenceAnalysisItem(
                        anchor=content.anchor,
                        label=content.label,
                        analysis=content.analysis,
                        chunks=content.chunks,
                    )
                )
            output_model = SentenceAnalysisLayerOutput(items=sentence_items)
        else:  # pragma: no cover - defensive fallback
            raise ValueError(f"unsupported layer_type: {layer_type}")

        output_json = output_model.model_dump(mode="json")
        # quality_json stores provenance (§8.3): plan_id / window_id /
        # window_index / dedup_key / pattern_key / quality_score. These fields
        # MUST NOT appear in output_json.
        quality_json: dict[str, Any] = {
            "plan_id": str(plan_id),
            "window_id": str(window_id),
            "window_index": window_index,
            "semantic_dedup_key": candidates[0].semantic_dedup_key,
            "pattern_key": candidates[0].pattern_key,
            "quality_score": candidates[0].quality_score,
            "items": [
                {
                    "semantic_dedup_key": c.semantic_dedup_key,
                    "pattern_key": c.pattern_key,
                    "quality_score": c.quality_score,
                }
                for c in candidates
            ],
        }
        return output_json, quality_json
