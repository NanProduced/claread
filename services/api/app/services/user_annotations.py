from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

from app.contracts.anchor_validation import AnchorValidationError
from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
)
from app.database import connection as db_connect
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.user_annotations import (
    UserAnnotationCreateRequest,
    UserAnnotationResponse,
    UserAnnotationSegment,
    UserAnnotationUpdateRequest,
)
from app.services.reader_orchestration.anchor_gate import (
    ValidatedReadingRecordAnchor,
    load_validated_reading_record_anchor,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)
from app.services.reader_orchestration.representation_event_payload import (
    build_representation_payload,
)

_ANNOTATION_FIELDS = (
    "id, anchor_type, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, color, payload_json, created_at, updated_at, "
    "reading_record_id, base_id, generation, unit_id, anchor_segment_id, "
    "unit_start_utf16, unit_end_utf16"
)


@dataclass(frozen=True, slots=True)
class _SingleSentenceRange:
    start_offset: int
    end_offset: int


def _build_reading_record_target_key(
    validated: ValidatedReadingRecordAnchor,
    *,
    unit_start_utf16: int,
    unit_end_utf16: int,
    text_hash: str,
) -> str:
    """Deterministic compatibility key for Reading Record anchor rows.

    The key is NOT the authority — `reading_record_id` / `base_id` /
    `anchor_segment_id` / unit-local offsets are. It exists only because
    `user_annotations.target_key` is NOT NULL and carries a UNIQUE
    constraint per user.
    """
    return (
        f"reading-record:{validated.record_id}:base:{validated.base_id}:"
        f"gen:{validated.generation}:unit:{validated.unit.unit_id}:"
        f"segment:{validated.anchor_segment.anchor_segment_id}:"
        f"range:{unit_start_utf16}:{unit_end_utf16}:{text_hash}"
    )


def _range_from_reading_record_row(row: dict) -> _SingleSentenceRange | None:
    unit_start_utf16 = row.get("unit_start_utf16")
    unit_end_utf16 = row.get("unit_end_utf16")
    if (
        isinstance(unit_start_utf16, int)
        and isinstance(unit_end_utf16, int)
        and unit_start_utf16 < unit_end_utf16
    ):
        return _SingleSentenceRange(unit_start_utf16, unit_end_utf16)
    return None


def _range_within_anchor_segment(
    range_: _SingleSentenceRange,
    validated: ValidatedReadingRecordAnchor,
) -> bool:
    return (
        validated.anchor_segment.unit_start_utf16 <= range_.start_offset
        and range_.end_offset <= validated.anchor_segment.unit_end_utf16
    )


def _is_adjacent(left: _SingleSentenceRange, right: _SingleSentenceRange) -> bool:
    return (
        left.end_offset == right.start_offset
        or right.end_offset == left.start_offset
    )


def _merge_ranges(
    left: _SingleSentenceRange,
    right: _SingleSentenceRange,
) -> _SingleSentenceRange:
    return _SingleSentenceRange(
        min(left.start_offset, right.start_offset),
        max(left.end_offset, right.end_offset),
    )


def _collect_reading_record_highlight_merge_rows(
    rows: list[dict],
    *,
    request_range: _SingleSentenceRange,
    request_color: str,
    validated: ValidatedReadingRecordAnchor,
) -> list[dict]:
    """Collect unit-local rows that canonicalize with the requested highlight.

    Reading Record user highlights merge on true overlap regardless of color,
    and on adjacency only when the color is the same. The loop is transitive:
    if merging one row expands the union into another row, that row is also
    collected.
    """
    merge_rows: list[dict] = []
    merged_range = request_range

    changed = True
    while changed:
        changed = False
        for row in rows:
            if any(candidate["id"] == row["id"] for candidate in merge_rows):
                continue

            row_range = _range_from_reading_record_row(row)
            if row_range is None or not _range_within_anchor_segment(row_range, validated):
                continue

            should_merge = _is_overlap(row_range, merged_range)
            if not should_merge and row.get("color") == request_color:
                should_merge = _is_adjacent(row_range, merged_range)

            if should_merge:
                merge_rows.append(row)
                merged_range = _merge_ranges(merged_range, row_range)
                changed = True

    return merge_rows


def _slice_reading_record_range(
    validated: ValidatedReadingRecordAnchor,
    range_: _SingleSentenceRange,
) -> tuple[str, str]:
    selected_text = slice_by_utf16_offsets(
        validated.unit.text,
        range_.start_offset,
        range_.end_offset,
    )
    if selected_text is None:
        raise HTTPException(
            status_code=400,
            detail="merged range offsets are outside reading unit text",
        )
    return selected_text, compute_text_range_hash(selected_text)


