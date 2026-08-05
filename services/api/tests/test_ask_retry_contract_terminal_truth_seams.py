# task-history: ASK-RETRY-CONTRACT-R8 (renamed from test_ask_retry_contract_r8.py)
"""Ask retry contract executable terminal-truth seams (no source-text)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.reader_record_ask.production_stream import (
    _submission_status_from_terminal_chunk,
    apply_agentic_submission_terminal,
    merge_known_submission_status,
    resolve_agentic_submission_write_status,
)
from app.services.reader_record_ask.submission_gateway import SubmissionTerminalHook

pytestmark = [
    pytest.mark.chain_reader_ask,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

# ---------------------------------------------------------------------------
# R8.1 monotonic merge
# ---------------------------------------------------------------------------


def test_merge_failed_then_cancelled_stays_failed() -> None:
    assert merge_known_submission_status("failed", "cancelled") == "failed"


def test_merge_cancelled_then_failed_promotes_failed() -> None:
    assert merge_known_submission_status("cancelled", "failed") == "failed"


def test_merge_failed_then_completed_promotes_completed() -> None:
    assert merge_known_submission_status("failed", "completed") == "completed"


def test_merge_completed_then_cancelled_stays_completed() -> None:
    assert merge_known_submission_status("completed", "cancelled") == "completed"


def test_merge_completed_then_failed_stays_completed() -> None:
    assert merge_known_submission_status("completed", "failed") == "completed"


def test_merge_none_with_incoming() -> None:
    assert merge_known_submission_status(None, "cancelled") == "cancelled"
    assert merge_known_submission_status("failed", None) == "failed"
    assert merge_known_submission_status(None, None) is None


# ---------------------------------------------------------------------------
# resolve write status (pure)
# ---------------------------------------------------------------------------


def test_resolve_known_wins_over_message() -> None:
    assert (
        resolve_agentic_submission_write_status(
            known="completed",
            message_status="failed",
            message_lookup_ok=True,
        )
        == "completed"
    )


def test_resolve_unknown_message_none_zero_write() -> None:
    assert (
        resolve_agentic_submission_write_status(
            known=None,
            message_status=None,
            message_lookup_ok=True,
        )
        is None
    )


def test_resolve_lookup_error_zero_write() -> None:
    assert (
        resolve_agentic_submission_write_status(
            known=None,
            message_status=None,
            message_lookup_ok=False,
        )
        is None
    )


# ---------------------------------------------------------------------------
# apply_agentic_submission_terminal seam (marks real statuses)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_known_completed_get_message_none() -> None:
    statuses: list[str] = []

    async def capture(**kwargs: Any) -> None:
        statuses.append(kwargs["status"])

    async def load_none() -> str | None:
        return None

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
        assistant_message_id=uuid4(),
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=capture,
    ):
        written = await apply_agentic_submission_terminal(
            hook=hook,
            known="completed",
            load_message_status=load_none,
        )
    assert written == "completed"
    assert statuses == ["completed"]


@pytest.mark.asyncio
async def test_seam_known_completed_get_message_raises() -> None:
    statuses: list[str] = []

    async def capture(**kwargs: Any) -> None:
        statuses.append(kwargs["status"])

    async def load_boom() -> str | None:
        raise RuntimeError("db blip")

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
        assistant_message_id=uuid4(),
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=capture,
    ):
        # known is set → load_message_status should not be required; even if
        # called, raise must not demote completed.
        written = await apply_agentic_submission_terminal(
            hook=hook,
            known="completed",
            load_message_status=load_boom,
        )
    assert written == "completed"
    assert statuses == ["completed"]


@pytest.mark.asyncio
async def test_seam_unknown_get_message_none_zero_writes() -> None:
    called = {"n": 0}

    async def capture(**_kwargs: Any) -> None:
        called["n"] += 1

    async def load_none() -> str | None:
        return None

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=capture,
    ):
        written = await apply_agentic_submission_terminal(
            hook=hook,
            known=None,
            load_message_status=load_none,
        )
    assert written is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_seam_known_failed_late_cancelled_keeps_failed() -> None:
    statuses: list[str] = []

    async def capture(**kwargs: Any) -> None:
        statuses.append(kwargs["status"])

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=2,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=capture,
    ):
        known = merge_known_submission_status(None, "failed")
        known = merge_known_submission_status(known, "cancelled")
        assert known == "failed"
        written = await apply_agentic_submission_terminal(
            hook=hook,
            known=known,
            load_message_status=None,
        )
        # Late cancelled after successful sync must not rewrite.
        late = merge_known_submission_status(known, "cancelled")
        assert late == "failed"
        written2 = await apply_agentic_submission_terminal(
            hook=hook,
            known=late,
            load_message_status=None,
        )
    assert written == "failed"
    assert written2 == "failed"
    assert statuses == ["failed"]  # second mark is no-op after synced


@pytest.mark.asyncio
async def test_transient_write_retry_keeps_completed() -> None:
    attempts: list[str] = []

    async def flaky(**kwargs: Any) -> None:
        attempts.append(kwargs["status"])
        if len(attempts) == 1:
            raise RuntimeError("blip")

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=3,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=flaky,
    ):
        written = await apply_agentic_submission_terminal(
            hook=hook,
            known="completed",
        )
    assert written == "completed"
    assert attempts == ["completed", "completed"]


def test_terminal_chunk_helpers_still_work() -> None:
    assert (
        _submission_status_from_terminal_chunk(
            'event: message.completed\ndata: {"final_status":"ok"}\n\n'
        )
        == "completed"
    )
    assert (
        _submission_status_from_terminal_chunk(
            'event: agentic.terminal\ndata: {"final_status":"cancelled"}\n\n'
        )
        == "cancelled"
    )
