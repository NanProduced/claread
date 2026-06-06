from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from claread_eval.schemas.dataset import EvalCase


@runtime_checkable
class ArticleAnalysisAdapterClient(Protocol):
    async def analyze(self, case: EvalCase, run_config: dict[str, Any]) -> dict[str, Any]:
        ...
