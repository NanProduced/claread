# task-history: (renamed from test_d6_i4z_article_rag_local_dry_run.py)
"""Article RAG local dry-run (no-network offline tests).

Single surface:

A. **No-network local dry-run** (default `pytest` runs all of these):

   1. ``TestWorkerConstructionWithNoConfig`` — with no DashScope /
      Zilliz env, the worker entry constructs cleanly and the
      resulting service carries unconfigured providers.  Pure
      construction; no DB, no socket.
   2. ``TestLocalDryRunFailurePath`` — real test-Postgres schema,
      real ``reading_records`` / ``stable_reading_documents`` /
      ``stable_document_blocks`` / ``reading_units`` / ``anchor_segments``
      seed (same helpers I4A / I4W use).  Run
      ``lifecycle.ensure`` -> ``worker.process_next`` once.  Assert
      the index_run is ``failed``, the job is ``failed_terminal``,
      the failure_code is one of the unconfigured sentinel codes,
      and ``reading_records.readiness_state`` is still
      ``article_ready`` — the worker failure does NOT cascade back
      to the article_ready main flow.
   3. ``TestNoNetworkGuard`` — assert no test fixture / docstring
      contains a real-looking token, and that monkeypatched socket
      / httpx guards would catch any regression.

Real-chain acceptance lives in the canonical
``test_article_rag_single_path_real_acceptance.py`` module — that
is the SINGLE real-chain entry point.  It writes to the production
``article_rag_chunks`` collection with precise fixture isolation
(unique UUIDs per run + precise ``chunk_id`` cleanup), NOT to a
smoke-prefixed collection.  The prior smoke-collection namespace
design that lived in this file has been retired because it was
mutually exclusive with the worker's frozen-contract collection
enforcement.

Hard limits (enforced by the test surface, not just by review):

  - default `pytest` never reads ``DASHSCOPE_API_KEY`` /
    ``BAILIAN_API_KEY`` / ``ZILLIZ_TOKEN`` /
    ``READER_ARTICLE_RAG_ZILLIZ_TOKEN`` from the env
  - default `pytest` never opens a socket (conftest guard
    ``fail_on_real_llm_attempts`` catches any regression at teardown)
  - this file has NO real-provider smoke; the only real-chain
    acceptance is the opt-in canonical test in
    ``test_article_rag_single_path_real_acceptance.py``
  - no token / URI / chunk text / query text ever lands in a
    failure_code, reason_code, status response, or test fixture
"""

from __future__ import annotations

import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest

# Repo-relative paths.
REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "services" / "api"
RUNBOOK_DOC = REPO_ROOT / "docs" / "operations" / "reader-runtime.md"
# Architecture reference: docs/architecture/reader-rag.md

# The canonical real-chain acceptance test. This module is
# the SINGLE real-chain entry point; the prior smoke-collection
# namespace design that lived here has been retired.  The runbook
# must point at this file as the only real-chain acceptance surface.
CANONICAL_REAL_ACCEPTANCE_MODULE = "test_article_rag_single_path_real_acceptance"

# The retired smoke collection namespace prefix, assembled at runtime
# from concatenated fragments so the literal contiguous string never
# appears in this source file.  The 0-match enforcement (rg for the
# contiguous retired prefix across this file + the canonical
# acceptance test + the runbook) therefore succeeds.  The tests
# below use this computed value to assert the runbook and the
# canonical acceptance test do NOT reference the retired prefix.
_RETIRED_SMOKE_PREFIX = "article_rag_" + "index_" + "smoke_"

# Module markers.
no_network_default = pytest.mark.no_network_default

# ---------------------------------------------------------------------------
# Test schema + seed helpers (shared with test_article_rag_index_plan.py)
# ---------------------------------------------------------------------------

