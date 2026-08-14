# task-history: (renamed from test_d6_i4d_article_rag_provider_adapters.py)
"""Tests for the Article RAG provider adapter foundation.

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
configured (mirrors ``test_oss_artifact_io.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import traceback
import types
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.article_rag_embedding_provider import (
    READER_ARTICLE_RAG_EMBEDDING_PROVIDER_DASHSCOPE,
    DashScopeArticleRagEmbeddingProvider,
    DashScopeArticleRagEmbeddingProviderError,
    build_default_article_rag_embedding_provider,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagEmbedding,
    ArticleRagVectorChunk,
    ArticleRagVectorWriteMetadata,
    ArticleRagVectorWriteResult,
    UnconfiguredArticleRagEmbeddingProvider,
    UnconfiguredArticleRagVectorWriter,
)
from app.services.reader_orchestration.article_rag_vector_store import (
    _ARTICLE_RAG_CITATION_KEYS,
    READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ,
    ZILLIZ_DEFAULT_VECTOR_DIM,
    ZillizArticleRagVectorWriter,
    ZillizArticleRagVectorWriterError,
    _build_article_rag_collection_schema,
    _build_article_rag_upsert_row,
    build_default_article_rag_vector_writer,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
    pytest.mark.life_characterization,
]

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
_FAKE_ZILLIZ_COLLECTION = "article_rag_chunks"


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
    *, text: str, model: str = "text-embedding-v4", dim: int = 1024
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
    dim: int = 1024,
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
        plan_content_sha256=hashlib.sha256(b"plan").hexdigest(),
        chunk_count=chunk_count,
        # Frozen embedding + vector-space contract fields sourced from
        # the module-level ``ARTICLE_RAG_EMBEDDING_CONTRACT``.  These
        # fixture values match the contract so the writer's contract
        # validation passes without per-test opt-in.
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        embedding_text_type="provider_default",
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
        # diagnostic that excludes both.  The original exception is
        # intentionally discarded; ops diagnosis depends only on the
        # safe structured diagnostics envelope.
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
    # The original exception is intentionally discarded — no cause chain.
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.anyio
async def test_embedding_wrapper_error_traceback_contains_no_sentinel(
    monkeypatch: pytest.MonkeyPatch,
):
    """Safe-return TDD: traceback serialization must not leak sentinels.

    The lower-layer ``EmbeddingError`` message carries hostile sentinels
    (fake API key, fake chunk text, fake URI, fake raw upstream error).
    The adapter MUST raise an outer error whose:

    * ``str(error)`` is sentinel-free
    * ``repr(error)`` is sentinel-free
    * ``traceback.format_exception(error)`` is sentinel-free
    * ``__cause__`` is None (no ``raise ... from exc`` chain)
    * ``__context__`` is None (no implicit except-block chain)
    * structured diagnostics still carry the safe fields

    RED before fix: ``raise ... from exc`` propagates the lower
    ``EmbeddingError`` as ``__cause__``, so ``traceback.format_exception``
    serialises the lower message (with sentinels) into the rendered
    traceback.
    """
    from app.infra import bailian_embedding

    sentinel_api_key = "sk-traceback-leak-api-key-sentinel"
    sentinel_chunk_text = "SENTINEL-TRACEBACK-CHUNK-DO-NOT-LEAK"
    sentinel_uri = "https://traceback-leak-uri.example/path?token=secret"
    sentinel_upstream = "raw upstream SDK message with api_key and uri"

    async def _fake(texts, *, model=None, dimension=None):
        raise bailian_embedding.EmbeddingError(
            f"dashscope failed api_key={sentinel_api_key} "
            f"chunk={sentinel_chunk_text} uri={sentinel_uri} "
            f"upstream={sentinel_upstream}",
            status_code=400,
            provider_code="InvalidParameter",
            retryable=False,
            failed_batch_ordinal=1,
            batch_count=2,
        )

    monkeypatch.setattr(bailian_embedding, "embed_texts_with_metadata", _fake)

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await DashScopeArticleRagEmbeddingProvider().embed_texts([sentinel_chunk_text])

    error = exc_info.value
    sentinels = [sentinel_api_key, sentinel_chunk_text, sentinel_uri, sentinel_upstream]

    # str / repr / traceback must all be sentinel-free.
    for rendered in (str(error), repr(error)):
        for s in sentinels:
            assert s not in rendered, (
                f"sentinel {s!r} leaked into adapter error rendering: {rendered!r}"
            )

    tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb_lines)
    for s in sentinels:
        assert s not in tb_text, (
            f"sentinel {s!r} leaked into traceback.format_exception: {tb_text!r}"
        )

    # No cause / context chain — the lower exception is intentionally discarded.
    assert error.__cause__ is None
    assert error.__context__ is None

    # Safe structured diagnostics still present.
    diag = error.diagnostics
    assert diag.get("provider_status") == 400
    assert diag.get("provider_code") == "InvalidParameter"
    assert diag.get("provider_retryable") is False
    assert diag.get("failed_batch_ordinal") == 1
    assert diag.get("batch_count") == 2


@pytest.mark.anyio
async def test_embedding_diagnostics_rejects_bool_ordinal_and_count(
    monkeypatch: pytest.MonkeyPatch,
):
    """Round 3 TDD: bool MUST NOT be accepted as ordinal/count.

    ``bool`` is a subclass of ``int`` in Python, so the legacy check
    ``isinstance(value, int) and value > 0`` accepts ``True`` (== 1)
    as a valid positive integer.  This is a boundary bug: a future
    caller that constructs ``EmbeddingError(failed_batch_ordinal=True)``
    would surface ``True`` into diagnostics, where it would later be
    JSON-serialised as ``true`` rather than ``1`` — breaking downstream
    consumers that expect an int.

    Verified through the public ``DashScopeArticleRagEmbeddingProvider.
    embed_texts()`` seam only — no direct import of private helpers.
    """
    from app.infra import bailian_embedding

    async def _fake_bool(texts, *, model=None, dimension=None):
        # bool True is technically int(1) and passes ``> 0``.  This
        # MUST be rejected so ``true`` does not leak into diagnostics.
        raise bailian_embedding.EmbeddingError(
            "fake bool ordinal/count leak",
            status_code=400,
            provider_code="InvalidParameter",
            retryable=False,
            failed_batch_ordinal=True,  # type: ignore[arg-type]
            batch_count=True,  # type: ignore[arg-type]
        )

    async def _fake_int(texts, *, model=None, dimension=None):
        # Genuine positive ints MUST still be accepted.
        raise bailian_embedding.EmbeddingError(
            "fake int ordinal/count ok",
            status_code=400,
            provider_code="InvalidParameter",
            retryable=False,
            failed_batch_ordinal=2,
            batch_count=3,
        )

    # --- RED path: bool values must be rejected ---
    monkeypatch.setattr(
        bailian_embedding, "embed_texts_with_metadata", _fake_bool
    )
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await DashScopeArticleRagEmbeddingProvider().embed_texts(["x"])

    diag_bool = exc_info.value.diagnostics
    # provider_status / provider_code / provider_retryable still safe.
    assert diag_bool.get("provider_status") == 400
    assert diag_bool.get("provider_code") == "InvalidParameter"
    assert diag_bool.get("provider_retryable") is False
    # bool ordinal/count MUST NOT appear in diagnostics.
    assert "failed_batch_ordinal" not in diag_bool, (
        f"bool failed_batch_ordinal leaked into diagnostics: {diag_bool!r}"
    )
    assert "batch_count" not in diag_bool, (
        f"bool batch_count leaked into diagnostics: {diag_bool!r}"
    )

    # --- GREEN path: genuine positive ints must still be accepted ---
    monkeypatch.setattr(
        bailian_embedding, "embed_texts_with_metadata", _fake_int
    )
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await DashScopeArticleRagEmbeddingProvider().embed_texts(["x"])

    diag_int = exc_info.value.diagnostics
    assert diag_int.get("failed_batch_ordinal") == 2
    assert diag_int.get("batch_count") == 3


@pytest.mark.anyio
async def test_embedding_wrapper_error_exposes_only_safe_structured_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
):
    """Structured DashScope diagnostics are actionable but never raw SDK text."""
    from app.infra import bailian_embedding

    secret_chunk_text = "SECRET-CHUNK-DO-NOT-LEAK"
    secret_upstream_message = "upstream says api_key=sk-not-for-storage"

    async def _fake(texts, *, model=None, dimension=None):
        raise bailian_embedding.EmbeddingError(
            secret_upstream_message,
            status_code=429,
            provider_code="Throttling.User",
            retryable=True,
            failed_batch_ordinal=2,
            batch_count=3,
        )

    monkeypatch.setattr(bailian_embedding, "embed_texts_with_metadata", _fake)

    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await DashScopeArticleRagEmbeddingProvider().embed_texts([secret_chunk_text])

    error = exc_info.value
    assert error.diagnostics == {
        "provider_status": 429,
        "provider_code": "Throttling.User",
        "provider_retryable": True,
        "failed_batch_ordinal": 2,
        "batch_count": 3,
    }
    serialized = json.dumps({"message": str(error), "diagnostics": error.diagnostics})
    assert secret_chunk_text not in serialized
    assert secret_upstream_message not in serialized
    assert "sk-not-for-storage" not in serialized


@pytest.mark.anyio
async def test_article_rag_provider_sdk_call_raises_sanitized_through_adapter(
    monkeypatch: pytest.MonkeyPatch,
):
    """SDK-raise closure TDD: adapter rewraps SDK-raise as safe typed error.

    The DashScope SDK can fail BEFORE returning a response object —
    e.g. on transport/auth/serialisation errors that surface as a plain
    ``RuntimeError`` carrying sensitive content (API key, chunk text,
    URI, raw upstream error message).  The lower wrapper now closes
    that path (see ``test_embed_texts_with_metadata_sdk_call_raises_is_caught_and_sanitized``
    in ``test_rag_infra.py``); this test verifies the adapter's PUBLIC
    seam converts the wrapper's safe ``EmbeddingError`` into a typed
    ``DashScopeArticleRagEmbeddingProviderError`` whose message,
    traceback, and diagnostics carry no sentinel.

    Mock boundary is the SDK's ``dashscope.TextEmbedding.call`` (the
    same boundary the wrapper test uses).  26 inputs produce 3 batches
    (10/10/6); the raise happens on the first batch.
    """
    sentinel_api_key = "sk-adapter-raise-api-key-sentinel"
    sentinel_chunk_text = "SENTINEL-ADAPTER-RAISE-CHUNK-DO-NOT-LEAK"
    sentinel_uri = "https://adapter-raise-uri.example/path?token=secret"
    sentinel_upstream = "raw upstream SDK message with api_key and uri"

    class RaisingTextEmbedding:
        @staticmethod
        def call(**kwargs):
            raise RuntimeError(
                f"dashscope sdk direct raise api_key={sentinel_api_key} "
                f"chunk={sentinel_chunk_text} uri={sentinel_uri} "
                f"upstream={sentinel_upstream}"
            )

    from app.infra import bailian_embedding

    # Patch the SDK boundary at the wrapper module attribute, and patch
    # resolve_embedding_config so the wrapper does not need real
    # settings/registry wiring.  ``monkeypatch.setattr`` does not parse
    # dotted attribute paths, so we patch ``TextEmbedding`` on the
    # ``bailian_embedding.dashscope`` module object directly.
    monkeypatch.setattr(
        bailian_embedding.dashscope, "TextEmbedding", RaisingTextEmbedding
    )
    monkeypatch.setattr(
        bailian_embedding,
        "resolve_embedding_config",
        lambda: ("text-embedding-v4", 1024, "test-key"),
    )

    texts = [f"chunk-{i}" for i in range(26)]
    with pytest.raises(DashScopeArticleRagEmbeddingProviderError) as exc_info:
        await DashScopeArticleRagEmbeddingProvider().embed_texts(texts)

    err = exc_info.value
    # Safe fixed message — no sentinel interpolation.
    msg = str(err)
    assert "DashScope embedding call failed via bailian_embedding" in msg
    assert "input_count=26" in msg
    assert "EmbeddingError" in msg

    # Retryable from SDK-raise path.
    assert err.retryable is True

    # Safe diagnostics — only bounded fields.  SDK-raise path has no
    # status_code / provider_code, so the adapter MUST NOT include
    # those keys.
    diag = err.diagnostics
    assert diag.get("provider_retryable") is True
    assert diag.get("failed_batch_ordinal") == 1
    assert diag.get("batch_count") == 3
    assert "provider_status" not in diag, (
        f"provider_status MUST NOT appear in SDK-raise diagnostics: {diag!r}"
    )
    assert "provider_code" not in diag, (
        f"provider_code MUST NOT appear in SDK-raise diagnostics: {diag!r}"
    )

    # No exception chain.
    assert err.__cause__ is None
    assert err.__context__ is None

    sentinels = [
        sentinel_api_key,
        sentinel_chunk_text,
        sentinel_uri,
        sentinel_upstream,
    ]

    # Diagnostics serialisation must be sentinel-free.
    diag_serialized = json.dumps(diag, sort_keys=True)
    for s in sentinels:
        assert s not in diag_serialized, (
            f"sentinel {s!r} leaked into diagnostics serialisation: "
            f"{diag_serialized!r}"
        )

    # str / repr / traceback must all be sentinel-free.
    for rendered in (str(err), repr(err)):
        for s in sentinels:
            assert s not in rendered, (
                f"sentinel {s!r} leaked into adapter error rendering: "
                f"{rendered!r}"
            )

    tb_text = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    for s in sentinels:
        assert s not in tb_text, (
            f"sentinel {s!r} leaked into traceback.format_exception: "
            f"{tb_text!r}"
        )


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
    settings = Settings(
        reader_article_rag_embedding_provider="",
        rag_embedding_model_profile="",
        default_model_profile="",
    )
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


def test_embedding_factory_returns_real_provider_when_api_key_resolves(
    monkeypatch: pytest.MonkeyPatch,
):
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

    def __init__(
        self,
        *,
        upserted_count: int | None = None,
        raise_exc: Exception | None = None,
        existing_indexes: list[str] | None = None,
        collection_exists: bool = False,
    ):
        self.has_collection_calls: list[str] = []
        self.create_collection_calls: list[dict[str, Any]] = []
        self.list_indexes_calls: list[dict[str, Any]] = []
        self.create_index_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []
        self.upserted_count_override = upserted_count
        self.raise_exc = raise_exc
        self.existing_indexes = list(existing_indexes or [])
        self.collection_exists = collection_exists

    def has_collection(self, *, collection_name: str) -> bool: # noqa:
        self.has_collection_calls.append(collection_name)
        return self.collection_exists

    def create_collection(self, *, collection_name: str, schema: dict[str, Any]) -> None:
        self.create_collection_calls.append(
            {"collection_name": collection_name, "schema": schema}
        )

    def list_indexes(self, *, collection_name: str, field_name: str) -> list[str]:
        self.list_indexes_calls.append(
            {"collection_name": collection_name, "field_name": field_name}
        )
        return list(self.existing_indexes)

    def prepare_index_params(self):
        return _FakeIndexParams()

    def create_index(self, *, collection_name: str, index_params) -> None:
        self.create_index_calls.append(
            {"collection_name": collection_name, "index_params": list(index_params)}
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
        return {"upsert_count": count}


class _FakeIndexParams(list):
    def add_index(
        self,
        *,
        field_name: str,
        index_type: str = "",
        index_name: str = "",
        **kwargs,
    ):
        self.append(
            {
                "field_name": field_name,
                "index_type": index_type,
                "index_name": index_name,
                **kwargs,
            }
        )


def _install_pymilvus_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    upserted_count: int | None = None,
    raise_exc: Exception | None = None,
    existing_indexes: list[str] | None = None,
    collection_exists: bool = False,
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
        upserted_count=upserted_count,
        raise_exc=raise_exc,
        existing_indexes=existing_indexes,
        collection_exists=collection_exists,
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
            self.list_indexes_calls: list[dict[str, Any]] = []
            self.create_index_calls: list[dict[str, Any]] = []

        def has_collection(self, *, collection_name: str) -> bool:
            self.has_collection_calls.append(collection_name)
            return False

        def create_collection(self, *, collection_name, schema) -> None:
            self.create_collection_calls.append(
                {"collection_name": collection_name, "schema": schema}
            )

        def list_indexes(self, *, collection_name: str, field_name: str) -> list[str]:
            self.list_indexes_calls.append(
                {"collection_name": collection_name, "field_name": field_name}
            )
            return []

        def prepare_index_params(self):
            return _FakeIndexParams()

        def create_index(self, *, collection_name: str, index_params) -> None:
            self.create_index_calls.append(
                {"collection_name": collection_name, "index_params": list(index_params)}
            )

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
    settings = Settings(
        reader_article_rag_vector_provider="",
        reader_article_rag_zilliz_uri="",
        reader_article_rag_zilliz_token="",
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_unconfigured_when_provider_name_blank():
    settings = Settings(
        reader_article_rag_vector_provider="",
        reader_article_rag_zilliz_uri=_FAKE_ZILLIZ_URI,
        reader_article_rag_zilliz_token=_FAKE_ZILLIZ_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=1024,
    )
    writer = build_default_article_rag_vector_writer(settings)
    assert isinstance(writer, UnconfiguredArticleRagVectorWriter)


def test_zilliz_writer_unconfigured_when_token_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        Settings,
        "resolve_external_env_var",
        lambda self, env_name, *, fallback="": fallback,
    )
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


def test_zilliz_writer_falls_back_to_few_shot_zilliz_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """Article RAG may reuse the existing few-shot RAG Zilliz URI/token
    while keeping an Article-specific collection."""

    monkeypatch.delenv("READER_ARTICLE_RAG_ZILLIZ_URI", raising=False)
    monkeypatch.delenv("READER_ARTICLE_RAG_ZILLIZ_TOKEN", raising=False)
    monkeypatch.setenv("ZILLIZ_URI", _FAKE_ZILLIZ_URI)
    monkeypatch.setenv("ZILLIZ_TOKEN", _FAKE_ZILLIZ_TOKEN)

    settings = Settings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri="",
        reader_article_rag_zilliz_token="",
        reader_article_rag_zilliz_collection=_FAKE_ZILLIZ_COLLECTION,
        reader_article_rag_vector_dim=1024,
    )
    writer = build_default_article_rag_vector_writer(settings)

    assert isinstance(writer, ZillizArticleRagVectorWriter)
    assert writer._uri == _FAKE_ZILLIZ_URI
    assert writer._token == _FAKE_ZILLIZ_TOKEN
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
        dim=1024,
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
        dim=1024,
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
    _ = _install_pymilvus_stub(monkeypatch, upserted_count=2)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=1024,
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
        dim=1024,
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
        dim=1024,
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
    assert fake_client.list_indexes_calls == [
        {"collection_name": _FAKE_ZILLIZ_COLLECTION, "field_name": "vector"}
    ]
    assert fake_client.create_index_calls == [
        {
            "collection_name": _FAKE_ZILLIZ_COLLECTION,
            "index_params": [
                {
                    "field_name": "vector",
                    "index_type": "AUTOINDEX",
                    "index_name": "article_rag_vector_autoin",
                    "metric_type": "COSINE",
                }
            ],
        }
    ]
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
async def test_zilliz_writer_repairs_existing_collection_without_vector_index(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """Existing dev collections created before index wiring must be repaired.

    Zilliz Cloud refuses to load a collection with "index not found" when
    the vector field has no index.  The writer therefore checks indexes
    even when the collection already exists.
    """
    fake_client = _install_pymilvus_stub(
        monkeypatch,
        collection_exists=True,
        existing_indexes=[],
    )
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=1024,
    )

    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[_make_chunk(text="legacy collection")],
        metadata=_make_write_metadata(chunk_count=1),
    )

    assert result.upserted_count == 1
    assert fake_client.create_collection_calls == []
    assert fake_client.list_indexes_calls == [
        {"collection_name": _FAKE_ZILLIZ_COLLECTION, "field_name": "vector"}
    ]
    assert fake_client.create_index_calls == [
        {
            "collection_name": _FAKE_ZILLIZ_COLLECTION,
            "index_params": [
                {
                    "field_name": "vector",
                    "index_type": "AUTOINDEX",
                    "index_name": "article_rag_vector_autoin",
                    "metric_type": "COSINE",
                }
            ],
        }
    ]


@pytest.mark.anyio
async def test_zilliz_writer_skips_index_creation_when_vector_index_exists(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(
        monkeypatch,
        collection_exists=True,
        existing_indexes=["article_rag_vector_autoin"],
    )
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=1024,
    )

    result = await writer.upsert_chunks(
        collection=_FAKE_ZILLIZ_COLLECTION,
        chunks_with_embeddings=[_make_chunk(text="indexed collection")],
        metadata=_make_write_metadata(chunk_count=1),
    )

    assert result.upserted_count == 1
    assert fake_client.create_collection_calls == []
    assert fake_client.list_indexes_calls == [
        {"collection_name": _FAKE_ZILLIZ_COLLECTION, "field_name": "vector"}
    ]
    assert fake_client.create_index_calls == []


@pytest.mark.anyio
async def test_zilliz_writer_collection_mismatch_rejected(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=1024,
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
        dim=1024,
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
    """Reviewer fix: when I4A's ``rag_ask_only`` paths (table /
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
        dim=1024,
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
        dim=1024,
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
    """Reviewer fix: the two ``canonical_*_utf16`` INT64 columns
    must be declared ``nullable=True`` so that ``rag_ask_only`` chunks
    (I4A-permitted sources: table / image_ocr / footnote / code RAG)
    whose citation offsets are ``None`` can be stored without raising
    a pymilvus ``not nullable`` error.

    All other INT64 fields (currently just ``record_generation`` and
    the offset pair) remain non-nullable by default — there is no
    I4A-permitted path that produces a missing record_generation, so
    we do not relax that contract here."""
    _ = __import__("pymilvus")
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
    the expected 17 entries with ``chunk_id`` as the primary key."""
    pymilvus = __import__("pymilvus")
    from app.services.reader_orchestration.article_rag_vector_store import (
        _build_pymilvus_collection_schema as build_schema,
    )

    schema = build_schema(1024)
    # pymilvus exposes ``CollectionSchema`` with ``fields`` and ``description``.
    assert isinstance(schema, pymilvus.CollectionSchema)
    assert len(schema.fields) == 17
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
        _ = __import__("pymilvus")
    except ImportError:
        pytest.skip("pymilvus not installed")

    writer = ZillizArticleRagVectorWriter(
        uri=real_uri,
        token=real_token,
        collection=os.environ.get("READER_ARTICLE_RAG_ZILLIZ_COLLECTION", "article_rag_chunks"),
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


# ===================================================================
# ZillizArticleRagVectorWriter defence-in-depth contract
#
# The writer must validate, BEFORE any pymilvus client/network/upsert
# call, that the configured collection / metadata collection / call
# collection all match, that writer dim / metadata dim / chunk dim /
# vector len all match, and that chunk.embedding.model matches
# metadata.embedding_model.  Any mismatch must raise a typed
# ZillizArticleRagVectorWriterError with retryable=False and a
# stable failure_code, and the pymilvus fake client MUST record
# zero upsert calls.
#
# Each test asserts a public-seam contract. Before the production
# fixes they FAIL (RED); after the fixes they PASS (GREEN).
# ===================================================================


# Writer failure codes (must be unique per scenario; exact-match only).
_WRITER_FAILURE_CODE_COLLECTION_MISMATCH = (
    "vector_writer_collection_mismatch"
)
_WRITER_FAILURE_CODE_DIMENSION_MISMATCH = (
    "vector_writer_dimension_mismatch"
)
_WRITER_FAILURE_CODE_MODEL_MISMATCH = "vector_writer_model_mismatch"

# Writer contract metadata mismatch failure code. Used when the
# 4 contract fields (collection, model, dim, text_type) do not match the
# frozen ARTICLE_RAG_EMBEDDING_CONTRACT. Defined here (before the dim
# matrix test) so the parametrize decorator can reference it at module
# import time.
_FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH = (
    "vector_writer_contract_mismatch"
)

# V1 contract literals (must match ARTICLE_RAG_EMBEDDING_CONTRACT
# exactly).  The single-path convergence froze these values; the writer
# validates metadata against the frozen contract before any upsert.
_FROZEN_DOC_EMBEDDING_MODEL = "text-embedding-v4"
_FROZEN_DOC_EMBEDDING_DIM = 1024
_FROZEN_DOC_EMBEDDING_TEXT_TYPE = "provider_default"
_FROZEN_VECTOR_NAMESPACE = "article_rag_chunks"


def _make_embedding(
    *,
    text: str,
    model: str = _FROZEN_DOC_EMBEDDING_MODEL,
    dim: int = _FROZEN_DOC_EMBEDDING_DIM,
    vector_len: int | None = None,
) -> ArticleRagEmbedding:
    """Build a deterministic ArticleRagEmbedding with explicit dim and
    vector_len control ( tests need to be able to set them
    independently to exercise the dim-vs-vector-len mismatch matrix).
    """
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    effective_vec_len = vector_len if vector_len is not None else dim
    return ArticleRagEmbedding(
        text_sha256=text_sha,
        model=model,
        vector=tuple(float(i) / max(effective_vec_len, 1) for i in range(effective_vec_len)),
        dim=dim,
    )


def _make_write_chunk(
    *,
    text: str,
    model: str = _FROZEN_DOC_EMBEDDING_MODEL,
    dim: int = _FROZEN_DOC_EMBEDDING_DIM,
    vector_len: int | None = None,
    chunk_id: str | None = None,
) -> ArticleRagVectorChunk:
    """Build an ArticleRagVectorChunk with explicit model/dim/vector_len."""
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagVectorChunk(
        chunk_id=chunk_id or f"p1g-chunk-{hashlib.sha1(text.encode()).hexdigest()[:8]}",
        content_sha256=content_sha,
        embedding_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        embedding=_make_embedding(text=text, model=model, dim=dim, vector_len=vector_len),
        citation={
            "reading_record_id": str(_RECORD_ID),
            "stable_document_id": str(_STABLE_DOC_ID),
            "base_id": str(_BASE_ID),
            "record_generation": 1,
            "block_ids": [str(uuid4())],
            "unit_ids": [],
            "anchor_segment_ids": [],
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": 12,
        },
        metadata={"chunk_kind": "block", "language": "en"},
    )


def _make_write_metadata(
    *,
    collection: str = _FROZEN_VECTOR_NAMESPACE,
    embedding_model: str = _FROZEN_DOC_EMBEDDING_MODEL,
    embedding_dimension: int = _FROZEN_DOC_EMBEDDING_DIM,
    embedding_text_type: str = _FROZEN_DOC_EMBEDDING_TEXT_TYPE,
    chunk_count: int = 1,
) -> ArticleRagVectorWriteMetadata:
    """Build ArticleRagVectorWriteMetadata with all required fields.

    The production contract requires ``embedding_model``,
    ``embedding_dimension`` and ``embedding_text_type`` to be required
    fields on the dataclass.  This helper provides frozen-contract
    defaults so individual tests can override only the field under test.
    """
    return ArticleRagVectorWriteMetadata(
        collection=collection,
        reading_record_id=_RECORD_ID,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        record_generation=1,
        plan_content_sha256=hashlib.sha256(b"plan").hexdigest(),
        chunk_count=chunk_count,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_text_type=embedding_text_type,
    )


# ---------------------------------------------------------------------
# Scenario 10: configured / call / metadata collection three-way
# mismatch → client/upsert 0 calls.
# ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_writer_call_collection_mismatch_zero_upsert(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: call collection != writer._collection → fail-closed, 0 upserts."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    chunk = _make_write_chunk(text="call-collection-mismatch")
    metadata = _make_write_metadata()

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection="some-other-call-collection",
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code
        == _WRITER_FAILURE_CODE_COLLECTION_MISMATCH
    )
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


@pytest.mark.anyio
async def test_zilliz_writer_metadata_collection_mismatch_zero_upsert(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: metadata.collection != writer._collection → fail-closed, 0 upserts.

    Even when the call collection matches the writer's configured
    collection, a metadata.collection mismatch must fail-closed BEFORE
    any client call.  This is the defence-in-depth check that prevents
    a worker bug from smuggling a wrong-namespace metadata payload
    through the writer.
    """
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    chunk = _make_write_chunk(text="metadata-collection-mismatch")
    # Metadata carries a WRONG collection while call collection matches
    # the writer's configured collection.
    metadata = _make_write_metadata(collection="wrong-metadata-collection")

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code
        == _WRITER_FAILURE_CODE_COLLECTION_MISMATCH
    )
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


