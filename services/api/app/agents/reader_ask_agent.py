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
    TOOL_GET_USER_VOCABULARY_BOOK,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RESOLVE_KNOWN_REFERENCE,
    TOOL_SUGGEST_PROMPTS,
    agent_callable_tool_names,
)
from app.agents.reader_ask_tool_runtime import (
    run_tool,
    truncate_tool_arg,
)
from app.agents.reader_ask_write_gate import (
    MISSING_NOTE_TEXT_PAYLOAD,
    check_write_proposal_precondition,
)
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation, ReaderAskToolTraceEntry
from app.services.analysis.prompting.prompt_loader import load_agent_instructions


# ---------------------------------------------------------------------------
# Round 2: tool IO contracts (stable, model-facing)
#
# Each contract is the explicit shape the main agent sees. Keeping them as
# ``dataclass``/TypedDict definitions makes tool behavior auditable from
# tests without having to round-trip through the LLM.
# ---------------------------------------------------------------------------

RecordContextScope = Literal["window", "paragraph", "full"]
InsightKind = Literal["grammar_note", "sentence_analysis", "vocabulary"]
VocabularySortBy = Literal["recent", "lemma_asc"]
ReferenceResolutionStatus = Literal["resolved", "ambiguous", "not_found"]


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
    cross_record_context_allowed: bool = False
    tool_call_count: int = 0
    max_tool_calls: int = 5
    latest_record_context: dict[str, Any] | None = None
    latest_record_insights: list[dict[str, Any]] = field(default_factory=list)
    latest_article_overview: str | None = None
    latest_external_record_contexts: list[dict[str, Any]] = field(default_factory=list)
    latest_external_asset_contexts: list[dict[str, Any]] = field(default_factory=list)
    latest_user_vocabulary: list[dict[str, Any]] = field(default_factory=list)
    latest_resolved_references: dict[str, Any] | None = None
    latest_generated_annotations: list[dict[str, Any]] = field(default_factory=list)
    latest_suggestions: list[dict[str, Any]] = field(default_factory=list)
    # Round 1 — fast-path routing telemetry. ``planner_skipped`` is True
    # when the request was eligible for the agent-loop fast path and
    # bypassed the legacy ``resolve_semantic_planning`` call.
    # ``planner_route_used`` records which path actually ran for billing
    # and eval: "planner_first" (legacy), "fast_path", or
    # "agent_loop_first" (Round 3 agent-loop-first entry).
    planner_skipped: bool = False
    planner_route_used: str = "planner_first"
    # Round 3 — degenerate-loop detection telemetry.
    degenerate_detected: bool = False
    degenerate_reason: str | None = None
    # Round 6 — observability: latency tracking.
    first_token_at: str | None = None  # ISO 8601, first text delta time
    run_started_at: str | None = None  # ISO 8601, run entry time


@dataclass(slots=True)
class ReaderAskAgentDeps:
    payload: dict[str, Any]
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]]
    state: ReaderAskRuntimeState
    query_seed: str
    task_mode: Literal["explain", "breakdown", "vocabulary", "grammar", "practice", "general"]
    record_id: str
    record_title: str | None
    primary_anchor: ReaderAskAnchorRef | None
    # Round 2 tool contracts.
    get_record_context_fn: Callable[
        ["ReaderAskAgentDeps" | None, RecordContextScope | None, str | None],
        Awaitable[dict[str, Any]],
    ]
    get_record_insights_fn: Callable[
        ["ReaderAskAgentDeps" | None, str | None, InsightKind | None, int | None],
        Awaitable[list[dict[str, Any]]],
    ]
    get_user_vocabulary_book_fn: Callable[
        ["ReaderAskAgentDeps" | None, str | None, int | None, VocabularySortBy | None],
        Awaitable[list[dict[str, Any]]],
    ]
    resolve_known_reference_fn: Callable[
        ["ReaderAskAgentDeps" | None, str, int | None],
        Awaitable[dict[str, Any]],
    ]
    generate_sentence_annotation_fn: Callable[
        [Literal["grammar_note", "sentence_analysis"]],
        Awaitable[dict[str, Any] | None],
    ]
    suggest_prompts_fn: Callable[
        [list[dict[str, Any]]],
        Awaitable[dict[str, Any]],
    ]
    vocabulary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation]
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


# ---------------------------------------------------------------------------
# Read tools (Round 2)
# ---------------------------------------------------------------------------


