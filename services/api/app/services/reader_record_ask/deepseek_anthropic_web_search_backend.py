"""G3-DeepSeek: Real DeepSeek Anthropic-compat Web Search adapter.

This adapter implements the :class:`WebSearchBackend` Protocol by calling
the DeepSeek Anthropic-compatible Messages API
(``https://api.deepseek.com/anthropic``) with the server-side
``web_search_20250305`` tool. It is the production replacement for the
G2 fixture stub.

Boundary rules
--------------
- Calls the real DeepSeek Anthropic-compat endpoint via ``httpx``.
- Uses the provider-native ``web_search_20250305`` server tool only.
- Extracts URLs **only** from ``content_block_start`` events whose
  ``content_block.type == "web_search_tool_result"`` and then from
  ``content`` entries with ``type == "web_search_result"``.
- **Never** infers URLs from ``text`` / ``thinking`` blocks. If no
  ``web_search_tool_result`` block is returned, the search is
  ``unavailable`` (citations_behavior="ignored").
- **Never** fabricates URLs when an entry is missing ``url``
  (citations_behavior="partial"). The whole call returns ``failed``.
- ``encrypted_content`` is dropped and never appears on any public
  surface (DTO, SSE, log, cold history).
- Canonicalizes, deduplicates, and caps hits at ``max_results``.
- Preserves the provider-supplied ``title``; falls back to
  :func:`display_domain_from_canonical_url` when missing.
- **Never** exposes answer text, reasoning, request id, usage, query,
  or raw payload in the public :class:`WebSearchResult`.
- ``provider_result_ref`` carries the provider-side ``tool_use_id``
  for internal correlation only — it must never appear in public DTOs,
  SSE, logs, or cold history.

Outcome mapping
---------------
- HTTP 200 + ``web_search_tool_result`` block + ≥1 canonical hit →
  ``status="ok"``
- HTTP 200 + ``web_search_tool_result`` block with ``content == []`` →
  ``status="empty"``
- HTTP 200 + ``web_search_tool_result`` block with any entry missing
  ``url`` → ``status="failed"`` (citations_behavior="partial")
- HTTP 200 + no ``web_search_tool_result`` block →
  ``status="unavailable"`` (citations_behavior="ignored")
- HTTP 429 / 503 / 402 → ``status="unavailable"``
- HTTP 408 / ``httpx.TimeoutException`` → ``status="unavailable"``
  (detail_code ``deepseek_timeout``)
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
# These NEVER include provider text, API key, URL, request id,
# encrypted_content, or raw payload. They are the only strings that may
# appear on the public WebSearchResult.summary / detail_code surface.

_SUMMARY_OK = "DeepSeek Anthropic web search completed"
_SUMMARY_EMPTY = "DeepSeek Anthropic web search completed with no sources"
_SUMMARY_PARTIAL = (
    "DeepSeek Anthropic web search returned partial citations; "
    "refusing to fabricate missing URLs"
)
_SUMMARY_IGNORED = (
    "DeepSeek Anthropic web search returned no web_search_tool_result block"
)
_SUMMARY_RATE_LIMIT = "DeepSeek Anthropic web search rate-limited; try again later"
_SUMMARY_SERVICE_UNAVAILABLE = (
    "DeepSeek Anthropic web search temporarily unavailable"
)
_SUMMARY_TIMEOUT = "DeepSeek Anthropic web search timed out"
_SUMMARY_FAILED = "DeepSeek Anthropic web search failed"
_SUMMARY_MALFORMED = "DeepSeek Anthropic web search returned malformed data"
_SUMMARY_HTTP_ERROR = "DeepSeek Anthropic web search HTTP error"

_DETAIL_OK = "deepseek_completed"
_DETAIL_EMPTY = "deepseek_no_canonical_hits"
_DETAIL_PARTIAL = "deepseek_partial_citations_refused"
_DETAIL_IGNORED = "deepseek_citations_ignored"
_DETAIL_RATE_LIMIT = "deepseek_rate_limit"
_DETAIL_SERVICE_UNAVAILABLE = "deepseek_service_unavailable"
_DETAIL_TIMEOUT = "deepseek_timeout"
_DETAIL_MALFORMED = "deepseek_malformed_response"
_DETAIL_HTTP_400 = "deepseek_http_400"
_DETAIL_HTTP_422 = "deepseek_http_422"
_DETAIL_HTTP_500 = "deepseek_http_500"
_DETAIL_HTTP_OTHER = "deepseek_http_error"
_DETAIL_HTTP_TRANSPORT = "deepseek_http_transport_error"
_DETAIL_UNEXPECTED = "deepseek_unexpected_error"


# ---------------------------------------------------------------------------
# Internal exception for malformed SSE payloads
# ---------------------------------------------------------------------------


class _MalformedSseError(Exception):
    """Raised internally when an SSE data line cannot be parsed as a JSON
    object, or when a ``content_block_start`` event is missing the
    ``content_block`` field.

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
        try:
            text = self._buffer.decode("utf-8")
            self._buffer = b""
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                text = self._buffer[: exc.start].decode("utf-8")
                self._buffer = self._buffer[exc.start :]
            else:
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
# Extracted source representation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ExtractedSource:
    """One extracted ``web_search_result`` entry.

    ``url`` is the provider-supplied URL (pre-canonicalization).
    ``title`` is the provider-supplied title (may be empty).
    ``tool_use_id`` is the parent ``web_search_tool_result`` block's
    ``tool_use_id`` — carried internally as ``provider_result_ref``
    and never exposed on public DTOs.
    ``page_age`` is the optional provider-supplied freshness hint
    (e.g. "2 days ago"). Untrusted provider text — never authoritative.
    """

    url: str
    title: str
    tool_use_id: str | None
    page_age: str | None = None


