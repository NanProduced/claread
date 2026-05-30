from __future__ import annotations

from typing import Literal

from claread_eval.adapter.protocol import ArticleAnalysisAdapterClient

AdapterKind = Literal["fake", "in_process"]


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
    raise ValueError(f"Unsupported adapter kind: {kind}")
