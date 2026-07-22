"""Production Article RAG adapter for Reading Record Ask.

Wraps the neutral retrieval + optional lifecycle probe.  Does **not**
call the old Ask prompt attachment / integration / bridge modules.

``rag_substrate_id`` is taken from ``ArticleRagRetrievalResult.index_run_id``
— the exact indexed run used for that retrieval call.  There is **no**
post-retrieval SQL loader for "latest indexed run" (that races reindex).
"""

from __future__ import annotations

import re
from typing import Any, Protocol
from uuid import UUID

from app.services.reader_record_ask.article_rag_port import (
    ALLOWED_ASK_RAG_SOURCE_SCOPES,
    ArticleRagHitView,
    ArticleRagSearchOutcome,
)

_CONTENT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _RetrievalLike(Protocol):
    async def retrieve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = ...,
    ) -> Any: ...


class _LifecycleStatusProbe(Protocol):
    """Optional pre-search probe returning a lifecycle status string.

    Production can wrap
    ``ArticleRagIndexLifecycleService.load_article_rag_index_lifecycle_status``
    (which needs a DB connection) behind this zero-conn async callback.
    """

    async def __call__(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
    ) -> str: ...


def _plan_backed_content_sha256(hit: Any) -> str | None:
    """Return typed plan-backed content hash only.

    Accepts only the hit's first-class ``content_sha256`` field (64 hex).
    Never derives from chunk text, free-form metadata, or client payload —
    those are not plan-backed identity.
    """
    direct = getattr(hit, "content_sha256", None)
    if isinstance(direct, str) and _CONTENT_SHA256_RE.match(direct):
        return direct
    return None


def _hit_from_retrieval(
    hit: Any,
    *,
    envelope_record_id: UUID,
    envelope_base_id: UUID,
    envelope_generation: int,
    envelope_stable_document_id: UUID,
) -> ArticleRagHitView | None:
    """Map a retrieval hit to a view if it passes Ask eligibility filters.

    Rejects hits without plan-backed content_sha256 or canonical range.
    """
    citation = dict(getattr(hit, "citation", None) or {})
    metadata = dict(getattr(hit, "metadata_json", None) or {})
    source_scope = str(metadata.get("source_scope") or "")
    if source_scope not in ALLOWED_ASK_RAG_SOURCE_SCOPES:
        return None
    start = citation.get("canonical_text_start_utf16")
    end = citation.get("canonical_text_end_utf16")
    if start is None or end is None:
        return None
    try:
        start_i = int(start)
        end_i = int(end)
    except (TypeError, ValueError):
        return None
    if end_i <= start_i or start_i < 0:
        return None

    try:
        hit_record = UUID(str(citation.get("reading_record_id")))
        hit_base = UUID(str(citation.get("base_id")))
        hit_gen = int(citation.get("record_generation"))
        hit_doc = UUID(str(citation.get("stable_document_id")))
    except (TypeError, ValueError):
        return None

    if (
        hit_record != envelope_record_id
        or hit_base != envelope_base_id
        or hit_gen != envelope_generation
        or hit_doc != envelope_stable_document_id
    ):
        return None

    content_sha = _plan_backed_content_sha256(hit)
    if content_sha is None:
        return None

    block_type = str(metadata.get("block_type") or "")
    if not block_type:
        return None
    block_ids = tuple(str(x) for x in (citation.get("block_ids") or []))
    unit_ids = tuple(str(x) for x in (citation.get("unit_ids") or []))
    segment_ids = tuple(str(x) for x in (citation.get("anchor_segment_ids") or []))
    chunk_id = str(getattr(hit, "chunk_id", "") or "")
    if not chunk_id:
        return None

    return ArticleRagHitView(
        chunk_id=chunk_id,
        text=str(getattr(hit, "text", "") or ""),
        source_scope=source_scope,
        block_type=block_type,
        content_sha256=content_sha,
        canonical_text_start_utf16=start_i,
        canonical_text_end_utf16=end_i,
        score=float(getattr(hit, "score", 0.0) or 0.0),
        reading_record_id=hit_record,
        stable_document_id=hit_doc,
        base_id=hit_base,
        record_generation=hit_gen,
        block_ids=block_ids,
        unit_ids=unit_ids,
        anchor_segment_ids=segment_ids,
    )


def _map_lifecycle_status(status: str) -> ArticleRagSearchOutcome | None:
    """Map lifecycle status to a terminal port outcome, or None if ready to search."""
    if status == "not_ready":
        return ArticleRagSearchOutcome(
            status="not_ready",
            summary="Article RAG is not ready for this reading record",
            detail_code="lifecycle_not_ready",
        )
    if status == "not_indexed":
        return ArticleRagSearchOutcome(
            status="not_indexed",
            summary="Article RAG index has not been created for this record",
            detail_code="lifecycle_not_indexed",
        )
    if status in {"queued", "indexing"}:
        return ArticleRagSearchOutcome(
            status="indexing",
            summary="Article RAG index is still building",
            detail_code=f"lifecycle_{status}",
        )
    if status in {"failed", "superseded_or_stale", "unavailable"}:
        return ArticleRagSearchOutcome(
            status="unavailable",
            summary=f"Article RAG index is unavailable ({status})",
            detail_code=f"lifecycle_{status}",
        )
    # indexed / unknown — allow retrieval path
    return None


