"""Delete/write race integration tests (Wave 9 C).

Real-PostgreSQL + blocking fakes + ``asyncio.Event`` synchronization:

- writer holds the stable-document mutation lock and enters the upsert,
  the record is deleted, GC waits on the lock, the writer completes,
  GC deletes the writer's rows -> zero residual.
- GC holds the mutation lock first, the late writer waits, GC completes,
  the writer re-validates and fails -> upsert call_count == 0.

No sleep is used as a correctness proof: every ordering assertion is
backed by the advisory-lock contract (blocking acquire) plus final state
assertions that would fail under any wrong interleaving.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.article_rag_contract import ARTICLE_RAG_EMBEDDING_CONTRACT
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagVectorWriteResult,
    FakeArticleRagEmbeddingProvider,
)
from app.services.reader_orchestration.article_rag_vector_deleter import (
    ZillizArticleRagVectorDeleter,
)
from app.services.reader_orchestration.article_rag_vector_gc_service import (
    ArticleRagVectorGcService,
)
from app.services.reader_orchestration.reading_record_deletion_service import (
    ReadingRecordDeletionService,
)
from tests.test_article_rag_index_plan import (
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
)
from tests.test_article_rag_index_worker import (
    _LEASE_DURATION,
    _LEASE_OWNER,
    _RETRY_DELAY,
    _build_bootstrap_service,
    _build_worker_service,
    _seed_paragraph_environment,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

CONFIGURED_COLLECTION = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def race_env() -> asyncpg.Pool:
    schema_name = f"test_rag_gc_race_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=6,
            init=init_connection,
            setup=lambda conn: conn.execute(f'SET search_path TO "{schema_name}", public'),
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


@pytest.fixture
async def race_env_small() -> asyncpg.Pool:
    """Real pool with ``max_size=1`` to expose nested-acquire deadlocks."""
    schema_name = f"test_rag_gc_race_small_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    pool: asyncpg.Pool | None = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=1,
            timeout=5,
            init=init_connection,
            setup=lambda conn: conn.execute(f'SET search_path TO "{schema_name}", public'),
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Shared in-memory vector store + fakes
# ---------------------------------------------------------------------------


class _SharedVectorStore:
    """Shared rows between the blocking writer fake and the deleter client."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}


class _FakeMilvusClient:
    """In-memory MilvusClient backed by a shared store."""

    def __init__(self, store: _SharedVectorStore) -> None:
        self.store = store
        self.flush_calls: list[str] = []
        self.delete_calls: list[list[str]] = []
        self.create_calls = 0
        self.drop_calls = 0
        self.compact_calls = 0

    def has_collection(self, *, collection_name: str) -> bool:
        return True

    def flush(self, *, collection_name: str) -> None:
        self.flush_calls.append(collection_name)

    def query_iterator(
        self,
        *,
        collection_name: str,
        filter: str,
        output_fields: list[str],
        batch_size: int,
    ) -> object:
        del collection_name, output_fields, batch_size
        target = filter.split('"')[1]
        matches = sorted(
            (
                row for row in self.store.rows.values()
                if row["stable_document_id"] == target
            ),
            key=lambda row: str(row["chunk_id"]),
        )

        class _Iterator:
            def __init__(self, rows: list[dict]) -> None:
                self._rows = list(rows)

            def next(self) -> list[dict]:
                if not self._rows:
                    return []
                page = self._rows[:2]
                self._rows = self._rows[2:]
                return page

            def close(self) -> None:
                pass

        return _Iterator(matches)

    def delete(self, *, collection_name: str, ids: list[str]) -> dict:
        del collection_name
        self.delete_calls.append(list(ids))
        for cid in ids:
            self.store.rows.pop(cid, None)
        return {"delete_count": len(ids)}

    def create_collection(self, **kwargs: object) -> None:
        self.create_calls += 1

    def drop_collection(self, **kwargs: object) -> None:
        self.drop_calls += 1

    def compact(self, **kwargs: object) -> None:
        self.compact_calls += 1


class _BlockingVectorWriter:
    """Writes rows into the shared store; blocks inside the upsert."""

    def __init__(
        self,
        store: _SharedVectorStore,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self.store = store
        self.entered = entered
        self.release = release
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list,
        metadata: object,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        self.entered.set()
        await self.release.wait()
        for chunk in chunks_with_embeddings:
            self.store.rows[chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "stable_document_id": str(metadata.stable_document_id),
            }
        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=len(chunks_with_embeddings),
            provider_metadata={"provider": "fake-in-memory"},
        )


