"""Artifact input pipeline worker service (D6-I3P).

Composes :class:`ArtifactExtractionWorkerService` and
:class:`ArtifactMaterializationWorkerService` into a single entry point so
callers can drive the full artifact-backed text/markdown pipeline without
inventing a new queue table or scheduler.

Pipeline order (per ``process_once`` call):

1. Try to claim + process one ``input_artifact_extraction`` job. If a job is
   found, the extraction worker runs the provider, persists
   ``original_inputs.source_text``, transitions the job to ``succeeded``,
   and — in the same transaction — enqueues an
   ``extracted_artifact_materialization`` job. ``process_once`` returns.
2. If no extraction job is available, try to claim + process one
   ``extracted_artifact_materialization`` job. The materialization worker
   calls :meth:`ExtractedArtifactMaterializationService
   .materialize_extracted_artifact_in_transaction` inside the job-lease
   transaction, producing one of:

   - ``stable_document_ready`` → ``article_ready`` + stable doc + base
   - ``candidate_document_required`` → ``candidate_base_ready`` + candidate row
   - ``input_rejected_or_action_required`` → ``action_required``

Each ``process_once`` call processes at most ONE job (extraction OR
materialization). Call ``process_once`` repeatedly to drain the pipeline.

The service does NOT write business tables directly — it delegates entirely
to the existing workers. No new routes, no new queue tables, no new
scheduler.

Provider / reader injection:

- Pass ``storage_reader=`` to automatically wire a
  :class:`TextArtifactExtractionProvider` into the extraction worker. This is
  the production wiring path for text/markdown uploads.
- Pass ``extraction_worker=`` / ``materialization_worker=`` to override the
  entire worker (full control for tests).
- If neither ``storage_reader`` nor ``extraction_worker`` is provided, the
  extraction worker uses ``UnconfiguredArtifactExtractionProvider`` which
  fails closed — no network, no OCR, no PDF parsing.

Deferred (NOT implemented here):

- Real Aliyun OSS SDK network reads (``AliyunOssObjectReader`` stays a stub)
- PDF extraction
- OCR / qwen-ocr
- UI routes
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import asyncpg

from app.database import connection as db_connection

from .artifact_extraction_worker import (
    DEFAULT_EXTRACTION_RETRY_DELAY,
    ArtifactExtractionJobProcessResult,
    ArtifactExtractionWorkerService,
)
from .artifact_materialization_worker import (
    DEFAULT_MATERIALIZATION_RETRY_DELAY,
    ArtifactMaterializationWorkerService,
    MaterializationJobProcessResult,
)
from .job_runtime import ReaderJobRuntime
from .text_artifact_extraction_provider import StorageObjectReader, TextArtifactExtractionProvider

DEFAULT_PIPELINE_LEASE_DURATION = timedelta(seconds=30)
DEFAULT_PIPELINE_MAX_TICKS = 100


@dataclass(frozen=True, slots=True)
class ArtifactPipelineProcessResult:
    """Result of a single ``process_once`` call.

    Exactly one of ``extraction_result`` / ``materialization_result`` is
    non-None (unless the service returned ``None`` because no job was
    available).
    """

    stage: Literal["extraction", "materialization"]
    extraction_result: ArtifactExtractionJobProcessResult | None = None
    materialization_result: MaterializationJobProcessResult | None = None

    @property
    def status(self) -> str:
        """The job status from whichever sub-result is present."""
        if self.extraction_result is not None:
            return self.extraction_result.status
        if self.materialization_result is not None:
            return self.materialization_result.status
        return "idle"

    @property
    def outcome(self) -> str | None:
        """The materialization outcome (only set for the materialization stage)."""
        if self.materialization_result is not None:
            return self.materialization_result.outcome
        return None


class ArtifactInputPipelineWorkerService:
    """Single-entry-point service for the artifact input pipeline.

    Composes the extraction and materialization workers. ``process_once``
    prioritises extraction jobs so that a freshly submitted artifact is
    extracted before any pending materialization jobs are processed.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        storage_reader: StorageObjectReader | None = None,
        extraction_worker: ArtifactExtractionWorkerService | None = None,
        materialization_worker: ArtifactMaterializationWorkerService | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)

        if extraction_worker is not None:
            self._extraction_worker = extraction_worker
        elif storage_reader is not None:
            provider = TextArtifactExtractionProvider(reader=storage_reader)
            self._extraction_worker = ArtifactExtractionWorkerService(
                pool=pool,
                job_runtime=self._job_runtime,
                provider=provider,
            )
        else:
            # No reader / no explicit worker → UnconfiguredArtifactExtractionProvider
            # (fails closed on first extract call).
            self._extraction_worker = ArtifactExtractionWorkerService(
                pool=pool,
                job_runtime=self._job_runtime,
            )

        if materialization_worker is not None:
            self._materialization_worker = materialization_worker
        else:
            self._materialization_worker = ArtifactMaterializationWorkerService(
                pool=pool,
                job_runtime=self._job_runtime,
            )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def process_once(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta = DEFAULT_PIPELINE_LEASE_DURATION,
        extraction_retry_delay: timedelta = DEFAULT_EXTRACTION_RETRY_DELAY,
        materialization_retry_delay: timedelta = DEFAULT_MATERIALIZATION_RETRY_DELAY,
    ) -> ArtifactPipelineProcessResult | None:
        """Process at most one job: extraction first, then materialization.

        Returns ``None`` if no job is available in either stage. Otherwise
        returns an :class:`ArtifactPipelineProcessResult` indicating which
        stage ran and the sub-worker's result.

        If an extraction job is claimed and succeeds, the extraction worker
        enqueues a materialization job in the same transaction. The caller
        should invoke ``process_once`` again to process it.
        """
        # 1. Try extraction (priority)
        extraction_result = await self._extraction_worker.process_next(
            lease_owner=f"{lease_owner}:extraction",
            lease_duration=lease_duration,
            retry_delay=extraction_retry_delay,
        )
        if extraction_result is not None:
            return ArtifactPipelineProcessResult(
                stage="extraction",
                extraction_result=extraction_result,
            )

        # 2. No extraction job — try materialization
        materialization_result = await self._materialization_worker.process_next(
            lease_owner=f"{lease_owner}:materialization",
            lease_duration=lease_duration,
            retry_delay=materialization_retry_delay,
        )
        if materialization_result is not None:
            return ArtifactPipelineProcessResult(
                stage="materialization",
                materialization_result=materialization_result,
            )

        return None

    async def drain(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta = DEFAULT_PIPELINE_LEASE_DURATION,
        max_ticks: int = DEFAULT_PIPELINE_MAX_TICKS,
        extraction_retry_delay: timedelta = DEFAULT_EXTRACTION_RETRY_DELAY,
        materialization_retry_delay: timedelta = DEFAULT_MATERIALIZATION_RETRY_DELAY,
    ) -> list[ArtifactPipelineProcessResult]:
        """Repeatedly call ``process_once`` until no more jobs are available.

        Stops early if ``max_ticks`` is reached (safety valve against
        infinite loops from retryable errors that re-enqueue jobs).
        """
        results: list[ArtifactPipelineProcessResult] = []
        for _ in range(max_ticks):
            result = await self.process_once(
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                extraction_retry_delay=extraction_retry_delay,
                materialization_retry_delay=materialization_retry_delay,
            )
            if result is None:
                break
            results.append(result)
        return results
