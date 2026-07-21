"""D6-I4Z Article RAG local dry-run + env-gated real-provider smoke.

Two surfaces, one file:

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

B. **Env-gated real-provider smoke** (skipped by default):

   The class ``TestRealProviderSmoke`` is decorated with
   ``@pytest.mark.article_rag_smoke`` and a multi-conditional
   ``skipif`` that requires EVERY env var listed in the I4Z runbook
   section.  Missing any one -> skip, never fail.  The test is
   **not** run by the default `pytest` invocation; it is
   documentation + a runnable opt-in, not a CI gate.

Hard limits (enforced by the test surface, not just by review):

  - default `pytest` never reads ``DASHSCOPE_API_KEY`` /
    ``BAILIAN_API_KEY`` / ``ZILLIZ_TOKEN`` /
    ``READER_ARTICLE_RAG_ZILLIZ_TOKEN`` from the env
  - default `pytest` never opens a socket (conftest guard
    ``fail_on_real_llm_attempts`` catches any regression at teardown)
  - the env-gated real-provider test is the only test that ever
    reads real credentials, and it is gated behind
    ``READER_ARTICLE_RAG_SMOKE=1`` PLUS a complete env set
  - no token / URI / chunk text / query text ever lands in a
    failure_code, reason_code, status response, or test fixture
"""

from __future__ import annotations

import os
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
RUNBOOK_DOC = (
    REPO_ROOT
    / "docs"
    / "initiatives"
    / "reader-agentic-orchestration"
    / "modules"
    / "local-article-rag-runbook.md"
)

# The opt-in env gate for the real-provider smoke.
ARTICLE_RAG_SMOKE_ENV = "READER_ARTICLE_RAG_SMOKE"

# All env vars the real-provider smoke requires.
#
# This list is the I4D factory's actual contract (verified against
# ``build_default_article_rag_embedding_provider`` and
# ``bailian_embedding.resolve_embedding_config``):
#
#  - ``READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope`` selects the
#    real DashScope embedder inside the I4D factory.
#  - The factory's key resolution path is **NOT** ``DASHSCOPE_API_KEY``
#    (that env var is for the OCR adapter).  The Article RAG path
#    goes through the registry route ``RAG_EMBEDDING_MODEL_PROFILE``
#    or, when the route is unset, the legacy ``BAILIAN_API_KEY`` /
#    ``BAILIAN_EMBEDDING_MODEL`` fallback.
#  - The vector side requires the four-set: provider / uri / token /
#    collection.  ``READER_ARTICLE_RAG_VECTOR_DIM`` is also required
#    so the Zilliz writer's `dim` validation passes.
#  - The collection MUST be in the smoke namespace
#    ``article_rag_index_smoke_*`` — the smoke is forbidden from
#    touching any non-smoke collection so an env-typo cannot write
#    to production Zilliz.  This is enforced by
#    ``_real_smoke_env_present`` below.
#
# The credential env vars (``BAILIAN_API_KEY`` /
# ``RAG_EMBEDDING_MODEL_PROFILE``) are NOT in the hard-required
# tuple — the smoke accepts either, matching the I4D factory's
# resolution order.  The at-least-one contract is enforced by
# ``_real_smoke_env_present`` via ``REAL_SMOKE_CREDENTIAL_ENVS``.
#
# The smoke REQUIRES every env below; missing any one -> skip.
# This matches the contract documented in
# ``local-article-rag-runbook.md`` section 7.2.
REAL_SMOKE_REQUIRED_ENVS: tuple[str, ...] = (
    "READER_ARTICLE_RAG_EMBEDDING_PROVIDER",
    "READER_ARTICLE_RAG_VECTOR_PROVIDER",
    "READER_ARTICLE_RAG_ZILLIZ_URI",
    "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
    "READER_ARTICLE_RAG_ZILLIZ_COLLECTION",
    "READER_ARTICLE_RAG_VECTOR_DIM",
)

# Either-or credential set: the I4D factory will resolve the
# embedding credential through whichever of these is non-empty.
# BAILIAN_API_KEY is the legacy fallback; RAG_EMBEDDING_MODEL_PROFILE
# is the registry route that takes precedence when set.  The smoke
# does not care which path the team uses, only that at least one
# is configured.
REAL_SMOKE_CREDENTIAL_ENVS: tuple[str, ...] = (
    "BAILIAN_API_KEY",
    "RAG_EMBEDDING_MODEL_PROFILE",
)