async def _get_record_context_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    scope: RecordContextScope = "window",
    target_sentence_id: str | None = None,
) -> dict[str, Any]:
    """Agent tool: get the current record's local context window.

    Scope contract:
    - ``window`` (default): 5 sentences around the active anchor
      (2 before + active + 2 after). Cheap.
    - ``paragraph``: the full paragraph containing the target sentence.
    - ``full``: the full article text. The implementation MUST apply a
      length cap (default 10000 chars) and mark ``truncated: true`` if hit.
    """
    async def runner() -> dict[str, Any]:
        ctx.deps.state.source_labels.update({"current_record", "current_anchor"})
        if scope == "paragraph":
            ctx.deps.state.source_labels.add("current_paragraph")
        elif scope == "full":
            ctx.deps.state.source_labels.add("full_article")
        result = await ctx.deps.get_record_context_fn(
            ctx.deps, scope, target_sentence_id,
        )
        ctx.deps.state.latest_record_context = result
        return result

    summary_bits: list[str] = [f"scope={scope}"]
    if target_sentence_id:
        summary_bits.append(f"target={target_sentence_id}")
    return await run_tool(
        ctx.deps,
        TOOL_GET_RECORD_CONTEXT,
        runner,
        input_summary=" ".join(summary_bits),
    )


