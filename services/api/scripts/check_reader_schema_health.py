from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config.settings import get_settings
from app.services.reader_orchestration.schema_health import (
    check_reader_schema_health,
    format_reader_schema_health_failure,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Reader D5/D6 schema health for local/dev databases."
    )
    parser.add_argument(
        "--schema-name",
        default="public",
        help="Schema name to inspect. Defaults to public.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        report = await check_reader_schema_health(
            conn,
            schema_name=args.schema_name,
        )
    finally:
        await conn.close()

    if args.json:
        payload = report.to_dict()
        if not report.ok:
            payload["guidance"] = format_reader_schema_health_failure(report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    if report.ok:
        print(
            f"Reader schema health OK for schema '{report.schema_name}'. "
            "D5 attribution objects and D6 user asset anchor objects are present."
        )
        return 0

    print(format_reader_schema_health_failure(report), file=sys.stderr)
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
