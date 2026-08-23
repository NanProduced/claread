"""Legacy learner-reasoning snapshot read compatibility.

The learner-reasoning projector / sidecar execution chain
(projector, worker, router, capacity, buffer, scrub, sidecar) has been
physically removed from the production path: raw provider reasoning now
flows exclusively through ``reasoning_projection.ProviderReasoningObserver``.
This package retains ONLY the schemas and the fail-closed cold
validator needed to read historical ``reasoning_projection_json`` payloads
persisted under policy ``learner_reasoning_v1``. No execution code lives
here and nothing in this package performs model or provider I/O.
"""

from __future__ import annotations

from app.services.reader_record_ask.learner_reasoning.schemas import (
    LEARNER_REASONING_POLICY_VERSION,
    LearnerReasoningStage,
)

__all__ = [
    "LEARNER_REASONING_POLICY_VERSION",
    "LearnerReasoningStage",
]
