"""G2-Qwen: Qwen dashscope_responses adapter STUB (test-only).

This is a STUB adapter that implements the
:class:`WebSearchBackend` Protocol using only fixture data. It is NOT
wired into production and makes NO real network calls. Production
wiring lands in G3 after a real wire smoke passes the provenance gate.

Scope
-----
- Constructor accepts the frozen
  :class:`QwenDashscopeResponsesWireFixture` plus an optional scenario
  name (defaults to the happy path).
- ``search_web`` returns a :class:`WebSearchResult` built strictly from
  the fixture data. URLs are re-canonicalized through the Host
  :func:`canonicalize_url` so provider-supplied text is never trusted
  raw.
- The stub models the four closed outcome scenarios
  (``ok``/``empty``/``unavailable``/``failed``) so tests can exercise
  the full outcome mapping without a real provider.

Boundary rules
--------------
- Does NOT import the real ``dashscope`` SDK.
- Does NOT make HTTP calls.
- Does NOT accept user_id / tenant_id / turn_id (identity is host-owned).
- Does NOT fabricate URLs/titles/snippets — Qwen Responses returns
  URL-only sources; ``title`` falls back to the display domain derived
  by the Host (mirrors the Web evidence registry pattern).
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
from tests.fixtures.web_search.qwen_dashscope_responses_wire import (
    QWEN_DASHSCOPE_RESPONSES_WIRE,
    QwenDashscopeResponsesWireFixture,
)


def _extract_completed_sources(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract ``action.sources`` from completed ``web_search_call`` items.

    Only the ``response.output_item.done`` events with
    ``item.type == "web_search_call"`` and ``item.status == "completed"``
    yield sources. This mirrors the Host rule: only completed search
    calls may mint web evidence.
    """
    sources: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "response.output_item.done":
            continue
        item = event.get("item") or {}
        if (
            item.get("type") == "web_search_call"
            and item.get("status") == "completed"
        ):
            action = item.get("action") or {}
            for source in action.get("sources") or []:
                if source.get("type") == "url" and source.get("url"):
                    sources.append(source)
    return sources


def _hit_view_from_qwen_source(
    source: dict[str, Any],
) -> WebSearchHitView | None:
    """Build a :class:`WebSearchHitView` from a Qwen URL source.

    Returns ``None`` when the URL fails canonicalization (defensive:
    the Host must never register a malformed provider URL).

    Qwen Responses sources are URL-only. The Host falls back to the
    display domain for ``title``; ``description`` stays empty (the
    Host MUST NOT fabricate a snippet).
    """
    raw_url = source.get("url", "")
    try:
        canonical = canonicalize_url(raw_url)
    except ValueError:
        # Provider URL failed canonicalization — drop the source
        # rather than registering a malformed entry. This is the
        # fail-closed path documented in the contracts module.
        return None
    title = display_domain_from_canonical_url(canonical)
    return WebSearchHitView(
        raw_url=canonical,
        title=title,
        description="",
        provider_result_ref=None,
    )


@dataclass(slots=True)
class QwenDashscopeResponsesAdapterStub:
    """Test-only stub implementing :class:`WebSearchBackend`.

    The stub is parameterized by the frozen fixture plus a scenario
    name. It does NOT call any real provider. The four scenarios cover
    the closed outcome set:

    - ``"happy"``         → ``status="ok"`` with fixture hits.
    - ``"no_results"``    → ``status="empty"`` (search ran, 0 hits).
    - ``"unavailable"``   → ``status="unavailable"`` (silent skip /
      429). No hits, no fabricated sources.
    - ``"failed"``        → ``status="failed"`` (HTTP 400/422/500).
      No hits.
    """

    fixture: QwenDashscopeResponsesWireFixture = field(
        default_factory=lambda: QWEN_DASHSCOPE_RESPONSES_WIRE
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
            sources = _extract_completed_sources(
                self.fixture.expected_response_events
            )
            hits: list[WebSearchHitView] = []
            for source in sources:
                hit = _hit_view_from_qwen_source(source)
                if hit is not None:
                    hits.append(hit)
            # Honour max_results cap.
            hits = hits[: max(0, max_results)]
            if not hits:
                return WebSearchResult(
                    status="empty",
                    summary="Qwen fixture happy-path produced 0 canonical hits",
                    hits=(),
                    detail_code="qwen_happy_empty_after_canonicalize",
                )
            return WebSearchResult(
                status="ok",
                summary="Qwen fixture happy-path completed",
                hits=tuple(hits),
                detail_code="qwen_happy_completed",
            )

        if self.scenario == "no_results":
            scenario = self.fixture.error_scenarios["no_results"]
            return WebSearchResult(
                status="empty",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="qwen_no_results",
            )

        if self.scenario == "unavailable":
            scenario = self.fixture.error_scenarios["unavailable"]
            return WebSearchResult(
                status="unavailable",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="qwen_unavailable_silent_skip",
            )

        if self.scenario == "failed":
            scenario = self.fixture.error_scenarios["failed"]
            return WebSearchResult(
                status="failed",
                summary=str(scenario["description"]),
                hits=(),
                detail_code="qwen_failed_http_400",
            )

        # Unknown scenario — fail closed.
        return WebSearchResult(
            status="unavailable",
            summary=f"Qwen fixture scenario {self.scenario!r} not recognized",
            hits=(),
            detail_code="qwen_unknown_scenario",
        )


__all__ = [
    "QwenDashscopeResponsesAdapterStub",
]
