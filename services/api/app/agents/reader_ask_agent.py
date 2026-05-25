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


def _tool_trace(
    tool_name: str,
    status: Literal["started", "completed", "failed"],
    *,
    input_summary: str | None = None,
    summary: str | None = None,
    next_actions: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> ReaderAskToolTraceEntry:
    now = _iso_now()
    if status == "started":
        return ReaderAskToolTraceEntry(
            tool_name=tool_name,
            status=status,
            started_at=now,
            input_summary=input_summary,
        )
    return ReaderAskToolTraceEntry(
        tool_name=tool_name,
        status=status,
        started_at=now,
        completed_at=now,
        input_summary=input_summary,
        summary=summary,
        next_actions=next_actions or [],
        artifacts=artifacts or [],
    )


@dataclass(slots=True)
class ReaderAskRuntimeActionRequest:
    action_type: Literal["save_note", "save_highlight"]
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
    used_cross_record_context: bool = False
    tool_call_count: int = 0
    max_tool_calls: int = 5
    latest_record_context: dict[str, Any] | None = None
    latest_record_insights: list[dict[str, Any]] = field(default_factory=list)
    latest_article_overview: str | None = None
    latest_external_record_contexts: list[dict[str, Any]] = field(default_factory=list)
    latest_external_asset_contexts: list[dict[str, Any]] = field(default_factory=list)
    latest_user_vocabulary: list[dict[str, Any]] = field(default_factory=list)
    latest_dictionary_entry: dict[str, Any] | None = None
    latest_dictionary_ai: dict[str, Any] | None = None
    latest_generated_annotations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReaderAskAgentDeps:
    payload: dict[str, Any]
    event_queue: asyncio.Queue[tuple[_ToolEventName, dict[str, Any]]]
    state: ReaderAskRuntimeState
    query_seed: str
    task_mode: Literal["explain", "breakdown", "vocabulary", "grammar", "practice", "general"]
    record_id: str
    record_title: str | None
    primary_anchor: ReaderAskAnchorRef | None
    get_record_context_fn: Callable[[], Awaitable[dict[str, Any]]]
    get_record_insights_fn: Callable[[], Awaitable[list[dict[str, Any]]]]
    search_user_vocabulary_fn: Callable[[str], Awaitable[list[dict[str, Any]]]]
    lookup_dictionary_entry_fn: Callable[[str | None, int | None, str | None, str | None, int | None], Awaitable[dict[str, Any] | None]]
    run_dictionary_ai_context_explain_fn: Callable[[str, int, str, Literal["word", "phrase"], int | None], Awaitable[dict[str, Any] | None]]
    generate_sentence_annotation_fn: Callable[[Literal["grammar_note", "sentence_analysis"]], Awaitable[dict[str, Any] | None]]
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


def _tool_observation(result: Any) -> tuple[str, list[str], list[str]]:
    if isinstance(result, dict):
        summary = str(result.get("summary") or result.get("reason") or "Loaded")
        next_actions = [
            str(item).strip()
            for item in result.get("next_actions") or []
            if isinstance(item, str) and item.strip()
        ]
        artifacts = [
            str(item).strip()
            for item in result.get("artifacts") or []
            if isinstance(item, str) and item.strip()
        ]
        return summary, next_actions, artifacts
    if isinstance(result, list):
        return f"{len(result)} item(s)", [], []
    return "Loaded", [], []


async def _run_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    tool_name: str,
    runner: Callable[[], Awaitable[Any]],
    *,
    input_summary: str | None = None,
) -> Any:
    deps = ctx.deps
    deps.state.tool_call_count += 1
    if deps.state.tool_call_count > deps.state.max_tool_calls:
        detail = (
            f"Tool call limit exceeded ({deps.state.max_tool_calls}). "
            "Please provide a direct answer without additional tool calls."
        )
        deps.state.tool_trace.append(
            _tool_trace(
                tool_name,
                "failed",
                input_summary=input_summary,
                summary=detail,
                next_actions=["Answer directly without more tool calls."],
            )
        )
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise RuntimeError(detail)
    deps.state.tool_trace.append(_tool_trace(tool_name, "started", input_summary=input_summary))
    await _emit_tool_event(deps, "tool.started", tool_name=tool_name)
    try:
        result = await runner()
    except Exception as exc:
        detail = str(exc) or "Tool failed"
        deps.state.tool_trace.append(
            _tool_trace(
                tool_name,
                "failed",
                input_summary=input_summary,
                summary=detail,
                next_actions=["Retry only after clarifying the missing input or context."],
            )
        )
        await _emit_tool_event(deps, "tool.failed", tool_name=tool_name, detail=detail)
        raise
    summary, next_actions, artifacts = _tool_observation(result)
    deps.state.tool_trace.append(
        _tool_trace(
            tool_name,
            "completed",
            input_summary=input_summary,
            summary=summary,
            next_actions=next_actions,
            artifacts=artifacts,
        )
    )
    await _emit_tool_event(deps, "tool.completed", tool_name=tool_name, summary=summary)
    return result


