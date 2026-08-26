"""冻结图片 source URL override service/repository。

dedicated persistence（``stable_image_source_overrides``，B1 七列表）+
PUT/DELETE 写路径。复用 reader_notes 的 mutation+event 模板：
写前一条 ownership/active/target SELECT → 双 partial-index conflict
upsert / exact locator 硬删 → ``build_representation_payload`` →
``publish_event_in_transaction``（数据变更与事件同一事务）。

存储输入边界：raw override 逐字保存，唯一写入层拒绝 U+0000
（由 ``app.schemas.reader_image_overrides`` 在到达数据库前确定性 422）；
loadability 只在 snapshot 投影派生，绝不回写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection

from .reader_orchestration.event_runtime import ReaderEventRuntime
from .reader_orchestration.representation_event_payload import (
    build_representation_payload,
)

CODE_NOT_FOUND = "not_found"
CODE_STABLE_DOCUMENT_NOT_ACTIVE = "stable_document_not_active"
CODE_IMAGE_BLOCK_NOT_FOUND = "image_block_not_found"
CODE_IMAGE_TARGET_NOT_FOUND = "image_target_not_found"
CODE_URL_NULL_CHARACTER_NOT_PERSISTABLE = "url_null_character_not_persistable"
CODE_URL_NOT_REPRESENTABLE = "url_not_representable_as_postgres_text"
CODE_BLOCK_ID_NOT_REPRESENTABLE = "block_id_not_representable_as_postgres_text"


class ImageSourceOverrideError(Exception):
    def __init__(self, *, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class _OverrideWriteTarget:
    generation: int
    active_base_id: UUID
    block_type: str
    payload: dict[str, Any]


# 两条显式 partial-index conflict SQL，service 按
# ``inline_ordinal is None`` 二选一；不存在能同时命中两个 partial
# unique index 的无 predicate 通用 ON CONFLICT。
_UPSERT_STANDALONE_SQL = """
    INSERT INTO stable_image_source_overrides
        (id, stable_document_id, block_id, inline_ordinal, override_url)
    VALUES ($1, $2, $3, NULL, $4)
    ON CONFLICT (stable_document_id, block_id) WHERE inline_ordinal IS NULL
    DO UPDATE SET override_url = EXCLUDED.override_url, updated_at = now()
"""

_UPSERT_INLINE_SQL = """
    INSERT INTO stable_image_source_overrides
        (id, stable_document_id, block_id, inline_ordinal, override_url)
    VALUES ($1, $2, $3, $5, $4)
    ON CONFLICT (stable_document_id, block_id, inline_ordinal)
        WHERE inline_ordinal IS NOT NULL
    DO UPDATE SET override_url = EXCLUDED.override_url, updated_at = now()
"""

_DELETE_STANDALONE_SQL = """
    DELETE FROM stable_image_source_overrides
    WHERE stable_document_id = $1 AND block_id = $2 AND inline_ordinal IS NULL
"""

_DELETE_INLINE_SQL = """
    DELETE FROM stable_image_source_overrides
    WHERE stable_document_id = $1 AND block_id = $2 AND inline_ordinal = $3
