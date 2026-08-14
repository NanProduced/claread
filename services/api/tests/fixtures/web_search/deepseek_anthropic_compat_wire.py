"""G2-DeepSeek: DeepSeek Anthropic-compatible Web Search wire fixture (OFFLINE).

**Status: PROBE REQUIRED**

This fixture is a hand-authored protocol draft built from public docs.
It is NOT an official capture and is NOT evidence that the wire shape
matches the real DeepSeek Anthropic-compatible endpoint. Every field
below — including the model name, tool type, event ordering, and
citation behaviour — must be validated by a real G3 smoke run before
any GO decision. Treat all conclusions as PROBE REQUIRED until
backed by a real SDK call capture.

This fixture captures the EXPECTED wire format for the DeepSeek
Web Search transport over DeepSeek's Anthropic-compatible endpoint
(``https://api.deepseek.com/anthropic``). It is built strictly from
the official DeepSeek docs referenced in
``TMP-ask-web-search-deepseek-2026-07-26.md``:

- https://api-docs.deepseek.com/guides/anthropic_api/
- https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- https://api-docs.deepseek.com/guides/thinking_mode/
- https://api-docs.deepseek.com/quick_start/error_codes/
- https://api-docs.deepseek.com/api/create-chat-completion/ (for context
  DeepSeek ChatCompletions has NO built-in web search; only the
  Anthropic-compat path does)

Anthropic Web Search reference (used for probe design ONLY — DeepSeek
does NOT re-commit to every Anthropic field):
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool

Model identity
~~~~~~~~~~~~~~
The fixture uses ``deepseek-chat`` to match the model identity planned
for the G3 smoke run (see ``evals/scripts/run_reader_record_ask_r4_a3.py``
which sets ``CLAREAD_REAL_LLM_MODEL=deepseek-chat``). Earlier drafts
referenced ``deepseek-v4-flash`` / ``deepseek-v4-pro`` based on a
hypothetical V4 release; those names have been removed because they
are not confirmed by official DeepSeek docs and would diverge from
the smoke configuration. ``deepseek-pro`` is kept as the
probe model to match the smoke script's invocation.

Non-goals
---------
- It does NOT call any real DeepSeek endpoint.
- It does NOT import the real ``anthropic`` SDK.
- It does NOT validate PydanticAI's ``AnthropicModel`` integration
  (that is a G3 smoke concern).
- It does NOT assume DeepSeek honours every Anthropic field. Per
  research, DeepSeek's compatibility table explicitly says the
  ``citations`` field is IGNORED and ``search_result`` input blocks
  are NOT supported.
- It does NOT fabricate URLs/titles/snippets. DeepSeek's
  ``web_search_tool_result`` carries URLs+titles; snippets may or may
  not appear (per research, citation binding is the main risk).

Wire shape (frozen by this fixture)
-----------------------------------
Request (Anthropic Messages API)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: json

    {
      "model": "deepseek-chat",
      "max_tokens": 1024,
      "system": "...",
      "messages": [{"role": "user", "content": "..."}],
      "tools": [
        {
          "type": "web_search_20250305",
          "name": "web_search",
          "max_uses": 1
        }
      ],
      "thinking": {"type": "enabled"},
      "stream": true
    }

Response (Anthropic-style SSE event stream)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. ``message_start`` (with message id, model, role)
2. ``content_block_start`` (``type="server_tool_use"``,
   ``name="web_search"``) — the model delegated a server-side search
3. ``content_block_delta`` (``type="input_json_delta"`` — search query)
4. ``content_block_stop``
5. ``content_block_start`` (``type="web_search_tool_result"``) with
   ``content=[{"type": "web_search_result", "url": "...",
   "title": "...", "encrypted_content": "...", "page_age": "..."}]``
6. ``content_block_stop``
7. ``content_block_start`` (``type="thinking"``) — DeepSeek thinking
8. ``content_block_delta`` (``type="thinking_delta"``)
9. ``content_block_stop``
10. ``content_block_start`` (``type="text"``)
11. ``content_block_delta`` (``type="text_delta"``)
12. ``content_block_stop``
13. ``message_delta`` (with ``stop_reason``)
14. ``message_stop``

Citation behaviour (research: KEY RISK)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Per the DeepSeek Anthropic compatibility table, the ``citations``
field is IGNORED. This means even when ``web_search_tool_result``
carries URLs+titles, the final answer text may NOT carry reliable
citation locations.

The fixture models this risk as three explicit ``citations_behavior``
modes:

- ``"stable"``   — all ``web_search_tool_result`` URLs are returned
  and mappable to :class:`WebEvidence`. (Happy path.)
- ``"partial"``  — some ``web_search_tool_result`` URLs are missing
  from the response. The Host MUST mark the missing ones as
  ``unavailable`` outcome and NEVER fabricate them.
- ``"ignored"``  — no ``web_search_tool_result`` block returned at
  all (search happened but no URLs surfaced). The Host MUST return
  ``unavailable`` outcome.

Stop reasons
~~~~~~~~~~~~

- ``end_turn``    — model finished normally
- ``tool_use``    — model delegated to a server tool (web_search)
- ``max_tokens``  — hit the output cap
- ``stop_sequence`` — hit a stop sequence (not used by Ask Claread)

Error semantics
~~~~~~~~~~~~~~~

- ``no_results``   — ``web_search_tool_result.content == []``
- ``unavailable``  — HTTP 429 / 503 / 402 (insufficient balance);
  OR ``citations_behavior="ignored"``
- ``failed``       — HTTP 400 / 422 / 500; OR
  ``citations_behavior="partial"`` (Host refuses to fabricate missing
  URLs)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DeepSeek model names aligned with the G3 smoke run configuration.
# ``deepseek-chat`` is the chat model used for /2 of the smoke
# (see ``evals/scripts/run_reader_record_ask_r4_a3.py`` which sets
# ``CLAREAD_REAL_LLM_MODEL=deepseek-chat``). ``deepseek-pro`` is the
# Probe model. Earlier drafts used ``deepseek-v4-flash`` /
# ``deepseek-v4-pro`` based on a hypothetical V4 release; those names
# are NOT confirmed by official docs and have been removed.
#
# Status: PROBE REQUIRED — these names must be confirmed by a real
# G3 smoke run against the Anthropic-compat endpoint. The fixture
# only locks the wire SHAPE; the model identity is provisional.
DEEPSEEK_MODEL_FLASH: str = "deepseek-chat"
DEEPSEEK_MODEL_PRO: str = "deepseek-pro"

# DeepSeek Anthropic-compat base URL. Distinct from the OpenAI-compat
# base URL (https://api.deepseek.com/v1) — they are NOT
# interchangeable.
DEEPSEEK_ANTHROPIC_BASE_URL: str = "https://api.deepseek.com/anthropic"

# Anthropic Web Search tool version per official docs. DeepSeek's
# compatibility table says server_tool_use + web_search_tool_result
# are supported; this version is the probe value. G3 smoke confirms
# whether DeepSeek honours it.
DEEPSEEK_WEB_SEARCH_TOOL_TYPE: str = "web_search_20250305"

# Account-level concurrency caps per official docs.
DEEPSEEK_PRO_CONCURRENCY: int = 500
DEEPSEEK_FLASH_CONCURRENCY: int = 2500

# Fixture version — bumped only when the frozen wire shape changes.
DEEPSEEK_WIRE_FIXTURE_VERSION: str = "deepseek_anthropic_v1"

# Citation reliability modes (per research: KEY RISK field).
CitationsBehavior = Literal["stable", "partial", "ignored"]


# ---------------------------------------------------------------------------
# Web search tool shape (frozen)
# ---------------------------------------------------------------------------

# Per Anthropic Web Search reference. DeepSeek's compatibility table
# says server_tool_use + web_search_tool_result are supported; the
# exact field set (max_uses, allowed_domains, blocked_domains,
# user_location) is what G3 smoke must confirm. The fixture locks
# the MINIMUM shape: type + name + max_uses=1 (the G0 probe cap).
DEEPSEEK_WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": DEEPSEEK_WEB_SEARCH_TOOL_TYPE,
    "name": "web_search",
    "max_uses": 1,
}


# ---------------------------------------------------------------------------
# Expected request shape (happy path: thinking + web search)
# ---------------------------------------------------------------------------


def _build_expected_request_shape() -> dict[str, Any]:
    """Build the frozen expected request body for the happy-path wire.

    This shape is the **minimum** the G2 fixture locks. Real G3 smoke
    may extend it only with fields confirmed by DeepSeek's docs.
    Anthropic-private fields like ``user_location`` are NOT included
    unless G3 confirms DeepSeek honours them.
    """
    return {
        "model": DEEPSEEK_MODEL_FLASH,
        "max_tokens": 1024,
        "system": (
            "You are Ask Claread. Use web_search when the user asks "
            "about recent events."
        ),
        "messages": [
            {
                "role": "user",
                "content": "What is the latest stable version of Python?",
            },
        ],
        "tools": [
            # Anthropic Web Search server tool. DeepSeek honours
            # ``server_tool_use`` and ``web_search_tool_result`` per
            # its compatibility table.
            dict(DEEPSEEK_WEB_SEARCH_TOOL),
            # Custom function tool coexisting in the same round.
            {
                "type": "custom",
                "name": "search_current_article",
                "description": "Search the user's currently open article.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        ],
        # DeepSeek thinking mode (per official thinking_mode docs).
        "thinking": {"type": "enabled"},
        "stream": True,
    }


# ---------------------------------------------------------------------------
# Expected response event stream (happy path, citations_behavior="stable")
# ---------------------------------------------------------------------------


def _build_expected_response_events() -> list[dict[str, Any]]:
    """Build the frozen happy-path SSE event stream.

    The stream demonstrates:
    - ``server_tool_use`` block (DeepSeek delegated to web_search);
    - ``web_search_tool_result`` block carrying URL+title per source;
    - ``thinking`` block isolated from final text;
    - ``text`` block carrying the final answer;
    - ``message_delta`` with ``stop_reason="end_turn"``.

    URLs are deterministic example.com paths so the fixture is offline.
    """
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_deepseek_fixture_001",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 50, "output_tokens": 0},
            },
        },
        # ---- server_tool_use: web_search ----
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_deepseek_fixture_001",
                "name": "web_search",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"query": "latest stable Python version"}',
            },
        },
        {
            "type": "content_block_stop",
            "index": 0,
        },
        # ---- web_search_tool_result: the actual sources ----
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_deepseek_fixture_001",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://www.python.org/downloads/",
                        "title": "Download Python | Python.org",
                        "encrypted_content": "enc_deepseek_fixture_001",
                        "page_age": "2026-07-20",
                    },
                    {
                        "type": "web_search_result",
                        "url": "https://docs.python.org/3/whatsnew/3.13.html",
                        "title": "What's New in Python 3.13",
                        "encrypted_content": "enc_deepseek_fixture_002",
                        "page_age": "2026-07-15",
                    },
                ],
            },
        },
        {
            "type": "content_block_stop",
            "index": 1,
        },
        # ---- thinking block (DeepSeek reasoning, isolated) ----
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "thinking",
                "thinking": "",
            },
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {
                "type": "thinking_delta",
                "thinking": "The user asked about the latest Python version.",
            },
        },
        {
            "type": "content_block_stop",
            "index": 2,
        },
        # ---- text block (final answer) ----
        {
            "type": "content_block_start",
            "index": 3,
            "content_block": {
                "type": "text",
                "text": "",
            },
        },
        {
            "type": "content_block_delta",
            "index": 3,
            "delta": {
                "type": "text_delta",
                "text": "The latest stable Python version is 3.13.",
            },
        },
        {
            "type": "content_block_stop",
            "index": 3,
        },
        # ---- message_delta + message_stop ----
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": "end_turn",
                "stop_sequence": None,
            },
            "usage": {"output_tokens": 30},
        },
        {
            "type": "message_stop",
        },
    ]


# ---------------------------------------------------------------------------
# Expected citations (stable: all web_search_tool_result URLs mappable)
# ---------------------------------------------------------------------------


def _build_expected_citations() -> list[dict[str, Any]]:
    """Citations derived from ``web_search_tool_result.content``.

    Per the DeepSeek compatibility table, the ``citations`` field is
    IGNORED — so the Host MUST build the citation set from the
    ``web_search_tool_result`` block URLs, NOT from any citation
    markers in the final answer text.

    Unlike Qwen, DeepSeek DOES return ``title`` per source (per the
    Anthropic reference shape). ``snippet`` is the optional
    ``encrypted_content`` field, but the Host treats it as untrusted
    opaque text and DOES NOT expose it on public DTOs.
    """
    return [
        {
            "url": "https://www.python.org/downloads/",
            "title": "Download Python | Python.org",
            "snippet": None,  # encrypted_content is internal-only
        },
        {
            "url": "https://docs.python.org/3/whatsnew/3.13.html",
            "title": "What's New in Python 3.13",
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

    They MAY be used internally for tool-call continuation if DeepSeek
    requires the full ``reasoning_content`` to be replayed (per the
    OpenAI-compat thinking_mode docs — but the Anthropic-compat path
    may differ; G3 smoke confirms).
    """
    return [
        {
            "index": 2,
            "text": "The user asked about the latest Python version.",
            "isolated_from_public": True,
        },
    ]


