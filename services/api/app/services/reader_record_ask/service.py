from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from app.config.settings import get_settings
from app.contracts.anchor_validation import (
    ANCHOR_RECORD_ID_MISMATCH,
    READING_RECORD_NOT_FOUND,
    READING_RECORD_SNAPSHOT_INVALID,
    AnchorValidationError,
)
from app.database import connection as db_connect
from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionConfirmResponse,
    ReaderAskDeleteSupplementResponse,
    ReaderAskMessageRetryRequest,
    ReaderAskThreadListResponse,
    ReaderAskThreadSummary,
    ReaderRecordAskActionConfirmRequest,
    ReaderRecordAskMessageRequest,
)
from app.services.ask_runtime import action_service, stream_service, thread_service
from app.services.reader_orchestration.anchor_gate import load_validated_reading_record_anchor
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_record_ask.execution_config import (
    ReaderRecordAskExecutionConfig,
    ReaderRecordAskExecutionUnavailable,
    resolve_reader_record_ask_execution,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository
from app.services.reader_record_ask.turn_lifecycle import StreamLifecycleHook
from app.services.reader_record_ask.web_search_contracts import WebSearchMode

logger = logging.getLogger(__name__)


# Default replayed web search mode when the persisted user message metadata
# does not carry ``web_search_mode`` (legacy rows persisted before ASK-WEB-G1-R2).
# Fail-closed: never silently grant a capability the original turn did not
# explicitly record as ``allowed``.
_DEFAULT_REPLAY_WEB_SEARCH_MODE: WebSearchMode = "disabled"


# Retry mode determined during preflight. ``"agentic"`` → use the
# unified execution config + production_stream retry; ``"legacy"`` → use
# stream_service.retry_thread_message. Resolving this before the
# StreamingResponse starts prevents branch drift mid-stream.
RetryMode = Literal["agentic", "legacy"]


@dataclass(slots=True, frozen=True)
class RetryPreparedResult:
    """Result of the retry preflight (mirrors Send's prepared tuple).

    Carries everything ``retry_reading_record_ask_message`` needs to
    stream the retry without re-resolving the persisted option or
    re-building the model. The route awaits the preflight coroutine
    before constructing the StreamingResponse so a config-unavailable
    option surfaces as a real HTTP 503 instead of an SSE error frame.

    For legacy retry (agentic flag off), ``facts`` and ``execution``
    are both ``None`` — ``stream_service.retry_thread_message`` loads
    its own state.
    """

    reading_record_id: UUID
    mode: RetryMode
    facts: object | None
    execution: ReaderRecordAskExecutionConfig | None


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_uuid",
                "field": field,
                "message": f"{field} must be a UUID",
            },
        ) from exc


def _reading_record_error(
    *,
    code: str,
    field: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": code,
            "field": field,
            "message": message,
        },
    )


