"""MapSourceMaterialProvider — server-only map-source material provider.

Implements contract docs/initiatives/reader-agentic-orchestration/modules/ask-claread-agentic-product-runtime-contract.md
v5 sections §3.5.1.1 (唯一规范签名), §3.5.1 (opt-in control plane),
§3.4 (preflight fence validation), §5.1 6(b) (material fence failure
fallback), §5.1 26 (provider authorization subject), §4.2 / §5.2
(B3 heading enrichment).

This module is the M3-side server-only provider. It:

- Accepts the **完整 server-owned envelope** + ``turn_id`` +
  ``include_rag_ask_only``. Does NOT accept ``EnvelopeIdentity`` and
  does NOT accept an independent ``user_id`` parameter (v5 §3.5.1.1).
- **Always** calls
  :meth:`ArticleRagIndexPlanService.build_index_plan(record_id=
  envelope.reading_record_id, user_id=envelope.user_id,
  include_rag_ask_only=include_rag_ask_only)` after material fence
  checks pass — heading extraction (B3) runs regardless of opt-in
  because heading belongs to ``main_reading`` (§3.5.2 B3 heading-enabled
  baseline).
- Validates material fence (envelope ↔ plan identity).
- When ``include_rag_ask_only=False`` (default, §5.1 3): extracts
  ``heading_enrichments`` only. No descriptor parsing, no descriptor
  source output. ``descriptor_sources=()``.
- When ``include_rag_ask_only=True``: additionally converts plan
  chunks to candidate :class:`ArticleMapEntrySource` via
  :func:`build_descriptor_candidates` (§3.5.1.2 + §5.4).
- On material fence failure (§5.1 6(b)): returns
  ``material_fence_ok=False`` with empty ``descriptor_sources`` AND
  empty ``heading_enrichments`` (整份 fail-closed — material carries
  both heading and descriptor; rejecting the material means neither
  can be trusted). Ask owner falls back to the existing unit-window
  map — does NOT partially consume the material.
- Does NOT invoke the expansion pointer bookkeeping, the evidence
  bookkeeping, the article-map assembly routine, or any cursor
  mutation. Pure preflight computation.

Authorization subject uniqueness (§5.1 26)
-----------------------------------------
Both ``record_id`` and ``user_id`` for ``build_index_plan`` come from
the same ``envelope``. There is no alternative path that accepts an
``EnvelopeIdentity`` or a standalone ``user_id``. This guarantees the
authorization subject is the server-owned envelope, not a model-supplied
identity.

Material fence scope (§3.4 + §5.1 6(b))
---------------------------------------
Material-level fence checks performed by this provider:

1. ``envelope.stable_document_id`` is non-None.
2. ``envelope.base_content_sha256`` is non-None.
3. ``plan.stable_document_id == envelope.stable_document_id``.
4. ``plan.content_sha256 == envelope.base_content_sha256``.

Per-descriptor fence (block locator validity, §3.4 preflight check 3)
is delegated to :func:`build_descriptor_from_chunk` in
:mod:`source_evidence_descriptor` — it fail-closes individual chunks
without aborting the whole material.

B3 heading enrichment scope (§4.2 / §5.2)
-----------------------------------------
Heading chunks are ``metadata_json["block_type"] == "heading"`` plan
chunks (``main_reading`` route, canonical UTF-16 range set). Each
heading is associated with the first reading unit (chunk with non-empty
``citation.unit_ids``) whose ``canonical_text_start_utf16`` is strictly
greater than the heading's ``canonical_text_end_utf16`` (i.e. the unit
that begins after the heading in canonical order). Heading material is
part of the B3 heading-enabled baseline (§3.5.2) — populated regardless
of ``include_rag_ask_only`` opt-in.

Infrastructure failures (e.g. asyncpg connection errors) are NOT
caught here — they propagate up. Only material-level failures
(``LookupError`` for missing record / ownership mismatch,
``ArticleRagIndexPlanError`` for stale / inactive / mismatched stable
document) are converted to ``material_fence_ok=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
)
from app.services.reader_orchestration.source_evidence_descriptor import (
    build_descriptor_candidates,
)
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)

# ---------------------------------------------------------------------------
# Material fence diagnostic enum (server-only, never model-visible)
# ---------------------------------------------------------------------------

#: Safe fixed diagnostic values for ``MapSourceMaterial.material_failure_reason``.
#: Per §5.1 6(b) / project convention, never interpolates raw exception text,
#: caller-supplied values, or record identities. Used only for server-side
#: logging / metrics; never enters DTO / SSE / model-visible output.
MaterialFailureReason = Literal[
    # Material fence OK; descriptor_sources (possibly empty) is safe to use.
    "ok",
    # envelope.stable_document_id is None — cannot perform material fence.
    "envelope_stable_document_id_missing",
    # envelope.base_content_sha256 is None — cannot perform material fence.
    "envelope_base_content_sha256_missing",
    # plan.stable_document_id != envelope.stable_document_id.
    "stable_document_id_mismatch",
    # plan.content_sha256 != envelope.base_content_sha256.
    "base_content_sha256_mismatch",
    # ArticleRagIndexPlanService raised LookupError (record missing or
    # ownership mismatch) or ArticleRagIndexPlanError (stale / inactive /
    # mismatched stable document). Material is unusable.
    "plan_build_failed",
]


# ---------------------------------------------------------------------------
# HeadingEnrichment — B3 stable-document canonical heading for one unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeadingEnrichment:
    """B3 — stable-document canonical heading for one unit source.

    Frozen contract interface between M3 owner (producer) and Ask owner
    (consumer). Pairs one ``unit_id`` with the canonical heading text
    from the stable-document heading block that precedes the unit in
    canonical order (§4.2 / §5.2 10-14).

    The Ask owner merges this into the existing unit source's
    :attr:`ArticleMapEntrySource.heading` — it MUST NOT create a new
    independent heading-only entry (§5.2 13). When the material fence
    fails, ``heading_enrichments`` is empty (§5.1 6(b) integral
    fail-closed — heading and descriptor travel on the same material).

    Fields
    ------
    unit_id:
        The reading unit identifier (``citation.unit_ids`` member of
        the first main_reading chunk whose canonical_start is strictly
        greater than the heading chunk's canonical_end).
    heading:
        The canonical heading text extracted from the stable-document
        ``block_type="heading"`` chunk. May be empty only when the
        chunk text is empty/whitespace-only — but those chunks are
        skipped during enrichment building, so this field is always
        non-empty in practice.
    """

    unit_id: str
    heading: str


# ---------------------------------------------------------------------------
# MapSourceMaterial — frozen contract interface (M3 owner → Ask owner)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MapSourceMaterial:
    """Server-only material returned by :class:`MapSourceMaterialProvider`.

    Frozen contract interface between M3 owner (producer) and Ask owner
    (consumer). Defined here in v5; once the Ask owner wires consumption
    the signature MUST NOT change — extensions require a new contract
    round.

    Fields
    ------
    material_fence_ok:
        ``True`` when ``descriptor_sources`` and ``heading_enrichments``
        are safe to merge into the article-map assembly routine.
        ``False`` when the material fence failed (§5.1 6(b)) — Ask
        owner MUST fall back to the existing unit-window map and MUST
        NOT partially consume ``descriptor_sources`` or
        ``heading_enrichments`` (both empty in that case).
    descriptor_sources:
        Candidate :class:`ArticleMapEntrySource` from rag_ask_only
        blocks. Empty when ``include_rag_ask_only=False`` (§5.1 3) OR
        when material fence failed (§5.1 6(b)). Per §3.5.1.3 /
        §5.1 25, these are CANDIDATES — the Ask owner's article-map
        assembly may silently drop them via cost-fit; no visible
        retention guarantee, no ``stale_evidence`` /
        ``invalid_cursor`` produced for dropped candidates.
    heading_enrichments:
        B3 heading material (§4.2 / §5.2). Populated whenever material
        fence passes — including when ``include_rag_ask_only=False``
        (B3 heading-enabled baseline, §3.5.2). Empty when material
        fence failed (§5.1 6(b) integral fail-closed) OR when the plan
        contains no qualifying heading chunks / no units following any
        heading. Each entry pairs one ``unit_id`` with the heading
        text that precedes it in canonical order. Ask owner merges
        into existing unit source — does NOT create new entry (§5.2 13).
    material_failure_reason:
        Server-only diagnostic enum (never model-visible). Fixed safe
        values from :data:`MaterialFailureReason`. Use for server-side
        logging / metrics only.
    """

    # Server-only fence state (never model-visible).
    material_fence_ok: bool
    # Candidate descriptor sources (possibly empty). Frozen tuple —
    # Ask owner must NOT mutate.
    descriptor_sources: tuple[ArticleMapEntrySource, ...] = ()
    # B3 heading enrichments (possibly empty). Frozen tuple —
    # Ask owner must NOT mutate.
    heading_enrichments: tuple[HeadingEnrichment, ...] = ()
    # Server-only diagnostic (never model-visible). Fixed safe enum.
    material_failure_reason: MaterialFailureReason = "ok"


# ---------------------------------------------------------------------------
# PlanService protocol (DI seam for testability without asyncpg pool)
# ---------------------------------------------------------------------------


class _PlanServiceProtocol(Protocol):
    """Structural protocol for the Article RAG index plan service.

    Allows tests to substitute a fake plan service without constructing
    an :class:`ArticleRagIndexPlanService` with a real asyncpg pool.
    """

    async def build_index_plan(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
    ) -> ArticleRagIndexPlan: ...


# ---------------------------------------------------------------------------
# Provider (§3.5.1.1 — 唯一规范签名)
# ---------------------------------------------------------------------------


class MapSourceMaterialProvider:
    """Server-only provider for one Ask turn's map-source material.

    Implements the v5 §3.5.1.1 **唯一规范签名**:

    .. code-block:: python

        async def load(
            self,
            *,
            envelope: ReadingRecordAskContextEnvelope,
            turn_id: str,
            include_rag_ask_only: bool = False,
        ) -> MapSourceMaterial: ...

    Authorization subject (§5.1 26): both ``record_id`` and ``user_id``
    for the underlying ``build_index_plan`` call come from the same
    ``envelope``. There is no alternative path that accepts an
    ``EnvelopeIdentity`` or a standalone ``user_id`` parameter.

    The provider does NOT invoke the expansion pointer bookkeeping,
    the article-map assembly routine, or any cursor mutation. It is
    a pure preflight computation over the supplied envelope + plan
    service.
    """

    def __init__(self, plan_service: _PlanServiceProtocol) -> None:
        """Initialize the provider with a plan service dependency.

        Args:
            plan_service: Object implementing
                :class:`_PlanServiceProtocol` (typically
                :class:`ArticleRagIndexPlanService`). Tests may pass a
                fake to avoid asyncpg / DB dependencies.
        """
        self._plan_service = plan_service

    async def load(
        self,
        *,
        envelope: ReadingRecordAskContextEnvelope,
        turn_id: str,
        include_rag_ask_only: bool = False,
    ) -> MapSourceMaterial:
        """§3.5.1.1 — load map-source material for one Ask turn.

        Args:
            envelope: Server-owned
                :class:`ReadingRecordAskContextEnvelope` carrying
                ``user_id``, ``reading_record_id``,
                ``stable_document_id``, ``base_content_sha256``, and
                other identity / capability material. MUST be the
                complete envelope — not an ``EnvelopeIdentity`` and
                not a standalone ``user_id``.
            turn_id: Server-minted turn id (same source as
                :class:`ExpansionEnvelopeIdentity.turn_id`). Not used
                for plan build; reserved for future per-turn material
                caching / fence material. MUST NOT come from the model.
            include_rag_ask_only: Server-only opt-in flag (default
                ``False``). ``False`` (§5.1 3): no descriptor parsing,
                ``descriptor_sources=()``. ``True``: build plan with
                rag_ask_only chunks and parse descriptors per
                §3.5.1.2. Heading enrichment (B3) is populated in
                BOTH cases — heading belongs to ``main_reading``
                (§3.5.2 B3 heading-enabled baseline).

        Returns:
            :class:`MapSourceMaterial` with ``material_fence_ok`` set
            per §5.1 6(b). When fence fails, both
            ``descriptor_sources`` and ``heading_enrichments`` are
            empty (integral fail-closed) and the caller MUST fall back
            to unit-window map.

        Raises:
            Infrastructure exceptions (e.g. asyncpg connection errors)
            propagate up — they are NOT material fence failures and
            MUST NOT be silently swallowed. Only
            :class:`LookupError` (record missing / ownership mismatch)
            and :class:`ArticleRagIndexPlanError` (stale / inactive /
            mismatched stable document) are converted to
            ``material_fence_ok=False``.
        """
        # §3.4 / §5.1 6(b): material fence — envelope must carry
        # stable_document_id (cannot verify plan ↔ envelope identity
        # without it). Runs regardless of include_rag_ask_only because
        # heading extraction (B3) also requires material fence.
        if envelope.stable_document_id is None:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                heading_enrichments=(),
                material_failure_reason="envelope_stable_document_id_missing",
            )

        # §3.4 / §5.1 6(b): material fence — envelope must carry
        # base_content_sha256 (cannot verify plan content ↔ envelope
        # content without it).
        if envelope.base_content_sha256 is None:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                heading_enrichments=(),
                material_failure_reason="envelope_base_content_sha256_missing",
            )

        # §3.5.1.1: build plan with both identity fields from envelope.
        # §5.1 26: record_id and user_id both read from the same envelope.
        # Forward include_rag_ask_only so the plan service can include
        # or exclude rag_ask_only chunks accordingly. Heading chunks
        # (main_reading route) are present in either mode.
        try:
            plan = await self._plan_service.build_index_plan(
                record_id=envelope.reading_record_id,
                user_id=envelope.user_id,
                include_rag_ask_only=include_rag_ask_only,
            )
        except (LookupError, ArticleRagIndexPlanError) as exc:
            # §5.1 6(b): material fence failure — record missing /
            # ownership mismatch / stale / inactive / mismatched stable
            # document. Convert to safe diagnostic; do NOT leak repr(exc)
            # or str(exc) into the material (project convention: fixed
            # safe messages only).
            del exc  # explicit: not retained, not interpolated.
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                heading_enrichments=(),
                material_failure_reason="plan_build_failed",
            )

        # §3.4 / §5.1 6(b): material fence — plan identity must match
        # envelope identity. plan.stable_document_id is UUID;
        # envelope.stable_document_id is UUID | None (already None-checked
        # above). Compare as UUIDs.
        # envelope.stable_document_id is typed as UUID | None; the
        # None branch already returned. Mypy/pyright understand this.
        if plan.stable_document_id != envelope.stable_document_id:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                heading_enrichments=(),
                material_failure_reason="stable_document_id_mismatch",
            )

        # §3.4 / §5.1 6(b): material fence — plan content hash must
        # match envelope base content hash. Both are lowercase hex
        # SHA-256 (64 chars); compared as plain strings.
        if plan.content_sha256 != envelope.base_content_sha256:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                heading_enrichments=(),
                material_failure_reason="base_content_sha256_mismatch",
            )

        # Material fence passed — extract B3 heading enrichments.
        # §4.2 / §5.2: heading belongs to main_reading; populated
        # regardless of include_rag_ask_only opt-in (§3.5.2 B3
        # heading-enabled baseline).
        heading_enrichments = _build_heading_enrichments(plan)

        # §5.1 3: descriptor parsing only when include_rag_ask_only=True.
        # When False (default), descriptor_sources=() — B3 heading is
        # the only enrichment produced.
        if not include_rag_ask_only:
            return MapSourceMaterial(
                material_fence_ok=True,
                descriptor_sources=(),
                heading_enrichments=heading_enrichments,
                material_failure_reason="ok",
            )

        # include_rag_ask_only=True — also build descriptor candidates.
        # §3.5.1.2 + §5.4: build_descriptor_candidates filters chunks,
        # builds descriptors (fail-closed per chunk), sorts by §5.4.1
        # key, applies §5.4.2 hard cap 8, converts to ArticleMapEntrySource.
        # §3.5.1.3 / §5.1 25: these are CANDIDATES — no visible retention
        # guarantee; the article-map assembly may silently drop via cost-fit.
        descriptor_sources = build_descriptor_candidates(plan=plan)

        return MapSourceMaterial(
            material_fence_ok=True,
            descriptor_sources=descriptor_sources,
            heading_enrichments=heading_enrichments,
            material_failure_reason="ok",
        )


# ---------------------------------------------------------------------------
# B3 heading enrichment builder (§4.2 / §5.2)
# ---------------------------------------------------------------------------


def _read_metadata_str(
    metadata: dict[str, Any] | None,
    key: str,
) -> str | None:
    """Read a string field from chunk metadata, fail-closed on wrong type.

    Returns the string value when present and typed as ``str``; returns
    ``None`` when the key is missing, the metadata is not a dict, or
    the value is not a string. Used for ``block_type`` lookup — never
    re-queries the document.
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(key)
    if isinstance(raw, str):
        return raw
    return None


def _build_heading_enrichments(
    plan: ArticleRagIndexPlan,
) -> tuple[HeadingEnrichment, ...]:
    """B3 — extract heading enrichments from plan chunks (§4.2 / §5.2).

    Heading chunks are ``metadata_json["block_type"] == "heading"`` plan
    chunks. Heading chunks are ``main_reading`` route blocks with
    canonical UTF-16 range set; their text is the canonical heading.

    Each heading is paired with the first reading unit (chunk with
    non-empty ``citation.unit_ids``) whose ``canonical_text_start_utf16``
    is strictly greater than the heading's ``canonical_text_end_utf16``
    (i.e. the unit that begins after the heading in canonical order).

    Returns empty tuple when:
    - No qualifying heading chunks (block_type=heading with non-empty
      text and valid canonical range) in plan.
    - No reading units after any heading.
    - Multiple heading chunks map to the same unit — only the first
      (closest preceding) heading wins; later headings for the same
      unit are dropped to preserve §5.2 13 ("heading 只补到同一 unit
      source, 不新增独立 heading entry").

    The mapping is deterministic:
    1. Collect heading chunks, sort by ``canonical_text_start_utf16``.
    2. Collect reading units (deduplicate by ``unit_id``, keeping the
       smallest ``canonical_text_start_utf16`` per unit).
    3. Sort units by ``canonical_text_start_utf16``.
    4. For each heading in canonical order, find the first unit whose
       start > heading's end; if found AND that unit is not already
       paired with an earlier heading, emit a HeadingEnrichment.

    Does NOT invoke the expansion pointer bookkeeping, the evidence
    bookkeeping, the article-map assembly routine, or any DB / embedding
    / Zilliz seam. Pure computation over the supplied plan.
    """
    # 1. Collect qualifying heading chunks.
    #    - metadata_json["block_type"] == "heading"
    #    - canonical_text_start_utf16 / canonical_text_end_utf16 are
    #      both non-None (heading is main_reading → range is set)
    #    - text is non-empty (whitespace-only is treated as empty)
    heading_chunks: list[tuple[int, int, str]] = []
    for chunk in plan.chunks:
        block_type = _read_metadata_str(chunk.metadata_json, "block_type")
        if block_type != "heading":
            continue
        start = chunk.citation.canonical_text_start_utf16
        end = chunk.citation.canonical_text_end_utf16
        if start is None or end is None:
            continue
        if not chunk.text or not chunk.text.strip():
            continue
        heading_chunks.append((start, end, chunk.text))

    if not heading_chunks:
        return ()

    # Sort headings by canonical_start (deterministic canonical order).
    heading_chunks.sort(key=lambda h: h[0])

    # 2. Collect reading units — chunks with non-empty unit_ids.
    #    Deduplicate by unit_id, keeping the smallest canonical_start
    #    per unit_id (a unit may span multiple chunks; the earliest
    #    chunk determines its canonical position).
    unit_starts: dict[str, int] = {}
    for chunk in plan.chunks:
        if not chunk.citation.unit_ids:
            continue
        start = chunk.citation.canonical_text_start_utf16
        if start is None:
            continue
        for unit_id in chunk.citation.unit_ids:
            if not unit_id:
                continue
            existing = unit_starts.get(unit_id)
            if existing is None or start < existing:
                unit_starts[unit_id] = start

    if not unit_starts:
        return ()

    # 3. Sort units by canonical_start (deterministic canonical order).
    sorted_units = sorted(unit_starts.items(), key=lambda kv: kv[1])

    # 4. For each heading in canonical order, find the first unit whose
    #    start > heading's end. Skip units already paired with an earlier
    #    heading (§5.2 13: heading 只补到同一 unit source, 不新增独立
    #    heading entry — one heading per unit).
    paired_units: set[str] = set()
    enrichments: list[HeadingEnrichment] = []
    for _, heading_end, heading_text in heading_chunks:
        for unit_id, unit_start in sorted_units:
            if unit_start <= heading_end:
                continue
            if unit_id in paired_units:
                continue
            enrichments.append(
                HeadingEnrichment(unit_id=unit_id, heading=heading_text)
            )
            paired_units.add(unit_id)
            break

    return tuple(enrichments)


__all__ = [
    "HeadingEnrichment",
    "MapSourceMaterial",
    "MapSourceMaterialProvider",
    "MaterialFailureReason",
]
