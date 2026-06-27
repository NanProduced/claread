from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.artifact_input_application_service import (
    ArtifactInputApplicationConflictError,
    ArtifactInputApplicationError,
    ArtifactInputApplicationNotFoundError,
    ArtifactInputApplicationResult,
    ArtifactInputApplicationService,
)

_USER_ID = UUID("00000000-0000-0000-0000-000000000a01")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000a02")
_READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000a03")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000a04")
_EXTRACTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000a05")
_EXTRACTION_JOB_ID = UUID("00000000-0000-0000-0000-000000000a06")
_NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)
_UNSET = object()


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
        source_artifact_row: Mapping[str, Any] | None | object = _UNSET,
        log: list[str] | None = None,
        fail_on_query_substring: str | None = None,
        fail_exception: Exception | None = None,
    ) -> None:
        self.calls: list[_RecordedCall] = []
        self._in_transaction = False
        self._source_artifact_row = source_artifact_row
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

    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None:
        self.calls.append(
            _RecordedCall(
                "fetchrow",
                query,
                args,
                in_transaction=self._in_transaction,
            )
        )
        if (
            self._fail_on_query_substring is not None
            and self._fail_on_query_substring in query
        ):
            raise self._fail_exception
        if self._log is not None and "FROM source_artifacts" in query:
            self._log.append("select_source_artifact")
        if self._log is not None and "INSERT INTO reader_runs" in query:
            self._log.append("insert_reader_run")
        if self._log is not None and "INSERT INTO reader_jobs" in query:
            self._log.append("insert_extraction_job")
        if "FROM source_artifacts" in query:
            if self._source_artifact_row is _UNSET:
                return _build_source_artifact_row()
            return self._source_artifact_row
        if "INSERT INTO reader_runs" in query:
            return {"id": _EXTRACTION_RUN_ID}
        if "INSERT INTO reader_jobs" in query:
            return {"id": _EXTRACTION_JOB_ID, "status": "queued"}
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(
            _RecordedCall(
                "execute",
                query,
                args,
                in_transaction=self._in_transaction,
            )
        )
        if self._log is not None and "INSERT INTO reading_records" in query:
            self._log.append("insert_reading_record")
        if self._log is not None and "INSERT INTO original_inputs" in query:
            self._log.append("insert_original_input")
        if self._log is not None and "UPDATE source_artifacts" in query:
            self._log.append("bind_source_artifact")
        if (
            self._fail_on_query_substring is not None
            and self._fail_on_query_substring in query
        ):
            raise self._fail_exception
        return "OK"

    @property
    def fetchrow_calls(self) -> list[_RecordedCall]:
        return [call for call in self.calls if call.kind == "fetchrow"]

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


def _build_service(conn: _FakeConn) -> ArtifactInputApplicationService:
    return ArtifactInputApplicationService(pool=FakePool(conn))


def _submit(
    service: ArtifactInputApplicationService,
    *,
    artifact_id: UUID = _ARTIFACT_ID,
    title: str | None = None,
    language: str | None = None,
    client_record_id: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> ArtifactInputApplicationResult:
    return asyncio.run(
        service.submit_available_artifact_as_input(
            user_id=_USER_ID,
            artifact_id=artifact_id,
            title=title,
            language=language,
            client_record_id=client_record_id,
            source_metadata=source_metadata,
            now=_NOW,
        )
    )


def _find_execute_call(conn: _FakeConn, fragment: str) -> _RecordedCall:
    for call in conn.execute_calls:
        if fragment in call.query:
            return call
    raise AssertionError(f"missing execute call containing {fragment!r}")


def _build_source_artifact_row(
    *,
    artifact_kind: str = "original_upload",
    storage_provider: str = "oss",
    status: str = "available",
    content_type: str | None = "application/pdf",
    byte_size: int | None = 4096,
    content_sha256: str | None = "a" * 64,
    source_filename: str | None = "report.pdf",
    bucket: str | None = "claread-dev",
    endpoint: str | None = "https://oss-cn-shenzhen.aliyuncs.com",
    object_key: str | None = f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf",
    reading_record_id: UUID | None = None,
    original_input_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": _ARTIFACT_ID,
        "artifact_kind": artifact_kind,
        "storage_provider": storage_provider,
        "bucket": bucket,
        "endpoint": endpoint,
        "object_key": object_key,
        "content_type": content_type,
        "byte_size": byte_size,
        "content_sha256": content_sha256,
        "source_filename": source_filename,
        "status": status,
        "reading_record_id": reading_record_id,
        "original_input_id": original_input_id,
    }


def test_submit_available_pdf_artifact_happy_path() -> None:
    log: list[str] = []
    conn = _FakeConn(log=log)
    service = _build_service(conn)

    result = _submit(
        service,
        title="Uploaded PDF",
        language="en",
        client_record_id="client-artifact-001",
        source_metadata={"origin": "ios"},
    )

    assert result.artifact_id == _ARTIFACT_ID
    assert result.source_type == "pdf"
    assert result.input_type == "file_ref"
    assert result.record_generation == 1
    assert result.product_state == "processing"
    assert result.readiness_state == "submitted"
    assert result.title == "Uploaded PDF"
    assert result.language == "en"
    assert result.bucket == "claread-dev"
    assert result.endpoint == "https://oss-cn-shenzhen.aliyuncs.com"
    assert result.object_key == f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf"
    assert result.content_sha256 == "a" * 64
    assert result.extraction_job_id == _EXTRACTION_JOB_ID
    assert result.extraction_job_status == "queued"
    assert log == [
        "transaction_started",
        "select_source_artifact",
        "insert_reading_record",
        "insert_original_input",
        "bind_source_artifact",
        "insert_reader_run",
        "insert_extraction_job",
        "transaction_committed",
    ]
    assert all(call.in_transaction for call in conn.calls)
    assert "FOR UPDATE" in conn.fetchrow_calls[0].query


def test_submit_available_image_artifact_derives_image_and_image_ref() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(content_type="image/png"),
    )
    service = _build_service(conn)

    result = _submit(service)

    assert result.source_type == "image"
    assert result.input_type == "image_ref"
    insert_original_input = _find_execute_call(conn, "INSERT INTO original_inputs")
    assert insert_original_input.args[3] == "image_ref"


