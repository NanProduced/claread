from __future__ import annotations

from typing import Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException

from app.config.settings import get_settings
from app.schemas.reader_ask import (
    ReaderAskSelectedModel,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
)
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V2,
    ReaderRecordAskThreadDetail,
)
from app.services.reader_record_ask import model_options as model_options_svc
from app.services.reader_record_ask import repository as repo
from app.services.reader_record_ask.web_search_common import (
    resolve_web_search_availability_for_option,
)


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
    # ASK-WEB-G3-R1: project the server-declared Web Search capability
    # via the canonical ``resolve_web_search_availability_for_option``
    # helper in ``web_search_common``. The helper resolves the model
    # config from ``option.selection``, calls the production registry
    # exactly once, and projects the binding to ``"available"`` /
    # ``"unavailable"``. There is no duplicate resolver here — the
    # previous local copy was removed in G3-R1 to collapse all
    # capability projection into a single canonical call chain.
    web_search_capability: Literal["unavailable", "available"] = (
        resolve_web_search_availability_for_option(option)
    )
    return ReaderAskSelectedModel(
        key=option.key,
        label=option.label,
        description=option.description,
        model_name=option.main_model_name,
        replan_model_name=option.replan_model_name,
        price_multiplier=option.billing.price_multiplier,
        web_search_capability=web_search_capability,
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


async def list_reading_record_threads(
    user_id: UUID,
    reading_record_id: UUID,
) -> ReaderAskThreadListResponse:
    items = await repo.list_reading_record_threads(user_id, reading_record_id)
    return ReaderAskThreadListResponse(
        items=[
            ReaderAskThreadSummary.model_validate(_thread_summary_payload(item))
            for item in items
        ]
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
    for message in messages:
        if (
            message.get("role") == "assistant"
            and message.get("execution_version") != EXECUTION_VERSION_AGENTIC_V2
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "history_execution_version_untrusted",
                    "message": "该历史回答不是可验证的 Reading Record Ask v2 结果。",
                },
            )
    # RR-only history DTO: the public shape is defined by the v2 stream schema
    # and never falls back to an older Ask message contract.
    return ReaderRecordAskThreadDetail.model_validate(
        {**_thread_summary_payload(thread), "messages": messages}
    )


async def resolve_and_persist_thread_model_option(
    *,
    user_id: UUID,
    thread_id: UUID,
    requested_key: str | None,
    reading_record_id: UUID,
) -> model_options_svc.ResolvedReaderAskModelOption:
    """Resolve and persist a model option for a Reading Record Ask v2 thread.

    Every v2 call carries the Reading Record identity so the thread scope and
    identity fence are always checked before model-option resolution.
    - request.model present → strict=True (unknown/deleted keys → 422)
    - only thread.selected_model_key → strict=False (historical keys soft-fallback)
    When fallback or explicit selection changes the key, persist it on the thread.
    """
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
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
