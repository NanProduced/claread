from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FewShotMode = Literal["off", "baseline", "variant", "settings"]


class PromptRuntimeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str
    target: Literal["article_analysis"] = "article_analysis"
    description: str = ""
    few_shot_mode: FewShotMode = "settings"
    instructions: dict[str, str] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    examples: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshot_hash: str | None = None

_GRAMMAR_RAG_ENABLED_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "grammar_rag_enabled_override",
    default=None,
)
_PROMPT_RUNTIME_OVERRIDE: ContextVar[PromptRuntimeOverride | None] = ContextVar(
    "prompt_runtime_override",
    default=None,
)


def is_grammar_rag_enabled(settings: Any) -> bool:
    override = _GRAMMAR_RAG_ENABLED_OVERRIDE.get()
    if override is not None:
        return override
    return bool(getattr(settings, "grammar_rag_enabled", False))


def get_prompt_runtime_override() -> PromptRuntimeOverride | None:
    return _PROMPT_RUNTIME_OVERRIDE.get()


def is_prompt_override_active() -> bool:
    return get_prompt_runtime_override() is not None


def resolve_few_shot_mode(default_mode: str) -> str:
    override = get_prompt_runtime_override()
    if override is None or override.few_shot_mode == "settings":
        return default_mode
    return override.few_shot_mode


def get_prompt_override_policy_lines(
    policy_name: str,
    focus: str,
    variant: str | None = None,
) -> list[str] | None:
    override = get_prompt_runtime_override()
    if override is None:
        return None
    policy_data = override.policies.get(policy_name)
    if not isinstance(policy_data, dict):
        return None
    focus_data = policy_data.get(focus)
    if focus_data is None:
        return None
    return _resolve_variant_lines(focus_data, variant)


def get_prompt_override_agent_instructions(agent_name: str) -> str | None:
    override = get_prompt_runtime_override()
    if override is None:
        return None
    value = override.instructions.get(agent_name)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def get_prompt_override_examples(example_name: str, variant: str) -> list[dict[str, Any]]:
    override = get_prompt_runtime_override()
    if override is None or override.few_shot_mode != "variant":
        return []
    example_data = override.examples.get(example_name)
    if not isinstance(example_data, dict):
        return []
    raw_entries = example_data.get(variant, [])
    if isinstance(raw_entries, list):
        return [entry for entry in raw_entries if isinstance(entry, dict)]
    if isinstance(raw_entries, dict):
        return [raw_entries]
    return []


def _resolve_variant_lines(value: Any, variant: str | None) -> list[str]:
    if isinstance(value, list):
        return [str(line) for line in value]
    if not isinstance(value, dict):
        return [str(value)]
    if variant and variant in value:
        lines = value[variant]
    else:
        lines = []
    if isinstance(lines, list):
        return [str(line) for line in lines]
    return [str(lines)]


@contextmanager
def grammar_rag_enabled_override(enabled: bool | None) -> Iterator[None]:
    token = _GRAMMAR_RAG_ENABLED_OVERRIDE.set(enabled)
    try:
        yield
    finally:
        _GRAMMAR_RAG_ENABLED_OVERRIDE.reset(token)


@contextmanager
def prompt_runtime_override(override: PromptRuntimeOverride | None) -> Iterator[None]:
    token = _PROMPT_RUNTIME_OVERRIDE.set(override)
    try:
        yield
    finally:
        _PROMPT_RUNTIME_OVERRIDE.reset(token)
