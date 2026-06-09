from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic_ai import Agent, RunContext

from app.agents.reader_ask_tool_policy import ToolAvailabilityResult
from app.agents.reader_ask_tool_registry import (
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_LOOKUP_DICTIONARY_ENTRY,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
    TOOL_SEARCH_USER_VOCABULARY,
)
from app.agents.reader_ask_tool_runtime import (
    ToolEventName,
    run_tool,
    truncate_tool_arg,
)
from app.agents.reader_ask_write_gate import (
    MISSING_NOTE_TEXT_PAYLOAD,
    check_write_proposal_precondition,
)
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation, ReaderAskToolTraceEntry
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


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
    event_queue: asyncio.Queue[tuple[ToolEventName, dict[str, Any]]]
    state: ReaderAskRuntimeState
    query_seed: str
    task_mode: Literal["explain", "breakdown", "vocabulary", "grammar", "practice", "general"]
    record_id: str
    record_title: str | None
    primary_anchor: ReaderAskAnchorRef | None
    get_record_context_fn: Callable[[], Awaitable[dict[str, Any]]]
    get_record_insights_fn: Callable[[], Awaitable[list[dict[str, Any]]]]
    search_user_vocabulary_fn: Callable[[str], Awaitable[list[dict[str, Any]]]]
    lookup_dictionary_entry_fn: Callable[
        [str | None, int | None, str | None, str | None, int | None],
        Awaitable[dict[str, Any] | None],
    ]
    run_dictionary_ai_context_explain_fn: Callable[
        [str, int, str, Literal["word", "phrase"], int | None],
        Awaitable[dict[str, Any] | None],
    ]
    generate_sentence_annotation_fn: Callable[
        [Literal["grammar_note", "sentence_analysis"]],
        Awaitable[dict[str, Any] | None],
    ]
    vocabulary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation]
    dictionary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation]
    dictionary_ai_to_citation_fn: Callable[[dict[str, Any], str, int], ReaderAskCitation]
    tool_availability: ToolAvailabilityResult | None = None


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

    return await run_tool(
        ctx.deps,
        TOOL_GENERATE_SENTENCE_ANNOTATION,
        runner,
        input_summary=f"kind={kind}",
    )


async def _propose_save_note_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    note_text: str | None = None,
) -> dict[str, Any]:
    """Agent tool: propose saving a note with write-gate precondition check.

    If the write gate rejects the proposal, returns the error payload
    directly without consuming tool budget.  This is the backend protection
    layer.
    """
    precondition = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_NOTE,
        has_primary_anchor=ctx.deps.primary_anchor is not None,
    )
    if not precondition.allowed:
        assert precondition.error_payload is not None
        return precondition.error_payload
    anchor = ctx.deps.primary_anchor
    assert anchor is not None

    async def runner() -> dict[str, Any]:
        if not isinstance(note_text, str) or not note_text.strip():
            return MISSING_NOTE_TEXT_PAYLOAD
        ctx.deps.state.action_requests.append(
            ReaderAskRuntimeActionRequest(
                action_type="save_note",
                label="保存为笔记",
                description="把当前解释或补充内容保存到当前锚点笔记",
                payload_json={
                    "record_id": ctx.deps.record_id,
                    "anchor": anchor.model_dump(mode="json"),
                    "note_text": note_text,
                },
            )
        )
        return {
            "status": "success",
            "summary": "Prepared save_note confirmation",
            "next_actions": ["Wait for user confirmation before writing the note."],
            "artifacts": [
                f"record:{ctx.deps.record_id}",
                f"anchor:{anchor.target_key or 'selected'}",
            ],
            "ok": True,
            "action_type": "save_note",
        }

    return await run_tool(
        ctx.deps, TOOL_PROPOSE_SAVE_NOTE, runner,
        input_summary=truncate_tool_arg(note_text),
    )


