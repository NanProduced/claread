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
    create_reading_record_ask_agent,
)
from app.services.reader_record_ask.answer_block_provenance import (
    ValidatedAnswerBlocks,
)
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
    FinalizedAskResult,
    ResponseKind,
    finalize_agent_answer,
)
from app.services.reader_record_ask.grounding_validator import AgentAnswerDraftOutput
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
    ContextCompactionEvent,
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
from app.services.reader_record_ask.turn_prompt import build_production_agent_user_prompt
from app.services.reader_record_ask.web_evidence_registry import (
    WebEvidenceRegistry,
)
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
    WebSearchTurnObservation,
    registrable_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import WebSearchBackend


@dataclass(slots=True)
class ReadingRecordAskRunResult:
    """Outcome of one independent agent run (including finalizer).

    ``baseline_context`` carries the typed baseline assembly result. Successful
    grounded runs carry immutable validated blocks that the finalizer consumes
    directly (no flat compatibility projection).

    ASK-WEB-G1-R1: ``web_search_calls`` mirrors
    :attr:`ReaderRecordAskDeps.web_search_calls` so production_stream can
    surface the per-turn web search budget in observability / logs.
    """

    final_text: str | None
    validated_answer_blocks: ValidatedAnswerBlocks | None = None
    events: list[RuntimeEvent] = field(default_factory=list)
    read_range_calls: int = 0
    search_current_article_calls: int = 0
    evidence_observations: tuple[ServerEvidenceObservation, ...] = ()
    initial_anchor_handle: EvidenceHandleRef | None = None
    agent_draft: AgentAnswerDraftOutput | None = None
    finalized: FinalizedAskResult | None = None
    agent_output: Any = None
    baseline_context: BaselineAgentContext | None = None
    # G1-b5: per-turn web search call count (host-owned; never model-supplied).
    # ``0`` when the capability was not enabled or no call was made.
    web_search_calls: int = 0
    # Terminal-only aggregate; production_stream logs it but never serializes
    # it into SSE, completed DTOs, history, or persistence JSON.
    web_search_turn_observation: WebSearchTurnObservation | None = None


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
    # ASK-WEB-G1-R1: web search capability + port + registry. The
    # capability is the server-owned execution truth — when ``None`` the
    # ``search_web`` tool must NOT be mounted. The backend port is
    # provider-neutral; ``None`` means fail-soft even when
    # ``enabled_for_turn=True`` (the tool returns ``unavailable``).
    # The registry is bound to the same envelope fingerprint as the
    # article evidence registry; when ``None`` the coordinator builds a
    # fresh one bound to the envelope. Production callers pass all three
    # so retry reuses the same capability / port identity as send.
    web_search_capability: ResolvedWebSearchCapability | None = None,
    web_search_backend: WebSearchBackend | None = None,
    web_evidence_registry: WebEvidenceRegistry | None = None,
    # R1.5 P0-2: thread-memory integration. When ``memory_enabled=False``
    # (default) the coordinator never touches the thread_memory package —
    # zero behavioral drift, zero DB I/O, prompt字节级不含 memory. When
    # ``True`` the coordinator loads + CAS-checks + fence-rebuilds +
    # validates a deterministic snapshot (R1 path; no model call).
    # ``memory_repository`` and ``thread_id`` are required when ``True``.
    memory_enabled: bool = False,
    memory_repository: Any | None = None,
    thread_id: str | None = None,
    memory_manager_enabled: bool = False,
    memory_compactor: Any | None = None,
    memory_settings: Any | None = None,
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

    ASK-WEB-G1-R1: ``web_search_capability`` is the resolved execution
    truth for one turn; the runtime reads ``enabled_for_turn`` to decide
    whether to mount the ``search_web`` tool (G1-b4). The capability
    never enters the model surface — only the mounted tool does.
    ``web_search_backend`` is the provider-neutral port; ``None`` means
    fail-soft (the ``search_web`` tool returns ``unavailable`` even when
    ``enabled_for_turn=True``). ``web_evidence_registry`` is the in-turn
    web evidence registry used by the finalizer (G0-b3) to resolve web
    handles to public citations.
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

    def _emit_memory_event(event: Any) -> None:
        if event_sink is None:
            return
        event_sink(
            ContextCompactionEvent(
                phase=event.kind,
                detail_code=event.detail_code,
                attempt_count=event.attempt_count,
                elapsed_ms=event.elapsed_ms,
            )
        )

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
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        web_evidence_registry=web_evidence_registry,
        memory_enabled=memory_enabled,
        memory_repository=memory_repository,
        thread_id=thread_id,
        memory_manager_enabled=memory_manager_enabled,
        memory_compactor=memory_compactor,
        memory_event_sink=(
            _emit_memory_event if memory_manager_enabled else None
        ),
        memory_settings=memory_settings,
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
    # G1-b5: web search execution truth propagated into the deps so the
    # ``search_web`` tool (when mounted) can read the capability / port /
    # registry through the coordinator + deps seam. The capability never
    # enters the model surface — only the mounted tool does.
    web_search_enabled_for_turn = (
        web_search_capability is not None
        and web_search_capability.enabled_for_turn
    )
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=document_access,
        fence=active_fence,
        evidence_registry=registry,
        confirmed_article_scopes=assembly.confirmed_article_scopes,
        article_rag=article_rag,
        max_read_range_calls=0,
        max_search_current_article_calls=max_search_current_article_calls,
        event_sink=event_sink,
        observation=observation,
        turn_coordinator=coordinator,
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        web_evidence_registry=coordinator.web_evidence_registry,
        max_web_search_calls=coordinator.max_web_search_calls,
    )

    deps.emit_event(
        RunStartedEvent(
            envelope_fingerprint=envelope.envelope_fingerprint,
            has_initial_selection=assembly.selection_result.selection.present,
        )
    )

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
                web_search_calls=0,
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
            web_search_calls=0,
            web_search_turn_observation=coordinator.web_search_turn_observation(
                cited_source_count=0,
                distinct_domain_count=0,
            ),
        )

    # Production prompt: exact turn_frame user surface (no re-assembly).
    prompt = build_production_agent_user_prompt(
        turn_frame=assembly.turn_frame,
        selection_prompt=assembly.selection_result.prompt_capability,
        baseline_prompt=assembly.baseline_result.prompt_capability,
        map_prompt=assembly.map_result.prompt_capability,
        # R3 P2 — append the coordinator-rendered focus selections block
        # (untrusted article text; emphasis, not restriction).
        focus_section=assembly.focus_selections_text,
    )

    # G1-b4: conditionally mount the ``search_web`` tool. The flag is
    # the resolved execution truth — never the request toggle directly.
    # ASK-WEB-R4: also gate ``expand_evidence`` and
    # ``search_current_article`` by real executable capability so the
    # model never sees a non-executable tool and no ``unavailable``
    # activity is produced for tools that would always return a safe
    # ``invalid_cursor`` / ``port_or_document_missing`` view.
    agent = create_reading_record_ask_agent(
        model,
        web_search_enabled=web_search_enabled_for_turn,
        expand_evidence_enabled=coordinator.has_expand_pointer,
        search_current_article_enabled=(
            coordinator.has_executable_article_rag
        ),
    )
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
    agent_output = streamed.output
    if not isinstance(agent_output, AgentAnswerDraftOutput):
        error = TypeError("agent returned an invalid structured answer output")
        error.reader_ask_raise_site = "runtime_type"  # type: ignore[attr-defined]
        error.reader_ask_final_output_type = type(agent_output).__name__  # type: ignore[attr-defined]
        raise error
    validated_answer_blocks = deps.consume_validated_answer_blocks()
    if (
        agent_output.response_kind == "grounded_answer"
        and validated_answer_blocks is None
    ):
        error = TypeError("grounded answer did not pass block validation")
        error.reader_ask_raise_site = "runtime_blocks"  # type: ignore[attr-defined]
        error.reader_ask_final_output_type = type(agent_output).__name__  # type: ignore[attr-defined]
        raise error
    if (
        validated_answer_blocks is not None
        and agent_output.validated_answer_blocks is None
    ):
        agent_output.bind_validated_answer_blocks(validated_answer_blocks)

    finalizer_kind: ResponseKind = agent_output.response_kind  # type: ignore[assignment]

    deps.emit_event(ComposingAnswerEvent())
    if finalizer_kind == "grounded_answer":
        deps.emit_event(ValidatingEvidenceEvent(activity="started"))

    if observation is not None:
        observation.execution_stage = "finalizer"
    # G1-b5: propagate the in-turn web evidence registry + outcome to the
    # finalizer so web-block handles can be resolved and the completed DTO
    # can carry the turn-level web search summary. The outcome is the
    # coordinator's last translated public outcome (``None`` when the
    # tool was never invoked).
    finalized = await finalize_agent_answer(
        envelope=envelope,
        registry=registry,
        fence=active_fence,
        response_kind=finalizer_kind,
        validated_answer_blocks=validated_answer_blocks,
        clarification_text=agent_output.clarification_text,
        web_evidence_registry=coordinator.web_evidence_registry,
        web_search_outcome=coordinator.web_search_outcome,
    )
    if finalizer_kind == "grounded_answer":
        deps.emit_event(
            ValidatingEvidenceEvent(
                activity="completed" if finalized.status == "ok" else "failed",
                outcome=finalized.status,
            )
        )

    final_text = finalized.answer_text if finalized.status == "ok" else None
    cited_web_citations = [
        citation
        for citation in finalized.public_citations
        if citation.source_kind == "web" and citation.url is not None
    ]
    web_search_turn_observation = coordinator.web_search_turn_observation(
        cited_source_count=len(cited_web_citations),
        distinct_domain_count=len(
            {
                registrable_domain_from_canonical_url(citation.url)
                for citation in cited_web_citations
                if citation.url is not None
            }
            - {None}
        ),
    )
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
            web_search_calls=deps.web_search_calls,
        )
    )
    return ReadingRecordAskRunResult(
        final_text=final_text,
        validated_answer_blocks=validated_answer_blocks,
        events=list(deps.events),
        read_range_calls=0,
        search_current_article_calls=deps.search_current_article_calls,
        evidence_observations=registry.list_observations(),
        initial_anchor_handle=(
            assembly.selection_result.handle_ref
            if assembly.selection_result.status == "injected"
            else None
        ),
        agent_draft=agent_output,
        finalized=finalized,
        agent_output=agent_output,
        baseline_context=baseline,
        web_search_calls=deps.web_search_calls,
        web_search_turn_observation=web_search_turn_observation,
    )
