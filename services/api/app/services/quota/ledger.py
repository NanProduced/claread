"""Credit ledger service.

Handles credit ledger query logic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger(__name__)

ENTRY_TYPE_DESCRIPTIONS: dict[str, str] = {
    "analysis_deduct": "分析扣减",
    "ai_capability_deduct": "AI 能力扣减",
    "feedback_reward": "反馈奖励 · 你的反馈已被采纳",
    "daily_grant": "每日常规额度刷新",
    "bonus_grant": "奖励积分到账",
    "refund": "能力失败 · 积分退回",
    "manual_adjust": "管理员调整",
}


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("metadata_json is a non-JSON string, falling back to empty dict")
            return {}
    return {}


async def get_credit_ledger(
    user_id: UUID,
    cursor: UUID | None = None,
    limit: int = 20,
) -> dict:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    limit_val = min(limit, 100)
    params: list = [user_id]
    where_clauses = ["l.user_id = $1"]

    if cursor:
        params.append(cursor)
        where_clauses.append(f"l.id < ${len(params)}")

    params.append(limit_val + 1)
    query_limit = f"${len(params)}"
    where_sql = " AND ".join(where_clauses)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT l.id, l.entry_type, l.points, l.bucket_type,
                   l.balance_after, l.metadata_json, l.created_at,
                   l.task_id,
                   r.title AS article_title
            FROM user_credit_ledger l
            LEFT JOIN analysis_tasks t ON l.task_id = t.id
            LEFT JOIN analysis_records r ON t.analysis_record_id = r.id
            WHERE {where_sql}
            ORDER BY l.created_at DESC
            LIMIT {query_limit}
            """,
            *params,
        )

    items = []
    for row in rows[:limit_val]:
        entry_type = row["entry_type"]
        description = ENTRY_TYPE_DESCRIPTIONS.get(entry_type, entry_type)
        article_title = row.get("article_title")
        metadata = _parse_metadata(row.get("metadata_json"))

        if entry_type == "analysis_deduct" and article_title:
            description = f"分析扣减 · {article_title[:30]}"
        elif entry_type == "ai_capability_deduct":
            capability_code = metadata.get("capability_code")
            query = metadata.get("query")
            if capability_code == "dict_ai_lookup" and query:
                description = f"AI 词典能力扣减 · {str(query)[:30]}"
            elif capability_code == "reader_ask":
                description = f"Ask Claread 扣减 · {str(article_title or '当前文章')[:30]}"
        elif entry_type == "refund":
            capability_code = metadata.get("capability_code")
            query = metadata.get("query")
            if capability_code == "dict_ai_lookup" and query:
                description = f"AI 词典能力退款 · {str(query)[:30]}"
            elif capability_code == "reader_ask":
                description = f"Ask Claread 退款 · {str(article_title or '当前文章')[:30]}"

        items.append({
            "id": str(row["id"]),
            "entry_type": entry_type,
            "points": row["points"],
            "bucket_type": row["bucket_type"],
            "balance_after": row["balance_after"],
            "description": description,
            "article_title": article_title,
            "metadata": metadata,
            "task_id": str(row["task_id"]) if row.get("task_id") else None,
            "created_at": row["created_at"],
        })

    has_more = len(rows) > limit_val
    next_cursor = str(items[-1]["id"]) if items and has_more else None

    return {
        "items": items,
        "cursor": next_cursor,
        "has_more": has_more,
    }