def _row_list_contains_id(rows: list[dict], row_id: UUID) -> bool:
    return any(row["id"] == row_id for row in rows)


def _select_reading_record_canonical_row(
    rows: list[dict],
    *,
    final_target_key: str,
) -> dict:
    for row in rows:
        if row.get("target_key") == final_target_key:
            return row
    return min(
        rows,
        key=lambda row: (row["created_at"], str(row["id"])),
    )


async def _insert_reading_record_highlight_row(
    conn,
    *,
    user_id: UUID,
    req: UserAnnotationCreateRequest,
    validated: ValidatedReadingRecordAnchor,
    unit_start_utf16: int,
    unit_end_utf16: int,
    selected_text: str,
    text_hash: str,
) -> UserAnnotationResponse:
    target_key = _build_reading_record_target_key(
        validated,
        unit_start_utf16=unit_start_utf16,
        unit_end_utf16=unit_end_utf16,
        text_hash=text_hash,
    )

    row = await conn.fetchrow(
        f"""
        INSERT INTO user_annotations (
            user_id, anchor_type, target_key,
            paragraph_id, sentence_id, selected_text, start_offset, end_offset,
            text_hash, color, payload_json,
            reading_record_id, base_id, generation, unit_id, anchor_segment_id,
            unit_start_utf16, unit_end_utf16
        )
        VALUES ($1, 'text_range', $2, NULL, NULL, $3, NULL, NULL, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (user_id, target_key) DO UPDATE SET
            anchor_type = EXCLUDED.anchor_type,
            selected_text = EXCLUDED.selected_text,
            text_hash = EXCLUDED.text_hash,
            color = EXCLUDED.color,
            payload_json = EXCLUDED.payload_json,
            reading_record_id = EXCLUDED.reading_record_id,
            base_id = EXCLUDED.base_id,
            generation = EXCLUDED.generation,
            unit_id = EXCLUDED.unit_id,
            anchor_segment_id = EXCLUDED.anchor_segment_id,
            unit_start_utf16 = EXCLUDED.unit_start_utf16,
            unit_end_utf16 = EXCLUDED.unit_end_utf16,
            deleted_at = NULL,
            deleted_by = NULL,
            updated_at = NOW()
        RETURNING {_ANNOTATION_FIELDS}
        """,
        user_id,
        target_key,
        selected_text,
        text_hash,
        req.color,
        jsonb_param(dict(req.payload_json)),
        validated.record_id,
        validated.base_id,
        validated.generation,
        validated.unit.unit_id,
        validated.anchor_segment.anchor_segment_id,
        unit_start_utf16,
        unit_end_utf16,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create user annotation")
    return _row_to_response(dict(row))


async def _merge_reading_record_highlight_rows(
    conn,
    *,
    user_id: UUID,
    req: UserAnnotationCreateRequest,
    validated: ValidatedReadingRecordAnchor,
    merge_rows: list[dict],
    request_range: _SingleSentenceRange,
) -> UserAnnotationResponse:
    merged_range = request_range
    for row in merge_rows:
        row_range = _range_from_reading_record_row(row)
        if row_range is not None:
            merged_range = _merge_ranges(merged_range, row_range)

    selected_text, text_hash = _slice_reading_record_range(validated, merged_range)
    target_key = _build_reading_record_target_key(
        validated,
        unit_start_utf16=merged_range.start_offset,
        unit_end_utf16=merged_range.end_offset,
        text_hash=text_hash,
    )

    final_target_row = await conn.fetchrow(
        f"""
        SELECT {_ANNOTATION_FIELDS}
        FROM user_annotations
        WHERE user_id = $1
          AND target_key = $2
        FOR UPDATE
        """,
        user_id,
        target_key,
    )
    if final_target_row:
        final_target_candidate = dict(final_target_row)
        if not _row_list_contains_id(merge_rows, final_target_candidate["id"]):
            merge_rows.append(final_target_candidate)

    canonical_row = _select_reading_record_canonical_row(
        merge_rows,
        final_target_key=target_key,
    )
    superseded_ids: list[UUID] = []
    now = datetime.now(UTC)
    for row in merge_rows:
        if row["id"] == canonical_row["id"]:
            continue
        await conn.execute(
            """
            UPDATE user_annotations
            SET deleted_at = $3, deleted_by = $1
            WHERE id = $2 AND user_id = $1 AND deleted_at IS NULL
            """,
            user_id,
            row["id"],
            now,
        )
        superseded_ids.append(row["id"])

    row = await conn.fetchrow(
        f"""
        UPDATE user_annotations
        SET anchor_type = 'text_range',
            target_key = $1,
            paragraph_id = NULL,
            sentence_id = NULL,
            selected_text = $2,
            start_offset = NULL,
            end_offset = NULL,
            text_hash = $3,
            color = $4,
            payload_json = $5::jsonb,
            reading_record_id = $6,
            base_id = $7,
            generation = $8,
            unit_id = $9,
            anchor_segment_id = $10,
            unit_start_utf16 = $11,
            unit_end_utf16 = $12,
            deleted_at = NULL,
            deleted_by = NULL,
            updated_at = NOW()
        WHERE id = $13 AND user_id = $14
        RETURNING {_ANNOTATION_FIELDS}
        """,
        target_key,
        selected_text,
        text_hash,
        req.color,
        jsonb_param(dict(req.payload_json)),
        validated.record_id,
        validated.base_id,
        validated.generation,
        validated.unit.unit_id,
        validated.anchor_segment.anchor_segment_id,
        merged_range.start_offset,
        merged_range.end_offset,
        canonical_row["id"],
        user_id,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to merge highlights")
    return _row_to_response(dict(row), superseded_ids)


async def _persist_reading_record_anchor_branch(
    conn,
    *,
    user_id: UUID,
    req: UserAnnotationCreateRequest,
    repository: ReaderOrchestrationRepository | None,
) -> UserAnnotationResponse:
    """Persist and canonicalize a Reading Record user highlight.

    Runs the request through the Reading Record anchor gate, then writes
    a real row into `user_annotations` with the Reading Record anchor
    columns as the authority. Uses unit-local UTF-16 offsets and never
    touches any legacy target_key / render_scene surface.
    """
    assert req.anchor is not None  # caller guards
    repo = repository or ReaderOrchestrationRepository()

    try:
        validated: ValidatedReadingRecordAnchor = await load_validated_reading_record_anchor(
            conn,
            repository=repo,
            user_id=user_id,
            anchor=req.anchor,
        )
    except AnchorValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "message": exc.message,
                "field": "anchor",
            },
        ) from exc

    request_range = _SingleSentenceRange(
        req.anchor.start_offset,
        req.anchor.end_offset,
    )
    selected_text, text_hash = _slice_reading_record_range(validated, request_range)

    async with conn.transaction():
        if not await ReaderEventRuntime().is_active_fence(
            conn,
            record_id=validated.record_id,
            base_id=validated.base_id,
            generation=validated.generation,
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_reading_record_anchor",
                    "message": "Reading record changed; refresh and try again.",
                    "field": "anchor",
                },
            )

        rows = await conn.fetch(
            f"""
            SELECT {_ANNOTATION_FIELDS}
            FROM user_annotations
            WHERE user_id = $1
              AND reading_record_id = $2
              AND base_id = $3
              AND generation = $4
              AND unit_id = $5
              AND anchor_segment_id = $6
              AND deleted_at IS NULL
              AND anchor_type = 'text_range'
            ORDER BY created_at ASC, id ASC
            FOR UPDATE
            """,
            user_id,
            validated.record_id,
            validated.base_id,
            validated.generation,
            validated.unit.unit_id,
            validated.anchor_segment.anchor_segment_id,
        )
        merge_rows = _collect_reading_record_highlight_merge_rows(
            [dict(row) for row in rows],
            request_range=request_range,
            request_color=req.color,
            validated=validated,
        )

        if merge_rows:
            # No-op merge detection: if the only existing annotation has the
            # exact same range and color as the request, the merge is a
            # semantic no-op — skip the UPDATE and the representation event.
            if len(merge_rows) == 1 and merge_rows[0].get("color") == req.color:
                row_range = _range_from_reading_record_row(merge_rows[0])
                if (
                    row_range is not None
                    and row_range.start_offset == request_range.start_offset
                    and row_range.end_offset == request_range.end_offset
                ):
                    return _row_to_response(dict(merge_rows[0]))

            response = await _merge_reading_record_highlight_rows(
                conn,
                user_id=user_id,
                req=req,
                validated=validated,
                merge_rows=merge_rows,
                request_range=request_range,
            )
            target_keys = [str(response.id)] + [
                str(sid) for sid in (response.superseded_ids or [])
            ]
            payload = build_representation_payload(
                representation_section="user_assets",
                operation="merge",
                generation=validated.generation,
                base_id=str(validated.base_id),
                target_keys=target_keys,
            )
            await ReaderEventRuntime().publish_event_in_transaction(
                conn,
                record_id=validated.record_id,
                event_type="projection_ops",
                payload_json=payload,
            )
            return response

        response = await _insert_reading_record_highlight_row(
            conn,
            user_id=user_id,
            req=req,
            validated=validated,
            unit_start_utf16=request_range.start_offset,
            unit_end_utf16=request_range.end_offset,
            selected_text=selected_text,
            text_hash=text_hash,
        )
        payload = build_representation_payload(
            representation_section="user_assets",
            operation="upsert",
            generation=validated.generation,
            base_id=str(validated.base_id),
            target_keys=[str(response.id)],
        )
        await ReaderEventRuntime().publish_event_in_transaction(
            conn,
            record_id=validated.record_id,
            event_type="projection_ops",
            payload_json=payload,
        )
        return response


