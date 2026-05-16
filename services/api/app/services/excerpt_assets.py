from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection
from app.schemas.excerpt_assets import (
    ExcerptAnchorType,
    ExcerptAssetState,
    ExcerptAssetsResponse,
)

_FAVORITE_FIELDS = (
    "id, target_type, target_key, analysis_record_id, payload_json, note, "
    "created_at, updated_at"
)
_ANNOTATION_FIELDS = (
    "id, analysis_record_id, annotation_type, anchor_type, target_key, "
    "paragraph_id, sentence_id, selected_text, start_offset, end_offset, "
    "text_hash, color, note, payload_json, created_at, updated_at"
)


@dataclass
class _ResolvedRecord:
    record_id: str
    client_record_id: str | None
    title: str | None
    source_text: str
    render_scene_json: dict[str, Any]


@dataclass
class _ExcerptAsset:
    target_key: str
    record_id: str
    client_record_id: str | None
    title: str
    subtitle: str | None
    anchor_type: str
    sentence_id: str | None
    selected_text: str
    translation: str | None
    start_offset: int | None
    end_offset: int | None
    text_hash: str | None
    segments: list[dict[str, Any]]
    updated_at: datetime
    is_favorited: bool = False
    is_highlighted: bool = False
    is_noted: bool = False
    annotation_id: str | None = None
    annotation_type: str | None = None
    annotation_color: str | None = None
    note: str | None = None
    insights: list[dict[str, Any]] = field(default_factory=list)


def _ensure_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _read_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _read_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _trim_text(value: str | None, max_length: int) -> str | None:
    text = _read_string(value)
    if not text:
        return None
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length]}..."


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _parse_record_filter(record_id: str | None) -> tuple[UUID | None, str | None]:
    normalized = _read_string(record_id)
    if not normalized:
        return None, None
    return (UUID(normalized), normalized) if _is_uuid(normalized) else (None, normalized)


def _extract_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _extract_render_scene_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _title_from_record(record: _ResolvedRecord | None) -> str | None:
    if not record:
        return None
    explicit = _trim_text(record.title, 72)
    if explicit:
        return explicit
    render_scene_title = _trim_text(
        _extract_render_scene_string(record.render_scene_json, "title"),
        72,
    )
    if render_scene_title:
        return render_scene_title
    first_line = next(
        (
            line.strip()
            for line in record.source_text.splitlines()
            if isinstance(line, str) and line.strip()
        ),
        "",
    )
    return _trim_text(first_line, 72) or "未命名文章"


def _subtitle_from_record(record: _ResolvedRecord | None) -> str | None:
    if not record:
        return None
    return _trim_text(record.source_text, 96)


def _resolve_article_display(
    record: _ResolvedRecord | None,
    payload: dict[str, Any],
) -> tuple[str, str | None]:
    zh_title = _trim_text(
        _read_string(payload.get("article_title_zh"))
        or _read_string(payload.get("title_zh")),
        72,
    )
    record_title = _title_from_record(record)
    payload_title = _trim_text(_read_string(payload.get("article_title")), 72)
    title = zh_title or record_title or payload_title or "未命名文章"
    subtitle = None
    if payload_title and payload_title != title:
        subtitle = _trim_text(payload_title, 86)
    elif not subtitle:
        subtitle = _subtitle_from_record(record)
    return title, subtitle


def _insight_meta(entry_type: str) -> tuple[str, str] | None:
    mapping = {
        "grammar_note": ("grammar", "语法"),
        "sentence_analysis": ("sentence", "句析"),
        "term_note": ("term", "术语"),
        "logic_note": ("logic", "逻辑"),
        "interpretation_note": ("interpretation", "解读"),
        "content_summary": ("summary", "概要"),
    }
    return mapping.get(entry_type)


def _detail_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(
        value.replace("#", " ")
        .replace(">", " ")
        .replace("*", " ")
        .replace("_", " ")
        .replace("`", " ")
        .replace("-", " ")
        .split()
    )
    return normalized or None


def _payload_review_assets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    raw_assets = _read_list(payload.get("review_assets"))
    for index, raw in enumerate(raw_assets):
        if not isinstance(raw, dict):
            continue
        entry_type = _read_string(raw.get("type"))
        if not entry_type:
            continue
        meta = _insight_meta(entry_type)
        if not meta:
            continue
        title = _trim_text(
            _read_string(raw.get("title")) or _read_string(raw.get("label")) or meta[1],
            28,
        ) or meta[1]
        insights.append(
            {
                "id": _read_string(raw.get("id")) or f"{entry_type}-{index}",
                "type": meta[0],
                "label": meta[1],
                "title": title,
                "detail": _detail_text(_read_string(raw.get("summary"))),
            }
        )
    return insights


