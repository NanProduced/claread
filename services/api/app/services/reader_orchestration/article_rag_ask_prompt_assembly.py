"""D6-I4M: Article RAG Ask Prompt Assembly Boundary.

Final pure service-layer transform: converts an
:class:`ArticleRagAskRuntimeContext` (D6-I4L) into a flat
:class:`ArticleRagAskPromptAssembly` value object that the
existing Ask prompt constructor can consume.

This module is the **PROMPT-ASSEMBLY** boundary — the LAST
transform before the Ask layer assembles the prompt.  The
assembly service:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — every failure (malformed context shape,
    unknown status, missing ``prompt_section_text`` on the
    attach path) maps to a fail-soft assembly with
    ``should_attach=False``;
  * never mutates ``context.prompt_section_text`` — the I4L
    runtime already wrapped the upstream composer output in
    fixed marker lines; the assembly service copies it
    verbatim into a prompt attachment block.  No re-parsing,
    no truncation, no citation extraction from the text;
  * never inlines citation JSON into the prompt attachment
    block — citations stay structured in a separate tuple;
  * never includes ``query_text`` — only ``query_sha256``;
  * never includes ``provider_metadata``, vector payload, or
    any Plate / Markdown / DOM / Slate / UI display group /
    render / text / html / chunks key — the ``metadata_json``
    field is a strict allowlist with a value-level guard;
  * never re-derives citation / text from anything —
    citations come from ``context.citations`` only.

Truth boundary
--------------

The assembly service is the LAST transformation layer before
the Ask layer.  It MUST NOT re-derive citations from the
prompt text, MUST NOT interpret projection fields as fact
sources, and MUST NOT trust a regression that surfaces a
hostile value on any upstream field.  Defence in depth:

  1. **Shape validation** — every context input is checked
     before any field is copied; a malformed shape fail-softs.
  2. **Runtime status allowlist** — the context's ``status``
     MUST be in the I4H 5-value Literal set; otherwise
     fail-soft to ``not_indexed_or_unavailable``.
  3. **Attach-path prompt presence** — the attach path
     requires non-empty ``prompt_section_text``; the no-attach
     path requires empty ``prompt_section_text``.
  4. **SHA-256 strict validation** — ``query_sha256`` MUST
     be a 64-char lowercase-hex string; raw queries /
     truncated / non-hex values are dropped to ``None``.
  5. **repr/str safety** — every field that may carry
     user-derived content is marked ``field(repr=False)`` so
     the default dataclass repr / str does NOT echo it.
  6. **metadata allowlist + value guard** — the metadata
     projection is a strict allowlist; every value is passed
     through the scalar / length / forbidden-substring
     guard used by I4J / I4K / I4L.

This is the **assembly** boundary — the contract here is
what the Ask prompt constructor is allowed to read.  Nothing
uncontrolled leaks through.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .article_rag_ask_runtime_adapter import (
    ArticleRagAskRuntimeContext,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default ``max_block_chars`` for the prompt attachment block.
# Mirrors the I4F / I4K / I4L default caps so behaviour is
# symmetric across the chain.
DEFAULT_MAX_BLOCK_CHARS = 4000

# Failure codes — stable, machine-readable.  Differs from I4L
# so dashboards can distinguish "the runtime adapter returned
# something unexpected" from "the assembly service itself
# caught an unexpected error".
FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR = (
    "article_rag_ask_prompt_assembly_unexpected_error"
)
FAILURE_CODE_ASSEMBLY_OVERSIZE = (
    "article_rag_ask_prompt_assembly_oversize"
)
FAILURE_CODE_ASSEMBLY_SHAPE_INVALID = (
    "article_rag_ask_prompt_assembly_shape_invalid"
)
FAILURE_CODE_ASSEMBLY_STATUS_INVALID = (
    "article_rag_ask_prompt_assembly_status_invalid"
)

# The set of statuses we recognise.  Mirrors I4H / I4J / I4K /
# I4L: the same 5 values.  Anything else (e.g. ``"paused"``,
# ``""``, ``"SECRET-..."``) is a contract violation — fail
# soft to ``not_indexed_or_unavailable``.  This is the LAST
# line of defence for status before the Ask prompt
# constructor reads it.
_ALLOWED_ASSEMBLY_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)

# The ``metadata_json`` allowlist.  Mirrors I4L — same
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
# Case-insensitive.  Mirrors I4F / I4J / I4K / I4L sets.
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

# SHA-256 hex digest length.
_SHA256_HEX_LEN = 64


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskPromptAssembly:
    """A deterministic Ask-prompt-consumable assembly.

    The Ask prompt constructor keys its prompt-attach policy
    on ``should_attach``:

      * ``True`` — ``prompt_attachment_block`` contains the
        I4L runtime's ``prompt_section_text`` verbatim.  The
        Ask layer embeds this block into the prompt verbatim;
        ``citations`` are rendered as a separate footnote /
        source list.
      * ``False`` — ``prompt_attachment_block`` is the empty
        string and ``citations`` is empty.  The Ask layer
        answers without RAG.  ``status`` / ``failure_code``
        are populated for ops visibility.

    ``kind`` is a single Literal value
    (``"article_rag_context"``) so the Ask prompt constructor
    can dispatch on it without importing assembly-specific
    code.

    ``metadata_json`` is a STRICT allowlist of the upstream
    context's safe fields.  Provider / query / vector /
    projection fields are NEVER surfaced.

    Every field that may carry user-derived content uses
    ``field(repr=False)`` so the assembly's default repr /
    str does NOT echo it.
    """

    # Discriminator for the Ask prompt constructor.
    kind: Literal["article_rag_context"]
    # Whether to attach the RAG block to the Ask prompt.
    should_attach: bool
    # The prompt attachment block.  On the attach path this
    # is ``context.prompt_section_text`` verbatim.  On the
    # no-attach path this is the empty string.
    #
    # ``repr=False``: carries chunk text / query fragments.
    prompt_attachment_block: str = field(repr=False)
    # Structured citations (verbatim from the I4L context,
    # which copied verbatim from the I4K section / I4G
    # composer / I4F pack / I4E plan).
    #
    # ``repr=False``: the citation dicts are plan-backed
    # content.
    citations: tuple[dict[str, Any], ...] = field(repr=False)
    # Stable context ids embedded in the prompt attachment
    # block.
    #
    # ``repr=False``.
    context_ids: tuple[str, ...] = field(repr=False)
    # Source identity hash from the I4G composer.  The Ask
    # prompt constructor can use this as a cache key for the
    # assembly attachment.
    #
    # ``repr=False``.
    source_pack_hash: str | None = field(repr=False)
    # SHA-256 of the query text, for traceability.  NEVER
    # the raw query text.  Strict 64-char lowercase-hex.
    #
    # ``repr=False``.
    query_sha256: str | None = field(repr=False)
    # Upstream status (propagated unchanged — guarded by
    # the 5-value allowlist).
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
    # no-attach path (the Ask layer can answer without RAG).
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
    """Same value guard as I4L."""
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


def _scrub_metadata(metadata: Any) -> dict[str, Any]:
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


def _assembly_status_ok(status: Any) -> bool:
    """Runtime guard on the context's ``status`` field.

    ``ArticleRagAskRuntimeContext.status`` is typed as a
    ``typing.Literal`` at compile time only.  A regression /
    hostile fake could surface an unrecognised string
    (``"paused"``, ``""``, ``"SECRET-..."``) at runtime.
    Anything outside
    :data:`_ALLOWED_ASSEMBLY_STATUSES` fail-softs to
    ``not_indexed_or_unavailable``.
    """
    if not isinstance(status, str):
        return False
    return status in _ALLOWED_ASSEMBLY_STATUSES


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagAskPromptAssemblyService:
    """Final layer: I4L context → Ask prompt assembly.

    Pure orchestrator.  No I/O.  The Ask prompt constructor
    calls :meth:`assemble` and gets a typed, never-raises
    :class:`ArticleRagAskPromptAssembly` value object.
    """

    def __init__(
        self,
        *,
        max_block_chars: int = DEFAULT_MAX_BLOCK_CHARS,
    ) -> None:
        if max_block_chars <= 0:
            raise ValueError(
                "ArticleRagAskPromptAssemblyService constructed with "
                f"max_block_chars={max_block_chars}; must be a "
                "positive integer"
            )
        self._max_block_chars = max_block_chars

    def assemble(
        self,
        context: ArticleRagAskRuntimeContext,
    ) -> ArticleRagAskPromptAssembly:
        """Build an :class:`ArticleRagAskPromptAssembly` from
        ``context``.

        Never raises.  Every failure maps to a fail-soft
        assembly with ``should_attach=False``.
        """
        # 1. Defensive shape check (wrong type).
        if not isinstance(context, ArticleRagAskRuntimeContext):
            return self._make_unexpected_assembly()

        # 2. Runtime status allowlist.
        if not _assembly_status_ok(context.status):
            return self._make_unexpected_assembly()

        # 3. The attach path.
        if context.should_attach:
            return self._build_attach_assembly(context)

        # 4. The no-attach path.
        return self._build_no_attach_assembly(context)

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_attach_assembly(
        self,
        context: ArticleRagAskRuntimeContext,
    ) -> ArticleRagAskPromptAssembly:
        """Build the attach-path assembly.

        The check here is the LAST line of defence before the
        Ask prompt constructor — any hostile / regressed
        upstream value is dropped / fail-softed.
        """
        # The attach path requires the include-path status.
        if context.status != "available":
            return self._make_status_invalid_assembly(
                reason="attach_path_status_must_be_available"
            )

        # prompt_section_text MUST be a non-empty string.
        prompt_text = context.prompt_section_text
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return self._make_shape_invalid_assembly(
                reason="missing_or_empty_prompt_section_text"
            )

        # citations / context_ids MUST be iterable sequences
        # with matching lengths.
        citations = context.citations
        context_ids = context.context_ids
        if not isinstance(citations, (tuple, list)) or not isinstance(
            context_ids, (tuple, list)
        ):
            return self._make_shape_invalid_assembly(
                reason="citations_or_context_ids_not_iterable"
            )
        if len(citations) != len(context_ids):
            return self._make_shape_invalid_assembly(
                reason="citations_context_ids_length_mismatch"
            )

        # Length cap.  Truncation would corrupt the marker
        # alignment with the citation list; fail-soft instead.
        if len(prompt_text) > self._max_block_chars:
            return self._make_oversize_assembly(
                actual_chars=len(prompt_text),
                max_chars=self._max_block_chars,
            )

        return ArticleRagAskPromptAssembly(
            kind="article_rag_context",
            should_attach=True,
            # The prompt attachment block is the I4L
            # ``prompt_section_text`` verbatim.  No mutation
            # / no re-parsing / no citation inlining.
            prompt_attachment_block=prompt_text,
            citations=tuple(citations),
            context_ids=tuple(context_ids),
            source_pack_hash=_scrub_top_level_string(
                context.source_pack_hash
            ),
            query_sha256=_scrub_sha256(context.query_sha256),
            status=context.status,
            failure_code=_scrub_top_level_string(
                context.failure_code
            ),
            retryable=bool(context.retryable),
            fallback_allowed=bool(context.fallback_allowed),
            metadata_json=_scrub_metadata(context.metadata_json),
        )

    def _build_no_attach_assembly(
        self,
        context: ArticleRagAskRuntimeContext,
    ) -> ArticleRagAskPromptAssembly:
        """Build the no-attach assembly.

        Empty block / empty citations / empty context_ids;
        diagnostic fields preserved with the same value scrub.
        """
        # The no-attach path requires empty text / citations /
        # context_ids and a non-``"available"`` status
        # (state-semantic consistency — the attach path owns
        # ``"available"``; the no-attach path owns the other 4).
        if not (
            isinstance(context.prompt_section_text, str)
            and context.prompt_section_text == ""
        ):
            return self._make_shape_invalid_assembly(
                reason="no_attach_path_prompt_section_text_must_be_empty"
            )
        if not (isinstance(context.citations, (tuple, list))
                and len(context.citations) == 0):
            return self._make_shape_invalid_assembly(
                reason="no_attach_path_citations_must_be_empty"
            )
        if not (isinstance(context.context_ids, (tuple, list))
                and len(context.context_ids) == 0):
            return self._make_shape_invalid_assembly(
                reason="no_attach_path_context_ids_must_be_empty"
            )
        if context.status == "available":
            return self._make_shape_invalid_assembly(
                reason="no_attach_path_status_must_not_be_available"
            )

        return ArticleRagAskPromptAssembly(
            kind="article_rag_context",
            should_attach=False,
            prompt_attachment_block="",
            citations=(),
            context_ids=(),
            source_pack_hash=_scrub_top_level_string(
                context.source_pack_hash
            ),
            query_sha256=_scrub_sha256(context.query_sha256),
            status=context.status,
            failure_code=_scrub_top_level_string(
                context.failure_code
            ),
            retryable=bool(context.retryable),
            fallback_allowed=bool(context.fallback_allowed),
            metadata_json=_scrub_metadata(context.metadata_json),
        )

    # ------------------------------------------------------------------
    # Fail-soft helpers
    # ------------------------------------------------------------------

    def _make_unexpected_assembly(self) -> ArticleRagAskPromptAssembly:
        """Generic fail-soft assembly (wrong-type shape or
        unrecognised status).
        """
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR,
        )

    def _make_shape_invalid_assembly(
        self,
        *,
        reason: str,
    ) -> ArticleRagAskPromptAssembly:
        """Fail-soft assembly for a malformed attach-path or
        no-attach-path shape.
        """
        logger.info(
            "Article RAG ask prompt assembly: malformed shape "
            "(reason=%s); returning fail-soft assembly",
            reason,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_ASSEMBLY_SHAPE_INVALID,
        )

    def _make_status_invalid_assembly(
        self,
        *,
        reason: str,
    ) -> ArticleRagAskPromptAssembly:
        """Fail-soft assembly for an attach-path status that
        is not ``"available"``.
        """
        logger.info(
            "Article RAG ask prompt assembly: attach-path status "
            "invalid (reason=%s); returning fail-soft assembly",
            reason,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_ASSEMBLY_STATUS_INVALID,
        )

    def _make_oversize_assembly(
        self,
        *,
        actual_chars: int,
        max_chars: int,
    ) -> ArticleRagAskPromptAssembly:
        """Fail-soft assembly for an oversized
        ``prompt_section_text``.
        """
        logger.info(
            "Article RAG ask prompt assembly: prompt_section_text "
            "exceeds max_block_chars (actual=%d, max=%d); "
            "returning fail-soft assembly (no truncation)",
            actual_chars,
            max_chars,
        )
        return self._build_fail_soft(
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_ASSEMBLY_OVERSIZE,
        )

    def _build_fail_soft(
        self,
        *,
        status: str,
        failure_code: str,
    ) -> ArticleRagAskPromptAssembly:
        """Build a fail-soft assembly with empty content."""
        return ArticleRagAskPromptAssembly(
            kind="article_rag_context",
            should_attach=False,
            prompt_attachment_block="",
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
    "DEFAULT_MAX_BLOCK_CHARS",
    "FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR",
    "FAILURE_CODE_ASSEMBLY_OVERSIZE",
    "FAILURE_CODE_ASSEMBLY_SHAPE_INVALID",
    "FAILURE_CODE_ASSEMBLY_STATUS_INVALID",
    "ArticleRagAskPromptAssembly",
    "ArticleRagAskPromptAssemblyService",
]
