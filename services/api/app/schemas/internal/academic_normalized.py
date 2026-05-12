from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.internal.academic_drafts import (
    AcademicSentenceTranslation,
    ContentSummary,
    InterpretationNote,
    LogicNote,
    ParagraphRole,
    TermNote,
)
from app.schemas.internal.normalized import DropLogEntry

AcademicQualityState = Literal["normal", "degraded"]


class AcademicNormalizedResult(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    term_annotations: list[TermNote] = Field(default_factory=list)
    sentence_translations: list[AcademicSentenceTranslation] = Field(default_factory=list)
    logic_notes: list[LogicNote] = Field(default_factory=list)
    interpretation_notes: list[InterpretationNote] = Field(default_factory=list)
    paragraph_roles: list[ParagraphRole] = Field(default_factory=list)
    content_summary: ContentSummary | None = None
    title: str = Field(min_length=1, description="中文标题")
    quality_state: AcademicQualityState = Field(
        default="normal",
        description="归一化质量判定。normal=结果完整; degraded=关键产出缺失或异常。",
    )
    quality_issues: list[str] = Field(
        default_factory=list,
        description="quality_state 为 degraded 时的具体原因列表。",
    )
    drop_log: list[DropLogEntry] = Field(default_factory=list, description="归一化阶段的删除/降级日志")
