from __future__ import annotations

from app.schemas.reader_ask import (
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
) -> str:
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
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | None = None,
    disambiguation: ReaderAskDisambiguation | None = None,
    include_clarification: bool = False,
) -> list[ReaderAskEvidenceItem]:
    evidence: list[ReaderAskEvidenceItem] = []
    for attachment in attachments:
        scope = "external_record" if attachment.kind == "record_ref" and attachment.subtype == "related_record" else "current_record"
        record_id = attachment.metadata.asset_id if scope == "external_record" else current_record_id
        record_title = attachment.metadata.title if scope == "external_record" else current_record_title
        evidence.append(
            ReaderAskEvidenceItem(
                kind="attachment",
                label=attachment.label,
                detail=attachment.subtype,
                scope=scope,
                record_id=record_id,
                record_title=record_title,
                reason="explicit_attachment" if scope == "external_record" else "local_anchor",
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
