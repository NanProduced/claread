"""Structured provenance output validation for Reading Record Ask.

The model supplies semantic answer blocks. The host projects the current
turn's registered evidence and confirmed article coverage into the canonical
block provenance validator, then attaches its immutable result privately.
Failures raise ``ModelRetry``; no block is reclassified or silently repaired.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from app.services.reader_record_ask.answer_block_provenance import (
    AnswerBlockBasis,
    AnswerBlockDraft,
    ArticleScope,
    EvidenceValidationContext,
    KnowledgeMode,
    ValidatedAnswerBlocks,
    ValidatedEvidence,
    validate_answer_blocks,
)
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps

# Hard cap on the number of cited evidence handles per answer. The model
# is prompted to return the MINIMAL sufficient set; exceeding this cap is
# a correctable output error (ModelRetry), never silently truncated by
# the finalizer.
MAX_CITED_EVIDENCE_HANDLES: Final[int] = 6

_EVIDENCE_KIND_TO_SOURCE_KIND: Final[dict[str, Literal["article"]]] = {
    "initial_anchor": "article",
    "read_range": "article",
    "search_hit": "article",
    "observation": "article",
    "article_seed": "article",
}


class AgentAnswerBlockOutput(BaseModel):
    """Thin model-output adapter for one provenance-explicit answer block."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=8_000)
    basis: AnswerBlockBasis
    article_scope: ArticleScope | None
    evidence_handles: list[str] = Field(default_factory=list)

    def to_block_draft(self) -> AnswerBlockDraft:
        """Project model syntax into the canonical provenance block draft."""

        return AnswerBlockDraft(
            text=self.text,
            basis=self.basis,
            article_scope=self.article_scope,
            evidence_handles=tuple(self.evidence_handles),
        )


