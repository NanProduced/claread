"""Item-level repair schemas.

定义 repair patch 的 request/response 结构，
让 repair agent 只输出"小补丁"而非完整 NormalizedAnnotationResult。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.internal.analysis import BASE_MODEL_CONFIG
from app.schemas.internal.drafts import DraftAnnotation


class RepairTarget(BaseModel):
    """一个需要修复的 drop 条目。"""

    model_config = BASE_MODEL_CONFIG

    source_agent: Literal[
        "vocabulary", "grammar", "translation", "term", "understanding",
    ] = Field(description="来源 agent")
    annotation_type: str = Field(
        description="被删除的标注类型，如 vocab_highlight、phrase_gloss 等",
    )
    sentence_id: str = Field(description="句子ID")
    anchor_text: str = Field(description="锚定文本")
    drop_reason: str = Field(description="删除原因")
    drop_stage: str = Field(description="删除阶段")
    is_canonical: bool = Field(
        description="是否来自 canonical_drop_log（True）或旧 drop_log（False）",
    )
    draft_payload: dict[str, Any] | None = Field(
        default=None,
        description="匹配到的原始 draft item（JSON dict），供 repair 参考",
    )


class RepairPatchRequest(BaseModel):
    """Item-level repair 请求：只包含受影响句子和失败条目。"""

    model_config = BASE_MODEL_CONFIG

    sentences: list[dict[str, str]] = Field(
        description="受影响句子列表，每项含 sentence_id 和 text",
    )
    targets: list[RepairTarget] = Field(
        description="需要修复的 drop 条目列表",
    )


class RepairPatch(BaseModel):
    """一个 repair 补丁。"""

    model_config = BASE_MODEL_CONFIG

    target_index: int = Field(
        ge=0,
        description="对应 RepairPatchRequest.targets 的索引",
    )
    action: Literal["replace", "delete"] = Field(
        description="replace=提供新 annotation 替换；delete=确认删除",
    )
    annotation: DraftAnnotation | None = Field(
        default=None,
        description=(
            "action=replace 时提供新的 DraftAnnotation；"
            "action=delete 时为 None"
        ),
    )
    repair_reason: str = Field(
        description="修复原因或删除原因",
    )

    @model_validator(mode="after")
    def _validate_action_annotation_consistency(self) -> RepairPatch:
        if self.action == "replace" and self.annotation is None:
            msg = "action='replace' requires annotation to be set"
            raise ValueError(msg)
        if self.action == "delete" and self.annotation is not None:
            msg = "action='delete' requires annotation to be None"
            raise ValueError(msg)
        return self


class RepairPatchResult(BaseModel):
    """Repair patch 输出：一组补丁。"""

    model_config = BASE_MODEL_CONFIG

    patches: list[RepairPatch] = Field(
        description="修复补丁列表",
    )
