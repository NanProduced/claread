"""History projection for Agentic Reading Record Ask turns.

Cold-load / thread-detail path only. Does **not** touch legacy
``output_contract.USER_VISIBLE_OUTPUT_FIELDS`` and never maps agentic
evidence into legacy ``evidence`` or ``article_rag``.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V2,
    ReaderRecordAskCompletedDTO,
    ReaderRecordAskTerminalDTO,
)

AGENTIC_EXECUTION_VERSION = EXECUTION_VERSION_AGENTIC_V2
AGENTIC_EXECUTION_VERSIONS = frozenset({EXECUTION_VERSION_AGENTIC_V2})

_TERMINAL_UI_STATUS: dict[str, str] = {
    "failed": "failed",
    "context_stale": "interrupted",
    "invalid_citations": "interrupted",
    "cancelled": "interrupted",
}


def is_agentic_execution_version(value: Any) -> bool:
    return value in AGENTIC_EXECUTION_VERSIONS


def claims_agentic_payload(value: Any) -> bool:
    """True when a persisted JSON blob self-identifies as trusted v2.

    Used only for isolation: such blobs must not enter an untrusted history
    row. They are never treated as a trusted successful fact without a
    matching DB ``execution_version`` column.
    """
    if not isinstance(value, dict):
        return False
    return value.get("execution_version") in AGENTIC_EXECUTION_VERSIONS


def quarantine_untrusted_agentic_claim(
    *,
    message_id: str,
    thread_id: str,
    role: str,
    created_at: str | None,
    updated_at: str | None,
    context_anchors: list[Any] | None,
    usage_event_id: str | None,
    current_turn_run_id: str | None,
    current_turn_run: dict[str, Any] | None,
) -> dict[str, Any]:
    """DB version missing/non-agentic but JSON claims agentic → isolate.

    Emits a failed empty message with **no** legacy evidence and **no**
    public agentic citations, so substrate/index/hash cannot leak through
    the loose legacy evidence channel.
    """
    anchors = list(context_anchors or [])
    safe_turn_run = _sanitize_turn_run_for_wire(current_turn_run)
    return {
        "id": message_id,
        "thread_id": thread_id,
        "role": role,
        "status": "failed",
        "content_md": "",
        "submission_mode": "chat",
        "resolved_intent": None,
        "context_anchors": anchors,
        **_empty_legacy_lists(),
        "usage_event_id": usage_event_id,
        "current_turn_run_id": current_turn_run_id,
        "current_turn_run": safe_turn_run,
        "current_user_visible_output": None,
        "current_eval_trace": None,
        "created_at": created_at,
        "updated_at": updated_at,
        # Do not advertise agentic success identity without a trusted DB column.
        "execution_version": None,
        "final_status": "failed",
        "agentic_answer_blocks": None,
        "agentic_citations": None,
        "knowledge_mode": None,
        "source_status": None,
        "legacy_classification": None,
    }


def _empty_legacy_lists() -> dict[str, Any]:
    return {
        "citations": [],
        "action_proposals": [],
        "tool_trace": [],
        "evidence": [],
        "response_cards": [],
        "supplement_candidates": [],
        "persisted_supplements": [],
        "follow_up_suggestions": None,
        "trace_summary": None,
        "disambiguation": None,
        "external_asset_disambiguation": None,
        "resolved_context": None,
        "context_plan": None,
        "resolved_context_input": None,
        "run_info": None,
        "run_history": [],
        "reasoning_md": None,
        "reasoning_status": None,
        # R4-4: cold history truncated flag — None when no reasoning.
        "reasoning_truncated": None,
        "article_rag": None,
        "article_rag_citations": [],
    }


def _sanitize_turn_run_for_wire(turn_run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop internal diagnostic fields before nesting turn_run on a message."""
    if turn_run is None:
        return None
    cleaned = dict(turn_run)
    for key in (
        "terminal_reason",
        "envelope_fingerprint",
        "user_visible_output_json",
        "resolved_evidence_json",
        "reasoning_projection_json",
        "usage_summary_json",
    ):
        cleaned.pop(key, None)
    return cleaned