def test_submit_available_unknown_content_type_derives_file_and_file_ref() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(content_type="application/zip"),
    )
    service = _build_service(conn)

    result = _submit(service)

    assert result.source_type == "file"
    assert result.input_type == "file_ref"
    insert_reading_record = _find_execute_call(conn, "INSERT INTO reading_records")
    assert insert_reading_record.args[3] == "file"


def test_title_fallback_uses_source_filename() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(source_filename="deck-final.pdf"),
    )
    service = _build_service(conn)

    result = _submit(service)

    assert result.title == "deck-final.pdf"
    insert_reading_record = _find_execute_call(conn, "INSERT INTO reading_records")
    assert insert_reading_record.args[4] == "deck-final.pdf"


def test_client_record_id_trim_and_empty_normalizes_to_none() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    _submit(service, client_record_id="   ")

    insert_reading_record = _find_execute_call(conn, "INSERT INTO reading_records")
    assert insert_reading_record.args[2] is None


def test_source_ref_json_contains_full_artifact_reference() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    _submit(service, source_metadata={"origin": "ios"})

    insert_original_input = _find_execute_call(conn, "INSERT INTO original_inputs")
    source_ref_json = insert_original_input.args[4]
    assert source_ref_json == {
        "artifact_id": str(_ARTIFACT_ID),
        "storage_provider": "oss",
        "bucket": "claread-dev",
        "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
        "object_key": f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf",
        "artifact_kind": "original_upload",
        "content_type": "application/pdf",
        "byte_size": 4096,
        "content_sha256": "a" * 64,
        "source_filename": "report.pdf",
    }
    assert insert_original_input.args[5] == {
        "origin": "ios",
        "source_artifact_status": "available",
    }


def test_source_artifacts_update_happens_after_reading_record_and_original_input_insert() -> None:
    log: list[str] = []
    conn = _FakeConn(log=log)
    service = _build_service(conn)

    result = _submit(service)

    assert result.reading_record_id
    assert log.index("bind_source_artifact") > log.index("insert_reading_record")
    assert log.index("bind_source_artifact") > log.index("insert_original_input")
    assert log.index("insert_extraction_job") > log.index("bind_source_artifact")
    assert log.index("insert_reader_run") > log.index("bind_source_artifact")
    bind_call = _find_execute_call(conn, "UPDATE source_artifacts")
    assert bind_call.args[1] == result.reading_record_id
    assert bind_call.args[2] == result.original_input_id
    assert bind_call.args[3] == _NOW


def test_missing_or_wrong_user_artifact_fails_closed() -> None:
    conn = _FakeConn(source_artifact_row=None)
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationNotFoundError, match="source artifact not found"):
        _submit(service)

    assert len(conn.fetchrow_calls) == 1
    assert conn.execute_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


@pytest.mark.parametrize("status", ["pending", "failed", "deleted"])
def test_non_available_status_fails_closed(status: str) -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(status=status),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="status must be 'available'"):
        _submit(service)

    assert conn.execute_calls == []


def test_already_bound_artifact_fails_closed() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(
            reading_record_id=_READING_RECORD_ID,
        ),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="already bound"):
        _submit(service)

    assert conn.execute_calls == []


def test_non_original_upload_artifact_fails_closed() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(artifact_kind="ocr_result"),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="original_upload"):
        _submit(service)

    assert conn.execute_calls == []


def test_non_oss_artifact_fails_closed() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(storage_provider="local"),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="storage_provider"):
        _submit(service)

    assert conn.execute_calls == []


