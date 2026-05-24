from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from app.services.reader_ask import planner
from app.services.reader_ask import repository as repo
from app.services.reader_ask import utils


def _normalize_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _normalize_title_for_matching(value: str | None) -> str:
    """Normalize title for fuzzy matching: strip articles, punctuation, and lowercase."""
    text = _normalize_title(value)
    # Remove common English articles
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
            break
    # Remove punctuation for matching
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


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
    # Partial token match (>=50% tokens hit)
    if query_tokens:
        hit_count = sum(1 for token in query_tokens if token in normalized_title)
        if hit_count >= max(len(query_tokens) // 2, 1) and hit_count < len(query_tokens):
            return 50
    # Fuzzy matching on normalized titles (strip articles/punctuation)
    match_query = _normalize_title_for_matching(query)
    match_title = _normalize_title_for_matching(title)
    if match_query and match_title:
        if match_query in match_title or match_title in match_query:
            return 60
        dist = _levenshtein_distance(match_query, match_title)
        max_len = max(len(match_query), len(match_title))
        if max_len > 0 and dist <= 3 and dist / max_len <= 0.3:
            return 40
    return 0


def _truncate_text(value: str | None, limit: int) -> str | None:
    return utils.truncate_text_optional(value, limit)


def _extract_article_overview(render_scene: dict[str, Any]) -> str | None:
    return utils.extract_article_overview(render_scene)


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
    page_state_json: dict[str, Any] | None = None,
    reason: str | None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    overview = utils.resolve_record_overview(
        render_scene=render_scene,
        page_state_json=page_state_json,
    )
    article_overview = overview.get("overview")
    record_insights = _extract_stable_record_insights(render_scene)
    source_labels = ["external_record"]
    if article_overview:
        source_labels.append(str(overview.get("source") or "article_overview"))
    if record_insights:
        source_labels.append("record_assets")
    if not article_overview:
        source_labels.append("overview_missing")
    return {
        "record_id": record_id,
        "record_title": record_title,
        "updated_at": updated_at,
        "article_overview": article_overview,
        "article_overview_status": overview.get("status"),
        "article_overview_source": overview.get("source"),
        "article_overview_confidence": overview.get("confidence"),
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
                "content_md": str(entry.get("content") or "").strip(),
                "source_labels": ["external_asset", "analysis"],
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
                "content_md": str(row.get("content") or "").strip(),
                "source_labels": ["external_asset", "supplement"],
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
        # When user references another article without a title, return recent records as candidates
        rows = await repo.list_recent_records(
            user_id,
            exclude_record_id=current_record_id,
            limit=5,
        )
        if not rows:
            return planner.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query=None,
                reason="请补充你想引用的文章标题，我再把它并入当前讨论。",
            )
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=None,
            reason="请从以下最近阅读的文章中选择你想引用的：",
            ambiguous_records=[_candidate_payload(row) for row in rows],
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
    # Low confidence matches should not be treated as ambiguous candidates
    if top_score < 70:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=reference_needs.query,
            reason=f'没有找到标题能直接命中\u201c{reference_needs.query}\u201d的已知文章。',
        )
    top_hits = [row for score, row in ranked if score == top_score]
    runner_up_score = ranked[1][0] if len(ranked) > 1 else None
    high_confidence_single_hit = top_score >= 90 and len(top_hits) == 1
    clear_margin = runner_up_score is None or (top_score - runner_up_score) >= 20
    if not high_confidence_single_hit or not clear_margin:
        def _candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
            payload = {
                "record_id": row["id"],
                "title": row.get("title") or "Untitled",
                "updated_at": row.get("updated_at"),
            }
            overview_hint = utils.truncate_text_optional(
                utils.resolve_record_overview(
                    render_scene=row.get("render_scene_json") or {},
                    page_state_json=row.get("page_state_json") or {},
                ).get("overview"),
                140,
            )
            if overview_hint:
                payload["overview_hint"] = overview_hint
            return payload

        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=reference_needs.query,
            reason=f"“{reference_needs.query}”命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[
                _candidate_payload(row)
                for row in (top_hits[:3] if top_hits else [candidate for _, candidate in ranked[:3]])
            ],
        )

    match = top_hits[0]
    resolved_payload = {
        "record_id": match["id"],
        "title": match.get("title") or reference_needs.query,
        "updated_at": match.get("updated_at"),
    }
    overview_hint = utils.truncate_text_optional(
        utils.resolve_record_overview(
            render_scene=match.get("render_scene_json") or {},
            page_state_json=match.get("page_state_json") or {},
        ).get("overview"),
        140,
    )
    if overview_hint:
        resolved_payload["overview_hint"] = overview_hint
    return planner.ReaderAskReferenceResolution(
        attempted=True,
        status="resolved",
        query=reference_needs.query,
        reason=f"已命中历史文章“{match.get('title') or reference_needs.query}”。",
        resolved_records=[resolved_payload],
    )
