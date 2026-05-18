from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Awaitable, Callable, Literal

from pydantic_ai import Agent, RunContext

from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation, ReaderAskToolTraceEntry
from app.services.analysis.prompting.prompt_loader import load_agent_instructions

_ToolEventName = Literal["tool.started", "tool.completed", "tool.failed"]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _tool_trace(tool_name: str, status: Literal["started", "completed", "failed"], *, summary: str | None = None) -> ReaderAskToolTraceEntry:
    now = _iso_now()
    if status == "started":
        return ReaderAskToolTraceEntry(tool_name=tool_name, status=status, started_at=now)
    return ReaderAskToolTraceEntry(
        tool_name=tool_name,
        status=status,
        started_at=now,
        completed_at=now,
        summary=summary,
    )


@dataclass(slots=True)
class ReaderAskRuntimeActionRequest:
    action_type: Literal["save_note", "save_excerpt", "favorite_anchor", "save_answer_note"]
    label: str
    description: str
    payload_json: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = True


@dataclass(slots=True)
class ReaderAskRuntimeState:
    citations: list[ReaderAskCitation] = field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = field(default_factory=list)
    action_requests: list[ReaderAskRuntimeActionRequest] = field(default_factory=list)
    source_labels: set[str] = field(default_factory=set)
    used_history_lookup: bool = False
    tool_call_count: int = 0
    max_tool_calls: int = 5
    latest_record_context: dict[str, Any] | None = None
    latest_record_insights: list[dict[str, Any]] = field(default_factory=list)
    latest_record_excerpt_assets: list[dict[str, Any]] = field(default_factory=list)
    latest_user_excerpt_assets: list[dict[str, Any]] = field(default_factory=list)
    latest_user_vocabulary: list[dict[str, Any]] = field(default_factory=list)
    latest_dictionary_entry: dict[str, Any] | None = None
    latest_dictionary_ai: dict[str, Any] | None = None


@dataclass(slots=True)
class ReaderAskAgentDeps:
    payload: dict[str, Any]
    event_queue: asyncio.Queue[tuple[_ToolEventName, dict[str, Any]]]
    state: ReaderAskRuntimeState
    query_seed: str
    task_mode: Literal["explain", "breakdown", "vocabulary", "grammar", "practice"]
    record_id: str
    record_title: str | None
    primary_anchor: ReaderAskAnchorRef | None
    history_lookup_allowed: bool
    get_record_context_fn: Callable[[], Awaitable[dict[str, Any]]]
    get_record_insights_fn: Callable[[], Awaitable[list[dict[str, Any]]]]
    get_record_excerpt_assets_fn: Callable[[str], Awaitable[list[dict[str, Any]]]]
    search_user_excerpt_assets_fn: Callable[[str], Awaitable[list[dict[str, Any]]]]
    search_user_vocabulary_fn: Callable[[str], Awaitable[list[dict[str, Any]]]]
    lookup_dictionary_entry_fn: Callable[[str | None, int | None, str | None, str | None, int | None], Awaitable[dict[str, Any] | None]]
    run_dictionary_ai_context_explain_fn: Callable[[str, int, str, Literal["word", "phrase"], int | None], Awaitable[dict[str, Any] | None]]
    excerpt_item_to_citation_fn: Callable[[dict[str, Any], str], ReaderAskCitation]
    vocabulary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation]
    dictionary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation]
    dictionary_ai_to_citation_fn: Callable[[dict[str, Any], str, int], ReaderAskCitation]


def build_reader_ask_prompt(deps: ReaderAskAgentDeps) -> str:
    return json.dumps(deps.payload, ensure_ascii=False, indent=2)


def _append_citation(state: ReaderAskRuntimeState, citation: ReaderAskCitation) -> None:
    for existing in state.citations:
        if (
            existing.kind == citation.kind
            and existing.label == citation.label
            and existing.record_id == citation.record_id
            and existing.target_key == citation.target_key
            and existing.sentence_id == citation.sentence_id
        ):
            return
    state.citations.append(citation)


async def _emit_tool_event(
    deps: ReaderAskAgentDeps,
    event: _ToolEventName,
    *,
    tool_name: str,
    summary: str | None = None,
    detail: str | None = None,
) -> None:
    payload: dict[str, Any] = {"tool_name": tool_name}
    if summary is not None:
        payload["summary"] = summary
    if detail is not None:
        payload["detail"] = detail
    await deps.event_queue.put((event, payload))


