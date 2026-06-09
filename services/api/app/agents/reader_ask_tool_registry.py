"""Ask Claread tool registry — single source of truth for tool metadata.

Every tool the reader-ask agent can invoke must have an entry here.
The registry is a static constant; do not add dynamic plugin logic.

This module is placed at ``app.agents.reader_ask_tool_registry`` (not inside
``app.services.reader_ask``) to avoid a circular import:
``reader_ask_agent`` → ``tool_registry`` must not trigger
``services.reader_ask.__init__`` → ``service`` → ``reader_ask_agent``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolCategory = Literal["context", "vocabulary", "dictionary", "annotation", "write_proposal"]
ToolEffect = Literal["read", "propose_write"]
ToolOutputKind = Literal["dict_or_none", "list_or_empty", "dict_always"]
ToolObservationStatus = Literal["success", "warning", "error"]


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Stable metadata for a single Ask Claread tool.

    ``observation_statuses`` lists the ``status`` values that the tool
    implementation itself can return in its output dict.  It does **not**
    include statuses injected by the runtime wrapper layer (e.g. the
    ``tool_not_available`` error from availability hard enforcement in
    ``run_tool``).  Those runtime-layer errors are tested separately in
    ``test_reader_ask_tool_runtime.py``.
    """

    name: str
    category: ToolCategory
    effect: ToolEffect
    requires_anchor: bool
    consumes_budget_when_precondition_fails: bool
    agent_callable: bool
    output_kind: ToolOutputKind
    observation_statuses: tuple[ToolObservationStatus, ...]


# ---------------------------------------------------------------------------
# Stable tool-name constants
# ---------------------------------------------------------------------------

TOOL_GET_RECORD_CONTEXT = "get_record_context"
TOOL_GET_RECORD_INSIGHTS = "get_record_insights"
TOOL_SEARCH_USER_VOCABULARY = "search_user_vocabulary"
TOOL_LOOKUP_DICTIONARY_ENTRY = "lookup_dictionary_entry"
TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN = "run_dictionary_ai_context_explain"
TOOL_GENERATE_SENTENCE_ANNOTATION = "generate_sentence_annotation"
TOOL_PROPOSE_SAVE_NOTE = "propose_save_note"
TOOL_PROPOSE_SAVE_HIGHLIGHT = "propose_save_highlight"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

READER_ASK_TOOL_REGISTRY: dict[str, ToolSpec] = {
    TOOL_GET_RECORD_CONTEXT: ToolSpec(
        name=TOOL_GET_RECORD_CONTEXT,
        category="context",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success",),
    ),
    TOOL_GET_RECORD_INSIGHTS: ToolSpec(
        name=TOOL_GET_RECORD_INSIGHTS,
        category="context",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="list_or_empty",
        observation_statuses=("success",),
    ),
    TOOL_SEARCH_USER_VOCABULARY: ToolSpec(
        name=TOOL_SEARCH_USER_VOCABULARY,
        category="vocabulary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="list_or_empty",
        observation_statuses=("success",),
    ),
    TOOL_LOOKUP_DICTIONARY_ENTRY: ToolSpec(
        name=TOOL_LOOKUP_DICTIONARY_ENTRY,
        category="dictionary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success",),
    ),
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN: ToolSpec(
        name=TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
        category="dictionary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success",),
    ),
    TOOL_GENERATE_SENTENCE_ANNOTATION: ToolSpec(
        name=TOOL_GENERATE_SENTENCE_ANNOTATION,
        category="annotation",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success",),
    ),
    TOOL_PROPOSE_SAVE_NOTE: ToolSpec(
        name=TOOL_PROPOSE_SAVE_NOTE,
        category="write_proposal",
        effect="propose_write",
        requires_anchor=True,
        consumes_budget_when_precondition_fails=False,
        agent_callable=True,
        output_kind="dict_always",
        observation_statuses=("success", "error"),
    ),
    TOOL_PROPOSE_SAVE_HIGHLIGHT: ToolSpec(
        name=TOOL_PROPOSE_SAVE_HIGHLIGHT,
        category="write_proposal",
        effect="propose_write",
        requires_anchor=True,
        consumes_budget_when_precondition_fails=False,
        agent_callable=True,
        output_kind="dict_always",
        observation_statuses=("success", "error"),
    ),
}

READER_ASK_TOOL_NAMES: frozenset[str] = frozenset(READER_ASK_TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_tool_spec(name: str) -> ToolSpec | None:
    """Return the ToolSpec for *name*, or ``None`` if unknown."""
    return READER_ASK_TOOL_REGISTRY.get(name)


def is_write_proposal_tool(name: str) -> bool:
    """Return ``True`` if *name* is a write-proposal tool."""
    spec = READER_ASK_TOOL_REGISTRY.get(name)
    return spec is not None and spec.effect == "propose_write"


def requires_anchor(name: str) -> bool:
    """Return ``True`` if *name* requires an anchor before execution."""
    spec = READER_ASK_TOOL_REGISTRY.get(name)
    return spec is not None and spec.requires_anchor
