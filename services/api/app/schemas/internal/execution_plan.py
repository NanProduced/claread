from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.internal.analysis import (
    GrammarGranularity,
    ReadingGoal,
    ReadingVariant,
    TranslationStyle,
    VocabularyPolicy,
)


class GoalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    annotation_density: int = Field(ge=1, description="控制每句最大标注数。")
    vocabulary_focus: VocabularyPolicy = Field(description="词汇筛选策略。")
    grammar_focus: GrammarGranularity = Field(description="语法侧重点。")
    translation_focus: TranslationStyle = Field(description="翻译侧重点。")


class AcademicGoalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    term_density: int = Field(default=5, ge=1, description="每句最大术语标注数")
    logic_density: int = Field(default=2, ge=1, description="每句最大逻辑标注数")
    interpretation_density: int = Field(default=1, ge=0, description="每句最大解释标注数")
    require_paragraph_role: bool = False
    require_content_summary: bool = False
    translation_rigor: Literal["research_reading"] = "research_reading"


class GoalExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal_id: ReadingGoal = Field(description="阅读目标。")
    variant_id: ReadingVariant = Field(description="阅读变体。")
    topology_mode: Literal["learning", "academic"] = Field(description="工作流拓扑模式。")
    output_mode: Literal["learning_scene", "academic_scene"] = Field(description="输出渲染模式。")
    prompt_profile: str = Field(description="对应旧的 profile_id。")
    few_shot_mode: Literal["baseline", "manual", "rag"] = Field(default="baseline", description="Few-shot 策略。")
    policy: GoalPolicy = Field(description="后处理硬策略。")
    academic_policy: AcademicGoalPolicy | None = Field(default=None, description="academic 模式后处理硬策略。")
