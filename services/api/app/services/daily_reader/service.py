"""Daily Reader CRUD service."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import asyncpg
import orjson

from app.database import connection as db_connection
from app.schemas.daily_reader import (
    DailyReaderArticleResponse,
    DailyReaderCoverCandidate,
    DailyReaderListItem,
    DailyReaderListResponse,
    DailyReaderReviewMachineFlags,
    DailyReaderReviewQueueItem,
    DailyReaderReviewQueueResponse,
    DailyReaderSelectedCover,
)
from app.services.daily_reader.extraction import find_boilerplate_hits

logger = logging.getLogger(__name__)

BUSINESS_TZ = timezone(timedelta(hours=8))


def business_today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def encode_cursor(publish_date: date, article_id: str) -> str:
    return f"{publish_date.isoformat()}|{article_id}"


def decode_cursor(cursor: str) -> tuple[date, str]:
    if "|" in cursor:
        parts = cursor.split("|", 1)
        return date.fromisoformat(parts[0]), parts[1]
    try:
        return date.fromisoformat(cursor), ""
    except ValueError:
        raise ValueError(f"Invalid cursor format: {cursor!r}")


async def get_today_articles() -> list[DailyReaderArticleResponse]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    today = business_today()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM daily_readers
                WHERE status = 'published' AND publish_date = $1
                ORDER BY score DESC
                """,
                today,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning empty list")
        return []
    return [_row_to_article_response(row) for row in rows]


async def get_article_by_id(article_id: str) -> DailyReaderArticleResponse | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM daily_readers WHERE id = $1 AND status = 'published'",
                article_id,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning None")
        return None
    if row is None:
        return None
    return _row_to_article_response(row)


async def get_article_by_id_any_status(article_id: str) -> DailyReaderArticleResponse | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM daily_readers WHERE id = $1",
                article_id,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning None")
        return None
    if row is None:
        return None
    return _row_to_article_response(row)


async def list_articles(
    cursor: str | None = None,
    limit: int = 10,
) -> DailyReaderListResponse:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    params: list[object] = [limit + 1]

    if cursor:
        try:
            cursor_date, cursor_id = decode_cursor(cursor)
        except (ValueError, IndexError):
            cursor_date = date.fromisoformat(cursor)
            cursor_id = ""
        params.extend([cursor_date, cursor_id])
        query = """
            SELECT id, title, subtitle, original_title, subtitle_zh,
                   source, publish_date, difficulty,
                   read_time_minutes, tags, cover_image_url, cover_theme
            FROM daily_readers
            WHERE status = 'published'
              AND (publish_date < $2 OR (publish_date = $2 AND id < $3))
            ORDER BY publish_date DESC, id DESC
            LIMIT $1
        """
    else:
        query = """
            SELECT id, title, subtitle, original_title, subtitle_zh,
                   source, publish_date, difficulty,
                   read_time_minutes, tags, cover_image_url, cover_theme
            FROM daily_readers
            WHERE status = 'published'
            ORDER BY publish_date DESC, id DESC
            LIMIT $1
        """

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning empty list")
        return DailyReaderListResponse(items=[], cursor=None, has_more=False)

    has_more = len(rows) > limit
    items = [_row_to_list_item(row) for row in rows[:limit]]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.publish_date, last.id)

    return DailyReaderListResponse(items=items, cursor=next_cursor, has_more=has_more)


async def publish_article(article_id: str, operator: str) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE daily_readers
                SET status = 'published',
                    published_at = NOW(),
                    review_status = 'approved',
                    reviewed_by = $2,
                    reviewed_at = NOW()
                WHERE id = $1 AND status = 'draft'
                """,
                article_id,
                operator,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, publish skipped")
        return False
    return result == "UPDATE 1"


async def unpublish_article(article_id: str, operator: str) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE daily_readers
                SET status = 'draft',
                    published_at = NULL,
                    reviewed_by = $2,
                    reviewed_at = NOW()
                WHERE id = $1 AND status = 'published'
                """,
                article_id,
                operator,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, unpublish skipped")
        return False
    return result == "UPDATE 1"


