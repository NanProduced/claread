from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AIUsageEventCreate:
    usage_scope: str
    capability_code: str
    billing_mode: str
    status: str
    user_id: UUID | None = None
    task_id: UUID | None = None
    record_id: UUID | None = None
    daily_reader_article_id: str | None = None
    client_platform: str | None = None
    request_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    schema_version: str | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    usage_data: dict[str, Any] | None = None
    latency_ms: int | None = None
    billed_points: int | None = None
    billing_policy_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


def _sum_from_per_agent(per_agent: object, field: str) -> int:
    if not isinstance(per_agent, Mapping):
        return 0
    total = 0
    for usage in per_agent.values():
        if isinstance(usage, Mapping):
            total += int(usage.get(field) or 0)
    return total


def _extract_usage_totals(usage_data: dict[str, Any] | None) -> dict[str, int]:
    if not usage_data:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    aggregate: Mapping[str, Any]
    if isinstance(usage_data.get("aggregate"), Mapping):
        aggregate = usage_data["aggregate"]
    else:
        aggregate = usage_data

    input_tokens = int(aggregate.get("input_tokens") or 0)
    output_tokens = int(aggregate.get("output_tokens") or 0)
    total_tokens = int(aggregate.get("total_tokens") or 0) or (input_tokens + output_tokens)

    cache_read_tokens = int(aggregate.get("cache_read_tokens") or 0)
    cache_write_tokens = int(aggregate.get("cache_write_tokens") or 0)

    if cache_read_tokens == 0:
        cache_read_tokens = _sum_from_per_agent(usage_data.get("per_agent"), "cache_read_tokens")
    if cache_write_tokens == 0:
        cache_write_tokens = _sum_from_per_agent(usage_data.get("per_agent"), "cache_write_tokens")

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


async def record_ai_usage_event(event: AIUsageEventCreate) -> UUID | None:
    """
    Persist an AI usage audit event without interrupting the main business flow.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        logger.warning("Skipping ai_usage_event because database pool is not initialized")
        return None

    usage_totals = _extract_usage_totals(event.usage_data)
    metadata_json = dict(event.metadata_json)
    if event.usage_data is not None:
        metadata_json.setdefault("usage_snapshot", event.usage_data)

    try:
        async with pool.acquire() as conn:
            inserted_id = await conn.fetchval(
                """
                INSERT INTO ai_usage_events (
                    usage_scope, capability_code, billing_mode, status,
                    user_id, task_id, record_id, daily_reader_article_id,
                    client_platform, request_id,
                    workflow_name, workflow_version, schema_version, prompt_version,
                    model_route, model_profile, model_provider, model_name,
                    input_tokens, output_tokens, total_tokens,
                    cache_read_tokens, cache_write_tokens,
                    latency_ms, billed_points, billing_policy_version,
                    error_code, error_message, metadata_json, created_at
                )
                VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8,
                    $9, $10,
                    $11, $12, $13, $14,
                    $15, $16, $17, $18,
                    $19, $20, $21,
                    $22, $23,
                    $24, $25, $26,
                    $27, $28, $29::jsonb, $30
                )
                RETURNING id
                """,
                event.usage_scope,
                event.capability_code,
                event.billing_mode,
                event.status,
                event.user_id,
                event.task_id,
                event.record_id,
                event.daily_reader_article_id,
                event.client_platform,
                event.request_id,
                event.workflow_name,
                event.workflow_version,
                event.schema_version,
                event.prompt_version,
                event.model_route,
                event.model_profile,
                event.model_provider,
                event.model_name,
                usage_totals["input_tokens"],
                usage_totals["output_tokens"],
                usage_totals["total_tokens"],
                usage_totals["cache_read_tokens"],
                usage_totals["cache_write_tokens"],
                event.latency_ms,
                event.billed_points,
                event.billing_policy_version,
                event.error_code,
                (event.error_message or "")[:1000] or None,
                json.dumps(metadata_json, ensure_ascii=False),
                datetime.now(timezone.utc),
            )
        return inserted_id if isinstance(inserted_id, UUID) else None
    except Exception:
        logger.exception(
            "Failed to record ai_usage_event(scope=%s, capability=%s, status=%s)",
            event.usage_scope,
            event.capability_code,
            event.status,
        )
        return None