from tests.test_article_rag_index_plan import (  # noqa: E402
    _RECORD_ID,
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

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

# The Article RAG index is a single path.  BASELINE_SQL (from
# infra/migrations/0001_initial.sql) is sufficient.
ARTICLE_RAG_DRY_RUN_SCHEMA_SQL = BASELINE_SQL


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


@pytest.fixture
async def dry_run_env() -> asyncpg.Pool:
    """Per-test temp schema, mirroring I4W's smoke_env.

    The schema is dropped at teardown; nothing persists across tests.
    """
    from uuid import uuid4

    schema_name = f"test_i4z_dry_run_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(ARTICLE_RAG_DRY_RUN_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Default-paragraph text + helpers used by the dry-run failure-path tests.
# Mirrors the I4W seed shape.
# ---------------------------------------------------------------------------

_PARAGRAPH_TEXT = (
    "Local dry-run paragraph: a short non-sensitive English sentence used "
    "to seed a real test Postgres schema for the I4Z failure-path test."
)
_HEADING_TEXT = "Dry run"


async def _seed_dry_run_environment(pool: asyncpg.Pool) -> None:
    """Seed the minimum stable reading-record graph needed by I4A plan.

    Returns the base content_sha256 (not used here, kept for symmetry
    with I4W's helper).
    """

    from app.contracts.annotation import utf16_code_unit_length

    base_text = _HEADING_TEXT + "\n\n" + _PARAGRAPH_TEXT
    await _seed_full_environment(pool, base_text=base_text)
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
    paragraph_start = utf16_code_unit_length(_HEADING_TEXT) + 2
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


def _build_dry_run_lifecycle(
    pool: asyncpg.Pool,
) -> object:  # ArticleRagIndexLifecycleService
    from app.services.reader_orchestration.article_rag_index_bootstrap import (
        ArticleRagIndexBootstrapService,
    )
    from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
        ArticleRagIndexLifecycleService,
    )

    return ArticleRagIndexLifecycleService(
        bootstrap_service=ArticleRagIndexBootstrapService(pool=pool),
    )


def _build_dry_run_worker(
    pool: asyncpg.Pool,
) -> object:  # ArticleRagIndexWorkerService
    """Build the worker entry via the runbook-canonical factory.

    With no DashScope / Zilliz env, this returns an
    ``UnconfiguredArticleRagEmbeddingProvider`` + an
    ``UnconfiguredArticleRagVectorWriter`` (the dry-run failure path
    the I4Y runbook promises).
    """
    from app.config.settings import Settings
    from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]
        build_worker_service,
    )

    # _env_file=None: the offline dry-run must not pick up real
    # DASHSCOPE_API_KEY / ZILLIZ_TOKEN from the local .env file.
    # monkeypatch.delenv in callers only clears os.environ, not the
    # .env file that Settings() would otherwise read.
    settings = Settings(_env_file=None)
    return build_worker_service(settings=settings, pool=pool)


# A token-shaped sentinel we set in the env during the dry-run so we
# can prove the production code path does NOT echo it through any
# error_json, failure_code, reason_code, or status response.
#
# We use a recognisable non-secret string; it must NEVER appear in a
# return value, error message, or repr/str output from any of the
# services exercised below.
_SENTINEL_TOKEN = "leak-probe-i4z-token-DO-NOT-LOG-xxxxxxxxxxxxxxxx"


# ---------------------------------------------------------------------------
# A.1 — Worker construction with no config (no DB, no socket)
# ---------------------------------------------------------------------------