async def _generate_sentence_annotation_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    kind: Literal["grammar_note", "sentence_analysis"],
) -> dict[str, Any] | None:
    """Agent tool: generate sentence annotation with cache short-circuit.

    If a pre-generated annotation of the same kind already exists (from the
    quick-action path), return it directly without consuming tool budget.
    This is the backend protection layer — even if the prompt fails to
    prevent the agent from calling this tool, the budget is preserved.
    """
    existing = next(
        (
            item
            for item in reversed(ctx.deps.state.latest_generated_annotations)
            if item.get("kind") == kind
        ),
        None,
    )
    if existing is not None:
        return existing

    async def runner() -> dict[str, Any] | None:
        item = await ctx.deps.generate_sentence_annotation_fn(kind)
        if item is not None:
            ctx.deps.state.source_labels.add("record_assets")
            ctx.deps.state.latest_generated_annotations.append(item)
        return item

    return await _run_tool(
        ctx,
        "generate_sentence_annotation",
        runner,
        input_summary=f"kind={kind}",
    )


_NO_ANCHOR_ERROR: dict[str, Any] = {
    "status": "error",
    "summary": "No anchor available",
    "next_actions": ["Ask the user to select a sentence or text span first."],
    "artifacts": [],
}


async def _propose_save_note_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    note_text: str | None = None,
) -> dict[str, Any]:
    """Agent tool: propose saving a note with anchor precondition check.

    If no primary_anchor exists, returns error directly without consuming
    tool budget. This is the backend protection layer.
    """
    if ctx.deps.primary_anchor is None:
        return _NO_ANCHOR_ERROR

    async def runner() -> dict[str, Any]:
        if not isinstance(note_text, str) or not note_text.strip():
            return {
                "status": "error",
                "summary": "Missing note_text",
                "next_actions": ["Provide the note content before proposing save_note."],
                "artifacts": [],
            }
        ctx.deps.state.action_requests.append(
            ReaderAskRuntimeActionRequest(
                action_type="save_note",
                label="保存为笔记",
                description="把当前解释或补充内容保存到当前锚点笔记",
                payload_json={
                    "record_id": ctx.deps.record_id,
                    "anchor": ctx.deps.primary_anchor.model_dump(mode="json"),
                    "note_text": note_text,
                },
            )
        )
        return {
            "status": "success",
            "summary": "Prepared save_note confirmation",
            "next_actions": ["Wait for user confirmation before writing the note."],
            "artifacts": [f"record:{ctx.deps.record_id}", f"anchor:{ctx.deps.primary_anchor.target_key or 'selected'}"],
            "ok": True,
            "action_type": "save_note",
        }

    return await _run_tool(ctx, "propose_save_note", runner, input_summary=_truncate_tool_arg(note_text))


async def _propose_save_highlight_tool(
    ctx: RunContext[ReaderAskAgentDeps],
) -> dict[str, Any]:
    """Agent tool: propose saving a highlight with anchor precondition check.

    If no primary_anchor exists, returns error directly without consuming
    tool budget. This is the backend protection layer.
    """
    if ctx.deps.primary_anchor is None:
        return _NO_ANCHOR_ERROR

    async def runner() -> dict[str, Any]:
        ctx.deps.state.action_requests.append(
            ReaderAskRuntimeActionRequest(
                action_type="save_highlight",
                label="保存为高亮",
                description="把当前锚点保存成高亮/摘录",
                payload_json={
                    "record_id": ctx.deps.record_id,
                    "anchor": ctx.deps.primary_anchor.model_dump(mode="json"),
                },
            )
        )
        return {
            "status": "success",
            "summary": "Prepared save_highlight confirmation",
            "next_actions": ["Wait for user confirmation before saving the highlight."],
            "artifacts": [f"record:{ctx.deps.record_id}", f"anchor:{ctx.deps.primary_anchor.target_key or 'selected'}"],
            "ok": True,
            "action_type": "save_highlight",
        }

    return await _run_tool(ctx, "propose_save_highlight", runner)


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

    @agent.tool(name="search_user_vocabulary")
    async def search_user_vocabulary(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str,
    ) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            items = await ctx.deps.search_user_vocabulary_fn(query)
            if items:
                ctx.deps.state.source_labels.add("vocabulary")
                ctx.deps.state.latest_user_vocabulary = items
            for item in items:
                _append_citation(ctx.deps.state, ctx.deps.vocabulary_item_to_citation_fn(item))
            return items

        return await _run_tool(ctx, "search_user_vocabulary", runner, input_summary=_truncate_tool_arg(query))

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

        summary_bits = [query, str(entry_id) if entry_id is not None else None, query_type, context_sentence]
        return await _run_tool(
            ctx,
            "lookup_dictionary_entry",
            runner,
            input_summary=_truncate_tool_arg(" | ".join(bit for bit in summary_bits if bit)),
        )

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

        return await _run_tool(
            ctx,
            "run_dictionary_ai_context_explain",
            runner,
            input_summary=_truncate_tool_arg(query),
        )

    @agent.tool(name="generate_sentence_annotation")
    async def generate_sentence_annotation(
        ctx: RunContext[ReaderAskAgentDeps],
        kind: Literal["grammar_note", "sentence_analysis"],
    ) -> dict[str, Any] | None:
        return await _generate_sentence_annotation_tool(ctx, kind)

    @agent.tool(name="propose_save_note")
    async def propose_save_note(
        ctx: RunContext[ReaderAskAgentDeps],
        note_text: str | None = None,
    ) -> dict[str, Any]:
        return await _propose_save_note_tool(ctx, note_text)

    @agent.tool(name="propose_save_highlight")
    async def propose_save_highlight(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        return await _propose_save_highlight_tool(ctx)

    return agent


def _truncate_tool_arg(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:120]
