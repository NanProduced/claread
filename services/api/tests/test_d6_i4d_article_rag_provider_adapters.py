"""Tests for D6-I4D: Article RAG Provider Adapter Foundation.

Covers:

1. :class:`DashScopeArticleRagEmbeddingProvider` — SHA-256 contract, input
   order, model override, error rewrap, no key/text leakage, empty input.
2. :func:`build_default_article_rag_embedding_provider` — unconfigured
   default + enable path via DASHSCOPE_API_KEY env / BAILIAN_API_KEY fallback.
3. :class:`ZillizArticleRagVectorWriter` — schema, idempotency, partial
   count propagation, payload sanitisation, denylist guard, no token leak,
   lazy pymilvus init.
4. :func:`build_default_article_rag_vector_writer` — unconfigured default
   + enable path with full configuration.
5. :func:`_build_article_rag_upsert_row` — row shape + denylist guard
   enforced even when caller shoves forbidden keys into the metadata dict.

No real network calls are made.  ``app.infra.bailian_embedding`` is
monkeypatched at the module-attribute level; ``pymilvus`` is stubbed via
``sys.modules`` so the lazy import path is exercised without installing
the real SDK on the test machine.  Opt-in smoke skeletons at the end
are skipped unless ``READER_ARTICLE_RAG_SMOKE=1`` AND real keys are
configured (mirrors ``test_d6_i3q_oss_artifact_io.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import types
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.article_rag_embedding_provider import (
    DashScopeArticleRagEmbeddingProvider,
    DashScopeArticleRagEmbeddingProviderError,
    READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE,
    build_default_article_rag_embedding_provider,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagEmbedding,
    ArticleRagVectorChunk,
    ArticleRagVectorWriteMetadata,
    ArticleRagVectorWriteResult,
    FakeArticleRagVectorWriter,
    UnconfiguredArticleRagEmbeddingProvider,
    UnconfiguredArticleRagVectorWriter,
)
from app.services.reader_orchestration.article_rag_vector_store import (
    READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ,
    ZILLIZ_DEFAULT_VECTOR_DIM,
    ZillizArticleRagVectorWriter,
    ZillizArticleRagVectorWriterError,
    _ARTICLE_RAG_CITATION_KEYS,
    _build_article_rag_collection_schema,
    _build_article_rag_upsert_row,
    _FORBIDDEN_VECTOR_PAYLOAD_KEYS,
    build_default_article_rag_vector_writer,
)

# ---------------------------------------------------------------------------
# Fixed UUIDs + canonical fixtures (deterministic across runs)
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("00000000-0000-0000-0000-00000000a401")
_STABLE_DOC_ID = UUID("00000000-0000-0000-0000-00000000a402")
_BASE_ID = UUID("00000000-0000-0000-0000-00000000a403")
_INDEX_RUN_ID = UUID("00000000-0000-0000-0000-00000000a404")

_FAKE_API_KEY = "sk-dashscope-test-only-do-not-use-in-prod"
_FAKE_ZILLIZ_URI = "https://fake-uri.zilliztest.com:19540"
_FAKE_ZILLIZ_TOKEN = "zilliz-fake-token-do-not-use-in-prod"
_FAKE_ZILLIZ_COLLECTION = "article_rag_index_v1"


# ---------------------------------------------------------------------------
# Settings cache hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Ensure tests do not leak env-patched ``Settings()`` instances."""
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeEmbeddingCallResult:
    """Minimal stand-in for ``bailian_embedding.EmbeddingCallResult``."""

    embeddings: list[list[float]]
    usage_data: dict
    provider_metadata: dict
    model: str
    dimension: int
    input_count: int
    input_chars: int
    batch_count: int


def _stub_embed_texts_with_metadata(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: _FakeEmbeddingCallResult | None = None,
    exc: Exception | None = None,
) -> dict[str, Any]:
    """Replace ``bailian_embedding.embed_texts_with_metadata`` and return a call log."""
    calls: dict[str, Any] = {"texts": [], "model": [], "dimension": []}

    async def _fake(texts, *, model=None, dimension=None):
        calls["texts"].append(list(texts))
        calls["model"].append(model)
        calls["dimension"].append(dimension)
        if exc is not None:
            raise exc
        assert response is not None
        return response

    # The adapter imports ``from app.infra import bailian_embedding`` and
    # then calls ``bailian_embedding.embed_texts_with_metadata``.  We
    # patch the function on the module attribute to intercept that call.
    from app.infra import bailian_embedding

    monkeypatch.setattr(
        bailian_embedding, "embed_texts_with_metadata", _fake
    )
    return calls


def _make_embedding(
    *, text: str, model: str = "text-embedding-v4", dim: int = 8
) -> ArticleRagEmbedding:
    """Build a deterministic :class:`ArticleRagEmbedding` for fixture use."""
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagEmbedding(
        text_sha256=text_sha,
        model=model,
        vector=tuple(float(i) / dim for i in range(dim)),
        dim=dim,
    )


def _make_chunk(
    *,
    text: str,
    chunk_id: str | None = None,
    citation: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    model: str = "text-embedding-v4",
    dim: int = 8,
) -> ArticleRagVectorChunk:
    """Build a :class:`ArticleRagVectorChunk` matching I4C's contract."""
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagVectorChunk(
        chunk_id=chunk_id or f"chunk-{hashlib.sha1(text.encode()).hexdigest()[:8]}",
        content_sha256=content_sha,
        embedding_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        embedding=_make_embedding(text=text, model=model, dim=dim),
        citation=citation
        or {
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [str(uuid4()), str(uuid4())],
            "unit_ids": [str(uuid4())],
            "anchor_segment_ids": [str(uuid4())],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 12,
        },
        metadata=metadata or {"chunk_kind": "block", "language": "en"},
    )


def _make_write_metadata(
    *, chunk_count: int = 3
) -> ArticleRagVectorWriteMetadata:
    """Build :class:`ArticleRagVectorWriteMetadata` for fixtures."""
    return ArticleRagVectorWriteMetadata(
        collection=_FAKE_ZILLIZ_COLLECTION,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        index_version="article_rag_index_v1",
        chunker_version="chunker-v1",
        plan_content_sha256=hashlib.sha256(b"plan").hexdigest(),
        chunk_count=chunk_count,
    )


