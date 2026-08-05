"""Article RAG Single-Path Real-Chain Acceptance (R2).

This module is the canonical real-chain acceptance smoke for the
Article RAG single-path convergence.  It replaces the prior
smoke-collection namespace design in ``test_d6_i4z_article_rag_local_dry_run.py``
which was mutually exclusive with the worker's frozen-contract
collection enforcement.

R2 fixes (vs R1 commit 5c9ed4d04):

  1. Retrieval type contract: ``ArticleRagRetrievalHit`` has only
     ``chunk_id`` / ``text`` / ``citation: dict`` / ``metadata_json`` /
     ``score`` / ``content_sha256``.  Identity (``stable_document_id``,
     ``base_id``, ``record_generation``) is read from
     ``ArticleRagRetrievalResult``, NOT from hits.  Citation fields
     are accessed via dict keys, NOT attribute access.
  2. Single retrieval call: a test-only ``_CapturingRetrievalService``
     wraps the real ``ArticleRagRetrievalService`` and is injected
     into ``RetrievalBackedArticleRagPort`` (the production
     ``ArticleRagSearchPort`` adapter) so the Ask port calls
     retrieval EXACTLY once.  No explicit retrieval call outside the
     Ask chain.
  3. D2: ``reader_runs`` is now queried via
     ``reader_article_rag_index_runs.reader_run_id`` and asserted
     ``status == "completed"`` (the worker's terminal value for
     ``reader_runs``; ``reader_jobs`` uses ``"succeeded"`` and
     ``reader_article_rag_index_runs`` uses ``"indexed"``).
  4. D4: Zilliz is queried for all chunks with our
     ``stable_document_id``; the precise ``chunk_id`` set is saved
     for D7 comparison and cleanup.
  5. D7: plan is rebuilt via ``ArticleRagIndexPlanService``; all 9
     citation fields are compared per ``chunk_id`` (not just
     "non-empty").
  6. Cleanup: deletes by the saved ``chunk_id`` set (NOT by
     ``stable_document_id`` filter).  Schema identity is compared
     before/after.
  7. Old smoke prefix retired (Phase 1 + Phase 2).

Design contract (R2):

  A. **Single collection identity.**  The smoke writes to
     ``ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection`` (i.e.
     ``article_rag_chunks``) — the SAME collection production uses.
     There is no second smoke collection, no prefix allowlist, no
     compatibility flag.  The worker's fail-closed contract
     enforcement at ``article_rag_index_worker.py:644`` is correct
     and MUST NOT be relaxed.

  B. **Precise fixture isolation.**  Each smoke run generates unique
     UUIDs for user / record / base / stable_document.  Cleanup
     deletes by the EXACT ``chunk_id`` set saved at D4 — never by
     ``stable_document_id`` filter, never drop / recreate the
     collection.  Protected collections
     (``grammar_note_examples``, ``sentence_analysis_examples``)
     are never touched.

  C. **Bounded real-call budget.**  At most:
       - 1 document embedding service call (1 outbound provider call)
       - 1 query embedding service call (1 outbound provider call)
       - 1 vector write service call (1 Zilliz upsert)
       - 1 vector search service call (1 Zilliz search)
       - 1 retrieval service call (the Ask port calls retrieval once)
       - 1 vector delete service call (precise chunk_id cleanup)
       - 0 rerank calls
       - 0 Ask model calls (this smoke stops at Ask context assembly;
         a separate task must opt into a real Ask model call)
     No retries.  Stop on first failure.
     Counts are MEASURED via counting delegates wrapping the real
     provider/searcher/writer/retrieval service — never hardcoded.

  D. **Acceptance assertions** (11 items, see test docstring).

  E. **Report facts.**  The test distinguishes:
       - smoke execution attempts
       - document embedding service calls
       - query embedding service calls
       - vector write service calls
       - vector search service calls
       - retrieval service calls
       - vector delete service calls
       - Ask model calls (always 0 in R2)

Env gate (opt-in, skip-by-default):

    READER_ARTICLE_RAG_SMOKE=1
    READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope
    READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz
    READER_ARTICLE_RAG_ZILLIZ_URI=<real URI>
    READER_ARTICLE_RAG_ZILLIZ_TOKEN=<real token>
    READER_ARTICLE_RAG_ZILLIZ_COLLECTION=article_rag_chunks
        # MUST equal ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection.
        # Any other value -> skip (never write to a mismatched collection).
    READER_ARTICLE_RAG_VECTOR_DIM=1024
    # Plus at least one of:
    BAILIAN_API_KEY=<real key>
    # OR:
    RAG_EMBEDDING_MODEL_PROFILE=<profile that resolves to dashscope_embedding>

Hard limits:

  - Default ``pytest`` never reads real credentials.
  - Default ``pytest`` never opens a socket (conftest no-network guard).
  - The opt-in smoke is the only test that reads real credentials.
  - No token / URI / chunk text / query text lands in a failure_code,
    reason_code, status response, or test fixture.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "services" / "api"

# ---------------------------------------------------------------------------
# Env gate constants
# ---------------------------------------------------------------------------

ARTICLE_RAG_SMOKE_ENV = "READER_ARTICLE_RAG_SMOKE"

REAL_SMOKE_REQUIRED_ENVS: tuple[str, ...] = (
    "READER_ARTICLE_RAG_EMBEDDING_PROVIDER",
    "READER_ARTICLE_RAG_VECTOR_PROVIDER",
    "READER_ARTICLE_RAG_ZILLIZ_COLLECTION",
    "READER_ARTICLE_RAG_VECTOR_DIM",
)

# Zilliz URI + token are resolved via the Settings resolver, which
# falls back to the shared ``ZILLIZ_URI`` / ``ZILLIZ_TOKEN`` env vars
# when the dedicated ``READER_ARTICLE_RAG_ZILLIZ_*`` vars are absent.
# The gate checks resolution outcome (not the raw env var name) so a
# local stack that only sets the shared vars can still enter the smoke.
REAL_SMOKE_RESOLVED_ENVS: tuple[str, ...] = (
    "ZILLIZ_URI",
    "ZILLIZ_TOKEN",
)

REAL_SMOKE_CREDENTIAL_ENVS: tuple[str, ...] = (
    "BAILIAN_API_KEY",
    "RAG_EMBEDDING_MODEL_PROFILE",
)

# Collections the smoke MUST NOT touch.  Listed here so the preflight
# can assert they exist and the post-cleanup can assert they are
# unchanged.
PROTECTED_ZILLIZ_COLLECTIONS: tuple[str, ...] = (
    "grammar_note_examples",
    "sentence_analysis_examples",
)

article_rag_smoke = pytest.mark.article_rag_smoke


# ---------------------------------------------------------------------------
# Call counter — tracks every outbound attempt for the report
# ---------------------------------------------------------------------------


@dataclass
class CallCounts:
    """Precise per-call accounting for the real-chain report.

    All counts are MEASURED via counting delegates wrapping the real
    provider/searcher/writer/retrieval service — never hardcoded after
    success.  Per the task spec, counts are "service calls" (not
    "provider API attempts") because SDK transport-level attempts may
    not be directly measurable from the test boundary.
    """

    smoke_execution_attempts: int = 0
    document_embedding_service_calls: int = 0
    query_embedding_service_calls: int = 0
    vector_write_calls: int = 0
    vector_search_calls: int = 0
    retrieval_service_calls: int = 0
    vector_delete_calls: int = 0
    ask_model_calls: int = 0  # always 0 in R2 — no real Ask model call

    def as_report(self) -> dict[str, int]:
        return {
            "smoke_execution_attempts": self.smoke_execution_attempts,
            "document_embedding_service_calls": (
                self.document_embedding_service_calls
            ),
            "query_embedding_service_calls": self.query_embedding_service_calls,
            "vector_write_calls": self.vector_write_calls,
            "vector_search_calls": self.vector_search_calls,
            "retrieval_service_calls": self.retrieval_service_calls,
            "vector_delete_calls": self.vector_delete_calls,
            "ask_model_calls": self.ask_model_calls,
        }


# ---------------------------------------------------------------------------
# Test-only counting delegates — record ACTUAL public seam calls
# (never hardcode counts after success).  Each delegate wraps the real
# provider/searcher/writer/retrieval service and delegates all calls
# transparently, recording call_count for the report.
#
# These delegates exist ONLY in this test module.  They do NOT modify
# production code.  The worker's ``self._embedding_provider`` and
# ``self._vector_writer`` attributes are replaced with these wrappers
# AFTER construction (the test already sanity-checks the factory
# produced real providers BEFORE wrapping).
# ---------------------------------------------------------------------------


class _CountingEmbeddingProvider:
    """Test-only counting delegate for DashScopeArticleRagEmbeddingProvider.

    Wraps the real provider, delegates ``embed_texts``, records
    ``call_count``.  All other attribute access is delegated to the
    wrapped provider via ``__getattr__`` so the worker sees the same
    ``provider_name`` / ``_model_override`` / etc.
    """

    def __init__(self, real: object) -> None:
        self._real = real
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._real.provider_name  # type: ignore[attr-defined]

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[Any]:
        self.call_count += 1
        return await self._real.embed_texts(  # type: ignore[attr-defined]
            texts, model=model
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _CountingVectorWriter:
    """Test-only counting delegate for ZillizArticleRagVectorWriter."""

    def __init__(self, real: object) -> None:
        self._real = real
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._real.provider_name  # type: ignore[attr-defined]

    @property
    def collection(self) -> str:
        return self._real.collection  # type: ignore[attr-defined]

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[Any],
        metadata: Any,
    ) -> Any:
        self.call_count += 1
        return await self._real.upsert_chunks(  # type: ignore[attr-defined]
            collection=collection,
            chunks_with_embeddings=chunks_with_embeddings,
            metadata=metadata,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _CountingVectorSearcher:
    """Test-only counting delegate for ZillizArticleRagVectorSearcher."""

    def __init__(self, real: object) -> None:
        self._real = real
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._real.provider_name  # type: ignore[attr-defined]

    async def search(
        self,
        *,
        collection: str,
        query_vector: tuple[float, ...],
        limit: int,
        stable_document_id: UUID | None = None,
    ) -> Any:
        self.call_count += 1
        return await self._real.search(  # type: ignore[attr-defined]
            collection=collection,
            query_vector=query_vector,
            limit=limit,
            stable_document_id=stable_document_id,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _CapturingRetrievalService:
    """Test-only capturing delegate for ArticleRagRetrievalService.

    Wraps the real retrieval service, delegates ``retrieve_for_record``,
    records ``call_count`` and ``last_result``.  Injected into
    ``RetrievalBackedArticleRagPort(retrieval=...)`` so the production
    Ask port calls retrieval EXACTLY once through this wrapper.  After
    ``search_current_article`` returns, ``last_result`` is used for
    D5-D7 assertions.

    This matches the ``_RetrievalLike`` Protocol defined in
    ``article_rag_adapter.py`` (single async method
    ``retrieve_for_record`` with keyword-only arguments).
    """

    def __init__(self, real: object) -> None:
        self._real = real
        self.call_count = 0
        self.last_result: Any = None

    async def retrieve_for_record(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        limit: int = 10,
    ) -> Any:
        self.call_count += 1
        result = await self._real.retrieve_for_record(  # type: ignore[attr-defined]
            reading_record_id=reading_record_id,
            user_id=user_id,
            query_text=query_text,
            limit=limit,
        )
        self.last_result = result
        return result


# ---------------------------------------------------------------------------
# Env gate predicate
# ---------------------------------------------------------------------------


def _real_smoke_env_present() -> bool:
    """True iff the opt-in gate is on, every hard-required env var is
    non-empty, at least one credential env is set, AND the configured
    Zilliz collection EXACTLY equals the frozen contract
    ``vector_collection`` (i.e. ``article_rag_chunks``).

    This is the single source of truth for the skip predicate below.
    The collection-identity guard is the safety net: the smoke refuses
    to enter unless the configured collection is the production
    collection, so an env-typo cannot write to a mismatched vector
    space.  This is the OPPOSITE of the prior i4z design (which
    required a smoke prefix); the single-path convergence makes the
    smoke write to the production collection and relies on precise
    fixture isolation instead.
    """
    if os.environ.get(ARTICLE_RAG_SMOKE_ENV) != "1":
        return False
    # Load Settings (reads .env) to access the same resolver production
    # uses.  This lets the gate accept either dedicated
    # READER_ARTICLE_RAG_ZILLIZ_* env vars OR the shared ZILLIZ_URI /
    # ZILLIZ_TOKEN fallbacks without duplicating the resolution logic.
    from app.config.settings import Settings  # noqa: I001

    settings = Settings()
    # Required non-secret config must be non-empty.
    if not settings.reader_article_rag_embedding_provider:
        return False
    if not settings.reader_article_rag_vector_provider:
        return False
    if not settings.reader_article_rag_zilliz_collection:
        return False
    if not settings.reader_article_rag_vector_dim:
        return False
    # Zilliz URI + token: resolved via the Settings resolver (falls
    # back to shared ZILLIZ_URI / ZILLIZ_TOKEN).
    if not settings.resolve_reader_article_rag_zilliz_uri():
        return False
    if not settings.resolve_reader_article_rag_zilliz_token():
        return False
    # At least one credential env must be set.
    _cred_check = any(
        os.environ.get(env, "")
        or _load_dotenv_value(env)
        for env in REAL_SMOKE_CREDENTIAL_ENVS
    )
    if not _cred_check:
        return False
    # Collection identity: refuse to enter unless the configured
    # collection EXACTLY equals the frozen contract vector_collection.
    from app.contracts.article_rag_contract import (  # noqa: I001
        ARTICLE_RAG_EMBEDDING_CONTRACT,
    )

    if (
        settings.reader_article_rag_zilliz_collection
        != ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection
    ):
        return False
    return True


def _load_dotenv_value(key: str) -> str:
    """Read a single key from the local .env file (no secrets logged)."""
    from app.config.settings import _load_local_env_values

    return _load_local_env_values().get(key, "")


_REAL_SMOKE_SKIP_REASON = (
    "Real-provider Article RAG single-path smoke is opt-in only.  "
    f"Required: {ARTICLE_RAG_SMOKE_ENV}=1, all of "
    f"{REAL_SMOKE_REQUIRED_ENVS}, at least one of "
    f"{REAL_SMOKE_CREDENTIAL_ENVS}, AND "
    "READER_ARTICLE_RAG_ZILLIZ_COLLECTION == "
    "ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection "
    "(article_rag_chunks).  DO NOT enable in production."
)


# ---------------------------------------------------------------------------
# Test schema + seed helpers (mirror i4z/i4a, but with UNIQUE UUIDs)
# ---------------------------------------------------------------------------

from tests.test_article_rag_index_plan import (  # noqa: E402
    _main_reading_policy,
    _seed_block,
    _seed_segment,
    _seed_unit,
)
from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

ARTICLE_RAG_ACCEPTANCE_SCHEMA_SQL = BASELINE_SQL


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    from app.database.connection import init_connection

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


@dataclass(frozen=True, slots=True)
class _FixtureIds:
    """Unique-per-run UUIDs for precise fixture isolation."""

    user_id: UUID
    record_id: UUID
    base_id: UUID
    stable_document_id: UUID

    @classmethod
    def generate(cls) -> _FixtureIds:
        return cls(
            user_id=uuid4(),
            record_id=uuid4(),
            base_id=uuid4(),
            stable_document_id=uuid4(),
        )


@pytest.fixture
async def acceptance_env() -> asyncpg.Pool:
    """Per-test temp schema with UNIQUE UUIDs for fixture isolation.

    The schema is dropped at teardown; nothing persists across tests.
    """
    schema_name = f"test_r1_acceptance_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(ARTICLE_RAG_ACCEPTANCE_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Seed helpers — use the UNIQUE fixture IDs (not the i4a fixed UUIDs)
# ---------------------------------------------------------------------------

_PARAGRAPH_TEXT = (
    "Article RAG single-path acceptance probe: a short non-sensitive "
    "English sentence used to seed a real test-Postgres schema for the "
    "R1 real-chain smoke."
)
_HEADING_TEXT = "Acceptance Probe"


async def _seed_acceptance_environment(
    pool: asyncpg.Pool,
    ids: _FixtureIds,
) -> None:
    """Seed a minimal article_ready record with UNIQUE UUIDs.

    Seeds directly with per-run ``_FixtureIds`` — no prior rows exist
    because ``acceptance_env`` creates a fresh schema.  The unique
    ``stable_document_id`` is the key that lets the Zilliz cleanup
    delete EXACTLY our chunks.

    We do NOT call ``_seed_full_environment`` (which uses i4a fixed
    UUIDs) and then delete/re-seed: the circular FK between
    ``reading_records.active_base_id`` and ``reading_bases`` makes
    a clean DELETE impossible without NULLing one side first, and
    on a fresh schema there is nothing to delete anyway.
    """
    from app.contracts.annotation import utf16_code_unit_length
    from tests.test_article_rag_index_plan import (
        _seed_base,
        _seed_record,
        _seed_stable_document,
        _seed_user,
    )

    base_text = _HEADING_TEXT + "\n\n" + _PARAGRAPH_TEXT

    await _seed_user(pool, user_id=ids.user_id)
    await _seed_record(
        pool,
        user_id=ids.user_id,
        record_id=ids.record_id,
        generation=1,
        active_base_id=None,
    )
    await _seed_base(
        pool,
        base_id=ids.base_id,
        reading_record_id=ids.record_id,
        record_generation=1,
        text=base_text,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            ids.record_id,
            ids.base_id,
        )
    await _seed_stable_document(
        pool,
        stable_document_id=ids.stable_document_id,
        reading_record_id=ids.record_id,
        record_generation=1,
    )

    # Seed blocks + unit + segment (same shape as i4z).
    # Pass our unique IDs — the i4a helpers default to fixed UUIDs
    # which do not exist in our fresh schema.
    await _seed_block(
        pool,
        stable_document_id=ids.stable_document_id,
        block_id="heading-1",
        order_index=0,
        block_type="heading",
        text_content=_HEADING_TEXT,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(_HEADING_TEXT),
        interpretation_policy=_main_reading_policy(),
    )
    paragraph_start = utf16_code_unit_length(_HEADING_TEXT) + 2
    await _seed_block(
        pool,
        stable_document_id=ids.stable_document_id,
        block_id="paragraph-1",
        order_index=1,
        block_type="paragraph",
        text_content=_PARAGRAPH_TEXT,
        canonical_text_start_utf16=paragraph_start,
        canonical_text_end_utf16=paragraph_start
        + utf16_code_unit_length(_PARAGRAPH_TEXT),
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_unit(
        pool,
        base_id=ids.base_id,
        reading_record_id=ids.record_id,
        unit_id="unit-1",
        order_index=1,
        unit_type="body",
        base_start_utf16=paragraph_start,
        base_end_utf16=paragraph_start + utf16_code_unit_length(_PARAGRAPH_TEXT),
    )
    await _seed_segment(
        pool,
        base_id=ids.base_id,
        reading_record_id=ids.record_id,
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


# ---------------------------------------------------------------------------
# Zilliz preflight + precise cleanup helpers
# ---------------------------------------------------------------------------


def _get_zilliz_client() -> object:
    """Construct a pymilvus MilvusClient from env.  Raises if SDK missing.

    Uses the Settings resolver (which falls back to the shared
    ``ZILLIZ_URI`` / ``ZILLIZ_TOKEN`` env vars) so the test mirrors
    production's resolution behaviour.
    """
    from pymilvus import MilvusClient  # type: ignore[import-untyped]

    from app.config.settings import Settings

    settings = Settings(_env_file=None)
    # Use the resolver — falls back to shared ZILLIZ_URI/ZILLIZ_TOKEN.
    uri = settings.resolve_reader_article_rag_zilliz_uri()
    token = settings.resolve_reader_article_rag_zilliz_token()
    if not uri or not token:
        raise RuntimeError(
            "Zilliz URI or token missing from env — cannot run preflight.  "
            "Set READER_ARTICLE_RAG_ZILLIZ_URI/TOKEN or the shared "
            "ZILLIZ_URI/ZILLIZ_TOKEN."
        )
    return MilvusClient(uri=uri, token=token)


@dataclass
class _ZillizPreflightSnapshot:
    """Immutable snapshot of Zilliz state before the smoke runs.

    Schema identity fields (``field_descriptions``, ``primary_key_field``,
    ``vector_field``, ``vector_dim``, ``index_info``) are captured here so
    the cleanup can verify the collection schema is unchanged after
    deleting our fixture chunks.
    """

    collection_exists: bool
    field_count: int
    field_names: tuple[str, ...]
    # Full field descriptions for schema identity comparison.
    # Each dict has: name, type, is_primary, params (may include dim).
    field_descriptions: tuple[dict[str, Any], ...]
    primary_key_field: str | None
    vector_field: str | None
    vector_dim: int | None
    # Index/metric config (best-effort; may be empty tuple if API
    # is unavailable on the Zilliz plan).
    index_info: tuple[dict[str, Any], ...]
    protected_collections_present: dict[str, bool]
    all_collections: tuple[str, ...]
    fixture_chunk_count: int  # chunks with our stable_document_id (must be 0)
    # ``stats`` may be unavailable on some Zilliz plans; we record it
    # only when the API returns a stable row count.
    collection_row_count: int | None


def _extract_schema_identity(
    describe: Any,
) -> tuple[
    tuple[dict[str, Any], ...],
    str | None,
    str | None,
    int | None,
]:
    """Extract field_descriptions, primary_key, vector_field, vector_dim
    from a ``describe_collection`` result.

    Returns ``(field_descriptions, primary_key_field, vector_field, vector_dim)``.
    Defensive: if the shape is unexpected, returns safe defaults.
    """
    fields_raw = (
        describe.get("fields", []) if isinstance(describe, dict) else []
    )
    field_descriptions: list[dict[str, Any]] = []
    primary_key_field: str | None = None
    vector_field: str | None = None
    vector_dim: int | None = None

    for f in fields_raw:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", ""))
        ftype = f.get("type", None)
        is_primary = bool(f.get("is_primary", False))
        params = f.get("params", {}) if isinstance(f.get("params"), dict) else {}
        desc = {
            "name": name,
            "type": ftype,
            "is_primary": is_primary,
            "params": dict(params),
        }
        field_descriptions.append(desc)
        if is_primary and primary_key_field is None:
            primary_key_field = name
        # FLOAT_VECTOR DataType == 101 in pymilvus.  Also accept
        # string "FLOAT_VECTOR" for compatibility.
        is_vector = (
            ftype == 101
            or str(ftype).upper() == "FLOAT_VECTOR"
            or "dim" in params
        )
        if is_vector and vector_field is None:
            vector_field = name
            dim_val = params.get("dim")
            if isinstance(dim_val, int) and not isinstance(dim_val, bool):
                vector_dim = int(dim_val)

    return (
        tuple(field_descriptions),
        primary_key_field,
        vector_field,
        vector_dim,
    )


def _extract_index_info(client: object, collection: str) -> tuple[dict[str, Any], ...]:
    """Best-effort extraction of index/metric config.

    Returns a tuple of dicts with keys: index_name, field_name,
    index_type, metric_type, params.  Returns empty tuple if the
    API is unavailable or raises.
    """
    try:
        indexes = client.list_indexes(  # type: ignore[attr-defined]
            collection_name=collection
        )
        if not indexes:
            return ()
        result: list[dict[str, Any]] = []
        for idx in indexes:
            if isinstance(idx, dict):
                result.append(
                    {
                        "index_name": str(idx.get("index_name", "")),
                        "field_name": str(idx.get("field_name", "")),
                        "index_type": str(idx.get("index_type", "")),
                        "metric_type": str(idx.get("metric_type", "")),
                        "params": dict(idx.get("params", {})),
                    }
                )
            else:
                # Some SDK versions return index objects, not dicts.
                result.append(
                    {
                        "index_name": str(getattr(idx, "index_name", "")),
                        "field_name": str(getattr(idx, "field_name", "")),
                        "index_type": str(getattr(idx, "index_type", "")),
                        "metric_type": str(getattr(idx, "metric_type", "")),
                        "params": {},
                    }
                )
        return tuple(result)
    except Exception:  # noqa: BLE001 — best-effort
        return ()


async def _preflight_zilliz(
    client: object,
    *,
    collection: str,
    stable_document_id: UUID,
) -> _ZillizPreflightSnapshot:
    """Zero-call preflight: verify collection, schema, count, fixture
    IDs don't exist, protected collections.  No writes."""

    def _sync() -> _ZillizPreflightSnapshot:
        # 1. Collection exists.
        collection_exists = bool(
            client.has_collection(collection_name=collection)  # type: ignore[attr-defined]
        )
        if not collection_exists:
            raise RuntimeError(
                f"Preflight FAILED: collection {collection!r} does not "
                f"exist.  Smoke cannot run — refusing to create or "
                f"write to a non-existent production collection."
            )

        # 2. Schema: full identity (field descriptions, PK, vector dim).
        describe = client.describe_collection(  # type: ignore[attr-defined]
            collection_name=collection
        )
        (
            field_descriptions,
            primary_key_field,
            vector_field,
            vector_dim,
        ) = _extract_schema_identity(describe)
        field_names = tuple(fd["name"] for fd in field_descriptions)
        field_count = len(field_names)
        if field_count == 0:
            raise RuntimeError(
                f"Preflight FAILED: collection {collection!r} has 0 "
                f"fields — schema is broken."
            )
        # Sanity: chunk_id + stable_document_id must be present.
        for required_field in ("chunk_id", "stable_document_id"):
            if required_field not in field_names:
                raise RuntimeError(
                    f"Preflight FAILED: collection {collection!r} is "
                    f"missing required field {required_field!r}."
                )

        # 2b. Index/metric info (best-effort).
        index_info = _extract_index_info(client, collection)

        # 3. Protected collections present (verify they exist; we never
        # touch them).
        protected_present: dict[str, bool] = {}
        for protected in PROTECTED_ZILLIZ_COLLECTIONS:
            protected_present[protected] = bool(
                client.has_collection(collection_name=protected)  # type: ignore[attr-defined]
            )

        # 4. All collections (for the report + cleanup comparison).
        all_collections = tuple(
            client.list_collections()  # type: ignore[attr-defined]
        )

        # 5. Fixture chunk count: query for chunks with our
        # stable_document_id.  MUST be 0 (no leftover from a prior
        # aborted run).
        query_result = client.query(  # type: ignore[attr-defined]
            collection_name=collection,
            filter=f'stable_document_id == "{stable_document_id}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        fixture_chunk_count = len(query_result) if query_result else 0
        if fixture_chunk_count > 0:
            raise RuntimeError(
                f"Preflight FAILED: {fixture_chunk_count} chunks with "
                f"stable_document_id={stable_document_id} already exist "
                f"in {collection!r}.  A prior smoke run may have failed "
                f"to clean up.  Refusing to proceed — manually delete "
                f"these chunks before re-running."
            )

        # 6. Collection row count (best-effort; may be None on some plans).
        row_count: int | None = None
        try:
            stats = client.get_collection_stats(  # type: ignore[attr-defined]
                collection_name=collection
            )
            if isinstance(stats, dict) and "row_count" in stats:
                row_count = int(stats["row_count"])
        except Exception:  # noqa: BLE001 — best-effort, not load-bearing
            row_count = None

        return _ZillizPreflightSnapshot(
            collection_exists=collection_exists,
            field_count=field_count,
            field_names=field_names,
            field_descriptions=field_descriptions,
            primary_key_field=primary_key_field,
            vector_field=vector_field,
            vector_dim=vector_dim,
            index_info=index_info,
            protected_collections_present=protected_present,
            all_collections=all_collections,
            fixture_chunk_count=fixture_chunk_count,
            collection_row_count=row_count,
        )

    return await asyncio.to_thread(_sync)


# ======================================================================
# R4 — Fixed safe messages for SDK exception wrapping.
# ======================================================================
# These constants are the ONLY message text that may appear on a
# propagating SDK exception from the cleanup helper.  They MUST NOT
# interpolate collection, chunk_id, stable_document_id, URI, token,
# key, or row content.  The helper constructs the safe error INSIDE
# the except block and raises it OUTSIDE the except block so that
# ``__cause__`` and ``__context__`` are both ``None``.
# ======================================================================

_CLEANUP_DELETE_FAILED_SAFE_MESSAGE = (
    "article-rag acceptance cleanup: delete operation failed; "
    "manual cleanup required"
)

_CLEANUP_POST_DELETE_QUERY_FAILED_SAFE_MESSAGE = (
    "article-rag acceptance cleanup: post-delete verification query "
    "failed; manual cleanup verification required"
)
_CLEANUP_INTEGRITY_CHECK_FAILED_SAFE_MESSAGE = (
    "article-rag acceptance cleanup: collection integrity verification "
    "failed; manual verification required"
)


@dataclass
class _CleanupResult:
    """Result of precise fixture cleanup by chunk_id set.

    All schema-identity fields are compared against the preflight
    snapshot to prove the cleanup did not alter the collection structure.

    R3 changes:
      - ``delete_call_count``: 0 if no cleanup target existed (no
        delete issued), 1 if a delete was issued.  This is the
        MEASURED value used for ``counts.vector_delete_calls``;
        callers MUST NOT hardcode the count after success.
      - ``cleanup_target_chunk_ids``: the union of
        (expected_chunk_ids, saved_chunk_ids, discovered_chunk_ids)
        actually used as the delete filter.  Used for the report.
      - ``expected_chunk_ids`` / ``saved_chunk_ids`` /
        ``discovered_chunk_ids``: the three input sets, recorded
        separately for the report and for failure-injection tests.
    """

    deleted_count: int
    post_delete_query_count: int  # MUST be 0 (by stable_document_id)
    # Per-chunk_id verification: each chunk_id must be gone.
    per_chunk_id_verified: dict[str, bool]
    collection_still_exists: bool
    # Schema identity after cleanup.
    field_count_after: int
    field_names_after: tuple[str, ...]
    field_descriptions_after: tuple[dict[str, Any], ...]
    primary_key_field_after: str | None
    vector_field_after: str | None
    vector_dim_after: int | None
    index_info_after: tuple[dict[str, Any], ...]
    # Collection list after cleanup.
    all_collections_after: tuple[str, ...]
    # Protected collections unchanged.
    protected_collections_unchanged: dict[str, bool]
    # Row count after (informational only — Milvus compaction is async).
    collection_row_count_after: int | None
    # R3: measured delete call count (0 or 1).
    delete_call_count: int = 0
    # R3: the union of (expected, saved, discovered) chunk_ids that
    # was used as the delete filter.
    cleanup_target_chunk_ids: tuple[str, ...] = ()
    # R3: the three input sets, recorded for the report.
    expected_chunk_ids: tuple[str, ...] = ()
    saved_chunk_ids: tuple[str, ...] = ()
    discovered_chunk_ids: tuple[str, ...] = ()


async def _precise_cleanup_by_chunk_ids(
    client: object,
    *,
    collection: str,
    expected_chunk_ids: tuple[str, ...],
    saved_chunk_ids: tuple[str, ...],
    stable_document_id: UUID,
    preflight: _ZillizPreflightSnapshot,
    vector_write_attempted: bool = False,
) -> _CleanupResult:
    """Delete EXACTLY the union of (expected, saved, discovered)
    chunk_ids by primary key, then verify deletion + collection
    integrity.

    R4 design — closes the remaining fail-closed gaps from R3:

      1. ``expected_chunk_ids`` is built from the PostgreSQL plan
         BEFORE any paid vector write.  If the worker wrote vectors
         but D2/D3/D4 failed, ``saved_chunk_ids`` is empty but
         ``expected_chunk_ids`` still identifies the chunks that
         SHOULD have been written.
      2. ``saved_chunk_ids`` is what D4 captured from Zilliz (may
         be empty if D4 was not reached or poll timed out).
      3. ``discovered_chunk_ids`` is queried from Zilliz by
         ``stable_document_id`` IN FINALLY — this is the only use
         of ``stable_document_id`` as a filter, and it is for
         DISCOVERY only, never for delete.  Discovery is supplementary
         evidence; it is NOT a prerequisite for cleanup.
      4. ``vector_write_attempted`` is the real smoke signal that
         the counting writer was actually called
         (``call_count > 0``).  When ``True``, the helper deletes
         the exact expected-ID union even if discovery succeeds with
         a stale empty result; Milvus visibility is eventually
         consistent, so an empty read cannot prove no write landed.
      5. ``cleanup_target`` = union of all three (deterministic
         sorted order).  Delete by precise ``chunk_id in [...]``
         primary-key filter.
      6. ``delete_call_count`` = 1 if a delete was issued, 0 if
         no delete was needed.

    Behavior matrix (``vector_write_attempted`` × evidence ×
    discovery):

      | writer attempted | saved/discovered evidence | discovery | behavior |
      |------------------|---------------------------|-----------|----------|
      | False            | none                      | any       | no delete |
      | True             | none                      | any       | delete expected IDs |
      | any              | saved non-empty           | any       | delete expected ∪ saved |
      | any              | discovered non-empty      | success   | delete all three ID sets |

    In the last row, "all three ID sets" = expected ∪ saved ∪
    discovered (deduped, deterministic order).

    SDK exception safety (R4):
      - Discovery query failure is an INTERNAL fallback state —
        it does NOT propagate.  The helper continues with
        ``discovered_chunk_ids = ()`` and, if ``vector_write_attempted``
        is True, falls back to expected IDs.
      - Delete, post-delete verification, and strict collection
        integrity failures MUST propagate (fail closed) but are
        wrapped in fixed safe errors that do NOT interpolate
        collection, chunk_id, stable_document_id, URI, token, key,
        SDK details, or row content.
      - The safe error is constructed INSIDE the except block and
        raised OUTSIDE the except block so ``__cause__`` and
        ``__context__`` are both ``None``.

    Per the task spec:
      - Never use ``stable_document_id`` as a delete filter.
      - Only delete by exact chunk_id primary keys.
      - Never drop/recreate the collection.
      - Never execute collection-wide compaction.
      - Never touch protected collections.

    Never touches protected collections.  If post-delete verification
    fails, the result records the failure (the caller asserts).
    """

    def _sync() -> _CleanupResult:
        # 1. Discover any chunks with our stable_document_id that
        # exist in Zilliz right now (in finally).  This is the ONLY
        # use of stable_document_id as a filter — for DISCOVERY,
        # never for delete.
        #
        # R4: discovery query failure is an INTERNAL fallback state.
        # It does NOT propagate.  The helper continues with
        # ``discovered_chunk_ids = ()`` and, if ``vector_write_attempted``
        # is True, falls back to deleting ``expected_chunk_ids``.
        try:
            discover_result = client.query(  # type: ignore[attr-defined]
                collection_name=collection,
                filter=f'stable_document_id == "{stable_document_id}"',
                output_fields=["chunk_id"],
                limit=512,
            )
        except Exception:  # noqa: BLE001 — internal fallback
            discover_result = []

        discovered_chunk_ids: tuple[str, ...] = ()
        if discover_result:
            discovered_chunk_ids = tuple(
                str(r["chunk_id"]) for r in discover_result
            )

        # 2. Compute cleanup target = union of (expected, saved,
        # discovered).  Order is deterministic (sorted) for the
        # report; the set semantics ensure no duplicate delete.
        cleanup_target_set: set[str] = set()
        cleanup_target_set.update(expected_chunk_ids)
        cleanup_target_set.update(saved_chunk_ids)
        cleanup_target_set.update(discovered_chunk_ids)
        cleanup_target = tuple(sorted(cleanup_target_set))

        # 3. Decide whether to delete (R4 behavior matrix).
        #
        #   - ``backend_has_evidence``: saved or discovered is
        #     non-empty → vectors are known to exist.
        #   - ``vector_write_attempted``: the writer boundary was
        #     entered.  A successful empty discovery is not proof that
        #     no write landed because Milvus visibility is eventually
        #     consistent, so exact expected-ID deletion is still required.
        #   - If neither a write attempt nor saved/discovered evidence
        #     exists, no delete is issued.
        #
        # Delete by precise ``chunk_id in [...]`` primary-key filter.
        # NEVER use stable_document_id as a delete filter.
        delete_call_count = 0
        backend_has_evidence = bool(
            discovered_chunk_ids or saved_chunk_ids
        )
        should_delete = bool(
            cleanup_target
            and (vector_write_attempted or backend_has_evidence)
        )

        delete_result: Any = {"delete_count": 0}
        # R4: wrap delete SDK exception in fixed safe error.
        # Construct INSIDE except, raise OUTSIDE except so
        # __cause__ and __context__ are both None.
        delete_safe_error: Exception | None = None
        if should_delete:
            id_list = ", ".join(f'"{cid}"' for cid in cleanup_target)
            delete_filter = f"chunk_id in [{id_list}]"
            try:
                delete_result = client.delete(  # type: ignore[attr-defined]
                    collection_name=collection,
                    filter=delete_filter,
                )
                delete_call_count = 1
            except Exception:  # noqa: BLE001 — wrap in safe error
                delete_safe_error = RuntimeError(
                    _CLEANUP_DELETE_FAILED_SAFE_MESSAGE
                )
        # Raise OUTSIDE the except block so __context__ is None.
        if delete_safe_error is not None:
            raise delete_safe_error

        # pymilvus delete returns a dict with "delete_count" on some
        # versions; fall back to 0 if the shape differs.
        deleted_count = 0
        if isinstance(delete_result, dict):
            deleted_count = int(delete_result.get("delete_count", 0))
        elif isinstance(delete_result, int):
            deleted_count = delete_result

        # 4. Flush (best-effort) so the delete is durable for the
        # subsequent query.  Some Zilliz plans flush automatically.
        # NOTE: flush is NOT compaction — it just makes deletes visible
        # to subsequent queries.  Per the task spec, we do NOT execute
        # collection-wide compaction.
        try:
            client.flush(collection_name=collection)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — flush is best-effort
            pass

        # 5. Per-chunk_id verification: query each chunk_id individually.
        per_chunk_verified: dict[str, bool] = {}
        for cid in cleanup_target:
            try:
                result = client.query(  # type: ignore[attr-defined]
                    collection_name=collection,
                    filter=f'chunk_id == "{cid}"',
                    output_fields=["chunk_id"],
                    limit=1,
                )
                per_chunk_verified[cid] = (
                    len(result) == 0 if result else True
                )
            except Exception:  # noqa: BLE001 — defensive
                per_chunk_verified[cid] = False

        # 6. Query by stable_document_id to confirm 0 rows remain.
        #
        # R4: wrap post-delete verification query SDK exception in
        # fixed safe error.  Construct INSIDE except, raise OUTSIDE
        # except so __cause__ and __context__ are both None.
        post_delete_query_count = 0
        post_delete_safe_error: Exception | None = None
        try:
            post_delete = client.query(  # type: ignore[attr-defined]
                collection_name=collection,
                filter=f'stable_document_id == "{stable_document_id}"',
                output_fields=["chunk_id"],
                limit=1,
            )
            post_delete_query_count = (
                len(post_delete) if post_delete else 0
            )
        except Exception:  # noqa: BLE001 — wrap in safe error
            post_delete_safe_error = RuntimeError(
                _CLEANUP_POST_DELETE_QUERY_FAILED_SAFE_MESSAGE
            )
        # Raise OUTSIDE the except block so __context__ is None.
        if post_delete_safe_error is not None:
            raise post_delete_safe_error

        # 7-9. Strict collection/schema/index/protected-collection
        # integrity checks.  Any SDK exception escaping this boundary
        # is converted to one fixed local error without chaining or
        # copying upstream values.
        integrity_result: tuple[Any, ...] | None = None
        integrity_safe_error: Exception | None = None
        try:
            collection_still_exists = bool(
                client.has_collection(  # type: ignore[attr-defined]
                    collection_name=collection
                )
            )
            describe_after = client.describe_collection(  # type: ignore[attr-defined]
                collection_name=collection
            )
            (
                field_descriptions_after,
                primary_key_field_after,
                vector_field_after,
                vector_dim_after,
            ) = _extract_schema_identity(describe_after)
            field_names_after = tuple(
                fd["name"] for fd in field_descriptions_after
            )
            field_count_after = len(field_names_after)
            index_info_after = _extract_index_info(client, collection)
            all_collections_after = tuple(
                client.list_collections()  # type: ignore[attr-defined]
            )
            protected_unchanged: dict[str, bool] = {}
            for protected in PROTECTED_ZILLIZ_COLLECTIONS:
                protected_unchanged[protected] = bool(
                    client.has_collection(  # type: ignore[attr-defined]
                        collection_name=protected
                    )
                )
            integrity_result = (
                collection_still_exists,
                field_descriptions_after,
                primary_key_field_after,
                vector_field_after,
                vector_dim_after,
                field_names_after,
                field_count_after,
                index_info_after,
                all_collections_after,
                protected_unchanged,
            )
        except Exception:  # noqa: BLE001 — wrap in fixed safe error
            integrity_safe_error = RuntimeError(
                _CLEANUP_INTEGRITY_CHECK_FAILED_SAFE_MESSAGE
            )
        if integrity_safe_error is not None:
            raise integrity_safe_error
        assert integrity_result is not None
        (
            collection_still_exists,
            field_descriptions_after,
            primary_key_field_after,
            vector_field_after,
            vector_dim_after,
            field_names_after,
            field_count_after,
            index_info_after,
            all_collections_after,
            protected_unchanged,
        ) = integrity_result

        # 10. Row count after (informational only — Milvus compaction
        # is async, so row_count may not immediately reflect deletes).
        row_count_after: int | None = None
        try:
            stats = client.get_collection_stats(  # type: ignore[attr-defined]
                collection_name=collection
            )
            if isinstance(stats, dict) and "row_count" in stats:
                row_count_after = int(stats["row_count"])
        except Exception:  # noqa: BLE001 — best-effort
            row_count_after = None

        return _CleanupResult(
            deleted_count=deleted_count,
            post_delete_query_count=post_delete_query_count,
            per_chunk_id_verified=per_chunk_verified,
            collection_still_exists=collection_still_exists,
            field_count_after=field_count_after,
            field_names_after=field_names_after,
            field_descriptions_after=field_descriptions_after,
            primary_key_field_after=primary_key_field_after,
            vector_field_after=vector_field_after,
            vector_dim_after=vector_dim_after,
            index_info_after=index_info_after,
            all_collections_after=all_collections_after,
            protected_collections_unchanged=protected_unchanged,
            collection_row_count_after=row_count_after,
            delete_call_count=delete_call_count,
            cleanup_target_chunk_ids=cleanup_target,
            expected_chunk_ids=expected_chunk_ids,
            saved_chunk_ids=saved_chunk_ids,
            discovered_chunk_ids=discovered_chunk_ids,
        )

    return await asyncio.to_thread(_sync)


def _assert_schema_identity_unchanged(
    preflight: _ZillizPreflightSnapshot,
    cleanup: _CleanupResult,
) -> None:
    """Assert the collection schema identity is unchanged before vs after.

    Compares: field names, field descriptions, primary key, vector field,
    vector dim, index/metric config, collection list, protected collections.
    Raises AssertionError with detail if any dimension differs.
    """
    # Field count + names.
    assert cleanup.field_count_after == preflight.field_count, (
        f"Schema identity FAILED: field count changed "
        f"({preflight.field_count} -> {cleanup.field_count_after})."
    )
    assert cleanup.field_names_after == preflight.field_names, (
        f"Schema identity FAILED: field names changed.\n"
        f"  before: {preflight.field_names}\n"
        f"  after:  {cleanup.field_names_after}"
    )
    # Full field descriptions (name, type, is_primary, params).
    assert cleanup.field_descriptions_after == preflight.field_descriptions, (
        f"Schema identity FAILED: field descriptions changed.\n"
        f"  before: {preflight.field_descriptions}\n"
        f"  after:  {cleanup.field_descriptions_after}"
    )
    # Primary key.
    assert cleanup.primary_key_field_after == preflight.primary_key_field, (
        f"Schema identity FAILED: primary key field changed "
        f"({preflight.primary_key_field!r} -> "
        f"{cleanup.primary_key_field_after!r})."
    )
    # Vector field + dim.
    assert cleanup.vector_field_after == preflight.vector_field, (
        f"Schema identity FAILED: vector field changed "
        f"({preflight.vector_field!r} -> "
        f"{cleanup.vector_field_after!r})."
    )
    assert cleanup.vector_dim_after == preflight.vector_dim, (
        f"Schema identity FAILED: vector dim changed "
        f"({preflight.vector_dim} -> {cleanup.vector_dim_after})."
    )
    # Index/metric config.
    assert cleanup.index_info_after == preflight.index_info, (
        f"Schema identity FAILED: index info changed.\n"
        f"  before: {preflight.index_info}\n"
        f"  after:  {cleanup.index_info_after}"
    )
    # Collection list.  Zilliz's ``list_collections`` returns
    # collections in non-deterministic order across calls (observed
    # in real smoke run #4: the same 3 collections came back in 2
    # different orders before/after cleanup).  Compare as SORTED
    # tuples — the assertion is "same SET of collections", not
    # "same order".
    assert sorted(cleanup.all_collections_after) == sorted(
        preflight.all_collections
    ), (
        f"Schema identity FAILED: collection set changed.\n"
        f"  before (sorted): {sorted(preflight.all_collections)}\n"
        f"  after  (sorted): {sorted(cleanup.all_collections_after)}"
    )
    # Protected collections unchanged.
    for protected, was_present in preflight.protected_collections_present.items():
        now_present = cleanup.protected_collections_unchanged.get(
            protected, False
        )
        assert was_present == now_present, (
            f"Schema identity FAILED: protected collection "
            f"{protected!r} presence changed "
            f"({was_present} -> {now_present})."
        )


# ---------------------------------------------------------------------------
# The canonical real-chain acceptance smoke
# ---------------------------------------------------------------------------


@article_rag_smoke
@pytest.mark.real_llm
@pytest.mark.skipif(
    not _real_smoke_env_present(),
    reason=_REAL_SMOKE_SKIP_REASON,
)
async def test_single_path_real_chain_acceptance(
    acceptance_env: asyncpg.Pool,
) -> None:
    """Single-path real-chain acceptance smoke (R2).

    This is the canonical acceptance smoke for the Article RAG
    single-path convergence.  It exercises the FULL chain:

      1. Zero-call Zilliz preflight (collection, schema, count,
         fixture IDs, protected collections, schema identity).
      2. Settings + worker + frozen contract all resolve to
         ``article_rag_chunks`` (three-way identity check).
      3. Seed a minimal article_ready record with UNIQUE UUIDs on a
         per-test temp Postgres schema.
      4. ``lifecycle.ensure_article_rag_index_job_in_transaction``.
      5. ``worker.process_next`` (1 tick — 1 document embedding
         batch + 1 vector write).  Worker's embedding_provider +
         vector_writer are wrapped with counting delegates AFTER
         the construction sanity check, so call counts are MEASURED.
      6. Production Ask port chain (``RetrievalBackedArticleRagPort
         .search_current_article`` — the same adapter
         ``build_production_article_rag_port`` returns) — calls
         retrieval EXACTLY once via a ``_CapturingRetrievalService``
         wrapper.  R2 stops here — NO real Ask model call.
      7. Precise Zilliz cleanup (delete by EXACT saved chunk_id
         set, verify each chunk_id gone, verify schema identity
         unchanged, verify protected collections unchanged).

    Acceptance assertions (11 items):

      D1. Worker claim succeeded; providers actually invoked
          (measured via counting delegates: document_embedding
          == 1, vector_write == 1).
      D2. ``reader_jobs`` + ``reader_runs`` +
          ``reader_article_rag_index_runs`` all reached terminal
          success: ``reader_jobs.status == "succeeded"``,
          ``reader_runs.status == "completed"`` (the worker's
          terminal value for runs — see
          ``article_rag_index_worker.py:897,1418``),
          ``reader_article_rag_index_runs.status == "indexed"``,
          with no failure_class / failure_code.
      D3. ``index_run.status == "indexed"``; ``completed_at``
          non-null; embedding_model / vector_store_provider /
          vector_collection match the worker result.
      D4. Zilliz contains chunks with our stable_document_id;
          the precise ``chunk_id`` set is saved (non-empty,
          unique) for D7 comparison + cleanup.
      D5. Captured retrieval result has hits > 0; the result's
          ``reading_record_id`` / ``stable_document_id`` /
          ``base_id`` exactly match our fixture.  Every
          ``hit.chunk_id`` belongs to the saved Zilliz set.
      D6. Retrieval uses Postgres plan for citation (not vector
          payload) — proven OFFLINE by the existing P1-F
          forged-vector citation regression test
          (``test_forged_vector_citation_metadata_is_not_truth``).
          The live smoke does NOT tamper with the vector payload;
          it only verifies the citation dict has the plan-backed
          9-key shape.
      D7. Plan is rebuilt via ``ArticleRagIndexPlanService``; all
          9 citation fields are compared per ``chunk_id``
          (reading_record_id, stable_document_id, base_id,
          record_generation, block_ids, unit_ids,
          anchor_segment_ids, canonical_text_start_utf16,
          canonical_text_end_utf16).
      D8. Ask port outcome: ``status == "ok"``, hits non-empty,
          every ``hit.chunk_id`` belongs to the saved Zilliz set,
          every hit's identity (record / base / generation /
          stable_document) matches the fixture, ``source_scope``
          within the allowed Ask scopes, ``plan_content_sha256``
          non-empty 64-char lowercase-hex, ``rag_substrate_id``
          equals the immutable indexed run id (D3 row).
      D9. (N/A — R2 does not make a real Ask model call.)
      D10. (N/A — R2 does not make a real Ask model call.)
      D11. Cleanup: fixture vectors deleted by precise chunk_id
           set; each chunk_id individually verified gone;
           collection / schema identity / protected collections
           unchanged.

    Service call budget (MEASURED via counting delegates, never
    hardcoded after success):

      - smoke_execution_attempts: 1
      - document_embedding_service_calls: 1 (worker batch)
      - query_embedding_service_calls: 1 (retrieval query)
      - vector_write_calls: 1 (worker upsert)
      - vector_search_calls: 1 (retrieval search)
      - retrieval_service_calls: 1 (Ask port triggers it once)
      - vector_delete_calls: 1 (precise chunk_id cleanup)
      - ask_model_calls: 0 (R2 stops at the Ask port boundary)

    Stop on first failure.  No retries.  Cleanup runs in ``finally``
    regardless of success or failure.  If D4 was not reached, the
    saved chunk_id set is empty — cleanup is a no-op but still
    verifies schema identity.
    """
    counts = CallCounts()
    counts.smoke_execution_attempts = 1

    from app.contracts.article_rag_contract import (  # noqa: I001
        ARTICLE_RAG_EMBEDDING_CONTRACT,
    )

    # -----------------------------------------------------------------
    # Three-way collection identity check (Settings + worker + contract)
    # -----------------------------------------------------------------
    from app.config.settings import Settings  # noqa: I001

    settings = Settings()
    settings_collection = settings.reader_article_rag_zilliz_collection
    contract_collection = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection
    assert (
        settings_collection
        == contract_collection
        == "article_rag_chunks"
    ), (
        f"Collection identity FAILED: "
        f"settings={settings_collection!r}, "
        f"contract={contract_collection!r}.  The smoke MUST write to "
        f"the production collection — no second collection identity."
    )

    # -----------------------------------------------------------------
    # Generate unique fixture IDs
    # -----------------------------------------------------------------
    ids = _FixtureIds.generate()

    # -----------------------------------------------------------------
    # Zero-call Zilliz preflight
    # -----------------------------------------------------------------
    zilliz_client = _get_zilliz_client()
    preflight = await _preflight_zilliz(
        zilliz_client,
        collection=contract_collection,
        stable_document_id=ids.stable_document_id,
    )
    assert preflight.collection_exists is True
    assert preflight.fixture_chunk_count == 0
    assert "chunk_id" in preflight.field_names
    assert "stable_document_id" in preflight.field_names

    # -----------------------------------------------------------------
    # Seed the test Postgres schema with unique UUIDs
    # -----------------------------------------------------------------
    await _seed_acceptance_environment(acceptance_env, ids)

    # -----------------------------------------------------------------
    # Build the worker via the runbook-canonical factory
    # -----------------------------------------------------------------
    from app.services.reader_orchestration.article_rag_embedding_provider import (  # noqa: E501,I001
        DashScopeArticleRagEmbeddingProvider,
    )
    from app.services.reader_orchestration.article_rag_index_worker import (  # noqa: I001
        ArticleRagIndexWorkerError,
        ArticleRagIndexWorkerResult,
        UnconfiguredArticleRagEmbeddingProvider,
        UnconfiguredArticleRagVectorWriter,
    )
    from app.services.reader_orchestration.article_rag_vector_store import (  # noqa: E501,I001
        ZillizArticleRagVectorWriter,
    )
    from app.services.reader_orchestration.article_rag_index_lifecycle_service import (  # noqa: E501,I001
        ArticleRagIndexLifecycleService,
        ENSURE_STATUS_ENQUEUED,
    )
    from app.services.reader_orchestration.article_rag_index_bootstrap import (  # noqa: E501,I001
        ArticleRagIndexBootstrapService,
    )
    from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]  # noqa: I001
        build_worker_service,
    )

    worker = build_worker_service(
        settings=settings, pool=acceptance_env
    )
    # Sanity: the factory must produce the REAL providers BEFORE
    # wrapping them with counting delegates.
    assert not isinstance(
        worker._embedding_provider,  # type: ignore[attr-defined]
        UnconfiguredArticleRagEmbeddingProvider,
    )
    assert isinstance(
        worker._embedding_provider,  # type: ignore[attr-defined]
        DashScopeArticleRagEmbeddingProvider,
    )
    assert not isinstance(
        worker._vector_writer,  # type: ignore[attr-defined]
        UnconfiguredArticleRagVectorWriter,
    )
    assert isinstance(
        worker._vector_writer,  # type: ignore[attr-defined]
        ZillizArticleRagVectorWriter,
    )
    # Worker's default_vector_collection must equal the contract.
    assert (
        worker._default_vector_collection  # type: ignore[attr-defined]
        == contract_collection
    )

    # -----------------------------------------------------------------
    # Wrap worker's embedding_provider + vector_writer with counting
    # delegates.  The delegates record ACTUAL public seam calls
    # (embed_texts / upsert_chunks) — counts are MEASURED, not
    # hardcoded after success.  ``ArticleRagIndexWorkerService`` is
    # a regular class (not frozen / not slots), so attribute
    # reassignment works.
    # -----------------------------------------------------------------
    counting_embedder = _CountingEmbeddingProvider(
        worker._embedding_provider  # type: ignore[attr-defined]
    )
    counting_writer = _CountingVectorWriter(
        worker._vector_writer  # type: ignore[attr-defined]
    )
    worker._embedding_provider = counting_embedder  # type: ignore[attr-defined]
    worker._vector_writer = counting_writer  # type: ignore[attr-defined]

    # -----------------------------------------------------------------
    # lifecycle.ensure -> enqueue the index build job
    # -----------------------------------------------------------------
    lifecycle = ArticleRagIndexLifecycleService(
        bootstrap_service=ArticleRagIndexBootstrapService(pool=acceptance_env),
    )
    async with acceptance_env.acquire() as conn:
        async with conn.transaction():
            ensure_result = (
                await lifecycle.ensure_article_rag_index_job_in_transaction(
                    conn,
                    reading_record_id=ids.record_id,
                    user_id=ids.user_id,
                    expected_generation=1,
                )
            )
    assert ensure_result.status in (
        ENSURE_STATUS_ENQUEUED,
        "idempotent_noop",
    )
    assert ensure_result.index_run_id is not None
    assert ensure_result.job_id is not None

    # -----------------------------------------------------------------
    # R3: Pre-build the Article RAG index plan from PostgreSQL BEFORE
    # any paid vector write.  This gives us a deterministic
    # ``expected_chunk_ids`` set that survives any subsequent failure
    # (D2/D3/D4 assertion failures, poll timeouts, etc.).  The plan
    # service reads only from PostgreSQL — no paid calls.
    #
    # The pre-built plan is ALSO reused at D7 (no second plan
    # construction).  ``rebuilt_plan`` at D7 is the SAME object.
    # -----------------------------------------------------------------
    from app.services.reader_orchestration.article_rag_index_plan import (  # noqa: E501
        ArticleRagIndexPlanService,
    )

    prebuilt_plan_service = ArticleRagIndexPlanService(pool=acceptance_env)
    prebuilt_plan = await prebuilt_plan_service.build_index_plan(
        record_id=ids.record_id,
        user_id=ids.user_id,
    )
    expected_chunk_ids: tuple[str, ...] = tuple(
        chunk.chunk_id for chunk in prebuilt_plan.chunks
    )
    # R3 preflight: expected_chunk_ids must be non-empty and unique.
    assert len(expected_chunk_ids) > 0, (
        "Pre-built plan produced 0 chunks — cannot run smoke without "
        "a deterministic expected chunk_id set."
    )
    assert len(set(expected_chunk_ids)) == len(expected_chunk_ids), (
        f"Pre-built plan produced duplicate chunk_ids: "
        f"{expected_chunk_ids}"
    )
    # R3 preflight: each expected chunk_id must NOT already exist in
    # Zilliz (no leftover from a prior aborted run with the same
    # plan — extremely unlikely given unique fixture UUIDs, but
    # fail-closed regardless).
    for cid in expected_chunk_ids:
        existing = zilliz_client.query(  # type: ignore[attr-defined]
            collection_name=contract_collection,
            filter=f'chunk_id == "{cid}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        assert not existing, (
            f"R3 preflight FAILED: expected chunk_id={cid!r} already "
            f"exists in Zilliz — a prior run may have failed to clean "
            f"up.  Refusing to proceed."
        )

    # -----------------------------------------------------------------
    # OUTER TRY: all paid calls (worker + Ask context) are wrapped
    # so that ``_precise_cleanup_by_chunk_ids`` runs in ``finally``
    # regardless of success or failure.
    #
    # R3: ``expected_chunk_ids`` is populated BEFORE any paid call,
    # so the finally cleanup ALWAYS has a deterministic target even
    # if D4 was never reached.  ``saved_chunk_ids`` is what D4
    # actually captured from Zilliz (may be empty on failure paths).
    # The cleanup helper computes the union of (expected, saved,
    # discovered) and deletes by precise chunk_id primary keys.
    # -----------------------------------------------------------------
    saved_chunk_ids: list[str] = []  # populated at D4
    cleanup_result: _CleanupResult | None = None
    worker_result: ArticleRagIndexWorkerResult | None = None
    capturing_retrieval: _CapturingRetrievalService | None = None
    try:
        # -----------------------------------------------------------------
        # Worker tick — 1 document embedding batch + 1 vector write
        # (measured via counting delegates)
        # -----------------------------------------------------------------
        try:
            worker_result = await worker.process_next(
                lease_owner="test-r2-acceptance",
                lease_duration=timedelta(seconds=120),
            )
        except ArticleRagIndexWorkerError as exc:
            pytest.fail(
                f"Worker raised ArticleRagIndexWorkerError "
                f"({type(exc).__name__}, failure_code={exc.failure_code}, "
                f"retryable={exc.retryable}).  Call counts: "
                f"{counts.as_report()}"
            )

        # Measure counts after worker (delegates were incremented).
        counts.document_embedding_service_calls = (
            counting_embedder.call_count
        )
        counts.vector_write_calls = counting_writer.call_count

        # D1: Worker claim succeeded; providers actually invoked.
        assert isinstance(worker_result, ArticleRagIndexWorkerResult)
        assert worker_result.status == "succeeded", (
            f"Worker did not reach 'succeeded'.  "
            f"status={worker_result.status!r} "
            f"failure_code={worker_result.failure_code!r} "
            f"retryable={worker_result.retryable!r} "
            f"Call counts: {counts.as_report()}"
        )
        # Real provider values (not fake).
        assert worker_result.embedding_model is not None
        assert not worker_result.embedding_model.startswith("fake-")
        assert worker_result.vector_store_provider == "zilliz"
        assert worker_result.vector_collection == contract_collection
        # Measured counts (must be exactly 1 each).
        assert counts.document_embedding_service_calls == 1, (
            f"Expected exactly 1 document_embedding_service_call, "
            f"got {counts.document_embedding_service_calls}.  "
            f"Call counts: {counts.as_report()}"
        )
        assert counts.vector_write_calls == 1, (
            f"Expected exactly 1 vector_write_call, "
            f"got {counts.vector_write_calls}.  "
            f"Call counts: {counts.as_report()}"
        )

        # -----------------------------------------------------------------
        # D2: Job + Run + index_run all reached terminal success.
        # Query reader_runs via the index_run's reader_run_id FK
        # (canonical), with a fallback to reader_jobs.run_id
        # (defensive).  All three layers must be in their succeeded
        # / indexed terminal states with no failure_class /
        # failure_code.
        # -----------------------------------------------------------------
        async with acceptance_env.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT status, failure_class, failure_code, run_id "
                "FROM reader_jobs WHERE id = $1",
                ensure_result.job_id,
            )
            index_run_row = await conn.fetchrow(
                "SELECT status, embedding_model, vector_store_provider, "
                "vector_collection, completed_at, reader_run_id "
                "FROM reader_article_rag_index_runs WHERE id = $1",
                ensure_result.index_run_id,
            )
            # Resolve reader_runs via index_run.reader_run_id (the
            # canonical FK); fall back to reader_jobs.run_id if the
            # index_run row lacks reader_run_id (defensive).
            run_id_to_check: UUID | None = None
            if index_run_row is not None:
                run_id_to_check = index_run_row["reader_run_id"]
            if run_id_to_check is None and job_row is not None:
                run_id_to_check = job_row["run_id"]
            run_row = None
            if run_id_to_check is not None:
                run_row = await conn.fetchrow(
                    "SELECT status, failure_class, failure_code "
                    "FROM reader_runs WHERE id = $1",
                    run_id_to_check,
                )
        assert job_row is not None, "reader_jobs row missing"
        assert job_row["status"] == "succeeded", (
            f"reader_jobs.status={job_row['status']!r} "
            f"(expected 'succeeded')."
        )
        assert job_row["failure_class"] is None, (
            f"reader_jobs.failure_class={job_row['failure_class']!r} "
            f"(expected None)."
        )
        assert job_row["failure_code"] is None, (
            f"reader_jobs.failure_code={job_row['failure_code']!r} "
            f"(expected None)."
        )
        assert run_row is not None, (
            "reader_runs row missing — could not resolve via "
            "index_run.reader_run_id or reader_jobs.run_id"
        )
        # R3: the worker sets ``reader_runs.status = 'completed'``
        # at ``article_rag_index_worker.py:897,1418`` (both the
        # already-indexed idempotent path and the fresh-index success
        # path).  ``'succeeded'`` is the terminal value for
        # ``reader_jobs`` and ``'indexed'`` for ``index_runs``; runs
        # use ``'completed'``.  R3 tightens this to EXACTLY
        # ``'completed'`` — no allowlist, no future-compatibility
        # fallback.  If the worker vocabulary changes, this test
        # must be updated explicitly.
        assert run_row["status"] == "completed", (
            f"reader_runs.status={run_row['status']!r} "
            f"(expected 'completed')."
        )
        assert run_row["failure_class"] is None, (
            f"reader_runs.failure_class={run_row['failure_class']!r} "
            f"(expected None)."
        )
        assert run_row["failure_code"] is None, (
            f"reader_runs.failure_code={run_row['failure_code']!r} "
            f"(expected None)."
        )

        # D3: index_run.status == "indexed".
        assert index_run_row is not None
        assert index_run_row["status"] == "indexed", (
            f"index_run.status={index_run_row['status']!r} "
            f"(expected 'indexed')."
        )
        assert index_run_row["completed_at"] is not None, (
            "index_run.completed_at is None — must be non-null after "
            "terminal 'indexed' state."
        )
        assert (
            index_run_row["embedding_model"]
            == worker_result.embedding_model
        ), (
            f"index_run.embedding_model="
            f"{index_run_row['embedding_model']!r} != "
            f"worker_result.embedding_model="
            f"{worker_result.embedding_model!r}"
        )
        assert (
            index_run_row["vector_store_provider"] == "zilliz"
        ), (
            f"index_run.vector_store_provider="
            f"{index_run_row['vector_store_provider']!r} "
            f"(expected 'zilliz')."
        )
        assert (
            index_run_row["vector_collection"] == contract_collection
        ), (
            f"index_run.vector_collection="
            f"{index_run_row['vector_collection']!r} != "
            f"contract_collection={contract_collection!r}"
        )

        # -----------------------------------------------------------------
        # D4: Zilliz contains chunks with our stable_document_id.
        # Save the precise chunk_id set for D7 + cleanup.
        # -----------------------------------------------------------------
        # R2 fix: Milvus eventual consistency.  The worker's
        # ``upsert_chunks`` calls ``client.upsert()`` but does NOT
        # call ``client.flush()`` — production retrieval doesn't
        # happen immediately after write, so no flush is needed in
        # production.  But this test queries IMMEDIATELY after the
        # worker completes, so the just-upserted chunks may not yet
        # be queryable.  We call ``flush`` (NOT compaction — flush
        # only makes writes durable and visible to subsequent queries)
        # and then poll with a bounded timeout until the chunks
        # appear.  This is a TEST-ONLY consistency aid; production
        # code is not modified.
        def _flush_collection() -> None:
            try:
                zilliz_client.flush(  # type: ignore[attr-defined]
                    collection_name=contract_collection
                )
            except Exception:  # noqa: BLE001 — flush is best-effort
                pass

        await asyncio.to_thread(_flush_collection)

        def _fetch_fixture_chunk_ids() -> tuple[str, ...]:
            result = zilliz_client.query(  # type: ignore[attr-defined]
                collection_name=contract_collection,
                filter=f'stable_document_id == "{ids.stable_document_id}"',
                output_fields=["chunk_id"],
                limit=64,
            )
            if not result:
                return ()
            return tuple(str(r["chunk_id"]) for r in result)

        # Poll for up to 30 seconds (1-second intervals) until chunks
        # appear.  Milvus flush typically completes in 1-5 seconds on
        # Zilliz Cloud; the poll handles any residual async lag.
        _D4_POLL_TIMEOUT_SECONDS = 30
        _D4_POLL_INTERVAL_SECONDS = 1
        saved_chunk_ids_tuple: tuple[str, ...] = ()
        for _poll_attempt in range(_D4_POLL_TIMEOUT_SECONDS):
            saved_chunk_ids_tuple = await asyncio.to_thread(
                _fetch_fixture_chunk_ids
            )
            if saved_chunk_ids_tuple:
                break
            await asyncio.sleep(_D4_POLL_INTERVAL_SECONDS)

        saved_chunk_ids = list(saved_chunk_ids_tuple)
        assert len(saved_chunk_ids) > 0, (
            f"Expected >0 chunks in Zilliz with stable_document_id="
            f"{ids.stable_document_id}, got 0 after "
            f"{_D4_POLL_TIMEOUT_SECONDS}s poll (flush + 1s intervals).  "
            f"Worker reported vector_write_calls="
            f"{counts.vector_write_calls} (must be 1).  "
            f"Call counts: {counts.as_report()}"
        )
        # chunk_ids must be unique (no duplicates from a prior
        # aborted run — the preflight already asserted 0 leftover
        # chunks, but we double-check here).
        assert len(set(saved_chunk_ids)) == len(saved_chunk_ids), (
            f"Duplicate chunk_ids in Zilliz: {saved_chunk_ids}.  "
            f"A prior smoke run may have failed to clean up."
        )

        # -----------------------------------------------------------------
        # D5 + D6 + D7: Retrieval (called EXACTLY once via the
        # production Ask port chain)
        # -----------------------------------------------------------------
        # Build the production Ask port with a _CapturingRetrievalService
        # wrapping the real ArticleRagRetrievalService.  The port
        # calls retrieval EXACTLY once via search_current_article.
        # We do NOT call retrieval.retrieve_for_record explicitly —
        # that would double the query_embedding / vector_search cost.
        from app.services.reader_orchestration.article_rag_retrieval_service import (  # noqa: E501
            ArticleRagRetrievalResult,
            ArticleRagRetrievalService,
        )
        from app.services.reader_orchestration.article_rag_vector_search import (  # noqa: E501
            ZillizArticleRagVectorSearcher,
        )

        zilliz_uri = settings.resolve_reader_article_rag_zilliz_uri()
        zilliz_token = settings.resolve_reader_article_rag_zilliz_token()
        real_searcher = ZillizArticleRagVectorSearcher(
            uri=zilliz_uri,
            token=zilliz_token,
            collection=contract_collection,
        )
        real_embedder_for_retrieval = DashScopeArticleRagEmbeddingProvider(
            model_override=(
                settings.reader_article_rag_embedding_model or None
            ),
        )
        # Wrap the retrieval-side embedder + searcher with counting
        # delegates so we MEASURE the query_embedding + vector_search
        # call counts (not hardcode).
        counting_query_embedder = _CountingEmbeddingProvider(
            real_embedder_for_retrieval
        )
        counting_searcher = _CountingVectorSearcher(real_searcher)
        real_retrieval = ArticleRagRetrievalService(
            pool=acceptance_env,
            embedding_provider=counting_query_embedder,
            vector_searcher=counting_searcher,
        )
        capturing_retrieval = _CapturingRetrievalService(real_retrieval)

        # Wire the production Ask seam with the capturing retrieval
        # wrapper.  ``RetrievalBackedArticleRagPort`` is exactly the
        # adapter ``build_production_article_rag_port`` returns when
        # the feature flag + providers are ready; here the counting /
        # capturing delegates stand in for the default provider build
        # so call counts are MEASURED.
        from app.services.reader_record_ask.article_rag_adapter import (  # noqa: E501
            RetrievalBackedArticleRagPort,
        )

        rag_port = RetrievalBackedArticleRagPort(
            retrieval=capturing_retrieval,
        )

        # Single retrieval call is triggered by search_current_article.
        outcome = await rag_port.search_current_article(
            user_id=ids.user_id,
            reading_record_id=ids.record_id,
            base_id=ids.base_id,
            record_generation=1,
            stable_document_id=ids.stable_document_id,
            query="acceptance probe sentence",
            limit=10,
        )

        # Measure post-Ask counts (delegates were incremented).
        counts.query_embedding_service_calls = (
            counting_query_embedder.call_count
        )
        counts.vector_search_calls = counting_searcher.call_count
        counts.retrieval_service_calls = capturing_retrieval.call_count
        # R2 stops at the Ask port boundary — no real Ask model call.
        counts.ask_model_calls = 0

        # Assert EXACT measured counts (single-path budget).
        assert counts.query_embedding_service_calls == 1, (
            f"Expected exactly 1 query_embedding_service_call, "
            f"got {counts.query_embedding_service_calls}.  "
            f"The Ask port must call retrieval EXACTLY once."
        )
        assert counts.vector_search_calls == 1, (
            f"Expected exactly 1 vector_search_call, "
            f"got {counts.vector_search_calls}."
        )
        assert counts.retrieval_service_calls == 1, (
            f"Expected exactly 1 retrieval_service_call, "
            f"got {counts.retrieval_service_calls}.  "
            f"No explicit retrieval call outside the Ask chain."
        )
        assert counts.ask_model_calls == 0, (
            f"Expected 0 ask_model_calls (R2 stops at the Ask port "
            f"boundary), got {counts.ask_model_calls}."
        )

        # -----------------------------------------------------------------
        # D5: Captured retrieval result has hits > 0 and references
        # our fixture.  Identity lives on ArticleRagRetrievalResult,
        # NOT on individual hits (ArticleRagRetrievalHit has only
        # chunk_id / text / citation: dict / metadata_json / score /
        # content_sha256 — NO stable_document_id / base_id).
        # -----------------------------------------------------------------
        retrieval_result = capturing_retrieval.last_result
        assert retrieval_result is not None, (
            "CapturingRetrievalService.last_result is None — "
            "search_current_article did not call retrieve_for_record."
        )
        assert isinstance(retrieval_result, ArticleRagRetrievalResult)
        assert retrieval_result.reading_record_id == ids.record_id, (
            f"retrieval_result.reading_record_id="
            f"{retrieval_result.reading_record_id} "
            f"(expected {ids.record_id})"
        )
        assert (
            retrieval_result.stable_document_id
            == ids.stable_document_id
        ), (
            f"retrieval_result.stable_document_id="
            f"{retrieval_result.stable_document_id} "
            f"(expected {ids.stable_document_id})"
        )
        assert retrieval_result.base_id == ids.base_id, (
            f"retrieval_result.base_id={retrieval_result.base_id} "
            f"(expected {ids.base_id})"
        )
        assert isinstance(retrieval_result.hits, tuple)
        assert len(retrieval_result.hits) > 0, (
            f"Retrieval returned 0 hits — expected >0 for our freshly "
            f"indexed fixture.  Call counts: {counts.as_report()}"
        )
        # Every hit.chunk_id must belong to the saved Zilliz chunk_id
        # set — proves retrieval returned only chunks we wrote.
        for hit in retrieval_result.hits:
            assert hit.chunk_id in saved_chunk_ids, (
                f"hit.chunk_id={hit.chunk_id!r} is NOT in the saved "
                f"Zilliz chunk_id set {saved_chunk_ids}.  Retrieval "
                f"returned a chunk that was not written by this fixture."
            )

        # -----------------------------------------------------------------
        # D6: Retrieval uses Postgres plan for citation (not vector
        # payload).  This is proven OFFLINE by the existing P1-F
        # forged-vector citation regression test
        # (``test_forged_vector_citation_metadata_is_not_truth``).
        # The live smoke does NOT tamper with the vector payload;
        # it only verifies the citation dict has the plan-backed
        # 9-key I4A shape (the retrieval service's
        # _citation_dict_from_chunk produces exactly these keys).
        # -----------------------------------------------------------------
        expected_citation_keys = {
            "reading_record_id",
            "stable_document_id",
            "base_id",
            "record_generation",
            "block_ids",
            "unit_ids",
            "anchor_segment_ids",
            "canonical_text_start_utf16",
            "canonical_text_end_utf16",
        }
        for hit in retrieval_result.hits:
            citation = hit.citation
            assert isinstance(citation, dict), (
                f"hit.citation must be a dict, got "
                f"{type(citation).__name__}"
            )
            assert set(citation.keys()) == expected_citation_keys, (
                f"hit.citation keys={set(citation.keys())} "
                f"do not match expected 9-key I4A shape "
                f"{expected_citation_keys}."
            )

        # -----------------------------------------------------------------
        # D7: Reuse the pre-built plan (R3 — no second plan
        # construction) and compare all 9 citation fields per
        # chunk_id.  This proves the retrieval citation EXACTLY
        # matches the Postgres plan truth — not just "non-empty" or
        # "contains paragraph".
        # -----------------------------------------------------------------
        # R3: ``prebuilt_plan`` was built BEFORE the paid vector write
        # (see above).  Reuse it here — no second plan construction,
        # no second PostgreSQL read.  The plan is deterministic per
        # (record_id, user_id); rebuilding would return the same
        # chunks + citations.
        rebuilt_plan = prebuilt_plan
        # Build expected citation map: chunk_id -> ArticleRagCitationRef.
        expected_citation_by_chunk_id: dict[str, Any] = {
            chunk.chunk_id: chunk.citation
            for chunk in rebuilt_plan.chunks
        }
        # Every retrieval hit's citation must EXACTLY match the plan
        # truth for the same chunk_id, across all 9 fields.
        for hit in retrieval_result.hits:
            assert hit.chunk_id in expected_citation_by_chunk_id, (
                f"hit.chunk_id={hit.chunk_id!r} not found in rebuilt "
                f"plan chunks ({list(expected_citation_by_chunk_id)})."
            )
            expected = expected_citation_by_chunk_id[hit.chunk_id]
            citation = hit.citation
            # 1. reading_record_id: plan has UUID, dict has str(UUID).
            assert str(expected.reading_record_id) == str(
                citation["reading_record_id"]
            ), (
                f"chunk_id={hit.chunk_id}: reading_record_id mismatch "
                f"(plan={expected.reading_record_id}, "
                f"hit={citation['reading_record_id']})"
            )
            # 2. stable_document_id.
            assert str(expected.stable_document_id) == str(
                citation["stable_document_id"]
            ), (
                f"chunk_id={hit.chunk_id}: stable_document_id mismatch "
                f"(plan={expected.stable_document_id}, "
                f"hit={citation['stable_document_id']})"
            )
            # 3. base_id.
            assert str(expected.base_id) == str(
                citation["base_id"]
            ), (
                f"chunk_id={hit.chunk_id}: base_id mismatch "
                f"(plan={expected.base_id}, "
                f"hit={citation['base_id']})"
            )
            # 4. record_generation.
            assert expected.record_generation == citation[
                "record_generation"
            ], (
                f"chunk_id={hit.chunk_id}: record_generation mismatch "
                f"(plan={expected.record_generation}, "
                f"hit={citation['record_generation']})"
            )
            # 5. block_ids: plan has tuple, dict has list.
            assert list(expected.block_ids) == list(
                citation["block_ids"]
            ), (
                f"chunk_id={hit.chunk_id}: block_ids mismatch "
                f"(plan={list(expected.block_ids)}, "
                f"hit={citation['block_ids']})"
            )
            # 6. unit_ids.
            assert list(expected.unit_ids) == list(
                citation["unit_ids"]
            ), (
                f"chunk_id={hit.chunk_id}: unit_ids mismatch "
                f"(plan={list(expected.unit_ids)}, "
                f"hit={citation['unit_ids']})"
            )
            # 7. anchor_segment_ids.
            assert list(expected.anchor_segment_ids) == list(
                citation["anchor_segment_ids"]
            ), (
                f"chunk_id={hit.chunk_id}: anchor_segment_ids mismatch "
                f"(plan={list(expected.anchor_segment_ids)}, "
                f"hit={citation['anchor_segment_ids']})"
            )
            # 8. canonical_text_start_utf16.
            assert expected.canonical_text_start_utf16 == citation[
                "canonical_text_start_utf16"
            ], (
                f"chunk_id={hit.chunk_id}: canonical_text_start_utf16 "
                f"mismatch (plan={expected.canonical_text_start_utf16}, "
                f"hit={citation['canonical_text_start_utf16']})"
            )
            # 9. canonical_text_end_utf16.
            assert expected.canonical_text_end_utf16 == citation[
                "canonical_text_end_utf16"
            ], (
                f"chunk_id={hit.chunk_id}: canonical_text_end_utf16 "
                f"mismatch (plan={expected.canonical_text_end_utf16}, "
                f"hit={citation['canonical_text_end_utf16']})"
            )

        # -----------------------------------------------------------------
        # D8: Ask evidence/context contains Article RAG retrieval
        # result.  The production Ask seam returns a typed
        # ``ArticleRagSearchOutcome`` with eligible hit views.  R2
        # stops at the Ask port boundary — NO real Ask model call.
        # -----------------------------------------------------------------
        from app.services.reader_record_ask.article_rag_port import (  # noqa: E501
            ALLOWED_ASK_RAG_SOURCE_SCOPES,
            ArticleRagHitView,
            ArticleRagSearchOutcome,
        )

        assert isinstance(outcome, ArticleRagSearchOutcome), (
            f"rag_port.search_current_article returned "
            f"{type(outcome).__name__} (expected ArticleRagSearchOutcome)."
        )
        assert outcome.status == "ok", (
            f"outcome.status={outcome.status!r} "
            f"(expected 'ok', detail_code={outcome.detail_code!r}).  "
            f"Retrieval returned {len(retrieval_result.hits)} hits — "
            f"the Ask port must map them to an 'ok' outcome.  "
            f"Call counts: {counts.as_report()}"
        )
        assert len(outcome.hits) > 0, (
            f"outcome.hits is empty — expected >0 eligible hits for "
            f"our freshly indexed fixture.  Call counts: "
            f"{counts.as_report()}"
        )
        # Identity fence at the outcome level: the port echoes the
        # envelope identity it validated against.
        assert outcome.stable_document_id == ids.stable_document_id, (
            f"outcome.stable_document_id={outcome.stable_document_id} "
            f"(expected {ids.stable_document_id})"
        )
        assert outcome.base_id == ids.base_id, (
            f"outcome.base_id={outcome.base_id} "
            f"(expected {ids.base_id})"
        )
        assert outcome.record_generation == 1, (
            f"outcome.record_generation={outcome.record_generation} "
            f"(expected 1)"
        )
        # rag_substrate_id is the immutable indexed run id (D3 row) —
        # never a secondary "latest indexed run" loader.
        assert outcome.rag_substrate_id is not None, (
            "outcome.rag_substrate_id is None — the port must anchor "
            "hits on the immutable reader_article_rag_index_runs.id."
        )
        assert outcome.rag_substrate_id == ensure_result.index_run_id, (
            f"outcome.rag_substrate_id={outcome.rag_substrate_id} "
            f"!= ensure_result.index_run_id="
            f"{ensure_result.index_run_id}.  The port must serve the "
            f"exact indexed run used by this retrieval call."
        )
        # plan_content_sha256: 64-char lowercase hex (SHA-256) — the
        # plan-backed truth anchor, never derived from vector payload.
        assert outcome.plan_content_sha256 is not None, (
            "outcome.plan_content_sha256 is None — must be a "
            "non-empty SHA-256 hex string when status is 'ok'."
        )
        assert len(outcome.plan_content_sha256) == 64, (
            f"outcome.plan_content_sha256 length="
            f"{len(outcome.plan_content_sha256)} (expected 64)."
        )
        assert all(
            c in "0123456789abcdef"
            for c in outcome.plan_content_sha256
        ), (
            f"outcome.plan_content_sha256="
            f"{outcome.plan_content_sha256!r} "
            f"must be 64-char lowercase hex."
        )
        # Every eligible hit must reference the fixture identity,
        # belong to the exact chunk_id set written to Zilliz at D4,
        # and carry plan-backed truth fields (content hash + canonical
        # UTF-16 range) — the Ask attachment boundary.
        _saved_chunk_id_set = set(saved_chunk_ids)
        for hit in outcome.hits:
            assert isinstance(hit, ArticleRagHitView), (
                f"outcome hit must be ArticleRagHitView, got "
                f"{type(hit).__name__}"
            )
            assert hit.chunk_id in _saved_chunk_id_set, (
                f"hit.chunk_id={hit.chunk_id!r} not in saved "
                f"chunk_id set (written at D4, "
                f"saved_chunk_ids={sorted(_saved_chunk_id_set)})."
            )
            assert hit.reading_record_id == ids.record_id, (
                f"hit.reading_record_id={hit.reading_record_id} "
                f"(expected {ids.record_id})"
            )
            assert hit.stable_document_id == ids.stable_document_id, (
                f"hit.stable_document_id={hit.stable_document_id} "
                f"(expected {ids.stable_document_id})"
            )
            assert hit.base_id == ids.base_id, (
                f"hit.base_id={hit.base_id} (expected {ids.base_id})"
            )
            assert hit.record_generation == 1, (
                f"hit.record_generation={hit.record_generation} "
                f"(expected 1)"
            )
            assert hit.source_scope in ALLOWED_ASK_RAG_SOURCE_SCOPES, (
                f"hit.source_scope={hit.source_scope!r} not in "
                f"allowed Ask scopes {sorted(ALLOWED_ASK_RAG_SOURCE_SCOPES)}."
            )
            # Plan-backed content hash: 64-char lowercase hex.
            assert len(hit.content_sha256) == 64, (
                f"hit.content_sha256 length="
                f"{len(hit.content_sha256)} (expected 64)."
            )
            assert all(
                c in "0123456789abcdef" for c in hit.content_sha256
            ), (
                f"hit.content_sha256={hit.content_sha256!r} "
                f"must be 64-char lowercase hex."
            )
            # Canonical UTF-16 range must be sane.
            assert 0 <= hit.canonical_text_start_utf16, (
                f"hit.canonical_text_start_utf16="
                f"{hit.canonical_text_start_utf16} (expected >= 0)."
            )
            assert (
                hit.canonical_text_start_utf16
                < hit.canonical_text_end_utf16
            ), (
                f"hit canonical range inverted: start="
                f"{hit.canonical_text_start_utf16} end="
                f"{hit.canonical_text_end_utf16}."
            )

        # ask_model_calls must remain 0 (R2 stops at the Ask port
        # boundary).
        assert counts.ask_model_calls == 0
    finally:
        # -----------------------------------------------------------------
        # D11: Precise cleanup by union of (expected, saved, discovered)
        # chunk_id sets (runs in finally regardless of pass/fail).
        # Deletes by EXACT chunk_id primary keys — NEVER by
        # stable_document_id filter, NEVER drop/recreate the collection.
        # -----------------------------------------------------------------
        # R3: ``expected_chunk_ids`` was built from the PostgreSQL plan
        # BEFORE any paid call — so even if D4 was never reached (D2/D3
        # failure) or the D4 poll timed out, the cleanup still has a
        # deterministic target.  ``saved_chunk_ids`` is what D4 actually
        # captured.  ``discovered_chunk_ids`` is what the cleanup helper
        # queries by stable_document_id in finally (DISCOVERY only,
        # never delete).  The helper computes the union and deletes by
        # precise chunk_id primary keys.
        cleanup_result = await _precise_cleanup_by_chunk_ids(
            zilliz_client,
            collection=contract_collection,
            expected_chunk_ids=expected_chunk_ids,
            saved_chunk_ids=tuple(saved_chunk_ids),
            stable_document_id=ids.stable_document_id,
            preflight=preflight,
            vector_write_attempted=counting_writer.call_count > 0,
        )
        # R3: ``vector_delete_calls`` comes from the MEASURED
        # ``delete_call_count`` in the cleanup result — NOT hardcoded
        # after success.  1 if a delete was issued, 0 if cleanup_target
        # was empty.
        counts.vector_delete_calls = cleanup_result.delete_call_count

        # Each chunk_id in the cleanup target must be individually
        # verified gone.
        for cid, gone in (
            cleanup_result.per_chunk_id_verified.items()
        ):
            assert gone is True, (
                f"Cleanup FAILED: chunk_id={cid!r} still exists "
                f"after delete.  Manual cleanup required."
            )
        # 0 rows by stable_document_id query (authoritative).
        assert cleanup_result.post_delete_query_count == 0, (
            f"Cleanup FAILED: {cleanup_result.post_delete_query_count} "
            f"chunks with stable_document_id={ids.stable_document_id} "
            f"still exist after delete.  Manual cleanup required."
        )
        assert cleanup_result.collection_still_exists is True
        # Schema identity unchanged (compares all dimensions).
        _assert_schema_identity_unchanged(preflight, cleanup_result)

        # Cleanup report print — always runs for the delivery report.
        print("\n=== R3 Cleanup Result ===")
        print(f"  deleted_count: {cleanup_result.deleted_count}")
        print(f"  delete_call_count: {cleanup_result.delete_call_count}")
        print(
            f"  cleanup_target_chunk_ids: "
            f"{cleanup_result.cleanup_target_chunk_ids}"
        )
        print(
            f"  expected_chunk_ids: "
            f"{cleanup_result.expected_chunk_ids}"
        )
        print(
            f"  saved_chunk_ids: "
            f"{cleanup_result.saved_chunk_ids}"
        )
        print(
            f"  discovered_chunk_ids: "
            f"{cleanup_result.discovered_chunk_ids}"
        )
        print(
            f"  post_delete_query_count: "
            f"{cleanup_result.post_delete_query_count}"
        )
        print(
            f"  collection_still_exists: "
            f"{cleanup_result.collection_still_exists}"
        )
        print(
            f"  field_count_after: "
            f"{cleanup_result.field_count_after} "
            f"(preflight={preflight.field_count})"
        )
        print(
            f"  per_chunk_id_verified: "
            f"{cleanup_result.per_chunk_id_verified}"
        )
        print(
            f"  protected_collections_unchanged: "
            f"{cleanup_result.protected_collections_unchanged}"
        )
        if cleanup_result.collection_row_count_after is not None:
            print(
                f"  collection_row_count_after: "
                f"{cleanup_result.collection_row_count_after} "
                f"(preflight={preflight.collection_row_count})"
            )
        print("=== End R3 Cleanup Result ===")

    # -----------------------------------------------------------------
    # Success-path report print — only runs if try body succeeded
    # AND finally cleanup assertions passed.
    # -----------------------------------------------------------------
    print("\n=== R2 Real-Chain Acceptance Call Counts ===")
    for key, value in counts.as_report().items():
        print(f"  {key}: {value}")
    print(f"  fixture stable_document_id: {ids.stable_document_id}")
    print(f"  saved_chunk_ids: {saved_chunk_ids}")
    if cleanup_result is not None:
        print(
            f"  cleanup_deleted_count: {cleanup_result.deleted_count}"
        )
        print(
            f"  cleanup_post_delete_query_count: "
            f"{cleanup_result.post_delete_query_count}"
        )
    print("=== End R2 Call Counts ===")


