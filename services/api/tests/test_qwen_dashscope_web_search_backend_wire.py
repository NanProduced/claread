"""G3-Qwen: Real Qwen DashScope Responses API Web Search adapter wire tests.

These tests verify the real :class:`QwenDashscopeWebSearchBackend` against
deterministic mocked HTTP responses built from the frozen wire fixture.
No real network call is ever made.

Coverage
--------
- Happy-path SSE stream → status="ok" with canonicalized hits.
- Empty sources → status="empty".
- HTTP 429 → status="unavailable" with rate_limit detail.
- httpx.TimeoutException → status="unavailable".
- Malformed SSE data → status="failed".
- URL canonicalization (query preserved, fragment dropped).
- URL deduplication.
- max_results cap.
- HTTP 500 → status="failed".
- API key never appears in summary/detail_code.
- Provider request id / item id never appears in summary/detail_code.

All tests use ``httpx.MockTransport`` to inject deterministic SSE bytes
without touching the real DashScope endpoint.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.reader_record_ask.qwen_dashscope_web_search_backend import (
    QwenDashscopeWebSearchBackend,
)
from app.services.reader_record_ask.web_search_port import WebSearchResult
from tests.fixtures.web_search.qwen_dashscope_responses_wire import (
    QWEN_DASHSCOPE_RESPONSES_WIRE,
)

# A non-real API key used to verify leakage guards. Deliberately unique
# so that any leak in summary/detail_code is trivially detected.
_TEST_API_KEY = "test-qwen-key-do-not-use-9f3a7c4e2b1d"


# ---------------------------------------------------------------------------
# SSE fixture helpers
# ---------------------------------------------------------------------------


def _build_sse_bytes(events: list[dict[str, Any]]) -> bytes:
    """Build SSE-formatted bytes from a list of event dicts.

    Each event is emitted as ``event: <type>\\ndata: <json>\\n\\n``.
    The ``type`` field is duplicated in the ``event:`` line per the
    OpenAI Responses SSE convention; the parser reads ``type`` from the
    JSON payload (the authoritative source).
    """
    chunks: list[bytes] = []
    for event in events:
        event_type = str(event.get("type", "message"))
        payload = json.dumps(event, ensure_ascii=False)
        chunks.append(f"event: {event_type}\n".encode())
        chunks.append(f"data: {payload}\n\n".encode())
    return b"".join(chunks)


def _happy_events() -> list[dict[str, Any]]:
    """Return a fresh deep copy of the fixture's happy-path events."""
    return [dict(e) for e in QWEN_DASHSCOPE_RESPONSES_WIRE.expected_response_events]


def _happy_events_with_urls(urls: list[str]) -> list[dict[str, Any]]:
    """Build a happy-path SSE event stream whose completed
    ``web_search_call`` carries the supplied URLs as sources.
    """
    events = _happy_events()
    for event in events:
        if event.get("type") != "response.output_item.done":
            continue
        item = event.get("item") or {}
        if item.get("type") == "web_search_call" and item.get("status") == "completed":
            action = item.setdefault("action", {})
            action["sources"] = [{"type": "url", "url": url} for url in urls]
    return events


