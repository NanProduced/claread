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
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.job_runtime import (
    ClaimResult,
    IllegalTransitionError,
    ReaderJobRuntime,
)


class PreflightResult(Enum):
    """Outcome of ``preflight_window_job`` (§8.2)."""

    PROCEED = "proceed"
    ALREADY_TERMINAL = "already_terminal"


# analysis_windows.status values that count as terminal per §8.2.
_TERMINAL_WINDOW_STATUSES: frozenset[str] = frozenset({
    "completed", "no_op", "failed",
})


class GrammarWindowWorkerService:
    """Z+ grammar window worker.

    Responsibilities (current phase):
      1. ``preflight_window_job`` — §8.2 pending → running transition.
      2. ``_heartbeat_loop`` — §8.6 lease renewal during the LLM call.
      3. ``process_window_job`` — orchestrates preflight → context load →
         LLM (with heartbeat) → publish (publisher lands in C2).

    The LLM call (``_call_llm``) and full context loading
    (``_load_window_context``) are skeletons; the C5 regression phase wires
    the PydanticAI agent + prompt template. Tests in this phase exercise
    preflight, heartbeat, and the ALREADY_TERMINAL short-circuit.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        lease_duration: timedelta = timedelta(seconds=120),
        heartbeat_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval

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
          3. ``_call_llm`` — PydanticAI agent (skeleton). Heartbeat task
             renews the lease every ~30s while the LLM call is in flight.
          4. publish — delegates to ``GrammarWindowPublisher`` (C2 phase).
             The current skeleton returns ``candidates_ready`` so the caller
             can hand off to the publisher once it lands.
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

        # 4. publish (GrammarWindowPublisher, C2 phase). Returned here so the
        # caller can wire the publisher; the skeleton returns candidates.
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
    # §8.3 context loading (skeleton — C5 fills in source_text slicing)
    # ------------------------------------------------------------------

    async def _load_window_context(self, job_id: UUID) -> dict[str, Any]:
        """Load window target anchors + context anchors + source text.

        Skeleton: returns the job/input_json fields needed by the prompt
        builder. Full source-text slicing (JOIN anchor_segments +
        reading_units, hash verification) mirrors ``grammar_worker._load_job_context``
        (lines 739-891) and is wired in the C5 regression phase.
        """
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT input_json, base_id, reading_record_id "
                "FROM reader_jobs WHERE id = $1",
                job_id,
            )
            if job_row is None:
                raise LookupError(f"reader job {job_id} not found")

        input_data: Any = job_row["input_json"]
        if isinstance(input_data, str):
            input_data = json.loads(input_data)

        return {
            "job_id": job_id,
            "window_id": UUID(str(input_data["window_id"])),
            "base_id": job_row["base_id"],
            "reading_record_id": job_row["reading_record_id"],
            "target_anchor_ids": input_data["target_anchor_ids"],
            "target_unit_ids": input_data["target_unit_ids"],
            "context_anchor_prev": input_data.get("context_anchor_prev", []),
            "context_anchor_next": input_data.get("context_anchor_next", []),
            "window_budget": input_data.get("window_budget", {}),
        }

    # ------------------------------------------------------------------
    # §8.3 LLM call (skeleton — C5 wires PydanticAI agent + prompt)
    # ------------------------------------------------------------------

    async def _call_llm(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Call the PydanticAI grammar_bundle agent and return candidates.

        Output is a candidate list (not ``GrammarNoteLayerOutput``); the
        selector filters candidates before the publisher persists them.

        TODO (C5 regression):
          1. Build the prompt (context_anchor_prev / target_anchors /
             context_anchor_next) — see ``grammar_worker._build_grammar_prompt``
             (lines 1042-1069) for the per-unit template; the window variant
             wraps multiple units.
          2. Invoke the PydanticAI agent on ``MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE``.
          3. Parse the structured output into ``CandidateItem`` objects.
          4. Extract usage events for the span recorder.

        The skeleton returns an empty list so ``process_window_job`` can be
        exercised end-to-end once a real LLM is mocked in the regression suite.
        """
        del context  # unused in skeleton; C5 consumes it
        return []
