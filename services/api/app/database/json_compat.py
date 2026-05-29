"""Helpers for JSON/JSONB values returned from or sent to asyncpg."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def ensure_json_object(value: Any) -> dict[str, Any]:
    """Return a dict for JSONB object values, tolerating legacy string rows."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def ensure_json_array(value: Any) -> list[Any]:
    """Return a list for JSONB array values, tolerating legacy string rows."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, list):
            return list(parsed)
    return []


def jsonb_param(value: Any) -> Any:
    """Pass JSONB params to asyncpg without manual serialization."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value
