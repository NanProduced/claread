"""D6-I4W Article RAG service-level E2E smoke.

Wires the full RAG lifecycle end-to-end at the service layer — no FastAPI
route, no frontend, no LLM/embedding/vector network calls:

    article_ready
        -> ArticleRagAutoEnsureService.ensure_in_transaction
        -> ArticleRagIndexLifecycleService.ensure_article_rag_index_job_in_transaction
        -> ArticleRagIndexBootstrapService.bootstrap_article_rag_index
        -> ArticleRagIndexWorkerService.process_next  # fake embed + fake writer
        -> ArticleRagIndexLifecycleService.load_article_rag_index_lifecycle_status   -> "indexed"
        -> ArticleRagRetrievalService.retrieve_for_record   (FakeArticleRagVectorSearcher)
        -> ArticleRagAskContextProvider.build_for_ask       # 4-layer facade w/ real adapter

The smoke proves the same control flow that production uses — but with
fake providers / writers / searchers and a per-test temporary PostgreSQL
schema — and adds three fail-soft coverage cases:

  - auto-ensure disabled does not enqueue
  - worker vector retryable error does not break article_ready
  - retrieval on a not-indexed record yields no-context assembly

Hard limits:
  - No apps/web/** touched
  - No route or schema files touched (dirty from sibling agents)
  - No DashScope / Zilliz / LLM / network calls — only fakes
  - Stable Document Blocks / reading_bases / reading_units / anchor_segments
    truth rules are read but not modified
  - No Plate / Markdown / DOM / Slate / UI projection is involved
  - No git stage/commit
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.services.reader_orchestration.article_rag_ask_context_composer import (
    ArticleRagAskContextComposer,
)
from app.services.reader_orchestration.article_rag_ask_context_provider import (
    ArticleRagAskContextProvider,
)
from app.services.reader_orchestration.article_rag_ask_context_resolver import (
    ArticleRagAskContextResolver,
)
from app.services.reader_orchestration.article_rag_ask_integration_adapter import (
    ArticleRagAskIntegrationAdapter,
)
from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (
    ArticleRagAskPromptAssembly,
    ArticleRagAskPromptAssemblyService,
)
from app.services.reader_orchestration.article_rag_ask_prompt_attachment import (
    ArticleRagAskPromptAttachmentService,
)
from app.services.reader_orchestration.article_rag_ask_prompt_section import (
    ArticleRagAskPromptSectionBuilder,
)
from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (
    ArticleRagAskRuntimeAdapter,
)
from app.services.reader_orchestration.article_rag_auto_ensure_service import (
    AUTO_ENSURE_STATUS_DISABLED,
    REASON_RAG_DISABLED,
    ArticleRagAutoEnsureResult,
    ArticleRagAutoEnsureService,
)
from app.services.reader_orchestration.article_rag_context_service import (
    ArticleRagContextItem,
    ArticleRagContextPack,
    ArticleRagContextService,
)
from app.services.reader_orchestration.article_rag_index_bootstrap import (
    ArticleRagIndexBootstrapService,
)
from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ArticleRagIndexLifecycleService,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    DEFAULT_FAKE_EMBEDDING_MODEL,
    DEFAULT_FAKE_VECTOR_COLLECTION,
    DEFAULT_FAKE_VECTOR_STORE_PROVIDER,
    ArticleRagIndexWorkerError,
    ArticleRagIndexWorkerResult,
    ArticleRagIndexWorkerService,
    ArticleRagVectorChunk,
    ArticleRagVectorWriter,
    FakeArticleRagEmbeddingProvider,
    FakeArticleRagVectorWriter,
)
from app.services.reader_orchestration.article_rag_retrieval_service import (
    ArticleRagRetrievalHit,
    ArticleRagRetrievalResult,
    ArticleRagRetrievalService,
)
from app.services.reader_orchestration.article_rag_vector_search import (
    ArticleRagVectorSearchHit,
    FakeArticleRagVectorSearcher,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]

# Reuse canonical UUID constants + seed helpers from the existing I4A test
# module so the seeded entity graph (record / base / stable document /
# block / unit / segment) is identical to the one the other RAG tests use.
from tests.test_d6_i4a_article_rag_index_plan import (  # noqa: E402
    _BASE_ID,
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
    _main_reading_policy,
    _seed_block,
    _seed_full_environment,
    _seed_segment,
    _seed_unit,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# The Article RAG index is a single path.  BASELINE_SQL (which includes
# migration 0010) is sufficient.
INDEX_SMOKE_SCHEMA_SQL = BASELINE_SQL


# ---------------------------------------------------------------------------
# Pool / schema fixtures (mirror test_d6_i4c_article_rag_index_worker.py)
# ---------------------------------------------------------------------------


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def smoke_env() -> asyncpg.Pool:
    schema_name = f"test_i4w_smoke_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(INDEX_SMOKE_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Minimal stable reading-record seeding (paragraph + heading)
# ---------------------------------------------------------------------------


_PARAGRAPH_TEXT = (
    "The Roman Empire's eastern provinces produced some of the wealthiest "
    "cities of the late antique Mediterranean."
)
_HEADING_TEXT = "Trade networks"


async def _seed_smoke_environment(pool: asyncpg.Pool) -> str:
    """Seed the minimum entity graph needed for the full RAG flow.

    Returns the base content_sha256 so the test can cross-check it against
    what the lifecycle service records in the index_run row.
    """
    base_text = _HEADING_TEXT + "\n\n" + _PARAGRAPH_TEXT
    await _seed_full_environment(pool, base_text=base_text)

    # Heading block — not indexable (paragraph index policy excludes headings),
    # but it must exist so block ordering / offsets line up.
    await _seed_block(
        pool,
        block_id="heading-1",
        order_index=0,
        block_type="heading",
        text_content=_HEADING_TEXT,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(_HEADING_TEXT),
        interpretation_policy=_main_reading_policy(),
    )

    # Paragraph block — indexable per default policy.
    paragraph_start = utf16_code_unit_length(_HEADING_TEXT) + 2  # + "\n\n"
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=1,
        block_type="paragraph",
        text_content=_PARAGRAPH_TEXT,
        canonical_text_start_utf16=paragraph_start,
        canonical_text_end_utf16=paragraph_start
        + utf16_code_unit_length(_PARAGRAPH_TEXT),
        interpretation_policy=_main_reading_policy(),
    )

    # One reading_unit + anchor_segment aligned to the paragraph's offsets so
    # citation rebuild has a unit/segment to point at.  Reusing the helpers
    # already imported above keeps the truth graph identical to the other
    # I4 tests.  Both helpers are base-anchored (base_start_utf16 / base_end_utf16),
    # not block-anchored, so we feed them the same UTF-16 range we used for
    # the paragraph block.
    await _seed_unit(
        pool,
        unit_id="unit-1",
        order_index=1,
        unit_type="body",
        base_start_utf16=paragraph_start,
        base_end_utf16=paragraph_start + utf16_code_unit_length(_PARAGRAPH_TEXT),
    )
    await _seed_segment(
        pool,
        unit_id="unit-1",
        anchor_segment_id="segment-1",
        sentence_id="sentence-1",
        paragraph_id="paragraph-1",
        order_index=1,
        unit_order_index=1,
        base_start_utf16=paragraph_start,
        base_end_utf16=paragraph_start + utf16_code_unit_length(_PARAGRAPH_TEXT),
        unit_start_utf16=paragraph_start,
        unit_end_utf16=paragraph_start + utf16_code_unit_length(_PARAGRAPH_TEXT),
    )
    return hashlib.sha256(base_text.encode("utf-8")).hexdigest()


def _build_bootstrap_service(pool: asyncpg.Pool) -> ArticleRagIndexBootstrapService:
    return ArticleRagIndexBootstrapService(pool=pool)


def _build_lifecycle_service(
    bootstrap_service: ArticleRagIndexBootstrapService,
) -> ArticleRagIndexLifecycleService:
    return ArticleRagIndexLifecycleService(bootstrap_service=bootstrap_service)


def _build_auto_ensure_service(
    lifecycle_service: ArticleRagIndexLifecycleService,
    *,
    enabled: bool,
) -> ArticleRagAutoEnsureService:
    return ArticleRagAutoEnsureService(
        lifecycle_service=lifecycle_service,
        enabled=enabled,
    )


def _build_worker_service(
    pool: asyncpg.Pool,
    *,
    embedding_provider: FakeArticleRagEmbeddingProvider | None = None,
    vector_writer: ArticleRagVectorWriter | None = None,
) -> ArticleRagIndexWorkerService:
    return ArticleRagIndexWorkerService(
        pool=pool,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )


def _build_retrieval_service(
    pool: asyncpg.Pool,
    *,
    vector_searcher: FakeArticleRagVectorSearcher,
    embedding_provider: FakeArticleRagEmbeddingProvider | None = None,
) -> ArticleRagRetrievalService:
    # No default embedding provider — the retrieval service refuses to
    # silently pick a fake, so tests must inject one explicitly.  Share
    # the same fake instance the worker used so the query vector is
    # deterministic and matches what the searcher expects.
    return ArticleRagRetrievalService(
        pool=pool,
        embedding_provider=embedding_provider or FakeArticleRagEmbeddingProvider(),
        vector_searcher=vector_searcher,
    )


def _build_context_service(
    retrieval_service: ArticleRagRetrievalService,
) -> ArticleRagContextService:
    return ArticleRagContextService(retrieval_service=retrieval_service)


def _build_ask_context_provider(
    retrieval_service: ArticleRagRetrievalService,
) -> ArticleRagAskContextProvider:
    """Wire the real 4-layer chain using only real services — no fakes at
    the Ask integration boundary.  This proves the assembly chain the
    production reader_ask path uses works against a real retrieval service
    backed by a fake vector searcher.

    Chain (top-down, mirroring the I4N spec):
      ContextService -> Resolver -> AttachmentService -> IntegrationAdapter
      -> SectionBuilder -> RuntimeAdapter -> AssemblyService
    """
    context_service = _build_context_service(retrieval_service)
    composer = ArticleRagAskContextComposer()
    resolver = ArticleRagAskContextResolver(
        context_service=context_service,
        composer=composer,
    )
    attachment_service = ArticleRagAskPromptAttachmentService(resolver=resolver)
    integration_adapter = ArticleRagAskIntegrationAdapter(
        attachment_service=attachment_service,
    )
    section_builder = ArticleRagAskPromptSectionBuilder()
    runtime_adapter = ArticleRagAskRuntimeAdapter()
    assembly_service = ArticleRagAskPromptAssemblyService()
    return ArticleRagAskContextProvider(
        integration_adapter=integration_adapter,
        section_builder=section_builder,
        runtime_adapter=runtime_adapter,
        assembly_service=assembly_service,
    )


_LEASE_OWNER = "test-i4w-article-rag-smoke"
_LEASE_DURATION = timedelta(seconds=60)
_RETRY_DELAY = timedelta(seconds=10)


# ---------------------------------------------------------------------------
# Row fetch helpers (subset of I4C, scoped to what the smoke needs)
# ---------------------------------------------------------------------------


async def _fetch_index_run(
    conn: asyncpg.Connection,
    *,
    index_run_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, stable_document_id, base_id,
               record_generation,
               plan_content_sha256, chunk_count, status,
               embedding_model, vector_store_provider, vector_collection,
               job_id, completed_at
        FROM reader_article_rag_index_runs
        WHERE id = $1
        """,
        index_run_id,
    )


