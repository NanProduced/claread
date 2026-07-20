"""Reading Record Ask agent runtime — run loop entry (not production stream).

Does not wire into ``service.py`` / SSE / turn persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.models import Model

from app.services.reader_record_ask.agent import (
    build_agent_user_prompt,
    create_reading_record_ask_agent,
)
from app.services.reader_record_ask.answer_correctness_policy import (
    build_answer_correctness_policy,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.baseline_context import (
    BaselineAgentContext,
    BaselineContextAssembler,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import DocumentAccess
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceFn, StaticGenerationFence
from app.services.reader_record_ask.finalizer import (
    AgentAnswerDraft,
    FinalizedAskResult,
    finalize_agent_answer,
)
from app.services.reader_record_ask.initial_anchor_evidence import (
    register_initial_anchor_evidence,
)
from app.services.reader_record_ask.read_range_executor import (
    DEFAULT_MAX_READ_RANGE_CALLS,
)
from app.services.reader_record_ask.runtime_deps import (
    ReaderRecordAskDeps,
    RuntimeObservation,
)
from app.services.reader_record_ask.runtime_events import (
    ComposingAnswerEvent,
    FinalAnswerEvent,
    RunFinishedEvent,
    RunStartedEvent,
    RuntimeEvent,
    RuntimeEventSink,
    ValidatingEvidenceEvent,
)
from app.services.reader_record_ask.search_current_article_executor import (
    DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
)


@dataclass(slots=True)
class ReadingRecordAskRunResult:
    """Outcome of one independent agent run (including finalizer).

    ``baseline_context`` carries the typed baseline assembly result. When
    ``baseline_context.baseline_status != "injected"`` the run fail-closed
    before invoking the agent: ``final_text`` is ``None``, ``finalized`` is
    ``None``, and ``agent_draft`` is ``None``. Callers (production stream)
    must emit a terminal event, never an ok completed.
    """

    final_text: str | None
    events: list[RuntimeEvent] = field(default_factory=list)
    read_range_calls: int = 0
    search_current_article_calls: int = 0
    evidence_observations: tuple[ServerEvidenceObservation, ...] = ()
    initial_anchor_handle: EvidenceHandleRef | None = None
    agent_draft: AgentAnswerDraft | None = None
    finalized: FinalizedAskResult | None = None
    agent_output: Any = None
    baseline_context: BaselineAgentContext | None = None


async def run_reading_record_ask(
    *,
    user_message: str,
    envelope: ReadingRecordAskContextEnvelope,
    document_access: DocumentAccess,
    model: Model | str,
    fence: FenceFn | None = None,
    evidence_registry: EvidenceRegistry | None = None,
    article_rag: ArticleRagSearchPort | None = None,
    max_read_range_calls: int = DEFAULT_MAX_READ_RANGE_CALLS,
    max_search_current_article_calls: int = DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
    event_sink: RuntimeEventSink | None = None,
    observation: RuntimeObservation | None = None,
) -> ReadingRecordAskRunResult:
    """Run the independent Reading Record Ask agent once, then finalize.

    The model decides whether to call ``read_range`` / ``search_current_article``;
    there is no keyword routing or article/RAG prefetch.

    Baseline article context is assembled before the agent run so the model
    can see the article text (short articles: full text; medium/long:
    deterministic first-N-units) even when RAG is off and there is no user
    selection. Baseline failure is typed fail-closed: the agent is not
    invoked, no pseudo-success completed is produced.

    ``event_sink`` is an optional live observation hook used by production
    stream for concurrent progress projection. Events are always retained on
    ``deps.events`` for tests and final diagnostics.

    R4-A4-2R5R2 Task 1+2: ``observation`` is an internal-only mutable
    :class:`RuntimeObservation` container. When non-None, the runtime
    writes ``baseline_context`` after assembly succeeds (BEFORE the
    ``is_injected`` check, so both ``captured`` and ``unavailable``
    states are observable), and the grounding output_validator
    increments TWO PRECISE counters: ``output_validation_final_attempts``
    on every FINAL-mode call (partial-mode calls do NOT touch it), and
    ``output_validation_retry_requests`` ONLY when ``ModelRetry`` is
    raised in final mode (a normal pass does NOT increment it). The
    harness reads all three fields on the success path and the
    exception path to preserve actual baseline audit data when the
    agent raises mid-flight, and to classify
    ``UnexpectedModelBehavior`` as ``output_retry_exhausted`` (retry
    budget proven exhausted — requires BOTH counters to EXACTLY equal
    ``DEFAULT_OUTPUT_RETRIES + 1``) vs.
    ``unexpected_model_behavior`` (conservative fallback — counters
    missing/unequal/undersized/oversized, or partial-only calls
    inflated the count). Production callers pass ``None`` (default) —
    no observation is recorded, no overhead, no DTO/SSE/Web/persistence
    contract change. The runtime never READS from the container, so
    the run result is structurally unaffected by observation.
    """
    if evidence_registry is not None:
        if evidence_registry.envelope_fingerprint != envelope.envelope_fingerprint:
            raise ValueError(
                "evidence_registry envelope_fingerprint does not match the "
                "turn envelope; construct a registry bound to this envelope"
            )
        registry = evidence_registry
    else:
        registry = EvidenceRegistry(envelope.envelope_fingerprint)

    initial_handle = register_initial_anchor_evidence(
        envelope=envelope,
        registry=registry,
    )

    active_fence: FenceFn = fence or StaticGenerationFence(
        live_generation=envelope.record_generation
    )
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=document_access,
        fence=active_fence,
        evidence_registry=registry,
        article_rag=article_rag,
        max_read_range_calls=max_read_range_calls,
        max_search_current_article_calls=max_search_current_article_calls,
        event_sink=event_sink,
        observation=observation,
    )

    projection = envelope.to_agent_projection()
    deps.emit_event(
        RunStartedEvent(
            envelope_fingerprint=envelope.envelope_fingerprint,
            has_initial_selection=projection.has_initial_selection,
        )
    )

    # Baseline context assembly — must happen before agent.run so the model
    # sees the article text on the first turn. Fail-closed: when the baseline
    # cannot be assembled, the agent is not invoked and no pseudo-success
    # completed is produced. Production stream maps finalized=None +
    # final_text=None to a terminal event.
    # R4-A4-2R5R3 Issue #1: write execution_stage BEFORE assembly runs so
    # the harness can disambiguate a ``ValidationError`` raised during
    # assembly (``baseline_assembly`` → ``runtime_exception``) from one
    # raised inside the validator-owned ``output_validation`` stage.
    # Other ``agent_run`` errors remain conservative runtime failures.
    # ``None`` in production.
    if observation is not None:
        observation.execution_stage = "baseline_assembly"
    assembler = BaselineContextAssembler(
        envelope=envelope,
        document_access=document_access,
        registry=registry,
    )
    baseline = await assembler.assemble_baseline()

    # R4-A4-2R5R Task 1: internal-only typed observation seam. Write
    # the assembled baseline to the observation container BEFORE the
    # ``is_injected`` check so both ``captured`` (chunks present) and
    # ``unavailable`` (assembly succeeded but 0 chunks) states are
    # observable by the harness. When assembly itself raises, this
    # write is never reached and ``observation.baseline_context`` stays
    # ``None`` (``failed`` state) — matching the fail-closed contract.
    # The runtime never READS from the container, so the run result is
    # structurally unaffected. ``observation`` is ``None`` in
    # production — no overhead.
    if observation is not None:
        observation.baseline_context = baseline

    # Tell the output_validator whether ``response_kind="unavailable"`` is
    # permitted. Only allowed when baseline is NOT available. Internal-only;
    # never serialised, never enters public DTO or persistence.
    deps.baseline_available = baseline.is_injected

    if not baseline.is_injected:
        # Typed fail-closed: map baseline failure to an internal
        # FinalizedAskResult(status="unavailable") so production_stream can
        # emit a typed terminal (not "missing_finalizer_result"). The
        # reason is one of two stable values so the wire terminal_reason
        # stays typed and audit-friendly. Do NOT invoke the agent, do not
        # emit composing/validating events, do not produce a pseudo-success
        # completed.
        baseline_reason = (
            "document_unavailable"
            if baseline.baseline_status == "document_scope_unavailable"
            else "baseline_unavailable"
        )
        finalized = FinalizedAskResult(
            status="unavailable",
            answer_text=None,
            resolved_evidence=(),
            rejected_handles=(),
            reason=baseline_reason,
            envelope_fingerprint=envelope.envelope_fingerprint,
        )
        deps.emit_event(
            RunFinishedEvent(
                read_range_calls=0,
                evidence_count=len(registry),
                search_current_article_calls=0,
            )
        )
        return ReadingRecordAskRunResult(
            final_text=None,
            events=list(deps.events),
            read_range_calls=0,
            search_current_article_calls=0,
            evidence_observations=registry.list_observations(),
            initial_anchor_handle=initial_handle,
            agent_draft=None,
            finalized=finalized,
            agent_output=None,
            baseline_context=baseline,
        )

    # R4-A4-1B: construct the answer-correctness policy once after baseline
    # assembly and write-once assign to deps. The grounding output_validator
    # reads it on every final draft, and R4-A4-1C renders
    # ``render_prompt_block()`` from this same instance for the user prompt.
    # Uses the model-visible chunk text tuple (NOT registry scan / snippets
    # / citation handles). The fail-closed path above returns early,
    # leaving deps.answer_correctness_policy at its default ``None``; the
    # agent is not invoked in that path so no prompt is composed.
    deps.answer_correctness_policy = build_answer_correctness_policy(
        user_message=user_message,
        model_visible_chunk_texts=tuple(
            chunk.text for chunk in baseline.model_context_chunks
        ),
        baseline_is_complete=baseline.is_complete,
    )

    # Available handles for the model = initial_anchor (if any) + baseline
    # seed handles (1:1 with chunks). We deliberately do NOT scan the whole
    # registry here: tool-created handles are returned to the model via tool
    # results, and the baseline seed handles come from
    # ``baseline.available_seed_handle_ids`` so the set of citable seed
    # handles is exactly the set of chunks the model has seen text for.
    available_handles: list[str] = []
    if initial_handle is not None:
        available_handles.append(initial_handle.handle_id)
    available_handles.extend(baseline.available_seed_handle_ids)
    agent = create_reading_record_ask_agent(model)
    # R4-A4-1C: render the turn-specific correctness block from the same
    # policy instance the grounding output_validator will use (write-once
    # convention). The block is the ONLY place turn-specific year allowset,
    # completeness constraint, and explicit exercise count enter the user
    # prompt. ``deps.answer_correctness_policy`` is guaranteed non-None here
    # because the fail-closed path above returned early when baseline
    # assembly failed.
    correctness_block = deps.answer_correctness_policy.render_prompt_block()
    prompt = build_agent_user_prompt(
        user_message=user_message,
        agent_context_json=json.dumps(
            projection.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        ),
        available_evidence_handle_ids=available_handles,
        model_context_chunks=baseline.model_context_chunks,
        baseline_is_complete=baseline.is_complete,
        correctness_block=correctness_block,
    )
    # Mark the broad agent-loop stage before awaiting ``agent.run()``.
    # The grounding validator temporarily narrows this to
    # ``output_validation``; only that nested stage proves an exception
    # originated from our validator. ``None`` in production.
    if observation is not None:
        observation.execution_stage = "agent_run"
    result = await agent.run(prompt, deps=deps)
    # R4-A4-2R5R3 Issue #1: write execution_stage AFTER ``agent.run()``
    # returns successfully and BEFORE the finalizer starts. A
    # ``ValidationError`` raised AFTER this transition did NOT come
    # from the output validator — it came from the finalizer or later
    # code, and MUST be classified as ``runtime_exception``. This is
    # the precise typed seam the harness classifier reads to
    # disambiguate validator-stage vs finalizer-stage ValidationErrors.
    # ``None`` in production.
    if observation is not None:
        observation.execution_stage = "agent_run_completed"
    draft = result.output
    if not isinstance(draft, AgentAnswerDraft):
        draft = AgentAnswerDraft(
            answer_text=str(draft),
            cited_evidence_handles=[],
            response_kind="grounded_answer",
        )

    # Pre-finalizer activity: composing, then validating. FinalAnswerEvent only
    # after finalize returns so UI "正在核对回答依据" matches real work.
    deps.emit_event(ComposingAnswerEvent())
    deps.emit_event(ValidatingEvidenceEvent())

    # R4-A4-2R5R3 Issue #1: write execution_stage BEFORE
    # ``finalize_agent_answer()`` is awaited. A ``ValidationError``
    # raised while this stage is current is conservatively classified
    # as ``runtime_exception`` — it did not come from the output
    # validator. ``None`` in production.
    if observation is not None:
        observation.execution_stage = "finalizer"
    finalized = await finalize_agent_answer(
        envelope=envelope,
        registry=registry,
        draft=draft,
        fence=active_fence,
    )

    final_text = finalized.answer_text if finalized.status == "ok" else None
    deps.emit_event(
        FinalAnswerEvent(text=final_text or f"[{finalized.status}] {finalized.reason or ''}")
    )
    deps.emit_event(
        RunFinishedEvent(
            read_range_calls=deps.read_range_calls,
            evidence_count=len(registry),
            search_current_article_calls=deps.search_current_article_calls,
        )
    )
    return ReadingRecordAskRunResult(
        final_text=final_text,
        events=list(deps.events),
        read_range_calls=deps.read_range_calls,
        search_current_article_calls=deps.search_current_article_calls,
        evidence_observations=registry.list_observations(),
        initial_anchor_handle=initial_handle,
        agent_draft=draft,
        finalized=finalized,
        agent_output=result.output,
        baseline_context=baseline,
    )
