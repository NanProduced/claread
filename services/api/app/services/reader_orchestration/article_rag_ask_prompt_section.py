"""D6-I4K: Article RAG Ask Prompt Section Builder.

Final deterministic transform: converts an
:class:`ArticleRagAskPromptSegment` (D6-I4J) into an
:class:`ArticleRagAskPromptSection` value object that the Ask
runtime / prompt constructor consumes verbatim.  This module
is the LAST layer before the Ask prompt is assembled — the
section object it produces is the contract boundary with the
Ask layer.

The section builder:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — every failure (malformed segment shape,
    missing prompt_text, citation/context_id mismatch,
    oversized section) maps to a fail-soft section with
    ``include_in_prompt=False``;
  * never mutates ``segment.prompt_text`` — the I4J attachment
    carries verbatim content from the I4G composer output
    (which built it from the I4F pack / I4E plan); the section
    wraps it in fixed marker lines but the inner content is
    preserved exactly.  No re-parsing, no truncation, no
    citation extraction from the text;
  * never includes ``query_text`` — only ``query_sha256``;
  * never includes ``provider_metadata``, vector payload, or
    any Plate / Markdown / DOM / Slate / UI display group /
    render / text / html / chunks key — the ``metadata_json``
    field is a strict allowlist with a value-level guard;
  * never re-derives citation / text from anything — citations
    come from ``segment.citations`` only.

Truth boundary
--------------

The section builder is the last transformation layer.  It
must not re-derive citations from the prompt text, must not
interpret projection fields as fact sources, and must not
trust a regression that surfaces a hostile value on any
upstream field.  Defence in depth:

  1. **Shape validation** — every segment input is checked
     before any field is copied; a malformed shape fail-softs.
  2. **Citation / context_id alignment** — on the include path
     the two lists MUST have matching lengths; a mismatch is a
     regression in the upstream chain and fail-softs.
  3. **Length cap** — the rendered ``section_text`` MUST fit
     within ``max_section_chars``.  The contract is
     fail-soft-on-overflow (NOT truncate): truncation would
     corrupt the marker alignment with the citation list and
     confuse the LLM about which citation maps to which block.
  4. **repr/str safety** — every field that may carry
     user-derived content (prompt / chunk text / query / secret /
     SDK message) is marked ``field(repr=False)`` so the default
     dataclass repr / str does NOT echo it.
  5. **metadata allowlist + value guard** — the metadata
     projection is a strict allowlist; every value is passed
     through the same scalar / length / forbidden-substring
     guard used by I4J.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .article_rag_ask_integration_adapter import (
    ArticleRagAskPromptSegment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The single kind the I4J segment carries.  Future kinds (e.g.
# a non-RAG source) would be added as additional Literal
# values; today the contract is intentionally narrow.
SECTION_KIND = "article_rag_context"

# Section-text boundary markers.  These are FIXED strings the
# Ask prompt constructor can match on to extract the section
# verbatim.  The markers are deliberately simple so they
# cannot be confused with Plate / Markdown / DOM / Slate / UI
# projection fields (which are forbidden by the truth
# boundary).
_SECTION_BEGIN_MARKER = "[ARTICLE_RAG_CONTEXT_BEGIN]"
_SECTION_END_MARKER = "[ARTICLE_RAG_CONTEXT_END]"

# Default ``max_section_chars``.  Mirrors the I4F default context
# budget so behaviour is symmetric.
DEFAULT_MAX_SECTION_CHARS = 4000

# Failure code for this layer's own fail-soft paths.  Differs
# from I4J's code so dashboards can distinguish "the
# integration adapter returned something unexpected" from "the
# section builder itself caught an unexpected error".
FAILURE_CODE_SECTION_UNEXPECTED_ERROR = (
    "article_rag_prompt_section_unexpected_error"
)
FAILURE_CODE_SECTION_OVERSIZE = (
    "article_rag_prompt_section_oversize"
)
FAILURE_CODE_SECTION_SHAPE_INVALID = (
    "article_rag_prompt_section_shape_invalid"
)

# The ``metadata_json`` allowlist.  Mirrors the I4J allowlist
# — same diagnostic / stable-id keys; same exclusions for
# provider / query / vector / projection / UI fields.
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "status",
        "failure_code",
        "retryable",
        "fallback_allowed",
        "omitted_hit_count",
        "budget_exceeded",
        "stable_document_id",
        "base_id",
        "record_generation",
        "plan_content_sha256",
        "index_version",
        "source_pack_hash",
    }
)

# The set of section statuses we recognise.  The I4H status is
# a ``typing.Literal`` (compile-time only); the I4J segment is
# supposed to forward one of 5 values through, but a regression
# in the attachment service (or a hostile fake in a test) could
# surface an unrecognised string (e.g. ``"paused"``,
# ``"failed"``, ``""``).  Anything outside this allowlist is
# treated as a malformed status and fail-softs to
# ``not_indexed_or_unavailable``.  Same allowlist as I4J.
_ALLOWED_SECTION_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)

# Substrings we refuse to surface even on allowlisted values.
# Case-insensitive.  Mirrors the I4F / I4J sets so the value
# policy is consistent across the chain.
_FORBIDDEN_METADATA_VALUE_SUBSTRINGS = (
    "token",
    "uri",
    "url=",
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "credential",
    "auth=",
    "bearer ",
    "query_text",
    "query=",
    "query_vector",
    "embedding=",
    "sdk_message",
    "error_message",
    "exception",
    "traceback",
    "stacktrace",
    "plate",
    "markdown",
    "dom",
    "slate",
    "render",
    "html=",
    "innerhtml",
    "innertext",
)

# Length cap on allowlisted string values.  Anything longer is
# almost certainly a regression.
_MAX_METADATA_VALUE_LEN = 256


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskPromptSection:
    """A deterministic Ask prompt section carrying the I4J RAG
    attachment.

    The Ask runtime keys its prompt-construction policy on
    ``include_in_prompt``:

      * ``True`` — the section is non-empty.  ``section_text``
        is a fixed-marker-bracketed block whose inner content
        is ``segment.prompt_text`` verbatim.  ``citations``
        / ``context_ids`` are the I4J attachment's structured
        lists (verbatim).  The Ask runtime embeds
        ``section_text`` into the prompt and renders
        ``citations`` as a separate footnote / source list.
      * ``False`` — no RAG context is available.  ``section_text``
        is the empty string and ``citations`` is empty.  The
        Ask runtime answers without RAG.  ``status`` /
        ``failure_code`` are populated for ops visibility.

    ``kind`` is a single Literal value
    (``"article_rag_context"``) so the Ask runtime can dispatch
    on it without importing adapter-specific code.

    ``metadata_json`` is a STRICT allowlist of the upstream
    segment's safe fields.  Provider / query / vector /
    projection fields are NEVER surfaced.

    Every field that may carry user-derived content uses
    ``field(repr=False)`` so the section's default repr / str
    does NOT echo it (the Ask runtime reads the field
    directly; ops / debug surfaces must be explicit).
    """

    # Discriminator for the Ask runtime.
    kind: Literal["article_rag_context"]
    # Whether to embed ``section_text`` in the Ask prompt.
    include_in_prompt: bool
    # The prompt-section text.  On the include path this is the
    # I4J segment's ``prompt_text`` wrapped in fixed marker
    # lines (so the Ask runtime can extract the section
    # verbatim).  On the no-context path this is the empty
    # string.
    #
    # ``repr=False``: this field carries chunk text / query
    # fragments; it MUST NOT appear in the default dataclass
    # repr / str.
    section_text: str = field(repr=False)
    # Structured citations (verbatim from the I4J segment,
    # which copied verbatim from the I4G composer / I4F pack /
    # I4E plan).
    #
    # ``repr=False``: the citation dicts are plan-backed
    # content; they MUST NOT appear in the default repr.
    citations: tuple[dict[str, Any], ...] = field(repr=False)
    # Stable context ids embedded in ``section_text`` (matches
    # ``bundle.context_ids`` from the upstream chain).
    #
    # ``repr=False``: low-risk but kept off repr.
    context_ids: tuple[str, ...] = field(repr=False)
    # Source identity hash from the I4G composer.  The Ask
    # runtime can use this as a cache key for the prompt block.
    #
    # ``repr=False``: a regression could surface a
    # secret-bearing value here; the value-level guard drops
    # such values, and ``field(repr=False)`` is the backstop.
    source_pack_hash: str | None = field(repr=False)
    # SHA-256 of the query text, for traceability.  NEVER the
    # raw query text.
    query_sha256: str | None = field(repr=False)
    # Upstream status (propagated unchanged).
    #
    # ``repr=False``: hostile value guard fail-softs to
    # ``not_indexed_or_unavailable``; ``field(repr=False)`` is
    # the backstop.
    status: str = field(repr=False)
    # Upstream failure code (propagated unchanged on the
    # no-context path; ``None`` on the include path).
    #
    # ``repr=False``: a regression could surface a
    # secret-bearing failure code; the value-level guard drops
    # such values, and ``field(repr=False)`` is the backstop.
    failure_code: str | None = field(repr=False)
    # Upstream retryable flag.
    retryable: bool
    # Upstream fallback-allowed flag.  Always ``True`` on the
    # no-context path (the Ask layer can answer without RAG).
    fallback_allowed: bool
    # Strict-allowlist metadata.
    #
    # ``repr=False``: even though ``metadata_json`` is a
    # strict-allowlist projection, an ops debug surface
    # should be explicit about reading it.
    metadata_json: dict[str, Any] = field(
        default_factory=dict, repr=False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_metadata_value(value: Any) -> Any:
    """Return ``value`` if it is a safe scalar; otherwise ``None``.

    Mirrors the I4J value guard exactly so the value policy
    is consistent across the chain.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_VALUE_LEN:
            return None
        lowered = value.lower()
        for substr in _FORBIDDEN_METADATA_VALUE_SUBSTRINGS:
            if substr in lowered:
                return None
        return value
    try:
        import uuid as _uuid

        if isinstance(value, _uuid.UUID):
            return str(value)
    except ImportError:  # pragma: no cover — stdlib always available
        pass
    return None


