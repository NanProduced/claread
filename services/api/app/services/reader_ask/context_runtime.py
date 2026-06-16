"""Context runtime: planned context materialization and external asset loading.

This module owns the logic for assembling resolved_context_input from a
planning snapshot — loading record contexts, insights, overviews, and
external assets.  It does NOT directly query the database; repo-dependent
operations are injected via callbacks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from fastapi import HTTPException

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskCurrentRecordContext,
    ReaderAskEntryAction,
    ReaderAskExternalAssetContext,
    ReaderAskExternalRecordContext,
)
from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.reader_ask import planner
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import utils


# ---------------------------------------------------------------------------
# Record bundle protocol (avoids importing service._RecordBundle)
# ---------------------------------------------------------------------------

class RecordBundle(Protocol):
    record_id: UUID
    title: str | None
    source_text: str
    render_scene: dict[str, Any]
    page_state_json: dict[str, Any]
    workflow_version: str | None
    schema_version: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def render_scene_article_overview(record: RecordBundle) -> str | None:
    resolved = utils.resolve_record_overview(
        render_scene=record.render_scene,
        page_state_json=getattr(record, "page_state_json", None),
    )
    overview = resolved.get("overview")
    return str(overview).strip() or None if isinstance(overview, str) else None


def current_record_source_labels(runtime_state: ReaderAskRuntimeState) -> list[str]:
    labels: list[str] = []
    if runtime_state.latest_record_context is not None:
        labels.append("current_paragraph")
    if runtime_state.latest_record_insights:
        labels.append("record_assets")
    if runtime_state.latest_article_overview:
        labels.append("article_overview")
    return labels


def external_context_has_structured_assets(items: list[dict[str, Any]] | None) -> bool:
    return bool(
        items
        and any(item.get("article_overview") or item.get("record_insights") for item in items)
    )


def external_asset_context_has_items(items: list[dict[str, Any]] | None) -> bool:
    return bool(items and any(item.get("asset_id") for item in items))


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


# ---------------------------------------------------------------------------
# External context loading
# ---------------------------------------------------------------------------

async def load_external_record_contexts(
    user_id: UUID,
    *,
    current_record_id: UUID,
    planned_external_refs: list[dict[str, str]],
    load_record_bundle_cb: Callable[[UUID, UUID], Awaitable[RecordBundle]],
) -> list[ReaderAskExternalRecordContext]:
    unique_refs: list[tuple[str, UUID, dict[str, str]]] = []
    seen: set[str] = set()
    for item in planned_external_refs:
        record_id = str(item.get("record_id") or "").strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        record_uuid = _parse_uuid(record_id, "external record id is invalid")
        if record_uuid == current_record_id:
            continue
        unique_refs.append((record_id, record_uuid, item))

    bundles = await asyncio.gather(*[
        load_record_bundle_cb(user_id, record_uuid)
        for _, record_uuid, _ in unique_refs
    ])

    contexts: list[ReaderAskExternalRecordContext] = []
    for (_, record_uuid, item), bundle in zip(unique_refs, bundles):
        structured_assets = resolver_svc.lookup_structured_record_assets(
            record_id=str(bundle.record_id),
            record_title=bundle.title or item.get("title"),
            render_scene=bundle.render_scene,
            page_state_json=bundle.page_state_json,
            reason=str(item.get("reason") or "explicit_attachment"),
            updated_at=item.get("updated_at"),
        )
        contexts.append(
            ReaderAskExternalRecordContext(
                record_id=str(structured_assets["record_id"]),
                record_title=structured_assets.get("record_title"),
                article_overview=structured_assets.get("article_overview"),
                article_overview_status=structured_assets.get("article_overview_status"),
                article_overview_source=structured_assets.get("article_overview_source"),
                article_overview_confidence=structured_assets.get("article_overview_confidence"),
                record_insights=list(structured_assets.get("record_insights") or []),
                source_labels=list(structured_assets.get("source_labels") or []),
                reason=str(structured_assets.get("reason") or "explicit_attachment"),
            )
        )
    return contexts


def load_external_asset_contexts(
    *,
    current_record_id: UUID,
    planned_external_assets: list[dict[str, object]],
) -> list[ReaderAskExternalAssetContext]:
    contexts: list[ReaderAskExternalAssetContext] = []
    seen: set[tuple[str, str]] = set()
    for item in planned_external_assets:
        record_id = str(item.get("record_id") or "").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        if not record_id or not asset_id:
            continue
        key = (record_id, asset_id)
        if key in seen:
            continue
        seen.add(key)
        record_uuid = _parse_uuid(record_id, "external asset record id is invalid")
        if record_uuid == current_record_id:
            continue
        contexts.append(
            ReaderAskExternalAssetContext(
                record_id=record_id,
                record_title=str(item.get("record_title") or "") or None,
                asset_type=str(item.get("asset_type") or "analysis"),  # type: ignore[arg-type]
                asset_id=asset_id,
                entry_type=str(item.get("entry_type") or "") or None,
                asset_title=str(item.get("asset_title") or "") or None,
                content_md=str(item.get("content_md") or "") or None,
                content_summary=str(item.get("content_summary") or "") or None,
                source_labels=[
                    str(label).strip()
                    for label in (item.get("source_labels") or [])
                    if str(label).strip()
                ],
                reason=str(item.get("reason") or "structured_asset_resolved"),
            )
        )
    return contexts


def build_agent_loop_context(
    *,
    record: RecordBundle,
    runtime_state: ReaderAskRuntimeState,
    anchors: list[ReaderAskAnchorRef],
    attachments: list[ReaderAskAttachment],
    user_id: UUID,
    page_identity: Any,
    entry_action: ReaderAskEntryAction,
    latest_user_message: str = "",
    cross_record_toggle: bool = False,
) -> Any:
    """Build a minimal resolved_context_input for the agent loop.

    Unlike ``materialize_planned_context``, this function does NOT call any
    pre-fetch callbacks (get_record_context_cb, get_record_insights_cb) and
    does NOT invoke the planner.  It assembles a lightweight context with only
    the record identity, overview, and source_labels — suitable for the agent
    loop where full context materialization is unnecessary.

    Round 8: detects deictic-without-anchor and sets
    ``runtime_state.deictic_clarification_hint`` so the service layer can
    inject a clarification hint into the prompt payload.

    Round 9: detects cross-record intent (toggle + keywords) and sets
    ``runtime_state.cross_record_intent_hint`` so the agent calls
    ``resolve_known_reference`` on demand.

    Round 10: detects explicit external attachments (record_ref /
    analysis_ref / supplement_ref) and sets
    ``runtime_state.external_attachment_hint`` so the agent calls
    ``load_explicit_attachment_context`` on demand.

    Round 11: detects dictionary anchors/attachments and sets
    ``runtime_state.dictionary_anchor_hint`` so the agent answers based
    on article context and the explicit dictionary anchor metadata
    instead of requiring planner pre-resolution.
    """
    from app.services.reader_ask.planner_route_policy import (
        has_cross_record_intent,
        has_deictic_without_anchor,
        has_dictionary_anchor_or_attachment,
        has_explicit_external_attachments,
    )

    # Round 11: detect dictionary anchors/attachments and set hint.
    if has_dictionary_anchor_or_attachment(anchors, attachments):
        runtime_state.dictionary_anchor_hint = (
            "用户查询了词典条目。"
            "请基于当前文章语境和 canonical_context.anchors 中的 dictionary_entry 锚点信息"
            "（dict_entry_id / query / payload_json）回答词义/用法问题；"
            "如果 anchors 为空但 canonical_context.attachments 中有 subtype=dictionary_entry 的附件，"
            "则从该附件的 label / selected_text / metadata 获取被查词信息。"
            "优先用你的语言能力解释该词在当前语境中的含义和用法；"
            "如果需要更精确的释义，引导用户打开 reader 右侧的词典卡片查看。"
            "不要调用任何词典类工具——这些工具已不在 agent 可见集合里。"
        )

    # Round 10: detect external attachments and set hint.
    if has_explicit_external_attachments(
        attachments, current_record_id=str(record.record_id)
    ):
        runtime_state.external_attachment_hint = (
            "用户附加了外部引用（其他文章/分析/笔记）。"
            "请查看 canonical_context.attachments 中的 record_ref/analysis_ref/supplement_ref，"
            "然后调用 load_explicit_attachment_context(record_id, asset_id) 加载具体内容。"
            "使用 attachment 中的 tool_record_id 作为 record_id，tool_asset_id 作为 asset_id（空字符串则不传）。"
            "只能加载本轮 attachments 中列出的外部引用。"
        )

    # Round 9: detect cross-record intent and set hint.
    if has_cross_record_intent(cross_record_toggle, latest_user_message):
        runtime_state.cross_record_intent_hint = (
            "用户表达了跨文章意图（如'另一篇''之前那篇'）且已开启跨文章功能。"
            "请优先调用 resolve_known_reference(query, top_k=5) 查找相关文章。"
        )

    # Round 8: detect deictic without anchor and set clarification hint.
    if has_deictic_without_anchor(latest_user_message, anchors):
        runtime_state.deictic_clarification_hint = (
            "用户使用了指代表达（如'这句''这段'）但未选中具体文本。"
            "请先追问用户选中具体位置，再进行解释。"
        )

    resolved_overview = utils.resolve_record_overview(
        render_scene=record.render_scene,
        page_state_json=getattr(record, "page_state_json", None),
    )
    overview = resolved_overview.get("overview")
    overview_str = str(overview).strip() or None if isinstance(overview, str) else None

    source_labels: list[str] = []
    if overview_str:
        runtime_state.latest_article_overview = overview_str
        runtime_state.source_labels.add(str(resolved_overview.get("source") or "article_overview"))
        source_labels.append("article_overview")

    current_record_context = ReaderAskCurrentRecordContext(
        record_id=str(record.record_id),
        record_title=record.title,
        local_context=None,
        record_insights=[],
        article_overview=overview_str,
        article_overview_status=str(resolved_overview.get("status") or "") or None,
        article_overview_source=str(resolved_overview.get("source") or "") or None,
        article_overview_confidence=str(resolved_overview.get("confidence") or "") or None,
        source_labels=source_labels,
    )
    return planner.build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=[],
        external_asset_contexts=[],
    )


# ---------------------------------------------------------------------------
# Main entry: materialize planned context
# ---------------------------------------------------------------------------

async def materialize_planned_context(
    *,
    user_id: UUID,
    record: RecordBundle,
    runtime_state: ReaderAskRuntimeState,
    planning_snapshot: planner.ReaderAskPlanningSnapshot | None,
    page_identity: Any,
    entry_action: ReaderAskEntryAction,
    attachments: list[ReaderAskAttachment],
    anchors: list[ReaderAskAnchorRef],
    get_record_context_cb: Callable[[], Awaitable[Any]],
    get_record_insights_cb: Callable[[], Awaitable[Any]],
    load_record_bundle_cb: Callable[[UUID, UUID], Awaitable[RecordBundle]],
) -> Any:
    """Materialize the planned context into a resolved_context_input.

    This function:
    1. Loads current record context / insights / overview based on working_set
    2. Loads external record contexts and asset contexts
    3. Assembles the final resolved_context_input via planner

    All repo-dependent operations are injected via callbacks.

    When ``planning_snapshot`` is None (agent-loop-first), the working_set-driven
    fetches are skipped — only the article_overview is attempted, and the
    external lists are empty. The returned ``resolved_context_input`` is a
    minimal shape that satisfies the rest of the runtime contract.
    """
    resolved_overview = utils.resolve_record_overview(
        render_scene=record.render_scene,
        page_state_json=record.page_state_json,
    )
    if planning_snapshot is not None:
        working_set = planning_snapshot.working_set
        if working_set.local_context_window_needed and runtime_state.latest_record_context is None:
            runtime_state.latest_record_context = await get_record_context_cb()
            if runtime_state.latest_record_context is not None:
                runtime_state.source_labels.update({"current_record", "current_anchor", "current_paragraph"})
        if working_set.record_insights_needed and not runtime_state.latest_record_insights:
            runtime_state.latest_record_insights = await get_record_insights_cb()
            if runtime_state.latest_record_insights:
                runtime_state.source_labels.add("record_assets")
        if working_set.article_overview_needed and not runtime_state.latest_article_overview:
            article_overview = render_scene_article_overview(record)
            if article_overview:
                runtime_state.latest_article_overview = article_overview
                runtime_state.source_labels.add(str(resolved_overview.get("source") or "article_overview"))

        external_record_contexts = await load_external_record_contexts(
            user_id,
            current_record_id=record.record_id,
            planned_external_refs=working_set.external_record_refs,
            load_record_bundle_cb=load_record_bundle_cb,
        )
        if external_record_contexts:
            runtime_state.latest_external_record_contexts = [
                item.model_dump(mode="json") for item in external_record_contexts
            ]
            runtime_state.used_cross_record_context = True
            runtime_state.source_labels.add("external_record_context")
            for item in external_record_contexts:
                runtime_state.source_labels.update(item.source_labels)
        external_asset_contexts = load_external_asset_contexts(
            current_record_id=record.record_id,
            planned_external_assets=working_set.external_asset_refs,
        )
        if external_asset_contexts:
            runtime_state.latest_external_asset_contexts = [
                item.model_dump(mode="json") for item in external_asset_contexts
            ]
            runtime_state.used_cross_record_context = True
            runtime_state.source_labels.update({"external_record_context", "external_assets"})
    else:
        # Agent-loop-first: skip record/insights/external fetches. Still attempt the
        # article overview so source_labels can pick it up if present.
        if not runtime_state.latest_article_overview:
            article_overview = render_scene_article_overview(record)
            if article_overview:
                runtime_state.latest_article_overview = article_overview
                runtime_state.source_labels.add(str(resolved_overview.get("source") or "article_overview"))
        external_record_contexts = []
        external_asset_contexts = []

    current_record_context = ReaderAskCurrentRecordContext(
        record_id=str(record.record_id),
        record_title=record.title,
        local_context=runtime_state.latest_record_context,
        record_insights=runtime_state.latest_record_insights,
        article_overview=runtime_state.latest_article_overview,
        article_overview_status=str(resolved_overview.get("status") or "") or None,
        article_overview_source=str(resolved_overview.get("source") or "") or None,
        article_overview_confidence=str(resolved_overview.get("confidence") or "") or None,
        source_labels=current_record_source_labels(runtime_state),
    )
    return planner.build_resolved_context_input(
        page_identity=page_identity,
        entry_action=entry_action,
        attachments=attachments,
        anchors=anchors,
        current_record_context=current_record_context,
        external_record_contexts=external_record_contexts,
        external_asset_contexts=external_asset_contexts,
    )
