from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskEntryAction,
    ReaderAskPageIdentity,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedIntent,
)
from app.services.reader_ask import planner
from app.services.reader_ask import prompting as prompt_layers_svc
from app.services.reader_ask import utils


def build_structured_history_summary(
    history_messages: list[dict[str, Any]],
    *,
    recent_window: int,
    max_intents: int = 5,
    max_refs: int = 5,
    max_anchors: int = 3,
) -> dict[str, Any] | None:
    """Extract structured state from messages outside the recent window.

    This preserves key context (resolved intents, reference resolutions, anchors)
    that would otherwise be lost when older messages are truncated. The summary
    is compact and deterministic — no LLM calls.

    Returns None if there are no messages outside the recent window or no
    extractable structured state.
    """
    if len(history_messages) <= recent_window:
        return None

    older_messages = history_messages[:-recent_window]
    intents: list[str] = []
    resolved_refs: list[dict[str, str]] = []
    ambiguous_refs: list[dict[str, str]] = []
    anchor_summaries: list[dict[str, str]] = []

    for msg in older_messages:
        if msg.get("role") != "user":
            continue
        # Collect resolved intents (capped)
        intent = msg.get("resolved_intent")
        if intent and intent not in intents and len(intents) < max_intents:
            intents.append(intent)
        # Collect resolved reference records with semantic info
        context_plan = msg.get("context_plan")
        if isinstance(context_plan, dict) and len(resolved_refs) < max_refs:
            ref_query = context_plan.get("reference_query")
            ref_status = context_plan.get("reference_resolution_status")
            if ref_status == "resolved":
                expanded = context_plan.get("expanded_record_ids", [])
                for rid in expanded:
                    if len(resolved_refs) >= max_refs:
                        break
                    if not any(r.get("record_id") == rid for r in resolved_refs):
                        resolved_refs.append({
                            "record_id": rid,
                            "alias": ref_query or rid,
                        })
        # Collect ambiguous/disambiguation candidates separately
        # These are NOT resolved — the user saw candidates but may not have
        # confirmed which one. Keeping them separate avoids misleading the
        # model into thinking these references were already resolved.
        disambiguation = msg.get("disambiguation")
        if isinstance(disambiguation, dict) and len(ambiguous_refs) < max_refs:
            dis_query = disambiguation.get("query")
            candidates = disambiguation.get("candidates", [])
            for cand in candidates:
                if len(ambiguous_refs) >= max_refs:
                    break
                if not isinstance(cand, dict):
                    continue
                rid = str(cand.get("record_id") or "")
                title = str(cand.get("title") or "")
                if rid and not any(r.get("record_id") == rid for r in ambiguous_refs):
                    ambiguous_refs.append({
                        "record_id": rid,
                        "alias": title or dis_query or rid,
                    })
        # Collect anchor summaries (capped)
        anchors = msg.get("context_anchors")
        if isinstance(anchors, list):
            for anchor in anchors:
                if len(anchor_summaries) >= max_anchors:
                    break
                if isinstance(anchor, dict):
                    sel = str(anchor.get("selected_text") or "")[:60]
                    atype = str(anchor.get("anchor_type") or "")
                    if sel and not any(a.get("selected_text") == sel for a in anchor_summaries):
                        anchor_summaries.append({"selected_text": sel, "anchor_type": atype})

    if not intents and not resolved_refs and not ambiguous_refs and not anchor_summaries:
        return None

    summary: dict[str, Any] = {}
    if intents:
        summary["prior_intents"] = intents
    if resolved_refs:
        summary["prior_resolved_references"] = resolved_refs
    if ambiguous_refs:
        summary["prior_disambiguation_candidates"] = ambiguous_refs
    if anchor_summaries:
        summary["prior_anchors"] = anchor_summaries
    return summary


def format_structured_history_summary(summary: dict[str, Any], *, max_chars: int = 500) -> str:
    """Format a structured history summary into a human-readable string.

    The output is capped at max_chars to prevent unbounded growth in the
    system message, which preserved during compaction.
    """
    summary_parts: list[str] = []
    if "prior_intents" in summary:
        intents_str = ", ".join(summary["prior_intents"])
        summary_parts.append(f"Previous intents: {intents_str}")
    if "prior_resolved_references" in summary:
        refs = summary["prior_resolved_references"]
        refs_str = "; ".join(r.get("alias", r["record_id"]) for r in refs)
        summary_parts.append(f"Previously resolved references: {refs_str}")
    if "prior_disambiguation_candidates" in summary:
        candidates = summary["prior_disambiguation_candidates"]
        cands_str = "; ".join(c.get("alias", c["record_id"]) for c in candidates)
        summary_parts.append(f"Previously suggested candidates (not confirmed): {cands_str}")
    if "prior_anchors" in summary:
        anchors = summary["prior_anchors"]
        anchors_str = "; ".join(
            f'"{a["selected_text"]}" ({a["anchor_type"]})' for a in anchors
        )
        summary_parts.append(f"Previously discussed text: {anchors_str}")
    result = "[History summary] " + " | ".join(summary_parts)
    if len(result) > max_chars:
        result = result[:max_chars - 3] + "..."
    return result


