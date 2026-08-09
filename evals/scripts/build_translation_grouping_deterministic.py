"""Build sentence lists + deterministic translation groups for the
translation-grouping eval dataset.

Run with the services/api venv so ``app`` imports resolve:

    cd services/api && uv run python ../../evals/scripts/build_translation_grouping_deterministic.py

Outputs per article into evals/datasets/translation-grouping-v1/groups/deterministic/{id}.json:
  {id, category, sentences: [{n, unit_id, anchor_segment_id, text}],
   sentence_provider, groups: [{group_id, sentence_ns, text}]}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "evals" / "datasets" / "translation-grouping-v1"
ARTICLES_DIR = DATASET_DIR / "articles"
OUT_DIR = DATASET_DIR / "groups" / "deterministic"

from app.services.reader_orchestration.base_builder import (  # noqa: E402
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.translation_worker import (  # noqa: E402
    TranslationAnchorSegmentTarget,
    TranslationBatchUnitContext,
    plan_translation_groups,
)


def build_article_groups(article_id: str, text: str) -> dict:
    result = build_reading_base_from_canonical_text(
        reading_record_id=f"eval-{article_id}",
        base_id=f"base-{article_id}",
        canonical_text=text,
        language="en",
    )
    sentences: list[dict] = []
    groups: list[dict] = []
    n = 0
    for unit in result.units:
        targets = tuple(
            TranslationAnchorSegmentTarget(
                anchor_segment_id=segment.anchor_segment_id,
                sentence_id=segment.sentence_id,
                order_index=segment.order_index,
                segment_type=segment.segment_type,
                boundary_quality=segment.boundary_quality,
                unit_start_utf16=segment.unit_start_utf16,
                unit_end_utf16=segment.unit_end_utf16,
                text_hash=segment.text_hash,
                source_text=segment.text,
            )
            for segment in result.anchor_segments
            if segment.unit_id == unit.unit_id
        )
        unit_sentences = []
        for segment in targets:
            n += 1
            unit_sentences.append(n)
            sentences.append(
                {
                    "n": n,
                    "unit_id": unit.unit_id,
                    "anchor_segment_id": segment.anchor_segment_id,
                    "text": segment.source_text,
                }
            )
        context = TranslationBatchUnitContext(
            unit_id=unit.unit_id,
            order_index=unit.order_index,
            source_text=unit.text,
            text_hash=unit.text_hash,
            anchor_segments=targets,
        )
        for index, plan in enumerate(plan_translation_groups(context), start=1):
            first = plan.anchor_segment_ids[0]
            last = plan.anchor_segment_ids[-1]
            group_sentences = [
                s["n"]
                for s in sentences
                if s["anchor_segment_id"] in plan.anchor_segment_ids
            ]
            groups.append(
                {
                    "group_id": f"{unit.unit_id}_g{first}_{last}" if len(plan.anchor_segment_ids) > 1 else f"{unit.unit_id}_g{first}",
                    "sentence_ns": group_sentences,
                    "text": " ".join(
                        s["text"] for s in sentences if s["n"] in group_sentences
                    ),
                    "order_index": index,
                }
            )
    return {
        "id": article_id,
        "sentence_provider": result.base.segmenter_version,
        "sentences": sentences,
        "groups": groups,
    }


def main() -> None:
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    providers: dict[str, int] = {}
    for entry in manifest:
        article_id = entry["id"]
        text = (ARTICLES_DIR / f"{article_id}.txt").read_text(encoding="utf-8")
        payload = build_article_groups(article_id, text)
        payload["category"] = entry["category"]
        providers[payload["sentence_provider"]] = (
            providers.get(payload["sentence_provider"], 0) + 1
        )
        (OUT_DIR / f"{article_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"{article_id}: sentences={len(payload['sentences'])} "
            f"groups={len(payload['groups'])} provider={payload['sentence_provider']}"
        )
    print("providers:", providers)


if __name__ == "__main__":
    sys.exit(main())
