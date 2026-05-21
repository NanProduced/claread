from uuid import UUID

from fastapi import APIRouter, Query

from app.schemas.reader_notes import (
    ReaderNoteCreateRequest,
    ReaderNoteListResponse,
    ReaderNoteResponse,
    ReaderNoteUpdateRequest,
)
from app.services import reader_notes as svc
from app.services.auth.dependencies import AuthUserDep

router = APIRouter(prefix="/reader-notes", tags=["reader-notes"])


@router.post("", response_model=ReaderNoteResponse)
async def create_reader_note(
    req: ReaderNoteCreateRequest,
    current_user: AuthUserDep,
) -> ReaderNoteResponse:
    return await svc.create_reader_note(UUID(current_user.user_id), req)


@router.get("", response_model=ReaderNoteListResponse)
async def list_reader_notes(
    current_user: AuthUserDep,
    record_id: str = Query(alias="analysis_record_id", min_length=1),
) -> ReaderNoteListResponse:
    items = await svc.list_reader_notes(UUID(current_user.user_id), record_id)
    return ReaderNoteListResponse(items=items)


@router.patch("/{note_id}", response_model=ReaderNoteResponse)
async def update_reader_note(
    note_id: UUID,
    req: ReaderNoteUpdateRequest,
    current_user: AuthUserDep,
) -> ReaderNoteResponse:
    return await svc.update_reader_note(UUID(current_user.user_id), note_id, req)


@router.delete("/{note_id}")
async def delete_reader_note(
    note_id: UUID,
    current_user: AuthUserDep,
) -> dict[str, bool]:
    await svc.delete_reader_note(UUID(current_user.user_id), note_id)
    return {"ok": True}
