"""Learner reasoning summary projector (ASK-LEARNER-REASONING-PROJECTOR-).

User-visible content is a short Chinese stage summary, never raw provider
reasoning. Raw text exists only in turn-local memory and is scrubbed before
any same-authority projector call. Failures are silent and fail-closed.
"""

from __future__ import annotations

from app.services.reader_record_ask.learner_reasoning.schemas import (
    LEARNER_REASONING_POLICY_VERSION,
    LearnerReasoningDraft,
    LearnerReasoningStage,
)
from app.services.reader_record_ask.learner_reasoning.sidecar import (
    LearnerReasoningSidecar,
    build_learner_reasoning_observer,
)

__all__ = [
    "LEARNER_REASONING_POLICY_VERSION",
    "LearnerReasoningDraft",
    "LearnerReasoningSidecar",
    "LearnerReasoningStage",
    "build_learner_reasoning_observer",
]
