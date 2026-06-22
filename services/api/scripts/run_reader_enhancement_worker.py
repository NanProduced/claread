from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config.settings import Settings, get_settings
from app.database.connection import close_db, init_db
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopCycleSummary,
    ReaderEnhancementWorkerLoopService,
)

logger = logging.getLogger(__name__)


def _parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local/deployment Reader enhancement worker loop."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan/process cycle and exit",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=settings.reader_worker_batch_size,
        help="Maximum number of eligible records to scan per cycle",
    )
    parser.add_argument(
        "--scan-interval-seconds",
        type=int,
        default=settings.reader_worker_scan_interval_seconds,
        help="Sleep interval between loop cycles when not using --once",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=settings.reader_worker_max_ticks,
        help="Maximum worker attempts per scoped pipeline run",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=settings.reader_worker_max_jobs,
        help="Maximum claimed jobs per scoped pipeline run",
    )
    parser.add_argument(
        "--lease-owner-prefix",
        default=settings.reader_worker_lease_owner_prefix,
        help="Prefix used to build job lease_owner values",
    )
    return parser.parse_args()


def _build_cycle_payload(
    summary: ReaderEnhancementWorkerLoopCycleSummary,
) -> dict[str, Any]:
    return {
        "recovered_stale_leases": summary.recovered_stale_leases,
        "scanned_candidate_count": summary.scanned_candidate_count,
        "processed_count": summary.processed_count,
        "lock_skipped_count": summary.lock_skipped_count,
        "candidates": [
            {
                "record_id": str(candidate.record_id),
                "user_id": str(candidate.user_id),
                "base_id": str(candidate.base_id),
                "expected_generation": candidate.expected_generation,
                "runnable_job_count": candidate.runnable_job_count,
                "tracked_job_count": candidate.tracked_job_count,
            }
            for candidate in summary.candidates
        ],
        "results": [
            {
                "record_id": str(result.candidate.record_id),
                "user_id": str(result.candidate.user_id),
                "base_id": str(result.candidate.base_id),
                "expected_generation": result.candidate.expected_generation,
                "outcome": result.outcome,
                "pipeline_summary": (
                    {
                        "record_id": str(result.pipeline_summary.record_id),
                        "base_id": str(result.pipeline_summary.base_id),
                        "expected_generation": result.pipeline_summary.expected_generation,
                        "bootstrapped_job_counts": asdict(
                            result.pipeline_summary.bootstrapped_job_counts
                        ),
                        "worker_tick_counts": asdict(
                            result.pipeline_summary.worker_tick_counts
                        ),
                        "outcome_counts": asdict(
                            result.pipeline_summary.outcome_counts
                        ),
                        "total_ticks": result.pipeline_summary.total_ticks,
                        "total_jobs": result.pipeline_summary.total_jobs,
                        "last_event_sequence": result.pipeline_summary.last_event_sequence,
                        "snapshot_reload_recommended": (
                            result.pipeline_summary.snapshot_reload_recommended
                        ),
                        "stopped_reason": result.pipeline_summary.stopped_reason,
                        "stopped_worker_type": result.pipeline_summary.stopped_worker_type,
                        "stopped_outcome": result.pipeline_summary.stopped_outcome,
                        "attention_code": result.pipeline_summary.attention_code,
                    }
                    if result.pipeline_summary is not None
                    else None
                ),
            }
            for result in summary.results
        ],
    }


async def _run_once(
    *,
    service: ReaderEnhancementWorkerLoopService,
    args: argparse.Namespace,
) -> ReaderEnhancementWorkerLoopCycleSummary:
    return await service.run_once(
        batch_size=args.batch_size,
        lease_owner_prefix=args.lease_owner_prefix,
        max_ticks=args.max_ticks,
        max_jobs=args.max_jobs,
    )


async def _run_worker(args: argparse.Namespace, settings: Settings) -> None:
    if args.batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if args.max_ticks < 1:
        raise ValueError("max_ticks must be >= 1")
    if args.max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")
    if not args.once and args.scan_interval_seconds < 1:
        raise ValueError("scan_interval_seconds must be >= 1 in loop mode")

    await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        service = ReaderEnhancementWorkerLoopService()
        if args.once:
            summary = await _run_once(service=service, args=args)
            print(json.dumps(_build_cycle_payload(summary), ensure_ascii=False, indent=2))
            return

        while True:
            summary = await _run_once(service=service, args=args)
            logger.info(
                "reader enhancement worker cycle completed",
                extra={
                    "recovered_stale_leases": summary.recovered_stale_leases,
                    "scanned_candidate_count": summary.scanned_candidate_count,
                    "processed_count": summary.processed_count,
                    "lock_skipped_count": summary.lock_skipped_count,
                },
            )
            await asyncio.sleep(args.scan_interval_seconds)
    finally:
        await close_db()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    args = _parse_args(settings)
    asyncio.run(_run_worker(args, settings))


if __name__ == "__main__":
    main()
