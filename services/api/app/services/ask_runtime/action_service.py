from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskActionConfirmResponse,
    ReaderAskActionConfirmResult,
    ReaderAskActionProposal,
    ReaderAskDeleteSupplementResponse,
    ReaderAskMessage,
    ReaderAskPersistedSupplement,
    ReaderAskReadingRecordAnchor,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedIntent,
    ReaderAskRunInfo,
    ReaderAskSubmissionMode,
    ReaderAskSupplementCandidate,
    ReaderAskTraceSummary,
    ReaderAskWriteProposalPayload,
)
from app.schemas.reader_notes import ReaderNoteCreateRequest
from app.schemas.user_annotations import UserAnnotationCreateRequest
from app.services import reader_notes as reader_notes_svc
from app.services import user_annotations as user_annotations_svc
from app.services.reader_ask import output_contract as output_contract_svc
from app.services.reader_ask import repository as repo
from app.services.reader_ask import supplements as supplements_svc


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _assistant_message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    run_info: dict[str, Any] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    submission_mode: ReaderAskSubmissionMode = "chat",
) -> dict[str, Any]:
    return output_contract_svc.build_assistant_message_metadata(
        resolved_intent=resolved_intent,
        run_info=run_info,
        run_history=run_history,
        resolved_context_input=resolved_context_input,
        submission_mode=submission_mode,
    )


def _current_turn_run_id(message_dict: dict[str, Any], run_info: ReaderAskRunInfo | None = None) -> UUID | None:
    current_turn_run_id = message_dict.get("current_turn_run_id")
    if isinstance(current_turn_run_id, str) and current_turn_run_id.strip():
        try:
            return UUID(current_turn_run_id)
        except ValueError:
            return None
    run_id = run_info.run_id if run_info is not None else None
    if isinstance(run_id, str) and run_id.strip():
        try:
            return UUID(run_id)
        except ValueError:
            return None
    return None


def _visible_output_from_message(message: ReaderAskMessage, message_dict: dict[str, Any]) -> dict[str, Any]:
    return output_contract_svc.visible_output_from_message(message, message_dict)


