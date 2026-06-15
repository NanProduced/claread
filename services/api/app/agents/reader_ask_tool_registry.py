"""Ask Claread tool registry — single source of truth for tool metadata.

Every tool the reader-ask agent can invoke must have an entry here.
The registry is a static constant; do not add dynamic plugin logic.

This module is placed at ``app.agents.reader_ask_tool_registry`` (not inside
``app.services.reader_ask``) to avoid a circular import:
``reader_ask_agent`` → ``tool_registry`` must not trigger
``services.reader_ask.__init__`` → ``service`` → ``reader_ask_agent``.

Round 2 (Agent loop tool surface):
- ``agent_callable`` distinguishes tools the main agent may call
  (``True``) from tools that exist only as schema reservations / deprecated
  references (``False``).
- New tools added: ``get_user_vocabulary_book`` (replaces
  ``search_user_vocabulary``), ``resolve_known_reference``, ``suggest_prompts``.
- Reserved spec: ``lookup_record_by_embedding`` (``agent_callable=False``)
  — schema placeholder for the future pgvector-backed RAG.
- Deprecated (kept as schema entries, ``agent_callable=False``):
  ``search_user_vocabulary``, ``lookup_dictionary_entry``,
  ``run_dictionary_ai_context_explain``. They are not removed in Round 2
  to avoid breaking legacy call sites; the prompt no longer encourages
  them and the agent never sees them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolCategory = Literal["context", "vocabulary", "dictionary", "annotation", "write_proposal", "resolver", "suggestion"]
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

# Read / context tools (Round 2)
TOOL_GET_RECORD_CONTEXT = "get_record_context"
TOOL_GET_RECORD_INSIGHTS = "get_record_insights"
TOOL_GET_USER_VOCABULARY_BOOK = "get_user_vocabulary_book"

# Resolver tool (Round 2)
TOOL_RESOLVE_KNOWN_REFERENCE = "resolve_known_reference"

# Annotation tool
TOOL_GENERATE_SENTENCE_ANNOTATION = "generate_sentence_annotation"

# Write-proposal tools
TOOL_PROPOSE_SAVE_NOTE = "propose_save_note"
TOOL_PROPOSE_SAVE_HIGHLIGHT = "propose_save_highlight"

# Suggestion tool (Round 2)
TOOL_SUGGEST_PROMPTS = "suggest_prompts"

# Reserved for future RAG (Round 2 spec; not yet implemented as a tool)
TOOL_LOOKUP_RECORD_BY_EMBEDDING = "lookup_record_by_embedding"

# Deprecated (Round 2): retained as schema entries only, agent_callable=False.
TOOL_SEARCH_USER_VOCABULARY = "search_user_vocabulary"
TOOL_LOOKUP_DICTIONARY_ENTRY = "lookup_dictionary_entry"
TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN = "run_dictionary_ai_context_explain"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

READER_ASK_TOOL_REGISTRY: dict[str, ToolSpec] = {
    # ----- Read / context (Round 2: agent-callable) -----
    TOOL_GET_RECORD_CONTEXT: ToolSpec(
        name=TOOL_GET_RECORD_CONTEXT,
        category="context",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success", "warning"),
    ),
    TOOL_GET_RECORD_INSIGHTS: ToolSpec(
        name=TOOL_GET_RECORD_INSIGHTS,
        category="context",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="list_or_empty",
        observation_statuses=("success", "warning"),
    ),
    TOOL_GET_USER_VOCABULARY_BOOK: ToolSpec(
        name=TOOL_GET_USER_VOCABULARY_BOOK,
        category="vocabulary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="list_or_empty",
        observation_statuses=("success", "warning"),
    ),
    # ----- Resolver (Round 2: agent-callable) -----
    TOOL_RESOLVE_KNOWN_REFERENCE: ToolSpec(
        name=TOOL_RESOLVE_KNOWN_REFERENCE,
        category="resolver",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success", "warning"),
    ),
    # ----- Annotation -----
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
    # ----- Write-proposal -----
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
    # ----- Suggestion (Round 2: agent-callable) -----
    TOOL_SUGGEST_PROMPTS: ToolSpec(
        name=TOOL_SUGGEST_PROMPTS,
        category="suggestion",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=True,
        output_kind="dict_or_none",
        observation_statuses=("success", "warning"),
    ),
    # ----- Reserved for future RAG (Round 2 spec only; not implemented) -----
    TOOL_LOOKUP_RECORD_BY_EMBEDDING: ToolSpec(
        name=TOOL_LOOKUP_RECORD_BY_EMBEDDING,
        category="context",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=False,  # main agent must not call; reserved for RAG sprint
        output_kind="list_or_empty",
        observation_statuses=("success", "warning"),
    ),
    # ----- Deprecated (Round 2): agent_callable=False, kept as schema entries -----
    TOOL_SEARCH_USER_VOCABULARY: ToolSpec(
        name=TOOL_SEARCH_USER_VOCABULARY,
        category="vocabulary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=False,  # superseded by get_user_vocabulary_book
        output_kind="list_or_empty",
        observation_statuses=("success",),
    ),
    TOOL_LOOKUP_DICTIONARY_ENTRY: ToolSpec(
        name=TOOL_LOOKUP_DICTIONARY_ENTRY,
        category="dictionary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=False,  # Reader dictionary feature owns this; Ask agent does not call
        output_kind="dict_or_none",
        observation_statuses=("success",),
    ),
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN: ToolSpec(
        name=TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
        category="dictionary",
        effect="read",
        requires_anchor=False,
        consumes_budget_when_precondition_fails=True,
        agent_callable=False,  # deprecated; LLM self-answers in system prompt
        output_kind="dict_or_none",
        observation_statuses=("success",),
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


def is_agent_callable(name: str) -> bool:
    """Return ``True`` if *name* is an ``agent_callable`` tool.

    Tools with ``agent_callable=False`` (reserved spec, deprecated schema
    entries) are intentionally excluded from the main agent's tool list.
    """
    spec = READER_ASK_TOOL_REGISTRY.get(name)
    return spec is not None and spec.agent_callable


def agent_callable_tool_names() -> frozenset[str]:
    """Return the set of tool names the main agent may call."""
    return frozenset(
        spec.name for spec in READER_ASK_TOOL_REGISTRY.values() if spec.agent_callable
    )
