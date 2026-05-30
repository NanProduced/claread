"""Daily Reader CRUD service."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta

import asyncpg
import orjson

from app.database import connection as db_connection
from app.schemas.daily_reader import (
    DailyReaderArticleResponse,
    DailyReaderListItem,
    DailyReaderListResponse,
)

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
            SELECT id, title, subtitle, source, publish_date, difficulty,
                   read_time_minutes, tags, cover_image_url, cover_theme
            FROM daily_readers
            WHERE status = 'published'
              AND (publish_date < $2 OR (publish_date = $2 AND id < $3))
            ORDER BY publish_date DESC, id DESC
            LIMIT $1
        """
    else:
        query = """
            SELECT id, title, subtitle, source, publish_date, difficulty,
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


async def publish_article(article_id: str) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE daily_readers
                SET status = 'published', published_at = NOW()
                WHERE id = $1 AND status = 'draft'
                """,
                article_id,
            )
    except asyncpg.UndefinedTableError:
        logger.warning("daily_readers table does not exist, publish skipped")
        return False
    return result == "UPDATE 1"


async def unpublish_article(article_id: str) -> bool:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE daily_readers
                SET status = 'draft', published_at = NULL
                WHERE id = $1 AND status = 'published'
                """,
                article_id,
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
                SELECT id, title, subtitle, source, publish_date, difficulty,
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


def _row_to_article_response(row: object) -> DailyReaderArticleResponse:
    return DailyReaderArticleResponse(
        id=row["id"],
        title=row["title"],
        subtitle=row["subtitle"],
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
        source=row["source"],
        publish_date=row["publish_date"],
        difficulty=row["difficulty"],
        read_time_minutes=row["read_time_minutes"],
        tags=_decode_jsonb(row["tags"], []),
        cover_image_url=row["cover_image_url"],
        cover_theme=row["cover_theme"],
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
