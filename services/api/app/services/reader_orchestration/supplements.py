"""Reading Record supplement persistence and projection events.

Supplements remain a shared Reading Record representation concern.  The
execution chain that used to own this helper is gone; this module keeps the
table and projection-event semantics needed by the current Reader surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.database import connection as db_connection
from app.schemas.reader_ask import (
    ReaderAskReadingRecordAnchor,
    ReaderAskSupplementCandidate,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.representation_event_payload import (
    build_representation_payload,
)

_SUPPLEMENT_SCHEMA_VERSION = "reader-ask-supplement-v1"


def _iso_now() -> datetime:
    return datetime.now(UTC)


def _entry_id(supplement_id: str) -> str:
    return f"ask-supplement:{supplement_id}"


def _reading_record_target_key(anchor: ReaderAskReadingRecordAnchor) -> str:
    return (
        f"reading-record:{anchor.record_id}:segment:{anchor.anchor_segment_id}:"
        f"{anchor.start_offset}:{anchor.end_offset}"
    )


def candidate_to_projection(candidate: ReaderAskSupplementCandidate) -> dict[str, Any]:
    """Build the non-content projection metadata written with a candidate."""
    return {
        "id": _entry_id(candidate.candidate_id),
        "sentence_id": candidate.sentence_id,
        "entry_type": candidate.supplement_type,
        "label": candidate.label,
        "title": candidate.title,
        "content": candidate.content,
        "source_kind": "ask_supplement",
        "supplement_id": candidate.candidate_id,
        "deletable": True,
        "target_key": candidate.target_key,
        "paragraph_id": candidate.paragraph_id,
        "created_from_turn_run_id": candidate.created_from_turn_run_id,
        "schema_version": candidate.schema_version,
        "lifecycle_status": candidate.lifecycle_status,
    }


async def create_supplement(
    *,
    user_id: UUID,
    reading_record_id: UUID | None = None,
    candidate: ReaderAskSupplementCandidate,
) -> dict[str, Any]:
    """Create an idempotent Reading Record supplement and publish its upsert."""
    if not isinstance(candidate.anchor, ReaderAskReadingRecordAnchor):
        raise HTTPException(
            status_code=400,
            detail="Reading Record supplement anchor required",
        )

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    anchor = candidate.anchor
    resolved_record_id = reading_record_id or UUID(anchor.record_id)
    target_key = candidate.target_key or _reading_record_target_key(anchor)
    sentence_id = candidate.sentence_id or anchor.anchor_segment_id
    now = _iso_now()
    supplement_id = UUID(candidate.candidate_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            is_active = await ReaderEventRuntime().is_active_fence(
                conn,
                record_id=resolved_record_id,
                base_id=UUID(anchor.base_id),
                generation=anchor.generation,
            )
            if not is_active:
                raise HTTPException(
                    status_code=409,
                    detail="Reading record changed; refresh and try again.",
                )

            row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_supplements (
                    id, user_id, analysis_record_id, reading_record_id, supplement_type,
                    target_key, sentence_id, paragraph_id,
                    title, content_md, anchor_payload_json, metadata_json, schema_version,
                    created_from_turn_run_id, created_at, updated_at,
                    base_id, generation, unit_id, anchor_segment_id,
                    start_offset, end_offset, text_hash, hash_algorithm
                )
                VALUES (
                    $1, $2, NULL, $3, $4,
                    $5, $6, $7,
                    $8, $9, $10::jsonb, $11::jsonb, $12,
                    $13, $14, $14,
                    $15, $16, $17, $18,
                    $19, $20, $21, $22
                )
                ON CONFLICT (id) DO NOTHING
                RETURNING id, analysis_record_id, reading_record_id, supplement_type,
                          target_key, sentence_id, paragraph_id,
                          title, content_md, anchor_payload_json, metadata_json, schema_version,
                          created_from_turn_run_id, created_at, updated_at, deleted_at,
                          base_id, generation, unit_id, anchor_segment_id,
                          start_offset, end_offset, text_hash, hash_algorithm
                """,
                supplement_id,
                user_id,
                resolved_record_id,
                candidate.supplement_type,
                target_key,
                sentence_id,
                candidate.paragraph_id,
                candidate.title,
                candidate.content,
                candidate.anchor.model_dump(mode="json"),
                candidate_to_projection(candidate),
                candidate.schema_version or _SUPPLEMENT_SCHEMA_VERSION,
                candidate.created_from_turn_run_id,
                now,
                UUID(anchor.base_id),
                anchor.generation,
                anchor.unit_id,
                anchor.anchor_segment_id,
                anchor.start_offset,
                anchor.end_offset,
                anchor.text_hash,
                anchor.hash_algorithm,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT id, analysis_record_id, reading_record_id, supplement_type,
                           target_key, sentence_id, paragraph_id,
                           title, content_md, anchor_payload_json, metadata_json, schema_version,
                           created_from_turn_run_id, created_at, updated_at, deleted_at,
                           base_id, generation, unit_id, anchor_segment_id,
                           start_offset, end_offset, text_hash, hash_algorithm
                    FROM reader_ask_supplements
                    WHERE id = $1 AND user_id = $2
                    """,
                    supplement_id,
                    user_id,
                )
            else:
                payload = build_representation_payload(
                    representation_section="ask_supplements",
                    operation="upsert",
                    generation=anchor.generation,
                    base_id=str(anchor.base_id),
                    target_keys=[str(row["id"])],
                )
                await ReaderEventRuntime().publish_event_in_transaction(
                    conn,
                    record_id=resolved_record_id,
                    event_type="projection_ops",
                    payload_json=payload,
                )

    assert row is not None
    return dict(row)


async def delete_supplement(
    user_id: UUID,
    supplement_id: UUID,
) -> dict[str, Any] | None:
    """Soft-delete a Reading Record supplement and publish its delete."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = _iso_now()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE reader_ask_supplements
                SET deleted_at = $3, updated_at = $3
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                RETURNING id, analysis_record_id, reading_record_id, supplement_type,
                          target_key, sentence_id, paragraph_id,
                          title, content_md, anchor_payload_json, metadata_json, schema_version,
                          created_from_turn_run_id, created_at, updated_at, deleted_at,
                          base_id, generation, unit_id, anchor_segment_id,
                          start_offset, end_offset, text_hash, hash_algorithm
                """,
                supplement_id,
                user_id,
                now,
            )
            if row is not None:
                record_id = row.get("reading_record_id")
                base_id = row.get("base_id")
                generation = row.get("generation")
                if record_id is not None and base_id is not None and generation is not None:
                    if await ReaderEventRuntime().is_active_fence(
                        conn,
                        record_id=record_id,
                        base_id=base_id,
                        generation=int(generation),
                    ):
                        payload = build_representation_payload(
                            representation_section="ask_supplements",
                            operation="delete",
                            generation=int(generation),
                            base_id=str(base_id),
                            target_keys=[str(row["id"])],
                        )
                        await ReaderEventRuntime().publish_event_in_transaction(
                            conn,
                            record_id=record_id,
                            event_type="projection_ops",
                            payload_json=payload,
                        )
    return dict(row) if row is not None else None
