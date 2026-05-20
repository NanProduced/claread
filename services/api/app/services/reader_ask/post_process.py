from __future__ import annotations

from app.schemas.reader_ask import (
    ReaderAskAttachment,
    ReaderAskCitation,
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
    reference_resolution: planner.ReaderAskReferenceResolution | None = None,
    supplement_candidates: list[ReaderAskSupplementCandidate] | None = None,
    include_clarification: bool = False,
) -> list[ReaderAskEvidenceItem]:
    evidence: list[ReaderAskEvidenceItem] = []
    for attachment in attachments:
        evidence.append(
            ReaderAskEvidenceItem(
                kind="attachment",
                label=attachment.label,
                detail=attachment.subtype,
                target_key=attachment.target_key,
                metadata_json={"kind": attachment.kind, "subtype": attachment.subtype},
            )
        )
    for citation in citations:
        evidence.append(
            ReaderAskEvidenceItem(
                kind="citation",
                label=citation.label,
                detail=citation.selected_text,
                record_id=citation.record_id,
                source_article_title=citation.source_article_title,
                target_key=citation.target_key,
                metadata_json=citation.metadata_json,
            )
        )
    if reference_resolution:
        for record in reference_resolution.resolved_records:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="resolved_reference",
                    label=record["title"],
                    detail=reference_resolution.reason,
                    record_id=record["record_id"],
                    source_article_title=record["title"],
                    metadata_json={"query": reference_resolution.query},
                )
            )
        if include_clarification and reference_resolution.status in {"ambiguous", "not_found"}:
            evidence.append(
                ReaderAskEvidenceItem(
                    kind="clarification",
                    label="引用解析需要补充",
                    detail=reference_resolution.reason,
                    metadata_json={
                        "query": reference_resolution.query,
                        "ambiguous_records": reference_resolution.ambiguous_records,
                    },
                )
            )
    for candidate in supplement_candidates or []:
        evidence.append(
            ReaderAskEvidenceItem(
                kind="supplement_candidate",
                label=candidate.title,
                detail=candidate.label,
                target_key=candidate.target_key,
                metadata_json={"candidate_id": candidate.candidate_id, "supplement_type": candidate.supplement_type},
            )
        )
    return evidence