@dataclass(slots=True)
class ReaderAskAnswerRuntimeInput:
    thread: dict[str, Any]
    record: Any
    user_message: str
    history_messages: list[dict[str, Any]]
    page_identity: ReaderAskPageIdentity
    attachments: list[ReaderAskAttachment]
    anchors: list[ReaderAskAnchorRef]
    resolved_intent: ReaderAskResolvedIntent
    resolved_intent_label: str
    entry_action: ReaderAskEntryAction
    submission_mode: str
    cross_record_context_allowed: bool
    resolved_context_input: ReaderAskResolvedContextInput | None
    quick_action_annotation: dict[str, Any] | None
    reference_resolution: planner.ReaderAskReferenceResolution | None
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None
    max_history_messages: int
    max_message_text: int
    followup_hint: str | None = None
    cross_record_intent_hint: str | None = None
    external_attachment_hint: str | None = None


def _truncate_history_message(content: str | None, *, role: str, limit: int) -> str:
    normalized = utils.normalize_text(content)
    if len(normalized) <= limit:
        return normalized
    if role != "assistant":
        return utils.truncate_text(normalized, limit)

    head_limit = max(limit // 2, 200)
    tail_limit = max(limit - head_limit - 5, 120)
    head = normalized[:head_limit].rstrip()
    tail = normalized[-tail_limit:].lstrip()
    return f"{head}\n...\n{tail}"


def _entry_action_guidance(entry_action: ReaderAskEntryAction) -> str | None:
    if entry_action == "why_here":
        return (
            "This request came from why_here. Prioritize explaining the local grammar or writing "
            "choice in the current sentence before expanding outward."
        )
    return None


def build_prompt_payload(contract: ReaderAskAnswerRuntimeInput) -> dict[str, Any]:
    prompt_layers = prompt_layers_svc.load_prompt_layers()

    # Build structured summary from messages outside the recent window
    structured_summary = build_structured_history_summary(
        contract.history_messages, recent_window=contract.max_history_messages
    )
    summary_message: dict[str, Any] | None = None
    if structured_summary:
        summary_message = {
            "role": "system",
            "content_md": format_structured_history_summary(structured_summary),
        }

    history = []
    if summary_message:
        history.append(summary_message)
    recent_history = contract.history_messages[-contract.max_history_messages:]
    last_recent_user_index = max(
        (index for index, item in enumerate(recent_history) if item.get("role") == "user"),
        default=-1,
    )
    for index, item in enumerate(recent_history):
        history_item = {
            "role": item["role"],
            "content_md": _truncate_history_message(
                str(item.get("content_md") or ""),
                role=str(item.get("role") or ""),
                limit=contract.max_message_text,
            ),
        }
        if index == last_recent_user_index and item.get("resolved_intent"):
            history_item["resolved_intent"] = item["resolved_intent"]
        history.append(history_item)
    anchor_payload = [
        {
            "anchor_type": anchor.anchor_type,
            "label": anchor.label,
            "sentence_id": anchor.sentence_id,
            "selected_text": utils.truncate_text(anchor.selected_text, 200),
            "note": utils.truncate_text(anchor.note, 180) or None,
            "entry_type": anchor.entry_type,
        }
        for anchor in contract.anchors
    ]
    # Resolve tool_record_id / tool_asset_id using planner's fallback logic.
    # record_ref uses _attachment_target_record (metadata.asset_id fallback for record id).
    # analysis_ref / supplement_ref use _attachment_record_id + _attachment_asset_id.
    from app.services.reader_ask.planner import (
        _attachment_asset_id,
        _attachment_record_id,
        _attachment_target_record,
    )

    def _resolve_tool_ids(attachment: ReaderAskAttachment) -> dict[str, str]:
        if attachment.kind not in ("record_ref", "analysis_ref", "supplement_ref"):
            return {}
        if attachment.kind == "record_ref":
            rid = _attachment_target_record(attachment)
        else:
            rid = _attachment_record_id(attachment)
        if not rid:
            return {}
        if attachment.kind == "record_ref":
            aid = ""
        else:
            aid = _attachment_asset_id(attachment) or ""
        return {"tool_record_id": rid, "tool_asset_id": aid}

    attachment_payload = [
        {
            "kind": attachment.kind,
            "subtype": attachment.subtype,
            "label": attachment.label,
            "selected_text": utils.truncate_text(attachment.selected_text, 200) or None,
            "target_key": attachment.target_key,
            "metadata": attachment.metadata.model_dump(mode="json"),
            # Round 10 fix: normalized tool parameters for load_explicit_attachment_context.
            # These are the canonical record_id/asset_id values the agent should pass
            # to the tool, and the same values used in the allowlist validation.
            **_resolve_tool_ids(attachment),
        }
        for attachment in contract.attachments
    ]
    return {
        "thread": {
            "id": contract.thread["id"],
            "title": contract.thread.get("title"),
        },
        "record": {
            "record_id": str(contract.record.record_id),
            "title": contract.record.title,
            "workflow_version": contract.record.workflow_version,
            "schema_version": contract.record.schema_version,
        },
        "page_identity": contract.page_identity.model_dump(mode="json"),
        "entry_action": contract.entry_action,
        "submission_mode": contract.submission_mode,
        "user_message": contract.user_message,
        "resolved_intent": contract.resolved_intent,
        "resolved_intent_label": contract.resolved_intent_label,
        "prompt_layers": prompt_layers,
        "history": history,
        "canonical_context": {
            "attachments": attachment_payload,
            "anchors": anchor_payload,
            "resolved_context_input": contract.resolved_context_input.model_dump(mode="json")
            if contract.resolved_context_input
            else None,
        },
        "reference_resolution": {
            "status": contract.reference_resolution.status if contract.reference_resolution else "not_needed",
            "query": contract.reference_resolution.query if contract.reference_resolution else None,
            "reason": contract.reference_resolution.reason if contract.reference_resolution else None,
            "resolved_records": contract.reference_resolution.resolved_records if contract.reference_resolution else [],
            "ambiguous_records": contract.reference_resolution.ambiguous_records if contract.reference_resolution else [],
        },
        "quick_action_annotation": contract.quick_action_annotation,
        "entry_action_guidance": _entry_action_guidance(contract.entry_action),
        "planning": {
            "retrieval_needs": contract.planning_snapshot.retrieval_needs if contract.planning_snapshot else "none",
            "working_set": {
                "primary_anchor_type": contract.planning_snapshot.working_set.primary_anchor.anchor_type
                if contract.planning_snapshot and contract.planning_snapshot.working_set.primary_anchor
                else None,
                "local_context_window_needed": contract.planning_snapshot.working_set.local_context_window_needed
                if contract.planning_snapshot
                else bool(contract.anchors),
                "record_insights_needed": contract.planning_snapshot.working_set.record_insights_needed
                if contract.planning_snapshot
                else False,
                "article_overview_needed": contract.planning_snapshot.working_set.article_overview_needed
                if contract.planning_snapshot
                else False,
                "dictionary_needed": contract.planning_snapshot.working_set.dictionary_needed
                if contract.planning_snapshot
                else False,
                "cross_record_context_allowed": contract.planning_snapshot.working_set.cross_record_context_allowed
                if contract.planning_snapshot
                else contract.cross_record_context_allowed,
                "external_record_refs": contract.planning_snapshot.working_set.external_record_refs
                if contract.planning_snapshot
                else [],
                "external_asset_refs": contract.planning_snapshot.working_set.external_asset_refs
                if contract.planning_snapshot
                else [],
                "external_asset_lookup_needed": contract.planning_snapshot.working_set.external_asset_lookup_needed
                if contract.planning_snapshot
                else False,
            },
            "context_plan": contract.planning_snapshot.context_plan.model_dump(mode="json")
            if contract.planning_snapshot and contract.planning_snapshot.context_plan
            else None,
            "trace_summary": contract.planning_snapshot.trace_summary.model_dump(mode="json")
            if contract.planning_snapshot and contract.planning_snapshot.trace_summary
            else None,
        },
        "cross_record_context_allowed": contract.cross_record_context_allowed,
        "cross_record_intent_hint": contract.cross_record_intent_hint,
        "external_attachment_hint": contract.external_attachment_hint,
        "followup_hint": (
            contract.followup_hint
            if contract.followup_hint
            else (
                contract.planning_snapshot.clarification_reason
                if contract.planning_snapshot and contract.planning_snapshot.clarification_mode == "can_answer_with_followup"
                else None
            )
        ),
        "tooling_contract": {
            "call_tools_on_demand": True,
            "cross_record_context_requires_explicit_intent": contract.cross_record_context_allowed,
            "writes_require_confirmation": True,
            "dictionary_context_explain_available": False,
        },
        "response_contract": {
            "format": "markdown",
            "be_concise": True,
            "article_bound": True,
            "do_not_claim_unknown_history": True,
            "structured_cards_available": [
                "grammar_note_card",
                "sentence_breakdown_card",
            ],
        },
        "intent_instructions": {
            "explain": "优先解释这句话或这段在当前文章里的意思，回答以简洁 Markdown 为主。",
            "breakdown": "优先拆主干、修饰和阅读顺序；需要时调用解析相关工具。",
            "vocabulary": "优先解释词义、短语义和为什么在这里是这个意思；需要时使用词典和词典 AI。",
            "grammar": "优先解释当前句子里的语法作用和句法关系，不要泛化成整节语法课。",
            "practice": "优先围绕当前句子或段落，用简洁的 Markdown 引导用户主动复述、辨析或判断结构，不生成练习卡。",
            "general": (
                "根据用户具体问题灵活回答，保持简洁，围绕当前文章和已提供的上下文。"
            ),
        }[contract.resolved_intent],
    }


# Round 1 re-exports so service.py can build the agent-loop-first runtime input
# without a direct planner import (keeps the import graph narrow).
build_minimal_resolved_intent = planner.build_minimal_resolved_intent
