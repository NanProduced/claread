"""Focused tests for D6-I2B Stable Document Freeze Persistence.

These tests use a fake asyncpg connection recorder to assert SQL order
and parameters without requiring a real database. This keeps the tests
fast, hermetic, and free of any dependency on the dirty repository.py
or existing DB test harnesses.

Test coverage:
    * Happy-path freeze: stable_reading_documents + stable_document_blocks
      + reading_bases + reading_records.active_base_id update.
    * Idempotency: existing same (record, generation, content_sha256)
      -> no-op, returns existing.
    * Fail-closed: existing same (record, generation) but different
      content_sha256 -> raises.
    * Generation fence violation -> raises.
    * Candidate confirmation guarded by (record, generation, user_id).
    * parent_block_id is the block_id string, NOT a row UUID.
    * reading_bases.text uses plan.canonical_text (not preview/composer).
    * reading_bases.content_sha256 = sha256(canonical_text), NOT
      plan.content_sha256 (block-level hash).
    * interpretation_policy_json is always present in block insert params
      and is never an empty dict.
    * Plan and blocks are not mutated.
    * Pure helpers (sha256, utf16_length) for emoji / surrogate pairs.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.schemas.reader_documents import (
    StableDocumentBlock,
    StableDocumentInterpretationPolicy,
)
from app.services.reader_orchestration.document_freeze_plan import (
    CANONICAL_TEXT_BLOCK_SEPARATOR,
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceError,
    StableDocumentFreezePersistenceResult,
    compute_canonical_text_sha256,
    compute_canonical_text_utf16_length,
    persist_stable_document_freeze_plan,
)


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
    result), consumed once on the first matching query. This avoids
    the FIFO-queue problem where INSERT calls would consume queued
    UPDATE results.
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

        The override is consumed once (popped) when matched. Tests can
        register multiple overrides; they are checked in registration
        order on each execute() call.
        """
        self._execute_overrides.append((sql_substring, result))

    # -- asyncpg-compatible interface --

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(_RecordedCall("execute", query, args))
        # Check substring overrides first (consumed once on match).
        for i, (substr, result) in enumerate(self._execute_overrides):
            if substr in query:
                self._execute_overrides.pop(i)
                return result
        # Default by SQL verb.
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

    def calls_matching(self, sql_substring: str) -> list[_RecordedCall]:
        return [c for c in self.calls if sql_substring in c.query]


# --------------------------------------------------------------------
# Plan builders
# --------------------------------------------------------------------


_RECORD_ID = "00000000-0000-0000-0000-000000000001"
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000002")
_USER_ID = UUID("00000000-0000-0000-0000-000000000003")


def _paragraph(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="paragraph",
        text_content=text,
    )


def _heading(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="heading",
        text_content=text,
    )


def _table(block_id: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table",
        text_content=None,
        payload_json={"rows": 2, "cols": 2},
    )


def _table_cell(
    block_id: str, text: str, order: int, parent: str
) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="table_cell",
        text_content=text,
        parent_block_id=parent,
    )


def _build_simple_plan() -> Any:
    return build_stable_document_freeze_plan(
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        title="Test Article",
        blocks=[
            _heading("h1", "Title", 0),
            _paragraph("p1", "First paragraph.", 1),
            _paragraph("p2", "Second paragraph.", 2),
        ],
    )


def _build_plan_with_table() -> Any:
    return build_stable_document_freeze_plan(
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        title="Table Article",
        blocks=[
            _heading("h1", "Title", 0),
            _table("t1", 1),
            _table_cell("t1_c1", "Cell A", 2, "t1"),
            _paragraph("p1", "Body text.", 3),
        ],
    )