def _scrub_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strict allowlist + value guard for ``metadata_json``.

    Two layers of defence:

      1. **Key allowlist** (12 keys) — anything outside the
         allowlist is dropped silently.
      2. **Value guard** (:func:`_safe_metadata_value`) — every
         allowlisted value is filtered: non-scalar values are
         dropped; ``str`` values that exceed the length cap OR
         contain any forbidden substring are dropped.
    """
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, raw_value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        safe_value = _safe_metadata_value(raw_value)
        if safe_value is None and raw_value is not None:
            continue
        safe[str(key)] = safe_value
    return safe


def _scrub_top_level_string(value: Any) -> str | None:
    """Scrub a top-level string field (``source_pack_hash`` /
    ``failure_code``) using the same value policy as
    :func:`_safe_metadata_value`.  Untrusted values are dropped
    (replaced with ``None``).  ``None`` is preserved.
    """
    if value is None:
        return None
    safe_value = _safe_metadata_value(value)
    if safe_value is None:
        return None
    if not isinstance(safe_value, str):
        return None
    return safe_value


# SHA-256 hex digest length.  Anything else is not a valid
# query_sha256 value.  Used by ``_scrub_sha256`` below.
_SHA256_HEX_LEN = 64


def _scrub_sha256(value: Any) -> str | None:
    """Validate and return a SHA-256 hex digest, or ``None``.

    Used for the top-level ``query_sha256`` field on the
    section.  A regression / hostile fake in the upstream chain
    could surface a non-SHA-256 value on this field (e.g. the
    raw query text, a short hash, a non-hex string, a ``None``
    value, a list).  The general ``_safe_metadata_value`` helper
    accepts ANY short string that passes the forbidden-substring
    check — which is too permissive for a value that is
    contractually defined to be a SHA-256 hex digest.

    This helper enforces:
      * the value MUST be a ``str``;
      * the length MUST be exactly 64 characters;
      * every character MUST be a hex digit (``[0-9a-f]``).

    Anything else is replaced with ``None`` so the secret
    cannot surface as a top-level field.  ``None`` input is
    preserved (signals "absent").
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) != _SHA256_HEX_LEN:
        return None
    # ``str.isdigit`` + lowercase hex digits.  We test
    # character-by-character to reject mixed-case + non-hex
    # characters uniformly.
    for ch in value:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "f")):
            return None
    return value


