"""L2 — Confirmed Source repository helpers（migration 0025）。

事务纪律（与 ``lock_record_for_candidate_write`` /
``persist_stable_document_freeze_plan`` 的 fail-closed 先例一致）：
所有写/锁 helper 强制 ``conn.is_in_transaction()``，拒绝在事务外执行。

锁顺序约定（设计文档 §3.4 R4）：record → source → candidate。
``lock_confirmed_source_for_update`` 只在调用方已持有 record 行
FOR UPDATE 锁之后调用。

不包含任何正文读取的旁路：读正文只能通过
``lock_confirmed_source_for_update`` / ``load_confirmed_source``。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.schemas.reader_documents import (
    ConfirmedSourceDocument,
    ConfirmedSourceEditSource,
)


class ConfirmedSourceError(ValueError):
    """Raised when a confirmed-source repository operation fails closed.

    ``reason_code`` values:
        * ``transaction_required`` — caller forgot to open a transaction.
        * ``row_not_found`` — UPDATE/lock matched no row.
        * ``stale_revision`` — optimistic-concurrency revision mismatch
          (or the row is frozen and thus immutable).
        * ``freeze_conflict`` — freeze UPDATE did not match exactly one
          draft row.
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _require_transaction(conn: asyncpg.Connection, helper: str) -> None:
    if not conn.is_in_transaction():
        raise ConfirmedSourceError(
            f"{helper} must be called within an active transaction. "
            "Refusing to execute outside a transaction.",
            reason_code="transaction_required",
        )


def confirmed_source_content_sha256(markdown_text: str) -> str:
    """SHA-256 of the normalized markdown body (DB CHECK 自校验同式）。"""
    return hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()


