import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi import HTTPException

from app.contracts.annotation import (
    build_multi_text_target_key,
    build_sentence_target_key,
    build_text_range_target_key,
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connect
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationResponse,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
)
from app.services.text_anchors import (
    load_render_scene,
    sentence_map,
    validate_multi_text_against_render_scene,
    validate_text_range_against_render_scene,
)

_ANNOTATION_FIELDS = (
    "id, analysis_record_id, anchor_type, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, color, payload_json, created_at, updated_at"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SingleSentenceRange:
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class _NormalizedSegment:
    sentence_id: str
    paragraph_id: str | None
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
        if (
            isinstance(start_offset, int)
            and isinstance(end_offset, int)
            and start_offset < end_offset
        ):
            return _SingleSentenceRange(start_offset, end_offset)
    return None


def _range_from_request(req: UserAnnotationCreateRequest) -> _SingleSentenceRange | None:
    if req.anchor_type == "sentence":
        return _SingleSentenceRange(0, utf16_code_unit_length(req.selected_text))
    if (
        req.anchor_type == "text_range"
        and req.start_offset is not None
        and req.end_offset is not None
    ):
        if req.start_offset < req.end_offset:
            return _SingleSentenceRange(req.start_offset, req.end_offset)
    return None


def _is_subset(inner: _SingleSentenceRange, outer: _SingleSentenceRange) -> bool:
    return outer.start_offset <= inner.start_offset and inner.end_offset <= outer.end_offset


def _is_overlap(left: _SingleSentenceRange, right: _SingleSentenceRange) -> bool:
    return max(left.start_offset, right.start_offset) < min(left.end_offset, right.end_offset)


def _compute_merged_range(
    existing_rows: list[dict],
    request_range: _SingleSentenceRange,
) -> _SingleSentenceRange:
    """计算所有重叠行与请求的并集范围。"""
    start = request_range.start_offset
    end = request_range.end_offset
    for row in existing_rows:
        row_range = _range_from_annotation_row(row)
        if row_range:
            start = min(start, row_range.start_offset)
            end = max(end, row_range.end_offset)
    return _SingleSentenceRange(start, end)


def _resolve_merged_color(existing_rows: list[dict], request_color: str) -> str:
    """所有已有高亮颜色一致则保留，否则用请求颜色。"""
    colors = {row["color"] for row in existing_rows}
    if len(colors) == 1:
        return colors.pop()
    return request_color


def _payload_json_from_row(row: dict) -> dict:
    payload_json = row.get("payload_json")
    if isinstance(payload_json, dict):
        return dict(payload_json)
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            parsed = json.loads(payload_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalized_segments_from_row(row: dict) -> list[_NormalizedSegment]:
    anchor_type = row.get("anchor_type")
    sentence_id = row.get("sentence_id")
    paragraph_id = row.get("paragraph_id")

    if anchor_type == "sentence":
        selected_text = row.get("selected_text")
        if isinstance(sentence_id, str) and isinstance(selected_text, str) and selected_text:
            return [
                _NormalizedSegment(
                    sentence_id=sentence_id,
                    paragraph_id=paragraph_id if isinstance(paragraph_id, str) else None,
                    start_offset=0,
                    end_offset=utf16_code_unit_length(selected_text),
                )
            ]
        return []

    if anchor_type == "text_range":
        start_offset = row.get("start_offset")
        end_offset = row.get("end_offset")
        if (
            isinstance(sentence_id, str)
            and isinstance(start_offset, int)
            and isinstance(end_offset, int)
            and start_offset < end_offset
        ):
            return [
                _NormalizedSegment(
                    sentence_id=sentence_id,
                    paragraph_id=paragraph_id if isinstance(paragraph_id, str) else None,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            ]
        return []

    if anchor_type != "multi_text":
        return []

    payload_json = _payload_json_from_row(row)
    raw_segments = payload_json.get("segments")
    if not isinstance(raw_segments, list):
        return []

    normalized: list[_NormalizedSegment] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        segment_sentence_id = segment.get("sentence_id")
        start_offset = segment.get("start_offset")
        end_offset = segment.get("end_offset")
        if (
            isinstance(segment_sentence_id, str)
            and isinstance(start_offset, int)
            and isinstance(end_offset, int)
            and start_offset < end_offset
        ):
            normalized.append(
                _NormalizedSegment(
                    sentence_id=segment_sentence_id,
                    paragraph_id=segment.get("paragraph_id")
                    if isinstance(segment.get("paragraph_id"), str)
                    else None,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )
    return normalized


def _normalized_segments_from_request(req: UserAnnotationCreateRequest) -> list[_NormalizedSegment]:
    if req.anchor_type == "sentence":
        if not req.sentence_id:
            return []
        return [
            _NormalizedSegment(
                sentence_id=req.sentence_id,
                paragraph_id=req.paragraph_id,
                start_offset=0,
                end_offset=utf16_code_unit_length(req.selected_text),
            )
        ]

    if req.anchor_type == "text_range":
        if (
            not req.sentence_id
            or req.start_offset is None
            or req.end_offset is None
            or req.start_offset >= req.end_offset
        ):
            return []
        return [
            _NormalizedSegment(
                sentence_id=req.sentence_id,
                paragraph_id=req.paragraph_id,
                start_offset=req.start_offset,
                end_offset=req.end_offset,
            )
        ]

    if req.anchor_type != "multi_text":
        return []

    return [
        _NormalizedSegment(
            sentence_id=segment.sentence_id,
            paragraph_id=segment.paragraph_id,
            start_offset=segment.start_offset,
            end_offset=segment.end_offset,
        )
        for segment in req.segments
        if segment.start_offset < segment.end_offset
    ]


def _segments_overlap(
    left_segments: list[_NormalizedSegment],
    right_segments: list[_NormalizedSegment],
) -> bool:
    for left in left_segments:
        for right in right_segments:
            if left.sentence_id == right.sentence_id and _is_overlap(
                _SingleSentenceRange(left.start_offset, left.end_offset),
                _SingleSentenceRange(right.start_offset, right.end_offset),
            ):
                return True
    return False


def _merged_intervals_by_sentence(
    segments: list[_NormalizedSegment],
) -> dict[str, list[tuple[int, int]]]:
    grouped: dict[str, list[tuple[int, int]]] = {}
    for segment in segments:
        grouped.setdefault(segment.sentence_id, []).append((segment.start_offset, segment.end_offset))

    merged: dict[str, list[tuple[int, int]]] = {}
    for sentence_id, intervals in grouped.items():
        sorted_intervals = sorted(intervals)
        merged_intervals: list[tuple[int, int]] = []
        for start_offset, end_offset in sorted_intervals:
            if not merged_intervals or start_offset > merged_intervals[-1][1]:
                merged_intervals.append((start_offset, end_offset))
                continue
            merged_intervals[-1] = (
                merged_intervals[-1][0],
                max(merged_intervals[-1][1], end_offset),
            )
        merged[sentence_id] = merged_intervals
    return merged


def _render_scene_sentence_lookup(scene: dict) -> tuple[dict[str, dict], dict[str, int]]:
    sm = sentence_map(scene)
    sentence_order: dict[str, int] = {}
    index = 0
    for sentence in scene.get("article", {}).get("sentences", []):
        sentence_id = sentence.get("sentence_id")
        if isinstance(sentence_id, str) and sentence_id not in sentence_order:
            sentence_order[sentence_id] = index
            index += 1
    return sm, sentence_order


def _materialize_merged_annotation(
    *,
    record_id: UUID,
    scene: dict,
    merged_segments: list[_NormalizedSegment],
) -> tuple[
    str,
    str | None,
    str | None,
    str,
    int | None,
    int | None,
    str | None,
    str,
    list[dict],
]:
    if not merged_segments:
        raise HTTPException(status_code=400, detail="merged highlight segments are empty")

    sentence_lookup, sentence_order = _render_scene_sentence_lookup(scene)
    ordered_segments = sorted(
        merged_segments,
        key=lambda segment: (
            sentence_order.get(segment.sentence_id, 10**6),
            segment.start_offset,
            segment.end_offset,
        ),
    )

    segment_payloads: list[dict] = []
    selected_parts: list[str] = []

    for segment in ordered_segments:
        sentence_obj = sentence_lookup.get(segment.sentence_id)
        if not sentence_obj or not isinstance(sentence_obj.get("text"), str):
            raise HTTPException(status_code=400, detail="sentence text is unavailable in render scene")
        sentence_text = sentence_obj["text"]
        selected_text = slice_by_utf16_offsets(sentence_text, segment.start_offset, segment.end_offset)
        if selected_text is None:
            raise HTTPException(status_code=400, detail="merged range offsets are outside sentence text")
        selected_parts.append(selected_text.strip())
        segment_payloads.append(
            {
                "paragraph_id": segment.paragraph_id or sentence_obj.get("paragraph_id"),
                "sentence_id": segment.sentence_id,
                "selected_text": selected_text,
                "start_offset": segment.start_offset,
                "end_offset": segment.end_offset,
                "text_hash": compute_text_range_hash(selected_text),
            }
        )

    if len(segment_payloads) == 1:
        segment_payload = segment_payloads[0]
        sentence_obj = sentence_lookup.get(segment_payload["sentence_id"])
        sentence_text = sentence_obj["text"] if sentence_obj else None
        sentence_utf16_len = utf16_code_unit_length(sentence_text) if isinstance(sentence_text, str) else None

        if (
            sentence_utf16_len is not None
            and segment_payload["start_offset"] == 0
            and segment_payload["end_offset"] == sentence_utf16_len
        ):
            return (
                "sentence",
                segment_payload.get("paragraph_id"),
                segment_payload["sentence_id"],
                sentence_text,
                None,
                None,
                None,
                build_sentence_target_key(str(record_id), segment_payload["sentence_id"]),
                [],
            )

        return (
            "text_range",
            segment_payload.get("paragraph_id"),
            segment_payload["sentence_id"],
            segment_payload["selected_text"],
            segment_payload["start_offset"],
            segment_payload["end_offset"],
            segment_payload["text_hash"],
            build_text_range_target_key(
                str(record_id),
                segment_payload["sentence_id"],
                segment_payload["start_offset"],
                segment_payload["end_offset"],
                segment_payload["text_hash"],
            ),
            [],
        )

    selected_text = " ".join(part for part in selected_parts if part).strip()
    first_segment = segment_payloads[0]
    return (
        "multi_text",
        first_segment.get("paragraph_id"),
        first_segment["sentence_id"],
        selected_text,
        None,
        None,
        None,
        build_multi_text_target_key(str(record_id), segment_payloads),
        segment_payloads,
    )


def _row_to_response(
    row: dict,
    superseded_ids: list[UUID] | None = None,
) -> UserAnnotationResponse:
    payload_json = (
        row["payload_json"]
        if isinstance(row["payload_json"], dict)
        else json.loads(row["payload_json"] or "{}")
    )
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
        superseded_ids=superseded_ids or [],
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


async def _resolve_single_sentence_conflict(
    conn,
    *,
    user_id: UUID,
    record_id: UUID,
    req: UserAnnotationCreateRequest,
    target_key: str,
    render_scene: dict | None = None,
) -> UserAnnotationResponse | None:
    request_segments = _normalized_segments_from_request(req)
    if not request_segments:
        return None

    rows = await conn.fetch(
        f"""
        SELECT {_ANNOTATION_FIELDS}
        FROM user_annotations
        WHERE user_id = $1
          AND analysis_record_id = $2
          AND deleted_at IS NULL
        """,
        user_id,
        record_id,
    )
    overlapping_rows: list[dict] = []
    for row in rows:
        candidate = dict(row)
        if candidate.get("target_key") == target_key:
            continue
        candidate_segments = _normalized_segments_from_row(candidate)
        if not candidate_segments:
            continue
        if _segments_overlap(candidate_segments, request_segments):
            overlapping_rows.append(candidate)

    if not overlapping_rows:
        return None

    scene = render_scene
    if scene is None:
        scene = await load_render_scene(conn, user_id, record_id)

    merged_color = _resolve_merged_color(overlapping_rows, req.color)
    all_segments = list(request_segments)
    for row in overlapping_rows:
        all_segments.extend(_normalized_segments_from_row(row))

    merged_segments: list[_NormalizedSegment] = []
    for sentence_id, intervals in _merged_intervals_by_sentence(all_segments).items():
        paragraph_id = next(
            (
                segment.paragraph_id
                for segment in all_segments
                if segment.sentence_id == sentence_id and segment.paragraph_id
            ),
            None,
        )
        for start_offset, end_offset in intervals:
            merged_segments.append(
                _NormalizedSegment(
                    sentence_id=sentence_id,
                    paragraph_id=paragraph_id,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )
    (
        new_anchor_type,
        new_paragraph_id,
        new_sentence_id,
        new_selected_text,
        new_start_offset,
        new_end_offset,
        new_text_hash,
        new_target_key,
        merged_segment_payloads,
    ) = _materialize_merged_annotation(
        record_id=record_id,
        scene=scene,
        merged_segments=merged_segments,
    )

    earliest_row = min(overlapping_rows, key=lambda r: r["created_at"])
    superseded_ids: list[UUID] = []
    merged_payload_json = _payload_json_from_row(earliest_row)
    merged_payload_json.update(dict(req.payload_json))
    if new_anchor_type == "multi_text":
        merged_payload_json["segments"] = merged_segment_payloads
        merged_payload_json["range_status"] = "multi_text_anchor"
        merged_payload_json.pop("selected_text_hash", None)
        merged_payload_json.pop("sentence_text_hash", None)
        merged_payload_json.pop("prefix", None)
        merged_payload_json.pop("suffix", None)
    elif new_anchor_type == "text_range":
        merged_payload_json["range_status"] = "text_range_anchor"
        merged_payload_json.pop("segments", None)
    else:
        merged_payload_json.pop("segments", None)
        merged_payload_json["range_status"] = "sentence_anchor"
        merged_payload_json.pop("selected_text_hash", None)
        merged_payload_json.pop("sentence_text_hash", None)
        merged_payload_json.pop("prefix", None)
        merged_payload_json.pop("suffix", None)

    now = datetime.now(UTC)
    for row in overlapping_rows:
        if row["id"] != earliest_row["id"]:
            await conn.execute(
                """
                UPDATE user_annotations
                SET deleted_at = $3, deleted_by = $1
                WHERE id = $2 AND deleted_at IS NULL
                """,
                user_id,
                row["id"],
                now,
            )
            superseded_ids.append(row["id"])

    # Update the earliest row to the merged range
    try:
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
            new_anchor_type,
            new_target_key,
            new_paragraph_id,
            new_sentence_id,
            new_selected_text,
            new_start_offset,
            new_end_offset,
            new_text_hash,
            merged_color,
            merged_payload_json,
            earliest_row["id"],
        )
    except asyncpg.CheckViolationError as exc:
        logger.exception(
            "user_annotations merge update violated constraint=%s anchor_type=%s target_key=%s row_id=%s payload=%s",
            getattr(exc, "constraint_name", None),
            new_anchor_type,
            new_target_key,
            earliest_row["id"],
            merged_payload_json,
        )
        raise HTTPException(status_code=400, detail="Merged highlight payload is invalid") from exc
    if not row:
        raise HTTPException(status_code=500, detail="Failed to merge highlights")
    return _row_to_response(dict(row), superseded_ids)


async def create_user_annotation(
    user_id: UUID,
    req: UserAnnotationCreateRequest,
) -> UserAnnotationResponse:
    target_key = _build_target_key(req)

    async with db_connect.acquire_connection() as conn:
        try:
            record_id = UUID(req.analysis_record_id) if req.analysis_record_id else None
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="analysis_record_id must be a UUID",
            ) from exc

        render_scene = None
        if record_id is not None and req.anchor_type in {"text_range", "multi_text"}:
            render_scene = await load_render_scene(conn, user_id, record_id)
            if req.anchor_type == "multi_text":
                validate_multi_text_against_render_scene(
                    render_scene,
                    [segment.model_dump(mode="python") for segment in req.segments],
                )
            else:
                if not req.sentence_id:
                    raise HTTPException(
                        status_code=400,
                        detail="sentence_id is not present in render scene",
                    )
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

        if record_id is not None:
            resolved_conflict = await _resolve_single_sentence_conflict(
                conn,
                user_id=user_id,
                record_id=record_id,
                req=req,
                target_key=target_key,
                render_scene=render_scene,
            )
            if resolved_conflict is not None:
                return resolved_conflict

        first_segment = _first_segment(req)
        payload_json = dict(req.payload_json)
        if req.anchor_type == "multi_text":
            payload_json["segments"] = [
                segment.model_dump(mode="python") for segment in req.segments
            ]
        try:
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
        except asyncpg.CheckViolationError as exc:
            logger.exception(
                "user_annotations insert/update violated constraint=%s anchor_type=%s target_key=%s payload=%s",
                getattr(exc, "constraint_name", None),
                req.anchor_type,
                target_key,
                payload_json,
            )
            raise HTTPException(status_code=400, detail="Highlight payload is invalid") from exc
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
                raise HTTPException(
                    status_code=400,
                    detail="analysis_record_id must be a UUID",
                ) from exc
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


async def update_user_annotation(
    user_id: UUID,
    annotation_id: UUID,
    req: UserAnnotationUpdateRequest,
) -> UserAnnotationResponse:
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
        now = datetime.now(UTC)
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