def _is_overlap(left: _SingleSentenceRange, right: _SingleSentenceRange) -> bool:
    return max(left.start_offset, right.start_offset) < min(left.end_offset, right.end_offset)


def _row_to_response(
    row: dict,
    superseded_ids: list[UUID] | None = None,
) -> UserAnnotationResponse:
    payload_json = ensure_json_object(row.get("payload_json"))
    raw_segments = payload_json.get("segments")
    segments = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, dict):
                segments.append(UserAnnotationSegment(**segment))
    return UserAnnotationResponse(
        id=row["id"],
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
        reading_record_id=row.get("reading_record_id"),
        base_id=row.get("base_id"),
        generation=row.get("generation"),
        unit_id=row.get("unit_id"),
        anchor_segment_id=row.get("anchor_segment_id"),
        unit_start_utf16=row.get("unit_start_utf16"),
        unit_end_utf16=row.get("unit_end_utf16"),
    )


async def create_user_annotation(
    user_id: UUID,
    req: UserAnnotationCreateRequest,
    *,
    repository: ReaderOrchestrationRepository | None = None,
) -> UserAnnotationResponse:
    # DATA-LEGACY-IDENTITY-EXIT: the Reading Record anchor is the only
    # highlight contract. Requests run through the Reading Record anchor
    # gate and persist with the Reading Record anchor columns as the
    # authority; no legacy target_key / render_scene path exists.
    async with db_connect.acquire_connection() as conn:
        return await _persist_reading_record_anchor_branch(
            conn,
            user_id=user_id,
            req=req,
            repository=repository,
        )