def test_missing_artifact_content_sha256_uses_deterministic_object_ref_hash() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(content_sha256=None),
    )
    service = _build_service(conn)

    _submit(service)

    insert_original_input = _find_execute_call(conn, "INSERT INTO original_inputs")
    source_ref_json = insert_original_input.args[4]
    expected_hash = hashlib.sha256(
        json.dumps(
            source_ref_json,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert insert_original_input.args[6] == expected_hash


def test_no_candidate_document_is_created_and_no_event_is_published() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    _submit(service)

    all_queries = "\n".join(call.query for call in conn.calls)
    assert "candidate_reading_documents" not in all_queries
    assert "reader_events" not in all_queries
    assert "article_ready" not in all_queries


def test_extraction_job_payload_contains_complete_artifact_reference() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    result = _submit(service)

    job_insert = next(
        call for call in conn.fetchrow_calls if "INSERT INTO reader_jobs" in call.query
    )
    input_json_arg = job_insert.args[9]
    assert input_json_arg == {
        "source": "artifact_input",
        "reading_record_id": str(result.reading_record_id),
        "original_input_id": str(result.original_input_id),
        "source_artifact_id": str(_ARTIFACT_ID),
        "artifact_kind": "original_upload",
        "storage_provider": "oss",
        "bucket": "claread-dev",
        "endpoint": "https://oss-cn-shenzhen.aliyuncs.com",
        "object_key": f"dev/original-inputs/{_USER_ID}/{_ARTIFACT_ID}/report.pdf",
        "content_type": "application/pdf",
        "byte_size": 4096,
        "content_sha256": "a" * 64,
        "source_filename": "report.pdf",
    }
    assert job_insert.args[2] == _USER_ID
    assert job_insert.args[3] == "input_artifact_extraction"
    assert job_insert.args[4] == "record"
    assert job_insert.args[5] == str(_ARTIFACT_ID)
    assert job_insert.args[6] == "input_artifact_extraction_v1"
    assert job_insert.args[7] == f"input_artifact_extraction_v1:{_ARTIFACT_ID}"


def test_extraction_job_enqueued_after_record_input_and_artifact_binding() -> None:
    log: list[str] = []
    conn = _FakeConn(log=log)
    service = _build_service(conn)

    _submit(service)

    ordering = {
        "select_source_artifact": log.index("select_source_artifact"),
        "insert_reading_record": log.index("insert_reading_record"),
        "insert_original_input": log.index("insert_original_input"),
        "bind_source_artifact": log.index("bind_source_artifact"),
        "insert_reader_run": log.index("insert_reader_run"),
        "insert_extraction_job": log.index("insert_extraction_job"),
    }
    assert ordering["select_source_artifact"] < ordering["insert_reading_record"]
    assert ordering["insert_reading_record"] < ordering["insert_original_input"]
    assert ordering["insert_original_input"] < ordering["bind_source_artifact"]
    assert ordering["bind_source_artifact"] < ordering["insert_reader_run"]
    assert ordering["insert_reader_run"] < ordering["insert_extraction_job"]


def test_extraction_job_creation_failure_rolls_back_transaction() -> None:
    log: list[str] = []
    conn = _FakeConn(
        log=log,
        fail_on_query_substring="INSERT INTO reader_jobs",
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationError, match="Failed to persist"):
        _submit(service)

    assert "insert_extraction_job" not in log
    assert "transaction_rolled_back" in log
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_extraction_job_run_insert_failure_rolls_back_transaction() -> None:
    log: list[str] = []
    conn = _FakeConn(
        log=log,
        fail_on_query_substring="INSERT INTO reader_runs",
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationError, match="Failed to persist"):
        _submit(service)

    assert "insert_reader_run" not in log
    assert "insert_extraction_job" not in log
    assert "transaction_rolled_back" in log


@pytest.mark.parametrize("status", ["pending", "failed", "deleted"])
def test_non_available_status_does_not_create_extraction_job(status: str) -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(status=status),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="status must be 'available'"):
        _submit(service)

    job_fetchrow_calls = [
        call for call in conn.fetchrow_calls
        if "INSERT INTO reader_jobs" in call.query
        or "INSERT INTO reader_runs" in call.query
    ]
    assert job_fetchrow_calls == []
    assert conn.execute_calls == []


def test_already_bound_artifact_does_not_create_extraction_job() -> None:
    conn = _FakeConn(
        source_artifact_row=_build_source_artifact_row(
            reading_record_id=_READING_RECORD_ID,
        ),
    )
    service = _build_service(conn)

    with pytest.raises(ArtifactInputApplicationConflictError, match="already bound"):
        _submit(service)

    job_fetchrow_calls = [
        call for call in conn.fetchrow_calls
        if "INSERT INTO reader_jobs" in call.query
        or "INSERT INTO reader_runs" in call.query
    ]
    assert job_fetchrow_calls == []
    assert conn.execute_calls == []


def test_source_metadata_conflict_fails_closed() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(
        ArtifactInputApplicationConflictError,
        match="source_metadata.source_artifact_status",
    ):
        _submit(
            service,
            source_metadata={"source_artifact_status": "pending"},
        )

    assert conn.fetchrow_calls == []
    assert conn.execute_calls == []
