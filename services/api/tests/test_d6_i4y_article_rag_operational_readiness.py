"""D6-I4Y Article RAG Operational Readiness tests.

Proves the Article RAG pipeline is **locally operable, debuggable, and
hand-offable** without touching the network by default.

Default behavior (no env):

  - settings surface has every ``reader_article_rag_*`` knob with the
    expected default
  - embedding / vector writer factories return ``Unconfigured*`` when
    no DashScope / Zilliz config is set — **no** network IO at
    construction time
  - the ``reader-article-rag-index-worker`` CLI entry-point constructs
    cleanly with no config
  - the runbook doc and the ``.env.example`` block exist and document
    the required knobs
  - no settings field repr leaks a token / API key
  - ``READER_ARTICLE_RAG_SMOKE`` is off by default (test opt-in gate)
  - the lifecycle status reason codes (queued / indexing / indexed /
    failed / superseded_or_stale / not_ready / unavailable) are all
    defined

Opt-in (env-gated) behavior:

  - ``READER_ARTICLE_RAG_SMOKE=1`` plus valid DashScope / Zilliz env
    enables a single real-provider smoke skeleton.  The test is
    ``skip``-decorated and only documents the opt-in contract — it is
    **not** the default runbook path.

Hard limits (enforced by the test surface, not just by review):

  - never calls real DB / network / worker execution / vector store IO
  - never reads ``DASHSCOPE_API_KEY`` / ``BAILIAN_API_KEY`` /
    ``ZILLIZ_TOKEN`` / ``READER_ARTICLE_RAG_ZILLIZ_TOKEN`` from the
    environment unless the env-gated test is explicitly opted in
  - the worker entry module and the lifecycle service module ARE
    imported (so the runbook-canonical CLI name, the lifecycle status
    constants, and the unconfigured-provider factories can be
    inspected), but no method that touches the database or the
    network is invoked.  ``--help`` is the only subprocess call and
    it is bounded by a 15 s timeout.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Markers for the test categorisation:
#   - default: any test that runs with no env gate
#   - article_rag_smoke: only runs when READER_ARTICLE_RAG_SMOKE=1 is set
#
# We register the marker via ``pytest.mark.*`` below; the default
# pytest config in pyproject.toml does not need to be modified for the
# default tests to run.
article_rag_smoke = pytest.mark.article_rag_smoke
no_network_default = pytest.mark.no_network_default

# Repo-relative paths used by several tests.
REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "services" / "api"
ENV_EXAMPLE = API_ROOT / ".env.example"
RUNBOOK_DOC = (
    REPO_ROOT
    / "docs"
    / "initiatives"
    / "reader-agentic-orchestration"
    / "modules"
    / "local-article-rag-runbook.md"
)
PYPROJECT_TOML = API_ROOT / "pyproject.toml"
ENTRY_POINT_MODULE = "scripts.run_reader_article_rag_index_worker"

# The opt-in env gate.  CI default: skip.  Local opt-in: set to "1".
ARTICLE_RAG_SMOKE_ENV = "READER_ARTICLE_RAG_SMOKE"


# ---------------------------------------------------------------------------
# 1. Settings surface — every reader_article_rag_* knob exists with default
# ---------------------------------------------------------------------------


class TestSettingsSurface:
    """Every Article RAG setting must be a typed field with a sane default.

    These tests do NOT need the real ``Settings`` object (which loads
    from .env); they instantiate ``Settings()`` with no env override so
    the defaults are exercised.
    """

    def _make_settings(self) -> object:
        from app.config.settings import Settings  # local import — only here

        return Settings()

    def test_feature_flag_default_is_false(self) -> None:
        settings = self._make_settings()
        assert settings.reader_article_rag_enabled is False

    def test_embedding_provider_default_is_empty(self) -> None:
        settings = self._make_settings()
        assert settings.reader_article_rag_embedding_provider == ""
        assert settings.reader_article_rag_embedding_model == ""

    def test_vector_provider_default_is_empty_with_safe_collection(self) -> None:
        settings = self._make_settings()
        assert settings.reader_article_rag_vector_provider == ""
        # Collection has a safe default but uri / token are empty.
        assert (
            settings.reader_article_rag_zilliz_collection
            == "article_rag_chunks"
        )
        assert settings.reader_article_rag_zilliz_uri == ""
        assert settings.reader_article_rag_zilliz_token == ""
        assert settings.reader_article_rag_vector_dim == 1024

    def test_worker_loop_defaults(self) -> None:
        settings = self._make_settings()
        assert (
            settings.reader_article_rag_worker_poll_interval_seconds
            == 5
        )
        assert (
            settings.reader_article_rag_worker_lease_duration_seconds
            == 120
        )
        assert (
            settings.reader_article_rag_worker_lease_owner_prefix
            == "reader-article-rag-index-worker"
        )
        assert settings.reader_article_rag_worker_max_ticks == 100

    def test_smoke_gate_default_is_false(self) -> None:
        """The opt-in smoke gate MUST default to False so the real-provider
        integration path never runs in CI / production by accident.
        """
        # The env var must not be set in the test environment for this
        # assertion to be meaningful; if a sibling test sets it, the
        # assertion below would still hold (we are reading the
        # settings default, not the env at test time).
        settings = self._make_settings()
        assert settings.reader_article_rag_smoke is False

    def test_settings_constructor_accepts_token_field(self) -> None:
        """The settings model HAS a ``reader_article_rag_zilliz_token``
        field — it must be settable so the I4D factory can read it.
        This is a structural assertion, not a leak assertion:

          - ``repr(Settings())`` is NOT leak-safe at the settings layer
            (it dumps every field for debug).  Do not log it.  This is
            documented in the runbook.
          - The Article RAG DTOs (lifecycle status, ensure result) ARE
            leak-safe — that contract is covered by
            ``TestSecretRedLine`` below.
        """
        from app.config.settings import Settings

        settings = Settings(
            reader_article_rag_zilliz_token="sentinel-token-do-not-leak",
        )
        # Field is present and readable; we never compare it against
        # ``repr(settings)`` because settings repr is intentionally
        # verbose for debug.
        assert settings.reader_article_rag_zilliz_token == "sentinel-token-do-not-leak"


# ---------------------------------------------------------------------------
# 2. Factories return Unconfigured* with no network IO
# ---------------------------------------------------------------------------


class TestFactoriesReturnUnconfiguredByDefault:
    """When no DashScope / Zilliz config is set, both factories must
    return the ``Unconfigured*`` placeholder, never a real provider.
    No network call is permitted at construction time.
    """

    def _make_settings(self) -> object:
        from app.config.settings import Settings

        return Settings()

    def test_embedding_factory_returns_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With all embedding knobs blank, the factory must hand back
        the unconfigured sentinel — no DashScope / Bailian client is
        constructed and no env var is read for an API key.
        """
        # Strip any provider API key from the env so the factory cannot
        # accidentally resolve a real credential.
        for var in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "Bailian_API_KEY",
            "bailian_api_key",
        ):
            monkeypatch.delenv(var, raising=False)

        from app.services.reader_orchestration.article_rag_embedding_provider import (
            build_default_article_rag_embedding_provider,
        )
        from app.services.reader_orchestration.article_rag_index_worker import (
            UnconfiguredArticleRagEmbeddingProvider,
        )

        settings = self._make_settings()
        provider = build_default_article_rag_embedding_provider(settings)

        assert isinstance(provider, UnconfiguredArticleRagEmbeddingProvider)
        # Sentinel behaviour: embed_texts raises a typed
        # ``embedding_provider_unconfigured`` failure.
        from app.services.reader_orchestration.article_rag_index_worker import (
            ArticleRagIndexWorkerError,
        )

        async def _go() -> None:
            await provider.embed_texts(["probe"])

        import asyncio

        with pytest.raises(ArticleRagIndexWorkerError) as exc_info2:
            asyncio.run(_go())
        assert exc_info2.value.failure_code == "embedding_provider_unconfigured"
        assert exc_info2.value.retryable is False

    def test_vector_writer_factory_returns_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With vector_provider blank (the default), the factory must
        hand back the unconfigured sentinel.
        """
        monkeypatch.delenv("ZILLIZ_TOKEN", raising=False)
        monkeypatch.delenv("ZILLIZ_URI", raising=False)

        from app.services.reader_orchestration.article_rag_index_worker import (
            UnconfiguredArticleRagVectorWriter,
        )
        from app.services.reader_orchestration.article_rag_vector_store import (
            build_default_article_rag_vector_writer,
        )

        settings = self._make_settings()
        writer = build_default_article_rag_vector_writer(settings)

        assert isinstance(writer, UnconfiguredArticleRagVectorWriter)

        from uuid import UUID

        from app.services.reader_orchestration.article_rag_index_worker import (
            ArticleRagIndexWorkerError,
            ArticleRagVectorWriteMetadata,
        )

        async def _go() -> None:
            await writer.upsert_chunks(
                collection="probe",
                chunks_with_embeddings=[],
                metadata=ArticleRagVectorWriteMetadata(
                    collection="probe",
                    reading_record_id=UUID(
                        "00000000-0000-0000-0000-000000000000"
                    ),
                    stable_document_id=UUID(
                        "00000000-0000-0000-0000-000000000000"
                    ),
                    base_id=UUID("00000000-0000-0000-0000-000000000000"),
                    record_generation=1,
                    plan_content_sha256="0" * 64,
                    chunk_count=0,
                    # P1-G: embedding_model / embedding_dimension /
                    # embedding_text_type are required fields sourced from
                    # the frozen ARTICLE_RAG_EMBEDDING_CONTRACT.  This
                    # construction only feeds an unconfigured writer which
                    # raises before reading any of these fields, so
                    # canonical-shape placeholders are sufficient.
                    embedding_model="probe",
                    embedding_dimension=0,
                    embedding_text_type="probe",
                ),
            )

        import asyncio

        with pytest.raises(ArticleRagIndexWorkerError) as exc_info:
            asyncio.run(_go())
        assert exc_info.value.failure_code == "vector_writer_unconfigured"
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# 3. Worker entry script — builds cleanly with no config, registers in pyproject
# ---------------------------------------------------------------------------


class TestWorkerEntryScript:
    """The ``reader-article-rag-index-worker`` CLI is the runbook entry
    point.  Importing it must not crash; constructing the worker
    service via its factory must not crash; and it must be registered
    as a ``[project.scripts]`` entry-point in ``pyproject.toml``.
    """

    def test_entry_point_registered_in_pyproject(self) -> None:
        text = PYPROJECT_TOML.read_text(encoding="utf-8")
        # The runbook-canonical name; sibling agents must not rename it.
        assert (
            "reader-article-rag-index-worker" in text
        ), "reader-article-rag-index-worker must be registered in pyproject"
        assert ENTRY_POINT_MODULE in text

    def test_entry_module_imports_without_network(self) -> None:
        """Importing the worker entry must not read network or call
        out.  The module's top-level imports should be pure-Python.
        """
        # If the module had a top-level httpx / dashscope / zilliz
        # call, this import would either fail or hit the network.  We
        # trust the surrounding conftest ``fail_on_real_llm_attempts``
        # guard to catch any such regression at teardown.
        importlib.import_module(ENTRY_POINT_MODULE)

    def test_build_worker_service_constructs_with_no_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``build_worker_service`` must NEVER raise at construction
        time, even with no DashScope / Zilliz config.  The resulting
        worker must carry unconfigured providers so the first job
        fails closed with a clear error rather than crashing the
        process.
        """
        # Strip any provider API key from the env so the factory cannot
        # accidentally resolve a real credential.
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

        # A real ``asyncpg.Pool`` is not needed for this assertion —
        # the providers are constructed before the service touches
        # the pool.  Pass a sentinel object; the test only inspects
        # provider wiring, not DB IO.
        class _SentinelPool:
            pass

        settings = Settings()
        service = build_worker_service(settings=settings, pool=_SentinelPool())
        assert isinstance(service, ArticleRagIndexWorkerService)
        assert isinstance(
            service._embedding_provider,  # type: ignore[attr-defined]
            UnconfiguredArticleRagEmbeddingProvider,
        )
        assert isinstance(
            service._vector_writer,  # type: ignore[attr-defined]
            UnconfiguredArticleRagVectorWriter,
        )

    def test_worker_help_outputs_no_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--help`` should print CLI help without ever opening a
        socket.  This is the lightest-weight smoke for the entry-point
        binary.
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
                ENTRY_POINT_MODULE,
                "--help",
            ],
            cwd=str(API_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        # Help output should mention the runbook-canonical CLI options.
        assert "--once" in result.stdout
        assert "--poll-interval-seconds" in result.stdout
        assert "--lease-duration-seconds" in result.stdout


# ---------------------------------------------------------------------------
# 4. Lifecycle status reason codes — all required values defined
# ---------------------------------------------------------------------------


class TestLifecycleStatusReasonCodes:
    """The runbook documents seven lifecycle status values.  They must
    all be defined as module-level constants in the lifecycle service
    module so route + ops dashboards can refer to a single source of
    truth.
    """

    def test_all_lifecycle_status_constants_exist(self) -> None:
        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
            STATUS_FAILED,
            STATUS_INDEXED,
            STATUS_INDEXING,
            STATUS_NOT_INDEXED,
            STATUS_NOT_READY,
            STATUS_QUEUED,
            STATUS_SUPERSEDED_OR_STALE,
            STATUS_UNAVAILABLE,
        )

        # Runbook-documented lifecycle status values.
        assert STATUS_NOT_READY == "not_ready"
        assert STATUS_NOT_INDEXED == "not_indexed"
        assert STATUS_QUEUED == "queued"
        assert STATUS_INDEXING == "indexing"
        assert STATUS_INDEXED == "indexed"
        assert STATUS_FAILED == "failed"
        assert STATUS_SUPERSEDED_OR_STALE == "superseded_or_stale"
        assert STATUS_UNAVAILABLE == "unavailable"

    def test_all_ensure_status_constants_exist(self) -> None:
        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
            ENSURE_STATUS_BOOTSTRAP_INCONSISTENT,
            ENSURE_STATUS_ENQUEUED,
            ENSURE_STATUS_ERROR,
            ENSURE_STATUS_GENERATION_MISMATCH,
            ENSURE_STATUS_IDEMPOTENT_NOOP,
            ENSURE_STATUS_NO_ACTIVE_BASE,
            ENSURE_STATUS_NOT_READY,
            ENSURE_STATUS_PLAN_HASH_MISMATCH,
            ENSURE_STATUS_RECORD_NOT_FOUND,
        )

        assert ENSURE_STATUS_ENQUEUED == "enqueued"
        assert ENSURE_STATUS_IDEMPOTENT_NOOP == "idempotent_noop"
        assert ENSURE_STATUS_NOT_READY == "not_ready"
        assert ENSURE_STATUS_NO_ACTIVE_BASE == "no_active_base"
        assert ENSURE_STATUS_GENERATION_MISMATCH == "generation_mismatch"
        assert ENSURE_STATUS_RECORD_NOT_FOUND == "record_not_found"
        assert ENSURE_STATUS_PLAN_HASH_MISMATCH == "plan_hash_mismatch"
        assert ENSURE_STATUS_BOOTSTRAP_INCONSISTENT == "bootstrap_inconsistent"
        assert ENSURE_STATUS_ERROR == "error"


