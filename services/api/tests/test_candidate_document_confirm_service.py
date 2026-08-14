# task-history: (renamed from test_d6_i2d_candidate_document_confirm_service.py)
"""Focused tests for the candidate document confirm service.

These tests use a fake asyncpg connection recorder to assert SQL order
and parameters without requiring a real database. This keeps the tests
fast, hermetic, and free of any dependency on the dirty repository.py
or existing DB test harnesses.

Test coverage:
    * Transaction validation (not in transaction -> raises, no SQL).
    * Candidate not found -> raises.
    * Candidate status rejected / superseded / confirmed -> raises, no
      persistence writes.
    * blocks_json non-list / empty / invalid block -> raises.
    * Happy path: ready candidate -> correct result, SQL order,
      candidate confirmed.
    * user_id guard in SELECT and persistence confirm.
    * source_refs_json / quality_json preserved in source_profile_json.
    * Persistence error wrapped as CandidateDocumentConfirmError with
      __cause__ preserved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.candidate_document_confirm_service import (
    CandidateDocumentConfirmError,
    CandidateDocumentConfirmResult,
    CandidateDocumentStatusError,
    confirm_candidate_document,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]


# --------------------------------------------------------------------
# Fake asyncpg connection
# --------------------------------------------------------------------


class _FakeRecord:
    """Mimics an asyncpg.Record for fetchrow results."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def __getitem__(self, key: str) -> Any:
        return self._mapping[key]

    def keys(self) -> list[str]:
        return list(self._mapping.keys())


class _RecordedCall:
    __slots__ = ("kind", "query", "args")

    def __init__(self, kind: str, query: str, args: tuple) -> None:
        self.kind = kind
        self.query = query
        self.args = args

    def __repr__(self) -> str:
        return f"_RecordedCall(kind={self.kind!r}, query={self.query!r}, args={self.args!r})"


class FakeConn:
    """Recording fake asyncpg.Connection for unit testing.

    Records every execute/fetchrow/fetchval call so tests can assert
    SQL order and parameters. fetchrow/fetchval results are queued by
    the test in the order they will be consumed.

    execute() returns verb-based defaults:
        - UPDATE queries -> "UPDATE 1"
        - INSERT queries -> "INSERT 0 1"
    Tests override per-call via set_execute_result(sql_substring,
    result), consumed once on the first matching query.
    """

    def __init__(self, *, in_transaction: bool = True) -> None:
        self.calls: list[_RecordedCall] = []
        self._fetchrow_queue: list[_FakeRecord | None] = []
        self._fetchval_queue: list[Any] = []
        self._execute_overrides: list[tuple[str, str]] = []
        self._in_transaction = in_transaction

    # -- Queuing helpers for tests --

    def queue_fetchrow(self, mapping: dict[str, Any] | None) -> None:
        self._fetchrow_queue.append(_FakeRecord(mapping) if mapping else None)

    def queue_fetchval(self, value: Any) -> None:
        self._fetchval_queue.append(value)

    def set_execute_result(self, sql_substring: str, result: str) -> None:
        """Override the next execute() whose SQL contains sql_substring.

        The override is consumed once (popped) when matched.
        """
        self._execute_overrides.append((sql_substring, result))

    # -- asyncpg-compatible interface --

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(_RecordedCall("execute", query, args))
        for i, (substr, result) in enumerate(self._execute_overrides):
            if substr in query:
                self._execute_overrides.pop(i)
                return result
        stripped = query.lstrip().upper()
        if stripped.startswith("UPDATE"):
            return "UPDATE 1"
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        self.calls.append(_RecordedCall("fetchrow", query, args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(_RecordedCall("fetchval", query, args))
        if self._fetchval_queue:
            return self._fetchval_queue.pop(0)
        return None

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    # -- Query helpers for assertions --

    @property
    def execute_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "execute"]

    @property
    def fetchrow_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "fetchrow"]

    @property
    def fetchval_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "fetchval"]

    def calls_matching(self, sql_substring: str) -> list[_RecordedCall]:
        return [c for c in self.calls if sql_substring in c.query]


# --------------------------------------------------------------------
# Constants and helpers
# --------------------------------------------------------------------


_RECORD_ID = UUID("00000000-0000-0000-0000-000000000001")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000002")
_USER_ID = UUID("00000000-0000-0000-0000-000000000003")


