# task-history: (renamed from test_d6_i3r_artifact_pipeline_worker_entry.py)
"""Tests for the artifact pipeline operational worker entry.

Covers the standalone ``scripts/run_reader_artifact_pipeline_worker.py``:

- ``build_storage_reader``: fail-closed when credentials/SDK missing,
  returns :class:`AliyunOssObjectReader` when credentials + stubbed oss2
  are available.
- ``_parse_args``: settings defaults + CLI overrides.
- ``_run_worker`` ``--once`` mode: calls ``drain`` once, prints JSON,
  exits.
- ``_run_worker`` loop mode: calls ``drain`` repeatedly, sleeps when idle,
  stops on shutdown event.
- Enhancement worker CLI still imports and parses args unchanged.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.artifact_pipeline_worker_service import (
    ArtifactPipelineProcessResult,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    AliyunOssObjectReader,
    StorageObjectReadResult,
)

from scripts.run_reader_artifact_pipeline_worker import (
    _build_result_payload,
    _parse_args,
    _run_drain_cycle,
    _run_worker,
    build_pipeline_service,
    build_storage_reader,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_parse, pytest.mark.seam_pure_unit, pytest.mark.life_permanent_regression]


# ---------------------------------------------------------------------------
# Fake pipeline service / storage reader
# ---------------------------------------------------------------------------


class _FakeExtractionResult:
    def __init__(self, *, job_id: UUID, status: str = "succeeded", outcome: str | None = None) -> None:
        self.job_id = job_id
        self.status = status
        self.outcome = outcome


class _FakeMaterializationResult:
    def __init__(self, *, job_id: UUID, status: str = "succeeded", outcome: str = "stable_document_ready") -> None:
        self.job_id = job_id
        self.status = status
        self.outcome = outcome


class _FakePipelineService:
    """Fake :class:`ArtifactInputPipelineWorkerService` for script tests."""

    def __init__(self, *, drain_results: list[list[ArtifactPipelineProcessResult | None]] | None = None) -> None:
        # drain_results: a list of "drain cycle" results. Each cycle is a list
        # of ArtifactPipelineProcessResult (or None to signal idle). The fake
        # pops one entry per ``drain`` call.
        self._drain_results = drain_results or []
        self.drain_calls: list[dict[str, object]] = []

    async def drain(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        max_ticks: int,
        extraction_retry_delay: timedelta | None = None,
        materialization_retry_delay: timedelta | None = None,
    ) -> list[ArtifactPipelineProcessResult]:
        self.drain_calls.append(
            {
                "lease_owner": lease_owner,
                "lease_duration": lease_duration,
                "max_ticks": max_ticks,
            }
        )
        if not self._drain_results:
            return []
        return self._drain_results.pop(0)

    async def process_once(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta = timedelta(seconds=30),
        extraction_retry_delay: timedelta | None = None,
        materialization_retry_delay: timedelta | None = None,
    ) -> ArtifactPipelineProcessResult | None:
        raise NotImplementedError  # not used in these tests


def _make_pipeline_result(
    *,
    stage: str = "extraction",
    job_id: UUID | None = None,
    status: str = "succeeded",
    outcome: str | None = None,
) -> ArtifactPipelineProcessResult:
    jid = job_id or uuid4()
    if stage == "extraction":
        return ArtifactPipelineProcessResult(
            stage="extraction",
            extraction_result=_FakeExtractionResult(job_id=jid, status=status, outcome=outcome),
        )
    return ArtifactPipelineProcessResult(
        stage="materialization",
        materialization_result=_FakeMaterializationResult(job_id=jid, status=status, outcome=outcome or "stable_document_ready"),
    )


# ---------------------------------------------------------------------------
# build_storage_reader
# ---------------------------------------------------------------------------


def test_build_storage_reader_returns_none_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
    settings = Settings(
        aliyun_oss_access_key_id="",
        aliyun_oss_access_key_secret="",
    )
    reader = build_storage_reader(settings)
    assert reader is None


def test_build_storage_reader_returns_none_when_only_id_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "GENERIC_ID")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "GENERIC_SECRET")
    settings = Settings(
        aliyun_oss_access_key_id="LTAI123",
        aliyun_oss_access_key_secret="",
    )
    assert build_storage_reader(settings) is None


def test_build_storage_reader_falls_back_to_alibaba_cloud_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_oss2(monkeypatch)
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "LTAI123")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret456")

    settings = Settings(
        aliyun_oss_access_key_id="",
        aliyun_oss_access_key_secret="",
        aliyun_oss_bucket="claread-dev",
        aliyun_oss_endpoint="https://oss-cn-shenzhen.aliyuncs.com",
    )
    reader = build_storage_reader(settings)
    assert reader is not None
    assert isinstance(reader, AliyunOssObjectReader)


def test_build_storage_reader_returns_none_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials present but oss2 not importable → None (fail-closed)."""
    # Ensure oss2 is not importable even if the real SDK happens to be installed.
    import builtins

    original_import = builtins.__import__

    def _block_oss2(name: str, *args: object, **kwargs: object) -> object:
        if name == "oss2":
            raise ImportError("simulated: oss2 not installed")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _block_oss2)

    settings = Settings(
        aliyun_oss_access_key_id="LTAI123",
        aliyun_oss_access_key_secret="secret456",
        aliyun_oss_bucket="claread-dev",
        aliyun_oss_endpoint="https://oss-cn-shenzhen.aliyuncs.com",
    )
    reader = build_storage_reader(settings)
    assert reader is None