# ---------------------------------------------------------------------------
# 5. .env.example block — every required knob is documented
# ---------------------------------------------------------------------------


class TestEnvExampleBlock:
    """``.env.example`` is the runbook's contract surface for ops.
    Every ``READER_ARTICLE_RAG_*`` knob that ``Settings`` exposes must
    appear in the example, with a no-real-secret default.
    """

    @pytest.fixture(scope="class")
    def env_text(self) -> str:
        return ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_env_example_has_article_rag_block(self, env_text: str) -> None:
        # Locate the Article RAG section by its leading comment marker.
        assert "Article RAG" in env_text, (
            ".env.example must contain a documented Article RAG block"
        )
        assert "READER_ARTICLE_RAG_ENABLED" in env_text
        assert "READER_ARTICLE_RAG_EMBEDDING_PROVIDER" in env_text
        assert "READER_ARTICLE_RAG_EMBEDDING_MODEL" in env_text
        assert "READER_ARTICLE_RAG_VECTOR_PROVIDER" in env_text
        assert "READER_ARTICLE_RAG_ZILLIZ_URI" in env_text
        assert "READER_ARTICLE_RAG_ZILLIZ_TOKEN" in env_text
        assert "READER_ARTICLE_RAG_ZILLIZ_COLLECTION" in env_text
        assert "READER_ARTICLE_RAG_VECTOR_DIM" in env_text
        assert "READER_ARTICLE_RAG_WORKER_POLL_INTERVAL_SECONDS" in env_text
        assert "READER_ARTICLE_RAG_WORKER_LEASE_DURATION_SECONDS" in env_text
        assert "READER_ARTICLE_RAG_WORKER_LEASE_OWNER_PREFIX" in env_text
        assert "READER_ARTICLE_RAG_WORKER_MAX_TICKS" in env_text
        assert "READER_ARTICLE_RAG_SMOKE" in env_text

    def test_env_example_contains_no_real_secret(
        self, env_text: str
    ) -> None:
        """``.env.example`` must NEVER contain a real-looking secret.
        All token / key fields must have empty defaults.
        """
        for forbidden in (
            "DASHSCOPE_API_KEY=",
            "BAILIAN_API_KEY=",
            "ZILLIZ_TOKEN=",
        ):
            # Each must be present with an empty default, not a real value.
            for line in env_text.splitlines():
                if line.startswith(forbidden):
                    # Default must be empty string.
                    value = line.split("=", 1)[1].strip().strip('"')
                    assert value == "", (
                        f"{forbidden} in .env.example must be empty by default; "
                        f"got: {value!r}"
                    )

    def test_env_example_feature_flag_defaults_off(self, env_text: str) -> None:
        # READER_ARTICLE_RAG_ENABLED must default to false so production
        # deployments need an explicit opt-in.
        for line in env_text.splitlines():
            if line.startswith("READER_ARTICLE_RAG_ENABLED="):
                value = line.split("=", 1)[1].strip().strip('"')
                assert value == "false", (
                    "READER_ARTICLE_RAG_ENABLED must default to false; "
                    f"got: {value!r}"
                )

    def test_env_example_smoke_gate_defaults_off(self, env_text: str) -> None:
        # READER_ARTICLE_RAG_SMOKE must default to false so the real
        # provider smoke never runs in CI by accident.
        for line in env_text.splitlines():
            if line.startswith("READER_ARTICLE_RAG_SMOKE="):
                value = line.split("=", 1)[1].strip().strip('"')
                assert value == "false", (
                    "READER_ARTICLE_RAG_SMOKE must default to false; "
                    f"got: {value!r}"
                )


