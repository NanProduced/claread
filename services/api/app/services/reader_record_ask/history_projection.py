"""History projection for Agentic Reading Record Ask turns.

Cold-load / thread-detail path only. Does **not** touch legacy
``output_contract.USER_VISIBLE_OUTPUT_FIELDS`` and never maps agentic
evidence into legacy ``evidence`` or ``article_rag``.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.schemas.reader_record_ask_stream import (
    EXECUTION_VERSION_AGENTIC_V1,
    ReaderRecordAskCompletedDTO,
    ReaderRecordAskEvidenceItem,
    ReaderRecordAskTerminalDTO,
)

AGENTIC_EXECUTION_VERSION = EXECUTION_VERSION_AGENTIC_V1

_TERMINAL_UI_STATUS: dict[str, str] = {
    "failed": "failed",
    "context_stale": "interrupted",
    "invalid_citations": "interrupted",
    "cancelled": "interrupted",
}


def is_agentic_execution_version(value: Any) -> bool:
    return value == AGENTIC_EXECUTION_VERSION


def claims_agentic_payload(value: Any) -> bool:
    """True when a persisted JSON blob self-identifies as agentic v1.

    Used only for isolation: such blobs must not enter legacy evidence
    hydration. They are never treated as a trusted successful agentic fact
    without a matching DB ``execution_version`` column.
    """
    if not isinstance(value, dict):
        return False
    return value.get("execution_version") == AGENTIC_EXECUTION_VERSION


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
    """DB version missing/non-v1 but JSON claims v1 → isolate, do not parse.

    Emits a failed empty message with **no** legacy evidence and **no**
    agentic_evidence, so substrate/index/hash cannot leak through the
    loose legacy evidence channel.
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
        "agentic_evidence": None,
        "agentic_evidence_scope": None,
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
        "usage_summary_json",
    ):
        cleaned.pop(key, None)
    return cleaned


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
    """Corrupt / untrusted agentic payload → no answer, no evidence leak."""
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
        "agentic_evidence": None,
        "agentic_evidence_scope": None,
    }


def _validate_evidence_items(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(ReaderRecordAskEvidenceItem.model_validate(item).model_dump(mode="json"))
        except ValidationError:
            return None
    return out


def _completed_evidence(
    completed: ReaderRecordAskCompletedDTO,
    resolved_evidence_json: Any,
) -> list[dict[str, Any]]:
    """Prefer validated completed.evidence; restricted fallback to resolved_evidence_json."""
    primary = [item.model_dump(mode="json") for item in completed.evidence]
    if primary:
        return primary
    fallback = _validate_evidence_items(resolved_evidence_json)
    return fallback if fallback is not None else []


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
    - Never emit ``envelope_fingerprint`` or raw ``terminal_reason``.
    - ``resolved_evidence_json`` is only a restricted fallback after completed
      DTO validation — never mapped to legacy evidence.
    """
    del row_status  # reserved for future streaming mid-run projection
    anchors = list(context_anchors or [])
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
    # DB column is the only status authority when present.
    db_final = final_status if isinstance(final_status, str) else None

    if db_final == "ok":
        if not isinstance(visible, dict):
            return _safe_degraded_message(**base_kwargs)
        try:
            completed = ReaderRecordAskCompletedDTO.model_validate(visible)
        except ValidationError:
            return _safe_degraded_message(**base_kwargs)
        # Completed DTO is always final_status=ok by schema; still require match
        # against DB fact (defensive if schema ever widens).
        if completed.final_status != "ok":
            return _safe_degraded_message(**base_kwargs)

        answer = completed.answer_text
        content_md = answer if answer else (row_content_md or "")
        # Scope only from validated completed DTO — never invent from page or fingerprint.
        # None on old v1 rows: answer/evidence still hydrate; navigation must treat as
        # unavailable.legacy_scope_missing (no rag_citation-only or page-identity fallback).
        scope_wire = (
            completed.evidence_scope.model_dump(mode="json")
            if completed.evidence_scope is not None
            else None
        )
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
            "usage_event_id": usage_event_id,
            "current_turn_run_id": current_turn_run_id,
            "current_turn_run": safe_turn_run,
            "current_user_visible_output": None,
            "current_eval_trace": None,
            "created_at": created_at,
            "updated_at": updated_at,
            "execution_version": AGENTIC_EXECUTION_VERSION,
            "final_status": "ok",
            "agentic_evidence": _completed_evidence(completed, resolved_evidence_json),
            "agentic_evidence_scope": scope_wire,
        }

    if db_final in _TERMINAL_UI_STATUS:
        # If a terminal DTO is present it must agree with the DB column.
        if isinstance(visible, dict):
            try:
                terminal = ReaderRecordAskTerminalDTO.model_validate(visible)
            except ValidationError:
                terminal = None
            if terminal is not None and terminal.final_status != db_final:
                # DB vs JSON mismatch → corrupt persistence, do not let JSON win.
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
            "agentic_evidence": None,
            "agentic_evidence_scope": None,
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
            "agentic_evidence": None,
            "agentic_evidence_scope": None,
        }

    return _safe_degraded_message(**base_kwargs)
