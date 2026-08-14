"""
应用程序入口模块。

负责创建和配置 FastAPI 应用实例，包括路由注册、生命周期管理等功能。
"""

import asyncio
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config.logging_config import setup_logging
from app.config.settings import Settings, get_settings
from app.services.dictionary.nlp import preload_dict_nlp

# 尽可能早地配置日志（在任何 logger 使用之前）
setup_logging()
from app.database.connection import (  # noqa: E402
    close_db,
    close_redis,
    init_db,
    init_redis,
)
from app.observability.langsmith import setup_langsmith  # noqa: E402

logger = getLogger(__name__)

_WARMUP_WORDS = [
    "the", "a", "an", "is", "are", "was", "were", "be", "have", "has",
    "do", "does", "will", "would", "can", "could", "may", "might",
    "should", "must", "not", "no", "but", "or", "and", "if",
    "that", "this", "which", "who", "what", "when", "where", "how",
    "from", "with", "for", "about", "into", "through", "between",
]


async def _warm_dict_cache() -> None:
    from app.services.dictionary import cache as dict_cache
    from app.services.dictionary.db_pg import fetch_entry, lookup_candidates_batch
    from app.services.dictionary.providers.tecd3 import Tecd3Provider

    provider = Tecd3Provider()
    candidates_list = await lookup_candidates_batch(_WARMUP_WORDS, source="tecd3")
    seen_ids: set[int] = set()
    for c in candidates_list:
        if c.entry_id not in seen_ids:
            seen_ids.add(c.entry_id)
            entry = await fetch_entry(c.entry_id, source="tecd3")
            if entry is not None:
                cache_key = f"tecd3:v4:entry:{c.entry_id}"
                result = provider._build_entry_result(entry.display_headword, entry)
                await dict_cache.set(cache_key, result)
    logger.info("Dict cache warmed: %d entries preloaded", len(seen_ids))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    应用生命周期管理上下文管理器。

    启动时：初始化 PostgreSQL 连接池（必选）、Redis 连接（可选）
    关闭时：清理所有连接池
    """
    settings = get_settings()

    # 1. 初始化 PostgreSQL（必选）
    try:
        await init_db(
            database_url=settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
        )
        logger.info("PostgreSQL pool initialized")
    except Exception as e:
        logger.error("Failed to initialize PostgreSQL pool: %s", e)
        raise

    # 2. 初始化 Redis（可选，第二阶段增强）
    await init_redis(redis_url=settings.redis_url, enabled=settings.redis_enabled)

    # 2.5 初始化 Zilliz（可选，Grammar RAG 依赖）
    if settings.grammar_rag_enabled:
        try:
            from app.infra.zilliz_client import init_zilliz, is_zilliz_ready

            await init_zilliz(uri=settings.zilliz_uri, token=settings.zilliz_token)
            ready = await is_zilliz_ready()
            if ready:
                logger.info("Zilliz connection established")
            else:
                logger.warning("Zilliz readiness check failed, RAG will fallback to baseline")
        except Exception as e:
            logger.warning("Zilliz initialization failed (non-blocking, RAG will fallback): %s", e)
    else:
        logger.info("Grammar RAG disabled, skipping Zilliz initialization")

    # 3. 初始化 LangSmith
    setup_langsmith(settings)

    # 3.1 预热词典专用 spaCy pipeline，避免 /dict 首次带上下文请求承担冷启动开销
    try:
        if await asyncio.to_thread(preload_dict_nlp):
            logger.info("Dictionary spaCy pipeline preloaded")
        else:
            logger.warning(
                "Dictionary spaCy pipeline unavailable, "
                "/dict will fall back when needed"
            )
    except Exception as e:
        logger.warning("Failed to preload dictionary spaCy pipeline: %s", e)

    # 3.2 预热词典缓存（高频词条）
    try:
        await _warm_dict_cache()
    except Exception as e:
        logger.warning("Dict cache warmup failed (non-blocking): %s", e)

    # Reconcile orphan streaming rows from a
    # previous process lifetime. If this process crashed/restarted while
    # a ``reader_ask_turn_runs`` row was still ``streaming``, that row is
    # now orphaned — no route ``finally`` will ever run for it. The
    # startup sweep converges all such rows to ``cancelled`` with a typed
    # ``stale_stream_reconciled`` terminal_reason. Safe to call on a fresh
    # DB (returns scanned=0). Errors are logged but non-fatal — the
    # periodic sweeper will retry on the next interval.
    try:
        from app.services.reader_record_ask.stale_run_recovery import (
            StaleStreamSweeper,
            run_startup_stale_stream_sweep,
        )

        startup_summary = await run_startup_stale_stream_sweep()
        if startup_summary.get("reconciled", 0) > 0:
            logger.info(
                "Startup stale-stream sweep reconciled %d orphan turn_run(s)",
                startup_summary["reconciled"],
            )
        # Start the in-process periodic sweeper. This is the safety net
        # for rows orphaned mid-lifetime (e.g. a worker thread crashes
        # without unwinding the route ``finally``). The sweeper only
        # converges rows whose heartbeat is dead — active turns with a
        # recent ``updated_at`` are never touched.
        stale_sweeper = StaleStreamSweeper()
        stale_sweeper.start()
        app.state.stale_stream_sweeper = stale_sweeper
        logger.info("Stale-stream periodic sweeper started")
    except Exception as e:  # noqa: BLE001
        # Non-fatal: orphan rows will linger until the next restart, but
        # the app must still come up. The startup sweep is retried on the
        # next deploy.
        logger.warning(
            "Stale-stream recovery init failed (non-blocking): %s",
            e,
        )

    yield

    # 关闭时清理
    if hasattr(app.state, 'stale_stream_sweeper'):
        stale_sweeper = app.state.stale_stream_sweeper
        await stale_sweeper.stop()
    from app.infra.zilliz_client import close_zilliz
    await close_zilliz()
    await close_redis()
    await close_db()
    logger.info("Application shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    创建并配置 FastAPI 应用实例。

    Args:
        settings: 可选的应用程序配置对象。如果未提供，则从环境变量加载默认配置。

    Returns:
        配置完成的 FastAPI 应用实例
    """
    active_settings = settings or get_settings()
    app = FastAPI(
        title=active_settings.app_name,
        lifespan=lifespan,
    )
    app.include_router(api_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- 全局异常处理器 ---
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        logger.error(
            "HTTP %d | path=%s | detail=%s",
            exc.status_code,
            request.url.path,
            exc.detail,
        )
        content: dict = {"detail": exc.detail}
        if hasattr(exc, "error_code"):
            content["error"] = exc.error_code  # type: ignore[attr-defined]
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        tb = traceback.format_exc()
        logger.error(
            "Unhandled exception: %s | path=%s\n%s",
            type(exc).__name__,
            request.url.path,
            tb,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "Internal server error"},
        )

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """
        根路径健康检查端点。

        返回应用名称和运行环境信息。
        """
        return {
            "message": f"{active_settings.app_name} is running.",
            "env": active_settings.app_env,
        }

    return app


# 创建应用实例，供 uvicorn 等服务器使用
app = create_app()
