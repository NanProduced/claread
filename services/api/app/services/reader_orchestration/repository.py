from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.contracts.anchor_validation import (
    AnchorSegmentRange,
    AnchorValidationError,
    validate_text_anchor_against_unit,
)
from app.contracts.annotation import (
    TEXT_RANGE_OFFSET_UNIT,
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.database.json_compat import ensure_json_array, ensure_json_object, jsonb_param
from app.schemas.reader_orchestration import (
    DEFAULT_READER_ORCHESTRATION_READING_GOAL,
    DEFAULT_READER_ORCHESTRATION_READING_VARIANT,
    ReaderEnhancementProgress,
    ReaderEnhancementProgressLayer,
    ReaderSnapshotAskSupplement,
    ReaderSnapshotLayer,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotRecord,
    ReaderSnapshotUserAsset,
    ReaderTextRangeAnchor,
    ReadingRecordProductState,
)

from ._text import sanitize_failure_message
from .base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    NavigationUnitFact,
    ReadingBaseBuildResult,
    StableReadingBase,
    validate_reading_base_build_result,
)
from .span_recorder import parse_trace_id_from_envelope


@dataclass(frozen=True, slots=True)
class LoadedReaderSnapshotFacts:
    build_result: ReadingBaseBuildResult
    record: ReaderSnapshotRecord
    last_event_sequence: int
    snapshot_taken_at: datetime
    enhancement_layers: tuple[ReaderSnapshotLayer, ...]
    enhancement_progress: ReaderEnhancementProgress
    parsed_decisions: tuple[ReaderSnapshotParsedDecision, ...]
    user_assets: tuple[ReaderSnapshotUserAsset, ...] = ()
    ask_supplements: tuple[ReaderSnapshotAskSupplement, ...] = ()


@dataclass(frozen=True, slots=True)
class ReaderRecordSummary:
    record_id: UUID
    title: str | None
    source_type: str
    product_state: str
    readiness_state: str
    created_at: datetime
    source_metadata: dict[str, Any]
    last_event_sequence: int
    last_opened_at: datetime | None = None


_PROGRESS_CAPABILITIES = ("translation", "vocabulary", "grammar")
_JOB_CAPABILITY_BY_TYPE = {
    "translate_unit": "translation",
    "translate_article": "translation",
    "build_vocabulary_layer": "vocabulary",
    "build_vocabulary_layer_article": "vocabulary",
    "build_grammar_bundle": "grammar",
    "build_grammar_bundle_window": "grammar",
}
_JOB_LAYER_TYPE_BY_TYPE = {
    "translate_unit": "translation",
    "translate_article": "translation",
    "build_vocabulary_layer": "vocabulary",
    "build_vocabulary_layer_article": "vocabulary",
    "build_grammar_bundle": None,
    "build_grammar_bundle_window": None,
}
_LAYER_CAPABILITY_BY_TYPE = {
    "translation": "translation",
    "vocabulary": "vocabulary",
    "grammar_note": "grammar",
    "sentence_analysis": "grammar",
}
_USER_ACTION_REQUIRED_FAILURE_CODES = frozenset({"reader_user_confirmation_required"})


