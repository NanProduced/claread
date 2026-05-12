"""Pipeline run tracker — persists execution progress to pipeline_runs table."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from app.database import connection as db_connection

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class PipelineRunTracker:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or f"pr_{uuid.uuid4().hex[:12]}"
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = db_connection.DB_POOL
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        return self._pool

    async def start(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (id, status, stage, started_at)
                VALUES ($1, 'running', 'init', NOW())
                """,
                self.run_id,
            )
        logger.info("Pipeline run %s started", self.run_id)

    async def update_stage(
        self,
        stage: str,
        detail: dict | None = None,
        candidates_found: int | None = None,
        candidates_extracted: int | None = None,
        candidates_scored: int | None = None,
        articles_generated: int | None = None,
    ) -> None:
        pool = await self._ensure_pool()
        sets: list[str] = ["stage = $2"]
        params: list[object] = [self.run_id, stage]
        idx = 3

        if detail is not None:
            sets.append(f"stage_detail = ${idx}")
            params.append(detail)
            idx += 1
        if candidates_found is not None:
            sets.append(f"candidates_found = ${idx}")
            params.append(candidates_found)
            idx += 1
        if candidates_extracted is not None:
            sets.append(f"candidates_extracted = ${idx}")
            params.append(candidates_extracted)
            idx += 1
        if candidates_scored is not None:
            sets.append(f"candidates_scored = ${idx}")
            params.append(candidates_scored)
            idx += 1
        if articles_generated is not None:
            sets.append(f"articles_generated = ${idx}")
            params.append(articles_generated)
            idx += 1

        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = $1",
                *params,
            )
        logger.info("Pipeline run %s → stage=%s", self.run_id, stage)

    async def add_error(self, stage: str, message: str) -> None:
        pool = await self._ensure_pool()
        error_entry = json.dumps([{"stage": stage, "message": message}], ensure_ascii=False)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pipeline_runs
                SET errors = errors || $2::jsonb
                WHERE id = $1
                """,
                self.run_id,
                error_entry,
            )
        logger.warning("Pipeline run %s error at %s: %s", self.run_id, stage, message)

    async def complete(self, articles_generated: int) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'completed', stage = 'done',
                    articles_generated = $2, finished_at = NOW()
                WHERE id = $1
                """,
                self.run_id,
                articles_generated,
            )
        logger.info("Pipeline run %s completed, %d articles generated", self.run_id, articles_generated)

    async def fail(self, stage: str, message: str) -> None:
        pool = await self._ensure_pool()
        error_entry = json.dumps([{"stage": stage, "message": message}], ensure_ascii=False)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed', stage = $2,
                    errors = errors || $3::jsonb, finished_at = NOW()
                WHERE id = $1
                """,
                self.run_id,
                stage,
                error_entry,
            )
        logger.error("Pipeline run %s failed at %s: %s", self.run_id, stage, message)
