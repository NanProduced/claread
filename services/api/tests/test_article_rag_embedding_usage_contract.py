"""OBS-01B-B: typed embedding usage carrier contract tests.

Covers (no ai_usage DB writes, no real provider calls):
- ``canonical_embedding_tokens``: text-embedding-v4专属 canonical token 映射。
- wrapper (``embed_texts_with_metadata``): 多批次成功聚合、partial failure
  保留已完成批次 usage、preflight failure 不伪造 usage。
- typed carrier: ``ArticleRagEmbeddingInvocationResult`` /
  ``ArticleRagEmbeddingUsageReport`` / ``ArticleRagEmbeddingBatchUsage`` 与
  索引专用窄协议 ``ArticleRagIndexEmbeddingProvider``。
- adapter: 成功 typed result、count/dimension mismatch 后置校验携带
  provider_succeeded=true 的完整 report、失败 report 安全脱敏。
- Fake/Unconfigured provider 的守恒字段（不伪造 usage、call_count 不重复）。
- 旧 ``embed_texts`` 调用面与 retrieval 行为不变。

All new-contract symbols are imported inside each test so the RED run
fails per-test on the missing symbol (missing contract), never on a
collection-time import error of existing modules.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.infra import bailian_embedding
from app.services.reader_orchestration.article_rag_embedding_provider import (
    DashScopeArticleRagEmbeddingProvider,
    DashScopeArticleRagEmbeddingProviderError,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    _MSG_VECTOR_COLLECTION_MISMATCH,
    UnconfiguredArticleRagEmbeddingProvider,
)

pytestmark = pytest.mark.anyio

_DIM = 1024
_MODEL = "text-embedding-v4"
_SENTINEL = "SECRET-SDK-DO-NOT-LOG"


# ---------------------------------------------------------------------------
# Fake DashScope SDK harness (monkeypatched wrapper internals)
# ---------------------------------------------------------------------------


class _SdkScript:
    """Scripted fake for ``dashscope.TextEmbedding``.

    ``script`` holds one action per outbound batch call:
    ("ok", total_tokens, request_id) | ("raise",) | ("status", code)
    """

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        self.script = script
        self.calls: list[list[str]] = []

    def call(self, **kwargs: Any) -> Any:
        inputs = kwargs["input"]
        self.calls.append(list(inputs))
        action = self.script[len(self.calls) - 1]
        kind = action[0]
        if kind == "ok":
            total = action[1]
            request_id = action[2] if len(action) > 2 else "emb-1"
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id=request_id,
                usage={"total_tokens": total},
                output={"embeddings": [{"embedding": [0.01] * _DIM} for _ in inputs]},
            )
        if kind == "raise":
            raise RuntimeError(f"transport boom {_SENTINEL}")
        if kind == "status":
            return SimpleNamespace(
                status_code=action[1],
                code="Throttling.User",
                message="rate limited",
                request_id="emb-429",
                usage=None,
                output={"embeddings": []},
            )
        raise AssertionError(f"unknown script action {action!r}")


def _patch_wrapper(monkeypatch: pytest.MonkeyPatch, sdk: _SdkScript) -> None:
    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, "test-key"),
    )
    monkeypatch.setattr(bailian_embedding.dashscope, "TextEmbedding", sdk)


# ---------------------------------------------------------------------------
# 1. canonical_embedding_tokens — full matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "available", "input_tokens"),
    [
        ({"total_tokens": 27}, True, 27),
        ({"prompt_tokens": 23, "total_tokens": 23}, True, 23),
        ({"input_tokens": 5, "total_tokens": 9}, True, 5),
        ({"input_tokens": 0, "total_tokens": 9}, True, 0),
        ({"total_tokens": 0}, True, 0),
        ({"total_tokens": None}, False, 0),
        ({"total_tokens": True}, False, 0),
        ({"total_tokens": -3}, False, 0),
        ({"total_tokens": "27"}, False, 0),
        ({"total_tokens": 27.0}, False, 0),
        ({}, False, 0),
        (None, False, 0),
        ("nope", False, 0),
    ],
)
def test_canonical_mapping_matrix(raw, available: bool, input_tokens: int) -> None:
    from app.infra.bailian_usage import canonical_embedding_tokens

    result = canonical_embedding_tokens(raw)
    assert result == {
        "provider_usage_available": available,
        "aggregate": {
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }


def test_canonical_handles_normalize_and_combine_envelopes() -> None:
    from app.infra.bailian_usage import (
        canonical_embedding_tokens,
        combine_usage_data,
        normalize_usage_data,
    )

    # normalize envelope: aggregate input=0, total>0, provider_usage raw kept.
    normalized = normalize_usage_data({"total_tokens": 27})
    assert canonical_embedding_tokens(normalized) == {
        "provider_usage_available": True,
        "aggregate": {
            "input_tokens": 27,
            "output_tokens": 0,
            "total_tokens": 27,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }

    # combine envelope (no provider_usage): aggregate input=0, total>0
    # — canonical must take total as input.
    combined = combine_usage_data(
        [
            normalize_usage_data({"total_tokens": 100}),
            normalize_usage_data({"total_tokens": 120}),
        ]
    )
    assert combined["aggregate"]["input_tokens"] == 0
    assert combined["aggregate"]["total_tokens"] == 220
    assert canonical_embedding_tokens(combined)["aggregate"] == {
        "input_tokens": 220,
        "output_tokens": 0,
        "total_tokens": 220,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    # combine of nothing: unavailable.
    empty = combine_usage_data([])
    assert canonical_embedding_tokens(empty)["provider_usage_available"] is (False)

    # normalize of missing usage: unavailable.
    missing = normalize_usage_data(None)
    assert canonical_embedding_tokens(missing)["provider_usage_available"] is (False)


# ---------------------------------------------------------------------------
# 2. wrapper — multi-batch success / partial failure / preflight
# ---------------------------------------------------------------------------


async def test_wrapper_three_batch_success_aggregates(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("ok", 120, "emb-2"), ("ok", 80, "emb-3")])
    _patch_wrapper(monkeypatch, sdk)

    result = await bailian_embedding.embed_texts_with_metadata(
        [f"t{i}" for i in range(25)], model=_MODEL, dimension=_DIM
    )
    assert len(result.embeddings) == 25
    assert result.batch_count == 3
    # Generic aggregate still covers all completed batches.
    assert result.usage_data["aggregate"]["total_tokens"] == 300
    assert len(result.provider_metadata["batches"]) == 3


async def test_wrapper_success_batch_metadata_has_canonical_totals(
    monkeypatch,
) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("ok", 120, "emb-2")])
    _patch_wrapper(monkeypatch, sdk)

    result = await bailian_embedding.embed_texts_with_metadata(
        [f"t{i}" for i in range(15)], model=_MODEL, dimension=_DIM
    )
    batches = result.provider_metadata["batches"]
    assert [b["total_tokens"] for b in batches] == [100, 120]
    assert [b["provider_usage_available"] for b in batches] == [True, True]
    assert [b["request_id"] for b in batches] == ["emb-1", "emb-2"]
    assert [b["input_count"] for b in batches] == [10, 5]


async def test_wrapper_partial_failure_keeps_completed_usage(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("status", 429), ("ok", 999, "emb-3")])
    _patch_wrapper(monkeypatch, sdk)

    with pytest.raises(bailian_embedding.EmbeddingError) as excinfo:
        await bailian_embedding.embed_texts_with_metadata(
            [f"t{i}" for i in range(25)], model=_MODEL, dimension=_DIM
        )
    exc = excinfo.value
    assert exc.failed_batch_ordinal == 2
    assert exc.batch_count == 3
    assert exc.completed_batch_count == 1
    assert exc.provider_call_attempted is True
    assert exc.model == _MODEL
    # Completed-batch aggregate survives on the error itself.
    assert exc.usage_data["aggregate"]["total_tokens"] == 100
    assert exc.provider_metadata["batches"][0]["total_tokens"] == 100


async def test_wrapper_first_batch_transport_failure_zero_completed(
    monkeypatch,
) -> None:
    sdk = _SdkScript([("raise",)])
    _patch_wrapper(monkeypatch, sdk)

    with pytest.raises(bailian_embedding.EmbeddingError) as excinfo:
        await bailian_embedding.embed_texts_with_metadata(
            [f"t{i}" for i in range(10)], model=_MODEL, dimension=_DIM
        )
    exc = excinfo.value
    assert exc.provider_call_attempted is True
    assert exc.completed_batch_count == 0
    assert exc.failed_batch_ordinal == 1
    assert exc.usage_data["aggregate"]["total_tokens"] == 0


async def test_wrapper_preflight_api_key_failure_not_attempted(monkeypatch) -> None:
    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, ""),
    )
    with pytest.raises(bailian_embedding.EmbeddingError) as excinfo:
        await bailian_embedding.embed_texts_with_metadata(["t0"], model=_MODEL, dimension=_DIM)
    exc = excinfo.value
    assert exc.provider_call_attempted is False
    assert exc.usage_data is None
    assert exc.provider_metadata is None
    assert exc.completed_batch_count == 0


# ---------------------------------------------------------------------------
# 3. adapter — success / mismatch / failure report behavior
# ---------------------------------------------------------------------------


async def test_adapter_success_returns_typed_invocation_result(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("ok", 120, "emb-2")])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    result = await provider.embed_texts_with_usage([f"t{i}" for i in range(15)], model=_MODEL)
    report = result.usage_report
    assert report.provider_call_attempted is True
    assert report.provider_succeeded is True
    assert report.usage_completeness == "complete"
    assert report.input_tokens == 220
    assert report.output_tokens == 0
    assert report.total_tokens == 220
    assert report.batch_count == 2
    assert report.completed_batch_count == 2
    assert report.failed_batch_ordinal is None
    assert report.batches_truncated_count == 0
    assert report.provider_name == "dashscope"
    assert report.model_name == _MODEL
    assert [b.total_tokens for b in report.batches] == [100, 120]
    assert [b.request_id for b in report.batches] == ["emb-1", "emb-2"]
    assert len(result.embeddings) == 15


async def test_adapter_legacy_embed_texts_delegates_single_call(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("ok", 120, "emb-2")])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    embeddings = await provider.embed_texts([f"t{i}" for i in range(15)], model=_MODEL)
    # One wrapper invocation == exactly the two planned SDK batches,
    # never a second provider call from the delegation.
    assert len(sdk.calls) == 2
    assert isinstance(embeddings, list)
    assert len(embeddings) == 15
    assert all(e.model == _MODEL and e.dim == _DIM for e in embeddings)


async def test_adapter_count_mismatch_error_carries_succeeded_report(
    monkeypatch,
) -> None:
    class _ShortSdk:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, **kwargs: Any) -> Any:
            self.calls += 1
            inputs = kwargs["input"]
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id="emb-short",
                usage={"total_tokens": 40},
                output={
                    "embeddings": [
                        {"embedding": [0.01] * _DIM}
                        for _ in inputs[:-1]  # one embedding short
                    ]
                },
            )

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, "test-key"),
    )
    monkeypatch.setattr(bailian_embedding.dashscope, "TextEmbedding", _ShortSdk())
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage([f"t{i}" for i in range(10)], model=_MODEL)
    exc = excinfo.value
    assert exc.failure_code == "embedding_coverage_mismatch"
    report = exc.embedding_usage_report
    assert report is not None
    assert report.provider_call_attempted is True
    assert report.provider_succeeded is True
    assert report.usage_completeness == "complete"
    assert report.input_tokens == 40
    assert report.completed_batch_count == 1


async def test_adapter_dimension_mismatch_error_carries_succeeded_report(
    monkeypatch,
) -> None:
    class _BadDimSdk:
        def call(self, **kwargs: Any) -> Any:
            inputs = kwargs["input"]
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id="emb-dim",
                usage={"total_tokens": 55},
                output={
                    "embeddings": [
                        {"embedding": [0.01] * 3}
                        for _ in inputs  # wrong dim
                    ]
                },
            )

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, "test-key"),
    )
    monkeypatch.setattr(bailian_embedding.dashscope, "TextEmbedding", _BadDimSdk())
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage(["t0"], model=_MODEL)
    exc = excinfo.value
    assert exc.failure_code == "embedding_dimension_mismatch"
    report = exc.embedding_usage_report
    assert report is not None
    assert report.provider_succeeded is True
    assert report.provider_call_attempted is True
    assert report.input_tokens == 55


async def test_adapter_partial_error_report_safe_no_cause_context(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("status", 429)])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage([f"t{i}" for i in range(15)], model=_MODEL)
    exc = excinfo.value
    # Sanitised exception chain preserved.
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert _SENTINEL not in str(exc)

    report = exc.embedding_usage_report
    assert report is not None
    assert report.provider_call_attempted is True
    assert report.provider_succeeded is False
    assert report.usage_completeness == "partial"
    assert report.input_tokens == 100
    assert report.total_tokens == 100
    assert report.output_tokens == 0
    assert report.batch_count == 2
    assert report.completed_batch_count == 1
    assert report.failed_batch_ordinal == 2
    assert len(report.batches) == 1
    assert report.batches[0].ordinal == 1
    assert report.batches[0].request_id == "emb-1"
    assert report.batches[0].input_count == 10
    assert report.batches[0].total_tokens == 100
    assert report.model_name == _MODEL
    assert report.provider_name == "dashscope"


async def test_adapter_first_batch_failure_unavailable(monkeypatch) -> None:
    sdk = _SdkScript([("raise",)])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage([f"t{i}" for i in range(10)], model=_MODEL)
    report = excinfo.value.embedding_usage_report
    assert report is not None
    assert report.provider_call_attempted is True
    assert report.provider_succeeded is False
    assert report.usage_completeness == "unavailable"
    assert report.completed_batch_count == 0
    assert report.failed_batch_ordinal == 1
    assert report.input_tokens == 0
    assert report.batches == ()


async def test_adapter_preflight_failure_has_no_report(monkeypatch) -> None:
    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, ""),
    )
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage(["t0"], model=_MODEL)
    assert excinfo.value.embedding_usage_report is None


async def test_adapter_total_tokens_none_completeness_unavailable(monkeypatch) -> None:
    class _NoneUsageSdk:
        def call(self, **kwargs: Any) -> Any:
            inputs = kwargs["input"]
            return SimpleNamespace(
                status_code=200,
                code="",
                message="",
                request_id="emb-none",
                usage={"total_tokens": None},
                output={"embeddings": [{"embedding": [0.01] * _DIM} for _ in inputs]},
            )

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: (_MODEL, _DIM, "test-key"),
    )
    monkeypatch.setattr(bailian_embedding.dashscope, "TextEmbedding", _NoneUsageSdk())
    provider = DashScopeArticleRagEmbeddingProvider()

    result = await provider.embed_texts_with_usage(["t0"], model=_MODEL)
    report = result.usage_report
    # All planned batches completed but zero batches had usable usage.
    assert report.provider_succeeded is True
    assert report.usage_completeness == "unavailable"
    assert report.input_tokens == 0
    assert report.completed_batch_count == 1
    assert report.batches[0].total_tokens == 0


async def test_adapter_explicit_zero_tokens_complete(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 0, "emb-0")])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    result = await provider.embed_texts_with_usage(["t0"], model=_MODEL)
    report = result.usage_report
    assert report.usage_completeness == "complete"
    assert report.input_tokens == 0
    assert report.batches[0].total_tokens == 0


async def test_adapter_ten_batches_truncated_to_eight(monkeypatch) -> None:
    # 95 texts -> 10 batches of 10 (last 5).
    script = [("ok", 10 + i, f"emb-{i + 1}") for i in range(10)]
    sdk = _SdkScript(script)
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    result = await provider.embed_texts_with_usage([f"t{i}" for i in range(95)], model=_MODEL)
    report = result.usage_report
    assert report.batch_count == 10
    assert report.completed_batch_count == 10
    assert len(report.batches) == 8
    assert report.batches_truncated_count == 2
    assert report.batches[0].ordinal == 1
    assert report.batches[7].ordinal == 8
    # Aggregate covers ALL ten batches (10+11+...+19 = 145).
    assert report.input_tokens == 145
    assert report.total_tokens == 145


async def test_report_has_no_raw_payload_fields(monkeypatch) -> None:
    sdk = _SdkScript([("ok", 100, "emb-1"), ("status", 429)])
    _patch_wrapper(monkeypatch, sdk)
    provider = DashScopeArticleRagEmbeddingProvider()

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as excinfo:
        await provider.embed_texts_with_usage([f"t{i}" for i in range(15)], model=_MODEL)
    report = excinfo.value.embedding_usage_report
    assert report is not None
    serialized = json.dumps(
        {
            "report": {
                f: getattr(report, f)
                for f in (
                    "provider_call_attempted",
                    "provider_succeeded",
                    "usage_completeness",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "batch_count",
                    "completed_batch_count",
                    "failed_batch_ordinal",
                    "batches_truncated_count",
                    "provider_name",
                    "model_name",
                )
            },
            "batches": [dataclasses.asdict(b) for b in report.batches],
            "exc_str": str(excinfo.value),
        },
        default=str,
    )
    for forbidden in (
        "input_chars",
        "message",
        'embedding":',
        _SENTINEL,
        "api_key",
    ):
        assert forbidden not in serialized
    # Batch summaries carry exactly the four frozen fields.
    assert {f.name for f in dataclasses.fields(report.batches[0])} == {
        "ordinal",
        "request_id",
        "input_count",
        "total_tokens",
    }


# ---------------------------------------------------------------------------
# 4. typed carrier / protocol isolation
# ---------------------------------------------------------------------------


def test_index_protocol_is_separate_from_shared_provider() -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagEmbeddingProvider,
        ArticleRagIndexEmbeddingProvider,
    )

    # The shared retrieval-facing protocol must NOT grow the usage method.
    assert "embed_texts_with_usage" not in ArticleRagEmbeddingProvider.__protocol_attrs__
    assert "embed_texts_with_usage" in ArticleRagIndexEmbeddingProvider.__protocol_attrs__


def test_error_carries_optional_usage_report_field() -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagEmbeddingUsageReport,
        ArticleRagIndexWorkerError,
    )

    # Default None — existing callers unchanged.
    exc = ArticleRagIndexWorkerError(
        _MSG_VECTOR_COLLECTION_MISMATCH,
        retryable=False,
        failure_class="vector_collection_mismatch",
        failure_code="vector_collection_mismatch",
    )
    assert exc.embedding_usage_report is None

    report = ArticleRagEmbeddingUsageReport(
        provider_call_attempted=True,
        provider_succeeded=False,
        usage_completeness="partial",
        input_tokens=1,
        output_tokens=0,
        total_tokens=1,
        batch_count=1,
        completed_batch_count=1,
        failed_batch_ordinal=1,
        batches=(),
        batches_truncated_count=0,
        provider_name="dashscope",
        model_name=_MODEL,
    )
    exc2 = ArticleRagIndexWorkerError(
        "msg",
        retryable=True,
        failure_class="embedding",
        failure_code="embedding_backend_failed",
        embedding_usage_report=report,
    )
    assert exc2.embedding_usage_report is report
    # Not part of diagnostics / message.
    assert exc2.diagnostics == {}
    assert "partial" not in str(exc2)


def test_dataclasses_frozen_and_slotted() -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagEmbeddingBatchUsage,
        ArticleRagEmbeddingInvocationResult,
        ArticleRagEmbeddingUsageReport,
    )

    report = ArticleRagEmbeddingUsageReport(
        provider_call_attempted=False,
        provider_succeeded=True,
        usage_completeness="unavailable",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        batch_count=0,
        completed_batch_count=0,
        failed_batch_ordinal=None,
        batches=(),
        batches_truncated_count=0,
        provider_name="fake",
        model_name=_MODEL,
    )
    assert report.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert report.__slots__ is not None
    batch = ArticleRagEmbeddingBatchUsage(ordinal=1, request_id=None, input_count=1, total_tokens=0)
    assert batch.__dataclass_params__.frozen  # type: ignore[attr-defined]
    invocation = ArticleRagEmbeddingInvocationResult(embeddings=(), usage_report=report)
    assert invocation.usage_report is report


# ---------------------------------------------------------------------------
# 5. Fake / Unconfigured providers
# ---------------------------------------------------------------------------


async def test_fake_provider_no_forged_usage_single_call_count() -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        FakeArticleRagEmbeddingProvider,
    )

    fake = FakeArticleRagEmbeddingProvider()
    result = await fake.embed_texts_with_usage(["a", "b"], model=_MODEL)
    assert fake.call_count == 1
    assert len(result.embeddings) == 2
    report = result.usage_report
    assert report.provider_call_attempted is False
    assert report.provider_succeeded is True
    assert report.usage_completeness == "unavailable"
    assert (report.input_tokens, report.output_tokens, report.total_tokens) == (
        0,
        0,
        0,
    )
    assert report.batches == ()
    assert report.batch_count == 0
    assert report.completed_batch_count == 0
    assert report.batches_truncated_count == 0
    assert report.provider_name == "fake"
    assert report.model_name == _MODEL

    # Legacy surface unchanged; call_count keeps counting one per call.
    embeddings = await fake.embed_texts(["c"], model=_MODEL)
    assert fake.call_count == 2
    assert len(embeddings) == 1


async def test_unconfigured_provider_no_usage_report() -> None:
    unconfigured = UnconfiguredArticleRagEmbeddingProvider()
    with pytest.raises(Exception) as excinfo:
        await unconfigured.embed_texts_with_usage(["a"], model=_MODEL)
    assert getattr(excinfo.value, "embedding_usage_report", "missing") is None
    assert excinfo.value.failure_code == "embedding_provider_unconfigured"


# ---------------------------------------------------------------------------
# 6. retrieval regression — retrieval-facing protocol unchanged
# ---------------------------------------------------------------------------


async def test_retrieval_facing_provider_without_usage_method_still_works() -> None:
    # A provider exposing ONLY embed_texts (retrieval shape) keeps working
    # through the adapter surface that retrieval uses.
    class _RetrievalOnlyProvider:
        async def embed_texts(self, texts, *, model=None):
            from app.services.reader_orchestration.article_rag_index_worker import (
                FakeArticleRagEmbeddingProvider,
            )

            return await FakeArticleRagEmbeddingProvider().embed_texts(texts, model=model)

    provider = _RetrievalOnlyProvider()
    result = await provider.embed_texts(["hello"], model=_MODEL)
    assert len(result) == 1
    assert result[0].model == _MODEL
