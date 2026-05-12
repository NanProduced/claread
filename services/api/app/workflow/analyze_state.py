from __future__ import annotations

from typing import TypedDict

from app.schemas.analysis import AnalyzeRequest, RenderSceneModel, Warning
from app.schemas.internal.analysis import PreparedInput
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult


class AnalyzeState(TypedDict, total=False):
    # Request & Input
    payload: AnalyzeRequest
    prepared_input: PreparedInput
    goal_execution_plan: GoalExecutionPlan

    # Parallel agent drafts
    vocabulary_draft: VocabularyDraft | None
    grammar_draft: GrammarDraft | None
    translation_draft: TranslationDraft | None
    vocabulary_usage: dict[str, object] | None
    grammar_usage: dict[str, object] | None
    translation_usage: dict[str, object] | None
    repair_usage: dict[str, object] | None
    usage_summary: dict[str, object] | None

    # Normalization result
    normalized_result: NormalizedAnnotationResult | None
    drop_log: list[DropLogEntry]  # Alias for normalized_result.drop_log for direct access

    # Optional repair
    repair_request: dict | None

    # Final result
    render_scene: RenderSceneModel

    # Consolidated warnings
    warnings: list[Warning]
    processing_warnings: list[Warning]  # consolidated warnings
