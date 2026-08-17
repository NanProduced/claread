"""User-explicit analysis-section command. Enqueue/resume only; no LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    ReaderAnalysisSectionRequest,
    ReaderAnalysisSectionRequestResponse,
)
from app.services.reader_orchestration.analysis_progress_projection import (
    ReaderAnalysisProgressProjection,
)
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_PROGRESS_CHANGED_EVENT,
    USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
)
from app.services.reader_orchestration.analysis_section_plan import (
    AnalysisSection,
    AnalysisSectionUnit,
    plan_analysis_sections,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
    _load_locked_active_base_state,
)

_ACTIVE = frozenset({"queued", "processing"})
_COMPLETE = frozenset({"completed"})
_QUOTA = frozenset({"paused_quota"})
MutationKind = Literal["created", "resumed"]


@dataclass
class _Mutation:
    section_id: str
    kind: MutationKind


class AnalysisSectionRequestService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        bootstrap: EnhancementJobBootstrapService | None = None,
        projection: ReaderAnalysisProgressProjection | None = None,
        events: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._bootstrap = bootstrap or EnhancementJobBootstrapService(pool=pool)
        self._projection = projection or ReaderAnalysisProgressProjection(pool=pool)
        self._events = events or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def request_sections(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        body: ReaderAnalysisSectionRequest,
    ) -> ReaderAnalysisSectionRequestResponse:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                return await self._request_in_transaction(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    body=body,
                )

    async def _request_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        body: ReaderAnalysisSectionRequest,
    ) -> ReaderAnalysisSectionRequestResponse:
        state = await _load_locked_active_base_state(
            conn, record_id=record_id, user_id=user_id
        )
        progress = await self._projection.load_progress_on_connection(
            conn, record_id=record_id, user_id=user_id
        )
        if progress.mode != "segmented_on_demand":
            return _response("rejected", reason="analysis_mode_not_segmented")
        planned = await _load_planned_sections(conn, state=state)
        if not planned:
            return _response("rejected", reason="analysis_section_not_found")
        by_id = {section.section_id: section for section in planned}
        if body.scope == "single":
            section = by_id.get(str(body.section_id))
            if section is None:
                return _response("rejected", reason="analysis_section_not_found")
            targets = [section]
        else:
            targets = [
                by_id[row.section_id]
                for row in progress.sections
                if row.section_id in by_id and row.status not in _COMPLETE
            ]
        progress_by_id = {row.section_id: row for row in progress.sections}
        mutations: list[_Mutation] = []
        had_quota = False
        had_active = False
        for section in targets:
            row = progress_by_id.get(section.section_id)
            if row is None:
                continue
            if row.status in _QUOTA:
                had_quota = True
            if row.status in _ACTIVE:
                had_active = True
            need_vocab = row.vocabulary_status not in _COMPLETE
            need_grammar = row.grammar_status not in _COMPLETE
            if not need_vocab and not need_grammar:
                continue
            if row.vocabulary_status in _ACTIVE:
                need_vocab = False
                had_active = True
            if row.grammar_status in _ACTIVE:
                need_grammar = False
                had_active = True
            if not need_vocab and not need_grammar:
                continue
            created = await self._bootstrap.enqueue_analysis_section_jobs(
                conn,
                state=state,
                section=section,
                request_origin=USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
                include_vocabulary=need_vocab,
                include_grammar=need_grammar,
                resume_user_paused=True,
            )
            if created:
                mutations.append(_Mutation(section.section_id, "created"))
        if mutations:
            accepted = _unique_ordered(
                [item.section_id for item in mutations],
                planned,
            )
            event = await self._events.publish_event_in_transaction(
                conn,
                record_id=record_id,
                event_type=ANALYSIS_PROGRESS_CHANGED_EVENT,
                payload_json={
                    "base_id": str(state.base_id),
                    "generation": state.expected_generation,
                    "accepted_section_ids": accepted,
                    "mutation": "enqueue_or_resume",
                    "topic": "analysis_progress",
                },
            )
            return ReaderAnalysisSectionRequestResponse(
                outcome="started",
                accepted_section_ids=accepted,
                event_sequence=event.sequence,
                reason_code=None,
            )
        if had_quota:
            return _response("paused_quota")
        if had_active:
            return _response("already_active")
        if body.scope == "single":
            row = progress_by_id.get(str(body.section_id))
            if row is not None and row.status in _COMPLETE:
                return _response("already_complete")
        elif all(row.status in _COMPLETE for row in progress.sections):
            return _response("already_complete")
        if body.scope == "remaining" and not targets:
            return _response("already_complete")
        return _response("rejected", reason="analysis_section_not_runnable")


async def _load_planned_sections(
    conn: asyncpg.Connection,
    *,
    state,
) -> list[AnalysisSection]:
    rows = await conn.fetch(
        """
        SELECT unit_id, order_index, base_start_utf16, base_end_utf16
        FROM reading_units
        WHERE reading_record_id = $1
          AND base_id = $2
        ORDER BY order_index ASC
        """,
        state.record_id,
        state.base_id,
    )
    if not rows:
        return []
    return plan_analysis_sections(
        str(state.base_id),
        [
            AnalysisSectionUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ],
    )


def _unique_ordered(section_ids: list[str], planned: list[AnalysisSection]) -> list[str]:
    wanted = set(section_ids)
    return [section.section_id for section in planned if section.section_id in wanted]


def _response(
    outcome: str,
    *,
    reason: str | None = None,
) -> ReaderAnalysisSectionRequestResponse:
    return ReaderAnalysisSectionRequestResponse(
        outcome=outcome,  # type: ignore[arg-type]
        accepted_section_ids=[],
        event_sequence=None,
        reason_code=reason,
    )