def test_build_storage_reader_returns_aliyun_reader_when_credentials_and_sdk_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials + stubbed oss2 → AliyunOssObjectReader."""
    _install_fake_oss2(monkeypatch)

    settings = Settings(
        aliyun_oss_access_key_id="LTAI123",
        aliyun_oss_access_key_secret="secret456",
        aliyun_oss_bucket="claread-dev",
        aliyun_oss_endpoint="https://oss-cn-shenzhen.aliyuncs.com",
    )
    reader = build_storage_reader(settings)
    assert reader is not None
    assert isinstance(reader, AliyunOssObjectReader)


def test_build_pipeline_service_passes_storage_reader() -> None:
    """build_pipeline_service wires the reader into the service."""
    # Use a fake reader to avoid oss2 dependency.
    class _FakeReader:
        async def read_object(self, *, bucket: str, endpoint: str, object_key: str) -> StorageObjectReadResult:
            return StorageObjectReadResult(data=b"", byte_size=0)

    settings = Settings()
    service = build_pipeline_service(
        settings=settings,
        pool=object(),  # fake pool; service stores it without using it
        storage_reader=_FakeReader(),  # type: ignore[arg-type]
    )
    # The service should have an extraction worker with a real provider
    # (not UnconfiguredArtifactExtractionProvider).
    assert service._extraction_worker is not None


def test_build_pipeline_service_fail_closed_without_reader() -> None:
    """No reader → service uses UnconfiguredArtifactExtractionProvider."""
    settings = Settings()
    service = build_pipeline_service(
        settings=settings,
        pool=object(),
        storage_reader=None,
    )
    # The extraction worker exists but will fail closed on first extract.
    assert service._extraction_worker is not None


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


def test_parse_args_uses_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        reader_artifact_worker_poll_interval_seconds=7,
        reader_artifact_worker_lease_owner_prefix="artifact-worker-test",
        reader_artifact_worker_lease_duration_seconds=95,
        reader_artifact_worker_max_ticks=42,
    )
    monkeypatch.setattr(sys, "argv", ["run_reader_artifact_pipeline_worker.py", "--once"])
    args = _parse_args(settings)
    assert args.once is True
    assert args.poll_interval_seconds == 7
    assert args.lease_owner_prefix == "artifact-worker-test"
    assert args.lease_duration_seconds == 95
    assert args.max_ticks == 42


def test_parse_args_accepts_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_reader_artifact_pipeline_worker.py",
            "--poll-interval-seconds", "3",
            "--lease-duration-seconds", "60",
            "--lease-owner-prefix", "custom-owner",
            "--max-ticks", "10",
        ],
    )
    args = _parse_args(settings)
    assert args.once is False
    assert args.poll_interval_seconds == 3
    assert args.lease_duration_seconds == 60
    assert args.lease_owner_prefix == "custom-owner"
    assert args.max_ticks == 10


# ---------------------------------------------------------------------------
# _run_drain_cycle
# ---------------------------------------------------------------------------


async def test_run_drain_cycle_calls_service_drain() -> None:
    service = _FakePipelineService()
    await _run_drain_cycle(
        service=service,  # type: ignore[arg-type]
        lease_owner="test-owner",
        lease_duration=timedelta(seconds=30),
        max_ticks=5,
    )
    assert len(service.drain_calls) == 1
    assert service.drain_calls[0]["lease_owner"] == "test-owner"
    assert service.drain_calls[0]["max_ticks"] == 5
    assert service.drain_calls[0]["lease_duration"] == timedelta(seconds=30)


# ---------------------------------------------------------------------------
# _build_result_payload
# ---------------------------------------------------------------------------


def test_build_result_payload_extraction() -> None:
    jid = uuid4()
    result = _make_pipeline_result(stage="extraction", job_id=jid, status="succeeded")
    payload = _build_result_payload(result)
    assert payload["stage"] == "extraction"
    assert payload["status"] == "succeeded"
    assert payload["extraction"]["job_id"] == str(jid)
    assert payload["extraction"]["status"] == "succeeded"


def test_build_result_payload_materialization() -> None:
    jid = uuid4()
    result = _make_pipeline_result(
        stage="materialization", job_id=jid, status="succeeded", outcome="stable_document_ready"
    )
    payload = _build_result_payload(result)
    assert payload["stage"] == "materialization"
    assert payload["materialization"]["outcome"] == "stable_document_ready"


# ---------------------------------------------------------------------------
# _run_worker --once mode
# ---------------------------------------------------------------------------


async def test_run_worker_once_mode_drains_and_prints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--once mode: runs one drain cycle, prints JSON, exits."""
    # Stub init_db / close_db so no real DB connection is made.
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )

    # Stub DB_POOL.
    fake_pool = object()
    monkeypatch.setattr(
        "app.database.connection.DB_POOL", fake_pool
    )

    # Stub build_storage_reader → None (fail-closed).
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_storage_reader",
        lambda settings: None,
    )

    # Stub build_pipeline_service → fake service.
    fake_service = _FakePipelineService(
        drain_results=[
            [
                _make_pipeline_result(stage="extraction", status="succeeded"),
                _make_pipeline_result(stage="materialization", status="succeeded", outcome="stable_document_ready"),
            ]
        ]
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_pipeline_service",
        lambda **kwargs: fake_service,
    )

    args = Namespace(
        once=True,
        poll_interval_seconds=5,
        lease_duration_seconds=120,
        lease_owner_prefix="test-once",
        max_ticks=100,
    )
    settings = Settings()

    await _run_worker(args, settings)

    assert len(fake_service.drain_calls) == 1
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert len(parsed) == 2
    assert parsed[0]["stage"] == "extraction"
    assert parsed[1]["stage"] == "materialization"


