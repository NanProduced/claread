"""Reader Article RAG index worker entry point.

Drives :class:`ArticleRagIndexWorkerService` in a standalone loop so
``article_rag_index_build`` jobs (enqueued by bootstrap) can be
processed independently of the enhancement / artifact pipeline workers.

Provider wiring (fail-closed by default):

- If ``reader_article_rag_embedding_provider == "dashscope"`` and the
  Bailian credential path resolves a non-empty API key →
  :class:`DashScopeArticleRagEmbeddingProvider` is constructed (lazy IO —
  no DashScope call happens until ``embed_texts`` is called on a real job).
- Otherwise → :class:`UnconfiguredArticleRagEmbeddingProvider` (terminal
  fail closed on first job with ``embedding_provider_unconfigured``).
- If ``reader_article_rag_vector_provider == "zilliz"`` and uri/token/
  collection/dim are present → :class:`ZillizArticleRagVectorWriter` is
  constructed (lazy IO — ``MilvusClient`` is created inside the first
  ``upsert_chunks`` call, not at construction time).
- Otherwise → :class:`UnconfiguredArticleRagVectorWriter` (terminal fail
  closed on first job with ``vector_writer_unconfigured``).

The worker never crashes on missing DashScope/Zilliz config — it starts
cleanly and lets jobs fail closed with a clear error. No secrets are
written to code, logs, or output.

Safety / truth boundary:
- Does NOT read Plate JSON, Markdown syntax, DOM selection, Slate path,
  or UI display group fields.
- Does NOT treat vector payload as citation truth — retrieval must
  rebuild the plan from Postgres truth layers.
- Does NOT write chunk text, query text, tokens, URIs, or raw SDK error
  messages into public result/log repr.
- Does NOT publish ``article_ready`` — RAG index is a substrate, not a
  readiness blocker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import timedelta
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config.settings import Settings, get_settings
from app.database.connection import close_db, init_db
from app.services.reader_orchestration.article_rag_embedding_provider import (
    build_default_article_rag_embedding_provider,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagIndexWorkerResult,
    ArticleRagIndexWorkerService,
)
from app.services.reader_orchestration.article_rag_vector_store import (
    build_default_article_rag_vector_writer,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

logger = logging.getLogger(__name__)


# Default batch size for stale-lease recovery — independent of ``max_ticks``
# so a backlog of crashed jobs is not throttled by the per-cycle budget.
DEFAULT_RECOVER_BATCH_SIZE = 200


# ---------------------------------------------------------------------------
# Provider / service factory
# ---------------------------------------------------------------------------


def build_worker_service(
    *,
    settings: Settings,
    pool: Any,
) -> ArticleRagIndexWorkerService:
    """Construct the Article RAG index worker service with default providers.

    Providers are built via the I4D factories
    (:func:`build_default_article_rag_embedding_provider` and
    :func:`build_default_article_rag_vector_writer`).  Both factories
    return fail-closed ``Unconfigured*`` instances when DashScope / Zilliz
    config is missing — they never raise and never make network calls at
    construction time.

    The worker can always start; real IO only happens when a job is
    processed and the provider is actually called.
    """
    embedding_provider = build_default_article_rag_embedding_provider(settings)
    vector_writer = build_default_article_rag_vector_writer(settings)
    return ArticleRagIndexWorkerService(
        pool=pool,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
        default_vector_collection=settings.reader_article_rag_zilliz_collection,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the reader Article RAG index worker "
            "(claims article_rag_index_build jobs, embeds chunks, "
            "upserts vectors, transitions index runs to indexed)."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single drain cycle and exit",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=settings.reader_article_rag_worker_poll_interval_seconds,
        help="Sleep interval between drain cycles when no job is available",
    )
    parser.add_argument(
        "--lease-duration-seconds",
        type=int,
        default=settings.reader_article_rag_worker_lease_duration_seconds,
        help="Lease duration for claimed article_rag_index_build jobs",
    )
    parser.add_argument(
        "--lease-owner-prefix",
        default=settings.reader_article_rag_worker_lease_owner_prefix,
        help="Prefix used to build job lease_owner values",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=settings.reader_article_rag_worker_max_ticks,
        help="Maximum process_next calls per drain cycle (safety valve)",
    )
    parser.add_argument(
        "--recover-batch-size",
        type=int,
        default=settings.reader_article_rag_worker_recover_batch_size
        if hasattr(settings, "reader_article_rag_worker_recover_batch_size")
        else DEFAULT_RECOVER_BATCH_SIZE,
        help="Independent batch size for stale-lease recovery (default 200)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------


def _build_result_payload(result: ArticleRagIndexWorkerResult) -> dict[str, Any]:
    """Serialize a worker result for ``--once`` JSON output.

    Only includes non-sensitive fields: no chunk text, no embedding
    vectors, no tokens, no URIs.  ``failure_code`` is included so the
    output is useful for debugging without leaking SDK internals.
    """
    payload: dict[str, Any] = {
        "job_id": str(result.job_id),
        "index_run_id": str(result.index_run_id),
        "reading_record_id": str(result.reading_record_id),
        "status": result.status,
        "chunk_count": result.chunk_count,
    }
    if result.stable_document_id is not None:
        payload["stable_document_id"] = str(result.stable_document_id)
    if result.base_id is not None:
        payload["base_id"] = str(result.base_id)
    if result.embedding_model is not None:
        payload["embedding_model"] = result.embedding_model
    if result.vector_store_provider is not None:
        payload["vector_store_provider"] = result.vector_store_provider
    if result.vector_collection is not None:
        payload["vector_collection"] = result.vector_collection
    if result.retryable is not None:
        payload["retryable"] = result.retryable
    if result.failure_code is not None:
        payload["failure_code"] = result.failure_code
    if result.idempotent_noop:
        payload["idempotent_noop"] = True
    return payload


# ---------------------------------------------------------------------------
# Drain cycle
# ---------------------------------------------------------------------------


async def _run_drain_cycle(
    *,
    service: ArticleRagIndexWorkerService,
    lease_owner: str,
    lease_duration: timedelta,
    max_ticks: int,
    recover_batch_size: int = DEFAULT_RECOVER_BATCH_SIZE,
) -> list[ArticleRagIndexWorkerResult]:
    """Run one drain cycle: stale-lease recovery then claim/process.

    Recovery uses an independent batch size so a backlog of crashed jobs is
    not throttled by the per-cycle ``max_ticks`` budget.

    If recovery fails, the exception is logged and re-raised — we MUST NOT
    silently swallow the failure, otherwise stale leases would never recover.
    """
    try:
        recovered = await ReaderJobRuntime().recover_stale_leases(
            batch_size=recover_batch_size,
        )
    except Exception:
        logger.exception(
            "article RAG index worker: stale-lease recovery failed; "
            "aborting drain cycle to avoid masking the failure"
        )
        raise
    if recovered:
        logger.info(
            "article RAG index worker: recovered stale leases",
            extra={"recovered": recovered, "recover_batch_size": recover_batch_size},
        )

    # Converge index runs orphaned by job-level recovery / fence
    # supersede (job terminal or requeued while the index run stayed
    # active). Runs AFTER recovery so job states are already converged,
    # and BEFORE process_next so reconciled runs are not mistaken for
    # in-flight work. Fail-open: bootstrap's idempotent fail-closed check
    # remains the safety net; a reconcile bug must not stop healthy
    # indexing.
    try:
        reconciled = await service.reconcile_orphaned_index_runs(
            batch_size=recover_batch_size,
        )
    except Exception:
        logger.exception(
            "article RAG index worker: orphan reconciliation failed; "
            "continuing drain cycle (bootstrap fail-closed remains the "
            "safety net)"
        )
    else:
        if reconciled:
            logger.info(
                "article RAG index worker: reconciled orphaned index runs",
                extra={"reconciled": reconciled},
            )
    results: list[ArticleRagIndexWorkerResult] = []
    for _ in range(max_ticks):
        result = await service.process_next(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if result is None:
            break
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


async def _run_worker(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    if args.poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")
    if not args.once and args.poll_interval_seconds < 1:
        raise ValueError("poll_interval_seconds must be >= 1 in loop mode")
    if args.lease_duration_seconds < 1:
        raise ValueError("lease_duration_seconds must be >= 1")
    if args.max_ticks < 1:
        raise ValueError("max_ticks must be >= 1")
    # ``recover_batch_size`` may be absent on a hand-rolled Namespace in
    # tests; fall back to the script default rather than crashing.
    recover_batch_size = getattr(args, "recover_batch_size", DEFAULT_RECOVER_BATCH_SIZE)
    if recover_batch_size < 1:
        raise ValueError("recover_batch_size must be >= 1")

    await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        from app.database import connection as db_connection

        pool = db_connection.DB_POOL
        if pool is None:  # pragma: no cover - init_db should set DB_POOL
            raise RuntimeError("Database pool not initialized after init_db")

        service = build_worker_service(settings=settings, pool=pool)

        lease_owner = args.lease_owner_prefix
        lease_duration = timedelta(seconds=args.lease_duration_seconds)

        if args.once:
            results = await _run_drain_cycle(
                service=service,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                max_ticks=args.max_ticks,
                recover_batch_size=recover_batch_size,
            )
            print(
                json.dumps(
                    [_build_result_payload(r) for r in results],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        # Loop mode with graceful shutdown
        shutdown_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("shutdown signal received, draining after current cycle")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except (NotImplementedError, RuntimeError):
                # Windows does not support add_signal_handler; fall back to
                # KeyboardInterrupt for graceful shutdown.
                pass

        logger.info(
            "article RAG index worker started",
            extra={
                "lease_owner": lease_owner,
                "lease_duration_seconds": args.lease_duration_seconds,
                "poll_interval_seconds": args.poll_interval_seconds,
                "max_ticks": args.max_ticks,
                "recover_batch_size": recover_batch_size,
            },
        )

        while not shutdown_event.is_set():
            results = await _run_drain_cycle(
                service=service,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                max_ticks=args.max_ticks,
                recover_batch_size=recover_batch_size,
            )
            if results:
                logger.info(
                    "article RAG index cycle completed",
                    extra={
                        "processed_count": len(results),
                        "last_status": results[-1].status,
                    },
                )
            else:
                # No job available — sleep, but wake early on shutdown
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=args.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass  # normal: poll interval elapsed, loop again

        logger.info("article RAG index worker stopped gracefully")
    finally:
        await close_db()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    args = _parse_args(settings)
    asyncio.run(_run_worker(args, settings))


if __name__ == "__main__":
    main()
