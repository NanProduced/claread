"""GrammarWindowWorkerService: Z+ grammar window worker.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  - §8.2 Window claim / preflight (pending → running state transition)
  - §8.3 LLM call (skeleton; full prompt + PydanticAI wiring deferred to C5)
  - §8.6 Heartbeat (asyncio task renewing the lease every ~30s)

This worker consumes ``build_grammar_bundle_window`` reader_jobs. The
preflight step is the critical fix: it must transition ``analysis_windows``
from ``pending`` to ``running`` BEFORE the LLM call, otherwise the publish
phase (window_locked.status == 'running' fence) rejects the output.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from app.contracts.annotation import slice_by_utf16_offsets
from app.database import connection as db_connection
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    IllegalTransitionError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.window_selector import CandidateItem


class PreflightResult(Enum):
    """Outcome of ``preflight_window_job`` (§8.2)."""

    PROCEED = "proceed"
    ALREADY_TERMINAL = "already_terminal"


# analysis_windows.status values that count as terminal per §8.2.
_TERMINAL_WINDOW_STATUSES: frozenset[str] = frozenset({
    "completed", "no_op", "failed",
})


class GrammarWindowExecutionError(Exception):
    """Raised when the grammar window executor is not configured or fails."""


class GrammarWindowExecutorProtocol(Protocol):
    """Protocol for Z+ grammar window LLM executors."""

    async def generate(self, context: dict[str, Any]) -> list[CandidateItem]:
        """Generate candidates from a window LLM context."""
        ...


class UnconfiguredGrammarWindowExecutor:
    """Default executor that raises when no real executor is configured."""

    async def generate(self, context: dict[str, Any]) -> list[CandidateItem]:
        del context
        raise GrammarWindowExecutionError(
            "GrammarWindowWorkerService has no executor configured. "
            "Pass an executor= parameter to the constructor."
        )


class GrammarWindowWorkerService:
    """Z+ grammar window worker.

    Responsibilities:
      1. ``preflight_window_job`` — §8.2 pending → running transition.
      2. ``_heartbeat_loop`` — §8.6 lease renewal during the LLM call.
      3. ``process_window_job`` — orchestrates preflight → context load →
         LLM (with heartbeat) → return candidates. The pipeline runner
         wires the publisher after ``candidates_ready`` is returned.

    The LLM call (``_call_llm``) delegates to an injected executor that
    implements ``GrammarWindowExecutorProtocol``. The default
    ``UnconfiguredGrammarWindowExecutor`` raises so a real executor must be
    passed for end-to-end processing. Context loading
    (``_load_window_context``) JOINs anchor_segments + reading_units +
    reading_bases to slice ``source_text`` for each target anchor.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        lease_duration: timedelta = timedelta(seconds=120),
        heartbeat_interval: timedelta = timedelta(seconds=30),
        executor: GrammarWindowExecutorProtocol | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._executor: GrammarWindowExecutorProtocol = (
            executor or UnconfiguredGrammarWindowExecutor()
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    # ------------------------------------------------------------------
    # §8.2 preflight: pending → running
    # ------------------------------------------------------------------

    async def preflight_window_job(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> PreflightResult:
        """Transition ``analysis_windows.status`` from ``pending`` to ``running``.

        Must run after ``claim_next_job`` and before the LLM call. The publish
        phase requires ``window.status == 'running'`` (window_locked fence),
        so skipping this step causes publish to fail.

        Branches (§8.2):
          - ``pending`` → UPDATE to ``running``, write ``started_at`` + ``job_id``.
          - ``running`` + same ``job_id`` → retry of the same job; allow.
          - ``running`` + different ``job_id`` → raise ``IllegalTransitionError``.
          - ``completed`` / ``no_op`` / ``failed`` → ``ALREADY_TERMINAL``.
          - any other status → raise ``IllegalTransitionError`` (defensive).

        ``lease_token`` is accepted for symmetry with the runtime contract but
        is not re-validated here; lease validity is enforced by ``heartbeat``
        and ``transition``.
        """
        del lease_token, lease_duration  # enforced upstream; not re-checked here

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # 1. Lock the reader_jobs row for fence context.
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")

                # 2. Resolve window_id from input_json.
                input_json: Any = job_row["input_json"]
                if isinstance(input_json, str):
                    input_json = json.loads(input_json)
                window_id = UUID(str(input_json["window_id"]))

                # 3. Lock the analysis_windows row.
                window_row = await conn.fetchrow(
                    "SELECT * FROM analysis_windows WHERE id = $1 FOR UPDATE",
                    window_id,
                )
                if window_row is None:
                    raise LookupError(f"analysis window {window_id} not found")

                status: str = window_row["status"]

                # 4. Dispatch on §8.2 status branches.
                if status == "pending":
                    # analysis_windows has no updated_at column (only
                    # created_at / started_at / completed_at), so we only
                    # touch status / started_at / job_id here.
                    await conn.execute(
                        """
                        UPDATE analysis_windows
                        SET status = 'running',
                            started_at = NOW(),
                            job_id = $2
                        WHERE id = $1
                        """,
                        window_id, job_id,
                    )
                    return PreflightResult.PROCEED

                if status == "running":
                    stored_job_id = window_row["job_id"]
                    if stored_job_id != job_id:
                        raise IllegalTransitionError(
                            f"window {window_id} is running by job "
                            f"{stored_job_id}, current job is {job_id}"
                        )
                    return PreflightResult.PROCEED

                if status in _TERMINAL_WINDOW_STATUSES:
                    return PreflightResult.ALREADY_TERMINAL

                raise IllegalTransitionError(
                    f"unexpected analysis_window status {status!r} for "
                    f"window {window_id}"
                )

    # ------------------------------------------------------------------
    # process_window_job: preflight → LLM (with heartbeat) → publish
    # ------------------------------------------------------------------

    async def process_window_job(
        self,
        *,
        claim: ClaimResult,
    ) -> dict[str, Any]:
        """Run the full window-job lifecycle.

        Steps:
          1. ``preflight_window_job`` — §8.2 state transition. Short-circuits
             on ``ALREADY_TERMINAL``.
          2. ``_load_window_context`` — load target anchors + source text.
          3. ``_call_llm`` — delegates to the injected executor. Heartbeat
             task renews the lease every ~30s while the LLM call is in flight.
          4. Return ``candidates_ready`` with the candidate list. The
             pipeline runner (``_run_grammar_window_attempt``) hands off to
             ``GrammarWindowPublisher.publish_window_grammar_bundle``.
        """
        # 1. preflight
        preflight = await self.preflight_window_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._lease_duration,
        )
        if preflight == PreflightResult.ALREADY_TERMINAL:
            return {"status": "already_terminal"}

        # 2. load window context (target anchors + source text)
        context = await self._load_window_context(claim.job_id)

        # 3. LLM call with heartbeat (§8.6)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
            )
        )
        try:
            candidates = await self._call_llm(context)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # 4. Return candidates_ready. The pipeline runner wires the publisher
        # after this return (see ``_run_grammar_window_attempt``).
        return {
            "status": "candidates_ready",
            "candidates": candidates,
        }

    # ------------------------------------------------------------------
    # §8.6 heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
    ) -> None:
        """Renew the lease every ``heartbeat_interval`` while the LLM runs.

        Cancels cleanly from ``process_window_job`` once the LLM call returns.
        """
        while True:
            await asyncio.sleep(self._heartbeat_interval.total_seconds())
            await self._job_runtime.heartbeat(
                job_id=job_id,
                lease_token=lease_token,
                lease_duration=self._lease_duration,
            )

    # ------------------------------------------------------------------
    # §8.3 context loading
    # ------------------------------------------------------------------

    async def _load_window_context(self, job_id: UUID) -> dict[str, Any]:
        """Load window target anchors + context anchors + source text.

        JOINs ``anchor_segments`` + ``reading_units`` + ``reading_bases`` to
        slice ``source_text`` for each target anchor using UTF-16 code unit
        offsets (mirrors ``grammar_worker._load_job_context``). Context
        anchors (prev/next) are loaded with the same metadata structure.
        """
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                """
                SELECT job.input_json,
                       job.base_id,
                       job.reading_record_id,
                       base.text AS base_text
                FROM reader_jobs job
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                WHERE job.id = $1
                """,
                job_id,
            )
            if job_row is None:
                raise LookupError(f"reader job {job_id} not found")

            input_data: Any = job_row["input_json"]
            if isinstance(input_data, str):
                input_data = json.loads(input_data)

            base_id = job_row["base_id"]
            base_text = str(job_row["base_text"])

            target_anchor_ids: list[str] = list(
                input_data.get("target_anchor_ids", [])
            )
            context_anchor_prev_ids: list[str] = list(
                input_data.get("context_anchor_prev", [])
            )
            context_anchor_next_ids: list[str] = list(
                input_data.get("context_anchor_next", [])
            )

            target_anchors = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=target_anchor_ids,
                base_text=base_text,
            )
            context_anchor_prev = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=context_anchor_prev_ids,
                base_text=base_text,
            )
            context_anchor_next = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=context_anchor_next_ids,
                base_text=base_text,
            )

        return {
            "job_id": job_id,
            "window_id": UUID(str(input_data["window_id"])),
            "base_id": base_id,
            "reading_record_id": job_row["reading_record_id"],
            "plan_id": str(input_data["plan_id"]),
            "window_index": int(input_data["window_index"]),
            "target_anchors": target_anchors,
            "context_anchor_prev": context_anchor_prev,
            "context_anchor_next": context_anchor_next,
            "window_budget": input_data.get("window_budget", {}),
            "target_unit_ids": list(input_data.get("target_unit_ids", [])),
            "target_anchor_ids": target_anchor_ids,
        }

    async def _load_anchor_rows(
        self,
        conn: asyncpg.Connection,
        *,
        base_id: UUID,
        anchor_ids: list[str],
        base_text: str,
    ) -> list[dict[str, Any]]:
        """Load anchor segment + unit metadata and slice source_text.

        JOINs ``anchor_segments`` + ``reading_units`` to get both the anchor
        range (``base_start_utf16`` / ``base_end_utf16``) and the unit range
        (``unit_base_start_utf16`` / ``unit_base_end_utf16``). ``source_text``
        is sliced from ``reading_bases.text`` using UTF-16 code unit offsets.
        """
        if not anchor_ids:
            return []
        rows = await conn.fetch(
            """
            SELECT seg.anchor_segment_id,
                   seg.unit_id,
                   seg.unit_order_index,
                   seg.base_start_utf16,
                   seg.base_end_utf16,
                   unit.base_start_utf16 AS unit_base_start_utf16,
                   unit.base_end_utf16 AS unit_base_end_utf16
            FROM anchor_segments seg
            JOIN reading_units unit
              ON unit.base_id = seg.base_id
             AND unit.unit_id = seg.unit_id
            WHERE seg.base_id = $1
              AND seg.anchor_segment_id = ANY($2::text[])
            ORDER BY seg.unit_order_index ASC
            """,
            base_id,
            anchor_ids,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            source_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            result.append({
                "anchor_segment_id": str(row["anchor_segment_id"]),
                "unit_id": str(row["unit_id"]),
                "unit_order_index": int(row["unit_order_index"]),
                "base_start_utf16": int(row["base_start_utf16"]),
                "base_end_utf16": int(row["base_end_utf16"]),
                "unit_base_start_utf16": int(row["unit_base_start_utf16"]),
                "unit_base_end_utf16": int(row["unit_base_end_utf16"]),
                "source_text": source_text or "",
            })
        return result

    # ------------------------------------------------------------------
    # §8.3 LLM call (delegates to injected executor)
    # ------------------------------------------------------------------

    async def _call_llm(self, context: dict[str, Any]) -> list[CandidateItem]:
        """Call the grammar window executor and return candidates.

        Delegates to ``self._executor.generate(context)``. The executor is
        responsible for building the prompt, invoking the LLM, and parsing
        the structured output into ``CandidateItem`` objects.

        Raises ``GrammarWindowExecutionError`` when no executor is
        configured (the default ``UnconfiguredGrammarWindowExecutor``).
        """
        return await self._executor.generate(context)
