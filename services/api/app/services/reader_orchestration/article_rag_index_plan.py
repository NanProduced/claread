"""D6-I4A: Reader Article RAG Index Plan Foundation.

Pure, read-only service that builds a deterministic index plan from the
Reader truth layer (stable_reading_documents / stable_document_blocks /
reading_bases / reading_units / anchor_segments / reading_records).

The plan is a list of ``ArticleRagIndexChunk`` objects, each carrying a
``ArticleRagCitationRef`` that anchors the chunk to canonical truth
(record / document / base / generation / block / unit / segment /
canonical UTF-16 offsets). No Plate JSON, Slate path, DOM selection,
or Markdown syntax offset is used.

V1 chunking: one chunk per eligible block.  Future versions may merge
contiguous main_reading blocks or split long blocks by reading_units /
anchor_segments, but V1 keeps the contract simple and deterministic.

This service does NOT:
  * write to the database
  * call any embedding provider
  * call Zilliz / Milvus
  * modify Ask, frontend, Plate projection, or the input pipeline
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg

from app.contracts.annotation import slice_by_utf16_offsets, utf16_code_unit_length
from app.schemas.reader_documents import default_interpretation_policy_for
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)

from .article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfile,
    ArticleRagIndexProfileResolutionError,
    resolve_article_rag_index_profile,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNKER_VERSION = "article_rag_index_plan_v1"

# P1-E: the single plan identity supported by this plan service.  V1
# requires BOTH ``profile.plan_version`` and ``profile.chunker_version``
# to equal this string; any other identity fails closed without
# silently falling through to the V1 builder.  No V2 identity is
# registered here.
# P1-E-R1: single source of truth — derive from ``CHUNKER_VERSION``
# rather than maintaining a duplicate literal.  ``CHUNKER_VERSION`` is
# the public canonical identity constant; its byte value is unchanged.
_SUPPORTED_PLAN_IDENTITY = CHUNKER_VERSION

# P1-E: fixed local error messages used by the version-aware dispatch
# wrapper.  These strings never interpolate the caller-supplied
# ``index_version`` or any other input — the offending value is never
# echoed in ``str``, ``repr``, ``args``, or traceback.
_P1E_MSG_PROFILE_NOT_RESOLVED = (
    "Article RAG index plan version is not supported"
)
_P1E_MSG_PLAN_IDENTITY_UNSUPPORTED = (
    "Article RAG index plan version is not supported"
)

_CANONICAL_SEPARATOR_UTF16 = 2  # "\n\n" = 2 UTF-16 code units

# Routes that never produce RAG chunks.
_EXCLUDED_ROUTES = frozenset({"metadata_only", "ignored"})

# Default interpretation-policy field values when the JSONB column is a
# NON-EMPTY dict but is missing individual keys.  These mirror the
# StableDocumentInterpretationPolicy Pydantic model defaults.  They are
# NOT used for the empty ``{}`` storage placeholder — that case is
# handled by ``default_interpretation_policy_for(block_type)`` to avoid
# silently routing tables/images/unknown blocks into main_reading.
_DEFAULT_DEFAULT_ROUTE = "main_reading"
_DEFAULT_RAG_ELIGIBLE = True
_DEFAULT_ALLOWED_SOURCE_SCOPE = ("main_reading_text",)


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ArticleRagIndexPlanError(ValueError):
    """Raised when the article RAG index plan cannot be built safely."""


# ---------------------------------------------------------------------------
# Data models (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagCitationRef:
    """Citation truth anchoring a RAG chunk to canonical facts.

    No Plate / Slate / DOM / Markdown fields appear here — only canonical
    truth fields that are stable across UI/projection changes.
    """

    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    block_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    anchor_segment_ids: tuple[str, ...]
    canonical_text_start_utf16: int | None
    canonical_text_end_utf16: int | None


@dataclass(frozen=True, slots=True)
class ArticleRagIndexChunk:
    """One indexable chunk produced by the plan."""

    chunk_id: str
    citation: ArticleRagCitationRef
    source_scope: str
    text: str
    content_sha256: str
    embedding_text_sha256: str
    metadata_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArticleRagIndexPlan:
    """The full deterministic index plan for one stable reading document."""

    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    content_sha256: str
    canonical_text_sha256: str
    chunker_version: str
    chunks: tuple[ArticleRagIndexChunk, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def compute_plan_content_sha256(plan: ArticleRagIndexPlan) -> str:
    """Deterministic SHA-256 of the full plan content.

    Captures every field that determines the index content:
      * stable_document_id, base_id, record_generation
      * content_sha256 (stable document hash)
      * canonical_text_sha256 (reading base text hash)
      * chunker_version
      * chunk count
      * per-chunk: chunk_id, content_sha256, embedding_text_sha256,
        source_scope, citation block_ids / unit_ids / anchor_segment_ids,
        canonical UTF-16 offsets

    If any of these change, the hash changes.  This lets the bootstrap
    service detect idempotent re-runs (same hash) vs. content drift
    (different hash) without storing chunk text or Plate / Slate / DOM
    projections.

    The hash never includes ``metadata_json`` directly — metadata is
    derived from the same truth fields already captured above
    (block_type, source_scope, default_route, chunk_index,
    has_canonical_offsets), so a metadata change always reflects an
    underlying truth change that this hash already captures.
    """
    parts: list[str] = [
        str(plan.stable_document_id),
        str(plan.base_id),
        str(plan.record_generation),
        plan.content_sha256,
        plan.canonical_text_sha256,
        plan.chunker_version,
        str(len(plan.chunks)),
    ]
    for chunk in plan.chunks:
        c = chunk.citation
        parts.extend(
            [
                chunk.chunk_id,
                chunk.content_sha256,
                chunk.embedding_text_sha256,
                chunk.source_scope,
                ",".join(c.block_ids),
                ",".join(c.unit_ids),
                ",".join(c.anchor_segment_ids),
                str(c.canonical_text_start_utf16),
                str(c.canonical_text_end_utf16),
            ]
        )
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_json_object(raw: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ArticleRagIndexPlanError(
                f"{field_name} is not valid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ArticleRagIndexPlanError(
                f"{field_name} parses to a non-object JSON value"
            )
        return dict(parsed)
    raise ArticleRagIndexPlanError(f"{field_name} must be a JSON object")


def _interpretation_policy_fields(
    raw: dict[str, Any],
    *,
    block_type: str,
) -> tuple[str, tuple[str, ...], bool]:
    """Extract (default_route, allowed_source_scope, rag_eligible) from
    ``interpretation_policy_json``.

    An empty dict ``{}`` is the DB storage placeholder.  It MUST be
    materialised via ``default_interpretation_policy_for(block_type)``
    to avoid silently routing tables/images/unknown blocks into
    main_reading — mirroring the
    ``StableDocumentBlock._apply_block_type_default_policy`` validator.

    For a NON-EMPTY dict, missing individual keys fall back to the same
    per-field defaults as ``StableDocumentInterpretationPolicy``.
    """
    if not raw:
        policy = default_interpretation_policy_for(block_type)  # type: ignore[arg-type]
        return (
            policy.default_route,
            tuple(policy.allowed_source_scope),
            policy.rag_eligible,
        )

    route = str(raw.get("default_route", _DEFAULT_DEFAULT_ROUTE))
    scope_raw = raw.get("allowed_source_scope", _DEFAULT_ALLOWED_SOURCE_SCOPE)
    if isinstance(scope_raw, list):
        scope = tuple(str(s) for s in scope_raw)
    elif isinstance(scope_raw, str):
        scope = (scope_raw,)
    else:
        scope = _DEFAULT_ALLOWED_SOURCE_SCOPE
    if not scope:
        scope = _DEFAULT_ALLOWED_SOURCE_SCOPE
    rag_eligible = bool(raw.get("rag_eligible", _DEFAULT_RAG_ELIGIBLE))
    return route, scope, rag_eligible


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _deterministic_chunk_id(
    *,
    stable_document_id: UUID,
    block_id: str,
    source_scope: str,
    canonical_start: int | None,
    canonical_end: int | None,
) -> str:
    """Deterministic chunk identifier (16 hex chars).

    The id is stable across rebuilds as long as the stable document,
    block, scope, and canonical offsets don't change.
    """
    start_str = "" if canonical_start is None else str(canonical_start)
    end_str = "" if canonical_end is None else str(canonical_end)
    raw = (
        f"{stable_document_id}"
        f":{block_id}"
        f":{source_scope}"
        f":{start_str}"
        f":{end_str}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _prepare_embedding_text(text: str) -> str:
    """V1: embedding text is the raw text (no transformation).

    Future versions may prefix with title, truncate, or normalise.
    Keeping this as a separate function ensures ``embedding_text_sha256``
    can diverge from ``content_sha256`` without changing call sites.
    """
    return text


def _build_metadata(
    *,
    block_type: str,
    block_order_index: int,
    source_scope: str,
    default_route: str,
    chunk_index: int,
    has_canonical_offsets: bool,
) -> dict[str, Any]:
    """Build deterministic per-chunk metadata.

    Keys are inserted in a fixed order so the dict is reproducible
    regardless of how callers inspect it.
    """
    return {
        "block_type": block_type,
        "block_order_index": block_order_index,
        "source_scope": source_scope,
        "default_route": default_route,
        "chunk_index": chunk_index,
        "has_canonical_offsets": has_canonical_offsets,
    }


def _validate_canonical_offsets(
    *,
    block_id: str,
    text_content: str,
    canonical_start: int,
    canonical_end: int,
    base_text: str,
    base_utf16_length: int,
) -> None:
    """Fail-closed validation of canonical offsets for main_reading blocks.

    RAG citation is truth layer.  Offsets must satisfy:
      1. ``0 <= start < end <= content_utf16_length`` (bounds)
      2. ``end - start == utf16_code_unit_length(text_content)`` (span)
      3. ``slice_by_utf16_offsets(base_text, start, end) == text_content``
         (slice content matches)

    Any mismatch indicates a data inconsistency between
    ``stable_document_blocks`` and ``reading_bases.text``.
    """
    # 1. Bounds check.
    if not (0 <= canonical_start < canonical_end <= base_utf16_length):
        raise ArticleRagIndexPlanError(
            f"Block {block_id} canonical offsets "
            f"({canonical_start}, {canonical_end}) are out of bounds for "
            f"base text UTF-16 length {base_utf16_length}."
        )

    # 2. Span length check.
    text_utf16_length = utf16_code_unit_length(text_content)
    span_length = canonical_end - canonical_start
    if span_length != text_utf16_length:
        raise ArticleRagIndexPlanError(
            f"Block {block_id} canonical span length ({span_length}) does "
            f"not match text_content UTF-16 length ({text_utf16_length})."
        )

    # 3. Slice content check.
    sliced = slice_by_utf16_offsets(base_text, canonical_start, canonical_end)
    if sliced is None or sliced != text_content:
        raise ArticleRagIndexPlanError(
            f"Block {block_id} canonical offset slice does not match "
            f"text_content."
        )


# ---------------------------------------------------------------------------
# Block / unit / segment row dataclasses (internal)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BlockRow:
    block_id: str
    order_index: int
    block_type: str
    text_content: str | None
    canonical_text_start_utf16: int | None
    canonical_text_end_utf16: int | None
    interpretation_policy: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _UnitRow:
    unit_id: str
    order_index: int
    base_start_utf16: int
    base_end_utf16: int


@dataclass(frozen=True, slots=True)
class _SegmentRow:
    anchor_segment_id: str
    unit_id: str
    order_index: int
    base_start_utf16: int
    base_end_utf16: int


# ---------------------------------------------------------------------------
# P1-E: version-aware plan dispatch seam (V1 only)
# ---------------------------------------------------------------------------


def _resolve_v1_plan_profile(index_version: str) -> ArticleRagIndexProfile:
    """Resolve ``index_version`` to the supported V1 plan profile.

    This is the single, runtime-immutable dispatch seam that maps an
    ``index_version`` string to a deterministic plan implementation
    identity.  It is the ONLY entry point the plan service uses to
    translate a caller-supplied ``index_version`` into a concrete
    ``chunker_version``; callers MUST NOT pass their own
    chunker/model/namespace strings to the plan builder.

    The seam delegates profile resolution to
    :func:`resolve_article_rag_index_profile` (the P1-B registry) and
    then verifies that the resolved profile's ``plan_version`` AND
    ``chunker_version`` both equal :data:`_SUPPORTED_PLAN_IDENTITY`.
    A future V2 profile registered in the P1-B registry will fail
    closed here rather than silently flowing into the V1 builder.

    Exception-chain closure: ``ArticleRagIndexProfileResolutionError``
    is caught, a fixed-safe :class:`ArticleRagIndexPlanError` is
    constructed INSIDE the except block, and the new error is raised
    OUTSIDE the except block.  This guarantees
    ``err.__cause__ is None`` and ``err.__context__ is None``; no
    ``raise ... from exc`` is used and the original exception's
    message / type / repr / args are never copied or re-emitted.

    Args:
        index_version: The index version string to resolve.  The
            offending value is NEVER echoed in any error surface.

    Returns:
        The frozen :class:`ArticleRagIndexProfile` for V1.  Its
        ``chunker_version`` is the canonical source for
        ``plan.chunker_version``.

    Raises:
        ArticleRagIndexPlanError: If the profile cannot be resolved,
            or if the resolved profile's plan / chunker identity is
            not the supported V1 identity.  The error message is a
            fixed local string; the offending input is never echoed.
    """
    resolution_error: ArticleRagIndexPlanError | None = None
    try:
        resolution = resolve_article_rag_index_profile(index_version)
    except ArticleRagIndexProfileResolutionError:
        # Construct the fixed-safe wrapper error INSIDE the except
        # block.  Do NOT use ``raise ... from exc`` — that would set
        # ``__cause__``.  Do NOT raise here — that would set
        # ``__context__`` implicitly.  Defer the raise to outside the
        # except block so both chain attributes remain None.
        resolution_error = ArticleRagIndexPlanError(
            _P1E_MSG_PROFILE_NOT_RESOLVED
        )

    if resolution_error is not None:
        # Raised outside the except block: __cause__ is None,
        # __context__ is None.
        raise resolution_error

    profile = resolution.profile
    # Forward-compatibility guard: a future V2 profile registered in
    # the P1-B registry must NOT silently flow into the V1 builder.
    # Unknown / unsupported plan/chunker identity fails closed with a
    # fixed local message; the offending identity is never echoed.
    if (
        profile.plan_version != _SUPPORTED_PLAN_IDENTITY
        or profile.chunker_version != _SUPPORTED_PLAN_IDENTITY
    ):
        raise ArticleRagIndexPlanError(_P1E_MSG_PLAN_IDENTITY_UNSUPPORTED)
    return profile


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagIndexPlanService:
    """Build a deterministic, read-only Article RAG index plan.

    The service is pure / read-only: it never writes to the database,
    never calls embedding providers, and never calls Zilliz / Milvus.
    """

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return ReaderOrchestrationRepository().get_pool()

    async def build_index_plan(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
        index_version: str = DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ) -> ArticleRagIndexPlan:
        """Build the index plan for the active stable document of ``record_id``.

        Parameters
        ----------
        record_id
            The reading record to index.
        user_id
            The requesting user (ownership check).
        include_rag_ask_only
            When ``False`` (default) only ``main_reading`` blocks are
            indexed.  When ``True`` also includes ``rag_ask_only``
            blocks (e.g. table_cell, image_ocr, footnote, code_block).
        index_version
            P1-E-R1: Article RAG index version string used to resolve
            the plan / chunker identity through
            :func:`resolve_article_rag_index_profile`.  Omitting the
            parameter defaults to
            :data:`DEFAULT_ARTICLE_RAG_INDEX_VERSION` so the V1
            default behaviour is preserved.  Explicit ``None`` is NOT
            a valid input: it flows directly to the resolver, which
            rejects non-string values via
            :class:`ArticleRagIndexPlanError` (fail-closed).  Bootstrap
            and worker callers MUST pass their already-frozen /
            validated ``index_version`` explicitly.  Unknown / blank /
            whitespace-padded / non-string / malicious values fail
            closed via :class:`ArticleRagIndexPlanError`; the
            offending input is never echoed.

        Raises
        ------
        LookupError
            If the record does not exist or does not belong to ``user_id``.
        ArticleRagIndexPlanError
            If the profile cannot be resolved, the resolved plan /
            chunker identity is not the supported V1 identity, the
            stable document / base is stale, inactive, mismatched,
            or no eligible blocks produce chunks.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            return await self.build_index_plan_in_transaction(
                conn,
                record_id=record_id,
                user_id=user_id,
                include_rag_ask_only=include_rag_ask_only,
                index_version=index_version,
            )

    async def build_index_plan_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
        index_version: str = DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ) -> ArticleRagIndexPlan:
        """Caller-managed-connection variant of :meth:`build_index_plan`.

        Reads the truth layer using ``conn`` and builds a deterministic
        index plan.  When a transaction is active on ``conn`` the reads
        participate in it, giving the caller a consistent snapshot for
        follow-up writes (e.g. inserting ``reader_article_rag_index_runs``).

        This method does NOT check ``conn.is_in_transaction()`` — the
        reads are safe under autocommit too.  Callers that need write
        atomicity (e.g. the bootstrap service) open their own
        transaction before calling this method.

        Parameters
        ----------
        conn
            The caller-managed connection.
        record_id
            The reading record to index.
        user_id
            The requesting user (ownership check).
        include_rag_ask_only
            When ``False`` (default) only ``main_reading`` blocks are
            indexed.  When ``True`` also includes ``rag_ask_only``
            blocks.
        index_version
            P1-E-R1: Article RAG index version string used to resolve
            the plan / chunker identity.  Omitting the parameter
            defaults to :data:`DEFAULT_ARTICLE_RAG_INDEX_VERSION`.
            Explicit ``None`` is NOT a valid input: it flows directly
            to the resolver, which rejects non-string values
            (fail-closed via :class:`ArticleRagIndexPlanError`).
            Bootstrap and worker callers MUST pass their already-frozen
            / validated ``index_version`` explicitly.  Unknown /
            malicious values fail closed via
            :class:`ArticleRagIndexPlanError`.

        Raises
        ------
        LookupError
            If the record does not exist or does not belong to ``user_id``.
        ArticleRagIndexPlanError
            If the profile cannot be resolved, the resolved plan /
            chunker identity is not the supported V1 identity, the
            stable document / base is stale, inactive, mismatched,
            or no eligible blocks produce chunks.
        """
        # P1-E-R1: resolve the plan / chunker identity through the single
        # runtime-immutable dispatch seam BEFORE any truth-layer read.
        # Omitting ``index_version`` uses the signature default
        # (``DEFAULT_ARTICLE_RAG_INDEX_VERSION``) so V1 default behaviour
        # is byte-stable.  Explicit ``None`` is NOT a valid input: it is
        # NOT normalized here — it flows directly to the resolver, which
        # rejects non-string values (fail-closed).  Bootstrap and worker
        # callers pass their already-frozen / validated ``index_version``
        # explicitly.
        resolved_profile = _resolve_v1_plan_profile(index_version)

        # 1. Load record with ownership check.
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            """,
            record_id,
            user_id,
        )
        if record_row is None:
            raise LookupError(
                f"Reading record {record_id} was not found for user {user_id}."
            )

        record_generation = int(record_row["generation"])
        active_base_id_raw = record_row["active_base_id"]
        if active_base_id_raw is None:
            raise ArticleRagIndexPlanError(
                f"Reading record {record_id} has no active base."
            )
        active_base_id = UUID(str(active_base_id_raw))

        # 2. Load active stable document.
        stable_row = await conn.fetchrow(
            """
            SELECT id, record_generation, document_version, content_sha256, status
            FROM stable_reading_documents
            WHERE reading_record_id = $1
              AND status = 'active'
            """,
            record_id,
        )
        if stable_row is None:
            raise ArticleRagIndexPlanError(
                f"Reading record {record_id} has no active stable document."
            )
        if str(stable_row["status"]) != "active":
            raise ArticleRagIndexPlanError(
                f"Stable document for record {record_id} is not active "
                f"(status={stable_row['status']})."
            )
        stable_document_id = UUID(str(stable_row["id"]))
        stable_generation = int(stable_row["record_generation"])
        if stable_generation != record_generation:
            raise ArticleRagIndexPlanError(
                f"Stable document generation {stable_generation} does not "
                f"match record generation {record_generation}."
            )
        stable_content_sha256 = str(stable_row["content_sha256"])

        # 3. Load active reading base (including text + utf16 length
        #    for canonical offset alignment validation).
        base_row = await conn.fetchrow(
            """
            SELECT id, content_sha256, record_generation, status,
                   text, content_utf16_length
            FROM reading_bases
            WHERE id = $1
              AND reading_record_id = $2
              AND status = 'active'
            """,
            active_base_id,
            record_id,
        )
        if base_row is None:
            raise ArticleRagIndexPlanError(
                f"Reading record {record_id} has no active reading base "
                f"for base_id {active_base_id}."
            )
        if str(base_row["status"]) != "active":
            raise ArticleRagIndexPlanError(
                f"Reading base {active_base_id} is not active "
                f"(status={base_row['status']})."
            )
        base_generation = int(base_row["record_generation"])
        if base_generation != record_generation:
            raise ArticleRagIndexPlanError(
                f"Reading base generation {base_generation} does not "
                f"match record generation {record_generation}."
            )
        base_id = UUID(str(base_row["id"]))
        canonical_text_sha256 = str(base_row["content_sha256"])
        base_text = str(base_row["text"])
        base_utf16_length = int(base_row["content_utf16_length"])

        # 4. Load blocks ordered by order_index.
        block_rows = await conn.fetch(
            """
            SELECT
                block_id,
                order_index,
                block_type,
                text_content,
                canonical_text_start_utf16,
                canonical_text_end_utf16,
                interpretation_policy_json
            FROM stable_document_blocks
            WHERE stable_document_id = $1
            ORDER BY order_index ASC
            """,
            stable_document_id,
        )
        if not block_rows:
            raise ArticleRagIndexPlanError(
                f"Stable document {stable_document_id} has no blocks."
            )

        blocks: list[_BlockRow] = []
        for row in block_rows:
            policy = _coerce_json_object(
                row["interpretation_policy_json"],
                field_name=(
                    "stable_document_blocks.interpretation_policy_json"
                    f"[block_id={row['block_id']}]"
                ),
            )
            blocks.append(
                _BlockRow(
                    block_id=str(row["block_id"]),
                    order_index=int(row["order_index"]),
                    block_type=str(row["block_type"]),
                    text_content=(
                        str(row["text_content"])
                        if row["text_content"] is not None
                        else None
                    ),
                    canonical_text_start_utf16=(
                        int(row["canonical_text_start_utf16"])
                        if row["canonical_text_start_utf16"] is not None
                        else None
                    ),
                    canonical_text_end_utf16=(
                        int(row["canonical_text_end_utf16"])
                        if row["canonical_text_end_utf16"] is not None
                        else None
                    ),
                    interpretation_policy=policy,
                )
            )

        # 5. Load units and segments for the base.
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE base_id = $1
            ORDER BY order_index ASC
            """,
            base_id,
        )
        units = tuple(
            _UnitRow(
                unit_id=str(r["unit_id"]),
                order_index=int(r["order_index"]),
                base_start_utf16=int(r["base_start_utf16"]),
                base_end_utf16=int(r["base_end_utf16"]),
            )
            for r in unit_rows
        )

        segment_rows = await conn.fetch(
            """
            SELECT anchor_segment_id, unit_id, order_index,
                   base_start_utf16, base_end_utf16
            FROM anchor_segments
            WHERE base_id = $1
            ORDER BY order_index ASC
            """,
            base_id,
        )
        segments = tuple(
            _SegmentRow(
                anchor_segment_id=str(r["anchor_segment_id"]),
                unit_id=str(r["unit_id"]),
                order_index=int(r["order_index"]),
                base_start_utf16=int(r["base_start_utf16"]),
                base_end_utf16=int(r["base_end_utf16"]),
            )
            for r in segment_rows
        )

        # 6. Filter and chunk.
        chunks = self._build_chunks(
            blocks=blocks,
            units=units,
            segments=segments,
            stable_document_id=stable_document_id,
            reading_record_id=record_id,
            base_id=base_id,
            record_generation=record_generation,
            include_rag_ask_only=include_rag_ask_only,
            base_text=base_text,
            base_utf16_length=base_utf16_length,
        )

        if not chunks:
            raise ArticleRagIndexPlanError(
                f"No RAG-eligible blocks found for stable document "
                f"{stable_document_id}."
            )

        return ArticleRagIndexPlan(
            reading_record_id=record_id,
            stable_document_id=stable_document_id,
            base_id=base_id,
            record_generation=record_generation,
            content_sha256=stable_content_sha256,
            canonical_text_sha256=canonical_text_sha256,
            # P1-E: source the chunker_version from the resolved V1
            # profile rather than the module-level literal.  The V1
            # profile's chunker_version equals CHUNKER_VERSION, so
            # plan_content_sha256 and all downstream bytes stay
            # byte-stable.
            chunker_version=resolved_profile.chunker_version,
            chunks=tuple(chunks),
            warnings=(),
        )

    # -----------------------------------------------------------------
    # Chunking
    # -----------------------------------------------------------------

    def _build_chunks(
        self,
        *,
        blocks: list[_BlockRow],
        units: tuple[_UnitRow, ...],
        segments: tuple[_SegmentRow, ...],
        stable_document_id: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        record_generation: int,
        include_rag_ask_only: bool,
        base_text: str,
        base_utf16_length: int,
    ) -> list[ArticleRagIndexChunk]:
        """V1: one chunk per eligible block."""
        allowed_routes: set[str] = {"main_reading"}
        if include_rag_ask_only:
            allowed_routes.add("rag_ask_only")

        chunks: list[ArticleRagIndexChunk] = []
        chunk_index = 0

        for block in blocks:
            route, scope, rag_eligible = _interpretation_policy_fields(
                block.interpretation_policy,
                block_type=block.block_type,
            )

            # Filter: must be RAG-eligible and route must be allowed.
            if not rag_eligible:
                continue
            if route in _EXCLUDED_ROUTES:
                continue
            if route not in allowed_routes:
                continue

            # Text must come from text_content (not Markdown / Plate).
            if block.text_content is None or len(block.text_content) == 0:
                raise ArticleRagIndexPlanError(
                    f"Block {block.block_id} (type={block.block_type}) is "
                    f"RAG-eligible but has no text_content."
                )

            text = block.text_content
            source_scope = scope[0] if scope else "main_reading_text"

            # Canonical offsets: only main_reading blocks have them.
            canonical_start = block.canonical_text_start_utf16
            canonical_end = block.canonical_text_end_utf16
            has_canonical_offsets = (
                canonical_start is not None and canonical_end is not None
            )

            # Find overlapping units / segments (only for main_reading
            # blocks that have canonical offsets into reading_bases.text).
            unit_ids: tuple[str, ...] = ()
            anchor_segment_ids: tuple[str, ...] = ()
            if has_canonical_offsets:
                assert canonical_start is not None
                assert canonical_end is not None
                unit_ids = tuple(
                    u.unit_id
                    for u in units
                    if u.base_start_utf16 < canonical_end
                    and u.base_end_utf16 > canonical_start
                )
                anchor_segment_ids = tuple(
                    s.anchor_segment_id
                    for s in segments
                    if s.base_start_utf16 < canonical_end
                    and s.base_end_utf16 > canonical_start
                )

            # Validate canonical offset alignment for main_reading blocks.
            # RAG citation is truth layer: offsets must be non-null AND
            # fall within the base text AND span length must match the
            # text_content UTF-16 length AND the slice content must match
            # text_content exactly.
            if route == "main_reading":
                if not has_canonical_offsets:
                    raise ArticleRagIndexPlanError(
                        f"Block {block.block_id} has route=main_reading but "
                        f"no canonical_text offsets."
                    )
                assert canonical_start is not None
                assert canonical_end is not None
                _validate_canonical_offsets(
                    block_id=block.block_id,
                    text_content=text,
                    canonical_start=canonical_start,
                    canonical_end=canonical_end,
                    base_text=base_text,
                    base_utf16_length=base_utf16_length,
                )

            chunk_id = _deterministic_chunk_id(
                stable_document_id=stable_document_id,
                block_id=block.block_id,
                source_scope=source_scope,
                canonical_start=canonical_start,
                canonical_end=canonical_end,
            )
            content_sha = _sha256_hex(text)
            embedding_text = _prepare_embedding_text(text)
            embedding_sha = _sha256_hex(embedding_text)
            metadata = _build_metadata(
                block_type=block.block_type,
                block_order_index=block.order_index,
                source_scope=source_scope,
                default_route=route,
                chunk_index=chunk_index,
                has_canonical_offsets=has_canonical_offsets,
            )

            citation = ArticleRagCitationRef(
                reading_record_id=reading_record_id,
                stable_document_id=stable_document_id,
                base_id=base_id,
                record_generation=record_generation,
                block_ids=(block.block_id,),
                unit_ids=unit_ids,
                anchor_segment_ids=anchor_segment_ids,
                canonical_text_start_utf16=canonical_start,
                canonical_text_end_utf16=canonical_end,
            )

            chunks.append(
                ArticleRagIndexChunk(
                    chunk_id=chunk_id,
                    citation=citation,
                    source_scope=source_scope,
                    text=text,
                    content_sha256=content_sha,
                    embedding_text_sha256=embedding_sha,
                    metadata_json=metadata,
                )
            )
            chunk_index += 1

        return chunks
