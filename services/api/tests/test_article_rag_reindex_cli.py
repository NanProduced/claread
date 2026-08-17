"""Tests for the operator reindex CLI entry (Wave 7 / F1c phase C).

Covers ``scripts/run_reader_article_rag_reindex.py``:

  * ``--record-id`` and ``--all`` are mutually exclusive;
  * default mode is dry-run — zero writes, zero service calls;
  * ``--execute`` calls the production lifecycle service (one
    caller-owned transaction per record);
  * batch failures do not abort the run; the final summary is stable
    (scanned / eligible / enqueued / in_progress / skipped / failed);
  * rate limiting spaces execute iterations without sleep-based
    concurrency tricks;
  * the CLI never touches embedding / vector providers.

All fakes — no DB, no network.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR.parent) not in sys.path:
    sys.path.append(str(_SCRIPTS_DIR.parent))

from scripts.run_reader_article_rag_reindex import (  # noqa: E402
    build_arg_parser,
    run_reindex,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


_RECORD_A = uuid.uuid4()
_RECORD_B = uuid.uuid4()
_RECORD_C = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeReindexResult:
    reading_record_id: uuid.UUID
    status: str
    reason_code: str = ""
    superseded_index_run_id: uuid.UUID | None = None
    new_index_run_id: uuid.UUID | None = None


class _FakeService:
    """Records calls; returns per-record configured results."""

    def __init__(
        self,
        results: dict[uuid.UUID, _FakeReindexResult | Exception] | None = None,
    ) -> None:
        self._results = results or {}
        self.calls: list[uuid.UUID] = []

    async def reindex_article_rag_index_in_transaction(
        self, conn: Any, *, reading_record_id: uuid.UUID, user_id: uuid.UUID
    ) -> _FakeReindexResult:
        self.calls.append(reading_record_id)
        configured = self._results.get(reading_record_id)
        if configured is None:
            return _FakeReindexResult(
                reading_record_id=reading_record_id,
                status="no_indexed_run",
            )
        if isinstance(configured, Exception):
            raise configured
        return configured


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeConn:
    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        return None


@dataclass
class _FakePool:
    """Routes ``fetch`` by SQL shape (mirrors the CLI's three queries):

    * dry-run classification (JOIN stable_reading_documents)
    * ``--all`` candidates (SELECT DISTINCT)
    * owner lookup (SELECT user_id FROM reading_records)
    """

    dry_run_rows: list[dict[str, Any]] = field(default_factory=list)
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    owner_rows: list[dict[str, Any]] = field(default_factory=list)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(_FakeConn())

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        s = sql.lower()
        # The --all candidates query embeds the latest-run truth (which
        # also mentions stable_reading_documents) — route DISTINCT first.
        if "distinct" in s:
            return list(self.candidate_rows)
        if "stable_reading_documents" in s:
            if args:
                key = args[0]
                return [
                    r
                    for r in self.dry_run_rows
                    if r["reading_record_id"] == key
                ]
            return list(self.dry_run_rows)
        if "user_id from reading_records" in s:
            if args:
                key = args[0]
                return [
                    r for r in self.owner_rows if r["reading_record_id"] == key
                ]
            return list(self.owner_rows)
        return []


def _enqueued(record: uuid.UUID) -> _FakeReindexResult:
    return _FakeReindexResult(
        reading_record_id=record,
        status="reindex_enqueued",
        superseded_index_run_id=uuid.uuid4(),
        new_index_run_id=uuid.uuid4(),
    )


def _in_progress(record: uuid.UUID) -> _FakeReindexResult:
    return _FakeReindexResult(
        reading_record_id=record,
        status="reindex_in_progress",
    )


# ---------------------------------------------------------------------------
# argparse contract
# ---------------------------------------------------------------------------


class TestArgParsing:
    def test_record_id_and_all_are_mutually_exclusive(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["--record-id", str(_RECORD_A), "--all"]
            )

    def test_one_of_record_id_or_all_required(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_default_is_dry_run(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--record-id", str(_RECORD_A)])
        assert args.execute is False

    def test_execute_flag_parses(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--all", "--execute"])
        assert args.execute is True
        assert args.all is True

    def test_rate_limit_and_limit_parse(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--all",
                "--execute",
                "--rate-limit-per-second",
                "2.5",
                "--limit",
                "10",
            ]
        )
        assert args.rate_limit_per_second == 2.5
        assert args.limit == 10

    def test_zero_rate_limit_is_allowed(self) -> None:
        """0 explicitly disables rate limiting — a valid value."""
        parser = build_arg_parser()
        args = parser.parse_args(
            ["--all", "--rate-limit-per-second", "0"]
        )
        assert args.rate_limit_per_second == 0.0

    def test_negative_rate_limit_rejected(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["--all", "--rate-limit-per-second", "-1"]
            )

    def test_non_positive_limit_rejected(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--all", "--limit", "0"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--all", "--limit", "-3"])

    def test_limit_requires_all_mode(self) -> None:
        """--limit only caps the --all candidate list; combining it
        with --record-id is an operator error."""
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["--record-id", str(_RECORD_A), "--limit", "5"]
            )


# ---------------------------------------------------------------------------
# dry-run: zero writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDryRun:
    async def test_dry_run_never_calls_service(self) -> None:
        service = _FakeService()
        summary = await run_reindex(
            pool=_FakePool(),
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A, _RECORD_B],
            all_records=False,
            execute=False,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert service.calls == []
        assert summary["scanned"] == 2
        assert summary["eligible"] == 0  # dry-run cannot enqueue anything

    async def test_dry_run_reports_eligible_via_readonly_scan(self) -> None:
        """Dry-run classifies each record by its current active-run
        status (read-only) and reports the would-reindex count."""
        pool = _FakePool(
            dry_run_rows=[
                {"reading_record_id": _RECORD_A, "status": "indexed"},
                {"reading_record_id": _RECORD_B, "status": "queued"},
            ]
        )
        service = _FakeService()
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A, _RECORD_B],
            all_records=False,
            execute=False,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert service.calls == []
        assert summary["scanned"] == 2
        assert summary["eligible"] == 1
        assert summary["in_progress"] == 1

    async def test_dry_run_classifies_failed_run_as_recoverable(self) -> None:
        """Wave 7.1 / P0: a terminal ``failed`` latest run means the
        record is recovery-eligible — dry-run must report it as
        eligible, not skipped (so batch planning sees it)."""
        pool = _FakePool(
            dry_run_rows=[
                {"reading_record_id": _RECORD_A, "status": "failed"},
            ]
        )
        service = _FakeService()
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A],
            all_records=False,
            execute=False,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert service.calls == []
        assert summary["eligible"] == 1
        assert summary["skipped"] == 0


# ---------------------------------------------------------------------------
# execute: production service per record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecute:
    async def test_execute_calls_service_per_record(self) -> None:
        service = _FakeService(
            results={
                _RECORD_A: _enqueued(_RECORD_A),
                _RECORD_B: _in_progress(_RECORD_B),
            }
        )
        pool = _FakePool(
            owner_rows=[
                {"reading_record_id": _RECORD_A, "user_id": uuid.uuid4()},
                {"reading_record_id": _RECORD_B, "user_id": uuid.uuid4()},
            ]
        )
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A, _RECORD_B],
            all_records=False,
            execute=True,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert service.calls == [_RECORD_A, _RECORD_B]
        assert summary["enqueued"] == 1
        assert summary["in_progress"] == 1
        assert summary["failed"] == 0

    async def test_batch_failure_continues_and_summarises(self) -> None:
        service = _FakeService(
            results={
                _RECORD_A: _enqueued(_RECORD_A),
                _RECORD_B: RuntimeError("db exploded"),
                _RECORD_C: _enqueued(_RECORD_C),
            }
        )
        pool = _FakePool(
            owner_rows=[
                {"reading_record_id": _RECORD_A, "user_id": uuid.uuid4()},
                {"reading_record_id": _RECORD_B, "user_id": uuid.uuid4()},
                {"reading_record_id": _RECORD_C, "user_id": uuid.uuid4()},
            ]
        )
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A, _RECORD_B, _RECORD_C],
            all_records=False,
            execute=True,
            rate_limit_per_second=0.0,
            limit=None,
        )
        # The failing record did not abort the batch.
        assert service.calls == [_RECORD_A, _RECORD_B, _RECORD_C]
        assert summary["scanned"] == 3
        assert summary["enqueued"] == 2
        assert summary["failed"] == 1

    async def test_all_mode_reads_candidates_from_pool(self) -> None:
        pool = _FakePool(
            candidate_rows=[
                {
                    "reading_record_id": _RECORD_A,
                    "user_id": uuid.uuid4(),
                }
            ],
        )
        service = _FakeService(results={_RECORD_A: _enqueued(_RECORD_A)})
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=None,
            all_records=True,
            execute=True,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert service.calls == [_RECORD_A]
        assert summary["enqueued"] == 1

    async def test_execute_counts_recovery_enqueued(self) -> None:
        """Wave 7.1 / P0: the service's recovery path status must map
        to the enqueued bucket, not skipped."""
        service = _FakeService(
            results={
                _RECORD_A: _FakeReindexResult(
                    reading_record_id=_RECORD_A,
                    status="recovery_enqueued",
                    new_index_run_id=uuid.uuid4(),
                ),
            }
        )
        pool = _FakePool(
            owner_rows=[
                {"reading_record_id": _RECORD_A, "user_id": uuid.uuid4()},
            ]
        )
        summary = await run_reindex(
            pool=pool,
            service=service,  # type: ignore[arg-type]
            record_ids=[_RECORD_A],
            all_records=False,
            execute=True,
            rate_limit_per_second=0.0,
            limit=None,
        )
        assert summary["enqueued"] == 1
        assert summary["eligible"] == 1
        assert summary["skipped"] == 0


# ---------------------------------------------------------------------------
# structural: no provider wiring
# ---------------------------------------------------------------------------


def test_cli_module_has_no_provider_imports() -> None:
    """The reindex CLI must never import embedding / vector provider
    modules — it only flips PostgreSQL state."""
    import scripts.run_reader_article_rag_reindex as cli_mod

    source = Path(cli_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "DashScopeArticleRagEmbeddingProvider",
        "ZillizArticleRagVectorWriter",
        "build_default_article_rag_embedding_provider",
        "build_default_article_rag_vector_writer",
        "embed_texts",
        "upsert_chunks",
    ):
        assert forbidden not in source


def test_dry_run_sql_filters_inactive_records() -> None:
    """Wave 7.1 / P2: the dry-run classification query must join the
    reading record with deleted_at IS NULL + lifecycle_status='active'
    so deleted/inactive records are skipped, never reported eligible."""
    import scripts.run_reader_article_rag_reindex as cli_mod

    source = Path(cli_mod.__file__).read_text(encoding="utf-8")
    dry_run_start = source.index("_DRY_RUN_STATUS_SQL")
    dry_run_end = source.index('_ALL_CANDIDATES_SQL')
    dry_run_sql = source[dry_run_start:dry_run_end]
    assert "reading_records" in dry_run_sql
    assert "deleted_at IS NULL" in dry_run_sql
    assert "lifecycle_status = 'active'" in dry_run_sql


def test_all_candidates_sql_includes_failed_runs() -> None:
    """Wave 7.1 / P0: the --all candidate list must include records
    whose latest run failed (recoverable), not only indexed ones."""
    import scripts.run_reader_article_rag_reindex as cli_mod

    source = Path(cli_mod.__file__).read_text(encoding="utf-8")
    candidates_start = source.index("_ALL_CANDIDATES_SQL")
    candidates_end = source.index("_RECORD_OWNER_SQL")
    candidates_sql = source[candidates_start:candidates_end]
    assert "'indexed'" in candidates_sql
    assert "'failed'" in candidates_sql

# ---------------------------------------------------------------------------
# Wave 11: latest-run truth (real PostgreSQL integration)
# ---------------------------------------------------------------------------


@dataclass
class _FakeServiceProbe:
    """Records execute-mode calls against the production service."""

    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def reindex_article_rag_index_in_transaction(
        self, conn: Any, *, reading_record_id: uuid.UUID, user_id: uuid.UUID
    ) -> _FakeReindexResult:
        self.calls.append(reading_record_id)
        return _FakeReindexResult(
            reading_record_id=reading_record_id,
            status="reindex_enqueued",
        )


async def _seed_env(pool: Any) -> uuid.UUID:
    """Seed one record + active stable document; returns record id."""
    from tests.test_article_rag_index_worker import _seed_paragraph_environment

    await _seed_paragraph_environment(pool)
    from tests.test_article_rag_index_plan import _RECORD_ID

    return _RECORD_ID


async def _insert_run(
    pool: Any,
    *,
    record_id: uuid.UUID,
    stable_document_id: uuid.UUID,
    status: str,
    base_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    updated_at_minutes_ago: int = 0,
) -> uuid.UUID:
    from tests.test_article_rag_index_plan import _BASE_ID

    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO reader_article_rag_index_runs (
                id, reading_record_id, stable_document_id, base_id,
                record_generation, stable_document_content_sha256,
                canonical_text_sha256, plan_content_sha256, chunk_count,
                status, updated_at
            )
            VALUES (
                COALESCE($1, gen_random_uuid()), $2, $3, $4, 1,
                $5, $5, $5, 1, $6,
                NOW() - make_interval(mins => $7)
            )
            RETURNING id
            """,
            run_id,
            record_id,
            stable_document_id,
            base_id or _BASE_ID,
            "a" * 64,
            status,
            updated_at_minutes_ago,
        )


