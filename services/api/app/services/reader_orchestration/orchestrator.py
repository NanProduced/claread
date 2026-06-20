"""Reader orchestration integration for D4-P2.

Provides the minimal orchestration layer that connects the D4-P0
``article_ready`` backend path to the D4-P1 translation worker and
records parsed decisions once a translation layer is published.

Responsibilities:

- ``submit_plain_text_and_bootstrap_translation``: runs the article_ready
  submit and then enqueues the first translation job for the active base.
- ``tick_translation_worker``: claims and processes at most one queued
  translation job. On success it writes a ``parsed_decisions`` row and
  publishes a ``parsed_decision_updated`` event in the same transaction.

The orchestrator never calls a real LLM and never starts a background
process. Ticks are explicit and intended for tests or API-driven flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceResult,
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_bootstrap import (
    TranslationJobBootstrapService,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.translation_worker import (
    DEFAULT_TRANSLATION_RETRY_DELAY,
    TranslationJobProcessResult,
    TranslationWorkerService,
)

TRANSLATION_PARSED_POLICY_CODE = "translation_layer_v1"
TRANSLATION_PARSED_RATIONALE_CODE = "translation_layer_published"
TRANSLATION_PARSED_POLICY_VERSION = "d4-p2-translation-parsed"


@dataclass(frozen=True, slots=True)
class TranslationTickResult:
    """Outcome of a single orchestrator translation tick."""

    worker_result: TranslationJobProcessResult | None
    parsed_decision_written: bool


@dataclass(frozen=True, slots=True)
class OrphanedTranslationDecision:
    """A published translation layer missing its parsed_decision row.

    D4 risk: ``parsed_decisions`` are written in a separate transaction
    from the layer publish. In single-thread tick mode this should never
    happen, but a crash between the two transactions would leave an
    orphaned layer. This diagnostic record supports detection and
    future repair.
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
        worker_service: TranslationWorkerService | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        repository: ReaderOrchestrationRepository | None = None,
    ) -> None:
        self._pool = pool
        self._article_ready_service = article_ready_service or ArticleReadyPersistenceService(
            pool=pool
        )
        self._bootstrap_service = bootstrap_service or TranslationJobBootstrapService(pool=pool)
        self._worker_service = worker_service or TranslationWorkerService(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)

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
        await self._bootstrap_service.bootstrap_translation_run(
            record_id=result.record_id,
            user_id=request.user_id,
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

        On a successful publish, writes a parsed_decision row and emits
        ``parsed_decision_updated`` in the same transaction. Returns a
        ``TranslationTickResult`` describing whether a job was processed
        and whether a parsed decision was written.
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

        if (
            worker_result.status == "succeeded"
            and worker_result.published_layer is not None
            and worker_result.context is not None
        ):
            await self._write_parsed_decision_for_translation(worker_result)
            return TranslationTickResult(
                worker_result=worker_result,
                parsed_decision_written=True,
            )

        return TranslationTickResult(
            worker_result=worker_result,
            parsed_decision_written=False,
        )

    async def _write_parsed_decision_for_translation(
        self,
        result: TranslationJobProcessResult,
    ) -> None:
        published = result.published_layer
        context = result.context
        if published is None or context is None:
            return

        coverage_json: dict[str, Any] = {
            "translation_layer_id": str(published.layer_id),
            "target_language": context.target_language,
            "source_language": context.source_language,
        }
        decision_json: dict[str, Any] = {
            "policy_version": TRANSLATION_PARSED_POLICY_VERSION,
            "trigger": "translation_layer_published",
            "generation": published.generation,
            "unit_id": published.unit_id,
        }

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                await self._repository.upsert_parsed_decision(
                    conn,
                    reading_record_id=published.reading_record_id,
                    base_id=published.base_id,
                    unit_id=published.unit_id,
                    policy_code=TRANSLATION_PARSED_POLICY_CODE,
                    parsed_state="parsed",
                    rationale_code=TRANSLATION_PARSED_RATIONALE_CODE,
                    coverage_json=coverage_json,
                    source_layer_id=published.layer_id,
                    source_job_id=context.job_id,
                    decision_json=decision_json,
                )
                await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=published.reading_record_id,
                    event_type="parsed_decision_updated",
                    payload_json={
                        "record_id": str(published.reading_record_id),
                        "base_id": str(published.base_id),
                        "unit_id": published.unit_id,
                        "policy_code": TRANSLATION_PARSED_POLICY_CODE,
                        "parsed_state": "parsed",
                        "rationale_code": TRANSLATION_PARSED_RATIONALE_CODE,
                        "source_layer_id": str(published.layer_id),
                        "source_job_id": str(context.job_id),
                    },
                    source_run_id=context.run_id,
                    source_job_id=context.job_id,
                    source_layer_id=published.layer_id,
                )

    async def diagnose_orphaned_translation_decisions(
        self,
        *,
        reading_record_id: UUID | None = None,
    ) -> list[OrphanedTranslationDecision]:
        """Find published translation layers missing a parsed_decision row.

        D4 risk: ``parsed_decisions`` are written in a separate
        transaction from the layer publish. A crash between the two
        transactions would leave an orphaned layer. In single-thread
        tick mode this should return an empty list.

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
