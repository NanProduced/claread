"""Production SSE + persistence adapter for the agentic Reading Record Ask path.

Flag-gated from ``service.py``.  Does not import legacy reader_ask agent,
planner, ask_runtime stream, or old RAG prompt bridges.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model

from app.config.settings import get_settings
from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    ReaderRecordAskCompletedDTO,
    ReaderRecordAskProgressDTO,
    ReaderRecordAskRunStartedDTO,
    ReaderRecordAskTerminalDTO,
    evidence_item_from_observation,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import DocumentAccess
from app.services.reader_record_ask.envelope_builder import (
    build_envelope_from_facts,
    document_access_from_facts,
)
from app.services.reader_record_ask.finalizer import FinalizedAskResult
from app.services.reader_record_ask.production_wiring import (
    build_production_article_rag_port,
    load_active_stable_document_id,
    resolve_agentic_model,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.sse import (
    EVENT_AGENTIC_PROGRESS,
    EVENT_AGENTIC_RUN_STARTED,
    EVENT_AGENTIC_TERMINAL,
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_INTERRUPTED,
    EVENT_MESSAGE_STARTED,
    EVENT_THREAD_READY,
    encode_sse,
)

RunFn = Callable[..., Any]

logger = logging.getLogger(__name__)

# Stable external terminal reasons. Must not leak pydantic-ai / provider
# internals (exception text, schema bodies, raw responses, thinking).
TERMINAL_REASON_AGENT_OUTPUT_INVALID = "agent_output_invalid"
TERMINAL_REASON_AGENT_RUN_FAILED = "agent_run_failed"


def _safe_model_route(model: Model | str | None) -> str:
    """Return a short, non-sensitive model route/name for diagnostics."""
    if model is None:
        return "none"
    if isinstance(model, str):
        return model[:64]
    name = getattr(model, "model_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()[:64]
    return type(model).__name__[:64]


def _safe_envelope_snapshot(envelope: ReadingRecordAskContextEnvelope) -> dict[str, Any]:
    """Persistable snapshot — includes server identity for fence/replay."""
    return envelope.model_dump(mode="json")


def build_completed_dto(
    *,
    run_result: ReadingRecordAskRunResult,
    message_id: str,
    thread_id: str,
    turn_run_id: str,
    envelope: ReadingRecordAskContextEnvelope,
) -> ReaderRecordAskCompletedDTO:
    assert run_result.finalized is not None
    assert run_result.finalized.status == "ok"
    assert run_result.final_text is not None
    evidence = [
        evidence_item_from_observation(obs) for obs in run_result.finalized.resolved_evidence
    ]
    return ReaderRecordAskCompletedDTO(
        answer_text=run_result.final_text,
        message_id=message_id,
        thread_id=thread_id,
        turn_run_id=turn_run_id,
        envelope_fingerprint=envelope.envelope_fingerprint,
        evidence=evidence,
    )


def build_terminal_dto(
    *,
    finalized: FinalizedAskResult | None,
    message_id: str | None,
    thread_id: str | None,
    turn_run_id: str | None,
    envelope_fingerprint: str | None,
    final_status: str,
    terminal_reason: str | None,
) -> ReaderRecordAskTerminalDTO:
    rejected: list[str] = []
    if finalized is not None:
        rejected = list(finalized.rejected_handles)
        terminal_reason = terminal_reason or finalized.reason
        final_status = finalized.status if finalized.status != "ok" else final_status
    return ReaderRecordAskTerminalDTO(
        final_status=final_status,  # type: ignore[arg-type]
        message_id=message_id,
        thread_id=thread_id,
        turn_run_id=turn_run_id,
        envelope_fingerprint=envelope_fingerprint,
        terminal_reason=terminal_reason,
        rejected_handles=rejected,
    )


async def stream_agentic_thread_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    content: str,
    facts: Any,
    request_anchor: Any | None,
    validated_anchor: Any | None = None,
    stable_document_id: UUID | None = None,
    repository: ReaderRecordAskRepository | None = None,
    document_access: DocumentAccess | None = None,
    article_rag: ArticleRagSearchPort | None = None,
    model: Model | str | None = None,
    run_fn: RunFn | None = None,
    auto_wire_dependencies: bool = True,
) -> AsyncIterator[str]:
    """Run the agentic path: persist + SSE with a single completed DTO truth.

    When ``auto_wire_dependencies`` is True (production default):
    - resolve a real model via the ``reader_ask`` route (no stub success);
    - load active stable document identity for the envelope;
    - build Article RAG port when ``reader_article_rag_enabled``.

    Explicit ``model`` / ``article_rag`` / ``stable_document_id`` overrides
    always win (tests).  Missing model → typed terminal failed, never
    ``message.completed``.
    """
    repo = repository or ReaderRecordAskRepository()
    run_agent = run_fn or run_reading_record_ask
    settings = get_settings()

    thread = await repo.get_thread(
        user_id=user_id,
        thread_id=thread_id,
        reading_record_id=reading_record_id,
    )
    if thread is None:
        yield encode_sse(
            "error",
            {"code": "404", "detail": "Reader ask thread not found for this Reading Record"},
        )
        return

    # Resolve base/generation from facts first so stable-document lookup
    # can fence against the active base.
    base = facts.build_result.base
    base_id = UUID(str(base.base_id))
    generation = int(facts.record.generation)

    resolved_stable_id = stable_document_id
    if resolved_stable_id is None and auto_wire_dependencies:
        resolved_stable_id = await load_active_stable_document_id(
            user_id=user_id,
            reading_record_id=reading_record_id,
            expected_generation=generation,
            expected_base_id=base_id,
        )

    envelope = build_envelope_from_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
        facts=facts,
        request_anchor=request_anchor,
        validated_anchor=validated_anchor,
        stable_document_id=resolved_stable_id,
    )
    access = document_access or document_access_from_facts(
        reading_record_id=reading_record_id,
        facts=facts,
        stable_document_id=resolved_stable_id,
    )

    # Model resolution — never invent a stub completed answer.
    # Explicit model always wins. Production auto-wire resolves reader_ask
    # route; test callers with auto_wire=False and model=None stay unconfigured.
    if model is not None:
        active_model: Model | str | None = model
    elif auto_wire_dependencies:
        active_model = resolve_agentic_model(settings, explicit=None)
    else:
        active_model = None
    wired_rag = article_rag
    if wired_rag is None and auto_wire_dependencies:
        wired_rag = build_production_article_rag_port(settings)

    user_msg = await repo.create_message(
        thread_id=thread_id,
        role="user",
        status="completed",
        content_md=content,
        metadata={"execution_version": EXECUTION_VERSION_AGENTIC_V1},
    )
    assistant_msg = await repo.create_message(
        thread_id=thread_id,
        role="assistant",
        status="streaming",
        content_md="",
        metadata={"execution_version": EXECUTION_VERSION_AGENTIC_V1},
    )
    turn = await repo.create_agentic_turn_run(
        message_id=UUID(assistant_msg["id"]),
        thread_id=thread_id,
        user_id=user_id,
        reading_record_id=reading_record_id,
        base_id=envelope.base_id,
        generation=envelope.record_generation,
        turn_id=UUID(user_msg["id"]),
        envelope_fingerprint=envelope.envelope_fingerprint,
        envelope_snapshot=_safe_envelope_snapshot(envelope),
    )

    yield encode_sse(
        EVENT_THREAD_READY,
        {"thread_id": str(thread_id), "execution_version": EXECUTION_VERSION_AGENTIC_V1},
    )
    yield encode_sse(
        EVENT_MESSAGE_STARTED,
        {
            "message_id": assistant_msg["id"],
            "thread_id": str(thread_id),
            "execution_version": EXECUTION_VERSION_AGENTIC_V1,
        },
    )
    run_started = ReaderRecordAskRunStartedDTO(
        message_id=assistant_msg["id"],
        thread_id=str(thread_id),
        turn_run_id=turn["id"],
        envelope_fingerprint=envelope.envelope_fingerprint,
        has_initial_selection=envelope.initial_anchor is not None,
    )
    yield encode_sse(EVENT_AGENTIC_RUN_STARTED, run_started.model_dump(mode="json"))

    turn_run_id = UUID(turn["id"])
    message_id = UUID(assistant_msg["id"])

    if active_model is None:
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=(
                "agentic_model_unconfigured: no validated model for reader_ask route; "
                "refusing pseudo-completed answer"
            ),
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return

    yield encode_sse(
        EVENT_AGENTIC_PROGRESS,
        ReaderRecordAskProgressDTO(
            phase="agent_running",
            summary="Running Reading Record Ask agent",
        ).model_dump(mode="json"),
    )

    try:
        run_result: ReadingRecordAskRunResult = await run_agent(
            user_message=content,
            envelope=envelope,
            document_access=access,
            model=active_model,
            article_rag=wired_rag,
        )
    except asyncio.CancelledError:
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="cancelled",
            terminal_reason="client disconnect or cancellation",
        )
        persisted = await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="cancelled",
            final_status="cancelled",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal.model_dump(mode="json"),
        )
        assert persisted.get("resolved_evidence_json") in (None, [], "[]")
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
        yield encode_sse(
            EVENT_MESSAGE_INTERRUPTED,
            terminal.model_dump(mode="json"),
        )
        raise
    except UnexpectedModelBehavior as exc:
        # Structured-output / tool-output validation exhausted. Do not
        # surface pydantic-ai messages ("Exceeded maximum retries…") or
        # schema / provider bodies to the Web client or logs.
        logger.warning(
            "reader_record_ask structured output invalid: type=%s turn_run_id=%s "
            "message_id=%s model_route=%s envelope_fp=%s",
            type(exc).__name__,
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            envelope.envelope_fingerprint[:12],
        )
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=TERMINAL_REASON_AGENT_OUTPUT_INVALID,
        )
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal.model_dump(mode="json"),
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
        yield encode_sse(
            EVENT_MESSAGE_INTERRUPTED,
            terminal.model_dump(mode="json"),
        )
        return
    except Exception as exc:
        # Generic failures stay typed-failed/interrupted. Never log str(exc),
        # traceback, request/response, or schema bodies — provider payloads
        # may contain sensitive context.
        logger.warning(
            "reader_record_ask agent run failed: type=%s turn_run_id=%s "
            "message_id=%s model_route=%s envelope_fp=%s",
            type(exc).__name__,
            turn["id"],
            assistant_msg["id"],
            _safe_model_route(active_model),
            envelope.envelope_fingerprint[:12],
        )
        terminal = build_terminal_dto(
            finalized=None,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status="failed",
            terminal_reason=TERMINAL_REASON_AGENT_RUN_FAILED,
        )
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status="failed",
            final_status="failed",
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal.model_dump(mode="json"),
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal.model_dump(mode="json"))
        yield encode_sse(
            EVENT_MESSAGE_INTERRUPTED,
            terminal.model_dump(mode="json"),
        )
        return

    finalized = run_result.finalized
    if finalized is None or finalized.status != "ok" or run_result.final_text is None:
        status = finalized.status if finalized is not None else "failed"
        run_status = "stale" if status == "context_stale" else "failed"
        terminal = build_terminal_dto(
            finalized=finalized,
            message_id=assistant_msg["id"],
            thread_id=str(thread_id),
            turn_run_id=turn["id"],
            envelope_fingerprint=envelope.envelope_fingerprint,
            final_status=status if status != "ok" else "failed",
            terminal_reason=(
                finalized.reason if finalized is not None else "missing_finalizer_result"
            ),
        )
        terminal_json = terminal.model_dump(mode="json")
        await repo.terminal_agentic_turn_run(
            turn_run_id=turn_run_id,
            message_id=message_id,
            run_status=run_status,
            final_status=terminal.final_status,
            terminal_reason=terminal.terminal_reason,
            terminal_dto=terminal_json,
        )
        yield encode_sse(EVENT_AGENTIC_TERMINAL, terminal_json)
        yield encode_sse(EVENT_MESSAGE_INTERRUPTED, terminal_json)
        return

    completed = build_completed_dto(
        run_result=run_result,
        message_id=assistant_msg["id"],
        thread_id=str(thread_id),
        turn_run_id=turn["id"],
        envelope=envelope,
    )
    completed_json = completed.model_dump(mode="json")
    evidence_json = [item.model_dump(mode="json") for item in completed.evidence]
    persisted = await repo.complete_agentic_turn_run(
        turn_run_id=turn_run_id,
        message_id=message_id,
        answer_text=completed.answer_text,
        completed_dto=completed_json,
        resolved_evidence=evidence_json,
        final_status="ok",
    )
    stored = persisted.get("user_visible_output_json")
    emit_payload = stored if isinstance(stored, dict) else completed_json
    yield encode_sse(EVENT_MESSAGE_COMPLETED, emit_payload)