@pytest.fixture
async def reindex_db_env() -> Any:
    import asyncpg as _asyncpg

    from app.database.connection import init_connection
    from tests.test_reader_orchestration_schema_baseline import (
        BASELINE_SQL,
        DATABASE_URL,
    )

    schema_name = f"test_w11_reindex_{uuid.uuid4().hex}"
    admin_conn = await _asyncpg.connect(DATABASE_URL)
    pool: Any = None
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=4,
            init=init_connection,
            setup=lambda conn: conn.execute(
                f'SET search_path TO "{schema_name}", public'
            ),
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


@pytest.mark.asyncio
class TestLatestRunTruthDb:
    async def test_old_indexed_with_latest_queued_is_in_progress(
        self, reindex_db_env: Any
    ) -> None:
        """A record whose LATEST run is queued is in_progress even when
        an older run was indexed — never eligible."""
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        old = await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
            updated_at_minutes_ago=10,
        )
        # A queued build cannot coexist with an active indexed run
        # (uq_reader_article_rag_index_runs_active) — supersede the old
        # run first, then enqueue the new build.
        async with reindex_db_env.acquire() as conn:
            await conn.execute(
                "UPDATE reader_article_rag_index_runs SET status='superseded' WHERE id=$1",
                old,
            )
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="queued",
        )

        # Single-record dry-run classifies the latest run as in_progress.
        single = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=[record_id], all_records=False, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )
        assert single["scanned"] == 1
        assert single["eligible"] == 0
        assert single["in_progress"] == 1

        # --all candidate list excludes the in-progress record entirely.
        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )
        assert summary["scanned"] == 0
        assert summary["eligible"] == 0

    async def test_latest_failed_is_recovery_eligible(
        self, reindex_db_env: Any
    ) -> None:
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
            updated_at_minutes_ago=10,
        )
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="failed",
        )

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )

        assert summary["scanned"] == 1
        assert summary["eligible"] == 1
        assert summary["in_progress"] == 0

    async def test_latest_superseded_is_recovery_eligible(
        self, reindex_db_env: Any
    ) -> None:
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
            updated_at_minutes_ago=10,
        )
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="superseded",
        )

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )

        assert summary["scanned"] == 1
        assert summary["eligible"] == 1

    async def test_old_indexed_on_inactive_stable_document_ignored(
        self, reindex_db_env: Any
    ) -> None:
        """Only the record's ACTIVE stable document's latest run counts."""
        from tests.test_article_rag_index_plan import _RECORD_ID, _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        # A second (superseded) stable document carrying an indexed run.
        async with reindex_db_env.acquire() as conn:
            stale_sd = await conn.fetchval(
                """
                INSERT INTO stable_reading_documents (
                    reading_record_id, record_generation, title,
                    document_version, content_sha256, status
                )
                VALUES ($1, 2, 'stale', 2,
                        encode(digest('stale', 'sha256'), 'hex'), 'superseded')
                RETURNING id
                """,
                _RECORD_ID,
            )
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=stale_sd, status="indexed",
            updated_at_minutes_ago=5,
        )
        # Active stable document has a queued run -> in_progress.
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="queued",
            updated_at_minutes_ago=5,
        )

        single = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=[record_id], all_records=False, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )
        assert single["eligible"] == 0
        assert single["in_progress"] == 1

        # --all candidates are drawn from the ACTIVE stable document
        # only: the stale indexed run does not select the record.
        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )
        assert summary["scanned"] == 0
        assert summary["eligible"] == 0

    async def test_deleted_record_not_selected(self, reindex_db_env: Any) -> None:
        from tests.test_article_rag_index_plan import (
            _RECORD_ID,
            _STABLE_DOC_ID,
        )

        record_id = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
        )
        async with reindex_db_env.acquire() as conn:
            await conn.execute(
                "UPDATE reading_records SET deleted_at = NOW() WHERE id=$1",
                _RECORD_ID,
            )

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )

        assert summary["scanned"] == 0
        assert summary["eligible"] == 0

    async def test_no_run_record_is_not_eligible(self, reindex_db_env: Any) -> None:
        record_id = await _seed_env(reindex_db_env)
        del record_id

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )

        assert summary["scanned"] == 0
        assert summary["eligible"] == 0
        assert summary["in_progress"] == 0

    async def test_id_tie_break_is_deterministic(self, reindex_db_env: Any) -> None:
        """Identical updated_at resolves by descending id."""
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="failed",
            run_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            updated_at_minutes_ago=5,
        )
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
            run_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            updated_at_minutes_ago=5,
        )

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )

        # Highest id wins -> indexed -> eligible.
        assert summary["eligible"] == 1

    async def test_limit_applies_after_stable_order(
        self, reindex_db_env: Any
    ) -> None:
        """--limit caps the stably-ordered candidate list."""
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        first = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=first,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
        )

        async with reindex_db_env.acquire() as conn:
            second_record = await conn.fetchval(
                "INSERT INTO reading_records (user_id, source_type) "
                "VALUES ((SELECT id FROM users LIMIT 1), 'text') RETURNING id"
            )
            second_sd = await conn.fetchval(
                """
                INSERT INTO stable_reading_documents (
                    reading_record_id, record_generation, title,
                    document_version, content_sha256, status
                )
                VALUES ($1, 1, 'second', 1,
                        encode(digest('second', 'sha256'), 'hex'), 'active')
                RETURNING id
                """,
                second_record,
            )
            base_id = await conn.fetchval(
                """
                INSERT INTO reading_bases (
                    reading_record_id, base_version, record_generation, text,
                    content_sha256, content_utf16_length,
                    canonicalizer_version, builder_version, segmenter_version,
                    status
                )
                VALUES ($1, 1, 1, 'second body',
                        encode(digest('second body', 'sha256'), 'hex'),
                        utf16_code_unit_length('second body'),
                        'c', 'b', 's', 'active')
                RETURNING id
                """,
                second_record,
            )
        await _insert_run(
            reindex_db_env, record_id=second_record,
            stable_document_id=second_sd, status="indexed",
            base_id=base_id,
        )

        summary = await run_reindex(
            pool=reindex_db_env, service=_FakeServiceProbe(),
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=1,
        )

        assert summary["scanned"] == 1
        assert summary["eligible"] == 1

    async def test_dry_run_and_execute_share_candidate_semantics(
        self, reindex_db_env: Any
    ) -> None:
        """Execute mode consumes the SAME latest-run candidate list."""
        from tests.test_article_rag_index_plan import _STABLE_DOC_ID

        record_id = await _seed_env(reindex_db_env)
        await _insert_run(
            reindex_db_env, record_id=record_id,
            stable_document_id=_STABLE_DOC_ID, status="indexed",
        )

        probe = _FakeServiceProbe()
        dry = await run_reindex(
            pool=reindex_db_env, service=probe,
            record_ids=None, all_records=True, execute=False,
            rate_limit_per_second=0.0, limit=None,
        )
        executed = await run_reindex(
            pool=reindex_db_env, service=probe,
            record_ids=None, all_records=True, execute=True,
            rate_limit_per_second=0.0, limit=None,
        )

        assert dry["eligible"] == 1
        assert executed["enqueued"] == 1
        assert probe.calls == [record_id]