"""Prompt strategy for V3 workflow.

负责为各 agent 构建 runtime prompt strategy。
设计原则：
- node 不直接拼零散 prompt 片段
- agent 通过统一 strategy builder 获取 prompt 和 examples
- baseline 配置尽量短，尽量少 few-shot
- runtime prompt 采用可替换 section 组装，便于后续 profile 差异化
- 差异化的核心是"用户需要什么"，而非"怎么限制 LLM 输出"
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.services.analysis.planning.goal_views import (
    get_annotation_style,
)
from app.services.analysis.prompting.prompt_composer import PromptSection
from app.services.analysis.prompting.prompt_loader import load_policy_lines


@dataclass
class PromptStrategy:
    """Prompt 策略。"""

    profile_id: str
    reading_goal: str
    reading_variant: str
    annotation_style: str | None = None
    translation_style: str | None = None
    grammar_granularity: str | None = None
    vocabulary_policy: str | None = None
    policy_lines: tuple[str, ...] = ()
    extra_instructions: tuple[str, ...] = ()
    extra_sections: tuple[PromptSection, ...] = ()


def build_prompt_sections(strategy: PromptStrategy) -> tuple[PromptSection, ...]:
    """Convert strategy metadata into replaceable runtime sections."""

    profile_lines = [
        f"profile_id: {strategy.profile_id}",
        f"reading_goal: {strategy.reading_goal}",
        f"reading_variant: {strategy.reading_variant}",
    ]
    if strategy.annotation_style:
        profile_lines.append(f"annotation_style: {strategy.annotation_style}")
    if strategy.translation_style:
        profile_lines.append(f"translation_style: {strategy.translation_style}")
    if strategy.grammar_granularity:
        profile_lines.append(f"grammar_granularity: {strategy.grammar_granularity}")
    if strategy.vocabulary_policy:
        profile_lines.append(f"vocabulary_policy: {strategy.vocabulary_policy}")

    sections: list[PromptSection] = [PromptSection("profile", tuple(profile_lines))]
    if strategy.policy_lines:
        sections.append(PromptSection("policy", strategy.policy_lines))
    if strategy.extra_instructions:
        sections.append(
            PromptSection("runtime_constraints", strategy.extra_instructions)
        )
    sections.extend(strategy.extra_sections)
    return tuple(sections)


def build_vocabulary_prompt_strategy(plan: GoalExecutionPlan) -> PromptStrategy:
    """构建 vocabulary agent 的 prompt 策略。"""

    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        vocabulary_policy=plan.policy.vocabulary_focus,
        annotation_style=get_annotation_style(plan),
        policy_lines=tuple(
            load_policy_lines("vocabulary", plan.policy.vocabulary_focus, plan.variant_id)
        ),
    )


def build_grammar_prompt_strategy(plan: GoalExecutionPlan) -> PromptStrategy:
    """构建 grammar agent 的 prompt 策略。"""

    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        grammar_granularity=plan.policy.grammar_focus,
        annotation_style=get_annotation_style(plan),
        policy_lines=tuple(
            load_policy_lines("grammar", plan.policy.grammar_focus, plan.variant_id)
        ),
    )


def build_translation_prompt_strategy(plan: GoalExecutionPlan) -> PromptStrategy:
    """构建 translation agent 的 prompt 策略。"""

    return PromptStrategy(
        profile_id=plan.prompt_profile,
        reading_goal=plan.goal_id,
        reading_variant=plan.variant_id,
        translation_style=plan.policy.translation_focus,
        policy_lines=tuple(
            load_policy_lines("translation", plan.policy.translation_focus, plan.variant_id)
        ),
    )


def build_repair_prompt_strategy(error_context: str) -> PromptStrategy:
    """构建 repair agent 的 prompt 策略。"""

    return PromptStrategy(
        profile_id="repair",
        reading_goal="repair",
        reading_variant="repair",
        extra_instructions=(error_context,),
    )