async def _propose_save_highlight_tool(
    ctx: RunContext[ReaderAskAgentDeps],
) -> dict[str, Any]:
    """Agent tool: propose saving a highlight with write-gate precondition check.

    If the write gate rejects the proposal, returns the error payload
    directly without consuming tool budget.  This is the backend protection
    layer.
    """
    precondition = check_write_proposal_precondition(
        TOOL_PROPOSE_SAVE_HIGHLIGHT,
        has_primary_anchor=ctx.deps.primary_anchor is not None,
    )
    if not precondition.allowed:
        assert precondition.error_payload is not None
        return precondition.error_payload
    anchor = ctx.deps.primary_anchor
    assert anchor is not None

    async def runner() -> dict[str, Any]:
        ctx.deps.state.action_requests.append(
            ReaderAskRuntimeActionRequest(
                action_type="save_highlight",
                label="保存为高亮",
                description="把当前锚点保存成高亮/摘录",
                payload_json={
                    "record_id": ctx.deps.record_id,
                    "anchor": anchor.model_dump(mode="json"),
                },
            )
        )
        return {
            "status": "success",
            "summary": "Prepared save_highlight confirmation",
            "next_actions": ["Wait for user confirmation before saving the highlight."],
            "artifacts": [
                f"record:{ctx.deps.record_id}",
                f"anchor:{anchor.target_key or 'selected'}",
            ],
            "ok": True,
            "action_type": "save_highlight",
        }

    return await run_tool(ctx.deps, TOOL_PROPOSE_SAVE_HIGHLIGHT, runner)


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

    @agent.tool(name=TOOL_GET_RECORD_CONTEXT)
    async def get_record_context(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        async def runner() -> dict[str, Any]:
            ctx.deps.state.source_labels.update({"current_record", "current_anchor"})
            ctx.deps.state.source_labels.add("current_paragraph")
            result = await ctx.deps.get_record_context_fn()
            ctx.deps.state.latest_record_context = result
            return result

        return await run_tool(ctx.deps, TOOL_GET_RECORD_CONTEXT, runner)

    @agent.tool(name=TOOL_GET_RECORD_INSIGHTS)
    async def get_record_insights(ctx: RunContext[ReaderAskAgentDeps]) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            items = await ctx.deps.get_record_insights_fn()
            if items:
                ctx.deps.state.source_labels.add("record_assets")
            ctx.deps.state.latest_record_insights = items
            return items

        return await run_tool(ctx.deps, TOOL_GET_RECORD_INSIGHTS, runner)

    @agent.tool(name=TOOL_SEARCH_USER_VOCABULARY)
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

        return await run_tool(
            ctx.deps, TOOL_SEARCH_USER_VOCABULARY, runner,
            input_summary=truncate_tool_arg(query),
        )

    @agent.tool(name=TOOL_LOOKUP_DICTIONARY_ENTRY)
    async def lookup_dictionary_entry(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str | None = None,
        entry_id: int | None = None,
        query_type: Literal["word", "phrase"] | None = None,
        context_sentence: str | None = None,
        occurrence: int | None = None,
    ) -> dict[str, Any] | None:
        async def runner() -> dict[str, Any] | None:
            item = await ctx.deps.lookup_dictionary_entry_fn(
                query, entry_id, query_type, context_sentence, occurrence,
            )
            if item is not None:
                ctx.deps.state.source_labels.add("dictionary")
                ctx.deps.state.latest_dictionary_entry = item
                _append_citation(ctx.deps.state, ctx.deps.dictionary_item_to_citation_fn(item))
            return item

        summary_bits = [
            query,
            str(entry_id) if entry_id is not None else None,
            query_type,
            context_sentence,
        ]
        return await run_tool(
            ctx.deps,
            TOOL_LOOKUP_DICTIONARY_ENTRY,
            runner,
            input_summary=truncate_tool_arg(" | ".join(bit for bit in summary_bits if bit)),
        )

    @agent.tool(name=TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN)
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

        return await run_tool(
            ctx.deps,
            TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
            runner,
            input_summary=truncate_tool_arg(query),
        )

    @agent.tool(name=TOOL_GENERATE_SENTENCE_ANNOTATION)
    async def generate_sentence_annotation(
        ctx: RunContext[ReaderAskAgentDeps],
        kind: Literal["grammar_note", "sentence_analysis"],
    ) -> dict[str, Any] | None:
        return await _generate_sentence_annotation_tool(ctx, kind)

    @agent.tool(name=TOOL_PROPOSE_SAVE_NOTE)
    async def propose_save_note(
        ctx: RunContext[ReaderAskAgentDeps],
        note_text: str | None = None,
    ) -> dict[str, Any]:
        return await _propose_save_note_tool(ctx, note_text)

    @agent.tool(name=TOOL_PROPOSE_SAVE_HIGHLIGHT)
    async def propose_save_highlight(ctx: RunContext[ReaderAskAgentDeps]) -> dict[str, Any]:
        return await _propose_save_highlight_tool(ctx)

    return agent
