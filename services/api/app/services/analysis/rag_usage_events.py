from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.infra.bailian_usage import combine_usage_data
from app.services.ai_usage import (
    AIUsageEventCreate,
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_RAG_EMBEDDING,
    CAPABILITY_RAG_RERANK,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    record_ai_usage_event,
)

logger = logging.getLogger(__name__)

_RAG_EVENT_DEFINITIONS = {
    CAPABILITY_RAG_EMBEDDING: {
        "usage_key": "embedding_usage",
        "model_key": "embedding_model",
        "metadata_key": "embedding_provider_metadata",
        "latency_key": "embedding_latency_ms",
        "input_count_key": "embedding_input_count",
        "input_chars_key": "embedding_input_chars",
    },
    CAPABILITY_RAG_RERANK: {
        "usage_key": "rerank_usage",
        "model_key": "rerank_model",
        "metadata_key": "rerank_provider_metadata",
        "latency_key": "rerank_latency_ms",
        "input_count_key": "rerank_input_count",
        "input_chars_key": "rerank_input_chars",
    },
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_latency_ms(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _extract_rag_groups(result: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    rag_debug = _as_mapping(result.get("rag_debug") if result else None)
    agents = _as_mapping(rag_debug.get("agents"))
    grammar = _as_mapping(agents.get("grammar"))
    return {
        str(key): _as_mapping(value)
        for key, value in grammar.items()
        if isinstance(value, Mapping)
    }


def _aggregate_event_payload(
    groups: dict[str, Mapping[str, Any]],
    capability_code: str,
) -> dict[str, Any] | None:
    definition = _RAG_EVENT_DEFINITIONS[capability_code]
    usage_items: list[dict[str, Any]] = []
    per_output_type: dict[str, dict[str, Any]] = {}
    models: set[str] = set()
    total_latency_ms = 0
    total_input_count = 0
    total_input_chars = 0

    for output_type, group in groups.items():
        usage_data = group.get(definition["usage_key"])
        if not isinstance(usage_data, Mapping):
            continue

        model = str(group.get(definition["model_key"]) or "").strip()
        if model:
            models.add(model)
        latency_ms = _as_latency_ms(group.get(definition["latency_key"]))
        input_count = _as_int(group.get(definition["input_count_key"]))
        input_chars = _as_int(group.get(definition["input_chars_key"]))
        provider_metadata = _as_mapping(group.get(definition["metadata_key"]))

        usage_items.append(dict(usage_data))
        total_latency_ms += latency_ms
        total_input_count += input_count
        total_input_chars += input_chars
        per_output_type[output_type] = {
            "model": model or None,
            "usage_data": usage_data,
            "provider_metadata": dict(provider_metadata),
            "latency_ms": latency_ms,
            "input_count": input_count,
            "input_chars": input_chars,
            "selection_mode": group.get("selection_mode"),
            "fallback_reason": group.get("fallback_reason"),
            "query_count": group.get("query_count"),
            "ann_hit_count": group.get("ann_hit_count"),
            "rerank_hit_count": group.get("rerank_hit_count"),
        }

    if not usage_items:
        return None

    usage_data = combine_usage_data(usage_items)
    provider_usage_available = bool(usage_data.get("provider_usage_available"))
    model_name = sorted(models)[0] if len(models) == 1 else None

    return {
        "usage_data": usage_data,
        "model_name": model_name,
        "latency_ms": total_latency_ms or None,
        "metadata": {
            "call_count": len(usage_items),
            "rag_output_types": sorted(per_output_type),
            "per_output_type": per_output_type,
            "provider_usage_available": provider_usage_available,
            "input_count": total_input_count,
            "input_chars": total_input_chars,
            "model_names": sorted(models),
        },
    }


async def record_rag_usage_events_from_result(
    *,
    result: Mapping[str, Any] | None,
    user_id: UUID | None,
    task_id: UUID | None,
    record_id: UUID | None,
    request_id: str | None,
    workflow_name: str,
    workflow_version: str,
    schema_version: str | None,
    prompt_version: str | None,
    metadata_json: dict[str, Any] | None = None,
) -> None:
    """Persist RAG provider usage events without affecting the caller flow."""
    groups = _extract_rag_groups(result)
    if not groups:
        return

    base_metadata = dict(metadata_json or {})
    for capability_code in (CAPABILITY_RAG_EMBEDDING, CAPABILITY_RAG_RERANK):
        payload = _aggregate_event_payload(groups, capability_code)
        if payload is None:
            continue

        try:
            await record_ai_usage_event(
                AIUsageEventCreate(
                    usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                    capability_code=capability_code,
                    billing_mode=BILLING_MODE_INTERNAL_ONLY,
                    status=STATUS_SUCCEEDED,
                    user_id=user_id,
                    task_id=task_id,
                    record_id=record_id,
                    request_id=request_id,
                    workflow_name=workflow_name,
                    workflow_version=workflow_version,
                    schema_version=schema_version,
                    prompt_version=prompt_version,
                    model_provider="bailian",
                    model_name=payload["model_name"],
                    usage_data=payload["usage_data"],
                    latency_ms=payload["latency_ms"],
                    billed_points=0,
                    metadata_json={
                        **base_metadata,
                        **payload["metadata"],
                    },
                )
            )
            logger.debug(
                "Recorded %s usage event for record=%s task=%s",
                capability_code,
                record_id,
                task_id,
            )
        except Exception:
            logger.warning(
                "Failed to record %s usage event for record=%s task=%s",
                capability_code,
                record_id,
                task_id,
                exc_info=True,
            )
