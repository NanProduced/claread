from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.contracts.anchor_validation import (
    ANCHOR_RECORD_ID_MISMATCH,
    READING_RECORD_NOT_FOUND,
    READING_RECORD_SNAPSHOT_INVALID,
    AnchorValidationError,
)
from app.database import connection as db_connect
from app.schemas.reader_ask import (
    ReaderAskMessageRetryRequest,
    ReaderAskModelOptionListResponse,
    ReaderAskModelOptionSummary,
    ReaderAskReadingRecordAnchor,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskMessageRequest,
)
from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2
from app.services.reader_orchestration.anchor_gate import load_validated_reading_record_anchor
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_record_ask import thread_service
from app.services.reader_record_ask.execution_config import (
    ReaderRecordAskExecutionConfig,
    ReaderRecordAskExecutionUnavailable,
    resolve_reader_record_ask_execution,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository
from app.services.reader_record_ask.turn_lifecycle import StreamLifecycleHook
from app.services.reader_record_ask.web_search_contracts import WebSearchMode

logger = logging.getLogger(__name__)


async def list_reading_record_ask_model_options() -> ReaderAskModelOptionListResponse:
    """Return the v2 model catalog owned by the Reading Record Ask surface."""
    from app.config.settings import get_settings
    from app.services.reader_record_ask.model_options import (
        list_reader_ask_model_options,
    )

    items, default_key = list_reader_ask_model_options(get_settings())
    return ReaderAskModelOptionListResponse(
        default_key=default_key,
        items=[
            ReaderAskModelOptionSummary(
                **thread_service._selected_model_payload(item),
                is_default=item.is_default,
            )
            for item in items
        ],
    )


# Default replayed web search mode when the persisted user message metadata
# does not carry ``web_search_mode`` (legacy rows persisted before ASK-WEB-G1-).
# Fail-closed: never silently grant a capability the original turn did not
# explicitly record as ``allowed``.
_DEFAULT_REPLAY_WEB_SEARCH_MODE: WebSearchMode = "disabled"


@dataclass(slots=True, frozen=True)
class RetryPreparedResult:
    """Result of the retry preflight (mirrors Send's prepared tuple).

    Carries everything ``retry_reading_record_ask_message`` needs to
    stream the retry without re-resolving the persisted option or
    re-building the model. The route awaits the preflight coroutine
    before constructing the StreamingResponse so a config-unavailable
    option surfaces as a real HTTP 503 instead of an SSE error frame.

    The result is always the v2 execution config. Historical v1, legacy,
    missing, and unknown execution identities are rejected before this
    object is constructed.
    """

    reading_record_id: UUID
    facts: object
    execution: ReaderRecordAskExecutionConfig
    # ASK-UX-COT-COMPOSER- — the replayed focus anchor set, parsed
    # from the persisted retry snapshot and re-validated against the live
    # document during preflight (fail-closed). ``None`` = legacy
    # single-anchor / no-anchor turns.
    focus_anchors: list[ReaderAskReadingRecordAnchor] | None = None


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_uuid",
                "field": field,
                "message": f"{field} must be a UUID",
            },
        ) from exc


def _reading_record_error(
    *,
    code: str,
    field: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": code,
            "field": field,
            "message": message,
        },
    )