def _record_sentence_insights(
    record: _ResolvedRecord | None,
    sentence_ids: list[str],
) -> list[dict[str, Any]]:
    if not record or not sentence_ids:
        return []
    sentence_id_set = {sentence_id for sentence_id in sentence_ids if sentence_id}
    if not sentence_id_set:
        return []
    render_scene = record.render_scene_json
    entries = _extract_list(render_scene, "sentence_entries", "sentenceEntries")
    seen: set[tuple[str, str]] = set()
    insights: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sentence_id = _read_string(entry.get("sentence_id")) or _read_string(entry.get("sentenceId"))
        if sentence_id not in sentence_id_set:
            continue
        entry_type = _read_string(entry.get("entry_type")) or _read_string(entry.get("entryType"))
        if not entry_type:
            continue
        meta = _insight_meta(entry_type)
        if not meta:
            continue
        title = _trim_text(
            _read_string(entry.get("title")) or _read_string(entry.get("label")) or meta[1],
            28,
        ) or meta[1]
        dedupe_key = (meta[0], title)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        insights.append(
            {
                "id": _read_string(entry.get("id")) or f"{entry_type}:{sentence_id}:{len(insights)}",
                "type": meta[0],
                "label": meta[1],
                "title": title,
                "detail": _detail_text(_read_string(entry.get("content"))),
            }
        )
    return insights


