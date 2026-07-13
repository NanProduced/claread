from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

BUCKET_NAMES = ("le_1500", "1501_4000", "4001_10000", "10001_30000", "gt_30000")

# Read-only SELECT. The word count is computed from original_inputs.source_text
# (the TRUE original article text) via the required CASE expression -- never
# from reading_bases.text or content_utf16_length.
#
# Schema note: migration 0001 defines the foreign key as
# original_inputs.reading_record_id -> reading_records.id. There is no
# reading_records.original_input_id column, so the JOIN uses the real column.
# A reading record can own multiple original_inputs (1:many), so DISTINCT ON
# (rr.id) keeps one row per record, preferring the latest original_input whose
# source_text is NOT NULL (so an extracted/text input is preferred over a
# file-ref-only input whose source_text is NULL).
_QUERY = """
SELECT
  sub.record_id,
  sub.generation,
  sub.base_text_length_utf16,
  sub.word_count
FROM (
  SELECT DISTINCT ON (rr.id)
    rr.id AS record_id,
    rr.generation,
    rr.created_at AS record_created_at,
    rb.content_utf16_length AS base_text_length_utf16,
    CASE WHEN oi.source_text IS NULL THEN NULL
         ELSE array_length(string_to_array(btrim(oi.source_text), ' '), 1)
    END AS word_count
  FROM reading_records rr
  LEFT JOIN original_inputs oi ON oi.reading_record_id = rr.id
  LEFT JOIN reading_bases rb ON rr.active_base_id = rb.id
  WHERE rr.active_base_id IS NOT NULL
  ORDER BY rr.id,
           (oi.source_text IS NULL) ASC,
           oi.created_at DESC NULLS LAST
) sub
ORDER BY sub.record_created_at DESC
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan local PostgreSQL for reading records, compute the TRUE "
            "original article word count from original_inputs.source_text "
            "(not reading_bases.text or content_utf16_length), bucket records "
            "by word count, and emit anonymized JSON. Read-only: no DB "
            "writes, no worker, no LLM."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write the JSON payload to this file (default: stdout).",
    )
    parser.add_argument(
        "--bucket",
        metavar="NAME",
        choices=BUCKET_NAMES,
        help=(
            "Filter output to a single bucket's records (one of: "
            + ", ".join(BUCKET_NAMES)
            + ")."
        ),
    )
    parser.add_argument(
        "--collected-at",
        metavar="UTC_ISO",
        help=(
            "UTC ISO-8601 timestamp recorded as collected_at "
            "(default: now UTC). Naive datetimes are assumed UTC."
        ),
    )
    return parser.parse_args()


def _bucket_for(word_count: int | None) -> str | None:
    if word_count is None:
        return None
    if word_count <= 1500:
        return "le_1500"
    if word_count <= 4000:
        return "1501_4000"
    if word_count <= 10000:
        return "4001_10000"
    if word_count <= 30000:
        return "10001_30000"
    return "gt_30000"


async def _scan_records() -> list[dict[str, object]]:
    from app.config.settings import get_settings
    from app.database.connection import close_db, init_db

    settings = get_settings()
    pool = await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=(
            settings.database_max_inactive_connection_lifetime
        ),
    )
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                rows = await conn.fetch(_QUERY)
    finally:
        await close_db()

    records: list[dict[str, object]] = []
    for row in rows:
        records.append(
            {
                "record_id_hash8": hashlib.sha256(
                    str(row["record_id"]).encode()
                ).hexdigest()[:8],
                "word_count": row["word_count"],
                "base_text_length_utf16": row["base_text_length_utf16"],
                "generation": row["generation"],
            }
        )
    return records


async def async_main(args: argparse.Namespace) -> int:
    if args.collected_at is None:
        collected_at = datetime.now(timezone.utc)
    else:
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

    try:
        records = await _scan_records()
    except Exception as e:
        print(
            f"Failed to scan reading records from database: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    buckets: dict[str, list[dict[str, object]]] = {
        name: [] for name in BUCKET_NAMES
    }
    word_count_unavailable: list[dict[str, object]] = []
    for index, record in enumerate(records, start=1):
        sample_id = f"S{index}"
        word_count = record["word_count"]
        bucket = _bucket_for(word_count)  # type: ignore[arg-type]
        record_id_hash8 = record["record_id_hash8"]
        base_text_length_utf16 = record["base_text_length_utf16"]
        generation = record["generation"]
        if bucket is None:
            word_count_unavailable.append(
                {
                    "sample_id": sample_id,
                    "record_id_hash8": record_id_hash8,
                    "base_text_length_utf16": base_text_length_utf16,
                    "generation": generation,
                }
            )
        else:
            buckets[bucket].append(
                {
                    "sample_id": sample_id,
                    "record_id_hash8": record_id_hash8,
                    "word_count": word_count,
                    "base_text_length_utf16": base_text_length_utf16,
                    "generation": generation,
                }
            )

    bucket_counts = {name: len(buckets[name]) for name in BUCKET_NAMES}
    total_records_scanned = len(records)
    records_word_count_unavailable = len(word_count_unavailable)
    records_with_word_count = total_records_scanned - records_word_count_unavailable

    if args.bucket is not None:
        output_buckets = {args.bucket: buckets[args.bucket]}
        output_unavailable: list[dict[str, object]] = []
    else:
        output_buckets = buckets
        output_unavailable = word_count_unavailable

    payload = {
        "schema_kind": "reader_word_bucket_scan",
        "schema_version": 1,
        "collected_at": collected_at.isoformat(),
        "buckets": output_buckets,
        "word_count_unavailable": output_unavailable,
        "summary": {
            "total_records_scanned": total_records_scanned,
            "records_with_word_count": records_with_word_count,
            "records_word_count_unavailable": records_word_count_unavailable,
            "bucket_counts": bucket_counts,
        },
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        try:
            Path(args.output).write_text(text, encoding="utf-8")
        except OSError as e:
            print(
                f"Failed to write {args.output}: {type(e).__name__}",
                file=sys.stderr,
            )
            return 1
    else:
        print(text)
    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