def _normalize_persisted_supplements(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        supplement_id = str(item.get("supplement_id") or "").strip()
        if not supplement_id or supplement_id in seen:
            continue
        seen.add(supplement_id)
        normalized.append(dict(item))
    return normalized


def _upsert_persisted_supplement(
    items: list[dict[str, Any]] | None,
    supplement: ReaderAskPersistedSupplement,
) -> list[dict[str, Any]]:
    supplement_json = supplement.model_dump(mode="json")
    next_items = _normalize_persisted_supplements(items)
    for index, item in enumerate(next_items):
        if item.get("supplement_id") == supplement.supplement_id:
            next_items[index] = supplement_json
            return next_items
    next_items.append(supplement_json)
    return next_items


def _mark_deleted_persisted_supplement(
    items: list[dict[str, Any]] | None,
    supplement: ReaderAskPersistedSupplement,
) -> list[dict[str, Any]]:
    supplement_json = supplement.model_dump(mode="json")
    next_items = _normalize_persisted_supplements(items)
    for index, item in enumerate(next_items):
        if item.get("supplement_id") == supplement.supplement_id:
            next_items[index] = supplement_json
            return next_items
    next_items.append(supplement_json)
    return next_items


def _canonical_record_id(thread: dict[str, Any]) -> str:
    record_id = thread.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise HTTPException(status_code=400, detail="thread record_id is invalid")
    return record_id


def _annotation_request_from_rr_anchor(anchor: ReaderAskReadingRecordAnchor) -> UserAnnotationCreateRequest:
    return UserAnnotationCreateRequest(
        anchor_type="text_range",
        selected_text=anchor.selected_text,
        color="soft_green",
        payload_json={"source": "reader_ask"},
        anchor=anchor,
    )


def _reader_note_request_from_rr_anchor(
    anchor: ReaderAskReadingRecordAnchor,
    *,
    note_text: str,
) -> ReaderNoteCreateRequest:
    return ReaderNoteCreateRequest(
        quote_mode="text_range",
        selected_text=anchor.selected_text,
        note_text=note_text,
        payload_json={"source": "reader_ask"},
        anchor=anchor,
    )


async def _update_turn_run_audits(
    *,
    turn_run_id: UUID | None,
    message: ReaderAskMessage,
    message_dict: dict[str, Any],
    updated_proposals: list[ReaderAskActionProposal],
    updated_evidence: list[Any],
    updated_trace_summary: ReaderAskTraceSummary | None,
    persisted_supplements: list[dict[str, Any]],
    action_id: str,
    decision: str,
    proposal: ReaderAskActionProposal,
    supplement_result: ReaderAskPersistedSupplement | None = None,
) -> None:
    if turn_run_id is None:
        return

    visible_output = _visible_output_from_message(message, message_dict)
    visible_output["action_proposals"] = [item.model_dump(mode="json") for item in updated_proposals]
    visible_output["evidence"] = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        for item in updated_evidence
    ]
    visible_output["trace_summary"] = (
        updated_trace_summary.model_dump(mode="json") if updated_trace_summary is not None else None
    )
    visible_output["persisted_supplements"] = persisted_supplements
    await repo.update_turn_run(
        turn_run_id=turn_run_id,
        status=message.status,
        user_visible_output_json=visible_output,
    )

    existing_trace = await repo.get_eval_trace(turn_run_id)
    action_audit = list((existing_trace or {}).get("action_audit_json") or [])
    action_audit.append(
        {
            "action_id": action_id,
            "action_type": proposal.action_type,
            "decision": decision,
            "timestamp": _iso_now(),
            "status_after_decision": "executed" if decision == "confirmed" else "rejected",
        }
    )
    supplement_audit = list((existing_trace or {}).get("supplement_audit_json") or [])
    if supplement_result is not None:
        supplement_audit.append(
            {
                "event": "persisted",
                "supplement_id": supplement_result.supplement_id,
                "supplement_type": supplement_result.supplement_type,
                "created_from_turn_run_id": supplement_result.created_from_turn_run_id,
                "timestamp": _iso_now(),
            }
        )
    await repo.upsert_eval_trace(
        turn_run_id=turn_run_id,
        trace_schema_version=(existing_trace or {}).get("trace_schema_version") or "reader-ask-eval-trace-v1",
        planning_snapshot_json=(existing_trace or {}).get("planning_snapshot_json") or {},
        capability_trace_json=(existing_trace or {}).get("capability_trace_json") or {},
        action_audit_json=action_audit,
        supplement_audit_json=supplement_audit,
        metrics_json=(existing_trace or {}).get("metrics_json") or {},
    )


