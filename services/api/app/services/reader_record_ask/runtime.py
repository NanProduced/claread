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
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
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
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
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
    """Outcome of one independent agent run (including finalizer)."""

    final_text: str | None
    events: list[RuntimeEvent] = field(default_factory=list)
    read_range_calls: int = 0
    search_current_article_calls: int = 0
    evidence_observations: tuple[ServerEvidenceObservation, ...] = ()
    initial_anchor_handle: EvidenceHandleRef | None = None
    agent_draft: AgentAnswerDraft | None = None
    finalized: FinalizedAskResult | None = None
    agent_output: Any = None


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
) -> ReadingRecordAskRunResult:
    """Run the independent Reading Record Ask agent once, then finalize.

    The model decides whether to call ``read_range`` / ``search_current_article``;
    there is no keyword routing or article/RAG prefetch.

    ``event_sink`` is an optional live observation hook used by production
    stream for concurrent progress projection. Events are always retained on
    ``deps.events`` for tests and final diagnostics.
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
    )

    projection = envelope.to_agent_projection()
    deps.emit_event(
        RunStartedEvent(
            envelope_fingerprint=envelope.envelope_fingerprint,
            has_initial_selection=projection.has_initial_selection,
        )
    )

    available_handles = [ref.handle_id for ref in registry.list_handle_refs()]
    agent = create_reading_record_ask_agent(model)
    prompt = build_agent_user_prompt(
        user_message=user_message,
        agent_context_json=json.dumps(
            projection.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        ),
        available_evidence_handle_ids=available_handles,
    )
    result = await agent.run(prompt, deps=deps)
    draft = result.output
    if not isinstance(draft, AgentAnswerDraft):
        draft = AgentAnswerDraft(answer_text=str(draft), cited_evidence_handles=[])

    # Pre-finalizer activity: composing, then validating. FinalAnswerEvent only
    # after finalize returns so UI "正在核对回答依据" matches real work.
    deps.emit_event(ComposingAnswerEvent())
    deps.emit_event(ValidatingEvidenceEvent())

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
    )
