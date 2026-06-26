from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.source_artifact_service import (
    SourceArtifactError,
    SourceArtifactRegistrationResult,
    SourceArtifactService,
)

_USER_ID = UUID("00000000-0000-0000-0000-000000000701")
_READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000702")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000703")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000704")
_NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)


class _RecordedCall:
    def __init__(
        self,
        kind: str,
        query: str,
        args: tuple[Any, ...],
        *,
        in_transaction: bool,
    ) -> None:
        self.kind = kind
        self.query = query
        self.args = args
        self.in_transaction = in_transaction


class _FakeTransaction:
    def __init__(self, conn: _FakeConn, log: list[str] | None = None) -> None:
        self._conn = conn
        self._log = log
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> None:
        self._conn._in_transaction = True
        if self._log is not None:
            self._log.append("transaction_started")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        self._conn._in_transaction = False
        if exc_type is None:
            self.committed = True
            if self._log is not None:
                self._log.append("transaction_committed")
        else:
            self.rolled_back = True
            if self._log is not None:
                self._log.append("transaction_rolled_back")
        return False


class _FakeConn:
    def __init__(
        self,
        *,
        log: list[str] | None = None,
        fail_on_query_substring: str | None = None,
        fail_exception: Exception | None = None,
    ) -> None:
        self.calls: list[_RecordedCall] = []
        self._in_transaction = False
        self._log = log
        self._last_transaction: _FakeTransaction | None = None
        self._fail_on_query_substring = fail_on_query_substring
        self._fail_exception = fail_exception or RuntimeError("db write failed")

    def transaction(
        self,
        *,
        isolation: str | None = None,
        readonly: bool = False,
    ) -> _FakeTransaction:
        self._last_transaction = _FakeTransaction(self, log=self._log)
        return self._last_transaction

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(
            _RecordedCall(
                "execute",
                query,
                args,
                in_transaction=self._in_transaction,
            )
        )
        if self._log is not None and "INSERT INTO source_artifacts" in query:
            self._log.append("insert_source_artifact")
        if (
            self._fail_on_query_substring is not None
            and self._fail_on_query_substring in query
        ):
            raise self._fail_exception
        return "INSERT 0 1"

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    @property
    def execute_calls(self) -> list[_RecordedCall]:
        return [call for call in self.calls if call.kind == "execute"]


class _FakePoolAcquireContext:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


class FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePoolAcquireContext:
        return _FakePoolAcquireContext(self._conn)


def _build_service(conn: _FakeConn) -> SourceArtifactService:
    return SourceArtifactService(
        pool=FakePool(conn),
        oss_bucket="claread-dev",
        oss_endpoint="https://oss-cn-shenzhen.aliyuncs.com",
    )


def _register(
    service: SourceArtifactService,
    *,
    reading_record_id: UUID | None = _READING_RECORD_ID,
    original_input_id: UUID | None = _ORIGINAL_INPUT_ID,
    artifact_id: UUID = _ARTIFACT_ID,
    artifact_kind: str = "original_upload",
    storage_provider: str = "oss",
    bucket: str | None = None,
    object_key: str | None = None,
    endpoint: str | None = None,
    content_type: str | None = "application/pdf",
    byte_size: int | None = 1024,
    content_sha256: str | None = "a" * 64,
    source_filename: str | None = "report.pdf",
    status: str = "available",
    source_refs_json: Any = None,
    metadata_json: Any = None,
    quality_json: Any = None,
) -> SourceArtifactRegistrationResult:
    return asyncio.run(
        service.register_source_artifact(
            user_id=_USER_ID,
            reading_record_id=reading_record_id,
            original_input_id=original_input_id,
            artifact_id=artifact_id,
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            storage_provider=storage_provider,  # type: ignore[arg-type]
            bucket=bucket,
            object_key=object_key,
            endpoint=endpoint,
            content_type=content_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            source_filename=source_filename,
            status=status,  # type: ignore[arg-type]
            source_refs_json=source_refs_json,
            metadata_json=metadata_json,
            quality_json=quality_json,
            now=_NOW,
        )
    )


def _find_execute_call(conn: _FakeConn, fragment: str) -> _RecordedCall:
    for call in conn.execute_calls:
        if fragment in call.query:
            return call
    raise AssertionError(f"missing execute call containing {fragment!r}")


