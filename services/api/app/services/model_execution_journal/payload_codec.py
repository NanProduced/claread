from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.services.model_execution_journal.models import (
    DisplayTitleResumePayloadV1,
    GrammarBatchResumePayloadV1,
    PayloadContractError,
    PreparedCaptureEnvelope,
    SemanticOutlineResumePayloadV1,
    UsageEventDraftV1,
)

MAX_RESUME_PAYLOAD_BYTES = 1_048_576
MAX_USAGE_EVENT_DRAFT_BYTES = 65_536
MAX_CAPTURE_PAYLOAD_BYTES = 1_114_112

_RESUME_MODELS: dict[tuple[str, int], type[BaseModel]] = {
    ("reader.display_title.result", 1): DisplayTitleResumePayloadV1,
    ("reader.grammar_batch.result", 1): GrammarBatchResumePayloadV1,
    ("reader.semantic_outline.result", 1): SemanticOutlineResumePayloadV1,
}
_INVOCATION_RESUME_KIND = {
    "reader.display_title": "reader.display_title.result",
    "reader.grammar_batch": "reader.grammar_batch.result",
    "reader.semantic_outline": "reader.semantic_outline.result",
}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "prompt",
    "raw_http_response",
    "raw_provider_response",
    "refresh_token",
    "sdk_request",
    "secret_headers",
    "system_instructions",
    "system_prompt",
    "access_token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|authorization)\s*[:=]\s*\S+"),
)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PayloadContractError("malformed_json_payload") from exc


def _assert_safe_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _FORBIDDEN_KEYS:
                raise PayloadContractError("forbidden_payload_content")
            _assert_safe_payload(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_safe_payload(item)
        return
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    ):
        raise PayloadContractError("forbidden_payload_content")


def decode_resume_payload(
    *,
    kind: str,
    schema_version: int,
    payload: Any,
) -> BaseModel:
    model_type = _RESUME_MODELS.get((kind, schema_version))
    if model_type is None:
        raise PayloadContractError("unsupported_resume_payload")
    _assert_safe_payload(payload)
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise PayloadContractError("invalid_resume_payload") from exc


def decode_usage_event_draft(
    *,
    schema_version: int,
    payload: Any,
) -> UsageEventDraftV1:
    if schema_version != 1:
        raise PayloadContractError("unsupported_usage_event_draft")
    _assert_safe_payload(payload)
    try:
        return UsageEventDraftV1.model_validate(payload)
    except ValidationError as exc:
        raise PayloadContractError("invalid_usage_event_draft") from exc


def prepare_capture_envelope(
    *,
    invocation_kind: str,
    resume_payload_kind: str,
    resume_payload_schema_version: int,
    usage_event_draft_schema_version: int,
    normalized_payload: Any,
    usage_event_draft: Any,
) -> PreparedCaptureEnvelope:
    if _INVOCATION_RESUME_KIND.get(invocation_kind) != resume_payload_kind:
        raise PayloadContractError("invocation_resume_kind_mismatch")

    decoded_resume = decode_resume_payload(
        kind=resume_payload_kind,
        schema_version=resume_payload_schema_version,
        payload=normalized_payload,
    )
    decoded_usage = decode_usage_event_draft(
        schema_version=usage_event_draft_schema_version,
        payload=usage_event_draft,
    )
    normalized_resume = decoded_resume.model_dump(mode="json")
    normalized_usage = decoded_usage.model_dump(mode="json")
    resume_bytes = canonical_json_bytes(normalized_resume)
    usage_bytes = canonical_json_bytes(normalized_usage)

    if len(resume_bytes) > MAX_RESUME_PAYLOAD_BYTES:
        raise PayloadContractError("resume_payload_too_large")
    if len(usage_bytes) > MAX_USAGE_EVENT_DRAFT_BYTES:
        raise PayloadContractError("usage_event_draft_too_large")
    if len(resume_bytes) + len(usage_bytes) > MAX_CAPTURE_PAYLOAD_BYTES:
        raise PayloadContractError("capture_payload_too_large")

    envelope = {
        "invocation_kind": invocation_kind,
        "resume_payload_kind": resume_payload_kind,
        "resume_payload_schema_version": resume_payload_schema_version,
        "usage_event_draft_schema_version": usage_event_draft_schema_version,
        "normalized_payload": normalized_resume,
        "usage_event_draft": normalized_usage,
    }
    envelope_sha256 = hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    return PreparedCaptureEnvelope(
        invocation_kind=invocation_kind,
        resume_payload_kind=resume_payload_kind,
        resume_payload_schema_version=resume_payload_schema_version,
        usage_event_draft_schema_version=usage_event_draft_schema_version,
        normalized_payload=normalized_resume,
        usage_event_draft=normalized_usage,
        capture_envelope_sha256=envelope_sha256,
        resume_payload_bytes=len(resume_bytes),
        usage_event_draft_bytes=len(usage_bytes),
    )
