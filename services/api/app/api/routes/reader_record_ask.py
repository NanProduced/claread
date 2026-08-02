from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.config.settings import get_settings
from app.schemas.reader_ask import (
    ReaderAskMessageRetryRequest,
    ReaderAskModelOptionListResponse,
    ReaderAskSubmissionReconcileResponse,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskMessageRequest,
)
from app.schemas.reader_record_ask_stream import ReaderRecordAskThreadDetail
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_record_ask import service as rr_ask_svc
from app.services.reader_record_ask.citation_navigation import (
    load_live_document_fence,
    resolve_citation_navigation,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reader-record-ask"])


class CitationNavigateLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    anchor_segment_id: str | None = None
    canonical_text_start_utf16: int | None = None
    canonical_text_end_utf16: int | None = None


class CitationNavigateResponse(BaseModel):
    """Public navigate response — typed location only, no handles/identity."""

    model_config = ConfigDict(extra="forbid")

    status: str
    location: CitationNavigateLocation | None = None
    reason: str | None = None


def _is_dev_error_mode() -> bool:
    return get_settings().app_env != "production"


class _StreamLifecycleContext:
    """Mutable lifecycle hook shared between the route and the generator.

    ASK-TURN-LIFECYCLE R1: the generator (``stream_agentic_thread_message``)
    sets ``turn_run_id`` / ``message_id`` as soon as the rows are persisted.
    The route's ``finally`` block reads them to reconcile any still-
    streaming row when the FastAPI generator is closed (client disconnect,
    BFF disconnect, ASGI cancellation) without a typed terminal.

    The hook is intentionally minimal — it does NOT carry answer text,
    reasoning, citations, or any user-visible payload. Only the two
    identifiers needed for the idempotent reconciliation write.
    """

    def __init__(self) -> None:
        self.turn_run_id: UUID | None = None
        self.message_id: UUID | None = None
        self.terminal_emitted: bool = False

    def mark_terminal_emitted(self) -> None:
        """Mark that the generator already wrote a typed terminal."""
        self.terminal_emitted = True

    def register_active_turn(
        self,
        *,
        turn_run_id: UUID,
        message_id: UUID,
    ) -> None:
        self.turn_run_id = turn_run_id
        self.message_id = message_id

    async def reconcile_if_streaming(self) -> None:
        """Reconcile any still-streaming row to ``cancelled``.

        Idempotent: ``reconcile_stale_streaming_turn_run`` itself guards
        on ``status = 'streaming'``, and ``terminal_emitted`` short-
        circuits the call entirely on the success path.
        """
        if self.terminal_emitted:
            return
        if self.turn_run_id is None or self.message_id is None:
            return
        repo = ReaderRecordAskRepository()
        try:
            await repo.reconcile_stale_streaming_turn_run(
                turn_run_id=self.turn_run_id,
                message_id=self.message_id,
                run_status="cancelled",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "stream_lifecycle reconcile failed: turn_run_id=%s message_id=%s",
                self.turn_run_id,
                self.message_id,
            )


def _streaming_response(
    generator,
    *,
    lifecycle: _StreamLifecycleContext | None = None,
) -> StreamingResponse:
    """Wrap an async generator in a StreamingResponse with terminal cleanup.

    ASK-TURN-LIFECYCLE R1: the ``finally`` clause guarantees that any
    streaming ``reader_ask_turn_runs`` / ``reader_ask_messages`` row
    created during the generator is reconciled to a terminal state when
    the FastAPI generator is closed — cleanly, via cancellation, or via
    ASGI cancellation. Without this, a client disconnect mid-stream
    would leave orphan streaming rows that never transition.

    The reconciliation is idempotent (``WHERE status = 'streaming'``)
    and skipped when the generator already emitted a typed terminal.
    """

    async def event_stream():
        try:
            async for chunk in generator:
                yield chunk
        except HTTPException as exc:
            payload = {
                "code": str(exc.status_code),
                "detail": exc.detail,
            }
            yield (
                "event: error\ndata: "
                f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            )
        except Exception as exc:
            if _is_dev_error_mode():
                payload = {"code": "READER_ASK_FAILED", "detail": str(exc)}
            else:
                payload = {
                    "code": "READER_ASK_FAILED",
                    "detail": "Ask Claread 暂时不可用。",
                    "user_message": "Ask Claread 暂时不可用。",
                }
            yield (
                "event: error\ndata: "
                f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            )
        finally:
            if lifecycle is not None:
                await lifecycle.reconcile_if_streaming()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/threads",
    response_model=ReaderAskThreadListResponse,
    summary="List Reading Record Ask threads",
)
async def list_reading_record_ask_threads(
    reading_record_id: str,
    current_user: AuthUserDep,
) -> ReaderAskThreadListResponse:
    return await rr_ask_svc.list_reading_record_ask_threads(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/model-options",
    response_model=ReaderAskModelOptionListResponse,
    summary="List Reading Record Ask v2 model options",
)
async def list_reading_record_ask_model_options(
    reading_record_id: str,
    current_user: AuthUserDep,
) -> ReaderAskModelOptionListResponse:
    _ = (reading_record_id, current_user)
    return await rr_ask_svc.list_reading_record_ask_model_options()


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/default",
    response_model=ReaderAskThreadSummary,
    summary="Create or get the default Reading Record Ask thread",
)
async def create_default_reading_record_ask_thread(
    reading_record_id: str,
    current_user: AuthUserDep,
) -> ReaderAskThreadSummary:
    return await rr_ask_svc.create_default_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}",
    response_model=ReaderRecordAskThreadDetail,
    response_model_exclude_none=True,
    summary="Get Reading Record Ask thread detail",
)
async def get_reading_record_ask_thread(
    reading_record_id: str,
    thread_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecordAskThreadDetail:
    return await rr_ask_svc.get_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/messages/{message_id}/citations/{citation_id}/navigate",
    response_model=CitationNavigateResponse,
    summary="Securely resolve a public citation_id for article navigation",
)
async def navigate_reading_record_ask_citation(
    reading_record_id: str,
    message_id: UUID,
    citation_id: str,
    current_user: AuthUserDep,
) -> CitationNavigateResponse:
    """Client path submits only message_id + citation_id.

    LiveDocumentFence is loaded from authoritative reading-record /
    stable-document snapshot data. Client bodies cannot supply or override
    base_id / generation / stable_document_id. Response never includes
    handles, internal identity, or raw evidence.
    """
    user_id = UUID(current_user.user_id)
    record_id = UUID(reading_record_id)

    live_fence = await load_live_document_fence(
        user_id=user_id,
        reading_record_id=record_id,
    )
    if live_fence is None:
        return CitationNavigateResponse(
            status="unavailable",
            reason="record_fence_unavailable",
        )

    repo = ReaderRecordAskRepository()
    row = await repo.get_message_restricted_evidence_for_navigation(
        user_id=user_id,
        reading_record_id=record_id,
        message_id=message_id,
    )
    if row is None:
        return CitationNavigateResponse(status="not_found", reason="message_not_found")
    if row.get("final_status") != "ok":
        return CitationNavigateResponse(
            status="unavailable",
            reason="message_not_completed",
        )

    result = resolve_citation_navigation(
        citation_id=citation_id,
        restricted_evidence=row.get("resolved_evidence_json"),
        live_fence=live_fence,
    )
    location = None
    if result.location is not None:
        location = CitationNavigateLocation(
            unit_id=result.location.unit_id,
            anchor_segment_id=result.location.anchor_segment_id,
            canonical_text_start_utf16=result.location.canonical_text_start_utf16,
            canonical_text_end_utf16=result.location.canonical_text_end_utf16,
        )
    return CitationNavigateResponse(
        status=result.status,
        location=location,
        reason=result.reason,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/reset",
    response_model=ReaderRecordAskThreadDetail,
    response_model_exclude_none=True,
    summary="Reset the Reading Record Ask thread",
)
async def reset_reading_record_ask_thread(
    reading_record_id: str,
    thread_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecordAskThreadDetail:
    return await rr_ask_svc.reset_reading_record_ask_thread(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream",
    summary="Stream a message on a Reading Record Ask thread",
)
async def stream_reading_record_ask_thread_message(
    reading_record_id: str,
    thread_id: UUID,
    body: ReaderRecordAskMessageRequest,
    current_user: AuthUserDep,
) -> StreamingResponse:
    user_id = UUID(current_user.user_id)
    prepared = await rr_ask_svc.prepare_reading_record_ask_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        request=body,
        thread_id=thread_id,
    )
    lifecycle = _StreamLifecycleContext()
    return _streaming_response(
        rr_ask_svc.stream_reading_record_ask_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            request=body,
            prepared=prepared,
            lifecycle=lifecycle,
        ),
        lifecycle=lifecycle,
    )