async def _load_snapshot_facts_raw(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        return await repository.load_snapshot_facts(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
        )


async def _load_snapshot_facts(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> object:
    try:
        return await _load_snapshot_facts_raw(
            user_id=user_id,
            reading_record_id=reading_record_id,
        )
    except LookupError as exc:
        raise _reading_record_error(
            code=READING_RECORD_NOT_FOUND,
            field="reading_record_id",
            message=str(exc),
        ) from exc
    except ValueError as exc:
        raise _reading_record_error(
            code=READING_RECORD_SNAPSHOT_INVALID,
            field="reading_record_id",
            message=str(exc),
        ) from exc


async def _load_validated_anchor_raw(
    *,
    user_id: UUID,
    anchor: object,
) -> None:
    repository = ReaderOrchestrationRepository()
    async with db_connect.acquire_connection() as conn:
        await load_validated_reading_record_anchor(
            conn,
            repository=repository,
            user_id=user_id,
            anchor=anchor,
        )


async def _validate_reading_record_anchor(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    request: ReaderRecordAskMessageRequest,
) -> None:
    if request.anchor is None:
        await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
        return
    anchor_record_id = _parse_uuid(request.anchor.record_id, field="anchor.record_id")
    if anchor_record_id != reading_record_id:
        raise _reading_record_error(
            code=ANCHOR_RECORD_ID_MISMATCH,
            field="anchor.record_id",
            message="anchor.record_id does not match the route reading_record_id",
        )
    try:
        await _load_validated_anchor_raw(
            user_id=user_id,
            anchor=request.anchor,
        )
    except AnchorValidationError as exc:
        raise _reading_record_error(
            code=exc.code,
            field="anchor",
            message=exc.message,
        ) from exc


async def _ensure_default_thread(
    *,
    user_id: UUID,
    reading_record_id: UUID,
) -> dict[str, str]:
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=reading_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        reading_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return {"id": str(thread["id"]), "title": str(thread.get("title") or "Ask Claread")}


async def list_reading_record_ask_threads(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadListResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.list_reading_record_threads(user_id, parsed_record_id)


async def create_default_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
) -> ReaderAskThreadSummary:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    thread = await thread_service.ensure_default_reading_record_thread(
        user_id,
        parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )
    return ReaderAskThreadSummary.model_validate(thread_service._thread_summary_payload(thread))


async def get_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
):
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await thread_service.get_reading_record_thread_detail(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
    )


async def reset_reading_record_ask_thread(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
):
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    facts = await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
    return await thread_service.reset_reading_record_thread(
        user_id,
        thread_id,
        reading_record_id=parsed_record_id,
        title=facts.record.title or "Ask Claread",
    )


async def _resolve_agentic_execution(
    option,
    *,
    web_search_mode: str = "disabled",
) -> ReaderRecordAskExecutionConfig:
    """Compile a persisted option into a unified execution config.

    ASK-M1: replaces the old ``_build_agentic_model_for_option``. Both
    send and retry paths now call this so the persisted option is the
    single source of truth for the model, the provider completion cap,
    and the host usage limit. Fail-closed: raises a typed 503 — never
    silently substitutes the global default model.

    ASK-WEB-G1-R1: ``web_search_mode`` is the user-visible request
    toggle propagated from :attr:`ReaderRecordAskMessageRequest.web_search_mode`.
    The resolver translates it into a :class:`ResolvedWebSearchCapability`
    attached to the returned config so the runtime can mount the
    ``search_web`` tool and inject the :class:`WebSearchBackend` port.
    """
    try:
        return resolve_reader_record_ask_execution(
            option,
            web_search_mode=web_search_mode,  # type: ignore[arg-type]
        )
    except ReaderRecordAskExecutionUnavailable as exc:
        logger.warning(
            "agentic_execution_unavailable code=model_unconfigured "
            "model_key=%s reason=%s",
            exc.option_key,
            exc.reason,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "model_unconfigured",
                "message": "Ask Claread model is not configured for the selected option.",
                "model_key": exc.option_key,
            },
        ) from None


async def prepare_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    thread_id: UUID | None = None,
) -> tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None]:
    """Validate anchor/thread and resolve execution config before StreamingResponse.

    Returns ``(reading_record_id, thread_id, execution)``. Raising here
    yields a real HTTP 4xx/503 (e.g. unknown model key, unconfigured
    provider) instead of an SSE error frame. For the legacy path,
    execution is None — stream_service resolves its own model.
    """
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    await _validate_reading_record_anchor(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        request=request,
    )
    if thread_id is None:
        thread = await _ensure_default_thread(
            user_id=user_id,
            reading_record_id=parsed_record_id,
        )
        resolved_thread_id = _parse_uuid(thread["id"], field="thread id is invalid")
    else:
        resolved_thread_id = thread_id

    if not get_settings().reader_record_ask_agentic_enabled:
        return parsed_record_id, resolved_thread_id, None

    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=resolved_thread_id,
        requested_key=request.model,
        reading_record_id=parsed_record_id,
    )
    execution = await _resolve_agentic_execution(
        option,
        web_search_mode=request.web_search_mode,
    )
    # G3-R3: when the user requested ``web_search_mode="allowed"`` but
    # the resolved capability is unavailable OR the adapter could not
    # construct a real backend, fail-closed with a typed 503 BEFORE
    # the StreamingResponse starts. Never silently stream a turn that
    # promised Web Search but cannot deliver it.
    if request.web_search_mode == "allowed":
        capability = execution.web_search_capability
        if (
            capability is None
            or not capability.enabled_for_turn
            or execution.web_search_backend is None
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "web_search_unavailable",
                    "message": "Web Search is temporarily unavailable for this model.",
                },
            )
    return parsed_record_id, resolved_thread_id, execution


