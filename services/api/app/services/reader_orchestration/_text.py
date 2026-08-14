from __future__ import annotations

from typing import Any

# Reader-wide default language rule. Missing/blank language
# defaults to "en" (the same rule the article-ready and stable-ready
# submission paths apply), so every record has a deterministic
# language for sentence-segmentation policy resolution. Callers must
# use this helper instead of inventing local defaults.
DEFAULT_READER_LANGUAGE = "en"


def resolve_default_reader_language(language: str | None) -> str:
    """Normalize a record/input language, defaulting to ``en``.

    ``None``, empty, and whitespace-only values all resolve to
    :data:`DEFAULT_READER_LANGUAGE`; any other value is stripped and
    returned as-is (no content-based guessing).
    """
    return (language or DEFAULT_READER_LANGUAGE).strip() or DEFAULT_READER_LANGUAGE


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
