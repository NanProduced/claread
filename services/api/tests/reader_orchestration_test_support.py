from __future__ import annotations

from uuid import UUID

import asyncpg

from app.database.connection import init_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceResult,
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL as _BASELINE_SQL,
)
from tests.test_reader_orchestration_schema_baseline import (
    DATABASE_URL,
)

BASELINE_SQL = _BASELINE_SQL


async def make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


async def insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def submit_article_ready(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    plain_text: str = "First sentence.\n\nSecond paragraph for translation.",
    title: str = "Translation Slice",
    language: str = "en",
) -> ArticleReadyPersistenceResult:
    service = ArticleReadyPersistenceService(pool=pool)
    return await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=plain_text,
            title=title,
            language=language,
        )
    )
