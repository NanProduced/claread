"""Fix ASCII double quotes used as Chinese quotation marks in grammar.yaml.

Strategy: Parse YAML, for each output_fragment that fails JSON parsing,
use a state-machine to identify and replace unescaped " inside JSON string values.
Properly pairs Chinese opening (\u201c) and closing (\u201d) quotes.
"""

import json
from pathlib import Path

import yaml

GRAMMAR_YAML = Path(__file__).resolve().parent.parent / "prompts" / "examples" / "grammar.yaml"


def fix_json_string(text: str) -> str:
    """Fix unescaped ASCII double quotes in a JSON string.

    Walks through the text tracking JSON structure. When inside a string value,
    any " that is not a proper string delimiter gets replaced with Chinese quotes.
    Tracks Chinese quote pairing to alternate between \u201c and \u201d.
    """
    result = []
    i = 0
    n = len(text)
    chinese_quote_open = False

    while i < n:
        c = text[i]

        if c == '"':
            i += 1
            result.append('"')

            while i < n:
                c2 = text[i]

                if c2 == '\\' and i + 1 < n:
                    result.append(c2)
                    result.append(text[i + 1])
                    i += 2
                    continue

                if c2 == '"':
                    prev = text[i - 1] if i > 0 else ''
                    nxt = text[i + 1] if i + 1 < n else ''

                    if _is_json_closing_quote(prev, nxt, text, i):
                        result.append('"')
                        i += 1
                        chinese_quote_open = False
                        break
                    else:
                        if not chinese_quote_open:
                            result.append('\u201c')
                            chinese_quote_open = True
                        else:
                            result.append('\u201d')
                            chinese_quote_open = False
                        i += 1
                        continue

                result.append(c2)
                i += 1
            continue

        result.append(c)
        i += 1

    return ''.join(result)


def _is_json_closing_quote(prev: str, nxt: str, text: str, pos: int) -> bool:
    if nxt == ':':
        return True
    if nxt == ',':
        return True
    if nxt == '}':
        return True
    if nxt == ']':
        return True
    if nxt == ' ':
        after_space = pos + 1
        while after_space < len(text) and text[after_space] == ' ':
            after_space += 1
        if after_space < len(text) and text[after_space] in ':,}]':
            return True
    if nxt == '\n':
        return True
    if nxt == '':
        return True
    return False


def main() -> None:
    raw = GRAMMAR_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    total = 0
    fixed_count = 0
    still_broken = []

    for variant, entries in data.items():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            total += 1
            frag = entry.get("output_fragment", "")

            try:
                json.loads(frag)
                continue
            except json.JSONDecodeError:
                pass

            fixed = fix_json_string(frag)
            try:
                obj = json.loads(fixed)
                entry["output_fragment"] = json.dumps(obj, ensure_ascii=False)
                fixed_count += 1
                print(f"  FIXED {variant}[{i}]")
            except json.JSONDecodeError as e:
                key = f"{variant}[{i}]"
                still_broken.append(f"{key}: {e}")
                print(f"  STILL BROKEN {key}: {e}")

    if still_broken:
        print(f"\n{len(still_broken)} entries still broken after automated fix.")

    print(f"\nFixed {fixed_count}/{total} entries. {len(still_broken)} still broken.")

    new_raw = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    GRAMMAR_YAML.write_text(new_raw, encoding="utf-8")
    print("File written.")


if __name__ == "__main__":
    main()