def _merge_insights(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for insight in [*primary, *fallback]:
        key = (_read_string(insight.get("type")) or "", _read_string(insight.get("title")) or "")
        if key in seen:
            continue
        seen.add(key)
        merged.append(insight)
    return merged


def _read_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for raw in _read_list(payload.get("segments")):
        if not isinstance(raw, dict):
            continue
        sentence_id = _read_string(raw.get("sentence_id"))
        selected_text = _read_string(raw.get("selected_text"))
        start_offset = _read_int(raw.get("start_offset"))
        end_offset = _read_int(raw.get("end_offset"))
        text_hash = _read_string(raw.get("text_hash"))
        if not sentence_id or not selected_text or start_offset is None or end_offset is None or not text_hash:
            continue
        segments.append(
            {
                "paragraph_id": _read_string(raw.get("paragraph_id")),
                "sentence_id": sentence_id,
                "selected_text": selected_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "text_hash": text_hash,
            }
        )
    return segments


def _selected_text_from_segments(segments: list[dict[str, Any]]) -> str | None:
    if not segments:
        return None
    parts = [
        _read_string(segment.get("selected_text"))
        for segment in segments
    ]
    accepted = [part for part in parts if part]
    if not accepted:
        return None
    return " ... ".join(accepted)


def _sentence_translation(record: _ResolvedRecord | None, sentence_id: str | None) -> str | None:
    if not record or not sentence_id:
        return None
    translations = _extract_list(record.render_scene_json, "translations")
    for raw in translations:
        if not isinstance(raw, dict):
            continue
        candidate_id = _read_string(raw.get("sentence_id")) or _read_string(raw.get("sentenceId"))
        if candidate_id != sentence_id:
            continue
        return _read_string(raw.get("translation_zh")) or _read_string(raw.get("translationZh"))
    return None


def _sentence_order(sentence_id: str | None) -> int:
    if not sentence_id:
        return 2**31 - 1
    digits = "".join(ch for ch in sentence_id if ch.isdigit())
    return int(digits) if digits else 2**31 - 1


def _resolve_record(
    record_map: dict[str, _ResolvedRecord],
    client_record_map: dict[str, _ResolvedRecord],
    analysis_record_id: str | None,
    client_record_id: str | None,
) -> _ResolvedRecord | None:
    if analysis_record_id and analysis_record_id in record_map:
        return record_map[analysis_record_id]
    if client_record_id and client_record_id in client_record_map:
        return client_record_map[client_record_id]
    return None


async def _load_favorites(
    conn,
    user_id: UUID,
    record_uuid: UUID | None,
    record_token: str | None,
    anchor_type: ExcerptAnchorType | None,
) -> list[dict[str, Any]]:
    has_record_filter = record_uuid is not None or bool(record_token)
    rows = await conn.fetch(
        f"""
        SELECT {_FAVORITE_FIELDS}
        FROM favorite_records
        WHERE user_id = $1
          AND deleted_at IS NULL
          AND target_type IN ('sentence', 'text_range', 'multi_text')
          AND ($2::boolean = FALSE OR analysis_record_id = $3 OR payload_json->>'client_record_id' = $4)
          AND ($5::text IS NULL OR target_type = $5)
        ORDER BY updated_at DESC
        """,
        user_id,
        has_record_filter,
        record_uuid,
        record_token,
        anchor_type,
    )
    return [{**dict(row), "payload_json": _ensure_json_dict(row["payload_json"])} for row in rows]


async def _load_annotations(
    conn,
    user_id: UUID,
    record_uuid: UUID | None,
    record_token: str | None,
    anchor_type: ExcerptAnchorType | None,
) -> list[dict[str, Any]]:
    has_record_filter = record_uuid is not None or bool(record_token)
    rows = await conn.fetch(
        f"""
        SELECT {_ANNOTATION_FIELDS}
        FROM user_annotations
        WHERE user_id = $1
          AND deleted_at IS NULL
          AND anchor_type IN ('sentence', 'text_range', 'multi_text')
          AND ($2::boolean = FALSE OR analysis_record_id = $3 OR payload_json->>'client_record_id' = $4)
          AND ($5::text IS NULL OR anchor_type = $5)
        ORDER BY updated_at DESC
        """,
        user_id,
        has_record_filter,
        record_uuid,
        record_token,
        anchor_type,
    )
    return [{**dict(row), "payload_json": _ensure_json_dict(row["payload_json"])} for row in rows]


async def _load_records(
    conn,
    user_id: UUID,
    record_ids: list[str],
    client_record_ids: list[str],
) -> tuple[dict[str, _ResolvedRecord], dict[str, _ResolvedRecord]]:
    if not record_ids and not client_record_ids:
        return {}, {}
    uuid_ids = [UUID(value) for value in record_ids if _is_uuid(value)]
    rows = await conn.fetch(
        """
        SELECT r.id, r.client_record_id, r.title, r.source_text, c.render_scene_json
        FROM analysis_records r
        LEFT JOIN analysis_results c ON c.record_id = r.id
        WHERE r.user_id = $1
          AND r.deleted_at IS NULL
          AND (
            (cardinality($2::uuid[]) > 0 AND r.id = ANY($2::uuid[]))
            OR (cardinality($3::text[]) > 0 AND r.client_record_id = ANY($3::text[]))
          )
        """,
        user_id,
        uuid_ids,
        client_record_ids,
    )
    record_map: dict[str, _ResolvedRecord] = {}
    client_record_map: dict[str, _ResolvedRecord] = {}
    for row in rows:
        resolved = _ResolvedRecord(
            record_id=str(row["id"]),
            client_record_id=_read_string(row["client_record_id"]),
            title=_read_string(row["title"]),
            source_text=row["source_text"] or "",
            render_scene_json=_ensure_json_dict(row["render_scene_json"]),
        )
        record_map[resolved.record_id] = resolved
        if resolved.client_record_id:
            client_record_map[resolved.client_record_id] = resolved
    return record_map, client_record_map


def _merge_favorite(
    assets: dict[str, _ExcerptAsset],
    favorite: dict[str, Any],
    record_map: dict[str, _ResolvedRecord],
    client_record_map: dict[str, _ResolvedRecord],
) -> None:
    payload = favorite["payload_json"]
    analysis_record_id = str(favorite["analysis_record_id"]) if favorite["analysis_record_id"] else None
    client_record_id = _read_string(payload.get("client_record_id"))
    record = _resolve_record(record_map, client_record_map, analysis_record_id, client_record_id)
    resolved_record_id = analysis_record_id or (record.record_id if record else None) or client_record_id
    selected_text = (
        _read_string(payload.get("selected_text"))
        or _read_string(payload.get("text"))
    )
    segments = _read_segments(payload)
    selected_text = selected_text or _selected_text_from_segments(segments)
    if not resolved_record_id or not selected_text:
        return
    sentence_id = _read_string(payload.get("sentence_id")) or (segments[0]["sentence_id"] if segments else None)
    title, subtitle = _resolve_article_display(record, payload)
    translation = _read_string(payload.get("translation")) or _sentence_translation(record, sentence_id)
    record_insights = _record_sentence_insights(
        record,
        [segment["sentence_id"] for segment in segments] or ([sentence_id] if sentence_id else []),
    )
    key = favorite["target_key"]
    current = assets.get(key)
    updated_at = favorite["updated_at"]
    favorite_anchor_type = "sentence" if favorite["target_type"] == "sentence" else favorite["target_type"]
    if current:
        current.is_favorited = True
        current.updated_at = max(current.updated_at, updated_at)
        current.client_record_id = current.client_record_id or client_record_id
        current.translation = current.translation or translation
        current.insights = _merge_insights(
            record_insights,
            _merge_insights(current.insights, _payload_review_assets(payload)),
        )
        return
    assets[key] = _ExcerptAsset(
        target_key=key,
        record_id=resolved_record_id,
        client_record_id=client_record_id or (record.client_record_id if record else None),
        title=title,
        subtitle=subtitle,
        anchor_type=favorite_anchor_type,
        sentence_id=sentence_id,
        selected_text=selected_text,
        translation=translation,
        start_offset=_read_int(payload.get("start_offset")) if favorite_anchor_type == "text_range" else None,
        end_offset=_read_int(payload.get("end_offset")) if favorite_anchor_type == "text_range" else None,
        text_hash=_read_string(payload.get("text_hash")) if favorite_anchor_type == "text_range" else None,
        segments=segments,
        updated_at=updated_at,
        is_favorited=True,
        insights=_merge_insights(record_insights, _payload_review_assets(payload)),
    )


def _merge_annotation(
    assets: dict[str, _ExcerptAsset],
    annotation: dict[str, Any],
    record_map: dict[str, _ResolvedRecord],
    client_record_map: dict[str, _ResolvedRecord],
) -> None:
    payload = annotation["payload_json"]
    analysis_record_id = str(annotation["analysis_record_id"]) if annotation["analysis_record_id"] else None
    client_record_id = _read_string(payload.get("client_record_id"))
    record = _resolve_record(record_map, client_record_map, analysis_record_id, client_record_id)
    resolved_record_id = analysis_record_id or (record.record_id if record else None) or client_record_id
    segments = _read_segments(payload)
    selected_text = _read_string(annotation["selected_text"]) or _selected_text_from_segments(segments)
    if not resolved_record_id or not selected_text:
        return
    sentence_id = _read_string(annotation["sentence_id"]) or (segments[0]["sentence_id"] if segments else None)
    title, subtitle = _resolve_article_display(record, payload)
    translation = _read_string(payload.get("translation")) or _sentence_translation(record, sentence_id)
    record_insights = _record_sentence_insights(
        record,
        [segment["sentence_id"] for segment in segments] or ([sentence_id] if sentence_id else []),
    )
    key = annotation["target_key"]
    current = assets.get(key)
    updated_at = annotation["updated_at"]
    is_noted = bool(_read_string(annotation.get("note")))
    is_highlighted = annotation["annotation_type"] == "highlight"
    if current:
        current.updated_at = max(current.updated_at, updated_at)
        current.is_highlighted = current.is_highlighted or is_highlighted
        current.is_noted = current.is_noted or is_noted
        current.annotation_id = str(annotation["id"])
        current.annotation_type = annotation["annotation_type"]
        current.annotation_color = annotation["color"]
        current.note = _read_string(annotation.get("note")) or current.note
        current.translation = current.translation or translation
        current.client_record_id = current.client_record_id or client_record_id
        current.insights = _merge_insights(
            record_insights,
            _merge_insights(current.insights, _payload_review_assets(payload)),
        )
        if annotation["anchor_type"] == "text_range":
            current.start_offset = annotation["start_offset"]
            current.end_offset = annotation["end_offset"]
            current.text_hash = annotation["text_hash"]
        if segments:
            current.segments = segments
        return
    assets[key] = _ExcerptAsset(
        target_key=key,
        record_id=resolved_record_id,
        client_record_id=client_record_id or (record.client_record_id if record else None),
        title=title,
        subtitle=subtitle,
        anchor_type=annotation["anchor_type"],
        sentence_id=sentence_id,
        selected_text=selected_text,
        translation=translation,
        start_offset=annotation["start_offset"] if annotation["anchor_type"] == "text_range" else None,
        end_offset=annotation["end_offset"] if annotation["anchor_type"] == "text_range" else None,
        text_hash=annotation["text_hash"] if annotation["anchor_type"] == "text_range" else None,
        segments=segments,
        updated_at=updated_at,
        is_highlighted=is_highlighted,
        is_noted=is_noted,
        annotation_id=str(annotation["id"]),
        annotation_type=annotation["annotation_type"],
        annotation_color=annotation["color"],
        note=_read_string(annotation.get("note")),
        insights=_merge_insights(record_insights, _payload_review_assets(payload)),
    )


def _matches_asset_state(asset: _ExcerptAsset, asset_state: ExcerptAssetState) -> bool:
    if asset_state == "all":
        return True
    if asset_state == "favorite":
        return asset.is_favorited
    if asset_state == "highlight":
        return asset.is_highlighted
    if asset_state == "note":
        return asset.is_noted
    return len(asset.insights) > 0


def _group_assets(
    assets: list[_ExcerptAsset],
    page: int,
    limit: int,
) -> ExcerptAssetsResponse:
    groups: dict[str, dict[str, Any]] = {}
    for asset in assets:
        current = groups.get(asset.record_id)
        group_updated_at = (
            max(asset.updated_at, current["updated_at"])
            if current
            else asset.updated_at
        )
        groups[asset.record_id] = {
            "record_id": asset.record_id,
            "client_record_id": asset.client_record_id
            or (current["client_record_id"] if current else None),
            "title": current["title"]
            if current and current["title"] != "未命名文章"
            else asset.title,
            "subtitle": current["subtitle"] if current and current["subtitle"] else asset.subtitle,
            "updated_at": group_updated_at,
            "items": [*(current["items"] if current else []), asset],
        }

    grouped_items = []
    for group in groups.values():
        items = sorted(
            group["items"],
            key=lambda item: (_sentence_order(item.sentence_id), -item.updated_at.timestamp()),
        )
        grouped_items.append(
            {
                "record_id": group["record_id"],
                "client_record_id": group["client_record_id"],
                "title": group["title"],
                "subtitle": group["subtitle"],
                "updated_at": group["updated_at"].isoformat(),
                "asset_count": len(items),
                "items": [
                    {
                        "target_key": item.target_key,
                        "anchor_type": item.anchor_type,
                        "sentence_id": item.sentence_id,
                        "selected_text": item.selected_text,
                        "translation": item.translation,
                        "start_offset": item.start_offset,
                        "end_offset": item.end_offset,
                        "text_hash": item.text_hash,
                        "segments": item.segments,
                        "updated_at": item.updated_at.isoformat(),
                        "is_favorited": item.is_favorited,
                        "is_highlighted": item.is_highlighted,
                        "is_noted": item.is_noted,
                        "annotation_id": item.annotation_id,
                        "annotation_type": item.annotation_type,
                        "annotation_color": item.annotation_color,
                        "note": item.note,
                        "insights": item.insights,
                    }
                    for item in items
                ],
            }
        )

    grouped_items.sort(key=lambda group: group["updated_at"], reverse=True)
    total_groups = len(grouped_items)
    offset = (page - 1) * limit
    paged_groups = grouped_items[offset:offset + limit]
    total_assets = sum(group["asset_count"] for group in grouped_items)
    return ExcerptAssetsResponse(
        groups=paged_groups,
        total_assets=total_assets,
        total_groups=total_groups,
        page=page,
        limit=limit,
    )


async def list_excerpt_assets(
    user_id: UUID,
    page: int = 1,
    limit: int = 20,
    record_id: str | None = None,
    asset_state: ExcerptAssetState = "all",
    anchor_type: ExcerptAnchorType | None = None,
) -> ExcerptAssetsResponse:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    record_uuid, record_token = _parse_record_filter(record_id)

    async with pool.acquire() as conn:
        favorites = await _load_favorites(conn, user_id, record_uuid, record_token, anchor_type)
        annotations = await _load_annotations(conn, user_id, record_uuid, record_token, anchor_type)

        record_ids = {
            str(item["analysis_record_id"])
            for item in favorites
            if item.get("analysis_record_id")
        } | {
            str(item["analysis_record_id"])
            for item in annotations
            if item.get("analysis_record_id")
        }
        client_record_ids = {
            client_id
            for client_id in (
                _read_string(item["payload_json"].get("client_record_id"))
                for item in [*favorites, *annotations]
            )
            if client_id
        }
        record_map, client_record_map = await _load_records(
            conn,
            user_id,
            sorted(record_ids),
            sorted(client_record_ids),
        )

    assets: dict[str, _ExcerptAsset] = {}
    for favorite in favorites:
        _merge_favorite(assets, favorite, record_map, client_record_map)
    for annotation in annotations:
        _merge_annotation(assets, annotation, record_map, client_record_map)

    filtered_assets = [
        asset
        for asset in assets.values()
        if _matches_asset_state(asset, asset_state)
    ]
    return _group_assets(filtered_assets, page, limit)
