from __future__ import annotations

from pydantic import BaseModel, Field


class TextSpan(BaseModel):
    start: int = Field(
        ge=0,
        description="相对于 render_text 的绝对起始偏移，采用 0-based 坐标。",
    )
    end: int = Field(
        gt=0,
        description="相对于 render_text 的绝对结束偏移，采用半开区间 [start, end)。",
    )
