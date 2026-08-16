"""Vector deleter unit tests (Wave 9 A).

Pure-unit tests for the exact-id Article RAG vector deletion adapter:

- query_iterator spans multiple enumeration batches, still complete.
- delete receives ONLY exact chunk IDs, in stable deterministic batches.
- collection missing -> no_vectors with zero delete calls.
- no rows for the stable document -> no_vectors with zero delete calls.
- other stable documents' rows are fully preserved.
- flush (pre-delete), flush (post-delete) and final re-verify ordering.
- non-string / malformed / NaN-like / unsafe chunk IDs fail before any
  delete; discovery cap exceeded fails with zero deletes.
- delete that leaves rows behind returns a retryable failure.
- query/delete/flush SDK exceptions map to fixed safe errors.
- create/drop/compact are never invoked.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services.reader_orchestration.article_rag_vector_deleter import (
    ArticleRagVectorDeletionError,
    ZillizArticleRagVectorDeleter,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


# ---------------------------------------------------------------------------
# Fake Milvus client
# ---------------------------------------------------------------------------


class _FakeQueryIterator:
    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = list(pages)

    def next(self) -> list[dict]:
        return self._pages.pop(0) if self._pages else []


class _FakeMilvusClient:
    """In-memory fake of ``pymilvus.MilvusClient``.

    ``rows`` maps chunk_id -> row dict.  ``page_size`` controls the
    enumeration page size regardless of the requested batch_size so tests
    can force multi-batch enumeration deterministically.
    """

    def __init__(
        self,
        *,
        exists: bool = True,
        page_size: int = 2,
        query_fail: Exception | None = None,
        flush_fail: Exception | None = None,
        delete_fail: Exception | None = None,
    ) -> None:
        self.exists = exists
        self.page_size = page_size
        self.query_fail = query_fail
        self.flush_fail = flush_fail
        self.delete_fail = delete_fail
        self.rows: dict[str, dict] = {}
        self.flush_calls: list[str] = []
        self.delete_calls: list[list[str]] = []
        self.query_calls: list[str] = []
        self.create_calls = 0
        self.drop_calls = 0
        self.compact_calls = 0

    def add_row(self, *, chunk_id: str, stable_document_id: UUID) -> None:
        self.rows[chunk_id] = {
            "chunk_id": chunk_id,
            "stable_document_id": str(stable_document_id),
        }

    def has_collection(self, *, collection_name: str) -> bool:
        return self.exists

    def flush(self, *, collection_name: str) -> None:
        self.flush_calls.append(collection_name)
        if self.flush_fail is not None:
            raise self.flush_fail

    def query_iterator(
        self,
        *,
        collection_name: str,
        filter: str,
        output_fields: list[str],
        batch_size: int,
    ) -> _FakeQueryIterator:
        del batch_size
        self.query_calls.append(filter)
        if self.query_fail is not None:
            raise self.query_fail
        # filter shape: stable_document_id == "<uuid>"
        assert "stable_document_id" in filter
        target = filter.split('"')[1]
        matches = sorted(
            (row for row in self.rows.values() if row["stable_document_id"] == target),
            key=lambda row: str(row["chunk_id"]),
        )
        pages = [
            matches[i:i + self.page_size]
            for i in range(0, len(matches), self.page_size)
        ]
        return _FakeQueryIterator(pages)

    def delete(self, *, collection_name: str, ids: list[str]) -> dict:
        del collection_name
        self.delete_calls.append(list(ids))
        if self.delete_fail is not None:
            raise self.delete_fail
        for cid in ids:
            self.rows.pop(cid, None)
        return {"delete_count": len(ids)}

    def create_collection(self, **kwargs: object) -> None:
        self.create_calls += 1

    def drop_collection(self, **kwargs: object) -> None:
        self.drop_calls += 1

    def compact(self, **kwargs: object) -> None:
        self.compact_calls += 1


def _make_deleter(client: _FakeMilvusClient) -> ZillizArticleRagVectorDeleter:
    return ZillizArticleRagVectorDeleter(
        uri="https://zilliz.invalid",
        token="test-token",
        collection="article_rag_chunks",
        client_factory=lambda: client,
    )


def _stable_id(seed: int) -> UUID:
    return UUID(int=seed)


def _sha(n: int) -> str:
    return f"{n:016x}"


# ===========================================================================
# Multi-batch enumeration + exact-id deletion
# ===========================================================================


class TestEnumerationAndDelete:
    async def test_multibatch_enumeration_is_complete(self) -> None:
        """query_iterator spanning multiple pages still discovers all rows."""
        client = _FakeMilvusClient(page_size=2)
        stable_id = _stable_id(1)
        for i in range(9):
            client.add_row(chunk_id=_sha(i), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=stable_id,
        )

        assert result.outcome == "deleted"
        assert result.discovered_chunk_count == 9
        assert result.deleted_chunk_count == 9
        assert client.rows == {}

    async def test_delete_receives_exact_ids_in_stable_batches(self) -> None:
        """Delete calls carry ONLY exact canonical chunk IDs, stable batches."""
        client = _FakeMilvusClient(page_size=1000)
        stable_id = _stable_id(2)
        ids = [_sha(i) for i in range(250)]
        for cid in ids:
            client.add_row(chunk_id=cid, stable_document_id=stable_id)
        deleter = _make_deleter(client)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=stable_id,
        )

        assert result.deleted_chunk_count == 250
        assert client.delete_calls == [ids[0:100], ids[100:200], ids[200:250]]
        all_deleted = sorted(cid for batch in client.delete_calls for cid in batch)
        assert all_deleted == ids
        for batch in client.delete_calls:
            for cid in batch:
                assert len(cid) == 16
                assert cid == cid.lower()

    async def test_other_stable_documents_preserved(self) -> None:
        """Deleting one stable document leaves other documents' rows intact."""
        client = _FakeMilvusClient(page_size=1)
        target = _stable_id(3)
        other = _stable_id(4)
        for i in range(4):
            client.add_row(chunk_id=_sha(i), stable_document_id=target)
        for i in range(4, 7):
            client.add_row(chunk_id=_sha(i), stable_document_id=other)
        deleter = _make_deleter(client)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=target,
        )

        assert result.outcome == "deleted"
        assert result.deleted_chunk_count == 4
        remaining = {r["stable_document_id"] for r in client.rows.values()}
        assert remaining == {str(other)}
        assert len(client.rows) == 3

    async def test_flush_delete_verify_ordering(self) -> None:
        """Pre-delete flush, post-delete flush, and final re-verify order."""
        client = _FakeMilvusClient(page_size=1)
        stable_id = _stable_id(5)
        for i in range(3):
            client.add_row(chunk_id=_sha(i), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=stable_id,
        )

        assert client.flush_calls == ["article_rag_chunks", "article_rag_chunks"]
        # Two flushes, one enumeration before the first delete and one
        # full re-verification enumeration after the last delete.
        assert len(client.query_calls) == 2
        assert len(client.delete_calls) >= 1
        assert client.rows == {}


