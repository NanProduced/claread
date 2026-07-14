"""Production dependency wiring for agentic Reading Record Ask.

Resolves model, active stable document identity, and Article RAG port
without importing legacy reader_ask agent / prompt-bridge modules.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic_ai.models import Model

from app.config.settings import Settings, get_settings
from app.database import connection as db_connection
from app.services.reader_record_ask.article_rag_adapter import (
    RetrievalBackedArticleRagPort,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort

logger = logging.getLogger(__name__)


async def load_active_stable_document_id(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    expected_generation: int,
    expected_base_id: UUID,
    pool: Any | None = None,
) -> UUID | None:
    """Load active stable document for the current record/base/generation.

    Returns ``None`` when missing or mismatched (caller treats as not_ready
    for RAG; envelope may still be built without it).
    """
    db_pool = pool or db_connection.DB_POOL
    if db_pool is None:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id AS stable_document_id,
                   s.record_generation AS stable_generation,
                   r.generation AS record_generation,
                   r.active_base_id
            FROM reading_records r
            LEFT JOIN stable_reading_documents s
              ON s.reading_record_id = r.id
             AND s.status = 'active'
            WHERE r.id = $1
              AND r.user_id = $2
              AND r.deleted_at IS NULL
            """,
            reading_record_id,
            user_id,
        )
    if row is None:
        return None
    if int(row["record_generation"]) != expected_generation:
        return None
    active_base = row["active_base_id"]
    if active_base is None or UUID(str(active_base)) != expected_base_id:
        return None
    if row["stable_document_id"] is None:
        return None
    if int(row["stable_generation"]) != expected_generation:
        return None
    return UUID(str(row["stable_document_id"]))


def resolve_agentic_model(
    settings: Settings | None = None,
    *,
    explicit: Model | str | None = None,
) -> Model | str | None:
    """Return a validated model instance/string, or ``None`` if unconfigured.

    Explicit injection always wins (tests / callers).  Otherwise resolve
    the neutral ``reader_ask`` LLM route.  Never invents a stub success model.
    """
    if explicit is not None:
        return explicit
    cfg = settings or get_settings()
    try:
        from app.llm.router import build_model_for_route
        from app.llm.routes import MODEL_ROUTE_READER_ASK

        model, model_config = build_model_for_route(cfg, MODEL_ROUTE_READER_ASK, None)
        if model is None:
            logger.debug(
                "Agentic Ask model unresolved (route=reader_ask, config=%s)",
                model_config,
            )
            return None
        return model
    except Exception as exc:  # noqa: BLE001
        logger.debug("Agentic Ask model resolution failed: %s", type(exc).__name__)
        return None


def build_production_article_rag_port(
    settings: Settings | None = None,
    *,
    pool: Any | None = None,
) -> ArticleRagSearchPort | None:
    """Construct RetrievalBackedArticleRagPort when Article RAG is enabled.

    Returns ``None`` when feature is disabled so tools return typed
    unavailable without I/O.  When enabled, still builds the port even if
    providers are Unconfigured* — retrieval then fails closed with typed
    statuses (not_indexed / unavailable).
    """
    cfg = settings or get_settings()
    if not bool(getattr(cfg, "reader_article_rag_enabled", False)):
        return None

    try:
        from app.services.reader_orchestration.article_rag_embedding_provider import (
            build_default_article_rag_embedding_provider,
        )
        from app.services.reader_orchestration.article_rag_retrieval_service import (
            ArticleRagRetrievalService,
        )
        from app.services.reader_orchestration.article_rag_vector_search import (
            build_default_article_rag_vector_searcher,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Article RAG import failed: %s", type(exc).__name__)
        return None

    db_pool = pool or db_connection.DB_POOL
    embedding = build_default_article_rag_embedding_provider(cfg)
    searcher = build_default_article_rag_vector_searcher(cfg)
    retrieval = ArticleRagRetrievalService(
        pool=db_pool,
        embedding_provider=embedding,
        vector_searcher=searcher,
    )
    return RetrievalBackedArticleRagPort(retrieval=retrieval)
