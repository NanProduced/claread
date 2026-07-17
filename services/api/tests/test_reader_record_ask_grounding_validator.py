"""Tests for the Reading Record Ask grounding output_validator.

Covers R4-A2 must-test scenarios 1–7, 11–15 (scenarios 8–10, 16 live in
test_reader_record_ask_baseline_context.py and test_reader_record_ask_production_stream.py).

The validator is invoked directly with a lightweight ``RunContext`` mock
(``SimpleNamespace``) — no LLM calls, no ``agent.run``. This keeps the
tests deterministic and focused on the grounding contract.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelRetry

from app.services.reader_record_ask.agent import (
    _SYSTEM_INSTRUCTIONS,
    _render_coverage_block,
    build_agent_user_prompt,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.evidence import (
    build_server_evidence_observation,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import StaticGenerationFence
from app.services.reader_record_ask.finalizer import (
    AgentAnswerDraft,
    FinalizedAskResult,
    finalize_agent_answer,
)
from app.services.reader_record_ask.grounding_validator import (
    CORE_GROUNDED_QUESTION_HINTS,
    MAX_CITED_EVIDENCE_HANDLES,
    grounding_validator,
)
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Two envelopes with different identity → different fingerprints. The
# fingerprint is computed by build_context_envelope; we capture it after
# construction so registry binding matches the envelope exactly.
_USER_1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
_RECORD_1 = uuid.UUID("00000000-0000-0000-0000-000000000002")
_BASE_1 = uuid.UUID("00000000-0000-0000-0000-000000000003")
_USER_2 = uuid.UUID("00000000-0000-0000-0000-000000000010")
_RECORD_2 = uuid.UUID("00000000-0000-0000-0000-000000000020")
_BASE_2 = uuid.UUID("00000000-0000-0000-0000-000000000030")


def _envelope(
    *,
    user_id: uuid.UUID = _USER_1,
    reading_record_id: uuid.UUID = _RECORD_1,
    base_id: uuid.UUID = _BASE_1,
) -> ReadingRecordAskContextEnvelope:
    """Minimal envelope for registry binding; identity fields are stubs."""
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=user_id,
            reading_record_id=reading_record_id,
            base_id=base_id,
            record_generation=1,
            stable_document_id=None,
            base_content_sha256=None,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=None,
            visible_range=None,
        )
    )


def _registry_with_seed(
    *,
    fingerprint: str,
    count: int = 2,
) -> EvidenceRegistry:
    """Registry pre-populated with ``count`` article_seed observations."""
    registry = EvidenceRegistry(fingerprint)
    for i in range(count):
        obs = build_server_evidence_observation(
            kind="article_seed",
            envelope_fingerprint=fingerprint,
            source_tool="baseline_context",
            snippet=f"seed snippet {i}",
            unit_id=f"unit-{i}",
            anchor_segment_id=None,
        )
        registry.register(obs)
    return registry


def _ctx(
    *,
    registry: EvidenceRegistry,
    envelope: ReadingRecordAskContextEnvelope,
    baseline_available: bool = True,
    partial_output: bool = False,
) -> Any:
    """Build a minimal RunContext-like object for the validator.

    The validator only reads ``ctx.partial_output``, ``ctx.deps.evidence_registry``,
    ``ctx.deps.envelope.envelope_fingerprint``, and ``ctx.deps.baseline_available``.
    A ``SimpleNamespace`` is sufficient and avoids constructing a full
    ``RunContext`` (which requires a model + usage trackers).
    """
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=None,  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=registry,
        baseline_available=baseline_available,
    )
    return SimpleNamespace(deps=deps, partial_output=partial_output)


def _draft(
    *,
    answer_text: str = "answer",
    handles: list[str] | None = None,
    response_kind: str = "grounded_answer",
) -> AgentAnswerDraft:
    return AgentAnswerDraft(
        answer_text=answer_text,
        cited_evidence_handles=handles or [],
        response_kind=response_kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Scenario 1: grounded_answer empty handles → ModelRetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounded_answer_empty_handles_triggers_model_retry() -> None:
    """grounded_answer with no cited handles must ModelRetry (scenario 1)."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    draft = _draft(
        answer_text="answer without handles",
        handles=[],
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