def _row_to_model(row: asyncpg.Record) -> ConfirmedSourceDocument:
    return ConfirmedSourceDocument(
        id=str(row["id"]),
        reading_record_id=str(row["reading_record_id"]),
        user_id=str(row["user_id"]),
        record_generation=int(row["record_generation"]),
        original_input_id=(
            str(row["original_input_id"])
            if row["original_input_id"] is not None
            else None
        ),
        markdown_text=str(row["markdown_text"]),
        revision=int(row["revision"]),
        content_sha256=str(row["content_sha256"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        edit_source=str(row["edit_source"]),  # type: ignore[arg-type]
    )


async def insert_confirmed_source(
    conn: asyncpg.Connection,
    *,
    source_document_id: UUID,
    record_id: UUID,
    user_id: UUID,
    generation: int,
    original_input_id: UUID | None,
    markdown_text: str,
    edit_source: ConfirmedSourceEditSource,
    now: datetime,
) -> ConfirmedSourceDocument:
    """Insert the single confirmed-source row for (record, generation).

    Always ``revision=1`` / ``status='draft'``; callers freeze via
    :func:`freeze_confirmed_source` in the same transaction when the
    stable-ready / direct-freeze path applies.
    """
    _require_transaction(conn, "insert_confirmed_source")
    row = await conn.fetchrow(
        """
        INSERT INTO confirmed_source_documents (
            id,
            reading_record_id,
            user_id,
            record_generation,
            original_input_id,
            markdown_text,
            revision,
            content_sha256,
            status,
            edit_source,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, 1, $7, 'draft', $8, $9, $9)
        RETURNING id, reading_record_id, user_id, record_generation,
                  original_input_id, markdown_text, revision,
                  content_sha256, status, edit_source
        """,
        source_document_id,
        record_id,
        user_id,
        generation,
        original_input_id,
        markdown_text,
        confirmed_source_content_sha256(markdown_text),
        edit_source,
        now,
    )
    if row is None:  # pragma: no cover - RETURNING always yields a row
        raise ConfirmedSourceError(
            "insert_confirmed_source returned no row",
            reason_code="row_not_found",
        )
    return _row_to_model(row)


async def lock_confirmed_source_for_update(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int,
) -> ConfirmedSourceDocument | None:
    """SELECT ... FOR UPDATE the confirmed-source row for (record, generation).

    Returns ``None`` when no row exists (legacy record) — callers branch
    to legacy behavior on ``None`` (设计文档 §7 R2：以 source 行存在性
    做单一判别）。
    """
    _require_transaction(conn, "lock_confirmed_source_for_update")
    row = await conn.fetchrow(
        """
        SELECT id, reading_record_id, user_id, record_generation,
               original_input_id, markdown_text, revision,
               content_sha256, status, edit_source
        FROM confirmed_source_documents
        WHERE reading_record_id = $1
          AND user_id = $2
          AND record_generation = $3
        FOR UPDATE
        """,
        record_id,
        user_id,
        generation,
    )
    if row is None:
        return None
    return _row_to_model(row)


async def load_confirmed_source(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int,
) -> ConfirmedSourceDocument | None:
    """Read (no lock) the confirmed-source row for GET endpoints."""
    _require_transaction(conn, "load_confirmed_source")
    row = await conn.fetchrow(
        """
        SELECT id, reading_record_id, user_id, record_generation,
               original_input_id, markdown_text, revision,
               content_sha256, status, edit_source
        FROM confirmed_source_documents
        WHERE reading_record_id = $1
          AND user_id = $2
          AND record_generation = $3
        """,
        record_id,
        user_id,
        generation,
    )
    if row is None:
        return None
    return _row_to_model(row)


async def update_confirmed_source_with_expected_revision(
    conn: asyncpg.Connection,
    *,
    source_document_id: UUID,
    record_id: UUID,
    expected_revision: int,
    markdown_text: str,
    edit_source: ConfirmedSourceEditSource,
    now: datetime,
) -> ConfirmedSourceDocument:
    """Optimistic-concurrency body update: revision 原地推进 +1。

    ``UPDATE ... WHERE id AND reading_record_id AND revision =
    expected_revision AND status = 'draft'``；未命中恰好一行即
    fail closed（stale_revision / frozen 不可变）。
    """
    _require_transaction(
        conn, "update_confirmed_source_with_expected_revision"
    )
    row = await conn.fetchrow(
        """
        UPDATE confirmed_source_documents
        SET markdown_text = $4,
            revision = revision + 1,
            content_sha256 = $5,
            edit_source = $6,
            updated_at = $7
        WHERE id = $1
          AND reading_record_id = $2
          AND revision = $3
          AND status = 'draft'
        RETURNING id, reading_record_id, user_id, record_generation,
                  original_input_id, markdown_text, revision,
                  content_sha256, status, edit_source
        """,
        source_document_id,
        record_id,
        expected_revision,
        markdown_text,
        confirmed_source_content_sha256(markdown_text),
        edit_source,
        now,
    )
    if row is None:
        raise ConfirmedSourceError(
            f"confirmed_source_documents row {source_document_id} not "
            f"updated: revision != {expected_revision} or status != "
            "'draft' (stale revision or frozen source)",
            reason_code="stale_revision",
        )
    return _row_to_model(row)


async def freeze_confirmed_source(
    conn: asyncpg.Connection,
    *,
    source_document_id: UUID,
    now: datetime,
) -> None:
    """Freeze a draft source row in the same transaction as the stable
    freeze（插入点 B；模式与 freeze persistence 的 ``UPDATE 1`` 期望相同）。

    Raises:
        ConfirmedSourceError: ``freeze_conflict`` when the UPDATE did
            not match exactly one ``status='draft'`` row.
    """
    _require_transaction(conn, "freeze_confirmed_source")
    result = await conn.execute(
        """
        UPDATE confirmed_source_documents
        SET status = 'frozen',
            frozen_at = $2,
            updated_at = $2
        WHERE id = $1
          AND status = 'draft'
        """,
        source_document_id,
        now,
    )
    if result != "UPDATE 1":
        raise ConfirmedSourceError(
            f"freeze_confirmed_source expected UPDATE 1 for "
            f"{source_document_id} (status='draft'), got {result!r}",
            reason_code="freeze_conflict",
        )


def candidate_confirmed_source_refs(
    source: ConfirmedSourceDocument,
) -> dict[str, Any]:
    """Candidate ``source_refs_json`` 三 key（设计文档 §6：JSONB 不加列）。"""
    return {
        "confirmed_source_document_id": source.id,
        "source_revision": source.revision,
        "source_content_sha256": source.content_sha256,
    }
