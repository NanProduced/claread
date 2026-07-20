"""D6-I4J: Reader Ask RAG Attachment Integration Adapter.

Final integration layer that converts
:class:`ArticleRagAskPromptAttachment` (D6-I4I) into a deterministic
:class:`ArticleRagAskPromptSegment` for the Ask prompt / runtime
to consume.  The adapter:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — every failure (unexpected exception from the
    attachment service, malformed attachment object) maps to a
    fail-soft segment with ``include_in_prompt=False`` and
    ``status="not_indexed_or_unavailable"``;
  * never includes ``query_text`` — only ``query_sha256``;
  * never includes ``provider_metadata``, vector payload, or any
    Plate / Markdown / DOM / Slate / UI display group / render /
    text / html / chunks key — the ``metadata_json`` field is
    a strict allowlist of the safe diagnostic / stable-id keys;
  * never mutates ``prompt_text`` or re-parses it to extract
    citations — citations come from
    ``attachment.citations`` only (which I4I built verbatim from
    the I4G composer, which in turn built verbatim from the I4F
    context pack, which joined against the I4E retrieval plan).
    Postgres is the truth; Zilliz is the replica; the adapter
    adds no interpretation layer;
  * carries the upstream ``status`` / ``failure_code`` /
    ``retryable`` / ``fallback_allowed`` so the Ask layer can
    dispatch on the same vocabulary the resolver produced.

Truth boundary
--------------

This module is the LAST transform before the Ask prompt
constructor.  It does not re-derive citation / text from
anything; it copies them verbatim from the attachment
(which copied them verbatim from the bundle / pack / plan).
A regression in the upstream chain that surfaces a hostile
field on the attachment MUST be caught by this adapter's
``metadata_json`` allowlist — the prompt segment that the Ask
runtime consumes is a strict value object.

Security contract
-----------------

* ``query_text`` is never echoed in any field the Ask layer
  reads, in any log line, or in any error path.  Only
  ``query_sha256`` is surfaced.
* ``metadata_json`` is a fixed allowlist — provider_metadata,
  query_text, vector payload, and any UI projection field are
  explicitly EXCLUDED.  A regression that surfaces a hostile
  key on the attachment MUST NOT be amplified here.
* Unexpected exceptions are mapped to a fail-soft segment
  whose ``failure_code`` is the integration-specific
  ``article_rag_ask_integration_unexpected_error``; the cause
  object is NOT attached to the segment (frozen dataclass, no
  exception chain).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from .article_rag_ask_prompt_attachment import (
    ArticleRagAskPromptAttachment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Integration-specific failure code (the segment's own fail-soft
# path).  Differs from I4I's
# ``article_rag_prompt_attachment_unexpected_error`` so dashboards
# can distinguish "the attachment service returned something
# unexpected" from "the integration adapter itself caught an
# unexpected error".
FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR = (
    "article_rag_ask_integration_unexpected_error"
)

# The single kind the I4I attachment can carry.  Future kinds
# (e.g. a non-RAG source) would be added as additional
# Literal values; today the contract is intentionally narrow.
SEGMENT_KIND = "article_rag_context"

# Default ``limit`` / character budget / index version forwarded
# to the attachment service.  Mirrors the attachment service's
# own defaults; we re-export them here so callers of the
# adapter have a single import surface.
DEFAULT_INTEGRATION_LIMIT = 8
DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS = 4000

# The ``metadata_json`` allowlist.  Every key on the segment's
# ``metadata_json`` MUST be in this set; any other key on the
# upstream attachment is dropped silently.
#
# The set is intentionally small: only the keys the Ask runtime
# can use for prompt-construction decisions, cache keys, log
# dedup, and ops dashboards.  Anything else (provider
# diagnostics, query text, vector payload, projection fields)
# is excluded by design.
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

# The set of attachment statuses we recognise.  The I4H status
# is a ``typing.Literal`` (compile-time only); the I4I
# attachment service is supposed to forward the same 5 values
# through, but a regression in the attachment service (or a
# hostile fake in a test) could surface an unrecognised
# string on ``ArticleRagAskPromptAttachment.status`` (e.g.
# ``"paused"``, ``"failed"``, ``""``).  Anything outside this
# allowlist is treated as a malformed attachment and
# fail-softs to ``not_indexed_or_unavailable``.
_ALLOWED_ATTACHMENT_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskPromptSegment:
    """A deterministic Ask prompt segment carrying an I4I RAG
    attachment.

    The Ask runtime / prompt constructor keys its policy on
    ``include_in_prompt``:

      * ``True`` — the attachment carried a non-empty RAG
        context.  The Ask layer should embed ``prompt_text``
        into the prompt and render ``citations`` as a separate
        footnote / source list.  ``context_ids`` mirrors the
        order of the chunks embedded in ``prompt_text``; the
        runtime can use it to map back from LLM-cited
        ``[rag-N]`` markers to structured citation rows.
      * ``False`` — no RAG context is available.  ``prompt_text``
        is the empty string and ``citations`` is empty.  The
        Ask layer answers without RAG.  ``status`` /
        ``failure_code`` / ``retryable`` / ``fallback_allowed``
        are populated for ops visibility.

    ``kind`` is a single Literal value (``"article_rag_context"``)
    so the Ask runtime can dispatch on it without importing
    adapter-specific code.

    ``metadata_json`` is a STRICT allowlist of the upstream
    attachment's safe fields.  Provider / query / vector /
    projection fields are NEVER surfaced.

    The segment NEVER carries ``query_text`` — only
    ``query_sha256`` for traceability.
    """

    # Discriminator for the Ask runtime.
    kind: Literal["article_rag_context"]
    # Whether to embed ``prompt_text`` in the Ask prompt.
    include_in_prompt: bool
    # Verbatim from the attachment on the include path; empty
    # string on the no-context path.  The adapter MUST NOT
    # mutate this — it is exactly what the I4I attachment
    # service built from the I4G composer output.
    #
    # ``repr=False``: this field may carry user-derived content
    # (chunk text, query fragments echoed by the I4G composer)
    # and MUST NOT appear in the default dataclass repr / str.
    # The Ask runtime reads the field directly; ops / debug
    # surfaces that need the prompt text must do so
    # explicitly.  Without this, ``repr(segment)`` would echo
    # the chunk text / query fragments into logs.
    prompt_text: str = field(repr=False)
    # Structured citations (I4I built these from
    # ``bundle.citations``; the adapter copies them verbatim).
    # Empty on the no-context path.
    #
    # ``repr=False``: the citation dicts are plan-backed
    # PostgreSQL content; they MUST NOT appear in the default
    # dataclass repr.  Reading is still via the field
    # directly.
    citations: tuple[dict[str, Any], ...] = field(repr=False)
    # Stable context ids embedded in ``prompt_text``.
    # Empty on the no-context path.
    #
    # ``repr=False``: low-risk (just identifiers like ``"rag-1"``)
    # but kept off repr so the segment's default str output
    # does not carry chunk references by default.
    context_ids: tuple[str, ...] = field(repr=False)
    # Source identity hash from the I4G composer.  The Ask
    # runtime can use this as a cache key for the prompt block.
    source_pack_hash: str | None = field(repr=False)
    # SHA-256 of the query text, for traceability.  NEVER the
    # raw query text.
    query_sha256: str | None = field(repr=False)
    # Upstream status (propagated unchanged on the
    # no-context path; ``"available"`` on the include path).
    # The Ask runtime can use it for fallback dispatch.
    #
    # ``repr=False``: a regression / hostile fake in the
    # upstream chain could surface a secret-bearing status
    # (e.g. ``"SECRET-..."``); the runtime status guard (see
    # ``_attachment_status_ok``) fail-softs to
    # ``not_indexed_or_unavailable`` for unrecognised values,
    # but ``field(repr=False)`` is the backstop in case a
    # future change accidentally bypasses the guard.
    status: str = field(repr=False)
    # Upstream failure code (propagated unchanged on the
    # no-context path; ``None`` on the include path).
    failure_code: str | None = field(repr=False)
    # Upstream retryable flag.
    retryable: bool
    # Upstream fallback-allowed flag.  Always ``True`` on the
    # no-context path (the Ask layer can answer without RAG).
    fallback_allowed: bool
    # Strict-allowlist metadata.  See module docstring.
    #
    # ``repr=False``: even though ``metadata_json`` is a
    # strict-allowlist projection, an ops debug surface should
    # be explicit about reading it — the segment's default
    # repr should NOT carry the metadata dict.
    metadata_json: dict[str, Any] = field(
        default_factory=dict, repr=False
    )


# ---------------------------------------------------------------------------
# Dependency protocol
# ---------------------------------------------------------------------------


class _AttachmentServiceLike(Protocol):
    """Minimal shape :class:`ArticleRagAskPromptAttachmentService`
    exposes."""

    async def build_for_ask(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = ...,
        limit: int = ...,
        max_context_chars: int = ...,
    ) -> ArticleRagAskPromptAttachment: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Substrings we refuse to surface even on allowlisted values.
# We check case-insensitively because a regression like
# ``"Token=ABC"`` or ``"SECRET-..."`` is exactly the class of
# leak we are defending against.  The list mirrors the I4F
# provider-metadata substring set so the value policy is
# consistent across the chain.
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
# almost certainly a regression (a "status" value should not
# be 1000 chars).
_MAX_METADATA_VALUE_LEN = 256

# Numeric / boolean values are always safe.  ``None`` is also
# safe (it explicitly signals "absent").  Lists / dicts / tuples
# are rejected — a regression that surfaces a complex value on
# the metadata path is suspicious.


def _safe_metadata_value(value: Any) -> Any:
    """Return ``value`` if it is a safe scalar; otherwise ``None``.

    Safe scalar:
      * ``str`` — at most :data:`_MAX_METADATA_VALUE_LEN` chars
        AND not containing any forbidden substring (case-
        insensitive);
      * ``int`` / ``float`` / ``bool`` — always safe;
      * ``uuid.UUID`` — always safe; we stringify so the
        segment's metadata_json is JSON-serialisable (a bare
        ``UUID`` instance would fail ``json.dumps``).  This
        matters because ``stable_document_id`` / ``base_id``
        on the upstream attachment are ``UUID | None``;
      * ``None`` — always safe (explicitly signals "absent");
      * anything else (list, dict, tuple, bytes, custom object)
        — treated as unsafe and dropped.
    """
    # ``bool`` is a subclass of ``int`` in Python, so we must
    # check ``bool`` BEFORE ``int`` to avoid accidentally
    # accepting booleans where an integer is expected.
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
    # UUID is a scalar but not a primitive.  We accept and
    # stringify so the metadata_json is JSON-serialisable.
    try:
        import uuid as _uuid

        if isinstance(value, _uuid.UUID):
            return str(value)
    except ImportError:  # pragma: no cover — stdlib always available
        pass
    return None


def _scrub_metadata(
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy of ``metadata`` with only the allowlisted
    keys AND allowlisted value shapes.

    Two layers of defence:

      1. **Key allowlist** (12 keys) — anything outside the
         allowlist is dropped silently.  This protects against
         a regression that surfaces a hostile field name
         (``provider_metadata`` / ``query_text`` / projection
         keys) on the metadata path.
      2. **Value guard** (:func:`_safe_metadata_value`) — every
         allowlisted value is filtered: non-scalar values are
         dropped; ``str`` values that exceed the length cap OR
         contain any forbidden substring (e.g. ``"token="``,
         ``"secret"``, ``"query="``) are dropped.  This
         protects against a regression that puts a
         credential / query fragment on an allowlisted key
         (e.g. ``failure_code="SECRET-..."`` or
         ``source_pack_hash="token=ABC"``).

    The integration adapter is the LAST transform before the
    Ask runtime, so the metadata that survives this filter is
    the only metadata the Ask layer can ever read.
    """
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, raw_value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        safe_value = _safe_metadata_value(raw_value)
        if safe_value is None and raw_value is not None:
            # The value failed the value-guard (e.g. a
            # ``str`` containing a forbidden substring, a
            # ``list`` / ``dict``, an overly long string).
            # Drop the entire entry.  Note: we keep entries
            # whose value is ``None`` (explicitly absent);
            # only UNSAFE values are dropped.
            continue
        safe[str(key)] = safe_value
    return safe


