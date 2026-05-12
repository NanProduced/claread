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
    """检查应用、数据库、Redis 和 Worker 的运行状态。"""
    settings = get_settings()
    db_ready = await is_db_ready()
    redis_ready = await is_redis_ready()
    worker_snapshot = _get_worker_snapshot(request)
    worker_ready = bool(worker_snapshot["healthy"])

    zilliz_ready: bool | None = None
    if settings.grammar_rag_enabled:
        from app.infra.zilliz_client import is_zilliz_ready as _is_zilliz_ready
        zilliz_ready = await _is_zilliz_ready()

    return {
        "status": "ok" if db_ready and worker_ready else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "postgres": db_ready,
        "redis": redis_ready,
        "worker": worker_ready,
        "worker_inflight_tasks": int(worker_snapshot["inflight_tasks"]),
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
    """就绪探针，数据库和 Worker 都健康时返回 200，否则 503。"""
    db_ready = await is_db_ready()
    worker_snapshot = _get_worker_snapshot(request)
    worker_ready = bool(worker_snapshot["healthy"])

    if not db_ready or not worker_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "postgres": db_ready,
                "worker": worker_ready,
                "worker_inflight_tasks": int(worker_snapshot["inflight_tasks"]),
            },
        )

    return {
        "status": "ok",
        "postgres": db_ready,
        "worker": worker_ready,
        "worker_inflight_tasks": int(worker_snapshot["inflight_tasks"]),
    }


def _get_worker_snapshot(request: Request) -> dict[str, bool | int | str]:
    worker = getattr(request.app.state, "analysis_task_worker", None)
    if worker is None:
        return {
            "healthy": False,
            "worker_token": "",
            "runner_running": False,
            "stopping": True,
            "inflight_tasks": 0,
        }
    return worker.health_snapshot()


def _get_dict_cache_stats() -> DictCacheStats | None:
    try:
        from app.services.dictionary.cache import stats
        return DictCacheStats(**stats())
    except Exception:
        return None
