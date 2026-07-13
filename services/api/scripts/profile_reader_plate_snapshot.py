from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from uuid import UUID

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration.snapshot_profiler import (
    build_deterministic_profiling_fixture,
    profile_reader_plate_snapshot,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile a ReaderPlateSnapshot and emit machine-readable "
            "SnapshotProfile JSON. Read-only: does not trigger worker, LLM, "
            "orchestration rerun, or DB writes."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--fixture",
        action="store_true",
        help="Use the deterministic in-memory profiling fixture (no DB).",
    )
    group.add_argument(
        "--from-json",
        metavar="PATH",
        help="Load a ReaderPlateSnapshot from a JSON file (no DB).",
    )
    group.add_argument(
        "--record-id",
        type=UUID,
        metavar="ID",
        help="Load a snapshot from the DB by record id (requires --user-id).",
    )
    parser.add_argument(
        "--user-id",
        type=UUID,
        metavar="UID",
        help="User id owning the record (required with --record-id).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write the profile JSON to this file (default: stdout).",
    )
    parser.add_argument(
        "--collected-at",
        metavar="UTC_ISO",
        help=(
            "UTC ISO-8601 timestamp recorded as collected_at "
            "(default: now UTC). Naive datetimes are assumed UTC."
        ),
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Repeat measurement N times (default 1). N>1 outputs a JSON array.",
    )
    args = parser.parse_args()
    if args.record_id is not None and args.user_id is None:
        parser.error("--user-id is required when --record-id is used")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    return args


async def _init_snapshot_service() -> tuple[
    object | None, Callable[[], Awaitable[None]] | None
]:
    """Initialize the DB pool and return ``(service, cleanup_fn)``.

    Returns ``(None, None)`` if pool initialization fails. The returned
    cleanup function closes the DB pool.

    Pool lifecycle (init / close) is intentionally kept OUTSIDE the
    ``record_snapshot_load_duration_ns`` timing boundary: only
    ``service.load_snapshot()`` itself is timed by the caller.
    """
    from app.config.settings import get_settings
    from app.database.connection import close_db, init_db
    from app.services.reader_orchestration.article_ready_service import (
        ArticleReadyPersistenceService,
    )

    settings = get_settings()
    try:
        pool = await init_db(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            max_inactive_connection_lifetime=(
                settings.database_max_inactive_connection_lifetime
            ),
        )
    except Exception:
        print(
            "Failed to initialize database pool "
            "(check DATABASE_URL / database settings).",
            file=sys.stderr,
        )
        return None, None

    service = ArticleReadyPersistenceService(pool=pool)

    async def cleanup() -> None:
        await close_db()

    return service, cleanup


def _parse_collected_at(args: argparse.Namespace) -> datetime | int:
    """Parse --collected-at; returns datetime or exit code int on error."""
    if args.collected_at is None:
        return datetime.now(timezone.utc)
    try:
        collected_at = datetime.fromisoformat(args.collected_at)
    except ValueError as e:
        print(
            f"Invalid --collected-at {args.collected_at!r}: {e}",
            file=sys.stderr,
        )
        return 2
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    return collected_at


