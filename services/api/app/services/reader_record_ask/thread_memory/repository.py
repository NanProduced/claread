"""Thread memory persistence seam（R0.1 H1 + H2 处理约定）。

H1 处理约定（R0.1 §4.2(d) 步骤 3 注释 / §13.2 H1）：
    cold-load 下 ``history_projection.py:319`` 显式 ``del resolved_evidence_json``，
    compactor 不能复用 history_projection 路径。本 repository 直接 SELECT
    ``reader_ask_turn_runs.resolved_evidence_json``，仅 server-side，不投影到 wire
    （不进入客户端可见 DTO）。

H2 处理约定（R0.1 §4.2(d) 步骤 3 注释 / §13.2 H2）：
    retry 失败时 ``current_turn_run_id`` 切到 failed run，但原 ok run 的 binding
    仍在 DB。本 repository 扫描 ``supersedes_run_id`` 链上所有 ``final_status='ok'``
    的 turn_run，不跟随 ``current_turn_run_id``；canonical run = supersedes 链
    最新 ok run。

R1.6 P0-3: 每个 assistant message 只取最新 canonical ok run（DISTINCT ON
    (message_id)）。成功 regenerate 后旧 ok run 的 binding 从 Host map /
    allowlist 中消失。failed/cancelled retry 回退到该 message 之前最新 ok run
    （DISTINCT ON 自然实现：旧 ok run 仍是该 message 最新 ok run）。

R1.6 P1-3: 0028 未应用且 memory flag 被误开时，snapshot table 缺失必须
    typed fail-soft 为"无 memory"，不得让整个 Ask 500。

agentic lane 独立：不导入 legacy Ask persistence。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.database import connection as db_connection
from app.database.json_compat import (
    ensure_json_array,
    ensure_json_object,
    jsonb_param,
)
from app.services.reader_record_ask.thread_memory.schema import (
    ThreadMemorySnapshot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed write result (R1.5 P0-1): replaces the old ``-> None`` return so
# callers (R2 CAS loop) can distinguish applied vs. conflict without a
# follow-up SELECT. Mirrors the SQL ``RETURNING`` semantics.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotWriteResult:
    """Outcome of an UPSERT attempt against ``reader_ask_thread_memory``.

    - ``applied=True``  → row was written; ``version`` is the new version.
    - ``applied=False`` → CAS mismatch (concurrent writer won); ``version``
      is the live DB version at attempt time so the caller can retry.
    """

    applied: bool
    version: int


@dataclass(frozen=True, slots=True)
class CanonicalMemoryView:
    """One repeatable-read view used to validate or rebuild thread memory.

    Snapshot, canonical messages, and latest successful turn runs must come
    from the same PostgreSQL snapshot.  Mixing three independently acquired
    connections can otherwise validate an old memory row against a newer
    regenerate result (or vice versa).
    """

    snapshot: ThreadMemorySnapshot | None
    snapshot_version: int
    canonical_messages: tuple[dict[str, Any], ...]
    ok_turn_runs: tuple[dict[str, Any], ...]
    storage_available: bool = True


# resolved_evidence_json 中保留的 ID 类字段（H1：不投影内容字段到 wire）。
# 内容字段（snippet / canonical_url / web_title / web_description /
# published_at / retrieved_at / source_fingerprint）一律不进入 compaction 输入，
# 也不进入本 repository 的返回 dict。
_BINDING_ID_FIELDS = (
    "citation_id",
    "handle_id",
    "source_kind",
    "unit_id",
    "anchor_segment_id",
    "kind",
    "source_tool",
)

_CANONICAL_MESSAGES_SQL = """
    SELECT m.id, m.role, m.status, m.content_md,
           m.created_at, m.current_turn_run_id,
           tr.id AS canonical_turn_run_id,
           tr.user_visible_output_json -> 'answer_blocks'
               AS answer_blocks_json,
           tr.user_visible_output_json -> 'web_search'
               AS web_search_json
    FROM reader_ask_messages m
    LEFT JOIN LATERAL (
        SELECT id, user_visible_output_json
        FROM reader_ask_turn_runs
        WHERE message_id = m.id AND final_status = 'ok'
        ORDER BY run_attempt DESC, created_at DESC, id DESC
        LIMIT 1
    ) tr ON true
    LEFT JOIN LATERAL (
        SELECT s.client_submission_id, s.created_at,
               s.user_message_id, s.assistant_message_id
        FROM reader_ask_client_submissions s
        WHERE s.thread_id = m.thread_id
          AND (s.user_message_id = m.id OR s.assistant_message_id = m.id)
        ORDER BY s.created_at ASC, s.client_submission_id ASC
        LIMIT 1
    ) submission ON true
    WHERE m.thread_id = $1
      AND (
        m.role = 'user'
        OR (
          m.role = 'assistant'
          AND m.status = 'completed'
          AND tr.id IS NOT NULL
        )
      )
    ORDER BY
      COALESCE(submission.created_at, m.created_at) ASC,
      COALESCE(submission.client_submission_id::text, m.id::text) ASC,
      CASE
        WHEN submission.user_message_id = m.id THEN 0
        WHEN submission.assistant_message_id = m.id THEN 1
        WHEN m.role = 'user' THEN 0
        ELSE 1
      END ASC,
      m.created_at ASC,
      m.id ASC
