"""Article RAG Single-Path Real-Chain Acceptance (R1).

This module is the canonical real-chain acceptance smoke for the
Article RAG single-path convergence.  It replaces the prior
``article_rag_index_smoke_*`` design in ``test_d6_i4z_article_rag_local_dry_run.py``
which was mutually exclusive with the worker's frozen-contract
collection enforcement.

Design contract (R1):

  A. **Single collection identity.**  The smoke writes to
     ``ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection`` (i.e.
     ``article_rag_chunks``) — the SAME collection production uses.
     There is no second smoke collection, no prefix allowlist, no
     compatibility flag.  The worker's fail-closed contract
     enforcement at ``article_rag_index_worker.py:644`` is correct
     and MUST NOT be relaxed.

  B. **Precise fixture isolation.**  Each smoke run generates unique
     UUIDs for user / record / base / stable_document.  Cleanup
     deletes by an exact ``stable_document_id == "<uuid>"`` filter —
     never drop / recreate the collection.  Protected collections
     (``grammar_note_examples``, ``sentence_analysis_examples``)
     are never touched.

  C. **Bounded real-call budget.**  At most:
       - 1 document embedding batch (1 outbound provider call)
       - 1 vector write (1 Zilliz upsert)
       - 1 retrieval query embedding (1 outbound provider call)
       - 1 vector search (1 Zilliz search)
       - 0 rerank calls
       - 0 Ask model calls (this smoke stops at Ask context assembly;
         a separate task must opt into a real Ask model call)
     No retries.  Stop on first failure.

  D. **Acceptance assertions** (11 items, see test docstring).

  E. **Report facts.**  The test distinguishes:
       - smoke execution attempts
       - embedding provider API attempts
       - query embedding attempts
       - Ask model attempts (always 0 in R1)
       - vector writes
       - vector searches

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
    """Precise per-call accounting for the real-chain report."""

    smoke_execution_attempts: int = 0
    embedding_provider_api_attempts: int = 0
    query_embedding_attempts: int = 0
    ask_model_attempts: int = 0
    vector_writes: int = 0
    vector_searches: int = 0
    vector_deletes: int = 0

    def as_report(self) -> dict[str, int]:
        return {
            "smoke_execution_attempts": self.smoke_execution_attempts,
            "embedding_provider_api_attempts": (
                self.embedding_provider_api_attempts
            ),
            "query_embedding_attempts": self.query_embedding_attempts,
            "ask_model_attempts": self.ask_model_attempts,
            "vector_writes": self.vector_writes,
            "vector_searches": self.vector_searches,
            "vector_deletes": self.vector_deletes,
        }


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

from tests.test_d6_i4a_article_rag_index_plan import (  # noqa: E402
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
    from tests.test_d6_i4a_article_rag_index_plan import (
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
    """Immutable snapshot of Zilliz state before the smoke runs."""

    collection_exists: bool
    field_count: int
    field_names: tuple[str, ...]
    protected_collections_present: dict[str, bool]
    all_collections: tuple[str, ...]
    fixture_chunk_count: int  # chunks with our stable_document_id (must be 0)
    # ``stats`` may be unavailable on some Zilliz plans; we record it
    # only when the API returns a stable row count.
    collection_row_count: int | None


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

        # 2. Schema: field count + names.
        describe = client.describe_collection(  # type: ignore[attr-defined]
            collection_name=collection
        )
        fields = describe.get("fields", []) if isinstance(describe, dict) else []
        field_names = tuple(
            f.get("name", "") if isinstance(f, dict) else str(f)
            for f in fields
        )
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

        # 3. Protected collections present (verify they exist; we never
        # touch them).
        protected_present: dict[str, bool] = {}
        for protected in PROTECTED_ZILLIZ_COLLECTIONS:
            protected_present[protected] = bool(
                client.has_collection(collection_name=protected)  # type: ignore[attr-defined]
            )

        # 4. All collections (for the report).
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
            protected_collections_present=protected_present,
            all_collections=all_collections,
            fixture_chunk_count=fixture_chunk_count,
            collection_row_count=row_count,
        )

    return await asyncio.to_thread(_sync)


@dataclass
class _CleanupResult:
    """Result of precise fixture cleanup."""

    deleted_count: int
    post_delete_query_count: int  # MUST be 0
    collection_still_exists: bool
    field_count_after: int
    protected_collections_unchanged: dict[str, bool]
    collection_row_count_after: int | None


async def _precise_cleanup(
    client: object,
    *,
    collection: str,
    stable_document_id: UUID,
    preflight: _ZillizPreflightSnapshot,
) -> _CleanupResult:
    """Delete EXACTLY the chunks with our stable_document_id, then
    verify deletion + collection integrity.

    Never drops / recreates the collection.  Never touches protected
    collections.  If post-delete verification fails, raise (the report
    will mark the smoke as BLOCKED on cleanup).
    """

    def _sync() -> _CleanupResult:
        # 1. Delete by exact filter.
        delete_result = client.delete(  # type: ignore[attr-defined]
            collection_name=collection,
            filter=f'stable_document_id == "{stable_document_id}"',
        )
        # pymilvus delete returns a dict with "delete_count" on some
        # versions; fall back to None if the shape differs.
        deleted_count = 0
        if isinstance(delete_result, dict):
            deleted_count = int(delete_result.get("delete_count", 0))
        elif isinstance(delete_result, int):
            deleted_count = delete_result

        # 2. Flush (best-effort) so the delete is durable for the
        # subsequent query.  Some Zilliz plans flush automatically;
        # we call it explicitly to make the verification query reliable.
        try:
            client.flush(collection_name=collection)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — flush is best-effort
            pass

        # 3. Query: confirm 0 chunks remain with our stable_document_id.
        post_delete = client.query(  # type: ignore[attr-defined]
            collection_name=collection,
            filter=f'stable_document_id == "{stable_document_id}"',
            output_fields=["chunk_id"],
            limit=1,
        )
        post_delete_query_count = len(post_delete) if post_delete else 0

        # 4. Collection still exists + schema unchanged.
        collection_still_exists = bool(
            client.has_collection(collection_name=collection)  # type: ignore[attr-defined]
        )
        describe = client.describe_collection(  # type: ignore[attr-defined]
            collection_name=collection
        )
        fields = describe.get("fields", []) if isinstance(describe, dict) else []
        field_count_after = len(fields)

        # 5. Protected collections unchanged.
        protected_unchanged: dict[str, bool] = {}
        for protected in PROTECTED_ZILLIZ_COLLECTIONS:
            protected_unchanged[protected] = bool(
                client.has_collection(collection_name=protected)  # type: ignore[attr-defined]
            )

        # 6. Row count after (best-effort).
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
            collection_still_exists=collection_still_exists,
            field_count_after=field_count_after,
            protected_collections_unchanged=protected_unchanged,
            collection_row_count_after=row_count_after,
        )

    return await asyncio.to_thread(_sync)


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
    """Single-path real-chain acceptance smoke (R1).

    This is the canonical acceptance smoke for the Article RAG
    single-path convergence.  It exercises the FULL chain:

      1. Zero-call Zilliz preflight (collection, schema, count,
         fixture IDs, protected collections).
      2. Settings + worker + frozen contract all resolve to
         ``article_rag_chunks`` (three-way identity check).
      3. Seed a minimal article_ready record with UNIQUE UUIDs on a
         per-test temp Postgres schema.
      4. ``lifecycle.ensure_article_rag_index_job_in_transaction``.
      5. ``worker.process_next`` (1 tick — 1 embedding batch + 1
         vector write).
      6. ``retrieval.retrieve_for_record`` (1 query embedding + 1
         vector search).
      7. Ask context assembly (``ArticleRagAskContextProvider.build_for_ask``).
         **R1 stops here — NO real Ask model call.**
      8. Precise Zilliz cleanup (delete by ``stable_document_id``
         filter, verify deletion, verify collection integrity).

    Acceptance assertions (11 items):

      D1. Worker claim succeeded; provider was actually invoked.
      D2. Job/run reached succeeded terminal state.
      D3. index_run.status == "indexed".
      D4. Zilliz contains chunks with our stable_document_id.
      D5. retrieval hit_count > 0 and hits reference our fixture.
      D6. Retrieval uses Postgres plan for citation (not vector
          payload).
      D7. Citation block/unit/anchor/range match plan truth.
      D8. Ask evidence/context contains Article RAG retrieval result.
      D9. (N/A — R1 does not make a real Ask model call; the report
          explicitly marks "Ask context assembly only".)
      D10. (N/A — R1 does not make a real Ask model call.)
      D11. Cleanup: fixture vectors deleted, collection/schema/
           protected collections unchanged.

    Budget:

      - smoke_execution_attempts: 1
      - embedding_provider_api_attempts: 1 (document embedding batch)
      - query_embedding_attempts: 1 (retrieval query)
      - ask_model_attempts: 0 (R1 stops at context assembly)
      - vector_writes: 1
      - vector_searches: 1
      - vector_deletes: 1

    Stop on first failure.  No retries.  Cleanup runs in ``finally``
    regardless of success or failure.
    """
    counts = CallCounts()
    counts.smoke_execution_attempts = 1

    from app.contracts.article_rag_contract import (  # noqa: I001
        ARTICLE_RAG_EMBEDDING_CONTRACT,
    )

    # -----------------------------------------------------------------
    # Three-way collection identity check (Settings + worker + contract)
    # -----------------------------------------------------------------
    # The worker's ``_default_vector_collection`` is checked later at
    # line ~877 (after ``build_worker_service``).  Here we verify
    # Settings + frozen contract resolve to the SAME collection.
    # We do NOT check ``os.environ.get("READER_ARTICLE_RAG_ZILLIZ_*
    # COLLECTION")`` because the value may live only in ``.env`` (read
    # by pydantic-settings) and not in the OS environment.  The Settings
    # value IS the production source of truth for the worker.
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
    # Sanity: the factory must produce the REAL providers.
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
    # OUTER TRY: all paid calls (worker + retrieval + Ask context) are
    # wrapped so that ``_precise_cleanup`` runs in ``finally``
    # regardless of success or failure.  If any D1-D8 assertion fails,
    # the finally still deletes our fixture chunks from Zilliz.  This
    # is required by the R1 contract.
    # -----------------------------------------------------------------
    zilliz_chunk_count = 0  # set in D4; default 0 for finally safety
    cleanup_result: _CleanupResult | None = None
    worker_result: ArticleRagIndexWorkerResult | None = None
    try:
        # -----------------------------------------------------------------
        # Worker tick — 1 embedding batch + 1 vector write
        # -----------------------------------------------------------------
        worker_result: ArticleRagIndexWorkerResult | None = None
        try:
            worker_result = await worker.process_next(
                lease_owner="test-r1-acceptance",
                lease_duration=timedelta(seconds=120),
            )
            counts.embedding_provider_api_attempts = 1
            counts.vector_writes = 1
        except ArticleRagIndexWorkerError as exc:
            counts.embedding_provider_api_attempts = 1
            pytest.fail(
                f"Worker raised ArticleRagIndexWorkerError "
                f"({type(exc).__name__}, failure_code={exc.failure_code}, "
                f"retryable={exc.retryable}).  Call counts: "
                f"{counts.as_report()}"
            )

        # D1: Worker claim succeeded; provider was actually invoked.
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

        # D2: Job reached succeeded terminal state.  The index_run row
        # (verified below as D3) is the canonical proof of the worker
        # chain reaching terminal success — ``reader_runs`` is intentionally
        # not asserted here because the article_rag_index_build job_id does
        # not always equal the reader_runs.id, and the index_run is the
        # authoritative durable state for this substrate.
        async with acceptance_env.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT status, failure_class, failure_code FROM reader_jobs "
                "WHERE id = $1",
                ensure_result.job_id,
            )
            index_run_row = await conn.fetchrow(
                "SELECT status, embedding_model, vector_store_provider, "
                "vector_collection, completed_at FROM reader_article_rag_index_runs "
                "WHERE id = $1",
                ensure_result.index_run_id,
            )
        assert job_row is not None
        assert job_row["status"] == "succeeded"
        assert job_row["failure_class"] is None
        assert job_row["failure_code"] is None

        # D3: index_run.status == "indexed".
        assert index_run_row is not None
        assert index_run_row["status"] == "indexed"
        assert index_run_row["completed_at"] is not None
        assert index_run_row["embedding_model"] == worker_result.embedding_model
        assert index_run_row["vector_store_provider"] == "zilliz"
        assert index_run_row["vector_collection"] == contract_collection

        # -----------------------------------------------------------------
        # D4: Zilliz contains chunks with our stable_document_id.
        # -----------------------------------------------------------------
        def _verify_chunks_in_zilliz() -> int:
            result = zilliz_client.query(  # type: ignore[attr-defined]
                collection_name=contract_collection,
                filter=f'stable_document_id == "{ids.stable_document_id}"',
                output_fields=["chunk_id", "stable_document_id", "base_id"],
                limit=64,
            )
            return len(result) if result else 0

        zilliz_chunk_count = await asyncio.to_thread(_verify_chunks_in_zilliz)
        assert zilliz_chunk_count > 0, (
            f"Expected >0 chunks in Zilliz with stable_document_id="
            f"{ids.stable_document_id}, got 0.  "
            f"Call counts: {counts.as_report()}"
        )

        # -----------------------------------------------------------------
        # D5 + D6 + D7: Retrieval with citation from Postgres plan
        # -----------------------------------------------------------------
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
        real_embedder = DashScopeArticleRagEmbeddingProvider(
            model_override=(
                settings.reader_article_rag_embedding_model or None
            ),
        )
        retrieval = ArticleRagRetrievalService(
            pool=acceptance_env,
            embedding_provider=real_embedder,
            vector_searcher=real_searcher,
        )
        retrieval_result = await retrieval.retrieve_for_record(
            reading_record_id=ids.record_id,
            user_id=ids.user_id,
            query_text="acceptance probe sentence",
        )
        counts.query_embedding_attempts = 1
        counts.vector_searches = 1

        assert isinstance(retrieval_result, ArticleRagRetrievalResult)
        assert retrieval_result.reading_record_id == ids.record_id
        assert retrieval_result.stable_document_id == ids.stable_document_id
        assert retrieval_result.base_id == ids.base_id

        # D5: hit_count > 0 and hits reference our fixture.
        assert isinstance(retrieval_result.hits, tuple)
        assert len(retrieval_result.hits) > 0, (
            f"Retrieval returned 0 hits — expected >0 for our freshly "
            f"indexed fixture.  Call counts: {counts.as_report()}"
        )
        for hit in retrieval_result.hits:
            # Every hit must reference our fixture's stable_document_id.
            assert hit.stable_document_id == ids.stable_document_id
            assert hit.base_id == ids.base_id

        # D6 + D7: citation comes from Postgres plan, not vector payload.
        # The retrieval service joins vector hits against the Postgres plan;
        # the citation's block_ids / unit_ids / anchor_segment_ids must
        # match the plan truth.
        for hit in retrieval_result.hits:
            citation = hit.citation
            assert citation is not None
            assert citation.stable_document_id == ids.stable_document_id
            assert citation.base_id == ids.base_id
            assert citation.reading_record_id == ids.record_id
            # block_ids must be non-empty and reference our seeded block.
            assert len(citation.block_ids) > 0
            assert "paragraph-1" in citation.block_ids or "heading-1" in citation.block_ids
            # canonical offsets must be non-null for main_reading route.
            assert citation.canonical_text_start_utf16 is not None
            assert citation.canonical_text_end_utf16 is not None

        # -----------------------------------------------------------------
        # D8: Ask context assembly (NO real Ask model call in R1)
        # -----------------------------------------------------------------
        from app.services.reader_orchestration.article_rag_ask_context_composer import (  # noqa: E501
            ArticleRagAskContextComposer,
        )
        from app.services.reader_orchestration.article_rag_ask_context_provider import (  # noqa: E501
            ArticleRagAskContextProvider,
        )
        from app.services.reader_orchestration.article_rag_ask_context_resolver import (  # noqa: E501
            ArticleRagAskContextResolver,
        )
        from app.services.reader_orchestration.article_rag_ask_integration_adapter import (  # noqa: E501
            ArticleRagAskIntegrationAdapter,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_assembly import (  # noqa: E501
            ArticleRagAskPromptAssemblyService,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_attachment import (  # noqa: E501
            ArticleRagAskPromptAttachmentService,
        )
        from app.services.reader_orchestration.article_rag_ask_prompt_section import (  # noqa: E501
            ArticleRagAskPromptSectionBuilder,
        )
        from app.services.reader_orchestration.article_rag_ask_runtime_adapter import (  # noqa: E501
            ArticleRagAskRuntimeAdapter,
        )
        from app.services.reader_orchestration.article_rag_context_service import (  # noqa: E501
            ArticleRagContextService,
        )

        ask_provider = ArticleRagAskContextProvider(
            integration_adapter=ArticleRagAskIntegrationAdapter(
                attachment_service=ArticleRagAskPromptAttachmentService(
                    resolver=ArticleRagAskContextResolver(
                        context_service=ArticleRagContextService(
                            retrieval_service=retrieval,
                        ),
                        composer=ArticleRagAskContextComposer(),
                    ),
                ),
            ),
            section_builder=ArticleRagAskPromptSectionBuilder(),
            runtime_adapter=ArticleRagAskRuntimeAdapter(),
            assembly_service=ArticleRagAskPromptAssemblyService(),
        )
        assembly = await ask_provider.build_for_ask(
            reading_record_id=ids.record_id,
            user_id=ids.user_id,
            query_text="acceptance probe sentence",
        )
        # Ask model attempts stays at 0 — R1 stops at context assembly.
        assert counts.ask_model_attempts == 0

        # D8: Ask evidence/context contains Article RAG retrieval result.
        assert assembly.kind == "article_rag_context"
        assert assembly.status in {
            "available",
            "not_indexed_or_unavailable",
        }
        # If hits were found, the assembly should be "available".
        if len(retrieval_result.hits) > 0:
            assert assembly.status == "available", (
                f"Retrieval returned {len(retrieval_result.hits)} hits but "
                f"Ask assembly status is {assembly.status!r} (expected "
                f"'available').  The Ask context must attach the Article "
                f"RAG evidence when hits exist."
            )
    finally:
        # -----------------------------------------------------------------
        # D11: Precise cleanup (runs in finally regardless of pass/fail)
        # -----------------------------------------------------------------
        cleanup_result = await _precise_cleanup(
            zilliz_client,
            collection=contract_collection,
            stable_document_id=ids.stable_document_id,
            preflight=preflight,
        )
        counts.vector_deletes = 1

        assert cleanup_result.post_delete_query_count == 0, (
            f"Cleanup FAILED: {cleanup_result.post_delete_query_count} "
            f"chunks with stable_document_id={ids.stable_document_id} "
            f"still exist after delete.  Manual cleanup required."
        )
        assert cleanup_result.collection_still_exists is True
        assert cleanup_result.field_count_after == preflight.field_count, (
            f"Cleanup FAILED: collection field count changed "
            f"({preflight.field_count} -> {cleanup_result.field_count_after}). "
            f"Collection schema must be unchanged."
        )
        # Protected collections unchanged.
        for protected, was_present in preflight.protected_collections_present.items():
            now_present = cleanup_result.protected_collections_unchanged.get(
                protected, False
            )
            assert was_present == now_present, (
                f"Cleanup FAILED: protected collection {protected!r} "
                f"presence changed ({was_present} -> {now_present})."
            )
        # Row count: informational only in Milvus.  The authoritative
        # cleanup check is ``post_delete_query_count == 0`` above —
        # Milvus ``get_collection_stats`` row count lags behind actual
        # deletions because compaction is asynchronous.  Per the task
        # spec ("如可稳定读取 count，确认恢复为运行前值"), the row count
        # check is conditional on stability, and Milvus row count is
        # NOT stable immediately after deletes.  We log the discrepancy
        # for the report but do NOT fail the cleanup verification.
        if (
            preflight.collection_row_count is not None
            and cleanup_result.collection_row_count_after is not None
            and cleanup_result.collection_row_count_after
            != preflight.collection_row_count
        ):
            print(
                f"  WARNING: collection row count did not restore "
                f"({preflight.collection_row_count} -> "
                f"{cleanup_result.collection_row_count_after}).  "
                f"This is expected Milvus behavior — compaction is "
                f"async and stats lag behind deletes.  The authoritative "
                f"check (post_delete_query_count == 0) confirmed all "
                f"fixture vectors are deleted."
            )
        # Cleanup report print — always runs for the delivery report.
        print("\n=== R1 Cleanup Result ===")
        print(f"  deleted_count: {cleanup_result.deleted_count}")
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
            f"  protected_collections_unchanged: "
            f"{cleanup_result.protected_collections_unchanged}"
        )
        if cleanup_result.collection_row_count_after is not None:
            print(
                f"  collection_row_count_after: "
                f"{cleanup_result.collection_row_count_after} "
                f"(preflight={preflight.collection_row_count})"
            )
        print("=== End R1 Cleanup Result ===")

    # -----------------------------------------------------------------
    # Success-path report print — only runs if try body succeeded
    # AND finally cleanup assertions passed.
    # -----------------------------------------------------------------
    print("\n=== R1 Real-Chain Acceptance Call Counts ===")
    for key, value in counts.as_report().items():
        print(f"  {key}: {value}")
    print(f"  fixture stable_document_id: {ids.stable_document_id}")
    print(f"  zilliz_chunk_count_at_d4: {zilliz_chunk_count}")
    if cleanup_result is not None:
        print(
            f"  cleanup_deleted_count: {cleanup_result.deleted_count}"
        )
        print(
            f"  cleanup_post_delete_query_count: "
            f"{cleanup_result.post_delete_query_count}"
        )
    print("=== End R1 Call Counts ===")


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
        env["READER_ARTICLE_RAG_ZILLIZ_COLLECTION"] = (
            "article_rag_index_smoke_abcdef12"
        )
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False, (
            "Gate let the smoke through with a smoke-prefix collection. "
            "R1 requires article_rag_chunks — the smoke prefix design "
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
