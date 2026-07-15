from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException

from app.config.settings import get_settings
from app.schemas.reader_ask import (
    ReaderAskSelectedModel,
    ReaderAskThreadCreateRequest,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
)
from app.schemas.reader_record_ask_stream import ReaderRecordAskThreadDetail
from app.services.reader_ask import model_options as model_options_svc
from app.services.reader_ask import repository as repo


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _resolve_reader_ask_model_option_or_422(
    *,
    selected_key: str | None,
    strict: bool,
) -> model_options_svc.ResolvedReaderAskModelOption:
    try:
        return model_options_svc.resolve_reader_ask_model_option(
            get_settings(),
            selected_key,
            strict=strict,
        )
    except model_options_svc.ReaderAskModelOptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _selected_model_payload(
    option: model_options_svc.ResolvedReaderAskModelOption,
) -> dict[str, Any]:
    return ReaderAskSelectedModel(
        key=option.key,
        label=option.label,
        description=option.description,
        model_name=option.main_model_name,
        replan_model_name=option.replan_model_name,
        price_multiplier=option.billing.price_multiplier,
    ).model_dump(mode="json")


def _thread_summary_payload(thread: dict[str, Any]) -> dict[str, Any]:
    option = model_options_svc.resolve_reader_ask_model_option(
        get_settings(),
        cast(str | None, thread.get("selected_model_key")),
        strict=False,
    )
    return {
        **thread,
        "selected_model": _selected_model_payload(option),
    }


async def list_analysis_threads(user_id: UUID, record_id: str) -> ReaderAskThreadListResponse:
    record_uuid = _parse_uuid(record_id, "record_id must be a UUID")
    await repo.ensure_record_access(user_id, record_uuid)
    items = await repo.list_threads(user_id, record_uuid)
    return ReaderAskThreadListResponse(
        items=[ReaderAskThreadSummary.model_validate(_thread_summary_payload(item)) for item in items]
    )


async def create_analysis_thread(
    user_id: UUID,
    body: ReaderAskThreadCreateRequest,
) -> ReaderAskThreadSummary:
    record_uuid = _parse_uuid(body.record_id, "record_id must be a UUID")
    record = await repo.ensure_record_access(user_id, record_uuid)
    selected_option = _resolve_reader_ask_model_option_or_422(
        selected_key=body.model,
        strict=body.model is not None,
    )
    thread = await repo.get_or_create_default_thread(
        user_id,
        record_uuid,
        title=body.title or record.get("title") or "Ask Claread",
        selected_model_key=selected_option.key if body.model is not None else None,
    )
    if thread.get("selected_model_key") is None:
        updated_thread = await repo.update_thread_selected_model(
            user_id,
            _parse_uuid(thread["id"], "thread id is invalid"),
            selected_model_key=selected_option.key,
        )
        if updated_thread is not None:
            thread = updated_thread
    return ReaderAskThreadSummary.model_validate(_thread_summary_payload(thread))


async def get_thread_detail(
    user_id: UUID,
    thread_id: UUID,
) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    messages = await repo.list_messages(thread_id, limit=100)
    return ReaderAskThreadDetail.model_validate({**_thread_summary_payload(thread), "messages": messages})