"""

_CANONICAL_OK_RUNS_SQL = """
    SELECT DISTINCT ON (message_id)
           id, message_id, thread_id, status, final_status,
           terminal_reason, resolved_evidence_json,
           envelope_fingerprint, execution_version,
           supersedes_run_id, run_attempt, created_at
    FROM reader_ask_turn_runs
    WHERE thread_id = $1
      AND final_status = 'ok'
    ORDER BY message_id, run_attempt DESC, created_at DESC, id DESC
"""


class ThreadMemoryRepository:
    """DB access for thread memory snapshot + canonical binding source.

    所有方法仅服务端消费，不投影 restricted 字段（snippet / canonical_url /
    source_fingerprint 等）到 wire。
    """

    def __init__(self, *, pool: Any | None = None) -> None:
        self._pool = pool

    def _pool_or_raise(self) -> Any:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    @staticmethod
    def _canonical_messages_from_rows(rows: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": str(row["id"]),
                "role": row["role"],
                "status": row["status"],
                "content_md": row["content_md"],
                "created_at": row["created_at"],
                "current_turn_run_id": (
                    str(row["current_turn_run_id"])
                    if row["current_turn_run_id"] is not None
                    else None
                ),
                "canonical_turn_run_id": (
                    str(row["canonical_turn_run_id"])
                    if row["canonical_turn_run_id"] is not None
                    else None
                ),
                "answer_blocks": ensure_json_array(row["answer_blocks_json"]),
                "web_search_summary": ensure_json_object(row["web_search_json"])
                or None,
            }
            for row in rows
        ]

    @staticmethod
    def _ok_turn_runs_from_rows(rows: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": str(row["id"]),
                "message_id": str(row["message_id"]),
                "thread_id": str(row["thread_id"]),
                "status": row["status"],
                "final_status": row["final_status"],
                "terminal_reason": row["terminal_reason"],
                "resolved_evidence_json": row["resolved_evidence_json"],
                "envelope_fingerprint": row["envelope_fingerprint"],
                "execution_version": row["execution_version"],
                "supersedes_run_id": (
                    str(row["supersedes_run_id"])
                    if row["supersedes_run_id"] is not None
                    else None
                ),
                "run_attempt": int(row["run_attempt"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _snapshot_from_row(row: Any | None) -> tuple[ThreadMemorySnapshot | None, int]:
        if row is None:
            return None, 0
        raw = row["snapshot_json"]
        if isinstance(raw, str):
            try:
                import json

                payload: Any = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning(
                    "thread_memory snapshot_json is not valid JSON; "
                    "fail-soft to deterministic rebuild"
                )
                return None, int(row["version"])
        elif isinstance(raw, dict):
            payload = raw
        else:
            logger.warning(
                "thread_memory snapshot_json has unexpected type %s; "
                "fail-soft to deterministic rebuild",
                type(raw).__name__,
            )
            return None, int(row["version"])
        try:
            return ThreadMemorySnapshot.model_validate(payload), int(row["version"])
        except ValidationError as exc:
            logger.warning(
                "thread_memory snapshot_json failed schema validation; "
                "fail-soft to deterministic rebuild: %s",
                exc,
            )
            return None, int(row["version"])

    async def load_canonical_memory_view(
        self, *, thread_id: UUID
    ) -> CanonicalMemoryView | None:
        """Load memory inputs from one read-only repeatable-read transaction.

        ``to_regclass`` avoids aborting the transaction when migration 0028
        has not yet been applied.  Other database failures fail soft to no
        memory so the optional feature cannot take down Ask.
        """

        pool = self._pool_or_raise()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(
                    isolation="repeatable_read",
                    readonly=True,
                ):
                    memory_table = await conn.fetchval(
                        "SELECT to_regclass('public.reader_ask_thread_memory')"
                    )
                    snapshot_row = (
                        await conn.fetchrow(
                            """
                            SELECT thread_id, snapshot_json, version, updated_at
                            FROM reader_ask_thread_memory
                            WHERE thread_id = $1
                            """,
                            thread_id,
                        )
                        if memory_table is not None
                        else None
                    )
                    message_rows = await conn.fetch(
                        _CANONICAL_MESSAGES_SQL,
                        thread_id,
                    )
                    run_rows = await conn.fetch(
                        _CANONICAL_OK_RUNS_SQL,
                        thread_id,
                    )
        except Exception as exc:  # noqa: BLE001 - optional-memory fail-soft
            logger.warning(
                "thread_memory canonical view read failed; skip memory: %s",
                exc,
            )
            return None

        snapshot, snapshot_version = self._snapshot_from_row(snapshot_row)
        return CanonicalMemoryView(
            snapshot=snapshot,
            snapshot_version=snapshot_version,
            canonical_messages=tuple(
                self._canonical_messages_from_rows(message_rows)
            ),
            ok_turn_runs=tuple(self._ok_turn_runs_from_rows(run_rows)),
            storage_available=memory_table is not None,
        )

    async def list_canonical_messages(
        self, *, thread_id: UUID
    ) -> list[dict[str, Any]]:
        """列出 canonical messages（R0.1 §4.2(e) 准入规则 + H2）。

        返回 user message（总是允许，即使对应回答失败——冻结决策 #5）+
        assistant message（``status='completed'`` 且其 supersedes 链上存在
        ``final_status='ok'`` 的 turn_run）。不跟随 ``current_turn_run_id``
        ——retry 失败时 current_turn_run_id 切到 failed run，但原 ok run 仍在
        DB（H2）。按 ``created_at ASC`` 排序。

        R1.5 P0-3: 对于 assistant message，LATERAL JOIN 最新 ok turn_run 的
        ``user_visible_output_json``，提取安全可见的 ``answer_blocks`` 和
        ``web_search`` 字段。**禁止**读取 ``reasoning_projection_json`` /
        ``tool_trace_json`` / raw provider payload（R0.1 §4.2(e) 准入规则）。

        R1.6.1 P0-1: LATERAL JOIN 必须返回真实 ok run 的 ``id`` 作为
        ``canonical_turn_run_id``，并使用 ``ORDER BY created_at DESC, id DESC``
        做稳定排序。**不得**使用消息行的 ``m.current_turn_run_id`` 代表
        canonical ok run —— 该字段在 failed/cancelled retry 后会指向失败 run，
        但 canonical ok run 仍是旧 run（H2）。watermark 只消费
        ``canonical_turn_run_id``。
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_CANONICAL_MESSAGES_SQL, thread_id)
        return self._canonical_messages_from_rows(rows)

    async def list_ok_turn_runs_with_bindings(
        self, *, thread_id: UUID
    ) -> list[dict[str, Any]]:
        """列出 canonical ok turn_run（R1.6 P0-3：每 message 最新 ok run）。

        R1.6 P0-3 修复：原实现返回 thread 内全部 ``final_status='ok'`` run，
        成功 regenerate 后旧 ok run 的 binding 仍进入 allowlist。现使用
        ``DISTINCT ON (message_id) ... ORDER BY message_id, created_at DESC, id DESC``
        只取每个 assistant message 的最新 ok run。

        R1.6.1 P0-1: ``ORDER BY`` 增加 ``id DESC`` 作为稳定 tiebreaker，
        确保同一 ``created_at`` 的 run 也有确定性顺序。

        行为：
        - 首次成功：该 message 的唯一 ok run 被返回。
        - 成功 regenerate：新 ok run 的 ``created_at`` 更晚 → 旧 ok run 被排除
          → 旧 binding 从 Host map / allowlist 中消失。
        - failed/cancelled retry：failed run 不在 ``final_status='ok'`` 集合中
          → 旧 ok run 仍是该 message 最新 ok run → binding 不变。
        """
        pool = self._pool_or_raise()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_CANONICAL_OK_RUNS_SQL, thread_id)
        return self._ok_turn_runs_from_rows(rows)

    async def list_bindings_for_compaction(
        self,
        *,
        thread_id: UUID,
        before_turn_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """列出 compaction 输入用的 citation bindings（H1：仅 server-side）。

        直接 SELECT ``resolved_evidence_json``，解析为扁平 binding dict 列表。
        每个 dict 仅含 ID 类字段（``citation_id`` / ``handle_id`` / ``source_kind``
        / ``unit_id`` / ``anchor_segment_id`` / ``kind`` / ``source_tool``）
        + ``rag_citation`` + ``turn_run_id``；不含 ``snippet`` / ``canonical_url``
        / ``web_title`` / ``web_description`` / ``published_at`` / ``retrieved_at``
        / ``source_fingerprint`` 等内容字段（H1：不投影到 wire）。

        ``before_turn_id`` 非空时仅包含该 turn_run 之前的 ok run（供增量压缩）。
        """
        pool = self._pool_or_raise()
        if before_turn_id is None:
            rows = await self._fetch_ok_bindings(pool, thread_id)
        else:
            rows = await self._fetch_ok_bindings_before(
                pool, thread_id, before_turn_id
            )

        flat: list[dict[str, Any]] = []
        for row in rows:
            turn_run_id = str(row["id"])
            raw = row["resolved_evidence_json"]
            bindings = ensure_json_array(raw)
            for b in bindings:
                if not isinstance(b, dict):
                    continue
                stripped: dict[str, Any] = {"turn_run_id": turn_run_id}
                for field in _BINDING_ID_FIELDS:
                    stripped[field] = b.get(field)
                stripped["rag_citation"] = b.get("rag_citation")
                flat.append(stripped)
        return flat

    async def _fetch_ok_bindings(
        self, pool: Any, thread_id: UUID
    ) -> list[Any]:
        # R1.6 P0-3: DISTINCT ON (message_id) — same canonical rule as
        # list_ok_turn_runs_with_bindings.
        # R1.6.1 P0-1: id DESC stable tiebreaker.
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT DISTINCT ON (message_id)
                       id, resolved_evidence_json, created_at
                FROM reader_ask_turn_runs
                WHERE thread_id = $1
                  AND final_status = 'ok'
                ORDER BY message_id, run_attempt DESC, created_at DESC, id DESC
                """,
                thread_id,
            )

    async def _fetch_ok_bindings_before(
        self, pool: Any, thread_id: UUID, before_turn_id: UUID
    ) -> list[Any]:
        # R1.6 P0-3: DISTINCT ON (message_id) with cutoff — same rule.
        # R1.6.1 P0-1: id DESC stable tiebreaker.
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT DISTINCT ON (message_id)
                       tr.id, tr.resolved_evidence_json, tr.created_at
                FROM reader_ask_turn_runs tr
                WHERE tr.thread_id = $1
                  AND tr.final_status = 'ok'
                  AND (tr.created_at, tr.id) < (
                    SELECT created_at, id
                    FROM reader_ask_turn_runs
                    WHERE id = $2
                  )
                ORDER BY message_id, tr.run_attempt DESC,
                         tr.created_at DESC, tr.id DESC
                """,
                thread_id,
                before_turn_id,
            )

    async def get_thread_memory_snapshot(
        self, *, thread_id: UUID
    ) -> ThreadMemorySnapshot | None:
        """读取 thread memory snapshot（单行，按 thread_id PK）。

        R1.5 P0-1: 解析 ``snapshot_json`` 为 :class:`ThreadMemorySnapshot`。
        异版（``version`` ≠ ``'thread_memory_v1'``）或非法 JSON → fail-soft
        返回 ``None``，调用方（coordinator）转 deterministic rebuild，绝不
        注入模型。这是防御深度：DB 中的 snapshot 是派生视图，真相源永远是
        canonical messages，可凭其完全重建（schema §6 / R0.1 §4.2(e)）。

        R1.6 P1-3: 0028 未应用且 memory flag 被误开时，``reader_ask_thread_memory``
        表缺失 → asyncpg 抛 ``UndefinedTableError``。此处 typed fail-soft
        为 ``None``（→ coordinator 转 deterministic rebuild），不得让整个
        Ask 500。同样适用于 ``list_canonical_messages`` /
        ``list_ok_turn_runs_with_bindings``（它们查的是已存在的
        ``reader_ask_messages`` / ``reader_ask_turn_runs`` 表，但仍可能遇到
        连接级故障）。
        """
        pool = self._pool_or_raise()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT thread_id, snapshot_json, version, updated_at
                    FROM reader_ask_thread_memory
                    WHERE thread_id = $1
                    """,
                    thread_id,
                )
        except Exception as exc:  # noqa: BLE001 — table-missing / DB error
            # R1.6 P1-3: fail-soft — log and return None so the
            # coordinator falls back to deterministic rebuild. Never
            # let a missing 0028 table crash the entire Ask pipeline.
            logger.warning(
                "thread_memory snapshot table read failed; "
                "fail-soft to deterministic rebuild: %s",
                exc,
            )
            return None
        if row is None:
            return None
        snapshot, _version = self._snapshot_from_row(row)
        return snapshot

    async def upsert_thread_memory_snapshot(
        self,
        *,
        thread_id: UUID,
        snapshot: ThreadMemorySnapshot,
        version: int,
    ) -> SnapshotWriteResult:
        """UPSERT thread memory snapshot（version 自增 + CAS 守卫）。

        R1.5 P0-1: 返回 :class:`SnapshotWriteResult` 使后续 R2 能正确处理
        watermark/version CAS。INSERT 时 ``version=1``；ON CONFLICT 时
        ``version = version + 1``，仅在当前 DB ``version`` == 传入 ``version``
        时更新（CAS 守卫，防并发轮竞争）。CAS 失配 → ``applied=False``，且
        ``version`` 是当时 DB 的 live version（通过 ``RETURNING`` 获取）。
        """
        pool = self._pool_or_raise()
        snapshot_json = snapshot.model_dump(mode="json")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_thread_memory
                    (thread_id, snapshot_json, version)
                VALUES ($1, $2::jsonb, 1)
                ON CONFLICT (thread_id) DO UPDATE
                SET snapshot_json = EXCLUDED.snapshot_json,
                    version = reader_ask_thread_memory.version + 1,
                    updated_at = NOW()
                WHERE reader_ask_thread_memory.version = $3
                RETURNING version
                """,
                thread_id,
                jsonb_param(snapshot_json),
                version,
            )
        if row is None:
            # ON CONFLICT 且 WHERE 失配 → CAS 冲突。读取 live version。
            async with pool.acquire() as conn:
                live = await conn.fetchrow(
                    """
                    SELECT version
                    FROM reader_ask_thread_memory
                    WHERE thread_id = $1
                    """,
                    thread_id,
                )
            live_version = int(live["version"]) if live is not None else 0
            return SnapshotWriteResult(applied=False, version=live_version)
        # row 非空 → INSERT 或 UPDATE 成功；version 是写入后的新 version。
        new_version = int(row["version"])
        return SnapshotWriteResult(applied=True, version=new_version)
