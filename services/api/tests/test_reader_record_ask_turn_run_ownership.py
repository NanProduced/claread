from __future__ import annotations

import inspect
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_record_ask.repository import ReaderRecordAskRepository

_TURN_RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
_MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000201")


class _AsyncContext(AbstractAsyncContextManager[Any]):
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _OwnershipRaceConnection:
    def __init__(self) -> None:
        self.superseded = False
        self.queries: list[str] = []

    def transaction(self) -> _AsyncContext:
        return _AsyncContext(self)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        if normalized.startswith("SELECT status, current_turn_run_id"):
            # A newer retry owns reader_ask_messages.current_turn_run_id.
            return {
                "status": "streaming",
                "current_turn_run_id": UUID(
                    "00000000-0000-0000-0000-000000000999"
                ),
            }
        if normalized.startswith("SELECT final_status"):
            return {
                "final_status": None,
                "terminal_reason": None,
                "user_visible_output_json": None,
                "envelope_fingerprint": "fp",
                "execution_version": "reader_record_ask_agentic_v2",
            }
        if "terminal_reason = 'superseded_by_newer_turn'" in normalized:
            self.superseded = True
            return {
                "final_status": "cancelled",
                "terminal_reason": "superseded_by_newer_turn",
                "user_visible_output_json": None,
                "envelope_fingerprint": "fp",
                "execution_version": "reader_record_ask_agentic_v2",
            }
        raise AssertionError(f"unexpected query: {normalized}")

    async def execute(self, query: str, *args: Any) -> str:
        raise AssertionError(f"message row must not be updated: {query}")


class _Pool:
    def __init__(self, connection: _OwnershipRaceConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_complete_loses_to_newer_message_owner_without_overwriting_message() -> None:
    connection = _OwnershipRaceConnection()
    repo = ReaderRecordAskRepository(pool=_Pool(connection))

    result = await repo.complete_agentic_turn_run(
        turn_run_id=_TURN_RUN_ID,
        message_id=_MESSAGE_ID,
        answer_text="stale answer",
        completed_dto={"answer_text": "stale answer"},
        resolved_evidence=[],
    )

    assert connection.superseded is True
    assert result["status"] == "already_terminal"
    assert result["winning_final_status"] == "cancelled"
    assert result["winning_terminal_reason"] == "superseded_by_newer_turn"


def test_turn_run_ownership_is_fenced_at_every_message_write() -> None:
    create_source = inspect.getsource(
        ReaderRecordAskRepository.create_agentic_turn_run
    )
    complete_source = inspect.getsource(
        ReaderRecordAskRepository.complete_agentic_turn_run
    )
    terminal_source = inspect.getsource(
        ReaderRecordAskRepository.terminal_agentic_turn_run
    )
    reset_source = inspect.getsource(
        ReaderRecordAskRepository.reset_assistant_message_for_retry
    )

    assert "SET current_turn_run_id = $2" in create_source
    assert "SELECT status, current_turn_run_id" in complete_source
    assert "FOR UPDATE" in complete_source
    assert 'owner["current_turn_run_id"] == turn_run_id' in complete_source
    assert "current_turn_run_id = $3" in complete_source
    assert "current_turn_run_id = $2" in terminal_source
    assert "current_turn_run_id = NULL" in reset_source
