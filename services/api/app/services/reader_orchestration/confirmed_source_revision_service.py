"""R8 — Confirmed Source immutable revision history (application layer).

Owner-scoped list / get / restore over ``confirmed_source_revisions``:

- ``list_revisions`` / ``get_revision`` — 404 collapse (not found / not
  owner / deleted record).
- ``restore_revision`` — loads an immutable snapshot and writes its body
  as the NEW current revision (monotonic +1, in-place current row,
  ``snapshot_reason='restore'``); never rewrites history. Optimistic
  concurrency: ``expected_revision`` mismatch → 409 ``stale_source_revision``;
  frozen source → 409 ``source_frozen``. A failed restore never corrupts
  the current body (single transaction, no row change on conflict).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from app.database.connection import DB_POOL
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceConflictError,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    ConfirmedSourceError,
    lock_confirmed_source_for_update,
    update_confirmed_source_with_expected_revision,
)
from app.services.reader_orchestration.confirmed_source_revision_repository import (
    ConfirmedSourceRevisionFull,
    ConfirmedSourceRevisionSummary,
    list_confirmed_source_revisions,
    load_confirmed_source_revision,
)


class ConfirmedSourceRevisionNotFoundError(ValueError):
    """404 collapse: not found / not owner / deleted / no draft source."""


@dataclass(frozen=True, slots=True)
class ConfirmedSourceRevisionListResult:
    revisions: list[ConfirmedSourceRevisionSummary]


@dataclass(frozen=True, slots=True)
class ConfirmedSourceRevisionGetResult:
    revision: ConfirmedSourceRevisionFull


@dataclass(frozen=True, slots=True)
class ConfirmedSourceRestoreResult:
    revision: int
    content_sha256: str
    markdown_text: str
    restored_to: int


class ConfirmedSourceRevisionService:
    """Owner-scoped revision history API."""

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        if DB_POOL is None:
            raise RuntimeError("database pool is not initialized")
        return DB_POOL

    async def _load_record_generation_or_collapse(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> int:
        row = await conn.fetchrow(
            """
            SELECT generation
            FROM reading_records
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
        if row is None:
            raise ConfirmedSourceRevisionNotFoundError("record not found")
        return int(row["generation"])

    async def list_revisions(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> ConfirmedSourceRevisionListResult:
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                generation = await self._load_record_generation_or_collapse(
                    conn, record_id=record_id, user_id=user_id
                )
                revisions = await list_confirmed_source_revisions(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                )
        return ConfirmedSourceRevisionListResult(revisions=revisions)

    async def get_revision(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        revision: int,
    ) -> ConfirmedSourceRevisionGetResult:
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                generation = await self._load_record_generation_or_collapse(
                    conn, record_id=record_id, user_id=user_id
                )
                snapshot = await load_confirmed_source_revision(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                    revision=revision,
                )
        if snapshot is None:
            raise ConfirmedSourceRevisionNotFoundError("revision not found")
        return ConfirmedSourceRevisionGetResult(revision=snapshot)

    async def restore_revision(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_revision: int,
        target_revision: int,
    ) -> ConfirmedSourceRestoreResult:
        pool = self._get_pool()
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                generation = await self._load_record_generation_or_collapse(
                    conn, record_id=record_id, user_id=user_id
                )
                # Lock order: record (locked above via SELECT FOR UPDATE is
                # not needed because revision writes are guarded by the
                # source-row lock + optimistic revision check).
                source = await lock_confirmed_source_for_update(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                )
                if source is None:
                    raise ConfirmedSourceRevisionNotFoundError("no draft confirmed source")
                if source.status != "draft":
                    raise ConfirmedSourceConflictError(
                        "confirmed source is frozen and cannot be restored",
                        code="source_frozen",
                        resolution="open_reader",
                        current_revision=source.revision,
                    )
                if source.revision != expected_revision:
                    raise ConfirmedSourceConflictError(
                        f"expected revision {expected_revision} but current "
                        f"revision is {source.revision}",
                        code="stale_source_revision",
                        resolution="reload",
                        current_revision=source.revision,
                    )
                snapshot = await load_confirmed_source_revision(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                    revision=target_revision,
                )
                if snapshot is None:
                    raise ConfirmedSourceRevisionNotFoundError(
                        f"revision {target_revision} not found"
                    )
                try:
                    updated = await update_confirmed_source_with_expected_revision(
                        conn,
                        source_document_id=UUID(source.id),
                        record_id=record_id,
                        expected_revision=source.revision,
                        markdown_text=snapshot["markdown_text"],
                        edit_source=source.edit_source,
                        now=now,
                        snapshot_reason="restore",
                    )
                except ConfirmedSourceError as exc:
                    raise ConfirmedSourceRevisionNotFoundError(f"restore failed: {exc}") from exc
        return ConfirmedSourceRestoreResult(
            revision=updated.revision,
            content_sha256=updated.content_sha256,
            markdown_text=updated.markdown_text,
            restored_to=target_revision,
        )