async def delete_article(article_id: str) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM daily_readers WHERE id = $1 AND status = 'draft'",
                article_id,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, delete skipped")
        return False
    return result == "DELETE 1"


async def get_draft_articles(limit: int = 20) -> list[DailyReaderListItem]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, subtitle, original_title, subtitle_zh,
                       source, publish_date, difficulty,
                       read_time_minutes, tags, cover_image_url, cover_theme
                FROM daily_readers
                WHERE status = 'draft'
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning empty list")
        return []
    return [_row_to_list_item(row) for row in rows]


async def get_review_queue(limit: int = 20, offset: int = 0) -> DailyReaderReviewQueueResponse:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, subtitle, original_title, subtitle_zh,
                       source, source_url, publish_date, difficulty,
                       read_time_minutes, tags, cover_image_url, cover_theme,
                       score, status, review_status, reviewed_by, reviewed_at,
                       created_at, updated_at, body_json, highlights_json,
                       paragraph_notes_json, takeaways_json, pipeline_meta
                FROM daily_readers
                WHERE status = 'draft' AND review_status = 'pending'
                ORDER BY created_at DESC, id DESC
                LIMIT $1 OFFSET $2
                """,
                limit + 1,
                offset,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, returning empty review queue")
        rows = []
    return DailyReaderReviewQueueResponse(
        items=[_row_to_review_queue_item(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


async def update_draft_article(article_id: str, updates: dict[str, object]) -> str:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    title_set = "title" in updates
    subtitle_zh_set = "subtitle_zh" in updates
    cover_set = "cover_image_url" in updates
    tags_set = "tags" in updates

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE daily_readers
            SET title = CASE WHEN $2 THEN $3 ELSE title END,
                subtitle_zh = CASE WHEN $4 THEN $5 ELSE subtitle_zh END,
                cover_image_url = CASE WHEN $6 THEN $7 ELSE cover_image_url END,
                tags = CASE WHEN $8 THEN $9 ELSE tags END,
                review_status = 'pending',
                reviewed_by = NULL,
                reviewed_at = NULL,
                updated_at = NOW()
            WHERE id = $1 AND status = 'draft'
              AND (
                  ($2 AND title IS DISTINCT FROM $3)
                  OR ($4 AND subtitle_zh IS DISTINCT FROM $5)
                  OR ($6 AND cover_image_url IS DISTINCT FROM $7)
                  OR ($8 AND tags IS DISTINCT FROM $9)
              )
            RETURNING id, review_status
            """,
            article_id,
            title_set,
            updates.get("title"),
            subtitle_zh_set,
            updates.get("subtitle_zh"),
            cover_set,
            updates.get("cover_image_url"),
            tags_set,
            updates.get("tags"),
        )
        if row is not None:
            return "updated"

        state = await conn.fetchrow(
            "SELECT status FROM daily_readers WHERE id = $1",
            article_id,
        )
    if state is None:
        return "not_found"
    if state["status"] != "draft":
        return "not_draft"
    return "unchanged"


def _row_to_article_response(row: object) -> DailyReaderArticleResponse:
    return DailyReaderArticleResponse(
        id=row["id"],
        title=row["title"],
        subtitle=row["subtitle"],
        # .get: rows/calls produced before A-3 have neither column value.
        original_title=row.get("original_title"),
        subtitle_zh=row.get("subtitle_zh"),
        source=row["source"],
        source_url=row["source_url"],
        publish_date=row["publish_date"],
        difficulty=row["difficulty"],
        read_time_minutes=row["read_time_minutes"],
        tags=_decode_jsonb(row["tags"], []),
        cover_image_url=row["cover_image_url"],
        cover_theme=row["cover_theme"],
        body=_decode_jsonb(row["body_json"], {}),
        highlights=_decode_jsonb(row["highlights_json"], []),
        paragraph_notes=_decode_jsonb(row["paragraph_notes_json"], {}),
        takeaways=_decode_jsonb(row["takeaways_json"], {}),
    )


