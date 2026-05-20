from __future__ import annotations

from app.schemas.reader_ask import (
    ReaderAskAssetDisambiguation,
    ReaderAskAttachment,
    ReaderAskCitation,
    ReaderAskDisambiguation,
    ReaderAskEvidenceItem,
    ReaderAskSupplementCandidate,
)
from app.services.reader_ask import planner


def build_clarification_message(
    *,
    local_anchor_required: bool,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    structured_asset_resolution: planner.ReaderAskStructuredAssetResolution | None = None,
) -> str:
    if structured_asset_resolution and structured_asset_resolution.status == "ambiguous":
        return structured_asset_resolution.reason or "我已经定位到那篇文章，但其中有多个稳定资产可能相关，请先选一个并入当前讨论。"
    if structured_asset_resolution and structured_asset_resolution.status == "not_found":
        return structured_asset_resolution.reason or "我已经定位到那篇文章，但当前没有命中可并入的稳定资产。"
    if local_anchor_required:
        return (
            "我还不能确定你说的“这里/这句”具体指哪一处。"
            "请先在正文里选中一句或把相关解析卡片加入对话上下文后再问我。"
        )
    if reference_resolution and reference_resolution.status == "ambiguous":
        return reference_resolution.reason or "我需要你补充更完整的文章标题，才能把那篇内容并入当前讨论。"
    if reference_resolution and reference_resolution.status == "not_found":
        return reference_resolution.reason or "我没有找到能直接命中的历史文章标题。请补充更准确的标题。"
    return "我还需要更明确的上下文，才能继续回答。"


