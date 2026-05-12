"""
Analysis Tasks API Schemas.

Defines request/response Pydantic models for /analysis-tasks endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.analysis import AnyRenderSceneModel, GOAL_VARIANT_MAP, SourceType
from app.schemas.internal.analysis import ReadingGoal, ReadingVariant

# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

TaskStatus = Literal[
    "queued", "running", "finalizing", "succeeded", "failed", "cancelled", "expired"
]


class TaskSubmitRequest(BaseModel):
    """POST /analysis-tasks — submit a new analysis task."""

    text: str = Field(min_length=1, description="待分析的原始英文文本。")
    reading_goal: ReadingGoal = Field(default="daily_reading")
    reading_variant: ReadingVariant = Field(default="intermediate_reading")
    client_record_id: str | None = Field(default=None, max_length=64, description="客户端生成的记录唯一标识。")
    source_type: SourceType = Field(default="user_input")
    extended: bool = Field(default=False)
    wait_for_result: bool = Field(
        default=False,
        description="是否在本次请求内等待任务结果（超时后仍返回任务状态）。",
    )
    wait_timeout_seconds: float = Field(
        default=45.0,
        ge=1.0,
        le=120.0,
        description="当 wait_for_result=true 时，最长等待秒数。",
    )

    def model_post_init(self, __context__: Any) -> None:
        allowed_variants = GOAL_VARIANT_MAP[self.reading_goal]
        if self.reading_variant not in allowed_variants:
            raise ValueError(
                f"reading_variant={self.reading_variant} does not match "
                f"reading_goal={self.reading_goal}"
            )


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class TaskSubmitResponse(BaseModel):
    """202 response after task submission."""

    task_id: UUID
    record_id: UUID = Field(deprecated=True, description="已弃用，请使用 cloud_record_id。")
    cloud_record_id: UUID = Field(description="云端 analysis_records.id (UUID)。")
    client_record_id: str | None = Field(default=None, description="前端生成的稳定记录主键。")
    status: TaskStatus
    created: bool = Field(description="当前实现恒为 True，保留该字段用于响应兼容。")
    render_scene: AnyRenderSceneModel | None = Field(
        default=None,
        description="当 wait_for_result=true 且任务在超时前成功完成时返回。",
    )

    @model_validator(mode="before")
    @classmethod
    def _default_cloud_record_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("cloud_record_id") is None:
            return {**data, "cloud_record_id": data.get("record_id")}
        return data


class TaskStatusResponse(BaseModel):
    """GET /analysis-tasks/{id} response."""

    task_id: UUID
    record_id: UUID = Field(deprecated=True, description="已弃用，请使用 cloud_record_id。")
    cloud_record_id: UUID = Field(description="云端 analysis_records.id (UUID)。")
    client_record_id: str | None = Field(default=None, description="前端生成的稳定记录主键。")
    status: TaskStatus
    failure_code: str | None = None
    failure_message: str | None = None
    quota_cost_points: int = 0
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _default_cloud_record_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("cloud_record_id") is None:
            return {**data, "cloud_record_id": data.get("record_id")}
        return data


class ActiveTaskResponse(BaseModel):
    """GET /analysis-tasks/current — returns current active task or null indicator."""

    has_active: bool
    task: TaskStatusResponse | None = None