async def _load_snapshot_facts_raw(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        return await repository.load_snapshot_facts(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
        )


async def _load_snapshot_facts(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    try:
        return await _load_snapshot_facts_raw(
            user_id=user_id,
            reading_record_id=reading_record_id,
        )
    except LookupError as exc:
        raise _reading_record_error(
            code=READING_RECORD_NOT_FOUND,
            field="reading_record_id",
            message=str(exc),
        ) from exc
    except ValueError as exc:
        raise _reading_record_error(
            code=READING_RECORD_SNAPSHOT_INVALID,
            field="reading_record_id",
            message=str(exc),
        ) from exc


async def _load_validated_anchor_raw(
    *,
    user_id: UUID,
    anchor: object,
) -> None:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        await load_validated_reading_record_anchor(
            conn,
            repository=repository,
            user_id=user_id,
            anchor=anchor,
        )


def resolve_request_focus_anchors(
    request: ReaderRecordAskMessageRequest,
) -> list[ReaderAskReadingRecordAnchor]:
    """ASK-UX-COT-COMPOSER- — the effective anchor set for a request.

    The plural ``focus_anchors`` field wins when present (it is the
    canonical multi-selection contract; new Web clients send every
    auto/manual selection anchor there). The singular ``anchor`` is the
    legacy compatibility entry and is used ONLY when ``focus_anchors`` is
    absent. The two are never merged — a plural request's singular field
    is ignored so a stale first anchor cannot sneak back in.
    """
    raw_focus_anchors = getattr(request, "focus_anchors", None)
    if isinstance(raw_focus_anchors, list):
        return list(raw_focus_anchors)
    raw_anchor = getattr(request, "anchor", None)
    if raw_anchor is not None:
        return [raw_anchor]
    return []


async def _validate_reading_record_anchors(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    request: ReaderRecordAskMessageRequest,
) -> list[ReaderAskReadingRecordAnchor]:
    """Gate EVERY effective anchor; fail the whole request closed.

     Each anchor is independently validated against the same
    record / base / generation / document (ownership + staleness +
    unit/segment/text match). ANY invalid, unauthorized, foreign, or
    stale anchor aborts the request before the stream — there is no
    partial acceptance followed by a model call.
    """
    anchors = resolve_request_focus_anchors(request)
    if not anchors:
        await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
        return []
    plural = request.focus_anchors is not None
    for index, anchor in enumerate(anchors):
        field = f"focus_anchors[{index}]" if plural else "anchor"
        anchor_record_id = _parse_uuid(anchor.record_id, field=f"{field}.record_id")
        if anchor_record_id != reading_record_id:
            raise _reading_record_error(
                code=ANCHOR_RECORD_ID_MISMATCH,
                field=f"{field}.record_id",
                message="anchor.record_id does not match the route reading_record_id",
            )
        try:
            await _load_validated_anchor_raw(
                user_id=user_id,
                anchor=anchor,
            )
        except AnchorValidationError as exc:
            raise _reading_record_error(
                code=exc.code,
                field=field,
                message=exc.message,
            ) from exc
    return anchors


async def _ensure_default_thread(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> dict[str, str]:
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        reading_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return {"id": str(thread["id"]), "title": str(thread.get("title") or "Ask Claread")}


async def list_reading_record_ask_threads(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadListResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.list_reading_record_threads(user_id, parsed_record_id)


async def create_default_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadSummary:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return ReaderAskThreadSummary.model_validate(thread_service._thread_summary_payload(thread))


async def get_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
):
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    # Shared record access guard: deleted records close thread-detail
    # access exactly like every other record-bound Ask entry point.
    await _load_snapshot_facts(
        user_id=user_id, reading_record_id=parsed_record_id
    )
    return await thread_service.get_reading_record_thread_detail(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
    )


async def reset_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
):
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.reset_reading_record_thread(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )


async def _resolve_agentic_execution(
    option,
    *,
    web_search_mode: str = "disabled",
) -> ReaderRecordAskExecutionConfig:
    """Compile a persisted option into a unified execution config.

    ASK-M1: replaces the old ``_build_agentic_model_for_option``. Both
    send and retry paths now call this so the persisted option is the
    single source of truth for the model, the provider completion cap,
    and the host usage limit. Fail-closed: raises a typed 503 — never
    silently substitutes the global default model.

    ASK-WEB-G1-``web_search_mode`` is the user-visible request
    toggle propagated from :attr:`ReaderRecordAskMessageRequest.web_search_mode`.
    The resolver translates it into a :class:`ResolvedWebSearchCapability`
    attached to the returned config so the runtime can mount the
    ``search_web`` tool and inject the :class:`WebSearchBackend` port.
    """
    try:
        return resolve_reader_record_ask_execution(
            option,
            web_search_mode=web_search_mode,  # type: ignore[arg-type]
        )
    except ReaderRecordAskExecutionUnavailable as exc:
        logger.warning(
            "agentic_execution_unavailable code=model_unconfigured "
            "model_key=%s reason=%s",
            exc.option_key,
            exc.reason,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "model_unconfigured",
                "message": "Ask Claread model is not configured for the selected option.",
                "model_key": exc.option_key,
            },
        ) from None


@dataclass(slots=True, frozen=True)
class SendPreparedResult:
    """Everything required before StreamingResponse is constructed.

    ``submission`` is the durable ensure result (or None for pre-
    clients without client_submission_id). Raising here produces a real
    HTTP 4xx/503 — never HTTP 200 + SSE error for missing tables.
    """

    reading_record_id: UUID
    thread_id: UUID
    execution: ReaderRecordAskExecutionConfig
    submission: Any  # SubmissionEnsureResult | None
    model_option_key: str


async def prepare_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    thread_id: UUID | None = None,
) -> SendPreparedResult:
    """Validate + resolve execution + durable submission BEFORE stream.

    ``Ensure_submission_for_send`` runs here so missing 0026 table
    surfaces as HTTP 503, not SSE error after StreamingResponse starts.
    """
    from app.services.reader_record_ask.submission_gateway import (
        SubmissionEnsureResult,
        build_retry_snapshot,
        ensure_submission_for_send,
    )

    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    validated_focus_anchors = await _validate_reading_record_anchors(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        request=request,
    )
    if thread_id is None:
        thread = await _ensure_default_thread(
            user_id=user_id,
            reading_record_id=parsed_record_id,
        )
        resolved_thread_id = _parse_uuid(thread["id"], field="thread id is invalid")
    else:
        resolved_thread_id = thread_id

    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=resolved_thread_id,
        requested_key=request.model,
        reading_record_id=parsed_record_id,
    )
    execution = await _resolve_agentic_execution(
        option,
        web_search_mode=request.web_search_mode,
    )
    model_option_key = execution.option_key
    if request.web_search_mode == "allowed":
        capability = execution.web_search_capability
        if (
            capability is None
            or not capability.enabled_for_turn
            or execution.web_search_backend is None
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "web_search_unavailable",
                    "message": "Web Search is temporarily unavailable for this model.",
                },
            )

    web_mode = (
        request.web_search_mode
        if isinstance(getattr(request, "web_search_mode", None), str)
        else "disabled"
    )
    retry_snapshot = build_retry_snapshot(
        model_option_key=model_option_key,
        web_search_mode=web_mode,
        route_identity="reader_record_ask",
        # Persist the full validated focus set for regenerate replay.
        focus_anchors=[
            anchor.model_dump(mode="json") for anchor in validated_focus_anchors
        ]
        or None,
    )

    # Durable claim+pair+bind BEFORE StreamingResponse.
    submission: SubmissionEnsureResult | None = await ensure_submission_for_send(
        repo=ReaderRecordAskRepository(),
        thread_id=resolved_thread_id,
        user_id=user_id,
        client_submission_id=(
            request.client_submission_id
            if isinstance(getattr(request, "client_submission_id", None), UUID)
            else None
        ),
        content_md=request.content,
        retry_snapshot=retry_snapshot,
    )

    return SendPreparedResult(
        reading_record_id=parsed_record_id,
        thread_id=resolved_thread_id,
        execution=execution,
        submission=submission,
        model_option_key=model_option_key,
    )


