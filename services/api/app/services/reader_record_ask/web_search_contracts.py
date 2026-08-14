"""Provider-neutral Web Search contracts for Reading Record Ask (G0).

Frozen contracts
----------------
This module freezes the **provider-neutral Host contracts** described in
the Ask Claread Web Search architecture brief (§4 + §5):

- :data:`WebSearchMode` — user-visible request mode (``disabled`` |
  ``allowed``). ``allowed`` only grants turn capability; it never forces
  a search.
- :data:`WebSearchOutcome` — closed outcome set used by both the
  fake-provider vertical slice and the future real adapters.
- :class:`ResolvedWebSearchCapability` — server-owned execution truth
  for one turn. Whether to call is the agent's decision, not a host
  intent classification.
- :class:`WebEvidence` — server-owned internal handle for one web
  source. Carries ``source_fingerprint`` so finalizer / provenance can
  re-confirm identity without re-trusting provider text.
- :class:`PublicWebSearchSummary` — turn-level outcome summary carried
  on the completed DTO. ``null`` means "search not invoked this turn".
- :func:`canonicalize_url` — single canonical-URL algorithm. Only
  ``http`` / ``https``, no credentials, no fragment, no
  ``file`` / ``data`` / ``javascript`` / ``mailto`` / ``tel`` schemes.

Scope boundary
--------------
This slice does **not**:

- enter the agent loop / production stream;
- call real LLMs / search providers;
- mint provider result refs or fabricate URLs;
- accept provider-supplied identity (user_id, tenant_id, turn id, …) —
  identity is taken from the server envelope.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from datetime import date as _date
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from tldextract import TLDExtract

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# User-visible request mode. ``allowed`` only grants turn capability.
WebSearchMode = Literal["disabled", "allowed"]

# Closed outcome set. ``cancelled`` is reserved for hot-UI freeze
# (caller can choose to fold into ``unavailable`` for persistence).
WebSearchOutcome = Literal[
    "completed",
    "no_results",
    "unavailable",
    "failed",
    "timeout",
]

# Provider-neutral execution truth. ``provider_native`` means the model
# decides and executes web search inside a single provider call
# (G2 — not yet wired). ``host_function`` means the agent
# calls the host-owned ``search_web`` function tool (G1 vertical slice).
WebSearchExecutionMode = Literal["provider_native", "host_function"]
WebSearchDecisionMode = Literal["agent_auto"]
WebSearchProtocol = Literal[
    # Fake-provider vertical slice. Real provider transports are added
    # only after their wire probe passes (G2-Qwen / G2-DeepSeek).
    "fake",
    # Reserved for future adapters; never enabled in G0/G1.
    "dashscope_responses",
    "deepseek_anthropic",
]

# Conservative hard caps. Provider text is untrusted content, so we
# bound every field that may flow into the model view or persistence.
WEB_URL_MAX_LEN: int = 2_048
WEB_TITLE_MAX_LEN: int = 512
WEB_DESCRIPTION_MAX_LEN: int = 1_024
WEB_QUERY_MAX_LEN: int = 1_000
WEB_MAX_RESULTS_PER_CALL: int = 5
WEB_MAX_CALLS_PER_TURN: int = 2
# Frozen latency budget. The host applies the remaining turn deadline to
# every call and providers receive the smaller of these two caps.
WEB_SEARCH_TURN_DEADLINE_SECONDS: float = 25.0
WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS: float = 18.0
# Source fingerprint length (SHA-256 hex).
WEB_SOURCE_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Allowed URL schemes. Anything else is rejected before canonicalization.
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Forbidden host patterns (case-insensitive). These hosts can never be
# registered as web evidence. The list is intentionally narrow —
# SSRF / DNS / redirect / size / timeout fences are the caller's job
# (the brief explicitly forbids server-side URL fetching without those
# fences in place).
_FORBIDDEN_HOSTS_LOWER: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
    }
)

# Deliberately disable remote suffix-list URLs and on-disk caching. The
# dependency ships a PSL snapshot, so host source-diversity decisions remain
# deterministic and never cause runtime network I/O.
_REGISTRABLE_DOMAIN_EXTRACTOR = TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    include_psl_private_domains=True,
)


# ---------------------------------------------------------------------------
# Canonical URL
# ---------------------------------------------------------------------------


def canonicalize_url(raw_url: str) -> str:
    """Return the canonical URL form, or raise ``ValueError``.

    Rules
    -----
    - Only ``http`` / ``https`` schemes are accepted.
    - Credentials (``user:pass@``) are rejected.
    - Fragment is dropped (RFC 3986: fragment is not part of the
      resource identity).
    - Host is lowercased; default port is dropped.
    - Empty path becomes ``/``.
    - Query is preserved (sorted keys are NOT applied — sorting could
      mask provider intent for pagination tokens). Callers that want
      deduplication should hash :func:`canonicalize_url` output.
    - Length is bounded by :data:`WEB_URL_MAX_LEN`.

    The returned value is the single canonical form used by
    :class:`WebEvidence`, :class:`PublicCitation` (in ``finalizer``),
    and the web evidence registry. Provider-supplied URLs are *always*
    routed through this function before any host-side registration.
    """
    if not isinstance(raw_url, str):
        raise ValueError("url must be a string")
    stripped = raw_url.strip()
    if not stripped:
        raise ValueError("url must be non-empty")
    if len(stripped) > WEB_URL_MAX_LEN:
        raise ValueError(
            f"url exceeds max length {WEB_URL_MAX_LEN}"
        )

    parts = urlsplit(stripped)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"url scheme {parts.scheme!r} is not allowed; "
            "only http/https are accepted"
        )

    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not carry credentials")

    host = parts.hostname or ""
    host_lower = host.lower()
    if not host_lower:
        raise ValueError("url must carry a non-empty host")
    if host_lower in _FORBIDDEN_HOSTS_LOWER:
        raise ValueError(f"host {host_lower!r} is forbidden as a web source")

    # Drop default port; preserve explicit non-default port.
    port = parts.port
    netloc = host_lower
    if port is not None:
        default_port = 443 if scheme == "https" else 80
        if port != default_port:
            # Preserve IPv6 brackets when an explicit port is set.
            if ":" in host_lower and not host_lower.startswith("["):
                netloc = f"[{host_lower}]:{port}"
            else:
                netloc = f"{host_lower}:{port}"

    path = parts.path or "/"
    # Fragment is dropped by omitting it from urlunsplit.
    canonical = urlunsplit(
        (scheme, netloc, path, parts.query, "")
    )
    if len(canonical) > WEB_URL_MAX_LEN:
        raise ValueError(
            f"canonicalized url exceeds max length {WEB_URL_MAX_LEN}"
        )
    return canonical


def compute_web_source_fingerprint(
    *,
    canonical_url: str,
    retrieved_at: str,
) -> str:
    """Stable SHA-256 hex fingerprint for one web source.

    Combines the canonical URL and the server-recorded retrieval
    timestamp (ISO-8601 string). Provider-supplied fields (title,
    description, score, raw payload) are intentionally excluded —
    drift in those must not invalidate the source identity used by
    the registry / finalizer.
    """
    if not isinstance(canonical_url, str) or not canonical_url:
        raise ValueError("canonical_url must be a non-empty str")
    if not isinstance(retrieved_at, str) or not retrieved_at:
        raise ValueError("retrieved_at must be a non-empty str")
    payload = f"{canonical_url}|{retrieved_at}".encode()
    return hashlib.sha256(payload).hexdigest()


def display_domain_from_canonical_url(canonical_url: str) -> str:
    """Extract a display domain (host without port) from a canonical URL.

    Used by :class:`WebEvidence` so the model view / public citation
    never re-derives host parsing. Falls back to the full host string
    when extraction fails — never raises.
    """
    if not isinstance(canonical_url, str) or not canonical_url:
        return ""
    parts = urlsplit(canonical_url)
    host = parts.hostname or ""
    return host.lower()


def registrable_domain_from_canonical_url(canonical_url: str) -> str | None:
    """Return the PSL registrable-domain key, or ``None`` when unsafe.

    This is for Host-only source diversity accounting, not public display. It
    maps subdomains such as ``news.example.com`` to ``example.com`` and
    ``a.example.co.uk`` to ``example.co.uk``. IP literals, localhost,
    malformed/illegal hosts, unknown suffixes, and non-HTTP(S) inputs return
    ``None`` rather than manufacturing a domain identity.

    The module-level extractor is configured with ``suffix_list_urls=()`` and
    ``cache_dir=None``; it uses only tldextract's bundled Public Suffix List
    snapshot and cannot download at runtime.
    """
    if not isinstance(canonical_url, str) or not canonical_url:
        return None
    parts = urlsplit(canonical_url)
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return None
    host = parts.hostname
    if not host:
        return None
    host = host.rstrip(".").lower()
    if not host or host == "localhost":
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(ascii_host) > 253:
        return None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(
            not (character.isascii() and (character.isalnum() or character == "-"))
            for character in label
        )
        for label in labels
    ):
        return None

    extracted = _REGISTRABLE_DOMAIN_EXTRACTOR(ascii_host)
    if not extracted.domain or not extracted.suffix:
        return None
    return f"{extracted.domain}.{extracted.suffix}".lower()


_STRICT_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_provider_published_at(value: object) -> str | None:
    """Accept only a provider-supplied strict ISO calendar date.

    The host never derives publication dates from ``page_age``, URLs, or a
    retrieval timestamp.  A missing or malformed provider value therefore
    remains ``None`` instead of becoming a freshness claim.
    """
    if not isinstance(value, str) or not _STRICT_ISO_DATE_RE.fullmatch(value):
        return None
    try:
        return _date.fromisoformat(value).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Execution capability (server-owned)
# ---------------------------------------------------------------------------


class ResolvedWebSearchCapability(BaseModel):
    """Server-owned execution truth for one Ask turn.

    Built by the execution-config resolver from the request
    ``web_search_mode`` + provider readiness. The agent reads only
    :attr:`enabled_for_turn` to decide whether the ``search_web`` tool
    is mounted; it never reads ``provider`` / ``protocol`` / ``policy_version``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled_for_turn: bool
    provider: str = Field(min_length=1, max_length=64)
    protocol: WebSearchProtocol
    execution_mode: WebSearchExecutionMode = "host_function"
    decision_mode: WebSearchDecisionMode = "agent_auto"
    # Freezes the lifecycle to two provider attempts. The second can only
    # follow an initial ``no_results`` outcome; the coordinator enforces that
    # state transition independently of this declarative capability.
    max_calls: int = Field(default=2, ge=1, le=WEB_MAX_CALLS_PER_TURN)
    max_results_per_call: int = Field(
        default=5, ge=1, le=WEB_MAX_RESULTS_PER_CALL
    )
    policy_version: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Internal web evidence (server-owned)