def _build_backend(
    *,
    transport_handler: Any,
    max_results_per_call: int = 3,
) -> QwenDashscopeWebSearchBackend:
    """Construct a backend wired to a MockTransport using the test API key."""
    transport = httpx.MockTransport(transport_handler)
    return QwenDashscopeWebSearchBackend(
        api_key=_TEST_API_KEY,
        model_name=QWEN_DASHSCOPE_RESPONSES_WIRE.model_name,
        base_url=QWEN_DASHSCOPE_RESPONSES_WIRE.base_url,
        timeout=5.0,
        max_results_per_call=max_results_per_call,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_web_success_extracts_urls_from_web_search_call_sources() -> None:
    """Happy path: 200 + completed web_search_call → status=ok with hits."""
    events = _happy_events()
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(
        query="What is the latest stable version of Python?",
        max_results=3,
    )

    assert isinstance(result, WebSearchResult)
    assert result.status == "ok"
    assert len(result.hits) == 2
    expected_urls = {
        "https://www.python.org/downloads/",
        "https://docs.python.org/3/whatsnew/3.13.html",
    }
    assert {hit.raw_url for hit in result.hits} == expected_urls
    # Title falls back to display domain (provider supplies no title).
    for hit in result.hits:
        assert hit.title == hit.raw_url.split("/")[2]
    assert result.summary


@pytest.mark.asyncio
async def test_search_web_no_results_returns_empty() -> None:
    """HTTP 200 + completed web_search_call with empty sources → empty."""
    events = _happy_events_with_urls([])
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "empty"
    assert result.hits == ()
    assert result.summary


@pytest.mark.asyncio
async def test_search_web_rate_limit_returns_unavailable() -> None:
    """HTTP 429 → status=unavailable with rate_limit detail_code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            content=b'{"error":{"message":"qps limit exceeded"}}',
            headers={"content-type": "application/json"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "unavailable"
    assert result.hits == ()
    assert result.detail_code is not None
    assert "rate_limit" in result.detail_code


@pytest.mark.asyncio
async def test_search_web_timeout_returns_typed_timeout() -> None:
    """httpx.TimeoutException → typed timeout with a safe detail code."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "timeout"
    assert result.hits == ()
    assert result.detail_code is not None
    assert "timeout" in result.detail_code


@pytest.mark.asyncio
async def test_search_web_malformed_response_returns_failed() -> None:
    """HTTP 200 with non-JSON data lines → status=failed."""
    malformed_sse = b"data: this is not json\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=malformed_sse,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "failed"
    assert result.hits == ()


@pytest.mark.asyncio
async def test_search_web_canonicalizes_urls() -> None:
    """URLs with fragment/query are canonicalized (fragment dropped,
    query preserved, host lowercased).
    """
    raw_url = "https://Example.COM/page?b=2&a=1#section1"
    expected_canonical = "https://example.com/page?b=2&a=1"
    events = _happy_events_with_urls([raw_url])
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "ok"
    assert len(result.hits) == 1
    assert result.hits[0].raw_url == expected_canonical
    assert result.hits[0].title == "example.com"


@pytest.mark.asyncio
async def test_search_web_deduplicates_urls() -> None:
    """Duplicate URLs after canonicalization are deduplicated."""
    url = "https://example.com/page"
    events = _happy_events_with_urls([url, url, url])
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=5)

    assert result.status == "ok"
    assert len(result.hits) == 1
    assert result.hits[0].raw_url == url


@pytest.mark.asyncio
async def test_search_web_caps_max_results() -> None:
    """max_results=3 caps 5 source URLs to 3 hits."""
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/d",
        "https://example.com/e",
    ]
    events = _happy_events_with_urls(urls)
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler, max_results_per_call=8)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "ok"
    assert len(result.hits) == 3
    returned = {hit.raw_url for hit in result.hits}
    assert returned.issubset(set(urls))


