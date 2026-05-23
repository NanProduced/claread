from __future__ import annotations

from typing import Any

from app.schemas.reader_ask import (
    ReaderAskCompletedPayload,
    ReaderAskContextPlan,
    ReaderAskDisambiguation,
    ReaderAskEvidenceItem,
    ReaderAskMessage,
    ReaderAskPersistedSupplement,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
    ReaderAskResponseCard,
    ReaderAskRunInfo,
    ReaderAskSubmissionMode,
    ReaderAskSupplementCandidate,
    ReaderAskToolTraceEntry,
    ReaderAskTraceSummary,
    ReaderAskUserVisibleOutput,
    ReaderAskAssetDisambiguation,
    ReaderAskActionProposal,
    ReaderAskCitation,
)


def build_user_message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    submission_mode: ReaderAskSubmissionMode = "chat",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"submission_mode": submission_mode}
    if resolved_intent is not None:
        metadata["resolved_intent"] = resolved_intent
    if resolved_context_input is not None:
        metadata["resolved_context_input"] = resolved_context_input.model_dump(mode="json")
    return metadata


def build_assistant_message_metadata(
    *,
    resolved_intent: ReaderAskResolvedIntent | None = None,
    run_info: dict[str, Any] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    resolved_context_input: ReaderAskResolvedContextInput | None = None,
    submission_mode: ReaderAskSubmissionMode = "chat",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"submission_mode": submission_mode}
    if resolved_intent is not None:
        metadata["resolved_intent"] = resolved_intent
    if run_info is not None:
        metadata["run_info"] = run_info
    if run_history:
        metadata["run_history"] = run_history
    if resolved_context_input is not None:
        metadata["resolved_context_input"] = resolved_context_input.model_dump(mode="json")
    return metadata


def build_user_visible_output(
    *,
    content_md: str,
    submission_mode: ReaderAskSubmissionMode,
    resolved_intent: ReaderAskResolvedIntent | None,
    citations: list[ReaderAskCitation],
    action_proposals: list[ReaderAskActionProposal],
    tool_trace: list[ReaderAskToolTraceEntry],
    evidence: list[ReaderAskEvidenceItem],
    trace_summary: ReaderAskTraceSummary | None,
    disambiguation: ReaderAskDisambiguation | None,
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None,
    response_cards: list[ReaderAskResponseCard],
    usage_summary: dict[str, Any] | None,
    billed_points: int,
    resolved_context: ReaderAskResolvedContextSummary,
    context_plan: ReaderAskContextPlan | None,
    resolved_context_input: ReaderAskResolvedContextInput | None,
    run_info: dict[str, Any] | ReaderAskRunInfo | None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | list[dict[str, Any]],
    persisted_supplements: list[ReaderAskPersistedSupplement] | list[dict[str, Any]],
) -> ReaderAskUserVisibleOutput:
    normalized_run_info = (
        run_info
        if isinstance(run_info, ReaderAskRunInfo) or run_info is None
        else ReaderAskRunInfo.model_validate(run_info)
    )
    normalized_candidates = [
        item if isinstance(item, ReaderAskSupplementCandidate) else ReaderAskSupplementCandidate.model_validate(item)
        for item in supplement_candidates
    ]
    normalized_persisted = [
        item if isinstance(item, ReaderAskPersistedSupplement) else ReaderAskPersistedSupplement.model_validate(item)
        for item in persisted_supplements
    ]
    return ReaderAskUserVisibleOutput(
        content_md=content_md,
        submission_mode=submission_mode,
        resolved_intent=resolved_intent,
        citations=citations,
        action_proposals=action_proposals,
        tool_trace=tool_trace,
        evidence=evidence,
        trace_summary=trace_summary,
        disambiguation=disambiguation,
        external_asset_disambiguation=external_asset_disambiguation,
        response_cards=response_cards,
        usage_summary=usage_summary,
        billed_points=billed_points,
        resolved_context=resolved_context,
        context_plan=context_plan,
        resolved_context_input=resolved_context_input,
        run_info=normalized_run_info,
        supplement_candidates=normalized_candidates,
        persisted_supplements=normalized_persisted,
    )


def to_completed_payload(
    *,
    message_id: str,
    thread_id: str,
    output: ReaderAskUserVisibleOutput,
) -> ReaderAskCompletedPayload:
    return ReaderAskCompletedPayload(
        id=message_id,
        thread_id=thread_id,
        **output.model_dump(mode="python"),
    )


def visible_output_from_message(message: ReaderAskMessage, message_dict: dict[str, Any]) -> dict[str, Any]:
    current = message_dict.get("current_user_visible_output")
    if isinstance(current, dict):
        return dict(current)
    current_turn_run = message_dict.get("current_turn_run") or {}
    output = build_user_visible_output(
        content_md=message.content_md,
        submission_mode=message.submission_mode,
        resolved_intent=message.resolved_intent,
        citations=message.citations,
        action_proposals=message.action_proposals,
        tool_trace=message.tool_trace,
        evidence=message.evidence,
        trace_summary=message.trace_summary,
        disambiguation=message.disambiguation,
        external_asset_disambiguation=message.external_asset_disambiguation,
        response_cards=message.response_cards,
        usage_summary=current_turn_run.get("usage_summary_json"),
        billed_points=int((current_turn_run.get("user_visible_output_json") or {}).get("billed_points") or 0),
        resolved_context=message.resolved_context,
        context_plan=message.context_plan,
        resolved_context_input=message.resolved_context_input,
        run_info=message.run_info,
        supplement_candidates=message.supplement_candidates,
        persisted_supplements=message.persisted_supplements,
    )
    return output.model_dump(mode="json")
