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
from app.schemas.internal.repair import RepairPatchRequest, RepairPatchResult
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.runtime_context import is_prompt_override_active


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


def _build_repair_agent() -> Agent[RepairAgentDeps, NormalizedAnnotationResult]:
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


@lru_cache(maxsize=1)
def _get_cached_repair_agent() -> Agent[RepairAgentDeps, NormalizedAnnotationResult]:
    return _build_repair_agent()


def get_repair_agent() -> Agent[RepairAgentDeps, NormalizedAnnotationResult]:
    if is_prompt_override_active():
        return _build_repair_agent()
    return _get_cached_repair_agent()


# ── Item-level Repair Patch ─────────────────────────────────────


@dataclass
class RepairPatchDeps:
    """Item-level repair agent 依赖。"""
    patch_request: RepairPatchRequest  # from app.schemas.internal.repair


def build_repair_patch_prompt(deps: RepairPatchDeps) -> str:
    import json

    sentence_lines = [
        f"  {s['sentence_id']}: {s['text']}"
        for s in deps.patch_request.sentences
    ]

    target_lines: list[str] = []
    for idx, target in enumerate(deps.patch_request.targets):
        target_lines.append(f"Target {idx}:")
        target_lines.append(f"  类型: {target.annotation_type}")
        target_lines.append(f"  句子: {target.sentence_id}")
        target_lines.append(f"  锚定文本: {target.anchor_text}")
        target_lines.append(f"  删除原因: {target.drop_reason}")
        target_lines.append(f"  删除阶段: {target.drop_stage}")
        target_lines.append(f"  来源: {target.source_agent} (canonical={target.is_canonical})")
        if target.draft_payload is not None:
            target_lines.append("  原始 draft:")
            target_lines.append(json.dumps(target.draft_payload, ensure_ascii=False, indent=4))

    patch_instructions = load_agent_instructions("repair", section="patch")

    return "\n".join([
        patch_instructions,
        "",
        "句子列表：",
        *sentence_lines,
        "",
        "需要修复的 targets：",
        *target_lines,
    ])


def _build_repair_patch_agent() -> Agent[RepairPatchDeps, RepairPatchResult]:
    return Agent[RepairPatchDeps, RepairPatchResult](
        model=None,
        output_type=RepairPatchResult,
        deps_type=RepairPatchDeps,
        instructions=load_agent_instructions("repair", section="patch"),
        name="repair_patch_agent",
        retries=1,
        output_retries=1,
        instrument=False,
    )


@lru_cache(maxsize=1)
def _get_cached_repair_patch_agent() -> Agent[RepairPatchDeps, RepairPatchResult]:
    return _build_repair_patch_agent()


def get_repair_patch_agent() -> Agent[RepairPatchDeps, RepairPatchResult]:
    if is_prompt_override_active():
        return _build_repair_patch_agent()
    return _get_cached_repair_patch_agent()