def _block(block_id: str, text: str, order: int, block_type: str = "paragraph") -> dict[str, Any]:
    return {
        "block_id": block_id,
        "order_index": order,
        "block_type": block_type,
        "text_content": text,
    }


_UNSET = object()


def _candidate_row(
    *,
    status: str = "ready",
    blocks: list[dict[str, Any]] | None = None,
    source_refs: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    record_generation: int = 1,
    title: str | None = "Test Article",
    source_refs_raw: Any = _UNSET,
    quality_raw: Any = _UNSET,
) -> dict[str, Any]:
    """Build a candidate row dict for FakeConn.

    ``source_refs`` / ``quality`` accept dicts and default to ``{}``
    when ``None`` (back-compat with happy-path tests). To test invalid
    raw values (JSON strings, lists, ``None``, numbers), pass
    ``source_refs_raw`` / ``quality_raw`` which override the field
    directly — including ``None``.
    """
    if blocks is None:
        blocks = [
            _block("h1", "Title", 0, "heading"),
            _block("p1", "First paragraph.", 1),
            _block("p2", "Second paragraph.", 2),
        ]
    row: dict[str, Any] = {
        "id": _CANDIDATE_ID,
        "reading_record_id": _RECORD_ID,
        "user_id": _USER_ID,
        "record_generation": record_generation,
        "title": title,
        "blocks_json": blocks,
        "source_refs_json": source_refs or {},
        "quality_json": quality or {},
        "status": status,
    }
    if source_refs_raw is not _UNSET:
        row["source_refs_json"] = source_refs_raw
    if quality_raw is not _UNSET:
        row["quality_json"] = quality_raw
    return row


def _queue_happy_path(
    conn: FakeConn,
    *,
    candidate_row: dict[str, Any] | None = None,
) -> None:
    """Queue fetchrow results for a happy-path confirm.

    Queues:
        fetchrow #1: candidate row (from service SELECT ... FOR UPDATE)
        fetchrow #2: None (L2 插入点 A — confirmed_source_documents 行
                     不存在 → legacy candidate 分支，无 source 校验/冻结)
        fetchrow #3: None (existing stable doc, from persistence idempotency check)
        fetchrow #4: {"status": "ready"} (candidate status lookup, from
                     persistence _confirm_candidate_document)

    Also sets the supersede UPDATE to return "UPDATE 0" (no prior active
    stable doc).
    """
    conn.queue_fetchrow(candidate_row or _candidate_row())
    conn.queue_fetchrow(None)  # L2: no confirmed source row (legacy)
    conn.queue_fetchrow(None)  # no existing stable doc
    conn.queue_fetchrow({"status": "ready"})  # candidate status for confirm
    conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")


def _confirm(
    conn: FakeConn,
    *,
    candidate_document_id: UUID = _CANDIDATE_ID,
    reading_record_id: UUID = _RECORD_ID,
    user_id: UUID = _USER_ID,
    language: str | None = "en",
) -> CandidateDocumentConfirmResult:
    import asyncio

    return asyncio.run(
        confirm_candidate_document(
            conn,
            candidate_document_id=candidate_document_id,
            reading_record_id=reading_record_id,
            user_id=user_id,
            canonicalizer_version="test_canonicalizer_v1",
            builder_version="test_builder_v1",
            segmenter_version="regex_sentence_clause_window_v1",
            language=language,
            now=datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        )
    )


# --------------------------------------------------------------------
# Transaction validation
# --------------------------------------------------------------------


class TestTransactionValidation:
    def test_not_in_transaction_raises_at_entry(self) -> None:
        conn = FakeConn(in_transaction=False)
        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"must be called within an active transaction",
        ):
            _confirm(conn)

        # No SQL should have been executed.
        assert len(conn.calls) == 0


# --------------------------------------------------------------------
# Candidate lookup
# --------------------------------------------------------------------


class TestCandidateLookup:
    def test_candidate_not_found_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # candidate not found

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"Candidate document not found",
        ):
            _confirm(conn)

        # Only the candidate SELECT should have been issued.
        assert len(conn.fetchrow_calls) == 1
        assert "candidate_reading_documents" in conn.fetchrow_calls[0].query
        assert "FOR UPDATE" in conn.fetchrow_calls[0].query
        assert len(conn.execute_calls) == 0

    def test_candidate_select_includes_user_id_guard(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)

        with pytest.raises(CandidateDocumentConfirmError):
            _confirm(conn)

        select_query = conn.fetchrow_calls[0].query
        assert "user_id = $3" in select_query
        assert "reading_record_id = $2" in select_query
        assert "id = $1" in select_query


