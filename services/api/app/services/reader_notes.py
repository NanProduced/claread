from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

from app.contracts.anchor_validation import AnchorValidationError
from app.database import connection as db_connect
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.reader_notes import (
    ReaderNoteCreateRequest,
    ReaderNoteResponse,
    ReaderNoteUpdateRequest,
)
from app.schemas.user_annotations import UserAnnotationSegment
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

_NOTE_FIELDS = (
    "id, quote_mode, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, note_text, payload_json, created_at, updated_at, "
    "reading_record_id, base_id, generation, unit_id, anchor_segment_id, "
    "unit_start_utf16, unit_end_utf16"
)


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
    `reader_notes.target_key` is NOT NULL.
    """
    return (
        f"reading-record:{validated.record_id}:base:{validated.base_id}:"
        f"gen:{validated.generation}:unit:{validated.unit.unit_id}:"
        f"segment:{validated.anchor_segment.anchor_segment_id}:"
        f"range:{unit_start_utf16}:{unit_end_utf16}:{text_hash}"
    )


async def _persist_reading_record_anchor_branch(
    conn,
    *,
    user_id: UUID,
    req: ReaderNoteCreateRequest,
    repository: ReaderOrchestrationRepository | None,
) -> ReaderNoteResponse:
    """D6-U4 V1c single-range persistence for Reading Record notes.

    Runs the request through the Reading Record anchor gate, then writes
    a real row into `reader_notes` with the Reading Record anchor columns
    as the authority. No legacy target_key / render_scene surface exists.
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

    unit_start_utf16 = req.anchor.start_offset
    unit_end_utf16 = req.anchor.end_offset
    target_key = _build_reading_record_target_key(
        validated,
        unit_start_utf16=unit_start_utf16,
        unit_end_utf16=unit_end_utf16,
        text_hash=req.anchor.text_hash,
    )

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

        row = await conn.fetchrow(
            f"""
            INSERT INTO reader_notes (
                user_id, quote_mode, target_key,
                paragraph_id, sentence_id, selected_text, start_offset, end_offset,
                text_hash, note_text, payload_json,
                reading_record_id, base_id, generation, unit_id, anchor_segment_id,
                unit_start_utf16, unit_end_utf16
            )
            VALUES ($1, 'text_range', $2, NULL, NULL, $3, NULL, NULL, $4, $5, $6,
                    $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (user_id, reading_record_id, base_id, anchor_segment_id,
                         unit_start_utf16, unit_end_utf16, text_hash)
                WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL
            DO UPDATE SET
                quote_mode = EXCLUDED.quote_mode,
                selected_text = EXCLUDED.selected_text,
                text_hash = EXCLUDED.text_hash,
                note_text = EXCLUDED.note_text,
                payload_json = EXCLUDED.payload_json,
                deleted_at = NULL,
                deleted_by = NULL,
                updated_at = NOW()
            WHERE reader_notes.note_text IS DISTINCT FROM EXCLUDED.note_text
               OR reader_notes.payload_json IS DISTINCT FROM EXCLUDED.payload_json
               OR reader_notes.selected_text IS DISTINCT FROM EXCLUDED.selected_text
               OR reader_notes.quote_mode IS DISTINCT FROM EXCLUDED.quote_mode
            RETURNING {_NOTE_FIELDS}
            """,
            user_id,
            target_key,
            req.selected_text,
            req.anchor.text_hash,
            req.note_text,
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
            # ON CONFLICT DO UPDATE WHERE was false — true no-op.
            # Fetch the existing row and return without publishing an event.
            row = await conn.fetchrow(
                f"""
                SELECT {_NOTE_FIELDS}
                FROM reader_notes
                WHERE user_id = $1
                  AND reading_record_id = $2
                  AND base_id = $3
                  AND anchor_segment_id = $4
                  AND unit_start_utf16 = $5
                  AND unit_end_utf16 = $6
                  AND text_hash = $7
                  AND deleted_at IS NULL
                """,
                user_id,
                validated.record_id,
                validated.base_id,
                validated.anchor_segment.anchor_segment_id,
                unit_start_utf16,
                unit_end_utf16,
                req.anchor.text_hash,
            )
            if not row:
                raise HTTPException(status_code=500, detail="Failed to create reader note")
            return _row_to_response(dict(row))
        response = _row_to_response(dict(row))
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


def _row_to_response(row: dict) -> ReaderNoteResponse:
    payload_json = ensure_json_object(row.get("payload_json"))
    raw_segments = payload_json.get("segments")
    segments = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, dict):
                segments.append(UserAnnotationSegment(**segment))
    return ReaderNoteResponse(
        id=row["id"],
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
        reading_record_id=row.get("reading_record_id"),
        base_id=row.get("base_id"),
        generation=row.get("generation"),
        unit_id=row.get("unit_id"),
        anchor_segment_id=row.get("anchor_segment_id"),
        unit_start_utf16=row.get("unit_start_utf16"),
        unit_end_utf16=row.get("unit_end_utf16"),
    )


