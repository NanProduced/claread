from __future__ import annotations

import logging
import sys
from argparse import Namespace
from datetime import timedelta
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.automatic_recovery import (
    AutomaticRecoveryScanSummary,
)
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopCycleSummary,
    WorkerLoopCandidateRecord,
)
from scripts.run_reader_enhancement_worker import (
    _build_once_payload,
    _parse_args,
    _run_cycle,
    _run_once,
)


class _CapturingLoopService:
    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._order = order

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
        if self._order is not None:
            self._order.append("enhancement")
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


class _FakeRecoveryService:
    def __init__(
        self,
        *,
        summary: AutomaticRecoveryScanSummary | None = None,
        exc: Exception | None = None,
        order: list[str] | None = None,
    ) -> None:
        self.calls: list[int] = []
        self._summary = summary
        self._exc = exc
        self._order = order

    async def run_once(self, *, batch_size: int) -> AutomaticRecoveryScanSummary:
        self.calls.append(batch_size)
        if self._order is not None:
            self._order.append("automatic_recovery")
        if self._exc is not None:
            raise self._exc
        assert self._summary is not None
        return self._summary


def _make_recovery_summary(**overrides: int) -> AutomaticRecoveryScanSummary:
    counts = {
        "recovered_count": 0,
        "noop_count": 0,
        "skipped_count": 0,
        "error_count": 0,
    }
    counts.update(overrides)
    return AutomaticRecoveryScanSummary(
        batch_size=5,
        results=(),
        recovered_count=counts["recovered_count"],
        noop_count=counts["noop_count"],
        skipped_count=counts["skipped_count"],
        error_count=counts["error_count"],
    )


def _make_args() -> Namespace:
    return Namespace(
        batch_size=5,
        lease_owner_prefix="lease-cycle",
        lease_duration_seconds=90,
        max_ticks=7,
        max_jobs=8,
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


# ---------------------------------------------------------------------------
# Automatic recovery cycle wiring (fake services, zero DB)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cycle_runs_automatic_recovery_strictly_before_enhancement() -> None:
    order: list[str] = []
    service = _CapturingLoopService(order=order)
    recovery_service = _FakeRecoveryService(
        summary=_make_recovery_summary(), order=order
    )

    await _run_cycle(service=service, recovery_service=recovery_service, args=_make_args())

    assert order == ["automatic_recovery", "enhancement"]


@pytest.mark.anyio
async def test_cycle_reuses_single_batch_size_for_both_services() -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(summary=_make_recovery_summary())

    await _run_cycle(service=service, recovery_service=recovery_service, args=_make_args())

    assert recovery_service.calls == [5]
    assert service.calls[0]["batch_size"] == 5


@pytest.mark.anyio
async def test_recovered_successors_still_processed_in_same_cycle() -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(
        summary=_make_recovery_summary(recovered_count=1)
    )

    summary, stats = await _run_cycle(
        service=service, recovery_service=recovery_service, args=_make_args()
    )

    # Enhancement still ran exactly once in the same cycle after recovery.
    assert len(service.calls) == 1
    assert stats["status"] == "completed"
    assert stats["recovered_count"] == 1
    assert summary.scanned_candidate_count == 0


@pytest.mark.anyio
async def test_candidate_errors_emit_single_sanitized_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(
        summary=_make_recovery_summary(recovered_count=1, error_count=2)
    )

    with caplog.at_level(logging.INFO):
        summary, stats = await _run_cycle(
            service=service, recovery_service=recovery_service, args=_make_args()
        )

    # Enhancement continues despite scanner candidate errors.
    assert len(service.calls) == 1
    assert stats["status"] == "completed"
    assert stats["error_count"] == 2

    alerts = [
        record
        for record in caplog.records
        if record.message == "reader_automatic_recovery_alert"
    ]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.levelno == logging.ERROR
    assert alert.event_schema == "reader_automatic_recovery_alert_v1"
    assert alert.alert_kind == "candidate_errors"
    assert alert.error_count == 2
    assert alert.batch_size == 5
    assert alert.recovered_count == 1
    assert alert.noop_count == 0
    assert alert.skipped_count == 0
    assert summary is not None


@pytest.mark.anyio
async def test_scan_failure_alert_is_sanitized_and_enhancement_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(
        exc=RuntimeError(
            "probe-secret-9f2a password=hunter2 Traceback (most recent call last)"
        )
    )

    with caplog.at_level(logging.INFO):
        summary, stats = await _run_cycle(
            service=service, recovery_service=recovery_service, args=_make_args()
        )

    # Enhancement cycle still executed after the scanner blew up.
    assert len(service.calls) == 1
    assert stats["status"] == "error"
    assert stats["error_count"] == 1

    alerts = [
        record
        for record in caplog.records
        if record.message == "reader_automatic_recovery_alert"
    ]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.levelno == logging.ERROR
    assert alert.alert_kind == "scan_failed"
    assert alert.error_count == 1
    assert alert.batch_size == 5
    # No exception body/type/traceback anywhere in logs.
    assert "probe-secret-9f2a" not in caplog.text
    assert "hunter2" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "RuntimeError" not in caplog.text
    assert summary is not None


@pytest.mark.anyio
async def test_clean_cycle_emits_no_error_alert(caplog: pytest.LogCaptureFixture) -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(
        summary=_make_recovery_summary(noop_count=1, skipped_count=1)
    )

    with caplog.at_level(logging.INFO):
        stats = (await _run_cycle(
            service=service, recovery_service=recovery_service, args=_make_args()
        ))[1]

    assert stats["status"] == "completed"
    assert stats["error_count"] == 0
    assert not [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert any(
        record.message == "reader automatic recovery cycle completed"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_once_payload_contains_sanitized_automatic_recovery_stats() -> None:
    service = _CapturingLoopService()
    recovery_service = _FakeRecoveryService(
        summary=_make_recovery_summary(recovered_count=1, skipped_count=1)
    )

    summary, stats = await _run_cycle(
        service=service, recovery_service=recovery_service, args=_make_args()
    )
    payload = _build_once_payload(summary, stats)

    assert payload["automatic_recovery"] == {
        "status": "completed",
        "batch_size": 5,
        "recovered_count": 1,
        "noop_count": 0,
        "skipped_count": 1,
        "error_count": 0,
    }
    # Counts only: the recovery stats carry no internal identifiers.
    assert set(payload["automatic_recovery"]) == {
        "status",
        "batch_size",
        "recovered_count",
        "noop_count",
        "skipped_count",
        "error_count",
    }