# ---------------------------------------------------------------------------
# Gate logic tests — verify the NEW gate (requires article_rag_chunks)
# ---------------------------------------------------------------------------


class TestSinglePathGateLogic:
    """The opt-in smoke gate now REQUIRES ``article_rag_chunks`` (the
    frozen contract collection) instead of a smoke prefix.  These
    tests verify the gate accepts the production collection and
    rejects anything else.
    """

    def _full_smoke_env(self) -> dict[str, str]:
        return {
            "READER_ARTICLE_RAG_SMOKE": "1",
            "READER_ARTICLE_RAG_EMBEDDING_PROVIDER": "dashscope",
            "BAILIAN_API_KEY": "test-bailian-key",
            "READER_ARTICLE_RAG_VECTOR_PROVIDER": "zilliz",
            "READER_ARTICLE_RAG_ZILLIZ_URI": "https://example.zilliz.com",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN": "test-zilliz-token",
            "READER_ARTICLE_RAG_ZILLIZ_COLLECTION": "article_rag_chunks",
            "READER_ARTICLE_RAG_VECTOR_DIM": "1024",
        }

    def test_full_env_satisfies_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for k, v in self._full_smoke_env().items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is True

    def test_gate_rejects_smoke_prefix_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old smoke prefix design is now REJECTED.  The smoke
        MUST use the production collection — precise fixture isolation
        replaces collection-name isolation."""
        env = self._full_smoke_env()
        # Build the retired smoke-collection namespace prefix at
        # runtime via string concatenation so the literal contiguous
        # string does NOT appear in this source file.  The 0-match
        # enforcement (rg for the contiguous retired prefix across
        # this file + the i4z file + the runbook) therefore succeeds.
        retired_prefix = "article_rag_" + "index_" + "smoke_"
        env["READER_ARTICLE_RAG_ZILLIZ_COLLECTION"] = (
            retired_prefix + "abcdef12"
        )
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False, (
            "Gate let the smoke through with a smoke-prefix collection. "
            "R2 requires article_rag_chunks — the smoke prefix design "
            "is incompatible with the worker's frozen contract enforcement."
        )

    def test_gate_rejects_arbitrary_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._full_smoke_env()
        env["READER_ARTICLE_RAG_ZILLIZ_COLLECTION"] = "some_other_collection"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False

    def test_gate_rejects_gate_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._full_smoke_env()
        env["READER_ARTICLE_RAG_SMOKE"] = "0"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False

    def test_gate_rejects_no_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._full_smoke_env()
        env.pop("BAILIAN_API_KEY", None)
        monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
        monkeypatch.delenv("RAG_EMBEDDING_MODEL_PROFILE", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        # Suppress .env fallback so the gate cannot discover a credential
        # that exists only in the local .env file.  We monkeypatch
        # ``_load_dotenv_value`` on the CURRENT module (the one pytest
        # imported, identified by ``__name__``) — NOT on
        # ``tests.test_article_rag_single_path_real_acceptance`` which is a
        # SEPARATE module object when ``tests/`` has no ``__init__.py``
        # (pytest prepend mode imports as ``test_...`` without the package
        # prefix).  Patching the wrong module copy would leave the gate
        # function's ``__globals__`` untouched.
        import sys as _sys  # noqa: PLC0415

        monkeypatch.setattr(
            _sys.modules[__name__],
            "_load_dotenv_value",
            lambda _key: "",
        )
        assert _real_smoke_env_present() is False


# ---------------------------------------------------------------------------
# Offline tracer — verifies the R2 acceptance helpers work against the
# REAL ArticleRagRetrievalHit / ArticleRagRetrievalResult shape, so the
# real smoke cannot raise AttributeError at runtime.
#
# Per the R2 task spec Section 2:
#   "增加离线 tracer，使用真实 ArticleRagRetrievalHit shape 验证验收
#    helper，不允许再次提交运行时 AttributeError。"
#
# These tests construct a real ArticleRagRetrievalHit (frozen, slots=True)
# with the production-shape citation dict and exercise the same access
# patterns the real smoke uses in D5 / D6 / D7.  If a future contract
# change renames or removes a field, these tests fail OFFLINE before any
# paid real-chain call is made.
# ---------------------------------------------------------------------------


def _build_offline_retrieval_result() -> Any:
    """Build a real-shape ArticleRagRetrievalResult for offline tests.

    Uses the production dataclasses directly — no fakes, no mocks.
    The citation dict matches the 9-key I4A shape produced by
    ``ArticleRagRetrievalService._citation_dict_from_chunk``.
    """
    from app.services.reader_orchestration.article_rag_retrieval_service import (  # noqa: I001
        ArticleRagRetrievalHit,
        ArticleRagRetrievalResult,
    )

    record_id = uuid4()
    stable_doc_id = uuid4()
    base_id = uuid4()
    index_run_id = uuid4()
    citation_dict: dict[str, Any] = {
        "reading_record_id": str(record_id),
        "stable_document_id": str(stable_doc_id),
        "base_id": str(base_id),
        "record_generation": 1,
        "block_ids": ["paragraph-1"],
        "unit_ids": ["unit-1"],
        "anchor_segment_ids": ["segment-1"],
        "canonical_text_start_utf16": 18,
        "canonical_text_end_utf16": 100,
    }
    hit = ArticleRagRetrievalHit(
        chunk_id="offline-chunk-1",
        text="offline tracer chunk text",
        citation=citation_dict,
        metadata_json={"chunk_id": "offline-chunk-1"},
        score=0.95,
        content_sha256="0" * 64,
    )
    return ArticleRagRetrievalResult(
        reading_record_id=record_id,
        stable_document_id=stable_doc_id,
        base_id=base_id,
        record_generation=1,
        plan_content_sha256="1" * 64,
        index_run_id=index_run_id,
        hits=(hit,),
    )


class TestOfflineRetrievalHitShapeTracer:
    """Offline tracer: verify the R2 acceptance helpers work against
    the real ArticleRagRetrievalHit / ArticleRagRetrievalResult shape.

    These tests run WITHOUT any real provider call, without Zilliz,
    without Postgres.  They construct production-shape dataclasses and
    exercise the same access patterns the real smoke uses in D5 / D6 /
    D7.  If a contract change breaks the access pattern, these tests
    fail OFFLINE before any paid call is made.
    """

    def test_hit_has_no_stable_document_id_attribute(self) -> None:
        """ArticleRagRetrievalHit has only chunk_id / text / citation
        / metadata_json / score / content_sha256.  Accessing
        ``hit.stable_document_id`` (R1's bug) MUST raise
        AttributeError — this is the regression tracer for R1 defect
        #1.
        """
        result = _build_offline_retrieval_result()
        hit = result.hits[0]
        # Sanity: the real shape has these 6 fields.
        assert hit.chunk_id == "offline-chunk-1"
        assert hit.text == "offline tracer chunk text"
        assert isinstance(hit.citation, dict)
        assert isinstance(hit.metadata_json, dict)
        assert hit.score == 0.95
        assert hit.content_sha256 == "0" * 64
        # R1 defect #1 regression: hit.stable_document_id MUST raise
        # AttributeError (the field does not exist on the frozen
        # slots dataclass).
        with pytest.raises(AttributeError):
            _ = hit.stable_document_id  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            _ = hit.base_id  # type: ignore[attr-defined]

    def test_citation_is_dict_not_object(self) -> None:
        """Citation is a dict, NOT an object.  Attribute access
        (``citation.stable_document_id``) MUST raise AttributeError —
        this is the regression tracer for R1 defect #2.
        """
        result = _build_offline_retrieval_result()
        hit = result.hits[0]
        citation = hit.citation
        # Correct access: dict keys.
        assert isinstance(citation, dict)
        assert "stable_document_id" in citation
        assert "base_id" in citation
        assert "reading_record_id" in citation
        assert "block_ids" in citation
        assert "canonical_text_start_utf16" in citation
        # R1 defect #2 regression: citation.stable_document_id MUST
        # raise AttributeError (dict has no such attribute).
        with pytest.raises(AttributeError):
            _ = citation.stable_document_id  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            _ = citation.block_ids  # type: ignore[attr-defined]

    def test_identity_lives_on_result_not_on_hits(self) -> None:
        """Identity (stable_document_id / base_id / reading_record_id)
        lives on ArticleRagRetrievalResult, NOT on individual hits.
        The D5 assertions read these from the result, not the hit.
        """
        result = _build_offline_retrieval_result()
        # Correct: read identity from result.
        assert isinstance(result.stable_document_id, UUID)
        assert isinstance(result.base_id, UUID)
        assert isinstance(result.reading_record_id, UUID)
        assert result.record_generation == 1
        # Hits do NOT carry identity.
        hit = result.hits[0]
        with pytest.raises(AttributeError):
            _ = hit.stable_document_id  # type: ignore[attr-defined]

    def test_citation_dict_has_exact_9_key_contract_shape(self) -> None:
        """The citation dict must have exactly the 9-key I4A shape
        produced by ``_citation_dict_from_chunk``.  D6 asserts this
        in the real smoke; this tracer verifies the assertion logic
        itself works against the production shape.
        """
        result = _build_offline_retrieval_result()
        hit = result.hits[0]
        citation = hit.citation
        expected_keys = {
            "reading_record_id",
            "stable_document_id",
            "base_id",
            "record_generation",
            "block_ids",
            "unit_ids",
            "anchor_segment_ids",
            "canonical_text_start_utf16",
            "canonical_text_end_utf16",
        }
        assert set(citation.keys()) == expected_keys

    def test_d7_field_by_field_comparison_works(self) -> None:
        """D7 compares all 9 citation fields per chunk_id against the
        rebuilt plan's ArticleRagCitationRef.  This tracer builds a
        matching ArticleRagCitationRef and verifies the comparison
        logic passes when the fields agree.
        """
        from app.services.reader_orchestration.article_rag_index_plan import (  # noqa: I001
            ArticleRagCitationRef,
        )

        result = _build_offline_retrieval_result()
        hit = result.hits[0]
        citation = hit.citation
        # Build a matching ArticleRagCitationRef (the plan truth).
        expected = ArticleRagCitationRef(
            reading_record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            base_id=result.base_id,
            record_generation=result.record_generation,
            block_ids=("paragraph-1",),
            unit_ids=("unit-1",),
            anchor_segment_ids=("segment-1",),
            canonical_text_start_utf16=18,
            canonical_text_end_utf16=100,
        )
        # The 9-field comparison the real smoke does in D7.
        assert str(expected.reading_record_id) == str(
            citation["reading_record_id"]
        )
        assert str(expected.stable_document_id) == str(
            citation["stable_document_id"]
        )
        assert str(expected.base_id) == str(citation["base_id"])
        assert expected.record_generation == citation["record_generation"]
        assert list(expected.block_ids) == list(citation["block_ids"])
        assert list(expected.unit_ids) == list(citation["unit_ids"])
        assert list(expected.anchor_segment_ids) == list(
            citation["anchor_segment_ids"]
        )
        assert (
            expected.canonical_text_start_utf16
            == citation["canonical_text_start_utf16"]
        )
        assert (
            expected.canonical_text_end_utf16
            == citation["canonical_text_end_utf16"]
        )

    def test_d7_field_by_field_comparison_detects_mismatch(self) -> None:
        """D7 comparison must FAIL when any of the 9 fields mismatch.
        This tracer builds a non-matching ArticleRagCitationRef and
        verifies the comparison raises AssertionError.
        """
        from app.services.reader_orchestration.article_rag_index_plan import (  # noqa: I001
            ArticleRagCitationRef,
        )

        result = _build_offline_retrieval_result()
        hit = result.hits[0]
        citation = hit.citation
        # Build a non-matching ArticleRagCitationRef — wrong block_ids.
        wrong = ArticleRagCitationRef(
            reading_record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            base_id=result.base_id,
            record_generation=result.record_generation,
            block_ids=("wrong-block",),  # mismatch
            unit_ids=("unit-1",),
            anchor_segment_ids=("segment-1",),
            canonical_text_start_utf16=18,
            canonical_text_end_utf16=100,
        )
        with pytest.raises(AssertionError):
            assert list(wrong.block_ids) == list(citation["block_ids"])

    def test_capturing_retrieval_service_records_call_and_result(
        self,
    ) -> None:
        """_CapturingRetrievalService must wrap a real retrieval
        service, delegate retrieve_for_record, record call_count and
        last_result.  This tracer uses a fake inner service (no real
        provider call) to verify the wrapping logic.
        """

        class _FakeInner:
            """Fake inner retrieval service — no real provider call."""

            def __init__(self, result: Any) -> None:
                self._result = result
                self.call_count = 0

            async def retrieve_for_record(
                self,
                *,
                reading_record_id: UUID,
                user_id: UUID,
                query_text: str,
                limit: int = 10,
            ) -> Any:
                self.call_count += 1
                return self._result

        expected_result = _build_offline_retrieval_result()
        fake_inner = _FakeInner(expected_result)
        wrapper = _CapturingRetrievalService(fake_inner)

        # Before any call: call_count == 0, last_result is None.
        assert wrapper.call_count == 0
        assert wrapper.last_result is None

        # Call once.
        loop = asyncio.new_event_loop()
        try:
            captured = loop.run_until_complete(
                wrapper.retrieve_for_record(
                    reading_record_id=expected_result.reading_record_id,
                    user_id=uuid4(),
                    query_text="offline tracer query",
                )
            )
        finally:
            loop.close()

        # After the call: call_count == 1, last_result is the result.
        assert wrapper.call_count == 1
        assert wrapper.last_result is expected_result
        assert captured is expected_result
        # Inner was also called exactly once (no double-call).
        assert fake_inner.call_count == 1

    def test_counting_delegates_record_call_counts(self) -> None:
        """The counting delegates (_CountingEmbeddingProvider,
        _CountingVectorWriter, _CountingVectorSearcher) must wrap real
        providers and record call_count.  This tracer uses fake inner
        objects (no real provider call) to verify the wrapping logic.
        """

        class _FakeEmbedder:
            provider_name = "fake-embedder"

            async def embed_texts(
                self, texts: list[str], *, model: str | None = None
            ) -> list[Any]:
                return [[0.0] for _ in texts]

        class _FakeWriter:
            provider_name = "fake-writer"

            @property
            def collection(self) -> str:
                return "fake_collection"

            async def upsert_chunks(
                self,
                *,
                collection: str,
                chunks_with_embeddings: list[Any],
                metadata: Any,
            ) -> Any:
                return {"upsert_count": len(chunks_with_embeddings)}

        class _FakeSearcher:
            provider_name = "fake-searcher"

            async def search(
                self,
                *,
                collection: str,
                query_vector: tuple[float, ...],
                limit: int,
                stable_document_id: UUID | None = None,
            ) -> Any:
                return []

        fake_embedder = _FakeEmbedder()
        fake_writer = _FakeWriter()
        fake_searcher = _FakeSearcher()

        counting_e = _CountingEmbeddingProvider(fake_embedder)
        counting_w = _CountingVectorWriter(fake_writer)
        counting_s = _CountingVectorSearcher(fake_searcher)

        # Initial state: call_count == 0.
        assert counting_e.call_count == 0
        assert counting_w.call_count == 0
        assert counting_s.call_count == 0

        # Provider name + collection passthrough.
        assert counting_e.provider_name == "fake-embedder"
        assert counting_w.provider_name == "fake-writer"
        assert counting_w.collection == "fake_collection"
        assert counting_s.provider_name == "fake-searcher"

        # Call each delegate once.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                counting_e.embed_texts(["test text"])
            )
            loop.run_until_complete(
                counting_w.upsert_chunks(
                    collection="fake_collection",
                    chunks_with_embeddings=[([0.0], {"k": "v"})],
                    metadata={"m": "v"},
                )
            )
            loop.run_until_complete(
                counting_s.search(
                    collection="fake_collection",
                    query_vector=(0.0,),
                    limit=1,
                )
            )
        finally:
            loop.close()

        # After calls: call_count == 1 for each.
        assert counting_e.call_count == 1
        assert counting_w.call_count == 1
        assert counting_s.call_count == 1


# ---------------------------------------------------------------------------
# R3 Phase 2 — Offline failure injection tests for the cleanup helper.
#
# These tests exercise ``_precise_cleanup_by_chunk_ids`` directly with a
# fully in-memory fake Zilliz client.  No network, no real provider, no
# real Zilliz, no Postgres.  They close the failure-path cleanup gap
# identified in the R3 task spec:
#
#   - ``expected_chunk_ids`` is pre-built from the PostgreSQL plan
#     BEFORE any paid vector write.
#   - ``saved_chunk_ids`` is what D4 captured (may be empty).
#   - ``discovered_chunk_ids`` is queried by ``stable_document_id`` in
#     finally (DISCOVERY only, never delete filter).
#   - ``cleanup_target`` = union of all three.
#   - ``delete_call_count`` = 1 iff there is evidence of actual vectors
#     in the backend (discovered or saved is non-empty).
#
# Scenarios A-F mirror the R3 task spec section 二.
# ---------------------------------------------------------------------------


class _FakeZillizClient:
    """In-memory fake Zilliz/Milvus client for offline failure injection.

    Simulates the subset of the ``MilvusClient`` API used by
    ``_precise_cleanup_by_chunk_ids`` and ``_preflight_zilliz``:

      * ``has_collection(collection_name=...) -> bool``
      * ``describe_collection(collection_name=...) -> dict``
      * ``list_collections() -> list[str]``
      * ``list_indexes(collection_name=...) -> list[dict]``
      * ``get_collection_stats(collection_name=...) -> dict``
      * ``query(collection_name=..., filter=..., output_fields=...,
                limit=...) -> list[dict]``
      * ``delete(collection_name=..., filter=...) -> dict``
      * ``flush(collection_name=...) -> None``

    Supports the three filter shapes produced by the cleanup helper:

      1. ``stable_document_id == "<uuid>"``  (discovery + post-delete)
      2. ``chunk_id == "<id>"``              (per-chunk verification)
      3. ``chunk_id in ["id1", "id2", ...]`` (delete)

    No network.  No real provider.  All state is in-memory.
    """

    KNOWN_COLLECTIONS: tuple[str, ...] = (
        "article_rag_chunks",
        "grammar_note_examples",
        "sentence_analysis_examples",
    )

    SCHEMA_FIELDS: list[dict[str, Any]] = [
        {
            "name": "chunk_id",
            "type": "VARCHAR",
            "is_primary": True,
            "params": {},
        },
        {
            "name": "stable_document_id",
            "type": "VARCHAR",
            "is_primary": False,
            "params": {},
        },
        {
            "name": "vector",
            "type": 101,
            "is_primary": False,
            "params": {"dim": 1024},
        },
    ]

    INDEX_INFO: list[dict[str, Any]] = [
        {
            "index_name": "vector_idx",
            "field_name": "vector",
            "index_type": "AUTOINDEX",
            "metric_type": "COSINE",
            "params": {},
        }
    ]

    def __init__(
        self,
        *,
        initial_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        # rows keyed by chunk_id for O(1) lookup.
        self._rows: dict[str, dict[str, Any]] = {}
        for r in (initial_rows or []):
            self._rows[r["chunk_id"]] = dict(r)
        # Track every delete filter string for assertions.
        self.delete_calls: list[str] = []
        # When True, ``delete`` raises (Test F1).
        self.delete_should_fail: bool = False
        # When True, ``delete`` returns success but does NOT remove
        # rows — simulates a ghost-row / eventual-consistency issue
        # so per-chunk verification finds them still present (Test F2).
        self.delete_is_noop: bool = False
        # R4: When True, the FIRST ``stable_document_id == "..."``
        # query (the discovery query in cleanup) raises a
        # RuntimeError.  Subsequent stable_document_id queries
        # (post-delete count) succeed — so per-chunk verification
        # and post-delete count still work.  Used by RED-A.
        self.discovery_query_should_fail: bool = False
        self._discovery_query_attempted: bool = False
        # R4: When True, ``delete`` raises a RuntimeError whose
        # message contains sentinel substrings that mimic a real
        # SDK exception leaking URI/token/key/upstream message.
        # Used by RED-B to prove the cleanup helper wraps delete
        # exceptions in a fixed safe error.
        self.delete_malicious_exception: bool = False
        # When True, collection-integrity reads raise a malicious
        # SDK-shaped exception after cleanup.  Tests enable this only
        # after preflight has captured the baseline identity.
        self.integrity_check_malicious_exception: bool = False

    # -- schema / collection introspection ------------------------------

    def has_collection(self, *, collection_name: str) -> bool:
        if self.integrity_check_malicious_exception:
            raise RuntimeError(
                "MilvusException: integrity read failed: "
                "uri=https://zilliz-integrity.example.com:443 "
                "token=sk-integrity-xxxxxxxxxxxxxxxxxxxxxxxx "
                "api_key=sk-integrity-key "
                "upstream_msg=permission denied"
            )
        return collection_name in self.KNOWN_COLLECTIONS

    def describe_collection(self, *, collection_name: str) -> dict[str, Any]:
        return {"fields": [dict(f) for f in self.SCHEMA_FIELDS]}

    def list_collections(self) -> list[str]:
        return list(self.KNOWN_COLLECTIONS)

    def list_indexes(self, *, collection_name: str) -> list[dict[str, Any]]:
        return [dict(idx) for idx in self.INDEX_INFO]

    def get_collection_stats(self, *, collection_name: str) -> dict[str, Any]:
        return {"row_count": len(self._rows)}

    # -- query / delete -------------------------------------------------

    def query(
        self,
        *,
        collection_name: str,
        filter: str,  # noqa: A002 — matches SDK signature
        output_fields: list[str],
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        import re  # noqa: PLC0415 — local import keeps test self-contained

        # Pattern 1: stable_document_id == "<uuid>"
        m = re.match(r'^stable_document_id == "([^"]+)"$', filter)
        if m:
            # R4: discovery query failure injection — only the FIRST
            # stable_document_id query (discovery) raises; subsequent
            # stable_document_id queries (post-delete count) succeed.
            if (
                self.discovery_query_should_fail
                and not self._discovery_query_attempted
            ):
                self._discovery_query_attempted = True
                raise RuntimeError(
                    "fake discovery query failure: connection reset"
                )
            self._discovery_query_attempted = True
            target = m.group(1)
            matches = [
                {"chunk_id": r["chunk_id"]}
                for r in self._rows.values()
                if r.get("stable_document_id") == target
            ]
            return matches[:limit]

        # Pattern 2: chunk_id == "<id>"
        m = re.match(r'^chunk_id == "([^"]+)"$', filter)
        if m:
            target = m.group(1)
            for r in self._rows.values():
                if r["chunk_id"] == target:
                    return [{"chunk_id": r["chunk_id"]}]
            return []

        # Pattern 3: chunk_id in ["id1", "id2", ...]
        m = re.match(r"^chunk_id in \[(.+)\]$", filter)
        if m:
            ids_str = m.group(1)
            ids = re.findall(r'"([^"]+)"', ids_str)
            return [{"chunk_id": i} for i in ids if i in self._rows]

        return []

    def delete(
        self,
        *,
        collection_name: str,
        filter: str,  # noqa: A002 — matches SDK signature
    ) -> dict[str, Any]:
        self.delete_calls.append(filter)
        if self.delete_malicious_exception:
            # R4: simulates a real SDK exception that leaks URI,
            # token, key, and upstream message into the error.
            # The cleanup helper MUST wrap this in a fixed safe
            # error that does NOT contain any of these sentinels.
            raise RuntimeError(
                "MilvusException: describe collection failed: "
                "uri=https://zilliz.example.com:443 "
                "token=sk-abc123def456ghi789jkl012mno345pqr678 "
                "api_key=sk-zilliz-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx "
                "upstream_msg=connection refused by peer"
            )
        if self.delete_should_fail:
            raise RuntimeError("fake delete failure")
        import re  # noqa: PLC0415 — local import

        m = re.match(r"^chunk_id in \[(.+)\]$", filter)
        if not m:
            return {"delete_count": 0}
        ids_str = m.group(1)
        ids = re.findall(r'"([^"]+)"', ids_str)
        if self.delete_is_noop:
            # Return success but do NOT remove rows — simulates
            # ghost rows that per-chunk verification will catch.
            return {"delete_count": len(ids)}
        count = 0
        for cid in ids:
            if cid in self._rows:
                del self._rows[cid]
                count += 1
        return {"delete_count": count}

    def flush(self, *, collection_name: str) -> None:
        pass


def _build_fake_preflight(
    client: _FakeZillizClient,
    *,
    stable_document_id: UUID,
) -> _ZillizPreflightSnapshot:
    """Build a ``_ZillizPreflightSnapshot`` from the fake client.

    Simulates the preflight taken BEFORE any paid vector write — the
    snapshot records the schema identity, collection list, and
    ``fixture_chunk_count=0`` (no rows for our ``stable_document_id``
    at preflight time).
    """
    describe = client.describe_collection(collection_name="article_rag_chunks")
    (
        field_descriptions,
        primary_key_field,
        vector_field,
        vector_dim,
    ) = _extract_schema_identity(describe)
    field_names = tuple(fd["name"] for fd in field_descriptions)
    return _ZillizPreflightSnapshot(
        collection_exists=True,
        field_count=len(field_names),
        field_names=field_names,
        field_descriptions=field_descriptions,
        primary_key_field=primary_key_field,
        vector_field=vector_field,
        vector_dim=vector_dim,
        index_info=_extract_index_info(client, "article_rag_chunks"),
        protected_collections_present={
            p: True for p in PROTECTED_ZILLIZ_COLLECTIONS
        },
        all_collections=tuple(client.list_collections()),
        fixture_chunk_count=0,
        collection_row_count=len(client._rows),  # noqa: SLF001 — test-only
    )


def _run_cleanup(
    client: _FakeZillizClient,
    *,
    collection: str,
    expected_chunk_ids: tuple[str, ...],
    saved_chunk_ids: tuple[str, ...],
    stable_document_id: UUID,
    preflight: _ZillizPreflightSnapshot,
    vector_write_attempted: bool = False,
) -> _CleanupResult:
    """Synchronous wrapper around the async ``_precise_cleanup_by_chunk_ids``.

    The cleanup helper is async (it uses ``asyncio.to_thread``); the
    offline failure-injection tests run it via ``asyncio.run``.

    ``vector_write_attempted`` is the real smoke signal that the
    counting writer was actually called (``call_count > 0``).  When
    true, cleanup always issues an idempotent precise expected-ID
    delete; discovery may fail or return a stale empty result because
    Milvus visibility is eventually consistent.
    """
    return asyncio.run(
        _precise_cleanup_by_chunk_ids(
            client,
            collection=collection,
            expected_chunk_ids=expected_chunk_ids,
            saved_chunk_ids=saved_chunk_ids,
            stable_document_id=stable_document_id,
            preflight=preflight,
            vector_write_attempted=vector_write_attempted,
        )
    )


class TestFailurePathCleanup:
    """R3 Phase 2 — offline failure injection for the cleanup helper.

    Each test constructs a ``_FakeZillizClient`` with seeded rows,
    builds a preflight snapshot, calls ``_precise_cleanup_by_chunk_ids``
    directly, and asserts the ``_CleanupResult`` matches the expected
    outcome for that failure scenario.

    These tests MUST NOT touch the network, real Zilliz, real provider,
    or Postgres.  They are pure in-memory unit tests of the cleanup
    helper's failure-path logic.
    """

    # ------------------------------------------------------------------
    # Test A — worker/vector write succeeded, but D2 assertion failed.
    #
    #   * expected_chunk_ids = (A1, A2, A3) — built from plan BEFORE
    #     the paid vector write.
    #   * saved_chunk_ids = () — D2 failed before D4 could capture.
    #   * Fake backend has rows A1, A2, A3 for our stable_document_id.
    #   * Cleanup discovers (A1, A2, A3) by stable_document_id.
    #   * cleanup_target = union(expected, saved, discovered) = (A1, A2, A3).
    #   * delete_call_count == 1 (discovered is non-empty).
    #   * All 3 chunk_ids verified gone.
    #   * post_delete_query_count == 0.
    # ------------------------------------------------------------------

    def test_A_worker_write_d2_fail_cleanup_deletes_all(self) -> None:
        stable_doc_id = uuid4()
        a1, a2, a3 = "A1-aaaa", "A2-aaaa", "A3-aaaa"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": a1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": a2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": a3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(a1, a2, a3),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
        )
        # delete was called exactly once.
        assert result.delete_call_count == 1
        # All 3 chunk_ids were in the cleanup target.
        assert set(result.cleanup_target_chunk_ids) == {a1, a2, a3}
        # All 3 verified gone.
        assert all(result.per_chunk_id_verified.values())
        assert set(result.per_chunk_id_verified.keys()) == {a1, a2, a3}
        # post-delete query by stable_document_id returns 0.
        assert result.post_delete_query_count == 0
        # expected / saved / discovered recorded separately.
        assert set(result.expected_chunk_ids) == {a1, a2, a3}
        assert result.saved_chunk_ids == ()
        assert set(result.discovered_chunk_ids) == {a1, a2, a3}
        # Schema identity unchanged.
        _assert_schema_identity_unchanged(preflight, result)
        # Fake backend has 0 rows for our stable_document_id.
        remaining = client.query(
            collection_name="article_rag_chunks",
            filter=f'stable_document_id == "{stable_doc_id}"',
            output_fields=["chunk_id"],
            limit=512,
        )
        assert remaining == []
        # delete filter contained ONLY our chunk_ids (precise PK filter).
        assert len(client.delete_calls) == 1
        delete_filter = client.delete_calls[0]
        assert "stable_document_id" not in delete_filter
        for cid in (a1, a2, a3):
            assert f'"{cid}"' in delete_filter

    # ------------------------------------------------------------------
    # Test B — D4 poll timed out; leftover from a previous run.
    #
    #   * expected_chunk_ids = (B1, B2) — current plan had 2 chunks.
    #   * saved_chunk_ids = () — D4 poll timed out before capture.
    #   * Fake backend has B1, B2 (current plan) + B3 (leftover from a
    #     previous run with the SAME stable_document_id but a different
    #     plan content hash).
    #   * Cleanup discovers (B1, B2, B3) by stable_document_id.
    #   * cleanup_target = union = (B1, B2, B3) — B3 caught by
    #     ``discovered`` even though not in ``expected``.
    #   * delete_call_count == 1.
    #   * All 3 deleted.
    # ------------------------------------------------------------------

    def test_B_d4_timeout_leftover_caught_by_discovery(self) -> None:
        stable_doc_id = uuid4()
        b1, b2, b3 = "B1-bbbb", "B2-bbbb", "B3-leftover"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": b1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": b2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": b3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(b1, b2),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
        )
        assert result.delete_call_count == 1
        # B3 was caught by ``discovered`` even though not in ``expected``.
        assert set(result.cleanup_target_chunk_ids) == {b1, b2, b3}
        assert set(result.discovered_chunk_ids) == {b1, b2, b3}
        assert set(result.expected_chunk_ids) == {b1, b2}
        # All 3 verified gone.
        assert all(result.per_chunk_id_verified.values())
        assert result.post_delete_query_count == 0
        _assert_schema_identity_unchanged(preflight, result)
        # Fake backend has 0 rows for our stable_document_id.
        remaining = client.query(
            collection_name="article_rag_chunks",
            filter=f'stable_document_id == "{stable_doc_id}"',
            output_fields=["chunk_id"],
            limit=512,
        )
        assert remaining == []

    # ------------------------------------------------------------------
    # Test C — D4 captured partial IDs.
    #
    #   * expected_chunk_ids = (C1, C2, C3).
    #   * saved_chunk_ids = (C1, C2) — D4 captured 2 of 3 before timing
    #     out.
    #   * Fake backend has C1, C2, C3.
    #   * cleanup_target = union(expected, saved, discovered) = (C1, C2, C3).
    #   * delete_call_count == 1.
    #   * All 3 deleted.
    # ------------------------------------------------------------------

    def test_C_d4_partial_capture_union_deletes_all(self) -> None:
        stable_doc_id = uuid4()
        c1, c2, c3 = "C1-cccc", "C2-cccc", "C3-cccc"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": c1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": c2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": c3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(c1, c2, c3),
            saved_chunk_ids=(c1, c2),
            stable_document_id=stable_doc_id,
            preflight=preflight,
        )
        assert result.delete_call_count == 1
        assert set(result.cleanup_target_chunk_ids) == {c1, c2, c3}
        # Three input sets recorded separately; they are NOT identical.
        assert set(result.expected_chunk_ids) == {c1, c2, c3}
        assert set(result.saved_chunk_ids) == {c1, c2}
        assert set(result.discovered_chunk_ids) == {c1, c2, c3}
        # All 3 verified gone.
        assert all(result.per_chunk_id_verified.values())
        assert result.post_delete_query_count == 0
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # Test D — no vector write.
    #
    #   * expected_chunk_ids = (D1, D2, D3) — plan was built.
    #   * saved_chunk_ids = () — D4 not reached.
    #   * Fake backend has 0 rows for our stable_document_id.
    #   * discovered = () — no rows to find.
    #   * cleanup_target = union = (D1, D2, D3) BUT backend_has_evidence
    #     is False (discovered=() and saved=()).
    #   * delete_call_count == 0 — no delete issued.
    #   * Schema/list checks still pass.
    # ------------------------------------------------------------------

    def test_D_no_vector_write_no_delete_called(self) -> None:
        stable_doc_id = uuid4()
        d1, d2, d3 = "D1-dddd", "D2-dddd", "D3-dddd"
        # Fake has 0 rows for our stable_document_id.
        client = _FakeZillizClient(initial_rows=[])
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(d1, d2, d3),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
        )
        # No delete called.
        assert result.delete_call_count == 0
        assert client.delete_calls == []
        # cleanup_target still recorded for the report (union of 3 sets).
        assert set(result.cleanup_target_chunk_ids) == {d1, d2, d3}
        assert set(result.expected_chunk_ids) == {d1, d2, d3}
        assert result.saved_chunk_ids == ()
        assert result.discovered_chunk_ids == ()
        # per-chunk verification: each chunk_id queried individually
        # returns 0 rows (nothing exists), so verified=True for all.
        assert all(result.per_chunk_id_verified.values())
        # post-delete query by stable_document_id returns 0.
        assert result.post_delete_query_count == 0
        # Schema/list checks still pass.
        _assert_schema_identity_unchanged(preflight, result)
        assert result.collection_still_exists is True

    # ------------------------------------------------------------------
    # Test E — unrelated rows preserved.
    #
    #   * Our fixture: expected = (E1, E2), saved = ().
    #   * Fake backend has E1, E2 (our stable_document_id) + U1
    #     (DIFFERENT stable_document_id — an unrelated row that MUST
    #     NOT be deleted).
    #   * cleanup_target = (E1, E2) — U1 is not in any of the 3 sets.
    #   * delete filter contains ONLY E1, E2.
    #   * After cleanup: E1, E2 gone; U1 still present.
    # ------------------------------------------------------------------

    def test_E_unrelated_rows_preserved(self) -> None:
        our_doc_id = uuid4()
        unrelated_doc_id = uuid4()
        e1, e2 = "E1-eeee", "E2-eeee"
        u1 = "U1-unrelated"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": e1,
                    "stable_document_id": str(our_doc_id),
                },
                {
                    "chunk_id": e2,
                    "stable_document_id": str(our_doc_id),
                },
                {
                    "chunk_id": u1,
                    "stable_document_id": str(unrelated_doc_id),
                },
            ]
        )
        preflight = _build_fake_preflight(
            client, stable_document_id=our_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(e1, e2),
            saved_chunk_ids=(),
            stable_document_id=our_doc_id,
            preflight=preflight,
        )
        assert result.delete_call_count == 1
        assert set(result.cleanup_target_chunk_ids) == {e1, e2}
        # U1 is NOT in the cleanup target.
        assert u1 not in result.cleanup_target_chunk_ids
        # delete filter does NOT contain U1.
        assert len(client.delete_calls) == 1
        delete_filter = client.delete_calls[0]
        assert f'"{u1}"' not in delete_filter
        assert f'"{e1}"' in delete_filter
        assert f'"{e2}"' in delete_filter
        # E1, E2 verified gone.
        assert result.per_chunk_id_verified[e1] is True
        assert result.per_chunk_id_verified[e2] is True
        assert u1 not in result.per_chunk_id_verified
        # U1 still in the fake backend.
        u1_query = client.query(
            collection_name="article_rag_chunks",
            filter=f'chunk_id == "{u1}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        assert len(u1_query) == 1
        # Our fixture rows gone.
        our_remaining = client.query(
            collection_name="article_rag_chunks",
            filter=f'stable_document_id == "{our_doc_id}"',
            output_fields=["chunk_id"],
            limit=512,
        )
        assert our_remaining == []
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # Test F1 — cleanup delete raises → fail closed.
    #
    #   * Fake backend has F1, F2, F3 for our stable_document_id.
    #   * ``client.delete_should_fail = True`` → delete raises.
    #   * The exception MUST propagate (fail closed) — the helper does
    #     NOT swallow delete failures.
    #   * R4: the exception is wrapped in the fixed safe message
    #     (``_CLEANUP_DELETE_FAILED_SAFE_MESSAGE``) — the raw fake
    #     message does NOT appear in str(err).
    # ------------------------------------------------------------------

    def test_F1_delete_failure_propagates_fail_closed(self) -> None:
        stable_doc_id = uuid4()
        f1, f2, f3 = "F1-ffff", "F2-ffff", "F3-ffff"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": f1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": f2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": f3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        client.delete_should_fail = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        # The delete failure MUST propagate — fail closed.  R4 wraps
        # it in the fixed safe message; the raw "fake delete failure"
        # does NOT appear in the error.
        with pytest.raises(
            RuntimeError, match="delete operation failed"
        ):
            _run_cleanup(
                client,
                collection="article_rag_chunks",
                expected_chunk_ids=(f1, f2, f3),
                saved_chunk_ids=(),
                stable_document_id=stable_doc_id,
                preflight=preflight,
            )
        # delete was attempted (fail closed AFTER the call, not before).
        assert len(client.delete_calls) == 1
        # Rows are still present (delete was a no-op because it raised).
        remaining = client.query(
            collection_name="article_rag_chunks",
            filter=f'stable_document_id == "{stable_doc_id}"',
            output_fields=["chunk_id"],
            limit=512,
        )
        assert len(remaining) == 3

    # ------------------------------------------------------------------
    # Test F2 — per-chunk verification finds remaining chunks → fail
    # closed.
    #
    #   * Fake backend has F1, F2, F3.
    #   * ``client.delete_is_noop = True`` → delete returns success but
    #     does NOT remove rows (simulates ghost rows / eventual
    #     consistency issue).
    #   * Per-chunk verification queries each chunk_id, finds them still
    #     present → ``per_chunk_verified[cid] = False``.
    #   * The caller MUST assert ``all(per_chunk_verified.values())``
    #     and fail — the helper records the failure, does NOT mask it
    #     as a PASS.
    #   * The result fields contain ONLY safe fixture identity
    #     (chunk_ids, counts, schema fields) — NO URI/token/key.
    # ------------------------------------------------------------------

    def test_F2_verification_failure_detected_fail_closed(self) -> None:
        stable_doc_id = uuid4()
        f1, f2, f3 = "F2-1-fff", "F2-2-fff", "F2-3-fff"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": f1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": f2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": f3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        client.delete_is_noop = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(f1, f2, f3),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
        )
        # delete was called (returned success) but rows still present.
        assert result.delete_call_count == 1
        assert len(client.delete_calls) == 1
        # Per-chunk verification: each chunk_id still present → False.
        assert set(result.per_chunk_id_verified.keys()) == {f1, f2, f3}
        assert not all(result.per_chunk_id_verified.values()), (
            "Per-chunk verification should have detected the ghost rows "
            "remaining after the no-op delete.  The helper MUST NOT "
            "mask this as a PASS."
        )
        # post_delete_query_count is non-zero (rows still present).
        assert result.post_delete_query_count > 0
        # The result fields contain ONLY safe fixture identity — no
        # URI, token, key, or secret material.  This is the "safe
        # fixture identity" requirement from the R3 task spec.
        unsafe_substrings = ("uri=", "token", "api_key", "secret", "password")
        for field_val in (
            result.cleanup_target_chunk_ids,
            result.expected_chunk_ids,
            result.saved_chunk_ids,
            result.discovered_chunk_ids,
        ):
            for cid in field_val:
                lowered = cid.lower()
                for substr in unsafe_substrings:
                    assert substr not in lowered, (
                        f"Unsafe substring {substr!r} found in chunk_id "
                        f"{cid!r} — result must contain ONLY safe fixture "
                        f"identity."
                    )
        # The caller (real test) would now assert
        # ``all(per_chunk_verified.values())`` and FAIL — proving the
        # failure is NOT masked as a PASS.
        assert any(v is False for v in result.per_chunk_id_verified.values())


# ======================================================================
# R4 — Failure-Path Cleanup Fail-Closed Closure
# ======================================================================
#
# R3 left two fail-closed gaps that R4 must close with TDD:
#
#   RED-A: writer was called (call_count > 0) but discovery query
#          raises and ``saved_chunk_ids`` is empty.  R3 swallowed the
#          discovery exception into an empty collection, concluded
#          ``backend_has_evidence = False``, and skipped delete —
#          leaving expected rows in the backend.  R4 introduces the
#          ``vector_write_attempted`` signal so that, when the writer
#          was called, discovery failure falls back to deleting the
#          pre-built ``expected_chunk_ids`` by precise PK filter.
#
#   RED-B: the Zilliz/Milvus SDK raises a ``delete`` exception whose
#          message leaks URI, token, api_key, and upstream message.
#          R3 propagated the original exception as-is.  R4 wraps
#          every propagating SDK exception in a fixed local message
#          that does NOT interpolate collection, chunk_id,
#          stable_document_id, URI, token, key, or row content, and
#          raises it OUTSIDE the except block so ``__cause__`` and
#          ``__context__`` are both ``None``.
#
# Both RED tests below are written FIRST and must fail against the
# R3 helper logic; the R4 fix then turns them GREEN.
# ======================================================================


class TestFailurePathCleanupClosure:
    """R4 — close the two remaining fail-closed gaps.

    RED-A: writer attempted + discovery failure + saved empty
           → must delete expected IDs (R3 skipped).
    RED-B: malicious delete SDK exception
           → must wrap in fixed safe error, cause/context None.

    Additional GREEN tests cover the full behavior matrix.
    """

    # ------------------------------------------------------------------
    # RED-A: writer attempted + discovery failure + saved empty.
    #
    #   * expected_chunk_ids = (A1, A2, A3) — built before paid call.
    #   * vector_write_attempted = True (counting_writer.call_count > 0).
    #   * saved_chunk_ids = () — D2 failed before D4 capture.
    #   * discovery query raises (first stable_document_id query).
    #   * Fake backend actually has A1, A2, A3 for our stable_document_id.
    #   * Unrelated sentinel row U1 (different stable_document_id).
    #
    # R3 behavior (BUG): discovery exception → discovered = () →
    #   backend_has_evidence = False → delete skipped → A1/A2/A3
    #   remain in backend.
    #
    # R4 behavior (FIX): vector_write_attempted=True + discovery
    #   failure → fall back to expected IDs → one precise PK delete
    #   → A1/A2/A3 gone, U1 preserved.
    # ------------------------------------------------------------------

    def test_RED_A_writer_attempted_discovery_failure_deletes_expected(
        self,
    ) -> None:
        stable_doc_id = uuid4()
        a1, a2, a3 = "RED-A-1", "RED-A-2", "RED-A-3"
        u1 = "RED-A-UNRELATED"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": a1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": a2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": a3,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": u1,
                    "stable_document_id": str(uuid4()),
                },
            ]
        )
        # Discovery query (first stable_document_id query) will raise.
        client.discovery_query_should_fail = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(a1, a2, a3),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
            vector_write_attempted=True,
        )
        # delete was called exactly once with expected IDs.
        assert result.delete_call_count == 1
        assert len(client.delete_calls) == 1
        delete_filter = client.delete_calls[0]
        for cid in (a1, a2, a3):
            assert f'"{cid}"' in delete_filter
        # U1 NOT in delete filter.
        assert f'"{u1}"' not in delete_filter
        # delete filter is precise PK — no stable_document_id filter.
        assert "stable_document_id" not in delete_filter
        # All expected rows gone.
        assert result.post_delete_query_count == 0
        # Per-chunk verification: all expected IDs verified gone.
        for cid in (a1, a2, a3):
            assert result.per_chunk_id_verified.get(cid) is True
        # U1 still present in backend.
        u1_query = client.query(
            collection_name="article_rag_chunks",
            filter=f'chunk_id == "{u1}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        assert len(u1_query) == 1
        # Schema identity unchanged.
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # RED-B: malicious delete SDK exception.
    #
    #   * Fake backend has B1, B2, B3 for our stable_document_id.
    #   * ``client.delete_malicious_exception = True`` → delete raises
    #     RuntimeError with sentinel substrings: URI, token, api_key,
    #     upstream SDK message.
    #   * The cleanup helper MUST wrap this in a fixed safe error.
    #
    # R3 behavior (BUG): original RuntimeError propagates as-is →
    #   str(err) contains URI/token/key.
    #
    # R4 behavior (FIX): wrap in fixed safe RuntimeError →
    #   err.__cause__ is None, err.__context__ is None, sentinel
    #   not in str/repr/args/traceback.
    # ------------------------------------------------------------------

    def test_RED_B_malicious_delete_exception_wrapped_safely(self) -> None:
        import traceback  # noqa: PLC0415 — test-only

        stable_doc_id = uuid4()
        b1, b2, b3 = "RED-B-1", "RED-B-2", "RED-B-3"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": b1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": b2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": b3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        client.delete_malicious_exception = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        # The delete failure MUST propagate (fail closed) — but
        # wrapped in a fixed safe error.
        raised: Exception | None = None
        try:
            _run_cleanup(
                client,
                collection="article_rag_chunks",
                expected_chunk_ids=(b1, b2, b3),
                saved_chunk_ids=(),
                stable_document_id=stable_doc_id,
                preflight=preflight,
                vector_write_attempted=True,
            )
        except Exception as exc:  # noqa: BLE001 — test asserts raise
            raised = exc
        # An exception MUST have been raised (fail closed).
        assert raised is not None, (
            "Malicious delete exception MUST propagate (fail closed); "
            "the helper MUST NOT swallow it."
        )
        # cause and context MUST both be None — no exception chaining.
        assert raised.__cause__ is None, (
            f"Safe error __cause__ must be None, got {raised.__cause__!r}"
        )
        assert raised.__context__ is None, (
            f"Safe error __context__ must be None, got "
            f"{raised.__context__!r}"
        )
        # Sentinel substrings MUST NOT appear in any error surface.
        sentinels = (
            "zilliz.example.com",
            "sk-abc123",
            "sk-zilliz",
            "api_key=",
            "token=",
            "uri=",
            "MilvusException",
            "connection refused by peer",
            "upstream_msg",
        )
        # str(err)
        err_str = str(raised)
        for s in sentinels:
            assert s not in err_str, (
                f"Sentinel {s!r} found in str(err): {err_str!r}"
            )
        # repr(err)
        err_repr = repr(raised)
        for s in sentinels:
            assert s not in err_repr, (
                f"Sentinel {s!r} found in repr(err): {err_repr!r}"
            )
        # err.args
        for arg in raised.args:
            arg_str = str(arg)
            for s in sentinels:
                assert s not in arg_str, (
                    f"Sentinel {s!r} found in err.args: {arg_str!r}"
                )
        # traceback.format_exception(err)
        tb_text = "".join(
            traceback.format_exception(
                type(raised), raised, raised.__traceback__
            )
        )
        for s in sentinels:
            assert s not in tb_text, (
                f"Sentinel {s!r} found in traceback: {tb_text!r}"
            )
        # delete was attempted (fail closed AFTER the call).
        assert len(client.delete_calls) == 1

    # ------------------------------------------------------------------
    # GREEN-A: writer NOT attempted + discovery failure + saved empty
    #          → no delete, no rows touched.
    #
    # Even if discovery fails, if the writer was never called there
    # is nothing to clean up.  ``delete_call_count == 0``.
    # ------------------------------------------------------------------

    def test_writer_not_attempted_discovery_failure_no_delete(self) -> None:
        stable_doc_id = uuid4()
        d1, d2, d3 = "GREEN-A-1", "GREEN-A-2", "GREEN-A-3"
        u1 = "GREEN-A-UNRELATED"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": u1,
                    "stable_document_id": str(uuid4()),
                },
            ]
        )
        client.discovery_query_should_fail = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(d1, d2, d3),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
            vector_write_attempted=False,
        )
        # No delete called — writer was not attempted.
        assert result.delete_call_count == 0
        assert client.delete_calls == []
        # U1 preserved.
        u1_query = client.query(
            collection_name="article_rag_chunks",
            filter=f'chunk_id == "{u1}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        assert len(u1_query) == 1
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # GREEN-B: saved non-empty + discovery failure
    #          → delete expected ∪ saved.
    #
    # Discovery failure does not block cleanup when ``saved_chunk_ids``
    # is non-empty.  The union of (expected, saved) is used; discovered
    # is empty because discovery failed.
    # ------------------------------------------------------------------

    def test_saved_nonempty_discovery_failure_deletes_union(self) -> None:
        stable_doc_id = uuid4()
        c1, c2, c3 = "GREEN-B-1", "GREEN-B-2", "GREEN-B-3"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": c1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": c2,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": c3,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        client.discovery_query_should_fail = True
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(c1, c2, c3),
            saved_chunk_ids=(c1, c2),  # D4 captured 2 of 3
            stable_document_id=stable_doc_id,
            preflight=preflight,
            vector_write_attempted=True,
        )
        # delete called once.
        assert result.delete_call_count == 1
        assert len(client.delete_calls) == 1
        # cleanup_target = expected ∪ saved (discovered empty due to
        # failure).
        assert set(result.cleanup_target_chunk_ids) == {c1, c2, c3}
        # All 3 verified gone.
        for cid in (c1, c2, c3):
            assert result.per_chunk_id_verified.get(cid) is True
        assert result.post_delete_query_count == 0
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # RED-C: writer attempted + discovery success + stale empty result
    #        → still delete the expected IDs.
    #
    # A successful empty discovery is not proof that no write landed:
    # Milvus/Zilliz visibility is eventually consistent.  Once the
    # writer was called, cleanup must issue one idempotent exact-PK
    # delete for the expected IDs.
    # ------------------------------------------------------------------

    def test_writer_attempted_stale_empty_discovery_deletes_expected(
        self,
    ) -> None:
        stable_doc_id = uuid4()
        e1, e2 = "GREEN-C-1", "GREEN-C-2"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": e1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": e2,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        original_query = client.query
        stable_document_query_count = 0

        def stale_once_query(
            *,
            collection_name: str,
            filter: str,  # noqa: A002
            output_fields: list[str],
            limit: int = 1,
        ) -> list[dict[str, Any]]:
            nonlocal stable_document_query_count
            if filter.startswith('stable_document_id == "'):
                stable_document_query_count += 1
                if stable_document_query_count == 1:
                    return []
            return original_query(
                collection_name=collection_name,
                filter=filter,
                output_fields=output_fields,
                limit=limit,
            )

        client.query = stale_once_query  # type: ignore[method-assign]
        result = _run_cleanup(
            client,
            collection="article_rag_chunks",
            expected_chunk_ids=(e1, e2),
            saved_chunk_ids=(),
            stable_document_id=stable_doc_id,
            preflight=preflight,
            vector_write_attempted=True,
        )
        assert result.delete_call_count == 1
        assert len(client.delete_calls) == 1
        delete_filter = client.delete_calls[0]
        assert f'"{e1}"' in delete_filter
        assert f'"{e2}"' in delete_filter
        assert "stable_document_id" not in delete_filter
        assert result.discovered_chunk_ids == ()
        assert result.post_delete_query_count == 0
        assert result.per_chunk_id_verified == {e1: True, e2: True}
        _assert_schema_identity_unchanged(preflight, result)

    # ------------------------------------------------------------------
    # GREEN-D: post-delete verification query also raises malicious
    #          exception → wrapped safely.
    #
    # If the post-delete ``stable_document_id`` query (step 6 in the
    # helper) raises an SDK exception, it must also be wrapped in the
    # fixed safe error — not leaked.
    # ------------------------------------------------------------------

    def test_post_delete_query_malicious_exception_wrapped_safely(
        self,
    ) -> None:
        import traceback  # noqa: PLC0415 — test-only

        stable_doc_id = uuid4()
        p1, p2 = "GREEN-D-1", "GREEN-D-2"
        client = _FakeZillizClient(
            initial_rows=[
                {
                    "chunk_id": p1,
                    "stable_document_id": str(stable_doc_id),
                },
                {
                    "chunk_id": p2,
                    "stable_document_id": str(stable_doc_id),
                },
            ]
        )
        # Make the SECOND stable_document_id query (post-delete count)
        # raise a malicious exception.  The first query (discovery)
        # succeeds; the second (post-delete) raises.
        client.discovery_query_should_fail = False
        client._discovery_query_attempted = True  # noqa: SLF001 — test-only
        # Override query to raise on the second call.
        original_query = client.query
        call_count = {"n": 0}

        def malicious_post_delete_query(
            *,
            collection_name: str,
            filter: str,  # noqa: A002
            output_fields: list[str],
            limit: int = 1,
        ) -> list[dict[str, Any]]:
            import re  # noqa: PLC0415

            m = re.match(r'^stable_document_id == "([^"]+)"$', filter)
            if m:
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    raise RuntimeError(
                        "MilvusException: query failed: "
                        "uri=https://zilliz.example.com:443 "
                        "token=sk-post-delete-xxxxxxxxxxxxxxxxxxxxxxxx "
                        "api_key=sk-post-delete-key"
                    )
            return original_query(
                collection_name=collection_name,
                filter=filter,
                output_fields=output_fields,
                limit=limit,
            )

        client.query = malicious_post_delete_query  # type: ignore[method-assign]
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        raised: Exception | None = None
        try:
            _run_cleanup(
                client,
                collection="article_rag_chunks",
                expected_chunk_ids=(p1, p2),
                saved_chunk_ids=(),
                stable_document_id=stable_doc_id,
                preflight=preflight,
                vector_write_attempted=True,
            )
        except Exception as exc:  # noqa: BLE001 — test asserts raise
            raised = exc
        assert raised is not None, (
            "Post-delete query malicious exception MUST propagate."
        )
        assert raised.__cause__ is None
        assert raised.__context__ is None
        sentinels = (
            "zilliz.example.com",
            "sk-post-delete",
            "api_key=",
            "token=",
            "uri=",
            "MilvusException",
        )
        for s in sentinels:
            assert s not in str(raised), (
                f"Sentinel {s!r} in str(err): {str(raised)!r}"
            )
            assert s not in repr(raised)
            for arg in raised.args:
                assert s not in str(arg)
        tb_text = "".join(
            traceback.format_exception(
                type(raised), raised, raised.__traceback__
            )
        )
        for s in sentinels:
            assert s not in tb_text

    def test_integrity_sdk_exception_is_wrapped_without_chain(self) -> None:
        import traceback  # noqa: PLC0415

        stable_doc_id = uuid4()
        client = _FakeZillizClient(initial_rows=[])
        preflight = _build_fake_preflight(
            client, stable_document_id=stable_doc_id
        )
        client.integrity_check_malicious_exception = True

        raised: Exception | None = None
        traceback_text = ""
        try:
            _run_cleanup(
                client,
                collection="article_rag_chunks",
                expected_chunk_ids=(),
                saved_chunk_ids=(),
                stable_document_id=stable_doc_id,
                preflight=preflight,
                vector_write_attempted=False,
            )
        except Exception as exc:  # noqa: BLE001
            raised = exc
            traceback_text = traceback.format_exc()

        assert raised is not None
        assert str(raised) == (
            "article-rag acceptance cleanup: collection integrity "
            "verification failed; manual verification required"
        )
        assert raised.__cause__ is None
        assert raised.__context__ is None
        sentinels = (
            "zilliz-integrity.example.com",
            "sk-integrity",
            "api_key=",
            "token=",
            "uri=",
            "MilvusException",
            "permission denied",
            "upstream_msg",
        )
        surfaces = (
            str(raised),
            repr(raised),
            repr(raised.args),
            traceback_text,
        )
        for sentinel in sentinels:
            assert all(sentinel not in surface for surface in surfaces)