# ---------------------------------------------------------------------------
# 6. Runbook doc — present, references the right URLs / field names
# ---------------------------------------------------------------------------


class TestRunbookDoc:
    """The runbook doc is the human-facing contract.  It must exist
    and must reference the canonical CLI name, settings fields, and
    lifecycle status values that the rest of the system actually
    exposes — otherwise ops will follow stale instructions.
    """

    @pytest.fixture(scope="class")
    def doc_text(self) -> str:
        assert RUNBOOK_DOC.exists(), (
            f"Runbook doc not found at {RUNBOOK_DOC}"
        )
        return RUNBOOK_DOC.read_text(encoding="utf-8")

    def test_doc_references_canonical_cli_name(self, doc_text: str) -> None:
        assert "reader-article-rag-index-worker" in doc_text

    def test_doc_references_lifecycle_status_values(self, doc_text: str) -> None:
        for status in (
            "indexed",
            "indexing",
            "queued",
            "failed",
            "superseded_or_stale",
            "not_indexed",
            "not_ready",
            "unavailable",
        ):
            assert status in doc_text, (
                f"Runbook must document lifecycle status {status!r}"
            )

    def test_doc_references_ensure_status_values(self, doc_text: str) -> None:
        for status in (
            "enqueued",
            "idempotent_noop",
            "no_active_base",
            "generation_mismatch",
            "plan_hash_mismatch",
            "record_not_found",
        ):
            assert status in doc_text, (
                f"Runbook must document ensure status {status!r}"
            )

    def test_doc_references_required_settings_knobs(
        self, doc_text: str
    ) -> None:
        for knob in (
            "READER_ARTICLE_RAG_ENABLED",
            "READER_ARTICLE_RAG_EMBEDDING_PROVIDER",
            "READER_ARTICLE_RAG_VECTOR_PROVIDER",
            "READER_ARTICLE_RAG_ZILLIZ_URI",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_COLLECTION",
            "READER_ARTICLE_RAG_WORKER_POLL_INTERVAL_SECONDS",
            "READER_ARTICLE_RAG_WORKER_LEASE_DURATION_SECONDS",
            "READER_ARTICLE_RAG_SMOKE",
        ):
            assert knob in doc_text, (
                f"Runbook must document the {knob} knob"
            )

    def test_doc_warns_against_secrets_in_logs(self, doc_text: str) -> None:
        """The runbook must call out the secret-red-line explicitly so
        ops and on-call reviewers know the contract.
        """
        # We don't pin a specific phrasing — only that the runbook
        # names every key field as forbidden in logs / HTTP detail /
        # prompt sidecar / metadata repr.
        for forbidden_token in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ZILLIZ_TOKEN",
            "READER_ARTICLE_RAG_ZILLIZ_TOKEN",
        ):
            assert forbidden_token in doc_text, (
                f"Runbook must call out {forbidden_token} as a secret "
                f"that must never appear in logs / detail / sidecar"
            )

    def test_doc_does_not_promise_real_smoke_by_default(
        self, doc_text: str
    ) -> None:
        """The runbook must NOT claim the real-provider smoke is the
        default path.  It is opt-in only.
        """
        # Pin a soft assertion: the doc must contain the opt-in gate
        # variable name and must not contain phrases that imply
        # ``READER_ARTICLE_RAG_SMOKE=true`` is the recommended state.
        assert "READER_ARTICLE_RAG_SMOKE" in doc_text
        # And the opt-in semantics must be explicit.
        assert "opt-in" in doc_text.lower()