class ReaderOrchestrationRepository:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def read_trace_id_for_record(self, record_id: UUID) -> UUID | None:
        """Return the ``trace_id`` from the latest run's envelope for a record.

        Used by the worker loop to link the ``pipeline_root`` span to the
        trace_id the orchestrator assigned at submit time (gap report #3).
        Returns ``None`` when the record has no runs or the envelope lacks
        a ``trace_id`` (legacy rows); the caller falls back to ``uuid4()``.
        """

        async with self.get_pool().acquire() as conn:
            envelope_json = await conn.fetchval(
                """
                SELECT envelope_json
                FROM reader_runs
                WHERE reading_record_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                record_id,
            )
        return parse_trace_id_from_envelope(envelope_json)

    async def insert_reading_record(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        client_record_id: str | None,
        title: str,
        language: str,
        created_at: datetime,
        reading_goal: str = DEFAULT_READER_ORCHESTRATION_READING_GOAL,
        reading_variant: str = DEFAULT_READER_ORCHESTRATION_READING_VARIANT,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id,
                user_id,
                client_record_id,
                source_type,
                title,
                language,
                lifecycle_status,
                product_state,
                readiness_state,
                generation,
                reading_goal,
                reading_variant,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                'text',
                $4,
                $5,
                'active',
                'processing',
                'submitted',
                1,
                $6,
                $7,
                $8,
                $8
            )
            """,
            record_id,
            user_id,
            client_record_id,
            title,
            language,
            reading_goal,
            reading_variant,
            created_at,
        )

    async def insert_original_input(
        self,
        conn: asyncpg.Connection,
        *,
        original_input_id: UUID,
        record_id: UUID,
        user_id: UUID,
        source_text: str,
        source_metadata: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id,
                reading_record_id,
                user_id,
                input_type,
                source_text,
                source_ref_json,
                metadata_json,
                content_sha256,
                created_at
            )
            VALUES (
                $1,
                $2,
                $3,
                'plain_text',
                $4,
                '{}'::jsonb,
                $5::jsonb,
                $6,
                $7
            )
            """,
            original_input_id,
            record_id,
            user_id,
            source_text,
            jsonb_param(source_metadata),
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            created_at,
        )

    async def insert_reading_base(
        self,
        conn: asyncpg.Connection,
        *,
        base_id: UUID,
        build_result: ReadingBaseBuildResult,
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id,
                reading_record_id,
                base_version,
                record_generation,
                text,
                content_sha256,
                content_utf16_length,
                canonicalizer_version,
                builder_version,
                segmenter_version,
                language,
                title_snapshot,
                navigation_json,
                status,
                frozen_at,
                created_at
            )
            VALUES (
                $1,
                $2,
                1,
                1,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10,
                $11::jsonb,
                'active',
                $12,
                $12
            )
            """,
            base_id,
            UUID(build_result.base.reading_record_id),
            build_result.base.text,
            build_result.base.content_sha256,
            build_result.base.content_utf16_length,
            build_result.base.canonicalizer_version,
            build_result.base.builder_version,
            build_result.base.segmenter_version,
            build_result.base.language,
            build_result.base.title_snapshot,
            jsonb_param(_navigation_json_from_build_result(build_result)),
            created_at,
        )

    async def insert_reading_units(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        units: tuple[BuiltReadingUnit, ...],
    ) -> None:
        for unit in units:
            await conn.execute(
                """
                INSERT INTO reading_units (
                    reading_record_id,
                    base_id,
                    unit_id,
                    order_index,
                    unit_type,
                    boundary_quality,
                    base_start_utf16,
                    base_end_utf16,
                    text_hash,
                    metadata_json
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10::jsonb
                )
                """,
                record_id,
                base_id,
                unit.unit_id,
                unit.order_index,
                unit.unit_type,
                unit.boundary_quality,
                unit.base_start_utf16,
                unit.base_end_utf16,
                unit.text_hash,
                jsonb_param({}),
            )

    async def insert_anchor_segments(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        anchor_segments: tuple[BuiltAnchorSegment, ...],
    ) -> None:
        for segment in anchor_segments:
            await conn.execute(
                """
                INSERT INTO anchor_segments (
                    reading_record_id,
                    base_id,
                    unit_id,
                    anchor_segment_id,
                    sentence_id,
                    paragraph_id,
                    order_index,
                    unit_order_index,
                    segment_type,
                    base_start_utf16,
                    base_end_utf16,
                    unit_start_utf16,
                    unit_end_utf16,
                    text_hash,
                    boundary_quality
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10,
                    $11,
                    $12,
                    $13,
                    $14,
                    $15
                )
                """,
                record_id,
                base_id,
                segment.unit_id,
                segment.anchor_segment_id,
                segment.sentence_id,
                segment.paragraph_id,
                segment.order_index,
                segment.unit_order_index,
                segment.segment_type,
                segment.base_start_utf16,
                segment.base_end_utf16,
                segment.unit_start_utf16,
                segment.unit_end_utf16,
                segment.text_hash,
                segment.boundary_quality,
            )

    async def set_active_base_and_mark_article_ready(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        updated_at: datetime,
    ) -> None:
        base_row = await conn.fetchrow(
            """
            SELECT reading_record_id, record_generation, status
            FROM reading_bases
            WHERE id = $1
            """,
            base_id,
        )
        if base_row is None:
            raise ValueError(f"active base {base_id} does not exist")
        if base_row["reading_record_id"] != record_id:
            raise ValueError("active base must belong to the same reading record")
        if int(base_row["record_generation"]) != expected_generation:
            raise ValueError("active base must match the reading record generation")
        if base_row["status"] != "active":
            raise ValueError("active base must have status 'active'")

        result = await conn.execute(
            """
            UPDATE reading_records
            SET active_base_id = $2,
                lifecycle_status = 'active',
                product_state = 'readable_enhancing',
                readiness_state = 'article_ready',
                updated_at = $4
            WHERE id = $1
              AND generation = $3
            """,
            record_id,
            base_id,
            expected_generation,
            updated_at,
        )
        if result != "UPDATE 1":
            raise ValueError("reading record generation mismatch while setting active base")

    async def update_record_product_state_if_active(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        expected_generation: int,
        next_product_state: ReadingRecordProductState,
        updated_at: datetime,
    ) -> bool:
        result = await conn.execute(
            """
            UPDATE reading_records
            SET product_state = $3,
                updated_at = $4
            WHERE id = $1
              AND generation = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
              AND product_state IN ('processing', 'readable_enhancing')
            """,
            record_id,
            expected_generation,
            next_product_state,
            updated_at,
        )
        return result == "UPDATE 1"

    # ------------------------------------------------------------------
    # T3.5 completion state finalizer helpers
    #
    # These helpers are pure-read PostgreSQL queries used by the completion
    # finalizer to decide whether a record's enhancement work is fully
    # terminal. They do not write, do not take locks, and do not modify the
    # public schema. The finalizer composes them with a single
    # ``update_record_readiness_state_if_active`` write inside the worker
    # loop's existing per-record transaction.
    # ------------------------------------------------------------------

    async def count_enhancement_jobs_by_terminal_status(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        job_types: Sequence[str],
    ) -> dict[str, int]:
        """Return per-status counts for the given enhancement job types.

        Only rows matching the (record_id, base_id, expected_generation)
        fence are counted. The result dict always contains every reader
        job status key (queued, claimed, retry_later, paused, skipped,
        succeeded, failed_terminal, cancelled, superseded) initialized to
        0 so callers can branch on ``.get(status, 0)`` without KeyError.
        """
        rows = await conn.fetch(
            """
            SELECT status, COUNT(*) AS count
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = ANY($4::text[])
            GROUP BY status
            """,
            record_id,
            base_id,
            expected_generation,
            list(job_types),
        )
        counts: dict[str, int] = {
            "queued": 0,
            "claimed": 0,
            "retry_later": 0,
            "paused": 0,
            "skipped": 0,
            "succeeded": 0,
            "failed_terminal": 0,
            "cancelled": 0,
            "superseded": 0,
        }
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        return counts

    async def count_analysis_windows_by_terminal_status(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
    ) -> dict[str, int]:
        """Return per-status counts for analysis windows attached to the
        record's active base / generation.

        Mirrors ``count_enhancement_jobs_by_terminal_status`` for
        ``analysis_windows``. The result dict always contains every
        analysis-window status key (pending, running, completed, no_op,
        failed) initialized to 0. Records without a plan return all-zero
        counts; the finalizer treats "no windows" as terminal (clean
        completion path for non-Z+ records).
        """
        rows = await conn.fetch(
            """
            SELECT aw.status, COUNT(*) AS count
            FROM analysis_windows aw
            JOIN layer_analysis_plans plan
              ON plan.id = aw.plan_id
            WHERE plan.reading_record_id = $1
              AND plan.base_id = $2
              AND plan.generation = $3
            GROUP BY aw.status
            """,
            record_id,
            base_id,
            expected_generation,
        )
        counts: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "no_op": 0,
            "failed": 0,
        }
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        return counts

    async def update_record_readiness_state_if_active(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        expected_generation: int,
        current_readiness_states: Sequence[str],
        next_readiness_state: str,
        updated_at: datetime,
    ) -> tuple[bool, str | None]:
        """Transition ``readiness_state`` for a record that is still in one
        of ``current_readiness_states``.

        Used by the T3.5 finalizer to move ``article_ready`` /
        ``initial_enhancement_ready`` records to ``coverage_complete``. The
        WHERE clause guards against stale generations, deleted records, and
        records that already advanced out of the expected readiness window
        (e.g. a parallel worker transitioned to ``failed``).

        Returns ``(True, previous_readiness_state)`` when exactly one row
        was updated, ``(False, None)`` otherwise. The previous value is
        read before the UPDATE so the finalizer can include it in the
        ``record_state_changed`` event payload. The read + UPDATE run
        inside the caller's transaction (which holds the per-record
        advisory lock), so there is no TOCTOU window.
        """
        previous_readiness_state = await conn.fetchval(
            """
            SELECT readiness_state
            FROM reading_records
            WHERE id = $1
              AND generation = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
              AND readiness_state = ANY($3::text[])
            """,
            record_id,
            expected_generation,
            list(current_readiness_states),
        )
        if previous_readiness_state is None:
            return False, None

        result = await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = $3,
                updated_at = $4
            WHERE id = $1
              AND generation = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
              AND readiness_state = ANY($5::text[])
            """,
            record_id,
            expected_generation,
            next_readiness_state,
            updated_at,
            list(current_readiness_states),
        )
        if result != "UPDATE 1":
            return False, None
        return True, str(previous_readiness_state)

    async def force_fail_non_terminal_analysis_windows(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        failure_code: str,
        failure_reason: str,
        updated_at: datetime,
    ) -> int:
        """Force-fail any ``pending`` / ``running`` analysis windows for the
        record's active base / generation.

        Used by the T3.5 finalizer when all enhancement jobs are terminal
        but analysis windows remain non-terminal (e.g. the Z+ grammar
        window worker is not registered in this deployment, or a window
        lease is stuck in ``running``). The candidate scan only re-picks
        records with runnable jobs, so leaving such windows pending would
        wedge the record in ``article_ready`` / ``initial_enhancement_ready``
        forever. The finalizer instead mutates the stuck windows to
        ``failed`` and proceeds to ``coverage_complete`` with a
        ``completed_with_failures`` outcome.

        The failure metadata is merged into ``coverage.diagnostics`` so
        T3.4a diagnostic queries surface the forced-fail reason without a
        schema migration. ``completed_at`` is stamped so the window is
        observably terminal.

        Returns the number of windows transitioned to ``failed``.
        """
        coverage_payload = {
            "diagnostics": {
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "forced_by": "completion_finalizer",
                "forced_at": updated_at.isoformat(),
            }
        }
        result = await conn.execute(
            """
            UPDATE analysis_windows SET
                status = 'failed',
                coverage = COALESCE(coverage, '{}'::jsonb) || $5::jsonb,
                completed_at = $4
            FROM layer_analysis_plans plan
            WHERE analysis_windows.plan_id = plan.id
              AND plan.reading_record_id = $1
              AND plan.base_id = $2
              AND plan.generation = $3
              AND analysis_windows.status IN ('pending', 'running')
            """,
            record_id,
            base_id,
            expected_generation,
            updated_at,
            jsonb_param(coverage_payload),
        )
        return int(result.split()[-1]) if result.startswith("UPDATE") else 0

    async def force_fail_non_terminal_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        job_types: tuple[str, ...],
        failure_code: str,
        failure_reason: str,
        updated_at: datetime,
    ) -> int:
        """T4.2a-R2: Force-fail non-terminal enhancement jobs.

        Used by the completion finalizer when the pipeline stopped due
        to ``budget_exhausted`` and non-terminal jobs remain. Without
        this, the record would be wedged: the candidate scan re-picks
        it (runnable jobs exist), but the budget guard keeps skipping
        all workers, creating a deadlock.

        Transitions ``queued`` / ``claimed`` / ``retry_later`` /
        ``paused`` jobs to ``failed_terminal`` with the given failure
        code / message. Returns the number of jobs transitioned.
        """
        if not job_types:
            return 0
        # Build placeholders for the IN clause: $7, $8, ...
        placeholders = ", ".join(
            f"${7 + i}" for i in range(len(job_types))
        )
        params: list[Any] = [
            record_id,
            base_id,
            expected_generation,
            failure_code,
            failure_reason,
            updated_at,
        ]
        params.extend(job_types)
        result = await conn.execute(
            f"""
            UPDATE reader_jobs SET
                status = 'failed_terminal',
                failure_code = $4,
                failure_message = $5,
                updated_at = $6,
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                claimed_at = NULL
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
              AND job_type IN ({placeholders})
            """,
            *params,
        )
        return int(result.split()[-1]) if result.startswith("UPDATE") else 0

    async def supersede_conflicting_legacy_grammar_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        rationale_code: str,
        updated_at: datetime,
    ) -> int:
        """T4.2a-R2-R2: Supersede conflicting legacy per-unit grammar jobs.

        When a grammar batch job is authoritative (succeeded /
        failed_terminal / skipped / non-terminal), any legacy
        ``build_grammar_bundle`` per-unit jobs (``target_type = 'unit'``)
        that are still non-superseded represent stale topology from a
        route cutover or upgrade. They must be transitioned to
        ``superseded`` — not left queued — to avoid a permanent hot-loop
        where the worker scanner keeps finding runnable jobs that the
        fallback guard suppresses every tick.

        Transitions ``queued`` / ``claimed`` / ``retry_later`` /
        ``paused`` legacy per-unit grammar jobs to ``superseded`` with
        the given ``rationale_code`` and inserts a ``job_superseded``
        event for each. Returns the number of jobs transitioned.

        ``claimed`` jobs have their lease cleared. This is safe because
        the fallback guard has already decided these jobs must never
        execute (batch path is authoritative).
        """
        rows = await conn.fetch(
            """
            SELECT id, run_id, reading_record_id, status
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = 'build_grammar_bundle'
              AND target_type = 'unit'
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
            ORDER BY id ASC
            FOR UPDATE
            """,
            record_id,
            base_id,
            expected_generation,
        )
        if not rows:
            return 0
        for row in rows:
            await conn.execute(
                """
                UPDATE reader_jobs SET
                    status = 'superseded',
                    rationale_code = $2,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    claimed_at = NULL,
                    updated_at = $3
                WHERE id = $1
                """,
                row["id"],
                rationale_code,
                updated_at,
            )
            await conn.execute(
                """
                INSERT INTO reader_job_events (
                    reading_record_id, run_id, job_id,
                    event_type, payload_json, created_at
                )
                VALUES ($1, $2, $3, 'job_superseded', $4::jsonb, $5)
                """,
                row["reading_record_id"],
                row["run_id"],
                row["id"],
                jsonb_param({
                    "rationale_code": rationale_code,
                    "previous_status": str(row["status"]),
                    "cleanup": "legacy_grammar_fallback_suppressed",
                }),
                updated_at,
            )
        return len(rows)

    async def ensure_event_sequence_row(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        updated_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reader_event_sequences (reading_record_id, next_sequence, updated_at)
            VALUES ($1, 1, $2)
            ON CONFLICT (reading_record_id) DO NOTHING
            """,
            record_id,
            updated_at,
        )

    async def allocate_event_sequence(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
    ) -> int:
        sequence = await conn.fetchval(
            """
            UPDATE reader_event_sequences
            SET next_sequence = next_sequence + 1,
                updated_at = NOW()
            WHERE reading_record_id = $1
            RETURNING next_sequence - 1
            """,
            record_id,
        )
        if not isinstance(sequence, int):
            raise ValueError(f"reader_event_sequences missing for record {record_id}")
        return sequence

    async def insert_reader_event(
        self,
        conn: asyncpg.Connection,
        *,
        event_id: UUID,
        record_id: UUID,
        sequence: int,
        event_type: str,
        payload_json: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reader_events (
                id,
                reading_record_id,
                sequence,
                event_type,
                payload_json,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            event_id,
            record_id,
            sequence,
            event_type,
            jsonb_param(payload_json),
            created_at,
        )

    async def upsert_parsed_decision(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        base_id: UUID,
        unit_id: str,
        policy_code: str,
        parsed_state: str,
        rationale_code: str | None = None,
        coverage_json: dict[str, Any] | None = None,
        source_layer_id: UUID | None = None,
        source_job_id: UUID | None = None,
        decision_json: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO parsed_decisions (
                reading_record_id,
                base_id,
                unit_id,
                policy_code,
                parsed_state,
                rationale_code,
                coverage_json,
                source_layer_id,
                source_job_id,
                decision_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
            ON CONFLICT (reading_record_id, base_id, unit_id, policy_code)
            DO UPDATE SET
                parsed_state = EXCLUDED.parsed_state,
                rationale_code = EXCLUDED.rationale_code,
                coverage_json = EXCLUDED.coverage_json,
                source_layer_id = EXCLUDED.source_layer_id,
                source_job_id = EXCLUDED.source_job_id,
                decision_json = EXCLUDED.decision_json,
                created_at = NOW()
            """,
            reading_record_id,
            base_id,
            unit_id,
            policy_code,
            parsed_state,
            rationale_code,
            jsonb_param(coverage_json or {}),
            source_layer_id,
            source_job_id,
            jsonb_param(decision_json or {}),
        )

    async def load_snapshot_facts(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> LoadedReaderSnapshotFacts:
        record_row = await conn.fetchrow(
            """
            SELECT
                r.id,
                r.user_id,
                r.source_type,
                r.title,
                r.generated_title_zh,
                r.title_generation_status,
                r.title_generation_error_code,
                r.title_generation_error_message,
                r.language,
                r.product_state,
                r.readiness_state,
                r.generation,
                r.active_base_id,
                r.reading_goal,
                r.reading_variant,
                r.created_at AS record_created_at,
                r.updated_at AS record_updated_at,
                b.id AS base_id,
                b.record_generation,
                b.text,
                b.content_sha256,
                b.content_utf16_length,
                b.canonicalizer_version,
                b.builder_version,
                b.segmenter_version,
                b.language AS base_language,
                b.title_snapshot,
                b.navigation_json,
                b.status AS base_status,
                b.created_at AS base_created_at,
                seq.next_sequence
            FROM reading_records r
            LEFT JOIN reading_bases b
              ON b.id = r.active_base_id
            LEFT JOIN reader_event_sequences seq
              ON seq.reading_record_id = r.id
            WHERE r.id = $1
              AND r.user_id = $2
              AND r.deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
        if record_row is None:
            raise LookupError(f"reading record {record_id} not found for user {user_id}")

        active_base_id = record_row["active_base_id"]
        if active_base_id is None or record_row["base_id"] is None:
            raise ValueError("reader snapshot requires an active base")

        base_id = UUID(str(record_row["base_id"]))
        record_generation = int(record_row["generation"])
        base_generation = int(record_row["record_generation"])

        if expected_base_id is not None and base_id != expected_base_id:
            raise ValueError(
                f"snapshot base_id {base_id} does not match expected {expected_base_id}"
            )
        if expected_generation is not None and record_generation != expected_generation:
            raise ValueError(
                "snapshot generation "
                f"{record_generation} does not match expected {expected_generation}"
            )
        if UUID(str(active_base_id)) != base_id:
            raise ValueError("active_base_id does not resolve to the selected snapshot base")
        if base_generation != record_generation:
            raise ValueError("active base generation does not match the reading record generation")
        if record_row["base_status"] != "active":
            raise ValueError("reader snapshot requires active_base_id to point to status='active'")

        input_row = await conn.fetchrow(
            """
            SELECT metadata_json
            FROM original_inputs
            WHERE reading_record_id = $1
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            record_id,
        )

        latest_event_row = await conn.fetchrow(
            """
            SELECT sequence, created_at
            FROM reader_events
            WHERE reading_record_id = $1
            ORDER BY sequence DESC
            LIMIT 1
            """,
            record_id,
        )
        if latest_event_row is None:
            raise ValueError("reader snapshot requires at least one committed reader event")

        next_sequence = record_row["next_sequence"]
        if next_sequence is None:
            raise ValueError("reader snapshot requires reader_event_sequences state")
        last_event_sequence = int(next_sequence) - 1
        if last_event_sequence != int(latest_event_row["sequence"]):
            raise ValueError("reader event counter does not match the latest committed event")

        base_text = str(record_row["text"])
        if utf16_code_unit_length(base_text) != int(record_row["content_utf16_length"]):
            raise ValueError("reading_bases.content_utf16_length does not match stored base text")
        if hashlib.sha256(base_text.encode("utf-8")).hexdigest() != record_row["content_sha256"]:
            raise ValueError("reading_bases.content_sha256 does not match stored base text")

        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, unit_type, boundary_quality,
                   base_start_utf16, base_end_utf16, text_hash
            FROM reading_units
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
        anchor_rows = await conn.fetch(
            """
            SELECT unit_id, anchor_segment_id, sentence_id, paragraph_id,
                   order_index, unit_order_index, segment_type, boundary_quality,
                   base_start_utf16, base_end_utf16, unit_start_utf16, unit_end_utf16, text_hash
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
        if not unit_rows or not anchor_rows:
            raise ValueError("reader snapshot requires persisted units and anchor segments")

        navigation_map = _navigation_map_by_unit_id(record_row["navigation_json"])
        base_language = record_row["base_language"] or record_row["language"]
        title_snapshot = record_row["title_snapshot"] or record_row["title"]
        source_metadata = (
            ensure_json_object(input_row["metadata_json"]) if input_row is not None else {}
        )
        stable_base = StableReadingBase(
            reading_record_id=str(record_id),
            base_id=str(base_id),
            text=base_text,
            content_sha256=str(record_row["content_sha256"]),
            content_utf16_length=int(record_row["content_utf16_length"]),
            canonicalizer_version=str(record_row["canonicalizer_version"]),
            builder_version=str(record_row["builder_version"]),
            segmenter_version=str(record_row["segmenter_version"]),
            language=str(base_language) if base_language is not None else None,
            title_snapshot=str(title_snapshot) if title_snapshot is not None else None,
        )

        units: list[BuiltReadingUnit] = []
        navigation_units: list[NavigationUnitFact] = []
        for row in unit_rows:
            unit_id = str(row["unit_id"])
            unit_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            if unit_text is None:
                raise ValueError(
                    f"reading unit {unit_id} does not round-trip from stored base text"
                )
            if compute_text_range_hash(unit_text) != row["text_hash"]:
                raise ValueError(
                    f"reading unit {unit_id} text_hash does not match stored base text"
                )

            navigation_item = navigation_map.get(unit_id, {})
            boundary_quality = str(
                navigation_item.get("boundary_quality") or row["boundary_quality"]
            )
            label = navigation_item.get("label")
            label_text = label if isinstance(label, str) else None

            units.append(
                BuiltReadingUnit(
                    reading_record_id=str(record_id),
                    base_id=str(base_id),
                    unit_id=unit_id,
                    order_index=int(row["order_index"]),
                    unit_type=str(row["unit_type"]),
                    boundary_quality=boundary_quality,
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                    text_hash=str(row["text_hash"]),
                    text=unit_text,
                    label=label_text,
                )
            )
            navigation_units.append(
                NavigationUnitFact(
                    unit_id=unit_id,
                    order_index=int(row["order_index"]),
                    unit_type=str(row["unit_type"]),
                    boundary_quality=boundary_quality,
                    label=label_text,
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                )
            )

        units_by_id = {unit.unit_id: unit for unit in units}
        anchor_segments: list[BuiltAnchorSegment] = []
        for row in anchor_rows:
            unit_id = str(row["unit_id"])
            unit = units_by_id.get(unit_id)
            if unit is None:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} references unknown unit {unit_id}"
                )

            segment_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            if segment_text is None:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} does not round-trip from stored base text"
                )
            unit_local_text = slice_by_utf16_offsets(
                unit.text,
                int(row["unit_start_utf16"]),
                int(row["unit_end_utf16"]),
            )
            if unit_local_text != segment_text:
                raise ValueError(
                    f"anchor segment {row['anchor_segment_id']} local offsets do not match its unit"
                )
            if compute_text_range_hash(segment_text) != row["text_hash"]:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} text_hash does not match stored text"
                )

            anchor_segments.append(
                BuiltAnchorSegment(
                    reading_record_id=str(record_id),
                    base_id=str(base_id),
                    unit_id=unit_id,
                    anchor_segment_id=str(row["anchor_segment_id"]),
                    sentence_id=str(row["sentence_id"] or row["anchor_segment_id"]),
                    paragraph_id=str(row["paragraph_id"]),
                    order_index=int(row["order_index"]),
                    unit_order_index=int(row["unit_order_index"]),
                    segment_type=str(row["segment_type"]),
                    boundary_quality=str(row["boundary_quality"]),
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                    unit_start_utf16=int(row["unit_start_utf16"]),
                    unit_end_utf16=int(row["unit_end_utf16"]),
                    text_hash=str(row["text_hash"]),
                    text=segment_text,
                )
            )

        build_result = ReadingBaseBuildResult(
            base=stable_base,
            units=tuple(units),
            anchor_segments=tuple(anchor_segments),
            navigation_units=tuple(navigation_units),
        )
        validate_reading_base_build_result(build_result)

        enhancement_rows = await conn.fetch(
            """
            SELECT id, layer_type, layer_subtype, target_scope, target_key,
                   schema_version, output_json, published_at
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND status = 'published'
            ORDER BY layer_type, target_scope, target_key, published_at, id
            """,
            record_id,
            base_id,
            record_generation,
        )
        progress_layer_rows = await conn.fetch(
            """
            SELECT id, layer_type, layer_subtype, target_scope, target_key,
                   status, source_job_id, created_at, updated_at, published_at
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND status IN ('draft', 'published', 'failed')
            ORDER BY layer_type, target_scope, target_key, created_at, id
            """,
            record_id,
            base_id,
            record_generation,
        )
        progress_job_rows = await conn.fetch(
            """
            SELECT id, job_type, target_type, target_key, status,
                   operation_fingerprint, failure_code, failure_message,
                   created_at, updated_at
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND user_id = $4
              AND job_type IN (
                'translate_unit',
                'translate_article',
                'build_vocabulary_layer',
                'build_vocabulary_layer_article',
                'build_grammar_bundle',
                'build_grammar_bundle_window'
              )
            ORDER BY created_at, id
            """,
            record_id,
            base_id,
            record_generation,
            user_id,
        )
        parsed_rows = await conn.fetch(
            """
            SELECT unit_id, policy_code, parsed_state, rationale_code
            FROM parsed_decisions
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY unit_id, policy_code
            """,
            record_id,
            base_id,
        )
        title_generation_status = str(record_row["title_generation_status"] or "pending")
        display_title_zh = (
            str(record_row["generated_title_zh"])
            if record_row["generated_title_zh"] is not None
            else None
        )
        if title_generation_status == "succeeded" and not display_title_zh:
            raise ValueError(
                "reader snapshot requires display_title_zh when title generation succeeded"
            )

        snapshot_record = ReaderSnapshotRecord(
            title=str(title_snapshot) if title_snapshot is not None else "Untitled Reading",
            display_title_zh=(
                display_title_zh if title_generation_status == "succeeded" else None
            ),
            title_generation_status=title_generation_status,
            title_generation_error_code=(
                str(record_row["title_generation_error_code"])
                if record_row["title_generation_error_code"] is not None
                else None
            ),
            title_generation_error_message=sanitize_failure_message(
                record_row["title_generation_error_message"]
            ),
            created_at=record_row["record_created_at"],
            source_type=str(record_row["source_type"]),
            source_metadata=source_metadata,
            generation=record_generation,
            product_state=str(record_row["product_state"]),
            readiness_state=str(record_row["readiness_state"]),
            reading_goal=str(record_row["reading_goal"]),
            reading_variant=str(record_row["reading_variant"]),
        )
        snapshot_layers = tuple(
            ReaderSnapshotLayer(
                layer_id=str(row["id"]),
                layer_type=str(row["layer_type"]),
                layer_subtype=(
                    str(row["layer_subtype"]) if row["layer_subtype"] is not None else None
                ),
                base_id=str(base_id),
                target_scope=str(row["target_scope"]),
                target_key=str(row["target_key"]),
                schema_version=int(row["schema_version"]),
                output=row["output_json"],
                published_at=row["published_at"],
            )
            for row in enhancement_rows
        )

        user_assets = await self._load_user_assets_for_snapshot(
            conn,
            record_id=record_id,
            user_id=user_id,
            base_id=base_id,
            generation=record_generation,
            build_result=build_result,
        )

        ask_supplements = await self._load_ask_supplements_for_snapshot(
            conn,
            record_id=record_id,
            user_id=user_id,
            base_id=base_id,
            generation=record_generation,
            build_result=build_result,
        )

        return LoadedReaderSnapshotFacts(
            build_result=build_result,
            record=snapshot_record,
            last_event_sequence=last_event_sequence,
            snapshot_taken_at=latest_event_row["created_at"],
            enhancement_layers=snapshot_layers,
            enhancement_progress=_build_enhancement_progress(
                product_state=snapshot_record.product_state,
                layer_rows=progress_layer_rows,
                job_rows=progress_job_rows,
            ),
            parsed_decisions=tuple(
                ReaderSnapshotParsedDecision(
                    unit_id=str(row["unit_id"]),
                    policy_code=str(row["policy_code"]),
                    parsed_state=str(row["parsed_state"]),
                    rationale_code=(
                        str(row["rationale_code"]) if row["rationale_code"] is not None else None
                    ),
                )
                for row in parsed_rows
            ),
            user_assets=user_assets,
            ask_supplements=ask_supplements,
        )

    async def _load_user_assets_for_snapshot(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        base_id: UUID,
        generation: int,
        build_result: ReadingBaseBuildResult,
    ) -> tuple[ReaderSnapshotUserAsset, ...]:
        """Load D6-U4 Reading Record user assets for the current snapshot.

        Only rows matching the active base_id + generation are returned.
        Legacy analysis_record_id rows are excluded. Stale base/generation
        rows are excluded by the base_id + generation filter.

        D6-U5.1 defensive validation: each row is validated against the
        active base facts (unit_text, anchor_segment range, selected_text,
        text_hash) before being admitted into the snapshot. Rows that fail
        validation are silently skipped so that a single dirty highlight or
        note does not make the article unreadable. This is read-side
        defensive filtering only; write-side validation remains the source
        of truth.
        """
        unit_text_by_id: dict[str, str] = {
            unit.unit_id: unit.text for unit in build_result.units
        }
        anchor_segment_by_id: dict[str, AnchorSegmentRange] = {
            seg.anchor_segment_id: AnchorSegmentRange(
                anchor_segment_id=seg.anchor_segment_id,
                unit_start_utf16=seg.unit_start_utf16,
                unit_end_utf16=seg.unit_end_utf16,
            )
            for seg in build_result.anchor_segments
        }

        highlight_rows = await conn.fetch(
            """
            SELECT ua.id, ua.unit_id, ua.anchor_segment_id,
                   ua.unit_start_utf16, ua.unit_end_utf16,
                   ua.selected_text, ua.text_hash, ua.color,
                   ua.created_at, ua.updated_at,
                   seg.sentence_id, seg.segment_type
            FROM user_annotations ua
            LEFT JOIN anchor_segments seg
              ON seg.reading_record_id = ua.reading_record_id
             AND seg.base_id = ua.base_id
             AND seg.anchor_segment_id = ua.anchor_segment_id
            WHERE ua.reading_record_id = $1
              AND ua.base_id = $2
              AND ua.generation = $3
              AND ua.user_id = $4
              AND ua.deleted_at IS NULL
              AND ua.analysis_record_id IS NULL
            ORDER BY ua.created_at, ua.id
            """,
            record_id,
            base_id,
            generation,
            user_id,
        )
        note_rows = await conn.fetch(
            """
            SELECT rn.id, rn.unit_id, rn.anchor_segment_id,
                   rn.unit_start_utf16, rn.unit_end_utf16,
                   rn.selected_text, rn.text_hash, rn.note_text,
                   rn.created_at, rn.updated_at,
                   seg.sentence_id, seg.segment_type
            FROM reader_notes rn
            LEFT JOIN anchor_segments seg
              ON seg.reading_record_id = rn.reading_record_id
             AND seg.base_id = rn.base_id
             AND seg.anchor_segment_id = rn.anchor_segment_id
            WHERE rn.reading_record_id = $1
              AND rn.base_id = $2
              AND rn.generation = $3
              AND rn.user_id = $4
              AND rn.deleted_at IS NULL
              AND rn.analysis_record_id IS NULL
            ORDER BY rn.created_at, rn.id
            """,
            record_id,
            base_id,
            generation,
            user_id,
        )

        assets: list[ReaderSnapshotUserAsset] = []
        for row in highlight_rows:
            asset = _build_validated_user_asset(
                row,
                base_id=base_id,
                record_id=record_id,
                generation=generation,
                unit_text_by_id=unit_text_by_id,
                anchor_segment_by_id=anchor_segment_by_id,
                asset_type="highlight",
            )
            if asset is not None:
                assets.append(asset)
        for row in note_rows:
            asset = _build_validated_user_asset(
                row,
                base_id=base_id,
                record_id=record_id,
                generation=generation,
                unit_text_by_id=unit_text_by_id,
                anchor_segment_by_id=anchor_segment_by_id,
                asset_type="note",
            )
            if asset is not None:
                assets.append(asset)
        return tuple(assets)

    async def _load_ask_supplements_for_snapshot(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        base_id: UUID,
        generation: int,
        build_result: ReadingBaseBuildResult,
    ) -> tuple[ReaderSnapshotAskSupplement, ...]:
        """Load Reading Record ask supplements for the current snapshot.

        Mirrors the user_assets contract: only rows matching the active
        base_id + generation with reading_record_id matching the current
        record and deleted_at IS NULL are returned. Legacy
        analysis_record_id rows are excluded so they do not leak into the
        Reading Record Plate projection.

        Defensive validation: each row's anchor is validated against the
        active base facts (unit_text, anchor_segment range, selected_text,
        text_hash). Rows that fail validation are silently skipped so a
        single dirty supplement does not make the article unreadable.
        """
        unit_text_by_id: dict[str, str] = {
            unit.unit_id: unit.text for unit in build_result.units
        }
        anchor_segment_by_id: dict[str, AnchorSegmentRange] = {
            seg.anchor_segment_id: AnchorSegmentRange(
                anchor_segment_id=seg.anchor_segment_id,
                unit_start_utf16=seg.unit_start_utf16,
                unit_end_utf16=seg.unit_end_utf16,
            )
            for seg in build_result.anchor_segments
        }

        rows = await conn.fetch(
            """
            SELECT s.id, s.supplement_type, s.target_key, s.sentence_id,
                   s.paragraph_id, s.title, s.content_md, s.metadata_json,
                   s.schema_version, s.created_from_turn_run_id,
                   s.created_at, s.updated_at,
                   s.base_id, s.generation, s.unit_id, s.anchor_segment_id,
                   s.start_offset, s.end_offset, s.text_hash, s.hash_algorithm,
                   s.anchor_payload_json,
                   seg.sentence_id AS segment_sentence_id,
                   seg.segment_type AS segment_type
            FROM reader_ask_supplements s
            LEFT JOIN anchor_segments seg
              ON seg.reading_record_id = s.reading_record_id
             AND seg.base_id = s.base_id
             AND seg.anchor_segment_id = s.anchor_segment_id
            WHERE s.reading_record_id = $1
              AND s.base_id = $2
              AND s.generation = $3
              AND s.user_id = $4
              AND s.deleted_at IS NULL
              AND s.analysis_record_id IS NULL
            ORDER BY s.created_at, s.id
            """,
            record_id,
            base_id,
            generation,
            user_id,
        )

        supplements: list[ReaderSnapshotAskSupplement] = []
        for row in rows:
            supplement = _build_validated_ask_supplement(
                row,
                base_id=base_id,
                record_id=record_id,
                generation=generation,
                unit_text_by_id=unit_text_by_id,
                anchor_segment_by_id=anchor_segment_by_id,
            )
            if supplement is not None:
                supplements.append(supplement)
        return tuple(supplements)

    async def mark_record_opened(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        opened_at: datetime,
    ) -> datetime | None:
        """Stamp ``reading_records.last_opened_at`` for an active, owned
        Reading Record. Must not touch ``updated_at`` and must not write
        ``reader_events``. Returns the new timestamp on success, or
        ``None`` when the record does not exist, is deleted, or belongs to
        another user.
        """
        pool = self.get_pool()
        async with pool.acquire() as conn:
            new_value = await conn.fetchval(
                """
                UPDATE reading_records
                SET last_opened_at = $3
                WHERE id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                  AND lifecycle_status = 'active'
                RETURNING last_opened_at
                """,
                record_id,
                user_id,
                opened_at,
            )
        return new_value

    async def list_user_records(
        self,
        *,
        user_id: UUID,
        limit: int = 20,
        query: str | None = None,
        product_states: tuple[str, ...] | None = None,
    ) -> tuple[tuple[ReaderRecordSummary, ...], int]:
        normalized_query = query.strip() if query and query.strip() else None
        normalized_product_states = product_states or None
        pool = self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    r.id,
                    r.title,
                    r.source_type,
                    r.product_state,
                    r.readiness_state,
                    r.created_at,
                    r.last_opened_at,
                    COALESCE(
                        (SELECT metadata_json FROM original_inputs
                         WHERE reading_record_id = r.id
                         ORDER BY created_at ASC, id ASC
                         LIMIT 1),
                        '{}'::jsonb
                    ) AS source_metadata,
                    COALESCE(
                        (SELECT (next_sequence - 1)::bigint FROM reader_event_sequences
                         WHERE reading_record_id = r.id),
                        0
                    ) AS last_event_sequence
                FROM reading_records r
                WHERE r.user_id = $1
                  AND r.deleted_at IS NULL
                  AND ($3::text IS NULL OR COALESCE(r.title, '') ILIKE '%' || $3 || '%')
                  AND ($4::text[] IS NULL OR r.product_state::text = ANY($4::text[]))
                ORDER BY r.last_opened_at DESC NULLS LAST,
                         r.created_at DESC,
                         r.id DESC
                LIMIT $2
                """,
                user_id,
                limit,
                normalized_query,
                normalized_product_states,
            )
            total = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reading_records
                WHERE user_id = $1
                  AND deleted_at IS NULL
                  AND ($2::text IS NULL OR COALESCE(title, '') ILIKE '%' || $2 || '%')
                  AND ($3::text[] IS NULL OR product_state::text = ANY($3::text[]))
                """,
                user_id,
                normalized_query,
                normalized_product_states,
            )
        summaries = tuple(
            ReaderRecordSummary(
                record_id=row["id"],
                title=row["title"],
                source_type=str(row["source_type"]),
                product_state=str(row["product_state"]),
                readiness_state=str(row["readiness_state"]),
                created_at=row["created_at"],
                source_metadata=ensure_json_object(row["source_metadata"]),
                last_event_sequence=int(row["last_event_sequence"]),
                last_opened_at=row["last_opened_at"],
            )
            for row in rows
        )
        return summaries, int(total)


