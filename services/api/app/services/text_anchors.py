from __future__ import annotations

import json
from typing import Any, Iterable, Mapping
from uuid import UUID

from fastapi import HTTPException

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets


def ensure_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def article_sentence_order(render_scene: dict) -> dict[str, int]:
    article = render_scene.get("article")
    if not isinstance(article, dict):
        return {}
    sentences = article.get("sentences")
    if not isinstance(sentences, list):
        return {}
    order: dict[str, int] = {}
    for index, sentence in enumerate(sentences):
        if not isinstance(sentence, dict):
            continue
        sentence_id = sentence.get("sentence_id")
        if isinstance(sentence_id, str) and sentence_id.strip():
            order[sentence_id] = index
    return order


def sentence_map(render_scene: dict) -> dict[str, dict[str, object]]:
    article = render_scene.get("article")
    if not isinstance(article, dict):
        return {}
    sentences = article.get("sentences")
    if not isinstance(sentences, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for sentence in sentences:
        if not isinstance(sentence, dict):
            continue
        sentence_id = sentence.get("sentence_id")
        if isinstance(sentence_id, str) and sentence_id.strip():
            result[sentence_id] = sentence
    return result


def _read_required_str(segment: Mapping[str, Any], key: str, detail: str) -> str:
    value = segment.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=detail)
    return value


def _read_required_int(segment: Mapping[str, Any], key: str, detail: str) -> int:
    value = segment.get(key)
    if not isinstance(value, int):
        raise HTTPException(status_code=400, detail=detail)
    return value


def validate_segment_against_sentence(
    sentence_obj: dict[str, object] | None,
    segment: Mapping[str, Any],
) -> None:
    if sentence_obj is None:
        raise HTTPException(status_code=400, detail="sentence_id is not present in render scene")

    sentence_text = sentence_obj.get("text")
    if not isinstance(sentence_text, str):
        raise HTTPException(status_code=400, detail="sentence text is unavailable in render scene")

    sentence_id = _read_required_str(segment, "sentence_id", "sentence_id is required for text anchors")
    selected_text = _read_required_str(segment, "selected_text", "selected_text is required for text anchors")
    start_offset = _read_required_int(segment, "start_offset", "start_offset is required for text anchors")
    end_offset = _read_required_int(segment, "end_offset", "end_offset is required for text anchors")
    text_hash = _read_required_str(segment, "text_hash", "text_hash is required for text anchors")
    paragraph_id = segment.get("paragraph_id")

    selected_text_at_offsets = slice_by_utf16_offsets(sentence_text, start_offset, end_offset)
    if selected_text_at_offsets is None:
        raise HTTPException(status_code=400, detail="text_range offsets are outside sentence text")
    if selected_text_at_offsets != selected_text:
        raise HTTPException(status_code=400, detail="selected_text does not match sentence offsets")
    if text_hash != compute_text_range_hash(selected_text_at_offsets):
        raise HTTPException(status_code=400, detail="text_hash does not match selected_text")

    expected_paragraph_id = sentence_obj.get("paragraph_id")
    if paragraph_id and isinstance(expected_paragraph_id, str) and paragraph_id != expected_paragraph_id:
        raise HTTPException(status_code=400, detail="paragraph_id does not match render scene sentence")
    if sentence_obj.get("sentence_id") != sentence_id:
        raise HTTPException(status_code=400, detail="sentence_id does not match render scene sentence")


def validate_text_range_against_render_scene(
    render_scene: dict,
    segment: Mapping[str, Any],
) -> None:
    target_sentence_id = _read_required_str(segment, "sentence_id", "sentence_id is required for text anchors")
    validate_segment_against_sentence(sentence_map(render_scene).get(target_sentence_id), segment)


def validate_multi_text_against_render_scene(
    render_scene: dict,
    segments: Iterable[Mapping[str, Any]],
) -> None:
    ordered_segments = list(segments)
    if len(ordered_segments) < 2:
        raise HTTPException(status_code=400, detail="multi_text anchors require at least two segments")

    scene_sentence_map = sentence_map(render_scene)
    scene_sentence_order = article_sentence_order(render_scene)
    previous_order = -1
    seen_sentence_ids: set[str] = set()

    for segment in ordered_segments:
        sentence_id = _read_required_str(segment, "sentence_id", "sentence_id is required for text anchors")
        current_order = scene_sentence_order.get(sentence_id)
        if current_order is None:
            raise HTTPException(status_code=400, detail="sentence_id is not present in render scene")
        if current_order <= previous_order:
            raise HTTPException(status_code=400, detail="multi_text segments must follow article order")
        if sentence_id in seen_sentence_ids:
            raise HTTPException(status_code=400, detail="multi_text segments cannot repeat sentence_id")
        validate_segment_against_sentence(scene_sentence_map.get(sentence_id), segment)
        previous_order = current_order
        seen_sentence_ids.add(sentence_id)


async def load_render_scene(
    conn: Any,
    user_id: UUID,
    record_id: UUID,
) -> dict:
    row = await conn.fetchrow(
        """
        SELECT c.render_scene_json
        FROM analysis_records r
        LEFT JOIN analysis_results c ON r.id = c.record_id
        WHERE r.id = $1 AND r.user_id = $2 AND r.deleted_at IS NULL
        """,
        record_id,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Analysis record not found")

    render_scene = ensure_json_dict(row["render_scene_json"])
    if not render_scene:
        raise HTTPException(status_code=400, detail="text anchors require a render scene")
    return render_scene
