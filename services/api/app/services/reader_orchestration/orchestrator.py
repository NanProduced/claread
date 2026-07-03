"""Reader orchestration integration for D5-G1.

Provides the minimal orchestration layer that connects the D4-P0
``article_ready`` backend path to the D4-P1 translation worker and
keeps translation parsed decisions aligned with the translation publish
transaction.

Responsibilities:

- ``submit_plain_text_and_bootstrap_translation``: runs the article_ready
  submit and then enqueues the first translation job for the active base.
- ``tick_translation_worker``: claims and processes at most one queued
  translation job. On success the publisher transaction has already
  written the ``parsed_decisions`` row and emitted
  ``parsed_decision_updated``.

The orchestrator never calls a real LLM and never starts a background
process. Ticks are explicit and intended for tests or API-driven flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceResult,
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.job_bootstrap import (
    DisplayTitleJobBootstrapService,
    TranslationJobBootstrapService,
)
from app.services.reader_orchestration.translation_parsed_decision import (
    TRANSLATION_PARSED_POLICY_CODE as TRANSLATION_PARSED_POLICY_CODE,
)
from app.services.reader_orchestration.translation_parsed_decision import (
    TRANSLATION_PARSED_RATIONALE_CODE as TRANSLATION_PARSED_RATIONALE_CODE,
)
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
    TranslationJobProcessResult,
    TranslationWorkerService,
)


@dataclass(frozen=True, slots=True)
class TranslationTickResult:
    """Outcome of a single orchestrator translation tick."""

    worker_result: TranslationJobProcessResult | None
    parsed_decision_written: bool


@dataclass(frozen=True, slots=True)
class OrphanedTranslationDecision:
    """A published translation layer missing its parsed_decision row.

    D5-G1 writes translation parsed decisions in the same transaction as
    the layer publish. Any orphan now indicates legacy pre-D5 data or a
    manually introduced partial state, and should be treated as a
    diagnostic finding instead of a normal crash window.
    """

    layer_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    source_job_id: UUID | None


class ReaderOrchestrator:
    """Coordinates article_ready, translation bootstrap, worker tick and parsed decisions."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        article_ready_service: ArticleReadyPersistenceService | None = None,
        bootstrap_service: TranslationJobBootstrapService | None = None,
        title_bootstrap_service: DisplayTitleJobBootstrapService | None = None,
        worker_service: TranslationWorkerService | None = None,
    ) -> None:
        self._pool = pool
        self._article_ready_service = article_ready_service or ArticleReadyPersistenceService(
            pool=pool
        )
        self._bootstrap_service = bootstrap_service or TranslationJobBootstrapService(pool=pool)
        self._title_bootstrap_service = (
            title_bootstrap_service or DisplayTitleJobBootstrapService(pool=pool)
        )
        self._worker_service = worker_service or TranslationWorkerService(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def submit_plain_text_and_bootstrap_translation(
        self,
        request: PlainTextArticleReadySubmitRequest,
    ) -> ArticleReadyPersistenceResult:
        """Submit plain text and enqueue the first translation job.

        The article_ready result is returned unchanged so callers can
        continue to use the existing snapshot/event contract. The
        bootstrap is idempotent: repeated calls reuse the active job.
        """
        result = await self._article_ready_service.submit_plain_text(request)
        # Generate a single trace_id at the entry point; both bootstrap
        # services persist it into reader_runs.envelope_json so workers can
        # read it back from the claim result and use it as the
        # parent_span_id root for the reader_runtime_spans tree.
        trace_id = uuid4()
        await self._title_bootstrap_service.bootstrap_display_title_job(
            record_id=result.record_id,
            user_id=request.user_id,
            trace_id=trace_id,
        )
        await self._bootstrap_service.bootstrap_translation_run(
            record_id=result.record_id,
            user_id=request.user_id,
            trace_id=trace_id,
        )
        return result

    async def tick_translation_worker(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationTickResult:
        """Process at most one queued translation job.

        On a successful publish, the translation layer publisher has
        already committed the matching parsed_decision row and
        ``parsed_decision_updated`` event in the same transaction.
        """
        worker_result = await self._worker_service.process_next_translation_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            retry_delay=retry_delay,
        )
        if worker_result is None:
            return TranslationTickResult(
                worker_result=None,
                parsed_decision_written=False,
            )

        return TranslationTickResult(
            worker_result=worker_result,
            parsed_decision_written=(
                worker_result.status == "succeeded"
                and worker_result.published_layer is not None
            ),
        )

    async def tick_translation_worker_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_TRANSLATION_RETRY_DELAY,
    ) -> TranslationTickResult:
        worker_result = await self._worker_service.process_next_translation_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            retry_delay=retry_delay,
        )
        if worker_result is None:
            return TranslationTickResult(
                worker_result=None,
                parsed_decision_written=False,
            )

        return TranslationTickResult(
            worker_result=worker_result,
            parsed_decision_written=(
                worker_result.status == "succeeded"
                and worker_result.published_layer is not None
            ),
        )

    async def diagnose_orphaned_translation_decisions(
        self,
        *,
        reading_record_id: UUID | None = None,
    ) -> list[OrphanedTranslationDecision]:
        """Find published translation layers missing a parsed_decision row.

        D5-G1 writes translation parsed decisions in the publisher
        transaction. Any hit should therefore be treated as legacy data
        or a manually introduced partial state.

        When ``reading_record_id`` is provided, the diagnostic is
        scoped to that record; otherwise all records are scanned.
        """
        async with self.get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    layer.id AS layer_id,
                    layer.reading_record_id,
                    layer.base_id,
                    layer.target_key AS unit_id,
                    layer.generation,
                    evt.source_job_id
                FROM enhancement_layers layer
                LEFT JOIN parsed_decisions decision
                  ON decision.reading_record_id = layer.reading_record_id
                 AND decision.base_id = layer.base_id
                 AND decision.unit_id = layer.target_key
                 AND decision.policy_code = $1
                LEFT JOIN reader_events evt
                  ON evt.source_layer_id = layer.id
                 AND evt.event_type = 'layer_published'
                WHERE layer.layer_type = 'translation'
                  AND layer.status = 'published'
                  AND layer.target_scope = 'unit'
                  AND decision.id IS NULL
                  AND ($2::uuid IS NULL OR layer.reading_record_id = $2)
                ORDER BY layer.created_at ASC
                """,
                TRANSLATION_PARSED_POLICY_CODE,
                reading_record_id,
            )

        return [
            OrphanedTranslationDecision(
                layer_id=row["layer_id"],
                reading_record_id=row["reading_record_id"],
                base_id=row["base_id"],
                unit_id=str(row["unit_id"]),
                generation=int(row["generation"]),
                source_job_id=row["source_job_id"],
            )
            for row in rows
        ]
