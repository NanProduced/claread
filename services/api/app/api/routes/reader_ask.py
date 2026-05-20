from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config.settings import get_settings
from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionConfirmResponse,
    ReaderAskMessageStreamRequest,
    ReaderAskThreadCreateRequest,
    ReaderAskThreadDetail,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_ask import service as ask_svc

router = APIRouter(prefix="/reader-ask", tags=["reader-ask"])


def _is_dev_error_mode() -> bool:
    return get_settings().app_env != "production"


@router.get("/threads", response_model=ReaderAskThreadListResponse, summary="当前文章 Ask 线程列表")
async def list_reader_ask_threads(
    current_user: AuthUserDep,
    record_id: str = Query(..., min_length=1),
) -> ReaderAskThreadListResponse:
    return await ask_svc.list_threads(UUID(current_user.user_id), record_id)


@router.post("/threads", response_model=ReaderAskThreadSummary, summary="创建或获取 Ask 线程")
async def create_reader_ask_thread(
    current_user: AuthUserDep,
    body: ReaderAskThreadCreateRequest,
) -> ReaderAskThreadSummary:
    return await ask_svc.create_thread(UUID(current_user.user_id), body)


@router.get("/threads/{thread_id}", response_model=ReaderAskThreadDetail, summary="Ask 线程详情")
async def get_reader_ask_thread(
    current_user: AuthUserDep,
    thread_id: UUID,
) -> ReaderAskThreadDetail:
    return await ask_svc.get_thread_detail(UUID(current_user.user_id), thread_id)


@router.post("/threads/{thread_id}/reset", response_model=ReaderAskThreadDetail, summary="重置当前 Ask 会话")
async def reset_reader_ask_thread(
    current_user: AuthUserDep,
    thread_id: UUID,
) -> ReaderAskThreadDetail:
    return await ask_svc.reset_thread(UUID(current_user.user_id), thread_id)


@router.post("/threads/{thread_id}/messages/stream", summary="Ask Claread 流式回复")
async def stream_reader_ask_message(
    current_user: AuthUserDep,
    thread_id: UUID,
    body: ReaderAskMessageStreamRequest,
) -> StreamingResponse:
    async def event_stream():
        try:
            async for chunk in ask_svc.stream_thread_message(UUID(current_user.user_id), thread_id, body):
                yield chunk
        except HTTPException as exc:
            yield (
                f"event: error\ndata: {json.dumps({'code': str(exc.status_code), 'detail': exc.detail}, ensure_ascii=False)}\n\n"
            )
        except Exception as exc:
            detail = str(exc) if _is_dev_error_mode() else "Ask Claread is temporarily unavailable."
            yield (
                "event: error\ndata: "
                f"{json.dumps({'code': 'READER_ASK_FAILED', 'detail': detail}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/threads/{thread_id}/messages/{message_id}/retry/stream", summary="重新生成当前 Ask 回复")
async def retry_reader_ask_message(
    current_user: AuthUserDep,
    thread_id: UUID,
    message_id: UUID,
) -> StreamingResponse:
    async def event_stream():
        try:
            async for chunk in ask_svc.retry_thread_message(UUID(current_user.user_id), thread_id, message_id):
                yield chunk
        except HTTPException as exc:
            yield (
                f"event: error\ndata: {json.dumps({'code': str(exc.status_code), 'detail': exc.detail}, ensure_ascii=False)}\n\n"
            )
        except Exception as exc:
            detail = str(exc) if _is_dev_error_mode() else "Ask Claread is temporarily unavailable."
            yield (
                "event: error\ndata: "
                f"{json.dumps({'code': 'READER_ASK_FAILED', 'detail': detail}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/threads/{thread_id}/actions/{action_id}/confirm",
    response_model=ReaderAskActionConfirmResponse,
    summary="确认执行 Ask 动作",
)
async def confirm_reader_ask_action(
    current_user: AuthUserDep,
    thread_id: UUID,
    action_id: str,
    body: ReaderAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    return await ask_svc.confirm_action(UUID(current_user.user_id), thread_id, action_id, body)


@router.delete("/supplements/{supplement_id}", summary="删除 Ask Claread 补充")
async def delete_reader_ask_supplement(
    current_user: AuthUserDep,
    supplement_id: UUID,
) -> dict[str, object]:
    return await ask_svc.delete_supplement(UUID(current_user.user_id), supplement_id)
