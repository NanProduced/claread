"""G2-DeepSeek: DeepSeek Anthropic-compat adapter STUB (test-only).

This is a STUB adapter that implements the
:class:`WebSearchBackend` Protocol using only fixture data. It is NOT
wired into production and makes NO real network calls. Production
wiring lands in G3 after a real wire smoke passes the provenance gate.

Scope
-----
- Constructor accepts the frozen
  :class:`DeepseekAnthropicCompatWireFixture` plus an optional scenario
  name (defaults to the happy path).
- ``search_web`` returns a :class:`WebSearchResult` built strictly from
  the fixture data. URLs are re-canonicalized through the Host
  :func:`canonicalize_url` so provider-supplied text is never trusted
  raw.
- The stub models the five closed scenario outcomes
  (``ok``/``empty``/``unavailable``/``failed`` plus DeepSeek-specific
  ``partial`` citation reliability mode) so tests can exercise the
  full outcome mapping without a real provider.

Boundary rules
--------------
- Does NOT import the real ``anthropic`` SDK.
- Does NOT make HTTP calls.
- Does NOT accept user_id / tenant_id / turn_id (identity is host-owned).
- Does NOT fabricate URLs/titles/snippets. Unlike Qwen, DeepSeek DOES
  return ``title`` per source (per the Anthropic reference shape), so
  the stub preserves it after re-canonicalization. ``description``
  stays empty — the Host MUST NOT expose ``encrypted_content`` on
  public DTOs (it is internal-only opaque text per the fixture).
- ``citations_behavior="partial"`` and ``citations_behavior="ignored"``
  both surface as ``failed`` / ``unavailable`` per the strict mapping
  locked in the fixture. The stub never fabricates missing URLs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.reader_record_ask.web_search_contracts import (
    canonicalize_url,
    display_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchHitView,
    WebSearchResult,
)
from tests.fixtures.web_search.deepseek_anthropic_compat_wire import (
    DEEPSEEK_ANTHROPIC_COMPAT_WIRE,
    DeepseekAnthropicCompatWireFixture,
)


def _extract_web_search_results(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract ``web_search_result`` entries from
    ``web_search_tool_result`` content blocks.

    Only ``content_block_start`` events whose ``content_block.type`` is
    ``web_search_tool_result`` carry source entries. Each entry's
    ``url`` and ``title`` are extracted; ``encrypted_content`` is
    deliberately dropped (Host treats it as untrusted opaque text and
    does NOT expose it on public DTOs).
    """
    results: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "content_block_start":
            continue
        block = event.get("content_block") or {}
        if block.get("type") != "web_search_tool_result":
            continue
        for entry in block.get("content") or []:
            if entry.get("type") != "web_search_result":
                continue
            if not entry.get("url"):
                continue
            results.append(entry)
    return results


def _hit_view_from_deepseek_result(
    result: dict[str, Any],
) -> WebSearchHitView | None:
    """Build a :class:`WebSearchHitView` from a DeepSeek
    ``web_search_result`` entry.

    Returns ``None`` when the URL fails canonicalization (defensive:
    the Host must never register a malformed provider URL).

    DeepSeek DOES return ``title`` per source (per the Anthropic
    reference shape). The stub preserves the title after
    re-canonicalization; if the title is missing or empty, it falls
    back to the display domain (mirrors the Qwen stub behaviour so the
    Web evidence registry always has a non-empty title).

    ``encrypted_content`` is dropped (internal-only opaque text).
    """
    raw_url = result.get("url", "")
    try:
        canonical = canonicalize_url(raw_url)
    except ValueError:
        # Provider URL failed canonicalization — drop the source
        # rather than registering a malformed entry. This is the
        # fail-closed path documented in the contracts module.
        return None
    title = str(result.get("title") or "").strip()
    if not title:
        # DeepSeek usually returns a title, but fall back to the
        # display domain if missing (mirrors the Qwen stub behaviour).
        title = display_domain_from_canonical_url(canonical)
    return WebSearchHitView(
        raw_url=canonical,
        title=title,
        description="",
        provider_result_ref=None,
    )


