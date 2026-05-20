from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
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


def _analysis_asset_candidates(
    *,
    record_id: str,
    record_title: str | None,
    render_scene: dict[str, Any],
) -> list[dict[str, str]]:
    entries_raw = render_scene.get("sentence_entries") or render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return []

    candidates: list[dict[str, str]] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("source_kind") or "").strip() == "ask_supplement":
            continue
        entry_type = str(entry.get("entry_type") or entry.get("entryType") or "").strip()
        if not entry_type or entry_type == "content_summary":
            continue
        asset_id = str(entry.get("id") or "").strip()
        if not asset_id:
            continue
        title = _truncate_text(entry.get("title") or entry.get("label") or entry_type, 60)
        summary = _truncate_text(entry.get("content"), 180)
        candidates.append(
            {
                "record_id": record_id,
                "record_title": record_title or "",
                "asset_type": "analysis",
                "asset_id": asset_id,
                "entry_type": entry_type,
                "title": title or entry_type,
                "summary": summary or "稳定分析对象",
            }
        )
    return candidates


def _supplement_asset_candidates(
    *,
    record_id: str,
    record_title: str | None,
    supplements: list[dict[str, Any]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in supplements:
        asset_id = str(row.get("id") or "").strip()
        if not asset_id:
            continue
        title = _truncate_text(row.get("title"), 60)
        summary = _truncate_text(row.get("content"), 180)
        entry_type = str(row.get("supplement_type") or row.get("entry_type") or "grammar_note")
        candidates.append(
            {
                "record_id": record_id,
                "record_title": record_title or "",
                "asset_type": "supplement",
                "asset_id": asset_id,
                "entry_type": entry_type,
                "title": title or "AI 补充",
                "summary": summary or "AI 补充",
            }
        )
    return candidates


def _filter_asset_candidates(
    candidates: list[dict[str, str]],
    *,
    requested_asset_type: Literal["analysis", "supplement"] | None,
    explicit_asset_id: str | None = None,
    explicit_entry_type: str | None = None,
) -> list[dict[str, str]]:
    filtered = list(candidates)
    if requested_asset_type is not None:
        filtered = [item for item in filtered if item.get("asset_type") == requested_asset_type]
    if explicit_asset_id:
        filtered = [item for item in filtered if item.get("asset_id") == explicit_asset_id]
    if explicit_entry_type:
        filtered = [item for item in filtered if item.get("entry_type") == explicit_entry_type]
    return filtered


async def resolve_structured_asset_references(
    *,
    user_id: UUID,
    current_record_id: UUID,
    external_record_refs: list[dict[str, str]],
    structured_asset_needs: planner.ReaderAskStructuredAssetNeeds,
    bundle_loader: Callable[[UUID, UUID], Awaitable[dict[str, Any]]] | None = None,
    supplement_loader: Callable[[UUID, UUID], Awaitable[list[dict[str, Any]]]] | None = None,
    explicit_asset_refs: list[dict[str, str]] | None = None,
) -> planner.ReaderAskStructuredAssetResolution:
    if not external_record_refs and not explicit_asset_refs:
        return planner.ReaderAskStructuredAssetResolution()
    if not structured_asset_needs.requested and not explicit_asset_refs:
        return planner.ReaderAskStructuredAssetResolution()

    if bundle_loader is None:
        raise RuntimeError("bundle_loader is required for structured asset resolution")
    if supplement_loader is None:
        raise RuntimeError("supplement_loader is required for structured asset resolution")

    explicit_refs = explicit_asset_refs or []
    if explicit_refs:
        resolved_assets: list[dict[str, str]] = []
        for asset_ref in explicit_refs:
            record_id = str(asset_ref.get("record_id") or "").strip()
            if not record_id:
                continue
            record_uuid = UUID(record_id)
            if record_uuid == current_record_id:
                continue
            bundle = await bundle_loader(user_id, record_uuid)
            supplement_rows = await supplement_loader(user_id, record_uuid)
            candidates = [
                *_analysis_asset_candidates(record_id=record_id, record_title=bundle.get("title"), render_scene=bundle.get("render_scene") or {}),
                *_supplement_asset_candidates(record_id=record_id, record_title=bundle.get("title"), supplements=supplement_rows),
            ]
            matches = _filter_asset_candidates(
                candidates,
                requested_asset_type=asset_ref.get("asset_type"),  # type: ignore[arg-type]
                explicit_asset_id=str(asset_ref.get("asset_id") or "").strip() or None,
                explicit_entry_type=str(asset_ref.get("entry_type") or "").strip() or None,
            )
            resolved_assets.extend(matches[:1])
        return planner.ReaderAskStructuredAssetResolution(
            attempted=bool(resolved_assets),
            status="resolved" if resolved_assets else "not_found",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已并入显式指定的外部稳定资产。" if resolved_assets else "没有找到显式指定的外部稳定资产。",
            record_id=resolved_assets[0]["record_id"] if resolved_assets else None,
            record_title=resolved_assets[0].get("record_title") if resolved_assets else None,
            resolved_assets=resolved_assets,
        )

    if len(external_record_refs) != 1:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=False,
            status="not_needed",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="需要先确定唯一外部文章，再继续定位其中的稳定资产。",
        )

    target = external_record_refs[0]
    record_id = str(target.get("record_id") or "").strip()
    if not record_id:
        return planner.ReaderAskStructuredAssetResolution()
    record_uuid = UUID(record_id)
    if record_uuid == current_record_id:
        return planner.ReaderAskStructuredAssetResolution()

    bundle = await bundle_loader(user_id, record_uuid)
    supplement_rows = await supplement_loader(user_id, record_uuid)
    candidates = [
        *_analysis_asset_candidates(record_id=record_id, record_title=bundle.get("title"), render_scene=bundle.get("render_scene") or {}),
        *_supplement_asset_candidates(record_id=record_id, record_title=bundle.get("title"), supplements=supplement_rows),
    ]
    matches = _filter_asset_candidates(
        candidates,
        requested_asset_type=structured_asset_needs.requested_asset_type,
    )
    if not matches:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="not_found",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已定位到外部文章，但当前没有命中可并入的稳定资产。",
            record_id=record_id,
            record_title=bundle.get("title"),
        )
    if len(matches) > 1:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="ambiguous",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已定位到外部文章，但命中了多个稳定资产，请先指定要并入哪一个。",
            record_id=record_id,
            record_title=bundle.get("title"),
            ambiguous_assets=matches[:4],
        )
    return planner.ReaderAskStructuredAssetResolution(
        attempted=True,
        status="resolved",
        requested_asset_type=structured_asset_needs.requested_asset_type,
        reason="已命中外部文章里的稳定资产。",
        record_id=record_id,
        record_title=bundle.get("title"),
        resolved_assets=matches,
    )


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
    runner_up_score = ranked[1][0] if len(ranked) > 1 else None
    high_confidence_single_hit = top_score >= 90 and len(top_hits) == 1
    clear_margin = runner_up_score is None or (top_score - runner_up_score) >= 20
    if not high_confidence_single_hit or not clear_margin:
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