# ---------------------------------------------------------------------------
# Scenario 2: unknown / cross-registry handle → ModelRetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounded_answer_unknown_handle_triggers_model_retry() -> None:
    """grounded_answer with a handle not in the registry must ModelRetry."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    fake_handle = "evh_" + ("ab" * 16)
    draft = _draft(
        answer_text="x",
        handles=[fake_handle],
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


@pytest.mark.asyncio
async def test_grounded_answer_cross_registry_handle_triggers_model_retry() -> None:
    """A handle minted under a different envelope_fingerprint must ModelRetry."""
    # Two envelopes with different identity → different fingerprints.
    # Register a seed under envelope-2, then validate against a registry
    # bound to envelope-1. The handle id is valid shape but the
    # observation is not in the target registry.
    envelope_other = _envelope(
        user_id=_USER_2,
        reading_record_id=_RECORD_2,
        base_id=_BASE_2,
    )
    other_registry = _registry_with_seed(
        fingerprint=envelope_other.envelope_fingerprint, count=1
    )
    foreign_obs = other_registry.list_observations()[0]
    foreign_handle = foreign_obs.handle.handle_id

    envelope = _envelope()
    target_registry = _registry_with_seed(
        fingerprint=envelope.envelope_fingerprint, count=1
    )
    ctx = _ctx(
        registry=target_registry,
        envelope=envelope,
        baseline_available=True,
    )
    draft = _draft(
        answer_text="x",
        handles=[foreign_handle],
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


# ---------------------------------------------------------------------------
# Scenario 3: over evidence limit → ModelRetry (no silent truncation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounded_answer_over_limit_triggers_model_retry() -> None:
    """Citing more than MAX_CITED_EVIDENCE_HANDLES must ModelRetry."""
    envelope = _envelope()
    registry = _registry_with_seed(
        fingerprint=envelope.envelope_fingerprint,
        count=MAX_CITED_EVIDENCE_HANDLES + 2,
    )
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    all_handles = [ref.handle_id for ref in registry.list_handle_refs()]
    assert len(all_handles) == MAX_CITED_EVIDENCE_HANDLES + 2
    draft = _draft(
        answer_text="x",
        handles=all_handles,
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


# ---------------------------------------------------------------------------
# Scenario 4: baseline available + unavailable → ModelRetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_available_unavailable_triggers_model_retry() -> None:
    """unavailable is forbidden when baseline_available=True (scenario 4)."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    draft = _draft(answer_text="x", handles=[], response_kind="unavailable")
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


# ---------------------------------------------------------------------------
# Scenario 5: clarification allows empty handles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clarification_allows_empty_handles() -> None:
    """clarification with no handles must pass the validator (scenario 5)."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    draft = _draft(
        answer_text="Could you clarify...",
        handles=[],
        response_kind="clarification",
    )
    result = await grounding_validator(ctx, draft)
    assert result is draft
    assert result.response_kind == "clarification"


@pytest.mark.asyncio
async def test_clarification_with_valid_handle_passes() -> None:
    """clarification may cite a handle when present; it must be valid."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint, count=1)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    handle = registry.list_handle_refs()[0].handle_id
    draft = _draft(
        answer_text="...",
        handles=[handle],
        response_kind="clarification",
    )
    result = await grounding_validator(ctx, draft)
    assert result is draft


# ---------------------------------------------------------------------------
# Scenario 6: unavailable with handles → ModelRetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unavailable_with_handles_triggers_model_retry() -> None:
    """unavailable must NOT carry evidence handles (scenario 6)."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=False)
    handle = registry.list_handle_refs()[0].handle_id
    draft = _draft(
        answer_text="x",
        handles=[handle],
        response_kind="unavailable",
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, draft)


@pytest.mark.asyncio
async def test_unavailable_allowed_when_baseline_not_available() -> None:
    """unavailable with empty handles passes when baseline_available=False."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=False)
    draft = _draft(answer_text="x", handles=[], response_kind="unavailable")
    result = await grounding_validator(ctx, draft)
    assert result is draft
    assert result.response_kind == "unavailable"


