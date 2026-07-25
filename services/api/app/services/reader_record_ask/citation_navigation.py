"""Secure citation navigation for Ask Claread (server-side only).

Clients submit only ``message_id`` + ``citation_id`` via the route path.
The host constructs :class:`LiveDocumentFence` from authoritative reading
record / stable-document snapshot data (never from client body fields),
re-resolves restricted evidence, and returns a minimal typed article
location — never internal handles or generic locator blobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.database import connection as db_connection

NavigateStatus = Literal[
    "ok",
    "not_found",
    "identity_mismatch",
    "stale_generation",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class ArticleLocation:
    """Minimal typed location safe to return to the client."""

    unit_id: str | None
    anchor_segment_id: str | None
    canonical_text_start_utf16: int | None
    canonical_text_end_utf16: int | None


@dataclass(frozen=True, slots=True)
class CitationNavigateResult:
    status: NavigateStatus
    location: ArticleLocation | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LiveDocumentFence:
    """Server-owned record identity used for navigation fencing."""

    reading_record_id: str
    base_id: str
    record_generation: int
    stable_document_id: str | None


async def load_live_document_fence(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    pool: Any | None = None,
) -> LiveDocumentFence | None:
    """Load authoritative LiveDocumentFence for the current user/record.

    Returns ``None`` when the record is missing, not owned, or has no
    active base identity. Client request bodies must never supply these
    fields.
    """
    db_pool = pool or db_connection.DB_POOL
    if db_pool is None:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.id AS reading_record_id,
                   r.generation AS record_generation,
                   r.active_base_id,
                   s.id AS stable_document_id
            FROM reading_records r
            LEFT JOIN stable_reading_documents s
              ON s.reading_record_id = r.id
             AND s.status = 'active'
             AND s.record_generation = r.generation
            WHERE r.id = $1
              AND r.user_id = $2
              AND r.deleted_at IS NULL
            """,
            reading_record_id,
            user_id,
        )
    if row is None:
        return None
    active_base = row["active_base_id"]
    if active_base is None:
        return None
    stable = row["stable_document_id"]
    return LiveDocumentFence(
        reading_record_id=str(row["reading_record_id"]),
        base_id=str(active_base),
        record_generation=int(row["record_generation"]),
        stable_document_id=str(stable) if stable is not None else None,
    )


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _check_stable_document_fence(
    *,
    stored_stable: str | None,
    live_fence: LiveDocumentFence,
) -> CitationNavigateResult | None:
    """Common stable-document fence for scope and RAG citation identity.

    Rules
    -----
    - Stored stable is None: no stable claim → pass (caller continues).
    - Stored stable present, live fence missing active stable: fail-closed
      ``unavailable.live_stable_document_missing``.
    - Both present but different: ``identity_mismatch.stable_document``.
    - Both present and equal: pass.
    """
    if stored_stable is None:
        return None
    if live_fence.stable_document_id is None:
        return CitationNavigateResult(
            status="unavailable",
            reason="live_stable_document_missing",
        )
    if stored_stable != live_fence.stable_document_id:
        return CitationNavigateResult(
            status="identity_mismatch",
            reason="stable_document",
        )
    return None


def resolve_citation_navigation(
    *,
    citation_id: str,
    restricted_evidence: Any,
    live_fence: LiveDocumentFence,
) -> CitationNavigateResult:
    """Resolve one public citation_id against restricted evidence + live fence.

    Fail-closed on missing binding, incomplete locator, or identity / generation
    mismatch. Never returns handle_id or raw evidence blobs.
    """
    if not isinstance(citation_id, str) or not citation_id.strip():
        return CitationNavigateResult(status="not_found", reason="invalid_citation_id")

    if not isinstance(restricted_evidence, list):
        return CitationNavigateResult(
            status="unavailable",
            reason="no_restricted_evidence",
        )

    binding: dict[str, Any] | None = None
    for item in restricted_evidence:
        if not isinstance(item, dict):
            continue
        if item.get("citation_id") == citation_id:
            binding = item
            break

    if binding is None:
        return CitationNavigateResult(status="not_found", reason="citation_not_found")

    scope_raw = binding.get("evidence_scope")
    scope = scope_raw if isinstance(scope_raw, dict) else {}

    scope_record = _as_str(scope.get("reading_record_id"))
    scope_base = _as_str(scope.get("base_id"))
    scope_gen = _as_int(scope.get("record_generation"))
    scope_stable = _as_str(scope.get("stable_document_id"))

    if scope_record is None or scope_base is None or scope_gen is None:
        return CitationNavigateResult(
            status="unavailable",
            reason="legacy_scope_missing",
        )

    if scope_record != live_fence.reading_record_id:
        return CitationNavigateResult(
            status="identity_mismatch",
            reason="reading_record",
        )
    if scope_base != live_fence.base_id:
        return CitationNavigateResult(
            status="identity_mismatch",
            reason="base",
        )
    if scope_gen != live_fence.record_generation:
        return CitationNavigateResult(status="stale_generation")

    # evidence_scope stable-document fence (shared helper — not rag-only).
    scope_stable_result = _check_stable_document_fence(
        stored_stable=scope_stable,
        live_fence=live_fence,
    )
    if scope_stable_result is not None:
        return scope_stable_result

    # Binding-level stable claim (e.g. top-level field on restricted item).
    binding_stable = _as_str(binding.get("stable_document_id"))
    binding_stable_result = _check_stable_document_fence(
        stored_stable=binding_stable,
        live_fence=live_fence,
    )
    if binding_stable_result is not None:
        return binding_stable_result

    rag = binding.get("rag_citation")
    rag_dict = rag if isinstance(rag, dict) else None
    if rag_dict is not None:
        rag_stable = _as_str(rag_dict.get("stable_document_id"))
        rag_base = _as_str(rag_dict.get("base_id"))
        rag_gen = _as_int(rag_dict.get("record_generation"))
        if rag_base is not None and rag_base != live_fence.base_id:
            return CitationNavigateResult(
                status="identity_mismatch",
                reason="base",
            )
        if rag_gen is not None and rag_gen != live_fence.record_generation:
            return CitationNavigateResult(status="stale_generation")
        rag_stable_result = _check_stable_document_fence(
            stored_stable=rag_stable,
            live_fence=live_fence,
        )
        if rag_stable_result is not None:
            return rag_stable_result

        unit_ids = rag_dict.get("unit_ids")
        anchor_ids = rag_dict.get("anchor_segment_ids")
        unit_id = (
            unit_ids[0]
            if isinstance(unit_ids, list) and unit_ids and isinstance(unit_ids[0], str)
            else _as_str(binding.get("unit_id"))
        )
        anchor_segment_id = (
            anchor_ids[0]
            if isinstance(anchor_ids, list)
            and anchor_ids
            and isinstance(anchor_ids[0], str)
            else _as_str(binding.get("anchor_segment_id"))
        )
        start = _as_int(rag_dict.get("canonical_text_start_utf16"))
        end = _as_int(rag_dict.get("canonical_text_end_utf16"))
    else:
        unit_id = _as_str(binding.get("unit_id"))
        anchor_segment_id = _as_str(binding.get("anchor_segment_id"))
        start = None
        end = None

    if unit_id is None and anchor_segment_id is None:
        return CitationNavigateResult(status="unavailable", reason="no_locator")

    return CitationNavigateResult(
        status="ok",
        location=ArticleLocation(
            unit_id=unit_id,
            anchor_segment_id=anchor_segment_id,
            canonical_text_start_utf16=start,
            canonical_text_end_utf16=end,
        ),
    )
