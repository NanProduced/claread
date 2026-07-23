"""MapSourceMaterialProvider — server-only map-source material provider.

Implements contract TMP-m3-stage-b-ask-evidence-contract-freeze-2026-07-23.md
v5 sections §3.5.1.1 (唯一规范签名), §3.5.1 (opt-in control plane),
§3.4 (preflight fence validation), §5.1 6(b) (material fence failure
fallback), §5.1 26 (provider authorization subject).

This module is the M3-side server-only provider. It:

- Accepts the **完整 server-owned envelope** + ``turn_id`` +
  ``include_rag_ask_only``. Does NOT accept ``EnvelopeIdentity`` and
  does NOT accept an independent ``user_id`` parameter (v5 §3.5.1.1).
- When ``include_rag_ask_only=False`` (default, §5.1 3): returns an
  empty ``MapSourceMaterial`` with ``material_fence_ok=True``. No
  plan build, no descriptor parsing, no DB / embedding / Zilliz calls.
- When ``include_rag_ask_only=True``: calls
  :meth:`ArticleRagIndexPlanService.build_index_plan(record_id=
  envelope.reading_record_id, user_id=envelope.user_id,
  include_rag_ask_only=True)`, validates material fence (envelope ↔
  plan identity), then converts plan chunks to candidate
  :class:`ArticleMapEntrySource` via :func:`build_descriptor_candidates`
  (§3.5.1.2 + §5.4).
- On material fence failure (§5.1 6(b)): returns
  ``material_fence_ok=False`` with empty ``descriptor_sources`` and a
  fixed safe diagnostic enum (never raw exception text). Ask owner
  falls back to the existing unit-window map — does NOT partially
  consume the material.
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

Infrastructure failures (e.g. asyncpg connection errors) are NOT
caught here — they propagate up. Only material-level failures
(``LookupError`` for missing record / ownership mismatch,
``ArticleRagIndexPlanError`` for stale / inactive / mismatched stable
document) are converted to ``material_fence_ok=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
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
        ``True`` when ``descriptor_sources`` is safe to merge into
        the article-map assembly routine. ``False`` when the material fence
        failed (§5.1 6(b)) — Ask owner MUST fall back to the existing
        unit-window map and MUST NOT partially consume
        ``descriptor_sources`` (which is empty in that case).
    descriptor_sources:
        Candidate :class:`ArticleMapEntrySource` from rag_ask_only
        blocks. Empty when ``include_rag_ask_only=False`` (§5.1 3) OR
        when material fence failed (§5.1 6(b)). Per §3.5.1.3 /
        §5.1 25, these are CANDIDATES — the Ask owner's article-map
        assembly may silently drop them via cost-fit; no visible
        retention guarantee, no ``stale_evidence`` /
        ``invalid_cursor`` produced for dropped candidates.
    material_failure_reason:
        Server-only diagnostic enum (never model-visible). Fixed safe
        values from :data:`MaterialFailureReason`. Use for server-side
        logging / metrics only.

    Heading material (B3) is intentionally NOT included in this round —
    this C1 task scope is descriptor sources only. B3 heading injection
    is a separate Ask-owner wiring concern (§4.2).
    """

    # Server-only fence state (never model-visible).
    material_fence_ok: bool
    # Candidate descriptor sources (possibly empty). Frozen tuple —
    # Ask owner must NOT mutate.
    descriptor_sources: tuple[ArticleMapEntrySource, ...] = ()
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
                no plan build, no DB / embedding / Zilliz calls.
                ``True``: build plan + parse descriptors per §3.5.1.2.

        Returns:
            :class:`MapSourceMaterial` with ``material_fence_ok`` set
            per §5.1 6(b). When fence fails, ``descriptor_sources`` is
            empty and the caller MUST fall back to unit-window map.

        Raises:
            Infrastructure exceptions (e.g. asyncpg connection errors)
            propagate up — they are NOT material fence failures and
            MUST NOT be silently swallowed. Only
            :class:`LookupError` (record missing / ownership mismatch)
            and :class:`ArticleRagIndexPlanError` (stale / inactive /
            mismatched stable document) are converted to
            ``material_fence_ok=False``.
        """
        # §5.1 3 / §3.5.2: default OFF — no descriptor parsing.
        if not include_rag_ask_only:
            return MapSourceMaterial(
                material_fence_ok=True,
                descriptor_sources=(),
                material_failure_reason="ok",
            )

        # §3.4 / §5.1 6(b): material fence — envelope must carry
        # stable_document_id (cannot verify plan ↔ envelope identity
        # without it).
        if envelope.stable_document_id is None:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                material_failure_reason="envelope_stable_document_id_missing",
            )

        # §3.4 / §5.1 6(b): material fence — envelope must carry
        # base_content_sha256 (cannot verify plan content ↔ envelope
        # content without it).
        if envelope.base_content_sha256 is None:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                material_failure_reason="envelope_base_content_sha256_missing",
            )

        # §3.5.1.1: build plan with both identity fields from envelope.
        # §5.1 26: record_id and user_id both read from the same envelope.
        try:
            plan = await self._plan_service.build_index_plan(
                record_id=envelope.reading_record_id,
                user_id=envelope.user_id,
                include_rag_ask_only=True,
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
                material_failure_reason="stable_document_id_mismatch",
            )

        # §3.4 / §5.1 6(b): material fence — plan content hash must
        # match envelope base content hash. Both are lowercase hex
        # SHA-256 (64 chars); compared as plain strings.
        if plan.content_sha256 != envelope.base_content_sha256:
            return MapSourceMaterial(
                material_fence_ok=False,
                descriptor_sources=(),
                material_failure_reason="base_content_sha256_mismatch",
            )

        # Material fence passed — build descriptor candidates via C3.
        # §3.5.1.2 + §5.4: build_descriptor_candidates filters chunks,
        # builds descriptors (fail-closed per chunk), sorts by §5.4.1
        # key, applies §5.4.2 hard cap 8, converts to ArticleMapEntrySource.
        # §3.5.1.3 / §5.1 25: these are CANDIDATES — no visible retention
        # guarantee; the article-map assembly may silently drop via cost-fit.
        descriptor_sources = build_descriptor_candidates(plan=plan)

        return MapSourceMaterial(
            material_fence_ok=True,
            descriptor_sources=descriptor_sources,
            material_failure_reason="ok",
        )


__all__ = [
    "MapSourceMaterial",
    "MapSourceMaterialProvider",
    "MaterialFailureReason",
]