def test_register_original_upload_writes_one_source_artifacts_row() -> None:
    log: list[str] = []
    conn = _FakeConn(log=log)
    service = _build_service(conn)

    result = _register(
        service,
        source_refs_json={"page": 1},
        metadata_json={"origin": "upload"},
        quality_json={"score": 0.99},
    )

    assert result.artifact_id == _ARTIFACT_ID
    assert result.storage_provider == "oss"
    assert result.bucket == "claread-dev"
    assert result.artifact_kind == "original_upload"
    assert result.source_filename == "report.pdf"
    assert result.status == "available"

    assert log == [
        "transaction_started",
        "insert_source_artifact",
        "transaction_committed",
    ]
    assert conn._last_transaction is not None
    assert conn._last_transaction.committed is True
    assert all(call.in_transaction for call in conn.execute_calls)

    insert_call = _find_execute_call(conn, "INSERT INTO source_artifacts")
    assert insert_call.args[0] == _ARTIFACT_ID
    assert insert_call.args[1] == _READING_RECORD_ID
    assert insert_call.args[2] == _ORIGINAL_INPUT_ID
    assert insert_call.args[3] == _USER_ID
    assert insert_call.args[4] == "original_upload"
    assert insert_call.args[5] == "oss"
    assert insert_call.args[6] == "claread-dev"
    assert (
        insert_call.args[7]
        == f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf"
    )
    assert insert_call.args[8] == "https://oss-cn-shenzhen.aliyuncs.com"
    assert insert_call.args[9] == "application/pdf"
    assert insert_call.args[10] == 1024
    assert insert_call.args[11] == "a" * 64
    assert insert_call.args[12] == "report.pdf"
    assert insert_call.args[13] == "available"
    assert insert_call.args[14] == {"page": 1}
    assert insert_call.args[15] == {"origin": "upload"}
    assert insert_call.args[16] == {"score": 0.99}
    assert insert_call.args[17] == _NOW


def test_object_key_contains_expected_original_upload_path() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    object_key = service.build_object_key(
        user_id=_USER_ID,
        artifact_id=_ARTIFACT_ID,
        source_filename="report.pdf",
        artifact_kind="original_upload",
    )

    assert object_key == f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf"


def test_unsafe_filename_path_is_sanitized() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    result = _register(
        service,
        source_filename=r"..\nested/unsafe report.pdf",
    )

    assert result.object_key.endswith("/unsafe_report.pdf")
    assert "../" not in result.object_key
    assert "..\\" not in result.object_key
    assert result.source_filename == "unsafe_report.pdf"


def test_blank_filename_falls_back_to_artifact_bin() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    result = _register(
        service,
        source_filename="   ",
    )

    assert result.object_key.endswith("/artifact.bin")
    assert result.source_filename == "artifact.bin"


def test_invalid_content_sha256_fails_closed_without_writes() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(SourceArtifactError, match="content_sha256"):
        _register(
            service,
            content_sha256="not-a-valid-sha",
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is None


def test_negative_byte_size_fails_closed_without_writes() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(SourceArtifactError, match="byte_size"):
        _register(
            service,
            byte_size=-1,
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is None


def test_invalid_artifact_kind_fails_closed() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(SourceArtifactError, match="invalid artifact_kind"):
        _register(
            service,
            artifact_kind="unsupported_kind",
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is None


def test_json_fields_must_be_object_shaped() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(SourceArtifactError, match="source_refs_json"):
        _register(
            service,
            source_refs_json=["bad"],  # type: ignore[list-item]
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is None


def test_pre_record_upload_allows_null_reading_record_and_original_input() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    result = _register(
        service,
        reading_record_id=None,
        original_input_id=None,
    )

    insert_call = _find_execute_call(conn, "INSERT INTO source_artifacts")
    assert result.artifact_id == _ARTIFACT_ID
    assert insert_call.args[1] is None
    assert insert_call.args[2] is None


def test_local_provider_normalizes_bucket_and_endpoint_to_none() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    result = _register(
        service,
        storage_provider="local",
        bucket="should-be-cleared",
        endpoint="https://should-be-cleared.example.com",
    )

    insert_call = _find_execute_call(conn, "INSERT INTO source_artifacts")

    assert result.storage_provider == "local"
    assert result.bucket is None
    assert insert_call.args[5] == "local"
    assert insert_call.args[6] is None
    assert insert_call.args[8] is None


def test_db_error_rolls_back_and_wraps() -> None:
    conn = _FakeConn(
        fail_on_query_substring="INSERT INTO source_artifacts",
        fail_exception=RuntimeError("insert failed"),
    )
    service = _build_service(conn)

    with pytest.raises(SourceArtifactError) as excinfo:
        _register(service)

    assert "Failed to persist source artifact metadata" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_migration_declares_json_object_checks_and_active_unique_object_index() -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "migrations"
        / "0007_reader_source_artifacts.sql"
    )
    migration_sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE source_artifacts" in migration_sql
    assert "CHECK (jsonb_typeof(source_refs_json) = 'object')" in migration_sql
    assert "CHECK (jsonb_typeof(metadata_json) = 'object')" in migration_sql
    assert "CHECK (jsonb_typeof(quality_json) = 'object')" in migration_sql
    assert (
        "ON source_artifacts (storage_provider, COALESCE(bucket, ''), object_key)"
        in migration_sql
    )
    assert "WHERE deleted_at IS NULL" in migration_sql