async def async_main(args: argparse.Namespace) -> int:
    collected_at = _parse_collected_at(args)
    if isinstance(collected_at, int):
        return collected_at

    repeat = args.repeat

    if repeat == 1:
        # Single-measurement path (backward compatible): output a single
        # SnapshotProfile JSON, byte-identical to the pre-repeat behavior.
        record_snapshot_load_duration_ns: int | None = None

        if args.fixture:
            snapshot = build_deterministic_profiling_fixture()
        elif args.from_json is not None:
            path = Path(args.from_json)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                print(
                    f"Failed to read {path}: {type(e).__name__}",
                    file=sys.stderr,
                )
                return 1
            try:
                snapshot = ReaderPlateSnapshot.model_validate_json(text)
            except Exception as e:
                print(
                    f"Failed to parse snapshot from {path}: {type(e).__name__}",
                    file=sys.stderr,
                )
                return 1
        elif args.record_id is not None:
            service, cleanup = await _init_snapshot_service()
            if service is None:
                return 1
            try:
                # Timing boundary: ONLY wraps service.load_snapshot().
                # Pool init / close and settings load are excluded.
                load_start = time.perf_counter_ns()
                try:
                    snapshot = await service.load_snapshot(
                        record_id=args.record_id,
                        user_id=args.user_id,
                    )
                except Exception:
                    print(
                        "Failed to load snapshot from database "
                        "(record not found or database error).",
                        file=sys.stderr,
                    )
                    return 1
                load_end = time.perf_counter_ns()
                record_snapshot_load_duration_ns = load_end - load_start
            finally:
                if cleanup is not None:
                    await cleanup()
        else:
            # Unreachable: argparse enforces the required mutually exclusive group.
            print("No mode selected (internal error).", file=sys.stderr)
            return 2

        profile = profile_reader_plate_snapshot(
            snapshot,
            collected_at=collected_at,
            record_snapshot_load_duration_ns=record_snapshot_load_duration_ns,
        )
        payload = profile.model_dump_json(indent=2)

        if args.output is not None:
            try:
                Path(args.output).write_text(payload, encoding="utf-8")
            except OSError as e:
                print(
                    f"Failed to write {args.output}: {type(e).__name__}",
                    file=sys.stderr,
                )
                return 1
        else:
            print(payload)

        return 0

    # N > 1 path: output a JSON array of N measurement objects, each
    # annotated with repeat_index. No cache_phase field: the label
    # "warm" / "cold_possible" implied cache semantics that this CLI does
    # not control (each --record-id iteration reuses the same pool but the
    # OS / DB page cache state is not managed by the harness).
    results: list[dict[str, object]] = []
    if args.fixture:
        snapshot = build_deterministic_profiling_fixture()
        for i in range(repeat):
            profile = profile_reader_plate_snapshot(
                snapshot, collected_at=collected_at
            )
            results.append(
                {
                    "repeat_index": i,
                    "profile": json.loads(profile.model_dump_json()),
                }
            )
    elif args.from_json is not None:
        path = Path(args.from_json)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(
                f"Failed to read {path}: {type(e).__name__}",
                file=sys.stderr,
            )
            return 1
        try:
            snapshot = ReaderPlateSnapshot.model_validate_json(text)
        except Exception as e:
            print(
                f"Failed to parse snapshot from {path}: {type(e).__name__}",
                file=sys.stderr,
            )
            return 1
        for i in range(repeat):
            profile = profile_reader_plate_snapshot(
                snapshot, collected_at=collected_at
            )
            results.append(
                {
                    "repeat_index": i,
                    "profile": json.loads(profile.model_dump_json()),
                }
            )
    elif args.record_id is not None:
        # Pool is initialized once and reused across all N iterations.
        # Each iteration's record_snapshot_load_duration_ns wraps ONLY
        # service.load_snapshot(); pool lifecycle is excluded.
        service, cleanup = await _init_snapshot_service()
        if service is None:
            return 1
        try:
            for i in range(repeat):
                load_start = time.perf_counter_ns()
                try:
                    snapshot = await service.load_snapshot(
                        record_id=args.record_id,
                        user_id=args.user_id,
                    )
                except Exception:
                    print(
                        "Failed to load snapshot from database "
                        "(record not found or database error).",
                        file=sys.stderr,
                    )
                    return 1
                load_end = time.perf_counter_ns()
                profile = profile_reader_plate_snapshot(
                    snapshot,
                    collected_at=collected_at,
                    record_snapshot_load_duration_ns=load_end - load_start,
                )
                results.append(
                    {
                        "repeat_index": i,
                        "profile": json.loads(profile.model_dump_json()),
                    }
                )
        finally:
            if cleanup is not None:
                await cleanup()
    else:
        # Unreachable: argparse enforces the required mutually exclusive group.
        print("No mode selected (internal error).", file=sys.stderr)
        return 2

    payload = json.dumps(results, indent=2, ensure_ascii=False)

    if args.output is not None:
        try:
            Path(args.output).write_text(payload, encoding="utf-8")
        except OSError as e:
            print(
                f"Failed to write {args.output}: {type(e).__name__}",
                file=sys.stderr,
            )
            return 1
    else:
        print(payload)

    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
