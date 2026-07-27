"""G2-Qwen: Qwen dashscope_responses Web Search wire fixture (OFFLINE).

**Status: PROBE REQUIRED**

This fixture is a hand-authored protocol draft built from public docs.
It is NOT an official capture and is NOT evidence that the wire shape
matches the real DashScope Responses endpoint. Every field below —
including the model name, tool shape, event ordering, and source
binding — must be validated by a real G3 smoke run before any GO
decision. Treat all conclusions as PROBE REQUIRED until backed by a
real SDK call capture.

This fixture captures the EXPECTED wire format for the Qwen3.7 Max Web
Search transport over DashScope's OpenAI-compatible Responses API. It is
built strictly from the official Aliyun Model Studio docs referenced in
``TMP-ask-web-search-qwen-2026-07-26.md``:

- https://help.aliyun.com/zh/model-studio/web-search/
- https://help.aliyun.com/en/model-studio/qwen-api-via-openai-responses
- https://help.aliyun.com/en/model-studio/deep-thinking
- https://help.aliyun.com/en/model-studio/qwen-function-calling

Non-goals
---------
- It does NOT call any real DashScope endpoint.
- It does NOT import the real ``dashscope`` SDK.
- It does NOT validate PydanticAI's ``OpenAIResponsesModel`` integration
  (that is a G3 smoke concern).
- It does NOT guess OpenAI-private fields. Per research:
  * ``search_context_size`` is OpenAI-only; Qwen docs only document
    ``{"type": "web_search"}``.
  * ``include`` is OpenAI-only; Qwen docs say sources are returned
    automatically on completed ``web_search_call`` items.
- It does NOT fabricate titles/snippets. Per research, Qwen Responses
  returns URL-only sources; ``title`` falls back to display domain.

Wire shape (frozen by this fixture)
-----------------------------------
Request
~~~~~~~

.. code-block:: json

    {
      "model": "qwen3.7-max",
      "input": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
      ],
      "tools": [
        {"type": "web_search"},
        {"type": "function", "function": {"name": "...", "parameters": {...}}}
      ],
      "tool_choice": "auto",
      "reasoning": {"effort": "high"},
      "stream": true
    }

Response (typed SSE event stream, monotonic ``sequence_number``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. ``response.created``
2. ``response.in_progress``
3. ``response.output_item.added`` (``type="web_search_call"``)
4. ``response.web_search_call.in_progress``
5. ``response.web_search_call.searching``
6. ``response.web_search_call.completed`` (with ``action.sources``:
   ``[{"type": "url", "url": "..."}]``)
7. ``response.output_item.done`` (the completed ``web_search_call``)
8. ``response.output_item.added`` (``type="function_call"`` — when the
   model also calls a custom function tool in the same round)
9. ``response.function_call_arguments.delta``
10. ``response.function_call_arguments.done``
11. ``response.output_item.added`` (``type="message"``)
12. ``response.output_text.delta`` (final answer text)
13. ``response.completed`` (with ``usage.x_tools.web_search.count`` and
    ``output_text``)

Source binding
~~~~~~~~~~~~~~

- Each completed ``web_search_call`` carries ``action.sources`` as a
  list of ``{"type": "url", "url": "..."}`` entries.
- Qwen does NOT automatically insert ``[1]`` markers into the answer
  text; ``output_text.annotations`` is typically empty.
- Therefore block-level provenance must be established by the Host
  through the model's ``web_source_refs`` output schema (each ref must
  match a URL returned by a completed ``web_search_call`` in this
  turn). The fixture exposes the URLs so the Host can verify refs.

Thinking coexistence
~~~~~~~~~~~~~~~~~~~~

Qwen3.7 Max is hybrid thinking; the Responses API emits typed
``response.reasoning.delta`` events. The fixture isolates thinking
parts so the SSE/DB projection never persists reasoning text.

Function tool coexistence
~~~~~~~~~~~~~~~~~~~~~~~~~

Qwen Responses explicitly supports built-in tools (``web_search``)
and custom function tools in the same round. The fixture includes one
``function_call`` output item alongside the ``web_search_call`` to
prove coexistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Official Qwen3.7 Max model name on DashScope Responses. The fixture
# also accepts ``qwen3-max`` as an alias for future compatibility.
QWEN_MODEL_NAME: str = "qwen3.7-max"
QWEN_MODEL_ALIAS: str = "qwen3-max"

# Per Aliyun docs: account-level 15 RPS shared across all API keys and
# models. Exceeding it silently skips the search (no error). This is the
# most important "unavailable" risk for Qwen.
QWEN_WEB_SEARCH_RPS_LIMIT: int = 15

# Fixture version — bumped only when the frozen wire shape changes.
QWEN_WIRE_FIXTURE_VERSION: str = "qwen_dashscope_responses_v1"

# ---------------------------------------------------------------------------
# Web search tool shape (frozen)
# ---------------------------------------------------------------------------

# Per official Aliyun docs, the only documented field on the Qwen
# Responses ``web_search`` tool is ``type``. OpenAI-private fields like
# ``search_context_size`` and ``include`` are intentionally absent —
# research says they are not in the DashScope Responses schema.
QWEN_WEB_SEARCH_TOOL: dict[str, Any] = {"type": "web_search"}


# ---------------------------------------------------------------------------
# Expected request shape (happy path: thinking + web search + function tool)
# ---------------------------------------------------------------------------


def _build_expected_request_shape() -> dict[str, Any]:
    """Build the frozen expected request body for the happy-path wire.

    This shape is the **minimum** the G2 fixture locks. Real G3 smoke
    may extend it only with fields explicitly documented by Aliyun
    (e.g. ``reasoning.effort`` values). OpenAI-private fields are
    forbidden.
    """
    return {
        "model": QWEN_MODEL_NAME,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are Ask Claread. Use web_search when the user "
                    "asks about recent events."
                ),
            },
            {
                "role": "user",
                "content": "What is the latest stable version of Python?",
            },
        ],
        "tools": [
            # Built-in web search — only ``type`` is documented.
            {"type": "web_search"},
            # Custom function tool coexisting in the same round.
            {
                "type": "function",
                "function": {
                    "name": "search_current_article",
                    "description": "Search the user's currently open article.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            },
        ],
        # Qwen Responses default; the model decides whether to call.
        "tool_choice": "auto",
        # Qwen3.7 Max is hybrid thinking. Official docs recommend
        # ``reasoning.effort`` over the deprecated ``enable_thinking``.
        "reasoning": {"effort": "high"},
        "stream": True,
    }


# ---------------------------------------------------------------------------
# Expected response event stream (happy path)
# ---------------------------------------------------------------------------


def _build_expected_response_events() -> list[dict[str, Any]]:
    """Build the frozen happy-path SSE event stream.

    The stream demonstrates:
    - web_search_call lifecycle (in_progress → searching → completed);
    - coexistence of a custom function_call in the same round;
    - thinking (reasoning) deltas isolated from the final text;
    - completed ``web_search_call.action.sources`` carrying URLs only;
    - ``response.completed.usage.x_tools.web_search.count`` = 1.

    URLs are deterministic example.com paths so the fixture is offline.
    """
    return [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {
                "id": "resp_qwen_fixture_001",
                "object": "response",
                "status": "in_progress",
                "model": QWEN_MODEL_NAME,
            },
        },
        {
            "type": "response.in_progress",
            "sequence_number": 1,
            "response": {"id": "resp_qwen_fixture_001", "status": "in_progress"},
        },
        # ---- web_search_call lifecycle ----
        {
            "type": "response.output_item.added",
            "sequence_number": 2,
            "output_index": 0,
            "item": {
                "id": "ws_qwen_fixture_001",
                "type": "web_search_call",
                "status": "in_progress",
            },
        },
        {
            "type": "response.web_search_call.in_progress",
            "sequence_number": 3,
            "output_index": 0,
            "item_id": "ws_qwen_fixture_001",
        },
        {
            "type": "response.web_search_call.searching",
            "sequence_number": 4,
            "output_index": 0,
            "item_id": "ws_qwen_fixture_001",
        },
        {
            "type": "response.web_search_call.completed",
            "sequence_number": 5,
            "output_index": 0,
            "item_id": "ws_qwen_fixture_001",
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 6,
            "output_index": 0,
            "item": {
                "id": "ws_qwen_fixture_001",
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    # URL-only sources per official Qwen docs. No
                    # title/snippet fields are documented.
                    "sources": [
                        {"type": "url", "url": "https://www.python.org/downloads/"},
                        {
                            "type": "url",
                            "url": "https://docs.python.org/3/whatsnew/3.13.html",
                        },
                    ],
                },
            },
        },
        # ---- function_call coexisting in the same round ----
        {
            "type": "response.output_item.added",
            "sequence_number": 7,
            "output_index": 1,
            "item": {
                "id": "fc_qwen_fixture_001",
                "type": "function_call",
                "status": "in_progress",
                "name": "search_current_article",
                "call_id": "call_qwen_fixture_001",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 8,
            "output_index": 1,
            "item_id": "fc_qwen_fixture_001",
            "delta": '{"query": "python version"',
        },
        {
            "type": "response.function_call_arguments.done",
            "sequence_number": 9,
            "output_index": 1,
            "item_id": "fc_qwen_fixture_001",
            "arguments": '{"query": "python version"}',
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 10,
            "output_index": 1,
            "item": {
                "id": "fc_qwen_fixture_001",
                "type": "function_call",
                "status": "completed",
                "name": "search_current_article",
                "call_id": "call_qwen_fixture_001",
                "arguments": '{"query": "python version"}',
            },
        },
        # ---- reasoning (thinking) deltas, isolated from final text ----
        {
            "type": "response.reasoning.delta",
            "sequence_number": 11,
            "output_index": 2,
            "delta": "The user asked about the latest Python version.",
        },
        {
            "type": "response.reasoning.done",
            "sequence_number": 12,
            "output_index": 2,
        },
        # ---- final answer text ----
        {
            "type": "response.output_item.added",
            "sequence_number": 13,
            "output_index": 3,
            "item": {
                "id": "msg_qwen_fixture_001",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
            },
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 14,
            "output_index": 3,
            "item_id": "msg_qwen_fixture_001",
            "delta": "The latest stable Python version is 3.13.",
        },
        {
            "type": "response.output_text.done",
            "sequence_number": 15,
            "output_index": 3,
            "item_id": "msg_qwen_fixture_001",
            "text": "The latest stable Python version is 3.13.",
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 16,
            "output_index": 3,
            "item": {
                "id": "msg_qwen_fixture_001",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "The latest stable Python version is 3.13.",
                        # Per docs: annotations typically empty — Qwen
                        # does not auto-insert [1] markers.
                        "annotations": [],
                    }
                ],
            },
        },
        # ---- terminal completion event with usage ----
        {
            "type": "response.completed",
            "sequence_number": 17,
            "response": {
                "id": "resp_qwen_fixture_001",
                "object": "response",
                "status": "completed",
                "model": QWEN_MODEL_NAME,
                "output": [
                    {"type": "web_search_call", "id": "ws_qwen_fixture_001"},
                    {"type": "function_call", "id": "fc_qwen_fixture_001"},
                    {"type": "message", "id": "msg_qwen_fixture_001"},
                ],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    # Qwen-specific: per-call web search count.
                    "x_tools": {"web_search": {"count": 1}},
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Expected citations (URL-only; title falls back to display domain)
# ---------------------------------------------------------------------------


def _build_expected_citations() -> list[dict[str, Any]]:
    """Citations derived from the completed ``web_search_call``.

    Per official docs, Qwen Responses sources are URL-only. The Host
    must NOT fabricate titles or snippets. The ``title`` field below is
    the Host-derived display-domain fallback (used by the Web evidence
    registry), NOT a value Qwen returns.
    """
    return [
        {
            "url": "https://www.python.org/downloads/",
            "title": "python.org",  # display-domain fallback
            "snippet": None,  # Qwen does not return snippets
        },
        {
            "url": "https://docs.python.org/3/whatsnew/3.13.html",
            "title": "docs.python.org",
            "snippet": None,
        },
    ]


# ---------------------------------------------------------------------------
# Expected thinking parts (isolated from SSE/DB projection)
# ---------------------------------------------------------------------------


def _build_expected_thinking_parts() -> list[dict[str, Any]]:
    """Thinking deltas isolated from the SSE/DB projection.

    These MUST NOT appear in:
    - public Web citations / Sources;
    - DB user-visible JSON;
    - cold history replay.

    They MAY be used internally for tool-call continuation (the Host
    sends back ``reasoning_content`` when continuing a tool loop).
    """
    return [
        {
            "sequence_number": 11,
            "output_index": 2,
            "text": "The user asked about the latest Python version.",
            "isolated_from_public": True,
        },
    ]


# ---------------------------------------------------------------------------
# Expected tool calls (function tool coexisting with web search)
# ---------------------------------------------------------------------------


def _build_expected_tool_calls() -> list[dict[str, Any]]:
    """Custom function tool calls coexisting with the web search.

    Proves Qwen Responses supports built-in + custom function tools in
    the same round (per official docs).
    """
    return [
        {
            "name": "search_current_article",
            "call_id": "call_qwen_fixture_001",
            "arguments": '{"query": "python version"}',
        },
    ]


# ---------------------------------------------------------------------------
# Error scenarios (deterministic; offline)
# ---------------------------------------------------------------------------

# Scenario -> expected outcome classification. The Host maps provider
# states to the closed WebSearchOutcome set
# (completed | no_results | unavailable | failed).
QWEN_ERROR_SCENARIOS: dict[str, dict[str, Any]] = {
    # Completed search but no sources returned. ``outcome=no_results``.
    "no_results": {
        "description": (
            "web_search_call.completed with empty action.sources"
        ),
        "trigger_event": {
            "type": "response.web_search_call.completed",
            "item_id": "ws_qwen_empty_001",
            "action": {"sources": []},
        },
        "expected_outcome": "no_results",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
    # 15 RPS silent skip — no error, no web_search_call events emitted.
    # ``outcome=unavailable``. This is Qwen's most common failure mode.
    "unavailable": {
        "description": (
            "15 RPS shared limit exceeded; search silently skipped "
            "(no error, no web_search_call events in stream)"
        ),
        "trigger_event": None,  # absence of events is the signal
        "expected_outcome": "unavailable",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
        # The Host MUST NOT treat "search was skipped" as "search
        # happened and passed". No web evidence may be minted.
    },
    # HTTP 400 / 422 — invalid request (e.g. OpenAI-private field
    # rejected). ``outcome=failed``.
    "failed": {
        "description": (
            "HTTP 400 / 422 from DashScope (e.g. OpenAI-private field "
            "search_context_size rejected)"
        ),
        "trigger_event": {
            "type": "error",
            "status_code": 400,
            "error": {
                "type": "invalid_request_error",
                "message": "unsupported tool parameter",
            },
        },
        "expected_outcome": "failed",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
    # HTTP 429 — explicit rate limit (rarer than silent skip).
    # ``outcome=unavailable`` (treated as transient; G3 smoke may
    # surface as ``failed`` if the Host prefers stricter mapping).
    "rate_limited": {
        "description": "HTTP 429 from DashScope (explicit rate limit)",
        "trigger_event": {
            "type": "error",
            "status_code": 429,
            "error": {
                "type": "rate_limit_exceeded",
                "message": "qps limit exceeded",
            },
        },
        "expected_outcome": "unavailable",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
}


# ---------------------------------------------------------------------------
# Fixture dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QwenDashscopeResponsesWireFixture:
    """Frozen wire fixture for the Qwen dashscope_responses transport.

    All fields are deterministic and offline. The fixture is the
    G2 contract surface — real G3 smoke may only extend it, never
    contract it.
    """

    fixture_version: str = QWEN_WIRE_FIXTURE_VERSION
    provider: str = "dashscope"
    protocol: str = "dashscope_responses"
    model_name: str = QWEN_MODEL_NAME
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    web_search_tool_shape: dict[str, Any] = field(
        default_factory=lambda: dict(QWEN_WEB_SEARCH_TOOL)
    )
    expected_request_shape: dict[str, Any] = field(
        default_factory=_build_expected_request_shape
    )
    expected_response_events: list[dict[str, Any]] = field(
        default_factory=_build_expected_response_events
    )
    expected_citations: list[dict[str, Any]] = field(
        default_factory=_build_expected_citations
    )
    expected_thinking_parts: list[dict[str, Any]] = field(
        default_factory=_build_expected_thinking_parts
    )
    expected_tool_calls: list[dict[str, Any]] = field(
        default_factory=_build_expected_tool_calls
    )
    error_scenarios: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            k: dict(v) for k, v in QWEN_ERROR_SCENARIOS.items()
        }
    )


# Convenience singleton — most tests want the canonical happy path.
QWEN_DASHSCOPE_RESPONSES_WIRE: QwenDashscopeResponsesWireFixture = (
    QwenDashscopeResponsesWireFixture()
)


__all__ = [
    "QWEN_DASHSCOPE_RESPONSES_WIRE",
    "QWEN_ERROR_SCENARIOS",
    "QWEN_MODEL_ALIAS",
    "QWEN_MODEL_NAME",
    "QWEN_WEB_SEARCH_RPS_LIMIT",
    "QWEN_WEB_SEARCH_TOOL",
    "QWEN_WIRE_FIXTURE_VERSION",
    "QwenDashscopeResponsesWireFixture",
]
