from __future__ import annotations

import os
from typing import Literal

from claread_eval.adapter.protocol import ArticleAnalysisAdapterClient

AdapterKind = Literal["fake", "in_process", "http"]


def create_adapter_client(
    kind: AdapterKind,
    *,
    fake_latency_seconds: float = 0.0,
) -> ArticleAnalysisAdapterClient:
    if kind == "fake":
        from claread_eval.adapter.fake_client import FakeArticleAnalysisAdapterClient

        return FakeArticleAnalysisAdapterClient(latency_seconds=fake_latency_seconds)
    if kind == "in_process":
        from claread_eval.adapter.in_process_client import InProcessArticleAnalysisAdapterClient

        return InProcessArticleAnalysisAdapterClient()
    if kind == "http":
        from claread_eval.adapter.http_client import HttpArticleAnalysisAdapterClient

        return HttpArticleAnalysisAdapterClient(
            base_url=os.environ.get("CLAREAD_API_BASE_URL", "http://127.0.0.1:8000"),
            admin_key=os.environ.get("CLAREAD_API_ADMIN_KEY", ""),
        )
    raise ValueError(f"Unsupported adapter kind: {kind}")
