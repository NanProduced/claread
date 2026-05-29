from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.schemas.analysis import AnalyzeRequestMeta
from app.schemas.reader_scene import (
    ReaderArticle,
    ReaderArticleParagraph,
    ReaderArticleSentence,
    ReaderRecordMeta,
    ReaderSceneModel,
    ReaderSceneResponse,
    ReaderViewMeta,
)
from app.services.reader_ask import supplements as ask_supplements_svc
from app.services.user_assets import records as records_svc

_READER_VIEW_VERSION = "reader-scene-1"
_DEFAULT_SCHEMA_VERSION = "3.0.0"


def _read_string(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _read_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


async def get_record_supplements(user_id: UUID, record_id: UUID) -> list[dict[str, Any]]:
    return await ask_supplements_svc.list_supplements_for_record(user_id, record_id)


def merge_record_scene_with_supplements(
    render_scene_json: dict[str, Any] | None,
    supplements: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(render_scene_json, dict) or not render_scene_json:
        return None, False
    if not supplements:
        return render_scene_json, False
    return ask_supplements_svc.merge_supplements_into_render_scene(render_scene_json, supplements), True


async def merge_record_with_reader_ask_supplements(user_id: UUID, record: dict[str, Any]) -> dict[str, Any]:
    render_scene = record.get("render_scene_json")
    if not isinstance(render_scene, dict) or not render_scene:
        return record
    supplements = await get_record_supplements(user_id, UUID(str(record["id"])))
    if not supplements:
        return record
    merged = dict(record)
    merged_scene, _ = merge_record_scene_with_supplements(render_scene, supplements)
    merged["render_scene_json"] = merged_scene
    return merged


def _source_text_to_article(source_text: str) -> ReaderArticle:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", source_text) if part.strip()]
    sentence_models: list[ReaderArticleSentence] = []
    paragraph_models: list[ReaderArticleParagraph] = []

    for paragraph_index, paragraph_text in enumerate(paragraphs):
        paragraph_id = f"p{paragraph_index}"
        sentence_ids: list[str] = []
        sentence_texts = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph_text)
            if sentence.strip()
        ]

        for sentence_text in sentence_texts:
            sentence_id = f"s{len(sentence_models)}"
            sentence_ids.append(sentence_id)
            sentence_models.append(
                ReaderArticleSentence(
                    sentence_id=sentence_id,
                    paragraph_id=paragraph_id,
                    text=sentence_text,
                )
            )

        paragraph_models.append(
            ReaderArticleParagraph(paragraph_id=paragraph_id, sentence_ids=sentence_ids)
        )

    if not sentence_models:
        return ReaderArticle(
            paragraphs=[ReaderArticleParagraph(paragraph_id="p0", sentence_ids=["s0"])],
            sentences=[ReaderArticleSentence(sentence_id="s0", paragraph_id="p0", text=source_text)],
        )

    return ReaderArticle(paragraphs=paragraph_models, sentences=sentence_models)


def _project_article(scene_article: dict[str, Any], source_text: str) -> tuple[ReaderArticle, str]:
    sentences = [
        ReaderArticleSentence(
            sentence_id=_read_string(sentence.get("sentence_id")),
            paragraph_id=_read_string(sentence.get("paragraph_id")),
            text=_read_string(sentence.get("text")),
        )
        for sentence in _read_list(scene_article.get("sentences"))
        if _is_record(sentence)
    ]
    sentences = [sentence for sentence in sentences if sentence.sentence_id and sentence.text]

    paragraphs = [
        ReaderArticleParagraph(
            paragraph_id=_read_string(paragraph.get("paragraph_id")),
            sentence_ids=[
                _read_string(sentence_id)
                for sentence_id in _read_list(paragraph.get("sentence_ids"))
                if _read_string(sentence_id)
            ],
        )
        for paragraph in _read_list(scene_article.get("paragraphs"))
        if _is_record(paragraph)
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph.paragraph_id]

    if not paragraphs or not sentences:
        fallback_source = _read_string(scene_article.get("source_text"), source_text)
        return _source_text_to_article(fallback_source), "article_rebuilt_from_source_text"

    return ReaderArticle(paragraphs=paragraphs, sentences=sentences), "none"