# ---------------------------------------------------------------------------
# Scenario 7: validator determinism — repeated calls keep raising ModelRetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validator_is_deterministic_on_repeated_invalid_draft() -> None:
    """The validator raises ``ModelRetry`` on every call for the same invalid draft.

    This is a PURE-FUNCTION determinism check of the validator itself — it
    does NOT exercise the Pydantic AI retry budget or the
    ``agent.output_validator`` seam. The validator does not track retry
    state; the framework wraps it and raises ``UnexpectedModelBehavior``
    after ``retries["output"]`` is exhausted. Framework-level budget
    enforcement (including the exact call count and the
    ``UnexpectedModelBehavior`` terminal) is covered by the FunctionModel
    integration tests in ``test_reader_record_ask_agent_runtime.py``
    (``test_grounding_validator_retry_then_success_via_real_seam`` and
    ``test_grounding_validator_retry_budget_exhausted_via_real_seam``).
    """
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    draft = _draft(answer_text="x", handles=[], response_kind="grounded_answer")
    # Call the validator N times directly; it must raise ModelRetry every
    # time and never silently start passing. This proves the validator
    # won't degenerate into a no-op after repeated failures.
    for _ in range(MAX_CITED_EVIDENCE_HANDLES + 5):
        with pytest.raises(ModelRetry):
            await grounding_validator(ctx, draft)


# ---------------------------------------------------------------------------
# Scenario 11: prompt contains coverage but no identity fields
# ---------------------------------------------------------------------------


def test_agent_prompt_contains_coverage_but_not_identity() -> None:
    """The agent user prompt carries coverage status but no identity fields.

    Identity fields (record id / base id / generation / fingerprint / hash)
    must never appear in the coverage block. The block is the ONLY place
    coverage state is communicated to the model.
    """
    complete_prompt = build_agent_user_prompt(
        user_message="test",
        agent_context_json='{"has_initial_selection": false}',
        available_evidence_handle_ids=[],
        model_context_chunks=[],
        baseline_is_complete=True,
    )
    partial_prompt = build_agent_user_prompt(
        user_message="test",
        agent_context_json='{"has_initial_selection": false}',
        available_evidence_handle_ids=[],
        model_context_chunks=[],
        baseline_is_complete=False,
    )
    # Both carry a Baseline coverage section
    assert "## Baseline coverage" in complete_prompt
    assert "## Baseline coverage" in partial_prompt
    assert "Status: complete" in complete_prompt
    assert "Status: partial" in partial_prompt
    # Neither contains identity fields in the coverage block. The
    # agent_context_json may carry envelope_fingerprint (server projection)
    # but the coverage block itself must not.
    coverage_complete = _render_coverage_block(is_complete=True)
    coverage_partial = _render_coverage_block(is_complete=False)
    forbidden = (
        "envelope_fingerprint",
        "record_id",
        "reading_record_id",
        "base_id",
        "record_generation",
        "stable_document_id",
        "fingerprint",
    )
    for token in forbidden:
        assert token not in coverage_complete, f"coverage complete leaks {token!r}"
        assert token not in coverage_partial, f"coverage partial leaks {token!r}"


# ---------------------------------------------------------------------------
# Scenario 12: partial coverage forbids exhaustive negative claims
# ---------------------------------------------------------------------------


def test_agent_prompt_partial_forbids_exhaustive_negative_claims() -> None:
    """The partial coverage block must explicitly forbid exhaustive/negative claims."""
    block = _render_coverage_block(is_complete=False)
    # The block must call out the forbidden claim patterns so the model
    # is instructed not to make whole-article exhaustive/negative claims
    # without expanding coverage.
    assert "exhaustive" in block.lower() or "negative" in block.lower()
    assert "read_range" in block
    assert "search_current_article" in block


# ---------------------------------------------------------------------------
# Scenario 13: external knowledge boundary prompt contract
# ---------------------------------------------------------------------------


def test_agent_prompt_external_knowledge_boundary_contract() -> None:
    """_SYSTEM_INSTRUCTIONS must carry the external knowledge boundary contract."""
    instructions = _SYSTEM_INSTRUCTIONS
    # Article knowledge vs general knowledge section
    assert "Article knowledge vs general knowledge" in instructions
    # Must allow extension/comparison/example questions
    assert "extension" in instructions.lower() or "comparison" in instructions.lower()
    # Must require labelling external facts as general knowledge / analogy
    assert "general knowledge" in instructions.lower()
    assert "analogy" in instructions.lower()
    # Must forbid using article evidence to back external facts
    assert "article" in instructions.lower()
    assert (
        "external" in instructions.lower()
        or "not provided by the article" in instructions.lower()
    )


# ---------------------------------------------------------------------------
# Scenario 14: 5 core grounded question hints constant shape
# ---------------------------------------------------------------------------