def _section_status_ok(status: Any) -> bool:
    """Runtime guard on the segment's ``status`` field.

    ``ArticleRagAskPromptSegment.status`` is typed
    ``ArticleRagAskContextResolveStatus`` (a ``typing.Literal``)
    at compile time, but Python does not enforce the Literal at
    runtime — a regression in the upstream chain (or a hostile
    fake) could surface an unrecognised string (e.g.
    ``"paused"``, ``""``, ``"SECRET-..."``).

    The Ask runtime keys its fallback policy on the status
    literal; an unknown value would silently break the dispatch
    contract.  This guard returns ``False`` for any status
    outside the 5-value allowlist
    (:data:`_ALLOWED_SECTION_STATUSES`); the builder fail-softs
    to ``not_indexed_or_unavailable`` in that case.

    ``status`` MUST be a string.
    """
    if not isinstance(status, str):
        return False
    return status in _ALLOWED_SECTION_STATUSES


def _build_section_text(prompt_text: str) -> str:
    """Wrap ``prompt_text`` in fixed-marker lines.

    The format is:

        [ARTICLE_RAG_CONTEXT_BEGIN]
        {prompt_text}
        [ARTICLE_RAG_CONTEXT_END]

    ``prompt_text`` is preserved verbatim — the section builder
    MUST NOT mutate, re-parse, or truncate it.  The markers are
    the only added structure; they are deliberately simple
    strings (not Plate / Markdown / DOM / Slate / UI projection
    syntax) so the Ask runtime can extract the inner content
    verbatim.
    """
    return (
        f"{_SECTION_BEGIN_MARKER}\n"
        f"{prompt_text}\n"
        f"{_SECTION_END_MARKER}"
    )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ArticleRagAskPromptSectionBuilder:
    """Final layer: I4J segment → Ask-prompt section.

    Pure orchestrator.  No I/O.  The Ask runtime calls
    :meth:`build` and gets a typed, never-raises
    :class:`ArticleRagAskPromptSection` value object.
    """

    def __init__(
        self,
        *,
        max_section_chars: int = DEFAULT_MAX_SECTION_CHARS,
    ) -> None:
        if max_section_chars <= 0:
            raise ValueError(
                "ArticleRagAskPromptSectionBuilder constructed with "
                f"max_section_chars={max_section_chars}; must be a "
                "positive integer"
            )
        self._max_section_chars = max_section_chars

    def build(
        self,
        segment: ArticleRagAskPromptSegment,
    ) -> ArticleRagAskPromptSection:
        """Build an :class:`ArticleRagAskPromptSection` from
        ``segment``.

        Never raises.  Every failure (malformed segment shape,
        missing prompt_text on the include path, citation /
        context_id mismatch, oversized section, hostile value)
        maps to a fail-soft section with
        ``include_in_prompt=False``.
        """
        # 1. Defensive shape check.
        if not isinstance(segment, ArticleRagAskPromptSegment):
            return self._make_unexpected_section()

        # 2. Runtime status guard: an unrecognised status string
        #    (e.g. ``"paused"``, ``""``, ``"SECRET-..."``) is a
        #    contract violation — fail soft.  The I4J segment is
        #    supposed to forward one of the 5 I4H status values
        #    through, but a regression / hostile fake could
        #    surface a different string.  The Ask runtime keys
        #    its fallback policy on the status literal — an
        #    unknown value would silently break the dispatch
        #    contract.
        if not _section_status_ok(segment.status):
            return self._make_unexpected_section()

        # 3. The include path.
        if segment.include_in_prompt:
            return self._build_include_section(segment)

        # 4. The no-context path: copy the safe diagnostic
        #    fields from the segment; the section text / citations
        #    / context_ids are empty.
        return self._build_no_context_section(segment)

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_include_section(
        self,
        segment: ArticleRagAskPromptSegment,
    ) -> ArticleRagAskPromptSection:
        """Build the include-path section with defensive shape
        checks.

        The checks here are the LAST line of defence against a
        regression / hostile fake in the upstream chain that
        surfaces a malformed shape (missing prompt text,
        citation / context_id length mismatch, oversized
        rendered section).
        """
        # The include path requires the include-path status.
        # A regression / hostile fake could surface
        # ``include_in_prompt=True`` with a non-include status
        # (e.g. ``"disabled"``); fail closed.
        if segment.status != "available":
            return self._make_shape_invalid_section(
                reason="include_path_status_must_be_available"
            )

        # prompt_text MUST be a non-empty string.
        prompt = segment.prompt_text
        if not isinstance(prompt, str) or not prompt.strip():
            return self._make_shape_invalid_section(
                reason="missing_or_empty_prompt_text"
            )

        # citations / context_ids MUST be iterable sequences with
        # matching lengths.  A mismatch is a regression in the
        # upstream chain (the LLM would otherwise have a hard
        # time mapping ``[rag-1]`` markers to citation rows).
        citations = segment.citations
        context_ids = segment.context_ids
        if not isinstance(citations, (tuple, list)) or not isinstance(
            context_ids, (tuple, list)
        ):
            return self._make_shape_invalid_section(
                reason="citations_or_context_ids_not_iterable"
            )
        if len(citations) != len(context_ids):
            return self._make_shape_invalid_section(
                reason="citations_context_ids_length_mismatch"
            )

        # Render the section text and check the length cap.
        section_text = _build_section_text(prompt)
        if len(section_text) > self._max_section_chars:
            # FAIL-SOFT (do NOT truncate): truncation would
            # corrupt the marker alignment with the citation
            # list and confuse the LLM about which citation
            # maps to which block.
            return self._make_oversize_section(
                actual_chars=len(section_text),
                max_chars=self._max_section_chars,
            )

        # Copy the diagnostic / stable-id fields with the same
        # value-level guard used for metadata_json (so a
        # secret-bearing ``source_pack_hash`` / ``failure_code``
        # is dropped before reaching the section).
        return ArticleRagAskPromptSection(
            kind=SECTION_KIND,
            include_in_prompt=True,
            section_text=section_text,
            citations=tuple(citations),
            context_ids=tuple(context_ids),
            source_pack_hash=_scrub_top_level_string(
                segment.source_pack_hash
            ),
            query_sha256=_scrub_sha256(segment.query_sha256),
            status=segment.status,
            failure_code=_scrub_top_level_string(segment.failure_code),
            retryable=bool(segment.retryable),
            fallback_allowed=bool(segment.fallback_allowed),
            metadata_json=_scrub_metadata(
                dict(segment.metadata_json or {})
            ),
        )

    def _build_no_context_section(
        self,
        segment: ArticleRagAskPromptSegment,
    ) -> ArticleRagAskPromptSection:
        """Build the no-context section (empty text, empty
        citations, diagnostic fields preserved with the same
        value-level guard).
        """
        return ArticleRagAskPromptSection(
            kind=SECTION_KIND,
            include_in_prompt=False,
            section_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=_scrub_top_level_string(
                segment.source_pack_hash
            ),
            query_sha256=_scrub_sha256(segment.query_sha256),
            status=segment.status,
            failure_code=_scrub_top_level_string(segment.failure_code),
            retryable=bool(segment.retryable),
            fallback_allowed=bool(segment.fallback_allowed),
            metadata_json=_scrub_metadata(
                dict(segment.metadata_json or {})
            ),
        )

    # ------------------------------------------------------------------
    # Fail-soft helpers
    # ------------------------------------------------------------------

    def _make_unexpected_section(self) -> ArticleRagAskPromptSection:
        """Build a generic fail-soft section."""
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_SECTION_UNEXPECTED_ERROR,
        )

    def _make_shape_invalid_section(
        self,
        *,
        reason: str,
    ) -> ArticleRagAskPromptSection:
        """Build a fail-soft section for a malformed include-path
        shape.

        The reason is logged (NEVER placed on the public
        section) so ops can diagnose without leaking detail to
        the Ask runtime.
        """
        logger.info(
            "Article RAG ask prompt section: malformed include "
            "shape (reason=%s); returning fail-soft section",
            reason,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_SECTION_SHAPE_INVALID,
        )

    def _make_oversize_section(
        self,
        *,
        actual_chars: int,
        max_chars: int,
    ) -> ArticleRagAskPromptSection:
        """Build a fail-soft section for an oversized rendered
        section text.

        The size info is logged (NEVER placed on the public
        section).
        """
        logger.info(
            "Article RAG ask prompt section: section_text exceeds "
            "max_section_chars (actual=%d, max=%d); returning "
            "fail-soft section (no truncation)",
            actual_chars,
            max_chars,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_SECTION_OVERSIZE,
        )

    def _build_fail_soft(
        self,
        *,
        status: str,
        failure_code: str,
    ) -> ArticleRagAskPromptSection:
        """Build a fail-soft section with empty content."""
        return ArticleRagAskPromptSection(
            kind=SECTION_KIND,
            include_in_prompt=False,
            section_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            query_sha256=None,
            status=status,
            failure_code=failure_code,
            retryable=False,
            fallback_allowed=True,
            metadata_json={
                "status": status,
                "failure_code": failure_code,
                "retryable": False,
                "fallback_allowed": True,
            },
        )


__all__ = [
    "DEFAULT_MAX_SECTION_CHARS",
    "FAILURE_CODE_SECTION_UNEXPECTED_ERROR",
    "FAILURE_CODE_SECTION_OVERSIZE",
    "FAILURE_CODE_SECTION_SHAPE_INVALID",
    "SECTION_KIND",
    "ArticleRagAskPromptSection",
    "ArticleRagAskPromptSectionBuilder",
]