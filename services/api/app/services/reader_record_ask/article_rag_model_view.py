"""Article RAG tool model-view scrub (offline core).

Pure offline assembler that turns an **already obtained**
:class:`ArticleRagSearchOutcome` into a narrow, metered, fail-soft tool
model-view. It never searches, embeds, queries an index, or touches a
DB — zero I/O (design TMP §8 / §18.1).

Contracts
---------
- All six port statuses are handled fail-soft: non-ok outcomes produce a
  safe model-visible view (fixed summary — never upstream detail text or
  ``detail_code``) or a typed non-model-visible budget-denied terminal.
  RAG unavailability never fails the Ask turn and never raises
  model-retry control flow.
- ok path: the outcome-level identity (stable document / base /
  generation) is fenced against the envelope BEFORE any hit processing
  or mutation — a mismatch yields the fixed safe unavailable view (or
  typed budget-denied when even that cannot be charged) with zero
  mutation and no identity/body leakage. Every hit is then re-verified
  individually (record / base / generation / stable document / source
  scope) — a matching outcome never makes individual hits trustworthy;
  mismatched hits are discarded with zero mutation. Each eligible hit
  becomes one ``search_hit`` observation (existing registry +
  ``ArticleRagCitationEvidence`` truth) and one renderer-minted
  ``<untrusted_article_text role="rag">`` block; text appears exactly
  once per hit, inside its block.
- score / chunk_id / hashes / UUIDs / UTF-16 ranges / substrate id /
  plan hash / detail_code live ONLY in :class:`RagSearchSidecar`
  (server-only, never model-visible).
- The complete tool JSON rendered by ``ModelViewRenderer.render_tool_view``
  is the single rag-account metering source. Hits are atomic: fit keeps
  the largest prefix of verified hits that fits ``RESERVE_RAG``; nothing
  fits → typed budget-denied host outcome with zero mutation (no
  registration, no charge).
- The success transaction reuses the shared batch compensation
  (:func:`evidence_transaction.rollback_charged_observations_batch`,
  ``failure_domain="rag_model_view"``) — no second transaction logic.
  Every attempted observation receives its conditional cleanup even if
  earlier cleanups mismatch or raise (no short-circuit, no stranded
  transaction observations, foreign entries never deleted); the charge
  is refunded exactly once; one aggregate stable code is raised — no
  body, repr, handle id, identity, or raw exception text is surfaced.

No runtime / production stream / wiring / route / legacy prompt
integration imports this module in (static reverse guards in the
tests). The legacy Ask prompt-integration bridge is never used.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.services.reader_record_ask.article_rag_port import (
    ALLOWED_ASK_RAG_SOURCE_SCOPES,
    ArticleRagHitView,
    ArticleRagSearchOutcome,
)
from app.services.reader_record_ask.evidence import (
    ArticleRagCitationEvidence,
    EvidenceHandleRef,
    ServerEvidenceObservation,
    build_server_evidence_observation,
    mint_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.evidence_transaction import (
    rollback_charged_observations_batch,
)
from app.services.reader_record_ask.model_view_budget import (
    BudgetChargeOk,
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.selection_model_view import (
    EVIDENCE_SNIPPET_HARD_CAP,
)
from app.services.reader_record_ask.tool_contracts import (
    RagSearchToolView,
)

RagAssembleKind = Literal[
    "ok",
    "empty",
    "not_ready",
    "not_indexed",
    "indexing",
    "unavailable",
    "budget_denied",
]

RAG_BLOCK_ROLE: str = "rag"

# Fixed model-visible summaries — no upstream detail, no detail_code.
_RAG_SUMMARIES: dict[str, str] = {
    "ok": "Article search returned supporting passages.",
    "empty": "Article search found no matching passages.",
    "not_ready": "Article search is not ready for this article.",
    "not_indexed": "Article search index is not available for this article.",
    "indexing": "Article search index is still building.",
    "unavailable": "Article search is unavailable for this article.",
}

# Stable failure codes — never embed body, repr, or raw exception text.
# Aggregate compensation verdicts come from the shared batch seam:
# rag_model_view_rollback_failed code=batch_complete|batch_partial|
# batch_refund|batch_partial_and_refund.

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Identity + sidecar + result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RagEnvelopeIdentity:
    """Server-owned envelope identity for RAG model-view assembly.

    Built by the host from the verified envelope; never from model input
    or port output. ``stable_document_id`` is required — RAG citations
    cannot be anchored without it.
    """

    envelope_fingerprint: str
    reading_record_id: UUID
    base_id: UUID
    record_generation: int
    stable_document_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(
            self.envelope_fingerprint, str
        ) or not re.match(r"^[0-9a-f]{64}$", self.envelope_fingerprint):
            raise ValueError(
                "envelope_fingerprint must be a 64-char lowercase hex "
                "SHA-256 digest"
            )
        if (
            not isinstance(self.record_generation, int)
            or isinstance(self.record_generation, bool)
            or self.record_generation < 1
        ):
            raise ValueError("record_generation must be an int >= 1")
        for field_name in (
            "reading_record_id",
            "base_id",
            "stable_document_id",
        ):
            if not isinstance(getattr(self, field_name), UUID):
                raise TypeError(f"{field_name} must be a UUID")


@dataclass(frozen=True, slots=True)
class RagHitSidecar:
    """Server-only per-hit provenance. Never model-visible."""

    chunk_id: str
    score: float
    content_sha256: str
    source_scope: str
    block_type: str
    canonical_text_start_utf16: int
    canonical_text_end_utf16: int
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    rag_substrate_id: UUID | None
    plan_content_sha256: str | None
    # Set when the hit was registered as citeable evidence; None when the
    # hit was verified but dropped by the budget fit.
    handle_id: str | None = None


@dataclass(frozen=True, slots=True)
class RagSearchSidecar:
    """Server-only diagnostics for one RAG model-view assembly."""

    status: str
    detail_code: str | None
    hits: tuple[RagHitSidecar, ...] = ()
    discarded_identity_mismatches: int = 0


@dataclass(frozen=True, slots=True)
class RagModelViewResult:
    """Host-owned RAG tool model-view assembly outcome.

    ``model_visible=True`` results carry the renderer-minted, charged
    tool-view. ``budget_denied`` is a typed non-model-visible host
    terminal: hosts map it to the typed budget-exhausted stream outcome,
    never to an unmetered JSON error. ``sidecar`` is server-only.
    """

    kind: RagAssembleKind
    model_visible: bool
    rendered_tool_view: RenderedModelView | None = None
    charge: BudgetChargeOk | None = None
    evidence_handles: tuple[EvidenceHandleRef, ...] = ()
    sidecar: RagSearchSidecar | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit_passes_identity_fence(
    hit: ArticleRagHitView, *, identity: RagEnvelopeIdentity
) -> bool:
    """Defense-in-depth re-verification of a port hit against the envelope.

    The adapter already fences hits; the assembler never trusts port
    output blindly.
    """
    return (
        hit.reading_record_id == identity.reading_record_id
        and hit.base_id == identity.base_id
        and hit.record_generation == identity.record_generation
        and hit.stable_document_id == identity.stable_document_id
        and hit.source_scope in ALLOWED_ASK_RAG_SOURCE_SCOPES
    )


def _render_rag_view(
    renderer: ModelViewRenderer,
    *,
    status: str,
    summary: str,
    handles: Sequence[EvidenceHandleRef] = (),
    blocks: Sequence[str] = (),
) -> RenderedModelView:
    view = RagSearchToolView(
        status=status,  # type: ignore[arg-type]
        summary=summary,
        evidence_handles=tuple(handles),
        article_text_blocks=tuple(blocks),
    )
    return renderer.render_tool_view(view.model_dump(mode="json"))


def _safe_status_outcome(
    renderer: ModelViewRenderer,
    budget: ModelVisibleTurnBudget,
    *,
    status: str,
    sidecar: RagSearchSidecar,
) -> RagModelViewResult:
    """Render + charge a fail-soft safe view; budget-denied if it can't."""
    kind = status if status != "budget_denied" else "unavailable"
    rendered = _render_rag_view(
        renderer, status=kind, summary=_RAG_SUMMARIES[kind]
    )
    if not budget.can_charge("rag", rendered):
        return RagModelViewResult(
            kind="budget_denied", model_visible=False, sidecar=sidecar
        )
    try:
        ok = budget.charge("rag", rendered)
    except ModelViewBudgetError:
        return RagModelViewResult(
            kind="budget_denied", model_visible=False, sidecar=sidecar
        )
    return RagModelViewResult(
        kind=kind,  # type: ignore[arg-type]
        model_visible=True,
        rendered_tool_view=rendered,
        charge=ok,
        sidecar=sidecar,
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_rag_model_view(
    *,
    outcome: ArticleRagSearchOutcome,
    envelope_identity: RagEnvelopeIdentity,
    registry: EvidenceRegistry,
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer | None = None,
) -> RagModelViewResult:
    """Scrub a port outcome into a metered, fail-soft RAG tool model-view.

    Zero I/O: consumes the already-obtained ``outcome`` only. Never
    raises for RAG failure states (fail-soft); raises only stable-code
    ``RuntimeError`` on incomplete host-side compensation after a
    successful charge.
    """
    active_renderer = renderer if renderer is not None else ModelViewRenderer()

    if registry.envelope_fingerprint != (
        envelope_identity.envelope_fingerprint
    ):
        raise ValueError(
            "evidence registry fingerprint does not match envelope "
            "fingerprint"
        )

    # Non-ok: fail-soft safe view (no hits, no registry mutation).
    if outcome.status != "ok":
        sidecar = RagSearchSidecar(
            status=outcome.status,
            detail_code=outcome.detail_code,
        )
        return _safe_status_outcome(
            active_renderer,
            budget,
            status=outcome.status,
            sidecar=sidecar,
        )

    # ok: outcome-level identity fence — BEFORE any hit processing or
    # budget/registry mutation. A matching outcome still does not make
    # individual hits trustworthy (per-hit fence remains below).
    if (
        outcome.stable_document_id != envelope_identity.stable_document_id
        or outcome.base_id != envelope_identity.base_id
        or outcome.record_generation != envelope_identity.record_generation
    ):
        # Fixed safe view + fixed internal detail code; raw identity
        # values never enter the model surface or error text.
        sidecar = RagSearchSidecar(
            status="unavailable",
            detail_code="outcome_identity_mismatch",
        )
        return _safe_status_outcome(
            active_renderer,
            budget,
            status="unavailable",
            sidecar=sidecar,
        )

    # ok: identity completeness — substrate + plan hash anchor citations.
    substrate_id = outcome.rag_substrate_id
    plan_hash = outcome.plan_content_sha256
    if (
        substrate_id is None
        or plan_hash is None
        or not _HEX64_RE.match(plan_hash)
    ):
        sidecar = RagSearchSidecar(
            status="unavailable",
            detail_code="missing_citation_anchor",
        )
        return _safe_status_outcome(
            active_renderer,
            budget,
            status="unavailable",
            sidecar=sidecar,
        )

    # Re-verify every hit against the envelope identity; discard failures.
    eligible: list[ArticleRagHitView] = []
    discarded = 0
    for hit in outcome.hits:
        if not _hit_passes_identity_fence(hit, identity=envelope_identity):
            discarded += 1
            continue
        if len(hit.text) > EVIDENCE_SNIPPET_HARD_CAP or not hit.text:
            # Citation truth (snippet ≤ hard cap) cannot represent the
            # hit faithfully — fail closed on this hit.
            discarded += 1
            continue
        eligible.append(hit)

    if not eligible:
        sidecar = RagSearchSidecar(
            status="empty",
            detail_code=outcome.detail_code,
            discarded_identity_mismatches=discarded,
        )
        return _safe_status_outcome(
            active_renderer, budget, status="empty", sidecar=sidecar
        )

    # Prospective handles + blocks (pure).
    handle_ids = [mint_evidence_handle_id() for _ in eligible]
    blocks = [
        active_renderer.render_untrusted_article_text(
            handle_id=handle_id,
            ordinal=index,
            role=RAG_BLOCK_ROLE,
            text=hit.text,
        ).text
        for index, (handle_id, hit) in enumerate(
            zip(handle_ids, eligible, strict=True)
        )
    ]
    handles = [
        EvidenceHandleRef(handle_id=handle_id) for handle_id in handle_ids
    ]

    # Fit on the REAL complete tool JSON: keep the largest hit prefix that
    # fits the rag account (hits are atomic — never truncated).
    best = 0
    lo, hi = 1, len(eligible)
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _render_rag_view(
            active_renderer,
            status="ok",
            summary=_RAG_SUMMARIES["ok"],
            handles=handles[:mid],
            blocks=blocks[:mid],
        )
        if budget.can_charge("rag", candidate):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best == 0:
        sidecar = RagSearchSidecar(
            status="ok",
            detail_code=outcome.detail_code,
            discarded_identity_mismatches=discarded,
        )
        return RagModelViewResult(
            kind="budget_denied", model_visible=False, sidecar=sidecar
        )

    final_view = _render_rag_view(
        active_renderer,
        status="ok",
        summary=_RAG_SUMMARIES["ok"],
        handles=handles[:best],
        blocks=blocks[:best],
    )
    if not budget.can_charge("rag", final_view):
        sidecar = RagSearchSidecar(
            status="ok",
            detail_code=outcome.detail_code,
            discarded_identity_mismatches=discarded,
        )
        return RagModelViewResult(
            kind="budget_denied", model_visible=False, sidecar=sidecar
        )

    # Charge the complete tool-view once.
    try:
        charge_ok = budget.charge("rag", final_view)
    except ModelViewBudgetError:
        sidecar = RagSearchSidecar(
            status="ok",
            detail_code=outcome.detail_code,
            discarded_identity_mismatches=discarded,
        )
        return RagModelViewResult(
            kind="budget_denied", model_visible=False, sidecar=sidecar
        )
    charge_cost = charge_ok.cost

    # Register fitted observations + postcondition; shared rollback on
    # failure (stable codes, no raw exception leakage).
    observations: list[ServerEvidenceObservation] = []
    for index in range(best):
        hit = eligible[index]
        citation = ArticleRagCitationEvidence(
            rag_substrate_id=str(substrate_id),
            index_run_id=str(substrate_id),
            plan_content_sha256=plan_hash,
            source_scope=hit.source_scope,
            block_type=hit.block_type,
            chunk_id=hit.chunk_id,
            content_sha256=hit.content_sha256,
            canonical_text_start_utf16=hit.canonical_text_start_utf16,
            canonical_text_end_utf16=hit.canonical_text_end_utf16,
            snippet=hit.text,
            score=hit.score,
            reading_record_id=str(hit.reading_record_id),
            stable_document_id=str(hit.stable_document_id),
            base_id=str(hit.base_id),
            record_generation=hit.record_generation,
            block_ids=hit.block_ids,
            unit_ids=hit.unit_ids,
            anchor_segment_ids=hit.anchor_segment_ids,
        )
        observations.append(
            build_server_evidence_observation(
                kind="search_hit",
                envelope_fingerprint=envelope_identity.envelope_fingerprint,
                source_tool="search_current_article",
                snippet=hit.text,
                handle_id=handle_ids[index],
                rag_citation=citation,
            )
        )

    registered: list[ServerEvidenceObservation] = []
    try:
        for observation in observations:
            # Track BEFORE the write: a write-then-raise implementation
            # must still leave the observation compensable. The shared
            # conditional discard treats never-written entries as absent.
            registered.append(observation)
            handle_ref = registry.register(observation)
            registered_observation = registry.get(
                observation.handle.handle_id
            )
            if (
                registered_observation is None
                or registered_observation != observation
                or handle_ref.handle_id != observation.handle.handle_id
            ):
                raise RuntimeError("postcondition")
    except Exception:
        # Best-effort COMPLETE cleanup: every attempted observation gets
        # its conditional discard (never short-circuits on mismatch /
        # residual / raise; foreign entries untouched), then exactly one
        # refund, then one aggregate stable verdict code (always raised).
        rollback_charged_observations_batch(
            budget=budget,
            account="rag",
            charge_cost=charge_cost,
            registry=registry,
            observations=registered,
            failure_domain="rag_model_view",
        )
        raise  # unreachable: the batch seam always raises a stable code

    sidecar = RagSearchSidecar(
        status="ok",
        detail_code=outcome.detail_code,
        hits=tuple(
            RagHitSidecar(
                chunk_id=hit.chunk_id,
                score=hit.score,
                content_sha256=hit.content_sha256,
                source_scope=hit.source_scope,
                block_type=hit.block_type,
                canonical_text_start_utf16=hit.canonical_text_start_utf16,
                canonical_text_end_utf16=hit.canonical_text_end_utf16,
                reading_record_id=hit.reading_record_id,
                stable_document_id=hit.stable_document_id,
                base_id=hit.base_id,
                record_generation=hit.record_generation,
                rag_substrate_id=substrate_id,
                plan_content_sha256=plan_hash,
                handle_id=(
                    handle_ids[index] if index < best else None
                ),
            )
            for index, hit in enumerate(eligible)
        ),
        discarded_identity_mismatches=discarded,
    )

    return RagModelViewResult(
        kind="ok",
        model_visible=True,
        rendered_tool_view=final_view,
        charge=charge_ok,
        evidence_handles=tuple(handles[:best]),
        sidecar=sidecar,
    )


__all__ = [
    "RAG_BLOCK_ROLE",
    "RagAssembleKind",
    "RagEnvelopeIdentity",
    "RagHitSidecar",
    "RagModelViewResult",
    "RagSearchSidecar",
    "assemble_rag_model_view",
]
