"""Assemble blinded judging pairs for the translation-grouping eval.

Reads sentences + deterministic/semantic groupings, emits one blinded
judging sheet per article (Grouping X / Grouping Y, sides randomized with
a recorded seed) plus a private key used only at aggregation time.

Run with any Python 3 (no app imports):
    uv run python scripts/build_translation_grouping_pairs.py   # from evals/
    python evals/scripts/build_translation_grouping_pairs.py    # from repo root
"""

from __future__ import annotations

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "evals" / "datasets" / "translation-grouping-v1"
PAIRS_DIR = DATASET_DIR / "pairs"
KEY_PATH = REPO_ROOT / "tmp" / "w2-translation-grouping-eval-key.json"

SEED = 20260808


def fmt_grouping(groups: list[dict]) -> str:
    lines = []
    for index, group in enumerate(groups, start=1):
        ns = group["sentence_ns"]
        span = f"{ns[0]}" if len(ns) == 1 else f"{ns[0]}–{ns[-1]}"
        lines.append(
            f"  G{index}: sentences {span} "
            f"({len(ns)} sentence{'s' if len(ns) > 1 else ''})"
        )
    return "\n".join(lines)


def main() -> None:
    rng = random.Random(SEED)
    PAIRS_DIR.mkdir(parents=True, exist_ok=True)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {"seed": SEED, "articles": {}}

    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        article_id = entry["id"]
        sentences = json.loads(
            (DATASET_DIR / "sentences" / f"{article_id}.json").read_text(encoding="utf-8")
        )["sentences"]
        det = json.loads(
            (DATASET_DIR / "groups" / "deterministic" / f"{article_id}.json").read_text(
                encoding="utf-8"
            )
        )
        sem = json.loads(
            (DATASET_DIR / "groups" / "semantic" / f"{article_id}.json").read_text(
                encoding="utf-8"
            )
        )

        x_is_det = rng.random() < 0.5
        x_groups, y_groups = (
            (det["groups"], sem["groups"]) if x_is_det else (sem["groups"], det["groups"])
        )
        key["articles"][article_id] = {"X": "deterministic" if x_is_det else "semantic"}

        sheet = [
            f"# Blind grouping comparison — {article_id}",
            "",
            "You will judge two alternative partitions of the same article into",
            "translation groups. The article's sentences are numbered below.",
            "Read the article first, then evaluate both groupings against the rubric.",
            "",
            "## Article sentences",
            "",
        ]
        for sentence in sentences:
            sheet.append(f"[{sentence['n']}] {sentence['text']}")
            sheet.append("")
        sheet += [
            "## Grouping X",
            "",
            fmt_grouping(x_groups),
            "",
            "## Grouping Y",
            "",
            fmt_grouping(y_groups),
            "",
        ]
        (PAIRS_DIR / f"{article_id}.md").write_text(
            "\n".join(sheet), encoding="utf-8"
        )

    KEY_PATH.write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sides = sum(1 for a in key["articles"].values() if a["X"] == "deterministic")
    print(f"pairs written: {len(key['articles'])}; X=deterministic in {sides}; key at {KEY_PATH}")


if __name__ == "__main__":
    main()