# ---------------------------------------------------------------------------
# 1. Embedding adapter contract
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embedding_returns_one_record_per_input_in_order(monkeypatch: pytest.MonkeyPatch):
    text_a = "hello world"
    text_b = "second chunk text"
    text_c = "third chunk text"
    expected_vectors: list[list[float]] = [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
        [0.7, 0.8, 0.9],
    ]
    response = _FakeEmbeddingCallResult(
        embeddings=expected_vectors,
        usage_data={"prompt_tokens": 4, "total_tokens": 4},
        provider_metadata={"provider_usage_available": True, "batches": []},
        model="text-embedding-v4",
        dimension=3,
        input_count=3,
        input_chars=len(text_a) + len(text_b) + len(text_c),
        batch_count=1,
    )
    calls = _stub_embed_texts_with_metadata(monkeypatch, response=response)

    provider = DashScopeArticleRagEmbeddingProvider()
    embeddings = await provider.embed_texts([text_a, text_b, text_c])

    assert len(embeddings) == 3
    # Order preserved.  ``ArticleRagEmbedding.vector`` is a tuple; convert
    # the expected list to a tuple for an order-sensitive compare.
    assert [e.vector for e in embeddings] == [tuple(v) for v in expected_vectors]
    # SHA-256 computed locally.
    assert embeddings[0].text_sha256 == hashlib.sha256(text_a.encode("utf-8")).hexdigest()
    assert embeddings[1].text_sha256 == hashlib.sha256(text_b.encode("utf-8")).hexdigest()
    assert embeddings[2].text_sha256 == hashlib.sha256(text_c.encode("utf-8")).hexdigest()
    # The wrapper was called once with all three inputs.
    assert calls["texts"] == [[text_a, text_b, text_c]]
    # Model was forwarded (None → wrapper resolves from registry).
    assert calls["model"] == [None]


@pytest.mark.anyio
async def test_embedding_dim_matches_wrapper(monkeypatch: pytest.MonkeyPatch):
    text = "single chunk"
    response = _FakeEmbeddingCallResult(
        embeddings=[[1.0, 2.0, 3.0, 4.0]],
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="text-embedding-v4",
        dimension=4,
        input_count=1,
        input_chars=len(text),
        batch_count=1,
    )
    _stub_embed_texts_with_metadata(monkeypatch, response=response)

    provider = DashScopeArticleRagEmbeddingProvider()
    embeddings = await provider.embed_texts([text])

    assert len(embeddings) == 1
    assert embeddings[0].dim == 4
    assert embeddings[0].vector == (1.0, 2.0, 3.0, 4.0)


@pytest.mark.anyio
async def test_embedding_empty_input_returns_empty_no_call(monkeypatch: pytest.MonkeyPatch):
    calls = _stub_embed_texts_with_metadata(monkeypatch)
    provider = DashScopeArticleRagEmbeddingProvider()
    embeddings = await provider.embed_texts([])
    assert embeddings == []
    # Wrapper should NOT have been called for empty input.
    assert calls["texts"] == []


@pytest.mark.anyio
async def test_embedding_local_sha256_overrides_wrapper_hash(monkeypatch: pytest.MonkeyPatch):
    """The text_sha256 is computed locally; a mismatched wrapper response is ignored."""
    text = "lorem ipsum dolor"
    local_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Wrapper returns a vector, but the wrapper-level text_sha (if it
    # had one) would not be used — the adapter always uses the local hash.
    response = _FakeEmbeddingCallResult(
        embeddings=[[0.1, 0.2]],
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="text-embedding-v4",
        dimension=2,
        input_count=1,
        input_chars=len(text),
        batch_count=1,
    )
    _stub_embed_texts_with_metadata(monkeypatch, response=response)

    provider = DashScopeArticleRagEmbeddingProvider()
    embeddings = await provider.embed_texts([text])

    assert len(embeddings) == 1
    assert embeddings[0].text_sha256 == local_sha
    # And it must NOT be equal to a hash of some other text — proving the
    # local hash actually depends on the input text.
    other_sha = hashlib.sha256(b"different text").hexdigest()
    assert embeddings[0].text_sha256 != other_sha


@pytest.mark.anyio
async def test_embedding_model_override_forwarded(monkeypatch: pytest.MonkeyPatch):
    text = "sample"
    response = _FakeEmbeddingCallResult(
        embeddings=[[0.0]],
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="custom-model",
        dimension=1,
        input_count=1,
        input_chars=len(text),
        batch_count=1,
    )
    calls = _stub_embed_texts_with_metadata(monkeypatch, response=response)

    provider = DashScopeArticleRagEmbeddingProvider(model_override="custom-model")
    embeddings = await provider.embed_texts([text])
    # Model override at construction time was passed through.
    assert calls["model"] == ["custom-model"]
    # Resolved model on the resulting embedding matches the wrapper response.
    assert embeddings[0].model == "custom-model"


@pytest.mark.anyio
async def test_embedding_model_override_per_call(monkeypatch: pytest.MonkeyPatch):
    """The ``model`` kwarg on ``embed_texts`` overrides both default and constructor."""
    text = "sample"
    response_a = _FakeEmbeddingCallResult(
        embeddings=[[0.1, 0.2]],
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="model-A",
        dimension=2,
        input_count=1,
        input_chars=len(text),
        batch_count=1,
    )
    response_b = _FakeEmbeddingCallResult(
        embeddings=[[0.3, 0.4]],
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="model-B",
        dimension=2,
        input_count=1,
        input_chars=len(text),
        batch_count=1,
    )
    calls_list: list[_FakeEmbeddingCallResult] = [response_a, response_b]

    async def _fake(texts, *, model=None, dimension=None):
        calls["model"].append(model)
        return calls_list.pop(0)

    calls = {"model": []}
    from app.infra import bailian_embedding

    monkeypatch.setattr(
        bailian_embedding, "embed_texts_with_metadata", _fake
    )

    provider = DashScopeArticleRagEmbeddingProvider(model_override="default-model")
    e1 = await provider.embed_texts([text], model="model-A")
    e2 = await provider.embed_texts([text], model="model-B")
    assert calls["model"] == ["model-A", "model-B"]
    assert e1[0].model == "model-A"
    assert e2[0].model == "model-B"


@pytest.mark.anyio
async def test_embedding_wrapper_error_rewrapped_without_key(monkeypatch: pytest.MonkeyPatch):
    from app.infra import bailian_embedding

    secret_api_key = "sk-leaked-into-message-do-not-use"
    secret_chunk_text = "SECRET-CHUNK-DO-NOT-LEAK-DO-NOT-LEAK"

    async def _fake(texts, *, model=None, dimension=None):
        # Simulate a misconfigured SDK that echoes BOTH the API key
        # AND the chunk text into its EmbeddingError message.  The
        # adapter must NOT forward either — the contract is a fixed
        # diagnostic that excludes both.  ``__cause__`` retains the
        # original exception for ops inspection.
        raise bailian_embedding.EmbeddingError(
            "dashscope call failed api_key="
            f"{secret_api_key} status=403 chunk_text="
            f"{secret_chunk_text}"
        )

    monkeypatch.setattr(bailian_embedding, "embed_texts_with_metadata", _fake)

    provider = DashScopeArticleRagEmbeddingProvider()
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await provider.embed_texts([secret_chunk_text])

    err = exc_info.value
    msg = str(err)
    # The API key MUST NOT appear in the rendered message.
    assert secret_api_key not in msg
    # Chunk text MUST NOT appear in the rendered message.
    assert secret_chunk_text not in msg
    # No <redacted> marker either (we don't forward any verbatim SDK content).
    assert "<redacted>" not in msg
    # The message is a fixed diagnostic naming the wrapper, the
    # input count, and the SDK exception class.
    assert "DashScope embedding call failed via bailian_embedding" in msg
    assert "input_count=1" in msg
    assert "EmbeddingError" in msg
    # Stable failure code + retryable for backend transient.
    assert err.failure_code == "embedding_backend_failed"
    assert err.retryable is True
    assert err.failure_class == "embedding"
    # ``__cause__`` preserves the original SDK error for ops.
    assert isinstance(err.__cause__, bailian_embedding.EmbeddingError)


@pytest.mark.anyio
async def test_embedding_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch):
    response = _FakeEmbeddingCallResult(
        embeddings=[[0.1, 0.2]],  # Only 1 vector for 2 inputs
        usage_data={},
        provider_metadata={"provider_usage_available": False, "batches": []},
        model="text-embedding-v4",
        dimension=2,
        input_count=2,
        input_chars=10,
        batch_count=1,
    )
    _stub_embed_texts_with_metadata(monkeypatch, response=response)

    provider = DashScopeArticleRagEmbeddingProvider()
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await provider.embed_texts(["one", "two"])

    assert exc_info.value.failure_code == "embedding_coverage_mismatch"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_embedding_empty_model_override_rejected_at_construction():
    """An empty ``model_override`` is rejected (the factory is
    responsible for stripping this before construction, but the
    adapter defends as well)."""
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        DashScopeArticleRagEmbeddingProvider(model_override="")
    assert exc_info.value.failure_code == "embedding_provider_unconfigured"
    assert exc_info.value.retryable is False


