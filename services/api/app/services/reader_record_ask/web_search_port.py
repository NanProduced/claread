"""Provider-neutral Web Search port for Reading Record Ask (G1-b1).

This port is the *only* seam the new agent uses for web search. It is
intentionally isolated from any real provider transport (DashScope /
DeepSeek) — those land later, after their wire probes pass (G2).

G0/G1 contract surface
-----------------------
- :data:`WebSearchPortOutcome` — closed outcome set mirroring
  :data:`WebSearchOutcome` plus ``ok`` for the port seam (the host
  translates ``ok`` to :data:`WebSearchOutcome` ``completed`` /
  ``no_results`` based on the hit count).
- :class:`WebSearchHitView` — provider-neutral hit item. ``raw_url`` is
  the provider-supplied URL; the host routes it through
  :func:`canonicalize_url` before any host-side registration.
  ``provider_result_ref`` is internal-only.
- :class:`WebSearchResult` — port result of one envelope-scoped search.
- :class:`WebSearchBackend` — Protocol implemented by fakes (G1) and
  real adapters (G2+).
- :class:`FakeWebSearchBackend` — scripted fake for unit tests; never
  makes any real network call.

Boundary rules
--------------
- Identity fields (``user_id``, ``tenant_id``, ``turn_id``, …) are
  intentionally NOT accepted by the port. The host owns the envelope;
  the port only sees the query + result cap.
- Provider text (title / description / snippet / raw_url) is
  **untrusted content**. The host re-canonicalizes every URL and
  recomputes ``source_fingerprint`` before storing
  :class:`WebEvidence`.
- ``provider_result_ref`` is internal-only — never on public DTOs, SSE,
  or persistence-replay payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.services.reader_record_ask.web_search_contracts import (
    WEB_DESCRIPTION_MAX_LEN,
    WEB_TITLE_MAX_LEN,
    WEB_URL_MAX_LEN,
)

# ---------------------------------------------------------------------------
# Closed outcome set (port seam)
# ---------------------------------------------------------------------------

# The port seam distinguishes a successful call with results (``ok``)
# from one with no results (``empty``). The host translates these to
# the public :data:`WebSearchOutcome` set: ``ok``→``completed`` (when
# hits exist) or ``no_results`` (when hits are empty); ``unavailable``
# and ``failed`` map 1:1.
WebSearchPortOutcome = Literal[
    "ok",
    "empty",
    "unavailable",
    "failed",
]


# ---------------------------------------------------------------------------
# Provider-neutral hit view
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WebSearchHitView:
    """One provider-neutral web search hit (provider-supplied, untrusted).

    The host re-canonicalizes ``raw_url`` via :func:`canonicalize_url`
    before any host-side registration. Title / description are
    length-bounded by the contracts module so a malicious provider
    cannot overflow the model view or persistence.

    ``provider_result_ref`` is internal-only — never on public DTOs.

    ASK-WEB-R4: ``published_at`` / ``page_age`` are optional
    provider-supplied freshness hints. ``published_at`` is an ISO-8601
    date/datetime string when the provider exposes one; ``page_age`` is
    the raw provider hint (e.g. "2 days ago") when only a relative age
    is available. Both are untrusted provider text — the host never
    treats them as authoritative, only as a ranking hint, and never
    echoes them as confirmed facts to the user.
    """

    raw_url: str = ""
    title: str = ""
    description: str = ""
    # Internal-only provider result id. Must never appear on public
    # DTOs, SSE, or persistence-replay payloads. Carried through so
    # the host can correlate a registered :class:`WebEvidence` with a
    # provider-side diagnostic id when auditing a turn.
    provider_result_ref: str | None = None
    # ASK-WEB-R4: optional provider-supplied freshness hints.
    published_at: str | None = None
    page_age: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_url, str):
            raise TypeError("raw_url must be str")
        if not isinstance(self.title, str):
            raise TypeError("title must be str")
        if not isinstance(self.description, str):
            raise TypeError("description must be str")
        if len(self.raw_url) > WEB_URL_MAX_LEN:
            raise ValueError(
                f"raw_url exceeds max length {WEB_URL_MAX_LEN}"
            )
        if len(self.title) > WEB_TITLE_MAX_LEN:
            raise ValueError(
                f"title exceeds max length {WEB_TITLE_MAX_LEN}"
            )
        if len(self.description) > WEB_DESCRIPTION_MAX_LEN:
            raise ValueError(
                f"description exceeds max length {WEB_DESCRIPTION_MAX_LEN}"
            )
        if (
            self.provider_result_ref is not None
            and not isinstance(self.provider_result_ref, str)
        ):
            raise TypeError("provider_result_ref must be str | None")
        if self.published_at is not None:
            if not isinstance(self.published_at, str):
                raise TypeError("published_at must be str | None")
            if len(self.published_at) > 64:
                raise ValueError("published_at exceeds max length 64")
        if self.page_age is not None:
            if not isinstance(self.page_age, str):
                raise TypeError("page_age must be str | None")
            if len(self.page_age) > 64:
                raise ValueError("page_age exceeds max length 64")


# ---------------------------------------------------------------------------
# Port result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Result of one envelope-scoped web search call.

    ``hits`` is empty for ``ok`` with zero results (callers should
    still surface ``completed`` to the public summary so the UI can
    show "search happened, no sources cited"). ``empty`` means the
    provider explicitly returned no results for the query.

    ``detail_code`` is internal-only; it never appears on public DTOs.
    """

    status: WebSearchPortOutcome
    summary: str
    hits: tuple[WebSearchHitView, ...] = ()
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("WebSearchResult.summary must be a non-empty str")
        if not isinstance(self.hits, tuple):
            # Defensive: callers may pass a list; coerce once.
            object.__setattr__(self, "hits", tuple(self.hits))  # type: ignore[arg-type]
        if self.status in {"unavailable", "failed"} and self.hits:
            raise ValueError(
                f"WebSearchResult status={self.status!r} must not carry hits"
            )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WebSearchBackend(Protocol):
    """Injected port — production wraps a real provider; tests use fakes.

    Contract
    --------
    - ``query`` is bounded by :data:`WEB_QUERY_MAX_LEN`. The caller
      (turn coordinator) is responsible for clamping / rejecting
      over-length queries *before* calling.
    - ``max_results`` is bounded by
      :data:`WEB_MAX_RESULTS_PER_CALL`. The caller clamps.
    - Implementations MUST NOT make any real network call when used as
      a fake (G1). Real adapters (G2+) own their own SSRF / DNS /
      redirect / timeout / size fences — the host does not re-implement
      them.
    - Implementations MUST NOT accept user_id / tenant_id / turn_id /
      record_id / envelope_fingerprint as parameters. The host owns
      identity; the port only sees the query + cap.
    """

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> WebSearchResult: ...