async def _stream_agentic_v2(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    execution: ReaderRecordAskExecutionConfig | None = None,
    lifecycle: StreamLifecycleHook | None = None,
    prepared: SendPreparedResult | None = None,
) -> AsyncIterator[str]:
    """Dispatch the v2 stream using the pre-stream submission.

    Generator must NOT re-claim or re-create pairs. Model only runs when
    prepared.submission is None (pre-) or may_create_model=True.
    """
    from app.services.reader_record_ask.sse import encode_sse

    if prepared is not None:
        ensure = prepared.submission
        model_option_key = prepared.model_option_key
        execution = prepared.execution
        thread_id = prepared.thread_id
        reading_record_id = prepared.reading_record_id
    else:
        # Defensive fallback for tests that skip prepare — still not ideal.
        ensure = None
        model_option_key = (
            execution.option_key if execution is not None else request.model
        )

    if ensure is not None and ensure.stop_model:
        yield encode_sse(
            "submission.reconcile",
            {
                "client_submission_id": ensure.client_submission_id,
                "thread_id": ensure.thread_id,
                "status": ensure.status,
                "user_message_id": ensure.user_message_id,
                "assistant_message_id": ensure.assistant_message_id,
                "terminal_code": ensure.terminal_code,
                "claim_generation": ensure.claim_generation,
                "action_hint": (
                    "wait"
                    if ensure.status == "streaming"
                    else "retry"
                    if ensure.status in {"failed", "cancelled"}
                    else "resend"
                    if ensure.status in {"claimed", "not_found"}
                    else "none"
                ),
            },
        )
        return

    precreated_user = ensure.user_message if ensure else None
    precreated_asst = ensure.assistant_message if ensure else None
    claim_gen = ensure.claim_generation if ensure else None
    client_sub_id = request.client_submission_id

    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
    )
    from app.services.reader_record_ask.production_stream import (
        stream_agentic_thread_message,
    )

    if execution is None:
        raise RuntimeError("Reader Record Ask v2 execution was not prepared")
    model = execution.model
    model_settings = execution.model_settings()
    usage_limits = execution.usage_limits
    web_search_capability = execution.web_search_capability
    resolved_backend = execution.web_search_backend
    web_search_backend = (
        resolved_backend if web_search_capability is not None else None
    )
    main_model_config = execution.resolved_model_config

    # The effective anchor set (plural focus_anchors, or the
    # legacy singular anchor as fallback). The primary selection is the
    # first anchor; the full set rides along for gate + model view +
    # retry replay.
    focus_anchors = resolve_request_focus_anchors(request)
    primary_anchor = focus_anchors[0] if focus_anchors else None

    async for chunk in stream_agentic_thread_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        content=request.content,
        facts=facts,
        request_anchor=primary_anchor,
        validated_anchor=None,
        focus_anchors=focus_anchors or None,
        main_model_config=main_model_config,
        client_submission_id=client_sub_id,
        existing_user_message=precreated_user,
        existing_assistant_message=precreated_asst,
        claim_generation=claim_gen,
        model_option_key=model_option_key,
        stable_document_id=None,
        model=model,
        model_settings=model_settings,
        usage_limits=usage_limits,
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        lifecycle=lifecycle,
    ):
        yield chunk