def test_embedding_error_inherits_article_rag_index_worker_error():
    """Fix 1: the adapter's typed error MUST inherit from the worker error
    class so the I4C worker's exception handler (which only catches
    ``ArticleRagIndexWorkerError``) requeues / fails the job correctly."""
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagIndexWorkerError,
    )

    err = DashScopeArticleRagEmbeddingProviderError(
        "synthetic",
        retryable=False,
        failure_class="embedding",
        failure_code="embedding_provider_unconfigured",
    )
    assert isinstance(err, ArticleRagIndexWorkerError)
    # Also assert it remains catchable as the typed adapter error
    # (forward compatibility — current contract).
    assert isinstance(err, DashScopeArticleRagEmbeddingProviderError)


@pytest.mark.anyio
async def test_embedding_error_message_omits_chunk_text(monkeypatch: pytest.MonkeyPatch):
    """Fix 5: the message of a re-wrapped EmbeddingError MUST NOT carry
    chunk text echoed by the upstream SDK, even if the SDK put it there.
    This is the chunk-text-leak guard that protects worker error JSON."""
    from app.infra import bailian_embedding

    secret_chunk_text = "SECRET-CHUNK-DO-NOT-LEAK-DO-NOT-LEAK"

    async def _fake(texts, *, model=None, dimension=None):
        # Wrap the input texts verbatim into the SDK error message,
        # simulating a debug-echoing SDK or a verbose log.
        raise bailian_embedding.EmbeddingError(
            f"dashscope failure; input texts={texts}"
        )

    monkeypatch.setattr(bailian_embedding, "embed_texts_with_metadata", _fake)

    provider = DashScopeArticleRagEmbeddingProvider()
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await provider.embed_texts([secret_chunk_text])

    err_msg = str(exc_info.value)
    assert secret_chunk_text not in err_msg
    # Generic content guard: any verbatim texts list fragment is gone.
    assert "[" not in err_msg or "input_count" in err_msg


# ---------------------------------------------------------------------------
# 2. Embedding factory
# ---------------------------------------------------------------------------


def test_embedding_factory_unconfigured_by_default():
    settings = Settings()
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)


def test_embedding_factory_unconfigured_when_provider_name_mismatch():
    settings = Settings(
        reader_article_rag_embedding_provider="not-dashscope",
        bailian_api_key=_FAKE_API_KEY,
    )
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)


def test_embedding_factory_unconfigured_when_no_api_key(monkeypatch: pytest.MonkeyPatch):
    """If the wrapper's ``resolve_embedding_config`` raises or returns
    an empty key, the factory must return the unconfigured provider."""
    from app.infra import bailian_embedding

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, ""),
    )
    settings = Settings(
        reader_article_rag_embedding_provider="dashscope",
        bailian_api_key="",
    )
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)


def test_embedding_factory_unconfigured_when_wrapper_raises(monkeypatch: pytest.MonkeyPatch):
    """If the wrapper's ``resolve_embedding_config`` raises
    ``EmbeddingError`` (e.g. no key configured), the factory must
    return the unconfigured provider (not raise startup failure)."""
    from app.infra import bailian_embedding

    def _raise():
        raise bailian_embedding.EmbeddingError("No API key configured for embedding.")

    monkeypatch.setattr(
        bailian_embedding, "resolve_embedding_config", _raise
    )
    settings = Settings(
        reader_article_rag_embedding_provider="dashscope",
        bailian_api_key="",
    )
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)


