"""R8 Commit 2 — confirmed source revision snapshot repository (unit tests).

Pure-unit coverage with a recording fake connection (no DB): transaction
discipline fail-closed, SQL shapes for snapshot / list / load, and the
immutable-row mapping contract. Real-DB behavior is covered by
``test_confirmed_source_revision_db.py`` (project isolated-schema pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.schemas.reader_documents import ConfirmedSourceDocument
from app.services.reader_orchestration.confirmed_source_revision_repository import (
    ConfirmedSourceRevisionError,
    list_confirmed_source_revisions,
    load_confirmed_source_revision,
    snapshot_confirmed_source_revision,
)

NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


class RecordingConnection:
    """Minimal asyncpg-like connection recording calls.

    ``is_in_transaction`` flips per test; ``fetchrow`` returns the canned
    row; ``fetch`` returns the canned list.
    """

    def __init__(
        self,
        *,
        in_transaction: bool = True,
        fetchrow_row: dict[str, Any] | None = None,
        fetch_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._in_transaction = in_transaction
        self._fetchrow_row = fetchrow_row
        self._fetch_rows = fetch_rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    async def fetchrow(self, query: str, *args: Any):  # noqa: ANN001
        self.calls.append(("fetchrow", (query, args)))
        if self._fetchrow_row is None:
            return None
        return _Record(self._fetchrow_row)

    async def fetch(self, query: str, *args: Any):  # noqa: ANN001
        self.calls.append(("fetch", (query, args)))
        return [_Record(row) for row in (self._fetch_rows or [])]


class _Record:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


def _source(revision: int = 3) -> ConfirmedSourceDocument:
    return ConfirmedSourceDocument(
        id=str(uuid4()),
        reading_record_id=str(uuid4()),
        user_id=str(uuid4()),
        record_generation=1,
        original_input_id=None,
        markdown_text="## Body\n\ncurrent text",
        revision=revision,
        content_sha256="a" * 64,
        status="draft",
        edit_source="content_check",
    )


async def test_snapshot_refuses_outside_transaction() -> None:
    conn = RecordingConnection(in_transaction=False)
    with pytest.raises(ConfirmedSourceRevisionError) as exc_info:
        await snapshot_confirmed_source_revision(
            conn,
            source=_source(),
            snapshot_reason="save",
            now=NOW,
        )
    assert exc_info.value.reason_code == "transaction_required"


async def test_snapshot_inserts_immutable_row() -> None:
    conn = RecordingConnection(fetchrow_row={})
    source = _source(revision=3)
    await snapshot_confirmed_source_revision(
        conn,
        source=source,
        snapshot_reason="restore",
        now=NOW,
    )
    (kind, (query, args)) = conn.calls[0]
    assert kind == "fetchrow"
    assert "INSERT INTO confirmed_source_revisions" in query
    assert "revision" in query
    text = query.lower()
    assert "markdown_text" in text
    assert str(source.id) in {str(a) for a in args}
    assert "restore" in {str(a) for a in args}


async def test_list_requires_transaction_fail_closed() -> None:
    conn = RecordingConnection(in_transaction=False)
    with pytest.raises(ConfirmedSourceRevisionError) as exc_info:
        await list_confirmed_source_revisions(
            conn,
            record_id=uuid4(),
            user_id=uuid4(),
            generation=1,
        )
    assert exc_info.value.reason_code == "transaction_required"


async def test_list_maps_metadata_without_markdown_text() -> None:
    conn = RecordingConnection(
        fetch_rows=[
            {
                "revision": 1,
                "snapshot_reason": "initial",
                "edit_source": "initial",
                "content_sha256": "b" * 64,
                "created_at": NOW,
            },
            {
                "revision": 2,
                "snapshot_reason": "save",
                "edit_source": "wysiwyg",
                "content_sha256": "c" * 64,
                "created_at": NOW,
            },
        ]
    )
    rows = await list_confirmed_source_revisions(
        conn,
        record_id=uuid4(),
        user_id=uuid4(),
        generation=1,
    )
    (kind, (query, _args)) = conn.calls[0]
    assert kind == "fetch"
    assert "FROM confirmed_source_revisions" in query
    assert "markdown_text" not in query.lower().split("select")[1].split("from")[0]
    assert [row["revision"] for row in rows] == [1, 2]
    assert rows[0]["snapshot_reason"] == "initial"
    assert "markdown_text" not in rows[0]


async def test_load_full_revision_maps_body() -> None:
    conn = RecordingConnection(
        fetchrow_row={
            "revision": 1,
            "snapshot_reason": "initial",
            "edit_source": "initial",
            "markdown_text": "## Original",
            "content_sha256": "b" * 64,
            "created_at": NOW,
        }
    )
    row = await load_confirmed_source_revision(
        conn,
        record_id=uuid4(),
        user_id=uuid4(),
        generation=1,
        revision=1,
    )
    assert row is not None
    assert row["markdown_text"] == "## Original"
    assert row["revision"] == 1
    (kind, (query, _args)) = conn.calls[0]
    assert kind == "fetchrow"
    assert "AND revision = $" in query


async def test_load_missing_revision_returns_none() -> None:
    conn = RecordingConnection(fetchrow_row=None)
    row = await load_confirmed_source_revision(
        conn,
        record_id=uuid4(),
        user_id=uuid4(),
        generation=1,
        revision=99,
    )
    assert row is None