def build_evidence_items(
    *,
    attachments: list[ReaderAskAttachment],
    citations: list[ReaderAskCitation],
    current_record_id: str | None = None,
    current_record_title: str | None = None,
    external_record_contexts: list[dict[str, object]] | None = None,
    external_asset_contexts: list[dict[str, object]] | None = None,
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | None = None,
    disambiguation: ReaderAskDisambiguation | None = None,
    asset_disambiguation: ReaderAskAssetDisambiguation | None = None,
    include_clarification: bool = False,
) -> list[ReaderAskEvidenceItem]:
    evidence: list[ReaderAskEvidenceItem] = []
    for attachment in attachments:
        attachment_record_id = attachment.metadata.record_id or attachment.metadata.asset_id
        is_external_attachment = (
            attachment.kind == "record_ref"
            and attachment.subtype == "related_record"
        ) or (
            attachment.kind in {"analysis_ref", "supplement_ref"}
            and isinstance(attachment_record_id, str)
            and current_record_id is not None
            and attachment_record_id != current_record_id
        )
        scope = "external_record" if is_external_attachment else "current_record"
        record_id = attachment_record_id if scope == "external_record" else current_record_id
        record_title = (
            attachment.metadata.record_title
            or attachment.metadata.title
            if scope == "external_record"
            else current_record_title
        )
        reason = "local_anchor"
        if scope == "external_record":
            if attachment.kind == "supplement_ref":
                reason = "external_supplement_asset"
            elif attachment.kind == "analysis_ref":
                reason = "external_analysis_asset"
            else:
                reason = "explicit_attachment"
        evidence.append(
            ReaderAskEvidenceItem(
                kind="attachment",
                label=attachment.label,
                detail=attachment.subtype,
                scope=scope,
                record_id=record_id,
                record_title=record_title,
                reason=reason,
                target_key=attachment.target_key,
                metadata_json={"kind": attachment.kind, "subtype": attachment.subtype},
            )
        )
    for citation in citations:
        citation_scope = "external_record" if citation.record_id and current_record_id and citation.record_id != current_record_id else "current_record"
        evidence.append(
            ReaderAskEvidenceItem(
                kind="citation",
                label=citation.label,
                detail=citation.selected_text,
                scope=citation_scope,
                record_id=citation.record_id,
                record_title=citation.source_article_title,
                source_article_title=citation.source_article_title,
                reason="local_anchor" if citation_scope == "current_record" else "article_overview",
                target_key=citation.target_key,
                metadata_json=citation.metadata_json,
            )
        )
    for item in external_record_contexts or []:
        record_id = str(item.get("record_id") or "")
        if not record_id:
            continue
        record_title = item.get("record_title")
        article_overview = item.get("article_overview")
        reason = item.get("reason")
        detail = None
        if isinstance(article_overview, str) and article_overview.strip():
            detail = article_overview.strip()[:180]
        else:
            detail = "已定位到该文章，但当前没有可用概览。"
        evidence.append(
            ReaderAskEvidenceItem(
                kind="citation",
                label=str(record_title or record_id),
                detail=detail,
                scope="external_record",
                record_id=record_id,
                record_title=str(record_title) if isinstance(record_title, str) else None,
                source_article_title=str(record_title) if isinstance(record_title, str) else None,
                reason="structured_asset_lookup",
                metadata_json={
                    "source_labels": item.get("source_labels") or [],
                    "context_reason": str(reason) if isinstance(reason, str) else "article_overview",
                    "record_insights": item.get("record_insights") or [],
                },
            )
        )
    for item in external_asset_contexts or []:
        record_id = str(item.get("record_id") or "")
        asset_id = str(item.get("asset_id") or "")
        if not record_id or not asset_id:
            continue
        asset_type = str(item.get("asset_type") or "analysis")
        record_title = item.get("record_title")
        asset_title = item.get("asset_title")
        detail = item.get("content_summary") or "已并入外部稳定资产。"
        evidence.append(
            ReaderAskEvidenceItem(
                kind="citation",
                label=str(asset_title or asset_id),
                detail=str(detail),
                scope="external_record",
                record_id=record_id,
                record_title=str(record_title) if isinstance(record_title, str) else None,
                source_article_title=str(record_title) if isinstance(record_title, str) else None,
                reason="external_supplement_asset" if asset_type == "supplement" else "external_analysis_asset",
                metadata_json={
                    "asset_id": asset_id,
                    "asset_type": asset_type,
                    "entry_type": item.get("entry_type"),
                    "context_reason": item.get("reason"),
                },
            )
        )
    if reference_resolution:
        for record in reference_resolution.resolved_records:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="resolved_reference",
                    label=record["title"],
                    detail=reference_resolution.reason,
                    scope="external_record",
                    record_id=record["record_id"],
                    record_title=record["title"],
                    source_article_title=record["title"],
                    reason="known_reference_resolved",
                    metadata_json={"query": reference_resolution.query},
                )
            )
        if include_clarification and reference_resolution.status in {"ambiguous", "not_found"}:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="clarification",
                    label="引用解析需要补充",
                    detail=reference_resolution.reason,
                    scope="current_record",
                    reason="clarification",
                    metadata_json={
                        "query": reference_resolution.query,
                        "ambiguous_records": reference_resolution.ambiguous_records,
                    },
                )
            )
    if disambiguation and disambiguation.required:
        for candidate in disambiguation.candidates:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="disambiguation_candidate",
                    label=candidate.title or candidate.record_id,
                    detail="候选外部文章，可加入当前讨论。",
                    scope="external_record",
                    record_id=candidate.record_id,
                    record_title=candidate.title,
                    source_article_title=candidate.title,
                    reason="disambiguation_candidate",
                    metadata_json={"updated_at": candidate.updated_at, "query": disambiguation.query},
                )
            )
    if asset_disambiguation and asset_disambiguation.required:
        for candidate in asset_disambiguation.candidates:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="disambiguation_candidate",
                    label=candidate.title or candidate.asset_id,
                    detail=candidate.summary or "候选外部稳定资产，可加入当前讨论。",
                    scope="external_record",
                    record_id=asset_disambiguation.record_id,
                    record_title=asset_disambiguation.record_title,
                    source_article_title=asset_disambiguation.record_title,
                    reason="asset_disambiguation_candidate",
                    metadata_json={
                        "asset_id": candidate.asset_id,
                        "asset_type": candidate.asset_type,
                        "entry_type": candidate.entry_type,
                    },
                )
            )
    for candidate in supplement_candidates or []:
        evidence.append(
            ReaderAskEvidenceItem(
                kind="supplement_candidate",
                label=candidate.title,
                detail=candidate.label,
                scope="current_record",
                record_id=current_record_id,
                record_title=current_record_title,
                reason="supplement_candidate",
                target_key=candidate.target_key,
                metadata_json={"candidate_id": candidate.candidate_id, "supplement_type": candidate.supplement_type},
            )
        )
    return evidence
