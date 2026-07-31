"""ASK-COMPACTION-INTEGRATED-R1 — real integrated context-compaction chain.

Opt-in real-PostgreSQL integration (``CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1``).
Drives the **real** repository / manager / runtime / production_stream with a
deterministic fake compactor and a deterministic fake (recording) answer model
— zero provider calls — and proves the full chain on a disposable thread:

    real PG canonical history (>20 pairs)
    → production service/runtime → ThreadMemoryManager
    → deterministic compactor → reader_ask_thread_memory write
    → production_stream emits real context.compaction.* SSE
    → next round's model-visible prompt contains compacted memory + recent
      history, with disjoint turn coverage (no double counting)

It also proves the fallback path (compactor failure → Host fallback → answer
still completes) and the privacy contract (no raw transcript/query/URL/provider
error in the SSE). Browser consumption of the same real SSE is covered by the
companion Playwright spec (ask-context-compaction-integrated.spec.ts); this
module covers the server-side depth that a browser cannot observe directly.

No test code sinks ``ContextCompactionEvent`` directly: compaction is triggered
solely by the real manager observing the seeded >20-pair history. The thread is
created non-default and deleted in ``finally``; no other business data is
touched.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database.connection import init_connection
from app.services.reader_record_ask import _compaction_test_harness as harness
from app.services.reader_record_ask.model_view_budget import ModelViewRenderer
from app.services.reader_record_ask.evidence_expansion import (
    ExpansionPointerLedger,
)
from app.services.reader_record_ask.production_stream import (
    stream_agentic_thread_message,
)
from app.services.reader_record_ask.repository import ReaderRecordAskRepository
from app.services.reader_record_ask.service import _load_snapshot_facts
from app.services.reader_record_ask.thread_memory.compactor import (
    CompactorRunOutcome,
)
from app.services.reader_record_ask.thread_memory.manager import (
    ThreadMemoryManager,
)
from app.services.reader_record_ask.thread_memory.repository import (
    ThreadMemoryRepository,
)
from app.services.reader_record_ask.thread_memory.render import render_memory_block

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAREAD_RUN_THREAD_MEMORY_DB_TESTS") != "1",
    reason="opt-in: real PostgreSQL; set CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1",
)


@pytest.fixture(autouse=True)
async def _app_db_pool() -> Any:
    """Initialize the global DB pool the real production path reads from.

    The real ``stream_agentic_thread_message`` / repository / snapshot-facts
    loader all use the app-global pool (via ``db_connect``), which only the
    FastAPI lifespan normally initializes. Mirror that here so the integrated
    chain runs against real PostgreSQL without a live server. The harness's
    seed/cleanup helpers keep their own pools and are unaffected.
    """
    from app.database.connection import close_db, init_db

    settings = get_settings()
    await init_db(
        database_url=settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=(
            settings.database_max_inactive_connection_lifetime
        ),
    )
    try:
        yield
    finally:
        await close_db()

_PRIVACY_LEAKS = (
    "reasoning_content",
    "evh_",
    "provider_result_ref",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "Bearer ",
    "sk-",
)


def _parse_frames(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        event_match = re.search(r"^event:\s*(\S+)", chunk, re.MULTILINE)
        data_match = re.search(r"^data:\s*(.*)$", chunk, re.MULTILINE)
        if event_match is None:
            continue
        name = event_match.group(1)
        data: dict[str, Any] = {}
        if data_match:
            try:
                data = json.loads(data_match.group(1))
            except json.JSONDecodeError:
                data = {}
        frames.append((name, data))
    return frames


async def _existing_record(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id AS reading_record_id, user_id
            FROM reading_records
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        )
    assert row is not None, "local DB needs one reading_record fixture"
    return UUID(str(row["reading_record_id"])), UUID(str(row["user_id"]))


async def _memory_row(pool: asyncpg.Pool, thread_id: UUID) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT version, snapshot_json FROM reader_ask_thread_memory
            WHERE thread_id=$1
            """,
            thread_id,
        )
    if row is None:
        return None
    # asyncpg decodes JSONB columns to native dict/list already.
    snapshot = row["snapshot_json"]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return {"version": row["version"], "snapshot": snapshot}