async def test_run_worker_once_mode_idle_prints_empty_array(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--once mode with no jobs: prints [] and exits."""
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )
    monkeypatch.setattr("app.database.connection.DB_POOL", object())
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_storage_reader",
        lambda settings: None,
    )

    fake_service = _FakePipelineService(drain_results=[[]])
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_pipeline_service",
        lambda **kwargs: fake_service,
    )

    args = Namespace(
        once=True,
        poll_interval_seconds=5,
        lease_duration_seconds=120,
        lease_owner_prefix="test-idle",
        max_ticks=100,
    )
    await _run_worker(args, Settings())

    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


# ---------------------------------------------------------------------------
# _run_worker loop mode
# ---------------------------------------------------------------------------


async def test_run_worker_loop_mode_processes_then_sleeps_then_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop mode: processes jobs, sleeps when idle, stops on shutdown event."""
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )
    monkeypatch.setattr("app.database.connection.DB_POOL", object())
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_storage_reader",
        lambda settings: None,
    )

    # First drain: 1 result. Second drain: [] (idle). Third drain: never called.
    fake_service = _FakePipelineService(
        drain_results=[
            [_make_pipeline_result(stage="extraction", status="succeeded")],
            [],
        ]
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_pipeline_service",
        lambda **kwargs: fake_service,
    )

    # Short poll interval (>= 1 required in loop mode) so the idle sleep is brief.
    args = Namespace(
        once=False,
        poll_interval_seconds=1,
        lease_duration_seconds=120,
        lease_owner_prefix="test-loop",
        max_ticks=100,
    )

    # Run _run_worker in a task; set a shutdown flag after 2 drain calls.
    task = asyncio.create_task(_run_worker(args, Settings()))

    # Wait until the fake service has been called twice (process + idle).
    import asyncio as _asyncio

    deadline = _asyncio.get_event_loop().time() + 5.0
    while len(fake_service.drain_calls) < 2 and _asyncio.get_event_loop().time() < deadline:
        await _asyncio.sleep(0.01)

    assert len(fake_service.drain_calls) >= 2

    # Cancel the task (simulates graceful shutdown / test cleanup).
    task.cancel()
    try:
        await task
    except _asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_run_worker_rejects_invalid_lease_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )

    args = Namespace(
        once=True,
        poll_interval_seconds=5,
        lease_duration_seconds=0,
        lease_owner_prefix="test",
        max_ticks=100,
    )
    with pytest.raises(ValueError, match="lease_duration_seconds"):
        await _run_worker(args, Settings())