def test_core_grounded_question_hints_constant_shape() -> None:
    """CORE_GROUNDED_QUESTION_HINTS must contain exactly the 5 required shapes."""
    expected = (
        "这篇文章主要说了什么",
        "概括核心观点",
        "作者最想说明什么",
        "文章是怎么展开论证的",
        "基于文章出一道小练习",
    )
    assert CORE_GROUNDED_QUESTION_HINTS == expected
    # Each hint is a non-empty Chinese string
    for hint in CORE_GROUNDED_QUESTION_HINTS:
        assert isinstance(hint, str)
        assert hint.strip() == hint
        assert len(hint) > 0


def test_max_cited_evidence_handles_constant_is_six() -> None:
    """MAX_CITED_EVIDENCE_HANDLES must be exactly 6 (contract lock)."""
    assert MAX_CITED_EVIDENCE_HANDLES == 6
    assert isinstance(MAX_CITED_EVIDENCE_HANDLES, int)


# ---------------------------------------------------------------------------
# Scenario 15: finalizer scope failure still returns typed terminal
#               (not swallowed by output retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalizer_scope_failure_still_returns_typed_terminal() -> None:
    """A finalizer scope-identity failure returns invalid_citations, not retried.

    The output_validator handles correctable handle/count issues. The
    finalizer handles non-retryable scope-identity failures. This test
    verifies that a registry bound to a different envelope_fingerprint
    produces a typed ``invalid_citations`` FinalizedAskResult that the
    production_stream maps to a wire terminal — it is NOT swallowed by
    the validator (the validator would have raised ModelRetry for a
    correctable issue, but scope-identity is finalizer-only).
    """
    envelope = _envelope()
    # Registry bound to a DIFFERENT fingerprint → scope mismatch
    envelope_other = _envelope(
        user_id=_USER_2,
        reading_record_id=_RECORD_2,
        base_id=_BASE_2,
    )
    foreign_registry = EvidenceRegistry(envelope_other.envelope_fingerprint)
    # Seed handle in foreign registry (shape-valid, but scope-mismatched)
    obs = build_server_evidence_observation(
        kind="article_seed",
        envelope_fingerprint=envelope_other.envelope_fingerprint,
        source_tool="baseline_context",
        snippet="x",
        unit_id="u-1",
        anchor_segment_id=None,
    )
    foreign_registry.register(obs)
    handle = foreign_registry.list_handle_refs()[0].handle_id

    draft = AgentAnswerDraft(
        answer_text="x",
        cited_evidence_handles=[handle],
        response_kind="grounded_answer",
    )
    result = await finalize_agent_answer(
        envelope=envelope,
        registry=foreign_registry,
        draft=draft,
        fence=StaticGenerationFence(live_generation=1),
    )
    assert isinstance(result, FinalizedAskResult)
    assert result.status == "invalid_citations"
    assert result.answer_text is None
    # The typed terminal reason is preserved (non-retryable)
    assert result.reason is not None


# ---------------------------------------------------------------------------
# Partial output mode: only response_kind presence is checked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_output_only_requires_response_kind_present() -> None:
    """In partial_output mode, only response_kind presence is checked.

    Partial mode allows missing required fields; the validator only nudges
    the model to include response_kind. Full grounding checks run on the
    final draft.
    """
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(
        registry=registry,
        envelope=envelope,
        baseline_available=True,
        partial_output=True,
    )
    # A draft with response_kind set passes partial mode even with empty
    # handles (which would fail final mode for grounded_answer).
    draft = _draft(answer_text="x", handles=[], response_kind="grounded_answer")
    result = await grounding_validator(ctx, draft)
    assert result is draft


@pytest.mark.asyncio
async def test_partial_output_missing_response_kind_triggers_model_retry() -> None:
    """Partial mode still rejects a draft lacking response_kind entirely.

    Pydantic AI partial validation tolerates missing required fields, so
    ``getattr(draft, "response_kind", None)`` returns None when the field
    is absent. The validator must nudge the model to include it.
    """
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint)
    ctx = _ctx(
        registry=registry,
        envelope=envelope,
        baseline_available=True,
        partial_output=True,
    )
    # Construct a draft-like object WITHOUT response_kind attribute.
    # AgentAnswerDraft itself requires response_kind, so we use a stub.
    stub = SimpleNamespace(
        answer_text="x",
        cited_evidence_handles=[],
        response_kind=None,
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx, stub)


