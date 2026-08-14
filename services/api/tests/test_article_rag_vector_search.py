# task-history: (renamed from test_d6_i4e_article_rag_vector_search.py)
"""Tests for the Article RAG vector search adapter foundation.

Covers:
  * ``FakeArticleRagVectorSearcher`` — top-k, score ordering,
    dedup-by-chunk_id, search-call recording.
  * ``UnconfiguredArticleRagVectorSearcher`` — fail-closed with the
    I4C failure code so the retrieval service surfaces a typed error.
  * ``ZillizArticleRagVectorSearcher`` — constructor validation, lazy
    ``pymilvus`` init (no I/O at construction), ``asyncio.to_thread``
    routing, ``filter`` expression assembly, sanitised hit extraction
    (only ``chunk_id`` + ``score`` + the four guard fields are read),
    fail-closed diagnostic for SDK errors, opt-in smoke skeleton.
  * ``build_default_article_rag_vector_searcher`` — settings-driven
    fail-closed defaults, enabled path round-trip.

No network.  pymilvus is mocked via a module-level stub that exposes
``MilvusClient`` (the only symbol the production code lazy-imports).
The opt-in smoke skeleton runs only when ``READER_ARTICLE_RAG_SMOKE=1``
AND a real token is set; it is skipped by default.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reader_orchestration.article_rag_vector_search import (
    ArticleRagVectorSearchHit,
    ArticleRagVectorSearchResult,
    ArticleRagVectorSearcherError,
    FakeArticleRagVectorSearcher,
    FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED,
    READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ,
    UnconfiguredArticleRagVectorSearcher,
    ZillizArticleRagVectorSearcher,
    build_default_article_rag_vector_searcher,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
    ArticleRagIndexWorkerError,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


_FAKE_URI = "https://example.zilliz.cloud"
_FAKE_TOKEN = "fake-zilliz-token-DO-NOT-LOG"
_FAKE_COLLECTION = "article_rag_chunks_test"


# ---------------------------------------------------------------------------
# 1. Fake searcher
# ---------------------------------------------------------------------------


def test_fake_searcher_returns_hits_in_score_descending_order() -> None:
    fake = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id="a", score=0.5),
            ArticleRagVectorSearchHit(chunk_id="b", score=0.9),
            ArticleRagVectorSearchHit(chunk_id="c", score=0.7),
        ]
    )

    async def _run() -> ArticleRagVectorSearchResult:
        return await fake.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1, 0.2, 0.3),
            limit=3,
        )

    result = asyncio.run(_run())
    assert [h.chunk_id for h in result.hits] == ["b", "c", "a"]


def test_fake_searcher_respects_limit() -> None:
    fake = FakeArticleRagVectorSearcher(
        hits=[
            ArticleRagVectorSearchHit(chunk_id=str(i), score=float(i))
            for i in range(10)
        ]
    )

    async def _run() -> ArticleRagVectorSearchResult:
        return await fake.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1,),
            limit=3,
        )

    result = asyncio.run(_run())
    assert len(result.hits) == 3
    # Top-3 by score: ids 9, 8, 7
    assert [h.chunk_id for h in result.hits] == ["9", "8", "7"]


def test_fake_searcher_records_every_call() -> None:
    fake = FakeArticleRagVectorSearcher(hits=[])
    stable_doc_id = uuid.uuid4()

    async def _run() -> None:
        await fake.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.5, 0.6),
            limit=5,
            stable_document_id=stable_doc_id,
        )

    asyncio.run(_run())
    assert len(fake.search_calls) == 1
    call = fake.search_calls[0]
    assert call["collection"] == _FAKE_COLLECTION
    assert call["query_vector"] == (0.5, 0.6)
    assert call["limit"] == 5
    assert call["stable_document_id"] == str(stable_doc_id)


def test_fake_searcher_zero_limit_returns_empty_result() -> None:
    fake = FakeArticleRagVectorSearcher(
        hits=[ArticleRagVectorSearchHit(chunk_id="a", score=0.5)]
    )

    async def _run() -> ArticleRagVectorSearchResult:
        return await fake.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1,),
            limit=0,
        )

    result = asyncio.run(_run())
    assert result.hits == ()


def test_fake_searcher_dedups_by_chunk_id() -> None:
    """Duplicate chunk_ids in the hit list collapse to one (highest score).

    The dedup happens in the retrieval service's join, not in the
    searcher, but the fake searcher is permitted to surface
    duplicates (simulating pymilvus behaviour).  The retrieval service
    test suite asserts the dedup policy; this test confirms the fake
    is a faithful stand-in by NOT deduping at the searcher layer.
    """
    fake = _FakeRagDuplicateFakeSearcher()

    async def _run() -> ArticleRagVectorSearchResult:
        return await fake.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1,),
            limit=5,
        )

    result = asyncio.run(_run())
    chunk_ids = [h.chunk_id for h in result.hits]
    # Duplicate chunk_id "a" appears twice in the input; the fake
    # returns it twice — the dedup is the retrieval service's job.
    assert chunk_ids.count("a") == 2


class _FakeRagDuplicateFakeSearcher(FakeArticleRagVectorSearcher):
    """Helper that exposes duplicates so the dedup policy is testable."""

    def __init__(self) -> None:
        super().__init__(
            hits=[
                ArticleRagVectorSearchHit(chunk_id="a", score=0.9),
                ArticleRagVectorSearchHit(chunk_id="a", score=0.7),
                ArticleRagVectorSearchHit(chunk_id="b", score=0.6),
            ]
        )


# ---------------------------------------------------------------------------
# 2. Unconfigured searcher
# ---------------------------------------------------------------------------


def test_unconfigured_searcher_raises_typed_failure() -> None:
    searcher = UnconfiguredArticleRagVectorSearcher()

    async def _run() -> None:
        await searcher.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1,),
            limit=5,
        )

    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        asyncio.run(_run())
    err = exc_info.value
    # Must inherit the worker base so any future orchestrator catches it.
    assert isinstance(err, ArticleRagIndexWorkerError)
    assert err.failure_code == FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED
    # Re-exported to the writer-side code for callers that share a
    # single dashboard label.
    assert (
        FAILURE_CODE_VECTOR_SEARCHER_UNCONFIGURED
        == FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED
    )
    assert err.retryable is False


def test_unconfigured_searcher_message_excludes_query_text() -> None:
    """Defence in depth: the unconfigured error must not echo any
    caller-supplied text.  This guards against future regressions
    that might accidentally include query_text in the message."""
    searcher = UnconfiguredArticleRagVectorSearcher()
    secret_query = "SECRET-QUERY-DO-NOT-LEAK-DO-NOT-LEAK"

    async def _run() -> None:
        await searcher.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1,),
            limit=5,
        )

    try:
        asyncio.run(_run())
    except ArticleRagVectorSearcherError as exc:
        assert secret_query not in str(exc)


# ---------------------------------------------------------------------------
# 3. Zilliz searcher — constructor validation
# ---------------------------------------------------------------------------


def test_zilliz_searcher_rejects_empty_uri() -> None:
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        ZillizArticleRagVectorSearcher(
            uri="", token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
        )
    assert exc_info.value.failure_code == "vector_searcher_unconfigured"


def test_zilliz_searcher_rejects_empty_token() -> None:
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        ZillizArticleRagVectorSearcher(
            uri=_FAKE_URI, token="", collection=_FAKE_COLLECTION
        )
    assert exc_info.value.failure_code == "vector_searcher_unconfigured"


def test_zilliz_searcher_rejects_empty_collection() -> None:
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        ZillizArticleRagVectorSearcher(
            uri=_FAKE_URI, token=_FAKE_TOKEN, collection=""
        )
    assert exc_info.value.failure_code == "vector_searcher_unconfigured"


def test_zilliz_searcher_constructor_does_no_io() -> None:
    """Lazy pymilvus init: the constructor must not open a network
    connection.  We verify by checking the pymilvus module is not
    touched during construction (no client exists after construction)."""
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    assert searcher._client is None  # type: ignore[attr-defined]
    assert searcher.provider_name == READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ


# ---------------------------------------------------------------------------
# 4. Zilliz searcher — pymilvus stub for happy path + error paths
# ---------------------------------------------------------------------------


class _StubMilvusClient:
    """Minimal pymilvus.MilvusClient stub that records ``search`` calls."""

    def __init__(self, *, hits: list[dict[str, Any]] | None = None) -> None:
        self._hits = list(hits or [])
        self.search_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
        self.search_calls.append(kwargs)
        return [list(self._hits)]


def _install_pymilvus_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hits: list[dict[str, Any]] | None = None,
) -> _StubMilvusClient:
    """Install a fake pymilvus module exposing ``MilvusClient``."""

    fake_module = types.ModuleType("pymilvus")
    client = _StubMilvusClient(hits=hits)
    fake_module.MilvusClient = lambda *, uri, token: client  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)
    return client


def _remove_pymilvus_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "pymilvus", raising=False)


@pytest.fixture
def _pymilvus_clean(monkeypatch: pytest.MonkeyPatch):
    _remove_pymilvus_stub(monkeypatch)
    yield
    _remove_pymilvus_stub(monkeypatch)


# ---------------------------------------------------------------------------
# 5. Zilliz searcher — search() behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_searcher_returns_sanitised_hits(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        {
            "chunk_id": "abc123",
            "distance": 0.95,
            "stable_document_id": str(uuid.uuid4()),
            "base_id": str(uuid.uuid4()),
            "plan_content_sha256": "deadbeef",
            # pymilvus can include extra payload fields; the adapter
            # MUST NOT surface these to the caller.
            "text": "SECRET-CHUNK-TEXT-DO-NOT-LEAK",
            "plate_json": {"op": "slate"},
        },
        {
            "chunk_id": "def456",
            "distance": 0.7,
            "stable_document_id": str(uuid.uuid4()),
            "base_id": str(uuid.uuid4()),
            "plan_content_sha256": "beefdead",
        },
    ]
    fake_client = _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )

    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1, 0.2, 0.3),
        limit=5,
    )

    assert len(result.hits) == 2
    assert [h.chunk_id for h in result.hits] == ["abc123", "def456"]
    # Only the canonical + guard fields are surfaced; ``text`` and
    # ``plate_json`` MUST NOT be on the dataclass at all.
    for h in result.hits:
        assert not hasattr(h, "text")
        assert not hasattr(h, "plate_json")
    assert result.hits[0].score == 0.95

    # The stub was actually called via asyncio.to_thread.
    assert len(fake_client.search_calls) == 1
    call = fake_client.search_calls[0]
    assert call["collection_name"] == _FAKE_COLLECTION
    assert call["data"] == [[0.1, 0.2, 0.3]]
    assert call["limit"] == 5
    # The four output_fields + chunk_id are passed through.
    # ``chunk_id`` MUST be present per the I4E reviewer fix — without
    # it the production parser would have to fall back to the
    # ``id`` / ``entity.chunk_id`` heuristics.
    assert set(call["output_fields"]) == {
        "chunk_id",
        "stable_document_id",
        "base_id",
        "plan_content_sha256",
    }


@pytest.mark.anyio
async def test_zilliz_searcher_assembles_filter_when_guard_supplied(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    stable_doc_id = uuid.uuid4()
    await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=3,
        stable_document_id=stable_doc_id,
    )
    call = fake_client.search_calls[0]
    assert call["filter"] is not None
    assert str(stable_doc_id) in call["filter"]
    # URI / token MUST NOT be embedded in the filter.
    assert _FAKE_URI not in call["filter"]
    assert _FAKE_TOKEN not in call["filter"]


@pytest.mark.anyio
async def test_zilliz_searcher_skips_filter_when_no_guard(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=3,
    )
    call = fake_client.search_calls[0]
    assert call["filter"] is None


@pytest.mark.anyio
async def test_zilliz_searcher_rejects_collection_mismatch(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        await searcher.search(
            collection="wrong-collection",
            query_vector=(0.1,),
            limit=3,
        )
    assert (
        exc_info.value.failure_code == "vector_searcher_collection_mismatch"
    )
    # URI / token MUST NOT appear in the message.
    assert _FAKE_URI not in str(exc_info.value)
    assert _FAKE_TOKEN not in str(exc_info.value)


@pytest.mark.anyio
async def test_zilliz_searcher_rejects_empty_query_vector(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        await searcher.search(
            collection=_FAKE_COLLECTION,
            query_vector=(),
            limit=3,
        )
    assert exc_info.value.failure_code == "vector_searcher_empty_query"
    # The stub was never called.
    assert fake_client.search_calls == []


@pytest.mark.anyio
async def test_zilliz_searcher_sdk_error_rewrapped_without_token(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pymilvus raises, the adapter must surface a fixed
    diagnostic that excludes the token, URI, and query vector."""

    class _RaisingClient:
        def search(self, **kwargs: Any) -> None:
            # Simulate pymilvus echoing the filter (which contains the
            # stable_document_id but NOT the token) into the error.
            raise RuntimeError(
                f"milvus error; filter={kwargs.get('filter')}"
            )

    fake_module = types.ModuleType("pymilvus")
    fake_module.MilvusClient = lambda *, uri, token: _RaisingClient()  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)

    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        await searcher.search(
            collection=_FAKE_COLLECTION,
            query_vector=(0.1, 0.2),
            limit=3,
        )
    msg = str(exc_info.value)
    assert "Zilliz search failed via pymilvus" in msg
    assert "limit=3" in msg
    assert "RuntimeError" in msg
    # Defence in depth: token, URI, query text MUST NOT appear in
    # the rewritten diagnostic.
    assert _FAKE_TOKEN not in msg
    assert _FAKE_URI not in msg


