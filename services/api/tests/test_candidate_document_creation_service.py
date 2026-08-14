# task-history: (renamed from test_d6_i3e_candidate_document_creation_service.py)
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationError,
    CandidateDocumentCreationResult,
    CandidateDocumentCreationService,
    _build_candidate_blocks,
)
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

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
        fetchrow_result: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[_RecordedCall] = []
        self._in_transaction = False
        self._log = log
        self._last_transaction: _FakeTransaction | None = None
        self._fail_on_query_substring = fail_on_query_substring
        self._fail_exception = fail_exception or RuntimeError("db write failed")
        self._fetchrow_result = fetchrow_result

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
            elif (
                "UPDATE candidate_reading_documents" in query
                and "superseded" in query
            ):
                self._log.append("supersede_ready_candidates")
        if (
            self._fail_on_query_substring is not None
            and self._fail_on_query_substring in query
        ):
            raise self._fail_exception
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(
            _RecordedCall(
                "fetchrow",
                query,
                args,
                in_transaction=self._in_transaction,
            )
        )
        if self._log is not None:
            if "reading_records" in query and "FOR UPDATE" in query:
                self._log.append("lock_record_for_update")
            elif "INSERT INTO confirmed_source_documents" in query:
                self._log.append("insert_confirmed_source")
        if (
            self._fail_on_query_substring is not None
            and self._fail_on_query_substring in query
        ):
            raise self._fail_exception
        # L2: insert_confirmed_source uses INSERT ... RETURNING via
        # fetchrow; synthesize the inserted row from the query args
        # (id, record_id, user_id, generation, original_input_id,
        # markdown_text, content_sha256, edit_source, now).
        if "INSERT INTO confirmed_source_documents" in query:
            return {
                "id": args[0],
                "reading_record_id": args[1],
                "user_id": args[2],
                "record_generation": args[3],
                "original_input_id": args[4],
                "markdown_text": args[5],
                "revision": 1,
                "content_sha256": args[6],
                "status": "draft",
                "edit_source": args[7],
            }
        return self._fetchrow_result

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