@dataclass(slots=True)
class _ExtractionOutcome:
    """Outcome of parsing the SSE stream.

    - ``sources``: extracted sources (may be empty).
    - ``saw_tool_result_block``: True if at least one
      ``web_search_tool_result`` block was observed.
    - ``saw_partial_entry``: True if any ``web_search_result`` entry
      was missing ``url`` (citations_behavior="partial").
    """

    sources: list[_ExtractedSource]
    saw_tool_result_block: bool
    saw_partial_entry: bool


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DeepseekAnthropicWebSearchBackend:
    """Production DeepSeek Anthropic-compat Web Search adapter.

    Implements :class:`WebSearchBackend` by calling the DeepSeek
    Anthropic Messages API (``POST {base_url}/v1/messages``) with the
    server-side ``web_search_20250305`` tool and parsing the SSE
    response stream.

    Construction
    ------------
    - ``api_key``: DeepSeek API key (sent as ``x-api-key`` header).
    - ``model_name``: Resolved DeepSeek model name (e.g.
      ``deepseek-chat``).
    - ``base_url``: DeepSeek Anthropic-compat base URL
      (``https://api.deepseek.com/anthropic``).
    - ``timeout``: Per-request timeout in seconds.
    - ``max_results_per_call``: Upper bound on returned hits per call.
    - ``transport``: Optional ``httpx`` transport for testing; ``None``
      uses the real network transport in production.

    Security
    --------
    - The API key is sent only in the ``x-api-key`` header; it is
      never logged, never placed in the request body, and never appears
      in :class:`WebSearchResult` summary / detail_code.
    - ``encrypted_content`` is dropped during extraction and never
      appears on any public surface.
    - Provider request ids / message ids / answer text / reasoning /
      usage are never exposed on :class:`WebSearchResult`. The
      ``provider_result_ref`` field on each :class:`WebSearchHitView`
      carries the provider-side ``tool_use_id`` for internal
      correlation only.
    """

    api_key: str = field(repr=False)
    model_name: str = ""
    base_url: str = "https://api.deepseek.com/anthropic"
    timeout: float = 30.0
    max_results_per_call: int = 3
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> WebSearchResult:
        """Call the DeepSeek Anthropic Messages API with web_search tool.

        See the module docstring for the full outcome mapping.
        """
        # ASK-WEB-R4: inject Host current UTC date into the system prompt
        # so the search engine has freshness context. The date is
        # server-owned (never provider-supplied) and carries no user
        # content. Format: YYYY-MM-DD.
        host_date = _host_utc_date_iso()
        request_body: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": 1024,
            "system": (
                f"You are Ask Claread. Today is {host_date} (UTC). "
                "Use web_search when the user asks about recent events. "
                "Prefer newer sources for time-sensitive questions; do "
                "not claim a fact is confirmed 'as of today' when the "
                "source lacks a date."
            ),
            "messages": [{"role": "user", "content": query}],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 1,
                }
            ],
            "thinking": {"type": "enabled"},
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as client:
                async with client.stream(
                    "POST",
                    "/v1/messages",
                    json=request_body,
                ) as response:
                    status_code = response.status_code
                    if status_code in (429, 503, 402):
                        return WebSearchResult(
                            status="unavailable",
                            summary=(
                                _SUMMARY_RATE_LIMIT
                                if status_code == 429
                                else _SUMMARY_SERVICE_UNAVAILABLE
                            ),
                            hits=(),
                            detail_code=(
                                _DETAIL_RATE_LIMIT
                                if status_code == 429
                                else _DETAIL_SERVICE_UNAVAILABLE
                            ),
                        )
                    if status_code == 408:
                        return WebSearchResult(
                            status="unavailable",
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
                        outcome = await _extract_sources_from_sse(response)
                    except _MalformedSseError:
                        return WebSearchResult(
                            status="failed",
                            summary=_SUMMARY_MALFORMED,
                            hits=(),
                            detail_code=_DETAIL_MALFORMED,
                        )
        except httpx.TimeoutException:
            return WebSearchResult(
                status="unavailable",
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

        return _build_result_from_outcome(
            outcome,
            max_results=max_results,
            max_results_per_call=self.max_results_per_call,
        )


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


async def _extract_sources_from_sse(
    response: httpx.Response,
) -> _ExtractionOutcome:
    """Parse the SSE stream and return one :class:`_ExtractionOutcome`.

    Only ``content_block_start`` events whose ``content_block.type`` is
    ``web_search_tool_result`` carry source entries. Each entry's
    ``url`` and ``title`` are extracted; ``encrypted_content`` is
    deliberately dropped.

    Raises :class:`_MalformedSseError` if any ``data:`` line cannot be
    parsed as a JSON object, if a ``content_block_start`` event is
    missing the ``content_block`` field, or if the response body is
    non-empty but contains zero ``data:`` lines (clearly not a valid
    SSE stream — a real DeepSeek response always emits at least
    ``message_start`` / ``content_block_start`` events).
    """
    sources: list[_ExtractedSource] = []
    saw_tool_result_block = False
    saw_partial_entry = False
    data_buffer: list[str] = []
    saw_any_line = False
    saw_any_data_line = False

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

    def _handle_line(line: str) -> None:
        nonlocal saw_any_line, saw_any_data_line
        nonlocal saw_tool_result_block, saw_partial_entry
        saw_any_line = True
        if line == "":
            # End of SSE event.
            if data_buffer:
                raw = "\n".join(data_buffer)
                data_buffer.clear()
                block_result = _process_sse_payload(raw)
                if block_result is not None:
                    saw_tool_result_block = True
                    for entry in block_result.entries:
                        if entry.url == "":
                            # Entry missing URL → partial citation.
                            saw_partial_entry = True
                        else:
                            sources.append(
                                _ExtractedSource(
                                    url=entry.url,
                                    title=entry.title,
                                    tool_use_id=block_result.tool_use_id,
                                    page_age=entry.page_age,
                                )
                            )
            return
        if line.startswith("data:"):
            saw_any_data_line = True
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_buffer.append(value)
        # Ignore event:, id:, retry:, and comment (:) lines.

    text_gen = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
    try:
        decoder = _TextDecoder()
        line_buffer = ""
        async for chunk in text_gen:
            line_buffer += decoder.decode(chunk)
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                _handle_line(line.rstrip("\r"))
        # Flush decoder's final bytes.
        tail = decoder.flush()
        if tail:
            line_buffer += tail
        # Flush any trailing partial line (no trailing newline).
        if line_buffer:
            _handle_line(line_buffer.rstrip("\r"))
    finally:
        await text_gen.aclose()

    # Flush any trailing buffered event.
    if data_buffer:
        raw = "\n".join(data_buffer)
        block_result = _process_sse_payload(raw)
        if block_result is not None:
            saw_tool_result_block = True
            for entry in block_result.entries:
                if entry.url == "":
                    saw_partial_entry = True
                else:
                    sources.append(
                        _ExtractedSource(
                            url=entry.url,
                            title=entry.title,
                            tool_use_id=block_result.tool_use_id,
                            page_age=entry.page_age,
                        )
                    )

    # Non-empty body but zero ``data:`` lines → not a valid SSE stream.
    # A real DeepSeek response always emits at least ``message_start`` /
    # ``content_block_start`` events. Plain-text bodies (e.g. provider
    # error pages, HTML 500 pages) must be treated as malformed, not as
    # "no web_search_tool_result block" (which would map to
    # ``unavailable`` / citations_ignored).
    if saw_any_line and not saw_any_data_line:
        raise _MalformedSseError(
            "response body had content but zero SSE data lines"
        )

    return _ExtractionOutcome(
        sources=sources,
        saw_tool_result_block=saw_tool_result_block,
        saw_partial_entry=saw_partial_entry,
    )


@dataclass(slots=True)
class _BlockResult:
    """Parsed result of one ``web_search_tool_result`` content block."""

    tool_use_id: str | None
    entries: list[_BlockEntry]


@dataclass(slots=True)
class _BlockEntry:
    """One entry inside a ``web_search_tool_result`` content block.

    ``url`` is the empty string when the entry was missing a URL
    (partial citation). ``title`` is the provider-supplied title
    (may be empty). ``page_age`` is the optional provider-supplied
    freshness hint (untrusted).
    """

    url: str
    title: str
    page_age: str | None = None


def _process_sse_payload(raw: str) -> _BlockResult | None:
    """Parse one buffered SSE data payload.

    Returns a :class:`_BlockResult` if the payload is a
    ``content_block_start`` event whose ``content_block.type`` is
    ``web_search_tool_result``; otherwise returns ``None``.

    Raises :class:`_MalformedSseError` if the payload is not ``[DONE]``
    and cannot be parsed as a JSON object, or if a
    ``content_block_start`` event is missing the ``content_block``
    field.
    """
    if not raw or raw == "[DONE]":
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _MalformedSseError from exc
    if not isinstance(parsed, dict):
        raise _MalformedSseError

    if parsed.get("type") != "content_block_start":
        return None

    block = parsed.get("content_block")
    if not isinstance(block, dict):
        raise _MalformedSseError
    if block.get("type") != "web_search_tool_result":
        return None

    tool_use_id_raw = block.get("tool_use_id")
    tool_use_id: str | None = (
        tool_use_id_raw if isinstance(tool_use_id_raw, str) else None
    )

    entries: list[_BlockEntry] = []
    raw_content = block.get("content")
    if isinstance(raw_content, list):
        for entry in raw_content:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "web_search_result":
                continue
            url_raw = entry.get("url")
            url: str = url_raw if isinstance(url_raw, str) else ""
            title_raw = entry.get("title")
            title: str = (
                title_raw if isinstance(title_raw, str) else ""
            )
            # ASK-WEB-R4: extract optional provider-supplied page_age
            # freshness hint. DeepSeek Anthropic-compat may surface a
            # ``page_age`` string (e.g. "2 days ago") on
            # ``web_search_result`` entries. Untrusted provider text —
            # length-bounded to 64 chars to match the port contract.
            page_age_raw = entry.get("page_age")
            page_age: str | None = (
                page_age_raw[:64]
                if isinstance(page_age_raw, str) and page_age_raw
                else None
            )
            # encrypted_content is deliberately dropped here and never
            # stored on _BlockEntry — it must not appear on any public
            # surface.
            entries.append(
                _BlockEntry(url=url, title=title, page_age=page_age)
            )

    return _BlockResult(tool_use_id=tool_use_id, entries=entries)


# ---------------------------------------------------------------------------
# Result construction
# ---------------------------------------------------------------------------


def _build_result_from_outcome(
    outcome: _ExtractionOutcome,
    *,
    max_results: int,
    max_results_per_call: int,
) -> WebSearchResult:
    """Build the final :class:`WebSearchResult` from the extraction outcome.

    - If no ``web_search_tool_result`` block was observed →
      ``status="unavailable"`` (citations_behavior="ignored").
    - If any entry was missing ``url`` → ``status="failed"``
      (citations_behavior="partial"; never fabricate).
    - Otherwise canonicalize, deduplicate, cap, and return ``ok`` or
      ``empty``.
    """
    if not outcome.saw_tool_result_block:
        return WebSearchResult(
            status="unavailable",
            summary=_SUMMARY_IGNORED,
            hits=(),
            detail_code=_DETAIL_IGNORED,
        )

    if outcome.saw_partial_entry:
        return WebSearchResult(
            status="failed",
            summary=_SUMMARY_PARTIAL,
            hits=(),
            detail_code=_DETAIL_PARTIAL,
        )

    cap = max(0, min(int(max_results), int(max_results_per_call)))
    hits: list[WebSearchHitView] = []
    seen: set[str] = set()

    for source in outcome.sources:
        try:
            canonical = canonicalize_url(source.url)
        except ValueError:
            # Fail-closed per-source: skip malformed URL.
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        title = source.title.strip()
        if not title:
            title = display_domain_from_canonical_url(canonical)
        hits.append(
            WebSearchHitView(
                raw_url=canonical,
                title=title,
                description="",
                provider_result_ref=source.tool_use_id,
                page_age=source.page_age,
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


__all__ = ["DeepseekAnthropicWebSearchBackend"]