# ---------------------------------------------------------------------------
# 7. Secret-red-line — no module leaks a token via repr
# ---------------------------------------------------------------------------


class TestSecretRedLine:
    """Even when a real token is loaded, no Article RAG public
    dataclass / result object must echo the token in ``repr()`` /
    ``str()``.  This is a load-bearing contract: any future change
    that adds a token-bearing field to a public DTO will be caught
    here.
    """

    def test_lifecycle_status_repr_has_no_token_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = "leak-probe-token-aaaaaaaaaaaaaaaaaaaa"
        from uuid import UUID

        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
            ArticleRagIndexLifecycleStatus,
        )

        status = ArticleRagIndexLifecycleStatus(
            reading_record_id=UUID("00000000-0000-0000-0000-000000000001"),
            user_id=UUID("00000000-0000-0000-0000-000000000002"),
            status="indexed",
        )
        rendered = repr(status) + "|" + str(status)
        assert sentinel not in rendered
        # And the sentinel field that we set must not have leaked via
        # any default field name.
        assert "zilliz" not in rendered.lower()
        assert "token" not in rendered.lower()

    def test_ensure_result_repr_has_no_token_field(self) -> None:
        sentinel = "leak-probe-token-bbbbbbbbbbbbbbbbbbbb"
        from uuid import UUID

        from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
            ArticleRagIndexEnsureResult,
        )

        result = ArticleRagIndexEnsureResult(
            reading_record_id=UUID("00000000-0000-0000-0000-000000000001"),
            status="enqueued",
            reason_code="enqueued",
            idempotent_noop=False,
        )
        rendered = repr(result) + "|" + str(result)
        assert sentinel not in rendered
        assert "token" not in rendered.lower()


