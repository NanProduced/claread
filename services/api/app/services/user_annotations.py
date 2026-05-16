import json
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException

from app.contracts.annotation import (
    build_multi_text_target_key,
)
from app.database import connection as db_connect
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
    UserAnnotationResponse,
)
from app.services.text_anchors import (
    ensure_json_dict,
    load_render_scene,
    validate_multi_text_against_render_scene,
    validate_text_range_against_render_scene,
)

_ANNOTATION_FIELDS = (
    "id, analysis_record_id, annotation_type, anchor_type, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, color, note, payload_json, created_at, updated_at"
)


def _row_to_response(row: dict) -> UserAnnotationResponse:
    payload_json = row["payload_json"] if isinstance(row["payload_json"], dict) else json.loads(row["payload_json"] or "{}")
    raw_segments = payload_json.get("segments")
    segments = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, dict):
                segments.append(UserAnnotationSegment(**segment))
    return UserAnnotationResponse(
        id=row["id"],
        analysis_record_id=row["analysis_record_id"],
        annotation_type=row["annotation_type"],
        anchor_type=row["anchor_type"],
        target_key=row["target_key"],
        paragraph_id=row["paragraph_id"],
        sentence_id=row["sentence_id"],
        selected_text=row["selected_text"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        text_hash=row["text_hash"],
        segments=segments,
        color=row["color"],
        note=row["note"],
        payload_json=payload_json,
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _build_target_key(req: UserAnnotationCreateRequest) -> str:
    if req.target_key:
        return req.target_key
    record_part = req.analysis_record_id or "local"
    if req.anchor_type == "sentence":
        return f"record:{record_part}:sentence:{req.sentence_id}"
    if req.anchor_type == "paragraph":
        return f"record:{record_part}:paragraph:{req.paragraph_id or req.sentence_id}"
    if req.anchor_type == "multi_text":
        return build_multi_text_target_key(
            record_part,
            [segment.model_dump(mode="python") for segment in req.segments],
        )
    return f"record:{record_part}:range:{req.sentence_id}:{req.start_offset}:{req.end_offset}:{req.text_hash or ''}"


def _first_segment(req: UserAnnotationCreateRequest) -> UserAnnotationSegment | None:
    return req.segments[0] if req.segments else None


async def _validate_text_anchor_against_record(
    conn,
    user_id: UUID,
    record_id: UUID,
    req: UserAnnotationCreateRequest,
) -> None:
    render_scene = await load_render_scene(conn, user_id, record_id)
    if req.anchor_type == "multi_text":
        validate_multi_text_against_render_scene(
            render_scene,
            [segment.model_dump(mode="python") for segment in req.segments],
        )
        return

    if not req.sentence_id:
        raise HTTPException(status_code=400, detail="sentence_id is not present in render scene")
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


async def create_user_annotation(user_id: UUID, req: UserAnnotationCreateRequest) -> UserAnnotationResponse:
    target_key = _build_target_key(req)
    annotation_type = req.annotation_type
    if req.note:
        annotation_type = "note"

    async with db_connect.acquire_connection() as conn:
        try:
            record_id = UUID(req.analysis_record_id) if req.analysis_record_id else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="analysis_record_id must be a UUID") from exc
        if req.anchor_type in {"text_range", "multi_text"}:
            if record_id is None:
                raise HTTPException(status_code=400, detail="analysis_record_id is required for text anchors")
            await _validate_text_anchor_against_record(conn, user_id, record_id, req)

        first_segment = _first_segment(req)
        payload_json = dict(req.payload_json)
        if req.anchor_type == "multi_text":
            payload_json["segments"] = [segment.model_dump(mode="python") for segment in req.segments]
        row = await conn.fetchrow(
            f"""
            INSERT INTO user_annotations (
                user_id, analysis_record_id, annotation_type, anchor_type, target_key,
                paragraph_id, sentence_id, selected_text, start_offset, end_offset,
                text_hash, color, note, payload_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (user_id, target_key) DO UPDATE SET
                annotation_type = EXCLUDED.annotation_type,
                anchor_type = EXCLUDED.anchor_type,
                paragraph_id = EXCLUDED.paragraph_id,
                sentence_id = EXCLUDED.sentence_id,
                selected_text = EXCLUDED.selected_text,
                start_offset = EXCLUDED.start_offset,
                end_offset = EXCLUDED.end_offset,
                text_hash = EXCLUDED.text_hash,
                color = EXCLUDED.color,
                note = COALESCE(EXCLUDED.note, user_annotations.note),
                payload_json = EXCLUDED.payload_json,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = NOW()
            RETURNING {_ANNOTATION_FIELDS}
            """,
            user_id,
            record_id,
            annotation_type,
            req.anchor_type,
            target_key,
            first_segment.paragraph_id if first_segment else req.paragraph_id,
            first_segment.sentence_id if first_segment else req.sentence_id,
            req.selected_text,
            None if req.anchor_type == "multi_text" else req.start_offset,
            None if req.anchor_type == "multi_text" else req.end_offset,
            None if req.anchor_type == "multi_text" else req.text_hash,
            req.color,
            req.note,
            payload_json,
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create user annotation")
        return _row_to_response(dict(row))


async def list_user_annotations(
    user_id: UUID,
    record_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[UserAnnotationResponse]:
    async with db_connect.acquire_connection() as conn:
        if record_id:
            try:
                parsed_record_id = UUID(record_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="analysis_record_id must be a UUID") from exc
            rows = await conn.fetch(
                f"""
                SELECT {_ANNOTATION_FIELDS}
                FROM user_annotations
                WHERE user_id = $1 AND analysis_record_id = $2 AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_id,
                parsed_record_id,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_ANNOTATION_FIELDS}
                FROM user_annotations
                WHERE user_id = $1 AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )

        return [_row_to_response(dict(row)) for row in rows]


async def update_user_annotation(user_id: UUID, annotation_id: UUID, req: UserAnnotationUpdateRequest) -> UserAnnotationResponse:
    note_supplied = "note" in req.model_fields_set
    async with db_connect.acquire_connection() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE user_annotations
            SET color = COALESCE($1, color),
                note = CASE WHEN $2 THEN $3 ELSE note END
            WHERE id = $4 AND user_id = $5 AND deleted_at IS NULL
            RETURNING {_ANNOTATION_FIELDS}
            """,
            req.color,
            note_supplied,
            req.note,
            annotation_id,
            user_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Annotation not found or unauthorized")

        return _row_to_response(dict(row))


async def delete_user_annotation(user_id: UUID, annotation_id: UUID) -> None:
    async with db_connect.acquire_connection() as conn:
        now = datetime.now(timezone.utc)
        result = await conn.execute(
            """
            UPDATE user_annotations
            SET deleted_at = $3, deleted_by = $1
            WHERE id = $2 AND user_id = $1 AND deleted_at IS NULL
            """,
            user_id,
            annotation_id,
            now,
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Annotation not found or unauthorized")
