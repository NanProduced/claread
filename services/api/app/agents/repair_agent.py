"""Repair agent for V3 workflow.

在 normalize_and_ground 失败时触发。
职责：修复结构性问题，不新增语义标注。

只使用 item-level patch repair。
旧 full-result repair（RepairAgentDeps / build_repair_prompt / get_repair_agent）已移除。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.repair import RepairPatchRequest, RepairPatchResult
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.runtime_context import is_prompt_override_active

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