class RetrievalBackedArticleRagPort:
    """Adapter: lifecycle probe + ArticleRagRetrievalService.

    ``rag_substrate_id`` is always ``result.index_run_id`` from the same
    retrieval call that produced the hits.
    """

    def __init__(
        self,
        *,
        retrieval: _RetrievalLike,
        lifecycle_status_probe: _LifecycleStatusProbe | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._lifecycle_status_probe = lifecycle_status_probe

    async def search_current_article(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        record_generation: int,
        stable_document_id: UUID | None,
        query: str,
        limit: int,
    ) -> ArticleRagSearchOutcome:
        # Stable document identity is required — refuse search without it.
        if stable_document_id is None:
            return ArticleRagSearchOutcome(
                status="not_ready",
                summary=(
                    "Stable document identity is unknown; Article RAG search "
                    "is not available for this envelope"
                ),
                detail_code="stable_document_id_missing",
            )

        if self._lifecycle_status_probe is not None:
            try:
                life_status = await self._lifecycle_status_probe(
                    reading_record_id=reading_record_id,
                    user_id=user_id,
                )
                mapped = _map_lifecycle_status(str(life_status))
                if mapped is not None:
                    return mapped
            except Exception:  # noqa: BLE001 — fall through to retrieval
                pass

        try:
            from app.services.reader_orchestration.article_rag_retrieval_service import (
                FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
                FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH,
            )
        except ImportError:  # pragma: no cover
            FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN = "retrieval_no_indexed_run"
            FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH = "retrieval_plan_hash_mismatch"

        try:
            result = await self._retrieval.retrieve_for_record(
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query,
                limit=limit,
            )
        except LookupError:
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG record/scope not found",
                detail_code="lookup_error",
            )
        except Exception as exc:
            code = getattr(exc, "failure_code", None) or type(exc).__name__
            if code == FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN:
                return ArticleRagSearchOutcome(
                    status="not_indexed",
                    summary="No queryable Article RAG index run",
                    detail_code=str(code),
                )
            if code == FAILURE_CODE_RETRIEVAL_PLAN_HASH_MISMATCH:
                return ArticleRagSearchOutcome(
                    status="unavailable",
                    summary="Article RAG index is stale relative to current plan",
                    detail_code=str(code),
                )
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG retrieval failed",
                detail_code=str(code),
            )

        # Identity fence against envelope-requested scope.
        if (
            UUID(str(result.reading_record_id)) != reading_record_id
            or UUID(str(result.base_id)) != base_id
            or int(result.record_generation) != record_generation
        ):
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG result identity does not match envelope",
                detail_code="identity_mismatch",
            )
        if UUID(str(result.stable_document_id)) != stable_document_id:
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG stable_document_id does not match envelope",
                detail_code="stable_document_mismatch",
            )

        plan_hash = str(getattr(result, "plan_content_sha256", "") or "")
        if not _CONTENT_SHA256_RE.match(plan_hash):
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG result missing plan_content_sha256",
                detail_code="missing_plan_content_sha256",
            )

        index_run_raw = getattr(result, "index_run_id", None)
        if index_run_raw is None:
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary=(
                    "Article RAG retrieval result missing index_run_id; "
                    "refusing unanchored hits"
                ),
                detail_code="missing_index_run_id",
                stable_document_id=stable_document_id,
                base_id=base_id,
                record_generation=record_generation,
                plan_content_sha256=plan_hash,
            )
        try:
            substrate_id = UUID(str(index_run_raw))
        except (TypeError, ValueError):
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article RAG index_run_id is not a valid UUID",
                detail_code="invalid_index_run_id",
            )

        eligible: list[ArticleRagHitView] = []
        for hit in result.hits or ():
            view = _hit_from_retrieval(
                hit,
                envelope_record_id=reading_record_id,
                envelope_base_id=base_id,
                envelope_generation=record_generation,
                envelope_stable_document_id=stable_document_id,
            )
            if view is not None:
                eligible.append(view)

        if not eligible:
            return ArticleRagSearchOutcome(
                status="empty",
                summary=(
                    "Article RAG returned no eligible hits (main_reading_text/"
                    "heading, canonical UTF-16 range, plan content hash)"
                ),
                rag_substrate_id=substrate_id,
                plan_content_sha256=plan_hash,
                stable_document_id=stable_document_id,
                base_id=base_id,
                record_generation=record_generation,
                detail_code="no_eligible_hits",
            )

        return ArticleRagSearchOutcome(
            status="ok",
            summary=f"Article RAG returned {len(eligible)} eligible hit(s)",
            hits=tuple(eligible),
            rag_substrate_id=substrate_id,
            plan_content_sha256=plan_hash,
            stable_document_id=stable_document_id,
            base_id=base_id,
            record_generation=record_generation,
        )
