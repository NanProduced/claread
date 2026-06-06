from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from claread_eval.runner.entrypoint import run_from_config

from .materialize import materialize_runner_config
from .store import AsyncpgWorkflowRunRequestStore, WorkflowRunRequest, WorkflowRunRequestStore


class BridgeWorkerError(Exception):
    pass


@dataclass
class BridgeWorker:
    store: WorkflowRunRequestStore
    evals_root: Path
    worker_id: str
    poll_interval: float = 5.0
    lease_seconds: int = 300
    heartbeat_interval: float = 30.0

    def __post_init__(self) -> None:
        self.evals_root = Path(self.evals_root).resolve()
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive.")
        if self.poll_interval < 0:
            raise ValueError("poll_interval must be non-negative.")

    async def recover_stale_requests(self) -> list[WorkflowRunRequest]:
        return await self.store.recover_stale_requests(evals_root=self.evals_root)

    async def run_forever(self) -> None:
        await self.recover_stale_requests()
        while True:
            claimed = await self.run_once()
            if not claimed:
                await asyncio.sleep(self.poll_interval)

    async def run_once(self, *, dry_run_claim: bool = False) -> bool:
        if dry_run_claim:
            request = await self.store.peek_next_request()
            if request is None:
                print("No queued workflow eval request is available.")
                return False
            print(_request_preview(request))
            return True

        request = await self.store.claim_next_request(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if request is None:
            return False

        await self._execute_claimed_request(request)
        return True

    async def _execute_claimed_request(self, request: WorkflowRunRequest) -> None:
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                request_id=request.id,
                stop=stop_heartbeat,
                lease_lost=lease_lost,
            )
        )
        try:
            if request.max_concurrency != 1:
                raise BridgeWorkerError("Runner Bridge Worker v1 only supports max_concurrency=1.")

            runner_config = materialize_runner_config(request, evals_root=self.evals_root)
            report, run_dir = await run_from_config(runner_config)
            if lease_lost.is_set():
                return

            await self.store.mark_succeeded(
                request_id=request.id,
                worker_id=self.worker_id,
                artifact_run_id=report.run_id,
                artifact_path=_artifact_path(run_dir),
            )
        except Exception as exc:
            if lease_lost.is_set():
                return
            await self.store.mark_failed(
                request_id=request.id,
                worker_id=self.worker_id,
                error_json=safe_error_json(exc),
            )
        finally:
            stop_heartbeat.set()
            await heartbeat_task

    async def _heartbeat_loop(
        self,
        *,
        request_id: str,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval)
                return
            except TimeoutError:
                ok = await self.store.touch_heartbeat(
                    request_id=request_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if not ok:
                    lease_lost.set()
                    return
            except Exception:
                lease_lost.set()
                return


def safe_error_json(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    for key in ("CLAREAD_API_ADMIN_KEY", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            message = message.replace(value, "<redacted>")
    return {
        "code": type(exc).__name__,
        "message": message[:500],
    }


def _request_preview(request: WorkflowRunRequest) -> str:
    payload = {
        "id": request.id,
        "run_id": request.run_id,
        "dataset_id": request.dataset_id,
        "adapter_kind": request.adapter_kind,
        "status": request.status,
        "max_concurrency": request.max_concurrency,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _artifact_path(run_dir: Path) -> str:
    try:
        return (Path("evals") / "runs" / run_dir.name).as_posix()
    except Exception:
        return run_dir.as_posix()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run queued Claread workflow eval requests.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--evals-root", default=Path.cwd())
    parser.add_argument("--worker-id", default=f"eval-worker-{uuid4()}")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run-claim", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    parser.add_argument("--max-concurrency", type=int, default=1)
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.max_concurrency != 1:
        parser.error("Runner Bridge Worker v1 only supports --max-concurrency=1.")
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required.")

    store = await AsyncpgWorkflowRunRequestStore.connect(args.database_url)
    worker = BridgeWorker(
        store=store,
        evals_root=Path(args.evals_root),
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
        heartbeat_interval=args.heartbeat_interval,
    )
    try:
        if args.dry_run_claim:
            claimed = await worker.run_once(dry_run_claim=True)
            return 0 if claimed else 1
        if args.once:
            await worker.recover_stale_requests()
            claimed = await worker.run_once()
            return 0 if claimed else 1
        await worker.run_forever()
    finally:
        await store.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
