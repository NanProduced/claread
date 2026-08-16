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
        if "stable_reading_documents" in s:
            if args:
                key = args[0]
                return [
                    r
                    for r in self.dry_run_rows
                    if r["reading_record_id"] == key
                ]
            return list(self.dry_run_rows)
        if "distinct" in s:
            return list(self.candidate_rows)
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