@router.get(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/submissions/{client_submission_id}",
    response_model=ReaderAskSubmissionReconcileResponse,
    summary="Reconcile a client submission after a network blip",
)
async def reconcile_reading_record_ask_submission(
    reading_record_id: str,
    thread_id: UUID,
    client_submission_id: UUID,
    current_user: AuthUserDep,
) -> ReaderAskSubmissionReconcileResponse:
    """ASK-RETRY-CONTRACT-R5 — typed reconcile + safe public message hydrate."""
    from app.schemas.reader_ask import ReaderAskSubmissionPublicMessage
    from app.services.reader_record_ask.submission_gateway import (
        build_reconcile_view,
    )

    user_id = UUID(current_user.user_id)
    repo = ReaderRecordAskRepository()
    try:
        record_uuid = UUID(reading_record_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid reading_record_id") from exc
    thread = await repo.get_thread(
        user_id=user_id,
        thread_id=thread_id,
        reading_record_id=record_uuid,
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")

    async def project_public_message(message_id: UUID) -> dict | None:
        msg = await repo.get_message(message_id=message_id)
        if msg is None:
            return None
        # Strip internal-only fields if present.
        safe = {
            "id": msg.get("id"),
            "thread_id": msg.get("thread_id"),
            "role": msg.get("role"),
            "status": msg.get("status"),
            "content_md": msg.get("content_md") or "",
            "citations": msg.get("citations") or [],
            "agentic_citations": msg.get("agentic_citations"),
            "agentic_answer_blocks": msg.get("agentic_answer_blocks"),
            "agentic_web_search": msg.get("agentic_web_search"),
            "execution_version": msg.get("execution_version"),
            "created_at": msg.get("created_at"),
            "updated_at": msg.get("updated_at"),
        }
        try:
            return ReaderAskSubmissionPublicMessage.model_validate(safe).model_dump(
                mode="json"
            )
        except Exception:
            return {
                "id": str(msg.get("id")),
                "thread_id": str(msg.get("thread_id")),
                "role": msg.get("role") or "assistant",
                "status": msg.get("status") or "completed",
                "content_md": msg.get("content_md") or "",
            }

    view = await build_reconcile_view(
        repo=repo,
        thread_id=thread_id,
        client_submission_id=client_submission_id,
        project_public_message=project_public_message,
    )
    return ReaderAskSubmissionReconcileResponse(
        client_submission_id=view.client_submission_id,
        thread_id=view.thread_id,
        status=view.status,
        user_message_id=view.user_message_id,
        assistant_message_id=view.assistant_message_id,
        terminal_code=view.terminal_code,
        claim_generation=view.claim_generation,
        action_hint=view.action_hint,
        user_message=(
            ReaderAskSubmissionPublicMessage.model_validate(view.user_message)
            if view.user_message
            else None
        ),
        assistant_message=(
            ReaderAskSubmissionPublicMessage.model_validate(view.assistant_message)
            if view.assistant_message
            else None
        ),
    )


@router.post(
    "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/{message_id}/retry/stream",
    summary="Retry a Reading Record Ask assistant message",
)
async def retry_reading_record_ask_message(
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    body: ReaderAskMessageRetryRequest,
    current_user: AuthUserDep,
) -> StreamingResponse:
    # ASK-M1-R1: run the retry preflight BEFORE constructing the
    # StreamingResponse so a config-unavailable option (or unknown
    # model key, or missing record) surfaces as a real HTTP 503 / 422 /
    # 400 instead of an SSE error frame. The generator never re-
    # resolves facts / option / model — it reuses ``prepared``.
    prepared = await rr_ask_svc.prepare_reading_record_ask_retry(
        user_id=UUID(current_user.user_id),
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        message_id=message_id,
    )
    lifecycle = _StreamLifecycleContext()
    return _streaming_response(
        rr_ask_svc.retry_reading_record_ask_message(
            user_id=UUID(current_user.user_id),
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            message_id=message_id,
            request=body,
            prepared=prepared,
            lifecycle=lifecycle,
        ),
        lifecycle=lifecycle,
    )
