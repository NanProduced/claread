"""Opt-in real LLM smoke tests for Ask Claread.

Skipped by default. To run, all three gates must be open:

    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_REAL_LLM_MODEL=glm-5.1 \
        uv run pytest tests/test_reader_ask_real_llm_smoke.py -m real_llm -v

The resolved ``reader_ask`` route model must exactly match
``CLAREAD_REAL_LLM_MODEL``. This prevents a stale local profile from silently
calling a different model than the one explicitly authorized for the run.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
from app.agents.reader_ask_tool_policy import ToolAvailabilityInput, build_tool_availability
from app.agents.reader_ask_tool_registry import TOOL_GET_RECORD_CONTEXT
from app.llm.call_guard import real_llm_tests_allowed
from app.llm.types import RunModelSettings
from app.services.reader_ask.agent_invocation import (
    ReaderAskStreamCompleted,
    ReaderAskStreamSseEvent,
    resolve_reader_ask_agent,
    stream_reader_ask_agent_run,
)

_REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"
_ARTICLE_TITLE = "马斯克完成全球首位万亿富翁"
_ARTICLE_TEXT = (
    "这篇文章报道了埃隆·马斯克因 SpaceX 的历史性 IPO 成为全球首位万亿富翁。"
    "文章把他的财富增长放在商业创业和太空探索两条线索中解释：SpaceX 的上市"
    "重塑了市场对商业航天的估值，也让马斯克的个人财富进入新的量级。"
    "文章同时提醒读者，这一事件不只是个人财富新闻，也反映了资本市场、科技"
    "公司和太空产业之间正在形成更紧密的关系。"
)


def _cap_smoke_settings(settings: RunModelSettings | None) -> RunModelSettings:
    base = settings or RunModelSettings()
    return base.with_max_tokens(min(base.max_tokens or 900, 900))


def _resolve_authorized_reader_ask_agent():
    authorized_model = os.environ.get(_REAL_LLM_MODEL_ENV, "").strip()
    if not authorized_model:
        pytest.skip(f"{_REAL_LLM_MODEL_ENV} is required for real LLM smoke tests")

    try:
        resolved = resolve_reader_ask_agent()
    except RuntimeError as exc:
        pytest.skip(f"reader_ask model route is not configured: {exc}")

    model_config = resolved.model_config
    if model_config is None:
        pytest.skip("reader_ask model route resolved without model_config")
    if model_config.model_name != authorized_model:
        pytest.skip(
            "resolved reader_ask model does not match authorized model: "
            f"resolved={model_config.model_name!r}, authorized={authorized_model!r}. "
            "Set ASK_CLAREAD_PROFILE / MODEL_PROFILES_JSON to the intended profile."
        )
    return resolved


async def _get_record_context(
    _deps: ReaderAskAgentDeps | None,
    scope: str | None,
    _target_sentence_id: str | None,
) -> dict[str, Any]:
    return {
        "status": "loaded",
        "record_id": "real-llm-smoke-record",
        "record_title": _ARTICLE_TITLE,
        "scope": scope or "window",
        "text": _ARTICLE_TEXT,
        "sentences": [
            {"sentence_id": "s1", "text": _ARTICLE_TEXT[:62]},
            {"sentence_id": "s2", "text": _ARTICLE_TEXT[62:128]},
            {"sentence_id": "s3", "text": _ARTICLE_TEXT[128:]},
        ],
        "truncated": False,
    }


async def _get_record_insights(
    _deps: ReaderAskAgentDeps | None,
    _target_sentence_id: str | None,
    kind: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": kind or "sentence_analysis",
            "summary": "文章主线是 SpaceX IPO 推高估值，并把个人财富新闻连接到商业航天趋势。",
            "translation_zh": "SpaceX 的上市让马斯克财富进入新量级。",
            "sentence_id": "s2",
        }
    ][: limit or 1]


async def _get_user_vocabulary_book(
    _deps: ReaderAskAgentDeps | None,
    _lemma: str | None,
    _limit: int | None,
    _sort_by: str | None,
) -> list[dict[str, Any]]:
    return []


async def _resolve_known_reference(
    _deps: ReaderAskAgentDeps | None,
    query: str,
    _top_k: int | None,
) -> dict[str, Any]:
    return {"status": "not_found", "query": query, "candidates": []}


async def _load_explicit_attachment_context(
    _deps: ReaderAskAgentDeps | None,
    record_id: str,
    asset_id: str | None,
) -> dict[str, Any]:
    return {
        "status": "forbidden",
        "record_id": record_id,
        "asset_id": asset_id,
        "ok": False,
    }


async def _generate_sentence_annotation(kind: str) -> dict[str, Any]:
    return {"status": "not_applicable", "kind": kind}


async def _suggest_prompts(suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "success", "suggestions": suggestions}


def _make_smoke_deps() -> ReaderAskAgentDeps:
    state = ReaderAskRuntimeState()
    state.planner_skipped = True
    state.planner_route_used = "agent_loop_only"
    state.latest_article_overview = (
        "文章报道马斯克因 SpaceX IPO 成为全球首位万亿富翁，并说明这件事"
        "反映了商业航天、资本市场和科技产业的交汇。"
    )
    payload = {
        "thread": {"id": "real-llm-smoke-thread", "title": "Real LLM smoke"},
        "record": {
            "record_id": "real-llm-smoke-record",
            "title": _ARTICLE_TITLE,
            "workflow_version": "smoke",
            "schema_version": "smoke",
        },
        "entry_action": "general",
        "submission_mode": "freeform",
        "user_message": (
            "请先调用 get_record_context(scope='full') 读取全文上下文，"
            "再用三点中文总结这篇文章。"
        ),
        "resolved_intent": "general",
        "resolved_intent_label": "普通提问",
        "history": [],
        "canonical_context": {
            "attachments": [],
            "anchors": [],
            "resolved_context_input": {
                "article_overview": state.latest_article_overview,
                "source_labels": ["current_record", "article_overview"],
            },
        },
        "planning": {
            "retrieval_needs": "none",
            "working_set": {
                "primary_anchor_type": None,
                "local_context_window_needed": False,
                "record_insights_needed": False,
                "article_overview_needed": True,
                "dictionary_needed": False,
                "cross_record_context_allowed": False,
                "external_record_refs": [],
                "external_asset_refs": [],
                "external_asset_lookup_needed": False,
            },
            "context_plan": None,
            "trace_summary": None,
        },
        "tooling_contract": {
            "call_tools_on_demand": True,
            "cross_record_context_requires_explicit_intent": False,
            "writes_require_confirmation": True,
            "dictionary_context_explain_available": False,
        },
        "response_contract": {
            "format": "markdown",
            "be_concise": True,
            "article_bound": True,
        },
        "intent_instructions": "根据用户具体问题灵活回答，保持简洁，围绕当前文章和已提供的上下文。",
    }
    return ReaderAskAgentDeps(
        payload=payload,
        event_queue=asyncio.Queue(),
        state=state,
        query_seed=payload["user_message"],
        task_mode="general",
        record_id="real-llm-smoke-record",
        record_title=_ARTICLE_TITLE,
        primary_anchor=None,
        get_record_context_fn=_get_record_context,
        get_record_insights_fn=_get_record_insights,
        get_user_vocabulary_book_fn=_get_user_vocabulary_book,
        resolve_known_reference_fn=_resolve_known_reference,
        load_explicit_attachment_context_fn=_load_explicit_attachment_context,
        generate_sentence_annotation_fn=_generate_sentence_annotation,
        suggest_prompts_fn=_suggest_prompts,
        vocabulary_item_to_citation_fn=lambda _item: None,
        tool_availability=build_tool_availability(
            ToolAvailabilityInput(
                task_mode="general",
                entry_action="general",
                has_primary_anchor=False,
            )
        ),
    )


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_ask_claread_real_llm_streaming_tool_call_smoke() -> None:
    """Run one real Ask stream against the explicitly authorized model.

    This intentionally nudges the model to call ``get_record_context`` so the
    smoke covers the provider streaming path where reasoning and tool-call
    deltas can appear in the same run.
    """
    assert real_llm_tests_allowed(), "real LLM smoke should only run when explicitly enabled"
    resolved = _resolve_authorized_reader_ask_agent()
    deps = _make_smoke_deps()
    frames: list[str] = []
    completed: ReaderAskStreamCompleted | None = None

    async for item in stream_reader_ask_agent_run(
        agent=resolved.agent,
        deps=deps,
        model=resolved.model,
        route_settings=_cap_smoke_settings(resolved.model_config.model_settings),
        assistant_message_id="real-llm-smoke-message",
        model_config=resolved.model_config,
    ):
        if isinstance(item, ReaderAskStreamSseEvent):
            frames.append(item.encoded_sse)
        else:
            completed = item

    assert completed is not None
    assert completed.outcome.interrupted is False
    assert len(completed.outcome.content_md.strip()) >= 20
    assert "READER_ASK_FAILED" not in completed.outcome.content_md

    completed_tool_names = [
        entry.tool_name
        for entry in deps.state.tool_trace
        if entry.status == "completed"
    ]
    assert TOOL_GET_RECORD_CONTEXT in completed_tool_names
    assert any("event: message.delta" in frame for frame in frames)
