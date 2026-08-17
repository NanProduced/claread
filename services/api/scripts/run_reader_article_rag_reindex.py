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

# Wave 11: the single "latest run truth" source.  For every ACTIVE
# reading record it selects the LATEST run of the record's ACTIVE stable
# document (descending updated_at, then descending id for a stable
# tie-break).  Both the --all candidate list and the dry-run
# classification derive from THIS one query, so dry-run and execute can
# never disagree about which record is eligible / in-progress.
_LATEST_RUN_TRUTH_SQL = """
    (
        SELECT DISTINCT ON (ir.reading_record_id)
            ir.reading_record_id,
            rr.user_id,
            ir.status
        FROM reader_article_rag_index_runs ir
        JOIN stable_reading_documents sd
          ON sd.id = ir.stable_document_id
         AND sd.status = 'active'
        JOIN reading_records rr
          ON rr.id = ir.reading_record_id
         AND rr.deleted_at IS NULL
         AND rr.lifecycle_status = 'active'
        ORDER BY ir.reading_record_id, ir.updated_at DESC, ir.id DESC
    ) latest
"""

# Wave 7.1 / P2: the dry-run classification joins the reading record
# (deleted_at IS NULL + lifecycle_status='active') so deleted/inactive
# records are skipped, and classifies on the LATEST run of the ACTIVE
# stable document — indexed -> reindex-eligible; failed/superseded ->
# recovery-eligible (Wave 7.1 / P0); in-flight -> in-progress.
_DRY_RUN_STATUS_SQL = """
    SELECT ir.status
    FROM reader_article_rag_index_runs ir
    JOIN stable_reading_documents sd
      ON sd.id = ir.stable_document_id AND sd.status = 'active'
    JOIN reading_records rr
      ON rr.id = ir.reading_record_id
     AND rr.deleted_at IS NULL
     AND rr.lifecycle_status = 'active'
    WHERE ir.reading_record_id = $1
    ORDER BY ir.updated_at DESC, ir.id DESC
    LIMIT 1
"""

# Wave 7.1 / P0: the --all candidate list includes records whose LATEST
# run FAILED or was SUPERSEDED (both recoverable via the service's
# recovery path), not only currently-indexed ones.  Wave 11: the
# candidate list is the latest-run truth restricted to indexed /
# failed / superseded — an old indexed run on a stale stable document,
# or a newer queued/superseded run, no longer selects the record.
_ALL_CANDIDATES_SQL = f"""
    SELECT latest.reading_record_id, latest.user_id
    FROM {_LATEST_RUN_TRUTH_SQL}
    WHERE latest.status IN ('indexed', 'failed', 'superseded')
    ORDER BY latest.reading_record_id
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


def _non_negative_float(value: str) -> float:
    """argparse type: float >= 0 (0 disables rate limiting)."""
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _positive_int(value: str) -> int:
    """argparse type: int > 0."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


class _ReindexArgumentParser(argparse.ArgumentParser):
    """Parser with the cross-option guard argparse cannot express
    natively: ``--limit`` only caps the ``--all`` candidate list."""

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        parsed = super().parse_args(args, namespace)
        if parsed.limit is not None and not parsed.all:
            self.error("--limit is only valid with --all")
        return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = _ReindexArgumentParser(
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
        help=(
            "Reindex every active record whose LATEST Article RAG run "
            "is indexed, failed, or superseded (failed/superseded runs "
            "are recovery-eligible)."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write (default: dry-run, zero writes).",
    )
    parser.add_argument(
        "--rate-limit-per-second",
        type=_non_negative_float,
        default=DEFAULT_RATE_LIMIT_PER_SECOND,
        help="Minimum spacing between execute iterations (0 disables).",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Cap the --all candidate list (only valid with --all).",
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
    """Read-only classification of the latest run on the record's
    ACTIVE stable document (inactive / deleted records and records
    without an active stable document are skipped):

    * ``indexed``             — reindex-eligible (supersede path)
    * ``failed``/``superseded`` — recovery-eligible (no active run; the
                                  service bootstraps fresh)
    * ``planned``/``queued``/``indexing`` — in-progress
    * no row                  — skipped
    """
    rows = await pool.fetch(_DRY_RUN_STATUS_SQL, record_id)
    if not rows:
        return "skipped"
    status = str(rows[0]["status"])
    if status in ("indexed", "failed", "superseded"):
        return "indexed" if status == "indexed" else "recovery"
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
            if classification in ("indexed", "recovery"):
                # Both the supersede path (indexed) and the recovery
                # path (terminal failed/superseded latest run) would
                # act on this record in execute mode.
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
            if status in ("reindex_enqueued", "recovery_enqueued"):
                # Both paths produced a fresh queued build job.
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