async def _stream_legacy_or_agentic(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    execution: ReaderRecordAskExecutionConfig | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    """Dispatch to agentic path when flag is on; never fall back on agentic failure."""
    if not get_settings().reader_record_ask_agentic_enabled:
        async for chunk in stream_service.stream_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            request=request,
        ):
            yield chunk
        return

    # Agentic path: re-load facts for envelope (validation already done).
    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=reading_record_id,
    )
    from app.services.reader_record_ask.production_stream import (
        stream_agentic_thread_message,
    )

    model = execution.model if execution is not None else None
    model_settings = execution.model_settings() if execution is not None else None
    usage_limits = execution.usage_limits if execution is not None else None
    # ASK-WEB-G1-R1: forward the resolved web search capability so the
    # production stream can auto-wire the FakeWebSearchBackend + a fresh
    # WebEvidenceRegistry bound to the envelope fingerprint. When
    # ``web_search_mode="disabled"`` (or unset) the resolver returns
    # ``None`` and the runtime must NOT mount the ``search_web`` tool.
    web_search_capability = (
        execution.web_search_capability if execution is not None else None
    )
    # G3-R3: forward the real provider backend produced by the registry.
    # When ``None`` (capability not granted / adapter unverified) the
    # production stream auto-wires a fake backend only in tests; in
    # production the ``search_web`` tool is not mounted.
    #
    # Defensive invariant: when ``web_search_capability`` is ``None``
    # (disabled mode), the backend MUST also be ``None`` — even if a
    # buggy resolver ever returned a non-None backend with a disabled
    # capability. The runtime must never mount ``search_web`` for a
    # disabled turn.
    resolved_backend = (
        execution.web_search_backend if execution is not None else None
    )
    web_search_backend = (
        resolved_backend if web_search_capability is not None else None
    )

    async for chunk in stream_agentic_thread_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread_id=thread_id,
        content=request.content,
        facts=facts,
        request_anchor=request.anchor,
        validated_anchor=None,
        stable_document_id=None,
        model=model,
        model_settings=model_settings,
        usage_limits=usage_limits,
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        lifecycle=lifecycle,
    ):
        yield chunk


async def send_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    request: ReaderRecordAskMessageRequest,
    prepared: tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None] | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
        )
    parsed_record_id, resolved_thread_id, execution = prepared
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=resolved_thread_id,
        request=request,
        execution=execution,
        lifecycle=lifecycle,
    ):
        yield chunk


async def stream_reading_record_ask_thread_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    prepared: tuple[UUID, UUID, ReaderRecordAskExecutionConfig | None] | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    if prepared is None:
        prepared = await prepare_reading_record_ask_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request=request,
            thread_id=thread_id,
        )
    parsed_record_id, resolved_thread_id, execution = prepared
    async for chunk in _stream_legacy_or_agentic(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=resolved_thread_id,
        request=request,
        execution=execution,
        lifecycle=lifecycle,
    ):
        yield chunk


