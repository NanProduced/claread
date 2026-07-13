from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.database import connection as db_connection
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskPersistedSupplement,
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


def _canonical_row_record_id(row: dict[str, Any]) -> str:
    record_id = row.get("reading_record_id") or row.get("analysis_record_id") or row.get("record_id")
    return str(record_id)


def _reading_record_target_key(anchor: ReaderAskReadingRecordAnchor) -> str:
    return (
        f"reading-record:{anchor.record_id}:segment:{anchor.anchor_segment_id}:"
        f"{anchor.start_offset}:{anchor.end_offset}"
    )


def candidate_to_projection(candidate: ReaderAskSupplementCandidate) -> dict[str, Any]:
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


def candidate_to_persisted_supplement(
    candidate: ReaderAskSupplementCandidate,
    *,
    record_id: str,
    record_title: str | None,
    created_at: str | None = None,
    lifecycle_status: str = "persisted",
) -> ReaderAskPersistedSupplement:
    return ReaderAskPersistedSupplement(
        supplement_id=candidate.candidate_id,
        supplement_type=candidate.supplement_type,
        lifecycle_status=lifecycle_status,
        record_id=record_id,
        record_title=record_title,
        target_key=candidate.target_key,
        sentence_id=candidate.sentence_id,
        paragraph_id=candidate.paragraph_id,
        title=candidate.title,
        content=candidate.content,
        schema_version=candidate.schema_version,
        created_from_turn_run_id=candidate.created_from_turn_run_id,
        created_at=created_at,
    )


def row_to_persisted_supplement(
    row: dict[str, Any],
    *,
    record_title: str | None = None,
    lifecycle_status: str = "persisted",
) -> ReaderAskPersistedSupplement:
    created_at = row.get("created_at")
    if hasattr(created_at, "astimezone"):
        created_at_iso = created_at.astimezone(UTC).isoformat()
    elif isinstance(created_at, str):
        created_at_iso = created_at
    else:
        created_at_iso = None
    return ReaderAskPersistedSupplement(
        supplement_id=str(row["id"]),
        supplement_type=str(row.get("supplement_type") or row.get("entry_type") or "grammar_note"),
        lifecycle_status=lifecycle_status,
        record_id=_canonical_row_record_id(row),
        record_title=record_title,
        target_key=str(row["target_key"]) if row.get("target_key") is not None else None,
        sentence_id=str(row["sentence_id"]) if row.get("sentence_id") is not None else None,
        paragraph_id=str(row["paragraph_id"]) if row.get("paragraph_id") else None,
        title=str(row.get("title") or "AI 补充语法旁注"),
        content=str(row.get("content_md") or ""),
        schema_version=str(row.get("schema_version") or _SUPPLEMENT_SCHEMA_VERSION),
        created_from_turn_run_id=str(row.get("created_from_turn_run_id") or ""),
        created_at=created_at_iso,
    )