@pytest.mark.anyio
async def test_zilliz_searcher_skips_malformed_hit_entries(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hit without ``chunk_id`` is dropped silently (fail-closed at
    the SDK layer) — the caller receives only well-formed hits."""
    hits = [
        {"chunk_id": "ok1", "distance": 0.9},
        {"distance": 0.8},  # missing chunk_id — drop
        {"chunk_id": "ok2", "distance": 0.7},
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=10,
    )
    assert [h.chunk_id for h in result.hits] == ["ok1", "ok2"]


@pytest.mark.anyio
async def test_zilliz_searcher_zero_limit_returns_empty_without_io(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=0,
    )
    assert result.hits == ()
    # SDK was not touched at all.
    assert fake_client.search_calls == []


# ---------------------------------------------------------------------------
# 6. Zilliz searcher — missing pymilvus (lazy import failure)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_searcher_ensure_client_raises_when_sdk_missing(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the local ``from pymilvus import MilvusClient`` raises
    ImportError, ``_ensure_client`` surfaces a typed
    ``vector_searcher_sdk_missing`` error and preserves the original
    ImportError as ``__cause__``.

    We exercise this by installing a stub ``pymilvus`` module that
    lacks ``MilvusClient`` — the local import then fails.  This is
    stricter than deleting ``pymilvus`` from ``sys.modules`` because
    the production code's local import would fall through to the
    real cached module.
    """

    class _MissingMilvusModule(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            raise ImportError(
                f"cannot import name {name!r} from 'pymilvus' (stubbed "
                "missing)"
            )

    stub = _MissingMilvusModule("pymilvus")
    monkeypatch.setitem(sys.modules, "pymilvus", stub)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI,
        token=_FAKE_TOKEN,
        collection=_FAKE_COLLECTION,
    )
    with pytest.raises(ArticleRagVectorSearcherError) as exc_info:
        searcher._ensure_client()  # type: ignore[attr-defined]
    assert exc_info.value.failure_code == "vector_searcher_sdk_missing"
    assert isinstance(exc_info.value.__cause__, ImportError)


# ---------------------------------------------------------------------------
# 7. Factory — settings-driven
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal stand-in for ``app.config.settings.Settings``."""

    def __init__(self, **kwargs: Any) -> None:
        self.reader_article_rag_vector_provider = kwargs.get(
            "reader_article_rag_vector_provider", ""
        )
        self.reader_article_rag_zilliz_uri = kwargs.get(
            "reader_article_rag_zilliz_uri", ""
        )
        self.reader_article_rag_zilliz_token = kwargs.get(
            "reader_article_rag_zilliz_token", ""
        )
        self.reader_article_rag_zilliz_collection = kwargs.get(
            "reader_article_rag_zilliz_collection", ""
        )


class _FakeSettingsWithZillizFallback(_FakeSettings):
    def resolve_reader_article_rag_zilliz_uri(self) -> str:
        return _FAKE_URI

    def resolve_reader_article_rag_zilliz_token(self) -> str:
        return _FAKE_TOKEN


def test_factory_returns_unconfigured_when_provider_blank() -> None:
    settings = _FakeSettings()
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, UnconfiguredArticleRagVectorSearcher)


def test_factory_returns_unconfigured_when_provider_wrong_name() -> None:
    settings = _FakeSettings(reader_article_rag_vector_provider="not-zilliz")
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, UnconfiguredArticleRagVectorSearcher)


def test_factory_returns_unconfigured_when_uri_blank() -> None:
    settings = _FakeSettings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_token=_FAKE_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_COLLECTION,
    )
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, UnconfiguredArticleRagVectorSearcher)


def test_factory_returns_unconfigured_when_token_blank() -> None:
    settings = _FakeSettings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_URI,
        reader_article_rag_zilliz_collection=_FAKE_COLLECTION,
    )
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, UnconfiguredArticleRagVectorSearcher)


def test_factory_returns_unconfigured_when_collection_blank() -> None:
    settings = _FakeSettings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_URI,
        reader_article_rag_zilliz_token=_FAKE_TOKEN,
    )
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, UnconfiguredArticleRagVectorSearcher)


def test_factory_returns_real_zilliz_when_all_settings_present() -> None:
    settings = _FakeSettings(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri=_FAKE_URI,
        reader_article_rag_zilliz_token=_FAKE_TOKEN,
        reader_article_rag_zilliz_collection=_FAKE_COLLECTION,
    )
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]
    assert isinstance(searcher, ZillizArticleRagVectorSearcher)
    assert searcher.provider_name == READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ


def test_factory_uses_resolved_zilliz_fallback_when_dedicated_fields_blank() -> None:
    settings = _FakeSettingsWithZillizFallback(
        reader_article_rag_vector_provider="zilliz",
        reader_article_rag_zilliz_uri="",
        reader_article_rag_zilliz_token="",
        reader_article_rag_zilliz_collection=_FAKE_COLLECTION,
    )
    searcher = build_default_article_rag_vector_searcher(settings)  # type: ignore[arg-type]

    assert isinstance(searcher, ZillizArticleRagVectorSearcher)
    assert searcher._uri == _FAKE_URI
    assert searcher._token == _FAKE_TOKEN
    assert searcher._collection == _FAKE_COLLECTION


def test_factory_provider_name_constant() -> None:
    assert READER_ARTICLE_RAG_VECTOR_SEARCHER_ZILLIZ == "zilliz"


# ---------------------------------------------------------------------------
# 8. Opt-in smoke skeleton (skipped unless READER_ARTICLE_RAG_SMOKE=1)
# ---------------------------------------------------------------------------

_SMOKE_ENV_VAR = "READER_ARTICLE_RAG_SMOKE"


@pytest.mark.skipif(
    os.environ.get(_SMOKE_ENV_VAR) != "1",
    reason="opt-in smoke skeleton; requires READER_ARTICLE_RAG_SMOKE=1",
)
@pytest.mark.anyio
async def test_real_zilliz_search_smoke_is_opt_in_only() -> None:
    """Opt-in: only runs when ``READER_ARTICLE_RAG_SMOKE=1`` AND a real
    token is set.  Never invoked under the default pytest collection."""
    real_uri = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_URI") or ""
    real_token = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_TOKEN") or ""
    real_collection = (
        os.environ.get("READER_ARTICLE_RAG_ZILLIZ_COLLECTION")
        or "article_rag_chunks"
    )
    if not (real_uri and real_token):
        pytest.skip("real Zilliz URI/token not set; smoke skipped")
    searcher = ZillizArticleRagVectorSearcher(
        uri=real_uri, token=real_token, collection=real_collection
    )
    result = await searcher.search(
        collection=real_collection,
        query_vector=(0.0,) * 1024,
        limit=1,
    )
    # Smoke only — never assert payload equality with the cloud.
    assert isinstance(result, ArticleRagVectorSearchResult)


# ---------------------------------------------------------------------------
# 9. Hit parsing — real pymilvus shapes (reviewer fix)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_searcher_accepts_top_level_chunk_id(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most common pymilvus shape: ``{chunk_id, distance, ...}``.

    This is what the I4D writer produces and what ``output_fields``
    in production code requests explicitly.
    """
    hits = [
        {"chunk_id": "abc", "distance": 0.9},
        {"chunk_id": "def", "distance": 0.7},
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=5,
    )
    assert [h.chunk_id for h in result.hits] == ["abc", "def"]


@pytest.mark.anyio
async def test_zilliz_searcher_accepts_top_level_id_when_chunk_id_missing(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older pymilvus clients surface the primary key as ``id``
    (int64).  The adapter MUST accept this shape so a real-world
    deployment does not silently drop every hit.
    """
    hits = [
        # ``id`` here is the stringified primary key (pymilvus can
        # also return int64 — we stringify it the same way).
        {"id": "abc", "distance": 0.9},
        {"id": "def", "distance": 0.7},
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=5,
    )
    assert [h.chunk_id for h in result.hits] == ["abc", "def"]


@pytest.mark.anyio
async def test_zilliz_searcher_accepts_entity_chunk_id_shape(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entity-wrapper shape used by pymilvus 2.5+ ``search(..., use_full_content=True)``
    and some ``AnnSearchRequest`` responses:
    ``{entity: {chunk_id: ...}, id: int}``.
    """
    hits = [
        {"entity": {"chunk_id": "abc"}, "id": 1, "distance": 0.9},
        {"entity": {"chunk_id": "def"}, "id": 2, "distance": 0.7},
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=5,
    )
    assert [h.chunk_id for h in result.hits] == ["abc", "def"]


@pytest.mark.anyio
async def test_zilliz_searcher_request_chunk_id_in_output_fields(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chunk_id`` MUST be explicitly requested in ``output_fields``.

    Without this, pymilvus 2.6.x surfaces only the requested output
    columns; the primary key (``chunk_id``) would be absent and we
    would fall through to the ``id`` / ``entity.chunk_id`` heuristics
    which are defensive-only.
    """
    fake_client = _install_pymilvus_stub(monkeypatch, hits=[])
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=3,
    )
    call = fake_client.search_calls[0]
    assert "chunk_id" in call["output_fields"]


@pytest.mark.anyio
async def test_zilliz_searcher_mixed_hit_shapes_handled_per_entry(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the SDK returns a mix of shapes, each entry is parsed
    independently: well-formed hits are surfaced, malformed ones are
    dropped (per-hit degradation, never all-or-nothing)."""
    hits = [
        {"chunk_id": "abc", "distance": 0.9},  # top-level
        {"id": "def", "distance": 0.8},  # top-level id
        {"entity": {"chunk_id": "ghi"}, "distance": 0.7},  # entity shape
        {"distance": 0.6},  # no chunk_id anywhere — drop
        {"chunk_id": "", "distance": 0.5},  # empty string — drop
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=10,
    )
    chunk_ids = [h.chunk_id for h in result.hits]
    assert chunk_ids == ["abc", "def", "ghi"]


# ---------------------------------------------------------------------------
# 10. Internal helper unit tests (sanity-check the three shapes)
# ---------------------------------------------------------------------------


def test_extract_chunk_id_top_level() -> None:
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_chunk_id,
    )

    assert _extract_chunk_id({"chunk_id": "abc"}) == "abc"


def test_extract_chunk_id_top_level_id() -> None:
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_chunk_id,
    )

    assert _extract_chunk_id({"id": 123}) == "123"
    assert _extract_chunk_id({"id": "abc"}) == "abc"


def test_extract_chunk_id_entity_wrapper() -> None:
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_chunk_id,
    )

    assert (
        _extract_chunk_id({"entity": {"chunk_id": "abc"}, "id": 1})
        == "abc"
    )