def _default_fetchrow_result() -> dict[str, Any]:
    """Default fake row for the candidate-write lock helper's SELECT."""
    return {"id": str(uuid4()), "generation": 1}


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
    conn = _FakeConn(log=log, fetchrow_result=_default_fetchrow_result())
    service = _build_service(conn)
    text = (
        "# Weekly Review\n\n"
        f"{_english_paragraph()}\n\n"
        # L1: the extra raw cell makes the table structure-uncertain
        # (content_check); deterministic tables go stable-ready instead.
        "| City | Cost |\n"
        "| --- | --- |\n"
        "| A | 10 | 99 |\n\n"
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
    # Parser emits table wrapper + table_row + table_cell blocks, so the
    # block count is 10: heading, paragraph, table, table_row (header),
    # table_cell x2 (header), table_row (body), table_cell x2 (body),
    # final paragraph.
    assert result.block_count == 10

    assert log == [
        "transaction_started",
        "insert_reading_record",
        "insert_original_input",
        "insert_confirmed_source",
        "lock_record_for_update",
        "supersede_ready_candidates",
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
    # L2: original_inputs.source_text 恒 NULL（正文唯一载体是
    # confirmed_source_documents）；content_sha256 保留原始文本 hash。
    assert original_input_call.args[4] is None
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
        "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "b10",
    ]
    assert [block["order_index"] for block in blocks_json] == list(range(10))
    assert blocks_json[0]["interpretation_policy"]["default_route"] == "main_reading"
    assert blocks_json[2]["interpretation_policy"]["default_route"] == "main_reading"
    assert blocks_json[9]["interpretation_policy"]["default_route"] == "main_reading"
    assert all(
        block["source_refs_json"]["original_input_id"] == str(result.original_input_id)
        for block in blocks_json
    )

    # L2: candidate source_refs_json 含 Confirmed Source 三 key
    # （confirmed_source_document_id / source_revision /
    # source_content_sha256），指向 revision=1 的 source 行。
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    expected_source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert source_refs_json == {
        "source_type": "markdown_file",
        "filename": "weekly-review.md",
        "source_metadata": {
            "source_kind": "import",
            "doc_id": "doc-1",
        },
        "original_input_id": str(result.original_input_id),
        "confirmed_source_document_id": source_refs_json[
            "confirmed_source_document_id"
        ],
        "source_revision": 1,
        "source_content_sha256": expected_source_hash,
    }
    assert quality_json["candidate_creation_version"] == "candidate_creation_v1"
    assert quality_json["suitability"]["outcome"] == "candidate_document_required"
    assert "markdown_complex_structure" in quality_json["suitability"]["flags"]
    assert "table_structure_uncertain" in quality_json["suitability"]["flags"]
    assert quality_json["suitability"]["reasons"]


def test_pdf_text_defaults_to_candidate_and_creates_candidate_document() -> None:
    conn = _FakeConn(fetchrow_result=_default_fetchrow_result())
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
    conn = _FakeConn(fetchrow_result=_default_fetchrow_result())
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
    conn = _FakeConn(fetchrow_result=_default_fetchrow_result())
    service = _build_service(conn)
    text = (
        "# Candidate Contract\n\n"
        f"{_english_paragraph()}\n\n"
        "- First list item explains the tradeoff clearly for readers.\n"
        "- Second list item preserves grouping information for projection.\n\n"
        # L1: the extra raw cell makes the table structure-uncertain
        # (content_check); deterministic tables go stable-ready instead.
        "| Topic | Status |\n"
        "| --- | --- |\n"
        "| Lists | keep structure | spare |\n\n"
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
    # Parser expresses list grouping via parent_block_id (both items
    # share the list wrapper's block_id), not via a list_id payload key.
    # Unordered list items have ordinal=None per Structured Source Contract.
    assert list_blocks[0]["payload_json"] == {
        "ordered": False,
        "ordinal": None,
        "depth": 0,
        "marker": "-",
    }
    assert list_blocks[1]["payload_json"] == {
        "ordered": False,
        "ordinal": None,
        "depth": 0,
        "marker": "-",
    }
    # Both list items must share the same parent_block_id (list wrapper).
    list_parent = list_blocks[0]["parent_block_id"]
    assert list_parent is not None
    assert list_blocks[1]["parent_block_id"] == list_parent

    assert code_block["text_content"] == "def add(a, b):\n    return a + b"
    assert "```" not in code_block["text_content"]
    # Parser stores the full info string as language; fenced/closed
    # replace the legacy info_string/raw_fence_marker keys.
    assert code_block["payload_json"]["language"] == "python title=demo"
    assert code_block["payload_json"]["fenced"] is True
    assert code_block["payload_json"]["closed"] is True


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
        fetchrow_result=_default_fetchrow_result(),
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


def test_candidate_heading_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="## **Bold heading** with [link](https://x.test)",
        filename="x.md",
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "heading"
    assert blocks[0].text_content == "Bold heading with link"
    # Heading payload carries inline_marks (structured inline marks with
    # UTF-16 offsets). The markdown SYNTAX (**, []) is stripped from
    # text_content, but the mark ranges + safe link href are preserved as
    # structured data in inline_marks. No top-level `links` key is added
    # to heading payload (only paragraphs get the `links` key).
    assert blocks[0].payload_json.get("level") == 2
    assert "inline_marks" in blocks[0].payload_json, (
        f"contract: heading with inline marks must carry inline_marks, got {blocks[0].payload_json!r}"
    )
    marks = blocks[0].payload_json["inline_marks"]
    assert {"type": "strong", "start": 0, "end": 12} in marks
    assert {"type": "link", "start": 18, "end": 22, "href": "https://x.test"} in marks
    assert "links" not in blocks[0].payload_json
    assert "links" not in blocks[0].source_refs_json


def test_candidate_paragraph_strips_inline_code_and_strong() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="**bold** and `code` and *em*",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "paragraph"
    assert blocks[0].text_content == "bold and code and em"


def test_candidate_list_item_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="- **bold** [link](https://x.test)",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    # Parser emits a list wrapper (block_type="list") as blocks[0],
    # followed by list_item blocks as children. Find the list_item.
    list_item = next(b for b in blocks if b.block_type == "list_item")
    assert list_item.text_content == "bold link"
    # The list_item parser flattens inline marks via _extract_inline_text;
    # link text is preserved but the URL is not captured (same as heading).
    assert "links" not in list_item.payload_json
    assert "links" not in list_item.source_refs_json


def test_candidate_blockquote_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="> **quoted** [link](https://x.test)",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "blockquote"
    assert blocks[0].text_content == "quoted link"


def test_candidate_code_block_keeps_raw_code_and_no_links() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="```py\nprint(**kwargs)\n```",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    code = next(b for b in blocks if b.block_type == "code_block")
    assert code.text_content == "print(**kwargs)"
    assert "**" in code.text_content  # code body is untouched
    assert "links" not in code.source_refs_json


def test_candidate_fenced_block_does_not_emit_fence_in_text_content() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="```\nhello\n```",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    code = next(b for b in blocks if b.block_type == "code_block")
    assert "```" not in code.text_content
    assert code.text_content.strip() == "hello"


def test_markdown_candidate_blocks_carry_parser_identity_quality_json() -> None:
    """markdown_file candidate blocks MUST carry the parser identity
    triple (parser_name / parser_version / profile) in each block's
    ``quality_json`` to preserve provenance symmetry with the normalizer
    path (``input_document_normalizer._PARSER_IDENTITY``).

    Without this, candidate blocks lose their parser provenance and
    downstream consumers (Article RAG, Ask evidence adapter) cannot
    distinguish structured-source blocks from legacy regex-produced
    blocks. This is an M1 contract hard-gap per plan §5 G1.
    """
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="# Title\n\nParagraph with [link](https://x.test).\n",
        filename="x.md",
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks, "candidate path must produce at least one block"
    for block in blocks:
        quality = block.quality_json
        assert quality.get("parser_name") == PARSER_NAME, (
            f"block {block.block_id} ({block.block_type}) quality_json must "
            f"carry parser_name={PARSER_NAME!r}, got {quality!r}"
        )
        assert quality.get("parser_version") == PARSER_VERSION, (
            f"block {block.block_id} ({block.block_type}) quality_json must "
            f"carry parser_version={PARSER_VERSION!r}, got {quality!r}"
        )
        assert quality.get("profile") == PROFILE, (
            f"block {block.block_id} ({block.block_type}) quality_json must "
            f"carry profile={PROFILE!r}, got {quality!r}"
        )


def test_plain_text_candidate_blocks_do_not_carry_parser_identity() -> None:
    """plain text candidate blocks (pasted_text / txt_file) MUST NOT
    carry the markdown parser identity triple, because they are not
    produced by ``MarkdownSourceParser``. This guards against falsely
    attributing plain-text content to the structured-source parser when
    the markdown_file path is patched to carry identity.
    """
    blocks, _ = _build_candidate_blocks(
        source_type="pasted_text",
        text="Just plain text without any markdown syntax.\n\nSecond paragraph.",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks, "plain text candidate path must produce at least one block"
    for block in blocks:
        quality = block.quality_json
        assert "parser_name" not in quality, (
            f"block {block.block_id} ({block.block_type}) quality_json must "
            f"not carry parser_name for plain-text source, got {quality!r}"
        )
        assert "parser_version" not in quality
        assert "profile" not in quality


def test_candidate_freeze_plan_canonical_text_has_no_inline_markdown() -> None:
    """When the candidate is confirmed, the freeze plan must derive
    canonical_text from the stripped block text — not from the raw
    markdown source. This guards the round-trip against regressions
    that would re-introduce inline syntax into reading_bases.text.
    """
    from app.services.reader_orchestration.document_freeze_plan import (
        build_stable_document_freeze_plan,
    )

    # Build candidate blocks using the public function.
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text=(
            "## **Heading**\n"
            "\n"
            "Paragraph with **bold** and [link](https://x.test).\n"
            "\n"
            "- item with `code`\n"
        ),
        filename="x.md",
        source_metadata={},
        original_input_id=uuid4(),
    )

    # ``build_stable_document_freeze_plan`` is the canonical text builder
    # used by the candidate-confirm transaction service.
    plan = build_stable_document_freeze_plan(
        reading_record_id=str(uuid4()),
        record_generation=1,
        document_version=1,
        title="Test",
        blocks=blocks,
        source_profile_json={},
    )
    canonical = plan.canonical_text
    for forbidden in ("**", "[", "](", "`"):
        assert forbidden not in canonical, (
            f"freeze plan canonical_text leaked Markdown syntax {forbidden!r}: "
            f"{canonical!r}"
        )
