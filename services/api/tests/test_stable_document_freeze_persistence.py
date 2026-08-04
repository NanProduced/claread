# task-history: D6-I2 (renamed from test_d6_i2_stable_document_freeze_persistence.py)
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

from app.contracts.annotation import (
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.schemas.reader_documents import (
    StableDocumentBlock,
    StableDocumentInterpretationPolicy,
)
from app.services.reader_orchestration.base_builder import (
    EXACT_CANONICAL_TEXT_VERSION,
    build_reading_base_from_canonical_text,
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

    @property
    def fetchval_calls(self) -> list[_RecordedCall]:
        return [c for c in self.calls if c.kind == "fetchval"]

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
            segmenter_version="regex_sentence_clause_window_v1",
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
        # Simple plan: 3 blocks (heading + 2 paragraphs) -> 3 units +
        # 3 anchor segments. Total execute calls:
        #   1 supersede stable_docs
        # + 1 INSERT stable_doc
        # + 3 INSERT stable_document_blocks
        # + 1 supersede reading_bases
        # + 1 INSERT reading_bases
        # + 3 INSERT reading_units
        # + 3 INSERT anchor_segments
        # + 1 UPDATE reading_records (fence)
        # = 14
        assert len(execute_calls) == 14

        assert "stable_reading_documents" in execute_calls[0].query
        assert "status = 'superseded'" in execute_calls[0].query

        assert "INSERT INTO stable_reading_documents" in execute_calls[1].query

        for i in range(2, 5):
            assert "INSERT INTO stable_document_blocks" in execute_calls[i].query

        # supersede prior active reading_bases before INSERT.
        assert "UPDATE reading_bases" in execute_calls[5].query
        assert "status = 'superseded'" in execute_calls[5].query
        assert "AND status = 'active'" in execute_calls[5].query

        assert "INSERT INTO reading_bases" in execute_calls[6].query

        # reading_units inserts (3 units for simple plan).
        for i in range(7, 10):
            assert "INSERT INTO reading_units" in execute_calls[i].query

        # anchor_segments inserts (3 segments for simple plan).
        for i in range(10, 13):
            assert "INSERT INTO anchor_segments" in execute_calls[i].query

        # fence update is last.
        assert "UPDATE reading_records" in execute_calls[13].query
        assert "active_base_id" in execute_calls[13].query

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

    def test_reading_bases_navigation_json_has_non_empty_units(self) -> None:
        """D6-I2C: navigation_json must come from the build result's
        navigation_units, NOT the placeholder {"units": []}."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_simple_plan()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $13 is navigation_json (passed through jsonb_param).
        nav_param = reading_bases_call.args[12]
        assert isinstance(nav_param, dict)
        assert "units" in nav_param
        units = nav_param["units"]
        assert isinstance(units, list)
        # Simple plan has 3 blocks -> 3 units.
        assert len(units) == 3
        for unit in units:
            assert "unit_id" in unit
            assert "order_index" in unit
            assert "unit_type" in unit
            assert "boundary_quality" in unit
            assert "label" in unit
            assert "base_start_utf16" in unit
            assert "base_end_utf16" in unit

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
        # table -> main_reading (Markdown ecosystem refactor D2 / A1),
        # still not rag_eligible (structural wrapper, no text_content).
        assert policies_by_block_id["t1"]["default_route"] == "main_reading"
        assert policies_by_block_id["t1"]["rag_eligible"] is False
        # table_cell -> main_reading (Markdown ecosystem refactor D2 / A1)
        assert policies_by_block_id["t1_c1"]["default_route"] == "main_reading"
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
    def test_same_generation_same_hash_with_complete_state_no_mutation_writes_but_performs_validation_reads(
        self,
    ) -> None:
        """Same-hash idempotent branch: NO mutation writes, but DOES
        perform completeness validation reads (active base row, units
        count, segments count) before returning idempotent_noop=True.
        """
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
        # fetchrow #3: active reading_bases row with matching hash /
        # utf16 length / non-empty navigation_json.
        conn.queue_fetchrow({
            "id": existing_base_id,
            "reading_record_id": UUID(_RECORD_ID),
            "record_generation": 1,
            "status": "active",
            "content_sha256": compute_canonical_text_sha256(
                plan.canonical_text
            ),
            "content_utf16_length": compute_canonical_text_utf16_length(
                plan.canonical_text
            ),
            "navigation_json": {
                "units": [
                    {
                        "unit_id": "u1",
                        "order_index": 1,
                        "unit_type": "heading",
                        "boundary_quality": "normal",
                        "label": "Title",
                        "base_start_utf16": 0,
                        "base_end_utf16": 5,
                    }
                ]
            },
        })
        # fetchval #1: COUNT(*) of reading_units (> 0).
        conn.queue_fetchval(3)
        # fetchval #2: COUNT(*) of anchor_segments (> 0).
        conn.queue_fetchval(3)

        result = _persist(conn, plan)

        assert result.idempotent_noop is True
        assert result.stable_document_id == existing_id
        assert result.base_id == existing_base_id
        assert result.content_sha256 == plan.content_sha256
        assert result.candidate_confirmed is False

        # NO mutation writes (no INSERT / UPDATE / DELETE).
        assert len(conn.execute_calls) == 0
        # Validation reads: 3 fetchrow (stable doc + active_base_id +
        # active base row) + 2 fetchval (units count + segments count).
        assert len(conn.fetchrow_calls) == 3
        assert len(conn.fetchval_calls) == 2

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
        for original, actual in zip(original_blocks, plan.blocks, strict=False):
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
        # 14 = 8 (old) + 3 reading_units + 3 anchor_segments for the
        # simple plan (3 blocks -> 3 units + 3 segments).
        assert len(conn.execute_calls) == 14


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


def _queue_complete_idempotent_state(
    conn: FakeConn,
    plan: Any,
    *,
    existing_id: UUID,
    existing_base_id: UUID,
) -> None:
    """Queue fetchrow/fetchval results for a COMPLETE idempotent state.

    Queues the full completeness-validation read sequence:
        fetchrow #1: existing stable doc with same hash
        fetchrow #2: reading_records.active_base_id (non-null)
        fetchrow #3: active reading_bases row (matching hash/utf16/navigation)
        fetchval #1: COUNT(*) of reading_units (> 0)
        fetchval #2: COUNT(*) of anchor_segments (> 0)

    Tests that also exercise candidate confirmation should queue the
    candidate status fetchrow AFTER calling this helper.
    """
    conn.queue_fetchrow({
        "id": existing_id,
        "content_sha256": plan.content_sha256,
        "status": "active",
        "document_version": 1,
    })
    conn.queue_fetchrow({"active_base_id": existing_base_id})
    conn.queue_fetchrow({
        "id": existing_base_id,
        "reading_record_id": UUID(_RECORD_ID),
        "record_generation": 1,
        "status": "active",
        "content_sha256": compute_canonical_text_sha256(
            plan.canonical_text
        ),
        "content_utf16_length": compute_canonical_text_utf16_length(
            plan.canonical_text
        ),
        "navigation_json": {
            "units": [
                {
                    "unit_id": "u1",
                    "order_index": 1,
                    "unit_type": "heading",
                    "boundary_quality": "normal",
                    "label": "Title",
                    "base_start_utf16": 0,
                    "base_end_utf16": 5,
                }
            ]
        },
    })
    conn.queue_fetchval(3)  # reading_units count
    conn.queue_fetchval(3)  # anchor_segments count


class TestIdempotentBranchCandidateConfirmation:
    """Fix 2 + Fix 3: The same-hash idempotent stable-doc branch must
    still execute/validate candidate confirmation if a
    candidate_document_id is provided. This handles the case where a
    prior freeze committed the stable document + reading_bases but
    was interrupted before confirming the candidate.

    Fix 3: If reading_records.active_base_id is NULL in this branch,
    fail closed (interrupted prior freeze).

    D6-I2C review fix: Completeness validation must pass BEFORE
    candidate confirmation. If the freeze state is incomplete, the
    candidate must NOT be confirmed.
    """

    def test_idempotent_stable_doc_with_ready_candidate_confirms_candidate(
        self,
    ) -> None:
        conn = FakeConn()
        plan = _build_simple_plan()

        existing_id = UUID("aaaaaaaa-0000-0000-0000-000000000001")
        existing_base_id = UUID("bbbbbbbb-0000-0000-0000-000000000002")

        # Queue complete idempotent state (5 reads), then candidate
        # status lookup (ready).
        _queue_complete_idempotent_state(
            conn, plan, existing_id=existing_id, existing_base_id=existing_base_id
        )
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

        _queue_complete_idempotent_state(
            conn, plan, existing_id=existing_id, existing_base_id=existing_base_id
        )
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

        _queue_complete_idempotent_state(
            conn, plan, existing_id=existing_id, existing_base_id=existing_base_id
        )
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

        _queue_complete_idempotent_state(
            conn, plan, existing_id=existing_id, existing_base_id=existing_base_id
        )

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


# --------------------------------------------------------------------
# D6-I2C: Reading Units / Anchor Segments / navigation_json
# --------------------------------------------------------------------


def _list_item(block_id: str, text: str, order: int) -> StableDocumentBlock:
    return StableDocumentBlock(
        block_id=block_id,
        order_index=order,
        block_type="list_item",
        text_content=text,
    )


def _build_plan_with_emoji_and_sentences() -> Any:
    """Plan with heading + paragraph with 2 sentences + paragraph
    with emoji. Produces 3 units and 4 anchor segments."""
    return build_stable_document_freeze_plan(
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        title="Emoji Article",
        blocks=[
            _heading("h1", "My Title", 0),
            _paragraph("p1", "First sentence. Second sentence!", 1),
            _paragraph("p2", "Emoji test \U0001f600 done.", 2),
        ],
    )


def _build_plan_with_list() -> Any:
    """Plan with heading + list_item + paragraph. The list_item text
    contains markdown list markers that the base builder classifies
    as a 'list' unit type."""
    return build_stable_document_freeze_plan(
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        title="List Article",
        blocks=[
            _heading("h1", "Title", 0),
            _list_item("li1", "- item one\n- item two\n- item three", 1),
            _paragraph("p1", "Closing paragraph.", 2),
        ],
    )


class TestReadingUnitsInsert:
    """D6-I2C: reading_units rows are inserted with correct params and
    UTF-16 offsets that round-trip to the canonical text."""

    def test_reading_units_count_matches_built_units(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        # 3 blocks -> 3 units.
        assert len(unit_calls) == 3

    def test_reading_units_params_and_utf16_offsets_round_trip(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        _persist(conn, plan)

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        canonical_text = plan.canonical_text

        for call in unit_calls:
            # SQL param order: reading_record_id, base_id, unit_id,
            # order_index, unit_type, boundary_quality,
            # base_start_utf16, base_end_utf16, text_hash, metadata_json
            assert call.args[0] == UUID(_RECORD_ID)
            assert isinstance(call.args[1], UUID)
            unit_id = call.args[2]
            assert isinstance(unit_id, str)
            assert unit_id.startswith("u")
            order_index = call.args[3]
            assert isinstance(order_index, int)
            assert order_index >= 1
            unit_type = call.args[4]
            # ``unit_type`` mirrors the DB CHECK constraint on
            # ``reading_units.unit_type`` (migration 0001): only the
            # 6 legacy heuristic values are accepted. The stable block
            # type (paragraph / list_item / blockquote / table* /
            # code_block) is carried by the separate
            # ``stable_block_type`` column — it MUST NOT be written to
            # ``unit_type``. Only ``heading`` overrides the heuristic
            # (downstream A6 skip / B4 outline key off
            # ``unit_type == "heading"``).
            assert unit_type in (
                "body",
                "heading",
                "list",
                "quote",
                "unknown",
                "fallback",
            )
            boundary_quality = call.args[5]
            assert boundary_quality in ("normal", "low")
            base_start = call.args[6]
            base_end = call.args[7]
            assert base_end > base_start
            text_hash = call.args[8]
            assert isinstance(text_hash, str)
            assert len(text_hash) == 8  # fnv1a32-utf16
            metadata = call.args[9]
            # R7-1: metadata_json records the actual sentence provider
            # for sentence-stage units (spaCy main path, named regex v2
            # fallback, or pinned regex v1); it stays empty for units
            # built by the clause / fallback-window stage.
            assert isinstance(metadata, dict)
            assert set(metadata) <= {"sentence_provider"}
            assert metadata.get("sentence_provider") in (
                None,
                "spacy_en_core_web_sm",
                "regex_v2",
                "regex_v1",
            )

            # UTF-16 offsets must slice back to the unit text from the
            # EXACT canonical text.
            unit_text = slice_by_utf16_offsets(canonical_text, base_start, base_end)
            assert unit_text  # must not be empty
            # Heading unit should contain the title text.
            # Body units should contain sentence text.

    def test_reading_units_order_index_is_sequential(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        order_indices = [c.args[3] for c in unit_calls]
        assert order_indices == [1, 2, 3]

    def test_reading_units_inserted_after_reading_bases_insert(self) -> None:
        """SQL order: reading_bases insert -> reading_units inserts."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        execute_calls = conn.execute_calls
        bases_insert_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_bases" in c.query
        )
        first_unit_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_units" in c.query
        )
        assert bases_insert_idx < first_unit_idx

    def test_reading_units_with_list_type(self) -> None:
        """A list_item block with markdown list markers should keep
        the legacy heuristic ``unit_type='list'`` (DB CHECK constraint
        on ``reading_units.unit_type`` only allows ``body`` / ``heading``
        / ``list`` / ``quote`` / ``unknown`` / ``fallback``), while the
        authoritative stable block type ``list_item`` is projected to
        the separate ``stable_block_type`` column on the navigation unit
        and the snapshot payload's ``stableBlockType`` field.

        A5: only ``heading`` overrides ``unit_type`` (because downstream
        A6 skip / B4 outline key off ``unit_type == "heading"``); all
        other stable block types keep the heuristic ``unit_type``."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_list())

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        unit_types = {c.args[2]: c.args[4] for c in unit_calls}
        # u2 is the list_item block. Heuristic ``_classify_unit_type``
        # detects all-list-lines → ``list`` (legacy allowed value).
        # ``unit_type`` MUST stay within the DB CHECK allowed set.
        assert unit_types["u2"] == "list"


class TestAnchorSegmentsInsert:
    """D6-I2C: anchor_segments rows are inserted with correct params
    and unit-local offsets that round-trip to the segment text."""

    def test_anchor_segments_count_matches_built_segments(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        segment_calls = [
            c for c in conn.execute_calls if "INSERT INTO anchor_segments" in c.query
        ]
        # 3 units: heading (1 segment) + paragraph with 2 sentences
        # (2 segments) + paragraph with emoji (1 segment) = 4 segments.
        assert len(segment_calls) == 4

    def test_anchor_segments_params_and_offsets_round_trip(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        _persist(conn, plan)

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        segment_calls = [
            c for c in conn.execute_calls if "INSERT INTO anchor_segments" in c.query
        ]
        canonical_text = plan.canonical_text

        # Build unit_id -> unit_text from the reading_units calls.
        unit_texts: dict[str, str] = {}
        for call in unit_calls:
            unit_id = call.args[2]
            base_start = call.args[6]
            base_end = call.args[7]
            unit_texts[unit_id] = slice_by_utf16_offsets(canonical_text, base_start, base_end)

        for call in segment_calls:
            # SQL param order: reading_record_id, base_id, unit_id,
            # anchor_segment_id, sentence_id, paragraph_id, order_index,
            # unit_order_index, segment_type, base_start_utf16,
            # base_end_utf16, unit_start_utf16, unit_end_utf16,
            # text_hash, boundary_quality
            assert call.args[0] == UUID(_RECORD_ID)
            assert isinstance(call.args[1], UUID)
            unit_id = call.args[2]
            assert unit_id in unit_texts
            anchor_segment_id = call.args[3]
            assert isinstance(anchor_segment_id, str)
            assert anchor_segment_id.startswith("s")
            sentence_id = call.args[4]
            assert sentence_id == anchor_segment_id
            paragraph_id = call.args[5]
            assert isinstance(paragraph_id, str)
            assert paragraph_id.startswith("p")
            order_index = call.args[6]
            assert order_index >= 1
            unit_order_index = call.args[7]
            assert unit_order_index >= 1
            segment_type = call.args[8]
            assert segment_type in ("sentence", "clause", "fallback_window")
            base_start = call.args[9]
            base_end = call.args[10]
            unit_start = call.args[11]
            unit_end = call.args[12]
            assert base_end > base_start
            assert unit_end > unit_start
            text_hash = call.args[13]
            assert isinstance(text_hash, str)
            assert len(text_hash) == 8
            boundary_quality = call.args[14]
            assert boundary_quality in ("normal", "low")
            # fallback_window segments must be low quality.
            if segment_type == "fallback_window":
                assert boundary_quality == "low"

            # Base offsets slice back to segment text from canonical text.
            segment_text_base = slice_by_utf16_offsets(canonical_text, base_start, base_end)
            assert segment_text_base  # must not be empty

            # Unit-local offsets slice back to the SAME text from the
            # unit's text.
            unit_text = unit_texts[unit_id]
            segment_text_unit = slice_by_utf16_offsets(unit_text, unit_start, unit_end)
            assert segment_text_unit == segment_text_base

    def test_anchor_segments_inserted_after_reading_units(self) -> None:
        """SQL order: reading_units inserts -> anchor_segments inserts.
        anchor_segments have a FK to reading_units, so units must be
        inserted first."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn)

        execute_calls = conn.execute_calls
        last_unit_idx = max(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_units" in c.query
        )
        first_segment_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO anchor_segments" in c.query
        )
        assert last_unit_idx < first_segment_idx

    def test_anchor_segments_order_index_is_global_sequential(self) -> None:
        """anchor_segments.order_index is global (1, 2, 3, 4, ...) not
        per-unit."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        segment_calls = [
            c for c in conn.execute_calls if "INSERT INTO anchor_segments" in c.query
        ]
        order_indices = [c.args[6] for c in segment_calls]
        assert order_indices == [1, 2, 3, 4]


class TestExactCanonicalTextNotRecanonicalized:
    """D6-I2C: The canonical text passed to reading_bases.text must be
    the EXACT plan.canonical_text, NOT recanonicalized by the base
    builder. D6 block offsets are bound to the exact text.

    Fixtures cover: double newlines (block separator), emoji (surrogate
    pairs in UTF-16), heading/list markers.
    """

    def test_reading_bases_text_is_exact_canonical_text_with_emoji(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $5 is text.
        text_param = reading_bases_call.args[4]
        assert text_param == plan.canonical_text
        # The text must contain emoji (surrogate pair).
        assert "\U0001f600" in text_param
        # The text must contain double newlines (block separator).
        assert "\n\n" in text_param

    def test_reading_bases_text_is_exact_canonical_text_with_list(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_list()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        text_param = reading_bases_call.args[4]
        assert text_param == plan.canonical_text
        # The text must contain list markers.
        assert "- item one" in text_param
        assert "- item two" in text_param

    def test_reading_bases_canonicalizer_version_uses_persistence_parameter(self) -> None:
        """The canonicalizer_version in the reading_bases insert must
        match the canonicalizer_version parameter passed to
        persist_stable_document_freeze_plan (pass-through), so
        build_result.base.canonicalizer_version and the DB row stay
        aligned."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $8 is canonicalizer_version.
        canonicalizer_version = reading_bases_call.args[7]
        assert canonicalizer_version == "test_canonicalizer_v1"

    def test_reading_bases_content_sha256_matches_exact_canonical_text(self) -> None:
        """content_sha256 must hash the EXACT canonical text, not a
        recanonicalized version."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        _persist(conn, plan)

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $6 is content_sha256.
        sha_param = reading_bases_call.args[5]
        expected = hashlib.sha256(plan.canonical_text.encode("utf-8")).hexdigest()
        assert sha_param == expected

    def test_unit_offsets_are_consistent_with_exact_canonical_text(self) -> None:
        """The unit UTF-16 offsets must be computed from the EXACT
        canonical text (with emoji and double newlines), not a
        recanonicalized version. If the text were recanonicalized, the
        offsets would not slice back correctly."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        _persist(conn, plan)

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        canonical_text = plan.canonical_text

        # Every unit must slice back to non-empty text from the exact
        # canonical text. If the text were recanonicalized, offsets
        # would be wrong and slicing would produce garbage.
        for call in unit_calls:
            base_start = call.args[6]
            base_end = call.args[7]
            unit_text = slice_by_utf16_offsets(canonical_text, base_start, base_end)
            assert unit_text, (
                f"Unit {call.args[2]} produced empty text when slicing "
                f"the exact canonical text at [{base_start}, {base_end})"
            )