def _row_to_list_item(row: object) -> DailyReaderListItem:
    return DailyReaderListItem(
        id=row["id"],
        title=row["title"],
        subtitle=row["subtitle"],
        original_title=row.get("original_title"),
        subtitle_zh=row.get("subtitle_zh"),
        source=row["source"],
        publish_date=row["publish_date"],
        difficulty=row["difficulty"],
        read_time_minutes=row["read_time_minutes"],
        tags=_decode_jsonb(row["tags"], []),
        cover_image_url=row["cover_image_url"],
        cover_theme=row["cover_theme"],
    )


def _row_to_review_queue_item(row: object) -> DailyReaderReviewQueueItem:
    pipeline_meta = _decode_jsonb(row["pipeline_meta"], {})
    if not isinstance(pipeline_meta, dict):
        pipeline_meta = {}
    cover_meta = pipeline_meta.get("cover")
    if not isinstance(cover_meta, dict):
        cover_meta = {}

    candidates: list[DailyReaderCoverCandidate] = []
    raw_candidates = cover_meta.get("candidates")
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates:
            if not isinstance(candidate, dict) or not candidate.get("url"):
                continue
            candidates.append(DailyReaderCoverCandidate.model_validate(candidate))

    selected_cover = None
    raw_selected = cover_meta.get("selected")
    if isinstance(raw_selected, dict):
        raw_selected = raw_selected.get("cover")
    if isinstance(raw_selected, dict) and raw_selected.get("url"):
        selected_cover = DailyReaderSelectedCover.model_validate(raw_selected)

    cover_url = row["cover_image_url"]
    if not cover_url:
        cover_quality = "missing"
    elif selected_cover and selected_cover.url == cover_url:
        cover_quality = "qualified"
    else:
        cover_quality = "unavailable"

    artifacts = [
        _decode_jsonb(row["body_json"], {}),
        _decode_jsonb(row["highlights_json"], []),
        _decode_jsonb(row["paragraph_notes_json"], {}),
        _decode_jsonb(row["takeaways_json"], {}),
    ]
    artifact_texts = [orjson.dumps(value).decode("utf-8") for value in artifacts]
    boilerplate_hits = sorted(set(find_boilerplate_hits(artifact_texts)))

    return DailyReaderReviewQueueItem(
        id=row["id"],
        title=row["title"],
        subtitle=row["subtitle"],
        original_title=row.get("original_title"),
        subtitle_zh=row.get("subtitle_zh"),
        source=row["source"],
        source_url=row["source_url"],
        publish_date=row["publish_date"],
        difficulty=row["difficulty"],
        read_time_minutes=row["read_time_minutes"],
        tags=_decode_jsonb(row["tags"], []),
        cover_image_url=cover_url,
        cover_theme=row["cover_theme"],
        selection_score=row["score"],
        # The workflow review result is not persisted on the current schema.
        review_score=None,
        review_score_available=False,
        status=row["status"],
        review_status=row["review_status"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=row["reviewed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        machine_flags=DailyReaderReviewMachineFlags(
            cover_missing=not bool(cover_url),
            cover_quality=cover_quality,
            cover_width=selected_cover.width if selected_cover else None,
            cover_height=selected_cover.height if selected_cover else None,
            boilerplate_suspected=bool(boilerplate_hits),
            boilerplate_hits=boilerplate_hits,
        ),
        cover_candidates=candidates,
        selected_cover=selected_cover,
    )


def _decode_jsonb(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            decoded = orjson.loads(value)
        except (orjson.JSONDecodeError, ValueError):
            return default
        if isinstance(decoded, (dict, list)):
            return decoded
        if isinstance(decoded, str):
            try:
                return orjson.loads(decoded)
            except (orjson.JSONDecodeError, ValueError):
                return default
        return default
    return value