async def reset_analysis_thread(user_id: UUID, thread_id: UUID) -> ReaderAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if thread.get("record_scope") != "analysis":
        raise HTTPException(status_code=400, detail="Reader ask thread is not a legacy analysis thread")

    record_id = _parse_uuid(str(thread["record_id"]), "thread record_id is invalid")
    archived = await repo.archive_thread(user_id, thread_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    next_thread_option = _resolve_reader_ask_model_option_or_422(
        selected_key=cast(str | None, thread.get("selected_model_key")),
        strict=False,
    )

    next_thread = await repo.get_or_create_default_thread(
        user_id,
        record_id,
        title=thread.get("title") or "Ask Claread",
        selected_model_key=next_thread_option.key,
    )
    messages = await repo.list_messages(_parse_uuid(next_thread["id"], "thread id is invalid"), limit=100)
    return ReaderAskThreadDetail.model_validate({**_thread_summary_payload(next_thread), "messages": messages})


async def list_reading_record_threads(
    user_id: UUID,
    reading_record_id: UUID,
) -> ReaderAskThreadListResponse:
    items = await repo.list_reading_record_threads(user_id, reading_record_id)
    return ReaderAskThreadListResponse(
        items=[ReaderAskThreadSummary.model_validate(_thread_summary_payload(item)) for item in items]
    )


async def ensure_default_reading_record_thread(
    user_id: UUID,
    reading_record_id: UUID,
    *,
    title: str,
    model_key: str | None = None,
) -> dict[str, Any]:
    thread = await repo.get_or_create_default_thread_for_reading_record(
        user_id,
        reading_record_id,
        title=title,
        selected_model_key=model_key,
    )
    if thread.get("selected_model_key") is None:
        selected_option = _resolve_reader_ask_model_option_or_422(
            selected_key=model_key,
            strict=False,
        )
        updated_thread = await repo.update_thread_selected_model(
            user_id,
            _parse_uuid(thread["id"], "thread id is invalid"),
            selected_model_key=selected_option.key,
        )
        if updated_thread is not None:
            thread = updated_thread
    return thread


async def get_reading_record_thread_detail(
    user_id: UUID,
    thread_id: UUID,
    *,
    reading_record_id: UUID,
) -> ReaderRecordAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if thread.get("record_scope") != "reading_record":
        raise HTTPException(
            status_code=404, detail="Reader ask thread not found for this Reading Record"
        )
    if thread.get("reading_record_id") != str(reading_record_id):
        raise HTTPException(
            status_code=404, detail="Reader ask thread not found for this Reading Record"
        )
    messages = await repo.list_messages(thread_id, limit=100)
    # RR-only history DTO: allows agentic_evidence with strict schema without
    # expanding the Analysis Ask ReaderAskMessage wire contract.
    return ReaderRecordAskThreadDetail.model_validate(
        {**_thread_summary_payload(thread), "messages": messages}
    )


async def resolve_and_persist_thread_model_option(
    *,
    user_id: UUID,
    thread_id: UUID,
    requested_key: str | None,
    reading_record_id: UUID | None = None,
) -> model_options_svc.ResolvedReaderAskModelOption:
    """Resolve Ask model option for a thread and persist fallback/explicit selection.

    Composition-layer helper shared by legacy stream and agentic Ask wiring.
    - request.model present → strict=True (unknown/deleted keys → 422)
    - only thread.selected_model_key → strict=False (historical keys soft-fallback)
    When fallback or explicit selection changes the key, persist it on the thread.
    """
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if reading_record_id is not None:
        if thread.get("record_scope") != "reading_record":
            raise HTTPException(
                status_code=404,
                detail="Reader ask thread not found for this Reading Record",
            )
        if thread.get("reading_record_id") != str(reading_record_id):
            raise HTTPException(
                status_code=404,
                detail="Reader ask thread not found for this Reading Record",
            )

    requested = requested_key or None
    current_key = cast(str | None, thread.get("selected_model_key"))
    selected_key = requested or current_key
    option = _resolve_reader_ask_model_option_or_422(
        selected_key=selected_key,
        strict=requested is not None,
    )
    should_persist = (
        requested is not None or option.used_fallback or current_key is None
    ) and current_key != option.key
    if should_persist:
        await repo.update_thread_selected_model(
            user_id,
            thread_id,
            selected_model_key=option.key,
        )
    return option


async def reset_reading_record_thread(
    user_id: UUID,
    thread_id: UUID,
    *,
    reading_record_id: UUID,
    title: str,
) -> ReaderRecordAskThreadDetail:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if thread.get("record_scope") != "reading_record":
        raise HTTPException(
            status_code=404, detail="Reader ask thread not found for this Reading Record"
        )
    if thread.get("reading_record_id") != str(reading_record_id):
        raise HTTPException(
            status_code=404, detail="Reader ask thread not found for this Reading Record"
        )

    archived = await repo.archive_thread(user_id, thread_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    next_thread_option = _resolve_reader_ask_model_option_or_422(
        selected_key=cast(str | None, thread.get("selected_model_key")),
        strict=False,
    )
    next_thread = await repo.get_or_create_default_thread_for_reading_record(
        user_id,
        reading_record_id,
        title=thread.get("title") or title,
        selected_model_key=next_thread_option.key,
    )
    messages = await repo.list_messages(
        _parse_uuid(next_thread["id"], "thread id is invalid"), limit=100
    )
    return ReaderRecordAskThreadDetail.model_validate(
        {**_thread_summary_payload(next_thread), "messages": messages}
    )
