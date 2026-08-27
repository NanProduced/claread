"""Prompt 调试接口，用于预览和检查完整 prompt 模板。"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config.settings import get_settings
from app.services.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_teaching_blueprint_strategy,
    build_teaching_language_support_strategy,
    build_teaching_refinement_strategy,
    build_teaching_semantic_review_strategy,
    build_teaching_translation_strategy,
)
from app.services.prompting.prompt_composer import render_prompt_sections
from app.services.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)

router = APIRouter(prefix="/debug", tags=["debug"])


async def _verify_debug_key(x_debug_api_key: str = Header(...)) -> str:
    settings = get_settings()
    if not settings.daily_reader_admin_api_key:
        raise HTTPException(status_code=503, detail="Debug API not configured")
    if not secrets.compare_digest(x_debug_api_key, settings.daily_reader_admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid debug API key")
    return x_debug_api_key


_DAILY_AGENTS = (
    "daily_blueprint",
    "daily_language_support",
    "daily_translation",
    "daily_semantic_review",
    "daily_teaching_refinement",
)


class PromptPreviewRequest(BaseModel):
    reading_goal: str
    reading_variant: str
    agent_type: str | None = None
    few_shot_mode: str = "baseline"
    include_instructions: bool = False
    sample_sentences: list[dict] | None = None


class PromptPreviewResponse(BaseModel):
    prompt_version: str
    instructions: str | None = None
    prompt_template: str | None = None
    prompt_full: str | None = None
    strategy_meta: dict[str, Any]


def _build_daily_preview(
    agent_type: str,
    include_instructions: bool,
) -> PromptPreviewResponse:
    strategy_builders = {
        "daily_blueprint": build_teaching_blueprint_strategy,
        "daily_language_support": build_teaching_language_support_strategy,
        "daily_translation": build_teaching_translation_strategy,
        "daily_semantic_review": build_teaching_semantic_review_strategy,
        "daily_teaching_refinement": build_teaching_refinement_strategy,
    }
    builder = strategy_builders.get(agent_type)
    if builder is None:
        raise HTTPException(status_code=400, detail=f"Unknown daily agent: {agent_type}")

    strategy: DailyPromptStrategy = builder()
    sections = build_daily_prompt_sections(strategy)

    sentences = [
        {"sentence_id": "p1", "text": "[示例段落 1]"},
    ]
    prompt_template = (
        render_prompt_sections(sections)
        + "\n<input_sentences>\n"
        + "\n".join(f"{s['sentence_id']}: {s['text']}" for s in sentences)
        + "\n</input_sentences>"
    )

    instructions = None
    if include_instructions:
        instructions = load_agent_instructions(agent_type)

    return PromptPreviewResponse(
        prompt_version=get_prompt_version(),
        instructions=instructions,
        prompt_template=prompt_template,
        strategy_meta={
            "agent_type": agent_type,
            "profile_id": strategy.profile_id,
            "node_type": strategy.node_type,
            "policy_lines_count": len(strategy.policy_lines),
            "workflow": "daily_reader",
        },
    )


@router.post("/prompt-preview", response_model=PromptPreviewResponse, summary="预览 Prompt 模板")
async def prompt_preview(
    request: PromptPreviewRequest,
    _auth: str = Header(..., alias="x-debug-api-key"),
) -> PromptPreviewResponse:
    """仅预览 Daily Reader prompt。"""
    await _verify_debug_key(_auth)

    if request.reading_goal != "daily_reading" or request.agent_type not in _DAILY_AGENTS:
        raise HTTPException(
            status_code=400,
            detail="Only Daily Reader prompt preview is supported",
        )
    return _build_daily_preview(request.agent_type, request.include_instructions)