# ----------------------------------------------------------------------
# Candidate write-side uniqueness helper
# ----------------------------------------------------------------------


class CandidateWriteLockError(ValueError):
    """Raised when the candidate write lock cannot be acquired.

    ``reason_code`` is a stable identifier:
    - ``transaction_required``: the connection is not inside an active
      transaction. The caller must open one before calling
      :func:`lock_record_for_candidate_write`.
    - ``record_not_found``: the reading_record does not exist, does not
      belong to the given user, or is soft-deleted.
    - ``generation_mismatch``: the reading_record's current generation
      does not match ``expected_generation``.
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class CandidateWriteLockResult:
    """Result of locking a reading_records row for candidate writes.

    Attributes:
        record_id: The locked reading_record id.
        generation: The validated generation of the locked row.
    """

    record_id: UUID
    generation: int


async def lock_record_for_candidate_write(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    expected_generation: int,
) -> CandidateWriteLockResult:
    """Lock the parent reading_records row for a candidate write.

    Performs (within the caller's transaction):
        1. Fail-closed check: ``conn.is_in_transaction()`` must be True.
        2. ``SELECT id, generation FROM reading_records WHERE id = $1
           AND user_id = $2 AND deleted_at IS NULL FOR UPDATE``.
        3. Validate ``generation == expected_generation``.

    This function does NOT touch ``candidate_reading_documents``. The
    caller is responsible for calling
    :func:`supersede_ready_candidates_for_locked_record` immediately
    before INSERT-ing a new ``status='ready'`` candidate, and only on
    the candidate-writing branch (never on stable/rejected branches).

    The ``FOR UPDATE`` lock serializes concurrent callers: at most one
    transaction holds the lock at a time, so supersede-then-INSERT is
    atomic per (record_id, generation).

    Args:
        conn: An asyncpg connection that MUST already be inside a
            transaction (``async with conn.transaction():``).
        record_id: The reading_record id to lock.
        user_id: The owner of the reading_record.
        expected_generation: The expected generation value.

    Returns:
        A :class:`CandidateWriteLockResult` with the locked row's id and
        validated generation.

    Raises:
        CandidateWriteLockError: If ``conn`` is not in a transaction
            (``transaction_required``), the record is not found / not
            owned / deleted (``record_not_found``), or the generation
            does not match (``generation_mismatch``).
    """
    if not conn.is_in_transaction():
        raise CandidateWriteLockError(
            "lock_record_for_candidate_write must be called within an "
            "active transaction. Refusing to acquire a FOR UPDATE lock "
            "outside a transaction.",
            reason_code="transaction_required",
        )

    row = await conn.fetchrow(
        """
        SELECT id, generation
        FROM reading_records
        WHERE id = $1
          AND user_id = $2
          AND deleted_at IS NULL
        FOR UPDATE
        """,
        record_id,
        user_id,
    )
    if row is None:
        raise CandidateWriteLockError(
            f"reading_record {record_id} not found for user {user_id} "
            f"(not owned, soft-deleted, or does not exist)",
            reason_code="record_not_found",
        )
    actual_generation = int(row["generation"])
    if actual_generation != expected_generation:
        raise CandidateWriteLockError(
            f"reading_record {record_id} generation is "
            f"{actual_generation}, expected {expected_generation}",
            reason_code="generation_mismatch",
        )

    return CandidateWriteLockResult(
        record_id=record_id,
        generation=actual_generation,
    )


async def supersede_ready_candidates_for_locked_record(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int,
    now: datetime,
) -> None:
    """Supersede existing ``status='ready'`` candidates for a locked record.

    Performs (within the caller's transaction):
        ``UPDATE candidate_reading_documents SET status = 'superseded'
        WHERE reading_record_id = $1 AND user_id = $2 AND
        record_generation = $3 AND status = 'ready'``.

    The caller MUST already hold the ``FOR UPDATE`` lock on the parent
    ``reading_records`` row (via
    :func:`lock_record_for_candidate_write`) in the same transaction /
    same connection. This function does NOT acquire any lock itself — it
    relies entirely on the caller's held lock for serialization.

    Call this ONLY on the candidate-writing branch, immediately before
    INSERT-ing a new ``status='ready'`` candidate. Never call it on
    stable_document_ready or rejected branches.

    Args:
        conn: An asyncpg connection in the same transaction that holds
            the lock.
        record_id: The locked reading_record id.
        user_id: The owner of the reading_record.
        generation: The validated generation (from
            :class:`CandidateWriteLockResult.generation`).
        now: Timestamp for the supersede UPDATE.

    Raises:
        CandidateWriteLockError: If ``conn`` is not in a transaction
            (``transaction_required``). This is a defense-in-depth check;
            the caller should have already been in a transaction when it
            acquired the lock.
    """
    if not conn.is_in_transaction():
        raise CandidateWriteLockError(
            "supersede_ready_candidates_for_locked_record must be called "
            "within an active transaction. The caller must hold the FOR "
            "UPDATE lock on the parent reading_records row.",
            reason_code="transaction_required",
        )

    await conn.execute(
        """
        UPDATE candidate_reading_documents
        SET status = 'superseded',
            updated_at = $4
        WHERE reading_record_id = $1
          AND user_id = $2
          AND record_generation = $3
          AND status = 'ready'
        """,
        record_id,
        user_id,
        generation,
        now,
    )


def _build_validated_user_asset(
    row: asyncpg.Record,
    *,
    base_id: UUID,
    record_id: UUID,
    generation: int,
    unit_text_by_id: dict[str, str],
    anchor_segment_by_id: dict[str, AnchorSegmentRange],
    asset_type: str,
) -> ReaderSnapshotUserAsset | None:
    """Build a validated ReaderSnapshotUserAsset from a DB row.

    Returns None when the row fails defensive validation against the active
    base facts. This is read-side defensive filtering: a dirty highlight or
    note must not make the article unreadable.
    """
    unit_id = str(row["unit_id"])
    anchor_segment_id = str(row["anchor_segment_id"])

    unit_text = unit_text_by_id.get(unit_id)
    if unit_text is None:
        return None

    anchor_segment = anchor_segment_by_id.get(anchor_segment_id)
    if anchor_segment is None:
        return None

    start_offset = int(row["unit_start_utf16"])
    end_offset = int(row["unit_end_utf16"])
    selected_text = str(row["selected_text"])
    text_hash = str(row["text_hash"])

    try:
        validate_text_anchor_against_unit(
            offset_unit=TEXT_RANGE_OFFSET_UNIT,
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
            unit_text=unit_text,
            anchor_segment=anchor_segment,
        )
    except AnchorValidationError:
        return None

    segment_type = row["segment_type"] if row["segment_type"] is not None else "sentence"
    anchor = ReaderTextRangeAnchor(
        base_id=str(base_id),
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        sentence_id=str(row["sentence_id"]) if row["sentence_id"] is not None else None,
        segment_type=segment_type,
        start_offset=start_offset,
        end_offset=end_offset,
        selected_text=selected_text,
        text_hash=text_hash,
    )

    if asset_type == "highlight":
        return ReaderSnapshotUserAsset(
            asset_id=str(row["id"]),
            asset_type="highlight",
            reading_record_id=str(record_id),
            generation=generation,
            anchor=anchor,
            color=row["color"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    return ReaderSnapshotUserAsset(
        asset_id=str(row["id"]),
        asset_type="note",
        reading_record_id=str(record_id),
        generation=generation,
        anchor=anchor,
        note_text=row["note_text"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_validated_ask_supplement(
    row: asyncpg.Record,
    *,
    base_id: UUID,
    record_id: UUID,
    generation: int,
    unit_text_by_id: dict[str, str],
    anchor_segment_by_id: dict[str, AnchorSegmentRange],
) -> ReaderSnapshotAskSupplement | None:
    """Build a validated ReaderSnapshotAskSupplement from a DB row.

    Returns None when the row fails defensive validation against the active
    base facts, or when the row is missing the Reading Record anchor
    columns required to project onto the current snapshot. Read-side
    defensive filtering only; write-side validation remains the source of
    truth.
    """
    unit_id = row["unit_id"]
    anchor_segment_id = row["anchor_segment_id"]
    if unit_id is None or anchor_segment_id is None:
        return None

    unit_id_str = str(unit_id)
    anchor_segment_id_str = str(anchor_segment_id)

    unit_text = unit_text_by_id.get(unit_id_str)
    if unit_text is None:
        return None

    anchor_segment = anchor_segment_by_id.get(anchor_segment_id_str)
    if anchor_segment is None:
        return None

    start_offset = row["start_offset"]
    end_offset = row["end_offset"]
    text_hash = row["text_hash"]
    if (
        start_offset is None
        or end_offset is None
        or text_hash is None
    ):
        return None

    start_offset_int = int(start_offset)
    end_offset_int = int(end_offset)
    text_hash_str = str(text_hash)

    selected_text = _extract_selected_text_from_anchor_payload(
        row["anchor_payload_json"],
    )
    if selected_text is None:
        return None

    try:
        validate_text_anchor_against_unit(
            offset_unit=TEXT_RANGE_OFFSET_UNIT,
            start_offset=start_offset_int,
            end_offset=end_offset_int,
            selected_text=selected_text,
            text_hash=text_hash_str,
            unit_text=unit_text,
            anchor_segment=anchor_segment,
        )
    except AnchorValidationError:
        return None

    segment_type = row["segment_type"] if row["segment_type"] is not None else "sentence"
    sentence_id_value = row["segment_sentence_id"]
    anchor = ReaderTextRangeAnchor(
        base_id=str(base_id),
        unit_id=unit_id_str,
        anchor_segment_id=anchor_segment_id_str,
        sentence_id=str(sentence_id_value) if sentence_id_value is not None else None,
        segment_type=segment_type,
        start_offset=start_offset_int,
        end_offset=end_offset_int,
        selected_text=selected_text,
        text_hash=text_hash_str,
    )

    content_payload = {
        "supplement_type": str(row["supplement_type"] or "grammar_note"),
        "title": str(row["title"] or "AI 补充语法旁注"),
        "content_md": str(row["content_md"] or ""),
        "target_key": str(row["target_key"]) if row["target_key"] is not None else None,
        "sentence_id": str(row["sentence_id"]) if row["sentence_id"] is not None else None,
        "paragraph_id": (
            str(row["paragraph_id"]) if row["paragraph_id"] is not None else None
        ),
        "schema_version": str(row["schema_version"] or "reader-ask-supplement-v1"),
        "created_from_turn_run_id": (
            str(row["created_from_turn_run_id"])
            if row["created_from_turn_run_id"] is not None
            else ""
        ),
        "lifecycle_status": "persisted",
        "record_id": str(record_id),
        "base_id": str(base_id),
        "generation": generation,
    }

    return ReaderSnapshotAskSupplement(
        supplement_id=str(row["id"]),
        anchor=anchor,
        content=content_payload,
        created_at=row["created_at"],
    )


def _extract_selected_text_from_anchor_payload(
    anchor_payload_json: Any,
) -> str | None:
    """Extract selected_text from the persisted anchor_payload_json.

    The anchor payload is the model_dump of the original
    UserEditorialAssetAnchor / ReaderAskAnchorRef. Reading Record
    supplements store the full anchor including selected_text.
    """
    if anchor_payload_json is None:
        return None
    if isinstance(anchor_payload_json, str):
        try:
            import json

            anchor_payload_json = json.loads(anchor_payload_json)
        except (TypeError, ValueError):
            return None
    if not isinstance(anchor_payload_json, dict):
        return None
    selected_text = anchor_payload_json.get("selected_text")
    if not isinstance(selected_text, str) or not selected_text:
        return None
    return selected_text


def _build_enhancement_progress(
    *,
    product_state: ReadingRecordProductState,
    layer_rows: Sequence[asyncpg.Record],
    job_rows: Sequence[asyncpg.Record],
) -> ReaderEnhancementProgress:
    effective_layer_rows = _effective_progress_layer_rows(layer_rows)
    effective_job_rows = _effective_progress_job_rows(job_rows)
    job_by_id = {str(row["id"]): row for row in effective_job_rows}
    progress_layers: list[ReaderEnhancementProgressLayer] = []
    layer_source_job_ids: set[str] = set()

    for row in effective_layer_rows:
        layer_type = str(row["layer_type"])
        capability = _LAYER_CAPABILITY_BY_TYPE.get(layer_type)
        if capability is None:
            continue

        source_job_id = _optional_str(row["source_job_id"])
        if source_job_id is not None:
            layer_source_job_ids.add(source_job_id)
        job_row = job_by_id.get(source_job_id) if source_job_id is not None else None
        failure_code = _optional_str(job_row["failure_code"]) if job_row is not None else None

        progress_layers.append(
            ReaderEnhancementProgressLayer(
                capability=capability,
                layer_type=layer_type,
                status=_layer_progress_status(
                    str(row["status"]),
                    failure_code=failure_code,
                    product_state=product_state,
                ),
                job_status=str(job_row["status"]) if job_row is not None else None,
                job_type=str(job_row["job_type"]) if job_row is not None else None,
                layer_id=str(row["id"]),
                job_id=source_job_id,
                target_type=str(job_row["target_type"]) if job_row is not None else None,
                target_scope=str(row["target_scope"]),
                target_key=str(row["target_key"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"] or row["published_at"],
                failure_code=failure_code,
                failure_message=(
                    sanitize_failure_message(job_row["failure_message"])
                    if job_row is not None
                    else None
                ),
            )
        )

    for row in effective_job_rows:
        job_id = str(row["id"])
        if job_id in layer_source_job_ids:
            continue

        job_type = str(row["job_type"])
        capability = _JOB_CAPABILITY_BY_TYPE.get(job_type)
        if capability is None:
            continue
        failure_code = _optional_str(row["failure_code"])

        progress_layers.append(
            ReaderEnhancementProgressLayer(
                capability=capability,
                layer_type=_JOB_LAYER_TYPE_BY_TYPE[job_type],
                status=_job_progress_status(
                    str(row["status"]),
                    failure_code=failure_code,
                    product_state=product_state,
                ),
                job_status=str(row["status"]),
                job_type=job_type,
                job_id=job_id,
                target_type=str(row["target_type"]),
                target_key=str(row["target_key"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                failure_code=failure_code,
                failure_message=sanitize_failure_message(row["failure_message"]),
            )
        )

    capabilities_with_progress = {layer.capability for layer in progress_layers}
    for capability in _PROGRESS_CAPABILITIES:
        if capability not in capabilities_with_progress:
            progress_layers.append(
                ReaderEnhancementProgressLayer(
                    capability=capability,
                    status="not_started",
                )
            )

    return ReaderEnhancementProgress(
        overall_status=_overall_progress_status(product_state, progress_layers),
        layers=progress_layers,
    )


def _effective_progress_layer_rows(
    layer_rows: Sequence[asyncpg.Record],
) -> tuple[asyncpg.Record, ...]:
    effective_by_key: dict[tuple[str, str, str], asyncpg.Record] = {}
    for row in layer_rows:
        key = (
            str(row["layer_type"]),
            str(row["target_scope"]),
            str(row["target_key"]),
        )
        current = effective_by_key.get(key)
        if current is None or _prefer_progress_layer_row(row, current):
            effective_by_key[key] = row
    return tuple(effective_by_key.values())


def _prefer_progress_layer_row(
    candidate: asyncpg.Record,
    current: asyncpg.Record,
) -> bool:
    candidate_status = str(candidate["status"])
    current_status = str(current["status"])
    if candidate_status == "published" and current_status != "published":
        return True
    if current_status == "published" and candidate_status != "published":
        return False
    return _progress_row_timestamp(candidate) >= _progress_row_timestamp(current)


def _effective_progress_job_rows(
    job_rows: Sequence[asyncpg.Record],
) -> tuple[asyncpg.Record, ...]:
    effective_by_key: dict[tuple[str, str, str, str], asyncpg.Record] = {}
    for row in job_rows:
        effective_by_key[_progress_job_work_key(row)] = row
    return tuple(effective_by_key.values())


def _progress_job_work_key(row: asyncpg.Record) -> tuple[str, str, str, str]:
    return (
        str(row["job_type"]),
        str(row["target_type"]),
        str(row["target_key"]),
        str(row["operation_fingerprint"]),
    )


def _progress_row_timestamp(row: asyncpg.Record) -> tuple[datetime, str]:
    return (row["updated_at"] or row["created_at"], str(row["id"]))


def _job_progress_status(
    job_status: str,
    *,
    failure_code: str | None,
    product_state: ReadingRecordProductState,
) -> str:
    if job_status in {"queued", "retry_later", "paused"}:
        return "queued"
    if job_status == "claimed":
        return "processing"
    if job_status in {"skipped", "succeeded"}:
        return "succeeded"
    return _failed_progress_status(failure_code, product_state)


def _layer_progress_status(
    layer_status: str,
    *,
    failure_code: str | None,
    product_state: ReadingRecordProductState,
) -> str:
    if layer_status == "published":
        return "succeeded"
    if layer_status == "draft":
        return "processing"
    return _failed_progress_status(failure_code, product_state)


def _failed_progress_status(
    failure_code: str | None,
    product_state: ReadingRecordProductState,
) -> str:
    if (
        product_state in {"action_required", "needs_confirmation"}
        or failure_code in _USER_ACTION_REQUIRED_FAILURE_CODES
    ):
        return "action_required"
    return "failed"


def _overall_progress_status(
    product_state: ReadingRecordProductState,
    progress_layers: Sequence[ReaderEnhancementProgressLayer],
) -> str:
    layer_statuses = {layer.status for layer in progress_layers}
    if product_state in {"action_required", "needs_confirmation"}:
        return "action_required"
    if product_state in {"deleted", "failed"}:
        return "failed"
    if "action_required" in layer_statuses:
        return "action_required"
    if "failed" in layer_statuses:
        return "failed"
    if product_state == "processing":
        return "processing"
    if layer_statuses.intersection({"not_started", "queued", "processing"}):
        return "readable_enhancing"
    return "ready"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _navigation_json_from_build_result(
    build_result: ReadingBaseBuildResult,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "units": [
            {
                "unit_id": unit.unit_id,
                "order_index": unit.order_index,
                "unit_type": unit.unit_type,
                "boundary_quality": unit.boundary_quality,
                "label": unit.label,
                "base_start_utf16": unit.base_start_utf16,
                "base_end_utf16": unit.base_end_utf16,
            }
            for unit in build_result.navigation_units
        ]
    }


def _navigation_map_by_unit_id(navigation_json: Any) -> dict[str, dict[str, Any]]:
    navigation = ensure_json_object(navigation_json)
    units = ensure_json_array(navigation.get("units"))
    result: dict[str, dict[str, Any]] = {}
    for item in units:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        if isinstance(unit_id, str):
            result[unit_id] = dict(item)
    return result
