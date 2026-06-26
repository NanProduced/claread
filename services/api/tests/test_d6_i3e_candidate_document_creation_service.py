from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationError,
    CandidateDocumentCreationResult,
    CandidateDocumentCreationService,
)

_USER_ID = UUID("00000000-0000-0000-0000-000000000501")
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
        if self._log is not None:
            if "INSERT INTO reading_records" in query:
                self._log.append("insert_reading_record")
            elif "INSERT INTO original_inputs" in query:
                self._log.append("insert_original_input")
            elif "INSERT INTO candidate_reading_documents" in query:
                self._log.append("insert_candidate_document")
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


def _english_paragraph(multiplier: int = 1) -> str:
    sentence = (
        "This article explains how communities compare evidence, revise plans, "
        "and communicate tradeoffs to readers in clear English prose."
    )
    return " ".join(sentence for _ in range(8 * multiplier))


def _build_service(
    conn: _FakeConn,
) -> CandidateDocumentCreationService:
    return CandidateDocumentCreationService(pool=FakePool(conn))


def _create(
    service: CandidateDocumentCreationService,
    *,
    source_type: str = "markdown_file",
    text: str,
    filename: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    client_record_id: str | None = None,
    language: str | None = "en",
    now: datetime = _NOW,
) -> CandidateDocumentCreationResult:
    return asyncio.run(
        service.create_candidate_document_from_input(
            user_id=_USER_ID,
            source_type=source_type,
            text=text,
            filename=filename,
            source_metadata=source_metadata,
            client_record_id=client_record_id,
            language=language,
            now=now,
        )
    )


def _find_execute_call(conn: _FakeConn, fragment: str) -> _RecordedCall:
    for call in conn.execute_calls:
        if fragment in call.query:
            return call
    raise AssertionError(f"missing execute call containing {fragment!r}")


def test_markdown_table_candidate_creates_record_original_input_and_candidate_document() -> None:
    log: list[str] = []
    conn = _FakeConn(log=log)
    service = _build_service(conn)
    text = (
        "# Weekly Review\n\n"
        f"{_english_paragraph()}\n\n"
        "| City | Cost |\n"
        "| --- | --- |\n"
        "| A | 10 |\n\n"
        "Final paragraph explains the tradeoff in full sentences for readers."
    )

    result = _create(
        service,
        source_type="markdown_file",
        filename="weekly-review.md",
        text=text,
        source_metadata={"source_kind": "import", "doc_id": "doc-1"},
        client_record_id="client-route-501",
    )

    assert result.record_generation == 1
    assert result.status == "ready"
    assert result.title == "Weekly Review"
    assert result.source_type == "markdown_file"
    assert result.filename == "weekly-review.md"
    assert result.block_count == 4

    assert log == [
        "transaction_started",
        "insert_reading_record",
        "insert_original_input",
        "insert_candidate_document",
        "transaction_committed",
    ]
    assert conn._last_transaction is not None
    assert conn._last_transaction.committed is True
    assert all(call.in_transaction for call in conn.execute_calls)

    reading_call = _find_execute_call(conn, "INSERT INTO reading_records")
    assert reading_call.args[0] == result.reading_record_id
    assert reading_call.args[2] == "client-route-501"
    assert reading_call.args[3] == "markdown"
    assert reading_call.args[4] == "Weekly Review"
    assert reading_call.args[5] == "en"

    original_input_call = _find_execute_call(conn, "INSERT INTO original_inputs")
    assert original_input_call.args[0] == result.original_input_id
    assert original_input_call.args[1] == result.reading_record_id
    assert original_input_call.args[4] == text
    assert original_input_call.args[5] == {
        "adapter_source_type": "markdown_file",
        "filename": "weekly-review.md",
    }
    assert original_input_call.args[6] == {
        "source_kind": "import",
        "doc_id": "doc-1",
    }
    assert original_input_call.args[7] == hashlib.sha256(text.encode("utf-8")).hexdigest()

    candidate_call = _find_execute_call(conn, "INSERT INTO candidate_reading_documents")
    blocks_json = candidate_call.args[4]
    source_refs_json = candidate_call.args[6]
    quality_json = candidate_call.args[7]

    assert candidate_call.args[0] == result.candidate_document_id
    assert candidate_call.args[1] == result.reading_record_id
    assert candidate_call.args[3] == "Weekly Review"
    assert [block["block_id"] for block in blocks_json] == [
        "heading-0000",
        "paragraph-0001",
        "table-0002",
        "paragraph-0003",
    ]
    assert [block["order_index"] for block in blocks_json] == [0, 1, 2, 3]
    assert blocks_json[0]["interpretation_policy"]["default_route"] == "main_reading"
    assert blocks_json[2]["interpretation_policy"]["default_route"] == "metadata_only"
    assert blocks_json[3]["interpretation_policy"]["default_route"] == "main_reading"
    assert all(block["source_refs_json"]["original_input_id"] == str(result.original_input_id) for block in blocks_json)

    assert source_refs_json == {
        "source_type": "markdown_file",
        "filename": "weekly-review.md",
        "source_metadata": {
            "source_kind": "import",
            "doc_id": "doc-1",
        },
        "original_input_id": str(result.original_input_id),
    }
    assert quality_json["candidate_creation_version"] == "candidate_creation_v1"
    assert quality_json["suitability"]["outcome"] == "candidate_document_required"
    assert "markdown_complex_structure" in quality_json["suitability"]["flags"]
    assert "table_structure_uncertain" in quality_json["suitability"]["flags"]
    assert quality_json["suitability"]["reasons"]