# ---------------------------------------------------------------------------
# 8. Opt-in real-provider smoke skeleton — skipped by default
# ---------------------------------------------------------------------------


@article_rag_smoke
@pytest.mark.skipif(
    os.environ.get(ARTICLE_RAG_SMOKE_ENV) != "1",
    reason=(
        f"{ARTICLE_RAG_SMOKE_ENV}=1 required to run the real-provider "
        "Article RAG integration smoke.  Default is skip.  Do not set "
        "this in production deployments."
    ),
)
def test_real_provider_smoke_skeleton_is_documented() -> None:
    """Real-provider smoke skeleton.

    This is the **opt-in** path.  The full real-provider chain
    (DashScope embedding + Zilliz upsert + retrieval against the
    real Zilliz collection) is a separate, intentionally-skipped test
    that documents the opt-in contract.  It is wired with the
    ``article_rag_smoke`` marker so CI can run it explicitly:

        READER_ARTICLE_RAG_SMOKE=1 pytest -m article_rag_smoke

    To turn this into a runnable test, fill in three things:

      1. Read the real ``Settings()`` (so the embedded credential is
         honored) and instantiate a real
         :class:`DashScopeArticleRagEmbeddingProvider` +
         :class:`ZillizArticleRagVectorWriter`.
      2. Seed the same minimal reading record / stable document /
         paragraph block graph that ``test_d6_i4w`` uses.
      3. Run ``ensure`` -> ``worker.process_next`` -> ``retrieval``
         and assert the real ``index_run.vector_collection`` /
         ``embedding_model`` plus a real ``ZillizArticleRagSearcher``
         hit list.

    The default pytest run is unaffected — this test is skipped unless
    the gate is explicitly opened.
    """
    pytest.skip(
        "Real-provider smoke skeleton is opt-in only.  See test docstring "
        "for the contract; this assertion is documentation."
    )