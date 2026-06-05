"""RAG-05: Static grammar examples audit script.

Validates that grammar.yaml output_fragment entries conform to the few-shot
JSON contract per the RAG reconstruction design:

1. Are valid JSON
2. type field only contains grammar_note / sentence_analysis
3. example_type and output_fragment.type are consistent
4. Required fields present per type:
   - grammar_note: type, label, note_zh; spans is optional but must be array
   - sentence_analysis: type, label, analysis_zh; chunks is optional but must be array
5. spans/chunks text matches sentence_text
6. label consistency: output_fragment.label should match entry-level label if present
"""

import json
from pathlib import Path

import yaml

GRAMMAR_YAML = Path(__file__).resolve().parent.parent / "prompts" / "examples" / "grammar.yaml"

VALID_TYPES = {"grammar_note", "sentence_analysis"}

# example_type → expected output_fragment.type mapping
EXAMPLE_TYPE_TO_FRAGMENT_TYPE = {
    "grammar": "grammar_note",
    "sentence_analysis": "sentence_analysis",
}


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
            if et not in EXAMPLE_TYPE_TO_FRAGMENT_TYPE:
                errors.append(
                    f"{key}: example_type={et!r} "
                    f"(expected one of {set(EXAMPLE_TYPE_TO_FRAGMENT_TYPE)})"
                )

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

            # Check example_type ↔ output_fragment.type consistency
            expected_frag_type = EXAMPLE_TYPE_TO_FRAGMENT_TYPE.get(et)
            if expected_frag_type and obj_type != expected_frag_type:
                errors.append(
                    f"{key}: example_type={et!r} but output_fragment.type={obj_type!r} "
                    f"(expected {expected_frag_type!r})"
                )

            sentence = entry.get("sentence_text", "")
            entry_label = entry.get("label", "")

            if obj_type == "grammar_note":
                # Required fields
                for field in ("label", "note_zh"):
                    if field not in obj or not obj[field]:
                        errors.append(f"{key}: grammar_note missing required field: {field}")
                # Optional but must be array if present
                spans = obj.get("spans")
                if spans is not None:
                    if not isinstance(spans, list):
                        errors.append(f"{key}: grammar_note 'spans' must be an array")
                    else:
                        for j, span in enumerate(spans):
                            if "text" not in span:
                                errors.append(f"{key}: spans[{j}] missing text")
                            elif span["text"] and span["text"] not in sentence:
                                warnings.append(f"{key}: spans[{j}].text not found in sentence_text")

            elif obj_type == "sentence_analysis":
                # Required fields
                for field in ("label", "analysis_zh"):
                    if field not in obj or not obj[field]:
                        errors.append(f"{key}: sentence_analysis missing required field: {field}")
                # Optional but must be array if present
                chunks = obj.get("chunks")
                if chunks is not None:
                    if not isinstance(chunks, list):
                        errors.append(f"{key}: sentence_analysis 'chunks' must be an array")
                    else:
                        for j, chunk in enumerate(chunks):
                            if "text" not in chunk:
                                errors.append(f"{key}: chunks[{j}] missing text")
                            elif "order" not in chunk:
                                errors.append(f"{key}: chunks[{j}] missing order")
                            elif chunk["text"] and chunk["text"] not in sentence:
                                warnings.append(f"{key}: chunks[{j}].text not found in sentence_text")

            # Label consistency check
            frag_label = obj.get("label", "")
            if frag_label and entry_label and frag_label != entry_label:
                warnings.append(
                    f"{key}: output_fragment.label={frag_label!r} differs from entry label={entry_label!r}"
                )

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
