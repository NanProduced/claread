import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from app.contracts.annotation import (
    build_multi_text_target_key,
    build_sentence_target_key,
    build_text_range_target_key,
)
from app.database import connection as db_connect
from app.schemas.reader_notes import (
    ReaderNoteCreateRequest,
    ReaderNoteResponse,
    ReaderNoteUpdateRequest,
)
from app.schemas.user_annotations import UserAnnotationSegment
from app.services.text_anchors import (
    load_render_scene,
    sentence_map,
    validate_multi_text_against_render_scene,
    validate_text_range_against_render_scene,
)

_NOTE_FIELDS = (
    "id, analysis_record_id, anchor_sentence_id, quote_mode, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, note_text, payload_json, created_at, updated_at"
)


def _row_to_response(row: dict) -> ReaderNoteResponse:
    payload_json = row["payload_json"] if isinstance(row["payload_json"], dict) else json.loads(row["payload_json"] or "{}")
    raw_segments = payload_json.get("segments")
    segments = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, dict):
                segments.append(UserAnnotationSegment(**segment))
    return ReaderNoteResponse(
        id=row["id"],
        analysis_record_id=row["analysis_record_id"],
        anchor_sentence_id=row["anchor_sentence_id"],
        quote_mode=row["quote_mode"],
        target_key=row["target_key"],
        paragraph_id=row["paragraph_id"],
        sentence_id=row["sentence_id"],
        selected_text=row["selected_text"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        text_hash=row["text_hash"],
        segments=segments,
        note_text=row["note_text"],
        payload_json=payload_json,
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _build_target_key(req: ReaderNoteCreateRequest) -> str:
    if req.target_key:
        return req.target_key
    if req.quote_mode == "sentence":
        return build_sentence_target_key(req.analysis_record_id, req.sentence_id or "")
    if req.quote_mode == "multi_text":
        return build_multi_text_target_key(
            req.analysis_record_id,
            [segment.model_dump(mode="python") for segment in req.segments],
        )
    return build_text_range_target_key(
        req.analysis_record_id,
        req.sentence_id or "",
        req.start_offset or 0,
        req.end_offset or 0,
        req.text_hash or "",
    )


async def _validate_quote_against_record(
    conn,
    user_id: UUID,
    record_id: UUID,
    req: ReaderNoteCreateRequest,
) -> None:
    render_scene = await load_render_scene(conn, user_id, record_id)
    if req.quote_mode == "multi_text":
        validate_multi_text_against_render_scene(
            render_scene,
            [segment.model_dump(mode="python") for segment in req.segments],
        )
        if req.anchor_sentence_id != req.segments[0].sentence_id:
            raise HTTPException(status_code=400, detail="anchor_sentence_id must match first segment sentence_id")
        return
    if not req.sentence_id:
        raise HTTPException(status_code=400, detail="sentence_id is not present in render scene")
    if req.quote_mode == "sentence":
        sentence = sentence_map(render_scene).get(req.sentence_id)
        if sentence is None:
            raise HTTPException(status_code=400, detail="sentence_id is not present in render scene")
        sentence_text = sentence.get("text")
        if not isinstance(sentence_text, str):
            raise HTTPException(status_code=400, detail="sentence text is unavailable in render scene")
        if sentence_text != req.selected_text:
            raise HTTPException(status_code=400, detail="selected_text does not match full sentence text")
        if req.anchor_sentence_id != req.sentence_id:
            raise HTTPException(status_code=400, detail="anchor_sentence_id must match sentence_id for sentence notes")
        return
    if req.anchor_sentence_id != req.sentence_id:
        raise HTTPException(status_code=400, detail="anchor_sentence_id must match sentence_id for single-sentence notes")
    validate_text_range_against_render_scene(
        render_scene,
        {
            "paragraph_id": req.paragraph_id,
            "sentence_id": req.sentence_id,
            "selected_text": req.selected_text,
            "start_offset": req.start_offset,
            "end_offset": req.end_offset,
            "text_hash": req.text_hash,
        },
    )


async def create_reader_note(user_id: UUID, req: ReaderNoteCreateRequest) -> ReaderNoteResponse:
    try:
        record_id = UUID(req.analysis_record_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="analysis_record_id must be a UUID") from exc

    target_key = _build_target_key(req)
    async with db_connect.acquire_connection() as conn:
        await _validate_quote_against_record(conn, user_id, record_id, req)
        payload_json = dict(req.payload_json)
        if req.quote_mode == "multi_text":
            payload_json["segments"] = [segment.model_dump(mode="python") for segment in req.segments]
        row = await conn.fetchrow(
            f"""
            INSERT INTO reader_notes (
                user_id, analysis_record_id, anchor_sentence_id, quote_mode, target_key,
                paragraph_id, sentence_id, selected_text, start_offset, end_offset,
                text_hash, note_text, payload_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (user_id, analysis_record_id, target_key) DO UPDATE SET
                note_text = EXCLUDED.note_text,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = NOW()
            RETURNING {_NOTE_FIELDS}
            """,
            user_id,
            record_id,
            req.anchor_sentence_id,
            req.quote_mode,
            target_key,
            req.segments[0].paragraph_id if req.quote_mode == "multi_text" and req.segments else req.paragraph_id,
            req.segments[0].sentence_id if req.quote_mode == "multi_text" and req.segments else req.sentence_id,
            req.selected_text,
            None if req.quote_mode == "multi_text" else req.start_offset,
            None if req.quote_mode == "multi_text" else req.end_offset,
            None if req.quote_mode == "multi_text" else req.text_hash,
            req.note_text,
            payload_json,
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create reader note")
        return _row_to_response(dict(row))


async def list_reader_notes(user_id: UUID, record_id: str) -> list[ReaderNoteResponse]:
    try:
        parsed_record_id = UUID(record_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="analysis_record_id must be a UUID") from exc
    async with db_connect.acquire_connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_NOTE_FIELDS}
            FROM reader_notes
            WHERE user_id = $1 AND analysis_record_id = $2 AND deleted_at IS NULL
            ORDER BY anchor_sentence_id ASC, start_offset ASC NULLS FIRST, char_length(selected_text) ASC, created_at ASC
            """,
            user_id,
            parsed_record_id,
        )
        return [_row_to_response(dict(row)) for row in rows]


async def update_reader_note(user_id: UUID, note_id: UUID, req: ReaderNoteUpdateRequest) -> ReaderNoteResponse:
    async with db_connect.acquire_connection() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE reader_notes
            SET note_text = $1
            WHERE id = $2 AND user_id = $3 AND deleted_at IS NULL
            RETURNING {_NOTE_FIELDS}
            """,
            req.note_text,
            note_id,
            user_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Reader note not found or unauthorized")
        return _row_to_response(dict(row))


async def delete_reader_note(user_id: UUID, note_id: UUID) -> None:
    async with db_connect.acquire_connection() as conn:
        now = datetime.now(timezone.utc)
        result = await conn.execute(
            """
            UPDATE reader_notes
            SET deleted_at = $3, deleted_by = $1
            WHERE id = $2 AND user_id = $1 AND deleted_at IS NULL
            """,
            user_id,
            note_id,
            now,
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Reader note not found or unauthorized")
