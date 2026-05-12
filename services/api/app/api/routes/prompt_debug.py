"""Prompt 调试接口，用于预览和检查完整 prompt 模板。"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config.settings import get_settings
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_daily_prompt_sections,
    build_vocab_highlight_strategy,
    build_phrase_gloss_strategy,
    build_paragraph_notes_strategy,
    build_close_reading_takeaways_strategy,
    build_quality_review_strategy,
    build_refinement_strategy,
)
from app.services.analysis.prompting.example_strategy import (
    ExampleEntry,
    ExampleStrategy,
)
from app.services.analysis.prompting.prompt_composer import (
    PromptSection,
    build_agent_prompt,
    render_prompt_sections,
)
from app.services.analysis.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
    load_examples,
    load_policy_lines,
)
from app.services.analysis.prompting.prompt_strategy import (
    PromptStrategy,
    build_grammar_prompt_strategy,
    build_prompt_sections,
    build_translation_prompt_strategy,
    build_vocabulary_prompt_strategy,
)
from app.services.analysis.prompting.strategy_builder import (
    StrategyBundle,
    build_grammar_bundle,
    build_translation_bundle,
    build_vocabulary_bundle,
)

router = APIRouter(prefix="/debug", tags=["debug"])


async def _verify_debug_key(x_debug_api_key: str = Header(...)) -> str:
    settings = get_settings()
    if not settings.daily_reader_admin_api_key:
        raise HTTPException(status_code=503, detail="Debug API not configured")
    if not secrets.compare_digest(x_debug_api_key, settings.daily_reader_admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid debug API key")
    return x_debug_api_key


_LEARNING_AGENTS = ("vocabulary", "grammar", "translation")
_DAILY_AGENTS = (
    "daily_vocab",
    "daily_footer",
    "daily_interpretation",
    "daily_review",
    "daily_refinement",
)
_ACADEMIC_AGENTS = ("term", "academic_translation", "understanding")
_ALL_AGENTS = _LEARNING_AGENTS + _DAILY_AGENTS + _ACADEMIC_AGENTS


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


def _build_learning_preview(
    plan: Any,
    agent_type: str,
    few_shot_mode: str,
    include_instructions: bool,
    sample_sentences: list[dict] | None,
) -> PromptPreviewResponse:
    bundle_builders = {
        "vocabulary": build_vocabulary_bundle,
        "grammar": build_grammar_bundle,
        "translation": build_translation_bundle,
    }
    builder = bundle_builders.get(agent_type)
    if builder is None:
        raise HTTPException(status_code=400, detail=f"Unknown learning agent: {agent_type}")

    plan.few_shot_mode = few_shot_mode
    bundle: StrategyBundle = builder(plan, sentences=sample_sentences)

    sections = build_prompt_sections(bundle.prompt_strategy)
    example_entries = bundle.example_strategy.examples
    sentences = sample_sentences or [
        {"sentence_id": "s1", "text": "[示例句子 1]"},
        {"sentence_id": "s2", "text": "[示例句子 2]"},
    ]

    prompt_template = build_agent_prompt(
        strategy_sections=sections,
        examples=example_entries,
        sentences=sentences,
    )

    instructions = None
    if include_instructions:
        instructions = load_agent_instructions(agent_type)

    rag_debug = bundle.rag_debug or {}
    return PromptPreviewResponse(
        prompt_version=get_prompt_version(),
        instructions=instructions,
        prompt_template=prompt_template,
        strategy_meta={
            "agent_type": agent_type,
            "profile_id": bundle.prompt_strategy.profile_id,
            "reading_goal": bundle.prompt_strategy.reading_goal,
            "reading_variant": bundle.prompt_strategy.reading_variant,
            "policy_lines_count": len(bundle.prompt_strategy.policy_lines),
            "examples_count": len(example_entries),
            "selection_mode": bundle.example_strategy.selection_mode,
            "few_shot_mode": few_shot_mode,
            "example_count": len(example_entries),
            "fallback_reason": rag_debug.get("grammar_note", {}).get("fallback_reason"),
            "selected_example_ids": rag_debug.get("grammar_note", {}).get("selected_example_ids", []),
            "ann_topk": rag_debug.get("grammar_note", {}).get("ann_topk", 0),
            "rerank_topn": rag_debug.get("grammar_note", {}).get("rerank_topn", 0),
            "embedding_latency_ms": rag_debug.get("grammar_note", {}).get("embedding_latency_ms", 0.0),
            "ann_latency_ms": rag_debug.get("grammar_note", {}).get("ann_latency_ms", 0.0),
            "rerank_latency_ms": rag_debug.get("grammar_note", {}).get("rerank_latency_ms", 0.0),
            "rag_debug": rag_debug,
        },
    )


def _build_daily_preview(
    agent_type: str,
    include_instructions: bool,
) -> PromptPreviewResponse:
    strategy_builders = {
        "daily_vocab": build_vocab_highlight_strategy,
        "daily_footer": build_paragraph_notes_strategy,
        "daily_interpretation": build_close_reading_takeaways_strategy,
        "daily_review": build_quality_review_strategy,
        "daily_refinement": build_refinement_strategy,
    }
    builder = strategy_builders.get(agent_type)
    if builder is None:
        raise HTTPException(status_code=400, detail=f"Unknown daily agent: {agent_type}")

    strategy: DailyPromptStrategy = builder()
    sections = build_daily_prompt_sections(strategy)

    sentences = [
        {"sentence_id": "p1", "text": "[示例段落 1]"},
    ]
    prompt_template = render_prompt_sections(sections) + "\n<input_sentences>\n" + "\n".join(
        f"{s['sentence_id']}: {s['text']}" for s in sentences
    ) + "\n</input_sentences>"

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


def _build_academic_preview(
    agent_type: str,
    include_instructions: bool,
) -> PromptPreviewResponse:
    policy_keys = {
        "term": "term",
        "academic_translation": "academic_translation",
        "understanding": "understanding",
    }
    policy_key = policy_keys.get(agent_type)
    if policy_key is None:
        raise HTTPException(status_code=400, detail=f"Unknown academic agent: {agent_type}")

    policy_lines = load_policy_lines("academic", policy_key)
    sections = (
        PromptSection("profile", ("profile_id: academic", f"node_type: {agent_type}")),
        PromptSection("policy", tuple(policy_lines)),
    )

    example_variant = policy_key
    raw_examples = load_examples("academic", example_variant)
    example_entries = [
        ExampleEntry(
            example_type=e["example_type"],
            sentence_text=e["sentence_text"],
            output_fragment=e["output_fragment"],
        )
        for e in raw_examples
    ]

    sentences = [
        {"sentence_id": "s1", "text": "[示例句子 1]"},
    ]
    prompt_template = build_agent_prompt(
        strategy_sections=sections,
        examples=example_entries,
        sentences=sentences,
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
            "profile_id": "academic",
            "policy_lines_count": len(policy_lines),
            "examples_count": len(example_entries),
            "workflow": "academic",
        },
    )


@router.post("/prompt-preview", response_model=PromptPreviewResponse, summary="预览 Prompt 模板")
async def prompt_preview(
    request: PromptPreviewRequest,
    _auth: str = Header(..., alias="x-debug-api-key"),
) -> PromptPreviewResponse:
    """根据阅读目标和变体预览完整 prompt 模板，支持 learning/daily/academic 三种工作流。"""
    await _verify_debug_key(_auth)

    agent_type = request.agent_type

    if agent_type in _LEARNING_AGENTS:
        plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
        return _build_learning_preview(
            plan, agent_type, request.few_shot_mode,
            request.include_instructions, request.sample_sentences,
        )

    if agent_type in _DAILY_AGENTS:
        return _build_daily_preview(agent_type, request.include_instructions)

    if agent_type in _ACADEMIC_AGENTS:
        return _build_academic_preview(agent_type, request.include_instructions)

    if agent_type is None:
        plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
        results = {}
        for at in _LEARNING_AGENTS:
            resp = _build_learning_preview(
                plan, at, request.few_shot_mode,
                False, request.sample_sentences,
            )
            results[at] = resp.strategy_meta
        return PromptPreviewResponse(
            prompt_version=get_prompt_version(),
            strategy_meta={
                "reading_goal": request.reading_goal,
                "reading_variant": request.reading_variant,
                "agents": results,
            },
        )

    raise HTTPException(status_code=400, detail=f"Unknown agent_type: {agent_type}")
