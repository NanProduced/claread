"""
数据库模块。

提供 PostgreSQL async 连接池和 Redis 可选连接。
不强制要求 Redis 可用，redis_enabled=False 时跳过连接。
"""

from __future__ import annotations

from app.database.connection import (
    DB_POOL,
    RedisPool,
    acquire_db,
    close_db,
    close_redis,
    get_redis,
    init_db,
    init_redis,
    is_db_ready,
    is_redis_ready,
)
from app.database.session import get_db_session

__all__ = [
    "DB_POOL",
    "RedisPool",
    "acquire_db",
    "close_db",
    "close_redis",
    "get_db_session",
    "get_redis",
    "init_db",
    "init_redis",
    "is_db_ready",
    "is_redis_ready",
]
