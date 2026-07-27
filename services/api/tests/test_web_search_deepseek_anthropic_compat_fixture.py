"""G2-DeepSeek: DeepSeek Anthropic-compat wire fixture tests (OFFLINE).

Verifies that the frozen wire fixture in
``tests/fixtures/web_search/deepseek_anthropic_compat_wire.py`` is
well-formed and that the stub adapter correctly maps fixture data to
the provider-neutral :class:`WebSearchResult` /
:class:`WebSearchHitView` contract.

Test surface
------------
- Fixture well-formedness: all required fields present, non-empty,
  and of the right type.
- Request shape contract (Anthropic Messages API):
  * ``model``, ``max_tokens``, ``system``, ``messages``, ``tools``,
    ``thinking``, ``stream`` keys present.
  * Web search tool uses ``web_search_20250305`` server tool type.
  * ``thinking`` carries ``{"type": "enabled"}``.
  * Custom function tool coexists with the server web_search tool.
- Response event sequence:
  * ``message_start`` → content blocks → ``message_delta`` →
    ``message_stop``.
  * ``server_tool_use`` block precedes ``web_search_tool_result``.
  * ``thinking`` block is isolated from the final ``text`` block.
- Web evidence mapping:
  * URLs in ``web_search_tool_result.content`` map 1:1 to
    :class:`WebSearchHitView` entries (after canonicalization).
  * DeepSeek DOES return ``title`` per source; the stub preserves it.
  * ``encrypted_content`` is dropped (internal-only opaque text).
  * ``description`` stays empty (Host MUST NOT expose
    ``encrypted_content`` as a public snippet).
- Tool call coexistence: a ``server_tool_use`` (web_search) coexists
  with a custom ``tool_use`` in the same round.
- Thinking isolation: ``thinking_delta`` events are tagged
  ``isolated_from_public=True`` and never appear in citations.
- Error scenarios:
  * ``no_results``   → ``outcome=no_results``.
  * ``unavailable``  → ``outcome=unavailable`` (HTTP 429/503/402).
  * ``failed``       → ``outcome=failed`` (HTTP 400/422/500).
  * ``partial``      → ``outcome=failed`` (strict: refuse to fabricate
    missing URLs).
  * ``ignored``      → ``outcome=unavailable`` (no
    web_search_tool_result block at all).
- Citation reliability modes: ``stable``/``partial``/``ignored``
  enforced as frozen fixture invariants.

All tests are OFFLINE — no real HTTP, no real SDK.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.reader_record_ask.web_search_contracts import (
    WEB_URL_MAX_LEN,
    canonicalize_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchHitView,
    WebSearchResult,
)
from tests.fixtures.web_search.deepseek_anthropic_compat_adapter_stub import (
    DeepseekAnthropicCompatAdapterStub,
)
from tests.fixtures.web_search.deepseek_anthropic_compat_wire import (
    DEEPSEEK_ANTHROPIC_BASE_URL,
    DEEPSEEK_ANTHROPIC_COMPAT_WIRE,
    DEEPSEEK_MODEL_FLASH,
    DEEPSEEK_WEB_SEARCH_TOOL,
    DEEPSEEK_WEB_SEARCH_TOOL_TYPE,
    DEEPSEEK_WIRE_FIXTURE_VERSION,
    DeepseekAnthropicCompatWireFixture,
)

# ---------------------------------------------------------------------------
# Fixture well-formedness
# ---------------------------------------------------------------------------


class TestFixtureWellFormed:
    def test_singleton_is_constructed(self) -> None:
        assert isinstance(
            DEEPSEEK_ANTHROPIC_COMPAT_WIRE,
            DeepseekAnthropicCompatWireFixture,
        )

    def test_required_fields_present_and_non_empty(self) -> None:
        f = DEEPSEEK_ANTHROPIC_COMPAT_WIRE
        assert f.fixture_version == DEEPSEEK_WIRE_FIXTURE_VERSION
        assert f.provider == "deepseek"
        assert f.protocol == "deepseek_anthropic"
        assert f.model_name == DEEPSEEK_MODEL_FLASH
        assert f.base_url == DEEPSEEK_ANTHROPIC_BASE_URL
        assert f.base_url.startswith("https://")
        assert f.web_search_tool_shape
        assert f.expected_request_shape
        assert f.expected_response_events
        assert f.expected_citations
        # thinking parts and tool calls must be non-empty to prove
        # coexistence and thinking isolation.
        assert f.expected_thinking_parts
        assert f.expected_tool_calls
        assert f.error_scenarios

    def test_default_citations_behavior_is_stable(self) -> None:
        """Happy-path fixture defaults to ``stable`` citation
        reliability. ``partial`` and ``ignored`` are modelled as
        error scenarios."""
        assert DEEPSEEK_ANTHROPIC_COMPAT_WIRE.citations_behavior == "stable"

    def test_error_scenarios_cover_closed_outcome_set(self) -> None:
        scenarios = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios
        # DeepSeek-specific: five scenarios (the closed outcome set
        # plus DeepSeek's ``partial`` and ``ignored`` citation
        # reliability modes).
        for required in (
            "no_results",
            "unavailable",
            "failed",
            "partial",
            "ignored",
        ):
            assert required in scenarios, f"missing scenario {required!r}"
            scenario = scenarios[required]
            assert "description" in scenario
            assert "expected_outcome" in scenario
            assert "expected_cited_source_count" in scenario
            assert "expected_web_evidence_count" in scenario

    def test_fixture_is_immutable(self) -> None:
        # Frozen dataclass + slots → mutation must raise.
        f = DEEPSEEK_ANTHROPIC_COMPAT_WIRE
        with pytest.raises((AttributeError, TypeError)):
            f.provider = "other"  # type: ignore[misc]

    def test_fixture_independent_default_factory_instances(self) -> None:
        # Each new instance must build independent list/dict copies
        # (default_factory, not shared class-level literals).
        a = DeepseekAnthropicCompatWireFixture()
        b = DeepseekAnthropicCompatWireFixture()
        a.expected_request_shape["model"] = "mutated"
        assert b.expected_request_shape["model"] == DEEPSEEK_MODEL_FLASH


# ---------------------------------------------------------------------------
# Request shape contract (Anthropic Messages API)
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_anthropic_messages_api_top_level_keys(self) -> None:
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        assert req["model"] == DEEPSEEK_MODEL_FLASH
        # Anthropic Messages API uses ``messages`` + ``system``.
        assert "messages" in req
        assert "system" in req
        assert "max_tokens" in req
        assert "tools" in req
        assert "thinking" in req
        assert req["stream"] is True

    def test_messages_use_anthropic_role_content_shape(self) -> None:
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        for entry in req["messages"]:
            assert "role" in entry
            assert "content" in entry
            assert isinstance(entry["content"], str)

    def test_deepseek_web_search_tool_shape(self) -> None:
        """DeepSeek honours Anthropic ``web_search_20250305`` server
        tool type per its compatibility table."""
        assert DEEPSEEK_WEB_SEARCH_TOOL["type"] == DEEPSEEK_WEB_SEARCH_TOOL_TYPE
        assert DEEPSEEK_WEB_SEARCH_TOOL["name"] == "web_search"
        assert DEEPSEEK_WEB_SEARCH_TOOL["max_uses"] == 1
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        web_tools = [
            t for t in req["tools"]
            if t.get("type") == DEEPSEEK_WEB_SEARCH_TOOL_TYPE
        ]
        assert len(web_tools) == 1
        assert web_tools[0]["name"] == "web_search"
        assert web_tools[0]["max_uses"] == 1

    @pytest.mark.parametrize(
        "forbidden_field",
        ["search_context_size", "include", "filters", "user_location"],
    )
    def test_no_openai_private_fields_in_request(
        self, forbidden_field: str
    ) -> None:
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        # Top-level.
        assert forbidden_field not in req
        # On the web_search tool entry.
        for tool in req["tools"]:
            if tool.get("type") == DEEPSEEK_WEB_SEARCH_TOOL_TYPE:
                assert forbidden_field not in tool

    def test_thinking_field_is_enabled(self) -> None:
        """DeepSeek thinking mode per official thinking_mode docs."""
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        assert req["thinking"] == {"type": "enabled"}

    def test_custom_function_tool_coexists_with_web_search(self) -> None:
        """The fixture includes a custom ``search_current_article``
        function tool to prove coexistence with the server-side
        ``web_search`` tool in the same round."""
        req = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_request_shape
        tool_types = {t.get("type") for t in req["tools"]}
        assert DEEPSEEK_WEB_SEARCH_TOOL_TYPE in tool_types
        assert "custom" in tool_types
        # Find the custom tool and verify its input_schema.
        custom_tools = [
            t for t in req["tools"] if t.get("type") == "custom"
        ]
        assert len(custom_tools) == 1
        custom = custom_tools[0]
        assert custom["name"] == "search_current_article"
        assert "input_schema" in custom
        assert custom["input_schema"]["type"] == "object"


# ---------------------------------------------------------------------------
# Response event sequence
# ---------------------------------------------------------------------------


class TestResponseEventSequence:
    def test_starts_with_message_start(self) -> None:
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        assert events[0]["type"] == "message_start"

    def test_ends_with_message_stop(self) -> None:
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        assert events[-1]["type"] == "message_stop"

    def test_server_tool_use_precedes_web_search_tool_result(self) -> None:
        """The model delegates to the server-side ``web_search`` tool
        (``server_tool_use`` block) BEFORE the ``web_search_tool_result``
        block carrying the actual sources."""
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        server_tool_use_idx = None
        web_search_result_idx = None
        for i, event in enumerate(events):
            if event.get("type") != "content_block_start":
                continue
            block = event.get("content_block") or {}
            if block.get("type") == "server_tool_use" and server_tool_use_idx is None:
                server_tool_use_idx = i
            elif (
                block.get("type") == "web_search_tool_result"
                and web_search_result_idx is None
            ):
                web_search_result_idx = i
        assert server_tool_use_idx is not None
        assert web_search_result_idx is not None
        assert server_tool_use_idx < web_search_result_idx

    def test_thinking_block_isolated_from_text_block(self) -> None:
        """DeepSeek thinking block MUST be isolated from the final
        text block — the SSE/DB projection never persists reasoning
        text."""
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        thinking_idx = None
        text_idx = None
        for i, event in enumerate(events):
            if event.get("type") != "content_block_start":
                continue
            block = event.get("content_block") or {}
            if block.get("type") == "thinking" and thinking_idx is None:
                thinking_idx = i
            elif block.get("type") == "text" and text_idx is None:
                text_idx = i
        assert thinking_idx is not None
        assert text_idx is not None
        # Thinking block precedes the final text block.
        assert thinking_idx < text_idx

    def test_message_delta_carries_stop_reason(self) -> None:
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        delta_events = [
            e for e in events if e.get("type") == "message_delta"
        ]
        assert len(delta_events) == 1
        assert delta_events[0]["delta"]["stop_reason"] == "end_turn"

    def test_web_search_tool_result_carries_url_and_title(self) -> None:
        """Per the Anthropic reference shape, DeepSeek DOES return
        ``url`` and ``title`` per source (unlike Qwen which is
        URL-only). ``encrypted_content`` is also present but the Host
        treats it as internal-only opaque text."""
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        for event in events:
            if event.get("type") != "content_block_start":
                continue
            block = event.get("content_block") or {}
            if block.get("type") != "web_search_tool_result":
                continue
            for entry in block.get("content") or []:
                assert entry.get("type") == "web_search_result"
                assert entry.get("url", "").startswith("https://")
                assert entry.get("title"), (
                    "DeepSeek returns title per source (Anthropic ref)"
                )
                # encrypted_content is internal-only — Host drops it
                # from public DTOs but the fixture models it.
                assert "encrypted_content" in entry


# ---------------------------------------------------------------------------
# Web evidence mapping (fixture -> WebSearchResult -> WebSearchHitView)
# ---------------------------------------------------------------------------


class TestWebEvidenceMapping:
    def test_stub_happy_path_returns_ok_with_hits(self) -> None:
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        assert result.status == "ok"
        assert len(result.hits) >= 1
        assert all(isinstance(h, WebSearchHitView) for h in result.hits)

    def test_stub_happy_path_urls_are_canonical(self) -> None:
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        for hit in result.hits:
            # canonicalize_url must round-trip.
            assert canonicalize_url(hit.raw_url) == hit.raw_url

    def test_stub_happy_path_preserves_provider_title(self) -> None:
        """DeepSeek DOES return ``title`` per source (Anthropic ref
        shape). The stub preserves it after re-canonicalization."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        fixture_titles = {
            c["url"]: c["title"]
            for c in DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_citations
        }
        for hit in result.hits:
            # The fixture URL is already canonical; the stub preserves
            # the title verbatim.
            expected = fixture_titles.get(hit.raw_url)
            assert expected is not None
            assert hit.title == expected

    def test_stub_happy_path_description_is_empty_no_encrypted_content(
        self,
    ) -> None:
        """``encrypted_content`` is internal-only opaque text — the
        Host MUST NOT expose it as a public snippet."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        for hit in result.hits:
            assert hit.description == ""

    def test_stub_honours_max_results_cap(self) -> None:
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=1))
        assert len(result.hits) == 1

    def test_stub_records_call_instrumentation(self) -> None:
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        asyncio.run(stub.search_web(query="latest python", max_results=3))
        assert stub.call_count == 1
        assert stub.last_query == "latest python"
        assert stub.last_max_results == 3

    def test_expected_citations_match_extracted_sources(self) -> None:
        """The fixture's ``expected_citations`` list must match the
        URLs the stub extracts from ``web_search_tool_result`` events.
        """
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        extracted_urls = {h.raw_url for h in result.hits}
        expected_urls = {
            canonicalize_url(c["url"])
            for c in DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_citations
        }
        assert extracted_urls == expected_urls


# ---------------------------------------------------------------------------
# Tool call coexistence
# ---------------------------------------------------------------------------


class TestToolCallCoexistence:
    def test_server_tool_use_block_present(self) -> None:
        """The fixture includes a ``server_tool_use`` block for the
        ``web_search`` server tool (distinct from a custom ``tool_use``
        block)."""
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        server_tool_use_blocks = [
            e for e in events
            if e.get("type") == "content_block_start"
            and (e.get("content_block") or {}).get("type") == "server_tool_use"
        ]
        assert len(server_tool_use_blocks) == 1
        block = server_tool_use_blocks[0]["content_block"]
        assert block["name"] == "web_search"

    def test_expected_tool_calls_match_events(self) -> None:
        fixture_calls = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_tool_calls
        assert len(fixture_calls) == 1
        call = fixture_calls[0]
        assert call["type"] == "server_tool_use"
        assert call["name"] == "web_search"
        assert "query" in call["input"]
        assert call["id"]


# ---------------------------------------------------------------------------
# Thinking isolation
# ---------------------------------------------------------------------------


class TestThinkingIsolation:
    def test_thinking_delta_events_present(self) -> None:
        events = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events
        deltas = [
            e for e in events
            if e.get("type") == "content_block_delta"
            and (e.get("delta") or {}).get("type") == "thinking_delta"
        ]
        assert len(deltas) >= 1

    def test_expected_thinking_parts_tagged_isolated(self) -> None:
        for part in DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_thinking_parts:
            assert part["isolated_from_public"] is True
            assert part["text"]

    def test_thinking_text_not_in_citations(self) -> None:
        thinking_texts = {
            p["text"] for p in DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_thinking_parts
        }
        for citation in DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_citations:
            for thinking_text in thinking_texts:
                # No thinking text bleeds into title or snippet fields.
                assert thinking_text not in (citation.get("title") or "")
                assert thinking_text not in (citation.get("snippet") or "")


# ---------------------------------------------------------------------------
# Error scenarios
# ---------------------------------------------------------------------------


class TestErrorScenarios:
    def test_no_results_scenario_maps_to_empty(self) -> None:
        stub = DeepseekAnthropicCompatAdapterStub(scenario="no_results")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "empty"
        assert result.hits == ()
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["no_results"]
        assert scenario["expected_outcome"] == "no_results"

    def test_unavailable_scenario_maps_to_unavailable(self) -> None:
        """HTTP 429/503/402 — transient (rate limit, overloaded,
        insufficient balance)."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="unavailable")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "unavailable"
        assert result.hits == ()
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["unavailable"]
        assert scenario["expected_outcome"] == "unavailable"
        assert scenario["trigger_event"]["status_code"] in (429, 503, 402)

    def test_failed_scenario_maps_to_failed(self) -> None:
        """HTTP 400/422/500 — invalid request (e.g. unsupported tool
        version rejected by DeepSeek)."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="failed")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "failed"
        assert result.hits == ()
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["failed"]
        assert scenario["expected_outcome"] == "failed"
        assert scenario["trigger_event"]["status_code"] in (400, 422, 500)

    def test_partial_scenario_maps_to_failed_strict(self) -> None:
        """``citations_behavior="partial"`` — some URLs missing.
        Strict mapping per fixture: the Host refuses to fabricate the
        missing URLs and surfaces ``failed`` (NOT ``ok`` with partial
        hits). This is the KEY RISK mitigation for DeepSeek."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="partial")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "failed"
        assert result.hits == ()
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["partial"]
        assert scenario["expected_outcome"] == "failed"
        # The partial scenario carries a non-empty content list with
        # fewer URLs than the happy path — the fixture locks this.
        trigger = scenario["trigger_event"]
        assert trigger is not None
        block = trigger["content_block"]
        assert block["type"] == "web_search_tool_result"
        assert len(block["content"]) < len(
            DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_citations
        )

    def test_ignored_scenario_maps_to_unavailable(self) -> None:
        """``citations_behavior="ignored"`` — no
        ``web_search_tool_result`` block returned at all (DeepSeek
        citation reliability risk; the compatibility table says the
        ``citations`` field is ignored). The Host treats absence as
        ``unavailable`` (NOT ``ok``)."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="ignored")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "unavailable"
        assert result.hits == ()
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["ignored"]
        assert scenario["expected_outcome"] == "unavailable"
        # Absence of the block is the signal — trigger_event is None.
        assert scenario["trigger_event"] is None

    def test_unavailable_and_failed_never_carry_hits(self) -> None:
        """WebSearchResult.__post_init__ enforces this; verify the
        stub honours the invariant."""
        for scenario in ("unavailable", "failed", "partial", "ignored"):
            stub = DeepseekAnthropicCompatAdapterStub(scenario=scenario)
            result = asyncio.run(stub.search_web(query="x", max_results=8))
            assert result.hits == (), (
                f"scenario {scenario!r} must not carry hits"
            )


# ---------------------------------------------------------------------------
# Citation reliability modes (DeepSeek-specific KEY RISK)
# ---------------------------------------------------------------------------


class TestCitationReliabilityModes:
    """DeepSeek's KEY RISK (per research) is that the ``citations``
    field is IGNORED per the compatibility table. The fixture models
    this via three ``citations_behavior`` modes that the stub adapter
    must handle differently.

    - ``stable``   → all URLs returned → ``ok``.
    - ``partial``  → some URLs missing → ``failed`` (strict).
    - ``ignored``  → no block returned → ``unavailable``.
    """

    def test_stable_mode_yields_ok_with_full_hits(self) -> None:
        """Happy path: all ``web_search_tool_result`` URLs returned
        and mappable to :class:`WebEvidence`."""
        assert DEEPSEEK_ANTHROPIC_COMPAT_WIRE.citations_behavior == "stable"
        stub = DeepseekAnthropicCompatAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "ok"
        assert len(result.hits) == len(
            DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_citations
        )

    def test_partial_mode_yields_failed_no_fabrication(self) -> None:
        """Strict mapping: the Host MUST NOT fabricate missing URLs.
        Even though the fixture's partial trigger_event carries ONE
        URL, the stub surfaces ``failed`` with zero hits."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="partial")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "failed"
        assert result.hits == ()
        # The fixture's partial trigger_event DOES carry a URL, but
        # the stub refuses to surface it as a partial success.
        scenario = DEEPSEEK_ANTHROPIC_COMPAT_WIRE.error_scenarios["partial"]
        block = scenario["trigger_event"]["content_block"]
        assert len(block["content"]) >= 1

    def test_ignored_mode_yields_unavailable_no_block(self) -> None:
        """No ``web_search_tool_result`` block at all. The Host treats
        this as ``unavailable`` (NOT ``ok`` with zero hits)."""
        stub = DeepseekAnthropicCompatAdapterStub(scenario="ignored")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "unavailable"
        assert result.hits == ()


# ---------------------------------------------------------------------------
# WebSearchResult invariants (defensive)
# ---------------------------------------------------------------------------


class TestWebSearchResultInvariants:
    def test_unavailable_result_with_hits_raises(self) -> None:
        with pytest.raises(ValueError):
            WebSearchResult(
                status="unavailable",
                summary="bad",
                hits=(
                    WebSearchHitView(
                        raw_url="https://example.com",
                        title="t",
                        description="",
                    ),
                ),
            )

    def test_failed_result_with_hits_raises(self) -> None:
        with pytest.raises(ValueError):
            WebSearchResult(
                status="failed",
                summary="bad",
                hits=(
                    WebSearchHitView(
                        raw_url="https://example.com",
                        title="t",
                        description="",
                    ),
                ),
            )

    def test_url_length_cap_enforced_on_hit_view(self) -> None:
        long_url = "https://example.com/" + "a" * (WEB_URL_MAX_LEN + 10)
        with pytest.raises(ValueError):
            WebSearchHitView(raw_url=long_url, title="t", description="")