# ---------------------------------------------------------------------------
# Fake backend (scripted; G1 vertical slice)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FakeWebSearchBackend:
    """Scripted web search backend for unit tests (no real network I/O).

    Mirrors :class:`FakeArticleRagSearchPort` semantics:

    - ``outcomes`` is a list of scripted :class:`WebSearchResult`.
      When empty, the fake returns a typed ``unavailable`` result.
    - ``call_count`` / ``last_query`` / ``last_max_results`` expose
      call instrumentation for assertions.
    - Never raises unless the scripted outcome is malformed (which is
      a test-author bug, not a runtime path).

    The fake is the **default** backend for the G1 vertical slice.
    Real provider adapters land in G2+ and replace this fake in
    production wiring.
    """

    outcomes: list[WebSearchResult] = field(default_factory=list)
    call_count: int = 0
    last_query: str | None = None
    last_max_results: int | None = None

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> WebSearchResult:
        self.call_count += 1
        self.last_query = query
        self.last_max_results = max_results
        if not self.outcomes:
            return WebSearchResult(
                status="unavailable",
                summary="Fake web search backend has no scripted outcomes",
                hits=(),
                detail_code="fake_empty_script",
            )
        index = min(self.call_count - 1, len(self.outcomes) - 1)
        return self.outcomes[index]


__all__ = [
    "FakeWebSearchBackend",
    "WebSearchBackend",
    "WebSearchHitView",
    "WebSearchPortOutcome",
    "WebSearchResult",
]
