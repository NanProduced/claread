from __future__ import annotations

from typing import Any

from app.schemas.internal.overview_hint import StoredOverviewHint
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


def extract_article_overview(render_scene: dict[str, Any]) -> str | None:
    direct = render_scene.get("content_summary")
    if isinstance(direct, dict):
        overview = direct.get("overview")
        if isinstance(overview, str) and overview.strip():
            return overview.strip()

    queue: list[Any] = [render_scene]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            entry_type = current.get("entryType") or current.get("entry_type")
            node_type = current.get("type")
            if entry_type == "content_summary" or node_type == "reader_content_summary":
                overview = current.get("overview")
                if isinstance(overview, str) and overview.strip():
                    return overview.strip()
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def extract_overview_hint(page_state_json: dict[str, Any] | None) -> StoredOverviewHint | None:
    if not isinstance(page_state_json, dict):
        return None
    derived = page_state_json.get("derived")
    if not isinstance(derived, dict):
        return None
    payload = derived.get("overview_hint")
    if not isinstance(payload, dict):
        return None
    try:
        return StoredOverviewHint.model_validate(payload)
    except Exception:
        return None


def resolve_record_overview(
    *,
    render_scene: dict[str, Any],
    page_state_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hint = extract_overview_hint(page_state_json)
    if hint is not None and hint.status == "ready" and (hint.overview or "").strip():
        return {
            "status": hint.status,
            "overview": hint.overview,
            "confidence": hint.confidence,
            "source": hint.source,
            "reason": hint.reason,
        }

    overview = extract_article_overview(render_scene)
    if overview:
        return {
            "status": "ready",
            "overview": overview,
            "confidence": None,
            "source": "academic_render_scene",
            "reason": None,
        }

    if hint is not None:
        return {
            "status": hint.status,
            "overview": None,
            "confidence": hint.confidence,
            "source": hint.source,
            "reason": hint.reason,
        }

    return {
        "status": None,
        "overview": None,
        "confidence": None,
        "source": None,
        "reason": None,
    }


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