# --------------------------------------------------------------------
# Candidate status validation
# --------------------------------------------------------------------


class TestCandidateStatusValidation:
    @pytest.mark.parametrize("status", ["confirmed", "rejected", "superseded"])
    def test_non_ready_status_raises_and_no_persistence_writes(
        self, status: str
    ) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(_candidate_row(status=status))

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=rf"status='{status}' \(expected 'ready'\)",
        ):
            _confirm(conn)

        # Only the candidate SELECT should have been issued — no
        # persistence writes or lookups.
        assert len(conn.fetchrow_calls) == 1
        assert len(conn.execute_calls) == 0
        assert len(conn.fetchval_calls) == 0
        # No stable_reading_documents or reading_bases queries.
        assert not any(
            "stable_reading_documents" in c.query for c in conn.calls
        )
        assert not any(
            "reading_bases" in c.query for c in conn.calls
        )


class TestCandidateStatusTypedError:
    """Non-ready candidates raise a typed
    CandidateDocumentStatusError (subclass of
    CandidateDocumentConfirmError) carrying structured fields."""

    @pytest.mark.parametrize("status", ["confirmed", "rejected", "superseded"])
    def test_non_ready_status_raises_typed_error(self, status: str) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(_candidate_row(status=status, record_generation=3))

        with pytest.raises(CandidateDocumentStatusError, match=rf"status='{status}'") as exc_info:
            _confirm(conn)

        assert exc_info.value.status == status
        assert exc_info.value.candidate_document_id == _CANDIDATE_ID
        assert exc_info.value.reading_record_id == _RECORD_ID
        assert exc_info.value.user_id == _USER_ID
        assert exc_info.value.record_generation == 3

    def test_status_error_is_subclass_of_confirm_error(self) -> None:
        """CandidateDocumentStatusError must be a subclass of
        CandidateDocumentConfirmError so existing ``except
        CandidateDocumentConfirmError`` handlers still catch it."""
        conn = FakeConn()
        conn.queue_fetchrow(_candidate_row(status="confirmed"))

        with pytest.raises(CandidateDocumentConfirmError):
            _confirm(conn)


# --------------------------------------------------------------------
# blocks_json validation
# --------------------------------------------------------------------


class TestBlocksJsonValidation:
    def test_blocks_json_not_list_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(_candidate_row(blocks=None, status="ready"))  # type: ignore[arg-type]
        # Override blocks_json with a non-list value.
        row = _candidate_row(status="ready")
        row["blocks_json"] = {"not": "a list"}
        conn = FakeConn()
        conn.queue_fetchrow(row)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"blocks_json that is not a list",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0

    def test_blocks_json_empty_list_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(_candidate_row(blocks=[], status="ready"))

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"empty blocks_json",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0

    def test_blocks_json_invalid_block_raises(self) -> None:
        conn = FakeConn()
        row = _candidate_row(status="ready")
        # Missing required field block_id.
        row["blocks_json"] = [
            {"order_index": 0, "block_type": "paragraph", "text_content": "Text"}
        ]
        conn.queue_fetchrow(row)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"blocks_json with invalid block\(s\)",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0

    def test_blocks_json_invalid_json_string_raises(self) -> None:
        conn = FakeConn()
        row = _candidate_row(status="ready")
        row["blocks_json"] = "{not valid json"
        conn.queue_fetchrow(row)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"blocks_json that is not valid JSON",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0


# --------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------


