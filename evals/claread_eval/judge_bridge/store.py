from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class JudgeRunRequest:
    id: str
    judge_run_id: str
    run_id: str
    rubric_id: str
    rubric_version: str
    status: str = "queued"
    judge_adapter_kind: str = "fake"
    config_json: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_json: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any) -> JudgeRunRequest:
        data = dict(row)
        config_json = data.get("config_json") or {}
        if isinstance(config_json, str):
            config_json = json.loads(config_json)
        error_json = data.get("error_json")
        if isinstance(error_json, str):
            error_json = json.loads(error_json)
        return cls(
            id=str(data["id"]),
            judge_run_id=str(data["judge_run_id"]),
            run_id=str(data["run_id"]),
            rubric_id=str(data["rubric_id"]),
            rubric_version=str(data.get("rubric_version") or ""),
            status=str(data.get("status") or "queued"),
            judge_adapter_kind=str(data.get("judge_adapter_kind") or "fake"),
            config_json=config_json if isinstance(config_json, dict) else {},
            artifact_path=data.get("artifact_path"),
            lease_owner=data.get("lease_owner"),
            lease_until=data.get("lease_until"),
            heartbeat_at=data.get("heartbeat_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error_json=error_json if isinstance(error_json, dict) else None,
        )


class JudgeRunRequestStore(Protocol):
    async def peek_next_request(self) -> JudgeRunRequest | None:
        ...

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> JudgeRunRequest | None:
        ...

    async def touch_heartbeat(
        self,
        *,
        request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        ...

    async def mark_succeeded(
        self,
        *,
        request_id: str,
        worker_id: str,
        artifact_path: str,
    ) -> bool:
        ...

    async def mark_failed(
        self,
        *,
        request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
    ) -> bool:
        ...

    async def recover_stale_requests(self, *, evals_root: Path) -> list[JudgeRunRequest]:
        ...

    async def close(self) -> None:
        ...


class AsyncpgJudgeRunRequestStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> AsyncpgJudgeRunRequestStore:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is required for the Eval Judge Worker Postgres store. "
                "Install evals dependencies before running the worker."
            ) from exc
        pool = await asyncpg.create_pool(database_url)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def peek_next_request(self) -> JudgeRunRequest | None:
        row = await self._pool.fetchrow(
            """
            SELECT *
            FROM eval_judge_run_requests
            WHERE status = 'queued'
            ORDER BY date_created ASC
            LIMIT 1
            """
        )
        return JudgeRunRequest.from_row(row) if row else None

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> JudgeRunRequest | None:
        row = await self._pool.fetchrow(
            """
            WITH next AS (
              SELECT id
              FROM eval_judge_run_requests
              WHERE status = 'queued'
              ORDER BY date_created ASC
              LIMIT 1
              FOR UPDATE SKIP LOCKED
            )
            UPDATE eval_judge_run_requests r
            SET status = 'running',
                lease_owner = $1,
                lease_until = now() + ($2 * interval '1 second'),
                heartbeat_at = now(),
                started_at = COALESCE(started_at, now()),
                date_updated = now(),
                error_json = NULL
            FROM next
            WHERE r.id = next.id
            RETURNING r.*
            """,
            worker_id,
            lease_seconds,
        )
        return JudgeRunRequest.from_row(row) if row else None

    async def touch_heartbeat(
        self,
        *,
        request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_judge_run_requests
            SET lease_until = now() + ($3 * interval '1 second'),
                heartbeat_at = now(),
                date_updated = now()
            WHERE id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            request_id,
            worker_id,
            lease_seconds,
        )
        return _updated_one(result)

    async def mark_succeeded(
        self,
        *,
        request_id: str,
        worker_id: str,
        artifact_path: str,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_judge_run_requests
            SET status = 'succeeded',
                artifact_path = $3,
                finished_at = now(),
                date_updated = now(),
                error_json = NULL
            WHERE id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            request_id,
            worker_id,
            artifact_path,
        )
        return _updated_one(result)

    async def mark_failed(
        self,
        *,
        request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_judge_run_requests
            SET status = 'failed',
                finished_at = now(),
                date_updated = now(),
                error_json = $3::jsonb
            WHERE id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            request_id,
            worker_id,
            json.dumps(error_json),
        )
        return _updated_one(result)

    async def recover_stale_requests(self, *, evals_root: Path) -> list[JudgeRunRequest]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM eval_judge_run_requests
            WHERE status = 'running'
              AND (lease_until IS NULL OR lease_until < now())
            ORDER BY date_updated ASC
            FOR UPDATE SKIP LOCKED
            """
        )
        recovered: list[JudgeRunRequest] = []
        for row in rows:
            request = JudgeRunRequest.from_row(row)
            if _artifact_complete(evals_root, request.run_id, request.judge_run_id):
                result = await self._pool.execute(
                    """
                    UPDATE eval_judge_run_requests
                    SET status = 'succeeded',
                        artifact_path = $2,
                        finished_at = COALESCE(finished_at, now()),
                        date_updated = now(),
                        error_json = NULL
                    WHERE id = $1
                      AND status = 'running'
                      AND (lease_until IS NULL OR lease_until < now())
                    """,
                    request.id,
                    _artifact_path(request.run_id, request.judge_run_id),
                )
            else:
                result = await self._pool.execute(
                    """
                    UPDATE eval_judge_run_requests
                    SET status = 'failed',
                        finished_at = COALESCE(finished_at, now()),
                        date_updated = now(),
                        error_json = $2::jsonb
                    WHERE id = $1
                      AND status = 'running'
                      AND (lease_until IS NULL OR lease_until < now())
                    """,
                    request.id,
                    json.dumps(
                        _safe_error(
                            "StaleJudgeRequest",
                            "Stale running judge request has no complete artifact.",
                        )
                    ),
                )
            if _updated_one(result):
                recovered.append(request)
        return recovered


class InMemoryJudgeRunRequestStore:
    def __init__(self, requests: list[JudgeRunRequest] | None = None) -> None:
        self.requests: list[JudgeRunRequest] = requests or []

    def add_request(
        self,
        *,
        judge_run_id: str,
        run_id: str,
        rubric_id: str,
        rubric_version: str = "v1",
        judge_adapter_kind: str = "fake",
        config_json: dict[str, Any] | None = None,
        status: str = "queued",
    ) -> JudgeRunRequest:
        request = JudgeRunRequest(
            id=str(uuid4()),
            judge_run_id=judge_run_id,
            run_id=run_id,
            rubric_id=rubric_id,
            rubric_version=rubric_version,
            status=status,
            judge_adapter_kind=judge_adapter_kind,
            config_json=config_json or {},
        )
        self.requests.append(request)
        return request

    async def close(self) -> None:
        return None

    async def peek_next_request(self) -> JudgeRunRequest | None:
        for request in self.requests:
            if _claimable(request):
                return copy.deepcopy(request)
        return None

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> JudgeRunRequest | None:
        now = datetime.now(UTC)
        for request in self.requests:
            if not _claimable(request):
                continue
            request.status = "running"
            request.lease_owner = worker_id
            request.lease_until = now + timedelta(seconds=lease_seconds)
            request.heartbeat_at = now
            request.started_at = request.started_at or now
            request.error_json = None
            return copy.deepcopy(request)
        return None

    async def touch_heartbeat(
        self,
        *,
        request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        request = self._find(request_id)
        if not request or request.status != "running" or request.lease_owner != worker_id:
            return False
        now = datetime.now(UTC)
        request.heartbeat_at = now
        request.lease_until = now + timedelta(seconds=lease_seconds)
        return True

    async def mark_succeeded(
        self,
        *,
        request_id: str,
        worker_id: str,
        artifact_path: str,
    ) -> bool:
        request = self._running_owned(request_id, worker_id)
        if not request:
            return False
        request.status = "succeeded"
        request.artifact_path = artifact_path
        request.finished_at = datetime.now(UTC)
        request.error_json = None
        return True

    async def mark_failed(
        self,
        *,
        request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
    ) -> bool:
        request = self._running_owned(request_id, worker_id)
        if not request:
            return False
        request.status = "failed"
        request.finished_at = datetime.now(UTC)
        request.error_json = copy.deepcopy(error_json)
        return True

    async def recover_stale_requests(self, *, evals_root: Path) -> list[JudgeRunRequest]:
        now = datetime.now(UTC)
        recovered: list[JudgeRunRequest] = []
        for request in self.requests:
            if request.status != "running":
                continue
            if request.lease_until is not None and request.lease_until >= now:
                continue
            if _artifact_complete(evals_root, request.run_id, request.judge_run_id):
                request.status = "succeeded"
                request.artifact_path = _artifact_path(request.run_id, request.judge_run_id)
                request.error_json = None
            else:
                request.status = "failed"
                request.error_json = _safe_error(
                    "StaleJudgeRequest",
                    "Stale running judge request has no complete artifact.",
                )
            request.finished_at = request.finished_at or now
            recovered.append(copy.deepcopy(request))
        return recovered

    def _find(self, request_id: str) -> JudgeRunRequest | None:
        return next((request for request in self.requests if request.id == request_id), None)

    def _running_owned(self, request_id: str, worker_id: str) -> JudgeRunRequest | None:
        request = self._find(request_id)
        if not request or request.status != "running" or request.lease_owner != worker_id:
            return None
        return request


def _updated_one(result: str) -> bool:
    return result.endswith(" 1")


def _claimable(request: JudgeRunRequest) -> bool:
    return request.status == "queued"


def _artifact_complete(evals_root: Path, run_id: str, judge_run_id: str) -> bool:
    artifact_dir = evals_root / "runs" / run_id / "judge" / judge_run_id
    return (artifact_dir / "report.json").is_file() and (
        artifact_dir / "case-results.json"
    ).is_file()


def _artifact_path(run_id: str, judge_run_id: str) -> str:
    return f"evals/runs/{run_id}/judge/{judge_run_id}"


def _safe_error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message[:500]}