def _persist(
    conn: FakeConn,
    plan: Any | None = None,
    *,
    candidate_document_id: UUID | None = None,
    user_id: UUID | None = None,
    language: str | None = "en",
) -> StableDocumentFreezePersistenceResult:
    import asyncio

    plan = plan or _build_simple_plan()
    return asyncio.run(
        persist_stable_document_freeze_plan(
            conn,
            plan=plan,
            canonicalizer_version="test_canonicalizer_v1",
            builder_version="test_builder_v1",
            segmenter_version="test_segmenter_v1",
            language=language,
            candidate_document_id=candidate_document_id,
            user_id=user_id,
            now=datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        )
    )


# --------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------


class TestPureHelpers:
    def test_compute_canonical_text_sha256_matches_sha256_of_text(self) -> None:
        text = "Hello, world!"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert compute_canonical_text_sha256(text) == expected
        assert len(compute_canonical_text_sha256(text)) == 64

    def test_compute_canonical_text_sha256_changes_when_text_changes(self) -> None:
        a = compute_canonical_text_sha256("AAA")
        b = compute_canonical_text_sha256("AAB")
        assert a != b

    def test_compute_canonical_text_utf16_length_uses_utf16_code_units(self) -> None:
        assert compute_canonical_text_utf16_length("abc") == 3
        assert compute_canonical_text_utf16_length("") == 0
        # Emoji: 😀 is U+1F600, a surrogate pair in UTF-16 -> 2 code units.
        assert compute_canonical_text_utf16_length("😀") == 2
        # Mixed: a + emoji + b = 1 + 2 + 1 = 4 code units.
        assert compute_canonical_text_utf16_length("a😀b") == 4

    def test_canonical_text_sha256_is_distinct_from_block_level_hash(self) -> None:
        plan = _build_simple_plan()
        # plan.content_sha256 is the block-level hash.
        # compute_canonical_text_sha256 hashes the canonical TEXT.
        assert plan.content_sha256 != compute_canonical_text_sha256(
            plan.canonical_text
        )


# --------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------


