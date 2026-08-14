# task-history: ASK-RETRY-CONTRACT- (renamed from test_ask_retry_contract_r6.py)
"""Ask retry contract submission-gateway unit gates (no real DB / no migration execution)."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.reader_record_ask.submission_gateway import (
    SubmissionTerminalHook,
    build_retry_snapshot,
    ensure_submission_for_send,
)

pytestmark = [
    pytest.mark.chain_reader_ask,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_claim_uses_on_conflict_not_unique_violation_recover() -> None:
    """Fresh claim must use INSERT ... ON CONFLICT DO NOTHING."""
    src = (
        REPO_ROOT
        / "services"
        / "api"
        / "app"
        / "services"
        / "reader_record_ask"
        / "repository.py"
    ).read_text(encoding="utf-8")
    assert "ON CONFLICT (thread_id, client_submission_id)" in src
    assert "DO NOTHING" in src
    # Must not catch UniqueViolation and continue same transaction.
    assert "UniqueViolation" not in src or "never catch" in src.lower()


def test_prepare_runs_ensure_before_stream() -> None:
    from app.services.reader_record_ask import service as svc

    src = inspect.getsource(svc.prepare_reading_record_ask_message)
    assert "ensure_submission_for_send" in src
    stream_src = inspect.getsource(svc._stream_agentic_v2)
    assert "submission.reconcile" in stream_src
    assert "stop_model" in stream_src


@pytest.mark.asyncio
async def test_preflight_missing_table_is_http_503_not_sse() -> None:
    """Missing submission-idempotency table → HTTPException 503 before StreamingResponse."""
    from app.services.reader_record_ask.repository import (
        SubmissionIdempotencyUnavailable,
    )

    repo = MagicMock()
    repo.ensure_submission_message_pair = AsyncMock(
        side_effect=SubmissionIdempotencyUnavailable()
    )

    with pytest.raises(HTTPException) as ei:
        await ensure_submission_for_send(
            repo=repo,
            thread_id=uuid4(),
            user_id=uuid4(),
            client_submission_id=uuid4(),
            content_md="hello",
            retry_snapshot=build_retry_snapshot(
                model_option_key="ask-clarity",
                web_search_mode="disabled",
            ),
        )
    assert ei.value.status_code == 503
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "submission_idempotency_unavailable"


@pytest.mark.asyncio
async def test_no_client_submission_id_returns_none() -> None:
    repo = MagicMock()
    result = await ensure_submission_for_send(
        repo=repo,
        thread_id=uuid4(),
        user_id=uuid4(),
        client_submission_id=None,
        content_md="hello",
        retry_snapshot=build_retry_snapshot(
            model_option_key=None,
            web_search_mode="disabled",
        ),
    )
    assert result is None
    repo.ensure_submission_message_pair.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_stop_model_no_may_create() -> None:
    repo = MagicMock()
    sid = uuid4()
    tid = uuid4()
    umid = str(uuid4())
    amid = str(uuid4())
    repo.ensure_submission_message_pair = AsyncMock(
        return_value={
            "may_create_model": False,
            "status": "completed",
            "claim_generation": 1,
            "user_message_id": umid,
            "assistant_message_id": amid,
            "user_message": {"id": umid},
            "assistant_message": {"id": amid, "content_md": "done"},
            "terminal_code": "submission_completed",
        }
    )
    result = await ensure_submission_for_send(
        repo=repo,
        thread_id=tid,
        user_id=uuid4(),
        client_submission_id=sid,
        content_md="hello",
        retry_snapshot=build_retry_snapshot(
            model_option_key="ask-clarity",
            web_search_mode="disabled",
        ),
    )
    assert result is not None
    assert result.stop_model is True
    assert result.may_create_model is False
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_submission_terminal_hook_completed_failed_cancelled() -> None:
    statuses: list[str] = []

    async def fake_mark(**kwargs: Any) -> None:
        statuses.append(kwargs["status"])

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
        assistant_message_id=uuid4(),
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=fake_mark,
    ):
        assert await hook.mark("completed") is True
        # second call is no-op (exactly-once local after successful sync)
        assert await hook.mark("failed") is True
    assert statuses == ["completed"]

    hook2 = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=2,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=fake_mark,
    ):
        assert await hook2.mark("failed") is True
        assert await hook2.mark("cancelled") is True
    assert statuses == ["completed", "failed"]

    hook3 = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=3,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=fake_mark,
    ):
        # Default: streaming/None must not invent cancelled.
        assert await hook3.mark_from_message_status("streaming") is False
        assert await hook3.mark_from_message_status(
            "streaming", unknown_as_cancelled=True
        ) is True
    assert statuses[-1] == "cancelled"


def test_agentic_finally_syncs_submission() -> None:
    src = (
        REPO_ROOT
        / "services"
        / "api"
        / "app"
        / "services"
        / "reader_record_ask"
        / "production_stream.py"
    ).read_text(encoding="utf-8")
    assert "SubmissionTerminalHook" in src
    assert "_sync_submission_terminal" in src
    assert "ensure_synced" in src


def test_route_prepare_before_streaming_response() -> None:
    src = (
        REPO_ROOT
        / "services"
        / "api"
        / "app"
        / "api"
        / "routes"
        / "reader_record_ask.py"
    ).read_text(encoding="utf-8")
    # prepare must appear before StreamingResponse construction in send/stream
    assert "prepare_reading_record_ask_message" in src
    stream_idx = src.index("async def stream_reading_record_ask_thread_message")
    chunk = src[stream_idx : stream_idx + 800]
    assert "prepare_reading_record_ask_message" in chunk
    assert chunk.index("prepare_reading_record_ask_message") < chunk.index(
        "_streaming_response"
    )


def test_snapshot_fail_closed_no_regression() -> None:
    from app.services.reader_record_ask.service import _extract_snapshot_model_option_key

    key = _extract_snapshot_model_option_key(
        assistant_msg={
            "metadata_json": {
                "retry_snapshot": {"model_option_key": "ask-clarity"}
            }
        },
        user_msg={"metadata_json": {}},
    )
    assert key == "ask-clarity"


def test_send_prepared_result_dataclass() -> None:
    from app.services.reader_record_ask.service import SendPreparedResult

    assert "submission" in SendPreparedResult.__dataclass_fields__
    assert "execution" in SendPreparedResult.__dataclass_fields__