def _build_request_meta(record: dict[str, Any], render_scene: dict[str, Any] | None) -> AnalyzeRequestMeta:
    request = render_scene.get("request") if isinstance(render_scene, dict) else None
    if isinstance(request, dict):
        return AnalyzeRequestMeta.model_validate(
            {
                "request_id": request.get("request_id") or record.get("client_record_id") or str(record["id"]),
                "source_type": request.get("source_type") or record["source_type"],
                "reading_goal": request.get("reading_goal") or record.get("reading_goal") or "daily_reading",
                "reading_variant": request.get("reading_variant") or record.get("reading_variant") or "intermediate_reading",
                "profile_id": request.get("profile_id") or "upstream",
            }
        )

    request_payload = (
        record.get("request_payload_json")
        if isinstance(record.get("request_payload_json"), dict)
        else {}
    )
    return AnalyzeRequestMeta.model_validate(
        {
            "request_id": record.get("client_record_id") or str(record["id"]),
            "source_type": request_payload.get("source_type") or record["source_type"],
            "reading_goal": request_payload.get("reading_goal") or record.get("reading_goal") or "daily_reading",
            "reading_variant": request_payload.get("reading_variant") or record.get("reading_variant") or "intermediate_reading",
            "profile_id": request_payload.get("profile_id") or "upstream",
        }
    )


def _build_reader_scene(
    record: dict[str, Any],
    render_scene: dict[str, Any] | None,
    supplements_merged: bool,
) -> ReaderSceneResponse:
    source_text = _read_string(record.get("source_text"))
    record_meta = ReaderRecordMeta.model_validate(record)

    if not isinstance(render_scene, dict) or not render_scene:
        request = _build_request_meta(record, None)
        reader_scene = ReaderSceneModel(
            schema_version=record.get("schema_version") or _DEFAULT_SCHEMA_VERSION,
            request=request,
            article=_source_text_to_article(source_text),
            user_facing_state=record.get("user_facing_state") or "normal",
        )
        view_meta = ReaderViewMeta(
            view_version=_READER_VIEW_VERSION,
            data_source="source_text_fallback",
            fallback_mode="scene_missing",
            supplements_merged=supplements_merged,
        )
        return ReaderSceneResponse(
            record_meta=record_meta,
            reader_scene=reader_scene,
            view_meta=view_meta,
        )

    request = _build_request_meta(record, render_scene)
    scene_article = render_scene.get("article") if isinstance(render_scene.get("article"), dict) else {}
    article, fallback_mode = _project_article(scene_article, source_text)

    reader_scene = ReaderSceneModel(
        schema_version=_read_string(
            render_scene.get("schema_version"),
            record.get("schema_version") or _DEFAULT_SCHEMA_VERSION,
        ),
        request=request,
        article=article,
        user_facing_state=_read_string(
            render_scene.get("user_facing_state"),
            record.get("user_facing_state") or "normal",
        ),
        translations=[item for item in _read_list(render_scene.get("translations")) if _is_record(item)],
        inline_marks=[item for item in _read_list(render_scene.get("inline_marks")) if _is_record(item)],
        sentence_entries=[item for item in _read_list(render_scene.get("sentence_entries")) if _is_record(item)],
        warnings=[item for item in _read_list(render_scene.get("warnings")) if _is_record(item)],
        content_summary=render_scene.get("content_summary"),
        title=_read_string(render_scene.get("title")) or None,
    )
    if fallback_mode != "none" and _read_string(scene_article.get("source_text")):
        reader_scene.article.source_text = _read_string(scene_article.get("source_text"))

    view_meta = ReaderViewMeta(
        view_version=_READER_VIEW_VERSION,
        data_source="render_scene_snapshot" if fallback_mode == "none" else "source_text_fallback",
        fallback_mode=fallback_mode,
        supplements_merged=supplements_merged,
    )
    return ReaderSceneResponse(
        record_meta=record_meta,
        reader_scene=reader_scene,
        view_meta=view_meta,
    )


async def get_reader_scene_by_id(user_id: UUID, record_id: UUID) -> ReaderSceneResponse:
    record = await records_svc.get_record_by_id(user_id=user_id, record_id=record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    supplements = await get_record_supplements(user_id, record_id)
    merged_scene, supplements_merged = merge_record_scene_with_supplements(
        record.get("render_scene_json") if isinstance(record.get("render_scene_json"), dict) else None,
        supplements,
    )
    return _build_reader_scene(record, merged_scene, supplements_merged)


async def get_reader_scene_by_client_id(user_id: UUID, client_record_id: str) -> ReaderSceneResponse:
    record = await records_svc.get_record_by_client_id(user_id=user_id, client_record_id=client_record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    record_id = UUID(str(record["id"]))
    supplements = await get_record_supplements(user_id, record_id)
    merged_scene, supplements_merged = merge_record_scene_with_supplements(
        record.get("render_scene_json") if isinstance(record.get("render_scene_json"), dict) else None,
        supplements,
    )
    return _build_reader_scene(record, merged_scene, supplements_merged)
