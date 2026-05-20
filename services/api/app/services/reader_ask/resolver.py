from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
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
                }
                for row in top_hits[:3]
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
            }
        ],
    )
