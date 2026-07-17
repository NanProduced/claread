"""Evidence finalizer for Reading Record Ask.

Resolves model-cited opaque handles against the turn's
:class:`EvidenceRegistry`, re-checks the generation fence, and produces
an internal final result (no SSE / DB writes).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
    is_valid_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceFn, run_fence

# Internal-only finalize status. Wire FinalStatus (in
# reader_record_ask_stream.py) is a 5-value Literal that is NOT extended
# here. ``"unavailable"`` is an internal status used by the runtime /
# finalizer to signal document/baseline unavailability; production_stream
# maps it to wire ``final_status="failed"`` + a typed ``terminal_reason``.
FinalizeStatus = Literal[
    "ok",
    "context_stale",
    "invalid_citations",
    "unavailable",
]

# Internal-only response discriminator on AgentAnswerDraft. Never enters
# the public SSE / completed / history DTO, persistence, or Web DTO.
ResponseKind = Literal["grounded_answer", "clarification", "unavailable"]


class AgentAnswerDraft(BaseModel):
    """Structured agent output — answer text + opaque handle ids + response kind.

    ``response_kind`` is an internal-only discriminator validated by the
    agent output_validator (grounding_validator.py). It is not serialised
    into any wire DTO or persistence layer.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer_text: str = Field(min_length=1, max_length=20_000)
    cited_evidence_handles: list[str] = Field(default_factory=list)
    # Required internal discriminator. Pydantic AI partial validation
    # tolerates missing required fields during streaming; the final
    # validation path + the output_validator enforce presence + semantics.
    response_kind: ResponseKind

    @field_validator("cited_evidence_handles", mode="before")
    @classmethod
    def _coerce_handles(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("cited_evidence_handles must be a list")
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict) and "handle_id" in item:
                out.append(str(item["handle_id"]).strip())
            elif hasattr(item, "handle_id"):
                out.append(str(item.handle_id).strip())
            else:
                raise TypeError(
                    "cited_evidence_handles items must be handle id strings "
                    "or EvidenceHandleRef-like objects"
                )
        return [h for h in out if h]


class FinalizedAskResult(BaseModel):
    """Internal final result after handle resolution + fence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FinalizeStatus
    answer_text: str | None = None
    resolved_evidence: tuple[ServerEvidenceObservation, ...] = ()
    rejected_handles: tuple[str, ...] = ()
    reason: str | None = None
    envelope_fingerprint: str


async def finalize_agent_answer(
    *,
    envelope: ReadingRecordAskContextEnvelope,
    registry: EvidenceRegistry,
    draft: AgentAnswerDraft,
    fence: FenceFn,
) -> FinalizedAskResult:
    """Finalize the model draft into a fence-checked, handle-resolved result.

    Responsibility scope (R4-A2):
    - Scope identity: registry must be bound to this envelope fingerprint
      (non-retryable → ``invalid_citations``).
    - Final generation fence (non-retryable → ``context_stale``).
    - Handle resolution + envelope_fingerprint match (non-retryable
      defense-in-depth → ``invalid_citations``). Grounding checks that
      can be corrected by the model (handle existence, count limit,
      response_kind semantics) live in the output_validator, not here.
    - ``response_kind="unavailable"`` → return ``status="unavailable"``
      with no answer/evidence. This is defense in depth: the validator
      only allows ``unavailable`` when ``baseline_available=False``, but
      runtime never invokes the agent in that case. If we ever reach
      finalizer with ``unavailable``, production_stream maps to wire
      ``final_status="failed"`` + ``terminal_reason`` from the runtime.
    """
    if registry.envelope_fingerprint != envelope.envelope_fingerprint:
        return FinalizedAskResult(
            status="invalid_citations",
            reason="evidence registry is not bound to this envelope",
            envelope_fingerprint=envelope.envelope_fingerprint,
            rejected_handles=tuple(draft.cited_evidence_handles),
        )

    # Defense-in-depth: if the model emitted unavailable despite
    # baseline_available=True (validator should have rejected this), do
    # not produce a pseudo-success completed. Return typed terminal.
    if draft.response_kind == "unavailable":
        return FinalizedAskResult(
            status="unavailable",
            answer_text=None,
            resolved_evidence=(),
            rejected_handles=(),
            reason=None,
            envelope_fingerprint=envelope.envelope_fingerprint,
        )

    fence_result = await run_fence(fence, envelope)
    if not fence_result.ok:
        return FinalizedAskResult(
            status="context_stale",
            reason=fence_result.reason or "generation mismatch at finalize",
            envelope_fingerprint=envelope.envelope_fingerprint,
            rejected_handles=tuple(draft.cited_evidence_handles),
        )

    resolved: list[ServerEvidenceObservation] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for raw_id in draft.cited_evidence_handles:
        if raw_id in seen:
            # Deduplicate silently — still only one observation.
            continue
        seen.add(raw_id)

        if not is_valid_evidence_handle_id(raw_id):
            rejected.append(raw_id)
            continue
        try:
            EvidenceHandleRef(handle_id=raw_id)
        except Exception:  # noqa: BLE001
            rejected.append(raw_id)
            continue

        observation = registry.get(raw_id)
        if observation is None:
            rejected.append(raw_id)
            continue
        if (
            observation.handle.envelope_fingerprint
            != envelope.envelope_fingerprint
        ):
            rejected.append(raw_id)
            continue
        resolved.append(observation)

    if rejected:
        return FinalizedAskResult(
            status="invalid_citations",
            reason=(
                "one or more cited evidence handles are unknown, foreign, "
                "or malformed"
            ),
            rejected_handles=tuple(rejected),
            # Do not submit partial answers with bad citations.
            envelope_fingerprint=envelope.envelope_fingerprint,
        )

    return FinalizedAskResult(
        status="ok",
        answer_text=draft.answer_text,
        resolved_evidence=tuple(resolved),
        rejected_handles=(),
        envelope_fingerprint=envelope.envelope_fingerprint,
    )