def test_pdf_text_defaults_to_candidate_and_creates_candidate_document() -> None:
    conn = _FakeConn()
    service = _build_service(conn)
    result = _create(
        service,
        source_type="pdf_text",
        text=_english_paragraph(multiplier=2),
        filename="report.pdf",
        source_metadata={"extraction_confidence": 0.4},
    )

    candidate_call = _find_execute_call(conn, "INSERT INTO candidate_reading_documents")
    quality_json = candidate_call.args[7]

    assert result.status == "ready"
    assert result.record_generation == 1
    assert result.block_count >= 1
    assert quality_json["suitability"]["outcome"] == "candidate_document_required"


def test_ocr_low_confidence_candidate_quality_includes_flag() -> None:
    conn = _FakeConn()
    service = _build_service(conn)
    result = _create(
        service,
        source_type="ocr_text",
        text=_english_paragraph(),
        source_metadata={"ocr_confidence": 0.41},
    )

    candidate_call = _find_execute_call(conn, "INSERT INTO candidate_reading_documents")
    quality_json = candidate_call.args[7]

    assert result.status == "ready"
    assert "ocr_low_confidence" in quality_json["suitability"]["flags"]


def test_markdown_candidate_preserves_list_payload_and_code_block_contract() -> None:
    conn = _FakeConn()
    service = _build_service(conn)
    text = (
        "# Candidate Contract\n\n"
        f"{_english_paragraph()}\n\n"
        "- First list item explains the tradeoff clearly for readers.\n"
        "- Second list item preserves grouping information for projection.\n\n"
        "| Topic | Status |\n"
        "| --- | --- |\n"
        "| Lists | keep structure |\n\n"
        "```python title=demo\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n\n"
        "Closing paragraph keeps the candidate non-empty after code."
    )

    _create(
        service,
        source_type="markdown_file",
        filename="candidate-contract.md",
        text=text,
    )

    candidate_call = _find_execute_call(conn, "INSERT INTO candidate_reading_documents")
    blocks_json = candidate_call.args[4]
    list_blocks = [block for block in blocks_json if block["block_type"] == "list_item"]
    code_block = next(block for block in blocks_json if block["block_type"] == "code_block")

    assert len(list_blocks) == 2
    assert list_blocks[0]["payload_json"] == {
        "list_id": "l1",
        "ordered": False,
        "ordinal": 1,
        "depth": 0,
        "marker": "-",
    }
    assert list_blocks[1]["payload_json"] == {
        "list_id": "l1",
        "ordered": False,
        "ordinal": 2,
        "depth": 0,
        "marker": "-",
    }

    assert code_block["text_content"] == "def add(a, b):\n    return a + b"
    assert "```" not in code_block["text_content"]
    assert code_block["payload_json"]["language"] == "python"
    assert code_block["payload_json"]["info_string"] == "python title=demo"
    assert code_block["payload_json"]["closed"] is True
    assert code_block["payload_json"]["raw_fence_marker"] == "```"


def test_stable_ready_pasted_text_fails_closed_without_writes() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(
        CandidateDocumentCreationError,
        match="stable-document-ready",
    ):
        _create(
            service,
            source_type="pasted_text",
            text=_english_paragraph(multiplier=2),
            source_metadata={"source_kind": "paste"},
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_rejected_too_short_input_fails_closed_without_writes() -> None:
    conn = _FakeConn()
    service = _build_service(conn)

    with pytest.raises(
        CandidateDocumentCreationError,
        match="input_rejected_or_action_required",
    ):
        _create(
            service,
            source_type="pasted_text",
            text="This note is too short.",
        )

    assert conn.execute_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_db_write_error_rolls_back_and_wraps_cause() -> None:
    conn = _FakeConn(
        fail_on_query_substring="INSERT INTO candidate_reading_documents",
        fail_exception=RuntimeError("candidate insert failed"),
    )
    service = _build_service(conn)

    with pytest.raises(CandidateDocumentCreationError) as excinfo:
        _create(
            service,
            source_type="pdf_text",
            text=_english_paragraph(multiplier=2),
            filename="report.pdf",
            source_metadata={"extraction_confidence": 0.4},
        )

    assert "Failed to persist the candidate-required input envelope" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True