# ---------------------------------------------------------------------
# Scenario 11: dim matrix — writer/metadata/chunk/vector-len mismatch
# → client/upsert 0 calls.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "writer_dim,metadata_dim,chunk_dim,chunk_vec_len,expected_failure_code,label",
    [
        # writer vs metadata mismatch — contract check catches metadata
        # dim != frozen contract dim (1024) before the legacy metadata
        # dim check. changed the failure code from
        # vector_writer_dimension_mismatch to
        # vector_writer_contract_mismatch.
        (
            1024, 512, 512, 512,
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "writer_metadata_dim_mismatch",
        ),
        # metadata vs chunk mismatch — metadata dim matches the frozen
        # contract (1024), so contract check passes; per-chunk check
        # catches.
        (
            1024, 1024, 512, 512,
            _WRITER_FAILURE_CODE_DIMENSION_MISMATCH,
            "metadata_chunk_dim_mismatch",
        ),
        # chunk dim vs vector len mismatch (dim correct, vector wrong)
        (
            1024, 1024, 1024, 1023,
            _WRITER_FAILURE_CODE_DIMENSION_MISMATCH,
            "chunk_dim_vs_vector_len_mismatch",
        ),
        # chunk dim wrong, vector len "correct" relative to writer
        (
            1024, 1024, 512, 1024,
            _WRITER_FAILURE_CODE_DIMENSION_MISMATCH,
            "chunk_dim_wrong_vector_len_correct",
        ),
        # bool dim is never a valid dimension — contract check catches
        # metadata bool dim (True != 1024) before the legacy metadata
        # dim check. changed the failure code.
        (
            1024, True, 1024, 1024,
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "metadata_bool_dim",
        ),
        (
            1024, 1024, True, 1024,
            _WRITER_FAILURE_CODE_DIMENSION_MISMATCH,
            "chunk_bool_dim",
        ),
    ],
    ids=[
        "writer_metadata_dim_mismatch",
        "metadata_chunk_dim_mismatch",
        "chunk_dim_vs_vector_len_mismatch",
        "chunk_dim_wrong_vector_len_correct",
        "metadata_bool_dim",
        "chunk_bool_dim",
    ],
)
@pytest.mark.anyio
async def test_zilliz_writer_dim_matrix_zero_upsert(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    writer_dim: int,
    metadata_dim: int,
    chunk_dim: int,
    chunk_vec_len: int,
    expected_failure_code: str,
    label: str,
):
    """RED: any dim mismatch in the writer/metadata/chunk/vector-len
    chain must fail-closed with 0 upsert calls.
    """
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=writer_dim,
    )
    chunk = _make_write_chunk(
        text=f"dim-matrix-{label}",
        dim=chunk_dim,
        vector_len=chunk_vec_len,
    )
    metadata = _make_write_metadata(embedding_dimension=metadata_dim)

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code == expected_failure_code
    ), (
        f"unexpected failure_code for {label}: "
        f"expected {expected_failure_code!r}, "
        f"got {exc_info.value.failure_code!r}"
    )
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