async def _run_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    tool_name: str,
    runner: Callable[[], Awaitable[Any]],
) -> Any:
    deps = ctx.deps
    deps.state.tool_call_count += 1
    if deps.state.tool_call_count > deps.state.max_tool_calls:
        detail = f"Tool call limit exceeded ({deps.state.max_tool_calls})"
        deps.state.tool_trace.append(_tool_trace(tool_name, "failed", summary=detail))
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise RuntimeError(detail)
    deps.state.tool_trace.append(_tool_trace(tool_name, "started"))
    await _emit_tool_event(deps, "tool.started", tool_name=tool_name)
    try:
        result = await runner()
    except Exception as exc:
        detail = str(exc) or "Tool failed"
        deps.state.tool_trace.append(_tool_trace(tool_name, "failed", summary=detail))
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise
    summary = (
        f"{len(result)} item(s)"
        if isinstance(result, list)
        else "Loaded"
    )
    deps.state.tool_trace.append(_tool_trace(tool_name, "completed", summary=summary))
    await _emit_tool_event(deps, "tool.completed", tool_name=tool_name, summary=summary)
    return result


@lru_cache(maxsize=1)
def get_reader_ask_agent() -> Agent[ReaderAskAgentDeps, str]:
    agent = Agent[ReaderAskAgentDeps, str](
        model=None,
        output_type=str,
        deps_type=ReaderAskAgentDeps,
        instructions=load_agent_instructions("reader_ask"),
        name="reader_ask_agent",
        retries=1,
        output_retries=1,
        instrument=False,
    )

    @agent.tool(name="get_record_context")
    async def get_record_context(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        async def runner() -> dict[str, Any]:
            ctx.deps.state.source_labels.update({"current_record", "current_anchor"})
            ctx.deps.state.source_labels.add("current_paragraph")
            result = await ctx.deps.get_record_context_fn()
            ctx.deps.state.latest_record_context = result
            return result

        return await _run_tool(ctx, "get_record_context", runner)

    @agent.tool(name="get_record_insights")
    async def get_record_insights(ctx: RunContext[ReaderAskAgentDeps]) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            items = await ctx.deps.get_record_insights_fn()
            if items:
                ctx.deps.state.source_labels.add("record_assets")
            ctx.deps.state.latest_record_insights = items
            return items

        return await _run_tool(ctx, "get_record_insights", runner)

    @agent.tool(name="get_record_excerpt_assets")
    async def get_record_excerpt_assets(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            items = await ctx.deps.get_record_excerpt_assets_fn(query or ctx.deps.query_seed)
            if items:
                ctx.deps.state.source_labels.add("record_assets")
            ctx.deps.state.latest_record_excerpt_assets = items
            for item in items:
                _append_citation(ctx.deps.state, ctx.deps.excerpt_item_to_citation_fn(item, "record_excerpt_asset"))
            return items

        return await _run_tool(ctx, "get_record_excerpt_assets", runner)

    @agent.tool(name="search_user_excerpt_assets")
    async def search_user_excerpt_assets(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str,
    ) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            if not ctx.deps.history_lookup_allowed:
                return []
            items = await ctx.deps.search_user_excerpt_assets_fn(query)
            ctx.deps.state.used_history_lookup = True
            ctx.deps.state.source_labels.add("history_assets")
            ctx.deps.state.latest_user_excerpt_assets = items
            for item in items:
                _append_citation(ctx.deps.state, ctx.deps.excerpt_item_to_citation_fn(item, "user_excerpt_asset"))
            return items

        return await _run_tool(ctx, "search_user_excerpt_assets", runner)

    @agent.tool(name="search_user_vocabulary")
    async def search_user_vocabulary(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str,
    ) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            if not ctx.deps.history_lookup_allowed:
                return []
            items = await ctx.deps.search_user_vocabulary_fn(query)
            if items:
                ctx.deps.state.source_labels.add("vocabulary")
                ctx.deps.state.latest_user_vocabulary = items
            for item in items:
                _append_citation(ctx.deps.state, ctx.deps.vocabulary_item_to_citation_fn(item))
            return items

        return await _run_tool(ctx, "search_user_vocabulary", runner)

    @agent.tool(name="lookup_dictionary_entry")
    async def lookup_dictionary_entry(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str | None = None,
        entry_id: int | None = None,
        query_type: Literal["word", "phrase"] | None = None,
        context_sentence: str | None = None,
        occurrence: int | None = None,
    ) -> dict[str, Any] | None:
        async def runner() -> dict[str, Any] | None:
            item = await ctx.deps.lookup_dictionary_entry_fn(query, entry_id, query_type, context_sentence, occurrence)
            if item is not None:
                ctx.deps.state.source_labels.add("dictionary")
                ctx.deps.state.latest_dictionary_entry = item
                _append_citation(ctx.deps.state, ctx.deps.dictionary_item_to_citation_fn(item))
            return item

        return await _run_tool(ctx, "lookup_dictionary_entry", runner)

    @agent.tool(name="run_dictionary_ai_context_explain")
    async def run_dictionary_ai_context_explain(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str,
        entry_id: int,
        context_sentence: str,
        query_type: Literal["word", "phrase"] = "word",
        occurrence: int | None = None,
    ) -> dict[str, Any] | None:
        async def runner() -> dict[str, Any] | None:
            item = await ctx.deps.run_dictionary_ai_context_explain_fn(
                query,
                entry_id,
                context_sentence,
                query_type,
                occurrence,
            )
            if item is not None:
                ctx.deps.state.source_labels.add("dictionary")
                ctx.deps.state.latest_dictionary_ai = item
                _append_citation(
                    ctx.deps.state,
                    ctx.deps.dictionary_ai_to_citation_fn(item, query, entry_id),
                )
            return item

        return await _run_tool(ctx, "run_dictionary_ai_context_explain", runner)

    @agent.tool(name="propose_save_note")
    async def propose_save_note(
        ctx: RunContext[ReaderAskAgentDeps],
        note_text: str | None = None,
        use_assistant_answer: bool = False,
    ) -> dict[str, Any]:
        async def runner() -> dict[str, Any]:
            if ctx.deps.primary_anchor is None:
                return {"ok": False, "reason": "No anchor available"}
            action_type = "save_answer_note" if use_assistant_answer else "save_note"
            ctx.deps.state.action_requests.append(
                ReaderAskRuntimeActionRequest(
                    action_type=action_type,
                    label="保存为笔记",
                    description="把当前解释或补充内容保存到当前锚点笔记",
                    payload_json={
                        "record_id": ctx.deps.record_id,
                        "anchor": ctx.deps.primary_anchor.model_dump(mode="json"),
                        "note_text": note_text,
                        "use_assistant_answer": use_assistant_answer,
                    },
                )
            )
            return {"ok": True, "action_type": action_type}

        return await _run_tool(ctx, "propose_save_note", runner)

    @agent.tool(name="propose_save_excerpt")
    async def propose_save_excerpt(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        async def runner() -> dict[str, Any]:
            if ctx.deps.primary_anchor is None:
                return {"ok": False, "reason": "No anchor available"}
            ctx.deps.state.action_requests.append(
                ReaderAskRuntimeActionRequest(
                    action_type="save_excerpt",
                    label="保存为高亮",
                    description="把当前锚点保存成高亮/摘录",
                    payload_json={
                        "record_id": ctx.deps.record_id,
                        "anchor": ctx.deps.primary_anchor.model_dump(mode="json"),
                    },
                )
            )
            return {"ok": True, "action_type": "save_excerpt"}

        return await _run_tool(ctx, "propose_save_excerpt", runner)

    @agent.tool(name="propose_favorite_anchor")
    async def propose_favorite_anchor(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        async def runner() -> dict[str, Any]:
            if ctx.deps.primary_anchor is None:
                return {"ok": False, "reason": "No anchor available"}
            ctx.deps.state.action_requests.append(
                ReaderAskRuntimeActionRequest(
                    action_type="favorite_anchor",
                    label="加入收藏",
                    description="收藏当前锚点",
                    payload_json={
                        "record_id": ctx.deps.record_id,
                        "anchor": ctx.deps.primary_anchor.model_dump(mode="json"),
                    },
                )
            )
            return {"ok": True, "action_type": "favorite_anchor"}

        return await _run_tool(ctx, "propose_favorite_anchor", runner)

    return agent
