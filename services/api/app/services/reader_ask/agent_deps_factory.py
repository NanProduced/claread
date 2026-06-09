"""Ask Claread agent deps factory — single entry point for ReaderAskAgentDeps construction.

Every code path that creates a ``ReaderAskAgentDeps`` instance must go through
``build_reader_ask_agent_deps`` so that ``tool_availability`` is always wired
consistently and the construction surface stays auditable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
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
    get_record_context_fn: Callable[[], Awaitable[dict[str, Any]]],
    get_record_insights_fn: Callable[[], Awaitable[list[dict[str, Any]]]],
    search_user_vocabulary_fn: Callable[[str], Awaitable[list[dict[str, Any]]]],
    lookup_dictionary_entry_fn: Callable[
        [str | None, int | None, str | None, str | None, int | None],
        Awaitable[dict[str, Any] | None],
    ],
    run_dictionary_ai_context_explain_fn: Callable[
        [str, int, str, Literal["word", "phrase"], int | None],
        Awaitable[dict[str, Any] | None],
    ],
    generate_sentence_annotation_fn: Callable[
        [Literal["grammar_note", "sentence_analysis"]],
        Awaitable[dict[str, Any] | None],
    ],
    vocabulary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation],
    dictionary_item_to_citation_fn: Callable[[dict[str, Any]], ReaderAskCitation],
    dictionary_ai_to_citation_fn: Callable[[dict[str, Any], str, int], ReaderAskCitation],
    has_dictionary_anchor: bool = False,
    has_generated_annotation_cache: bool = False,
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
        search_user_vocabulary_fn=search_user_vocabulary_fn,
        lookup_dictionary_entry_fn=lookup_dictionary_entry_fn,
        run_dictionary_ai_context_explain_fn=run_dictionary_ai_context_explain_fn,
        generate_sentence_annotation_fn=generate_sentence_annotation_fn,
        vocabulary_item_to_citation_fn=vocabulary_item_to_citation_fn,
        dictionary_item_to_citation_fn=dictionary_item_to_citation_fn,
        dictionary_ai_to_citation_fn=dictionary_ai_to_citation_fn,
        tool_availability=tool_availability,
    )
