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


# ---------------------------------------------------------------------------
# Embedding-boundary canonical token mapping (text-embedding-v3/v4)
# ---------------------------------------------------------------------------
#
# DashScope text-embedding responses bill by INPUT tokens and only report
# ``total_tokens`` (native API) or ``prompt_tokens`` + ``total_tokens``
# (OpenAI-compatible surface). The generic LLM weighted billing reads
# input/output tokens, so an unmapped embedding usage would price at 0.
# This mapping is ONLY for the embedding usage boundary — it must never be
# applied to generic LLM usage normalization.


def _valid_embedding_token(value: Any) -> int | None:
    """Return ``value`` iff it is a non-bool, non-negative int; else None."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _canonical_embedding_result(available: bool, input_tokens: int) -> dict[str, Any]:
    return {
        "provider_usage_available": available,
        "aggregate": {
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }


def _canonical_from_flat_usage(usage: Mapping[str, Any]) -> int | None:
    """Raw provider usage rules: input > prompt > total, explicit 0 wins."""
    for key in ("input_tokens", "prompt_tokens"):
        value = _valid_embedding_token(usage.get(key))
        if value is not None:
            return value
    return _valid_embedding_token(usage.get("total_tokens"))


def canonical_embedding_tokens(raw: Any) -> dict[str, Any]:
    """Canonical token mapping for the DashScope text-embedding boundary.

    Accepts the three shapes actually produced in the embedding pipeline:

    1. raw provider usage mappings (``{"total_tokens": 27}``,
       ``{"prompt_tokens": 23, "total_tokens": 23}``,
       ``{"input_tokens": 5, "total_tokens": 9}``);
    2. ``normalize_usage_data`` envelopes (``provider_usage_available`` +
       ``aggregate`` + raw ``provider_usage``);
    3. ``combine_usage_data`` envelopes (``provider_usage_available`` +
       ``aggregate`` with input=0 / total>0 when batches only reported
       totals).

    Frozen rules:

    - valid token = non-bool, non-negative int (None/str/float/negative
      are unusable);
    - raw shape: valid ``input_tokens`` (incl. explicit 0) wins, then
      ``prompt_tokens``, then ``total_tokens``;
    - envelope shape: raw ``provider_usage`` wins when present; otherwise
      the aggregate is used, where input=0 with total>0 means "input was
      not reported" and total is taken as input;
    - ``output_tokens`` is always 0 and canonical ``total_tokens``
      always equals ``input_tokens``;
    - a legal explicit 0 is available with zero tokens; no usable source
      is unavailable with zero tokens;
    - an envelope whose ``provider_usage_available`` flag is explicitly
      False is unavailable.
    """
    if not isinstance(raw, Mapping):
        return _canonical_embedding_result(False, 0)

    aggregate = raw.get("aggregate")
    if isinstance(aggregate, Mapping):
        # Envelope shapes from normalize_usage_data / combine_usage_data.
        if raw.get("provider_usage_available") is False:
            return _canonical_embedding_result(False, 0)
        provider_usage = raw.get("provider_usage")
        if isinstance(provider_usage, Mapping):
            # The retained raw provider usage is authoritative when
            # present: if it has no usable token source (e.g. values are
            # None / invalid), the batch is unavailable even though the
            # generic aggregate may have normalised the gap to zeros.
            flat = _canonical_from_flat_usage(provider_usage)
            if flat is None:
                return _canonical_embedding_result(False, 0)
            return _canonical_embedding_result(True, flat)
        agg_input = _valid_embedding_token(aggregate.get("input_tokens"))
        agg_total = _valid_embedding_token(aggregate.get("total_tokens"))
        if agg_input is not None and agg_input > 0:
            return _canonical_embedding_result(True, agg_input)
        if agg_total is not None:
            return _canonical_embedding_result(True, agg_total)
        if agg_input is not None:
            return _canonical_embedding_result(True, agg_input)
        return _canonical_embedding_result(False, 0)

    # Raw provider usage mapping.
    flat = _canonical_from_flat_usage(raw)
    if flat is None:
        return _canonical_embedding_result(False, 0)
    return _canonical_embedding_result(True, flat)
