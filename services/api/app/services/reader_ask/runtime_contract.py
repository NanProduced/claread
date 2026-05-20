from __future__ import annotations

import json
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


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def _truncate_text(value: str | None, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _estimate_token_count(payload: dict[str, Any]) -> int:
    serialized = json.dumps(payload, ensure_ascii=False)
    return max((len(serialized) + 3) // 4, 1)


def _compact_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = json.loads(json.dumps(payload, ensure_ascii=False))
    history = compact.get("history")
    if isinstance(history, list) and len(history) > 4:
        compact["history"] = history[-4:]

    record_assets = compact.get("record_assets")
    if isinstance(record_assets, list) and len(record_assets) > 3:
        compact["record_assets"] = record_assets[:3]

    history_assets = compact.get("history_assets")
    if isinstance(history_assets, list) and len(history_assets) > 2:
        compact["history_assets"] = history_assets[:2]

    vocabulary_items = compact.get("vocabulary_items")
    if isinstance(vocabulary_items, list) and len(vocabulary_items) > 3:
        compact["vocabulary_items"] = vocabulary_items[:3]

    record_insights = compact.get("record_insights")
    if isinstance(record_insights, list) and len(record_insights) > 3:
        compact["record_insights"] = record_insights[:3]

    record_context = compact.get("record_context")
    if isinstance(record_context, dict):
        sentence_windows = record_context.get("sentence_windows")
        if isinstance(sentence_windows, list) and len(sentence_windows) > 3:
            record_context["sentence_windows"] = sentence_windows[:3]
        source_excerpt = record_context.get("source_excerpt")
        if isinstance(source_excerpt, str) and len(source_excerpt) > 800:
            record_context["source_excerpt"] = _truncate_text(source_excerpt, 800)
    article_overview = compact.get("article_overview")
    if isinstance(article_overview, str) and len(article_overview) > 400:
        compact["article_overview"] = _truncate_text(article_overview, 400)
    return compact


def prepare_prompt_payload(
    payload: dict[str, Any],
    *,
    reserved_points: int,
    tokens_per_point: int,
    multiplier_output: int,
    budget_buffer_tokens: int,
    default_max_output_tokens: int,
    min_max_output_tokens: int,
) -> tuple[dict[str, Any], int]:
    prompt_payload = payload
    estimated_input_tokens = _estimate_token_count(prompt_payload)
    if estimated_input_tokens > 4500:
        prompt_payload = _compact_prompt_payload(payload)
        estimated_input_tokens = _estimate_token_count(prompt_payload)

    weighted_budget = reserved_points * tokens_per_point
    weighted_remaining = max(weighted_budget - estimated_input_tokens - budget_buffer_tokens, 0)
    budgeted_output_tokens = max(
        min_max_output_tokens,
        min(default_max_output_tokens, weighted_remaining // multiplier_output if weighted_remaining else 0),
    )
    return prompt_payload, budgeted_output_tokens


def build_prompt_payload(
    *,
    thread: dict[str, Any],
    record: Any,
    user_message: str,
    history_messages: list[dict[str, Any]],
    page_identity: ReaderAskPageIdentity,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    resolved_intent: ReaderAskResolvedIntent,
    resolved_intent_label: str,
    entry_action: ReaderAskEntryAction,
    history_lookup_allowed: bool,
    resolved_context_input: ReaderAskResolvedContextInput | None,
    reference_resolution: planner.ReaderAskReferenceResolution | None,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None,
    max_history_messages: int,
    max_message_text: int,
) -> dict[str, Any]:
    prompt_layers = prompt_layers_svc.load_prompt_layers()
    history = [
        {
            "role": item["role"],
            "content_md": _truncate_text(item["content_md"], max_message_text),
        }
        for item in history_messages[-max_history_messages:]
    ]
    anchor_payload = [
        {
            "anchor_type": anchor.anchor_type,
            "label": anchor.label,
            "sentence_id": anchor.sentence_id,
            "selected_text": _truncate_text(anchor.selected_text, 200),
            "note": _truncate_text(anchor.note, 180) or None,
            "entry_type": anchor.entry_type,
        }
        for anchor in anchors
    ]
    attachment_payload = [
        {
            "kind": attachment.kind,
            "subtype": attachment.subtype,
            "label": attachment.label,
            "selected_text": _truncate_text(attachment.selected_text, 200) or None,
            "target_key": attachment.target_key,
            "metadata": attachment.metadata.model_dump(mode="json"),
        }
        for attachment in attachments
    ]
    return {
        "thread": {
            "id": thread["id"],
            "title": thread.get("title"),
        },
        "record": {
            "record_id": str(record.record_id),
            "title": record.title,
            "workflow_version": record.workflow_version,
            "schema_version": record.schema_version,
        },
        "page_identity": page_identity.model_dump(mode="json"),
        "entry_action": entry_action,
        "user_message": user_message,
        "resolved_intent": resolved_intent,
        "resolved_intent_label": resolved_intent_label,
        "prompt_layers": prompt_layers,
        "history": history,
        "attachments": attachment_payload,
        "anchors": anchor_payload,
        "reference_resolution": {
            "status": reference_resolution.status if reference_resolution else "not_needed",
            "query": reference_resolution.query if reference_resolution else None,
            "reason": reference_resolution.reason if reference_resolution else None,
            "resolved_records": reference_resolution.resolved_records if reference_resolution else [],
            "ambiguous_records": reference_resolution.ambiguous_records if reference_resolution else [],
        },
        "resolved_context_input": resolved_context_input.model_dump(mode="json") if resolved_context_input else None,
        "planning": {
            "retrieval_needs": planning_snapshot.retrieval_needs if planning_snapshot else "none",
            "working_set": {
                "primary_anchor_type": planning_snapshot.working_set.primary_anchor.anchor_type
                if planning_snapshot and planning_snapshot.working_set.primary_anchor
                else None,
                "local_context_window_needed": planning_snapshot.working_set.local_context_window_needed
                if planning_snapshot
                else bool(anchors),
                "record_insights_needed": planning_snapshot.working_set.record_insights_needed
                if planning_snapshot
                else False,
                "article_overview_needed": planning_snapshot.working_set.article_overview_needed
                if planning_snapshot
                else False,
                "dictionary_needed": planning_snapshot.working_set.dictionary_needed
                if planning_snapshot
                else False,
                "history_assets_allowed": planning_snapshot.working_set.history_assets_allowed
                if planning_snapshot
                else history_lookup_allowed,
                "external_record_refs": planning_snapshot.working_set.external_record_refs
                if planning_snapshot
                else [],
            },
            "context_plan": planning_snapshot.context_plan.model_dump(mode="json") if planning_snapshot else None,
            "trace_summary": planning_snapshot.trace_summary.model_dump(mode="json") if planning_snapshot else None,
        },
        "history_lookup_allowed": history_lookup_allowed,
        "tooling_contract": {
            "call_tools_on_demand": True,
            "history_lookup_requires_explicit_intent": history_lookup_allowed,
            "writes_require_confirmation": True,
            "dictionary_context_explain_available": True,
        },
        "response_contract": {
            "format": "markdown",
            "be_concise": True,
            "article_bound": True,
            "do_not_claim_unknown_history": True,
            "structured_cards_available": [
                "sentence_breakdown_card",
                "vocabulary_in_context_card",
                "practice_card",
            ],
        },
        "intent_instructions": {
            "explain": "优先解释这句话或这段在当前文章里的意思，回答以简洁 Markdown 为主。",
            "breakdown": "优先拆主干、修饰和阅读顺序；需要时调用解析相关工具。",
            "vocabulary": "优先解释词义、短语义和为什么在这里是这个意思；需要时使用词典和词典 AI。",
            "grammar": "优先解释当前句子里的语法作用和句法关系，不要泛化成整节语法课。",
            "practice": "优先围绕当前句子或段落生成练习，帮助用户主动复述、辨析或判断结构。",
        }[resolved_intent],
    }
