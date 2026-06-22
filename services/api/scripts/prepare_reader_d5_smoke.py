from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from uuid import UUID

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config.settings import get_settings
from app.database.connection import close_db, init_db
from app.services.reader_orchestration.smoke_harness import (
    ReaderEnhancementSmokeHarness,
    ReaderSmokeHarnessResult,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a local D5 reader smoke record for snapshot reload / Web inspection."
    )
    parser.add_argument("--user-id", required=True, help="Target user UUID")
    parser.add_argument("--plain-text", required=True, help="Plain text input to submit")
    parser.add_argument("--title", default=None, help="Optional record title")
    parser.add_argument(
        "--executor-mode",
        choices=("fake", "real"),
        default="real",
        help="Use deterministic dev/test-only fake executors or the real configured profiles",
    )
    parser.add_argument(
        "--allow-fake-executors",
        action="store_true",
        help=(
            "Required together with --executor-mode=fake. Fake executors write "
            "dev-only layers and are still disabled when APP_ENV=production."
        ),
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=24,
        help="Maximum worker attempts for the scoped pipeline run",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=24,
        help="Maximum claimed jobs for the scoped pipeline run",
    )
    return parser.parse_args()


async def _prepare_smoke_record(args: argparse.Namespace) -> ReaderSmokeHarnessResult:
    settings = get_settings()
    await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        harness = ReaderEnhancementSmokeHarness()
        return await harness.prepare_record(
            user_id=UUID(args.user_id),
            plain_text=args.plain_text,
            title=args.title,
            executor_mode=args.executor_mode,
            allow_fake_executors=args.allow_fake_executors,
            max_ticks=args.max_ticks,
            max_jobs=args.max_jobs,
        )
    finally:
        await close_db()


def _build_output_payload(result: ReaderSmokeHarnessResult) -> dict[str, object]:
    summary = result.pipeline_summary
    payload: dict[str, object] = {
        "record_id": str(result.record_id),
        "base_id": str(result.base_id),
        "executor_mode": result.executor_mode,
        "summary": {
            "bootstrapped_job_counts": asdict(summary.bootstrapped_job_counts),
            "worker_tick_counts": asdict(summary.worker_tick_counts),
            "outcome_counts": asdict(summary.outcome_counts),
            "total_ticks": summary.total_ticks,
            "total_jobs": summary.total_jobs,
            "last_event_sequence": summary.last_event_sequence,
            "snapshot_reload_recommended": summary.snapshot_reload_recommended,
            "stopped_reason": summary.stopped_reason,
            "stopped_worker_type": summary.stopped_worker_type,
            "stopped_outcome": summary.stopped_outcome,
            "attention_code": summary.attention_code,
        },
        "snapshot": {
            "snapshot_id": result.snapshot.snapshot_id,
            "last_event_sequence": result.snapshot.last_event_sequence,
            "layer_counts": asdict(result.layer_counts),
        },
    }
    if result.executor_note is not None:
        payload["executor_note"] = result.executor_note
    return payload


def main() -> None:
    args = _parse_args()
    result = asyncio.run(_prepare_smoke_record(args))
    print(json.dumps(_build_output_payload(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
