from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.internal.analysis import (
    BASE_MODEL_CONFIG,
    Annotation,
    SentenceTranslation,
)


class DropLogEntry(BaseModel):
    """normalize_and_ground 阶段的删除/降级日志。

    记录所有被删除的候选标注及其原因。
    """

    model_config = BASE_MODEL_CONFIG

    source_agent: Literal["vocabulary", "grammar", "translation", "term", "understanding"] = Field(
        description="来源 agent"
    )
    annotation_type: str = Field(
        description="被删除的标注类型，如 vocab_highlight、phrase_gloss 等"
    )
    sentence_id: str = Field(description="句子ID")
    anchor_text: str = Field(description="锚定文本（用于追溯）")
    drop_reason: str = Field(
        description="删除原因，如 duplicate、low_value、anchor_invalid、conflict 等"
    )
    drop_stage: Literal[
        "grounding",
        "deduplication",
        "conflict_resolution",
        "density_control",
        "pruning",
    ] = Field(description="删除发生的阶段")
    dropped_at: datetime = Field(
        default_factory=datetime.now,
        description="删除时间戳",
    )


class NormalizedAnnotationResult(BaseModel):
    """归一化后的标注结果。

    经过 normalize_and_ground 阶段处理后：
    - 已完成 substring grounding
    - 已校验 sentence_id
    - 已处理 occurrence
    - 已去重
    - 已消解类型冲突
    - 已裁剪低价值标注
    - 记录所有删除日志
    """

    model_config = BASE_MODEL_CONFIG

    annotations: list[Annotation] = Field(
        default_factory=list,
        description="归一化后的标注列表",
    )
    sentence_translations: list[SentenceTranslation] = Field(
        default_factory=list,
        description="归一化后的逐句翻译",
    )
    drop_log: list[DropLogEntry] = Field(
        default_factory=list,
        description="删除/降级日志列表",
    )
