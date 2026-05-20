from __future__ import annotations

from pathlib import Path

import yaml
from cachetools import TTLCache

_PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "prompts" / "reader_ask"
_CACHE: TTLCache[str, dict[str, str]] = TTLCache(maxsize=1, ttl=300)


def _load_layer(name: str) -> str:
    cache_key = f"layer:{name}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached["content"]
    data = yaml.safe_load((_PROMPTS_ROOT / f"{name}.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"reader_ask/{name}.yaml must contain a mapping")
    content = data.get("content", "")
    text = content.strip() if isinstance(content, str) else str(content).strip()
    _CACHE[cache_key] = {"content": text}
    return text


def load_prompt_layers() -> dict[str, str]:
    return {
        "system": _load_layer("system"),
        "planner": _load_layer("planner"),
        "answer": _load_layer("answer"),
        "schema": _load_layer("schema"),
        "policy_examples": _load_layer("policy_examples"),
    }
