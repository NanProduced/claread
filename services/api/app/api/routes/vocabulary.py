"""生词本管理接口。"""

from __future__ import annotations

import json
from logging import getLogger
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.schemas.user_assets.vocabulary import (
    ReviewResultResponse,
    ReviewSubmitRequest,
    VocabHighlightsRequest,
    VocabHighlightsResponse,
    VocabMatchItem,
    VocabularyCreateRequest,
    VocabularyDeleteResponse,
    VocabularyListResponse,
    VocabularyResponse,
    VocabularyUpdateRequest,
    VocabularyUpsertResponse,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.user_assets import vocabulary as vocab_svc

logger = getLogger("app.api")

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


def _vocab_row_to_response(row: dict) -> VocabularyResponse:
    meanings_raw = row.get("meanings_json")
    payload_raw = row.get("payload_json")
    return VocabularyResponse(
        id=row["id"],
        user_id=row["user_id"],
        lemma=row["lemma"],
        display_word=row["display_word"],
        phonetic=row.get("phonetic"),
        part_of_speech=row.get("part_of_speech"),
        short_meaning=row["short_meaning"],
        meanings_json=json.loads(meanings_raw) if isinstance(meanings_raw, str) else meanings_raw,
        tags=row.get("tags", []),
        exchange=row.get("exchange", []),
        source_provider=row.get("source_provider", "tecd3"),
        dict_entry_id=row.get("dict_entry_id"),
        source_sentence=row.get("source_sentence"),
        source_context=row.get("source_context"),
        mastery_status=row.get("mastery_status", "new"),
        review_count=row.get("review_count", 0),
        last_reviewed_at=row.get("last_reviewed_at"),
        payload_json=json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("", response_model=VocabularyUpsertResponse, summary="添加生词")
async def add_vocabulary(
    current_user: AuthUserDep,
    body: VocabularyCreateRequest,
) -> VocabularyUpsertResponse:
    """添加单词或短语到生词本，按 lemma 去重。"""
    try:
        vocab_id, created, updated_at = await vocab_svc.upsert_vocabulary(
            user_id=UUID(current_user.user_id),
            lemma=body.lemma,
            display_word=body.display_word,
            short_meaning=body.short_meaning,
            dict_entry_id=body.dict_entry_id,
            phonetic=body.phonetic,
            part_of_speech=body.part_of_speech,
            meanings_json=body.meanings_json,
            tags=body.tags,
            exchange=body.exchange,
            source_provider=body.source_provider,
            source_sentence=body.source_sentence,
            source_context=body.source_context,
            payload_json=body.payload_json,
        )
        return VocabularyUpsertResponse(
            id=vocab_id,
            lemma=body.lemma,
            created=created,
            updated_at=updated_at,
        )
    except Exception as e:
        logger.error("add_vocabulary failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("", response_model=VocabularyListResponse, summary="生词列表")
async def get_vocabulary_list(
    current_user: AuthUserDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    mastery_status: str | None = Query(default=None),
    lite: bool = Query(default=False, description="是否仅返回轻量字段用于列表展示"),
) -> VocabularyListResponse:
    """分页获取当前用户的生词列表。"""
    try:
        items, total = await vocab_svc.list_vocabulary(
            user_id=UUID(current_user.user_id),
            page=page,
            limit=limit,
            mastery_status=mastery_status,
            lite=lite,
        )
        return VocabularyListResponse(
            items=[_vocab_row_to_response(row) for row in items],
            total=total,
            page=page,
            limit=limit,
        )
    except Exception as e:
        logger.error("list_vocabulary failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/highlights", response_model=VocabHighlightsResponse, summary="生词高亮匹配")
async def get_vocab_highlights(
    current_user: AuthUserDep,
    body: VocabHighlightsRequest,
) -> VocabHighlightsResponse:
    """查询句子列表中与用户生词本匹配的词条，用于结果页 overlay。"""
    try:
        sentences = [
            {"sentence_id": s.sentence_id, "tokens": s.tokens}
            for s in body.sentences
        ]
        matches = await vocab_svc.find_vocab_highlights(
            user_id=UUID(current_user.user_id),
            sentences=sentences,
        )
        return VocabHighlightsResponse(
            matches=[
                VocabMatchItem(
                    vocab_id=m["vocab_id"],
                    lemma=m["lemma"],
                    sentence_id=m["sentence_id"],
                    anchor_text=m["anchor_text"],
                    occurrence=m["occurrence"],
                    mastery_status=m["mastery_status"],
                )
                for m in matches
            ]
        )
    except Exception as e:
        logger.error("get_vocab_highlights failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/review/due", response_model=VocabularyListResponse, summary="待复习生词")
async def get_due_vocabulary(
    current_user: AuthUserDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> VocabularyListResponse:
    """获取当前用户待复习的生词列表（next_review_at <= now）。"""
    try:
        items = await vocab_svc.get_due_vocabulary(
            user_id=UUID(current_user.user_id),
            limit=limit,
        )
        return VocabularyListResponse(
            items=[_vocab_row_to_response(row) for row in items],
            total=len(items),
            page=1,
            limit=limit,
        )
    except Exception as e:
        logger.error("get_due_vocabulary failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/{vocab_id}/review", response_model=ReviewResultResponse, summary="提交复习结果")
async def submit_vocab_review(
    current_user: AuthUserDep,
    vocab_id: UUID,
    body: ReviewSubmitRequest,
) -> ReviewResultResponse:
    """提交单词复习结果（known / unfamiliar），更新调度数据。"""
    try:
        updated = await vocab_svc.submit_review(
            user_id=UUID(current_user.user_id),
            vocab_id=vocab_id,
            result=body.result.value,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Vocabulary entry not found")

        payload_raw = updated.get("payload_json")
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
        review = payload.get("review", {})

        return ReviewResultResponse(
            vocab_id=updated["id"],
            lemma=updated["lemma"],
            stage=review.get("stage", 0),
            next_review_at=review.get("next_review_at"),
            mastery_status=updated["mastery_status"],
            review_count=updated.get("review_count", 0),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("submit_vocab_review failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.patch("/{vocab_id}", response_model=VocabularyResponse, summary="更新生词")
async def update_vocabulary(
    current_user: AuthUserDep,
    vocab_id: UUID,
    body: VocabularyUpdateRequest,
) -> VocabularyResponse:
    """更新生词条目，如掌握状态等。"""
    try:
        updated = await vocab_svc.update_vocabulary(
            user_id=UUID(current_user.user_id),
            vocab_id=vocab_id,
            mastery_status=body.mastery_status,
            short_meaning=body.short_meaning,
            payload_json=body.payload_json,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Vocabulary entry not found")
        return _vocab_row_to_response(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_vocabulary failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{vocab_id}", response_model=VocabularyDeleteResponse, summary="删除生词")
async def delete_vocabulary(
    current_user: AuthUserDep,
    vocab_id: UUID,
) -> dict:
    """删除一条生词记录。"""
    try:
        deleted = await vocab_svc.delete_vocabulary(
            user_id=UUID(current_user.user_id),
            vocab_id=vocab_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Vocabulary entry not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_vocabulary failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