async def create_reader_note(
    user_id: UUID,
    req: ReaderNoteCreateRequest,
    *,
    repository: ReaderOrchestrationRepository | None = None,
) -> ReaderNoteResponse:
    # DATA-LEGACY-IDENTITY-EXIT: the Reading Record anchor is the only note
    # contract; the anchor gate validates and the row persists with the
    # Reading Record anchor columns as the authority.
    async with db_connect.acquire_connection() as conn:
        return await _persist_reading_record_anchor_branch(
            conn,
            user_id=user_id,
            req=req,
            repository=repository,
        )


async def list_reader_notes(user_id: UUID, reading_record_id: str) -> list[ReaderNoteResponse]:
    try:
        parsed_record_id = UUID(reading_record_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="reading_record_id must be a UUID") from exc
    async with db_connect.acquire_connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_NOTE_FIELDS}
            FROM reader_notes
            WHERE user_id = $1 AND reading_record_id = $2 AND deleted_at IS NULL
            ORDER BY
                unit_start_utf16 ASC NULLS FIRST,
                created_at ASC
            """,
            user_id,
            parsed_record_id,
        )
        return [_row_to_response(dict(row)) for row in rows]


async def update_reader_note(
    user_id: UUID,
    note_id: UUID,
    req: ReaderNoteUpdateRequest,
) -> ReaderNoteResponse:
    async with db_connect.acquire_connection() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                f"""
                SELECT {_NOTE_FIELDS}
                FROM reader_notes
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                FOR UPDATE
                """,
                note_id,
                user_id,
            )
            if not current:
                raise HTTPException(
                    status_code=404,
                    detail="Reader note not found or unauthorized",
                )

            note_text_changed = current["note_text"] != req.note_text
            row = await conn.fetchrow(
                f"""
                UPDATE reader_notes
                SET note_text = $1
                WHERE id = $2 AND user_id = $3
                RETURNING {_NOTE_FIELDS}
                """,
                req.note_text,
                note_id,
                user_id,
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Reader note not found or unauthorized",
                )

            reading_record_id = row.get("reading_record_id")
            base_id = row.get("base_id")
            generation = row.get("generation")
            if (
                note_text_changed
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
                        target_keys=[str(note_id)],
                    )
                    await ReaderEventRuntime().publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="projection_ops",
                        payload_json=payload,
                    )
            return _row_to_response(dict(row))


async def delete_reader_note(user_id: UUID, note_id: UUID) -> None:
    async with db_connect.acquire_connection() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT reading_record_id, base_id, generation
                FROM reader_notes
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                FOR UPDATE
                """,
                note_id,
                user_id,
            )
            if not current:
                raise HTTPException(
                    status_code=404,
                    detail="Reader note not found or unauthorized",
                )

            now = datetime.now(UTC)
            await conn.execute(
                """
                UPDATE reader_notes
                SET deleted_at = $3, deleted_by = $1
                WHERE id = $2 AND user_id = $1
                """,
                user_id,
                note_id,
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
                        target_keys=[str(note_id)],
                    )
                    await ReaderEventRuntime().publish_event_in_transaction(
                        conn,
                        record_id=reading_record_id,
                        event_type="projection_ops",
                        payload_json=payload,
                    )