async def confirm_action(
    *,
    user_id: UUID,
    thread_id: UUID,
    action_id: str,
    body: ReaderAskActionConfirmRequest,
    expected_reading_record_id: UUID | None = None,
) -> ReaderAskActionConfirmResponse:
    message_dict, proposal_dict = await repo.find_action_proposal(
        user_id=user_id,
        thread_id=thread_id,
        action_id=action_id,
    )
    if message_dict is None or proposal_dict is None:
        raise HTTPException(status_code=404, detail="Reader ask action proposal not found")

    proposal_status = proposal_dict.get("status")
    if proposal_status == "executed":
        persisted_result = proposal_dict.get("result_json") or {}
        return ReaderAskActionConfirmResponse(
            ok=True,
            action_id=action_id,
            status="executed",
            result=ReaderAskActionConfirmResult.model_validate(persisted_result),
        )
    if proposal_status == "rejected" and body.confirmed:
        raise HTTPException(status_code=409, detail="Action proposal has already been rejected")

    message = ReaderAskMessage.model_validate(message_dict)
    proposal = ReaderAskActionProposal.model_validate(proposal_dict)
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if expected_reading_record_id is not None:
        if thread.get("record_scope") != "reading_record":
            raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")
        if thread.get("reading_record_id") != str(expected_reading_record_id):
            raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")

    run_history = list(message_dict.get("run_history") or [])
    persisted_supplements = _normalize_persisted_supplements(
        [item.model_dump(mode="json") for item in message.persisted_supplements]
    )
    turn_run_id = _current_turn_run_id(message_dict, message.run_info)

    if not body.confirmed:
        updated_proposals = [
            proposal_item.model_copy(update={"status": "rejected"}) if proposal_item.id == action_id else proposal_item
            for proposal_item in message.action_proposals
        ]
        await repo.update_message(
            message_id=_parse_uuid(message.id, "message id is invalid"),
            status=message.status,
            content_md=message.content_md,
            context_anchors=[anchor.model_dump(mode="json") for anchor in message.context_anchors],
            citations=[citation.model_dump(mode="json") for citation in message.citations],
            action_proposals=[item.model_dump(mode="json") for item in updated_proposals],
            tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
            metadata=_assistant_message_metadata(
                resolved_intent=message.resolved_intent,
                run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
                run_history=run_history,
                resolved_context_input=message.resolved_context_input,
            ),
            usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
            current_turn_run_id=turn_run_id,
        )
        await _update_turn_run_audits(
            turn_run_id=turn_run_id,
            message=message,
            message_dict=message_dict,
            updated_proposals=updated_proposals,
            updated_evidence=list(message.evidence),
            updated_trace_summary=message.trace_summary,
            persisted_supplements=persisted_supplements,
            action_id=action_id,
            decision="rejected",
            proposal=proposal,
        )
        return ReaderAskActionConfirmResponse(ok=True, action_id=action_id, status="rejected")

    executing_proposals = [
        proposal_item.model_copy(update={"status": "executing"}) if proposal_item.id == action_id else proposal_item
        for proposal_item in message.action_proposals
    ]
    await repo.update_message(
        message_id=_parse_uuid(message.id, "message id is invalid"),
        status=message.status,
        content_md=message.content_md,
        context_anchors=[anchor.model_dump(mode="json") for anchor in message.context_anchors],
        citations=[citation.model_dump(mode="json") for citation in message.citations],
        action_proposals=[item.model_dump(mode="json") for item in executing_proposals],
        tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
        metadata=_assistant_message_metadata(
            resolved_intent=message.resolved_intent,
            run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
            run_history=run_history,
            resolved_context_input=message.resolved_context_input,
        ),
        usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
        current_turn_run_id=turn_run_id,
    )

    result = ReaderAskActionConfirmResult()
    updated_trace_summary = message.trace_summary
    updated_evidence = list(message.evidence)
    thread_scope = thread.get("record_scope")
    thread_record_id = _canonical_record_id(thread)
    thread_record_uuid = _parse_uuid(thread_record_id, "thread record_id is invalid")

    if proposal.action_type == "create_supplement_grammar_note":
        candidate_payload = proposal.payload_json.get("candidate")
        if not isinstance(candidate_payload, dict):
            raise HTTPException(status_code=400, detail="Action proposal is missing supplement candidate")
        candidate = ReaderAskSupplementCandidate.model_validate(candidate_payload)
        created = await supplements_svc.create_supplement(
            user_id=user_id,
            record_id=thread_record_uuid if thread_scope == "analysis" else None,
            reading_record_id=thread_record_uuid if thread_scope == "reading_record" else None,
            candidate=candidate,
        )
        record_title = thread.get("title")
        persisted_supplement = supplements_svc.row_to_persisted_supplement(
            created,
            record_title=record_title,
        )
        persisted_supplements = _upsert_persisted_supplement(persisted_supplements, persisted_supplement)
        if updated_trace_summary is not None:
            updated_trace_summary = updated_trace_summary.model_copy(
                update={
                    "supplement_persisted_count": len(
                        [item for item in persisted_supplements if item.get("lifecycle_status") == "persisted"]
                    ),
                }
            )
        result = ReaderAskActionConfirmResult(
            record_id=persisted_supplement.record_id,
            supplement_projection=supplements_svc.supplement_projection_entry(created),
            persisted_supplement=persisted_supplement,
        )
    else:
        payload = ReaderAskWriteProposalPayload.model_validate(proposal.payload_json)
        anchor = payload.anchor
        if anchor is None:
            raise HTTPException(status_code=400, detail="Action proposal is missing anchor payload")

        if isinstance(anchor, ReaderAskReadingRecordAnchor):
            if anchor.record_id != thread_record_id:
                raise HTTPException(status_code=400, detail="Action proposal anchor record_id does not match thread")
            if proposal.action_type == "save_highlight":
                annotation = await user_annotations_svc.create_user_annotation(
                    user_id,
                    _annotation_request_from_rr_anchor(anchor),
                )
                result = ReaderAskActionConfirmResult(
                    annotation_id=str(annotation.id),
                    annotation_type="highlight",
                    target_key=annotation.target_key,
                )
            elif proposal.action_type == "save_note":
                note_text = payload.note_text
                if not isinstance(note_text, str) or not note_text.strip():
                    raise HTTPException(status_code=400, detail="Action proposal is missing note_text")
                note = await reader_notes_svc.create_reader_note(
                    user_id,
                    _reader_note_request_from_rr_anchor(anchor, note_text=note_text),
                )
                result = ReaderAskActionConfirmResult(
                    target_key=note.target_key,
                    note_id=str(note.id),
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported action type: {proposal.action_type}")
        else:
            from app.services.reader_ask.service import (
                _annotation_request_from_anchor,
                _reader_note_request_from_anchor,
            )

            if proposal.action_type == "save_highlight":
                annotation = await user_annotations_svc.create_user_annotation(
                    user_id,
                    _annotation_request_from_anchor(record_id=thread_record_uuid, anchor=anchor),
                )
                result = ReaderAskActionConfirmResult(
                    annotation_id=str(annotation.id),
                    annotation_type="highlight",
                    target_key=annotation.target_key,
                )
            elif proposal.action_type == "save_note":
                note_text = payload.note_text
                if not isinstance(note_text, str) or not note_text.strip():
                    raise HTTPException(status_code=400, detail="Action proposal is missing note_text")
                note = await reader_notes_svc.create_reader_note(
                    user_id,
                    _reader_note_request_from_anchor(
                        record_id=thread_record_uuid,
                        anchor=anchor,
                        note_text=note_text,
                    ),
                )
                result = ReaderAskActionConfirmResult(
                    target_key=note.target_key,
                    note_id=str(note.id),
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported action type: {proposal.action_type}")

    updated_proposals = [
        proposal_item.model_copy(update={"status": "executed", "result_json": result.model_dump(mode="json")})
        if proposal_item.id == action_id
        else proposal_item
        for proposal_item in message.action_proposals
    ]
    await repo.update_message(
        message_id=_parse_uuid(message.id, "message id is invalid"),
        status=message.status,
        content_md=message.content_md,
        context_anchors=[anchor_item.model_dump(mode="json") for anchor_item in message.context_anchors],
        citations=[citation.model_dump(mode="json") for citation in message.citations],
        action_proposals=[item.model_dump(mode="json") for item in updated_proposals],
        tool_trace=[item.model_dump(mode="json") for item in message.tool_trace],
        metadata=_assistant_message_metadata(
            resolved_intent=message.resolved_intent,
            run_info=message.run_info.model_dump(mode="json") if message.run_info else None,
            run_history=run_history,
            resolved_context_input=message.resolved_context_input,
        ),
        usage_event_id=_parse_uuid(message.usage_event_id, "usage_event_id is invalid") if message.usage_event_id else None,
        current_turn_run_id=turn_run_id,
    )
    await _update_turn_run_audits(
        turn_run_id=turn_run_id,
        message=message,
        message_dict=message_dict,
        updated_proposals=updated_proposals,
        updated_evidence=updated_evidence,
        updated_trace_summary=updated_trace_summary,
        persisted_supplements=persisted_supplements,
        action_id=action_id,
        decision="confirmed",
        proposal=proposal,
        supplement_result=result.persisted_supplement,
    )
    return ReaderAskActionConfirmResponse(ok=True, action_id=action_id, status="executed", result=result)


async def delete_supplement(
    *,
    user_id: UUID,
    supplement_id: UUID,
    expected_reading_record_id: UUID | None = None,
) -> ReaderAskDeleteSupplementResponse:
    supplement = await supplements_svc.get_supplement_projection_or_404(user_id, supplement_id)
    if expected_reading_record_id is not None:
        if str(supplement.get("reading_record_id") or "") != str(expected_reading_record_id):
            raise HTTPException(status_code=404, detail="Reader ask supplement not found for this Reading Record")
    elif supplement.get("analysis_record_id") is not None:
        await repo.ensure_record_access(
            user_id,
            _parse_uuid(str(supplement["analysis_record_id"]), "supplement record id is invalid"),
        )

    deleted = await supplements_svc.delete_supplement(user_id, supplement_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Reader ask supplement not found")

    persisted_supplement = supplements_svc.row_to_persisted_supplement(
        deleted,
        record_title=None,
        lifecycle_status="deleted",
    )
    source_turn_run_id = _parse_uuid(
        persisted_supplement.created_from_turn_run_id,
        "supplement created_from_turn_run_id is invalid",
    )
    source_turn_run = await repo.get_turn_run(source_turn_run_id)
    if source_turn_run is not None:
        source_message_id = _parse_uuid(source_turn_run["message_id"], "turn run message id is invalid")
        turn_runs = await repo.list_turn_runs_for_message(source_message_id)
        for turn_run in turn_runs:
            turn_run_id = _parse_uuid(turn_run["id"], "turn run id is invalid")
            output = dict(turn_run.get("user_visible_output_json") or {})
            output["persisted_supplements"] = _mark_deleted_persisted_supplement(
                list(output.get("persisted_supplements") or []),
                persisted_supplement,
            )
            await repo.update_turn_run(
                turn_run_id=turn_run_id,
                status=turn_run["status"],
                user_visible_output_json=output,
            )
        existing_trace = await repo.get_eval_trace(source_turn_run_id)
        supplement_audit = list((existing_trace or {}).get("supplement_audit_json") or [])
        supplement_audit.append(
            {
                "event": "deleted",
                "supplement_id": persisted_supplement.supplement_id,
                "supplement_type": persisted_supplement.supplement_type,
                "created_from_turn_run_id": persisted_supplement.created_from_turn_run_id,
                "timestamp": _iso_now(),
            }
        )
        await repo.upsert_eval_trace(
            turn_run_id=source_turn_run_id,
            trace_schema_version=(existing_trace or {}).get("trace_schema_version") or "reader-ask-eval-trace-v1",
            planning_snapshot_json=(existing_trace or {}).get("planning_snapshot_json") or {},
            capability_trace_json=(existing_trace or {}).get("capability_trace_json") or {},
            action_audit_json=(existing_trace or {}).get("action_audit_json") or [],
            supplement_audit_json=supplement_audit,
            metrics_json=(existing_trace or {}).get("metrics_json") or {},
        )
    return ReaderAskDeleteSupplementResponse(
        deleted=True,
        supplement_id=str(supplement_id),
        record_id=persisted_supplement.record_id,
        target_key=persisted_supplement.target_key,
        lifecycle_status="deleted",
        persisted_supplement=persisted_supplement,
    )
