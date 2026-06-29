from __future__ import annotations

from typing import Any


def sanitize_failure_message(
    value: Any,
    *,
    default: str | None = None,
    max_length: int = 240,
) -> str | None:
    message = _compact_text(value)
    if not message:
        message = _compact_text(default)
    if not message:
        return None
    return message[:max_length]


def _compact_text(value: Any) -> str | None:
    if value is None:
        return None
    compacted = " ".join(str(value).split())
    return compacted or None