async def prepare_reading_record_ask_retry(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID | None = None,
) -> RetryPreparedResult:
    """Retry preflight — runs before the StreamingResponse starts.

    Mirrors :func:`prepare_reading_record_ask_message` for the retry
    path. Completes the same five preflight stages so a config-
    unavailable option surfaces as a typed HTTP 503 (or 422 / 400)
    before any SSE byte is written:

    1. ``reading_record_id`` UUID parsing;
    2. agentic feature flag decision (``mode``);
    3. snapshot facts load (only required for the agentic path);
    4. persisted option resolution via ``thread_service``;
    5. ``resolve_reader_record_ask_execution``.

    Legacy retry keeps the original ``stream_service.retry_thread_message``
    behavior, but ``mode`` is fixed here so the generator cannot drift
    branches after the response has started.

    ASK-WEB-G1-R2: when ``message_id`` is provided, the preflight loads
    the persisted user message metadata and replays the original turn's
    ``web_search_mode`` (the **resolved** value persisted at send time,
    not the raw request toggle). This is the single source of truth for
    retry capability — the runtime must NOT fall back to the current UI
    toggle. If the persisted metadata does not carry ``web_search_mode``
    (legacy rows persisted before G1-R2), the replay defaults to
    ``"disabled"`` (fail-closed). When the persisted mode is
    ``"allowed"`` but the provider is no longer wired (or the resolver
    returns ``enabled_for_turn=False`` for any reason), the resolver
    returns a typed unavailable capability — retry never silently
    switches to a fake backend or another provider.
    """
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    if not get_settings().reader_record_ask_agentic_enabled:
        # Legacy mode still loads facts for parity with the previous
        # behavior — retry_thread_message does not accept facts but
        # the prior generator called _load_snapshot_facts first to
        # produce the same 404/400 error before streaming.
        await _load_snapshot_facts(user_id=user_id, reading_record_id=parsed_record_id)
        return RetryPreparedResult(
            reading_record_id=parsed_record_id,
            mode="legacy",
            facts=None,
            execution=None,
        )

    # Agentic mode: preflight facts + option + execution config so the
    # generator never re-resolves any of them.
    facts = await _load_snapshot_facts(
        user_id=user_id,
        reading_record_id=parsed_record_id,
    )
    option = await thread_service.resolve_and_persist_thread_model_option(
        user_id=user_id,
        thread_id=thread_id,
        requested_key=None,
        reading_record_id=parsed_record_id,
    )

    # ASK-WEB-G1-R2: replay the original turn's persisted
    # ``web_search_mode`` so retry uses the same capability truth as
    # the original send. The persisted value is the **resolved** mode
    # (``allowed`` only when a real provider was wired and
    # ``enabled_for_turn=True`` at send time), not the raw UI toggle.
    # When ``message_id`` is None (defensive — caller did not supply
    # the retry target) or the metadata has no ``web_search_mode``
    # (legacy row), the replay defaults to ``"disabled"`` fail-closed.
    replayed_web_search_mode: WebSearchMode = _DEFAULT_REPLAY_WEB_SEARCH_MODE
    if message_id is not None:
        replayed_web_search_mode = await _load_replayed_web_search_mode(
            thread_id=thread_id,
            message_id=message_id,
        )

    execution = await _resolve_agentic_execution(
        option,
        web_search_mode=replayed_web_search_mode,
    )
    if replayed_web_search_mode == "allowed":
        capability = execution.web_search_capability
        if (
            capability is None
            or not capability.enabled_for_turn
            or execution.web_search_backend is None
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "web_search_replay_unavailable",
                    "message": "Web Search is no longer available for this retry.",
                },
            )
    return RetryPreparedResult(
        reading_record_id=parsed_record_id,
        mode="agentic",
        facts=facts,
        execution=execution,
    )