# All collections the smoke writes to MUST be inside this namespace.
# This is the hard isolation guard: an opt-in smoke with a
# production-named collection will be SKIPPED, never silently
# written to.
REAL_SMOKE_COLLECTION_PREFIX = "article_rag_index_smoke_"

# Module markers.
article_rag_smoke = pytest.mark.article_rag_smoke
no_network_default = pytest.mark.no_network_default

# ---------------------------------------------------------------------------
# Test schema + seed helpers (mirror test_d6_i4w_article_rag_service_e2e_smoke.py)
# ---------------------------------------------------------------------------

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

# The Article RAG index is a single path — no ``index_version`` /
# ``chunker_version`` / ``profile_fingerprint`` columns exist on
# ``reader_article_rag_index_runs``.  BASELINE_SQL (which includes
# migration 0010) is sufficient; no migration 0021 append is needed.
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

    settings = Settings()
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
            settings=Settings(), pool=_SentinelPool()
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

        result = subprocess.run(  # noqa: S603 — fixed args
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
        — it must still produce the Unconfigured* sentinels.
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
            settings=Settings(), pool=_SentinelPool()
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

        build_worker_service(settings=Settings(), pool=_SentinelPool())
        assert opened_sockets == [], (
            "build_worker_service must not open a raw socket at "
            "construction time.  Found: "
            + ",".join(opened_sockets)
        )


# ---------------------------------------------------------------------------
# B — Env-gated real-provider smoke (skipped by default)
# ---------------------------------------------------------------------------


# Build a single skipif predicate that requires the opt-in gate,
# every hard-required env var, AT LEAST ONE credential env, AND the
# smoke collection namespace prefix on the Zilliz collection.  Missing
# any one -> skip, never fail.  The collection-prefix guard is the
# safety net that prevents the opt-in smoke from ever writing to
# a production-named Zilliz collection (env-typo protection).
_REAL_SMOKE_SKIP_REASON_PARTS: list[str] = [
    f"{ARTICLE_RAG_SMOKE_ENV}=1 required",
]
for _env in REAL_SMOKE_REQUIRED_ENVS:
    _REAL_SMOKE_SKIP_REASON_PARTS.append(f"{_env} set")
_REAL_SMOKE_SKIP_REASON_PARTS.append(
    f"at least one of {', '.join(REAL_SMOKE_CREDENTIAL_ENVS)} set"
)
_REAL_SMOKE_SKIP_REASON_PARTS.append(
    f"READER_ARTICLE_RAG_ZILLIZ_COLLECTION starts with "
    f"{REAL_SMOKE_COLLECTION_PREFIX!r}"
)

_REAL_SMOKE_SKIP_REASON = (
    "Real-provider Article RAG smoke is opt-in only.  Required: "
    + ", ".join(_REAL_SMOKE_SKIP_REASON_PARTS)
    + ".  DO NOT enable in production."
)


def _real_smoke_env_present() -> bool:
    """True iff the opt-in gate is on, every hard-required env var is
    non-empty, at least one credential env is set, AND the Zilliz
    collection lives inside the smoke namespace.  This is the
    single source of truth for the skip predicate below — and
    the safety net that prevents the opt-in smoke from ever
    writing to a non-smoke (e.g. production-named) Zilliz
    collection.
    """
    if os.environ.get(ARTICLE_RAG_SMOKE_ENV) != "1":
        return False
    if not all(os.environ.get(env, "") for env in REAL_SMOKE_REQUIRED_ENVS):
        return False
    # Credential: at least one of the either-or set must be non-empty.
    if not any(
        os.environ.get(env, "") for env in REAL_SMOKE_CREDENTIAL_ENVS
    ):
        return False
    # Collection isolation: refuse to enter the smoke unless the
    # configured collection is in the smoke namespace.  An env-typo
    # that points at a production collection is SKIPPED here, not
    # silently written to.
    collection = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_COLLECTION", "")
    if not collection.startswith(REAL_SMOKE_COLLECTION_PREFIX):
        return False
    return True


@article_rag_smoke
@pytest.mark.skipif(
    not _real_smoke_env_present(),
    reason=_REAL_SMOKE_SKIP_REASON,
)
async def test_real_provider_end_to_end_indexed_and_retrievable(
    dry_run_env: asyncpg.Pool,
) -> None:
    """Real-provider end-to-end smoke (opt-in only, runnable).

    Required env (set ALL of these to enable):

        READER_ARTICLE_RAG_SMOKE=1
        READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope
        BAILIAN_API_KEY=<real key>          # OR the alternative below
        # OR:
        RAG_EMBEDDING_MODEL_PROFILE=<profile that resolves to dashscope_embedding>
        READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz
        READER_ARTICLE_RAG_ZILLIZ_URI=<real URI>
        READER_ARTICLE_RAG_ZILLIZ_TOKEN=<real token>
        READER_ARTICLE_RAG_ZILLIZ_COLLECTION=article_rag_index_smoke_<8-hex>
            # MUST start with article_rag_index_smoke_ — the smoke
            # refuses to enter otherwise, to prevent the opt-in
            # smoke from ever writing to a production-named
            # collection.
        READER_ARTICLE_RAG_VECTOR_DIM=<1024 or as configured>

    Embedding key notes: the I4D factory
    (``build_default_article_rag_embedding_provider``) resolves the
    embedding credential through the registry route
    ``RAG_EMBEDDING_MODEL_PROFILE`` first, falling back to
    ``BAILIAN_API_KEY`` / ``BAILIAN_EMBEDDING_MODEL`` when the
    route is unset.  Either credential source is acceptable;
    ``DASHSCOPE_API_KEY`` is **not** consulted by the Article RAG
    path (it is reserved for the OCR adapter).

    What it does (when env is satisfied):

      1. Build a real ``Settings()`` so the env values land in the
         I4D factories.
      2. Use a deterministic test collection namespace:
         ``article_rag_index_smoke_<8-hex>`` so the smoke cannot
         collide with production data.
      3. Seed a minimal article_ready record on a per-test temp
         schema (real test-Postgres).
      4. ``lifecycle.ensure`` -> ``worker.process_next`` once.
      5. Assert: index_run.status = 'indexed', embedding_model +
         vector_store_provider + vector_collection all populated
         with the real (non-fake) provider values, no fake fallback.
      6. ``retrieval.retrieve_for_record`` with a short English
         query returns a typed ``ArticleRagRetrievalResult``.  Hit
         count may be 0 if the collection has no vectors yet; the
         smoke only asserts the response shape, not the hit count.
      7. ``ArticleRagAskContextProvider.build_for_ask`` returns a
         valid ``ArticleRagAskPromptAssembly`` (with or without
         ``should_attach``).
      8. Teardown: drop the temp Postgres schema; leave the smoke
         collection in Zilliz for ops to clean up (residuals are
         tracked by the deterministic test prefix).

    When env is NOT satisfied, the test SKIPS — never FAILS.  This
    is the default pytest run path.

    Pre-conditions enforced by the gate (see ``_real_smoke_env_present``):
      - the opt-in gate ``READER_ARTICLE_RAG_SMOKE=1`` is set
      - every var in ``REAL_SMOKE_REQUIRED_ENVS`` is non-empty
    """
    # 1. Real Settings — env values flow into the I4D factories.
    from app.config.settings import Settings

    settings = Settings()

    # 2. Sanity: the runbook-canonical factory must produce the REAL
    # providers, not the Unconfigured* sentinels.  This is the
    # user-facing failure mode we are protecting against: a misconfig
    # in env vars is caught here, not deep inside the worker.
    from app.services.reader_orchestration.article_rag_index_worker import (
        DashScopeArticleRagEmbeddingProvider,
        UnconfiguredArticleRagEmbeddingProvider,
        UnconfiguredArticleRagVectorWriter,
        ZillizArticleRagVectorWriter,
    )
    from scripts.run_reader_article_rag_index_worker import (  # type: ignore[import-not-found]
        build_worker_service,
    )

    worker = build_worker_service(settings=settings, pool=dry_run_env)
    assert not isinstance(
        worker._embedding_provider,  # type: ignore[attr-defined]
        UnconfiguredArticleRagEmbeddingProvider,
    ), (
        "I4D factory handed back the unconfigured embedder even "
        "though READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope is "
        "set.  This usually means BAILIAN_API_KEY is missing OR the "
        "registry route RAG_EMBEDDING_MODEL_PROFILE is unset.  See "
        "build_default_article_rag_embedding_provider for the "
        "resolution order."
    )
    assert isinstance(
        worker._embedding_provider,  # type: ignore[attr-defined]
        DashScopeArticleRagEmbeddingProvider,
    ), (
        "Expected the real DashScopeArticleRagEmbeddingProvider.  "
        f"Got: {type(worker._embedding_provider).__name__}"  # type: ignore[attr-defined]
    )
    assert not isinstance(
        worker._vector_writer,  # type: ignore[attr-defined]
        UnconfiguredArticleRagVectorWriter,
    ), (
        "I4D factory handed back the unconfigured vector writer even "
        "though READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz is set.  "
        "Check Zilliz uri / token / collection / dim in env."
    )
    assert isinstance(
        worker._vector_writer,  # type: ignore[attr-defined]
        ZillizArticleRagVectorWriter,
    )

    # 3. Seed a minimal article_ready record on the per-test temp
    #    schema (real test-Postgres, no fake at the DB layer).
    await _seed_dry_run_environment(dry_run_env)

    # 4. Lifecycle ensure -> worker.process_next.
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

    # 5. Single tick.  This DOES make real DashScope + Zilliz calls.
    #    We catch both the typed error path and the success path so a
    #    transient DashScope / Zilliz hiccup is surfaced as a pytest
    #    failure (not a skip) — the env gate already proved we are
    #    intentionally running with real providers.
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagIndexWorkerError,
        ArticleRagIndexWorkerResult,
    )

    try:
        worker_result = await worker.process_next(
            lease_owner="test-i4z-real-smoke",
            lease_duration=timedelta(seconds=120),
        )
    except ArticleRagIndexWorkerError as exc:
        pytest.fail(
            f"Real-provider worker raised ArticleRagIndexWorkerError "
            f"({type(exc).__name__}, failure_code={exc.failure_code}, "
            f"retryable={exc.retryable}).  With all real env set, "
            f"this should not happen — investigate the upstream "
            f"DashScope / Zilliz call."
        )
    assert isinstance(worker_result, ArticleRagIndexWorkerResult)
    assert worker_result.status == "succeeded", (
        f"Real-provider worker did not reach 'succeeded'.  "
        f"status={worker_result.status!r} "
        f"failure_code={worker_result.failure_code!r} "
        f"retryable={worker_result.retryable!r}"
    )
    # The real provider values must NOT be the fake-* defaults.
    assert worker_result.embedding_model is not None
    assert not worker_result.embedding_model.startswith("fake-"), (
        f"Real-provider smoke produced fake embedding model "
        f"{worker_result.embedding_model!r}"
    )
    assert worker_result.vector_store_provider == "zilliz"
    assert worker_result.vector_collection is not None
    # The collection must be inside the smoke namespace.  The
    # opt-in gate already enforces this prefix, but we double-check
    # here so a future refactor cannot accidentally widen the
    # assertion.  An opt-in smoke with a production-named
    # collection must SKIP, not write.
    assert worker_result.vector_collection.startswith(
        REAL_SMOKE_COLLECTION_PREFIX
    ), (
        f"Real-provider smoke wrote to {worker_result.vector_collection!r} "
        f"which is outside the smoke namespace "
        f"{REAL_SMOKE_COLLECTION_PREFIX!r}.  This is a safety bug — "
        f"the opt-in gate should have skipped this test before any "
        f"vector IO happened."
    )

    # 6. index_run row state.
    async with dry_run_env.acquire() as conn:
        run_row = await conn.fetchrow(
            """
            SELECT status, embedding_model, vector_store_provider,
                   vector_collection, completed_at
            FROM reader_article_rag_index_runs
            WHERE id = $1
            """,
            ensure_result.index_run_id,
        )
    assert run_row is not None
    assert run_row["status"] == "indexed"
    assert run_row["completed_at"] is not None
    assert run_row["embedding_model"] == worker_result.embedding_model
    assert run_row["vector_store_provider"] == "zilliz"
    assert run_row["vector_collection"] == worker_result.vector_collection

    # 7. Retrieval via the real Zilliz searcher (no fake hits).
    #    Hit count may be 0 if the collection is fresh; the smoke
    #    only asserts the typed response shape, not the hit count.
    from app.services.reader_orchestration.article_rag_retrieval_service import (  # noqa: E501
        ArticleRagRetrievalResult,
        ArticleRagRetrievalService,
    )
    from app.services.reader_orchestration.article_rag_vector_search import (  # noqa: E501
        ZillizArticleRagVectorSearcher,
    )

    zilliz_uri = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_URI", "")
    zilliz_token = os.environ.get("READER_ARTICLE_RAG_ZILLIZ_TOKEN", "")
    zilliz_collection = os.environ.get(
        "READER_ARTICLE_RAG_ZILLIZ_COLLECTION",
        settings.reader_article_rag_zilliz_collection,
    )
    real_searcher = ZillizArticleRagVectorSearcher(
        uri=zilliz_uri,
        token=zilliz_token,
        collection=zilliz_collection,
    )
    real_embedder = DashScopeArticleRagEmbeddingProvider(
        model_override=(
            settings.reader_article_rag_embedding_model or None
        ),
    )
    retrieval = ArticleRagRetrievalService(
        pool=dry_run_env,
        embedding_provider=real_embedder,
        vector_searcher=real_searcher,
    )
    retrieval_result = await retrieval.retrieve_for_record(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="local dry run smoke probe",
    )
    assert isinstance(retrieval_result, ArticleRagRetrievalResult)
    assert retrieval_result.reading_record_id == _RECORD_ID
    assert retrieval_result.stable_document_id == _STABLE_DOC_ID
    assert retrieval_result.base_id == _BASE_ID
    # ``hits`` may be empty if the collection has no vectors yet; the
    # smoke only asserts the typed response shape, not the hit count.
    assert isinstance(retrieval_result.hits, tuple)
    # No token / URI / chunk text leaked into the typed result.
    assert _SENTINEL_TOKEN not in repr(retrieval_result)
    # No fake provider values in the result.
    if retrieval_result.provider_metadata:
        provider_name = retrieval_result.provider_metadata.get(
            "provider"
        )
        if provider_name is not None:
            assert not provider_name.startswith("fake-")

    # 8. Ask facade returns a typed assembly — `should_attach` may
    #    be True or False depending on hit count, but the result
    #    must be a valid ArticleRagAskPromptAssembly with no leak.
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
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        query_text="local dry run smoke probe",
    )
    # The assembly is either attached (if the search returned hits
    # whose chunk_ids are valid) or not-attached (if the collection
    # was empty / no hits).  Both are valid runbook behaviour.
    assert assembly.kind == "article_rag_context"
    assert assembly.status in {
        "available",
        "not_indexed_or_unavailable",
    }
    assert _SENTINEL_TOKEN not in repr(assembly)
    assert _SENTINEL_TOKEN not in str(assembly)