def _metadata_from_attachment(
    attachment: ArticleRagAskPromptAttachment,
) -> dict[str, Any]:
    """Project the attachment onto the
    ``ArticleRagAskPromptSegment.metadata_json`` allowlist.

    Only the 12 safe fields listed in ``_ALLOWED_METADATA_KEYS``
    are passed through.  ``query_text``, ``provider_metadata``,
    vector payload, and any UI projection key are NEVER
    included.
    """
    raw: dict[str, Any] = {
        "status": attachment.status,
        "failure_code": attachment.failure_code,
        "retryable": bool(attachment.retryable),
        "fallback_allowed": bool(attachment.fallback_allowed),
        "omitted_hit_count": attachment.omitted_hit_count,
        "budget_exceeded": attachment.budget_exceeded,
        "stable_document_id": attachment.stable_document_id,
        "base_id": attachment.base_id,
        "record_generation": attachment.record_generation,
        "plan_content_sha256": attachment.plan_content_sha256,
        "source_pack_hash": attachment.source_pack_hash,
    }
    return _scrub_metadata(raw)


def _scrub_top_level_string(value: Any) -> str | None:
    """Scrub a top-level string field (``source_pack_hash`` /
    ``failure_code``) using the same value policy as
    :func:`_safe_metadata_value`.

    A non-scalar value, a non-string value, an over-cap
    string, or a string containing any forbidden substring is
    dropped (replaced with ``None``) so it cannot surface on
    the segment.  ``None`` is preserved (it signals
    "absent" / "unset").
    """
    if value is None:
        return None
    safe_value = _safe_metadata_value(value)
    if safe_value is None:
        # ``value`` was unsafe.  Drop.
        return None
    # ``_safe_metadata_value`` accepts non-string scalars; for a
    # top-level string field we stringify non-strings defensively
    # (the only realistic case is ``bool`` → ``"True"``/``"False"``
    # which we don't want here either — but we keep the
    # defence: if it's not a str, drop).
    if not isinstance(safe_value, str):
        return None
    return safe_value


