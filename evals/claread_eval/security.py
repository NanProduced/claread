from __future__ import annotations

import re
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"'}]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|admin[_-]?key|token|secret|password|authorization)"
        r"\"?\s*[:=]\s*\"?)[^\"'\s,}]+"
    ),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
]


def validate_https_or_local_url(base_url: str, *, setting_name: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    parsed = urlparse(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{setting_name} must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise RuntimeError(f"{setting_name} must not include URL credentials")
    hostname = parsed.hostname or ""
    if parsed.scheme == "http" and hostname not in _LOCAL_HOSTS:
        raise RuntimeError(f"{setting_name} must use https unless targeting localhost")
    return trimmed


def redact_sensitive_text(value: str, *, limit: int = 500) -> str:
    redacted = value[:limit]
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(
            lambda match: f"{match.group(1)}<redacted>"
            if match.lastindex
            else "<redacted>",
            redacted,
        )
    return redacted
