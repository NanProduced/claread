"""Reader Article RAG operator reindex entry point (Wave 7 / F1c).

Drives :meth:`ArticleRagIndexLifecycleService.reindex_article_rag_index_in_transaction`
for operators only — NO HTTP route, NO scheduler, NO automatic
contract-drift trigger.

Behaviour (O3 — explicit operator trigger only):

- Default mode is DRY-RUN: read-only classification, zero writes,
  zero service calls.
- ``--execute`` is required for any write.  Each record runs in its
  OWN transaction; a single failure never aborts the batch.
- ``--record-id`` and ``--all`` are mutually exclusive; one is
  required.
- ``--all`` only selects active records that currently hold an
  ``indexed`` Article RAG run.
- ``--rate-limit-per-second`` spaces execute iterations (default 1.0;
  ``0`` disables).
- ``--limit`` caps the ``--all`` candidate list.

The CLI only flips PostgreSQL state (supersede + bootstrap).  It never
calls embedding / vector providers, never starts the worker, and never
reads or prints secrets.  Recovery from a failed new build is
re-running the reindex — there is no automatic rollback.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any
from uuid import UUID

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config.settings import get_settings
from app.database.connection import close_db, init_db
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ArticleRagIndexLifecycleService,
)

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_PER_SECOND = 1.0

# Stable summary keys printed at the end of every run.
SUMMARY_KEYS = (
    "scanned",
    "eligible",
    "enqueued",
    "in_progress",
    "skipped",
    "failed",
)

# Active (non-terminal) index-run statuses — mirrors the lifecycle
# service's active set.
_ACTIVE_RUN_STATUSES = ("planned", "queued", "indexing", "indexed")

_DRY_RUN_STATUS_SQL = """
    SELECT ir.status
    FROM reader_article_rag_index_runs ir
    JOIN stable_reading_documents sd
      ON sd.id = ir.stable_document_id AND sd.status = 'active'
    WHERE ir.reading_record_id = $1
      AND ir.status = ANY($2::text[])
    LIMIT 1
"""

_ALL_CANDIDATES_SQL = """
    SELECT DISTINCT ir.reading_record_id, rr.user_id
    FROM reader_article_rag_index_runs ir
    JOIN reading_records rr
      ON rr.id = ir.reading_record_id
     AND rr.deleted_at IS NULL
     AND rr.lifecycle_status = 'active'
    WHERE ir.status = 'indexed'
    ORDER BY ir.reading_record_id
"""

_RECORD_OWNER_SQL = """
    SELECT user_id FROM reading_records
    WHERE id = $1
      AND deleted_at IS NULL
      AND lifecycle_status = 'active'
"""


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_reader_article_rag_reindex",
        description=(
            "Operator-only explicit Article RAG reindex "
            "(supersede indexed run + enqueue new build). "
            "Default is dry-run; --execute is required for writes."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--record-id",
        type=UUID,
        help="Reindex a single reading record.",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Reindex every active record with an indexed Article RAG run.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write (default: dry-run, zero writes).",
    )
    parser.add_argument(
        "--rate-limit-per-second",
        type=float,
        default=DEFAULT_RATE_LIMIT_PER_SECOND,
        help="Minimum spacing between execute iterations (0 disables).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the --all candidate list.",
    )
    return parser


# ---------------------------------------------------------------------------
# Core runner (injectable pool + service for tests)
# ---------------------------------------------------------------------------


def _new_summary() -> dict[str, int]:
    return {key: 0 for key in SUMMARY_KEYS}


async def _classify_dry_run(
    pool: Any,
    record_id: UUID,
) -> str:
    """Read-only classification: 'indexed' | 'in_progress' | 'skipped'."""
    rows = await pool.fetch(_DRY_RUN_STATUS_SQL, record_id, list(_ACTIVE_RUN_STATUSES))
    if not rows:
        return "skipped"
    status = str(rows[0]["status"])
    if status == "indexed":
        return "indexed"
    return "in_progress"


async def run_reindex(
    *,
    pool: Any,
    service: Any,
    record_ids: list[UUID] | None,
    all_records: bool,
    execute: bool,
    rate_limit_per_second: float,
    limit: int | None,
) -> dict[str, int]:
    """Run the reindex flow; returns the stable summary mapping."""
    summary = _new_summary()

    # Resolve the candidate list.
    if all_records:
        sql = _ALL_CANDIDATES_SQL
        args: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT $1"
            args = (limit,)
        candidate_rows = await pool.fetch(sql, *args)
        targets: list[tuple[UUID, UUID | None]] = [
            (row["reading_record_id"], row["user_id"])
            for row in candidate_rows
        ]
    else:
        assert record_ids is not None
        targets = [(rid, None) for rid in record_ids]

    for index, (record_id, owner_user_id) in enumerate(targets):
        summary["scanned"] += 1

        if not execute:
            classification = await _classify_dry_run(pool, record_id)
            if classification == "indexed":
                summary["eligible"] += 1
            elif classification == "in_progress":
                summary["in_progress"] += 1
            else:
                summary["skipped"] += 1
            continue

        # Execute mode: resolve the record owner when the candidate
        # list did not carry it (single-record mode).
        user_id = owner_user_id
        if user_id is None:
            owner_rows = await pool.fetch(_RECORD_OWNER_SQL, record_id)
            if not owner_rows:
                summary["skipped"] += 1
                continue
            user_id = owner_rows[0]["user_id"]

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    result = await service.reindex_article_rag_index_in_transaction(
                        conn,
                        reading_record_id=record_id,
                        user_id=user_id,
                    )
        except Exception:
            logger.exception("reindex failed for record %s", record_id)
            summary["failed"] += 1
        else:
            status = result.status
            if status == "reindex_enqueued":
                summary["eligible"] += 1
                summary["enqueued"] += 1
            elif status == "reindex_in_progress":
                summary["in_progress"] += 1
            else:
                summary["skipped"] += 1

        # Rate limiting between execute iterations (never after the
        # last one; 0 disables).
        if (
            rate_limit_per_second > 0
            and index < len(targets) - 1
        ):
            await asyncio.sleep(1.0 / rate_limit_per_second)

    return summary


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def _async_main(args: argparse.Namespace) -> int:
    settings = get_settings()
    pool = await init_db(settings)
    try:
        service = ArticleRagIndexLifecycleService()
        summary = await run_reindex(
            pool=pool,
            service=service,
            record_ids=[args.record_id] if args.record_id else None,
            all_records=args.all,
            execute=args.execute,
            rate_limit_per_second=args.rate_limit_per_second,
            limit=args.limit,
        )
    finally:
        await close_db()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"article rag reindex summary ({mode}):")
    for key in SUMMARY_KEYS:
        print(f"  {key}: {summary[key]}")
    return 0 if summary["failed"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