def test_embedding_factory_returns_real_provider_when_api_key_resolves(monkeypatch: pytest.MonkeyPatch):
    """Fix 4: the factory resolves the API key via
    ``bailian_embedding.resolve_embedding_config`` — the same path the
    wrapper uses on every call.  The ``DASHSCOPE_API_KEY`` env var is
    NOT consulted (mirroring the wrapper behaviour)."""
    from app.infra import bailian_embedding

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, _FAKE_API_KEY),
    )
    settings = Settings(
        reader_article_rag_embedding_provider="dashscope",
        reader_article_rag_embedding_model="text-embedding-v4",
    )
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, DashScopeArticleRagEmbeddingProvider)
    assert provider.provider_name == READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE
    # and the model override was honoured
    assert provider._model_override == "text-embedding-v4"


def test_embedding_factory_falls_back_to_legacy_bailian_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    """The wrapper's ``resolve_embedding_config`` honours ``settings.bailian_api_key``
    as a fallback when no registry route is set.  When that fallback
    resolves, the factory enables the real provider (single resolution path)."""
    from app.infra import bailian_embedding

    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, _FAKE_API_KEY),
    )
    settings = Settings(
        reader_article_rag_embedding_provider="dashscope",
        bailian_api_key=_FAKE_API_KEY,
    )
    provider = build_default_article_rag_embedding_provider(settings)
    assert isinstance(provider, DashScopeArticleRagEmbeddingProvider)


def test_embedding_factory_ignores_dashscope_api_key_env(monkeypatch: pytest.MonkeyPatch):
    """Fix 4: the factory does NOT consult the ``DASHSCOPE_API_KEY`` env var.
    Setting it alone does not enable the real provider if the wrapper's
    resolution path returns an empty key."""
    from app.infra import bailian_embedding

    monkeypatch.setenv("DASHSCOPE_API_KEY", _FAKE_API_KEY)
    # Wrapper says no key resolved.
    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, ""),
    )
    settings = Settings(
        reader_article_rag_embedding_provider="dashscope",
        bailian_api_key="",
    )
    provider = build_default_article_rag_embedding_provider(settings)
    # Despite the DASHSCOPE_API_KEY env being set, the factory stays
    # unconfigured because the wrapper's resolution path returned
    # nothing.
    assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)


def test_embedding_factory_constants_align():
    """Sanity: the constant matches the I4C-documented provider name."""
    assert READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE == "dashscope"


# ---------------------------------------------------------------------------
# 3. Vector writer contract (with pymilvus SDK stub)
# ---------------------------------------------------------------------------


class _FakeMilvusClient:
    """Records every SDK call.  No network.  Idempotent by default."""

    def __init__(self, *, upserted_count: int | None = None, raise_exc: Exception | None = None):
        self.has_collection_calls: list[str] = []
        self.create_collection_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []
        self.upserted_count_override = upserted_count
        self.raise_exc = raise_exc

    def has_collection(self, *, collection_name: str) -> bool:  # noqa: D401
        self.has_collection_calls.append(collection_name)
        return False  # always create so the schema builder is exercised

    def create_collection(self, *, collection_name: str, schema: dict[str, Any]) -> None:
        self.create_collection_calls.append(
            {"collection_name": collection_name, "schema": schema}
        )

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> dict[str, Any]:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.upsert_calls.append({"collection_name": collection_name, "data": data})
        count = (
            self.upserted_count_override
            if self.upserted_count_override is not None
            else len(data)
        )
        return {"upserted_count": count}


def _install_pymilvus_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    upserted_count: int | None = None,
    raise_exc: Exception | None = None,
) -> _FakeMilvusClient:
    """Install a fake pymilvus module that exposes the symbols both the
    client constructor and ``_build_pymilvus_collection_schema`` import.

    The schema symbols are minimal stubs with the attribute names the
    production code reads (``dtype``, ``dim``, ``is_primary``, ``name``)
    plus the ``CollectionSchema`` shape used by the production builder
    (``fields=``, ``description=``).  They are NOT behavioural — the
    tests assert ``create_collection_calls`` directly, not via pymilvus's
    own ``schema.verify()``.
    """
    class _StubFieldSchema:
        def __init__(
            self,
            *,
            name,
            dtype,
            is_primary=False,
            max_length=None,
            dim=None,
            nullable=False,
        ):
            self.name = name
            self.dtype = dtype
            self.is_primary = is_primary
            self.max_length = max_length
            self.dim = dim
            self.nullable = nullable

    class _StubCollectionSchema:
        def __init__(self, *, fields, description=""):
            self.fields = list(fields)
            self.description = description

    class _StubDataType:
        VARCHAR = "VARCHAR"
        INT64 = "INT64"
        FLOAT_VECTOR = "FLOAT_VECTOR"

    fake_module = types.ModuleType("pymilvus")
    client = _FakeMilvusClient(
        upserted_count=upserted_count, raise_exc=raise_exc
    )
    fake_module.MilvusClient = lambda *, uri, token: client  # type: ignore[assignment]
    fake_module.CollectionSchema = _StubCollectionSchema  # type: ignore[assignment]
    fake_module.FieldSchema = _StubFieldSchema  # type: ignore[assignment]
    fake_module.DataType = _StubDataType  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)
    return client


def _install_pymilvus_stub_with_raising_upsert(
    monkeypatch: pytest.MonkeyPatch, *, exc: Exception
) -> types.ModuleType:
    """Install a fake pymilvus module whose MilvusClient raises ``exc``
    on every ``upsert`` call.  Exposes the schema-symbol stubs too so
    that ``_build_pymilvus_collection_schema`` (called by the writer
    inside ``_sync_upsert`` BEFORE the user-installed ``upsert`` runs)
    completes successfully — the exception is then surfaced by the
    writer's rewrap.

    Returns the fake module so tests can assert against
    ``MilvusClient`` call counts in future if needed.
    """
    class _StubFieldSchema:
        def __init__(
            self,
            *,
            name,
            dtype,
            is_primary=False,
            max_length=None,
            dim=None,
            nullable=False,
        ):
            self.name = name
            self.dtype = dtype
            self.is_primary = is_primary
            self.max_length = max_length
            self.dim = dim
            self.nullable = nullable

    class _StubCollectionSchema:
        def __init__(self, *, fields, description=""):
            self.fields = list(fields)
            self.description = description

    class _StubDataType:
        VARCHAR = "VARCHAR"
        INT64 = "INT64"
        FLOAT_VECTOR = "FLOAT_VECTOR"

    class _RaisingClient:
        def __init__(self):
            self.has_collection_calls: list[str] = []
            self.create_collection_calls: list[dict[str, Any]] = []

        def has_collection(self, *, collection_name: str) -> bool:
            self.has_collection_calls.append(collection_name)
            return False

        def create_collection(self, *, collection_name, schema) -> None:
            self.create_collection_calls.append({"collection_name": collection_name, "schema": schema})

        def upsert(self, *, collection_name, data):
            raise exc

    fake_module = types.ModuleType("pymilvus")
    raising = _RaisingClient()
    fake_module.MilvusClient = lambda *, uri, token: raising  # type: ignore[assignment]
    fake_module.CollectionSchema = _StubCollectionSchema  # type: ignore[assignment]
    fake_module.FieldSchema = _StubFieldSchema  # type: ignore[assignment]
    fake_module.DataType = _StubDataType  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)
    return fake_module


