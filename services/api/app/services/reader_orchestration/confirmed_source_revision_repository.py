"""R8 — Immutable Confirmed Source revision snapshots (repository layer).

``confirmed_source_documents`` remains the single current row (in-place
optimistic-concurrency UPDATE, revision +1). Every durable write of the
current body additionally persists an **immutable snapshot row** in
``confirmed_source_revisions`` inside the same transaction:

- ``initial`` — the first extraction / candidate-creation body (rev 1),
- ``save``   — each successful content update (rev N),
- ``restore`` — a version-restore write (rev N).

Snapshots are never rewritten: each ``(confirmed_source_document_id,
revision)`` is unique and the row content is fixed at write time. Reads of
snapshot bodies go exclusively through this module (no sideloading).

事务纪律与 ``confirmed_source_repository`` 一致：所有写/读 helper 强制
``conn.is_in_transaction()``，拒绝在事务外执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

import asyncpg

from app.schemas.reader_documents import ConfirmedSourceDocument

ConfirmedSourceRevisionReason = Literal["initial", "save", "restore"]


class ConfirmedSourceRevisionError(ValueError):
    """Raised when a revision-snapshot operation fails closed.

    ``reason_code`` values:
        * ``transaction_required`` — caller forgot to open a transaction.
        * ``row_not_found`` — INSERT RETURNING matched no row.
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ConfirmedSourceRevisionSummary(TypedDict):
    revision: int
    snapshot_reason: str
    edit_source: str
    content_sha256: str
    created_at: datetime


class ConfirmedSourceRevisionFull(TypedDict):
    revision: int
    snapshot_reason: str
    edit_source: str
    markdown_text: str
    content_sha256: str
    created_at: datetime


def _require_transaction(conn: asyncpg.Connection, helper: str) -> None:
    if not conn.is_in_transaction():
        raise ConfirmedSourceRevisionError(
            f"{helper} must be called within an active transaction. "
            "Refusing to execute outside a transaction.",
            reason_code="transaction_required",
        )


async def snapshot_confirmed_source_revision(
    conn: asyncpg.Connection,
    *,
    source: ConfirmedSourceDocument,
    snapshot_reason: ConfirmedSourceRevisionReason,
    now: datetime,
) -> None:
    """Persist one immutable snapshot row for the source's current
    revision. Must run in the same transaction as the current-row write."""
    _require_transaction(conn, "snapshot_confirmed_source_revision")
    row = await conn.fetchrow(
        """
        INSERT INTO confirmed_source_revisions (
            confirmed_source_document_id,
            reading_record_id,
            user_id,
            record_generation,
            revision,
            markdown_text,
            content_sha256,
            snapshot_reason,
            edit_source,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        UUID(source.id),
        UUID(source.reading_record_id),
        UUID(source.user_id),
        source.record_generation,
        source.revision,
        source.markdown_text,
        source.content_sha256,
        snapshot_reason,
        source.edit_source,
        now,
    )
    if row is None:  # pragma: no cover - RETURNING always yields a row
        raise ConfirmedSourceRevisionError(
            "snapshot_confirmed_source_revision returned no row",
            reason_code="row_not_found",
        )


async def list_confirmed_source_revisions(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int,
) -> list[ConfirmedSourceRevisionSummary]:
    """List immutable snapshot metadata (never the body) newest-first."""
    _require_transaction(conn, "list_confirmed_source_revisions")
    rows = await conn.fetch(
        """
        SELECT revision, snapshot_reason, edit_source, content_sha256,
               created_at
        FROM confirmed_source_revisions
        WHERE reading_record_id = $1
          AND user_id = $2
          AND record_generation = $3
        ORDER BY revision DESC
        """,
        record_id,
        user_id,
        generation,
    )
    return [
        {
            "revision": int(row["revision"]),
            "snapshot_reason": str(row["snapshot_reason"]),
            "edit_source": str(row["edit_source"]),
            "content_sha256": str(row["content_sha256"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def load_confirmed_source_revision(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int,
    revision: int,
) -> ConfirmedSourceRevisionFull | None:
    """Load one immutable snapshot (full body). ``None`` when missing."""
    _require_transaction(conn, "load_confirmed_source_revision")
    row = await conn.fetchrow(
        """
        SELECT revision, snapshot_reason, edit_source, markdown_text,
               content_sha256, created_at
        FROM confirmed_source_revisions
        WHERE reading_record_id = $1
          AND user_id = $2
          AND record_generation = $3
          AND revision = $4
        """,
        record_id,
        user_id,
        generation,
        revision,
    )
    if row is None:
        return None
    return {
        "revision": int(row["revision"]),
        "snapshot_reason": str(row["snapshot_reason"]),
        "edit_source": str(row["edit_source"]),
        "markdown_text": str(row["markdown_text"]),
        "content_sha256": str(row["content_sha256"]),
        "created_at": row["created_at"],
    }


def revision_snapshot_reasons() -> tuple[str, str, str]:
    return ("initial", "save", "restore")
