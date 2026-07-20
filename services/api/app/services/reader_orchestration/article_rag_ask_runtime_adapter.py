"""D6-I4L: Article RAG Ask Runtime Boundary Adapter.

Final deterministic transform: converts an
:class:`ArticleRagAskPromptSection` (D6-I4K) into an
:class:`ArticleRagAskRuntimeContext` value object that the Ask
runtime layer consumes verbatim.  This module is the
**RUNTIME boundary** — the LAST transform between the
Article RAG pipeline (I4E → I4K) and the Ask runtime.

The runtime adapter:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — every failure (malformed section shape,
    unknown status, missing prompt_text, citation /
    context_id mismatch, oversized runtime text, hostile
    value) maps to a fail-soft context with
    ``should_attach=False``;
  * never mutates ``section.section_text`` — the I4K section
    wraps the upstream composer output in fixed marker
    lines; the runtime adapter copies it verbatim.  No
    re-parsing, no truncation, no citation extraction from
    the text;
  * never includes ``query_text`` — only ``query_sha256``;
  * never includes ``provider_metadata``, vector payload, or
    any Plate / Markdown / DOM / Slate / UI display group /
    render / text / html / chunks key — the ``metadata_json``
    field is a strict allowlist with a value-level guard;
  * never re-derives citation / text from anything —
    citations come from ``section.citations`` only;
  * the runtime text length cap is a fail-soft boundary
    (NOT a truncation): truncating would corrupt the marker
    alignment with the citation list and confuse the LLM
    about which citation maps to which block.

Truth boundary
--------------

The runtime adapter is the LAST transformation layer.  It
MUST NOT re-derive citations from the prompt text, MUST NOT
interpret projection fields as fact sources, and MUST NOT
trust a regression that surfaces a hostile value on any
upstream field.  Defence in depth:

  1. **Shape validation** — every section input is checked
     before any field is copied; a malformed shape fail-softs.
  2. **Runtime status allowlist** — the section's ``status``
     MUST be in the I4H 5-value Literal set; otherwise
     fail-soft to ``not_indexed_or_unavailable``.
  3. **Runtime text length cap** — ``prompt_section_text``
     MUST fit within ``max_runtime_chars``.  Fail-soft on
     overflow (NOT truncation).
  4. **Citation / context_id alignment** — on the include
     path the two lists MUST have matching lengths; a
     mismatch is a regression in the upstream chain and
     fail-softs.
  5. **SHA-256 strict validation** — ``query_sha256`` MUST
     be a 64-char lowercase-hex string; raw queries /
     truncated / non-hex values are dropped to ``None``.
  6. **repr/str safety** — every field that may carry
     user-derived content (prompt / chunk text / query /
     secret / SDK message) is marked ``field(repr=False)``
     so the default dataclass repr / str does NOT echo it.
  7. **metadata allowlist + value guard** — the metadata
     projection is a strict allowlist; every value is passed
     through the scalar / length / forbidden-substring
     guard used by I4J / I4K.

This is the **runtime** boundary — the contract here is
what the Ask runtime is allowed to read.  Nothing
uncontrolled leaks through.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .article_rag_ask_prompt_section import ArticleRagAskPromptSection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default ``max_runtime_chars``.  Mirrors the I4F / I4K default
# caps so behaviour is symmetric across the chain.
DEFAULT_MAX_RUNTIME_CHARS = 4000

# Failure codes — stable, machine-readable.  Differs from
# I4K's codes so dashboards can distinguish "the section
# builder returned something unexpected" from "the runtime
# adapter itself caught an unexpected error".
FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR = (
    "article_rag_ask_runtime_adapter_unexpected_error"
)
FAILURE_CODE_RUNTIME_OVERSIZE = (
    "article_rag_ask_runtime_adapter_oversize"
)
FAILURE_CODE_RUNTIME_SHAPE_INVALID = (
    "article_rag_ask_runtime_adapter_shape_invalid"
)
FAILURE_CODE_RUNTIME_STATUS_INVALID = (
    "article_rag_ask_runtime_adapter_status_invalid"
)

# The set of statuses we recognise.  Mirrors I4H / I4J / I4K:
# the same 5 values.  Anything else (e.g. ``"paused"``,
# ``""``, ``"SECRET-..."``) is a contract violation — fail
# soft to ``not_indexed_or_unavailable``.  This is the LAST
# line of defence for status before the Ask runtime reads it.
_ALLOWED_RUNTIME_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)

# The ``metadata_json`` allowlist.  Mirrors I4K — same
# diagnostic / stable-id keys; same exclusions for provider /
# query / vector / projection / UI fields.
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
        "source_pack_hash",
    }
)

# Substrings we refuse to surface even on allowlisted values.
# Case-insensitive.  Mirrors I4F / I4J / I4K sets.
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

# Length cap on allowlisted string values.
_MAX_METADATA_VALUE_LEN = 256

# SHA-256 hex digest length.  The ``query_sha256`` field is
# contractually defined to be a 64-char lowercase-hex string.
_SHA256_HEX_LEN = 64


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskRuntimeContext:
    """A deterministic Ask runtime value object carrying the I4K
    section.

    The Ask runtime layer keys its attach policy on
    ``should_attach``:

      * ``True`` — the runtime boundary passes
        ``prompt_section_text`` through to the prompt
        constructor.  The text is verbatim — no mutation /
        no re-parsing.  ``citations`` / ``context_ids`` are
        the I4K section's structured lists (verbatim).
      * ``False`` — the runtime boundary does NOT attach any
        RAG text.  ``prompt_section_text`` is the empty string
        and ``citations`` is empty.  The Ask runtime answers
        without RAG.  ``status`` / ``failure_code`` are
        populated for ops visibility.

    ``kind`` is a single Literal value
    (``"article_rag_context"``) so the Ask runtime can
    dispatch on it without importing adapter-specific code.

    ``metadata_json`` is a STRICT allowlist of the upstream
    section's safe fields.  Provider / query / vector /
    projection fields are NEVER surfaced.

    Every field that may carry user-derived content uses
    ``field(repr=False)`` so the runtime context's default
    repr / str does NOT echo it (the Ask runtime reads the
    field directly; ops / debug surfaces must be explicit).
    """

    # Discriminator for the Ask runtime.
    kind: Literal["article_rag_context"]
    # Whether to attach the RAG section to the Ask prompt.
    should_attach: bool
    # The prompt-section text.  On the attach path this is
    # ``section.section_text`` verbatim (the I4K layer wrapped
    # the upstream prompt body in fixed markers; the runtime
    # adapter does NOT re-parse or modify it).  On the
    # no-attach path this is the empty string.
    #
    # ``repr=False``: carries chunk text / query fragments.
    prompt_section_text: str = field(repr=False)
    # Structured citations (verbatim from the I4K section,
    # which copied verbatim from the I4G composer / I4F pack /
    # I4E plan).
    #
    # ``repr=False``: the citation dicts are plan-backed
    # content.
    citations: tuple[dict[str, Any], ...] = field(repr=False)
    # Stable context ids embedded in ``prompt_section_text``.
    #
    # ``repr=False``.
    context_ids: tuple[str, ...] = field(repr=False)
    # Source identity hash from the I4G composer.  The Ask
    # runtime can use this as a cache key for the runtime
    # boundary attachment.
    #
    # ``repr=False``: a regression could surface a
    # secret-bearing value here.
    source_pack_hash: str | None = field(repr=False)
    # SHA-256 of the query text, for traceability.  NEVER the
    # raw query text.  Strict 64-char lowercase-hex.
    #
    # ``repr=False``.
    query_sha256: str | None = field(repr=False)
    # Upstream status (propagated unchanged — guarded by the
    # 5-value allowlist).
    #
    # ``repr=False``.
    status: str = field(repr=False)
    # Upstream failure code (propagated unchanged).
    #
    # ``repr=False``.
    failure_code: str | None = field(repr=False)
    # Upstream retryable flag.
    retryable: bool
    # Upstream fallback-allowed flag.  Always ``True`` on the
    # no-attach path (the Ask runtime can answer without RAG).
    fallback_allowed: bool
    # Strict-allowlist metadata.
    #
    # ``repr=False``.
    metadata_json: dict[str, Any] = field(
        default_factory=dict, repr=False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_metadata_value(value: Any) -> Any:
    """Same value guard as I4J / I4K."""
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
    """Strict allowlist + value guard for ``metadata_json``."""
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
    ``failure_code``).  Untrusted values are dropped
    (replaced with ``None``).  ``None`` is preserved."""
    if value is None:
        return None
    safe_value = _safe_metadata_value(value)
    if safe_value is None:
        return None
    if not isinstance(safe_value, str):
        return None
    return safe_value


