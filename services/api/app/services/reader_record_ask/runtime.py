"""Reading Record Ask agent runtime — run loop entry (not production stream).

R4-A5-7: constructs a :class:`TurnCoordinator`, commits the initial
model-view assembly, then runs the agent. Does not re-implement a second
state machine beyond coordinator + finalizer. Does not wire into
``service.py`` / SSE / turn persistence directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from app.services.reader_record_ask.agent import (
    _SYSTEM_INSTRUCTIONS,
    build_agent_user_prompt,
    create_reading_record_ask_agent,
)
from app.services.reader_record_ask.answer_correctness_policy import (
    build_answer_correctness_policy,
)

# Re-export for tests that monkeypatch the policy factory on this module.
__all__ = [
    "ReadingRecordAskRunResult",
    "build_answer_correctness_policy",
    "run_reading_record_ask",
]
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.baseline_context import BaselineAgentContext
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import DocumentAccess
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
)
from app.services.reader_record_ask.evidence_expansion import ExpansionPointerLedger
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceFn, StaticGenerationFence
from app.services.reader_record_ask.finalizer import (
    AgentAnswerDraft,
    FinalizedAskResult,
    finalize_agent_answer,
)
from app.services.reader_record_ask.pointer_ledger_owner import (
    get_process_pointer_ledger,
)

# M3 C2 wiring: MapSourceMaterialProvider is imported under TYPE_CHECKING to
# avoid a circular import (see turn_coordinator.py header comment for the
# cycle chain). It is only used as a type annotation here; the runtime
# instance is constructed by production_stream.py and passed in.
if TYPE_CHECKING:
    from app.services.reader_orchestration.map_source_material_provider import (
        MapSourceMaterialProvider,
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
from app.services.reader_record_ask.thinking_transport import (
    ThinkingObserver,
    run_agent_with_thinking_transport,
)
from app.services.reader_record_ask.turn_coordinator import (
    DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
    HostBudgetExhausted,
    TurnCoordinator,
)


@dataclass(slots=True)
class ReadingRecordAskRunResult:
    """Outcome of one independent agent run (including finalizer).

    ``baseline_context`` carries the typed baseline assembly result. When
    ``baseline_context.baseline_status != "injected"`` the run fail-closed
    before invoking the agent: ``final_text`` is ``None``, ``finalized`` is
    ``None`` (or unavailable), and ``agent_draft`` is ``None``.
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
    max_read_range_calls: int = 0,  # retained for call-site compat; unused
    max_search_current_article_calls: int = DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
    event_sink: RuntimeEventSink | None = None,
    observation: RuntimeObservation | None = None,
    pointer_ledger: ExpansionPointerLedger | None = None,
    thinking_observer: ThinkingObserver | None = None,
    # ASK-M1: provider completion cap + host usage guard, compiled by
    # resolve_reader_record_ask_execution from the persisted option's
    # runtime_budget. Both default to None (agent/model default) so
    # existing test callers are unaffected.
    model_settings: ModelSettings | None = None,
    usage_limits: UsageLimits | None = None,
    # M3 C2 wiring: server-only map-source material provider. When None
    # (tests / legacy callers), TurnCoordinator falls back to the unit-window
    # map (pre-C2 behavior). Production wiring constructs the provider and
    # passes it in so B3 heading enrichment (§4.2) takes effect.
    map_source_material_provider: MapSourceMaterialProvider | None = None,
) -> ReadingRecordAskRunResult:
    """Run the independent Reading Record Ask agent once, then finalize.

    Production path (R4-A5-7 / A5-8A1):
    - :class:`TurnCoordinator` owns assembly, expand, and RAG model-views;
    - tools are expand_evidence + search_current_article only;
    - ``pointer_ledger`` is injectable; production default is the
      process-scoped owner from :func:`get_process_pointer_ledger`;
    - agent runs via the stream path so ThinkingPart start/delta/snapshot
      can be captured by an optional in-memory ``thinking_observer``
      (default ``None``: zero collection). Safe analysis phase events may
      be emitted; raw reasoning never enters SSE/DTO/DB.

    ``event_sink`` / ``observation`` semantics match the prior runtime.
    """
    del max_read_range_calls  # production no longer registers read_range

    active_fence: FenceFn = fence or StaticGenerationFence(
        live_generation=envelope.record_generation
    )
    ledger = (
        pointer_ledger
        if pointer_ledger is not None
        else get_process_pointer_ledger()
    )

    if observation is not None:
        observation.execution_stage = "baseline_assembly"

    coordinator = TurnCoordinator(
        envelope=envelope,
        document_access=document_access,
        user_message=user_message,
        system_instructions=_SYSTEM_INSTRUCTIONS,
        article_rag=article_rag,
        fence=active_fence,
        evidence_registry=evidence_registry,
        pointer_ledger=ledger,
        max_search_current_article_calls=max_search_current_article_calls,
        product_search_enabled=True,
        map_source_material_provider=map_source_material_provider,
    )

    try:
        assembly = await coordinator.assemble_turn()
    except HostBudgetExhausted:
        # Request-frame (or outer) budget fail-closed before agent.run.
        raise

    baseline = assembly.baseline_context
    if observation is not None:
        observation.baseline_context = baseline

    registry = coordinator.registry
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=document_access,
        fence=active_fence,
        evidence_registry=registry,
        article_rag=article_rag,
        max_read_range_calls=0,
        max_search_current_article_calls=max_search_current_article_calls,
        event_sink=event_sink,
        observation=observation,
        turn_coordinator=coordinator,
    )

    deps.emit_event(
        RunStartedEvent(
            envelope_fingerprint=envelope.envelope_fingerprint,
            has_initial_selection=assembly.selection_result.selection.present,
        )
    )

    deps.baseline_available = baseline.is_injected

    if not baseline.is_injected:
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
            initial_anchor_handle=(
                assembly.selection_result.handle_ref
                if assembly.selection_result.status == "injected"
                else None
            ),
            agent_draft=None,
            finalized=finalized,
            agent_output=None,
            baseline_context=baseline,
        )

    deps.answer_correctness_policy = assembly.answer_correctness_policy

    # Production prompt: exact turn_frame user surface (no re-assembly).
    prompt = build_agent_user_prompt(
        turn_frame=assembly.turn_frame,
        selection_prompt=assembly.selection_result.prompt_capability,
        baseline_prompt=assembly.baseline_result.prompt_capability,
        map_prompt=assembly.map_result.prompt_capability,
    )

    agent = create_reading_record_ask_agent(model)
    if observation is not None:
        observation.execution_stage = "agent_run"
    streamed = await run_agent_with_thinking_transport(
        agent=agent,
        prompt=prompt,
        deps=deps,
        thinking_observer=thinking_observer,
        model=model,
        model_settings=model_settings,
        usage_limits=usage_limits,
    )
    if observation is not None:
        observation.execution_stage = "agent_run_completed"
    draft = streamed.output
    if not isinstance(draft, AgentAnswerDraft):
        draft = AgentAnswerDraft(
            answer_text=str(draft),
            cited_evidence_handles=[],
            response_kind="grounded_answer",
        )

    deps.emit_event(ComposingAnswerEvent())
    deps.emit_event(ValidatingEvidenceEvent())

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
        FinalAnswerEvent(
            text=final_text or f"[{finalized.status}] {finalized.reason or ''}"
        )
    )
    deps.emit_event(
        RunFinishedEvent(
            read_range_calls=0,
            evidence_count=len(registry),
            search_current_article_calls=deps.search_current_article_calls,
        )
    )
    return ReadingRecordAskRunResult(
        final_text=final_text,
        events=list(deps.events),
        read_range_calls=0,
        search_current_article_calls=deps.search_current_article_calls,
        evidence_observations=registry.list_observations(),
        initial_anchor_handle=(
            assembly.selection_result.handle_ref
            if assembly.selection_result.status == "injected"
            else None
        ),
        agent_draft=draft,
        finalized=finalized,
        agent_output=draft,
        baseline_context=baseline,
    )