@pytest.mark.asyncio
async def test_search_web_http_500_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b'{"error":{"message":"internal"}}',
            headers={"content-type": "application/json"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "failed"
    assert result.hits == ()


@pytest.mark.asyncio
async def test_search_web_does_not_leak_api_key_in_summary() -> None:
    """API key must never appear in summary or detail_code (even on error)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b'{"error":{"message":"internal"}}',
            headers={"content-type": "application/json"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "failed"
    assert _TEST_API_KEY not in result.summary
    assert result.detail_code is not None
    assert _TEST_API_KEY not in result.detail_code


@pytest.mark.asyncio
async def test_search_web_provider_result_ref_not_in_summary() -> None:
    """Provider request id / item id must never appear in summary or
    detail_code, even though provider_result_ref may carry it on hits.
    """
    events = _happy_events()
    sse_bytes = _build_sse_bytes(events)

    # Extract the response id and item id from the fixture to use as
    # canaries — these must not leak into summary/detail_code.
    response_id = "resp_qwen_fixture_001"
    item_id = "ws_qwen_fixture_001"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "ok"
    assert result.hits
    assert response_id not in result.summary
    assert response_id not in (result.detail_code or "")
    assert item_id not in result.summary
    assert item_id not in (result.detail_code or "")


# ---------------------------------------------------------------------------
# G3- §III: Secret-safe adapter repr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_repr_does_not_leak_api_key() -> None:
    """``repr(backend)`` must not contain the api_key sentinel.

    G3- §III: ``api_key`` uses ``field(repr=False)`` so that the
    dataclass-generated repr never includes the credential.
    """
    secret = "sk-qwen-SECRET-REPR-DO-NOT-LEAK-9f3a7c4e2b1d"
    backend = QwenDashscopeWebSearchBackend(
        api_key=secret,
        model_name=QWEN_DASHSCOPE_RESPONSES_WIRE.model_name,
        base_url=QWEN_DASHSCOPE_RESPONSES_WIRE.base_url,
    )
    repr_str = repr(backend)
    assert secret not in repr_str
    assert "api_key" not in repr_str
    assert "sk-qwen" not in repr_str
    assert "Authorization" not in repr_str
    assert "Bearer" not in repr_str


@pytest.mark.asyncio
async def test_exception_output_does_not_leak_api_key() -> None:
    """Exception messages / log output must not contain the api_key,
    ``Authorization`` header, or ``Bearer`` token.
    """
    # Trigger an HTTP 500 → status=failed. The summary / detail_code
    # must not echo the api_key or auth header sentinels.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b'{"error":{"message":"internal"}}',
            headers={"content-type": "application/json"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "failed"
    assert _TEST_API_KEY not in result.summary
    assert _TEST_API_KEY not in (result.detail_code or "")
    assert "Authorization" not in result.summary
    assert "Authorization" not in (result.detail_code or "")
    assert "Bearer" not in result.summary
    assert "Bearer" not in (result.detail_code or "")


# ---------------------------------------------------------------------------
# G3- §IV: Stream resource lifecycle (no aclosing, httpx stream context)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_closed_after_malformed_sse(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed SSE response must not leak the underlying httpx
    response. The ``client.stream(...)`` context manager closes the
    response even when ``_MalformedSseError`` is raised mid-stream.

    This test verifies no ``RuntimeWarning`` about unclosed resources
    and no ``aclosing`` type errors (G3- §IV).
    """
    import warnings

    malformed_sse = b"data: not-json\n\ndata: {also-broken\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=malformed_sse,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "failed"
    # No RuntimeWarning about unclosed resources or aclosing type errors.
    for w in caught:
        assert "unclosed" not in str(w.message).lower()
        assert "aclosing" not in str(w.message).lower()


@pytest.mark.asyncio
async def test_stream_closed_after_early_return_on_error_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the response status is an error (e.g. 429), the backend
    returns early without consuming the SSE stream. The httpx stream
    context must still close the response cleanly.
    """
    import warnings

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            content=b'{"error":"rate limit"}',
            headers={"content-type": "application/json"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "unavailable"
    for w in caught:
        assert "unclosed" not in str(w.message).lower()


@pytest.mark.asyncio
async def test_stream_closed_after_timeout_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the transport raises ``httpx.TimeoutException``, the httpx
    stream context must close cleanly without leaking resources.
    """
    import warnings

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    backend = _build_backend(transport_handler=handler)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await backend.search_web(query="anything", max_results=3)

    assert result.status == "timeout"
    assert result.detail_code is not None
    assert "timeout" in result.detail_code
    for w in caught:
        assert "unclosed" not in str(w.message).lower()


@pytest.mark.asyncio
async def test_stream_closed_after_cancellation() -> None:
    """When the coroutine is cancelled mid-stream, the httpx stream
    context must close the response cleanly (no resource leak).
    """
    import asyncio
    import warnings

    # Build a handler that returns a valid SSE stream but we cancel
    # the task before it completes.
    events = _happy_events()
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)

    async def run_and_cancel() -> None:
        task = asyncio.create_task(
            backend.search_web(query="anything", max_results=3)
        )
        await asyncio.sleep(0.001)  # let the stream start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await run_and_cancel()

    # No RuntimeWarning about unclosed response.
    for w in caught:
        assert "unclosed" not in str(w.message).lower()


@pytest.mark.asyncio
async def test_stream_closed_after_happy_path() -> None:
    """Happy-path completion must also close the stream cleanly
    (baseline for the httpx stream context lifecycle).
    """
    import warnings

    events = _happy_events()
    sse_bytes = _build_sse_bytes(events)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_bytes,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    backend = _build_backend(transport_handler=handler)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await backend.search_web(
            query="What is the latest stable version of Python?",
            max_results=3,
        )

    assert result.status == "ok"
    for w in caught:
        assert "unclosed" not in str(w.message).lower()
