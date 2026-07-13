"""Representation event payload builder and validator.

Implements the Snapshot Representation Event Contract:
docs/initiatives/reader-agentic-orchestration/modules/representation-event-contract.md

G1 (User Editorial Assets) and G2 (Ask Supplements) reuse the existing
``projection_ops`` event type with ``representation_section`` set to
``user_assets`` or ``ask_supplements``.

G3 (user-visible record metadata, e.g. display-title status) reuses the
existing ``record_state_changed`` event type with ``representation_section``
set to ``record_metadata``.

This module is the single authoritative builder and validator for
representation event payloads.  Write paths must NOT hand-roll JSON.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

REPRESENTATION_PAYLOAD_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Hard resource limits (fail-closed; never silently truncate)
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES = 16 * 1024  # 16 KB serialized JSON
MAX_TARGET_KEYS = 64
MAX_KEY_LENGTH = 128

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

ALLOWED_SECTIONS: frozenset[str] = frozenset(
    {"user_assets", "ask_supplements", "record_metadata"}
)

ALLOWED_OPERATIONS_BY_SECTION: dict[str, frozenset[str]] = {
    "user_assets": frozenset({"upsert", "delete", "merge"}),
    "ask_supplements": frozenset({"upsert", "delete", "reactivate"}),
    "record_metadata": frozenset({"status_changed"}),
}

ALLOWED_METADATA_FIELDS: frozenset[str] = frozenset(
    {
        "display_title_zh",
        "title_generation_status",
        "title_generation_error_code",
        "title_generation_error_message",
    }
)

# ---------------------------------------------------------------------------
# Forbidden payload keys — redaction is fail-closed.
#
# These keys must NEVER appear in a representation event payload because they
# either leak user content (note text, selected text, Ask prompt/answer),
# internal diagnostics, auth material, raw Plate/Slate paths, or because they
# encode transport/rollout strategy (``reload_policy``, ``cursor_only`` …)
# which the contract explicitly bans from the persisted payload.
# ---------------------------------------------------------------------------

FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        # user content
        "note_text",
        "selected_text",
        "content",
        "content_md",
        "body",
        "text",
        # ask content
        "prompt",
        "answer",
        "question",
        "response",
        # raw layer / worker output
        "raw_output",
        "output",
        "layer_output",
        # auth / credentials
        "auth",
        "token",
        "authorization",
        "credentials",
        "api_key",
        # internal diagnostics
        "diagnostics",
        "stack_trace",
        "traceback",
        "error",
        "error_code",
        "error_message",
        # raw plate / slate paths
        "plate_path",
        "slate_path",
        "node_path",
        "path",
        # transport / rollout strategy (banned by contract)
        "reload_policy",
        "full_snapshot_until_pux_r4",
        "cursor_only",
        "delivery_strategy",
        "rollout",
    }
)


class RepresentationPayloadError(Exception):
    """Raised when a representation event payload violates the contract."""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_representation_payload(
    *,
    representation_section: str,
    operation: str,
    generation: int,
    base_id: str,
    target_keys: Sequence[str],
) -> dict[str, Any]:
    """Build and validate a representation event payload.

    Returns a validated dict suitable for ``reader_events.payload_json``.
    Raises :class:`RepresentationPayloadError` on any violation.
    """
    _validate_section_and_operation(representation_section, operation)
    _validate_generation(generation)
    _validate_base_id(base_id)
    target_keys_list = _validate_target_keys(target_keys, representation_section)

    payload: dict[str, Any] = {
        "schema_version": REPRESENTATION_PAYLOAD_SCHEMA_VERSION,
        "representation_section": representation_section,
        "operation": operation,
        "generation": generation,
        "base_id": base_id,
        "target_keys": target_keys_list,
    }
    # Full validation includes redaction + byte-size check.
    validate_representation_payload(payload)
    return payload


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_representation_payload(payload: Mapping[str, Any]) -> None:
    """Validate a representation event payload fail-closed.

    Raises :class:`RepresentationPayloadError` on any violation.
    """
    if not isinstance(payload, Mapping):
        raise RepresentationPayloadError("payload must be a mapping")

    # Redaction: fail-closed on forbidden keys.
    for key in payload:
        if key in FORBIDDEN_PAYLOAD_KEYS:
            raise RepresentationPayloadError(
                f"forbidden key {key!r} in representation payload"
            )

    # Only the canonical fields are allowed — reject extra fields.
    allowed_top_level = {
        "schema_version",
        "representation_section",
        "operation",
        "generation",
        "base_id",
        "target_keys",
    }
    extra_keys = set(payload.keys()) - allowed_top_level
    if extra_keys:
        raise RepresentationPayloadError(
            f"unexpected payload keys: {sorted(extra_keys)}"
        )

    # schema_version
    if payload.get("schema_version") != REPRESENTATION_PAYLOAD_SCHEMA_VERSION:
        raise RepresentationPayloadError(
            f"schema_version must be {REPRESENTATION_PAYLOAD_SCHEMA_VERSION}, "
            f"got {payload.get('schema_version')!r}"
        )

    # representation_section + operation
    section = payload.get("representation_section")
    _validate_section_and_operation(section, payload.get("operation"))

    # generation
    _validate_generation(payload.get("generation"))

    # base_id
    _validate_base_id(payload.get("base_id"))

    # target_keys
    _validate_target_keys(payload.get("target_keys"), section)

    # Serialized byte-size limit.
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise RepresentationPayloadError(
            f"payload size {len(serialized)} bytes exceeds limit {MAX_PAYLOAD_BYTES}"
        )


# ---------------------------------------------------------------------------
# Internal field validators
# ---------------------------------------------------------------------------


def _validate_section_and_operation(section: object, operation: object) -> None:
    if section not in ALLOWED_SECTIONS:
        raise RepresentationPayloadError(
            f"unknown representation_section: {section!r}"
        )
    assert isinstance(section, str)  # narrowing — ALLOWED_SECTIONS contains str
    allowed_ops = ALLOWED_OPERATIONS_BY_SECTION.get(section, frozenset())
    if operation not in allowed_ops:
        raise RepresentationPayloadError(
            f"operation {operation!r} not allowed for section {section!r}; "
            f"allowed: {sorted(allowed_ops)}"
        )


def _validate_generation(generation: object) -> None:
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise RepresentationPayloadError(
            f"generation must be a positive int, got {generation!r}"
        )
    if generation < 1:
        raise RepresentationPayloadError(
            f"generation must be >= 1, got {generation}"
        )


def _validate_base_id(base_id: object) -> None:
    if not isinstance(base_id, str) or not base_id:
        raise RepresentationPayloadError(
            f"base_id must be a non-empty string, got {base_id!r}"
        )


def _validate_target_keys(
    target_keys: object,
    section: str,
) -> list[str]:
    if not isinstance(target_keys, list | tuple):
        raise RepresentationPayloadError(
            f"target_keys must be a list, got {type(target_keys).__name__}"
        )
    if len(target_keys) == 0:
        raise RepresentationPayloadError("target_keys must not be empty")
    if len(target_keys) > MAX_TARGET_KEYS:
        raise RepresentationPayloadError(
            f"target_keys count {len(target_keys)} exceeds limit {MAX_TARGET_KEYS}"
        )
    result: list[str] = []
    for key in target_keys:
        if not isinstance(key, str) or not key:
            raise RepresentationPayloadError(
                "each target_key must be a non-empty string"
            )
        if len(key) > MAX_KEY_LENGTH:
            raise RepresentationPayloadError(
                f"target_key length {len(key)} exceeds limit {MAX_KEY_LENGTH}"
            )
        result.append(key)

    if section == "record_metadata":
        for key in result:
            if key not in ALLOWED_METADATA_FIELDS:
                raise RepresentationPayloadError(
                    f"metadata field {key!r} not in allowlist; "
                    f"allowed: {sorted(ALLOWED_METADATA_FIELDS)}"
                )
    return result
