"""Ask Claread agent deps factory — single entry point for ReaderAskAgentDeps construction.

Every code path that creates a ``ReaderAskAgentDeps`` instance must go through
``build_reader_ask_agent_deps`` so that ``tool_availability`` is always wired
consistently and the construction surface stays auditable.

Round 2: tool callbacks now carry the model-facing parameters (scope,
target_sentence_id, lemma, sort_by, query, top_k, suggestions, etc.).
The agent module is the single source of truth for the Round 2 contracts;
the callbacks close over per-run state (record bundle, anchors, user_id).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from app.agents.reader_ask_agent import (
    InsightKind,
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    RecordContextScope,
    VocabularySortBy,
)
from app.agents.reader_ask_tool_policy import (
    ToolAvailabilityInput,
    build_tool_availability,
)
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskCitation,
    ReaderAskEntryAction,
    ReaderAskTaskMode,
)


def build_reader_ask_agent_deps(
    *,
    payload: dict[str, Any],
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    state: ReaderAskRuntimeState,
    query_seed: str,
    task_mode: ReaderAskTaskMode,
    entry_action: ReaderAskEntryAction,
    record_id: str,
    record_title: str | None,
    primary_anchor: ReaderAskAnchorRef | None,
    # Round 2 tool callbacks (model-facing parameters).
    get_record_context_fn: Callable[
        [ReaderAskAgentDeps | None, RecordContextScope | None, str | None],
        Awaitable[dict[str, Any]],
    ],
    get_record_insights_fn: Callable[
        [ReaderAskAgentDeps | None, str | None, InsightKind | None, int | None],
        Awaitable[list[dict[str, Any]]],
    ],
    get_user_vocabulary_book_fn: Callable[
        [ReaderAskAgentDeps | None, str | None, int | None, VocabularySortBy | None],
        Awaitable[list[dict[str, Any]]],
    ],
    resolve_known_reference_fn: Callable[
        [ReaderAskAgentDeps | None, str, int | None],
        Awaitable[dict[str, Any]],
    ],
    load_explicit_attachment_context_fn: Callable[
        [ReaderAskAgentDeps | None, str, str | None],
        Awaitable[dict[str, Any]],
    ],
    generate_sentence_annotation_fn: Callable[
        [Literal["grammar_note", "sentence_analysis"]],
        Awaitable[dict[str, Any] | None],
    ],
    suggest_prompts_fn: Callable[
        [list[dict[str, Any]]],
        Awaitable[dict[str, Any]],
    ],
    vocabulary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation],
    has_dictionary_anchor: bool = False,
    has_generated_annotation_cache: bool = False,
    allowed_external_attachments: list[dict[str, str]] | None = None,
) -> ReaderAskAgentDeps:
    """Construct a fully-wired ``ReaderAskAgentDeps`` with tool availability.

    All parameters are explicit — no context-object shortcut.  The factory
    centralises the ``build_tool_availability(ToolAvailabilityInput(...))``
    call so that every agent run / replan / retry path injects tool
    availability consistently.
    """
    tool_availability = build_tool_availability(
        ToolAvailabilityInput(
            task_mode=task_mode,
            entry_action=entry_action,
            has_primary_anchor=primary_anchor is not None,
            has_dictionary_anchor=has_dictionary_anchor,
            has_generated_annotation_cache=has_generated_annotation_cache,
        )
    )

    return ReaderAskAgentDeps(
        payload=payload,
        event_queue=event_queue,
        state=state,
        query_seed=query_seed,
        task_mode=task_mode,
        record_id=record_id,
        record_title=record_title,
        primary_anchor=primary_anchor,
        get_record_context_fn=get_record_context_fn,
        get_record_insights_fn=get_record_insights_fn,
        get_user_vocabulary_book_fn=get_user_vocabulary_book_fn,
        resolve_known_reference_fn=resolve_known_reference_fn,
        load_explicit_attachment_context_fn=load_explicit_attachment_context_fn,
        allowed_external_attachments=allowed_external_attachments or [],
        generate_sentence_annotation_fn=generate_sentence_annotation_fn,
        suggest_prompts_fn=suggest_prompts_fn,
        vocabulary_item_to_citation_fn=vocabulary_item_to_citation_fn,
        tool_availability=tool_availability,
    )
