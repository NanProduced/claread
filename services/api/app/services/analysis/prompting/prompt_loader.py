"""YAML-based prompt loader with version tracking."""

from __future__ import annotations

from pathlib import Path

import yaml
from cachetools import TTLCache

PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "prompts"

_REGISTRY_PATH = PROMPTS_ROOT / "registry.yaml"

_REGISTRY_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)
_YAML_FILE_CACHE: TTLCache = TTLCache(maxsize=64, ttl=300)


def _load_registry() -> dict:
    cached = _REGISTRY_CACHE.get("registry")
    if cached is not None:
        return cached
    text = _REGISTRY_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict in registry, got {type(data).__name__}")
    _REGISTRY_CACHE["registry"] = data
    return data


def _load_yaml_file(relative_path: str) -> dict:
    cached = _YAML_FILE_CACHE.get(relative_path)
    if cached is not None:
        return cached
    path = PROMPTS_ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict in {path}, got {type(data).__name__}")
    _YAML_FILE_CACHE[relative_path] = data
    return data


def get_prompt_version() -> str:
    return _load_registry().get("version", "unknown")


def load_agent_instructions(agent_name: str) -> str:
    registry = _load_registry()
    agents = registry.get("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Agent '{agent_name}' not found in prompt registry")
    entry = agents[agent_name]
    data = _load_yaml_file(entry["file"])
    content = data.get("content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def load_policy_lines(
    policy_name: str,
    focus: str,
    variant: str | None = None,
) -> list[str]:
    data = _load_yaml_file(f"policies/{policy_name}.yaml")
    focus_data = data.get(focus)
    if focus_data is None:
        return []
    if isinstance(focus_data, list):
        return focus_data
    if not isinstance(focus_data, dict):
        return [str(focus_data)]
    if variant and variant in focus_data:
        lines = focus_data[variant]
    else:
        lines = focus_data.get("default", [])
    return lines if isinstance(lines, list) else [lines]


def load_examples(
    example_name: str,
    variant: str,
) -> list[dict]:
    data = _load_yaml_file(f"examples/{example_name}.yaml")
    if variant in data:
        entries = data[variant]
    else:
        entries = data.get("default", [])
    return entries if isinstance(entries, list) else [entries]


def clear_cache() -> None:
    _REGISTRY_CACHE.clear()
    _YAML_FILE_CACHE.clear()
