from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger(__name__)


async def insert_candidate_entry(
    *,
    query: str,
    normalized_query: str,
    query_type: str,
    classification: str,
    result_kind: str,
    confidence: str | None,
    generated_payload_json: dict,
    context_sentence: str,
    record_id: UUID | None,
    sentence_id: str | None,
    usage_event_id: UUID | None,
) -> UUID | None:
    pool = db_connection.DB_POOL
    if pool is None:
        logger.warning("Skipping dict_ai_candidate_entry because database pool is not initialized")
        return None

    try:
        async with pool.acquire() as conn:
            inserted_id = await conn.fetchval(
                """
                INSERT INTO dict_ai_candidate_entries (
                    query, normalized_query, query_type, classification, result_kind,
                    confidence, generated_payload_json, context_sentence,
                    record_id, sentence_id, usage_event_id,
                    review_status, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7::jsonb, $8,
                    $9, $10, $11,
                    'pending', $12, $12
                )
                RETURNING id
                """,
                query,
                normalized_query,
                query_type,
                classification,
                result_kind,
                confidence,
                json.dumps(generated_payload_json, ensure_ascii=False),
                context_sentence,
                record_id,
                sentence_id,
                usage_event_id,
                datetime.now(timezone.utc),
            )
        return inserted_id if isinstance(inserted_id, UUID) else None
    except Exception:
        logger.exception("Failed to persist dict_ai_candidate_entry(query=%s)", query)
        return None
