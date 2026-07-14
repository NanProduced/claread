"""Neutral Article RAG search port for Reading Record Ask.

This port is the *only* seam the new agent uses for article search.
It intentionally does not import the old Ask prompt-integration bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

# Allowed source scopes for first-wave Reading Record Ask RAG.
ALLOWED_ASK_RAG_SOURCE_SCOPES: frozenset[str] = frozenset(
    {"main_reading_text", "heading"}
)

ArticleRagPortStatus = Literal[
    "ok",
    "empty",
    "not_ready",
    "not_indexed",
    "indexing",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class ArticleRagHitView:
    """One plan-backed hit eligible for Ask evidence after filtering."""

    chunk_id: str
    text: str
    source_scope: str
    block_type: str
    content_sha256: str
    canonical_text_start_utf16: int
    canonical_text_end_utf16: int
    score: float
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    block_ids: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    anchor_segment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArticleRagSearchOutcome:
    """Result of a single envelope-scoped Article RAG search.

    ``rag_substrate_id`` is the immutable ``reader_article_rag_index_runs.id``
    when status is ``ok`` or ``empty`` after a successful index resolve.
    Missing substrate identity forces ``unavailable`` — never invent an id.
    """

    status: ArticleRagPortStatus
    summary: str
    hits: tuple[ArticleRagHitView, ...] = ()
    rag_substrate_id: UUID | None = None
    index_version: str | None = None
    plan_content_sha256: str | None = None
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    record_generation: int | None = None
    detail_code: str | None = None


class ArticleRagSearchPort(Protocol):
    """Injected port — production wraps retrieval; tests use fakes."""

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
    ) -> ArticleRagSearchOutcome: ...


@dataclass(slots=True)
class FakeArticleRagSearchPort:
    """Scripted port for unit tests (no real embedding / vector I/O)."""

    outcomes: list[ArticleRagSearchOutcome] = field(default_factory=list)
    call_count: int = 0
    last_query: str | None = None
    last_limit: int | None = None

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
        del user_id, reading_record_id, base_id, record_generation, stable_document_id
        self.call_count += 1
        self.last_query = query
        self.last_limit = limit
        if not self.outcomes:
            return ArticleRagSearchOutcome(
                status="unavailable",
                summary="Fake Article RAG port has no scripted outcomes",
                detail_code="fake_empty_script",
            )
        index = min(self.call_count - 1, len(self.outcomes) - 1)
        return self.outcomes[index]
