"""Repair agent for V3 workflow.

在 normalize_and_ground 失败时触发。
职责：修复结构性问题，不新增语义标注。

可修复范围：
- sentence_id
- anchor_text
- 补齐缺失字段
- 修正枚举值与结构格式
- 删除无效项

不可做的事：
- 凭空新增新的语义标注点
- 改写原有标注意图
- 重做全文分析
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.normalized import NormalizedAnnotationResult
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


@dataclass
class RepairAgentDeps:
    """Repair agent 依赖。"""
    sentences: list[dict[str, object]]
    original_drafts: dict[str, object]  # 原始 drafts 引用，用于修复时参考


def build_repair_prompt(
    deps: RepairAgentDeps,
    error_context: str,
) -> str:
    import json

    sentence_lines = [
        f"{sentence['sentence_id']}: {sentence['text']}"
        for sentence in deps.sentences
    ]

    # 包含原始 drafts 供修复参考
    vocab_draft_str = json.dumps(
        deps.original_drafts.get("vocabulary_draft", {}), ensure_ascii=False, indent=2
    )
    grammar_draft_str = json.dumps(
        deps.original_drafts.get("grammar_draft", {}), ensure_ascii=False, indent=2
    )
    translation_draft_str = json.dumps(
        deps.original_drafts.get("translation_draft", {}), ensure_ascii=False, indent=2
    )

    return "\n".join(
        [
            "句子列表：",
            *sentence_lines,
            "",
            "错误上下文：",
            error_context,
            "",
            "原始 Vocabulary Draft：",
            vocab_draft_str,
            "",
            "原始 Grammar Draft：",
            grammar_draft_str,
            "",
            "原始 Translation Draft：",
            translation_draft_str,
        ]
    )


@lru_cache(maxsize=1)
def get_repair_agent() -> Agent[RepairAgentDeps, NormalizedAnnotationResult]:
    return Agent[RepairAgentDeps, NormalizedAnnotationResult](
        model=None,
        output_type=NormalizedAnnotationResult,
        deps_type=RepairAgentDeps,
        instructions=load_agent_instructions("repair"),
        name="repair_agent",
        retries=1,
        output_retries=1,
        instrument=False,
    )
