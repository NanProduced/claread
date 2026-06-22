from __future__ import annotations

import sys
from argparse import Namespace
from datetime import timedelta
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopCycleSummary,
    WorkerLoopCandidateRecord,
)
from scripts.run_reader_enhancement_worker import _parse_args, _run_once


class _CapturingLoopService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_once(
        self,
        *,
        batch_size: int,
        lease_owner_prefix: str,
        lease_duration: timedelta,
        max_ticks: int,
        max_jobs: int,
    ) -> ReaderEnhancementWorkerLoopCycleSummary:
        self.calls.append(
            {
                "batch_size": batch_size,
                "lease_owner_prefix": lease_owner_prefix,
                "lease_duration": lease_duration,
                "max_ticks": max_ticks,
                "max_jobs": max_jobs,
            }
        )
        return ReaderEnhancementWorkerLoopCycleSummary(
            recovered_stale_leases=0,
            scanned_candidate_count=0,
            processed_count=0,
            lock_skipped_count=0,
            candidates=(
                WorkerLoopCandidateRecord(
                    record_id=UUID("00000000-0000-0000-0000-000000000001"),
                    user_id=UUID("00000000-0000-0000-0000-000000000002"),
                    base_id=UUID("00000000-0000-0000-0000-000000000003"),
                    expected_generation=1,
                    runnable_job_count=0,
                    tracked_job_count=0,
                ),
            ),
            results=(),
        )


def test_parse_args_uses_settings_defaults_for_worker_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        reader_worker_batch_size=11,
        reader_worker_scan_interval_seconds=12,
        reader_worker_max_ticks=13,
        reader_worker_max_jobs=14,
        reader_worker_lease_duration_seconds=120,
        reader_worker_lease_owner_prefix="lease-default",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_reader_enhancement_worker.py", "--once"],
    )

    args = _parse_args(settings)

    assert args.once is True
    assert args.batch_size == 11
    assert args.scan_interval_seconds == 12
    assert args.max_ticks == 13
    assert args.max_jobs == 14
    assert args.lease_duration_seconds == 120
    assert args.lease_owner_prefix == "lease-default"


def test_parse_args_accepts_custom_lease_duration_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(reader_worker_lease_duration_seconds=120)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_reader_enhancement_worker.py",
            "--once",
            "--lease-duration-seconds",
            "95",
            "--lease-owner-prefix",
            "lease-custom",
        ],
    )

    args = _parse_args(settings)

    assert args.lease_duration_seconds == 95
    assert args.lease_owner_prefix == "lease-custom"


@pytest.mark.anyio
async def test_run_once_passes_lease_duration_seconds_as_timedelta() -> None:
    service = _CapturingLoopService()
    args = Namespace(
        batch_size=5,
        lease_owner_prefix="lease-timedelta",
        lease_duration_seconds=90,
        max_ticks=7,
        max_jobs=8,
    )

    await _run_once(service=service, args=args)

    assert len(service.calls) == 1
    assert service.calls[0]["lease_duration"] == timedelta(seconds=90)
    assert service.calls[0]["batch_size"] == 5
    assert service.calls[0]["max_ticks"] == 7
    assert service.calls[0]["max_jobs"] == 8
