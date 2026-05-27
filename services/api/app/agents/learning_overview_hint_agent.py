from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.overview_hint import LearningOverviewHintDraft
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


@dataclass
class LearningOverviewHintAgentDeps:
    source_text: str
    reading_goal: str
    reading_variant: str
    sentence_texts: list[str] = field(default_factory=list)
    translations: list[str] = field(default_factory=list)
    sentence_entries: list[dict[str, str]] = field(default_factory=list)


def build_learning_overview_hint_prompt(deps: LearningOverviewHintAgentDeps) -> str:
    parts = [
        "请判断下面的文本是否适合生成一个供 Ask Claread 决策使用的轻量 overview hint。",
        "",
        f"reading_goal: {deps.reading_goal}",
        f"reading_variant: {deps.reading_variant}",
        "",
        "原文：",
        deps.source_text.strip(),
    ]
    if deps.sentence_texts:
        preview = "\n".join(f"- {text}" for text in deps.sentence_texts[:6] if text.strip())
        if preview:
            parts.extend(["", "句子预览：", preview])
    if deps.sentence_entries:
        entry_lines = []
        for item in deps.sentence_entries[:4]:
            label = str(item.get("label") or item.get("title") or item.get("entry_type") or "").strip()
            content = str(item.get("content") or "").strip()
            if not label and not content:
                continue
            entry_lines.append(f"- {label}: {content[:160]}".rstrip(": "))
        if entry_lines:
            parts.extend(["", "现有句尾解析（仅供弱参考）：", *entry_lines])
    return "\n".join(parts).strip()


@lru_cache(maxsize=1)
def get_learning_overview_hint_agent() -> Agent[LearningOverviewHintAgentDeps, LearningOverviewHintDraft]:
    return Agent[LearningOverviewHintAgentDeps, LearningOverviewHintDraft](
        model=None,
        output_type=LearningOverviewHintDraft,
        deps_type=LearningOverviewHintAgentDeps,
        instructions=load_agent_instructions("learning_overview_hint"),
        name="learning_overview_hint_agent",
        retries=2,
        output_retries=2,
        instrument=False,
    )
