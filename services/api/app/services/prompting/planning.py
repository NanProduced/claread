"""Neutral planning-to-prompt presentation helpers."""

from __future__ import annotations

from app.schemas.internal.execution_plan import GoalExecutionPlan


def get_annotation_style(plan: GoalExecutionPlan) -> str:
    """Return the annotation style selected by a goal execution plan."""

    if plan.goal_id == "exam":
        if plan.variant_id == "gaokao":
            return "exam_gaokao"
        if plan.variant_id == "cet":
            return "exam_cet"
        if plan.variant_id == "kaoyan":
            return "exam_kaoyan"
        if plan.variant_id == "tem":
            return "exam_tem"
        if plan.variant_id == "ielts_toefl":
            return "exam_ielts_toefl"
        return "exam_oriented"
    if plan.goal_id == "academic":
        return "structural_and_academic"
    return "plain_and_supportive"