def _safe_reasoning_projection(
    turn_run: dict[str, Any] | None
) -> tuple[str | None, bool | None, str | None]:
    """Policy-gated cold restore of learner-reasoning summary.

    Reuses the live text/schema/stage/basis validator. Legacy
    ``reasoning_projection_v1`` remains fail-closed.
    Returns ``(text, truncated, stage)``.
    """
    if not isinstance(turn_run, dict):
        return None, None, None
    payload = turn_run.get("reasoning_projection_json")
    if not isinstance(payload, dict):
        return None, None, None
    from app.services.reader_record_ask.learner_reasoning.validator import (
        validate_cold_learner_payload,
    )

    text, stage, _basis = validate_cold_learner_payload(payload)
    if text is None:
        return None, None, None
    truncated = payload.get("truncated")
    trunc_flag = bool(truncated) if isinstance(truncated, bool) else False
    return text, trunc_flag, stage


def _safe_reasoning_projection_text(turn_run: dict[str, Any] | None) -> str | None:
    """Backward-compat wrapper returning only the text. Prefer
    :func:`_safe_reasoning_projection` for new callers.
    """
    text, _, _ = _safe_reasoning_projection(turn_run)
    return text


def _safe_degraded_message(
    *,
    message_id: str,
    thread_id: str,
    role: str,
    created_at: str | None,
    updated_at: str | None,
    context_anchors: list[Any],
    usage_event_id: str | None,
    current_turn_run_id: str | None,
    current_turn_run: dict[str, Any] | None,
) -> dict[str, Any]:
    """Corrupt / untrusted agentic payload — no answer, no evidence leak."""
    return {
        "id": message_id,
        "thread_id": thread_id,
        "role": role,
        "status": "failed",
        "content_md": "",
        "submission_mode": "chat",
        "resolved_intent": None,
        "context_anchors": context_anchors,
        **_empty_legacy_lists(),
        "usage_event_id": usage_event_id,
        "current_turn_run_id": current_turn_run_id,
        "current_turn_run": current_turn_run,
        "current_user_visible_output": None,
        "current_eval_trace": None,
        "created_at": created_at,
        "updated_at": updated_at,
        "execution_version": AGENTIC_EXECUTION_VERSION,
        "final_status": "failed",
        "agentic_answer_blocks": None,
        "agentic_citations": None,
        "knowledge_mode": None,
        "source_status": None,
        "legacy_classification": None,
    }


def _try_project_v2_completed(visible: dict[str, Any]) -> ReaderRecordAskCompletedDTO | None:
    try:
        completed = ReaderRecordAskCompletedDTO.model_validate(visible)
    except ValidationError:
        return None
    if completed.final_status != "ok":
        return None
    return completed


