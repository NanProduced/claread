from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from claread_eval.node_lab_judge.runner import NodeLabJudgeRunConfig, run_node_lab_judge
from claread_eval.node_lab_judge.store import (
    AsyncpgNodeLabJudgeRequestStore,
    NodeLabJudgeRequest,
    NodeLabJudgeRequestStore,
)


@dataclass
class NodeLabJudgeWorker:
    store: NodeLabJudgeRequestStore
    evals_root: Path
    worker_id: str
    poll_interval: float = 5.0
    lease_seconds: int = 300
    heartbeat_interval: float = 30.0

    def __post_init__(self) -> None:
        self.evals_root = Path(self.evals_root).resolve()

    async def recover_stale_requests(self) -> list[NodeLabJudgeRequest]:
        return await self.store.recover_stale_requests(evals_root=self.evals_root)

    async def run_forever(self) -> None:
        await self.recover_stale_requests()
        while True:
            claimed = await self.run_once()
            if not claimed:
                await asyncio.sleep(self.poll_interval)

    async def run_once(self, *, judge_request_id: str | None = None) -> bool:
        if judge_request_id:
            request = await self.store.claim_request(
                judge_request_id=judge_request_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        else:
            request = await self.store.claim_next_request(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        if request is None:
            return False
        await self._execute_claimed_request(request)
        return True

    async def _execute_claimed_request(self, request: NodeLabJudgeRequest) -> None:
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                judge_request_id=request.judge_request_id,
                stop=stop_heartbeat,
                lease_lost=lease_lost,
            )
        )
        try:
            _, judge_dir = await run_node_lab_judge(
                NodeLabJudgeRunConfig(
                    judge_request_id=request.judge_request_id,
                    session_id=request.session_id,
                    trial_id=request.trial_id,
                    node_name=request.node_name,
                    judge_config_snapshot_json=request.judge_config_snapshot_json,
                    artifact_path=request.artifact_path,
                ),
                evals_root=self.evals_root,
            )
            if lease_lost.is_set():
                return
            await self.store.mark_succeeded(
                judge_request_id=request.judge_request_id,
                worker_id=self.worker_id,
                artifact_path=_artifact_path(judge_dir),
            )
        except Exception as exc:
            if lease_lost.is_set():
                return
            judge_dir = (
                self.evals_root
                / "node-lab"
                / "sessions"
                / request.session_id
                / "trials"
                / request.trial_id
                / "judge"
                / request.judge_request_id
            )
            artifact_path = _artifact_path(judge_dir) if (judge_dir / "result.json").is_file() else None
            await self.store.mark_failed(
                judge_request_id=request.judge_request_id,
                worker_id=self.worker_id,
                error_json=safe_error_json(exc),
                artifact_path=artifact_path,
            )
        finally:
            stop_heartbeat.set()
            await heartbeat_task

    async def _heartbeat_loop(
        self,
        *,
        judge_request_id: str,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval)
                return
            except TimeoutError:
                ok = await self.store.touch_heartbeat(
                    judge_request_id=judge_request_id,
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
    for key in ("CLAREAD_API_ADMIN_KEY", "CLAREAD_EVAL_JUDGE_API_KEY", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            message = message.replace(value, "<redacted>")
    return {"code": type(exc).__name__, "message": message[:500]}


def _artifact_path(judge_dir: Path) -> str:
    relative = judge_dir.as_posix().split("/node-lab/", 1)[-1]
    return f"evals/node-lab/{relative}/result.json".replace("//", "/")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run queued Claread Node Lab judge requests.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--evals-root", default=Path.cwd())
    parser.add_argument("--worker-id", default=f"node-lab-judge-worker-{uuid4()}")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--request-id", help="When used with --once, claim this queued request only.")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required.")
    store = await AsyncpgNodeLabJudgeRequestStore.connect(args.database_url)
    worker = NodeLabJudgeWorker(
        store=store,
        evals_root=Path(args.evals_root),
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
        heartbeat_interval=args.heartbeat_interval,
    )
    try:
        if args.once:
            await worker.recover_stale_requests()
            claimed = await worker.run_once(judge_request_id=args.request_id)
            return 0 if claimed else 1
        await worker.run_forever()
    finally:
        await store.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