def _attachment_status_ok(status: Any) -> bool:
    """Runtime guard on the attachment's ``status`` field.

    ``ArticleRagAskPromptAttachment.status`` is typed
    ``ArticleRagAskContextResolveStatus`` (a ``typing.Literal``)
    at compile time, but Python does not enforce the Literal
    at runtime — a regression in the attachment service (or
    a hostile fake in a test) could surface an unrecognised
    string (e.g. ``"paused"``, ``"failed"``, ``""``).

    The Ask runtime keys its fallback policy on the status
    literal; an unknown value would silently break the
    dispatch contract.  This guard returns ``False`` for any
    status outside the 5-value allowlist
    (:data:`_ALLOWED_ATTACHMENT_STATUSES`); the adapter
    fail-softs to ``not_indexed_or_unavailable`` in that
    case.

    ``status`` MUST be a string (we do not accept non-string
    values — they are also considered malformed).
    """
    if not isinstance(status, str):
        return False
    return status in _ALLOWED_ATTACHMENT_STATUSES


def _include_path_shape_ok(
    attachment: ArticleRagAskPromptAttachment,
) -> bool:
    """Defensive shape check on the include path.

    The I4I attachment service's invariant is:
    ``should_include_context=True`` ⇒
    ``status == "available"`` AND
    ``isinstance(prompt_context_text, str) and prompt_context_text`` non-empty
    AND ``isinstance(citations, tuple)`` AND
    ``isinstance(context_ids, tuple)``.

    A regression in the attachment service (or a hostile fake)
    could surface ``should_include_context=True`` with a
    mismatch.  This helper checks the shape; the adapter
    fail-softs when it returns ``False``.

    We accept any tuple / list / ``None``-ish sequence for
    ``citations`` / ``context_ids`` so that test fakes that
    bypass the dataclass (and return a list) are still
    tolerated.  The adapter's own contract requires tuple,
    but the shape check is intentionally permissive to
    minimise the surface area for spurious fail-soft paths.
    """
    # Status MUST be the include-path status.
    if attachment.status != "available":
        return False
    # prompt_context_text MUST be a non-empty str.
    prompt = attachment.prompt_context_text
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    # citations / context_ids MUST be iterable sequences (we
    # normalise to tuple at the assignment site).
    if not isinstance(attachment.citations, (tuple, list)):
        return False
    if not isinstance(attachment.context_ids, (tuple, list)):
        return False
    return True


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ArticleRagAskIntegrationAdapter:
    """Final integration layer: I4I attachment → Ask prompt segment.

    Pure orchestrator.  No I/O beyond what the injected
    attachment service does.  The Ask runtime calls
    :meth:`build_prompt_segment` and gets a typed, never-raises
    :class:`ArticleRagAskPromptSegment` value object.
    """

    def __init__(
        self,
        *,
        attachment_service: _AttachmentServiceLike | None = None,
    ) -> None:
        # Lazy default: we refuse to silently pick a fake / an
        # unconfigured attachment service.  Tests inject fakes;
        # production code injects the real attachment service.
        self._attachment_service = attachment_service

    async def build_prompt_segment(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_INTEGRATION_LIMIT,
        max_context_chars: int = DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskPromptSegment:
        """Build a deterministic Ask prompt segment.

        Never raises.  Every failure (including a misconfigured
        attachment service, an unexpected exception, or a
        malformed attachment object) maps to a fail-soft
        segment with ``include_in_prompt=False``.
        """
        # 1. Validate injected dependency.
        if self._attachment_service is None:
            return self._make_unexpected_segment(
                reading_record_id=reading_record_id,
            )

        # 2. Delegate to the attachment service.  The attachment
        #    service itself is already designed to never raise
        #    (every failure is mapped to a typed
        #    ``ArticleRagAskPromptAttachment``) — but a
        #    regression in the attachment service (or a bug in
        #    this adapter's wiring) could surface an unexpected
        #    exception.  We catch defensively.
        try:
            attachment = await self._attachment_service.build_for_ask(
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query_text,
                enabled=enabled,
                limit=limit,
                max_context_chars=max_context_chars,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            # Unexpected attachment service exception — the
            # cause class name is logged for ops dashboards; the
            # cause object is NOT attached to the public
            # segment.
            logger.info(
                "Article RAG ask integration adapter: attachment "
                "service raised %s (unexpected) for record=%s; "
                "returning fail-soft segment",
                type(exc).__name__,
                reading_record_id,
            )
            return self._make_unexpected_segment(
                reading_record_id=reading_record_id,
            )

        # 3. Defensive shape check: a regression / hostile fake
        #    could return a non-dataclass object.  Fail-soft.
        if not isinstance(attachment, ArticleRagAskPromptAttachment):
            return self._make_unexpected_segment(
                reading_record_id=reading_record_id,
            )

        # 4. The include path: the attachment says we have
        #    non-empty RAG context.  We additionally validate the
        #    shape — ``should_include_context=True`` is the I4I
        #    service's promise, but a regression in the I4I
        #    attachment (or a hostile fake) could surface
        #    ``should_include_context=True`` with
        #    ``status="disabled"`` / empty ``prompt_context_text``
        #    / non-tuple ``citations``.  We defend here: any
        #    shape mismatch fail-softs to
        #    ``not_indexed_or_unavailable`` so the Ask layer's
        #    default branch still works.
        if attachment.should_include_context:
            if not _include_path_shape_ok(attachment):
                return self._make_unexpected_segment(
                    reading_record_id=reading_record_id,
                )
            return ArticleRagAskPromptSegment(
                kind=SEGMENT_KIND,
                include_in_prompt=True,
                # Verbatim from the attachment — the adapter
                # MUST NOT mutate or re-parse this.
                prompt_text=attachment.prompt_context_text,
                citations=tuple(attachment.citations),
                context_ids=tuple(attachment.context_ids),
                # Top-level value scrub: a regression in the
                # upstream chain could surface a secret-bearing
                # value here (e.g. ``"token=ABC"`` on
                # ``source_pack_hash``).  The value guard drops
                # such values; ``field(repr=False)`` is the
                # backstop.
                source_pack_hash=_scrub_top_level_string(
                    attachment.source_pack_hash
                ),
                query_sha256=_safe_metadata_value(
                    attachment.query_sha256
                ),
                status=attachment.status,
                failure_code=_scrub_top_level_string(
                    attachment.failure_code
                ),
                retryable=bool(attachment.retryable),
                fallback_allowed=bool(attachment.fallback_allowed),
                metadata_json=_metadata_from_attachment(attachment),
            )

        # 5. The no-context path: the attachment is well-formed
        #    but ``should_include_context`` is False.  Copy the
        #    status / failure_code / retryable / fallback_allowed
        #    / query_sha256 from the attachment; the prompt /
        #    citations / context_ids / source_pack_hash are
        #    empty.
        #
        #    Runtime status guard: an unrecognised status string
        #    on the attachment (e.g. ``"paused"``, ``""``,
        #    ``"SECRET-..."``) is a contract violation — fail
        #    soft to ``not_indexed_or_unavailable`` so the Ask
        #    runtime's default branch still works.
        if not _attachment_status_ok(attachment.status):
            return self._make_unexpected_segment(
                reading_record_id=reading_record_id,
            )
        return ArticleRagAskPromptSegment(
            kind=SEGMENT_KIND,
            include_in_prompt=False,
            prompt_text="",
            citations=(),
            context_ids=(),
            # Top-level value scrub on the no-context path too.
            source_pack_hash=_scrub_top_level_string(
                attachment.source_pack_hash
            ),
            query_sha256=_safe_metadata_value(
                attachment.query_sha256
            ),
            status=attachment.status,
            failure_code=_scrub_top_level_string(
                attachment.failure_code
            ),
            retryable=bool(attachment.retryable),
            fallback_allowed=bool(attachment.fallback_allowed),
            metadata_json=_metadata_from_attachment(attachment),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_unexpected_segment(
        *,
        reading_record_id: UUID,
    ) -> ArticleRagAskPromptSegment:
        """Build a fail-soft segment for the unexpected-error
        path.

        Every field is set to its default / None / empty.  The
        failure_code is the integration-specific
        ``FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR``.  This
        helper exists so the unexpected path is symmetric across
        the three call sites (no attachment service /
        attachment service raised / malformed shape).
        """
        return ArticleRagAskPromptSegment(
            kind=SEGMENT_KIND,
            include_in_prompt=False,
            prompt_text="",
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            query_sha256=None,
            status="not_indexed_or_unavailable",
            failure_code=FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR,
            retryable=False,
            fallback_allowed=True,
            metadata_json={
                "status": "not_indexed_or_unavailable",
                "failure_code": FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR,
                "retryable": False,
                "fallback_allowed": True,
            },
        )


__all__ = [
    "DEFAULT_INTEGRATION_LIMIT",
    "DEFAULT_INTEGRATION_MAX_CONTEXT_CHARS",
    "FAILURE_CODE_INTEGRATION_UNEXPECTED_ERROR",
    "SEGMENT_KIND",
    "ArticleRagAskPromptSegment",
    "ArticleRagAskIntegrationAdapter",
]