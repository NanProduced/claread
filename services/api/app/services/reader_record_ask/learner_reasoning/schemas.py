"""Legacy learner-reasoning snapshot schemas (read compatibility only).

Retained solely so :func:`validate_cold_learner_payload` can fail-closed
validate historical ``reasoning_projection_json`` payloads persisted under
policy ``learner_reasoning_v1`` from before the provider-reasoning
migration. The projector / sidecar execution chain that once consumed the
other constants and models here has been physically removed; do not add
new runtime consumers to this module.
"""

from __future__ import annotations

from typing import Literal

LEARNER_REASONING_POLICY_VERSION: str = "learner_reasoning_v1"
LEARNER_REASONING_SCHEMA_VERSION: int = 1

LearnerReasoningStage = Literal[
    "analyzing",
    "article",
    "web",
    "synthesizing",
]

# Model output bound (Chinese grapheme-ish char count enforced by Host).
TEXT_ZH_MAX_CHARS: int = 80
TEXT_ZH_MIN_CHARS: int = 4


__all__ = [
    "LEARNER_REASONING_POLICY_VERSION",
    "LEARNER_REASONING_SCHEMA_VERSION",
    "LearnerReasoningStage",
    "TEXT_ZH_MAX_CHARS",
    "TEXT_ZH_MIN_CHARS",
]
