"""G2-Qwen: Qwen dashscope_responses wire fixture tests (OFFLINE).

Verifies that the frozen wire fixture in
``tests/fixtures/web_search/qwen_dashscope_responses_wire.py`` is
well-formed and that the stub adapter correctly maps fixture data to
the provider-neutral :class:`WebSearchResult` /
:class:`WebSearchHitView` contract.

Test surface
------------
- Fixture well-formedness: all required fields present, non-empty,
  and of the right type.
- Request shape contract:
  * OpenAI-compatible Responses API (``input`` / ``tools`` /
    ``tool_choice`` / ``stream``).
  * No OpenAI-private fields (``search_context_size``, ``include``).
  * The Qwen ``web_search`` tool shape is exactly ``{"type": "web_search"}``.
- Response event sequence:
  * ``response.created`` → ``response.in_progress`` → output items →
    ``response.completed``.
  * ``web_search_call`` lifecycle is monotone
    (in_progress → searching → completed).
  * ``response.completed.usage.x_tools.web_search.count`` is present.
- Web evidence mapping:
  * URLs in ``web_search_call.action.sources`` map 1:1 to
    :class:`WebSearchHitView` entries (after canonicalization).
  * Title falls back to display domain (Qwen returns URL-only).
  * Description stays empty (Host MUST NOT fabricate snippets).
- Tool call coexistence: a ``function_call`` output item coexists
  with the ``web_search_call`` in the same round.
- Thinking isolation: ``response.reasoning.delta`` events are tagged
  ``isolated_from_public=True`` and never appear in citations.
- Error scenarios:
  * ``no_results``   → ``outcome=no_results``.
  * ``unavailable``  → ``outcome=unavailable`` (silent 15 RPS skip).
  * ``failed``       → ``outcome=failed`` (HTTP 400/422).
  * ``rate_limited`` → ``outcome=unavailable`` (HTTP 429).

All tests are OFFLINE — no real HTTP, no real SDK.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.reader_record_ask.web_search_contracts import (
    WEB_URL_MAX_LEN,
    canonicalize_url,
    display_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchHitView,
    WebSearchResult,
)
from tests.fixtures.web_search.qwen_dashscope_responses_adapter_stub import (
    QwenDashscopeResponsesAdapterStub,
)
from tests.fixtures.web_search.qwen_dashscope_responses_wire import (
    QWEN_DASHSCOPE_RESPONSES_WIRE,
    QWEN_MODEL_NAME,
    QWEN_WEB_SEARCH_TOOL,
    QwenDashscopeResponsesWireFixture,
)

# ---------------------------------------------------------------------------
# Fixture well-formedness
# ---------------------------------------------------------------------------


class TestFixtureWellFormed:
    def test_singleton_is_constructed(self) -> None:
        assert isinstance(QWEN_DASHSCOPE_RESPONSES_WIRE, QwenDashscopeResponsesWireFixture)

    def test_required_fields_present_and_non_empty(self) -> None:
        f = QWEN_DASHSCOPE_RESPONSES_WIRE
        assert f.fixture_version
        assert f.provider == "dashscope"
        assert f.protocol == "dashscope_responses"
        assert f.model_name == QWEN_MODEL_NAME
        assert f.base_url.startswith("https://")
        assert f.web_search_tool_shape
        assert f.expected_request_shape
        assert f.expected_response_events
        assert f.expected_citations
        # thinking parts and tool calls may be empty for some
        # providers, but for Qwen's happy path they must be non-empty
        # to prove coexistence.
        assert f.expected_thinking_parts
        assert f.expected_tool_calls
        assert f.error_scenarios

    def test_error_scenarios_cover_closed_outcome_set(self) -> None:
        scenarios = QWEN_DASHSCOPE_RESPONSES_WIRE.error_scenarios
        for required in ("no_results", "unavailable", "failed", "rate_limited"):
            assert required in scenarios, f"missing scenario {required!r}"
            scenario = scenarios[required]
            assert "description" in scenario
            assert "expected_outcome" in scenario
            assert "expected_cited_source_count" in scenario
            assert "expected_web_evidence_count" in scenario

    def test_fixture_is_immutable(self) -> None:
        # Frozen dataclass + slots → mutation must raise.
        f = QWEN_DASHSCOPE_RESPONSES_WIRE
        with pytest.raises((AttributeError, TypeError)):
            f.provider = "other"  # type: ignore[misc]

    def test_fixture_independent_default_factory_instances(self) -> None:
        # Each new instance must build independent list/dict copies
        # (default_factory, not shared class-level literals).
        a = QwenDashscopeResponsesWireFixture()
        b = QwenDashscopeResponsesWireFixture()
        a.expected_request_shape["model"] = "mutated"
        assert b.expected_request_shape["model"] == QWEN_MODEL_NAME


# ---------------------------------------------------------------------------
# Request shape contract (OpenAI-compatible Responses API, no OpenAI-private)
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_responses_api_top_level_keys(self) -> None:
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        assert req["model"] == QWEN_MODEL_NAME
        # OpenAI Responses API uses ``input``, not ``messages``.
        assert "input" in req
        assert "tools" in req
        assert "stream" in req
        assert req["stream"] is True

    def test_input_uses_openai_responses_role_content_shape(self) -> None:
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        for entry in req["input"]:
            assert "role" in entry
            assert "content" in entry
            assert isinstance(entry["content"], str)

    def test_qwen_web_search_tool_shape_is_only_type(self) -> None:
        """Qwen docs only document ``{"type": "web_search"}``."""
        assert QWEN_WEB_SEARCH_TOOL == {"type": "web_search"}
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        web_tools = [
            t for t in req["tools"]
            if t.get("type") == "web_search"
        ]
        assert len(web_tools) == 1
        assert web_tools[0] == {"type": "web_search"}

    @pytest.mark.parametrize(
        "forbidden_field",
        ["search_context_size", "include", "filters", "user_location"],
    )
    def test_no_openai_private_fields_in_request(
        self, forbidden_field: str
    ) -> None:
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        # Top-level.
        assert forbidden_field not in req
        # On the web_search tool entry.
        for tool in req["tools"]:
            if tool.get("type") == "web_search":
                assert forbidden_field not in tool

    def test_tool_choice_is_auto(self) -> None:
        """Qwen default; the model decides whether to call."""
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        assert req["tool_choice"] == "auto"

    def test_reasoning_uses_effort_not_enable_thinking(self) -> None:
        """Qwen docs: ``reasoning.effort`` is recommended over the
        deprecated ``enable_thinking``."""
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        assert "reasoning" in req
        assert "effort" in req["reasoning"]
        assert "enable_thinking" not in req

    def test_function_tool_coexists_with_web_search(self) -> None:
        req = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_request_shape
        tool_types = {t.get("type") for t in req["tools"]}
        assert "web_search" in tool_types
        assert "function" in tool_types


# ---------------------------------------------------------------------------
# Response event sequence
# ---------------------------------------------------------------------------


class TestResponseEventSequence:
    def test_starts_with_created_and_in_progress(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        assert events[0]["type"] == "response.created"
        assert events[1]["type"] == "response.in_progress"

    def test_ends_with_completed(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        assert events[-1]["type"] == "response.completed"

    def test_sequence_numbers_are_monotone(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        seqs = [e["sequence_number"] for e in events]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))  # strictly increasing

    def test_web_search_call_lifecycle_is_well_formed(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        web_event_types = [
            e["type"]
            for e in events
            if "web_search_call" in e["type"]
        ]
        # in_progress -> searching -> completed (per official docs).
        assert "response.web_search_call.in_progress" in web_event_types
        assert "response.web_search_call.searching" in web_event_types
        assert "response.web_search_call.completed" in web_event_types
        # Order must be in_progress -> searching -> completed.
        ip = web_event_types.index("response.web_search_call.in_progress")
        sg = web_event_types.index("response.web_search_call.searching")
        cp = web_event_types.index("response.web_search_call.completed")
        assert ip < sg < cp

    def test_completed_event_carries_x_tools_web_search_count(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        completed = events[-1]
        usage = completed["response"]["usage"]
        assert "x_tools" in usage
        assert "web_search" in usage["x_tools"]
        assert usage["x_tools"]["web_search"]["count"] >= 1

    def test_completed_web_search_call_carries_url_only_sources(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        for event in events:
            if event.get("type") != "response.output_item.done":
                continue
            item = event.get("item") or {}
            if (
                item.get("type") == "web_search_call"
                and item.get("status") == "completed"
            ):
                sources = item.get("action", {}).get("sources", [])
                assert sources, "completed web_search_call must carry sources"
                for source in sources:
                    assert source.get("type") == "url"
                    assert source.get("url", "").startswith("https://")
                    # Qwen does NOT return title/snippet fields.
                    assert "title" not in source
                    assert "snippet" not in source


# ---------------------------------------------------------------------------
# Web evidence mapping (fixture -> WebSearchResult -> WebSearchHitView)
# ---------------------------------------------------------------------------


class TestWebEvidenceMapping:
    def test_stub_happy_path_returns_ok_with_hits(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        assert result.status == "ok"
        assert len(result.hits) >= 1
        assert all(isinstance(h, WebSearchHitView) for h in result.hits)

    def test_stub_happy_path_urls_are_canonical(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        for hit in result.hits:
            # canonicalize_url must round-trip.
            assert canonicalize_url(hit.raw_url) == hit.raw_url

    def test_stub_happy_path_title_falls_back_to_display_domain(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        for hit in result.hits:
            expected_title = display_domain_from_canonical_url(hit.raw_url)
            assert hit.title == expected_title

    def test_stub_happy_path_description_is_empty_no_fabrication(self) -> None:
        """Qwen returns URL-only; Host MUST NOT fabricate snippets."""
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        for hit in result.hits:
            assert hit.description == ""

    def test_stub_honours_max_results_cap(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=1))
        assert len(result.hits) == 1

    def test_stub_records_call_instrumentation(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        asyncio.run(stub.search_web(query="latest python", max_results=3))
        assert stub.call_count == 1
        assert stub.last_query == "latest python"
        assert stub.last_max_results == 3

    def test_expected_citations_match_extracted_sources(self) -> None:
        """The fixture's ``expected_citations`` list must match the
        URLs the stub extracts from completed ``web_search_call``
        events."""
        stub = QwenDashscopeResponsesAdapterStub(scenario="happy")
        result = asyncio.run(stub.search_web(query="python", max_results=8))
        extracted_urls = {h.raw_url for h in result.hits}
        expected_urls = {
            canonicalize_url(c["url"])
            for c in QWEN_DASHSCOPE_RESPONSES_WIRE.expected_citations
        }
        assert extracted_urls == expected_urls


# ---------------------------------------------------------------------------
# Tool call coexistence
# ---------------------------------------------------------------------------


class TestToolCallCoexistence:
    def test_function_call_output_item_present(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        function_items = [
            e for e in events
            if e.get("type") == "response.output_item.added"
            and (e.get("item") or {}).get("type") == "function_call"
        ]
        assert len(function_items) == 1
        item = function_items[0]["item"]
        assert item["name"] == "search_current_article"
        assert item["call_id"]

    def test_function_call_arguments_done_event_present(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        done_events = [
            e for e in events
            if e.get("type") == "response.function_call_arguments.done"
        ]
        assert len(done_events) == 1
        assert '"query"' in done_events[0]["arguments"]

    def test_expected_tool_calls_match_events(self) -> None:
        fixture_calls = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_tool_calls
        assert len(fixture_calls) == 1
        call = fixture_calls[0]
        assert call["name"] == "search_current_article"
        assert "python" in call["arguments"]


# ---------------------------------------------------------------------------
# Thinking isolation
# ---------------------------------------------------------------------------


class TestThinkingIsolation:
    def test_reasoning_delta_events_present(self) -> None:
        events = QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events
        deltas = [
            e for e in events if e.get("type") == "response.reasoning.delta"
        ]
        assert len(deltas) >= 1

    def test_expected_thinking_parts_tagged_isolated(self) -> None:
        for part in QWEN_DASHSCOPE_RESPONSES_WIRE.expected_thinking_parts:
            assert part["isolated_from_public"] is True
            assert part["text"]

    def test_thinking_text_not_in_citations(self) -> None:
        thinking_texts = {
            p["text"] for p in QWEN_DASHSCOPE_RESPONSES_WIRE.expected_thinking_parts
        }
        for citation in QWEN_DASHSCOPE_RESPONSES_WIRE.expected_citations:
            for thinking_text in thinking_texts:
                # No thinking text bleeds into title or snippet fields.
                assert thinking_text not in (citation.get("title") or "")
                assert thinking_text not in (citation.get("snippet") or "")


# ---------------------------------------------------------------------------
# Error scenarios
# ---------------------------------------------------------------------------


class TestErrorScenarios:
    def test_no_results_scenario_maps_to_empty(self) -> None:
        stub = QwenDashscopeResponsesAdapterStub(scenario="no_results")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "empty"
        assert result.hits == ()
        scenario = QWEN_DASHSCOPE_RESPONSES_WIRE.error_scenarios["no_results"]
        assert scenario["expected_outcome"] == "no_results"

    def test_unavailable_scenario_maps_to_unavailable(self) -> None:
        """Silent 15 RPS skip — no hits, no fabricated sources."""
        stub = QwenDashscopeResponsesAdapterStub(scenario="unavailable")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "unavailable"
        assert result.hits == ()
        scenario = QWEN_DASHSCOPE_RESPONSES_WIRE.error_scenarios["unavailable"]
        assert scenario["expected_outcome"] == "unavailable"
        # Silent skip is signalled by ABSENCE of web_search_call events.
        assert scenario["trigger_event"] is None

    def test_failed_scenario_maps_to_failed(self) -> None:
        """HTTP 400/422 — invalid request (e.g. OpenAI-private field
        rejected by DashScope)."""
        stub = QwenDashscopeResponsesAdapterStub(scenario="failed")
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        assert result.status == "failed"
        assert result.hits == ()
        scenario = QWEN_DASHSCOPE_RESPONSES_WIRE.error_scenarios["failed"]
        assert scenario["expected_outcome"] == "failed"
        assert scenario["trigger_event"]["status_code"] in (400, 422, 500)

    def test_rate_limited_scenario_maps_to_unavailable(self) -> None:
        """HTTP 429 — explicit rate limit. Host treats as transient
        ``unavailable`` (NOT ``failed``)."""
        stub = QwenDashscopeResponsesAdapterStub(scenario="rate_limited")
        # The stub currently routes rate_limited through the same
        # ``unavailable`` branch by falling through to the unknown
        # scenario path. The error_scenarios fixture still locks the
        # expected mapping for G3.
        result = asyncio.run(stub.search_web(query="x", max_results=8))
        # Stub falls through to "unavailable" for any non-happy
        # non-no_results non-failed scenario.
        assert result.status == "unavailable"
        scenario = QWEN_DASHSCOPE_RESPONSES_WIRE.error_scenarios["rate_limited"]
        assert scenario["expected_outcome"] == "unavailable"
        assert scenario["trigger_event"]["status_code"] == 429

    def test_unavailable_and_failed_never_carry_hits(self) -> None:
        """WebSearchResult.__post_init__ enforces this; verify the
        stub honours the invariant."""
        for scenario in ("unavailable", "failed"):
            stub = QwenDashscopeResponsesAdapterStub(scenario=scenario)
            result = asyncio.run(stub.search_web(query="x", max_results=8))
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
