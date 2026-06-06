from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from claread_eval.security import redact_sensitive_text, validate_https_or_local_url


class NodeLabJudgeExecuteClient(Protocol):
    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class HttpNodeLabJudgeExecuteClient:
    base_url: str
    admin_key: str
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        self.base_url = validate_https_or_local_url(
            self.base_url,
            setting_name="CLAREAD_API_BASE_URL",
        )
        if not self.admin_key:
            raise RuntimeError("CLAREAD_API_ADMIN_KEY or CLAREAD_EVAL_JUDGE_API_KEY is required.")

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._execute_sync, payload)

    def _execute_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}/eval/article-analysis/node-lab/judge-execute",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-admin-api-key": self.admin_key,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = redact_sensitive_text(exc.read().decode("utf-8", errors="replace"))
            raise RuntimeError(f"Node Lab judge execute failed: {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Node Lab judge execute request failed: {exc.reason}") from exc


def build_default_execute_client() -> HttpNodeLabJudgeExecuteClient:
    base_url = os.environ.get("CLAREAD_API_BASE_URL") or os.environ.get("CLAREAD_SERVER_BASE_URL") or "http://127.0.0.1:8000"
    admin_key = os.environ.get("CLAREAD_API_ADMIN_KEY") or os.environ.get("CLAREAD_EVAL_JUDGE_API_KEY") or ""
    timeout_raw = os.environ.get("CLAREAD_EVAL_JUDGE_TIMEOUT_SECONDS", "120").strip()
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 120.0
    return HttpNodeLabJudgeExecuteClient(
        base_url=base_url,
        admin_key=admin_key,
        timeout_seconds=timeout_seconds if timeout_seconds > 0 else 120.0,
    )