# ---------------------------------------------------------------------------


class WebEvidence(BaseModel):
    """Server-owned internal web evidence handle.

    Minted only by the host after a completed search call. The model
    only receives the opaque ``internal_handle_id`` (evh_ shape); all
    other fields are server-side registry material.

    ``source_fingerprint`` is recomputed from ``canonical_url`` +
    ``retrieved_at`` and verified on every registry read so provider
    text drift cannot silently replace a source.

    ``provider_result_ref`` is internal-only: it must never appear on
    public DTOs, SSE, or persistence-replay payloads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    internal_handle_id: str = Field(min_length=1, max_length=64)
    canonical_url: str = Field(min_length=1, max_length=WEB_URL_MAX_LEN)
    display_domain: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=WEB_TITLE_MAX_LEN)
    description: str | None = Field(
        default=None, max_length=WEB_DESCRIPTION_MAX_LEN
    )
    retrieved_at: str = Field(min_length=1, max_length=64)
    provider_result_ref: str | None = Field(
        default=None,
        description="Internal-only provider result id; never on public DTO.",
    )
    source_fingerprint: str = Field(pattern=WEB_SOURCE_FINGERPRINT_PATTERN)
    # Retain optional provider freshness metadata internally. Only a
    # strict ``YYYY-MM-DD`` provider value becomes ``published_at``;
    # datetimes and malformed values become ``None``. ``page_age`` remains
    # raw provider text for internal use and is never a public freshness claim.
    published_at: str | None = Field(
        default=None,
        max_length=64,
        description="Optional strict ISO calendar date from provider; untrusted.",
    )
    page_age: str | None = Field(
        default=None,
        max_length=64,
        description="Optional relative page-age hint from provider; untrusted.",
    )

    @field_validator("published_at", mode="before")
    @classmethod
    def _normalize_provider_published_at(cls, value: object) -> str | None:
        return normalize_provider_published_at(value)

    @field_validator("canonical_url")
    @classmethod
    def _validate_canonical_url(cls, value: str) -> str:
        # Re-canonicalize to catch any drift; the canonical form must
        # round-trip through :func:`canonicalize_url`.
        canonical = canonicalize_url(value)
        if canonical != value:
            raise ValueError(
                "canonical_url must already be in canonical form; "
                "route provider URLs through canonicalize_url()"
            )
        return value

    @model_validator(mode="after")
    def _verify_source_fingerprint(self) -> WebEvidence:
        expected = compute_web_source_fingerprint(
            canonical_url=self.canonical_url,
            retrieved_at=self.retrieved_at,
        )
        if expected != self.source_fingerprint:
            raise ValueError(
                "source_fingerprint does not match canonical_url + retrieved_at"
            )
        return self


# ---------------------------------------------------------------------------
# Public DTOs (wire / SSE / persistence)
# ---------------------------------------------------------------------------


# ASK-WEB-G1-``PublicWebCitation`` has been removed. The single
# canonical public citation contract is :class:`PublicCitation` in
# ``app.services.reader_record_ask.finalizer``. It supports both
# ``article`` and ``web`` source kinds (discriminated union), enforces
# canonical URL form and non-empty title for web citations, and never
# carries internal handles / provider refs / query / rank / score / raw
# payload. Import from ``finalizer`` instead of this module.


class PublicWebSearchSummary(BaseModel):
    """Turn-level web search outcome summary.

    Carried on :class:`ReaderRecordAskCompletedDTO.web_search` so hot
    SSE, DB persistence, and cold history replay all observe the same
    "search happened but no cited source" state.

    ``cited_source_count`` counts message-local public web citations
    that were actually attached to the answer — never the raw provider
    result count.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: WebSearchOutcome
    cited_source_count: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class WebSearchTurnObservation:
    """One terminal-only, content-free Web Search telemetry record.

    This object is deliberately not a DTO, SSE payload, or persistence model.
    It contains aggregate state only; query text, URLs, titles, provider raw
    payloads, credentials, and opaque evidence handles are absent by design.
    """

    attempt_count: int
    final_outcome: WebSearchOutcome | None
    total_duration_ms: int | None
    cited_source_count: int
    distinct_domain_count: int
    deadline_exhausted: bool
    second_query_changed: bool | None
    final_detail_code: str | None


__all__ = [
    "PublicWebSearchSummary",
    "ResolvedWebSearchCapability",
    "WEB_DESCRIPTION_MAX_LEN",
    "WEB_MAX_CALLS_PER_TURN",
    "WEB_MAX_RESULTS_PER_CALL",
    "WEB_QUERY_MAX_LEN",
    "WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS",
    "WEB_SEARCH_TURN_DEADLINE_SECONDS",
    "WEB_SOURCE_FINGERPRINT_PATTERN",
    "WEB_TITLE_MAX_LEN",
    "WEB_URL_MAX_LEN",
    "WebEvidence",
    "WebSearchDecisionMode",
    "WebSearchExecutionMode",
    "WebSearchMode",
    "WebSearchOutcome",
    "WebSearchProtocol",
    "WebSearchTurnObservation",
    "canonicalize_url",
    "compute_web_source_fingerprint",
    "display_domain_from_canonical_url",
    "normalize_provider_published_at",
    "registrable_domain_from_canonical_url",
]
