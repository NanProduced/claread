"""Narrow schemas for the learner-reasoning projector path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LEARNER_REASONING_POLICY_VERSION: str = "learner_reasoning_v1"
LEARNER_REASONING_SCHEMA_VERSION: int = 1

LearnerReasoningStage = Literal[
    "analyzing",
    "article",
    "web",
    "synthesizing",
]

LearnerReasoningBasis = Literal["article", "web", "general"]

AdvanceRoundReason = Literal[
    "normal_tool_result",
    "tool_argument_retry",
    "output_validator_retry",
]

# Projector input window (chars of scrubbed private reasoning).
WINDOW_CHAR_LIMIT: int = 2_000
# Turn-local raw buffer cap (chars) — ring: newest wins, oldest dropped.
TURN_BUFFER_CHAR_CAP: int = 12_000
# Model output bound (Chinese grapheme-ish char count enforced by Host).
TEXT_ZH_MAX_CHARS: int = 80
TEXT_ZH_MIN_CHARS: int = 4
# Turn-global projector dispatch budget.
MAX_DISPATCHES_PER_TURN: int = 3
# Projector run timeout (seconds).
PROJECTOR_TIMEOUT_SECONDS: float = 6.0
PROJECTOR_MODEL_TIMEOUT_SECONDS: float = 5.0
PROJECTOR_MAX_OUTPUT_TOKENS: int = 256
# Success-path finalize: drain in-flight projector, then freeze snapshot.
# Short and configurable; CP3 is best-effort within this window.
DEFAULT_FINALIZE_GRACE_SECONDS: float = 0.75


class LearnerReasoningDraft(BaseModel):
    """Only field the projector model may emit.

    Stage / basis / identity / policy are Host-owned and never read from
    the model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text_zh: str = Field(min_length=1, max_length=240)

    @field_validator("text_zh")
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()


@dataclass(frozen=True, slots=True)
class FrozenCheckpoint:
    """Immutable buffer slice frozen at the transport event source."""

    stage: LearnerReasoningStage
    basis: tuple[LearnerReasoningBasis, ...]
    revision: int
    generation_id: int
    window_text: str
    cursor: int
    # Host-only; never logged as content.
    checkpoint_kind: Literal[
        "preliminary_analysis",
        "post_evidence",
        "pre_answer",
    ]


@dataclass(frozen=True, slots=True)
class ValidatedLearnerSummary:
    """Host-validated public summary ready for SSE / persistence."""

    text: str
    stage: LearnerReasoningStage
    basis: tuple[LearnerReasoningBasis, ...]
    revision: int
    sequence: int
    generation_id: int
    policy_version: str = LEARNER_REASONING_POLICY_VERSION


def persistence_payload_from_summary(
    summary: ValidatedLearnerSummary,
) -> dict[str, Any]:
    """Shape stored in ``reasoning_projection_json`` (policy-gated)."""
    return {
        "projection_policy_version": LEARNER_REASONING_POLICY_VERSION,
        "schema": LEARNER_REASONING_SCHEMA_VERSION,
        "text": summary.text,
        "stage": summary.stage,
        "basis": list(summary.basis),
        "revision": summary.revision,
        "sequence": summary.sequence,
        "generation_id": summary.generation_id,
        "truncated": False,
    }


__all__ = [
    "LEARNER_REASONING_POLICY_VERSION",
    "LEARNER_REASONING_SCHEMA_VERSION",
    "MAX_DISPATCHES_PER_TURN",
    "DEFAULT_FINALIZE_GRACE_SECONDS",
    "PROJECTOR_MAX_OUTPUT_TOKENS",
    "PROJECTOR_MODEL_TIMEOUT_SECONDS",
    "PROJECTOR_TIMEOUT_SECONDS",
    "TEXT_ZH_MAX_CHARS",
    "TEXT_ZH_MIN_CHARS",
    "TURN_BUFFER_CHAR_CAP",
    "WINDOW_CHAR_LIMIT",
    "AdvanceRoundReason",
    "FrozenCheckpoint",
    "LearnerReasoningBasis",
    "LearnerReasoningDraft",
    "LearnerReasoningStage",
    "ValidatedLearnerSummary",
    "persistence_payload_from_summary",
]