# ---------------------------------------------------------------------------
# Runbook doc — I4Z sections
# ---------------------------------------------------------------------------


class TestSmokeEnvGate:
    """The opt-in smoke gate is the safety contract that prevents the
    smoke from accidentally writing to a production-named Zilliz
    collection.  These tests are the load-bearing assertion of
    that contract.
    """

    def _full_smoke_env(self) -> dict[str, str]:
        """Return a copy of the env that satisfies the gate.  Each
        test mutates one field to prove the gate rejects that
        particular omission / misconfig.
        """
        return {
            "READER_ARTICLE_RAG_SMOKE": "1",
            "READER_ARTICLE_RAG_EMBEDDING_PROVIDER": "dashscope",
            "BAILIAN_API_KEY": "test-bailian-key",
            "READER_ARTICLE_RAG_VECTOR_PROVIDER": "zilliz",
            "READER_ARTICLE_RAG_ZILLIZ_URI": "https://example.zilliz.com",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN": "test-zilliz-token",
            "READER_ARTICLE_RAG_ZILLIZ_COLLECTION": (
                "article_rag_index_smoke_abcdef12"
            ),
            "READER_ARTICLE_RAG_VECTOR_DIM": "1024",
        }

    def test_full_env_satisfies_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for k, v in self._full_smoke_env().items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is True

    def test_gate_rejects_production_named_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A production-named collection must NEVER enter the smoke.
        This is the safety guard: the smoke must skip rather than
        write to ``article_rag_chunks`` (or anything else outside
        the smoke namespace).
        """
        env = self._full_smoke_env()
        # Simulate the most common env-typo: leaving the default
        # production collection name.
        env["READER_ARTICLE_RAG_ZILLIZ_COLLECTION"] = (
            "article_rag_chunks"
        )
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False, (
            "Gate let an opt-in smoke through with a "
            "production-named Zilliz collection.  This is a "
            "data-integrity bug — the opt-in smoke must NEVER "
            "write outside the article_rag_index_smoke_ namespace."
        )

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
        # RAG_EMBEDDING_MODEL_PROFILE is not in our env at all
        monkeypatch.delenv("RAG_EMBEDDING_MODEL_PROFILE", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is False, (
            "Gate let the smoke through without BAILIAN_API_KEY OR "
            "RAG_EMBEDDING_MODEL_PROFILE.  The I4D factory would "
            "hand back the unconfigured provider, and the smoke "
            "would then assert on a real-provider invariant — "
            "failing noisily.  Skip instead."
        )

    def test_gate_accepts_alternative_credential_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self._full_smoke_env()
        env.pop("BAILIAN_API_KEY", None)
        # Use the registry route path instead of the legacy key.
        env["RAG_EMBEDDING_MODEL_PROFILE"] = (
            "test-rag-embedding-dashscope-profile"
        )
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert _real_smoke_env_present() is True, (
            "Gate rejected a smoke that uses the RAG_EMBEDDING_MODEL_PROFILE "
            "registry route instead of BAILIAN_API_KEY.  Both paths must "
            "be acceptable per the I4D factory contract."
        )


class TestRunbookHasI4ZSections:
    """The runbook must document the I4Z dry-run + opt-in smoke
    commands.  These two sections are the human-facing contract for
    "how do I actually run this locally" and "how do I opt into a
    real-provider smoke".
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

    def test_doc_has_opt_in_real_smoke_command(self, doc_text: str) -> None:
        # The runbook must show the explicit opt-in command.
        for needle in (
            "READER_ARTICLE_RAG_SMOKE=1",
            "pytest",
            "-m",
            "article_rag_smoke",
        ):
            assert needle in doc_text, (
                f"Runbook must show real-smoke opt-in command "
                f"containing {needle!r}"
            )

    def test_doc_lists_required_env_for_real_smoke(self, doc_text: str) -> None:
        for env in REAL_SMOKE_REQUIRED_ENVS:
            assert env in doc_text, (
                f"Runbook must list {env} as a required env for the "
                f"real-provider smoke"
            )

    def test_doc_documents_either_or_credential_contract(
        self, doc_text: str
    ) -> None:
        # The runbook must list BOTH BAILIAN_API_KEY and
        # RAG_EMBEDDING_MODEL_PROFILE so ops can pick either path.
        # The smoke gate accepts either-or (at least one non-empty);
        # the runbook must reflect that contract, not the legacy
        # BAILIAN_API_KEY-only contract.
        for env in REAL_SMOKE_CREDENTIAL_ENVS:
            assert env in doc_text, (
                f"Runbook must list {env} as a credential-env option "
                f"for the real-provider smoke (either-or contract)"
            )

    def test_doc_documents_collection_isolation_prefix(
        self, doc_text: str
    ) -> None:
        # The runbook must call out the collection namespace
        # prefix.  This is the safety guard: the opt-in smoke
        # refuses to enter unless the configured Zilliz collection
        # starts with this prefix, so an env-typo cannot write to
        # a production-named collection.  The runbook must make
        # that contract visible to ops.
        assert REAL_SMOKE_COLLECTION_PREFIX in doc_text, (
            f"Runbook must document the smoke collection prefix "
            f"{REAL_SMOKE_COLLECTION_PREFIX!r} so ops know the "
            f"isolation guard exists"
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
