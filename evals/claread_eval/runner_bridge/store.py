from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class WorkflowRunRequest:
    id: str
    run_id: str
    dataset_id: str
    status: str = "queued"
    mode: str = "workflow"
    eval_purpose: str = "dataset_regression"
    adapter_kind: str = "fake"
    runner_kind: str = "external_worker"
    config_json: dict[str, Any] = field(default_factory=dict)
    artifact_run_id: str | None = None
    artifact_path: str | None = None
    max_concurrency: int = 1
    lease_owner: str | None = None
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_json: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any) -> WorkflowRunRequest:
        data = dict(row)
        config_json = data.get("config_json") or {}
        if isinstance(config_json, str):
            config_json = json.loads(config_json)
        error_json = data.get("error_json")
        if isinstance(error_json, str):
            error_json = json.loads(error_json)
        return cls(
            id=str(data["id"]),
            run_id=str(data["run_id"]),
            dataset_id=str(data["dataset_id"]),
            status=str(data.get("status") or "queued"),
            mode=str(data.get("mode") or "workflow"),
            eval_purpose=str(data.get("eval_purpose") or "dataset_regression"),
            adapter_kind=str(data.get("adapter_kind") or "fake"),
            runner_kind=str(data.get("runner_kind") or "external_worker"),
            config_json=config_json if isinstance(config_json, dict) else {},
            artifact_run_id=data.get("artifact_run_id"),
            artifact_path=data.get("artifact_path"),
            max_concurrency=int(data.get("max_concurrency") or 1),
            lease_owner=data.get("lease_owner"),
            lease_until=data.get("lease_until"),
            heartbeat_at=data.get("heartbeat_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error_json=error_json if isinstance(error_json, dict) else None,
        )


class WorkflowRunRequestStore(Protocol):
    async def peek_next_request(self) -> WorkflowRunRequest | None:
        ...

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkflowRunRequest | None:
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
        artifact_run_id: str,
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

    async def recover_stale_requests(self, *, evals_root: Path) -> list[WorkflowRunRequest]:
        ...

    async def close(self) -> None:
        ...


class AsyncpgWorkflowRunRequestStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> AsyncpgWorkflowRunRequestStore:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is required for the Eval Runner Bridge Postgres store. "
                "Install evals dependencies before running the worker."
            ) from exc
        pool = await asyncpg.create_pool(database_url)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def peek_next_request(self) -> WorkflowRunRequest | None:
        row = await self._pool.fetchrow(
            """
            SELECT *
            FROM eval_workflow_run_requests
            WHERE status = 'queued'
              AND mode = 'workflow'
              AND runner_kind = 'external_worker'
            ORDER BY date_created ASC
            LIMIT 1
            """
        )
        return WorkflowRunRequest.from_row(row) if row else None

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkflowRunRequest | None:
        row = await self._pool.fetchrow(
            """
            WITH next AS (
              SELECT id
              FROM eval_workflow_run_requests
              WHERE status = 'queued'
                AND mode = 'workflow'
                AND runner_kind = 'external_worker'
              ORDER BY date_created ASC
              LIMIT 1
              FOR UPDATE SKIP LOCKED
            )
            UPDATE eval_workflow_run_requests r
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
        return WorkflowRunRequest.from_row(row) if row else None

    async def touch_heartbeat(
        self,
        *,
        request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_workflow_run_requests
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
        artifact_run_id: str,
        artifact_path: str,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_workflow_run_requests
            SET status = 'succeeded',
                artifact_run_id = $3,
                artifact_path = $4,
                finished_at = now(),
                date_updated = now(),
                error_json = NULL
            WHERE id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            request_id,
            worker_id,
            artifact_run_id,
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
            UPDATE eval_workflow_run_requests
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

    async def recover_stale_requests(self, *, evals_root: Path) -> list[WorkflowRunRequest]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM eval_workflow_run_requests
            WHERE status = 'running'
              AND (lease_until IS NULL OR lease_until < now())
            ORDER BY date_updated ASC
            FOR UPDATE SKIP LOCKED
            """
        )
        recovered: list[WorkflowRunRequest] = []
        for row in rows:
            request = WorkflowRunRequest.from_row(row)
            if _artifact_complete(evals_root, request.run_id):
                result = await self._pool.execute(
                    """
                    UPDATE eval_workflow_run_requests
                    SET status = 'succeeded',
                        artifact_run_id = $2,
                        artifact_path = $3,
                        finished_at = COALESCE(finished_at, now()),
                        date_updated = now(),
                        error_json = NULL
                    WHERE id = $1
                      AND status = 'running'
                      AND (lease_until IS NULL OR lease_until < now())
                    """,
                    request.id,
                    request.run_id,
                    _artifact_path(request.run_id),
                )
            else:
                result = await self._pool.execute(
                    """
                    UPDATE eval_workflow_run_requests
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
                            "StaleRequest",
                            "Stale running request has no complete artifact.",
                        )
                    ),
                )
            if _updated_one(result):
                recovered.append(request)
        return recovered


class InMemoryWorkflowRunRequestStore:
    def __init__(self, requests: list[WorkflowRunRequest] | None = None) -> None:
        self.requests: list[WorkflowRunRequest] = requests or []

    def add_request(
        self,
        *,
        run_id: str,
        dataset_id: str,
        adapter_kind: str = "fake",
        config_json: dict[str, Any] | None = None,
        status: str = "queued",
        max_concurrency: int = 1,
    ) -> WorkflowRunRequest:
        request = WorkflowRunRequest(
            id=str(uuid4()),
            run_id=run_id,
            dataset_id=dataset_id,
            status=status,
            adapter_kind=adapter_kind,
            config_json=config_json or {},
            max_concurrency=max_concurrency,
        )
        self.requests.append(request)
        return request

    async def close(self) -> None:
        return None

    async def peek_next_request(self) -> WorkflowRunRequest | None:
        for request in self.requests:
            if _claimable(request):
                return copy.deepcopy(request)
        return None

    async def claim_next_request(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkflowRunRequest | None:
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
        artifact_run_id: str,
        artifact_path: str,
    ) -> bool:
        request = self._running_owned(request_id, worker_id)
        if not request:
            return False
        request.status = "succeeded"
        request.artifact_run_id = artifact_run_id
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

    async def recover_stale_requests(self, *, evals_root: Path) -> list[WorkflowRunRequest]:
        now = datetime.now(UTC)
        recovered: list[WorkflowRunRequest] = []
        for request in self.requests:
            if request.status != "running":
                continue
            if request.lease_until is not None and request.lease_until >= now:
                continue
            if _artifact_complete(evals_root, request.run_id):
                request.status = "succeeded"
                request.artifact_run_id = request.run_id
                request.artifact_path = _artifact_path(request.run_id)
                request.error_json = None
            else:
                request.status = "failed"
                request.error_json = _safe_error(
                    "StaleRequest",
                    "Stale running request has no complete artifact.",
                )
            request.finished_at = request.finished_at or now
            recovered.append(copy.deepcopy(request))
        return recovered

    def _find(self, request_id: str) -> WorkflowRunRequest | None:
        return next((request for request in self.requests if request.id == request_id), None)

    def _running_owned(self, request_id: str, worker_id: str) -> WorkflowRunRequest | None:
        request = self._find(request_id)
        if not request or request.status != "running" or request.lease_owner != worker_id:
            return None
        return request


def _updated_one(result: str) -> bool:
    return result.endswith(" 1")


def _claimable(request: WorkflowRunRequest) -> bool:
    return (
        request.status == "queued"
        and request.mode == "workflow"
        and request.runner_kind == "external_worker"
    )


def _artifact_complete(evals_root: Path, run_id: str) -> bool:
    run_dir = evals_root / "runs" / run_id
    return (run_dir / "report.json").is_file() and (run_dir / "case-index.json").is_file()


def _artifact_path(run_id: str) -> str:
    return f"evals/runs/{run_id}"


def _safe_error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message[:500]}
