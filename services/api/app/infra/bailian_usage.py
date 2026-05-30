from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TOKEN_FIELD_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens", "input_token_count"),
    "output_tokens": ("output_tokens", "completion_tokens", "output_token_count"),
    "total_tokens": ("total_tokens", "tokens", "token_count", "total_token_count"),
    "cache_read_tokens": ("cache_read_tokens",),
    "cache_write_tokens": ("cache_write_tokens",),
}

SAFE_RESPONSE_FIELDS = ("request_id", "status_code", "code", "message")


def _response_value(response: Any, key: str) -> Any:
    if isinstance(response, Mapping):
        return response.get(key)
    return getattr(response, key, None)


def _safe_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _safe_json(child, depth=depth + 1)
            for key, child in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth=depth + 1) for item in list(value)[:30]]
    return str(value)[:200]


def _number_from_mapping(source: Mapping[str, Any], aliases: tuple[str, ...]) -> int:
    for key in aliases:
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def normalize_usage_data(raw_usage: Any) -> dict[str, Any]:
    usage = raw_usage if isinstance(raw_usage, Mapping) else {}
    provider_usage_available = bool(usage)
    aggregate = {
        field: _number_from_mapping(usage, aliases)
        for field, aliases in TOKEN_FIELD_ALIASES.items()
    }
    if aggregate["total_tokens"] == 0:
        aggregate["total_tokens"] = aggregate["input_tokens"] + aggregate["output_tokens"]

    payload: dict[str, Any] = {
        "provider_usage_available": provider_usage_available,
        "aggregate": aggregate,
    }
    if provider_usage_available:
        payload["provider_usage"] = _safe_json(usage)
    return payload


def usage_data_from_response(response: Any) -> dict[str, Any]:
    usage = _response_value(response, "usage")
    if usage is None:
        output = _response_value(response, "output")
        if isinstance(output, Mapping):
            usage = output.get("usage")
    return normalize_usage_data(usage)


def provider_metadata_from_response(response: Any) -> dict[str, Any]:
    usage = _response_value(response, "usage")
    if usage is None:
        output = _response_value(response, "output")
        if isinstance(output, Mapping):
            usage = output.get("usage")
    metadata = {
        field: _safe_json(_response_value(response, field))
        for field in SAFE_RESPONSE_FIELDS
        if _response_value(response, field) is not None
    }
    metadata["provider_usage_available"] = bool(usage)
    return metadata


def combine_usage_data(items: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = {field: 0 for field in TOKEN_FIELD_ALIASES}
    provider_usage_available = False

    for item in items:
        if not isinstance(item, Mapping):
            continue
        provider_usage_available = provider_usage_available or bool(item.get("provider_usage_available"))
        item_aggregate = item.get("aggregate")
        if not isinstance(item_aggregate, Mapping):
            item_aggregate = item
        for field in aggregate:
            try:
                aggregate[field] += int(item_aggregate.get(field) or 0)
            except (AttributeError, TypeError, ValueError):
                continue

    if aggregate["total_tokens"] == 0:
        aggregate["total_tokens"] = aggregate["input_tokens"] + aggregate["output_tokens"]

    return {
        "provider_usage_available": provider_usage_available,
        "aggregate": aggregate,
    }