def _remove_pymilvus_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "pymilvus", raising=False)


@pytest.fixture
def _pymilvus_clean(monkeypatch: pytest.MonkeyPatch):
    """Ensure no pymilvus stub leaks between tests."""
    _remove_pymilvus_stub(monkeypatch)
    yield
    _remove_pymilvus_stub(monkeypatch)


def test_zilliz_writer_unconfigured_when_factory_default():
    settings = Settings()
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_unconfigured_when_provider_name_blank():
    settings = Settings(
        reader_article_rag_zilliz_uri=_FAKE_ZILLIZ_URI,
        reader_article_rag_zilliz_token=_FAKE_ZILLIZ_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=1024,
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_unconfigured_when_token_blank():
    settings = Settings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_ZILLIZ_URI,
        reader_article_rag_zilliz_token="",
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=1024,
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_unconfigured_when_dim_nonpositive():
    settings = Settings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_ZILLIZ_URI,
        reader_article_rag_zilliz_token=_FAKE_ZILLIZ_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=0,
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_configured_when_all_settings_present():
    settings = Settings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_ZILLIZ_URI,
        reader_article_rag_zilliz_token=_FAKE_ZILLIZ_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=1024,
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, ZillizArticleRagVectorWriter)
    assert writer.provider_name == READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ
    assert writer.collection == _FAKE_ZILLIZ_COLLECTION


@pytest.mark.anyio
async def test_zilliz_writer_upserts_chunks_with_no_text(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunks = [_make_chunk(text=f"text-{i}") for i in range(3)]
    metadata = _make_write_metadata(chunk_count=3)

    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=chunks,
        metadata=metadata,
    )

    assert isinstance(result, ArticleRagVectorWriteResult)
    assert result.collection == _FAKE_ZILLIZ_COLLECTION
    assert result.upserted_count == 3
    # Verify pymilvus received exactly 3 rows.
    assert len(fake_client.upsert_calls) == 1
    upsert_payload = fake_client.upsert_calls[0]["data"]
    assert len(upsert_payload) == 3
    # Defence in depth: the wire payload must NOT contain text / chunk_text keys.
    for row in upsert_payload:
        assert "text" not in row
        assert "chunk_text" not in row
        assert "chunk_texts" not in row
        assert "chunks" not in row
        assert "plate" not in row
        assert "markdown" not in row
        # Citation values MUST be JSON strings, not raw text blobs.
        assert isinstance(row["citation_metadata_json"], str)
        assert isinstance(row["metadata_json"], str)


@pytest.mark.anyio
async def test_zilliz_writer_idempotent_on_chunk_id(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunk = _make_chunk(text="re-upsertable", chunk_id="chunk-fixed-id")
    metadata = _make_write_metadata(chunk_count=1)

    r1 = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[chunk],
        metadata=metadata,
    )
    r2 = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[chunk],
        metadata=metadata,
    )
    assert r1.upserted_count == 1
    assert r2.upserted_count == 1
    # Same primary key on both upserts — pymilvus would overwrite on the
    # second call, but the stub records both. The important thing is
    # neither raised.
    assert len(fake_client.upsert_calls) == 2


@pytest.mark.anyio
async def test_zilliz_writer_propagates_partial_upsert_count(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """Partial upsert must NOT be silently coerced: worker Phase-4 check
    surfaces ``upserted_count != len(chunks)`` as retryable error."""
    fake_client = _install_pymilvus_stub(monkeypatch, upserted_count=2)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunks = [_make_chunk(text=f"text-{i}") for i in range(3)]
    metadata = _make_write_metadata(chunk_count=3)

    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=chunks,
        metadata=metadata,
    )
    # Verbatim propagation: we sent 3 rows, pymilvus says 2 were upserted.
    # The writer does NOT silently coerce to 3.
    assert result.upserted_count == 2
    assert result.provider_metadata["requested_count"] == 3


