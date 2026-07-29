"""G3-Qwen: Real Qwen DashScope Responses API Web Search adapter.

This adapter implements the :class:`WebSearchBackend` Protocol by calling
the DashScope Responses API (OpenAI-compatible ``/responses`` endpoint)
with the native ``{"type": "web_search"}`` tool. It is the production
replacement for the G2 fixture stub.

Boundary rules
--------------
- Calls the real DashScope Responses endpoint via ``httpx``.
- Uses the provider-native ``{"type": "web_search"}`` tool only — no
  OpenAI-private fields (``search_context_size``, ``include``) are sent.
- Extracts URLs only from ``web_search_call.action.sources`` entries
  with ``type == "url"`` on ``response.output_item.done`` events where
  ``item.status == "completed"``.
- Canonicalizes, deduplicates, and caps hits at ``max_results``.
- Uses :func:`display_domain_from_canonical_url` as the title when the
  provider supplies no title (Qwen Responses sources are URL-only).
- **Never** exposes answer text, reasoning, request id, usage, query,
  or raw payload in the public :class:`WebSearchResult`.
- ``provider_result_ref`` carries the provider-side ``web_search_call``
  item id for internal correlation only — it must never appear in
  public DTOs, SSE, logs, or cold history.

Outcome mapping
---------------
- HTTP 200 + ≥1 canonical hit → ``status="ok"``
- HTTP 200 + 0 hits (search completed) → ``status="empty"``
- HTTP 429 / 503 → ``status="unavailable"`` (detail_code distinguishes
  ``qwen_rate_limit`` / ``qwen_service_unavailable``)
- HTTP 408 / ``httpx.TimeoutException`` → ``status="unavailable"``
  (detail_code ``qwen_timeout``)
- HTTP 400 / 422 / 500 → ``status="failed"``
- Malformed SSE JSON / missing key fields → ``status="failed"``
- URL canonicalize failure: per-source fail-closed (skip the hit)
"""

from __future__ import annotations

import datetime
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from app.services.reader_record_ask.web_search_contracts import (
    canonicalize_url,
    display_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchHitView,
    WebSearchResult,
)

# ---------------------------------------------------------------------------
# Fixed summary / detail_code constants
# ---------------------------------------------------------------------------
# These NEVER include provider text, API key, URL, request id, or raw
# payload. They are the only strings that may appear on the public
# WebSearchResult.summary / detail_code surface.

_SUMMARY_OK = "Qwen DashScope web search completed"
_SUMMARY_EMPTY = "Qwen DashScope web search completed with no sources"
_SUMMARY_RATE_LIMIT = "Qwen DashScope web search rate-limited; try again later"
_SUMMARY_SERVICE_UNAVAILABLE = "Qwen DashScope web search temporarily unavailable"
_SUMMARY_TIMEOUT = "Qwen DashScope web search timed out"
_SUMMARY_FAILED = "Qwen DashScope web search failed"
_SUMMARY_MALFORMED = "Qwen DashScope web search returned malformed data"
_SUMMARY_HTTP_ERROR = "Qwen DashScope web search HTTP error"

_DETAIL_OK = "qwen_completed"
_DETAIL_EMPTY = "qwen_no_canonical_hits"
_DETAIL_RATE_LIMIT = "qwen_rate_limit"
_DETAIL_SERVICE_UNAVAILABLE = "qwen_service_unavailable"
_DETAIL_TIMEOUT = "qwen_timeout"
_DETAIL_MALFORMED = "qwen_malformed_response"
_DETAIL_HTTP_400 = "qwen_http_400"
_DETAIL_HTTP_422 = "qwen_http_422"
_DETAIL_HTTP_500 = "qwen_http_500"
_DETAIL_HTTP_OTHER = "qwen_http_error"
_DETAIL_HTTP_TRANSPORT = "qwen_http_transport_error"
_DETAIL_UNEXPECTED = "qwen_unexpected_error"


# ---------------------------------------------------------------------------
# Internal exception for malformed SSE payloads
# ---------------------------------------------------------------------------