async def send_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    prepared: SendPreparedResult | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
        )
    async for chunk in _stream_agentic_v2(
        user_id=user_id,
        reading_record_id=prepared.reading_record_id,
        thread_id=prepared.thread_id,
        request=request,
        execution=prepared.execution,
        lifecycle=lifecycle,
        prepared=prepared,
    ):
        yield chunk


async def stream_reading_record_ask_thread_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    prepared: SendPreparedResult | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
            thread_id=thread_id,
        )
    async for chunk in _stream_agentic_v2(
        user_id=user_id,
        reading_record_id=prepared.reading_record_id,
        thread_id=prepared.thread_id,
        request=request,
        execution=prepared.execution,
        lifecycle=lifecycle,
        prepared=prepared,
    ):
        yield chunk


async def prepare_reading_record_ask_retry(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID | None = None,
) -> RetryPreparedResult:
    """Retry preflight — runs before the StreamingResponse starts.

    v2 retry preflight order (immutable execution identity):

    1. Validate reading_record_id / thread / message ownership;
    2. Load target assistant + preceding user message;
    3. Require the persisted ``reader_record_ask_agentic_v2`` identity;
    4. Resolve only the v2 execution adapter;
    5. v1, legacy, missing, or unknown identities → typed 409 before any
       provider execution.

    ASK-WEB-G1-when ``message_id`` is provided, the preflight loads
    the persisted user message metadata and replays the original turn's
    ``web_search_mode`` (the **resolved** value persisted at send time,
    not the raw request toggle). Fail-closed to ``disabled`` when absent.
    """
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    if message_id is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "retry_target_missing",
                "message": "Retry requires a persisted assistant message id.",
            },
        )

    # 1–2: ownership + load assistant / user pair.
    repo = ReaderRecordAskRepository()
    thread = await repo.get_thread(
        user_id=user_id,
        thread_id=thread_id,
        reading_record_id=parsed_record_id,
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    assistant_msg, user_msg = await repo.get_assistant_message_with_preceding_user_message(
        thread_id=thread_id,
        message_id=message_id,
    )
    if assistant_msg is None or user_msg is None:
        raise HTTPException(
            status_code=404,
            detail="Retried assistant message or its preceding user message was not found",
        )

    # 3: require the immutable v2 execution identity from persisted facts.
    has_v2_execution = _has_persisted_v2_execution(
        assistant_msg=assistant_msg,
        user_msg=user_msg,
    )
    if not has_v2_execution:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "retry_execution_version_untrusted",
                "message": "无法确认这轮回答是 Reading Record Ask v2，请新建提问。",
            },
        )

    # v2 preflight facts + execution from the *persisted*
    # retry snapshot model option (never current UI / thread selection /
    # retry body model).
    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=parsed_record_id,
    )
    snapshot_model_key = _extract_snapshot_model_option_key(
        assistant_msg=assistant_msg,
        user_msg=user_msg,
    )
    if not snapshot_model_key:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "retry_snapshot_incomplete",
                "message": "无法确认原模型配置，请重新提问。",
                "action_hint": "reask",
            },
        )
    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=thread_id,
        requested_key=snapshot_model_key,
        reading_record_id=parsed_record_id,
    )

    replayed_web_search_mode: WebSearchMode = await _load_replayed_web_search_mode(
        thread_id=thread_id,
        message_id=message_id,
    )

    execution = await _resolve_agentic_execution(
        option,
        web_search_mode=replayed_web_search_mode,
    )
    # Replay the persisted focus set; re-gated against the live
    # document (fail-closed on staleness) before any model call.
    replayed_focus_anchors = await _revalidate_snapshot_focus_anchors(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        raw_anchors=_extract_snapshot_focus_anchors(
            assistant_msg=assistant_msg,
            user_msg=user_msg,
        ),
    )
    if replayed_web_search_mode == "allowed":
        capability = execution.web_search_capability
        if (
            capability is None
            or not capability.enabled_for_turn
            or execution.web_search_backend is None
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "web_search_replay_unavailable",
                    "message": "Web Search is no longer available for this retry.",
                },
            )
    return RetryPreparedResult(
        reading_record_id=parsed_record_id,
        facts=facts,
        execution=execution,
        focus_anchors=replayed_focus_anchors,
    )