@pytest.mark.anyio
async def test_zilliz_writer_no_token_in_raised_error(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """The token + chunk text MUST NOT appear in raised exception messages.
    The contract is a fixed diagnostic that excludes any verbatim SDK
    content (which may echo the chunk text or token).  ``__cause__``
    retains the original exception for ops inspection."""
    secret_token = "zilliz-real-token-do-not-leak"
    secret_chunk_text = "SECRET-CHUNK-DO-NOT-LEAK-DO-NOT-LEAK"

    _install_pymilvus_stub_with_raising_upsert(
        monkeypatch,
        exc=RuntimeError(
            f"pymilvus transport error token={secret_token} status=500 "
            f"chunk_text={secret_chunk_text}"
        ),
    )

    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=secret_token,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunk = _make_chunk(text=secret_chunk_text)
    metadata = _make_write_metadata(chunk_count=1)

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FAKE_ZILLIZ_COLLECTION,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    err = exc_info.value
    err_msg = str(err)
    # Token MUST NOT appear in the rendered message.
    assert secret_token not in err_msg
    # Chunk text MUST NOT appear in the rendered message.
    assert secret_chunk_text not in err_msg
    # No <redacted> marker (we don't forward SDK content).
    assert "<redacted>" not in err_msg
    # No verbatim SDK status text either (the contract is a fixed diagnostic).
    assert "status=500" not in err_msg
    # Fixed diagnostic naming the wrapper, the input count, and the SDK class.
    assert "Zilliz upsert failed via pymilvus" in err_msg
    assert "input_count=1" in err_msg
    assert "RuntimeError" in err_msg
    # Stable failure code + retryable for transport errors.
    assert err.failure_code == "vector_write_failed"
    assert err.retryable is True
    # ``__cause__`` preserves the original SDK exception.
    assert isinstance(err.__cause__, RuntimeError)


@pytest.mark.anyio
async def test_zilliz_writer_lazy_sdk_init(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """Construction does not construct MilvusClient.  First upsert does."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    # SDK not constructed yet.
    assert fake_client.has_collection_calls == []
    # First upsert builds the client + ensures the collection.
    await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[_make_chunk(text="lazy")],
        metadata=_make_write_metadata(chunk_count=1),
    )
    assert fake_client.has_collection_calls == [_FAKE_ZILLIZ_COLLECTION]
    assert len(fake_client.create_collection_calls) == 1
    created_schema = fake_client.create_collection_calls[0]["schema"]
    # Fix 2: the writer now passes a real ``CollectionSchema`` (ORM-style),
    # not the structural dict.  ``CollectionSchema`` exposes ``fields``
    # and ``description``; each ``FieldSchema`` exposes ``name``,
    # ``is_primary``, ``dtype``.  Assert structurally via attribute access.
    primary_keys = [f for f in created_schema.fields if f.is_primary]
    assert [f.name for f in primary_keys] == ["chunk_id"]
    field_names = {f.name for f in created_schema.fields}
    assert {
        "chunk_id",
        "content_sha256",
        "embedding_text_sha256",
        "embedding_model",
        "vector",
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "index_version",
        "chunker_version",
        "plan_content_sha256",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_start_utf16",
        "canonical_end_utf16",
        "citation_metadata_json",
        "metadata_json",
    } == field_names


@pytest.mark.anyio
async def test_zilliz_writer_collection_mismatch_rejected(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunk = _make_chunk(text="mismatch-test")
    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection="some-other-collection",
            chunks_with_embeddings=[chunk],
            metadata=_make_write_metadata(chunk_count=1),
        )
    assert exc_info.value.retryable is False
    # No SDK call should have been made.
    assert fake_client.upsert_calls == []


@pytest.mark.anyio
async def test_zilliz_writer_empty_chunks_returns_zero(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[],
        metadata=_make_write_metadata(chunk_count=0),
    )
    assert result.upserted_count == 0
    # No SDK call expected.
    assert fake_client.upsert_calls == []


@pytest.mark.anyio
async def test_zilliz_writer_accepts_none_canonical_offsets(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """P2 reviewer fix: when I4A's ``rag_ask_only`` paths (table /
    image_ocr / footnote / code RAG) become wired, the citation
    offsets can legitimately be ``None``.  The writer must accept the
    resulting row, propagating the ``None`` values into the upsert
    payload so the (now nullable) ``canonical_start_utf16`` /
    ``canonical_end_utf16`` columns can hold them."""
    fake_client = _install_pymilvus_stub(monkeypatch, upserted_count=1)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )

    none_offsets_citation = {
        "reading_record_id": str(_RECORD_ID),
        "stable_document_id": str(_STABLE_DOC_ID),
        "base_id": str(_BASE_ID),
        "record_generation": 1,
        "block_ids": [str(uuid4())],
        "unit_ids": [],
        "anchor_segment_ids": [],
        "canonical_text_start_utf16": None,
        "canonical_text_end_utf16": None,
    }
    chunk = _make_chunk(
        text="rag-ask-only", citation=none_offsets_citation
    )

    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[chunk],
        metadata=_make_write_metadata(chunk_count=1),
    )

    assert result.upserted_count == 1
    assert len(fake_client.upsert_calls) == 1
    row = fake_client.upsert_calls[0]["data"][0]
    # The two offset columns are the ones explicitly declared nullable
    # in the Milvus schema; the row MUST carry None through, not be
    # silently coerced to 0 (which would corrupt the citation).
    assert row["canonical_start_utf16"] is None
    assert row["canonical_end_utf16"] is None


# ---------------------------------------------------------------------------
# 4. Payload sanitiser (defence in depth)
# ---------------------------------------------------------------------------


def test_payrow_exact_key_set():
    chunk = _make_chunk(text="defence in depth")
    metadata = _make_write_metadata()
    row = _build_article_rag_upsert_row(
        chunk, metadata=metadata
    )
    expected_keys = {
        "chunk_id",
        "content_sha256",
        "embedding_text_sha256",
        "embedding_model",
        "vector",
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "index_version",
        "chunker_version",
        "plan_content_sha256",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_start_utf16",
        "canonical_end_utf16",
        "citation_metadata_json",
        "metadata_json",
    }
    assert set(row.keys()) == expected_keys
    # Never carries chunk text in any field.
    for key, value in row.items():
        if isinstance(value, str):
            assert "defence in depth" not in value, (
                f"row key {key!r} leaked chunk text"
            )


def test_payrow_denylist_blocks_metadata_text_key():
    chunk = _make_chunk(
        text="safe-text",
        metadata={
            "chunk_kind": "block",
            # The caller tries to smuggle chunk text via metadata;
            # the denylist guard must catch it before the wire.
            "text": "SMUGGLED-CHUNK-TEXT",
        },
    )
    metadata = _make_write_metadata()
    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        _build_article_rag_upsert_row(
            chunk, metadata=metadata
        )
    assert exc_info.value.failure_code in {"vector_payload_leak", "vector_payload_too_large"}


def test_payrow_citation_denylist_silently_drops_extra_keys():
    """Citation dict sanitisation: extra keys (e.g. ``text``) are silently
    DROPPED by the explicit 9-key enumeration — they never reach the
    denylist scan because they never reach the serialised JSON."""
    chunk = _make_chunk(
        text="safe-text",
        citation={
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 0,
            "text": "SMUGGLED-CITATION-TEXT",  # extra key
        },
    )
    metadata = _make_write_metadata()
    row = _build_article_rag_upsert_row(
        chunk, metadata=metadata
    )
    citation_json = json.loads(row["citation_metadata_json"])
    # The "text" key was dropped by the explicit 9-key enumeration.
    assert "text" not in citation_json
    assert "SMUGGLED-CITATION-TEXT" not in row["citation_metadata_json"]
    # All 9 expected keys are still there.
    assert set(citation_json.keys()) == set(_ARTICLE_RAG_CITATION_KEYS)


def test_payrow_citation_drops_unknown_keys():
    """Only the 9 I4C-tested citation keys are persisted.  Extra keys
    on the citation dict are silently dropped — citation sanitisation."""
    chunk = _make_chunk(
        text="safe-text",
        citation={
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [str(uuid4())],
            "unit_ids": [str(uuid4())],
            "anchor_segment_ids": [str(uuid4())],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 5,
            "smuggled_extra_key": "should-not-appear",
        },
    )
    metadata = _make_write_metadata()
    row = _build_article_rag_upsert_row(
        chunk, metadata=metadata
    )
    citation_json = json.loads(row["citation_metadata_json"])
    assert set(citation_json.keys()) == set(_ARTICLE_RAG_CITATION_KEYS)
    assert "smuggled_extra_key" not in citation_json
    # And the literal text 'smuggled-extra-key' should not appear anywhere.
    assert "smuggled_extra_key" not in row["citation_metadata_json"]


def test_payrow_block_ids_serialised_as_json_list():
    block_a, block_b = str(uuid4()), str(uuid4())
    chunk = _make_chunk(
        text="safe-text",
        citation={
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [block_a, block_b],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 0,
        },
    )
    metadata = _make_write_metadata()
    row = _build_article_rag_upsert_row(
        chunk, metadata=metadata
    )
    parsed = json.loads(row["block_ids"])
    assert parsed == [block_a, block_b]


# ---------------------------------------------------------------------------
# 5. Schema builder + constant sanity
# ---------------------------------------------------------------------------


def _pymilvus_available() -> bool:
    """``True`` when ``pymilvus`` is importable.  Cached per process."""
    try:
        __import__("pymilvus")
        return True
    except ImportError:
        return False


def test_schema_dim_must_be_positive():
    with pytest.raises(ZillizArticleRagVectorWriterError):
        _build_article_rag_collection_schema(0)
    with pytest.raises(ZillizArticleRagVectorWriterError):
        _build_article_rag_collection_schema(-1)


def test_schema_field_count_and_types():
    schema = _build_article_rag_collection_schema(1024)
    assert schema["primary_key"] == "chunk_id"
    # One FLOAT_VECTOR field with the configured dim.
    vector_fields = [f for f in schema["fields"] if f["type"] == "FLOAT_VECTOR"]
    assert len(vector_fields) == 1
    assert vector_fields[0]["dim"] == 1024


def test_zilliz_error_inherits_article_rag_index_worker_error():
    """Fix 1: the writer's typed error MUST inherit the worker error class."""
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagIndexWorkerError,
    )

    err = ZillizArticleRagVectorWriterError(
        "synthetic",
        retryable=True,
        failure_class="vector_write",
        failure_code="vector_write_failed",
    )
    assert isinstance(err, ArticleRagIndexWorkerError)
    # Forward compatibility — current typed contract.
    assert isinstance(err, ZillizArticleRagVectorWriterError)


