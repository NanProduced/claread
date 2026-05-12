from __future__ import annotations

from typing import TypedDict

from app.schemas.analysis import AcademicRenderSceneModel, AnalyzeRequest, Warning
from app.schemas.internal.academic_drafts import (
    AcademicTranslationDraft,
    TermDraft,
    UnderstandingDraft,
)
from app.schemas.internal.academic_normalized import AcademicNormalizedResult
from app.schemas.internal.analysis import PreparedInput
from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.schemas.internal.normalized import DropLogEntry


class AcademicState(TypedDict, total=False):
    payload: AnalyzeRequest
    prepared_input: PreparedInput
    goal_execution_plan: GoalExecutionPlan

    term_draft: TermDraft | None
    translation_draft: AcademicTranslationDraft | None
    understanding_draft: UnderstandingDraft | None

    term_usage: dict[str, object] | None
    translation_usage: dict[str, object] | None
    understanding_usage: dict[str, object] | None
    usage_summary: dict[str, object] | None

    academic_normalized_result: AcademicNormalizedResult | None
    drop_log: list[DropLogEntry]

    render_scene: AcademicRenderSceneModel
    warnings: list[Warning]
    processing_warnings: list[Warning]