# ---------------------------------------------------------------------
# Scenario 12: chunk.embedding.model != metadata.embedding_model →
# client/upsert 0 calls.
# ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_writer_chunk_model_mismatch_zero_upsert(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: chunk model != metadata model → fail-closed, 0 upserts."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    # Chunk claims a different model than the metadata.
    chunk = _make_write_chunk(text="chunk-model-mismatch", model="wrong-chunk-model")
    metadata = _make_write_metadata()

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code
        == _WRITER_FAILURE_CODE_MODEL_MISMATCH
    )
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


@pytest.mark.anyio
async def test_zilliz_writer_multi_chunk_second_model_mismatch_zero_upsert(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: 2nd chunk model mismatch → fail-closed, 0 upserts (no partial)."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    chunks = [
        _make_write_chunk(text="first-chunk-ok"),
        _make_write_chunk(text="second-chunk-bad-model", model="wrong-model"),
        _make_write_chunk(text="third-chunk-ok"),
    ]
    metadata = _make_write_metadata(chunk_count=3)

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=chunks,
            metadata=metadata,
        )

    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code
        == _WRITER_FAILURE_CODE_MODEL_MISMATCH
    )
    assert fake_client.upsert_calls == []


# ---------------------------------------------------------------------
# Scenario 13: valid V1 metadata + chunks → row built + fake upsert
# succeeds.
# ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_writer_valid_v1_metadata_upserts(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: when all contracts hold, the writer must accept the
    metadata, build rows, and forward to the SDK upsert.
    """
    fake_client = _install_pymilvus_stub(monkeypatch, upserted_count=2)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    chunks = [
        _make_write_chunk(text="valid-chunk-one"),
        _make_write_chunk(text="valid-chunk-two"),
    ]
    metadata = _make_write_metadata(chunk_count=2)

    result = await writer.upsert_chunks(
        collection=_FROZEN_VECTOR_NAMESPACE,
        chunks_with_embeddings=chunks,
        metadata=metadata,
    )

    assert result.collection == _FROZEN_VECTOR_NAMESPACE
    assert result.upserted_count == 2
    assert len(fake_client.upsert_calls) == 1
    assert fake_client.upsert_calls[0]["collection_name"] == _FROZEN_VECTOR_NAMESPACE
    rows = fake_client.upsert_calls[0]["data"]
    assert len(rows) == 2
    # Row carries the embedding_model (already a pre- row field).
    # Defence-in-depth validates chunk.embedding.model ==
    # metadata.embedding_model before this point, so the row value is
    # guaranteed to equal the contract model.
    assert rows[0]["embedding_model"] == _FROZEN_DOC_EMBEDDING_MODEL
    assert rows[1]["embedding_model"] == _FROZEN_DOC_EMBEDDING_MODEL


# ---------------------------------------------------------------------
# Scenario 14: malicious sentinel must NOT appear in str(error),
# repr(error), traceback.format_exception(error).  Also asserts that
# the writer does not echo the malicious model, vector content, or
# chunk text in any error surface.
# ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_writer_sentinel_not_in_error_surface(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
):
    """RED: malicious sentinel values must not appear in any error surface.

    Verifies that a malicious chunk model, vector content, and chunk
    text are NOT echoed in str(error), repr(error), or
    traceback.format_exception(error) when the writer fails closed due
    to a model mismatch.
    """
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )

    sentinel_model = "sk-SENTINEL-MODEL-DO-NOT-LEAK-1234567890abcdef"
    sentinel_text = "SENTINEL-CHUNK-TEXT-DO-NOT-LEAK-0987654321"
    sentinel_vector_marker = 0.1357924680  # unique marker value

    chunk = _make_write_chunk(
        text=sentinel_text,
        model=sentinel_model,
    )
    # Overwrite vector with a marker so we can assert it does not leak.
    chunk = ArticleRagVectorChunk(
        chunk_id=chunk.chunk_id,
        content_sha256=chunk.content_sha256,
        embedding_text_sha256=chunk.embedding_text_sha256,
        embedding=ArticleRagEmbedding(
            text_sha256=chunk.embedding.text_sha256,
            model=sentinel_model,
            vector=tuple([sentinel_vector_marker] * _FROZEN_DOC_EMBEDDING_DIM),
            dim=_FROZEN_DOC_EMBEDDING_DIM,
        ),
        citation=chunk.citation,
        metadata=chunk.metadata,
    )
    metadata = _make_write_metadata()

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    err = exc_info.value
    err_str = str(err)
    err_repr = repr(err)
    err_tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))

    for surface in (err_str, err_repr, err_tb):
        assert sentinel_model not in surface, (
            f"sentinel model leaked into error surface: {surface!r}"
        )
        assert sentinel_text not in surface, (
            f"sentinel chunk text leaked into error surface: {surface!r}"
        )
        assert str(sentinel_vector_marker) not in surface, (
            f"sentinel vector marker leaked into error surface: {surface!r}"
        )

    # Failure code must be the stable model-mismatch label, not a
    # caller-supplied value.
    assert err.failure_code == _WRITER_FAILURE_CODE_MODEL_MISMATCH
    assert err.retryable is False
    assert fake_client.upsert_calls == []


# ===================================================================
# Writer constructor dimension matrix (RED test A)
#
# Verifies the writer constructor explicitly rejects bool dim (which
# Python treats as an int subclass) and never echoes the caller-supplied
# dim value in any error surface.
# ===================================================================


_SENTINEL_INVALID_DIMENSION = "invalid-dimension"
_FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED = "vector_writer_unconfigured"
# _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH is defined earlier
# (near the failure code constants) so the dim matrix parametrize
# can reference it at module import time.


@pytest.mark.parametrize(
    "bad_dim,label",
    [
        (True, "bool_true"),
        (False, "bool_false"),
        (0, "zero"),
        (-1, "negative"),
        (_SENTINEL_INVALID_DIMENSION, "sentinel_string"),
        (None, "none"),
        (1.5, "float"),
    ],
    ids=[
        "bool_true",
        "bool_false",
        "zero",
        "negative",
        "sentinel_string",
        "none",
        "float",
    ],
)
def test_writer_constructor_rejects_invalid_dim(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    bad_dim: Any,
    label: str,
):
    """RED: writer constructor must reject bool/non-int/non-positive dim.

    Bool is a subclass of int in Python, so ``isinstance(True, int)``
    returns True.  The constructor must explicitly reject bool values.
    The sentinel string must NOT appear in any error surface.
    """
    fake_client = _install_pymilvus_stub(monkeypatch)
    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        ZillizArticleRagVectorWriter(
            uri=_FAKE_ZILLIZ_URI,
            token=_FAKE_ZILLIZ_TOKEN,
            collection=_FAKE_ZILLIZ_COLLECTION,
            dim=bad_dim,
        )

    err = exc_info.value
    assert err.retryable is False
    assert err.failure_code == _FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED

    # Sentinel must NOT leak into any error surface.
    err_str = str(err)
    err_repr = repr(err)
    err_args = repr(err.args)
    err_tb = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    for surface in (err_str, err_repr, err_args, err_tb):
        assert _SENTINEL_INVALID_DIMENSION not in surface, (
            f"sentinel dim leaked into error surface ({label}): "
            f"{surface!r}"
        )

    # No pymilvus client constructed.
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []
    assert fake_client.upsert_calls == []


def test_writer_constructor_accepts_positive_int_dim(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
):
    """Positive characterization: dim=1024 must still construct OK.

    This is NOT a RED test — it verifies the constructor still accepts
    valid positive int dim values after the bool-rejection fix.
    """
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FAKE_ZILLIZ_COLLECTION,
        dim=1024,
    )
    assert writer.collection == _FAKE_ZILLIZ_COLLECTION
    # No client constructed yet (lazy).
    assert fake_client.has_collection_calls == []


# ===================================================================
# Empty batch metadata validation matrix (RED test B)
#
# Verifies that an empty chunks list does NOT skip metadata validation.
# Invalid metadata must fail-closed even when chunks_with_embeddings=[].
# ===================================================================


def _make_valid_contract_metadata() -> ArticleRagVectorWriteMetadata:
    """Build contract-matching metadata for the empty-batch tests."""
    return _make_write_metadata(chunk_count=0)


@pytest.mark.parametrize(
    "metadata_kwarg,failure_code,label",
    [
        # dim mismatch with frozen contract (writer=1024, metadata=512).
        # Contract check (step 2) fires before existing metadata dim
        # check (step 3).
        (
            {"embedding_dimension": 512},
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "dim_mismatch",
        ),
        # bool dim — contract check catches True != 1024 first.
        (
            {"embedding_dimension": True},
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "bool_dim",
        ),
        # non-str model — contract check catches 12345 !=
        # "text-embedding-v4" first.
        (
            {"embedding_model": 12345},
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "model_non_str",
        ),
        # empty model — contract check catches "" !=
        # "text-embedding-v4" first.
        (
            {"embedding_model": ""},
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "model_empty",
        ),
        # non-contract text_type — contract check catches mismatch.
        (
            {"embedding_text_type": "malicious-text-type"},
            _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            "text_type_non_v1",
        ),
        # collection mismatch — existing 3-way collection identity
        # check (step 1) fires first.
        (
            {"collection": "wrong-metadata-collection"},
            "vector_writer_collection_mismatch",
            "collection_mismatch",
        ),
    ],
    ids=[
        "dim_mismatch",
        "bool_dim",
        "model_non_str",
        "model_empty",
        "text_type_non_v1",
        "collection_mismatch",
    ],
)
@pytest.mark.anyio
async def test_empty_batch_invalid_metadata_fails_closed(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    metadata_kwarg: dict[str, Any],
    failure_code: str,
    label: str,
):
    """RED: invalid metadata must fail-closed even when batch is empty."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    metadata = _make_valid_contract_metadata()
    # Apply the invalid override.
    metadata = dataclass_replace(metadata, **metadata_kwarg)

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[],
            metadata=metadata,
        )

    err = exc_info.value
    assert err.retryable is False
    assert err.failure_code == failure_code, (
        f"unexpected failure_code for {label}: "
        f"expected {failure_code!r}, got {err.failure_code!r}"
    )
    # No client / network / upsert call.
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