class _MalformedSseError(Exception):
    """Raised internally when an SSE data line cannot be parsed as a JSON
    object, or when a ``response.output_item.done`` event is missing the
    ``item`` field.

    The caller maps this to ``status="failed"`` with a fixed summary.
    """


# ---------------------------------------------------------------------------
# Minimal UTF-8 text decoder for streaming SSE
# ---------------------------------------------------------------------------


class _TextDecoder:
    """Stateless UTF-8 decoder for byte chunks.

    SSE streams are always UTF-8. This decoder handles multi-byte
    characters split across chunk boundaries without the overhead or
    generator nesting of ``codecs.getincrementaldecoder``.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def decode(self, chunk: bytes) -> str:
        self._buffer += chunk
        # Try to decode all complete characters.
        try:
            text = self._buffer.decode("utf-8")
            self._buffer = b""
        except UnicodeDecodeError as exc:
            # Truncate to the last complete character boundary.
            if exc.reason == "unexpected end of data":
                text = self._buffer[: exc.start].decode("utf-8")
                self._buffer = self._buffer[exc.start :]
            else:
                # Invalid byte sequence — decode with replacement.
                text = self._buffer.decode("utf-8", errors="replace")
                self._buffer = b""
        return text

    def flush(self) -> str:
        if not self._buffer:
            return ""
        text = self._buffer.decode("utf-8", errors="replace")
        self._buffer = b""
        return text


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QwenDashscopeWebSearchBackend:
    """Production Qwen DashScope Responses API Web Search adapter.

    Implements :class:`WebSearchBackend` by calling the DashScope
    Responses API (``POST {base_url}/responses``) with the native
    ``{"type": "web_search"}`` tool and parsing the SSE response stream.

    Construction
    ------------
    - ``api_key``: DashScope API key (sent as ``Authorization: Bearer …``).
    - ``model_name``: Resolved Qwen model name (e.g. ``qwen3.7-max``).
    - ``base_url``: DashScope OpenAI-compatible base URL.
    - ``timeout``: Per-request timeout in seconds.
    - ``max_results_per_call``: Upper bound on returned hits per call.
    - ``transport``: Optional ``httpx`` transport for testing; ``None``
      uses the real network transport in production.

    Security
    --------
    - The API key is sent only in the ``Authorization`` header; it is
      never logged, never placed in the request body, and never appears
      in :class:`WebSearchResult` summary / detail_code.
    - Provider request ids / item ids / answer text / reasoning / usage
      are never exposed on :class:`WebSearchResult`. The
      ``provider_result_ref`` field on each :class:`WebSearchHitView`
      carries the provider-side ``web_search_call`` item id for internal
      correlation only.
    """

    api_key: str = field(repr=False)
    model_name: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout: float = 18.0
    max_results_per_call: int = 5
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> WebSearchResult:
        """Call the DashScope Responses API with the web_search tool.

        See the module docstring for the full outcome mapping.
        """
        # ASK-WEB-R4: inject Host current UTC date into the system prompt
        # so the search engine has freshness context. Server-owned (never
        # provider-supplied); carries no user content.
        host_date = _host_utc_date_iso()
        request_body: dict[str, Any] = {
            "model": self.model_name,
            "input": [
                {
                    "role": "system",
                    "content": (
                        f"You are Ask Claread. Today is {host_date} (UTC). "
                        "Use web_search when the user asks about recent "
                        "events. Prefer newer sources for time-sensitive "
                        "questions; do not claim a fact is confirmed 'as "
                        "of today' when the source lacks a date."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "tools": [{"type": "web_search"}],
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as client:
                async with client.stream(
                    "POST",
                    "/responses",
                    json=request_body,
                ) as response:
                    status_code = response.status_code
                    if status_code == 429:
                        return WebSearchResult(
                            status="unavailable",
                            summary=_SUMMARY_RATE_LIMIT,
                            hits=(),
                            detail_code=_DETAIL_RATE_LIMIT,
                        )
                    if status_code == 503:
                        return WebSearchResult(
                            status="unavailable",
                            summary=_SUMMARY_SERVICE_UNAVAILABLE,
                            hits=(),
                            detail_code=_DETAIL_SERVICE_UNAVAILABLE,
                        )
                    if status_code == 408:
                        return WebSearchResult(
                            status="timeout",
                            summary=_SUMMARY_TIMEOUT,
                            hits=(),
                            detail_code=_DETAIL_TIMEOUT,
                        )
                    if status_code in (400, 422, 500):
                        return WebSearchResult(
                            status="failed",
                            summary=_SUMMARY_FAILED,
                            hits=(),
                            detail_code=_detail_for_http_status(status_code),
                        )
                    if status_code != 200:
                        return WebSearchResult(
                            status="failed",
                            summary=_SUMMARY_HTTP_ERROR,
                            hits=(),
                            detail_code=_DETAIL_HTTP_OTHER,
                        )

                    try:
                        extracted = await _extract_sources_from_sse(response)
                    except _MalformedSseError:
                        return WebSearchResult(
                            status="failed",
                            summary=_SUMMARY_MALFORMED,
                            hits=(),
                            detail_code=_DETAIL_MALFORMED,
                        )
        except httpx.TimeoutException:
            return WebSearchResult(
                status="timeout",
                summary=_SUMMARY_TIMEOUT,
                hits=(),
                detail_code=_DETAIL_TIMEOUT,
            )
        except httpx.HTTPError:
            return WebSearchResult(
                status="failed",
                summary=_SUMMARY_HTTP_ERROR,
                hits=(),
                detail_code=_DETAIL_HTTP_TRANSPORT,
            )
        except Exception:
            return WebSearchResult(
                status="failed",
                summary=_SUMMARY_FAILED,
                hits=(),
                detail_code=_DETAIL_UNEXPECTED,
            )

        return _build_result_from_sources(
            extracted,
            max_results=max_results,
            max_results_per_call=self.max_results_per_call,
        )


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


async def _extract_sources_from_sse(
    response: httpx.Response,
) -> list[tuple[str, str | None]]:
    """Parse the SSE stream and return ``[(url, provider_item_id), …]``.

    Only ``response.output_item.done`` events with
    ``item.type == "web_search_call"`` and ``item.status == "completed"``
    yield sources. Each source must have ``type == "url"`` and a
    non-empty ``url`` string.

    Raises :class:`_MalformedSseError` if any ``data:`` line cannot be
    parsed as a JSON object, or if a ``response.output_item.done``
    event is missing the ``item`` field.
    """
    extracted: list[tuple[str, str | None]] = []
    data_buffer: list[str] = []

    # G3-R1 §IV: the httpx stream context (caller's ``client.stream(...)``)
    # owns the response lifecycle. We iterate ``aiter_bytes()`` directly
    # (the bottom of httpx's async iterator chain) and decode + split
    # lines ourselves. Using ``aiter_lines()`` or ``aiter_text()``
    # introduces nested async generators whose ``aclose()`` does not
    # reliably propagate through the chain on Python 3.13, producing
    # ``RuntimeWarning: coroutine method 'aclose' of 'Response.aiter_bytes'
    # was never awaited``. By using ``aiter_bytes()`` directly we have a
    # single generator with no inner wrappers. Explicit ``aclose()`` in
    # ``finally`` guarantees cleanup on all exit paths (normal, exception,
    # cancellation). Cast to ``AsyncGenerator`` so ``aclose()`` is visible
    # to mypy (``AsyncIterator`` lacks ``aclose`` in stubs; runtime is
    # always an async generator). This eliminates both the ``aclosing()``
    # wrapper's mypy type errors and the nested-generator RuntimeWarning.
    text_gen = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
    try:
        decoder = _TextDecoder()
        line_buffer = ""
        async for chunk in text_gen:
            line_buffer += decoder.decode(chunk)
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line == "":
                    # End of SSE event.
                    if data_buffer:
                        raw = "\n".join(data_buffer)
                        data_buffer.clear()
                        _process_sse_payload(raw, extracted)
                    continue
                if line.startswith("data:"):
                    # Accept "data:" and "data: " (with or without space).
                    value = line[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    data_buffer.append(value)
                # Ignore event:, id:, retry:, and comment (:) lines.
        # Flush decoder's final bytes.
        tail = decoder.flush()
        if tail:
            line_buffer += tail
        # Flush any trailing partial line (no trailing newline).
        if line_buffer:
            line = line_buffer.rstrip("\r")
            if line == "":
                if data_buffer:
                    raw = "\n".join(data_buffer)
                    data_buffer.clear()
                    _process_sse_payload(raw, extracted)
            elif line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                data_buffer.append(value)
    finally:
        await text_gen.aclose()

    # Flush any trailing buffered event (some servers omit the final
    # blank line terminator).
    if data_buffer:
        raw = "\n".join(data_buffer)
        _process_sse_payload(raw, extracted)

    return extracted


def _process_sse_payload(
    raw: str,
    extracted: list[tuple[str, str | None]],
) -> None:
    """Parse one buffered SSE data payload and append any sources.

    Raises :class:`_MalformedSseError` if the payload is not ``[DONE]``
    and cannot be parsed as a JSON object, or if a
    ``response.output_item.done`` event is missing the ``item`` field.
    """
    if not raw or raw == "[DONE]":
        return
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _MalformedSseError from exc
    if not isinstance(parsed, dict):
        raise _MalformedSseError

    if parsed.get("type") != "response.output_item.done":
        return

    item = parsed.get("item")
    if not isinstance(item, dict):
        raise _MalformedSseError
    if item.get("type") != "web_search_call":
        return
    if item.get("status") != "completed":
        return

    item_id_raw = item.get("id")
    item_id: str | None = item_id_raw if isinstance(item_id_raw, str) else None

    action = item.get("action")
    if not isinstance(action, dict):
        return
    raw_sources = action.get("sources")
    if not isinstance(raw_sources, list):
        return
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        if source.get("type") != "url":
            continue
        url = source.get("url")
        if not isinstance(url, str) or not url:
            continue
        extracted.append((url, item_id))


# ---------------------------------------------------------------------------
# Result construction
# ---------------------------------------------------------------------------


def _build_result_from_sources(
    extracted: list[tuple[str, str | None]],
    *,
    max_results: int,
    max_results_per_call: int,
) -> WebSearchResult:
    """Build the final :class:`WebSearchResult` from extracted sources.

    - Canonicalizes each URL via :func:`canonicalize_url`. Failures are
      skipped (per-source fail-closed).
    - Deduplicates by canonical URL.
    - Caps the hit count at ``min(max_results, max_results_per_call)``.
    - Falls back to :func:`display_domain_from_canonical_url` for the
      title (Qwen Responses sources are URL-only).
    - ``provider_result_ref`` carries the parent ``web_search_call``
      item id internally.
    """
    cap = max(0, min(int(max_results), int(max_results_per_call)))
    hits: list[WebSearchHitView] = []
    seen: set[str] = set()

    for url, item_id in extracted:
        try:
            canonical = canonicalize_url(url)
        except ValueError:
            # Fail-closed per-source: skip malformed URL.
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        title = display_domain_from_canonical_url(canonical)
        hits.append(
            WebSearchHitView(
                raw_url=canonical,
                title=title,
                description="",
                provider_result_ref=item_id,
            )
        )
        if len(hits) >= cap:
            break

    if not hits:
        return WebSearchResult(
            status="empty",
            summary=_SUMMARY_EMPTY,
            hits=(),
            detail_code=_DETAIL_EMPTY,
        )

    return WebSearchResult(
        status="ok",
        summary=_SUMMARY_OK,
        hits=tuple(hits),
        detail_code=_DETAIL_OK,
    )


def _detail_for_http_status(status_code: int) -> str:
    """Map an HTTP status code to a fixed detail_code string."""
    if status_code == 400:
        return _DETAIL_HTTP_400
    if status_code == 422:
        return _DETAIL_HTTP_422
    if status_code == 500:
        return _DETAIL_HTTP_500
    return _DETAIL_HTTP_OTHER


def _host_utc_date_iso() -> str:
    """Return the current UTC date as a ``YYYY-MM-DD`` string.

    ASK-WEB-R4: injected into the search-request system prompt so the
    provider has freshness context. Server-owned (never
    provider-supplied); carries no user content.
    """
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


__all__ = ["QwenDashscopeWebSearchBackend"]
