from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config.settings import get_settings
from app.database.connection import is_db_ready, is_redis_ready
from app.schemas.health import (
    DbHealthResponse,
    DictCacheStats,
    HealthCheckResponse,
    ReadinessCheckResponse,
)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthCheckResponse, summary="健康检查")
async def health_check(request: Request) -> HealthCheckResponse:
    """检查应用、数据库和 Redis 的运行状态。

    Reader workers are independent processes; the API must not own or
    require their lifecycle state.
    """
    settings = get_settings()
    db_ready = await is_db_ready()
    redis_ready = await is_redis_ready()

    zilliz_ready: bool | None = None
    if settings.grammar_rag_enabled:
        from app.infra.zilliz_client import is_zilliz_ready as _is_zilliz_ready
        zilliz_ready = await _is_zilliz_ready()

    return {
        "status": "ok" if db_ready else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "postgres": db_ready,
        "redis": redis_ready,
        "dict_cache": _get_dict_cache_stats(),
        "zilliz": zilliz_ready,
    }


@router.get("/db", response_model=DbHealthResponse, summary="数据库健康检查")
async def db_health() -> DbHealthResponse:
    """检查 PostgreSQL 连接是否正常。"""
    db_ready = await is_db_ready()
    return {
        "status": "ok" if db_ready else "unavailable",
        "postgres": db_ready,
    }


@router.get("/ready", response_model=ReadinessCheckResponse, summary="就绪探针")
async def readiness_check(request: Request) -> ReadinessCheckResponse:
    """就绪探针，数据库健康时返回 200，否则 503。"""
    db_ready = await is_db_ready()

    if not db_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "postgres": db_ready,
            },
        )

    return {
        "status": "ok",
        "postgres": db_ready,
    }


def _get_dict_cache_stats() -> DictCacheStats | None:
    try:
        from app.services.dictionary.cache import stats
        return DictCacheStats(**stats())
    except Exception:
        return None