async def _load_replayed_web_search_mode(
    *,
    thread_id: UUID,
    message_id: UUID,
) -> WebSearchMode:
    """Load the persisted ``web_search_mode`` for retry replay.

    Reads the assistant message identified by ``message_id`` and its
    closest preceding user message, then extracts the ``web_search_mode``
    field from the user message's persisted metadata.

    ASK-WEB-G1-R3 fail-closed contract
    ----------------------------------
    Per the R3 spec, retry must only trust server-side persisted facts.
    The previous implementation silently degraded every error case to
    ``"disabled"``, which let ownership mismatches and DB failures start
    a generator with the wrong capability truth. The new contract is:

    - Assistant message not found in this thread → ``HTTPException(404)``
      (typed not-found — message/thread ownership mismatch).
    - No preceding user message → ``HTTPException(404)`` (typed
      not-found — cannot replay a turn without the originating user
      message).
    - User message metadata is missing the ``web_search_mode`` key
      (legacy rows persisted before ASK-WEB-G1-R2) → ``"disabled"``
      (compatible — legacy rows never recorded a capability).
    - User message metadata is not a mapping → ``HTTPException(503)``
      (malformed persisted state, not a legacy row).
    - Persisted value is ``"allowed"`` → ``"allowed"`` (the resolver
      will separately enforce adapter readiness; the retry preflight
      returns typed 503 if the adapter is no longer wired).
    - Persisted value is ``"disabled"`` → ``"disabled"``.
    - Persisted value is present but not one of
      ``{"disabled", "allowed"}`` → ``HTTPException(503)`` (typed
      preflight unavailable — illegal metadata, never silently
      degraded to ``"disabled"``).
    - Any DB error → ``HTTPException(503)`` (typed preflight
      unavailable — DB failures must not start a generator with an
      unverified capability truth).

    The returned value is fed into :func:`resolve_web_search_capability`
    by the caller; if the provider is no longer wired, the resolver
    returns a typed unavailable capability (``enabled_for_turn=False``).
    """
    try:
        repo = ReaderRecordAskRepository()
        assistant_msg, user_msg = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=message_id,
        )
    except Exception:  # noqa: BLE001 — fail-closed, no leakage
        logger.warning(
            "retry_replay_web_search_mode_failed code=db_read_failed "
            "thread_id=%s message_id=%s",
            thread_id,
            message_id,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retry_replay_unavailable",
                "message": "Retry is temporarily unavailable.",
            },
        ) from None

    if assistant_msg is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "retry_message_not_found",
                "message": "Retried message was not found in this thread.",
            },
        )
    if user_msg is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "retry_preceding_user_message_not_found",
                "message": "Could not locate the original user message for retry.",
            },
        )

    metadata = user_msg["metadata_json"] if "metadata_json" in user_msg else {}
    if not isinstance(metadata, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retry_replay_unavailable",
                "message": "Retry is temporarily unavailable.",
            },
        )

    raw_mode = metadata.get("web_search_mode")
    if raw_mode == "allowed":
        return "allowed"
    if raw_mode == "disabled":
        return "disabled"
    if raw_mode is None:
        # Legacy row persisted before ASK-WEB-G1-R2 — no
        # ``web_search_mode`` key. Compatible: default to ``"disabled"``.
        return _DEFAULT_REPLAY_WEB_SEARCH_MODE
    # Persisted value is present but illegal — fail-closed with a typed
    # 503 so the generator never starts with an unverified capability.
    logger.warning(
        "retry_replay_web_search_mode_failed code=illegal_metadata_value "
        "thread_id=%s message_id=%s raw_mode=%r",
        thread_id,
        message_id,
        raw_mode if isinstance(raw_mode, str) else type(raw_mode).__name__,
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "retry_replay_unavailable",
            "message": "Retry is temporarily unavailable.",
        },
    )


