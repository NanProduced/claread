"""RAG-06: Generate first-version grammar seed JSONL.

Reads grammar.yaml examples, enriches each with grammar_tags, structure_signals,
and retrieval_text, then writes a JSONL seed file.
"""

import json
import re
from pathlib import Path

import yaml

GRAMMAR_YAML = Path(__file__).resolve().parent.parent / "prompts" / "examples" / "grammar.yaml"
SEED_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "seed" / "grammar_seed_v1.jsonl"

VARIANT_TEACHING_GOAL = {
    "beginner_reading": "explicit_split",
    "default": "explicit_exam",
    "intensive_reading": "balanced",
    "gaokao": "explicit_exam",
    "cet": "speed_support",
    "kaoyan": "structural",
    "tem": "rhetorical",
    "ielts_toefl": "info_extraction",
}

LABEL_TAG_MAP = {
    "定语从句": ["relative_clause"],
    "非限制性定语从句": ["nonrestrictive_relative_clause"],
    "宾语从句": ["object_clause"],
    "同位语从句": ["appositive_clause"],
    "过去分词作状语": ["participle_adverbial"],
    "过去分词后置定语": ["participle_attribute"],
    "倒装": ["inversion"],
    "倒装结构": ["inversion"],
    "被动语态": ["passive_voice"],
    "反复": ["parallelism"],
    "插入语": ["main_clause_interruption"],
    "虚拟条件句倒装": ["inversion"],
    "虚拟倒装": ["inversion"],
    "限制性定语从句": ["relative_clause"],
    "介词+关系代词": ["relative_clause"],
    "动词并列": ["parallelism"],
    "明喻": ["parallelism"],
    "让步": ["nested_clause"],
    "转折": ["nested_clause"],
    "分词结果状语": ["participle_adverbial"],
    "名词性从句": ["object_clause"],
    "否定副词前置": ["inversion"],
    "give up": ["nonfinite"],
    "not only": ["inversion", "parallelism"],
}


def extract_grammar_tags(label: str, output_type: str) -> list[str]:
    tags = set()
    for pattern, tag_list in LABEL_TAG_MAP.items():
        if pattern in label:
            tags.update(tag_list)
    if output_type == "sentence_analysis":
        if "从句" in label:
            tags.add("nested_clause")
        if "定语从句" in label or "宾语从句" in label:
            tags.add("nested_clause")
    if not tags:
        tags.add("general")
    return sorted(tags)


def extract_structure_signals(sentence: str, label: str) -> list[str]:
    signals = set()
    words = sentence.split()
    if len(words) > 20:
        signals.add("long_sentence")

    if re.match(r"^[A-Za-z]+ed\b", sentence):
        signals.add("leading_vbn")
    if re.match(r"^[A-Za-z]+ing\b", sentence):
        signals.add("leading_ving")

    if re.search(r"\bthat\b", sentence, re.IGNORECASE):
        signals.add("has_that_clause")
    if re.search(r"\bwhich\b", sentence, re.IGNORECASE):
        signals.add("has_wh_clause")
    if re.search(r"\bwho\b", sentence, re.IGNORECASE):
        signals.add("has_wh_clause")

    comma_count = sentence.count(",")
    if comma_count >= 2:
        signals.add("has_comma_insertion")

    if re.search(r",\s*(?:which|who|whose|whom)\b", sentence):
        signals.add("has_comma_insertion")

    if re.search(r"\bNever\b|\bRarely\b|\bNot only\b|\bHad\b\s+\w+\s+\w+\b", sentence):
        signals.add("has_inversion")

    if "插入语" in label or "插入" in label:
        signals.add("has_comma_insertion")

    if "从句" in label and ("从句" in label.replace("定语从句", "").replace("宾语从句", "") or label.count("从句") > 1):
        signals.add("nested_structure")

    if not signals:
        signals.add("local_structure")

    return sorted(signals)


def build_retrieval_text(
    output_type: str,
    variant: str,
    grammar_tags: list[str],
    signals: list[str],
    teaching_goal: str,
    sentence: str,
    label: str,
) -> str:
    lines = [
        f"output_type={output_type}",
        f"variant={variant}",
        f"grammar_tags={', '.join(grammar_tags)}",
        f"signals={', '.join(signals)}",
        f"teaching_goal={teaching_goal}",
        f"sentence={sentence}",
        f"label={label}",
    ]
    return "\n".join(lines)


def main() -> None:
    data = yaml.safe_load(GRAMMAR_YAML.read_text(encoding="utf-8"))

    SEED_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for variant, entries in data.items():
        if not isinstance(entries, list):
            continue
        teaching_goal = VARIANT_TEACHING_GOAL.get(variant, "balanced")

        for i, entry in enumerate(entries):
            frag = entry.get("output_fragment", "")
            obj = json.loads(frag)
            output_type = obj.get("type", "")
            label = obj.get("label", "")
            sentence = entry.get("sentence_text", "")

            example_id = f"grammar-{variant}-{i:03d}"
            grammar_tags = extract_grammar_tags(label, output_type)
            structure_signals = extract_structure_signals(sentence, label)
            retrieval_text = build_retrieval_text(
                output_type=output_type,
                variant=variant,
                grammar_tags=grammar_tags,
                signals=structure_signals,
                teaching_goal=teaching_goal,
                sentence=sentence,
                label=label,
            )

            record = {
                "example_id": example_id,
                "output_type": output_type,
                "variant": variant,
                "tags": grammar_tags,
                "signals": structure_signals,
                "retrieval_text": retrieval_text,
                "source_sentence": sentence,
                "output_fragment": frag,
                "label": label,
                "teaching_goal": teaching_goal,
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