def _extract_snapshot_model_option_key(
    *,
    assistant_msg: dict[str, Any],
    user_msg: dict[str, Any],
) -> str | None:
    """Model option key from immutable retry snapshot only."""
    for blob in (
        assistant_msg.get("metadata_json") or {},
        user_msg.get("metadata_json") or {},
    ):
        if not isinstance(blob, dict):
            continue
        snap = blob.get("retry_snapshot")
        if isinstance(snap, dict):
            key = snap.get("model_option_key")
            if isinstance(key, str) and key.strip():
                return key.strip()
        key2 = blob.get("model_option_key")
        if isinstance(key2, str) and key2.strip():
            return key2.strip()
    return None


def _extract_snapshot_focus_anchors(
    *,
    assistant_msg: dict[str, Any],
    user_msg: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """ASK-UX-COT-COMPOSER- — focus anchors from the retry snapshot.

    Assistant metadata wins (it carries the authoritative snapshot);
    returns ``None`` when the original turn had no focus set.
    """
    for blob in (
        assistant_msg.get("metadata_json") or {},
        user_msg.get("metadata_json") or {},
    ):
        if not isinstance(blob, dict):
            continue
        snap = blob.get("retry_snapshot")
        if isinstance(snap, dict) and snap.get("focus_anchors") is not None:
            raw = snap.get("focus_anchors")
            if isinstance(raw, list):
                return [entry for entry in raw if isinstance(entry, dict)]
    return None


async def _revalidate_snapshot_focus_anchors(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    raw_anchors: list[dict[str, Any]] | None,
) -> list[ReaderAskReadingRecordAnchor] | None:
    """Parse + re-gate the replayed focus set, fail-closed.

    Regenerate replays the SAME focus the original turn saw, but the
    document may have moved on (generation bump / reparse): every anchor
    is re-validated against the live record/base/generation/document.
    Any parse failure, foreign record, or stale/invalid anchor aborts
    the retry with a typed 409 — never a partial model call.
    """
    if not raw_anchors:
        return None
    parsed: list[ReaderAskReadingRecordAnchor] = []
    for index, entry in enumerate(raw_anchors):
        try:
            parsed.append(ReaderAskReadingRecordAnchor.model_validate(entry))
        except Exception as exc:  # pydantic validation
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "retry_focus_invalid",
                    "field": f"focus_anchors[{index}]",
                    "message": "原选区快照无法解析，请重新提问。",
                    "action_hint": "reask",
                },
            ) from exc
    for index, anchor in enumerate(parsed):
        anchor_record_id = _parse_uuid(
            anchor.record_id, field=f"focus_anchors[{index}].record_id"
        )
        if anchor_record_id != reading_record_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "retry_focus_stale",
                    "field": f"focus_anchors[{index}].record_id",
                    "message": "原选区已失效，请重新提问。",
                    "action_hint": "reask",
                },
            )
        try:
            await _load_validated_anchor_raw(user_id=user_id, anchor=anchor)
        except AnchorValidationError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "retry_focus_stale",
                    "field": f"focus_anchors[{index}]",
                    "message": "原选区已失效，请重新提问。",
                    "action_hint": "reask",
                },
            ) from exc
    return parsed