async def retry_reading_record_ask_message(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    message_id: UUID,
    request: ReaderAskMessageRetryRequest,
    prepared: RetryPreparedResult | None = None,
    lifecycle: StreamLifecycleHook | None = None,
) -> AsyncIterator[str]:
    """Stream a retry. ``prepared`` must come from the route's preflight.

    The generator must not re-load facts, re-resolve the persisted
    option, or rebuild the execution config — the route has already
    done all three via :func:`prepare_reading_record_ask_retry`. This
    guarantees Send and Retry have identical fail-closed HTTP semantics
    (typed 503 before StreamingResponse) and identical model + budget
    propagation.
    """
    if prepared is None:
        prepared = await prepare_reading_record_ask_retry(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            message_id=message_id,
        )
    parsed_record_id = prepared.reading_record_id

    if prepared.mode == "legacy":
        async for chunk in stream_service.retry_thread_message(
            user_id=user_id,
            reading_record_id=parsed_record_id,
            thread_id=thread_id,
            message_id=message_id,
            retry_body=request,
        ):
            yield chunk
        return

    # Agentic mode — preflight has already resolved facts + execution.
    assert prepared.execution is not None, "agentic mode requires execution config"
    assert prepared.facts is not None, "agentic mode requires preflight facts"
    from app.services.reader_record_ask.production_stream import (
        retry_agentic_thread_message,
    )

    execution = prepared.execution
    # G3-R3: defensive invariant — when ``web_search_capability`` is
    # ``None`` (disabled mode), the backend MUST also be ``None`` so
    # the runtime never mounts ``search_web`` on a disabled retry.
    retry_capability = execution.web_search_capability
    retry_backend = (
        execution.web_search_backend
        if retry_capability is not None
        else None
    )
    async for chunk in retry_agentic_thread_message(
        user_id=user_id,
        reading_record_id=parsed_record_id,
        thread_id=thread_id,
        message_id=message_id,
        facts=prepared.facts,
        model=execution.model,
        model_settings=execution.model_settings(),
        usage_limits=execution.usage_limits,
        # ASK-WEB-G1-R1: forward the resolved web search capability so
        # retry uses the same execution truth as the original send. When
        # ``None`` (capability not granted on the original turn) the
        # runtime must NOT mount the ``search_web`` tool on retry.
        web_search_capability=retry_capability,
        # G3-R3: forward the real provider backend so retry uses the
        # same adapter as the original send. When ``None`` the runtime
        # must NOT mount ``search_web`` on retry.
        web_search_backend=retry_backend,
        lifecycle=lifecycle,
    ):
        yield chunk


async def confirm_reading_record_ask_action(
    *,
    user_id: UUID,
    reading_record_id: str,
    action_id: str,
    request: ReaderRecordAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    threads = await thread_service.list_reading_record_threads(user_id, parsed_record_id)
    target_thread_id = next((UUID(item.id) for item in threads.items if item.is_default), None)
    if target_thread_id is None:
        raise HTTPException(status_code=404, detail="Reader ask action proposal not found")
    return await action_service.confirm_action(
        user_id=user_id,
        thread_id=target_thread_id,
        action_id=action_id,
        body=ReaderAskActionConfirmRequest(confirmed=request.confirmed),
        expected_reading_record_id=parsed_record_id,
    )


async def confirm_reading_record_ask_thread_action(
    *,
    user_id: UUID,
    reading_record_id: str,
    thread_id: UUID,
    action_id: str,
    request: ReaderRecordAskActionConfirmRequest,
) -> ReaderAskActionConfirmResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await action_service.confirm_action(
        user_id=user_id,
        thread_id=thread_id,
        action_id=action_id,
        body=ReaderAskActionConfirmRequest(confirmed=request.confirmed),
        expected_reading_record_id=parsed_record_id,
    )


async def delete_reading_record_ask_supplement(
    *,
    user_id: UUID,
    reading_record_id: str,
    supplement_id: UUID,
) -> ReaderAskDeleteSupplementResponse:
    parsed_record_id = _parse_uuid(reading_record_id, field="reading_record_id")
    return await action_service.delete_supplement(
        user_id=user_id,
        supplement_id=supplement_id,
        expected_reading_record_id=parsed_record_id,
    )