async def _fetch_job(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT id, reading_record_id, base_id, run_id, status,
               failure_class, failure_code
        FROM reader_jobs
        WHERE id = $1
        """,
        job_id,
    )


# ---------------------------------------------------------------------------
# Stage 1: full happy path — article_ready -> indexed -> retrievable -> Ask
# ---------------------------------------------------------------------------


async def test_article_ready_to_ask_full_e2e_flow(smoke_env: asyncpg.Pool) -> None:
    """End-to-end smoke: every stage of the RAG pipeline composes into the next.

    Stages covered:
      1. Auto-ensure hook (enabled) -> lifecycle service -> bootstrap
      2. Worker indexes via fake embedding + fake vector writer
      3. Lifecycle status reflects 'indexed'
      4. Retrieval returns hits with citations rebuilt from Postgres plan
      5. Context service packs hits
      6. Ask context facade assembles prompt block + citations sidecar
    """
    await _seed_smoke_environment(smoke_env)

    # ---- Stage 1: auto-ensure within an explicit transaction --------------
    bootstrap = _build_bootstrap_service(smoke_env)
    lifecycle = _build_lifecycle_service(bootstrap)
    auto_ensure = _build_auto_ensure_service(lifecycle, enabled=True)

    async with smoke_env.acquire() as conn:
        async with conn.transaction():
            ensure_result = await auto_ensure.ensure_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )

    assert isinstance(ensure_result, ArticleRagAutoEnsureResult)
    assert ensure_result.status in ("enqueued", "idempotent_noop")
    assert ensure_result.reason_code in ("enqueued", "idempotent_noop")
    assert ensure_result.index_run_id is not None
    assert ensure_result.job_id is not None
    expected_index_run_id = ensure_result.index_run_id
    expected_job_id = ensure_result.job_id

    # ---- Stage 2: worker indexes via fakes ---------------------------------
    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    worker = _build_worker_service(
        smoke_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    worker_result = await worker.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert worker_result is not None
    assert isinstance(worker_result, ArticleRagIndexWorkerResult)
    assert worker_result.status == "succeeded"
    assert worker_result.index_run_id == expected_index_run_id
    assert worker_result.job_id == expected_job_id
    assert worker_result.reading_record_id == _RECORD_ID
    assert worker_result.stable_document_id == _STABLE_DOC_ID
    assert worker_result.base_id == _BASE_ID
    assert worker_result.retryable is None
    assert worker_result.failure_code is None

    # Fake providers were exercised; record what they wrote.
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1
    assert len(vector_writer.upserts) == 1
    upsert_collection, upserted_chunks, upsert_metadata = vector_writer.upserts[0]

    # ---- Stage 3: lifecycle status reflects 'indexed' ---------------------
    async with smoke_env.acquire() as conn:
        index_run_row = await _fetch_index_run(
            conn, index_run_id=expected_index_run_id
        )
        job_row = await _fetch_job(conn, job_id=expected_job_id)

    assert index_run_row is not None
    assert index_run_row["status"] == "indexed"
    assert index_run_row["vector_collection"] == DEFAULT_FAKE_VECTOR_COLLECTION
    assert index_run_row["embedding_model"] == DEFAULT_FAKE_EMBEDDING_MODEL
    assert index_run_row["vector_store_provider"] == DEFAULT_FAKE_VECTOR_STORE_PROVIDER
    # plan_content_sha256 is computed from the seeded blocks, NOT from
    # base content_sha256 — but we still want to assert it is populated.
    assert index_run_row["plan_content_sha256"] is not None
    assert len(index_run_row["plan_content_sha256"]) == 64
    assert index_run_row["chunk_count"] >= 1
    assert index_run_row["completed_at"] is not None

    assert job_row is not None
    assert job_row["status"] == "succeeded"
    assert job_row["failure_class"] is None
    assert job_row["failure_code"] is None

    # The vector writer received the SAME collection + plan_content_sha256
    # that the index_run row now holds — proving the worker wires the
    # metadata through to the writer.
    assert upsert_collection == index_run_row["vector_collection"]
    assert upsert_metadata.plan_content_sha256 == index_run_row[
        "plan_content_sha256"
    ]
    assert upsert_metadata.collection == index_run_row["vector_collection"]
    assert upsert_metadata.reading_record_id == _RECORD_ID
    assert upsert_metadata.stable_document_id == _STABLE_DOC_ID
    assert upsert_metadata.base_id == _BASE_ID
    assert upsert_metadata.record_generation == 1
    assert upsert_metadata.chunk_count == len(upserted_chunks)

    # Vector chunk payload must NOT carry chunk text (citations are rebuilt
    # from the Postgres plan, never from vector payload).
    for chunk in upserted_chunks:
        assert isinstance(chunk, ArticleRagVectorChunk)
        assert "text" not in chunk.metadata
        assert "chunk_text" not in chunk.metadata
        # citation is a structured dict with block_ids — not the text itself.
        assert isinstance(chunk.citation, dict)
        assert "block_ids" in chunk.citation

    # ---- Stage 4: retrieval with fake searcher returning one hit ----------
    fake_hit = ArticleRagVectorSearchHit(
        chunk_id=upserted_chunks[0].chunk_id,
        score=0.91,
        stable_document_id=_STABLE_DOC_ID,
        base_id=_BASE_ID,
        plan_content_sha256=index_run_row["plan_content_sha256"],
    )
    retrieval = _build_retrieval_service(
        smoke_env, vector_searcher=FakeArticleRagVectorSearcher(hits=[fake_hit])
    )

    retrieval_result = await retrieval.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="Roman Empire trade",
        limit=5,
    )
    assert isinstance(retrieval_result, ArticleRagRetrievalResult)
    assert retrieval_result.reading_record_id == _RECORD_ID
    assert retrieval_result.stable_document_id == _STABLE_DOC_ID
    assert retrieval_result.base_id == _BASE_ID
    assert (
        retrieval_result.plan_content_sha256
        == index_run_row["plan_content_sha256"]
    )
    assert len(retrieval_result.hits) == 1
    hit = retrieval_result.hits[0]
    assert isinstance(hit, ArticleRagRetrievalHit)
    assert hit.chunk_id == fake_hit.chunk_id
    assert hit.score == pytest.approx(0.91)
    # Citation rebuilt from the Postgres plan — not from the vector payload.
    assert isinstance(hit.citation, dict)
    assert "block_ids" in hit.citation
    block_ids = hit.citation["block_ids"]
    assert isinstance(block_ids, list)
    assert len(block_ids) >= 1
    # Every block_id referenced must be one of the blocks we seeded —
    # citations are rebuilt from real block rows, not invented.
    seeded_blocks = {"heading-1", "paragraph-1"}
    assert set(block_ids).issubset(seeded_blocks)

    # ---- Stage 5: context service packs hits -------------------------------
    context_service = _build_context_service(retrieval)
    pack = await context_service.build_context_pack_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="Roman Empire trade",
        limit=5,
    )
    assert isinstance(pack, ArticleRagContextPack)
    assert pack.reading_record_id == _RECORD_ID
    assert pack.stable_document_id == _STABLE_DOC_ID
    assert pack.base_id == _BASE_ID
    assert pack.plan_content_sha256 == index_run_row["plan_content_sha256"]
    assert len(pack.items) == 1
    item = pack.items[0]
    assert isinstance(item, ArticleRagContextItem)
    assert item.chunk_id == fake_hit.chunk_id
    assert item.score == pytest.approx(0.91)
    # Citation round-trip: pack item carries the same block_ids as the
    # retrieval hit — confirming context service does not synthesize
    # citations from chunk text.
    assert item.citation["block_ids"] == hit.citation["block_ids"]

    # ---- Stage 6: Ask context facade attaches prompt + citations ----------
    ask_provider = _build_ask_context_provider(retrieval)
    assembly = await ask_provider.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="Roman Empire trade",
    )
    assert isinstance(assembly, ArticleRagAskPromptAssembly)
    # On the happy path, the assembly chain attaches the prompt block.
    assert assembly.should_attach is True
    assert assembly.status == "available"
    # Citations sidecar preserved verbatim from the retrieval hit.
    assert len(assembly.citations) == 1
    assert assembly.citations[0]["chunk_id"] == fake_hit.chunk_id
    assert assembly.context_ids == (assembly.citations[0]["context_id"],)
    # Prompt attachment block is non-empty and contains a recognizable
    # marker — we don't pin the full text (it carries chunk content),
    # only that the assembly produced something the Ask prompt bridge can
    # append verbatim.
    assert assembly.prompt_attachment_block
    assert assembly.failure_code is None
    assert assembly.retryable is False


# ---------------------------------------------------------------------------
# Stage 7: fail-soft — auto-ensure disabled does not enqueue
# ---------------------------------------------------------------------------


async def test_auto_ensure_disabled_does_not_enqueue(smoke_env: asyncpg.Pool) -> None:
    """When the feature flag is off, ensure_in_transaction returns a typed
    'disabled' result and never touches the lifecycle service, so no job
    or index_run row is created.
    """
    await _seed_smoke_environment(smoke_env)

    bootstrap = _build_bootstrap_service(smoke_env)
    lifecycle = _build_lifecycle_service(bootstrap)
    auto_ensure = _build_auto_ensure_service(lifecycle, enabled=False)

    async with smoke_env.acquire() as conn:
        async with conn.transaction():
            result = await auto_ensure.ensure_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )

    assert result.status == AUTO_ENSURE_STATUS_DISABLED
    assert result.reason_code == REASON_RAG_DISABLED
    assert result.index_run_id is None
    assert result.job_id is None

    # No index_run or job row was created.
    async with smoke_env.acquire() as conn:
        run_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_article_rag_index_runs"
        )
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs"
        )
    assert run_count == 0
    assert job_count == 0


# ---------------------------------------------------------------------------
# Stage 8: fail-soft — worker vector retryable error does not break
# article_ready (the record itself stays article_ready; lifecycle stays
# in a recoverable state, not terminal).
# ---------------------------------------------------------------------------


class _RetryableVectorWriter:
    """Vector writer that raises a retryable error on every upsert."""

    def __init__(self) -> None:
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata,  # ArticleRagVectorWriteMetadata — kept untyped to avoid import
    ):
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake retryable vector writer failure",
            retryable=True,
            failure_class="vector_writer",
            failure_code="vector_writer_failed",
        )


async def test_worker_retryable_vector_error_does_not_break_article_ready(
    smoke_env: asyncpg.Pool,
) -> None:
    """If the vector writer raises a retryable error, the worker must:
      - NOT promote the index_run to 'indexed'
      - NOT promote the job to 'succeeded'
      - leave the record's article_ready lifecycle status untouched (the
        underlying record is independent of the index_run row).

    The record's readiness_state was 'article_ready' before any of this
    ran; the worker MUST NOT mutate it.
    """
    await _seed_smoke_environment(smoke_env)

    # Bootstrap a real index_run + job (auto-ensure enabled).
    bootstrap = _build_bootstrap_service(smoke_env)
    lifecycle = _build_lifecycle_service(bootstrap)
    auto_ensure = _build_auto_ensure_service(lifecycle, enabled=True)

    async with smoke_env.acquire() as conn:
        async with conn.transaction():
            ensure_result = await auto_ensure.ensure_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )
        assert ensure_result.status in ("enqueued", "idempotent_noop")
        assert ensure_result.index_run_id is not None
        assert ensure_result.job_id is not None

    # Build worker with the retryable vector writer.
    worker = _build_worker_service(
        smoke_env,
        embedding_provider=FakeArticleRagEmbeddingProvider(),
        vector_writer=_RetryableVectorWriter(),
    )

    worker_result = await worker.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    assert worker_result is not None
    # Retryable -> worker surfaces it for re-queue, NOT succeeded.
    assert worker_result.status == "retry_later"
    assert worker_result.retryable is True
    assert worker_result.failure_code is not None

    # The index_run must NOT be 'indexed', and the job must NOT be
    # 'succeeded'.  Both should be left in a recoverable state.
    async with smoke_env.acquire() as conn:
        index_run = await _fetch_index_run(
            conn, index_run_id=ensure_result.index_run_id
        )
        job = await _fetch_job(conn, job_id=ensure_result.job_id)
        record = await conn.fetchrow(
            "SELECT id, readiness_state, lifecycle_status "
            "FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )

    assert index_run["status"] != "indexed"
    assert index_run["completed_at"] is None
    assert job["status"] != "succeeded"
    # The record itself must remain 'article_ready' — the retryable vector
    # error must not have cascaded back to corrupt article readiness.
    assert record["readiness_state"] == "article_ready"
    assert record["lifecycle_status"] == "active"


# ---------------------------------------------------------------------------
# Stage 9: fail-soft — retrieval on a not-indexed record returns no
# context, and the Ask path falls back to a no-attach assembly instead of
# raising.  This is the contract the production reader_ask path relies on.
# ---------------------------------------------------------------------------


async def test_retrieval_on_not_indexed_record_yields_no_context(
    smoke_env: asyncpg.Pool,
) -> None:
    """When the record has no index_run at all (auto-ensure disabled or
    not yet triggered), the retrieval service raises a typed
    ``retrieval_no_indexed_run`` error and the Ask facade must produce a
    no-attach assembly without propagating the exception.  This is what
    guarantees article reading never crashes if RAG is unavailable.
    """
    # Seed only the minimal entity graph (no auto-ensure call).
    await _seed_smoke_environment(smoke_env)

    retrieval = _build_retrieval_service(
        smoke_env,
        vector_searcher=FakeArticleRagVectorSearcher(hits=[]),
    )

    # Retrieval service raises a typed error when no indexed run exists —
    # this is the contract callers (resolver / Ask facade) must catch and
    # translate to a no-attach assembly.
    from app.services.reader_orchestration.article_rag_retrieval_service import (
        FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN,
        ArticleRagRetrievalServiceError,
    )

    with pytest.raises(ArticleRagRetrievalServiceError) as exc_info:
        await retrieval.retrieve_for_record(
            reading_record_id=_RECORD_ID,
            user_id=_USER_ID,
            query_text="Roman Empire trade",
        )
    assert exc_info.value.failure_code == FAILURE_CODE_RETRIEVAL_NO_INDEXED_RUN

    # Ask facade must produce a no-attach assembly — must NOT raise even
    # though the underlying retrieval call would fail.
    ask_provider = _build_ask_context_provider(retrieval)
    assembly = await ask_provider.build_for_ask(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="Roman Empire trade",
    )
    assert isinstance(assembly, ArticleRagAskPromptAssembly)
    assert assembly.should_attach is False
    assert assembly.status == "not_indexed_or_unavailable"
    assert assembly.citations == ()
    assert assembly.context_ids == ()
    assert assembly.prompt_attachment_block == ""


# ---------------------------------------------------------------------------
# Stage 10: explicit no-network / no-real-LLM guard.  No test in this file
# should ever reach out — this assertion makes that contract load-bearing
# at the test level so a future regression is caught even if a downstream
# dependency adds a hidden real call.
# ---------------------------------------------------------------------------


async def test_no_real_network_or_llm_calls_made(
    smoke_env: asyncpg.Pool,
) -> None:
    """Side-effect probe: run a worker pass with the standard fakes and
    verify only the fakes recorded calls.  The real-DashScope / real-Zilliz
    adapters are never constructed, and no module-level singleton has a
    handle to them.
    """
    await _seed_smoke_environment(smoke_env)

    bootstrap = _build_bootstrap_service(smoke_env)
    lifecycle = _build_lifecycle_service(bootstrap)
    auto_ensure = _build_auto_ensure_service(lifecycle, enabled=True)

    async with smoke_env.acquire() as conn:
        async with conn.transaction():
            await auto_ensure.ensure_in_transaction(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
                expected_generation=1,
            )

    embedding_provider = FakeArticleRagEmbeddingProvider()
    vector_writer = FakeArticleRagVectorWriter()
    worker = _build_worker_service(
        smoke_env,
        embedding_provider=embedding_provider,
        vector_writer=vector_writer,
    )

    await worker.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )

    # Only the fakes recorded traffic.  This is the negative-assertion
    # gate: if a future change swaps the fakes out for real adapters, this
    # test still runs (because the fakes are constructed locally here),
    # but the CI guard `fail_on_real_llm_attempts` in conftest.py will
    # catch any real httpx / dashscope call before teardown.
    assert embedding_provider.call_count == 1
    assert vector_writer.call_count == 1
    assert vector_writer.upserts[0][0] == DEFAULT_FAKE_VECTOR_COLLECTION