def _has_persisted_v2_execution(
    *,
    assistant_msg: dict[str, Any],
    user_msg: dict[str, Any],
) -> bool:
    """Accept only an explicit, consistent persisted v2 identity.

    The absence of a version is not evidence of v2.  Any v1, legacy, or
    unknown marker is rejected so retry cannot cross execution chains or
    reach a provider before the route has established its identity fence.
    """

    def _meta(msg: dict[str, Any]) -> dict[str, Any]:
        raw = msg.get("metadata_json") or {}
        return raw if isinstance(raw, dict) else {}

    a_meta = _meta(assistant_msg)
    u_meta = _meta(user_msg)
    a_snap = a_meta.get("retry_snapshot")
    u_snap = u_meta.get("retry_snapshot")

    # These are the three independent persisted authorities for retry.  Do
    # not treat a flattened metadata marker, or a single v2 marker, as proof
    # that the whole turn belongs to the v2 execution chain.
    required_markers = (
        assistant_msg.get("turn_run_execution_version"),
        a_snap.get("execution_version") if isinstance(a_snap, dict) else None,
        u_snap.get("execution_version") if isinstance(u_snap, dict) else None,
    )
    if any(value != EXECUTION_VERSION_AGENTIC_V2 for value in required_markers):
        return False

    # The flattened fields are derived copies written by the submission
    # gateway.  If they exist, they must agree as well; a stale v1/unknown
    # copy is a persisted conflict and must fail closed.
    for metadata in (a_meta, u_meta):
        if (
            "execution_version" in metadata
            and metadata["execution_version"] != EXECUTION_VERSION_AGENTIC_V2
        ):
            return False
    return True


