from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.services.reader_ask import planner
from app.services.reader_ask import repository as repo


def _normalize_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _score_title_match(query: str, title: str) -> int:
    normalized_query = _normalize_title(query)
    normalized_title = _normalize_title(title)
    if not normalized_query or not normalized_title:
        return 0
    if normalized_query == normalized_title:
        return 100
    if normalized_title.startswith(normalized_query):
        return 90
    if normalized_query in normalized_title:
        return 80
    query_tokens = [token for token in re.split(r"[\s\-:]+", normalized_query) if token]
    if query_tokens and all(token in normalized_title for token in query_tokens):
        return 70
    return 0


def _truncate_text(value: str | None, limit: int) -> str | None:
    normalized = re.sub(r"\s+", " ", (value or "").strip())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _extract_article_overview(render_scene: dict[str, Any]) -> str | None:
    direct = render_scene.get("content_summary")
    if isinstance(direct, dict):
        overview = _truncate_text(direct.get("overview"), 220)
        if overview:
            return overview

    queue: list[Any] = [render_scene]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            entry_type = current.get("entryType") or current.get("entry_type")
            node_type = current.get("type")
            if entry_type == "content_summary" or node_type == "reader_content_summary":
                overview = _truncate_text(current.get("overview"), 220)
                if overview:
                    return overview
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def _extract_stable_record_insights(render_scene: dict[str, Any], *, limit: int = 3) -> list[str]:
    entries_raw = render_scene.get("sentence_entries") or render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return []

    insights: list[str] = []
    seen: set[str] = set()
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        title = _truncate_text(entry.get("title") or entry.get("label") or entry.get("entry_type") or entry.get("entryType"), 40)
        content = _truncate_text(entry.get("content"), 120)
        if not title or not content:
            continue
        summary = f"{title}: {content}"
        if summary in seen:
            continue
        seen.add(summary)
        insights.append(summary)
        if len(insights) >= limit:
            break
    return insights


def lookup_structured_record_assets(
    *,
    record_id: str,
    record_title: str | None,
    render_scene: dict[str, Any],
    reason: str | None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    article_overview = _extract_article_overview(render_scene)
    record_insights = _extract_stable_record_insights(render_scene)
    source_labels = ["external_record"]
    if article_overview:
        source_labels.append("article_overview")
    if record_insights:
        source_labels.append("record_assets")
    if not article_overview:
        source_labels.append("overview_missing")
    return {
        "record_id": record_id,
        "record_title": record_title,
        "updated_at": updated_at,
        "article_overview": article_overview,
        "record_insights": record_insights,
        "reason": reason or "explicit_attachment",
        "source_labels": source_labels,
    }


async def resolve_known_references(
    *,
    user_id: UUID,
    current_record_id: UUID,
    reference_needs: planner.ReaderAskReferenceNeeds,
    finder: Callable[..., Awaitable[list[dict[str, str]]]] | None = None,
) -> planner.ReaderAskReferenceResolution:
    if not reference_needs.requested:
        return planner.ReaderAskReferenceResolution()

    if not reference_needs.query:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=None,
            reason="请补充你想引用的文章标题，我再把它并入当前讨论。",
        )

    finder_fn = finder or repo.search_records_by_title
    rows = await finder_fn(
        user_id,
        query=reference_needs.query,
        exclude_record_id=current_record_id,
        limit=8,
    )

    ranked: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        score = _score_title_match(reference_needs.query, row.get("title") or "")
        if score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)

    if not ranked:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=reference_needs.query,
            reason=f"没有找到标题能直接命中“{reference_needs.query}”的已知文章。",
        )

    top_score = ranked[0][0]
    top_hits = [row for score, row in ranked if score == top_score]
    if top_score < 80 or len(ranked) != 1 or len(top_hits) != 1:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=reference_needs.query,
            reason=f"“{reference_needs.query}”命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[
                {
                    "record_id": row["id"],
                    "title": row.get("title") or "Untitled",
                    "updated_at": row.get("updated_at"),
                }
                for row in (top_hits[:3] if top_hits else [candidate for _, candidate in ranked[:3]])
            ],
        )

    match = top_hits[0]
    return planner.ReaderAskReferenceResolution(
        attempted=True,
        status="resolved",
        query=reference_needs.query,
        reason=f"已命中历史文章“{match.get('title') or reference_needs.query}”。",
        resolved_records=[
            {
                "record_id": match["id"],
                "title": match.get("title") or reference_needs.query,
                "updated_at": match.get("updated_at"),
            }
        ],
    )