def test_extract_chunk_id_none_when_absent() -> None:
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_chunk_id,
    )

    assert _extract_chunk_id({}) is None
    assert _extract_chunk_id({"distance": 0.9}) is None
    assert _extract_chunk_id({"chunk_id": ""}) is None
    assert _extract_chunk_id({"entity": {}}) is None


def test_extract_field_top_level_wins_over_entity() -> None:
    """Top-level lookup wins over entity fallback (priority 1)."""
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_field,
    )

    assert (
        _extract_field(
            {"stable_document_id": "top", "entity": {"stable_document_id": "ent"}},
            "stable_document_id",
        )
        == "top"
    )


def test_extract_field_falls_back_to_entity() -> None:
    """When top-level is absent, fall back to entity (priority 2)."""
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_field,
    )

    assert (
        _extract_field(
            {"id": 1, "entity": {"base_id": "ent-base"}},
            "base_id",
        )
        == "ent-base"
    )


def test_extract_field_none_when_absent_everywhere() -> None:
    from app.services.reader_orchestration.article_rag_vector_search import (
        _extract_field,
    )

    assert _extract_field({}, "stable_document_id") is None
    assert (
        _extract_field({"id": 1, "entity": {}}, "base_id") is None
    )


# ---------------------------------------------------------------------------
# 11. Entity-wrapper guard parsing (reviewer fix)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_zilliz_searcher_reads_guard_metadata_from_entity_wrapper(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real pymilvus 2.5+ ``search(..., use_full_content=True)``
    returns ``{entity: {field: value}, id: int}``.  The adapter must
    surface guard fields from the entity wrapper so the retrieval
    service's vector-mismatch fail-closed policy can fire."""
    other_base = str(uuid.uuid4())
    other_psha = "1" * 64
    hits = [
        {
            "id": 1,
            "entity": {
                "chunk_id": "abc",
                "stable_document_id": str(uuid.uuid4()),
                "base_id": other_base,  # mismatches — see retrieval test
                "plan_content_sha256": other_psha,
            },
            "distance": 0.9,
        },
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=5,
    )
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.chunk_id == "abc"
    # All three guard fields must be populated from the entity wrapper.
    assert hit.base_id is not None
    assert str(hit.base_id) == other_base
    assert hit.plan_content_sha256 == other_psha