@pytest.mark.anyio
async def test_zilliz_error_message_omits_chunk_text(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """Fix 5: the Zilliz error message MUST NOT include chunk text if
    the SDK echoes it.  This protects worker error JSON from leaking."""
    secret_chunk_text = "SECRET-CHUNK-DO-NOT-LEAK-DO-NOT-LEAK"

    _install_pymilvus_stub_with_raising_upsert(
        monkeypatch,
        exc=RuntimeError(
            f"pymilvus failure echoing input {secret_chunk_text}"
        ),
    )

    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=8,
    )
    chunk = _make_chunk(text=secret_chunk_text)
    metadata = _make_write_metadata(chunk_count=1)

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FAKE_ZILLIZ_COLLECTION,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    err_msg = str(exc_info.value)
    assert secret_chunk_text not in err_msg
    # The chunk_id of the row may also be considered text — make sure
    # the contract surfaces this too.
    assert chunk.chunk_id not in err_msg
    assert "input_count=1" in err_msg
    assert "RuntimeError" in err_msg


@pytest.mark.skipif(
    not _pymilvus_available(),
    reason="pymilvus not installed; skip nullable-schema assertion",
)
def test_real_pymilvus_canonical_offsets_are_nullable_when_installed():
    """P2 reviewer fix: the two ``canonical_*_utf16`` INT64 columns
    must be declared ``nullable=True`` so that ``rag_ask_only`` chunks
    (I4A-permitted sources: table / image_ocr / footnote / code RAG)
    whose citation offsets are ``None`` can be stored without raising
    a pymilvus ``not nullable`` error.

    All other INT64 fields (currently just ``record_generation`` and
    the offset pair) remain non-nullable by default — there is no
    I4A-permitted path that produces a missing record_generation, so
    we do not relax that contract here."""
    pymilvus = __import__("pymilvus")
    from app.services.reader_orchestration.article_rag_vector_store import (
        _build_pymilvus_collection_schema as build_schema,
    )

    schema = build_schema(1024)
    fields_by_name = {f.name: f for f in schema.fields}

    # The two offset columns MUST be nullable.
    assert fields_by_name["canonical_start_utf16"].nullable is True, (
        "canonical_start_utf16 must be nullable=True; I4A permits "
        "rag_ask_only chunks to have None offsets and the writer "
        "must be able to store them as NULL."
    )
    assert fields_by_name["canonical_end_utf16"].nullable is True, (
        "canonical_end_utf16 must be nullable=True for the same "
        "reason as canonical_start_utf16."
    )

    # record_generation is required by I4A — keep it non-nullable.
    assert fields_by_name["record_generation"].nullable is False


@pytest.mark.skipif(
    not _pymilvus_available(),
    reason="pymilvus not installed; skip real schema assertion",
)
def test_real_pymilvus_collection_schema_when_installed():
    """Fix 2: when ``pymilvus`` is installed, ``_build_pymilvus_collection_schema``
    must return a real ``CollectionSchema`` whose ``fields`` list contains
    the expected 19 entries with ``chunk_id`` as the primary key."""
    pymilvus = __import__("pymilvus")
    from app.services.reader_orchestration.article_rag_vector_store import (
        _build_pymilvus_collection_schema as build_schema,
    )

    schema = build_schema(1024)
    # pymilvus exposes ``CollectionSchema`` with ``fields`` and ``description``.
    assert isinstance(schema, pymilvus.CollectionSchema)
    assert len(schema.fields) == 19
    # Primary key must be chunk_id.
    primary_keys = [f.name for f in schema.fields if f.is_primary]
    assert primary_keys == ["chunk_id"]
    # The float vector field is configured with the requested dim.
    vector_fields = [
        f for f in schema.fields if f.dtype == pymilvus.DataType.FLOAT_VECTOR
    ]
    assert len(vector_fields) == 1
    assert vector_fields[0].dim == 1024
    field_names = {f.name for f in schema.fields}
    assert {
        "chunk_id",
        "content_sha256",
        "embedding_text_sha256",
        "embedding_model",
        "vector",
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "index_version",
        "chunker_version",
        "plan_content_sha256",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_start_utf16",
        "canonical_end_utf16",
        "citation_metadata_json",
        "metadata_json",
    } == field_names