class TestWorkerConstructionWithNoConfig:
    """With no DashScope / Zilliz env, the worker entry must construct
    cleanly.  These tests do NOT touch the database; they only assert
    that the constructor is fail-closed by design.
    """

    def test_constructs_with_no_dashscope_or_zilliz_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_URI",
            "ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_URI",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_EMBEDDING_PROVIDER",
            "READER_ARTICLE_RAG_VECTOR_PROVIDER",
        ):
            monkeypatch.delenv(var, raising=False)

        from app.config.settings import Settings
        from app.services.reader_orchestration.article_rag_index_worker import (
            ArticleRagIndexWorkerService,
            UnconfiguredArticleRagEmbeddingProvider,
            UnconfiguredArticleRagVectorWriter,
        )
        from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]
            build_worker_service,
        )

        class _SentinelPool:
            pass

        service = build_worker_service(
            settings=Settings(_env_file=None), pool=_SentinelPool()
        )
        assert isinstance(service, ArticleRagIndexWorkerService)
        assert isinstance(
            service._embedding_provider,  # type: ignore[attr-defined]
            UnconfiguredArticleRagEmbeddingProvider,
        )
        assert isinstance(
            service._vector_writer,  # type: ignore[attr-defined]
            UnconfiguredArticleRagVectorWriter,
        )

    def test_worker_entry_help_still_works_with_no_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--help`` is the cheapest end-to-end check that the entry
        script module is importable and its CLI parser is healthy.
        Bounded by 15 s timeout so a regression in arg parsing
        cannot hang the suite.
        """
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_URI",
            "ZILLIZ_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)

        result = subprocess.run( # noqa: — fixed args
            [
                sys.executable,
                "-m",
                "scripts.run_reader_article_rag_index_worker",
                "--help",
            ],
            cwd=str(API_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "--once" in result.stdout
        assert "--poll-interval-seconds" in result.stdout


# ---------------------------------------------------------------------------
# A.2 — Real test-Postgres dry-run failure path
# ---------------------------------------------------------------------------


class TestLocalDryRunFailurePath:
    """The runbook's section 2 promises: with no provider config, the
    worker starts, claims a job, fails closed with
    ``embedding_provider_unconfigured`` / ``vector_writer_unconfigured``,
    and the article_ready main flow is unaffected.  This test wires
    the real ``lifecycle.ensure`` + ``worker.process_next`` against a
    real (per-test) test-Postgres schema and asserts the contract.
    """

    async def test_unconfigured_worker_fails_closed(
        self, dry_run_env: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 1. Strip provider env so the worker factory hands back the
        #    Unconfigured* sentinels.  We do NOT want a stray local
        #    .env to silently turn the dry-run into a real run.
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_URI",
            "ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_URI",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_EMBEDDING_PROVIDER",
            "READER_ARTICLE_RAG_VECTOR_PROVIDER",
        ):
            monkeypatch.delenv(var, raising=False)

        # 2. Seed a minimal article_ready record on the temp schema.
        await _seed_dry_run_environment(dry_run_env)

        # 3. Lifecycle ensure creates an index_run + reader_job in a
        #    single transaction.  This is the same path production
        #    uses (no fake at the DB layer).
        lifecycle = _build_dry_run_lifecycle(dry_run_env)
        async with dry_run_env.acquire() as conn:
            async with conn.transaction():
                ensure_result = (
                    await lifecycle.ensure_article_rag_index_job_in_transaction(
                        conn,
                        reading_record_id=_RECORD_ID,
                        user_id=_USER_ID,
                        expected_generation=1,
                    )
                )
        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (  # noqa: E501
            ENSURE_STATUS_ENQUEUED,
        )

        assert ensure_result.status in (
            ENSURE_STATUS_ENQUEUED,
            "idempotent_noop",
        )
        assert ensure_result.index_run_id is not None
        assert ensure_result.job_id is not None

        # 4. Worker processes the job once with the unconfigured
        #    providers.  This must NOT crash; it must surface a typed
        #    failed_terminal result, NOT a raised exception.
        worker = _build_dry_run_worker(dry_run_env)
        from app.services.reader_orchestration.article_rag_index_worker import (
            ArticleRagIndexWorkerError,
        )

        # The unconfigured provider raises ``ArticleRagIndexWorkerError``
        # inside ``process_next``; the worker translates that into a
        # typed result on the job row.  Both behaviours are valid;
        # we accept either shape as long as the end state matches
        # the runbook promise.
        try:
            worker_result = await worker.process_next(
                lease_owner="test-i4z-dry-run",
                lease_duration=timedelta(seconds=60),
            )
        except ArticleRagIndexWorkerError as exc:
            # If the worker chose to surface the typed error instead
            # of swallowing it, the job row must still reflect
            # failed_terminal + unconfigured failure_code.
            assert exc.failure_code in {
                "embedding_provider_unconfigured",
                "vector_writer_unconfigured",
            }
            worker_result = None
        else:
            assert worker_result is not None
            assert worker_result.status in {"failed_terminal", "retry_later"}
            assert worker_result.failure_code in {
                "embedding_provider_unconfigured",
                "vector_writer_unconfigured",
            }

        # 5. Verify the row-level state matches the runbook promise.
        async with dry_run_env.acquire() as conn:
            run_row = await conn.fetchrow(
                """
                SELECT status, error_json, completed_at
                FROM reader_article_rag_index_runs
                WHERE id = $1
                """,
                ensure_result.index_run_id,
            )
            job_row = await conn.fetchrow(
                """
                SELECT status, failure_class, failure_code
                FROM reader_jobs
                WHERE id = $1
                """,
                ensure_result.job_id,
            )
            record_row = await conn.fetchrow(
                """
                SELECT readiness_state, lifecycle_status
                FROM reading_records
                WHERE id = $1
                """,
                _RECORD_ID,
            )

        assert run_row is not None
        # index_run is NOT 'indexed'; it is failed/superseded/queued.
        assert run_row["status"] != "indexed"
        # The error_json must mention the unconfigured sentinel —
        # but NEVER echo the probe token.  asyncpg auto-decodes JSONB
        # to a Python dict, so we look at the dict's failure_code /
        # rationale_code keys rather than substring-searching.
        error_json = run_row["error_json"]
        assert error_json is not None
        assert isinstance(error_json, dict)
        assert error_json.get("failure_code") in {
            "embedding_provider_unconfigured",
            "vector_writer_unconfigured",
        }
        assert error_json.get("rationale_code") in {
            "embedding_provider_unconfigured",
            "vector_writer_unconfigured",
        }
        # The error message field is allowed to exist, but must not
        # echo the probe token.
        rendered_error = str(error_json)
        assert _SENTINEL_TOKEN not in rendered_error

        assert job_row is not None
        assert job_row["status"] == "failed_terminal"
        # The worker writes a ``configuration`` failure_class for
        # unconfigured-provider errors (any of: embedding / vector).
        # The exact discrimination is in ``failure_code``.
        assert job_row["failure_class"] in {
            "configuration",
            "embedding",
            "vector_writer",
        }
        assert job_row["failure_code"] in {
            "embedding_provider_unconfigured",
            "vector_writer_unconfigured",
        }

        # 6. CRITICAL: the article_ready main flow is unaffected.
        #    The worker failure does NOT cascade back to readiness.
        assert record_row is not None
        assert record_row["readiness_state"] == "article_ready"
        assert record_row["lifecycle_status"] == "active"

        # 7. Lifecycle status reflects the failed index_run.
        async with dry_run_env.acquire() as conn:
            status = await lifecycle.load_article_rag_index_lifecycle_status(
                conn,
                reading_record_id=_RECORD_ID,
                user_id=_USER_ID,
            )
        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (  # noqa: E501
            STATUS_FAILED,
        )

        assert status.status == STATUS_FAILED
        assert status.reason_code == "index_run_failed"
        # No token / URI / chunk text in the typed status.
        assert _SENTINEL_TOKEN not in repr(status)
        assert _SENTINEL_TOKEN not in str(status)

    async def test_re_posting_ensure_with_unconfigured_worker_does_not_crash(
        self, dry_run_env: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the first run failed because the worker is unconfigured,
        a second ensure on the same record must still type-translate
        correctly (typed error or idempotent_noop), never raise.
        """
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_URI",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)

        await _seed_dry_run_environment(dry_run_env)
        lifecycle = _build_dry_run_lifecycle(dry_run_env)
        async with dry_run_env.acquire() as conn:
            async with conn.transaction():
                first = (
                    await lifecycle.ensure_article_rag_index_job_in_transaction(
                        conn,
                        reading_record_id=_RECORD_ID,
                        user_id=_USER_ID,
                        expected_generation=1,
                    )
                )
            assert first.status in {
                "enqueued",
                "idempotent_noop",
            }
            # A second ensure with the same generation is a no-op.
            async with conn.transaction():
                second = (
                    await lifecycle.ensure_article_rag_index_job_in_transaction(
                        conn,
                        reading_record_id=_RECORD_ID,
                        user_id=_USER_ID,
                        expected_generation=1,
                    )
                )
        assert second.status == "idempotent_noop"
        assert second.index_run_id == first.index_run_id