async def _get_record_insights_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    target_sentence_id: str | None = None,
    kind: InsightKind | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Agent tool: get insights for the current record.

    The agent MUST pass at least one of ``target_sentence_id`` or ``kind``;
    unfiltered full loads are wasteful and the prompt forbids them. The
    result items carry ``translation_zh`` (workflow-generated) so the model
    can ground its translation/explanation in what the user already sees.
    """
    if target_sentence_id is None and kind is None:
        return [
            {
                "status": "error",
                "summary": "get_record_insights requires at least one of target_sentence_id or kind.",
                "next_actions": [
                    "Pass target_sentence_id for a specific sentence.",
                    "Pass kind='grammar_note' | 'sentence_analysis' | 'vocabulary' to filter by type.",
                ],
                "artifacts": [],
                "ok": False,
                "reason": "missing_filter",
            }
        ]

    async def runner() -> list[dict[str, Any]]:
        items = await ctx.deps.get_record_insights_fn(
            ctx.deps, target_sentence_id, kind, limit,
        )
        if items:
            ctx.deps.state.source_labels.add("record_assets")
        ctx.deps.state.latest_record_insights = items
        return items

    summary_bits: list[str] = []
    if kind:
        summary_bits.append(f"kind={kind}")
    if target_sentence_id:
        summary_bits.append(f"target={target_sentence_id}")
    summary_bits.append(f"limit={limit}")
    return await run_tool(
        ctx.deps,
        TOOL_GET_RECORD_INSIGHTS,
        runner,
        input_summary=" ".join(summary_bits) or "filtered",
    )


async def _get_user_vocabulary_book_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    lemma: str | None = None,
    limit: int = 10,
    sort_by: VocabularySortBy = "recent",
) -> list[dict[str, Any]] | dict[str, Any]:
    """Agent tool: list the user's vocabulary book entries.

    The vocabulary backend does not support search; we load all entries
    and filter by ``lemma`` in memory. The agent MUST pass at least one
    of ``lemma`` or ``sort_by`` (default ``sort_by='recent'``). When
    ``lemma`` is provided and no entry matches, return a warning
    observation dict (not a list) so the normalizer surfaces it as a
    warning instead of "0 item(s)" success.
    """
    async def runner() -> list[dict[str, Any]] | dict[str, Any]:
        items = await ctx.deps.get_user_vocabulary_book_fn(
            ctx.deps, lemma, limit, sort_by,
        )
        if items:
            ctx.deps.state.source_labels.add("vocabulary")
            ctx.deps.state.latest_user_vocabulary = items
            for item in items:
                _append_citation(
                    ctx.deps.state,
                    ctx.deps.vocabulary_item_to_citation_fn(item),
                )
            return items
        # No match — surface a warning observation (dict) so the agent
        # doesn't loop on empty results and the trace/chip is honest
        # about "not found".
        return {
            "status": "warning",
            "summary": "Word not in vocabulary book",
            "next_actions": [
                "Ask the user if they want to save the word.",
            ],
            "artifacts": [],
            "ok": False,
            "reason": "lemma_not_found",
        }

    summary_bits: list[str] = [f"sort_by={sort_by}", f"limit={limit}"]
    if lemma:
        summary_bits.insert(0, f"lemma={truncate_tool_arg(lemma)}")
    return await run_tool(
        ctx.deps,
        TOOL_GET_USER_VOCABULARY_BOOK,
        runner,
        input_summary=" ".join(summary_bits),
    )


# ---------------------------------------------------------------------------
# Resolver tool (Round 2)
# ---------------------------------------------------------------------------


async def _resolve_known_reference_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """Agent tool: resolve a cross-record reference.

    Returns one of three states: ``resolved`` (single match), ``ambiguous``
    (multiple candidates — the agent should ask the user via HITL) or
    ``not_found`` (zero matches). The implementation reuses the existing
    known-reference resolver backend and never causes a cross-HTTP
    resume — ambiguous results are returned to the model so the main
    loop can decide how to present them.
    """
    async def runner() -> dict[str, Any]:
        result = await ctx.deps.resolve_known_reference_fn(ctx.deps, query, top_k)
        # Always record the resolution, even when status='not_found'.
        ctx.deps.state.latest_resolved_references = result
        status = result.get("status") if isinstance(result, dict) else None
        if status == "resolved":
            ctx.deps.state.source_labels.add("cross_record_resolved")
        elif status == "ambiguous":
            ctx.deps.state.source_labels.add("cross_record_ambiguous")
        return result

    return await run_tool(
        ctx.deps,
        TOOL_RESOLVE_KNOWN_REFERENCE,
        runner,
        input_summary=truncate_tool_arg(query),
    )


# ---------------------------------------------------------------------------
# Annotation tool (cache-aware)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Write-proposal tools (with write-gate precondition)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Suggestion tool (Round 2)
# ---------------------------------------------------------------------------


async def _suggest_prompts_tool(
    ctx: RunContext[ReaderAskAgentDeps],
    suggestions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Agent tool: surface 2-3 follow-up prompt suggestions.

    The agent decides when to call this (e.g. when the user is drifting
    off-topic, after a complete in-scope answer, etc.). The backend
    validates the shape: 2-3 suggestions, each with a short ``label``
    and the actual ``prompt`` text. The frontend renders them as
    clickable chips at the tail of the assistant message; this round
    only wires up the contract, not the front-end.
    """
    if not suggestions:
        return {
            "status": "warning",
            "summary": "No suggestions provided.",
            "next_actions": ["Provide 2-3 suggestions if you want chips to render."],
            "artifacts": [],
            "ok": False,
            "suggestions": [],
        }

    # Validate shape and clamp to 2-3.
    cleaned: list[dict[str, Any]] = []
    for item in suggestions[:3]:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        prompt = item.get("prompt")
        if not isinstance(label, str) or not label.strip():
            continue
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        cleaned.append(
            {
                "label": label.strip()[:40],
                "prompt": prompt.strip()[:200],
            }
        )
    if len(cleaned) < 2:
        return {
            "status": "warning",
            "summary": "Need at least 2 valid suggestions to render chips.",
            "next_actions": [
                "Provide 2-3 suggestions with both 'label' and 'prompt'.",
            ],
            "artifacts": [],
            "ok": False,
            "suggestions": [],
        }

    async def runner() -> dict[str, Any]:
        result = await ctx.deps.suggest_prompts_fn(cleaned)
        ctx.deps.state.latest_suggestions = cleaned
        return result

    return await run_tool(
        ctx.deps,
        TOOL_SUGGEST_PROMPTS,
        runner,
        input_summary=f"{len(cleaned)} suggestion(s)",
    )


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


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
    async def get_record_context(
        ctx: RunContext[ReaderAskAgentDeps],
        scope: RecordContextScope = "window",
        target_sentence_id: str | None = None,
    ) -> dict[str, Any]:
        return await _get_record_context_tool(ctx, scope, target_sentence_id)

    @agent.tool(name=TOOL_GET_RECORD_INSIGHTS)
    async def get_record_insights(
        ctx: RunContext[ReaderAskAgentDeps],
        target_sentence_id: str | None = None,
        kind: InsightKind | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return await _get_record_insights_tool(ctx, target_sentence_id, kind, limit)

    @agent.tool(name=TOOL_GET_USER_VOCABULARY_BOOK)
    async def get_user_vocabulary_book(
        ctx: RunContext[ReaderAskAgentDeps],
        lemma: str | None = None,
        limit: int = 10,
        sort_by: VocabularySortBy = "recent",
    ) -> list[dict[str, Any]]:
        return await _get_user_vocabulary_book_tool(ctx, lemma, limit, sort_by)

    @agent.tool(name=TOOL_RESOLVE_KNOWN_REFERENCE)
    async def resolve_known_reference(
        ctx: RunContext[ReaderAskAgentDeps],
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        return await _resolve_known_reference_tool(ctx, query, top_k)

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

    @agent.tool(name=TOOL_SUGGEST_PROMPTS)
    async def suggest_prompts(
        ctx: RunContext[ReaderAskAgentDeps],
        suggestions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await _suggest_prompts_tool(ctx, suggestions)

    # Round 5: verify agent tool surface matches registry.
    # Uses explicit RuntimeError (not assert) so the check is not stripped
    # under PYTHONOPTIMIZE=1 / python -O.
    _registered = frozenset(agent._function_toolset.tools.keys())
    _expected = agent_callable_tool_names()
    if _registered != _expected:
        raise RuntimeError(
            f"Agent tool surface mismatch: registered={_registered - _expected}, "
            f"missing={_expected - _registered}"
        )

    return agent