def test_zilliz_default_dim_constant_matches_settings_default():
    assert ZILLIZ_DEFAULT_VECTOR_DIM == 1024


def test_zilliz_provider_name_constant():
    assert READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ == "zilliz"


# ---------------------------------------------------------------------------
# 6. I4C invariant compatibility
# ---------------------------------------------------------------------------


def test_unconfigured_writer_raises_unconfigured_code():
    """Match I4C's contract: the unconfigured writer raises the same
    failure_code the worker expects."""
    writer = UnconfiguredArticleRagVectorWriter()
    metadata = _make_write_metadata()
    chunk = _make_chunk(text="t")

    async def _invoke():
        await writer.upsert_chunks(
            collection=_FAKE_ZILLIZ_COLLECTION,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        asyncio.run(_invoke())

    # The unconfigured provider uses ArticleRagIndexWorkerError directly.
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagIndexWorkerError,
    )

    assert isinstance(exc_info.value, ArticleRagIndexWorkerError)
    assert exc_info.value.failure_code == "vector_writer_unconfigured"
    assert exc_info.value.retryable is False


def test_unconfigured_embedding_provider_raises_unconfigured_code():
    """Match I4C: unconfigured embedding surfaces
    ``FAILURE_CODE_EMBEDDING_PROVIDER_UNCONFIGURED``."""
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagIndexWorkerError,
    )

    provider = UnconfiguredArticleRagEmbeddingProvider()

    async def _invoke():
        await provider.embed_texts(["chunk-text"])

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        asyncio.run(_invoke())

    assert isinstance(exc_info.value, ArticleRagIndexWorkerError)
    assert exc_info.value.failure_code == "embedding_provider_unconfigured"
    assert exc_info.value.retryable is False


def test_text_sha256_contract_matches_fake_provider():
    """Sanity: ``_text_sha256`` matches what I4C's FakeArticleRagEmbeddingProvider produces.

    Both compute ``hashlib.sha256(text.encode("utf-8")).hexdigest()`` for
    every input, so the SHA-256 link between chunk + embedding holds.
    """
    from app.services.reader_orchestration.article_rag_index_worker import (
        FakeArticleRagEmbeddingProvider,
    )

    text = "the brown fox jumps over"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _fake_sha_match():
        return await FakeArticleRagEmbeddingProvider().embed_texts([text])

    embeddings = asyncio.run(_fake_sha_match())
    assert embeddings[0].text_sha256 == expected


# ---------------------------------------------------------------------------
# 7. Opt-in smoke skeletons (skipped unless READER_ARTICLE_RAG_SMOKE=1)
# ---------------------------------------------------------------------------


_SMOKE_ENV_VAR = "READER_ARTICLE_RAG_SMOKE"


@pytest.mark.skipif(
    os.environ.get(_SMOKE_ENV_VAR) != "1",
    reason="opt-in smoke skeleton; requires READER_ARTICLE_RAG_SMOKE=1",
)
@pytest.mark.anyio
async def test_real_dashscope_embedding_smoke_is_opt_in_only(monkeypatch: pytest.MonkeyPatch):
    """Opt-in: only runs when ``READER_ARTICLE_RAG_SMOKE=1`` AND a real API key is set.

    Records the call; never asserts payload equality with the cloud.
    """
    real_key = os.environ.get("DASHSCOPE_API_KEY")
    if not real_key:
        pytest.skip("DASHSCOPE_API_KEY not set; skipping real smoke")

    from app.infra import bailian_embedding

    original = bailian_embedding.embed_texts_with_metadata
    call_log: dict[str, Any] = {"called": False}

    async def _wrapped(texts, *, model=None, dimension=None):
        call_log["called"] = True
        call_log["count"] = len(texts)
        return await original(texts, model=model, dimension=dimension)

    monkeypatch.setattr(bailian_embedding, "embed_texts_with_metadata", _wrapped)

    provider = DashScopeArticleRagEmbeddingProvider()
    result = await provider.embed_texts(["hello"])
    assert call_log["called"] is True
    assert call_log["count"] == 1
    assert len(result) == 1
    assert result[0].text_sha256 == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.skipif(
    os.environ.get(_SMOKE_ENV_VAR) != "1",
    reason="opt-in smoke skeleton; requires READER_ARTICLE_RAG_SMOKE=1",
)
@pytest.mark.anyio
async def test_real_zilliz_smoke_is_opt_in_only(monkeypatch: pytest.MonkeyPatch):
    """Opt-in: only runs when ``READER_ARTICLE_RAG_SMOKE=1`` AND a real Zilliz URI/token is set."""
    real_uri = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_URI")
    real_token = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_TOKEN")
    if not real_uri or not real_token:
        pytest.skip(
            "READER_ARTICLE_RAG_ZILLIZ_URI/TOKEN not set; skipping real smoke"
        )

    # Force the real pymilvus module so we don't accidentally use a stub
    # if an earlier test installed one.
    monkeypatch.delitem(sys.modules, "pymilvus", raising=False)
    # Re-import fresh so we hit the real SDK.
    if "pymilvus" in sys.modules:
        del sys.modules["pymilvus"]
    try:
        real_pymilvus = __import__("pymilvus")
    except ImportError:
        pytest.skip("pymilvus not installed")

    writer = ZillizArticleRagVectorWriter(
        uri=real_uri,
        token=real_token,
        collection=os.environ.get("READER_ARTICLE_RAG_ZILLIZ_COLLECTION", "article_rag_index_v1"),
        dim=int(os.environ.get("READER_ARTICLE_RAG_VECTOR_DIM", "1024")),
    )
    # No assertion on payload equality; just verify the SDK handshake works.
    chunk = _make_chunk(text="smoke-chunk")
    metadata = _make_write_metadata()
    result = await writer.upsert_chunks(
        collection=writer.collection,
        chunks_with_embeddings=[chunk],
        metadata=metadata,
    )
    assert isinstance(result, ArticleRagVectorWriteResult)
    assert result.upserted_count >= 0