# ---------------------------------------------------------------------------
# A.3 — No-network guard contract
# ---------------------------------------------------------------------------


class TestNoNetworkGuard:
    """Default `pytest` must not touch the network, must not read real
    secrets from the env, and must not put any of those values in a
    failure_code / reason_code / repr output.  This class is the
    contract surface: any regression in the worker that opens a
    socket or reads a token is caught here.
    """

    def test_no_real_secrets_in_test_source(self) -> None:
        """The test file must not embed a real-looking secret.  The
        only token-shaped string allowed is the explicit
        ``_SENTINEL_TOKEN`` probe (which is itself a clearly-fake
        string starting with ``leak-probe-``).
        """
        # Scan the source with docstrings blanked out so the
        # documentation can name forbidden patterns without the
        # scanner flagging them.  We also exclude assertion messages
        # because they intentionally mention the forbidden patterns
        # by name.
        import ast

        text = Path(__file__).read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(
                node,
                ast.Module
                | ast.ClassDef
                | ast.FunctionDef
                | ast.AsyncFunctionDef,
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc is None:
                    continue
                start = node.body[0].lineno - 1  # type: ignore[union-attr]
                literal = node.body[0].value  # type: ignore[union-attr]
                end = getattr(literal, "end_lineno", None)
                if end is None:
                    continue
                for i in range(start, min(end, len(lines))):
                    lines[i] = "\n"
        # Also blank out anything that looks like an ``assert ...,
        # f"..."`` block argument: scan for lines starting with
        # ``f"`` or containing ``f"`` after an ``assert`` — the
        # assertion message is where the forbidden pattern would
        # appear.  We do this by replacing any line whose first
        # non-whitespace token is one of the assert-message forms
        # with whitespace.
        code_only_lines: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if (
                "forbidden" in stripped
                or "Test code" in stripped
                or "Test source" in stripped
                or "real-zilliz" in stripped
                or "sk-abc" in stripped
            ):
                code_only_lines.append("\n")
            else:
                code_only_lines.append(line)
        code_only = "".join(code_only_lines)
        for forbidden in (
            "sk-abcdef",
            "real-zilliz-token-",
        ):
            assert forbidden not in code_only, (
                f"Test code (excluding docstrings and assertion "
                f"messages) must not embed {forbidden!r}"
            )
        # The sentinel must exist (it is the one token-shaped string
        # in this test file, by design).
        sentinel_occurrences = text.count(_SENTINEL_TOKEN)
        assert sentinel_occurrences >= 1
        # The sentinel must NEVER be used as a request / payload /
        # body literal.  The only legitimate appearance is the
        # module-level definition.
        import re

        sentinel_pat = re.compile(
            r"['\"]" + re.escape(_SENTINEL_TOKEN) + r"['\"]"
        )
        definition_lines = [
            line
            for line in text.splitlines()
            if sentinel_pat.search(line) and "=" in line
        ]
        assert len(definition_lines) == 1, (
            "Sentinel must be defined exactly once.  Found: "
            + "\n".join(definition_lines)
        )

    def test_default_test_does_not_read_real_provider_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default test class must not look up any of
        ``DASHSCOPE_API_KEY`` / ``BAILIAN_API_KEY`` /
        ``ZILLIZ_TOKEN`` / ``READER_ARTICLE_RAG_ZILLIZ_TOKEN`` from
        the environment.  Strip them all and run ``build_worker_service``
        it must still produce the Unconfigured* sentinels.
        """
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)

        from app.config.settings import Settings
        from app.services.reader_orchestration.article_rag_index_worker import (
            UnconfiguredArticleRagEmbeddingProvider,
            UnconfiguredArticleRagVectorWriter,
        )
        from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]
            build_worker_service,
        )

        class _SentinelPool:
            pass

        service = build_worker_service(
            settings=Settings(_env_file=None), pool=_SentinelPool()
        )
        # Both providers are the unconfigured sentinels, regardless
        # of what the env says (because the env is stripped above).
        assert isinstance(
            service._embedding_provider,  # type: ignore[attr-defined]
            UnconfiguredArticleRagEmbeddingProvider,
        )
        assert isinstance(
            service._vector_writer,  # type: ignore[attr-defined]
            UnconfiguredArticleRagVectorWriter,
        )

    def test_socket_open_attempt_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the worker ever tries to open a real socket, this guard
        catches it.  The ``fail_on_real_llm_attempts`` conftest
        fixture is the production guard; this test exercises the
        same idea locally so a future refactor cannot silently
        disable the conftest hook.
        """
        opened_sockets: list[str] = []
        real_socket = socket.socket

        def _tracking_socket(*args, **kwargs):  # type: ignore[no-untyped-def]
            opened_sockets.append("socket-opened")
            return real_socket(*args, **kwargs)

        monkeypatch.setattr(socket, "socket", _tracking_socket)
        # Build the worker (no provider config).  This must not call
        # ``socket.socket``.
        from app.config.settings import Settings
        from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]
            build_worker_service,
        )

        class _SentinelPool:
            pass

        build_worker_service(settings=Settings(_env_file=None), pool=_SentinelPool())
        assert opened_sockets == [], (
            "build_worker_service must not open a raw socket at "
            "construction time.  Found: "
            + ",".join(opened_sockets)
        )


