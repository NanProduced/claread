"""Resolver facade: structured asset resolution + re-exports from known_reference_resolver.

This module provides:
1. Structured asset resolution (lookup_structured_record_assets,
   resolve_structured_asset_references) — the primary code that lives here.
2. Re-exports from known_reference_resolver for backward compatibility,
   so that existing `from app.services.reader_ask import resolver` +
   `resolver.resolve_known_references` etc. continue to work.

Phase 4 Round 4 extracted known reference resolution into
known_reference_resolver.py. This facade re-exports all public and
test-referenced private symbols to avoid breaking imports in service.py,
context_runtime.py, and test files.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from app.services.reader_ask import planner
from app.services.reader_ask import utils

# Re-export repo for test patching (resolver.repo.list_recent_records).
# The actual repo usage lives in known_reference_resolver, but tests
# patch resolver.repo to control async behavior.
from app.services.reader_ask import repository as repo  # noqa: F401

# Re-export all known reference resolution symbols from the new module.
# This preserves backward compatibility for:
# - service.py: resolver_svc.resolve_known_references, resolve_structured_asset_references
# - context_runtime.py: resolver_svc.lookup_structured_record_assets
# - test_reader_ask_resolver.py: resolver._score_title_match, resolver.ReferenceCandidate, etc.
# - test_reader_ask_service.py: resolver_svc.resolve_known_references, lookup_structured_record_assets
from app.services.reader_ask.known_reference_resolver import (  # noqa: F401
    ReferenceCandidate,
    ScoredReferenceCandidate,
    _CROSS_LANG_MAP,
    _EN_TO_ZH_MAP,
    _build_resolution_meta,
    _candidate_payload_from_typed,
    _cross_lang_score,
    _extract_chinese_segments,
    _extract_english_tokens,
    _levenshtein_distance,
    _normalize_title,
    _normalize_title_for_matching,
    _score_title_match,
    _to_reference_candidate,
    _to_reference_candidates,
    _token_match,
    apply_reference_resolution_policy,
    build_reference_candidate_pool,
    resolve_known_references,
    score_reference_candidates,
)

__all__ = [
    "ReferenceCandidate",
    "ScoredReferenceCandidate",
    "apply_reference_resolution_policy",
    "build_reference_candidate_pool",
    "lookup_structured_record_assets",
    "resolve_known_references",
    "resolve_structured_asset_references",
    "score_reference_candidates",
]


# ---------------------------------------------------------------------------
# Structured asset resolution (primary code in this module)
# ---------------------------------------------------------------------------


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