async def list_user_annotations(
    user_id: UUID,
    reading_record_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[UserAnnotationResponse]:
    async with db_connect.acquire_connection() as conn:
        if reading_record_id:
            try:
                parsed_record_id = UUID(reading_record_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="reading_record_id must be a UUID",
                ) from exc
            rows = await conn.fetch(
                f"""
                SELECT {_ANNOTATION_FIELDS}
                FROM user_annotations
                WHERE user_id = $1 AND reading_record_id = $2 AND deleted_at IS NULL
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
                  AND reading_record_id IS NOT NULL
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
        async with conn.transaction():
            current = await conn.fetchrow(
                f"""
                SELECT {_ANNOTATION_FIELDS}
                FROM user_annotations
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                FOR UPDATE
                """,
                annotation_id,
                user_id,
            )
            if not current:
                raise HTTPException(
                    status_code=404,
                    detail="Annotation not found or unauthorized",
                )

            color_changed = current["color"] != req.color
            row = await conn.fetchrow(
                f"""
                UPDATE user_annotations
                SET color = $1
                WHERE id = $2 AND user_id = $3
                RETURNING {_ANNOTATION_FIELDS}
                """,
                req.color,
                annotation_id,
                user_id,
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Annotation not found or unauthorized",
                )

            reading_record_id = row.get("reading_record_id")
            base_id = row.get("base_id")
            generation = row.get("generation")
            if (
                color_changed
                and reading_record_id is not None
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
                        representation_section="user_assets",
                        operation="upsert",
                        generation=int(generation),
                        base_id=str(base_id),
                        target_keys=[str(annotation_id)],
                    )
                    await ReaderEventRuntime().publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="projection_ops",
                        payload_json=payload,
                    )
            return _row_to_response(dict(row))


async def delete_user_annotation(user_id: UUID, annotation_id: UUID) -> None:
    async with db_connect.acquire_connection() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT reading_record_id, base_id, generation
                FROM user_annotations
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                FOR UPDATE
                """,
                annotation_id,
                user_id,
            )
            if not current:
                raise HTTPException(
                    status_code=404,
                    detail="Annotation not found or unauthorized",
                )

            now = datetime.now(UTC)
            await conn.execute(
                """
                UPDATE user_annotations
                SET deleted_at = $3, deleted_by = $1
                WHERE id = $2 AND user_id = $1
                """,
                user_id,
                annotation_id,
                now,
            )

            reading_record_id = current["reading_record_id"]
            base_id = current["base_id"]
            generation = current["generation"]
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
                        representation_section="user_assets",
                        operation="delete",
                        generation=int(generation),
                        base_id=str(base_id),
                        target_keys=[str(annotation_id)],
                    )
                    await ReaderEventRuntime().publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="projection_ops",
                        payload_json=payload,
                    )