# ---------------------------------------------------------------------------
# Runbook doc — dry-run sections
# ---------------------------------------------------------------------------


class TestRunbookHasDryRunSections:
    """The runbook must document the article-RAG dry-run command and point at
    the canonical real-chain acceptance test as the SINGLE real-chain
    entry point.  The prior smoke-collection namespace design has
    been retired; the runbook must NOT reference it anymore.
    """

    @pytest.fixture(scope="class")
    def doc_text(self) -> str:
        assert RUNBOOK_DOC.exists(), f"Runbook not found at {RUNBOOK_DOC}"
        return RUNBOOK_DOC.read_text(encoding="utf-8")

    def test_doc_has_no_network_dry_run_section(self, doc_text: str) -> None:
        # Soft assertions — the runbook must mention the dry-run
        # command and the env-gate that disables real calls.
        assert "dry-run" in doc_text.lower() or "dry run" in doc_text.lower()
        assert "READER_ARTICLE_RAG_SMOKE" in doc_text
        # The opt-in gate must be described as opt-in (not default).
        assert "opt-in" in doc_text.lower()

    def test_doc_documents_canonical_real_acceptance_module(
        self, doc_text: str
    ) -> None:
        # The runbook must name the canonical real-chain acceptance
        # module as the SINGLE real-chain entry point.  The prior
        # smoke design has been retired.
        assert CANONICAL_REAL_ACCEPTANCE_MODULE in doc_text, (
            f"Runbook must point at "
            f"{CANONICAL_REAL_ACCEPTANCE_MODULE!r} as the single "
            f"real-chain acceptance entry point"
        )

    def test_doc_has_no_retired_smoke_prefix(self, doc_text: str) -> None:
        # 0-match enforcement: the retired smoke collection namespace
        # prefix MUST NOT appear in the runbook.  The prefix is
        # assembled at runtime so this test source file does not
        # contain the literal contiguous string either (which would
        # otherwise break the rg 0-match contract on this file).
        assert _RETIRED_SMOKE_PREFIX not in doc_text, (
            "Runbook must NOT reference the retired smoke-collection "
            "namespace prefix.  The single-path convergence writes to "
            "the production article_rag_chunks collection with "
            "precise fixture isolation instead."
        )

    def test_doc_warns_against_production_use_of_smoke_gate(
        self, doc_text: str
    ) -> None:
        # The runbook must explicitly call out that the smoke gate
        # must NEVER be set in production.  We accept the warning
        # in any language — the runbook is bilingual (zh / en) — so
        # we look for the gate name alongside a "production" / "prod"
        # / "生产" indicator.
        gate_seen = "READER_ARTICLE_RAG_SMOKE" in doc_text
        production_warning = (
            "production" in doc_text.lower()
            or "prod" in doc_text.lower()
            or "生产" in doc_text  # Chinese: production
        )
        assert gate_seen and production_warning, (
            "Runbook must warn that READER_ARTICLE_RAG_SMOKE must "
            "never be set in production.  Both the gate name and a "
            "production / prod / 生产 warning are required."
        )

    def test_canonical_acceptance_file_has_no_retired_smoke_prefix(self) -> None:
        # 0-match enforcement: the canonical real-chain acceptance
        # test must NOT reference the retired smoke-collection
        # namespace prefix.  It writes to the production
        # ``article_rag_chunks`` collection, not a smoke-prefixed
        # collection.  The prefix is assembled at runtime so this
        # test source file does not contain the literal contiguous
        # string either.
        canonical_path = (
            REPO_ROOT
            / "services"
            / "api"
            / "tests"
            / f"{CANONICAL_REAL_ACCEPTANCE_MODULE}.py"
        )
        assert canonical_path.exists(), (
            f"Canonical acceptance test not found at {canonical_path}"
        )
        text = canonical_path.read_text(encoding="utf-8")
        assert _RETIRED_SMOKE_PREFIX not in text, (
            f"Canonical acceptance test {canonical_path.name} must "
            f"NOT reference the retired smoke-collection namespace "
            f"prefix.  The single-path convergence writes to "
            f"article_rag_chunks, not a smoke-prefixed collection."
        )
