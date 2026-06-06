from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

from claread_eval.adapter.http_client import HttpArticleAnalysisAdapterClient
from claread_eval.schemas.dataset import EvalCase


def test_http_adapter_requires_https_for_non_local_base_url() -> None:
    with pytest.raises(RuntimeError, match="must use https"):
        HttpArticleAnalysisAdapterClient(
            base_url="http://api.example.com",
            admin_key="eval-key",
        )


def test_http_adapter_allows_local_http_base_url() -> None:
    client = HttpArticleAnalysisAdapterClient(
        base_url="http://127.0.0.1:8000/",
        admin_key="eval-key",
    )

    assert client._base_url == "http://127.0.0.1:8000"


def test_http_adapter_redacts_upstream_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_request, timeout):
        del timeout
        raise HTTPError(
            url="https://api.example.com/eval/article-analysis/workflow",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=BytesIO(b'{"api_key":"secret-value","detail":"failed"}'),
        )

    monkeypatch.setattr("claread_eval.adapter.http_client.urlopen", fake_urlopen)
    client = HttpArticleAnalysisAdapterClient(
        base_url="https://api.example.com",
        admin_key="eval-key",
    )

    with pytest.raises(RuntimeError) as exc_info:
        client._analyze_sync(
            EvalCase(
                id="case-001",
                text="Sentence one.",
                reading_goal="daily_reading",
                reading_variant="intermediate_reading",
            ),
            {
                "run_id": "run-001",
                "dataset_id": "article-analysis-v1",
                "adapter_kind": "http",
            },
        )

    message = str(exc_info.value)
    assert "secret-value" not in message
    assert "<redacted>" in message
