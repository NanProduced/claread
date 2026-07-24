"""Frozen version literals + forbidden key markers for the parse-eval artifact.

Centralised so that :mod:`.schema`, :mod:`.fixture_builder`,
:mod:`.reader_adapter`, and :mod:`.gate` all reference the same
constants without circular imports.
"""

from __future__ import annotations

import re
from typing import Literal

# ---------------------------------------------------------------------------
# Schema + producer version constants
# ---------------------------------------------------------------------------

#: Frozen schema version literal. Bumping this string is a contract
#: change and requires a new ``reader_parse_eval_artifact.vN`` module.
ARTIFACT_SCHEMA_VERSION: Literal["reader_parse_eval_artifact.v1"] = (
    "reader_parse_eval_artifact.v1"
)

#: Frozen producer semantic version. Bumped only when the producer
#: algorithm changes in a way that alters the artifact bytes for the
#: same input. The ``artifact_id`` derivation includes this string so
#: a producer bump automatically invalidates old fixture hashes.
PRODUCER_SEMANTIC_VERSION: str = "v1"

#: Full producer version string (module identity + semantic version).
#: Recorded in :class:`~.schema.ArtifactProvenance`.
PRODUCER_VERSION: str = "reader_parse_eval_artifact_producer_v1"

#: Frozen deterministic clock token for fixture-grade production.
#: Replaces wall-clock ``datetime.now()`` so two consecutive runs on
#: the same fixed sample produce byte-identical JSON.
DEFAULT_DETERMINISTIC_CLOCK_TOKEN: str = "deterministic-fixture-v1"

#: Frozen pipeline-runner version label recorded in orchestration
#: provenance for fixture-grade artifacts.
FIXTURE_PIPELINE_RUNNER_VERSION: str = "fixture_pipeline_runner_v1"

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

#: SHA-256 lowercase hex pattern.
SHA256_LOWERCASE_HEX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")

#: FNV-1a32 lowercase hex pattern (8 hex chars). Mirrors the Reader
#: anchor segment text-hash contract.
FNV1A32_LOWERCASE_HEX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{8}$")

#: All-zeros SHA-256 (used as a regression negative in the gate).
ZERO_SHA256: str = "0" * 64

#: All-zeros FNV-1a32 (used as a regression negative in the gate).
ZERO_FNV1A32: str = "0" * 8

# ---------------------------------------------------------------------------
# Forbidden key markers
# ---------------------------------------------------------------------------

#: Forbidden key substrings that MUST NOT appear as field names or in
#: the top-level payload shape of the serialized artifact JSON. The
#: gate scans the serialized JSON **keys** (not free-form text values
#: like ``notes`` or ``unavailable_reason``) for these substrings and
#: fails closed if any are present as keys.
#:
#: This is defense-in-depth on top of the closed-schema Pydantic
#: boundary. The scan is key-only per the Task 5A-R1 spec: "forbidden
#: 检查只针对 key / 非法 payload shape，不扫描用户文本或 notes".
FORBIDDEN_KEY_MARKERS: frozenset[str] = frozenset(
    {
        "render_scene_json",
        "render_scene",
        "plate_value",
        "plateValue",
        "legacy_task",
        "legacyTask",
        "legacy_record",
        "legacyRecord",
        "prompt_template",
        "prompt_template_version",
        "llm_response",
        "llm_response_raw",
        "model_metadata",
        "trace_span",
        "trace_payload",
    }
)
