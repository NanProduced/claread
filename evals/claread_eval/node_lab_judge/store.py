from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


@dataclass
class NodeLabJudgeRequest:
    judge_request_id: str
    trial_id: str
    session_id: str
    node_name: str
    status: str = "queued"
    judge_config_snapshot_json: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_json: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any) -> NodeLabJudgeRequest:
        data = dict(row)
        config = data.get("judge_config_snapshot_json") or {}
        if isinstance(config, str):
            config = json.loads(config)
        error_json = data.get("error_json")
        if isinstance(error_json, str):
            error_json = json.loads(error_json)
        return cls(
            judge_request_id=str(data["judge_request_id"]),
            trial_id=str(data["trial_id"]),
            session_id=str(data["session_id"]),
            node_name=str(data["node_name"]),
            status=str(data.get("status") or "queued"),
            judge_config_snapshot_json=config if isinstance(config, dict) else {},
            artifact_path=data.get("artifact_path"),
            lease_owner=data.get("lease_owner"),
            lease_until=data.get("lease_until"),
            heartbeat_at=data.get("heartbeat_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error_json=error_json if isinstance(error_json, dict) else None,
        )


class NodeLabJudgeRequestStore(Protocol):
    async def claim_next_request(self, *, worker_id: str, lease_seconds: int) -> NodeLabJudgeRequest | None:
        ...

    async def claim_request(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> NodeLabJudgeRequest | None:
        ...

    async def touch_heartbeat(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        ...

    async def mark_succeeded(self, *, judge_request_id: str, worker_id: str, artifact_path: str) -> bool:
        ...

    async def mark_failed(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
        artifact_path: str | None = None,
    ) -> bool:
        ...

    async def recover_stale_requests(self, *, evals_root: Path) -> list[NodeLabJudgeRequest]:
        ...

    async def close(self) -> None:
        ...


class AsyncpgNodeLabJudgeRequestStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> AsyncpgNodeLabJudgeRequestStore:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("asyncpg is required for Node Lab Judge worker.") from exc
        pool = await asyncpg.create_pool(database_url)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def claim_next_request(self, *, worker_id: str, lease_seconds: int) -> NodeLabJudgeRequest | None:
        row = await self._pool.fetchrow(
            """
            WITH next AS (
              SELECT judge_request_id
              FROM eval_node_lab_judge_requests
              WHERE status = 'queued'
              ORDER BY date_created ASC
              LIMIT 1
              FOR UPDATE SKIP LOCKED
            )
            UPDATE eval_node_lab_judge_requests r
            SET status = 'running',
                lease_owner = $1,
                lease_until = now() + ($2 * interval '1 second'),
                heartbeat_at = now(),
                started_at = COALESCE(started_at, now()),
                date_updated = now(),
                error_json = NULL
            FROM next
            WHERE r.judge_request_id = next.judge_request_id
            RETURNING r.*
            """,
            worker_id,
            lease_seconds,
        )
        return NodeLabJudgeRequest.from_row(row) if row else None

    async def claim_request(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> NodeLabJudgeRequest | None:
        row = await self._pool.fetchrow(
            """
            UPDATE eval_node_lab_judge_requests
            SET status = 'running',
                lease_owner = $2,
                lease_until = now() + ($3 * interval '1 second'),
                heartbeat_at = now(),
                started_at = COALESCE(started_at, now()),
                date_updated = now(),
                error_json = NULL
            WHERE judge_request_id = $1
              AND status = 'queued'
            RETURNING *
            """,
            judge_request_id,
            worker_id,
            lease_seconds,
        )
        return NodeLabJudgeRequest.from_row(row) if row else None

    async def touch_heartbeat(self, *, judge_request_id: str, worker_id: str, lease_seconds: int) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_node_lab_judge_requests
            SET lease_until = now() + ($3 * interval '1 second'),
                heartbeat_at = now(),
                date_updated = now()
            WHERE judge_request_id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            judge_request_id,
            worker_id,
            lease_seconds,
        )
        return _updated_one(result)

    async def mark_succeeded(self, *, judge_request_id: str, worker_id: str, artifact_path: str) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_node_lab_judge_requests
            SET status = 'succeeded',
                artifact_path = $3,
                finished_at = now(),
                date_updated = now(),
                error_json = NULL
            WHERE judge_request_id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            judge_request_id,
            worker_id,
            artifact_path,
        )
        return _updated_one(result)

    async def mark_failed(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
        artifact_path: str | None = None,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE eval_node_lab_judge_requests
            SET status = 'failed',
                artifact_path = COALESCE($4, artifact_path),
                finished_at = now(),
                date_updated = now(),
                error_json = $3::jsonb
            WHERE judge_request_id = $1
              AND lease_owner = $2
              AND status = 'running'
            """,
            judge_request_id,
            worker_id,
            json.dumps(error_json),
            artifact_path,
        )
        return _updated_one(result)

    async def recover_stale_requests(self, *, evals_root: Path) -> list[NodeLabJudgeRequest]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM eval_node_lab_judge_requests
            WHERE status = 'running'
              AND (lease_until IS NULL OR lease_until < now())
            ORDER BY date_updated ASC
            FOR UPDATE SKIP LOCKED
            """
        )
        recovered: list[NodeLabJudgeRequest] = []
        for row in rows:
            request = NodeLabJudgeRequest.from_row(row)
            if _artifact_complete(evals_root, request.session_id, request.trial_id, request.judge_request_id):
                result = await self._pool.execute(
                    """
                    UPDATE eval_node_lab_judge_requests
                    SET status = 'succeeded',
                        artifact_path = $2,
                        finished_at = COALESCE(finished_at, now()),
                        date_updated = now(),
                        error_json = NULL
                    WHERE judge_request_id = $1
                      AND status = 'running'
                      AND (lease_until IS NULL OR lease_until < now())
                    """,
                    request.judge_request_id,
                    _artifact_path(request.session_id, request.trial_id, request.judge_request_id),
                )
            else:
                result = await self._pool.execute(
                    """
                    UPDATE eval_node_lab_judge_requests
                    SET status = 'failed',
                        finished_at = COALESCE(finished_at, now()),
                        date_updated = now(),
                        error_json = $2::jsonb
                    WHERE judge_request_id = $1
                      AND status = 'running'
                      AND (lease_until IS NULL OR lease_until < now())
                    """,
                    request.judge_request_id,
                    json.dumps(
                        {
                            "code": "StaleNodeLabJudgeRequest",
                            "message": "Stale running node lab judge request has no complete artifact.",
                        }
                    ),
                )
            if _updated_one(result):
                recovered.append(request)
        return recovered


class InMemoryNodeLabJudgeRequestStore:
    def __init__(self, requests: list[NodeLabJudgeRequest] | None = None) -> None:
        self.requests: list[NodeLabJudgeRequest] = requests or []

    def add_request(
        self,
        *,
        judge_request_id: str,
        trial_id: str,
        session_id: str,
        node_name: str,
        judge_config_snapshot_json: dict[str, Any],
        status: str = "queued",
    ) -> NodeLabJudgeRequest:
        request = NodeLabJudgeRequest(
            judge_request_id=judge_request_id,
            trial_id=trial_id,
            session_id=session_id,
            node_name=node_name,
            judge_config_snapshot_json=judge_config_snapshot_json,
            status=status,
        )
        self.requests.append(request)
        return request

    async def close(self) -> None:
        return None

    async def claim_next_request(self, *, worker_id: str, lease_seconds: int) -> NodeLabJudgeRequest | None:
        now = datetime.now(UTC)
        for request in self.requests:
            if request.status != "queued":
                continue
            request.status = "running"
            request.lease_owner = worker_id
            request.lease_until = now + timedelta(seconds=lease_seconds)
            request.heartbeat_at = now
            request.started_at = request.started_at or now
            request.error_json = None
            return copy.deepcopy(request)
        return None

    async def claim_request(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> NodeLabJudgeRequest | None:
        now = datetime.now(UTC)
        request = self._find(judge_request_id)
        if request is None or request.status != "queued":
            return None
        request.status = "running"
        request.lease_owner = worker_id
        request.lease_until = now + timedelta(seconds=lease_seconds)
        request.heartbeat_at = now
        request.started_at = request.started_at or now
        request.error_json = None
        return copy.deepcopy(request)

    async def touch_heartbeat(self, *, judge_request_id: str, worker_id: str, lease_seconds: int) -> bool:
        request = self._find(judge_request_id)
        if request is None or request.status != "running" or request.lease_owner != worker_id:
            return False
        now = datetime.now(UTC)
        request.heartbeat_at = now
        request.lease_until = now + timedelta(seconds=lease_seconds)
        return True

    async def mark_succeeded(self, *, judge_request_id: str, worker_id: str, artifact_path: str) -> bool:
        request = self._running_owned(judge_request_id, worker_id)
        if request is None:
            return False
        request.status = "succeeded"
        request.artifact_path = artifact_path
        request.finished_at = datetime.now(UTC)
        request.error_json = None
        return True

    async def mark_failed(
        self,
        *,
        judge_request_id: str,
        worker_id: str,
        error_json: dict[str, Any],
        artifact_path: str | None = None,
    ) -> bool:
        request = self._running_owned(judge_request_id, worker_id)
        if request is None:
            return False
        request.status = "failed"
        if artifact_path:
            request.artifact_path = artifact_path
        request.finished_at = datetime.now(UTC)
        request.error_json = copy.deepcopy(error_json)
        return True

    async def recover_stale_requests(self, *, evals_root: Path) -> list[NodeLabJudgeRequest]:
        now = datetime.now(UTC)
        recovered: list[NodeLabJudgeRequest] = []
        for request in self.requests:
            if request.status != "running":
                continue
            if request.lease_until is not None and request.lease_until >= now:
                continue
            if _artifact_complete(evals_root, request.session_id, request.trial_id, request.judge_request_id):
                request.status = "succeeded"
                request.artifact_path = _artifact_path(request.session_id, request.trial_id, request.judge_request_id)
                request.error_json = None
            else:
                request.status = "failed"
                request.error_json = {
                    "code": "StaleNodeLabJudgeRequest",
                    "message": "Stale running node lab judge request has no complete artifact.",
                }
            request.finished_at = request.finished_at or now
            recovered.append(copy.deepcopy(request))
        return recovered

    def _find(self, judge_request_id: str) -> NodeLabJudgeRequest | None:
        return next((item for item in self.requests if item.judge_request_id == judge_request_id), None)

    def _running_owned(self, judge_request_id: str, worker_id: str) -> NodeLabJudgeRequest | None:
        request = self._find(judge_request_id)
        if request is None or request.status != "running" or request.lease_owner != worker_id:
            return None
        return request


def _artifact_complete(evals_root: Path, session_id: str, trial_id: str, judge_request_id: str) -> bool:
    judge_dir = (
        Path(evals_root)
        / "node-lab"
        / "sessions"
        / session_id
        / "trials"
        / trial_id
        / "judge"
        / judge_request_id
    )
    return (judge_dir / "judge-run.json").is_file() and (judge_dir / "result.json").is_file()


def _artifact_path(session_id: str, trial_id: str, judge_request_id: str) -> str:
    return f"evals/node-lab/sessions/{session_id}/trials/{trial_id}/judge/{judge_request_id}/result.json"


def _updated_one(result: str) -> bool:
    return result.split()[-1] == "1"