@dataclass(slots=True)
class DeepseekAnthropicCompatAdapterStub:
    """Test-only stub implementing :class:`WebSearchBackend`.

    The stub is parameterized by the frozen fixture plus a scenario
    name. It does NOT call any real provider. The scenarios cover the
    closed outcome set plus DeepSeek-specific citation reliability
    modes:

    - ``"happy"``         → ``status="ok"`` with fixture hits.
    - ``"no_results"``    → ``status="empty"`` (search ran, 0 hits).
    - ``"unavailable"``   → ``status="unavailable"`` (HTTP 429/503/402
      OR ``citations_behavior="ignored"`` — no web_search_tool_result
      block returned).
    - ``"failed"``        → ``status="failed"`` (HTTP 400/422/500).
    - ``"partial"``       → ``status="failed"`` — strict mapping per
      fixture: ``citations_behavior="partial"`` (some URLs missing)
      MUST NOT be silently treated as a successful search. The Host
      refuses to fabricate the missing URLs and surfaces ``failed``.
    """

    fixture: DeepseekAnthropicCompatWireFixture = field(
        default_factory=lambda: DEEPSEEK_ANTHROPIC_COMPAT_WIRE
    )
    scenario: str = "happy"
    call_count: int = 0
    last_query: str | None = None
    last_max_results: int | None = None

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> WebSearchResult:
        """Return the scripted :class:`WebSearchResult` for this stub.

        Honours the WebSearchBackend Protocol contract:

        - ``query`` is taken as-is (caller is responsible for clamping
          to :data:`WEB_QUERY_MAX_LEN` before calling).
        - ``max_results`` is honoured: hits are truncated to this cap.
        - ``unavailable`` / ``failed`` never carry hits (enforced by
          :class:`WebSearchResult` ``__post_init__``).
        """
        self.call_count += 1
        self.last_query = query
        self.last_max_results = max_results

        if self.scenario == "happy":
            results = _extract_web_search_results(
                self.fixture.expected_response_events
            )
            hits: list[WebSearchHitView] = []
            for result in results:
                hit = _hit_view_from_deepseek_result(result)
                if hit is not None:
                    hits.append(hit)
            # Honour max_results cap.
            hits = hits[: max(0, max_results)]
            if not hits:
                return WebSearchResult(
                    status="empty",
                    summary="DeepSeek fixture happy-path produced 0 canonical hits",
                    hits=(),
                    detail_code="deepseek_happy_empty_after_canonicalize",
                )
            return WebSearchResult(
                status="ok",
                summary="DeepSeek fixture happy-path completed",
                hits=tuple(hits),
                detail_code="deepseek_happy_completed",
            )

        if self.scenario == "no_results":
            scenario = self.fixture.error_scenarios["no_results"]
            return WebSearchResult(
                status="empty",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="deepseek_no_results",
            )

        if self.scenario == "unavailable":
            scenario = self.fixture.error_scenarios["unavailable"]
            return WebSearchResult(
                status="unavailable",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="deepseek_unavailable_http_429_503_402",
            )

        if self.scenario == "failed":
            scenario = self.fixture.error_scenarios["failed"]
            return WebSearchResult(
                status="failed",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="deepseek_failed_http_400_422_500",
            )

        if self.scenario == "partial":
            # Strict mapping per fixture: refuse to fabricate missing
            # URLs. The Host MUST NOT silently treat a partial result
            # as a successful search.
            scenario = self.fixture.error_scenarios["partial"]
            return WebSearchResult(
                status="failed",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="deepseek_partial_citations_refused",
            )

        if self.scenario == "ignored":
            # citations_behavior="ignored": no web_search_tool_result
            # block at all. Per fixture, this maps to ``unavailable``.
            scenario = self.fixture.error_scenarios["ignored"]
            return WebSearchResult(
                status="unavailable",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="deepseek_ignored_no_tool_result_block",
            )

        # Unknown scenario — fail closed.
        return WebSearchResult(
            status="unavailable",
            summary=f"DeepSeek fixture scenario {self.scenario!r} not recognized",
            hits=(),
            detail_code="deepseek_unknown_scenario",
        )


__all__ = [
    "DeepseekAnthropicCompatAdapterStub",
]
