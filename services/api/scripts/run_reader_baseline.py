"""Reader baseline CLI.

Usage examples (run from the repo root):

    # List available samples.
    python services/api/scripts/run_reader_baseline.py --samples list

    # Run the Reader orchestration chain on every golden sample using
    # the dev smoke harness (deterministic fake executors); no LLM
    # calls are made.
    python services/api/scripts/run_reader_baseline.py \\
        --samples all --executor-mode fake --allow-fake-executors

    # Run new chain on a single sample with the real LLM profile.
    python services/api/scripts/run_reader_baseline.py \\
        --samples short_news --executor-mode real

    # Override the reading metadata for one run.
    python services/api/scripts/run_reader_baseline.py \\
        --samples reuters_bbc_970 --executor-mode fake \\
        --allow-fake-executors --reading-goal exam \\
        --reading-variant ielts_toefl

Exit codes:

- ``0`` -- every sample completed successfully.
- ``2`` -- at least one sample is ``incomplete`` or the new chain
  raised an exception.

Outputs are written under
``verification/reader_baseline/runs/<UTC-timestamp>/<sample_id>.{json,md}``.
The directory is created if it does not exist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID

import asyncpg

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[3]
API_ROOT = THIS_FILE.parents[1]
sys.path.insert(0, str(API_ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.database import connection as db_connection  # noqa: E402
from app.database.connection import close_db  # noqa: E402

from verification.reader_baseline import (  # noqa: E402
    cli_helpers,
    golden_samples,
    new_chain,
    report,
    schema_setup,
)
from verification.reader_baseline.golden_samples import GoldenSample  # noqa: E402
from verification.reader_baseline.report import ComparisonReport  # noqa: E402

EXIT_OK = 0
EXIT_INCOMPLETE = 2


@dataclass(frozen=True, slots=True)
class CliArgs:
    samples: tuple[str, ...]
    executor_mode: str
    allow_fake_executors: bool
    output_root: Path
    max_ticks: int
    max_jobs: int
    keep_schema: bool
    schema_name: str | None
    reading_goal: str | None
    reading_variant: str | None


def _coerce_schema_name(value: str | None) -> str | None:
    """Argparse ``type=`` callback for ``--schema-name``.

    Re-uses the safety check the harness runs at runtime, so a
    rejected name fails the CLI *before* it ever opens a database
    connection. Returns the name unchanged on success, ``None`` if
    the caller did not pass one.
    """
    if value is None:
        return None
    schema_setup.validate_schema_name(value)
    return value


def _parse_args() -> CliArgs:
    parser = argparse.ArgumentParser(
        description=(
            "Run the new Reader orchestration chain on the fixed "
            "golden sample set and emit a structured observation "
            "report per sample."
        )
    )
    parser.add_argument(
        "--samples",
        nargs="+",
        required=True,
        help=(
            "Sample id(s) to run, or 'all'. Use "
            "'list' to print the available ids and exit."
        ),
    )
    parser.add_argument(
        "--executor-mode",
        choices=("fake", "real"),
        default="fake",
        help=(
            "Executor mode for the new chain. 'fake' uses the "
            "dev-only deterministic executors; 'real' uses the "
            "configured model profile."
        ),
    )
    parser.add_argument(
        "--allow-fake-executors",
        action="store_true",
        help="Required when --executor-mode=fake.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "verification" / "reader_baseline" / "runs",
        help="Directory to write the per-sample reports into.",
    )
    # T1 acceptance: aligned with DEFAULT_PIPELINE_MAX_TICKS / MAX_JOBS so
    # the CLI completes medium samples (reuters_bbc_970 needs 60 ticks in
    # 6-worker fake mode) without an explicit --max-ticks override.
    parser.add_argument("--max-ticks", type=int, default=96)
    parser.add_argument("--max-jobs", type=int, default=48)
    parser.add_argument(
        "--keep-schema",
        action="store_true",
        help=(
            "Keep the isolated baseline schema after the run. By "
            "default the schema is dropped on exit so the dev DB "
            "stays clean."
        ),
    )
    parser.add_argument(
        "--schema-name",
        default=None,
        type=_coerce_schema_name,
        help=(
            "Override the auto-generated isolated schema name. Must "
            "match the whitelist (see ``schema_setup.py``); unsafe "
            "names like 'public' are rejected before any DB work."
        ),
    )
    parser.add_argument(
        "--reading-goal",
        default=None,
        help=(
            "Override the ``reading_goal`` resolved from the sample "
            "manifest. The chain receives the same value."
        ),
    )
    parser.add_argument(
        "--reading-variant",
        default=None,
        help=(
            "Override the ``reading_variant`` resolved from the "
            "sample manifest. The chain receives the same value."
        ),
    )
    raw = parser.parse_args()
    samples_arg = tuple(raw.samples)
    if samples_arg == ("list",):
        for sid in golden_samples.list_sample_ids():
            print(sid)
        sys.exit(0)
    if samples_arg == ("all",):
        samples = golden_samples.list_sample_ids()
    else:
        samples = samples_arg
    return CliArgs(
        samples=samples,
        executor_mode=raw.executor_mode,
        allow_fake_executors=raw.allow_fake_executors,
        output_root=raw.output_root,
        max_ticks=raw.max_ticks,
        max_jobs=raw.max_jobs,
        keep_schema=raw.keep_schema,
        schema_name=raw.schema_name,
        reading_goal=raw.reading_goal,
        reading_variant=raw.reading_variant,
    )


def _resolve_reading_metadata(
    *,
    sample: GoldenSample,
    args: CliArgs,
) -> tuple[str, str]:
    """Pick the (reading_goal, reading_variant) the chain runs with.

    Implementation lives in :mod:`verification.reader_baseline.cli_helpers`
    so the test suite can call it without loading this script via
    importlib. The wrapper exists so the CLI keeps a single
    call-site name and so a future refactor of the override shape
    stays local to this file.
    """
    return cli_helpers.resolve_reading_metadata(
        sample=sample,
        overrides=cli_helpers.ReadingMetadataOverrides(
            reading_goal=getattr(args, "reading_goal", None),
            reading_variant=getattr(args, "reading_variant", None),
        ),
    )


async def _run_new_chain(
    *,
    sample: GoldenSample,
    args: CliArgs,
    user_id: UUID,
    reading_goal: str,
    reading_variant: str,
    pool: asyncpg.Pool,
) -> new_chain.NewChainMetrics:
    """Run the new chain on a single sample and return its metrics.

    The ``pool`` is the same isolated baseline schema pool the smoke
    harness writes into. It is passed on to ``summarise`` so the
    metric extractor can read back the persisted
    ``reading_records.reading_goal`` / ``reading_variant`` and the
    ``ai_usage_events`` aggregates.

    The ``reading_goal`` / ``reading_variant`` resolved by
    :func:`cli_helpers.resolve_reading_metadata` are forwarded to
    the smoke harness so the persisted record reflects the same
    metadata the report shows.
    """
    from app.services.reader_orchestration.smoke_harness import (
        ReaderEnhancementSmokeHarness,
    )

    # The smoke harness reads its pool from the module-level
    # ``DB_POOL`` global, which is set by ``_run_all`` to the
    # isolated schema pool. We do not pass an explicit pool here
    # so the harness uses the global.
    harness = ReaderEnhancementSmokeHarness()
    result = await harness.prepare_record(
        user_id=user_id,
        plain_text=sample.plain_text,
        title=sample.sample_id,
        executor_mode=args.executor_mode,  # type: ignore[arg-type]
        allow_fake_executors=args.allow_fake_executors,
        max_ticks=args.max_ticks,
        max_jobs=args.max_jobs,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )
    return await new_chain.summarise_async(result=result, pool=pool)


async def _run_one_sample(
    *,
    sample: GoldenSample,
    args: CliArgs,
    user_id: UUID,
    pool: asyncpg.Pool,
) -> ComparisonReport:
    """Run a single sample through the new chain and return the report."""
    reading_goal, reading_variant = _resolve_reading_metadata(sample=sample, args=args)
    new_metrics = await _run_new_chain(
        sample=sample,
        args=args,
        user_id=user_id,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        pool=pool,
    )
    return report.build_report(
        sample=sample,
        new_metrics=new_metrics,
        notes="",
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )


def _write_report(
    *,
    out_dir: Path,
    report_obj: ComparisonReport,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report_obj.sample_id}.json"
    md_path = out_dir / f"{report_obj.sample_id}.md"
    json_path.write_text(report_obj.to_json(), encoding="utf-8")
    md_path.write_text(report_obj.to_markdown(), encoding="utf-8")
    return json_path, md_path


def _classify_status(
    *,
    completion_status: str,
    error: str | None,
) -> str:
    """Map (completion, new-chain error) -> ``status`` string."""
    if error is not None:
        return "failed"
    if completion_status != "complete":
        return "incomplete"
    return "ok"


def _summarise_status(summary: list[dict[str, object]]) -> int:
    """Roll the per-sample status list up to a single exit code."""
    statuses = {entry.get("status") for entry in summary}
    if "failed" in statuses or "incomplete" in statuses:
        return EXIT_INCOMPLETE
    return EXIT_OK


async def _run_all(args: CliArgs) -> int:
    """Run the baseline harness across the requested samples.

    The new chain runs inside an isolated PostgreSQL schema so the
    dev DB public schema is not touched. The schema is dropped on
    exit unless ``--keep-schema`` was passed.
    """
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_root / run_stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, object]] = []
    schema_ctx = schema_setup.isolated_schema(
        schema_name=args.schema_name,
        keep=args.keep_schema,
    )
    async with schema_ctx as (pool, user_id):
        # Make the smoke harness use our isolated pool instead of
        # the global DB_POOL that init_db() would otherwise set.
        previous_pool = db_connection.DB_POOL
        db_connection.DB_POOL = pool
        try:
            for sample_id in args.samples:
                sample = golden_samples.load_sample(sample_id)
                error: str | None = None
                try:
                    report_obj = await _run_one_sample(
                        sample=sample, args=args, user_id=user_id, pool=pool
                    )
                except Exception as exc:
                    # The new chain failing on a sample is a hard
                    # error; surface it and keep going so one bad
                    # sample does not abort the whole run.
                    error = f"{type(exc).__name__}: {exc}"
                    report_obj = None
                if report_obj is None:
                    summary.append(
                        {
                            "sample_id": sample.sample_id,
                            "status": "failed",
                            "error": error,
                        }
                    )
                    print(
                        f"FAILED {sample.sample_id}: {error}",
                        file=sys.stderr,
                    )
                    continue
                json_path, md_path = _write_report(
                    out_dir=out_dir, report_obj=report_obj
                )
                status = _classify_status(
                    completion_status=report_obj.completion_status,
                    error=None,
                )
                summary.append(
                    {
                        "sample_id": sample.sample_id,
                        "status": status,
                        "completion_status": report_obj.completion_status,
                        "json_path": str(json_path),
                        "md_path": str(md_path),
                        "new_layer_counts": report_obj.new_chain.get("layer_counts"),
                        "new_completion_reasons": report_obj.new_chain.get(
                            "completion_reasons"
                        ),
                        "new_outstanding_jobs": report_obj.new_chain.get(
                            "outstanding_jobs"
                        ),
                        "new_usage_source": (report_obj.new_chain.get("usage") or {}).get(
                            "source"
                        ),
                        "new_usage_event_count": (report_obj.new_chain.get("usage") or {}).get(
                            "event_count"
                        ),
                        "new_usage_total_tokens": (report_obj.new_chain.get("usage") or {}).get(
                            "total_tokens"
                        ),
                        "reading_goal": report_obj.reading_goal,
                        "reading_variant": report_obj.reading_variant,
                    }
                )
                marker = {
                    "ok": "OK  ",
                    "incomplete": "INC ",
                    "failed": "FAIL",
                }.get(status, "    ")
                print(
                    f"{marker} {sample.sample_id}: "
                    f"completion={report_obj.completion_status} "
                    f"layers={report_obj.new_chain.get('layer_counts')} "
                    f"-> {json_path.relative_to(REPO_ROOT)}"
                )
        finally:
            db_connection.DB_POOL = previous_pool
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nSummary: {summary_path}")
    return _summarise_status(summary)


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run_all(args))


if __name__ == "__main__":
    raise SystemExit(main())