@pytest.mark.anyio
async def test_empty_batch_valid_metadata_returns_zero(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
):
    """RED: valid contract-matching empty batch returns upserted_count=0."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    metadata = _make_valid_contract_metadata()

    result = await writer.upsert_chunks(
        collection=_FROZEN_VECTOR_NAMESPACE,
        chunks_with_embeddings=[],
        metadata=metadata,
    )

    assert result.upserted_count == 0
    assert result.collection == _FROZEN_VECTOR_NAMESPACE
    # No client / upsert call for empty batch.
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


# ===================================================================
# Writer contract metadata 4-field validation (RED test C)
#
# Verifies the writer validates all 4 contract fields (collection,
# model, dim, text_type) against the frozen ARTICLE_RAG_EMBEDDING_CONTRACT.
# ===================================================================


@pytest.mark.parametrize(
    "field,value,label",
    [
        # collection mismatch is caught by existing 3-way collection identity
        # check (step 1) before the contract check (step 2) — writer is
        # constructed with collection == contract.vector_collection, so any
        # metadata.collection override that differs from writer collection
        # also differs from contract.vector_collection.
        ("collection", "wrong-collection-name", "collection_mismatch"),
        ("embedding_model", "wrong-embedding-model", "model_mismatch"),
        ("embedding_dimension", 768, "dimension_mismatch"),
        ("embedding_text_type", "wrong_text_type", "text_type_mismatch"),
    ],
    ids=[
        "collection_mismatch",
        "model_mismatch",
        "dimension_mismatch",
        "text_type_mismatch",
    ],
)
@pytest.mark.anyio
async def test_writer_contract_metadata_4_field_validation(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
    label: str,
):
    """RED: each of the 4 contract fields must match the frozen contract."""
    fake_client = _install_pymilvus_stub(monkeypatch)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    metadata = _make_write_metadata(chunk_count=1)
    # Override one field with a wrong value.
    metadata = dataclass_replace(metadata, **{field: value})
    chunk = _make_write_chunk(text=f"contract-4-field-{label}")

    with pytest.raises(ZillizArticleRagVectorWriterError) as exc_info:
        await writer.upsert_chunks(
            collection=_FROZEN_VECTOR_NAMESPACE,
            chunks_with_embeddings=[chunk],
            metadata=metadata,
        )

    err = exc_info.value
    assert err.retryable is False
    # For collection_mismatch, the existing 3-way collection identity check
    # (step 1) fires first with vector_writer_collection_mismatch.  For all
    # other fields, the contract check (step 2) fires first with
    # vector_writer_contract_mismatch.
    if label == "collection_mismatch":
        assert err.failure_code == "vector_writer_collection_mismatch", (
            f"unexpected failure_code for {label}: "
            f"expected 'vector_writer_collection_mismatch', "
            f"got {err.failure_code!r}"
        )
    else:
        assert err.failure_code == _FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH, (
            f"unexpected failure_code for {label}: "
            f"expected {_FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH!r}, "
            f"got {err.failure_code!r}"
        )
    # No client / upsert call.
    assert fake_client.upsert_calls == []
    assert fake_client.has_collection_calls == []
    assert fake_client.create_collection_calls == []


@pytest.mark.anyio
async def test_writer_valid_contract_metadata_passes(
    _pymilvus_clean: None,
    monkeypatch: pytest.MonkeyPatch,
):
    """Positive characterization: valid contract metadata succeeds."""
    fake_client = _install_pymilvus_stub(monkeypatch, upserted_count=1)
    writer = ZillizArticleRagVectorWriter(
        uri=_FAKE_ZILLIZ_URI,
        token=_FAKE_ZILLIZ_TOKEN,
        collection=_FROZEN_VECTOR_NAMESPACE,
        dim=_FROZEN_DOC_EMBEDDING_DIM,
    )
    metadata = _make_write_metadata(chunk_count=1)
    chunk = _make_write_chunk(text="valid-contract-metadata")

    result = await writer.upsert_chunks(
        collection=_FROZEN_VECTOR_NAMESPACE,
        chunks_with_embeddings=[chunk],
        metadata=metadata,
    )

    assert result.upserted_count == 1
    assert result.collection == _FROZEN_VECTOR_NAMESPACE
    assert len(fake_client.upsert_calls) == 1
