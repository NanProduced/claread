from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.database import connection as db_connection
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskSupplementCandidate

_SUPPLEMENT_SCHEMA_VERSION = "reader-ask-supplement-v1"


def _iso_now() -> datetime:
    return datetime.now(UTC)


def _entry_id(supplement_id: str) -> str:
    return f"ask-supplement:{supplement_id}"


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
    }


async def create_supplement(
    *,
    user_id: UUID,
    record_id: UUID,
    candidate: ReaderAskSupplementCandidate,
) -> dict[str, Any]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = _iso_now()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reader_ask_supplements (
                id, user_id, record_id, supplement_type, target_key, sentence_id, paragraph_id,
                title, content_md, anchor_payload_json, metadata_json, schema_version,
                created_from_turn_run_id, created_at, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10::jsonb, $11::jsonb, $12,
                $13, $14, $14
            )
            RETURNING id, record_id, supplement_type, target_key, sentence_id, paragraph_id,
                      title, content_md, anchor_payload_json, metadata_json, schema_version,
                      created_from_turn_run_id, created_at, updated_at, deleted_at
            """,
            UUID(candidate.candidate_id),
            user_id,
            record_id,
            candidate.supplement_type,
            candidate.target_key,
            candidate.sentence_id,
            candidate.paragraph_id,
            candidate.title,
            candidate.content,
            candidate.anchor.model_dump(mode="json"),
            candidate_to_projection(candidate),
            candidate.schema_version,
            candidate.created_from_turn_run_id,
            now,
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
            SELECT id, record_id, supplement_type, target_key, sentence_id, paragraph_id,
                   title, content_md, anchor_payload_json, metadata_json, schema_version,
                   created_from_turn_run_id, created_at, updated_at, deleted_at
            FROM reader_ask_supplements
            WHERE user_id = $1 AND record_id = $2 AND deleted_at IS NULL
            ORDER BY created_at ASC
            """,
            user_id,
            record_id,
        )
    return [dict(row) for row in rows]


async def delete_supplement(user_id: UUID, supplement_id: UUID) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = _iso_now()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE reader_ask_supplements
            SET deleted_at = $3, updated_at = $3
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            RETURNING id
            """,
            supplement_id,
            user_id,
            now,
        )
    return row is not None


def supplement_projection_entry(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json")
    if isinstance(metadata, dict) and metadata:
        return metadata
    supplement_id = str(row["id"])
    return {
        "id": _entry_id(supplement_id),
        "sentence_id": row["sentence_id"],
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
    anchor: ReaderAskAnchorRef,
    assistant_content_md: str,
    created_from_turn_run_id: str,
) -> ReaderAskSupplementCandidate | None:
    sentence_id = anchor.sentence_id
    target_key = anchor.target_key
    if not sentence_id or not target_key:
        return None

    content = assistant_content_md.strip()
    if not content or len(content) < 20:
        return None

    title = anchor.label or anchor.selected_text or "AI 补充语法旁注"
    if len(title) > 60:
        title = f"{title[:57]}..."

    return ReaderAskSupplementCandidate(
        candidate_id=str(uuid4()),
        supplement_type="grammar_note",
        target_key=target_key,
        sentence_id=sentence_id,
        paragraph_id=anchor.paragraph_id,
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
            SELECT id, record_id, supplement_type, target_key, sentence_id, paragraph_id,
                   title, content_md, anchor_payload_json, metadata_json, schema_version,
                   created_from_turn_run_id, created_at, updated_at, deleted_at
            FROM reader_ask_supplements
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            supplement_id,
            user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Reader ask supplement not found")
    return dict(row)
