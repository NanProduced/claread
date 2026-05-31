from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Literal

from claread_eval.schemas.run import EvalCaseArtifact

SanitizeMode = Literal["block", "strip"]

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "api_secret",
    "api_token",
    "app_secret",
    "auth_header",
    "auth_token",
    "authorization",
    "base_url",
    "bearer_token",
    "billed_points",
    "client_secret",
    "database_url",
    "connection_string",
    "cookie",
    "credential",
    "credentials",
    "extra_headers",
    "id_token",
    "langsmith_token",
    "openai_api_key",
    "passwd",
    "password",
    "private_key",
    "private_key_pem",
    "record_id",
    "refresh_token",
    "secret",
    "secret_key",
    "session_id",
    "session_token",
    "task_id",
    "token",
    "user_id",
}

SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


class ArtifactSanitizationError(ValueError):
    pass


def _looks_sensitive_string(value: str) -> bool:
    return any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS)


def _scan_sensitive(value: Any, path: str, findings: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in SENSITIVE_FIELD_NAMES:
                findings.append(child_path)
            _scan_sensitive(child, child_path, findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_sensitive(child, f"{path}[{index}]", findings)
    elif isinstance(value, str) and _looks_sensitive_string(value):
        findings.append(path or "<root>")


def _strip_sensitive(value: Any, path: str, findings: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in SENSITIVE_FIELD_NAMES:
                findings.append(child_path)
                continue
            sanitized[key] = _strip_sensitive(child, child_path, findings)
        return sanitized
    if isinstance(value, list):
        return [
            _strip_sensitive(child, f"{path}[{index}]", findings)
            for index, child in enumerate(value)
        ]
    if isinstance(value, str) and _looks_sensitive_string(value):
        findings.append(path or "<root>")
        return "<redacted>"
    return value


def sanitized_payload(payload: dict[str, Any], *, mode: SanitizeMode = "block") -> dict[str, Any]:
    copied = deepcopy(payload)
    findings: list[str] = []
    if mode == "strip":
        sanitized = _strip_sensitive(copied, "", findings)
        if findings:
            metadata = sanitized.setdefault("artifact_sanitization", {})
            metadata["mode"] = "strip"
            metadata["removed_fields"] = findings
        return sanitized

    _scan_sensitive(copied, "", findings)
    if findings:
        joined = ", ".join(findings)
        raise ArtifactSanitizationError(f"Sensitive artifact fields found: {joined}")
    return copied


def assert_artifact_sanitized(artifact: EvalCaseArtifact) -> None:
    payload = artifact.model_dump(mode="json")
    findings: list[str] = []
    _scan_sensitive(payload, "", findings)
    if findings:
        joined = ", ".join(findings)
        raise ArtifactSanitizationError(f"Sensitive artifact fields found: {joined}")


def sanitized_artifact_payload(
    artifact: EvalCaseArtifact,
    *,
    mode: SanitizeMode = "block",
) -> dict[str, Any]:
    return sanitized_payload(artifact.model_dump(mode="json"), mode=mode)
