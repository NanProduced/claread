"""Pydantic AI output validator for the Reading Record Ask agent.

Deep module wired via the ``agent.output_validator(grounding_validator)``
decorator seam in :func:`create_reading_record_ask_agent`. It receives
``RunContext[ReaderRecordAskDeps]`` and the parsed ``AgentAnswerDraft``,
enforces ``response_kind`` semantics + handle existence + duplicate
handle rejection + evidence count limit, and raises ``ModelRetry``
(counted against ``retries["output"]``) on correctable failures.

Responsibility scope (design §5 frozen boundary, R4-A5-6):

- Validator does (structural / evidence contracts only): response_kind
  semantics, handle existence in registry, handle envelope_fingerprint
  match (correctable), duplicate handle rejection (correctable — model
  can remove duplicates), evidence count limit, baseline_available
  forbids unavailable.
- Validator does NOT: semantic answer-correctness heuristics (temporal /
  publication-year, numeric allowset, geo province/state/region,
  language ratio, explicit exercise-count text parsing — all migrated to
  the prompt block + typed non-retry evaluator layer), scope identity,
  final generation fence, stable document identity, citation/evidence
  public projection, typed terminal mapping, silent handle
  de-duplication. Those are non-retryable finalizer / evaluator
  responsibilities.

The validator never mutates ``draft`` and never silently truncates
``cited_evidence_handles`` — over-limit and duplicates are always a
``ModelRetry`` so the model can repair them.
"""

from __future__ import annotations

from typing import Final

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from app.services.reader_record_ask.evidence import is_valid_evidence_handle_id
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.finalizer import AgentAnswerDraft
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps

# Hard cap on the number of cited evidence handles per answer. The model
# is prompted to return the MINIMAL sufficient set; exceeding this cap is
# a correctable output error (ModelRetry), never silently truncated by
# the finalizer.
MAX_CITED_EVIDENCE_HANDLES: Final[int] = 6

# Short allowlist of article-level core question shapes that MUST receive
# ``grounded_answer`` when baseline coverage is complete. The prompt
# contract references this list; the validator does NOT pattern-match
# user input (no keyword routing). The list exists for prompt construction
# and prompt-content tests.
CORE_GROUNDED_QUESTION_HINTS: Final[tuple[str, ...]] = (
    "这篇文章主要说了什么",
    "概括核心观点",
    "作者最想说明什么",
    "文章是怎么展开论证的",
    "基于文章出一道小练习",
)


async def grounding_validator(
    ctx: RunContext[ReaderRecordAskDeps],
    draft: AgentAnswerDraft,
) -> AgentAnswerDraft:
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
    # covering grounding checks, handle verification, unavailable
    # checks, and the answer-correctness policy — without scattering
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
    draft: AgentAnswerDraft,
) -> AgentAnswerDraft:
    """Final-mode validation body. Raises ``ModelRetry`` on correctable
    failures; the caller's try/except wrapper increments the retry
    request counter.

    Extracted from :func:`grounding_validator` so the try/except wrapper
    can cover ALL raise sites with a single increment. This function
    MUST NOT catch ``ModelRetry`` — every raise must propagate to the
    wrapper.
    """
    response_kind = draft.response_kind
    if response_kind not in ("grounded_answer", "clarification", "unavailable"):
        raise ModelRetry(
            f"response_kind must be grounded_answer|clarification|unavailable, "
            f"got {response_kind!r}"
        )

    registry = ctx.deps.evidence_registry
    envelope_fingerprint = ctx.deps.envelope.envelope_fingerprint

    if response_kind == "grounded_answer":
        _check_grounded_answer(draft, registry, envelope_fingerprint)
    elif response_kind == "clarification":
        # clarification allows empty handles; if handles are present they
        # must still be valid (no fabricated citations).
        _check_handles_valid_if_present(draft, registry, envelope_fingerprint)
    else:  # unavailable
        _check_unavailable(draft, ctx.deps.baseline_available)

    # R4-A5-6: semantic answer-correctness violations (temporal /
    # publication-year, numeric allowset, geo province/state/region,
    # language-ratio, explicit-count text heuristics) no longer raise
    # ModelRetry here (design §5 frozen boundary). The policy's prompt
    # block (AnswerCorrectnessPolicy.render_prompt_block) remains the
    # ONLY model-facing surface for these constraints; typed evaluation
    # stays observable via AnswerCorrectnessPolicy.evaluate_draft (pure,
    # non-retry) for the prompt/evaluator layer. Semantic quality is
    # never repaired by silently rewriting the answer. The validator
    # keeps ONLY structural / evidence-contract retries: response_kind
    # structure, handle mint shape / registry existence / fingerprint /
    # duplicates / count cap, and unavailable ↔ baseline capability.

    return draft