class _CountingVectorWriter:
    """Fails the test if ever called; records the count."""

    def __init__(self) -> None:
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list,
        metadata: object,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        raise AssertionError("upsert must never run in this scenario")


class _BlockingEmbeddingProvider:
    def __init__(
        self,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self.entered = entered
        self.release = release
        self.inner = FakeArticleRagEmbeddingProvider()

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list:
        self.entered.set()
        await self.release.wait()
        return await self.inner.embed_texts(texts, model=model)


class _BlockingDeleter:
    """Blocks inside the delete; delegates to the real deleter."""

    def __init__(
        self,
        inner: ZillizArticleRagVectorDeleter,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self.inner = inner
        self.entered = entered
        self.release = release

    async def delete_for_stable_document(
        self,
        *,
        collection: str,
        stable_document_id: UUID,
    ):
        self.entered.set()
        await self.release.wait()
        return await self.inner.delete_for_stable_document(
            collection=collection,
            stable_document_id=stable_document_id,
        )


def _make_real_deleter(store: _SharedVectorStore) -> ZillizArticleRagVectorDeleter:
    return ZillizArticleRagVectorDeleter(
        uri="https://zilliz.invalid",
        token="test-token",
        collection=CONFIGURED_COLLECTION,
        client_factory=lambda: _FakeMilvusClient(store),
    )


async def _seed_and_bootstrap(pool: asyncpg.Pool) -> object:
    await _seed_paragraph_environment(pool)
    bootstrap = _build_bootstrap_service(pool)
    result = await bootstrap.bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert result.idempotent_noop is False
    assert result.chunk_count == 1
    assert result.stable_document_id == _STABLE_DOC_ID
    return result


async def _completed_events(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT payload_json
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'record_state_changed'
              AND payload_json->>'event_schema'
                  = 'article_rag_vector_gc_completed_v1'
            """,
            _RECORD_ID,
        )


# ===========================================================================
# Race A: writer holds the lock first
# ===========================================================================


class TestWriterFirst:
    async def test_writer_upsert_then_gc_deletes_zero_residual(
        self, race_env: asyncpg.Pool
    ) -> None:
        await _seed_and_bootstrap(race_env)

        store = _SharedVectorStore()
        entered_upsert = asyncio.Event()
        release_upsert = asyncio.Event()
        writer = _BlockingVectorWriter(
            store, entered=entered_upsert, release=release_upsert
        )
        service = _build_worker_service(
            race_env,
            embedding_provider=FakeArticleRagEmbeddingProvider(),
            vector_writer=writer,
        )

        writer_task = asyncio.create_task(
            service.process_next(
                lease_owner=_LEASE_OWNER,
                lease_duration=_LEASE_DURATION,
                retry_delay=_RETRY_DELAY,
            )
        )
        # Writer is inside the upsert and holds the mutation lock.
        await asyncio.wait_for(entered_upsert.wait(), timeout=20)

        # Record is deleted while the writer is in the upsert.
        result = await ReadingRecordDeletionService(pool=race_env).delete_record(
            record_id=_RECORD_ID, user_id=_USER_ID
        )
        assert result is not None and result.status == "deleted"

        # GC starts and must WAIT on the mutation lock.
        gc_service = ArticleRagVectorGcService(
            pool=race_env,
            deleter=_make_real_deleter(store),
        )
        gc_task = asyncio.create_task(gc_service.process_next_due_intent())
        await asyncio.sleep(0.05)
        assert not gc_task.done(), "GC must wait for the writer's lock"

        # Writer completes its upsert; its mark-indexed must fail because
        # the run was superseded by the deletion (cancelled job cannot be
        # transitioned -> IllegalTransitionError surfaces).
        release_upsert.set()
        with pytest.raises(ValueError):
            await writer_task
        assert writer.call_count == 1

        # GC now acquires the lock and deletes the writer's rows.  The
        # discovered count proves the writer's rows were present when GC
        # enumerated (ordering: GC could only see them after the writer's
        # upsert landed).
        gc_result = await asyncio.wait_for(gc_task, timeout=20)
        assert gc_result is not None
        assert gc_result.status == "completed"
        assert gc_result.outcome == "deleted"
        assert gc_result.discovered_chunk_count == 1
        assert gc_result.deleted_chunk_count == 1
        # Zero residual.
        assert store.rows == {}

        completed = await _completed_events(race_env)
        assert len(completed) == 1
        assert completed[0]["payload_json"]["outcome"] == "deleted"


# ===========================================================================
# Race B: GC holds the lock first
# ===========================================================================


class TestGcFirst:
    async def test_gc_first_late_writer_revalidates_and_skips(
        self, race_env: asyncpg.Pool
    ) -> None:
        bootstrap_result = await _seed_and_bootstrap(race_env)

        # Pre-existing indexed history for the stable document: identity
        # recorded + rows already in the store (GC deletes these).
        async with race_env.acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_article_rag_index_runs
                SET status = 'indexing',
                    vector_store_provider = $2,
                    vector_collection = $3
                WHERE id = $1
                """,
                bootstrap_result.index_run_id,
                "zilliz",
                CONFIGURED_COLLECTION,
            )
        store = _SharedVectorStore()
        for i in range(2):
            cid = f"{i:016x}"
            store.rows[cid] = {
                "chunk_id": cid,
                "stable_document_id": str(_STABLE_DOC_ID),
            }

        # Writer starts: claims the job, marks indexing, blocks in embed.
        embed_entered = asyncio.Event()
        embed_release = asyncio.Event()
        writer = _CountingVectorWriter()
        service = _build_worker_service(
            race_env,
            embedding_provider=_BlockingEmbeddingProvider(
                entered=embed_entered, release=embed_release
            ),
            vector_writer=writer,
        )
        writer_task = asyncio.create_task(
            service.process_next(
                lease_owner=_LEASE_OWNER,
                lease_duration=_LEASE_DURATION,
                retry_delay=_RETRY_DELAY,
            )
        )
        await asyncio.wait_for(embed_entered.wait(), timeout=20)

        # Record deleted while the writer is mid-build.
        result = await ReadingRecordDeletionService(pool=race_env).delete_record(
            record_id=_RECORD_ID, user_id=_USER_ID
        )
        assert result is not None and result.status == "deleted"

        # GC starts, acquires the mutation lock FIRST and blocks inside
        # the deleter.
        del_entered = asyncio.Event()
        del_release = asyncio.Event()
        gc_service = ArticleRagVectorGcService(
            pool=race_env,
            deleter=_BlockingDeleter(
                _make_real_deleter(store),
                entered=del_entered,
                release=del_release,
            ),
        )
        gc_task = asyncio.create_task(gc_service.process_next_due_intent())
        await asyncio.wait_for(del_entered.wait(), timeout=20)

        # Writer proceeds and must WAIT on the mutation lock while GC
        # holds it.
        embed_release.set()
        await asyncio.sleep(0.05)
        assert not writer_task.done(), "writer must wait for the GC's lock"

        # GC completes; writer then acquires the lock, re-validates,
        # sees the superseded run / cancelled job and fails with zero
        # upserts.
        del_release.set()
        gc_result = await asyncio.wait_for(gc_task, timeout=20)
        assert gc_result is not None
        assert gc_result.status == "completed"
        assert gc_result.outcome == "deleted"
        assert gc_result.deleted_chunk_count == 2

        with pytest.raises(ValueError):
            await writer_task
        assert writer.call_count == 0, "late writer must not upsert"
        # Zero residual: GC removed the pre-existing rows, writer added none.
        assert store.rows == {}

        completed = await _completed_events(race_env)
        assert len(completed) == 1
        assert completed[0]["payload_json"]["outcome"] == "deleted"

# ===========================================================================
# R3: single-connection pool must not deadlock (Wave 9.1)
# ===========================================================================


class _RecordingVectorWriter:
    """Records upsert calls and returns a normal write result."""

    def __init__(self) -> None:
        self.call_count = 0
        self.upserts: list = []

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list,
        metadata: object,
    ) -> ArticleRagVectorWriteResult:
        self.call_count += 1
        self.upserts.append((collection, list(chunks_with_embeddings), metadata))
        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=len(chunks_with_embeddings),
            provider_metadata={"provider": "fake-in-memory"},
        )


# ===========================================================================
# R3: single-connection pool must not deadlock (Wave 9.1)
# ===========================================================================


class TestSmallPool:
    async def test_worker_completes_with_single_connection_pool(
        self, race_env_small: asyncpg.Pool
    ) -> None:
        """Index worker must not nested-acquire while holding the lock conn."""
        await _seed_and_bootstrap(race_env_small)
        embedding = FakeArticleRagEmbeddingProvider()
        writer = _RecordingVectorWriter()
        service = _build_worker_service(
            race_env_small,
            embedding_provider=embedding,
            vector_writer=writer,
        )

        result = await asyncio.wait_for(
            service.process_next(
                lease_owner=_LEASE_OWNER,
                lease_duration=_LEASE_DURATION,
                retry_delay=_RETRY_DELAY,
            ),
            timeout=60,
        )

        assert result is not None
        assert result.status == "succeeded"
        assert writer.call_count == 1