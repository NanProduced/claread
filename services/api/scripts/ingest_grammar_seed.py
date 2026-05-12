"""Grammar seed 数据 ingestion 脚本。

从 grammar_seed_v1.jsonl 读取 seed 数据，
调用百炼 Embedding 生成向量，写入 Zilliz 两个 collection。

用法：
    python scripts/ingest_grammar_seed.py [--dry-run] [--batch-size 10] [--seed-file PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SERVER_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED_FILE = SERVER_ROOT / "data" / "seed" / "grammar_seed_v1.jsonl"


def _load_seed(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping line %d: %s", line_no, e)
    return records


def _map_record_to_zilliz(record: dict, vector: list[float]) -> dict:
    return {
        "example_id": record["example_id"],
        "vector": vector,
        "reading_variant": record.get("variant", ""),
        "output_type": record.get("output_type", ""),
        "grammar_tags": json.dumps(record.get("tags", []), ensure_ascii=False),
        "structure_signals": json.dumps(record.get("signals", []), ensure_ascii=False),
        "label": record.get("label", ""),
        "source_sentence": record.get("source_sentence", ""),
        "output_fragment": record.get("output_fragment", ""),
        "grammar_granularity": record.get("teaching_goal", ""),
        "quality_score": record.get("quality_score", 0.0),
        "approved": record.get("approved", True),
    }


async def _run_ingestion(
    seed_file: Path,
    batch_size: int,
    dry_run: bool,
) -> None:
    sys.path.insert(0, str(SERVER_ROOT))

    from app.config.settings import get_settings
    from app.infra.bailian_embedding import embed_texts
    from app.infra.zilliz_client import (
        close_zilliz,
        init_zilliz,
        zilliz_create_collection,
        zilliz_insert,
        zilliz_query,
    )

    settings = get_settings()

    records = _load_seed(seed_file)
    logger.info("Loaded %d seed records from %s", len(records), seed_file)

    grammar_note_records = [r for r in records if r.get("output_type") == "grammar_note"]
    sentence_analysis_records = [r for r in records if r.get("output_type") == "sentence_analysis"]
    logger.info(
        "Split: %d grammar_note, %d sentence_analysis",
        len(grammar_note_records),
        len(sentence_analysis_records),
    )

    if dry_run:
        logger.info("=== DRY RUN MODE ===")
        for r in records:
            mapped = _map_record_to_zilliz(r, [0.0] * settings.bailian_embedding_dimension)
            collection = (
                "grammar_note_examples"
                if r["output_type"] == "grammar_note"
                else "sentence_analysis_examples"
            )
            logger.info(
                "  %s → collection=%s, variant=%s, label=%s",
                mapped["example_id"],
                collection,
                mapped["reading_variant"],
                mapped["label"],
            )
        logger.info("Total: %d records to ingest", len(records))
        return

    if not settings.zilliz_uri or not settings.zilliz_token:
        logger.error("ZILLIZ_URI and ZILLIZ_TOKEN must be configured in .env")
        sys.exit(1)

    if not settings.bailian_api_key:
        logger.error("BAILIAN_API_KEY must be configured in .env")
        sys.exit(1)

    await init_zilliz(uri=settings.zilliz_uri, token=settings.zilliz_token)

    for collection_name, group in [
        (settings.zilliz_collection_grammar_note, grammar_note_records),
        (settings.zilliz_collection_sentence_analysis, sentence_analysis_records),
    ]:
        if not group:
            logger.info("No records for %s, skipping", collection_name)
            continue

        await zilliz_create_collection(collection_name, dimension=settings.bailian_embedding_dimension)

        existing = await zilliz_query(
            collection_name,
            filter_expr="approved == true",
            output_fields=["example_id"],
            limit=1000,
        )
        existing_ids = {r["example_id"] for r in existing}

        to_ingest = [r for r in group if r["example_id"] not in existing_ids]
        if not to_ingest:
            logger.info("All records already exist in %s, skipping", collection_name)
            continue

        logger.info(
            "Ingesting %d new records into %s (%d already exist)",
            len(to_ingest),
            collection_name,
            len(existing_ids),
        )

        for i in range(0, len(to_ingest), batch_size):
            batch = to_ingest[i : i + batch_size]
            texts = [r.get("retrieval_text", "") for r in batch]

            t0 = time.monotonic()
            vectors = await embed_texts(
                texts,
                model=settings.bailian_embedding_model,
                dimension=settings.bailian_embedding_dimension,
            )
            elapsed = time.monotonic() - t0
            logger.info(
                "Embedded batch %d-%d in %.1fs",
                i + 1,
                min(i + batch_size, len(to_ingest)),
                elapsed,
            )

            data = [_map_record_to_zilliz(r, v) for r, v in zip(batch, vectors)]
            await zilliz_insert(collection_name, data)

        logger.info("Finished ingesting %s", collection_name)

    await close_zilliz()
    logger.info("Ingestion complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest grammar seed data into Zilliz")
    parser.add_argument("--dry-run", action="store_true", help="Print mapped data without writing to Zilliz")
    parser.add_argument("--batch-size", type=int, default=10, help="Embedding batch size (default: 10)")
    parser.add_argument("--seed-file", type=str, default=str(DEFAULT_SEED_FILE), help="Path to seed JSONL file")
    args = parser.parse_args()

    asyncio.run(_run_ingestion(
        seed_file=Path(args.seed_file),
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