class TestHappyPath:
    def test_returns_result_with_expected_fields(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        # supersede prior active (none) -> override; fence uses default "UPDATE 1"
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        result = _persist(conn, plan)

        assert isinstance(result, StableDocumentFreezePersistenceResult)
        assert result.reading_record_id == UUID(_RECORD_ID)
        assert result.record_generation == 1
        assert result.document_version == 1
        assert result.content_sha256 == plan.content_sha256
        assert result.block_count == 3
        assert result.candidate_confirmed is False
        assert result.idempotent_noop is False
        assert result.base_id is not None
        assert result.stable_document_id is not None

    def test_sql_order_is_idempotency_then_supersede_then_inserts_then_fence(
        self,
    ) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        execute_calls = conn.execute_calls
        # Order: supersede stable_docs, stable_doc insert, 3 block inserts,
        # supersede reading_bases, reading_bases insert, fence update.
        assert len(execute_calls) == 8

        assert "stable_reading_documents" in execute_calls[0].query
        assert "status = 'superseded'" in execute_calls[0].query

        assert "INSERT INTO stable_reading_documents" in execute_calls[1].query

        for i in range(2, 5):
            assert "INSERT INTO stable_document_blocks" in execute_calls[i].query

        # NEW: supersede prior active reading_bases before INSERT.
        assert "UPDATE reading_bases" in execute_calls[5].query
        assert "status = 'superseded'" in execute_calls[5].query
        assert "AND status = 'active'" in execute_calls[5].query

        assert "INSERT INTO reading_bases" in execute_calls[6].query

        assert "UPDATE reading_records" in execute_calls[7].query
        assert "active_base_id" in execute_calls[7].query

    def test_reading_bases_text_uses_plan_canonical_text(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # The 5th positional arg ($5) is the text column.
        text_param = reading_bases_call.args[4]
        assert text_param == plan.canonical_text
        # Ensure it's NOT the preview/composer output (which would
        # contain structural markers for non-textual blocks).
        assert "[[structural:" not in text_param

    def test_reading_bases_content_sha256_is_sha256_of_canonical_text(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $6 is content_sha256.
        sha_param = reading_bases_call.args[5]
        expected = hashlib.sha256(plan.canonical_text.encode("utf-8")).hexdigest()
        assert sha_param == expected
        # Must NOT be the block-level hash.
        assert sha_param != plan.content_sha256

    def test_reading_bases_content_utf16_length_is_correct(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $7 is content_utf16_length.
        length_param = reading_bases_call.args[6]
        assert length_param == utf16_code_unit_length(plan.canonical_text)

    def test_reading_bases_navigation_json_has_empty_units(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $13 is navigation_json (passed through jsonb_param).
        nav_param = reading_bases_call.args[12]
        assert nav_param == {"units": []}

    def test_generation_fence_uses_generation_in_where_clause(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        fence_call = next(
            c for c in conn.execute_calls if "UPDATE reading_records" in c.query
        )
        assert "generation = $4" in fence_call.query
        # $4 is the generation value.
        assert fence_call.args[3] == 1


# --------------------------------------------------------------------
# Block insert params
# --------------------------------------------------------------------


class TestBlockInsertParams:
    def test_interpretation_policy_json_always_present_in_block_insert(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_simple_plan())

        block_calls = [
            c for c in conn.execute_calls if "INSERT INTO stable_document_blocks" in c.query
        ]
        assert len(block_calls) == 3

        for call in block_calls:
            # $12 is interpretation_policy_json (via jsonb_param).
            policy_param = call.args[11]
            assert policy_param is not None
            # Must NOT be an empty dict — that would be the DB default
            # placeholder which silently routes as main_reading.
            assert policy_param != {}
            assert "default_route" in policy_param
            assert "allowed_source_scope" in policy_param
            assert "rag_eligible" in policy_param

    def test_interpretation_policy_json_reflects_block_type_defaults(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_table())

        block_calls = [
            c for c in conn.execute_calls if "INSERT INTO stable_document_blocks" in c.query
        ]
        # h1 (heading), t1 (table), t1_c1 (table_cell), p1 (paragraph)
        assert len(block_calls) == 4

        policies_by_block_id: dict[str, dict] = {}
        for call in block_calls:
            # $3 is block_id, $12 is interpretation_policy_json.
            policies_by_block_id[call.args[2]] = call.args[11]

        # heading -> main_reading
        assert policies_by_block_id["h1"]["default_route"] == "main_reading"
        # table -> metadata_only
        assert policies_by_block_id["t1"]["default_route"] == "metadata_only"
        assert policies_by_block_id["t1"]["rag_eligible"] is False
        # table_cell -> rag_ask_only
        assert policies_by_block_id["t1_c1"]["default_route"] == "rag_ask_only"
        # paragraph -> main_reading
        assert policies_by_block_id["p1"]["default_route"] == "main_reading"

    def test_parent_block_id_is_block_id_string_not_uuid(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_table())

        block_calls = [
            c for c in conn.execute_calls if "INSERT INTO stable_document_blocks" in c.query
        ]
        # Find the table_cell block (t1_c1), which has parent_block_id = "t1".
        cell_call = next(c for c in block_calls if c.args[2] == "t1_c1")
        # $4 is parent_block_id.
        parent_param = cell_call.args[3]
        assert parent_param == "t1"
        assert isinstance(parent_param, str)
        # Must NOT be a UUID string.
        assert parent_param != str(_RECORD_ID)

    def test_canonical_offsets_written_for_main_reading_blocks(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_table()
        _persist(conn, plan)

        block_calls = [
            c for c in conn.execute_calls if "INSERT INTO stable_document_blocks" in c.query
        ]

        plan_blocks_by_id = {b.block_id: b for b in plan.blocks}
        for call in block_calls:
            block_id = call.args[2]
            # $10 is canonical_text_start_utf16, $11 is canonical_text_end_utf16.
            start_param = call.args[9]
            end_param = call.args[10]
            plan_block = plan_blocks_by_id[block_id]

            if plan_block.canonical_text_start_utf16 is not None:
                assert start_param == plan_block.canonical_text_start_utf16
                assert end_param == plan_block.canonical_text_end_utf16
            else:
                assert start_param is None
                assert end_param is None

    def test_block_count_matches_plan(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_table()
        result = _persist(conn, plan)

        assert result.block_count == 4


# --------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------


class TestIdempotency:
    def test_same_generation_same_hash_returns_existing_no_writes(self) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        # First fetchrow: existing stable doc with same hash.
        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        # Second fetchrow: reading_records.active_base_id.
        conn.queue_fetchrow({
            "active_base_id": existing_base_id,
        })

        result = _persist(conn, plan)

        assert result.idempotent_noop is True
        assert result.stable_document_id == existing_id
        assert result.base_id == existing_base_id
        assert result.content_sha256 == plan.content_sha256
        assert result.candidate_confirmed is False

        # No execute calls should have been made (no writes).
        assert len(conn.execute_calls) == 0
        # Two fetchrow calls: existing doc + active_base_id lookup.
        assert len(conn.fetchrow_calls) == 2

    def test_same_generation_same_hash_with_null_base_id_fails_closed(self) -> None:
        """A NULL active_base_id in the idempotent branch means the prior
        freeze was interrupted before setting the active base. Refuse to
        return a partial result; raise instead.
        """
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")

        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        # active_base_id is None (prior freeze was interrupted).
        conn.queue_fetchrow({
            "active_base_id": None,
        })

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"active_base_id is NULL",
        ):
            _persist(conn, plan)

        # No writes should have been attempted.
        assert len(conn.execute_calls) == 0

    def test_same_generation_same_hash_with_missing_record_row_fails_closed(
        self,
    ) -> None:
        """If reading_records row itself is missing (generation mismatch
        / deleted record), the idempotent branch must also fail closed.
        """
        conn = FakeConn()
        plan = _build_simple_plan()

        conn.queue_fetchrow({
            "id": UUID("aaaaaaaa-0000-0000-0000-000000000001"),
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        # reading_records row not found (None).
        conn.queue_fetchrow(None)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"active_base_id is NULL",
        ):
            _persist(conn, plan)

    def test_same_generation_different_hash_fails_closed(self) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        conn.queue_fetchrow({
            "id": UUID("aaaaaaaa-0000-0000-0000-000000000001"),
            "content_sha256": "0" * 64,  # different hash
            "status": "active",
            "document_version": 1,
        })

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"differs from the plan's content_sha256",
        ):
            _persist(conn, plan)

        # No writes should have been attempted.
        assert len(conn.execute_calls) == 0


# --------------------------------------------------------------------
# Generation fence
# --------------------------------------------------------------------


class TestGenerationFence:
    def test_fence_violation_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # Fence update returns "UPDATE 0" (generation mismatch).
        conn.set_execute_result("UPDATE reading_records", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"Generation fence violation",
        ):
            _persist(conn)

    def test_fence_update_includes_generation_in_where_clause(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        fence_call = next(
            c for c in conn.execute_calls if "UPDATE reading_records" in c.query
        )
        assert "AND generation = $4" in fence_call.query
        # The 4th arg ($4) is the generation.
        assert fence_call.args[3] == plan.stable_document.record_generation


# --------------------------------------------------------------------
# Candidate confirmation
# --------------------------------------------------------------------


class TestCandidateConfirmation:
    def test_candidate_confirmed_with_record_and_generation_guard(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "ready"})  # candidate status lookup
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # fence and candidate update use default "UPDATE 1"

        result = _persist(
            conn,
            candidate_document_id=_CANDIDATE_ID,
            user_id=_USER_ID,
        )

        assert result.candidate_confirmed is True

        candidate_call = next(
            c for c in conn.execute_calls
            if "UPDATE candidate_reading_documents" in c.query
        )
        # WHERE id = $1 AND reading_record_id = $2 AND record_generation = $4 AND user_id = $5
        assert "reading_record_id = $2" in candidate_call.query
        assert "record_generation = $4" in candidate_call.query
        assert "user_id = $5" in candidate_call.query
        assert "AND status = 'ready'" in candidate_call.query
        assert candidate_call.args[0] == _CANDIDATE_ID
        assert candidate_call.args[1] == UUID(_RECORD_ID)
        assert candidate_call.args[3] == 1  # record_generation
        assert candidate_call.args[4] == _USER_ID

    def test_candidate_confirmed_without_user_id_guard(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "ready"})  # candidate status lookup
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # fence and candidate update use default "UPDATE 1"

        result = _persist(
            conn,
            candidate_document_id=_CANDIDATE_ID,
            user_id=None,
        )

        assert result.candidate_confirmed is True

        candidate_call = next(
            c for c in conn.execute_calls
            if "UPDATE candidate_reading_documents" in c.query
        )
        # Without user_id, the WHERE clause should not include user_id.
        assert "user_id" not in candidate_call.query
        assert "reading_record_id = $2" in candidate_call.query
        assert "record_generation = $4" in candidate_call.query
        assert "AND status = 'ready'" in candidate_call.query

    def test_no_candidate_id_skips_candidate_update(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # fence uses default "UPDATE 1"

        result = _persist(conn, candidate_document_id=None)

        assert result.candidate_confirmed is False
        # No candidate update call and no candidate fetchrow.
        assert not any(
            "candidate_reading_documents" in c.query for c in conn.execute_calls
        )
        assert not any(
            "candidate_reading_documents" in c.query for c in conn.fetchrow_calls
        )


# --------------------------------------------------------------------
# Non-mutation
# --------------------------------------------------------------------


class TestNonMutation:
    def test_plan_not_mutated_after_persist(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        original_blocks = [b.model_copy(deep=True) for b in plan.blocks]
        original_canonical_text = plan.canonical_text
        original_content_sha256 = plan.content_sha256

        _persist(conn, plan)

        assert plan.canonical_text == original_canonical_text
        assert plan.content_sha256 == original_content_sha256
        assert len(plan.blocks) == len(original_blocks)
        for original, actual in zip(original_blocks, plan.blocks):
            assert original.block_id == actual.block_id
            assert original.canonical_text_start_utf16 == actual.canonical_text_start_utf16
            assert original.canonical_text_end_utf16 == actual.canonical_text_end_utf16
            assert original.text_content == actual.text_content

    def test_block_nested_dicts_not_aliased_with_plan(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_table()
        # Get the table block's payload_json reference before persist.
        table_block = next(b for b in plan.blocks if b.block_id == "t1")
        original_payload = table_block.payload_json

        _persist(conn, plan)

        # Plan's block payload should be unchanged.
        assert table_block.payload_json == original_payload
        assert table_block.payload_json == {"rows": 2, "cols": 2}


# --------------------------------------------------------------------
# Supersede prior active
# --------------------------------------------------------------------


class TestSupersedePriorActive:
    def test_supersede_update_targets_active_status(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        # 2 prior active rows superseded; fence uses default "UPDATE 1"
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 2")

        _persist(conn)

        supersede_call = conn.execute_calls[0]
        assert "UPDATE stable_reading_documents" in supersede_call.query
        assert "status = 'superseded'" in supersede_call.query
        assert "AND status = 'active'" in supersede_call.query
        assert "reading_record_id = $1" in supersede_call.query

    def test_supersede_happens_before_insert(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        supersede_call = conn.execute_calls[0]
        insert_call = conn.execute_calls[1]

        assert "UPDATE stable_reading_documents" in supersede_call.query
        assert "INSERT INTO stable_reading_documents" in insert_call.query


# --------------------------------------------------------------------
# Stable document insert params
# --------------------------------------------------------------------


class TestStableDocumentInsert:
    def test_stable_doc_insert_uses_plan_fields(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        insert_call = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        # $1=id (UUID), $2=reading_record_id, $3=record_generation,
        # $4=title, $5=document_version, $6=source_profile_json,
        # $7=content_sha256, $8=frozen_at
        assert insert_call.args[1] == UUID(_RECORD_ID)
        assert insert_call.args[2] == 1  # record_generation
        assert insert_call.args[3] == "Test Article"
        assert insert_call.args[4] == 1  # document_version
        assert insert_call.args[6] == plan.content_sha256

    def test_stable_doc_status_is_active_in_sql(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        insert_call = next(
            c for c in conn.execute_calls
            if "INSERT INTO stable_reading_documents" in c.query
        )
        assert "'active'" in insert_call.query


# --------------------------------------------------------------------
# Transaction validation (Fix 4)
# --------------------------------------------------------------------


class TestTransactionValidation:
    """Fix 4: Function entry validates conn.is_in_transaction(); fails
    closed if not in a transaction to prevent half-frozen documents."""

    def test_not_in_transaction_raises_at_entry(self) -> None:
        conn = FakeConn(in_transaction=False)
        plan = _build_simple_plan()

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"must be called within an active transaction",
        ):
            _persist(conn, plan)

        # No SQL should have been executed at all.
        assert len(conn.calls) == 0

    def test_in_transaction_proceeds_normally(self) -> None:
        conn = FakeConn(in_transaction=True)
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        result = _persist(conn)
        assert result.idempotent_noop is False
        assert len(conn.execute_calls) == 8


# --------------------------------------------------------------------
# reading_bases supersede (Fix 1)
# --------------------------------------------------------------------


class TestReadingBasesSupersede:
    """Fix 1: Supersede prior active reading_bases BEFORE INSERT to
    avoid violating the ``uq_reading_bases_active_record`` unique
    partial index (migration 0001, line 795):

        CREATE UNIQUE INDEX uq_reading_bases_active_record
          ON reading_bases(reading_record_id)
          WHERE status = 'active';

    Without this UPDATE, the INSERT would collide with the prior
    active base and the transaction would abort.
    """

    def test_supersede_reading_bases_targets_active_status(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        supersede_call = next(
            c for c in conn.execute_calls if "UPDATE reading_bases" in c.query
        )
        assert "status = 'superseded'" in supersede_call.query
        assert "AND status = 'active'" in supersede_call.query
        assert "reading_record_id = $1" in supersede_call.query

    def test_supersede_reading_bases_happens_before_insert(self) -> None:
        """The UPDATE reading_bases ... SET status='superseded' must
        come BEFORE the INSERT INTO reading_bases. Otherwise the INSERT
        would collide with the prior active base under
        uq_reading_bases_active_record.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        execute_calls = conn.execute_calls
        supersede_idx = next(
            i for i, c in enumerate(execute_calls)
            if "UPDATE reading_bases" in c.query and "status = 'superseded'" in c.query
        )
        insert_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_bases" in c.query
        )
        assert supersede_idx < insert_idx

    def test_supersede_reading_bases_mitigates_uq_active_record_constraint(
        self,
    ) -> None:
        """Reference / cover the uq_reading_bases_active_record risk.

        The unique partial index allows only ONE active base per
        reading_record_id. If a prior freeze left an active base and
        we INSERT a new one without superseding, the DB would raise a
        unique violation. The supersede UPDATE prevents this by setting
        the prior base's status to 'superseded' (excluded from the
        partial index) before the INSERT.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # Prior active base exists; supersede affects 1 row.
        conn.set_execute_result("UPDATE reading_bases", "UPDATE 1")

        _persist(conn)

        # Both the supersede and the INSERT must have happened.
        supersede_call = next(
            c for c in conn.execute_calls
            if "UPDATE reading_bases" in c.query and "status = 'superseded'" in c.query
        )
        insert_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        assert supersede_call is not None
        assert insert_call is not None
        # The INSERT itself uses status='active', which would collide
        # if the supersede had not run first.
        assert "'active'" in insert_call.query


# --------------------------------------------------------------------
# Candidate state machine (Fix 2)
# --------------------------------------------------------------------


class TestCandidateStateMachine:
    """Fix 2: Candidate confirmation is state-machine safe.

    Valid transitions:
        ready     -> confirmed  (UPDATE with AND status='ready')
        confirmed -> confirmed  (idempotent success, no write)

    Invalid (raise):
        rejected  -> confirmed
        superseded -> confirmed
        <unknown> -> confirmed
        not found  (no fetchrow result)
    """

    def test_candidate_already_confirmed_is_idempotent_success(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "confirmed"})  # candidate already confirmed
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        result = _persist(
            conn,
            candidate_document_id=_CANDIDATE_ID,
            user_id=_USER_ID,
        )

        assert result.candidate_confirmed is True
        # No UPDATE should have been issued for the candidate (already confirmed).
        assert not any(
            "UPDATE candidate_reading_documents" in c.query
            for c in conn.execute_calls
        )
        # But the fetchrow status lookup must have happened.
        assert any(
            "candidate_reading_documents" in c.query for c in conn.fetchrow_calls
        )

    def test_candidate_rejected_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "rejected"})  # candidate rejected
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"cannot confirm.*Only candidates in status='ready'",
        ):
            _persist(
                conn,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_candidate_superseded_raises(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "superseded"})  # candidate superseded
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"cannot confirm.*Only candidates in status='ready'",
        ):
            _persist(
                conn,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_candidate_not_found_raises(self) -> None:
        """Candidate lookup returns None (wrong record / generation /
        user, or candidate does not exist). Must fail closed.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        # Candidate fetchrow returns None (not found / guard mismatch).
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"Candidate document not found",
        ):
            _persist(
                conn,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_candidate_in_unexpected_status_raises(self) -> None:
        """An unknown status value (e.g. a future status) must fail
        closed rather than silently confirming.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "draft"})  # unexpected status
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"unexpected status='draft'",
        ):
            _persist(
                conn,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_candidate_update_affects_zero_rows_raises(self) -> None:
        """Status was 'ready' at fetchrow time but the UPDATE returned
        'UPDATE 0' (concurrent transition between fetch and update).
        Must fail closed.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "ready"})  # candidate ready
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")
        # Candidate UPDATE returns 0 rows (race condition).
        conn.set_execute_result("candidate_reading_documents", "UPDATE 0")

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"Candidate document confirmation failed",
        ):
            _persist(
                conn,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_candidate_lookup_uses_record_generation_and_user_guards(self) -> None:
        """The candidate status fetchrow must include
        reading_record_id, record_generation, and (when provided)
        user_id in the WHERE clause so a candidate belonging to a
        different record/generation/user is treated as not found.
        """
        conn = FakeConn()
        conn.queue_fetchrow(None)  # no existing stable doc
        conn.queue_fetchrow({"status": "ready"})
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(
            conn,
            candidate_document_id=_CANDIDATE_ID,
            user_id=_USER_ID,
        )

        lookup_call = next(
            c for c in conn.fetchrow_calls
            if "candidate_reading_documents" in c.query
        )
        assert "reading_record_id = $2" in lookup_call.query
        assert "record_generation = $3" in lookup_call.query
        assert "user_id = $4" in lookup_call.query
        assert lookup_call.args[0] == _CANDIDATE_ID
        assert lookup_call.args[1] == UUID(_RECORD_ID)
        assert lookup_call.args[2] == 1  # record_generation
        assert lookup_call.args[3] == _USER_ID


# --------------------------------------------------------------------
# Idempotent branch candidate confirmation (Fix 2 + Fix 3)
# --------------------------------------------------------------------


class TestIdempotentBranchCandidateConfirmation:
    """Fix 2 + Fix 3: The same-hash idempotent stable-doc branch must
    still execute/validate candidate confirmation if a
    candidate_document_id is provided. This handles the case where a
    prior freeze committed the stable document + reading_bases but
    was interrupted before confirming the candidate.

    Fix 3: If reading_records.active_base_id is NULL in this branch,
    fail closed (interrupted prior freeze).
    """

    def test_idempotent_stable_doc_with_ready_candidate_confirms_candidate(
        self,
    ) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        # fetchrow #1: existing stable doc with same hash.
        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        # fetchrow #2: reading_records.active_base_id (non-null).
        conn.queue_fetchrow({"active_base_id": existing_base_id})
        # fetchrow #3: candidate status lookup (ready).
        conn.queue_fetchrow({"status": "ready"})

        result = _persist(
            conn,
            plan,
            candidate_document_id=_CANDIDATE_ID,
            user_id=_USER_ID,
        )

        assert result.idempotent_noop is True
        assert result.stable_document_id == existing_id
        assert result.base_id == existing_base_id
        assert result.candidate_confirmed is True

        # No stable_doc / block / reading_bases writes should have happened.
        assert not any(
            "INSERT INTO stable_reading_documents" in c.query
            for c in conn.execute_calls
        )
        assert not any(
            "INSERT INTO stable_document_blocks" in c.query
            for c in conn.execute_calls
        )
        assert not any(
            "INSERT INTO reading_bases" in c.query for c in conn.execute_calls
        )
        # But the candidate UPDATE must have been issued.
        assert any(
            "UPDATE candidate_reading_documents" in c.query
            for c in conn.execute_calls
        )

    def test_idempotent_stable_doc_with_already_confirmed_candidate_is_idempotent(
        self,
    ) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        conn.queue_fetchrow({"active_base_id": existing_base_id})
        # Candidate already confirmed in prior freeze.
        conn.queue_fetchrow({"status": "confirmed"})

        result = _persist(
            conn,
            plan,
            candidate_document_id=_CANDIDATE_ID,
            user_id=_USER_ID,
        )

        assert result.idempotent_noop is True
        assert result.candidate_confirmed is True
        # No writes at all (idempotent stable doc + already-confirmed candidate).
        assert len(conn.execute_calls) == 0

    def test_idempotent_stable_doc_with_rejected_candidate_raises(self) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        conn.queue_fetchrow({"active_base_id": existing_base_id})
        # Candidate is rejected; cannot confirm.
        conn.queue_fetchrow({"status": "rejected"})

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"cannot confirm.*Only candidates in status='ready'",
        ):
            _persist(
                conn,
                plan,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

    def test_idempotent_stable_doc_with_null_base_id_and_candidate_raises(
        self,
    ) -> None:
        """Fix 3: NULL active_base_id in idempotent branch must fail
        closed BEFORE attempting candidate confirmation. The prior
        freeze was interrupted; we must not return a partial result.
        """
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")

        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        conn.queue_fetchrow({"active_base_id": None})

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"active_base_id is NULL",
        ):
            _persist(
                conn,
                plan,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

        # No writes should have been attempted.
        assert len(conn.execute_calls) == 0
        # Candidate status lookup must NOT have happened (we failed
        # closed before reaching candidate confirmation).
        assert not any(
            "candidate_reading_documents" in c.query for c in conn.fetchrow_calls
        )

    def test_idempotent_stable_doc_without_candidate_skips_candidate_lookup(
        self,
    ) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        conn.queue_fetchrow({
            "id": existing_id,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        conn.queue_fetchrow({"active_base_id": existing_base_id})

        result = _persist(conn, plan, candidate_document_id=None)

        assert result.idempotent_noop is True
        assert result.candidate_confirmed is False
        # No candidate lookup or update.
        assert not any(
            "candidate_reading_documents" in c.query for c in conn.fetchrow_calls
        )
        assert not any(
            "candidate_reading_documents" in c.query for c in conn.execute_calls
        )