async def create_supplement(
    *,
    user_id: UUID,
    record_id: UUID | None = None,
    reading_record_id: UUID | None = None,
    candidate: ReaderAskSupplementCandidate,
) -> dict[str, Any]:
    """Create a supplement. Idempotent: if a supplement with the same id already
    exists (from a previous confirm that partially succeeded), returns the
    existing row instead of raising a unique-constraint error."""
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = _iso_now()
    supplement_id = UUID(candidate.candidate_id)
    is_reading_record_candidate = isinstance(candidate.anchor, ReaderAskReadingRecordAnchor)
    if is_reading_record_candidate:
        anchor = candidate.anchor
        assert isinstance(anchor, ReaderAskReadingRecordAnchor)
        resolved_reading_record_id = reading_record_id or UUID(anchor.record_id)
        target_key = candidate.target_key or _reading_record_target_key(anchor)
        sentence_id = candidate.sentence_id or anchor.anchor_segment_id
    else:
        anchor = candidate.anchor
        assert isinstance(anchor, ReaderAskAnchorRef)
        if record_id is None:
            raise HTTPException(status_code=400, detail="Legacy supplement persistence requires record_id")
        resolved_reading_record_id = None
        target_key = candidate.target_key
        sentence_id = candidate.sentence_id

    async with pool.acquire() as conn:
        async with conn.transaction():
            if is_reading_record_candidate:
                is_active = await ReaderEventRuntime().is_active_fence(
                    conn,
                    record_id=resolved_reading_record_id,
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
                    $1, $2, $3, $4, $5,
                    $6, $7, $8,
                    $9, $10, $11::jsonb, $12::jsonb, $13,
                    $14, $15, $15,
                    $16, $17, $18, $19,
                    $20, $21, $22, $23
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
                None if is_reading_record_candidate else record_id,
                resolved_reading_record_id,
                candidate.supplement_type,
                target_key,
                sentence_id,
                candidate.paragraph_id,
                candidate.title,
                candidate.content,
                candidate.anchor.model_dump(mode="json"),
                candidate_to_projection(candidate),
                candidate.schema_version,
                candidate.created_from_turn_run_id,
                now,
                UUID(anchor.base_id) if is_reading_record_candidate else None,
                anchor.generation if is_reading_record_candidate else None,
                anchor.unit_id if is_reading_record_candidate else None,
                anchor.anchor_segment_id if is_reading_record_candidate else None,
                anchor.start_offset if is_reading_record_candidate else None,
                anchor.end_offset if is_reading_record_candidate else None,
                anchor.text_hash if is_reading_record_candidate else None,
                anchor.hash_algorithm if is_reading_record_candidate else None,
            )
            # ON CONFLICT DO NOTHING returns None — fetch the existing row
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
                    WHERE id = $1
                    """,
                    supplement_id,
                )
            else:
                reading_record_id = row.get("reading_record_id")
                base_id = row.get("base_id")
                generation = row.get("generation")
                if (
                    reading_record_id is not None
                    and base_id is not None
                    and generation is not None
                ):
                    is_active = await ReaderEventRuntime().is_active_fence(
                        conn,
                        record_id=reading_record_id,
                        base_id=base_id,
                        generation=int(generation),
                    )
                    if is_active:
                        payload = build_representation_payload(
                            representation_section="ask_supplements",
                            operation="upsert",
                            generation=int(generation),
                            base_id=str(base_id),
                            target_keys=[str(row["id"])],
                        )
                        await ReaderEventRuntime().publish_event_in_transaction(
                            conn,
                            record_id=reading_record_id,
                            event_type="projection_ops",
                            payload_json=payload,
                        )
    assert row is not None
    return dict(row)


async def list_supplements_for_record(user_id: UUID, record_id: UUID) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, analysis_record_id, reading_record_id, supplement_type,
                   target_key, sentence_id, paragraph_id,
                   title, content_md, anchor_payload_json, metadata_json, schema_version,
                   created_from_turn_run_id, created_at, updated_at, deleted_at,
                   base_id, generation, unit_id, anchor_segment_id,
                   start_offset, end_offset, text_hash, hash_algorithm
            FROM reader_ask_supplements
            WHERE user_id = $1 AND analysis_record_id = $2 AND deleted_at IS NULL
            ORDER BY created_at ASC
            """,
            user_id,
            record_id,
        )
    return [dict(row) for row in rows]


