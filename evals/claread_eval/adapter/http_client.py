from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.run import EvalRunConfig


class HttpArticleAnalysisAdapterClient:
    def __init__(
        self,
        *,
        base_url: str,
        admin_key: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not admin_key:
            raise RuntimeError("CLAREAD_API_ADMIN_KEY is required for adapter_kind=http")
        self._base_url = base_url.rstrip("/")
        self._admin_key = admin_key
        self._timeout_seconds = timeout_seconds

    async def analyze(self, case: EvalCase, run_config: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._analyze_sync, case, run_config)

    def _analyze_sync(self, case: EvalCase, run_config: dict[str, Any]) -> dict[str, Any]:
        config = EvalRunConfig.model_validate(run_config)
        payload = {
            "case_id": case.id,
            "run_id": config.run_id,
            "text": case.text,
            "reading_goal": case.reading_goal,
            "reading_variant": case.reading_variant,
            "source_type": case.source_type,
            "extended": case.extended,
            "model_selection": config.model_selection or None,
            "rag_mode": config.rag_mode,
            "prompt_variant_id": config.prompt_variant_id,
            "prompt_override": run_config.get("prompt_override"),
            "trace_scope": config.trace_scope,
            "trace_project": config.trace_project,
            "timeout_seconds": config.timeout_seconds,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self._base_url}/eval/article-analysis/workflow",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-admin-api-key": self._admin_key,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP eval adapter failed: {exc.code} {detail}") from exc
