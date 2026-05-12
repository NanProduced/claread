"""
TECD3 词典查询缓存。

L1: 进程内 OrderedDict + LRU 淘汰（同步，最快）
L2: Redis 共享缓存（异步，跨 Worker）
查询顺序: L1 → L2 → DB
写入顺序: DB → L1 → L2
Redis 不可用时自动降级为纯 L1。

L1 无显式锁：Python GIL 保证 OrderedDict 单操作原子性，
淘汰逻辑在极端并发下可能多淘汰几条，可接受。
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_L1_CACHE: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
_L1_TTL_SECONDS = 60 * 60 * 24
_L1_MAX_SIZE = 1000

_L2_KEY_PREFIX = "dict:"
_L2_TTL_SECONDS = 60 * 60 * 24

_MISS_TTL_SECONDS = 60 * 5
_MISS_MARKER = {"__miss": True}

_cache_hits = 0
_cache_misses = 0
_l2_hits = 0
_l2_misses = 0


def _is_miss(data: dict[str, Any]) -> bool:
    return data.get("__miss") is True


def _l1_get(key: str) -> dict[str, Any] | None:
    global _cache_hits, _cache_misses
    entry = _L1_CACHE.get(key)
    if entry is None:
        _cache_misses += 1
        return None
    data, expiry = entry
    if time.time() > expiry:
        _L1_CACHE.pop(key, None)
        _cache_misses += 1
        return None
    _L1_CACHE.move_to_end(key)
    _cache_hits += 1
    return data


def _l1_set(key: str, data: dict[str, Any], ttl: int | None = None) -> None:
    if key in _L1_CACHE:
        _L1_CACHE[key] = (data, time.time() + (ttl or _L1_TTL_SECONDS))
        _L1_CACHE.move_to_end(key)
        return

    if len(_L1_CACHE) >= _L1_MAX_SIZE:
        now = time.time()
        expired = [k for k, (_, exp) in _L1_CACHE.items() if now > exp]
        for k in expired:
            _L1_CACHE.pop(k, None)

        if len(_L1_CACHE) >= _L1_MAX_SIZE:
            evict_count = max(1, _L1_MAX_SIZE // 10)
            for _ in range(evict_count):
                if _L1_CACHE:
                    _L1_CACHE.popitem(last=False)

    _L1_CACHE[key] = (data, time.time() + (ttl or _L1_TTL_SECONDS))
    _L1_CACHE.move_to_end(key)


async def _l2_get(key: str) -> dict[str, Any] | None:
    global _l2_hits, _l2_misses
    try:
        from app.database.connection import get_redis

        redis = await get_redis()
        if redis is None:
            _l2_misses += 1
            return None
        raw = await redis.get(f"{_L2_KEY_PREFIX}{key}")
        if raw is None:
            _l2_misses += 1
            return None
        _l2_hits += 1
        return json.loads(raw)
    except Exception as e:
        logger.debug("L2 get failed, degrading to L1: %s", e)
        _l2_misses += 1
        return None


async def _l2_set(key: str, data: dict[str, Any], ttl: int | None = None) -> None:
    try:
        from app.database.connection import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.setex(f"{_L2_KEY_PREFIX}{key}", ttl or _L2_TTL_SECONDS, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.debug("L2 set failed, skipping: %s", e)


async def get(key: str) -> dict[str, Any] | None:
    result = _l1_get(key)
    if result is not None:
        if _is_miss(result):
            return None
        return result

    result = await _l2_get(key)
    if result is not None:
        if _is_miss(result):
            _l1_set(key, result, ttl=_MISS_TTL_SECONDS)
            return None
        _l1_set(key, result)
        return result

    return None


async def set(key: str, data: dict[str, Any]) -> None:
    _l1_set(key, data)
    await _l2_set(key, data)


async def set_miss(key: str) -> None:
    _l1_set(key, _MISS_MARKER, ttl=_MISS_TTL_SECONDS)
    await _l2_set(key, _MISS_MARKER, ttl=_MISS_TTL_SECONDS)


def stats() -> dict[str, int]:
    return {
        "l1_size": len(_L1_CACHE),
        "l1_max_size": _L1_MAX_SIZE,
        "l1_hits": _cache_hits,
        "l1_misses": _cache_misses,
        "l2_hits": _l2_hits,
        "l2_misses": _l2_misses,
    }


async def invalidate(key: str) -> None:
    _L1_CACHE.pop(key, None)
    try:
        from app.database.connection import get_redis

        redis = await get_redis()
        if redis is not None:
            await redis.delete(f"{_L2_KEY_PREFIX}{key}")
    except Exception:
        pass


async def invalidate_all() -> None:
    global _cache_hits, _cache_misses, _l2_hits, _l2_misses
    _L1_CACHE.clear()
    _cache_hits = 0
    _cache_misses = 0
    _l2_hits = 0
    _l2_misses = 0
    try:
        from app.database.connection import get_redis

        redis = await get_redis()
        if redis is not None:
            async for key in redis.scan_iter(f"{_L2_KEY_PREFIX}*"):
                await redis.delete(key)
    except Exception:
        pass
