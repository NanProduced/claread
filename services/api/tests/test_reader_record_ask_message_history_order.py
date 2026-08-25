from __future__ import annotations

from uuid import uuid4

import pytest


class _RecordingConnection:
    def __init__(self) -> None:
        self.query = ""

    async def fetch(self, query: str, *_args: object) -> list[dict[str, str]]:
        self.query = query
        assert "reader_ask_client_submissions" in query
        assert "submission.user_message_id = m.id THEN 0" in query
        assert "submission.assistant_message_id = m.id THEN 1" in query
        return [{"role": "user"}, {"role": "assistant"}]


class _AcquireConnection:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _RecordingConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _RecordingPool:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AcquireConnection:
        return _AcquireConnection(self.connection)


@pytest.mark.asyncio
async def test_history_orders_submission_pair_user_before_assistant(monkeypatch) -> None:
    """Cold history must preserve the explicit user -> assistant turn binding."""
    from app.services.reader_record_ask import repository

    connection = _RecordingConnection()
    repo = repository.ReaderRecordAskRepository(pool=_RecordingPool(connection))
    monkeypatch.setattr(repository, "_message_row_to_history", lambda row: row)

    messages = await repo.list_messages(thread_id=uuid4(), limit=None)

    assert [message["role"] for message in messages] == ["user", "assistant"]
