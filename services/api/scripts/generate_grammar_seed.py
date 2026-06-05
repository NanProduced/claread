"""RAG-06: Generate first-version grammar seed JSONL.

Reads grammar.yaml examples, enriches each with grammar_tags and
retrieval_text (canonical colon format), then writes a JSONL seed file.
"""

import json
import sys
from pathlib import Path

import yaml

SERVER_ROOT = Path(__file__).resolve().parent.parent
GRAMMAR_YAML = SERVER_ROOT / "prompts" / "examples" / "grammar.yaml"
SEED_OUTPUT = SERVER_ROOT / "data" / "seed" / "grammar_seed_v1.jsonl"


def extract_grammar_tags(label: str, output_type: str) -> list[str]:
    from app.eval_adapter.example_lab import _rule_extract_grammar_tags, normalize_grammar_tags

    tags = _rule_extract_grammar_tags(label, output_type)
    return normalize_grammar_tags(tags)


def build_retrieval_text(
    output_type: str,
    variant: str,
    grammar_tags: list[str],
    sentence: str,
    label: str,
    explanation: str,
) -> str:
    lines = [
        f"variant: {variant}",
        f"output_type: {output_type}",
        f"grammar_tags: {', '.join(grammar_tags)}",
        f"label: {label}",
        f"source_sentence: {sentence}",
        f"explanation: {explanation}",
    ]
    return "\n".join(lines)


def main() -> None:
    sys.path.insert(0, str(SERVER_ROOT))

    data = yaml.safe_load(GRAMMAR_YAML.read_text(encoding="utf-8"))

    SEED_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for variant, entries in data.items():
        if not isinstance(entries, list):
            continue

        for i, entry in enumerate(entries):
            frag = entry.get("output_fragment", "")
            obj = json.loads(frag)
            output_type = obj.get("type", "")
            label = obj.get("label", "")
            sentence = entry.get("sentence_text", "")

            if output_type == "grammar_note":
                explanation = obj.get("note_zh", "")
            elif output_type == "sentence_analysis":
                explanation = obj.get("analysis_zh", "")
            else:
                explanation = ""

            example_id = f"grammar-{variant}-{i:03d}"
            grammar_tags = extract_grammar_tags(label, output_type)
            retrieval_text = build_retrieval_text(
                output_type=output_type,
                variant=variant,
                grammar_tags=grammar_tags,
                sentence=sentence,
                label=label,
                explanation=explanation,
            )

            record = {
                "example_id": example_id,
                "output_type": output_type,
                "variant": variant,
                "tags": grammar_tags,
                "retrieval_text": retrieval_text,
                "source_sentence": sentence,
                "output_fragment": frag,
                "label": label,
                "quality_score": 1.0,
                "approved": True,
            }
            records.append(record)

    with open(SEED_OUTPUT, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Generated {len(records)} seed records to {SEED_OUTPUT}")

    type_counts = {}
    tag_counts = {}
    for rec in records:
        t = rec["output_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
        for tag in rec["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    print(f"\nOutput type distribution:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")
    print(f"\nTag distribution:")
    for t, c in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
