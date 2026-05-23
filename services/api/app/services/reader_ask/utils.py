from __future__ import annotations

from typing import Any

from app.schemas.reader_ask import ReaderAskCitation


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def truncate_text(value: str | None, limit: int) -> str:
    normalized = normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def truncate_text_optional(value: str | None, limit: int) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def clean_reference_query(value: str | None) -> str | None:
    cleaned = normalize_text(value).strip(" \t\r\n,.;:!?，。；：！？")
    return cleaned or None


def extract_article_overview(render_scene: dict[str, Any], *, limit: int = 220) -> str | None:
    direct = render_scene.get("content_summary")
    if isinstance(direct, dict):
        overview = truncate_text_optional(direct.get("overview"), limit)
        if overview:
            return overview

    queue: list[Any] = [render_scene]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            entry_type = current.get("entryType") or current.get("entry_type")
            node_type = current.get("type")
            if entry_type == "content_summary" or node_type == "reader_content_summary":
                overview = truncate_text_optional(current.get("overview"), limit)
                if overview:
                    return overview
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def merge_citation(citations: list[ReaderAskCitation], citation: ReaderAskCitation) -> None:
    for existing in citations:
        if (
            existing.kind == citation.kind
            and existing.label == citation.label
            and existing.record_id == citation.record_id
            and existing.target_key == citation.target_key
            and existing.sentence_id == citation.sentence_id
        ):
            return
    citations.append(citation)
