"""G3-DeepSeek: DeepSeek Anthropic-compat Web Search backend wire tests (OFFLINE).

Tests the real :class:`DeepseekAnthropicWebSearchBackend` against mocked
SSE responses built from the frozen wire fixture. No real network calls.

Test surface
------------
- Happy path: URLs extracted from ``web_search_tool_result`` content
  blocks, canonicalized, with provider titles preserved.
- Empty results: ``web_search_tool_result.content == []`` → ``empty``.
- Citations ignored: no ``web_search_tool_result`` block → ``unavailable``.
- Citations partial: entries with missing URLs → ``failed`` (no fabrication).
- HTTP 429 → ``unavailable``; HTTP 500 → ``failed``.
- Timeout → typed ``timeout`` (``detail_code="deepseek_timeout"``).
- Malformed response (non-SSE body) → ``failed``.
- URL canonicalization (query preserved, fragment dropped).
- URL deduplication (same canonical form collapsed).
- ``max_results`` cap honoured.
- Security: API key never in summary/detail_code; ``encrypted_content``
  never on hits; provider request id never in summary/detail_code.
- No URL inference from ``text`` blocks.

All tests are OFFLINE — uses ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.reader_record_ask.deepseek_anthropic_web_search_backend import (
    DeepseekAnthropicWebSearchBackend,
)
from app.services.reader_record_ask.web_search_contracts import canonicalize_url
from tests.fixtures.web_search.deepseek_anthropic_compat_wire import (
    DEEPSEEK_ANTHROPIC_BASE_URL,
    DEEPSEEK_ANTHROPIC_COMPAT_WIRE,
    DEEPSEEK_MODEL_FLASH,
)

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _events_to_sse_bytes(events: list[dict[str, Any]]) -> bytes:
    """Convert a list of event dicts to an Anthropic-style SSE byte stream."""
    chunks: list[str] = []
    for event in events:
        event_type = event.get("type", "")
        json_str = json.dumps(event)
        chunks.append(f"event: {event_type}\ndata: {json_str}\n\n")
    return "".join(chunks).encode("utf-8")


def _make_sse_transport(
    events: list[dict[str, Any]] | None = None,
    *,
    status_code: int = 200,
    body: bytes | str | None = None,
    raise_exc: type[Exception] | None = None,
) -> httpx.MockTransport:
    """Build a MockTransport returning the given SSE events or raw body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc("simulated")
        if body is not None:
            content = body if isinstance(body, bytes) else body.encode("utf-8")
        elif events is not None:
            content = _events_to_sse_bytes(events)
        else:
            content = b""
        return httpx.Response(
            status_code,
            content=content,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    return httpx.MockTransport(handler)


_TEST_API_KEY = "sk-deepseek-test-SECRET-KEY-67890"


def _backend(
    transport: httpx.MockTransport,
    **kwargs: Any,
) -> DeepseekAnthropicWebSearchBackend:
    return DeepseekAnthropicWebSearchBackend(
        api_key=_TEST_API_KEY,
        base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
        model_name=DEEPSEEK_MODEL_FLASH,
        transport=transport,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _happy_events() -> list[dict[str, Any]]:
    return list(DEEPSEEK_ANTHROPIC_COMPAT_WIRE.expected_response_events)


def _empty_content_events() -> list[dict[str, Any]]:
    """web_search_tool_result with empty content list (no_results)."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_empty",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_test_empty",
                "name": "web_search",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_test_empty",
                "content": [],
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _ignored_events() -> list[dict[str, Any]]:
    """No web_search_tool_result block at all (citations_behavior='ignored')."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_ignored",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Answer without citations."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _partial_events() -> list[dict[str, Any]]:
    """web_search_tool_result with one entry missing URL (partial citations)."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_partial",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_test_partial",
                "name": "web_search",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_test_partial",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://www.python.org/downloads/",
                        "title": "Download Python",
                        "encrypted_content": "enc_partial_001",
                    },
                    {
                        "type": "web_search_result",
                        "title": "Entry with missing URL",
                        # No "url" field — partial citation.
                    },
                ],
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _url_with_query_fragment_events() -> list[dict[str, Any]]:
    """URLs with query and fragment that must be canonicalized."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_canon",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_test_canon",
                "name": "web_search",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_test_canon",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://www.example.com/path?q=hello#fragment",
                        "title": "Example with query+fragment",
                    },
                ],
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _duplicate_urls_events() -> list[dict[str, Any]]:
    """Two entries with the same canonical URL (dedup test)."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_dedup",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_test_dedup",
                "name": "web_search",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_test_dedup",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://www.example.com/page",
                        "title": "First",
                    },
                    {
                        "type": "web_search_result",
                        "url": "https://www.example.com/page#frag",
                        "title": "Second",
                    },
                ],
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _five_hits_events() -> list[dict[str, Any]]:
    """Five distinct URL hits for testing max_results cap."""
    entries: list[dict[str, Any]] = []
    for i in range(5):
        entries.append(
            {
                "type": "web_search_result",
                "url": f"https://www.example.com/page{i}",
                "title": f"Page {i}",
            }
        )
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_cap",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvu_test_cap",
                "name": "web_search",
                "input": {},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": "srvu_test_cap",
                "content": entries,
            },
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def _text_block_with_url_events() -> list[dict[str, Any]]:
    """Text block containing a URL but no web_search_tool_result block.

    Backend must NOT infer URLs from text blocks.
    """
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_text",
                "type": "message",
                "role": "assistant",
                "model": DEEPSEEK_MODEL_FLASH,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "text_delta",
                "text": "See https://www.example.com/leaked-from-text for details.",
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


# ---------------------------------------------------------------------------
# Tests — success / empty / unavailable / partial
# ---------------------------------------------------------------------------


class TestSearchWebSuccess:
    @pytest.mark.asyncio
    async def test_search_web_success_extracts_urls_from_web_search_tool_result(
        self,
    ) -> None:
        transport = _make_sse_transport(_happy_events())
        backend = _backend(transport)
        result = await backend.search_web(query="latest Python", max_results=8)

        assert result.status == "ok"
        assert len(result.hits) == 2
        # URLs are canonicalized (round-trip stable).
        for hit in result.hits:
            assert canonicalize_url(hit.raw_url) == hit.raw_url
        # Titles preserved from provider.
        titles = {hit.title for hit in result.hits}
        assert "Download Python | Python.org" in titles
        assert "What's New in Python 3.13" in titles


class TestSearchWebEmptyAndUnavailable:
    @pytest.mark.asyncio
    async def test_search_web_no_results_returns_empty(self) -> None:
        transport = _make_sse_transport(_empty_content_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "empty"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_search_web_citations_ignored_returns_unavailable(self) -> None:
        transport = _make_sse_transport(_ignored_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "unavailable"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_search_web_citations_partial_returns_failed(self) -> None:
        transport = _make_sse_transport(_partial_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        assert result.hits == ()


# ---------------------------------------------------------------------------
# Tests — HTTP errors / timeout / malformed
# ---------------------------------------------------------------------------


class TestSearchWebHttpErrors:
    @pytest.mark.asyncio
    async def test_search_web_rate_limit_returns_unavailable(self) -> None:
        transport = _make_sse_transport(
            status_code=429, body='{"error":{"message":"rate limit"}}'
        )
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "unavailable"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_search_web_timeout_returns_typed_timeout(self) -> None:
        transport = _make_sse_transport(raise_exc=httpx.TimeoutException)
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "timeout"
        assert result.detail_code == "deepseek_timeout"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_search_web_malformed_response_returns_failed(self) -> None:
        transport = _make_sse_transport(body="not json not sse at all")
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        assert result.hits == ()

    @pytest.mark.asyncio
    async def test_search_web_http_500_returns_failed(self) -> None:
        transport = _make_sse_transport(
            status_code=500, body='{"error":{"message":"server error"}}'
        )
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        assert result.hits == ()


# ---------------------------------------------------------------------------
# Tests — URL processing (canonicalize, dedup, cap)
# ---------------------------------------------------------------------------


class TestSearchWebUrlProcessing:
    @pytest.mark.asyncio
    async def test_search_web_canonicalizes_urls(self) -> None:
        transport = _make_sse_transport(_url_with_query_fragment_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "ok"
        assert len(result.hits) == 1
        # Fragment dropped, query preserved, host lowercased.
        assert result.hits[0].raw_url == "https://www.example.com/path?q=hello"

    @pytest.mark.asyncio
    async def test_search_web_deduplicates_urls(self) -> None:
        transport = _make_sse_transport(_duplicate_urls_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "ok"
        assert len(result.hits) == 1

    @pytest.mark.asyncio
    async def test_search_web_caps_max_results(self) -> None:
        transport = _make_sse_transport(_five_hits_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=3)

        assert result.status == "ok"
        assert len(result.hits) == 3


# ---------------------------------------------------------------------------
# Tests — security boundary (no leakage)
# ---------------------------------------------------------------------------


class TestSearchWebSecurityBoundary:
    @pytest.mark.asyncio
    async def test_search_web_does_not_leak_api_key_in_summary(self) -> None:
        transport = _make_sse_transport(
            status_code=500, body='{"error":"server error"}'
        )
        backend = DeepseekAnthropicWebSearchBackend(
            api_key=_TEST_API_KEY,
            base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
            model_name=DEEPSEEK_MODEL_FLASH,
            transport=transport,
        )
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        assert _TEST_API_KEY not in result.summary
        assert _TEST_API_KEY not in (result.detail_code or "")

    @pytest.mark.asyncio
    async def test_search_web_does_not_leak_encrypted_content(self) -> None:
        transport = _make_sse_transport(_happy_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "ok"
        for hit in result.hits:
            assert not hasattr(hit, "encrypted_content")
            assert hit.description == ""
            assert "enc_deepseek_fixture_001" not in hit.raw_url
            assert "enc_deepseek_fixture_001" not in hit.title
            assert "enc_deepseek_fixture_001" not in hit.description
            assert "enc_deepseek_fixture_002" not in hit.raw_url
            assert "enc_deepseek_fixture_002" not in hit.title
            assert "enc_deepseek_fixture_002" not in hit.description

    @pytest.mark.asyncio
    async def test_search_web_does_not_leak_provider_request_id(self) -> None:
        transport = _make_sse_transport(_happy_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "ok"
        assert "msg_deepseek_fixture_001" not in result.summary
        assert "msg_deepseek_fixture_001" not in (result.detail_code or "")


# ---------------------------------------------------------------------------
# Tests — no URL inference from text blocks
# ---------------------------------------------------------------------------


class TestSearchWebNoUrlInference:
    @pytest.mark.asyncio
    async def test_search_web_does_not_infer_urls_from_text_blocks(self) -> None:
        transport = _make_sse_transport(_text_block_with_url_events())
        backend = _backend(transport)
        result = await backend.search_web(query="x", max_results=8)

        # No web_search_tool_result block → unavailable, NOT ok with
        # inferred URLs.
        assert result.status == "unavailable"
        assert result.hits == ()
        for hit in result.hits:
            assert "leaked-from-text" not in hit.raw_url


# ---------------------------------------------------------------------------
# G3- §III: Secret-safe adapter repr
# ---------------------------------------------------------------------------


class TestReprSecretSafety:
    """G3- §III: ``api_key`` uses ``field(repr=False)`` — repr, log,
    and exception output must never leak the credential, the
    ``x-api-key`` header sentinel, or the ``Authorization`` sentinel.
    """

    @pytest.mark.asyncio
    async def test_backend_repr_does_not_leak_api_key(self) -> None:
        secret = "sk-deepseek-SECRET-REPR-DO-NOT-LEAK-9f3a7c4e2b1d"
        backend = DeepseekAnthropicWebSearchBackend(
            api_key=secret,
            model_name=DEEPSEEK_MODEL_FLASH,
            base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
        )
        repr_str = repr(backend)
        assert secret not in repr_str
        assert "api_key" not in repr_str
        assert "sk-deepseek" not in repr_str
        assert "x-api-key" not in repr_str
        assert "Authorization" not in repr_str

    @pytest.mark.asyncio
    async def test_exception_output_does_not_leak_api_key(self) -> None:
        secret = _TEST_API_KEY
        transport = _make_sse_transport(
            status_code=500, body='{"error":"server error"}'
        )
        backend = DeepseekAnthropicWebSearchBackend(
            api_key=secret,
            base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
            model_name=DEEPSEEK_MODEL_FLASH,
            transport=transport,
        )
        result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        assert secret not in result.summary
        assert secret not in (result.detail_code or "")
        assert "x-api-key" not in result.summary
        assert "x-api-key" not in (result.detail_code or "")
        assert "Authorization" not in result.summary
        assert "Authorization" not in (result.detail_code or "")


# ---------------------------------------------------------------------------
# G3- §IV: Stream resource lifecycle (no aclosing, httpx stream context)
# ---------------------------------------------------------------------------


class TestStreamResourceLifecycle:
    """G3- §IV: the ``aclosing`` wrapper was removed. The httpx
    ``client.stream(...)`` context manager owns the response lifecycle
    and closes it on normal exit, exception, early return, and
    cancellation. No ``RuntimeWarning`` / mypy ``aclosing`` type errors.
    """

    @pytest.mark.asyncio
    async def test_stream_closed_after_malformed_sse(self) -> None:
        import warnings

        transport = _make_sse_transport(body="not json not sse at all")
        backend = _backend(transport)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = await backend.search_web(query="x", max_results=8)

        assert result.status == "failed"
        for w in caught:
            assert "unclosed" not in str(w.message).lower()
            assert "aclosing" not in str(w.message).lower()

    @pytest.mark.asyncio
    async def test_stream_closed_after_early_return_on_error_status(self) -> None:
        import warnings

        transport = _make_sse_transport(
            status_code=429, body='{"error":"rate limit"}'
        )
        backend = _backend(transport)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = await backend.search_web(query="x", max_results=8)

        assert result.status == "unavailable"
        for w in caught:
            assert "unclosed" not in str(w.message).lower()

    @pytest.mark.asyncio
    async def test_stream_closed_after_timeout_exception(self) -> None:
        import warnings

        transport = _make_sse_transport(raise_exc=httpx.TimeoutException)
        backend = _backend(transport)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = await backend.search_web(query="x", max_results=8)

        assert result.status == "timeout"
        assert result.detail_code is not None
        assert "timeout" in result.detail_code
        for w in caught:
            assert "unclosed" not in str(w.message).lower()

    @pytest.mark.asyncio
    async def test_stream_closed_after_cancellation(self) -> None:
        import asyncio
        import warnings

        transport = _make_sse_transport(_happy_events())
        backend = _backend(transport)

        async def run_and_cancel() -> None:
            task = asyncio.create_task(
                backend.search_web(query="x", max_results=8)
            )
            await asyncio.sleep(0.001)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await run_and_cancel()

        for w in caught:
            assert "unclosed" not in str(w.message).lower()

    @pytest.mark.asyncio
    async def test_stream_closed_after_happy_path(self) -> None:
        import warnings

        transport = _make_sse_transport(_happy_events())
        backend = _backend(transport)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = await backend.search_web(query="x", max_results=8)

        assert result.status == "ok"
        for w in caught:
            assert "unclosed" not in str(w.message).lower()