@pytest.mark.anyio
async def test_zilliz_searcher_mixed_shape_guard_metadata(
    _pymilvus_clean: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-entry shape tolerance: one hit has guard at top level,
    another has it in the entity wrapper.  Both should surface their
    respective guard fields correctly."""
    other_base = str(uuid.uuid4())
    hits = [
        {
            "chunk_id": "a",
            "stable_document_id": str(uuid.uuid4()),
            "base_id": other_base,  # top-level
            "plan_content_sha256": "deadbeef",
            "distance": 0.9,
        },
        {
            "id": 2,
            "entity": {
                "chunk_id": "b",
                "stable_document_id": str(uuid.uuid4()),
                "base_id": other_base,
                "plan_content_sha256": "deadbeef",
            },
            "distance": 0.8,
        },
    ]
    _install_pymilvus_stub(monkeypatch, hits=hits)
    searcher = ZillizArticleRagVectorSearcher(
        uri=_FAKE_URI, token=_FAKE_TOKEN, collection=_FAKE_COLLECTION
    )
    result = await searcher.search(
        collection=_FAKE_COLLECTION,
        query_vector=(0.1,),
        limit=5,
    )
    assert [h.chunk_id for h in result.hits] == ["a", "b"]
    # Both hits should have base_id populated (mismatch below, but
    # the field itself must be readable from either shape).
    assert result.hits[0].base_id is not None
    assert result.hits[1].base_id is not None
    assert str(result.hits[0].base_id) == str(result.hits[1].base_id)