# ---------------------------------------------------------------------------
# Internal helpers (not public — per design doc G.11)
# ---------------------------------------------------------------------------


def _check_grounded_answer(
    draft: AgentAnswerDraft,
    registry: EvidenceRegistry,
    envelope_fingerprint: str,
) -> None:
    if not draft.answer_text.strip():
        raise ModelRetry("grounded_answer requires non-empty answer_text")
    if not draft.cited_evidence_handles:
        raise ModelRetry(
            "grounded_answer requires at least one cited_evidence_handle; "
            "cite a handle from the server-registered list or call a tool"
        )
    if len(draft.cited_evidence_handles) > MAX_CITED_EVIDENCE_HANDLES:
        raise ModelRetry(
            f"grounded_answer cites {len(draft.cited_evidence_handles)} "
            f"handles; return only the MINIMAL sufficient set (at most "
            f"{MAX_CITED_EVIDENCE_HANDLES})"
        )
    _verify_handles_in_registry(draft.cited_evidence_handles, registry, envelope_fingerprint)


def _check_handles_valid_if_present(
    draft: AgentAnswerDraft,
    registry: EvidenceRegistry,
    envelope_fingerprint: str,
) -> None:
    if not draft.cited_evidence_handles:
        return
    if len(draft.cited_evidence_handles) > MAX_CITED_EVIDENCE_HANDLES:
        raise ModelRetry(
            f"clarification cites {len(draft.cited_evidence_handles)} "
            f"handles; at most {MAX_CITED_EVIDENCE_HANDLES} allowed"
        )
    _verify_handles_in_registry(draft.cited_evidence_handles, registry, envelope_fingerprint)


def _check_unavailable(draft: AgentAnswerDraft, baseline_available: bool) -> None:
    if draft.cited_evidence_handles:
        raise ModelRetry(
            "unavailable must not cite evidence handles; either provide a "
            "grounded_answer/clarification or omit handles"
        )
    if baseline_available:
        raise ModelRetry(
            "baseline article context is available; unavailable is not "
            "permitted — use grounded_answer or clarification instead"
        )


def _verify_handles_in_registry(
    handle_ids: list[str],
    registry: EvidenceRegistry,
    envelope_fingerprint: str,
) -> None:
    # Duplicate handles are a correctable model error — the model can
    # simply remove the duplicates. Reject BEFORE any registry resolution
    # so duplicates never reach the finalizer's silent de-dup path, and
    # so the check is safe both before and after registry lookup (the
    # helper is a pure function of the handle list).
    _reject_duplicate_handles(handle_ids)

    # Best-effort available-handle hint for retry messages. Uses the
    # existing read-only list_handle_refs() API — no new registry state.
    try:
        available = tuple(ref.handle_id for ref in registry.list_handle_refs())
    except Exception:  # noqa: BLE001 — hint must never break validation
        available = ()

    for raw_id in handle_ids:
        if not is_valid_evidence_handle_id(raw_id):
            raise ModelRetry(f"cited handle {raw_id!r} is not a valid mint-shaped handle id")
        observation = registry.get(raw_id)
        if observation is None:
            hint = f"; available handles: {sorted(available)}" if available else ""
            raise ModelRetry(
                f"cited handle {raw_id!r} is not registered in this turn's evidence registry{hint}"
            )
        if observation.handle.envelope_fingerprint != envelope_fingerprint:
            raise ModelRetry(
                f"cited handle {raw_id!r} belongs to a different turn "
                f"(envelope fingerprint mismatch)"
            )


def _reject_duplicate_handles(handle_ids: list[str]) -> None:
    """Reject duplicate entries in ``cited_evidence_handles``.

    Duplicate handles are a correctable model error: the model can simply
    remove the redundant entries. Raising ``ModelRetry`` here (counted
    against ``retries["output"]``) keeps the repair in the output
    validator and prevents the finalizer from silently de-duplicating,
    which would hide a citation-quality issue from the model.

    The error message names the duplicated handle id (which the model
    itself produced, so it is not server-internal data) and gives
    actionable guidance. It never includes answer text, snippets, the
    envelope fingerprint, or any other internal data.

    This helper is a pure function of ``handle_ids`` — it does not touch
    the registry — so it is safe to call both before and after registry
    resolution. It is invoked once at the start of
    ``_verify_handles_in_registry`` so both ``grounded_answer`` and
    ``clarification`` branches are covered without duplicating logic.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for hid in handle_ids:
        if hid in seen:
            duplicates.add(hid)
        else:
            seen.add(hid)
    if duplicates:
        dup_list = ", ".join(sorted(duplicates))
        raise ModelRetry(
            "cited_evidence_handles contains duplicate entries; "
            f"remove duplicate handles (duplicated: {dup_list})"
        )