def _scrub_sha256(value: Any) -> str | None:
    """Strict SHA-256 validation.  Returns the value only if
    it is a 64-char lowercase-hex string; otherwise
    ``None``.  ``None`` is preserved (signals "absent").
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) != _SHA256_HEX_LEN:
        return None
    for ch in value:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "f")):
            return None
    return value


def _runtime_status_ok(status: Any) -> bool:
    """Runtime guard on the section's ``status`` field.

    ``ArticleRagAskPromptSection.status`` is typed as a
    ``typing.Literal`` at compile time only.  A regression /
    hostile fake could surface an unrecognised string
    (``"paused"``, ``""``, ``"SECRET-..."``) at runtime.
    Anything outside
    :data:`_ALLOWED_RUNTIME_STATUSES` fail-softs to
    ``not_indexed_or_unavailable``.
    """
    if not isinstance(status, str):
        return False
    return status in _ALLOWED_RUNTIME_STATUSES


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ArticleRagAskRuntimeAdapter:
    """Final layer: I4K section → Ask runtime value object.

    Pure orchestrator.  No I/O.  The Ask runtime calls
    :meth:`build` and gets a typed, never-raises
    :class:`ArticleRagAskRuntimeContext` value object.
    """

    def __init__(
        self,
        *,
        max_runtime_chars: int = DEFAULT_MAX_RUNTIME_CHARS,
    ) -> None:
        if max_runtime_chars <= 0:
            raise ValueError(
                "ArticleRagAskRuntimeAdapter constructed with "
                f"max_runtime_chars={max_runtime_chars}; must be a "
                "positive integer"
            )
        self._max_runtime_chars = max_runtime_chars

    def build(
        self,
        section: ArticleRagAskPromptSection,
    ) -> ArticleRagAskRuntimeContext:
        """Build an :class:`ArticleRagAskRuntimeContext` from
        ``section``.

        Never raises.  Every failure maps to a fail-soft
        context with ``should_attach=False``.
        """
        # 1. Defensive shape check (wrong type).
        if not isinstance(section, ArticleRagAskPromptSection):
            return self._make_unexpected_context()

        # 2. Runtime status allowlist.
        if not _runtime_status_ok(section.status):
            return self._make_unexpected_context()

        # 3. The attach path.
        if section.include_in_prompt:
            return self._build_attach_context(section)

        # 4. The no-attach path.
        return self._build_no_attach_context(section)

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_attach_context(
        self,
        section: ArticleRagAskPromptSection,
    ) -> ArticleRagAskRuntimeContext:
        """Build the attach-path context.

        The check here is the LAST line of defence before the
        Ask runtime — any hostile / regressed upstream value
        is dropped / fail-softed so the runtime layer can
        consume a clean value object.
        """
        # The attach path requires the include-path status.
        if section.status != "available":
            return self._make_status_invalid_context(
                reason="attach_path_status_must_be_available"
            )

        # prompt_section_text MUST be a non-empty string.
        prompt_text = section.section_text
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return self._make_shape_invalid_context(
                reason="missing_or_empty_prompt_section_text"
            )

        # citations / context_ids MUST be iterable sequences
        # with matching lengths.  A mismatch is a regression
        # in the upstream chain (the LLM would otherwise have a
        # hard time mapping ``[rag-N]`` markers to citation
        # rows).
        citations = section.citations
        context_ids = section.context_ids
        if not isinstance(citations, (tuple, list)) or not isinstance(
            context_ids, (tuple, list)
        ):
            return self._make_shape_invalid_context(
                reason="citations_or_context_ids_not_iterable"
            )
        if len(citations) != len(context_ids):
            return self._make_shape_invalid_context(
                reason="citations_context_ids_length_mismatch"
            )

        # Length cap.  Truncation would corrupt the marker
        # alignment with the citation list; fail-soft instead.
        if len(prompt_text) > self._max_runtime_chars:
            return self._make_oversize_context(
                actual_chars=len(prompt_text),
                max_chars=self._max_runtime_chars,
            )

        return ArticleRagAskRuntimeContext(
            kind="article_rag_context",
            should_attach=True,
            prompt_section_text=prompt_text,
            citations=tuple(citations),
            context_ids=tuple(context_ids),
            source_pack_hash=_scrub_top_level_string(
                section.source_pack_hash
            ),
            query_sha256=_scrub_sha256(section.query_sha256),
            status=section.status,
            failure_code=_scrub_top_level_string(section.failure_code),
            retryable=bool(section.retryable),
            fallback_allowed=bool(section.fallback_allowed),
            metadata_json=_scrub_metadata(
                section.metadata_json
            ),
        )

    def _build_no_attach_context(
        self,
        section: ArticleRagAskPromptSection,
    ) -> ArticleRagAskRuntimeContext:
        """Build the no-attach context.

        Empty text / citations / context_ids; diagnostic fields
        preserved with the same value scrub.

        The no-attach path has a stricter shape contract than
        the attach path: every required field MUST be empty
        (no stray content) and the status MUST NOT be
        ``"available"`` (that would mean the upstream said
        "ready to attach" while the section builder said
        "don't attach" — a state-semantic inconsistency that
        the Ask runtime should never see).  Any mismatch here
        fail-softs.
        """
        # Required: section_text must be empty.
        if not (
            isinstance(section.section_text, str)
            and section.section_text == ""
        ):
            return self._make_shape_invalid_context(
                reason="no_attach_path_section_text_must_be_empty"
            )
        # Required: citations must be empty.
        if not (isinstance(section.citations, (tuple, list))
                and len(section.citations) == 0):
            return self._make_shape_invalid_context(
                reason="no_attach_path_citations_must_be_empty"
            )
        # Required: context_ids must be empty.
        if not (isinstance(section.context_ids, (tuple, list))
                and len(section.context_ids) == 0):
            return self._make_shape_invalid_context(
                reason="no_attach_path_context_ids_must_be_empty"
            )
        # Required: status must NOT be "available" (state-
        # semantic consistency — the attach path owns
        # "available"; the no-attach path owns the other 4).
        if section.status == "available":
            return self._make_shape_invalid_context(
                reason="no_attach_path_status_must_not_be_available"
            )

        return ArticleRagAskRuntimeContext(
            kind="article_rag_context",
            should_attach=False,
            prompt_section_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=_scrub_top_level_string(
                section.source_pack_hash
            ),
            query_sha256=_scrub_sha256(section.query_sha256),
            status=section.status,
            failure_code=_scrub_top_level_string(section.failure_code),
            retryable=bool(section.retryable),
            fallback_allowed=bool(section.fallback_allowed),
            metadata_json=_scrub_metadata(
                section.metadata_json
            ),
        )

    # ------------------------------------------------------------------
    # Fail-soft helpers
    # ------------------------------------------------------------------

    def _make_unexpected_context(self) -> ArticleRagAskRuntimeContext:
        """Generic fail-soft context (wrong-type shape or
        unrecognised status)."""
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR,
        )

    def _make_shape_invalid_context(
        self,
        *,
        reason: str,
    ) -> ArticleRagAskRuntimeContext:
        """Fail-soft context for a malformed attach-path shape
        (missing / empty prompt text, citation length
        mismatch, etc.).

        The reason is logged (NEVER placed on the public
        context) so ops can diagnose without leaking detail
        to the Ask runtime.
        """
        logger.info(
            "Article RAG ask runtime adapter: malformed attach "
            "shape (reason=%s); returning fail-soft context",
            reason,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_RUNTIME_SHAPE_INVALID,
        )

    def _make_status_invalid_context(
        self,
        *,
        reason: str,
    ) -> ArticleRagAskRuntimeContext:
        """Fail-soft context for an attach-path status that is
        not ``"available"``."""
        logger.info(
            "Article RAG ask runtime adapter: attach-path status "
            "invalid (reason=%s); returning fail-soft context",
            reason,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_RUNTIME_STATUS_INVALID,
        )

    def _make_oversize_context(
        self,
        *,
        actual_chars: int,
        max_chars: int,
    ) -> ArticleRagAskRuntimeContext:
        """Fail-soft context for an oversized
        ``prompt_section_text``.

        The size info is logged (NEVER placed on the public
        context).
        """
        logger.info(
            "Article RAG ask runtime adapter: prompt_section_text "
            "exceeds max_runtime_chars (actual=%d, max=%d); "
            "returning fail-soft context (no truncation)",
            actual_chars,
            max_chars,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_RUNTIME_OVERSIZE,
        )

    def _build_fail_soft(
        self,
        *,
        status: str,
        failure_code: str,
    ) -> ArticleRagAskRuntimeContext:
        """Build a fail-soft context with empty content."""
        return ArticleRagAskRuntimeContext(
            kind="article_rag_context",
            should_attach=False,
            prompt_section_text="",
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
    "DEFAULT_MAX_RUNTIME_CHARS",
    "FAILURE_CODE_RUNTIME_UNEXPECTED_ERROR",
    "FAILURE_CODE_RUNTIME_OVERSIZE",
    "FAILURE_CODE_RUNTIME_SHAPE_INVALID",
    "FAILURE_CODE_RUNTIME_STATUS_INVALID",
    "ArticleRagAskRuntimeContext",
    "ArticleRagAskRuntimeAdapter",
]