def project_agentic_history_message(
    *,
    message_id: str,
    thread_id: str,
    role: str,
    row_status: str,
    row_content_md: str,
    created_at: str | None,
    updated_at: str | None,
    context_anchors: list[Any] | None,
    usage_event_id: str | None,
    current_turn_run_id: str | None,
    current_turn_run: dict[str, Any] | None,
    user_visible_output_json: Any,
    resolved_evidence_json: Any,
    final_status: Any,
    turn_run_status: Any,
) -> dict[str, Any]:
    """Project one agentic turn into a Reading Record Ask message dict.

    Rules:
    - Trust DB ``final_status`` column as source of truth; JSON may only
      confirm it. Mismatch → safe degrade (P0).
    - Never hydrate legacy ``evidence`` / ``article_rag`` from agentic JSON.
    - Never emit ``envelope_fingerprint``, handles, or raw restricted evidence.
    - ``resolved_evidence_json`` is server-only and is never projected to history.
    """
    del row_status  # reserved for future streaming mid-run projection
    del resolved_evidence_json  # restricted server-only; never cold-project
    anchors = list(context_anchors or [])
    # ASK-REASONING-R1: cold reasoning comes only from the persisted
    # projection (committed atomically with the ok answer). Extracted
    # before wire sanitization strips the raw JSONB payload.
    # R4-4: extract both text and truncated flag from the same validated
    # snapshot so hot SSE, DB snapshot, and cold history stay consistent.
    reasoning_text, reasoning_truncated, learner_stage = _safe_reasoning_projection(
        current_turn_run
    )
    safe_turn_run = _sanitize_turn_run_for_wire(current_turn_run)
    base_kwargs = {
        "message_id": message_id,
        "thread_id": thread_id,
        "role": role,
        "created_at": created_at,
        "updated_at": updated_at,
        "context_anchors": anchors,
        "usage_event_id": usage_event_id,
        "current_turn_run_id": current_turn_run_id,
        "current_turn_run": safe_turn_run,
    }

    visible = user_visible_output_json if isinstance(user_visible_output_json, dict) else None
    if (
        isinstance(visible, dict)
        and visible.get("execution_version") != EXECUTION_VERSION_AGENTIC_V2
    ):
        return quarantine_untrusted_agentic_claim(**base_kwargs)
    # DB column is the only status authority when present.
    db_final = final_status if isinstance(final_status, str) else None

    if db_final == "ok":
        if not isinstance(visible, dict):
            return _safe_degraded_message(**base_kwargs)

        completed = _try_project_v2_completed(visible)
        if completed is not None:
            answer = completed.answer_text
            content_md = answer if answer else (row_content_md or "")
            return {
                "id": message_id,
                "thread_id": thread_id,
                "role": role,
                "status": "completed",
                "content_md": content_md,
                "submission_mode": "chat",
                "resolved_intent": None,
                "context_anchors": anchors,
                **_empty_legacy_lists(),
                # Learner-reasoning summary (policy learner_reasoning_v1).
                # Public fields use learner_reasoning_*; legacy reasoning_md
                # stays null on the agentic v2 cold path.
                "reasoning_md": None,
                "reasoning_status": None,
                "reasoning_truncated": None,
                "learner_reasoning_text": reasoning_text,
                "learner_reasoning_status": (
                    "completed" if reasoning_text is not None else None
                ),
                "learner_reasoning_stage": learner_stage,
                "usage_event_id": usage_event_id,
                "current_turn_run_id": current_turn_run_id,
                "current_turn_run": safe_turn_run,
                "current_user_visible_output": None,
                "current_eval_trace": None,
                "created_at": created_at,
                "updated_at": updated_at,
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                "final_status": "ok",
                "agentic_answer_blocks": [
                    block.model_dump(mode="json") for block in completed.answer_blocks
                ],
                # G0-b3: exclude_none so article citations keep their
                # pre-web shape (no url/title/description=None keys) while
                # web citations drop snippet=None. Web fields are only
                # present when source_kind="web".
                "agentic_citations": [
                    citation.model_dump(mode="json", exclude_none=True)
                    for citation in completed.citations
                ],
                "knowledge_mode": completed.knowledge_mode,
                "source_status": completed.source_status,
                # G0-b3: project web search summary for cold history
                # replay parity with hot SSE. None when web search was
                # not invoked this turn.
                "agentic_web_search": (
                    completed.web_search.model_dump(mode="json")
                    if completed.web_search is not None
                    else None
                ),
                "legacy_classification": None,
            }

        return _safe_degraded_message(**base_kwargs)

    if db_final in _TERMINAL_UI_STATUS:
        # If a terminal DTO is present it must agree with the DB column.
        if isinstance(visible, dict):
            try:
                terminal = ReaderRecordAskTerminalDTO.model_validate(visible)
            except ValidationError:
                return _safe_degraded_message(**base_kwargs)
            else:
                if terminal.final_status != db_final:
                    return _safe_degraded_message(**base_kwargs)

        ui_status = _TERMINAL_UI_STATUS[db_final]
        return {
            "id": message_id,
            "thread_id": thread_id,
            "role": role,
            "status": ui_status,
            "content_md": "",
            "submission_mode": "chat",
            "resolved_intent": None,
            "context_anchors": anchors,
            **_empty_legacy_lists(),
            "usage_event_id": usage_event_id,
            "current_turn_run_id": current_turn_run_id,
            "current_turn_run": safe_turn_run,
            "current_user_visible_output": None,
            "current_eval_trace": None,
            "created_at": created_at,
            "updated_at": updated_at,
            "execution_version": AGENTIC_EXECUTION_VERSION,
            "final_status": db_final,
            "agentic_answer_blocks": None,
            "agentic_citations": None,
            "knowledge_mode": None,
            "source_status": None,
            "legacy_classification": None,
        }

    # final_status column missing on an agentic row: incomplete / streaming /
    # corrupt. Never invent status from JSON alone.
    if turn_run_status == "streaming":
        return {
            "id": message_id,
            "thread_id": thread_id,
            "role": role,
            "status": "streaming",
            "content_md": row_content_md or "",
            "submission_mode": "chat",
            "resolved_intent": None,
            "context_anchors": anchors,
            **_empty_legacy_lists(),
            "usage_event_id": usage_event_id,
            "current_turn_run_id": current_turn_run_id,
            "current_turn_run": safe_turn_run,
            "current_user_visible_output": None,
            "current_eval_trace": None,
            "created_at": created_at,
            "updated_at": updated_at,
            "execution_version": AGENTIC_EXECUTION_VERSION,
            "final_status": None,
            "agentic_answer_blocks": None,
            "agentic_citations": None,
            "knowledge_mode": None,
            "source_status": None,
            "legacy_classification": None,
        }

    return _safe_degraded_message(**base_kwargs)