class TestHappyPath:
    def test_returns_result_with_expected_fields(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        result = _confirm(conn)

        assert isinstance(result, CandidateDocumentConfirmResult)
        assert result.reading_record_id == _RECORD_ID
        assert result.record_generation == 1
        assert result.document_version == 1  # pinned: document_version = record_generation
        assert result.block_count == 3
        assert result.candidate_confirmed is True
        assert result.idempotent_noop is False
        assert result.stable_document_id is not None
        assert result.base_id is not None
        assert result.content_sha256  # non-empty
        assert result.canonical_text_sha256  # non-empty

    def test_candidate_select_uses_for_update(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        candidate_select = conn.fetchrow_calls[0]
        assert "FOR UPDATE" in candidate_select.query
        assert "candidate_reading_documents" in candidate_select.query

    def test_persistence_writes_stable_doc_blocks_bases_units_segments(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        queries = [c.query for c in conn.execute_calls]
        # stable_reading_documents: 1 supersede UPDATE + 1 INSERT
        assert sum(1 for q in queries if "UPDATE stable_reading_documents" in q) == 1
        assert sum(1 for q in queries if "INSERT INTO stable_reading_documents" in q) == 1
        # stable_document_blocks: 3 INSERTs
        assert sum(1 for q in queries if "INSERT INTO stable_document_blocks" in q) == 3
        # reading_bases: 1 supersede UPDATE + 1 INSERT
        assert sum(1 for q in queries if "UPDATE reading_bases" in q) == 1
        assert sum(1 for q in queries if "INSERT INTO reading_bases" in q) == 1
        # reading_units: 3 INSERTs
        assert sum(1 for q in queries if "INSERT INTO reading_units" in q) == 3
        # anchor_segments: 3 INSERTs
        assert sum(1 for q in queries if "INSERT INTO anchor_segments" in q) == 3
        # reading_records: 1 fence UPDATE
        assert sum(1 for q in queries if "UPDATE reading_records" in q) == 1
        # candidate_reading_documents: 1 confirm UPDATE
        assert sum(
            1 for q in queries if "UPDATE candidate_reading_documents" in q
        ) == 1

    def test_candidate_confirmed_with_user_id_guard(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        candidate_confirm = next(
            c for c in conn.execute_calls
            if "UPDATE candidate_reading_documents" in c.query
        )
        # The confirm UPDATE must include user_id in the WHERE clause.
        assert "user_id = $5" in candidate_confirm.query
        assert "status = 'ready'" in candidate_confirm.query

    def test_total_execute_calls_for_simple_plan(self) -> None:
        """Simple plan: 3 blocks (heading + 2 paragraphs) -> 3 units +
        3 anchor segments. Total execute calls:
            1 supersede stable_docs
          + 1 INSERT stable_doc
          + 3 INSERT stable_document_blocks
          + 1 supersede reading_bases
          + 1 INSERT reading_bases
          + 3 INSERT reading_units
          + 3 INSERT anchor_segments
          + 1 UPDATE reading_records (fence)
          + 1 UPDATE candidate_reading_documents (confirm)
          = 15
        """
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        assert len(conn.execute_calls) == 15

    def test_fetchrow_count_for_happy_path(self) -> None:
        """4 fetchrow calls:
            1. candidate SELECT (service)
            2. L2 插入点 A — confirmed_source_documents lock (legacy: None)
            3. existing stable doc check (persistence)
            4. candidate status lookup (persistence _confirm_candidate_document)
        """
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        assert len(conn.fetchrow_calls) == 4


# --------------------------------------------------------------------
# user_id guard
# --------------------------------------------------------------------


class TestUserIdGuard:
    def test_candidate_select_args_include_user_id(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        candidate_select = conn.fetchrow_calls[0]
        # Args: (candidate_document_id, reading_record_id, user_id)
        assert candidate_select.args[0] == _CANDIDATE_ID
        assert candidate_select.args[1] == _RECORD_ID
        assert candidate_select.args[2] == _USER_ID

    def test_candidate_status_lookup_includes_user_id(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        # fetchrow #4 is the candidate status lookup in
        # _confirm_candidate_document (after the L2 插入点 A source lock).
        status_lookup = conn.fetchrow_calls[3]
        assert "candidate_reading_documents" in status_lookup.query
        assert "user_id = $4" in status_lookup.query

    def test_candidate_confirm_update_includes_user_id(self) -> None:
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        confirm_update = next(
            c for c in conn.execute_calls
            if "UPDATE candidate_reading_documents" in c.query
        )
        # Args for the user_id variant:
        # (candidate_document_id, reading_record_id, frozen_at,
        #  record_generation, user_id)
        assert confirm_update.args[0] == _CANDIDATE_ID
        assert confirm_update.args[1] == _RECORD_ID
        assert confirm_update.args[4] == _USER_ID


# --------------------------------------------------------------------
# source_refs_json / quality_json preservation
# --------------------------------------------------------------------


class TestSourceRefsPreservation:
    def test_source_refs_and_quality_preserved_in_source_profile_json(self) -> None:
        """The INSERT INTO stable_reading_documents must include a
        source_profile_json that contains the candidate's
        source_refs_json and quality_json.
        """
        conn = FakeConn()
        source_refs = {"url": "https://example.com/article", "author": "Jane"}
        quality = {"score": 0.95, "flags": ["ocr_corrected"]}
        candidate = _candidate_row(source_refs=source_refs, quality=quality)
        _queue_happy_path(conn, candidate_row=candidate)

        _confirm(conn)

        stable_doc_insert = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        # source_profile_json is $6 (args[5]).
        source_profile = stable_doc_insert.args[5]
        assert isinstance(source_profile, dict)
        assert source_profile["source_refs"] == source_refs
        assert source_profile["quality"] == quality

    def test_empty_source_refs_and_quality_still_preserved(self) -> None:
        """Empty source_refs_json / quality_json should still appear in
        source_profile_json as empty dicts."""
        conn = FakeConn()
        _queue_happy_path(conn)

        _confirm(conn)

        stable_doc_insert = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        source_profile = stable_doc_insert.args[5]
        assert source_profile["source_refs"] == {}
        assert source_profile["quality"] == {}


# --------------------------------------------------------------------
# source_refs_json / quality_json fail-closed (review fix)
# --------------------------------------------------------------------


class TestSourceRefsFailClosed:
    """Review fix: ``source_refs_json`` / ``quality_json`` must
    NOT be silently downgraded to ``{}``. Invalid values fail closed
    with :class:`CandidateDocumentConfirmError` and no persistence
    writes. JSON object strings are accepted for driver compatibility.
    """

    def test_source_refs_json_string_object_is_preserved(self) -> None:
        """A JSON string that parses to an object is accepted and
        preserved in ``source_profile_json``."""
        conn = FakeConn()
        source_refs = {"url": "https://example.com", "page": 5}
        quality = {"score": 0.9, "flags": ["ocr_corrected"]}
        candidate = _candidate_row(
            source_refs_raw=json.dumps(source_refs),
            quality_raw=json.dumps(quality),
        )
        _queue_happy_path(conn, candidate_row=candidate)

        _confirm(conn)

        stable_doc_insert = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        source_profile = stable_doc_insert.args[5]
        assert source_profile["source_refs"] == source_refs
        assert source_profile["quality"] == quality

    def test_source_refs_json_invalid_json_string_raises(self) -> None:
        conn = FakeConn()
        candidate = _candidate_row(source_refs_raw="{not valid json")
        conn.queue_fetchrow(candidate)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"source_refs_json that is not valid JSON",
        ) as exc_info:
            _confirm(conn)

        # Error message must include the candidate_document_id.
        assert str(_CANDIDATE_ID) in str(exc_info.value)
        # No persistence writes.
        assert len(conn.execute_calls) == 0

    def test_quality_json_invalid_json_string_raises(self) -> None:
        conn = FakeConn()
        candidate = _candidate_row(quality_raw="not json{")
        conn.queue_fetchrow(candidate)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"quality_json that is not valid JSON",
        ) as exc_info:
            _confirm(conn)

        assert str(_CANDIDATE_ID) in str(exc_info.value)
        assert len(conn.execute_calls) == 0

    def test_source_refs_json_array_raises(self) -> None:
        """A JSON array string is not an object -> fail closed."""
        conn = FakeConn()
        candidate = _candidate_row(source_refs_raw='[{"a": 1}]')
        conn.queue_fetchrow(candidate)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"source_refs_json that parses to a non-object JSON value",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0

    def test_quality_json_array_raises(self) -> None:
        """A JSON array string is not an object -> fail closed."""
        conn = FakeConn()
        candidate = _candidate_row(quality_raw="[1, 2, 3]")
        conn.queue_fetchrow(candidate)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=r"quality_json that parses to a non-object JSON value",
        ):
            _confirm(conn)

        assert len(conn.execute_calls) == 0

    @pytest.mark.parametrize(
        "value",
        [
            None,
            [1, 2, 3],
            42,
            True,
            3.14,
        ],
        ids=["none", "list", "int", "bool", "float"],
    )
    @pytest.mark.parametrize(
        ("field", "raw_kw"),
        [
            ("source_refs_json", "source_refs_raw"),
            ("quality_json", "quality_raw"),
        ],
        ids=["source_refs_json", "quality_json"],
    )
    def test_non_dict_non_str_values_fail_closed(
        self, value: Any, field: str, raw_kw: str
    ) -> None:
        """None / list / int / bool / float fail closed for both
        fields. No silent downgrade to {}."""
        conn = FakeConn()
        candidate = _candidate_row(**{raw_kw: value})
        conn.queue_fetchrow(candidate)

        with pytest.raises(
            CandidateDocumentConfirmError,
            match=rf"{field} with invalid type",
        ) as exc_info:
            _confirm(conn)

        # Error message must include the candidate_document_id and
        # the field name.
        assert str(_CANDIDATE_ID) in str(exc_info.value)
        assert field in str(exc_info.value)
        # No persistence writes.
        assert len(conn.execute_calls) == 0


# --------------------------------------------------------------------
# Persistence error wrapping
# --------------------------------------------------------------------


class TestPersistenceErrorWrapping:
    def test_same_generation_different_hash_persistence_error_wrapped(self) -> None:
        """If persistence raises StableDocumentFreezePersistenceError
        (e.g., existing stable doc with different hash), the service
        must wrap it as CandidateDocumentConfirmError and preserve
        __cause__.
        """
        conn = FakeConn()
        # fetchrow #1: candidate row (ready).
        conn.queue_fetchrow(_candidate_row(status="ready"))
        # fetchrow #2: L2 插入点 A — no confirmed source row (legacy).
        conn.queue_fetchrow(None)
        # fetchrow #3: existing stable doc with DIFFERENT hash.
        conn.queue_fetchrow({
            "id": UUID("aaaaaaaa-0000-0000-0000-000000000099"),
            "content_sha256": "0" * 64,  # different from plan hash
            "status": "active",
            "document_version": 1,
        })

        with pytest.raises(CandidateDocumentConfirmError) as exc_info:
            _confirm(conn)

        # __cause__ must be a StableDocumentFreezePersistenceError.
        from app.services.reader_orchestration.document_freeze_persistence import (
            StableDocumentFreezePersistenceError,
        )

        assert isinstance(exc_info.value.__cause__, StableDocumentFreezePersistenceError)
        # The error message should mention the persistence failure.
        assert "Failed to persist stable document freeze" in str(exc_info.value)

        # No INSERT/UPDATE writes should have happened (persistence
        # failed at the idempotency check before any writes).
        assert len(conn.execute_calls) == 0

    def test_generation_fence_violation_wrapped(self) -> None:
        """If the reading_records fence UPDATE returns "UPDATE 0"
        (generation mismatch), persistence raises and the service
        wraps it.
        """
        conn = FakeConn()
        _queue_happy_path(conn)
        # Override the fence UPDATE to return "UPDATE 0" (generation
        # mismatch).
        conn.set_execute_result("UPDATE reading_records", "UPDATE 0")

        with pytest.raises(CandidateDocumentConfirmError) as exc_info:
            _confirm(conn)

        from app.services.reader_orchestration.document_freeze_persistence import (
            StableDocumentFreezePersistenceError,
        )

        assert isinstance(exc_info.value.__cause__, StableDocumentFreezePersistenceError)


# --------------------------------------------------------------------
# document_version = record_generation pinning
# --------------------------------------------------------------------


class TestDocumentVersionPinning:
    def test_document_version_equals_record_generation(self) -> None:
        """document_version must equal record_generation to satisfy
        uq_stable_reading_documents_record_version across multiple
        generations.
        """
        conn = FakeConn()
        candidate = _candidate_row(record_generation=3)
        _queue_happy_path(conn, candidate_row=candidate)

        result = _confirm(conn)

        assert result.record_generation == 3
        assert result.document_version == 3

    def test_stable_doc_insert_uses_record_generation_as_document_version(self) -> None:
        conn = FakeConn()
        candidate = _candidate_row(record_generation=2)
        _queue_happy_path(conn, candidate_row=candidate)

        _confirm(conn)

        stable_doc_insert = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        # document_version is $5 (args[4]).
        assert stable_doc_insert.args[4] == 2
