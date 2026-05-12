from __future__ import annotations

from app.schemas.internal.analysis import ReadingGoal, ReadingVariant
from app.schemas.internal.execution_plan import AcademicGoalPolicy, GoalExecutionPlan, GoalPolicy


def build_goal_execution_plan(reading_goal: ReadingGoal, reading_variant: ReadingVariant) -> GoalExecutionPlan:
    """把请求场景映射为包含拓扑策略和密度策略的 ExecutionPlan。"""

    if reading_goal == "exam":
        if reading_variant == "gaokao":
            policy = GoalPolicy(
                annotation_density=6,
                vocabulary_focus="exam_priority",
                grammar_focus="explicit_exam",
                translation_focus="literal_support",
            )
            return GoalExecutionPlan(
                goal_id=reading_goal,
                variant_id=reading_variant,
                topology_mode="learning",
                output_mode="learning_scene",
                prompt_profile="exam_gaokao",
                policy=policy,
            )
        if reading_variant == "cet":
            policy = GoalPolicy(
                annotation_density=6,
                vocabulary_focus="exam_priority",
                grammar_focus="speed_support",
                translation_focus="natural",
            )
            return GoalExecutionPlan(
                goal_id=reading_goal,
                variant_id=reading_variant,
                topology_mode="learning",
                output_mode="learning_scene",
                prompt_profile="exam_cet",
                policy=policy,
            )
        if reading_variant == "kaoyan":
            policy = GoalPolicy(
                annotation_density=5,
                vocabulary_focus="exam_depth",
                grammar_focus="structural",
                translation_focus="academic",
            )
            return GoalExecutionPlan(
                goal_id=reading_goal,
                variant_id=reading_variant,
                topology_mode="learning",
                output_mode="learning_scene",
                prompt_profile="exam_kaoyan",
                policy=policy,
            )
        if reading_variant == "tem":
            policy = GoalPolicy(
                annotation_density=5,
                vocabulary_focus="literary_depth",
                grammar_focus="rhetorical",
                translation_focus="nuanced_aesthetic",
            )
            return GoalExecutionPlan(
                goal_id=reading_goal,
                variant_id=reading_variant,
                topology_mode="learning",
                output_mode="learning_scene",
                prompt_profile="exam_tem",
                policy=policy,
            )
        if reading_variant == "ielts_toefl":
            policy = GoalPolicy(
                annotation_density=6,
                vocabulary_focus="academic_strategy",
                grammar_focus="info_extraction",
                translation_focus="natural",
            )
            return GoalExecutionPlan(
                goal_id=reading_goal,
                variant_id=reading_variant,
                topology_mode="learning",
                output_mode="learning_scene",
                prompt_profile="exam_ielts_toefl",
                policy=policy,
            )

        policy = GoalPolicy(
            annotation_density=4,
            vocabulary_focus="exam_priority",
            grammar_focus="focused",
            translation_focus="exam",
        )
        return GoalExecutionPlan(
            goal_id=reading_goal,
            variant_id=reading_variant,
            topology_mode="learning",
            output_mode="learning_scene",
            prompt_profile=f"exam_{reading_variant}",
            policy=policy,
        )

    if reading_goal == "academic":
        policy = GoalPolicy(
            annotation_density=4,
            vocabulary_focus="academic_priority",
            grammar_focus="structural",
            translation_focus="academic",
        )
        academic_policy = AcademicGoalPolicy(
            term_density=5,
            logic_density=2,
            interpretation_density=1,
            require_paragraph_role=False,
            require_content_summary=False,
        )
        return GoalExecutionPlan(
            goal_id=reading_goal,
            variant_id=reading_variant,
            topology_mode="academic",
            output_mode="academic_scene",
            prompt_profile="academic_general",
            policy=policy,
            academic_policy=academic_policy,
        )

    variant_map: dict[str, tuple[str, str, str, str, int]] = {
        "beginner_reading": ("daily_beginner", "explicit_split", "high_value_only", "literal_support", 6),
        "intermediate_reading": ("daily_intermediate", "balanced", "high_value_only", "natural", 4),
        "intensive_reading": ("daily_intensive", "structural_logic", "semantic_nuance", "nuanced_aesthetic", 3),
    }
    profile_id, grammar_granularity, vocabulary_policy, translation_style, density = variant_map[reading_variant]

    policy = GoalPolicy(
        annotation_density=density,
        vocabulary_focus=vocabulary_policy,
        grammar_focus=grammar_granularity,
        translation_focus=translation_style,
    )
    return GoalExecutionPlan(
        goal_id=reading_goal,
        variant_id=reading_variant,
        topology_mode="learning",
        output_mode="learning_scene",
        prompt_profile=profile_id,
        policy=policy,
    )
