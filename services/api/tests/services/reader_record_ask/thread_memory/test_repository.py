"""repository.py 单元测试（mock asyncpg pool）。

覆盖 H1（list_bindings_for_compaction 不投影内容字段到 wire）与 H2
（list_ok_turn_runs_with_bindings 扫描 supersedes 链，不跟随 current_turn_run_id）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from app.services.reader_record_ask.thread_memory.repository import (
    CanonicalMemoryView,
    SnapshotWriteResult,
    ThreadMemoryRepository,
)
from app.services.reader_record_ask.thread_memory.schema import (
    ThreadMemorySnapshot,
)

THREAD_ID = UUID("11111111-1111-4111-8111-111111111111")
TURN_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
MSG_ID = UUID("33333333-3333-4333-8333-333333333333")
SUPERSEDED_OK_RUN_ID = UUID("55555555-5555-4555-8555-555555555555")


def _make_pool(conn: AsyncMock) -> MagicMock:
    """Build a MagicMock pool whose acquire() yields ``conn``."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.acquire.return_value.__aexit__.return_value = None
    return pool


def _enable_transaction(conn: AsyncMock) -> MagicMock:
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction)
    return transaction


class TestListCanonicalMessages:
    async def test_returns_user_and_ok_assistant_only(self) -> None:
        # SQL filters: user message always; assistant only if status='completed'
        # and an ok turn_run exists for the message (H2: not via
        # current_turn_run_id).
        # Repository outputs canonical_turn_run_id from the
        # LATERAL JOIN (latest ok turn_run id), NOT the message row's
        # current_turn_run_id.
        canonical_run_id = UUID("66666666-6666-4666-8666-666666666666")
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": MSG_ID,
                "role": "user",
                "status": "completed",
                "content_md": "question",
                "created_at": "2026-01-01T00:00:00Z",
                "current_turn_run_id": None,
                # LATERAL JOIN returns None for user messages
                # (no ok turn_run). Repository outputs canonical_turn_run_id=None.
                "canonical_turn_run_id": None,
                "answer_blocks_json": None,
                "web_search_json": None,
            },
            {
                "id": UUID("44444444-4444-4444-8444-444444444444"),
                "role": "assistant",
                "status": "completed",
                "content_md": "answer",
                "created_at": "2026-01-01T00:01:00Z",
                # Message row's current_turn_run_id may point to a failed
                # retry (r_failed). canonical_turn_run_id is the LATEST ok
                # run from the LATERAL JOIN — this is what watermark consumes.
                "current_turn_run_id": UUID("77777777-7777-4777-8777-777777777777"),
                "canonical_turn_run_id": canonical_run_id,
                "answer_blocks_json": [{"text": "answer"}],
                "web_search_json": None,
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_canonical_messages(thread_id=THREAD_ID)

        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["answer_blocks"] == []
        assert result[0]["web_search_summary"] is None
        # Canonical_turn_run_id is an explicit output field.
        assert result[0]["canonical_turn_run_id"] is None
        assert result[1]["role"] == "assistant"
        # current_turn_run_id is the message-row field (may differ from canonical).
        assert result[1]["current_turn_run_id"] == str(
            UUID("77777777-7777-4777-8777-777777777777")
        )
        # canonical_turn_run_id is the LATERAL JOIN result (latest ok run).
        assert result[1]["canonical_turn_run_id"] == str(canonical_run_id)
        assert result[1]["answer_blocks"] == [{"text": "answer"}]
        # SQL received thread_id as the first bind param.
        assert conn.fetch.await_count == 1
        call_args = conn.fetch.await_args
        assert call_args.args[1] == THREAD_ID

    async def test_empty_thread(self) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = []
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_canonical_messages(thread_id=THREAD_ID)
        assert result == []


class TestListOkTurnRuns:
    async def test_returns_latest_ok_run_per_message_distinct_on(self) -> None:
        # DISTINCT ON (message_id) — only the
        # LATEST ok run per assistant message is returned. A successful
        # regenerate produces a new ok run (r_new) with a later
        # created_at; the old ok run (r_old) is EXCLUDED so its bindings
        # disappear from the Host map / allowlist.
        #
        # This test replaces the old ``test_returns_all_ok_runs_including_superseded``
        # which expected ALL ok runs to be returned — that contract
        # conflicted with the DISTINCT ON latest-ok-run rule and allowed
        # stale bindings from superseded ok runs to survive regenerate.
        new_ok_run_id = UUID("66666666-6666-4666-8666-666666666666")
        conn = AsyncMock()
        # DB returns both rows; DISTINCT ON (message_id) + ORDER BY
        # created_at DESC, id DESC collapses them to the latest (r_new).
        # The repository's SQL does this; the fake pool simulates the
        # post-DISTINCT-ON result (one row per message_id).
        conn.fetch.return_value = [
            {
                "id": new_ok_run_id,
                "message_id": MSG_ID,
                "thread_id": THREAD_ID,
                "status": "completed",
                "final_status": "ok",
                "terminal_reason": None,
                "resolved_evidence_json": [{"citation_id": "c_new"}],
                "envelope_fingerprint": "fp_new",
                "execution_version": "v2",
                "supersedes_run_id": SUPERSEDED_OK_RUN_ID,
                "run_attempt": 2,
                "created_at": "2026-01-01T00:02:00Z",
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_ok_turn_runs_with_bindings(thread_id=THREAD_ID)

        # Only the latest ok run is returned (old ok run excluded).
        assert len(result) == 1
        assert result[0]["id"] == str(new_ok_run_id)
        assert result[0]["resolved_evidence_json"] == [{"citation_id": "c_new"}]
        assert result[0]["supersedes_run_id"] == str(SUPERSEDED_OK_RUN_ID)
        assert result[0]["run_attempt"] == 2
        assert conn.fetch.await_count == 1
        assert conn.fetch.await_args.args[1] == THREAD_ID

    async def test_failed_retry_preserves_old_ok_run(self) -> None:
        # Failed/cancelled retry does NOT produce a new ok
        # run. The old ok run remains the latest (and only) ok run for
        # the message → DISTINCT ON returns it → bindings unchanged.
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": SUPERSEDED_OK_RUN_ID,
                "message_id": MSG_ID,
                "thread_id": THREAD_ID,
                "status": "completed",
                "final_status": "ok",
                "terminal_reason": None,
                "resolved_evidence_json": [{"citation_id": "c_old"}],
                "envelope_fingerprint": "fp_old",
                "execution_version": "v2",
                "supersedes_run_id": None,
                "run_attempt": 1,
                "created_at": "2026-01-01T00:01:00Z",
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_ok_turn_runs_with_bindings(thread_id=THREAD_ID)

        assert len(result) == 1
        # Old ok run preserved (failed retry not in final_status='ok' set).
        assert result[0]["id"] == str(SUPERSEDED_OK_RUN_ID)
        assert result[0]["resolved_evidence_json"] == [{"citation_id": "c_old"}]


class TestCanonicalMemoryView:
    async def test_loads_one_repeatable_read_view_with_stable_pair_order(self) -> None:
        conn = AsyncMock()
        transaction = _enable_transaction(conn)
        conn.fetchval.return_value = "reader_ask_thread_memory"
        conn.fetchrow.return_value = {
            "thread_id": THREAD_ID,
            "snapshot_json": _valid_snapshot_json(),
            "version": 7,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        canonical_run_id = UUID("66666666-6666-4666-8666-666666666666")
        conn.fetch.side_effect = [
            [
                {
                    "id": MSG_ID,
                    "role": "user",
                    "status": "completed",
                    "content_md": "question",
                    "created_at": "2026-01-01T00:00:00Z",
                    "current_turn_run_id": None,
                    "canonical_turn_run_id": None,
                    "answer_blocks_json": None,
                    "web_search_json": None,
                }
            ],
            [
                {
                    "id": canonical_run_id,
                    "message_id": MSG_ID,
                    "thread_id": THREAD_ID,
                    "status": "completed",
                    "final_status": "ok",
                    "terminal_reason": None,
                    "resolved_evidence_json": [],
                    "envelope_fingerprint": "fp",
                    "execution_version": "v2",
                    "supersedes_run_id": None,
                    "run_attempt": 3,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        ]
        pool = _make_pool(conn)
        repo = ThreadMemoryRepository(pool=pool)

        view = await repo.load_canonical_memory_view(thread_id=THREAD_ID)

        assert isinstance(view, CanonicalMemoryView)
        assert view.snapshot is not None
        assert view.snapshot_version == 7
        assert view.canonical_messages[0]["content_md"] == "question"
        assert view.ok_turn_runs[0]["run_attempt"] == 3
        pool.acquire.assert_called_once_with()
        conn.transaction.assert_called_once_with(
            isolation="repeatable_read",
            readonly=True,
        )
        assert transaction.__aenter__.await_count == 1
        assert transaction.__aexit__.await_count == 1
        assert conn.fetch.await_count == 2
        message_sql = conn.fetch.await_args_list[0].args[0]
        run_sql = conn.fetch.await_args_list[1].args[0]
        assert "reader_ask_client_submissions" in message_sql
        assert "submission.user_message_id = m.id THEN 0" in message_sql
        assert "submission.assistant_message_id = m.id THEN 1" in message_sql
        assert "m.id ASC" in message_sql
        assert (
            "ORDER BY message_id, run_attempt DESC, created_at DESC, id DESC"
            in run_sql
        )

    async def test_missing_0028_table_still_reads_canonical_inputs(self) -> None:
        conn = AsyncMock()
        _enable_transaction(conn)
        conn.fetchval.return_value = None
        conn.fetch.side_effect = [[], []]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))

        view = await repo.load_canonical_memory_view(thread_id=THREAD_ID)

        assert isinstance(view, CanonicalMemoryView)
        assert view.snapshot is None
        assert view.snapshot_version == 0
        conn.fetchrow.assert_not_awaited()
        assert conn.fetch.await_count == 2

    async def test_database_failure_fails_soft_to_no_memory(self) -> None:
        conn = AsyncMock()
        _enable_transaction(conn)
        conn.fetchval.side_effect = RuntimeError("db unavailable")
        repo = ThreadMemoryRepository(pool=_make_pool(conn))

        view = await repo.load_canonical_memory_view(thread_id=THREAD_ID)

        assert view is None


class TestListBindingsForCompaction:
    async def test_returns_id_only_dicts_no_content_fields(self) -> None:
        # H1: resolved_evidence_json carries full InternalCitationBinding shape
        # (with snippet / canonical_url / source_fingerprint). The repository
        # must strip content fields and return ID-type fields only.
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": TURN_RUN_ID,
                "resolved_evidence_json": [
                    {
                        "citation_id": "cit_1",
                        "handle_id": "evh_abc",
                        "source_kind": "article",
                        "snippet": "SECRET SNIPPET",
                        "canonical_url": "https://example.com/x",
                        "web_title": "Web Title",
                        "web_description": "desc",
                        "published_at": "2026-01-01",
                        "retrieved_at": "2026-01-02",
                        "source_fingerprint": "fp123",
                        "unit_id": "u1",
                        "anchor_segment_id": "a1",
                        "kind": "search_hit",
                        "source_tool": "search_current_article",
                        "rag_citation": {
                            "stable_document_id": "doc1",
                            "base_id": "b1",
                            "record_generation": 3,
                            "reading_record_id": "rr1",
                        },
                    }
                ],
                "created_at": "2026-01-01T00:01:00Z",
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_bindings_for_compaction(thread_id=THREAD_ID)

        assert len(result) == 1
        binding = result[0]
        assert binding["turn_run_id"] == str(TURN_RUN_ID)
        assert binding["citation_id"] == "cit_1"
        assert binding["handle_id"] == "evh_abc"
        assert binding["source_kind"] == "article"
        assert binding["unit_id"] == "u1"
        assert binding["anchor_segment_id"] == "a1"
        assert binding["kind"] == "search_hit"
        assert binding["source_tool"] == "search_current_article"
        assert binding["rag_citation"]["stable_document_id"] == "doc1"
        # H1: content fields must NOT be projected to wire.
        for forbidden in (
            "snippet",
            "canonical_url",
            "web_title",
            "web_description",
            "published_at",
            "retrieved_at",
            "source_fingerprint",
        ):
            assert forbidden not in binding, (
                f"{forbidden} must not be projected (H1)"
            )

    async def test_handles_null_resolved_evidence(self) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": TURN_RUN_ID,
                "resolved_evidence_json": None,
                "created_at": "2026-01-01T00:01:00Z",
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_bindings_for_compaction(thread_id=THREAD_ID)
        assert result == []

    async def test_handles_multiple_bindings_across_runs(self) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": TURN_RUN_ID,
                "resolved_evidence_json": [
                    {"citation_id": "c1", "source_kind": "article"},
                    {"citation_id": "c2", "source_kind": "web"},
                ],
                "created_at": "2026-01-01T00:01:00Z",
            },
            {
                "id": UUID("77777777-7777-4777-8777-777777777777"),
                "resolved_evidence_json": [
                    {"citation_id": "c3", "source_kind": "article"},
                ],
                "created_at": "2026-01-01T00:02:00Z",
            },
        ]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.list_bindings_for_compaction(thread_id=THREAD_ID)
        # Flat list: 3 bindings total.
        assert len(result) == 3
        assert result[0]["turn_run_id"] == str(TURN_RUN_ID)
        assert result[0]["citation_id"] == "c1"
        assert result[2]["citation_id"] == "c3"


def _valid_snapshot_json() -> dict:
    """Minimal valid ThreadMemorySnapshot JSON for tests."""
    return {
        "version": "thread_memory_v1",
        "watermark": "a" * 64,
        "thread_id": str(THREAD_ID),
        "created_at": "2026-01-01T00:00:00Z",
        "last_compacted_at": None,
        "last_compaction_stats": None,
        "episodes": [],
    }


class TestGetThreadMemorySnapshot:
    async def test_returns_none_when_missing(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.get_thread_memory_snapshot(thread_id=THREAD_ID)
        assert result is None

    async def test_returns_typed_snapshot(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "thread_id": THREAD_ID,
            "snapshot_json": _valid_snapshot_json(),
            "version": 5,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.get_thread_memory_snapshot(thread_id=THREAD_ID)
        assert result is not None
        assert isinstance(result, ThreadMemorySnapshot)
        assert result.version == "thread_memory_v1"
        assert result.thread_id == str(THREAD_ID)

    async def test_fail_soft_on_invalid_json(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "thread_id": THREAD_ID,
            "snapshot_json": "not valid json{{{",
            "version": 1,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.get_thread_memory_snapshot(thread_id=THREAD_ID)
        assert result is None

    async def test_fail_soft_on_wrong_version(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "thread_id": THREAD_ID,
            "snapshot_json": {"version": "thread_memory_v2"},
            "version": 1,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        result = await repo.get_thread_memory_snapshot(thread_id=THREAD_ID)
        assert result is None


class TestUpsertThreadMemorySnapshot:
    async def test_applied_returns_typed_result(self) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = {"version": 5}
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        snapshot = ThreadMemorySnapshot.model_validate(_valid_snapshot_json())
        result = await repo.upsert_thread_memory_snapshot(
            thread_id=THREAD_ID,
            snapshot=snapshot,
            version=4,
        )
        assert isinstance(result, SnapshotWriteResult)
        assert result.applied is True
        assert result.version == 5

    async def test_cas_conflict_returns_applied_false(self) -> None:
        conn = AsyncMock()
        # First fetchrow (UPSERT) returns None → CAS conflict.
        # Second fetchrow (live version read) returns version=7.
        conn.fetchrow.side_effect = [None, {"version": 7}]
        repo = ThreadMemoryRepository(pool=_make_pool(conn))
        snapshot = ThreadMemorySnapshot.model_validate(_valid_snapshot_json())
        result = await repo.upsert_thread_memory_snapshot(
            thread_id=THREAD_ID,
            snapshot=snapshot,
            version=4,
        )
        assert isinstance(result, SnapshotWriteResult)
        assert result.applied is False
        assert result.version == 7


class TestPoolFallback:
    async def test_uses_db_pool_when_no_explicit_pool(self) -> None:
        # When no pool is passed to the constructor, the repository falls back
        # to app.database.connection.DB_POOL.
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        fallback_pool = _make_pool(conn)
        repo = ThreadMemoryRepository()  # no explicit pool
        try:
            from app.database import connection as db_connection

            original = db_connection.DB_POOL
            db_connection.DB_POOL = fallback_pool
            result = await repo.get_thread_memory_snapshot(thread_id=THREAD_ID)
        finally:
            db_connection.DB_POOL = original  # type: ignore[assignment]
        assert result is None
        assert conn.fetchrow.await_count == 1
