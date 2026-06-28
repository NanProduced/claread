"""Reader artifact pipeline worker entry point (D6-I3R).

Drives :class:`ArtifactInputPipelineWorkerService` in a standalone loop so
the artifact-backed text/markdown extraction + materialization pipeline can
run independently of the enhancement worker.

Storage reader wiring (fail-closed by default):

- If OSS credentials (``ALIYUN_OSS_ACCESS_KEY_ID`` +
  ``ALIYUN_OSS_ACCESS_KEY_SECRET``) are present AND the optional ``oss2``
  SDK is importable → :class:`AliyunOssObjectReader` is constructed and
  injected into :class:`TextArtifactExtractionProvider`.
- Otherwise → no reader is injected; the extraction worker uses
  :class:`UnconfiguredArtifactExtractionProvider` which fails closed on the
  first extraction job (no network, no OCR, no PDF).

No OSS secrets are written to code, logs, or output. The worker never
crashes on missing SDK/credentials — it starts cleanly and lets extraction
jobs fail closed with a clear error.
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
from app.services.reader_orchestration.artifact_extraction_provider_router import (
    build_default_extraction_provider_router,
)
from app.services.reader_orchestration.artifact_pipeline_worker_service import (
    ArtifactInputPipelineWorkerService,
    ArtifactPipelineProcessResult,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    AliyunOssObjectReader,
    StorageObjectReader,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage reader factory
# ---------------------------------------------------------------------------


def build_storage_reader(settings: Settings) -> StorageObjectReader | None:
    """Build an OSS storage reader from settings, or ``None`` (fail-closed).

    Returns :class:`AliyunOssObjectReader` only when BOTH:
    - OSS credentials (access key id + secret) are non-empty.
    - The optional ``oss2`` SDK is importable.

    Otherwise returns ``None`` so the pipeline service uses
    :class:`UnconfiguredArtifactExtractionProvider` (fail-closed).

    This function never raises — missing SDK/credentials is a startup-time
    degradation, not a crash.
    """
    ak_id = settings.aliyun_oss_access_key_id
    ak_secret = settings.aliyun_oss_access_key_secret
    if not ak_id or not ak_secret:
        logger.info(
            "artifact pipeline worker: OSS credentials not configured; "
            "extraction will fail closed (UnconfiguredArtifactExtractionProvider)"
        )
        return None

    try:
        import oss2  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        logger.warning(
            "artifact pipeline worker: oss2 SDK not installed; "
            "extraction will fail closed (UnconfiguredArtifactExtractionProvider). "
            "Install with: pip install -e '.[oss]'"
        )
        return None

    return AliyunOssObjectReader(
        access_key_id=ak_id,
        access_key_secret=ak_secret,
        bucket=settings.aliyun_oss_bucket,
        endpoint=settings.aliyun_oss_endpoint,
    )


def build_pipeline_service(
    *,
    settings: Settings,
    pool: Any,
    storage_reader: StorageObjectReader | None,
) -> ArtifactInputPipelineWorkerService:
    """Construct the pipeline service with a router (or fail-closed).

    When ``storage_reader`` is available, a
    :class:`ArtifactExtractionProviderRouter` is built (text + PDF providers)
    and injected as ``extraction_provider``. When ``storage_reader`` is
    ``None``, the pipeline uses ``UnconfiguredArtifactExtractionProvider``
    (fail-closed).
    """
    if storage_reader is None:
        return ArtifactInputPipelineWorkerService(pool=pool)
    router = build_default_extraction_provider_router(reader=storage_reader)
    return ArtifactInputPipelineWorkerService(
        pool=pool,
        extraction_provider=router,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the reader artifact pipeline worker "
            "(extraction + materialization for text/markdown uploads)."
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
        default=settings.reader_artifact_worker_poll_interval_seconds,
        help="Sleep interval between drain cycles when no job is available",
    )
    parser.add_argument(
        "--lease-duration-seconds",
        type=int,
        default=settings.reader_artifact_worker_lease_duration_seconds,
        help="Lease duration for claimed artifact jobs",
    )
    parser.add_argument(
        "--lease-owner-prefix",
        default=settings.reader_artifact_worker_lease_owner_prefix,
        help="Prefix used to build job lease_owner values",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=settings.reader_artifact_worker_max_ticks,
        help="Maximum process_once calls per drain cycle (safety valve)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def _build_result_payload(result: ArtifactPipelineProcessResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": result.stage,
        "status": result.status,
    }
    if result.extraction_result is not None:
        payload["extraction"] = {
            "job_id": str(result.extraction_result.job_id),
            "status": result.extraction_result.status,
            "outcome": result.extraction_result.outcome,
        }
    if result.materialization_result is not None:
        payload["materialization"] = {
            "job_id": str(result.materialization_result.job_id),
            "status": result.materialization_result.status,
            "outcome": result.materialization_result.outcome,
        }
    return payload


async def _run_drain_cycle(
    *,
    service: ArtifactInputPipelineWorkerService,
    lease_owner: str,
    lease_duration: timedelta,
    max_ticks: int,
) -> list[ArtifactPipelineProcessResult]:
    """Run one drain cycle: process jobs until idle or max_ticks reached."""
    return await service.drain(
        lease_owner=lease_owner,
        lease_duration=lease_duration,
        max_ticks=max_ticks,
    )


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

        storage_reader = build_storage_reader(settings)
        service = build_pipeline_service(
            settings=settings,
            pool=pool,
            storage_reader=storage_reader,
        )

        lease_owner = args.lease_owner_prefix
        lease_duration = timedelta(seconds=args.lease_duration_seconds)

        if args.once:
            results = await _run_drain_cycle(
                service=service,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                max_ticks=args.max_ticks,
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
            "artifact pipeline worker started",
            extra={
                "lease_owner": lease_owner,
                "lease_duration_seconds": args.lease_duration_seconds,
                "poll_interval_seconds": args.poll_interval_seconds,
                "max_ticks": args.max_ticks,
                "storage_reader": type(storage_reader).__name__
                if storage_reader is not None
                else "None(fail-closed)",
            },
        )

        while not shutdown_event.is_set():
            results = await _run_drain_cycle(
                service=service,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
                max_ticks=args.max_ticks,
            )
            if results:
                logger.info(
                    "artifact pipeline cycle completed",
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
                except asyncio.TimeoutError:
                    pass  # normal: poll interval elapsed, loop again

        logger.info("artifact pipeline worker stopped gracefully")
    finally:
        await close_db()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    args = _parse_args(settings)
    asyncio.run(_run_worker(args, settings))


if __name__ == "__main__":
    main()
