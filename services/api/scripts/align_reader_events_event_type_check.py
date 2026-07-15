"""Idempotent reader_events_event_type_check alignment.

Reads the canonical event-type set from ``app.services.reader_orchestration.
event_runtime.ReaderEventType`` (a single source of truth), then reconciles
the live ``reader_events_event_type_check`` constraint to match.

This script is intentionally idempotent: re-running it against a schema
that already matches is a no-op, and re-running against a schema that
diverges from the canonical list will converge. It is safe to run in any
environment.

Background: dev DBs created from older SQL dumps sometimes omit one or
more event types (notably ``record_product_state_updated``) that the
Python code now writes via ``worker_loop.process_candidate``. The result
is an asyncpg ``CheckViolationError`` the first time the worker tries to
publish a ``record_product_state_updated`` event, which surfaces as the
"reader-enhancement-worker errored on startup" symptom this script fixes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Iterable

import asyncpg


def _load_canonical_event_types() -> tuple[str, ...]:
    """Pull event names from the ``ReaderEventType`` ``Literal[...]`` body.

    Reading from source avoids hard-coding the list and keeps the script
    in sync with ``event_runtime.py`` whenever a new event type is
    added.
    """
    here = Path(__file__).resolve()
    # This script lives at ``services/api/scripts``; ``event_runtime`` is at
    # ``services/api/app/services/reader_orchestration/event_runtime.py``.
    api_root = here.parents[1]
    runtime_path = (
        api_root
        / "app"
        / "services"
        / "reader_orchestration"
        / "event_runtime.py"
    )
    src = runtime_path.read_text(encoding="utf-8")

    # Locate ``ReaderEventType = Literal[ ... ]`` and capture the body.
    body_match = re.search(
        r"ReaderEventType\s*(?::\s*[^=]+)?=\s*Literal\[\s*([^\]]+)\]",
        src,
        flags=re.DOTALL,
    )
    if body_match is None:
        raise SystemExit(
            "Could not locate ``ReaderEventType = Literal[...]`` in "
            f"{runtime_path}; refusing to guess."
        )
    body = body_match.group(1)
    literals = re.findall(r'"([a-z][a-z0-9_]*)"', body)
    # Dedup while preserving order.
    seen: set[str] = set()
    canonical: list[str] = []
    for lit in literals:
        if lit in seen:
            continue
        seen.add(lit)
        canonical.append(lit)
    return tuple(canonical)


async def _align_constraint(
    *,
    database_url: str,
    schema_name: str | None,
    canonical: Iterable[str],
    dry_run: bool,
) -> None:
    canonical_set = sorted(set(canonical))
    conn = await asyncpg.connect(database_url)
    try:
        if schema_name is not None:
            await conn.execute(f'SET search_path TO "{schema_name}", public')

        rows = await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            WHERE conrelid = 'reader_events'::regclass
              AND contype = 'c'
              AND conname = 'reader_events_event_type_check'
            """
        )
        if not rows:
            print(
                "reader_events_event_type_check not present in target schema; "
                "nothing to align. (Baseline SCHEMA will define it via 0001.)"
            )
            return
        current_def = rows[0]["def"]
        current_literals = set(
            re.findall(r"'([a-z][a-z0-9_]*)'", current_def)
        )
        target_literals = set(canonical_set)
        if current_literals == target_literals:
            print(
                "OK: reader_events_event_type_check already matches the "
                "Python ReaderEventType literal set; no change required."
            )
            return

        missing_in_db = sorted(target_literals - current_literals)
        extra_in_db = sorted(current_literals - target_literals)
        print(
            f"reader_events_event_type_check diverges from canonical list:"
            f"\n  missing in DB: {missing_in_db}"
            f"\n  extra in DB:   {extra_in_db}"
        )
        if dry_run:
            print("Dry-run; not modifying constraint.")
            return

        sql_values = ", ".join(
            f"'{name}'::text" for name in sorted(target_literals)
        )
        alter_sql = (
            "ALTER TABLE reader_events "
            "DROP CONSTRAINT reader_events_event_type_check"
        )
        await conn.execute(alter_sql)
        add_sql = (
            "ALTER TABLE reader_events "
            "ADD CONSTRAINT reader_events_event_type_check "
            f"CHECK (event_type = ANY (ARRAY[{sql_values}]))"
        )
        await conn.execute(add_sql)
        # Verify the change took effect.
        verify = await conn.fetchval(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            WHERE conrelid = 'reader_events'::regclass
              AND contype = 'c'
              AND conname = 'reader_events_event_type_check'
            """
        )
        print("Aligned constraint definition:")
        print(f"  {verify}")
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Align the live reader_events_event_type_check constraint with "
            "the canonical ReaderEventType literal list."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            None,
        ),
        help=(
            "PostgreSQL DSN. Defaults to DATABASE_URL; required when unset."
        ),
    )
    parser.add_argument(
        "--schema-name",
        default=None,
        help=(
            "Optional target schema (uses SET search_path). Omit for the "
            "default search_path, e.g. when running against an isolated "
            "baseline schema."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report divergence without modifying the constraint.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required.")
    canonical = _load_canonical_event_types()
    print(f"Canonical event types ({len(canonical)}): {canonical}")
    asyncio.run(
        _align_constraint(
            database_url=args.database_url,
            schema_name=args.schema_name,
            canonical=canonical,
            dry_run=args.dry_run,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
