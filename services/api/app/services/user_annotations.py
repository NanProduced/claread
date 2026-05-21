import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from app.contracts.annotation import (
    build_multi_text_target_key,
    build_sentence_target_key,
    build_text_range_target_key,
    utf16_code_unit_length,
)
from app.database import connection as db_connect
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
    UserAnnotationResponse,
)
from app.services.text_anchors import (
    load_render_scene,
    validate_multi_text_against_render_scene,
    validate_text_range_against_render_scene,
)

_ANNOTATION_FIELDS = (
    "id, analysis_record_id, anchor_type, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, color, payload_json, created_at, updated_at"
)


@dataclass(frozen=True, slots=True)
class _SingleSentenceRange:
    start_offset: int
    end_offset: int


def _range_from_annotation_row(row: dict) -> _SingleSentenceRange | None:
    anchor_type = row.get("anchor_type")
    if anchor_type == "sentence":
        selected_text = row.get("selected_text")
        if not isinstance(selected_text, str):
            return None
        return _SingleSentenceRange(0, utf16_code_unit_length(selected_text))
    if anchor_type == "text_range":
        start_offset = row.get("start_offset")
        end_offset = row.get("end_offset")
        if isinstance(start_offset, int) and isinstance(end_offset, int) and start_offset < end_offset:
            return _SingleSentenceRange(start_offset, end_offset)
    return None


def _range_from_request(req: UserAnnotationCreateRequest) -> _SingleSentenceRange | None:
    if req.anchor_type == "sentence":
        return _SingleSentenceRange(0, utf16_code_unit_length(req.selected_text))
    if req.anchor_type == "text_range" and req.start_offset is not None and req.end_offset is not None:
        if req.start_offset < req.end_offset:
            return _SingleSentenceRange(req.start_offset, req.end_offset)
    return None


def _is_subset(inner: _SingleSentenceRange, outer: _SingleSentenceRange) -> bool:
    return outer.start_offset <= inner.start_offset and inner.end_offset <= outer.end_offset


def _is_overlap(left: _SingleSentenceRange, right: _SingleSentenceRange) -> bool:
    return max(left.start_offset, right.start_offset) < min(left.end_offset, right.end_offset)


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
        payload_json=payload_json,
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _build_target_key(req: UserAnnotationCreateRequest) -> str:
    if req.target_key:
        return req.target_key
    record_part = req.analysis_record_id or "local"
    if req.anchor_type == "sentence":
        return build_sentence_target_key(record_part, req.sentence_id or "")
    if req.anchor_type == "multi_text":
        return build_multi_text_target_key(
            record_part,
            [segment.model_dump(mode="python") for segment in req.segments],
        )
    return build_text_range_target_key(
        record_part,
        req.sentence_id or "",
        req.start_offset or 0,
        req.end_offset or 0,
        req.text_hash or "",
    )


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