class AgentAnswerDraftOutput(BaseModel):
    """Model-visible answer shape; host-derived fields stay private."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    response_kind: Literal["grounded_answer", "clarification"]
    clarification_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=8_000,
    )
    answer_blocks: list[AgentAnswerBlockOutput] = Field(default_factory=list)
    _validated_answer_blocks: ValidatedAnswerBlocks | None = PrivateAttr(
        default=None
    )

    @model_validator(mode="after")
    def _validate_response_kind_shape(self) -> AgentAnswerDraftOutput:
        if self.response_kind == "clarification":
            if self.clarification_text is None:
                raise ValueError("clarification requires clarification_text")
            if self.answer_blocks:
                raise ValueError("clarification requires answer_blocks=[]")
            return self

        if self.clarification_text is not None:
            raise ValueError("grounded_answer requires clarification_text=null")
        if not self.answer_blocks:
            raise ValueError("grounded_answer requires at least one answer block")
        return self

    @property
    def validated_answer_blocks(self) -> ValidatedAnswerBlocks | None:
        """Return host validation output; clarifications intentionally have none."""

        return self._validated_answer_blocks

    @property
    def knowledge_mode(self) -> KnowledgeMode | None:
        """Expose only the host-derived mode, never a model input field."""

        validated = self._validated_answer_blocks
        return validated.knowledge_mode if validated is not None else None

    @property
    def answer_text(self) -> str:
        """Internal compatibility view; never accepted as model input."""

        if self.response_kind == "clarification":
            return self.clarification_text or ""
        return "\n\n".join(block.text for block in self.answer_blocks)

    @property
    def cited_evidence_handles(self) -> list[str]:
        """Internal compatibility view; never accepted as model input."""

        return [
            handle_id
            for block in self.answer_blocks
            for handle_id in block.evidence_handles
        ]

    def bind_validated_answer_blocks(
        self,
        validated: ValidatedAnswerBlocks,
    ) -> None:
        """Attach host-only validation output without changing model schema."""

        if self.response_kind != "grounded_answer":
            raise ValueError("clarification cannot bind validated answer blocks")
        self._validated_answer_blocks = validated


def build_evidence_validation_context(
    deps: ReaderRecordAskDeps,
) -> EvidenceValidationContext:
    """Project the current registry and coverage into the canonical context."""

    evidence: list[ValidatedEvidence] = []
    for observation in deps.evidence_registry.list_observations():
        evidence_kind = str(observation.handle.kind)
        source_kind = _EVIDENCE_KIND_TO_SOURCE_KIND.get(evidence_kind)
        if source_kind is None:
            raise ValueError(
                f"unsupported evidence kind for v1 provenance: {evidence_kind!r}"
            )
        evidence.append(
            ValidatedEvidence(
                handle_id=observation.handle.handle_id,
                source_kind=source_kind,
                envelope_id=observation.handle.envelope_fingerprint,
                publicly_mappable=bool(
                    (observation.snippet and observation.snippet.strip())
                    or observation.unit_id
                    or observation.anchor_segment_id
                    or observation.rag_citation
                ),
            )
        )
    return EvidenceValidationContext(
        envelope_id=deps.envelope.envelope_fingerprint,
        evidence=tuple(evidence),
        confirmed_article_scopes=deps.confirmed_article_scopes,
    )


async def grounding_validator(
    ctx: RunContext[ReaderRecordAskDeps],
    draft: AgentAnswerDraftOutput,
) -> AgentAnswerDraftOutput:
    """Pydantic AI output validator for the Reading Record Ask agent.

    Called on both partial and final structured output. Partial mode only
    checks ``response_kind`` is parseable; final mode does full grounding.

    Raises ``ModelRetry`` on correctable failures (counted against
    ``retries["output"]``). Never silently truncates evidence or repairs
    citation scope — those are finalizer responsibilities.

    R4-A4-2R5R2 Task 1: PRECISE retry evidence. The observation
    container (when attached) now tracks TWO counters:

    - ``output_validation_final_attempts``: incremented ONLY when the
      validator is called in FINAL mode (``partial_output=False``).
      Partial-mode calls do NOT touch this counter. Incremented BEFORE
      any validation logic so it is accurate even when ``ModelRetry``
      is raised.
    - ``output_validation_retry_requests``: incremented ONLY when the
      validator in FINAL mode actually RAISES ``ModelRetry``. A normal
      pass does NOT increment this counter. A try/except wrapper around
      ALL final-mode validation branches ensures every raise site is
      covered without scattering increments.

    The taxonomy classifier requires BOTH counters to equal
    ``DEFAULT_OUTPUT_RETRIES + 1`` (3) to classify as
    ``output_retry_exhausted``. This prevents mis-classification when
    the validator was called 3 times but only 2 raised ModelRetry (the
    3rd passed, but a subsequent non-validator UMB occurred), or when
    partial-mode calls inflated the old single counter.
    """
    observation = ctx.deps.observation

    if ctx.partial_output:
        # Partial mode: pydantic_ai allows missing required fields; we
        # only nudge the model to include response_kind early. No
        # grounding checks — the draft may be incomplete.
        #
        # R4-A4-2R5R2 Task 1: partial-mode calls do NOT increment
        # ``output_validation_final_attempts`` or
        # ``output_validation_retry_requests``. Only FINAL-mode calls
        # count toward the retry-exhaustion taxonomy.
        if not getattr(draft, "response_kind", None):
            raise ModelRetry("response_kind is required on the final draft")
        return draft

    # FINAL mode. Increment ``output_validation_final_attempts`` BEFORE
    # any validation logic so the counter is accurate even when
    # ``ModelRetry`` is raised. ``observation`` is None in production —
    # no overhead.
    previous_execution_stage = None
    if observation is not None:
        observation.output_validation_final_attempts += 1
        previous_execution_stage = observation.execution_stage
        observation.execution_stage = "output_validation"

    # R4-A4-2R5R2 Task 1: wrap ALL final-mode validation in a
    # try/except ``ModelRetry`` so we increment
    # ``output_validation_retry_requests`` on EVERY raise site —
    # covering grounding checks, handle verification, and provenance
    # checks — without scattering
    # increments at each raise. The original exception is re-raised
    # (preserving type, message, and traceback) so pydantic-ai's retry
    # budget accounting is unaffected. This is type-based (not
    # text-based) — we catch ``ModelRetry`` by type, never parse
    # ``str(exc)``.
    try:
        validated_draft = await _grounding_validator_final_body(ctx, draft)
    except ModelRetry:
        if observation is not None:
            observation.output_validation_retry_requests += 1
            observation.execution_stage = previous_execution_stage
        raise
    else:
        if observation is not None:
            observation.execution_stage = previous_execution_stage
        return validated_draft


async def _grounding_validator_final_body(
    ctx: RunContext[ReaderRecordAskDeps],
    draft: AgentAnswerDraftOutput,
) -> AgentAnswerDraftOutput:
    """Final-mode validation body. Raises ``ModelRetry`` on correctable
    failures; the caller's try/except wrapper increments the retry
    request counter.

    Extracted from :func:`grounding_validator` so the try/except wrapper
    can cover ALL raise sites with a single increment. This function
    MUST NOT catch ``ModelRetry`` — every raise must propagate to the
    wrapper.
    """
    response_kind = draft.response_kind
    if response_kind not in ("grounded_answer", "clarification"):
        raise ModelRetry(
            f"response_kind must be grounded_answer|clarification, "
            f"got {response_kind!r}"
        )

    if draft.response_kind == "clarification":
        return draft

    try:
        blocks = tuple(block.to_block_draft() for block in draft.answer_blocks)
        handle_ids = [
            handle_id
            for block in blocks
            for handle_id in block.evidence_handles
        ]
        if len(handle_ids) > MAX_CITED_EVIDENCE_HANDLES:
            raise ValueError(
                f"answer may cite at most {MAX_CITED_EVIDENCE_HANDLES} evidence handles"
            )
        if len(handle_ids) != len(set(handle_ids)):
            raise ValueError(
                "remove duplicate handles; duplicate evidence handles are not allowed"
            )
        validated = validate_answer_blocks(
            blocks=blocks,
            evidence_context=build_evidence_validation_context(ctx.deps),
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from None

    draft.bind_validated_answer_blocks(validated)

    return draft