async def _load_replayed_web_search_mode(
    *,
    thread_id: UUID,
    message_id: UUID,
) -> WebSearchMode:
    """Load the persisted ``web_search_mode`` for retry replay.

    Reads the assistant message identified by ``message_id`` and its
    closest preceding user message, then extracts the ``web_search_mode``
    field from the user message's persisted metadata.

    ASK-WEB-G1- fail-closed contract
    ----------------------------------
    Per the spec, retry must only trust server-side persisted facts.
    The previous implementation silently degraded every error case to
    ``"disabled"``, which let ownership mismatches and DB failures start
    a generator with the wrong capability truth. The new contract is:

    - Assistant message not found in this thread → ``HTTPException(404)``
      (typed not-found — message/thread ownership mismatch).
    - No preceding user message → ``HTTPException(404)`` (typed
      not-found — cannot replay a turn without the originating user
      message).
    - User message metadata is missing the ``web_search_mode`` key
      (legacy rows persisted before ASK-WEB-G1-) → ``"disabled"``
      (compatible — legacy rows never recorded a capability).
    - User message metadata is not a mapping → ``HTTPException(503)``
      (malformed persisted state, not a legacy row).
    - Persisted value is ``"allowed"`` → ``"allowed"`` (the resolver
      will separately enforce adapter readiness; the retry preflight
      returns typed 503 if the adapter is no longer wired).
    - Persisted value is ``"disabled"`` → ``"disabled"``.
    - Persisted value is present but not one of
      ``{"disabled", "allowed"}`` → ``HTTPException(503)`` (typed
      preflight unavailable — illegal metadata, never silently
      degraded to ``"disabled"``).
    - Any DB error → ``HTTPException(503)`` (typed preflight
      unavailable — DB failures must not start a generator with an
      unverified capability truth).

    The returned value is fed into :func:`resolve_web_search_capability`
    by the caller; if the provider is no longer wired, the resolver
    returns a typed unavailable capability (``enabled_for_turn=False``).
    """
    try:
        repo = ReaderRecordAskRepository()
        assistant_msg, user_msg = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=message_id,
        )
    except Exception:  # noqa: BLE001 — fail-closed, no leakage
        logger.warning(
            "retry_replay_web_search_mode_failed code=db_read_failed "
            "thread_id=%s message_id=%s",
            thread_id,
            message_id,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retry_replay_unavailable",
                "message": "Retry is temporarily unavailable.",
            },
        ) from None

    if assistant_msg is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "retry_message_not_found",
                "message": "Retried message was not found in this thread.",
            },
        )
    if user_msg is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "retry_preceding_user_message_not_found",
                "message": "Could not locate the original user message for retry.",
            },
        )

    metadata = user_msg["metadata_json"] if "metadata_json" in user_msg else {}
    if not isinstance(metadata, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retry_replay_unavailable",
                "message": "Retry is temporarily unavailable.",
            },
        )

    raw_mode = metadata.get("web_search_mode")
    if raw_mode == "allowed":
        return "allowed"
    if raw_mode == "disabled":
        return "disabled"
    if raw_mode is None:
        # Legacy row persisted before ASK-WEB-G1- — no
        # ``web_search_mode`` key. Compatible: default to ``"disabled"``.
        return _DEFAULT_REPLAY_WEB_SEARCH_MODE
    # Persisted value is present but illegal — fail-closed with a typed
    # 503 so the generator never starts with an unverified capability.
    logger.warning(
        "retry_replay_web_search_mode_failed code=illegal_metadata_value "
        "thread_id=%s message_id=%s raw_mode=%r",
        thread_id,
        message_id,
        raw_mode if isinstance(raw_mode, str) else type(raw_mode).__name__,
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "retry_replay_unavailable",
            "message": "Retry is temporarily unavailable.",
        },
    )


async def retry_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    request: ReaderAskMessageRetryRequest,
    prepared: RetryPreparedResult | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    """Stream a retry. ``prepared`` must come from the route's preflight.

    The generator must not re-load facts, re-resolve the persisted
    option, or rebuild the execution config — the route has already
    done all three via :func:`prepare_reading_record_ask_retry`. This
    guarantees Send and Retry have identical fail-closed HTTP semantics
    (typed 503 before StreamingResponse) and identical model + budget
    propagation.
    """
    if prepared is None:
        prepared = await prepare_reading_record_ask_retry(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            message_id=message_id,
        )
    parsed_record_id = prepared.reading_record_id

    # v2 mode — preflight has already resolved facts + execution.
    from app.services.reader_record_ask.production_stream import (
        retry_agentic_thread_message,
    )

    execution = prepared.execution
    # G3-defensive invariant — when ``web_search_capability`` is
    # ``None`` (disabled mode), the backend MUST also be ``None`` so
    # the runtime never mounts ``search_web`` on a disabled retry.
    retry_capability = execution.web_search_capability
    retry_backend = (
        execution.web_search_backend
        if retry_capability is not None
        else None
    )
    async for chunk in retry_agentic_thread_message(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=thread_id,
        message_id=message_id,
        facts=prepared.facts,
        model=execution.model,
        model_settings=execution.model_settings(),
        usage_limits=execution.usage_limits,
        main_model_config=execution.resolved_model_config,
        # Replay the same validated focus set as the original turn.
        focus_anchors=prepared.focus_anchors,
        # ASK-WEB-G1-forward the resolved web search capability so
        # retry uses the same execution truth as the original send. When
        # ``None`` (capability not granted on the original turn) the
        # runtime must NOT mount the ``search_web`` tool on retry.
        web_search_capability=retry_capability,
        # G3-forward the real provider backend so retry uses the
        # same adapter as the original send. When ``None`` the runtime
        # must NOT mount ``search_web`` on retry.
        web_search_backend=retry_backend,
        lifecycle=lifecycle,
    ):
        yield chunk