# ---------------------------------------------------------------------------
# Expected tool calls (custom function tool coexisting with web search)
# ---------------------------------------------------------------------------


def _build_expected_tool_calls() -> list[dict[str, Any]]:
    """Custom function tool calls coexisting with the server-side
    ``web_search`` tool.

    Note: per Anthropic's wire, ``web_search`` is a SERVER tool
    (``server_tool_use``), not a custom ``tool_use``. The fixture
    includes a separate ``search_current_article`` custom tool call to
    prove both can coexist.
    """
    return [
        {
            "type": "server_tool_use",
            "id": "srvu_deepseek_fixture_001",
            "name": "web_search",
            "input": {"query": "latest stable Python version"},
        },
        # A custom function tool call would appear as ``tool_use`` in a
        # real mixed round; the fixture leaves the second slot empty to
        # keep the happy path minimal. Tests can extend this list.
    ]


# ---------------------------------------------------------------------------
# Error scenarios (deterministic; offline)
# ---------------------------------------------------------------------------

# Scenario -> expected outcome classification. The Host maps provider
# states to the closed WebSearchOutcome set
# (completed | no_results | unavailable | failed).
#
# DeepSeek's KEY RISK (per research) is that the ``citations`` field
# is IGNORED. The fixture models this via three ``citations_behavior``
# modes that the stub adapter must handle differently.
DEEPSEEK_ERROR_SCENARIOS: dict[str, dict[str, Any]] = {
    # Completed search but no sources returned.
    # ``outcome=no_results``.
    "no_results": {
        "description": (
            "web_search_tool_result.content == [] (search ran, 0 hits)"
        ),
        "trigger_event": {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_deepseek_empty_001",
                "content": [],
            },
        },
        "expected_outcome": "no_results",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
    # HTTP 429 / 503 / 402 (insufficient balance, overloaded, rate
    # limit). ``outcome=unavailable`` (transient).
    "unavailable": {
        "description": (
            "HTTP 429 / 503 / 402 from DeepSeek Anthropic endpoint "
            "(transient: rate limit, overloaded, insufficient balance)"
        ),
        "trigger_event": {
            "type": "error",
            "status_code": 429,
            "error": {
                "type": "rate_limit_error",
                "message": "rate limit exceeded",
            },
        },
        "expected_outcome": "unavailable",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
    # HTTP 400 / 422 / 500 — invalid request or server error.
    # ``outcome=failed``.
    "failed": {
        "description": (
            "HTTP 400 / 422 / 500 from DeepSeek Anthropic endpoint "
            "(invalid request, malformed tool, server error)"
        ),
        "trigger_event": {
            "type": "error",
            "status_code": 400,
            "error": {
                "type": "invalid_request_error",
                "message": "unsupported tool version",
            },
        },
        "expected_outcome": "failed",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
    # citations_behavior="partial" — some web_search_tool_result URLs
    # are missing. Host MUST NOT fabricate. ``outcome=failed`` is the
    # strict mapping; G3 may relax to ``unavailable`` per product
    # policy. The fixture locks the STRICT mapping.
    "partial": {
        "description": (
            "citations_behavior='partial': web_search_tool_result "
            "missing some URLs (DeepSeek citation reliability risk)"
        ),
        "trigger_event": {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_deepseek_partial_001",
                # Only one of the two expected URLs is returned.
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://www.python.org/downloads/",
                        "title": "Download Python | Python.org",
                    },
                    # Missing: docs.python.org URL.
                ],
            },
        },
        "expected_outcome": "failed",  # strict: refuse to fabricate
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
        # The Host MUST surface a typed ``failed`` outcome and NOT
        # silently treat the partial result as a successful search.
    },
    # citations_behavior="ignored" — no web_search_tool_result block
    # at all. Per DeepSeek compatibility table, ``citations`` is
    # ignored, and in the worst case the entire tool_result block may
    # be absent. ``outcome=unavailable``.
    "ignored": {
        "description": (
            "citations_behavior='ignored': no web_search_tool_result "
            "block returned (DeepSeek citation reliability risk; the "
            "compatibility table says citations field is ignored)"
        ),
        "trigger_event": None,  # absence of the block is the signal
        "expected_outcome": "unavailable",
        "expected_cited_source_count": 0,
        "expected_web_evidence_count": 0,
    },
}