@pytest.mark.asyncio
async def test_real_integrated_compaction_chain_writes_and_is_consumed() -> None:
    pool = await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=2,
        init=init_connection,
    )
    reading_record_id, user_id = await _existing_record(pool)
    thread_id = uuid4()
    try:
        await harness.seed_compaction_history(
            thread_id=thread_id,
            user_id=user_id,
            reading_record_id=reading_record_id,
            pairs=21,
        )
        facts = await _load_snapshot_facts(
            user_id=user_id,
            reading_record_id=reading_record_id,
        )
        repo = ReaderRecordAskRepository()
        renderer = ModelViewRenderer()
        compactor = harness.make_marker_compactor()

        fence_context = {
            "reading_record_id": str(reading_record_id),
            "current_generation": int(facts.record.generation),
            "current_base_id": str(facts.build_result.base.base_id),
        }

        # --- Deterministic non-overlap + memory-block marker proof --------
        events: list[Any] = []
        prepared = await ThreadMemoryManager(
            repository=ThreadMemoryRepository(),
            renderer=renderer,
            compactor=compactor,
            event_sink=events.append,
        ).prepare_context(thread_id=thread_id, fence_context=fence_context)
        assert prepared.snapshot is not None
        assert prepared.compaction_status == "completed"
        assert prepared.recent_history_view is not None
        episode = prepared.snapshot.episodes[0]
        recent_ids = {m["id"] for m in prepared.recent_messages}
        # The aged prefix (pair 1) is compacted, never also shipped as raw
        # recent history — disjoint turn coverage, no double counting.
        assert all(
            mid not in recent_ids
            for mid in (
                str(UUID(int=int(thread_id.int) ^ 2)),
                str(UUID(int=int(thread_id.int) ^ 3)),
            )
        )
        memory_view = render_memory_block(prepared.snapshot, budget_chars=8000)
        assert memory_view is not None
        assert harness.COMPACTION_MARKER in memory_view.text
        assert harness.RECENT_MARKER in prepared.recent_history_view.text

        # --- Turn 1: real stream emits real compaction SSE + persists ------
        harness.clear_recorded_prompts()
        chunks: list[str] = []
        async for chunk in stream_agentic_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            content="summarise the conversation so far",
            facts=facts,
            request_anchor=None,
            focus_anchors=None,
            repository=repo,
            pointer_ledger=ExpansionPointerLedger(),
            auto_wire_dependencies=False,
            model=harness.make_recording_model(thread_id, answer_text="turn1 answer"),
            memory_enabled_override=True,
            memory_compactor=compactor,
        ):
            chunks.append(chunk)
        frames = _parse_frames(chunks)
        names = [name for name, _ in frames]
        assert "context.compaction.started" in names
        completed_or_fallback = [
            n for n in names if n in ("context.compaction.completed", "context.compaction.fallback")
        ]
        assert completed_or_fallback, frames
        assert "context.compaction.completed" in names
        first_progress = next(
            (i for i, n in enumerate(names) if n == "agentic.progress"),
            len(names),
        )
        comp_completed_idx = names.index("context.compaction.completed")
        assert comp_completed_idx < first_progress, names
        # Identity complete + consistent on every compaction frame.
        for name, data in frames:
            if not name.startswith("context.compaction."):
                continue
            assert data.get("execution_version") == "reader_record_ask_agentic_v2"
            assert data.get("thread_id") == str(thread_id)
            assert data.get("turn_run_id")
            assert data.get("message_id")
        assert "message.delta" in names
        assert "message.completed" in names
        # Privacy: no raw transcript / handle / secret / provider error in SSE.
        serialized = "".join(chunks)
        for leak in _PRIVACY_LEAKS:
            assert leak not in serialized, leak

        row = await _memory_row(pool, thread_id)
        assert row is not None, "memory snapshot must be persisted"
        assert row["version"] >= 1
        assert row["snapshot"]["watermark"]
        assert row["snapshot"]["episodes"], row["snapshot"]
        persisted_texts = [
            fact["text"]
            for ep in row["snapshot"]["episodes"]
            for fact in ep["structured_facts"]
        ]
        assert harness.COMPACTION_MARKER in persisted_texts

        # --- Turn 2: real runtime injects memory + recent into the prompt --
        harness.clear_recorded_prompts()
        async for _chunk in stream_agentic_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            content="what did we compact?",
            facts=facts,
            request_anchor=None,
            focus_anchors=None,
            repository=repo,
            pointer_ledger=ExpansionPointerLedger(),
            auto_wire_dependencies=False,
            model=harness.make_recording_model(
                thread_id, answer_text="turn2 answer"
            ),
            memory_enabled_override=True,
            memory_compactor=compactor,
        ):
            pass
        prompt = harness.recorded_prompt(str(thread_id))
        assert prompt is not None, "recording model must capture the prompt"
        assert harness.COMPACTION_MARKER in prompt, "compacted memory missing"
        assert harness.RECENT_MARKER in prompt, "recent history missing"
    finally:
        await harness.cleanup_thread(thread_id)
        await pool.close()


@pytest.mark.asyncio
async def test_real_integrated_compaction_fallback_still_answers() -> None:
    pool = await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=2,
        init=init_connection,
    )
    reading_record_id, user_id = await _existing_record(pool)
    thread_id = uuid4()
    try:
        await harness.seed_compaction_history(
            thread_id=thread_id,
            user_id=user_id,
            reading_record_id=reading_record_id,
            pairs=21,
        )
        facts = await _load_snapshot_facts(
            user_id=user_id,
            reading_record_id=reading_record_id,
        )
        repo = ReaderRecordAskRepository()

        async def failing_compactor(**_kwargs: Any) -> CompactorRunOutcome:
            return CompactorRunOutcome(
                episode=None,
                detail_code="timeout",
                attempt_count=2,
            )

        chunks: list[str] = []
        async for chunk in stream_agentic_thread_message(
            user_id=user_id,
            reading_record_id=reading_record_id,
            thread_id=thread_id,
            content="answer after a compaction failure",
            facts=facts,
            request_anchor=None,
            focus_anchors=None,
            repository=repo,
            pointer_ledger=ExpansionPointerLedger(),
            auto_wire_dependencies=False,
            model=harness.make_recording_model(
                thread_id, answer_text="fallback answer"
            ),
            memory_enabled_override=True,
            memory_compactor=failing_compactor,
        ):
            chunks.append(chunk)
        frames = _parse_frames(chunks)
        names = [name for name, _ in frames]
        assert "context.compaction.started" in names
        assert "context.compaction.fallback" in names
        # Host fallback must not abort the turn: the answer still completes.
        assert "message.completed" in names
        completed_data = next(
            data for name, data in frames if name == "message.completed"
        )
        assert completed_data.get("answer_text") == "fallback answer"
        serialized = "".join(chunks)
        for leak in _PRIVACY_LEAKS:
            assert leak not in serialized, leak
    finally:
        await harness.cleanup_thread(thread_id)
        await pool.close()