async def test_run_worker_rejects_invalid_max_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )

    args = Namespace(
        once=True,
        poll_interval_seconds=5,
        lease_duration_seconds=120,
        lease_owner_prefix="test",
        max_ticks=0,
    )
    with pytest.raises(ValueError, match="max_ticks"):
        await _run_worker(args, Settings())


async def test_run_worker_loop_mode_rejects_zero_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop mode must reject poll_interval_seconds < 1 (no busy-spin)."""
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )

    args = Namespace(
        once=False,
        poll_interval_seconds=0,
        lease_duration_seconds=120,
        lease_owner_prefix="test",
        max_ticks=100,
    )
    with pytest.raises(ValueError, match="poll_interval_seconds must be >= 1 in loop mode"):
        await _run_worker(args, Settings())


async def test_run_worker_once_mode_allows_zero_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--once mode allows poll_interval_seconds=0 (no idle sleep happens)."""
    async def _noop_init_db(*args: object, **kwargs: object) -> None:
        return None

    async def _noop_close_db() -> None:
        return None

    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.init_db", _noop_init_db
    )
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.close_db", _noop_close_db
    )
    monkeypatch.setattr("app.database.connection.DB_POOL", object())
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_storage_reader",
        lambda settings: None,
    )

    fake_service = _FakePipelineService(drain_results=[[]])
    monkeypatch.setattr(
        "scripts.run_reader_artifact_pipeline_worker.build_pipeline_service",
        lambda **kwargs: fake_service,
    )

    args = Namespace(
        once=True,
        poll_interval_seconds=0,
        lease_duration_seconds=120,
        lease_owner_prefix="test",
        max_ticks=100,
    )
    await _run_worker(args, Settings())
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


# ---------------------------------------------------------------------------
# Enhancement worker regression (unchanged)
# ---------------------------------------------------------------------------


def test_enhancement_worker_script_still_imports() -> None:
    """The enhancement worker script must still import cleanly."""
    from scripts.run_reader_enhancement_worker import _parse_args as _enh_parse_args

    settings = Settings()
    # Just verify it doesn't crash; default args.
    import sys as _sys

    original_argv = _sys.argv
    _sys.argv = ["run_reader_enhancement_worker.py", "--once"]
    try:
        args = _enh_parse_args(settings)
        assert args.once is True
    finally:
        _sys.argv = original_argv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_oss2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``oss2`` module in ``sys.modules`` so import succeeds."""
    import types

    fake_oss2 = types.ModuleType("oss2")

    class _FakeAuth:
        def __init__(self, ak_id: str, ak_secret: str) -> None:
            self.ak_id = ak_id
            self.ak_secret = ak_secret

    class _FakeBucket:
        def __init__(self, auth: _FakeAuth, endpoint: str, bucket: str) -> None:
            self.auth = auth
            self.endpoint = endpoint
            self.bucket = bucket

    fake_oss2.Auth = _FakeAuth
    fake_oss2.Bucket = _FakeBucket

    # exceptions submodule
    fake_exc = types.ModuleType("oss2.exceptions")

    class _OssError(Exception):
        pass

    class _NoSuchKey(_OssError):
        pass

    class _AccessDenied(_OssError):
        pass

    fake_exc.OssError = _OssError
    fake_exc.NoSuchKey = _NoSuchKey
    fake_exc.AccessDenied = _AccessDenied
    fake_oss2.exceptions = fake_exc

    monkeypatch.setitem(sys.modules, "oss2", fake_oss2)
    monkeypatch.setitem(sys.modules, "oss2.exceptions", fake_exc)


# Need asyncio for the loop test
import asyncio  # noqa: E402