class TestNavigationJsonFromBuildResult:
    """D6-I2C: navigation_json comes from the build result's
    navigation_units, with the full set of required fields."""

    def test_navigation_json_units_have_all_required_fields(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        nav_param = reading_bases_call.args[12]
        assert isinstance(nav_param, dict)
        units = nav_param["units"]
        assert len(units) == 3
        required_fields = {
            "unit_id",
            "order_index",
            "unit_type",
            "boundary_quality",
            "label",
            "base_start_utf16",
            "base_end_utf16",
        }
        for unit in units:
            assert required_fields.issubset(unit.keys()), (
                f"Navigation unit missing fields: {set(unit.keys())}"
            )

    def test_navigation_json_unit_ids_match_reading_units_inserts(self) -> None:
        """The unit_ids in navigation_json must match the unit_ids in
        the reading_units INSERT calls."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        nav_unit_ids = [u["unit_id"] for u in reading_bases_call.args[12]["units"]]

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        insert_unit_ids = [c.args[2] for c in unit_calls]

        assert nav_unit_ids == insert_unit_ids

    def test_navigation_json_offsets_match_reading_units_inserts(self) -> None:
        """The base_start_utf16 / base_end_utf16 in navigation_json
        must match the offsets in the reading_units INSERT calls."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        nav_units = {u["unit_id"]: u for u in reading_bases_call.args[12]["units"]}

        unit_calls = [
            c for c in conn.execute_calls if "INSERT INTO reading_units" in c.query
        ]
        for call in unit_calls:
            unit_id = call.args[2]
            nav_unit = nav_units[unit_id]
            assert nav_unit["base_start_utf16"] == call.args[6]
            assert nav_unit["base_end_utf16"] == call.args[7]
            assert nav_unit["unit_type"] == call.args[4]
            assert nav_unit["boundary_quality"] == call.args[5]
            assert nav_unit["order_index"] == call.args[3]

    def test_navigation_json_heading_label_is_set(self) -> None:
        """Heading units should have a label (the heading text)."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        units = reading_bases_call.args[12]["units"]
        heading_unit = next(u for u in units if u["unit_type"] == "heading")
        assert heading_unit["label"] is not None
        assert "My Title" in heading_unit["label"]


class TestD6I2CSqlOrder:
    """D6-I2C: Full SQL order including reading_units and
    anchor_segments inserts.

    Order: reading_bases supersede -> reading_bases insert ->
    reading_units inserts -> anchor_segments inserts ->
    reading_records fence.
    """

    def test_full_sql_order_with_units_and_segments(self) -> None:
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        execute_calls = conn.execute_calls
        # 3 blocks -> 3 units + 4 segments = 7 new inserts.
        # Total: 1 + 1 + 3 + 1 + 1 + 3 + 4 + 1 = 15
        assert len(execute_calls) == 15

        # Verify the critical ordering constraints.
        supersede_bases_idx = next(
            i for i, c in enumerate(execute_calls)
            if "UPDATE reading_bases" in c.query and "status = 'superseded'" in c.query
        )
        insert_bases_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_bases" in c.query
        )
        first_unit_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_units" in c.query
        )
        last_unit_idx = max(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO reading_units" in c.query
        )
        first_segment_idx = next(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO anchor_segments" in c.query
        )
        last_segment_idx = max(
            i for i, c in enumerate(execute_calls)
            if "INSERT INTO anchor_segments" in c.query
        )
        fence_idx = next(
            i for i, c in enumerate(execute_calls)
            if "UPDATE reading_records" in c.query
        )

        # supersede -> insert bases -> units -> segments -> fence
        assert supersede_bases_idx < insert_bases_idx
        assert insert_bases_idx < first_unit_idx
        assert last_unit_idx < first_segment_idx
        assert last_segment_idx < fence_idx


# --------------------------------------------------------------------
# D6-I2C review fix: Idempotent freeze completeness validation
# --------------------------------------------------------------------


def _queue_existing_stable_doc_and_active_base_id(
    conn: FakeConn,
    plan: Any,
    *,
    existing_id: UUID,
    existing_base_id: UUID,
) -> None:
    """Queue fetchrow #1 (existing stable doc) + fetchrow #2
    (reading_records.active_base_id non-null).

    Tests then queue their own fetchrow #3 (active base row) with the
    specific defect they want to exercise.
    """
    conn.queue_fetchrow({
        "id": existing_id,
        "content_sha256": plan.content_sha256,
        "status": "active",
        "document_version": 1,
    })
    conn.queue_fetchrow({"active_base_id": existing_base_id})


def _build_complete_active_base_row(
    plan: Any,
    base_id: UUID,
) -> dict[str, Any]:
    """Build a fetchrow dict for a COMPLETE active reading_bases row."""
    return {
        "id": base_id,
        "reading_record_id": UUID(_RECORD_ID),
        "record_generation": 1,
        "status": "active",
        "content_sha256": compute_canonical_text_sha256(
            plan.canonical_text
        ),
        "content_utf16_length": compute_canonical_text_utf16_length(
            plan.canonical_text
        ),
        "navigation_json": {
            "units": [
                {
                    "unit_id": "u1",
                    "order_index": 1,
                    "unit_type": "heading",
                    "boundary_quality": "normal",
                    "label": "Title",
                    "base_start_utf16": 0,
                    "base_end_utf16": 5,
                }
            ]
        },
    }


class TestIdempotentFreezeCompletenessValidation:
    """D6-I2C review fix: The same-hash idempotent branch must validate
    that the prior freeze completed ALL steps before returning
    idempotent_noop=True. If ANY completeness check fails, fail closed
    WITHOUT confirming the candidate.

    Validation checks (in order):
        1. reading_records.active_base_id non-NULL (covered by existing
           TestIdempotency tests).
        2. Active reading_bases row exists with matching (record,
           generation, status='active').
        3. content_sha256 == sha256(plan.canonical_text).
        4. content_utf16_length == utf16 length of plan.canonical_text.
        5. navigation_json.units non-empty.
        6. reading_units count > 0 for active_base_id.
        7. anchor_segments count > 0 for active_base_id.
    """

    _EXISTING_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    _EXISTING_BASE_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")

    def test_complete_state_passes_validation(self) -> None:
        """All validation checks pass -> idempotent_noop=True."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_complete_idempotent_state(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )

        result = _persist(conn, plan)

        assert result.idempotent_noop is True
        assert result.stable_document_id == self._EXISTING_ID
        assert result.base_id == self._EXISTING_BASE_ID
        assert len(conn.execute_calls) == 0

    def test_active_base_row_missing_fails_closed(self) -> None:
        """Active reading_bases row not found (WHERE status='active'
        does not match, or row was deleted). Fail closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        # fetchrow #3: active base row NOT found.
        conn.queue_fetchrow(None)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"active reading_bases row.*does not exist",
        ):
            _persist(conn, plan)

        assert len(conn.execute_calls) == 0

    def test_active_base_row_status_superseded_fails_closed(self) -> None:
        """Active base row has status='superseded' — the WHERE clause
        ``status='active'`` filters it out, so fetchrow returns None.
        Fail closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        # fetchrow #3 returns None because status='superseded' does
        # not match the WHERE status='active' clause.
        conn.queue_fetchrow(None)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"active reading_bases row.*does not exist",
        ):
            _persist(conn, plan)

    def test_content_sha256_mismatch_fails_closed(self) -> None:
        """Active base row has a different content_sha256 than
        sha256(plan.canonical_text). Fail closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        # Corrupt the content_sha256.
        base_row["content_sha256"] = "0" * 64
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"content_sha256=.*differs from sha256\(plan\.canonical_text\)",
        ):
            _persist(conn, plan)

    def test_content_utf16_length_mismatch_fails_closed(self) -> None:
        """Active base row has a different content_utf16_length. Fail
        closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        # Corrupt the content_utf16_length.
        base_row["content_utf16_length"] = 999
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"content_utf16_length=999 which differs",
        ):
            _persist(conn, plan)

    def test_empty_navigation_units_fails_closed(self) -> None:
        """Active base row has navigation_json = {"units": []}. Fail
        closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["navigation_json"] = {"units": []}
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"navigation_json\.units that is not a non-empty list",
        ):
            _persist(conn, plan)

    def test_no_reading_units_fails_closed(self) -> None:
        """Active base row is valid but reading_units count is 0. Fail
        closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        conn.queue_fetchrow(_build_complete_active_base_row(plan, self._EXISTING_BASE_ID))
        # fetchval #1: reading_units count = 0.
        conn.queue_fetchval(0)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"0 reading_units",
        ):
            _persist(conn, plan)

    def test_no_anchor_segments_fails_closed(self) -> None:
        """Active base row is valid, reading_units > 0, but
        anchor_segments count is 0. Fail closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        conn.queue_fetchrow(_build_complete_active_base_row(plan, self._EXISTING_BASE_ID))
        conn.queue_fetchval(3)  # reading_units count > 0
        # fetchval #2: anchor_segments count = 0.
        conn.queue_fetchval(0)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"0 anchor_segments",
        ):
            _persist(conn, plan)

    def test_candidate_not_confirmed_when_freeze_incomplete(self) -> None:
        """If the freeze state is incomplete (e.g., content_sha256
        mismatch), the candidate must NOT be confirmed. No candidate
        lookup or update should happen.
        """
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["content_sha256"] = "0" * 64  # mismatch
        conn.queue_fetchrow(base_row)

        with pytest.raises(StableDocumentFreezePersistenceError):
            _persist(
                conn,
                plan,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

        # No writes at all.
        assert len(conn.execute_calls) == 0
        # No candidate lookup or update.
        assert not any(
            "candidate_reading_documents" in c.query
            for c in conn.fetchrow_calls
        )
        assert not any(
            "candidate_reading_documents" in c.query
            for c in conn.execute_calls
        )


# --------------------------------------------------------------------
# D6-I2C-H hardening: tighten idempotency completeness validation
# --------------------------------------------------------------------


class TestIdempotentFreezeHardening:
    """D6-I2C-H hardening tests:

    1. existing stable_reading_documents row with same hash but
       status != 'active' must fail-closed (no candidate confirmation,
       no writes).
    2. navigation_json.units must be a non-empty list — dict, string,
       or other truthy non-list values must fail-closed.
    3. Invalid JSON string navigation_json must fail-closed with
       StableDocumentFreezePersistenceError (not JSONDecodeError).
    4. Valid JSON string navigation_json with non-empty units list
       must pass.
    """

    _EXISTING_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    _EXISTING_BASE_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")

    def test_existing_stable_doc_same_hash_but_status_superseded_fails_closed(
        self,
    ) -> None:
        """Same-hash idempotent branch: existing stable doc has
        status='superseded'. Must fail-closed BEFORE completeness
        validation or candidate confirmation."""
        conn = FakeConn()
        plan = _build_simple_plan()

        conn.queue_fetchrow({
            "id": self._EXISTING_ID,
            "content_sha256": plan.content_sha256,
            "status": "superseded",  # not 'active'
            "document_version": 1,
        })

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"status is 'superseded' \(expected 'active'\)",
        ):
            _persist(conn, plan)

        # No writes.
        assert len(conn.execute_calls) == 0
        # No completeness validation reads beyond the initial stable
        # doc lookup (1 fetchrow only).
        assert len(conn.fetchrow_calls) == 1
        assert len(conn.fetchval_calls) == 0

    def test_existing_stable_doc_same_hash_but_status_superseded_does_not_confirm_candidate(
        self,
    ) -> None:
        """status='superseded' + candidate_document_id provided: must
        NOT confirm candidate. No candidate lookup or update."""
        conn = FakeConn()
        plan = _build_simple_plan()

        conn.queue_fetchrow({
            "id": self._EXISTING_ID,
            "content_sha256": plan.content_sha256,
            "status": "superseded",
            "document_version": 1,
        })

        with pytest.raises(StableDocumentFreezePersistenceError):
            _persist(
                conn,
                plan,
                candidate_document_id=_CANDIDATE_ID,
                user_id=_USER_ID,
            )

        # No candidate lookup or update.
        assert not any(
            "candidate_reading_documents" in c.query
            for c in conn.fetchrow_calls
        )
        assert not any(
            "candidate_reading_documents" in c.query
            for c in conn.execute_calls
        )

    def test_navigation_units_as_dict_fails_closed(self) -> None:
        """navigation_json={"units": {"unit_id": "u1"}} — units is a
        dict (truthy but not a list). Must fail-closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["navigation_json"] = {"units": {"unit_id": "u1"}}
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"navigation_json\.units that is not a non-empty list",
        ):
            _persist(conn, plan)

    def test_navigation_units_as_string_fails_closed(self) -> None:
        """navigation_json={"units": "u1"} — units is a string (truthy
        but not a list). Must fail-closed."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["navigation_json"] = {"units": "u1"}
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"navigation_json\.units that is not a non-empty list",
        ):
            _persist(conn, plan)

    def test_navigation_json_as_invalid_json_string_fails_closed(self) -> None:
        """navigation_json is a string that is not valid JSON. Must
        fail-closed with StableDocumentFreezePersistenceError, not
        JSONDecodeError."""
        conn = FakeConn()
        plan = _build_simple_plan()

        _queue_existing_stable_doc_and_active_base_id(
            conn,
            plan,
            existing_id=self._EXISTING_ID,
            existing_base_id=self._EXISTING_BASE_ID,
        )
        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["navigation_json"] = "{not valid json"
        conn.queue_fetchrow(base_row)

        with pytest.raises(
            StableDocumentFreezePersistenceError,
            match=r"navigation_json that is not valid JSON",
        ):
            _persist(conn, plan)

    def test_navigation_json_as_valid_json_string_with_units_passes(self) -> None:
        """navigation_json is a valid JSON string with a non-empty
        units list. Must pass validation (str fallback path works)."""
        conn = FakeConn()
        plan = _build_simple_plan()

        conn.queue_fetchrow({
            "id": self._EXISTING_ID,
            "content_sha256": plan.content_sha256,
            "status": "active",
            "document_version": 1,
        })
        conn.queue_fetchrow({"active_base_id": self._EXISTING_BASE_ID})

        # Build a complete base row but serialize navigation_json as a
        # JSON string (simulating a DB driver that returns JSONB as
        # text).
        import json as _json

        base_row = _build_complete_active_base_row(plan, self._EXISTING_BASE_ID)
        base_row["navigation_json"] = _json.dumps(base_row["navigation_json"])
        conn.queue_fetchrow(base_row)
        conn.queue_fetchval(3)  # reading_units count
        conn.queue_fetchval(3)  # anchor_segments count

        result = _persist(conn, plan)

        assert result.idempotent_noop is True
        assert result.base_id == self._EXISTING_BASE_ID
        assert len(conn.execute_calls) == 0


# --------------------------------------------------------------------
# D6-I2C review fix: canonicalizer_version alignment
# --------------------------------------------------------------------


class TestCanonicalizerVersionAlignment:
    """D6-I2C review fix: canonicalizer_version must be aligned between
    the builder's ReadingBaseBuildResult.base.canonicalizer_version and
    the reading_bases DB insert.

    The persistence function passes its ``canonicalizer_version``
    parameter to ``build_reading_base_from_canonical_text`` so
    build_result.base and the DB row use the same label. The builder
    defaults to EXACT_CANONICAL_TEXT_VERSION when called directly
    without a canonicalizer_version.
    """

    def test_direct_builder_defaults_to_exact_canonical_text_version(
        self,
    ) -> None:
        """When build_reading_base_from_canonical_text is called
        directly without canonicalizer_version, the build result's
        base.canonicalizer_version must be EXACT_CANONICAL_TEXT_VERSION.
        """
        plan = _build_simple_plan()
        result = build_reading_base_from_canonical_text(
            reading_record_id=_RECORD_ID,
            base_id="00000000-0000-0000-0000-000000000099",
            canonical_text=plan.canonical_text,
        )
        assert result.base.canonicalizer_version == EXACT_CANONICAL_TEXT_VERSION

    def test_direct_builder_explicit_canonicalizer_version_passes_through(
        self,
    ) -> None:
        """When an explicit canonicalizer_version is passed to the
        direct builder, it must appear in build_result.base."""
        plan = _build_simple_plan()
        result = build_reading_base_from_canonical_text(
            reading_record_id=_RECORD_ID,
            base_id="00000000-0000-0000-0000-000000000099",
            canonical_text=plan.canonical_text,
            canonicalizer_version="custom_v42",
        )
        assert result.base.canonicalizer_version == "custom_v42"

    def test_persistence_passes_canonicalizer_version_to_builder_and_db(
        self,
    ) -> None:
        """The canonicalizer_version parameter passed to
        persist_stable_document_freeze_plan must flow through to BOTH
        build_result.base.canonicalizer_version (via the builder call)
        and the reading_bases DB insert param.

        Since we cannot inspect build_result from outside the
        persistence function, we verify the DB insert param matches
        the pass-through value. The builder pass-through is verified
        by test_direct_builder_explicit_canonicalizer_version_passes_through.
        """
        import asyncio

        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        plan = _build_plan_with_emoji_and_sentences()
        asyncio.run(
            persist_stable_document_freeze_plan(
                conn,
                plan=plan,
                canonicalizer_version="my_pass_through_v7",
                builder_version="test_builder_v1",
                segmenter_version="regex_sentence_clause_window_v1",
                language="en",
                now=datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
            )
        )

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # $8 is canonicalizer_version.
        assert reading_bases_call.args[7] == "my_pass_through_v7"

    def test_persistence_default_canonicalizer_version_is_not_exact_version(
        self,
    ) -> None:
        """The persistence function does NOT use
        EXACT_CANONICAL_TEXT_VERSION by default — the caller must
        always pass an explicit canonicalizer_version (there is no
        default in the persistence signature). This test documents
        that the _persist helper passes 'test_canonicalizer_v1'."""
        conn = FakeConn()
        conn.queue_fetchrow(None)
        conn.set_execute_result("UPDATE stable_reading_documents", "UPDATE 0")

        _persist(conn, _build_plan_with_emoji_and_sentences())

        reading_bases_call = next(
            c for c in conn.execute_calls if "INSERT INTO reading_bases" in c.query
        )
        # The _persist helper passes canonicalizer_version="test_canonicalizer_v1".
        # This is NOT EXACT_CANONICAL_TEXT_VERSION — the persistence
        # caller is responsible for choosing the version label.
        assert reading_bases_call.args[7] == "test_canonicalizer_v1"
        assert reading_bases_call.args[7] != EXACT_CANONICAL_TEXT_VERSION