# ---------------------------------------------------------------------------
# Validator does not mutate the draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validator_does_not_mutate_draft() -> None:
    """The validator must never silently truncate or mutate cited handles."""
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint, count=2)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    handles = [ref.handle_id for ref in registry.list_handle_refs()]
    draft = _draft(answer_text="x", handles=handles, response_kind="grounded_answer")
    original_handles = list(draft.cited_evidence_handles)
    await grounding_validator(ctx, draft)
    assert draft.cited_evidence_handles == original_handles


# ---------------------------------------------------------------------------
# Scenario 17: duplicate handles → ModelRetry (fail-closed, no silent de-dup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounded_answer_duplicate_handle_triggers_model_retry() -> None:
    """grounded_answer with a duplicate handle must ModelRetry, not de-dup.

    The validator rejects duplicates BEFORE registry resolution so the
    finalizer's silent de-dup path is never reached for correctable
    citation-quality issues. The error must be actionable (tells the
    model to remove duplicates) and must not leak body text, snippets, or
    the envelope fingerprint.
    """
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint, count=2)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    handles = [ref.handle_id for ref in registry.list_handle_refs()]
    # Cite the first handle twice → duplicate.
    dup_handles = [handles[0], handles[0], handles[1]]
    draft = _draft(
        answer_text="x",
        handles=dup_handles,
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry) as exc_info:
        await grounding_validator(ctx, draft)
    msg = str(exc_info.value)
    # Actionable guidance present.
    assert "remove duplicate handles" in msg
    # No internal data leakage.
    assert envelope.envelope_fingerprint not in msg
    assert "snippet" not in msg.lower()
    # The validator must NOT have mutated the draft.
    assert draft.cited_evidence_handles == dup_handles


@pytest.mark.asyncio
async def test_clarification_duplicate_handle_triggers_model_retry() -> None:
    """clarification with a duplicate handle must also ModelRetry.

    The duplicate check is shared across grounded_answer and
    clarification via a single private helper, so both branches enforce
    it identically.
    """
    envelope = _envelope()
    registry = _registry_with_seed(fingerprint=envelope.envelope_fingerprint, count=1)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    handle = registry.list_handle_refs()[0].handle_id
    draft = _draft(
        answer_text="Could you clarify...",
        handles=[handle, handle],
        response_kind="clarification",
    )
    with pytest.raises(ModelRetry) as exc_info:
        await grounding_validator(ctx, draft)
    assert "remove duplicate handles" in str(exc_info.value)
    # No fingerprint leakage.
    assert envelope.envelope_fingerprint not in str(exc_info.value)


@pytest.mark.asyncio
async def test_multiple_unique_handles_pass_without_duplicate_error() -> None:
    """Multiple distinct, valid handles must pass the duplicate check.

    This guards against false positives where the duplicate helper
    incorrectly flags unique handles. Up to ``MAX_CITED_EVIDENCE_HANDLES``
    unique handles from the registry must pass cleanly.
    """
    envelope = _envelope()
    registry = _registry_with_seed(
        fingerprint=envelope.envelope_fingerprint,
        count=MAX_CITED_EVIDENCE_HANDLES,
    )
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    all_handles = [ref.handle_id for ref in registry.list_handle_refs()]
    assert len(all_handles) == MAX_CITED_EVIDENCE_HANDLES
    draft = _draft(
        answer_text="grounded on multiple handles",
        handles=all_handles,
        response_kind="grounded_answer",
    )
    result = await grounding_validator(ctx, draft)
    assert result is draft
    assert result.cited_evidence_handles == all_handles


@pytest.mark.asyncio
async def test_duplicate_check_runs_before_registry_resolution() -> None:
    """Duplicate rejection fires even when handles are not in the registry.

    If the duplicate check depended on registry resolution, a draft with
    duplicate unknown handles could slip through (or produce a confusing
    "not registered" error instead of "remove duplicates"). The
    duplicate check is a pure function of the handle list and runs
    first, so the model gets the most actionable error.
    """
    envelope = _envelope()
    # Empty registry — no handles are registered.
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    ctx = _ctx(registry=registry, envelope=envelope, baseline_available=True)
    fake = "evh_" + ("ab" * 16)
    draft = _draft(
        answer_text="x",
        handles=[fake, fake],
        response_kind="grounded_answer",
    )
    with pytest.raises(ModelRetry) as exc_info:
        await grounding_validator(ctx, draft)
    # The duplicate error fires, NOT the "not registered" error.
    assert "remove duplicate handles" in str(exc_info.value)
    assert "not registered" not in str(exc_info.value)