"""


def build_override_target_key(
    stable_document_id: UUID,
    block_id: str,
    inline_ordinal: int | None,
) -> str:
    """Event target_keys locator 编码 ``"<doc>:<block>:<ordinal|->"``。"""
    ordinal_part = "-" if inline_ordinal is None else str(inline_ordinal)
    return f"{stable_document_id}:{block_id}:{ordinal_part}"


class StableImageSourceOverrideService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool
        self._events = ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def upsert_override(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        stable_document_id: UUID,
        block_id: str,
        inline_ordinal: int | None,
        url: str,
    ) -> int:
        _ensure_postgres_text(block_id, field="block_id")
        _ensure_postgres_text(url, field="url")
        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                target = await self._load_write_target(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    stable_document_id=stable_document_id,
                    block_id=block_id,
                )
                _validate_image_target(target, inline_ordinal)
                if not await self._events.is_active_fence(
                    conn,
                    record_id=record_id,
                    base_id=target.active_base_id,
                    generation=target.generation,
                ):
                    raise ImageSourceOverrideError(
                        status_code=409, code=CODE_STABLE_DOCUMENT_NOT_ACTIVE
                    )
                if inline_ordinal is None:
                    await conn.execute(
                        _UPSERT_STANDALONE_SQL,
                        uuid4(),
                        stable_document_id,
                        block_id,
                        url,
                    )
                else:
                    await conn.execute(
                        _UPSERT_INLINE_SQL,
                        uuid4(),
                        stable_document_id,
                        block_id,
                        url,
                        inline_ordinal,
                    )
                payload = build_representation_payload(
                    representation_section="image_overrides",
                    operation="upsert",
                    generation=target.generation,
                    base_id=str(target.active_base_id),
                    target_keys=[
                        build_override_target_key(stable_document_id, block_id, inline_ordinal)
                    ],
                )
                envelope = await self._events.publish_event_in_transaction(
                    conn,
                    record_id=record_id,
                    event_type="projection_ops",
                    payload_json=payload,
                )
                return envelope.sequence

    async def delete_override(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        stable_document_id: UUID,
        block_id: str,
        inline_ordinal: int | None,
    ) -> int:
        _ensure_postgres_text(block_id, field="block_id")
        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                target = await self._load_write_target(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    stable_document_id=stable_document_id,
                    block_id=block_id,
                )
                _validate_image_target(target, inline_ordinal)
                if not await self._events.is_active_fence(
                    conn,
                    record_id=record_id,
                    base_id=target.active_base_id,
                    generation=target.generation,
                ):
                    raise ImageSourceOverrideError(
                        status_code=409, code=CODE_STABLE_DOCUMENT_NOT_ACTIVE
                    )
                if inline_ordinal is None:
                    status = await conn.execute(
                        _DELETE_STANDALONE_SQL, stable_document_id, block_id
                    )
                else:
                    status = await conn.execute(
                        _DELETE_INLINE_SQL,
                        stable_document_id,
                        block_id,
                        inline_ordinal,
                    )
                # 无行删除：幂等 200、不发布事件、sequence 不变；返回当前
                # last_event_sequence（行为冻结，测试锁定，不在两种语义间漂移）。
                if status == "DELETE 0":
                    return await self._current_last_event_sequence(conn, record_id)
                payload = build_representation_payload(
                    representation_section="image_overrides",
                    operation="delete",
                    generation=target.generation,
                    base_id=str(target.active_base_id),
                    target_keys=[
                        build_override_target_key(stable_document_id, block_id, inline_ordinal)
                    ],
                )
                envelope = await self._events.publish_event_in_transaction(
                    conn,
                    record_id=record_id,
                    event_type="projection_ops",
                    payload_json=payload,
                )
                return envelope.sequence

    async def _load_write_target(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        stable_document_id: UUID,
        block_id: str,
    ) -> _OverrideWriteTarget:
        """写前一条 ownership/active/target SELECT（T10 同式）。

        404 collapse：record 不存在/非本人/已软删、document 不属于该
        record（含跨用户）同形，不泄露资源存在性。
        """
        row = await conn.fetchrow(
            """
            SELECT r.generation, r.active_base_id,
                   d.id AS document_id, d.status AS document_status,
                   b.block_type, b.payload_json
            FROM reading_records r
            LEFT JOIN stable_reading_documents d
              ON d.id = $3 AND d.reading_record_id = r.id
            LEFT JOIN stable_document_blocks b
              ON b.stable_document_id = d.id AND b.block_id = $4
            WHERE r.id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
            """,
            record_id,
            user_id,
            stable_document_id,
            block_id,
        )
        if row is None or row["document_id"] is None:
            raise ImageSourceOverrideError(status_code=404, code=CODE_NOT_FOUND)
        if row["document_status"] != "active":
            raise ImageSourceOverrideError(status_code=409, code=CODE_STABLE_DOCUMENT_NOT_ACTIVE)
        if row["block_type"] is None:
            raise ImageSourceOverrideError(status_code=404, code=CODE_IMAGE_BLOCK_NOT_FOUND)
        payload = row["payload_json"]
        return _OverrideWriteTarget(
            generation=int(row["generation"]),
            active_base_id=UUID(str(row["active_base_id"])),
            block_type=str(row["block_type"]),
            payload=payload if isinstance(payload, dict) else {},
        )

    async def _current_last_event_sequence(self, conn: asyncpg.Connection, record_id: UUID) -> int:
        value = await conn.fetchval(
            """
            SELECT COALESCE(
                (SELECT next_sequence - 1
                 FROM reader_event_sequences
                 WHERE reading_record_id = $1),
                0
            )
            """,
            record_id,
        )
        return int(value)


def _validate_image_target(target: _OverrideWriteTarget, inline_ordinal: int | None) -> None:
    """locator 语义校验（不存 block_type 列，行为由结构判定）。

    standalone：block_type=="image" 且 payload.position_kind=="standalone"
    且请求未带 ordinal。inline：payload.inline_images 是 list、ordinal
    落在真实数组范围、命中项是含 source_url 字段的图片 dict；
    paragraph/heading/list_item/blockquote/table_cell（含 image-only
    metadata_only cell）均可作为 owning block。
    """
    payload = target.payload
    if inline_ordinal is None:
        if target.block_type == "image" and payload.get("position_kind") == "standalone":
            return
        raise ImageSourceOverrideError(status_code=422, code=CODE_IMAGE_TARGET_NOT_FOUND)
    inline_images = payload.get("inline_images")
    if not isinstance(inline_images, list):
        raise ImageSourceOverrideError(status_code=422, code=CODE_IMAGE_TARGET_NOT_FOUND)
    if inline_ordinal >= len(inline_images):
        raise ImageSourceOverrideError(status_code=422, code=CODE_IMAGE_TARGET_NOT_FOUND)
    entry = inline_images[inline_ordinal]
    if not isinstance(entry, dict) or "source_url" not in entry:
        raise ImageSourceOverrideError(status_code=422, code=CODE_IMAGE_TARGET_NOT_FOUND)


def _ensure_postgres_text(value: str, *, field: str) -> None:
    """PostgreSQL text representability guard（技术合同修正）。

    PostgreSQL text 为 UTF-8 且不含 U+0000；lone surrogate（U+D800..U+DFFF）
    已覆盖所有非 UTF-8 路径，故在业务层以确定性 422 拒绝，零行零事件。
    """
    if "\u0000" in value:
        code = (
            CODE_URL_NULL_CHARACTER_NOT_PERSISTABLE
            if field == "url"
            else "block_id_null_character_not_persistable"
        )
        raise ImageSourceOverrideError(status_code=422, code=code)
    for ch in value:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            code = CODE_URL_NOT_REPRESENTABLE if field == "url" else CODE_BLOCK_ID_NOT_REPRESENTABLE
            raise ImageSourceOverrideError(status_code=422, code=code)
