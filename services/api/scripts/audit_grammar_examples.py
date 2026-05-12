"""RAG-05: Static grammar examples audit script.

Validates that grammar.yaml output_fragment entries:
1. Are valid JSON
2. type field only contains grammar_note / sentence_analysis
3. Required fields present per type
4. spans/chunks text matches sentence_text
"""

import json
from pathlib import Path

import yaml

GRAMMAR_YAML = Path(__file__).resolve().parent.parent / "prompts" / "examples" / "grammar.yaml"

VALID_TYPES = {"grammar_note", "sentence_analysis"}


def main() -> None:
    data = yaml.safe_load(GRAMMAR_YAML.read_text(encoding="utf-8"))

    errors: list[str] = []
    warnings: list[str] = []
    total = 0

    for variant, entries in data.items():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            total += 1
            key = f"{variant}[{i}]"

            et = entry.get("example_type", "")
            if et not in ("grammar", "sentence_analysis"):
                errors.append(f"{key}: example_type={et!r} (expected grammar or sentence_analysis)")

            frag = entry.get("output_fragment", "")
            try:
                obj = json.loads(frag)
            except json.JSONDecodeError as e:
                errors.append(f"{key}: output_fragment is not valid JSON: {e}")
                continue

            if not isinstance(obj, dict):
                errors.append(f"{key}: output_fragment parsed to {type(obj).__name__}, expected dict")
                continue

            obj_type = obj.get("type", "")
            if obj_type not in VALID_TYPES:
                errors.append(f"{key}: output_fragment.type={obj_type!r} (expected one of {VALID_TYPES})")

            if et == "grammar" and obj_type != "grammar_note":
                warnings.append(f"{key}: example_type=grammar but output_fragment.type={obj_type!r} (expected grammar_note)")
            if et == "sentence_analysis" and obj_type != "sentence_analysis":
                warnings.append(f"{key}: example_type=sentence_analysis but output_fragment.type={obj_type!r}")

            sentence = entry.get("sentence_text", "")

            if obj_type == "grammar_note":
                for field in ("spans", "label", "note_zh"):
                    if field not in obj:
                        errors.append(f"{key}: grammar_note missing required field: {field}")
                if "spans" in obj:
                    for j, span in enumerate(obj["spans"]):
                        if "text" not in span:
                            errors.append(f"{key}: spans[{j}] missing text")
                        elif span["text"] and span["text"] not in sentence:
                            warnings.append(f"{key}: spans[{j}].text not found in sentence_text")

            elif obj_type == "sentence_analysis":
                for field in ("label", "analysis_zh", "chunks"):
                    if field not in obj:
                        errors.append(f"{key}: sentence_analysis missing required field: {field}")
                if "chunks" in obj:
                    for j, chunk in enumerate(obj["chunks"]):
                        if "text" not in chunk:
                            errors.append(f"{key}: chunks[{j}] missing text")
                        elif "order" not in chunk:
                            errors.append(f"{key}: chunks[{j}] missing order")
                        elif chunk["text"] and chunk["text"] not in sentence:
                            warnings.append(f"{key}: chunks[{j}].text not found in sentence_text")

    print(f"Total examples audited: {total}")
    print(f"Errors: {len(errors)}")
    for e in errors:
        print(f"  ERROR: {e}")
    print(f"Warnings: {len(warnings)}")
    for w in warnings:
        print(f"  WARN: {w}")
    if not errors and not warnings:
        print("ALL CHECKS PASSED")

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