# ---------------------------------------------------------------------------
# Fixture dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeepseekAnthropicCompatWireFixture:
    """Frozen wire fixture for the DeepSeek Anthropic-compatible transport.

    All fields are deterministic and offline. The fixture is the
    G2 contract surface — real G3 smoke may only extend it, never
    contract it.
    """

    fixture_version: str = DEEPSEEK_WIRE_FIXTURE_VERSION
    provider: str = "deepseek"
    protocol: str = "deepseek_anthropic"
    model_name: str = DEEPSEEK_MODEL_FLASH
    base_url: str = DEEPSEEK_ANTHROPIC_BASE_URL
    web_search_tool_shape: dict[str, Any] = field(
        default_factory=lambda: dict(DEEPSEEK_WEB_SEARCH_TOOL)
    )
    # Default happy-path citations behaviour. Partial / ignored are
    # modelled as error scenarios (see ``error_scenarios``).
    citations_behavior: CitationsBehavior = "stable"
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
            k: dict(v) for k, v in DEEPSEEK_ERROR_SCENARIOS.items()
        }
    )


# Convenience singleton — most tests want the canonical happy path.
DEEPSEEK_ANTHROPIC_COMPAT_WIRE: DeepseekAnthropicCompatWireFixture = (
    DeepseekAnthropicCompatWireFixture()
)


__all__ = [
    "DEEPSEEK_ANTHROPIC_BASE_URL",
    "DEEPSEEK_ANTHROPIC_COMPAT_WIRE",
    "DEEPSEEK_ERROR_SCENARIOS",
    "DEEPSEEK_FLASH_CONCURRENCY",
    "DEEPSEEK_MODEL_FLASH",
    "DEEPSEEK_MODEL_PRO",
    "DEEPSEEK_PRO_CONCURRENCY",
    "DEEPSEEK_WEB_SEARCH_TOOL",
    "DEEPSEEK_WEB_SEARCH_TOOL_TYPE",
    "DEEPSEEK_WIRE_FIXTURE_VERSION",
    "CitationsBehavior",
    "DeepseekAnthropicCompatWireFixture",
]