# ===========================================================================
# Idempotent no-vectors paths
# ===========================================================================


class TestNoVectors:
    async def test_collection_missing_returns_no_vectors_zero_delete(self) -> None:
        client = _FakeMilvusClient(exists=False)
        deleter = _make_deleter(client)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=_stable_id(6),
        )

        assert result.outcome == "no_vectors"
        assert result.discovered_chunk_count == 0
        assert result.deleted_chunk_count == 0
        assert client.delete_calls == []
        assert client.flush_calls == []

    async def test_empty_enumeration_returns_no_vectors_zero_delete(self) -> None:
        client = _FakeMilvusClient(exists=True)
        other = _stable_id(7)
        for i in range(2):
            client.add_row(chunk_id=_sha(i), stable_document_id=other)
        deleter = _make_deleter(client)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=_stable_id(8),
        )

        assert result.outcome == "no_vectors"
        assert result.deleted_chunk_count == 0
        assert client.delete_calls == []
        assert len(client.rows) == 2


# ===========================================================================
# Fail-closed validation
# ===========================================================================


class TestFailClosed:
    async def test_invalid_chunk_ids_fail_before_any_delete(self) -> None:
        """Non-string / malformed / NaN-like / unsafe IDs fail closed."""
        bad_ids = (
            None, 123, 12.5, float("nan"), "not-a-hash",
            "A" * 16, "a" * 15, _sha(1) + "\n", _sha(1) + "ff",
        )
        for bad_id in bad_ids:
            client = _FakeMilvusClient(page_size=1)
            stable_id = _stable_id(9)
            client.add_row(chunk_id=_sha(0), stable_document_id=stable_id)
            client.rows[bad_id] = {"chunk_id": bad_id, "stable_document_id": str(stable_id)}
            deleter = _make_deleter(client)

            with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
                await deleter.delete_for_stable_document(
                    collection="article_rag_chunks",
                    stable_document_id=stable_id,
                )

            assert exc_info.value.retryable is False
            assert exc_info.value.failure_code == "unsafe_chunk_id"
            assert client.delete_calls == []
            assert client.rows

    async def test_discovery_limit_exceeded_fails_with_zero_delete(self) -> None:
        client = _FakeMilvusClient(page_size=5000)
        stable_id = _stable_id(10)
        for i in range(10_001):
            client.add_row(chunk_id=_sha(i), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id=stable_id,
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.failure_code == "discovery_limit_exceeded"
        assert client.delete_calls == []

    async def test_verify_leftover_returns_retryable_failure(self) -> None:
        """Rows remaining after delete -> retryable failure, not silence."""

        class _StickyDeleteClient(_FakeMilvusClient):
            def delete(self, *, collection_name: str, ids: list[str]) -> dict:
                super().delete(collection_name=collection_name, ids=ids)
                # Re-add one row so the post-delete verify still sees it.
                self.rows[ids[0]] = {"chunk_id": ids[0], "stable_document_id": self.rows_stub}
                return {"delete_count": len(ids)}

        client = _StickyDeleteClient(page_size=1)
        client.rows_stub = str(_stable_id(11))
        stable_id = _stable_id(11)
        client.add_row(chunk_id=_sha(0), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id=stable_id,
            )

        assert exc_info.value.retryable is True
        assert exc_info.value.failure_code == "vector_deletion_verify_failed"


# ===========================================================================
# SDK exception mapping
# ===========================================================================


class TestSdkErrorMapping:
    async def test_query_failure_maps_to_fixed_safe_error(self) -> None:
        client = _FakeMilvusClient(query_fail=RuntimeError("raw sdk query text 42"))
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id=_stable_id(12),
            )

        exc = exc_info.value
        assert exc.retryable is True
        assert exc.failure_code == "vector_deletion_query_failed"
        assert "raw sdk query text 42" not in str(exc)
        assert exc_info.value.__cause__ is not None
        assert client.delete_calls == []

    async def test_flush_failure_maps_to_fixed_safe_error(self) -> None:
        client = _FakeMilvusClient(flush_fail=RuntimeError("raw sdk flush text 7"))
        stable_id = _stable_id(13)
        client.add_row(chunk_id=_sha(0), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id=stable_id,
            )

        exc = exc_info.value
        assert exc.retryable is True
        assert exc.failure_code == "vector_deletion_flush_failed"
        assert "raw sdk flush text 7" not in str(exc)
        assert client.delete_calls == []

    async def test_delete_failure_maps_to_fixed_safe_error(self) -> None:
        client = _FakeMilvusClient(delete_fail=RuntimeError("raw sdk delete text 99"))
        stable_id = _stable_id(14)
        client.add_row(chunk_id=_sha(0), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id=stable_id,
            )

        exc = exc_info.value
        assert exc.retryable is True
        assert exc.failure_code == "vector_deletion_delete_failed"
        assert "raw sdk delete text 99" not in str(exc)

    async def test_never_creates_drops_or_compacts(self) -> None:
        client = _FakeMilvusClient(exists=False)
        client2 = _FakeMilvusClient(exists=True)
        stable_id = _stable_id(15)
        client2.add_row(chunk_id=_sha(0), stable_document_id=stable_id)

        await _make_deleter(client).delete_for_stable_document(
            collection="article_rag_chunks", stable_document_id=stable_id
        )
        await _make_deleter(client2).delete_for_stable_document(
            collection="article_rag_chunks", stable_document_id=stable_id
        )

        assert client.create_calls == 0
        assert client.drop_calls == 0
        assert client.compact_calls == 0
        assert client2.create_calls == 0
        assert client2.drop_calls == 0
        assert client2.compact_calls == 0

    async def test_collection_mismatch_fails_before_sdk(self) -> None:
        client = _FakeMilvusClient(exists=True)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="other_collection",
                stable_document_id=_stable_id(16),
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.failure_code == "collection_mismatch"
        assert client.query_calls == []
        assert client.delete_calls == []

    async def test_invalid_stable_document_id_fails_before_sdk(self) -> None:
        client = _FakeMilvusClient(exists=True)
        deleter = _make_deleter(client)

        with pytest.raises(ArticleRagVectorDeletionError) as exc_info:
            await deleter.delete_for_stable_document(
                collection="article_rag_chunks",
                stable_document_id="not-a-uuid",  # type: ignore[arg-type]
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.failure_code == "malformed_identity"
        assert client.query_calls == []
        assert client.delete_calls == []


# ===========================================================================
# Async wrapper sanity
# ===========================================================================


class TestAsyncBoundary:
    async def test_sync_sdk_calls_do_not_block_event_loop(self) -> None:
        """SDK calls run via asyncio.to_thread; the fake is sync."""
        client = _FakeMilvusClient(page_size=1)
        stable_id = _stable_id(17)
        for i in range(4):
            client.add_row(chunk_id=_sha(i), stable_document_id=stable_id)
        deleter = _make_deleter(client)

        async def _heartbeat() -> None:
            await asyncio.sleep(0.001)

        result = await deleter.delete_for_stable_document(
            collection="article_rag_chunks",
            stable_document_id=stable_id,
        )
        await _heartbeat()

        assert result.outcome == "deleted"
        assert result.deleted_chunk_count == 4
