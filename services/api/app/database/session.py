"""
FastAPI 依赖注入风格的数据访问接口。

提供 get_db_session() 供路由层使用，自动管理连接获取与归还。

Usage:
    @router.get("/users")
    async def list_users(session: asyncpg.Row | None = Depends(get_db_session)):
        if session is None:
            return {"error": "database unavailable"}
        rows = await session.fetch("SELECT * FROM users LIMIT 10")
        return [dict(r) for r in rows]
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


async def get_db_session() -> AsyncIterator[asyncpg.Connection | None]:
    """
    FastAPI Depends 依赖：从连接池获取一个数据库连接。

    在路由参数中使用：
        @router.get("/records")
        async def get_records(
            session: asyncpg.Connection | None = Depends(get_db_session),
        ):
            if session is None:
                return []
            records = await session.fetch("SELECT * FROM analysis_records LIMIT 10")
            return [dict(r) for r in records]

    Returns None 而不是抛出异常，当数据库不可用时优雅降级。
    """
    from app.database.connection import DB_POOL

    if DB_POOL is None:
        yield None
        return

    async with DB_POOL.acquire() as conn:
        yield conn
