"""
PostgreSQL 连接池和 Redis 连接管理。

使用 asyncpg 实现 PostgreSQL async 连接池（与 FastAPI 异步模型匹配）。
Redis 作为可选增强，通过 redis_enabled 标志控制。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = logging.getLogger(__name__)

# 全局连接池句柄
DB_POOL: asyncpg.Pool | None = None
RedisPool: redis.Redis | None = None


async def init_connection(conn: asyncpg.Connection) -> None:
    """初始化数据库连接，注册 JSONB 编解码器。"""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    # 也为 json 字段注册（如有）
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_db(
    database_url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    max_inactive_connection_lifetime: int = 3600,
) -> asyncpg.Pool:
    """
    初始化 PostgreSQL async 连接池。

    Args:
        database_url:  PostgreSQL DSN
        pool_size:    最小连接数
        max_overflow: 最大额外连接数（超过 pool_size 的部分）
        pool_timeout: 等待连接超时（秒）
        max_inactive_connection_lifetime: 空闲连接回收时间（秒）

    Returns:
        asyncpg.Pool 实例
    """
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=pool_size + max_overflow,
        command_timeout=pool_timeout,
        max_inactive_connection_lifetime=max_inactive_connection_lifetime,
        init=init_connection,
        server_settings={"application_name": "claread-server"},
    )
    logger.info("PostgreSQL connection pool created (max_size=%d)", pool_size + max_overflow)
    return DB_POOL


async def close_db() -> None:
    """关闭 PostgreSQL 连接池。"""
    global DB_POOL
    if DB_POOL is not None:
        await DB_POOL.close()
        DB_POOL = None
        logger.info("PostgreSQL connection pool closed")


async def acquire_db() -> asyncpg.Pool | None:
    """
    获取当前 PostgreSQL 连接池。

    Returns:
        asyncpg.Pool 实例，如果未初始化返回 None
    """
    return DB_POOL


async def is_db_ready() -> bool:
    """
    检查 PostgreSQL 是否可用（通过执行简单查询）。

    Returns:
        True 表示可用，False 表示不可用或未初始化
    """
    if DB_POOL is None:
        return False
    try:
        async with DB_POOL.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.warning("PostgreSQL readiness check failed: %s", e)
        return False


async def init_redis(redis_url: str, enabled: bool) -> redis.Redis | None:
    """
    初始化 Redis 连接（可选）。

    Args:
        redis_url: Redis DSN
        enabled:   是否启用，False 时跳过连接

    Returns:
        redis.Redis 实例或 None
    """
    global RedisPool
    if not enabled:
        logger.info("Redis disabled, skipping connection")
        return None

    try:
        import redis.asyncio as redis

        RedisPool = redis.from_url(  # type: ignore[no-untyped-call]
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # 探测连接
        await RedisPool.ping()
        logger.info("Redis connection established")
        return RedisPool
    except Exception as e:
        logger.warning("Redis connection failed (非阻塞，继续启动): %s", e)
        RedisPool = None
        return None


async def close_redis() -> None:
    """关闭 Redis 连接。"""
    global RedisPool
    if RedisPool is not None:
        await RedisPool.aclose()
        RedisPool = None
        logger.info("Redis connection closed")


async def get_redis() -> redis.Redis | None:
    """
    获取当前 Redis 连接。

    Returns:
        redis.Redis 实例或 None
    """
    return RedisPool


async def is_redis_ready() -> bool:
    """
    检查 Redis 是否可用（仅在 enabled=True 时检查）。

    Returns:
        True 表示可用，False 表示不可用或未启用
    """
    if RedisPool is None:
        return False
    try:
        await RedisPool.ping()
        return True
    except Exception:
        return False


@asynccontextmanager
async def acquire_connection() -> AsyncIterator[asyncpg.Connection]:
    """
    上下文管理器：从池中获取一个数据库连接。

    Usage:
        async with acquire_connection() as conn:
            result = await conn.fetch("SELECT * FROM users LIMIT 1")
    """
    if DB_POOL is None:
        raise RuntimeError("Database pool not initialized")
    async with DB_POOL.acquire() as conn:
        yield conn