async def _resolve_single_sentence_conflict(
    conn,
    *,
    user_id: UUID,
    record_id: UUID,
    req: UserAnnotationCreateRequest,
    target_key: str,
) -> UserAnnotationResponse | None:
    if req.anchor_type not in {"sentence", "text_range"} or not req.sentence_id:
        return None

    request_range = _range_from_request(req)
    if request_range is None:
        return None

    rows = await conn.fetch(
        f"""
        SELECT {_ANNOTATION_FIELDS}
        FROM user_annotations
        WHERE user_id = $1
          AND analysis_record_id = $2
          AND sentence_id = $3
          AND deleted_at IS NULL
          AND anchor_type IN ('sentence', 'text_range')
        """,
        user_id,
        record_id,
        req.sentence_id,
    )
    overlapping_rows: list[dict] = []
    for row in rows:
        candidate = dict(row)
        if candidate.get("target_key") == target_key:
            continue
        candidate_range = _range_from_annotation_row(candidate)
        if candidate_range is None:
            continue
        if _is_overlap(candidate_range, request_range):
            overlapping_rows.append(candidate)

    if not overlapping_rows:
        return None

    if len(overlapping_rows) > 1:
        raise HTTPException(status_code=409, detail="Selection overlaps multiple highlights; please adjust the range")

    existing_row = overlapping_rows[0]
    existing_range = _range_from_annotation_row(existing_row)
    if existing_range is None:
        return None

    if _is_subset(request_range, existing_range):
        row = await conn.fetchrow(
            f"""
            UPDATE user_annotations
            SET color = $1,
                payload_json = $2::jsonb,
                updated_at = NOW()
            WHERE id = $3
            RETURNING {_ANNOTATION_FIELDS}
            """,
            req.color,
            json.dumps(dict(req.payload_json), ensure_ascii=False),
            existing_row["id"],
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to update existing highlight")
        return _row_to_response(dict(row))

    if _is_subset(existing_range, request_range):
        row = await conn.fetchrow(
            f"""
            UPDATE user_annotations
            SET anchor_type = $1,
                target_key = $2,
                paragraph_id = $3,
                sentence_id = $4,
                selected_text = $5,
                start_offset = $6,
                end_offset = $7,
                text_hash = $8,
                color = $9,
                payload_json = $10::jsonb,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = NOW()
            WHERE id = $11
            RETURNING {_ANNOTATION_FIELDS}
            """,
            req.anchor_type,
            target_key,
            req.paragraph_id,
            req.sentence_id,
            req.selected_text,
            req.start_offset,
            req.end_offset,
            req.text_hash,
            req.color,
            json.dumps(dict(req.payload_json), ensure_ascii=False),
            existing_row["id"],
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to extend existing highlight")
        return _row_to_response(dict(row))

    raise HTTPException(status_code=409, detail="Selection partially overlaps an existing highlight")


async def create_user_annotation(user_id: UUID, req: UserAnnotationCreateRequest) -> UserAnnotationResponse:
    target_key = _build_target_key(req)

    async with db_connect.acquire_connection() as conn:
        try:
            record_id = UUID(req.analysis_record_id) if req.analysis_record_id else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="analysis_record_id must be a UUID") from exc
        if req.anchor_type in {"text_range", "multi_text"}:
            if record_id is None:
                raise HTTPException(status_code=400, detail="analysis_record_id is required for text anchors")
            await _validate_text_anchor_against_record(conn, user_id, record_id, req)

        if record_id is not None:
            resolved_conflict = await _resolve_single_sentence_conflict(
                conn,
                user_id=user_id,
                record_id=record_id,
                req=req,
                target_key=target_key,
            )
            if resolved_conflict is not None:
                return resolved_conflict

        first_segment = _first_segment(req)
        payload_json = dict(req.payload_json)
        if req.anchor_type == "multi_text":
            payload_json["segments"] = [segment.model_dump(mode="python") for segment in req.segments]
        row = await conn.fetchrow(
            f"""
            INSERT INTO user_annotations (
                user_id, analysis_record_id, anchor_type, target_key,
                paragraph_id, sentence_id, selected_text, start_offset, end_offset,
                text_hash, color, payload_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (user_id, target_key) DO UPDATE SET
                anchor_type = EXCLUDED.anchor_type,
                paragraph_id = EXCLUDED.paragraph_id,
                sentence_id = EXCLUDED.sentence_id,
                selected_text = EXCLUDED.selected_text,
                start_offset = EXCLUDED.start_offset,
                end_offset = EXCLUDED.end_offset,
                text_hash = EXCLUDED.text_hash,
                color = EXCLUDED.color,
                payload_json = EXCLUDED.payload_json,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = NOW()
            RETURNING {_ANNOTATION_FIELDS}
            """,
            user_id,
            record_id,
            req.anchor_type,
            target_key,
            first_segment.paragraph_id if first_segment else req.paragraph_id,
            first_segment.sentence_id if first_segment else req.sentence_id,
            req.selected_text,
            None if req.anchor_type == "multi_text" else req.start_offset,
            None if req.anchor_type == "multi_text" else req.end_offset,
            None if req.anchor_type == "multi_text" else req.text_hash,
            req.color,
            payload_json,
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create user annotation")
        return _row_to_response(dict(row))


async def list_user_annotations(
    user_id: UUID,
    record_id: str | None = None,
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
    async with db_connect.acquire_connection() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE user_annotations
            SET color = $1
            WHERE id = $2 AND user_id = $3 AND deleted_at IS NULL
            RETURNING {_ANNOTATION_FIELDS}
            """,
            req.color,
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