async def list_supplements_for_reading_record(
    user_id: UUID,
    reading_record_id: UUID,
) -> list[dict[str, Any]]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, analysis_record_id, reading_record_id, supplement_type,
                   target_key, sentence_id, paragraph_id,
                   title, content_md, anchor_payload_json, metadata_json, schema_version,
                   created_from_turn_run_id, created_at, updated_at, deleted_at,
                   base_id, generation, unit_id, anchor_segment_id,
                   start_offset, end_offset, text_hash, hash_algorithm
            FROM reader_ask_supplements
            WHERE user_id = $1 AND reading_record_id = $2 AND deleted_at IS NULL
            ORDER BY created_at ASC
            """,
            user_id,
            reading_record_id,
        )
    return [dict(row) for row in rows]


async def delete_supplement(user_id: UUID, supplement_id: UUID) -> dict[str, Any] | None:
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
                reading_record_id = row.get("reading_record_id")
                base_id = row.get("base_id")
                generation = row.get("generation")
                if (
                    reading_record_id is not None
                    and base_id is not None
                    and generation is not None
                ):
                    is_active = await ReaderEventRuntime().is_active_fence(
                        conn,
                        record_id=reading_record_id,
                        base_id=base_id,
                        generation=int(generation),
                    )
                    if is_active:
                        payload = build_representation_payload(
                            representation_section="ask_supplements",
                            operation="delete",
                            generation=int(generation),
                            base_id=str(base_id),
                            target_keys=[str(row["id"])],
                        )
                        await ReaderEventRuntime().publish_event_in_transaction(
                            conn,
                            record_id=reading_record_id,
                            event_type="projection_ops",
                            payload_json=payload,
                        )
    return dict(row) if row is not None else None


def supplement_projection_entry(row: dict[str, Any]) -> dict[str, Any]:
    supplement_id = str(row["id"])
    return {
        "id": _entry_id(supplement_id),
        "sentence_id": row.get("sentence_id"),
        "entry_type": row["supplement_type"],
        "label": "AI 补充语法旁注",
        "title": row.get("title") or "AI 补充语法旁注",
        "content": row.get("content_md") or "",
        "source_kind": "ask_supplement",
        "supplement_id": supplement_id,
        "deletable": True,
        "target_key": row.get("target_key"),
        "paragraph_id": row.get("paragraph_id"),
        "created_from_turn_run_id": row.get("created_from_turn_run_id"),
        "schema_version": row.get("schema_version") or _SUPPLEMENT_SCHEMA_VERSION,
        "lifecycle_status": "persisted",
    }


def merge_supplements_into_render_scene(
    render_scene_json: dict[str, Any],
    supplements: list[dict[str, Any]],
) -> dict[str, Any]:
    scene = dict(render_scene_json)
    sentence_entries = scene.get("sentence_entries")
    if not isinstance(sentence_entries, list):
        sentence_entries = []
    merged = list(sentence_entries)
    existing_ids = {
        str(entry.get("id"))
        for entry in merged
        if isinstance(entry, dict) and entry.get("id") is not None
    }
    for supplement in supplements:
        projection = supplement_projection_entry(supplement)
        if str(projection.get("id")) in existing_ids:
            continue
        merged.append(projection)
    scene["sentence_entries"] = merged
    return scene


def build_grammar_note_candidate(
    *,
    anchor: ReaderAskAnchorRef | ReaderAskReadingRecordAnchor,
    assistant_content_md: str,
    created_from_turn_run_id: str,
) -> ReaderAskSupplementCandidate | None:
    if isinstance(anchor, ReaderAskReadingRecordAnchor):
        sentence_id = anchor.anchor_segment_id
        target_key = _reading_record_target_key(anchor)
        paragraph_id = None
        title = anchor.selected_text or "AI 补充语法旁注"
    else:
        sentence_id = anchor.sentence_id
        target_key = anchor.target_key
        paragraph_id = anchor.paragraph_id
        title = anchor.label or anchor.selected_text or "AI 补充语法旁注"
        if not sentence_id or not target_key:
            return None

    content = assistant_content_md.strip()
    if not content or len(content) < 60:
        return None

    if len(title) > 60:
        title = f"{title[:57]}..."

    return ReaderAskSupplementCandidate(
        candidate_id=str(uuid4()),
        supplement_type="grammar_note",
        target_key=target_key,
        sentence_id=sentence_id,
        paragraph_id=paragraph_id,
        title=title,
        content=content,
        anchor=anchor,
        schema_version=_SUPPLEMENT_SCHEMA_VERSION,
        created_from_turn_run_id=created_from_turn_run_id,
    )


async def get_supplement_projection_or_404(user_id: UUID, supplement_id: UUID) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, analysis_record_id, reading_record_id, supplement_type,
                   target_key, sentence_id, paragraph_id,
                   title, content_md, anchor_payload_json, metadata_json, schema_version,
                   created_from_turn_run_id, created_at, updated_at, deleted_at,
                   base_id, generation, unit_id, anchor_segment_id,
                   start_offset, end_offset, text_hash, hash_algorithm
            FROM reader_ask_supplements
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            supplement_id,
            user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Reader ask supplement not found")
    return dict(row)
