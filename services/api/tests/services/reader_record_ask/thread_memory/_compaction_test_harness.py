"""ASK-COMPACTION-INTEGRATED- — provider-free integrated-chain test helpers.

In-process doubles + seeding helpers so an integration test can drive the
**real** repository / manager / runtime / production_stream against **real**
PostgreSQL and assert the full compaction chain (compaction SSE → memory write
→ next-round prompt consumption) with zero real model calls.

This module lives under ``tests/`` and is NOT a second business chain:

- The compactor double and the recording model double are injected through the
  existing default-real DI seams (``ThreadMemoryManager(compactor=...)``,
  ``run_reading_record_ask(model=...)`` and the ``memory_*`` overrides on
  ``stream_agentic_thread_message``). Production never sets them.
- Nothing here is imported by the request path; these helpers are test-only.

The compactor double compacts the aged prefix with the deterministic emergency
compactor and stamps a unique marker into the first structured fact's text so a
test can prove, by string search, that the compacted memory block reached the
next round's model-visible prompt. The marker is plain ASCII and survives the
memory renderer's XML escaping unchanged.

Browser/BFF coverage is deliberately not claimed here. This helper proves the
server-side production core with real PostgreSQL; UI rendering remains a
separate scripted-browser evidence layer until the cutover test suite replaces
the phase-numbered harnesses.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.config.settings import get_settings
from app.database.connection import init_connection
from app.services.reader_record_ask.thread_memory.allowlist import (
    compute_watermark,
)
from app.services.reader_record_ask.thread_memory.compactor import (
    CompactorRunOutcome,
)
from app.services.reader_record_ask.thread_memory.emergency import (
    emergency_compact,
)

# Plain ASCII; survives XML escaping; unique across the repo.
COMPACTION_MARKER = "ASKCTXR1COMPACTIONPROOF"
RECENT_MARKER = "RECENTCTXR1PROOF"

# In-process store of the model-visible prompt the recording model received on
# its latest call per thread.
_RECORDED_PROMPTS: dict[str, str] = {}


def recorded_prompt(thread_id: UUID | str) -> str | None:
    return _RECORDED_PROMPTS.get(str(thread_id))


def clear_recorded_prompts() -> None:
    _RECORDED_PROMPTS.clear()


def _serialize_messages(messages: Any) -> str:
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            text = getattr(part, "content", None)
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def make_recording_model(thread_id: UUID | str, *, answer_text: str = "ok") -> FunctionModel:
    """A deterministic streaming PydanticAI model that records the prompt.

    The runtime builds the model-visible prompt (projection + selection +
    memory block + recent history) and hands it to this model. We capture the
    full serialized text so a test can assert the compacted memory marker and
    the recent-history marker both reached the model.

    The model streams the structured draft envelope in chunks, splitting the
    inner answer text so the runtime's streaming answer extractor emits real
    ``message.delta`` frames (the same path a real model exercises) before the
    finalizer parses the completed envelope. The envelope is citation-free so
    finalization needs no handle wiring.
    """
    key = str(thread_id)
    payload = json.dumps(
        {
            "response_kind": "grounded_answer",
            "answer_blocks": [
                {
                    "text": answer_text,
                    "basis": "general",
                    "evidence_handles": [],
                }
            ],
        }
    )
    inner_start = payload.index(answer_text)
    inner_end = inner_start + len(answer_text)
    prefix = payload[:inner_start]
    suffix = payload[inner_end:]
    half = max(1, len(answer_text) // 2)
    inner_chunks = (answer_text[:half], answer_text[half:])

    async def stream_fn(messages: Any, info: AgentInfo) -> Any:
        del info
        _RECORDED_PROMPTS[key] = _serialize_messages(messages)
        for chunk in (prefix, *inner_chunks, suffix):
            yield chunk

    return FunctionModel(stream_function=stream_fn)


def make_marker_compactor() -> Any:
    """Deterministic compactor double: emergency compact + marker stamp."""

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        canonical = list(kwargs.get("canonical_messages") or [])
        turn_range = tuple(kwargs.get("turn_range") or (1, 1))
        prefix = canonical[: 2 * int(turn_range[1])]
        episode = emergency_compact(
            prefix,
            [],
            turn_range=turn_range,
            host_bindings={},
        )
        facts = list(episode.structured_facts)
        if facts:
            facts[0] = facts[0].model_copy(update={"text": COMPACTION_MARKER})
        episode = episode.model_copy(
            update={
                "structured_facts": facts,
                "compaction_input_watermark": compute_watermark(prefix),
                "compaction_model": "deepseek-v4-flash",
                "compaction_method": "model",
            }
        )
        return CompactorRunOutcome(
            episode=episode,
            detail_code="ok",
            attempt_count=1,
        )

    return compactor


# ---------------------------------------------------------------------------
# Seeding / cleanup (real PostgreSQL; disposable threads only)
# ---------------------------------------------------------------------------


async def _open_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=2,
        init=init_connection,
    )


async def seed_compaction_history(
    *,
    thread_id: UUID,
    user_id: UUID,
    reading_record_id: UUID,
    pairs: int = 21,
) -> None:
    """Seed ``pairs`` completed user/assistant turns into a disposable thread.

    Pair ``pairs`` (the newest seeded pair) carries :data:`RECENT_MARKER` in its
    user text so a test can prove the recent-history window reached the model;
    pair 1 is the aged prefix the compactor double stamps with
    :data:`COMPACTION_MARKER`. The thread is created non-default; cleanup drops
    it (cascade).
    """
    from datetime import UTC, datetime, timedelta

    pool = await _open_pool()
    try:
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO reader_ask_threads (
                    id, user_id, reading_record_id, title, is_default,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, 'compaction-integrated-gate', false, $4, $4)
                ON CONFLICT (id) DO NOTHING
                """,
                thread_id,
                user_id,
                reading_record_id,
                now,
            )
            base_id = UUID("00000000-0000-0000-0000-00000000c0de")
            for index in range(1, pairs + 1):
                user_mid = UUID(int=int(thread_id.int) ^ (index * 2))
                asst_mid = UUID(int=int(thread_id.int) ^ (index * 2 + 1))
                run_id = UUID(int=int(thread_id.int) ^ (index * 2 + 2))
                ts = now + timedelta(seconds=index)
                user_text = (
                    f"{RECENT_MARKER} seeded question {index}"
                    if index == pairs
                    else f"seeded question {index} about the article"
                )
                await conn.execute(
                    """
                    INSERT INTO reader_ask_messages (
                        id, thread_id, role, status, content_md,
                        created_at, updated_at
                    ) VALUES
                        ($1, $3, 'user', 'completed', $4, $5, $5),
                        ($2, $3, 'assistant', 'completed', $6, $5, $5)
                    """,
                    user_mid,
                    asst_mid,
                    thread_id,
                    user_text,
                    ts,
                    f"seeded answer {index}",
                )
                await conn.execute(
                    """
                    INSERT INTO reader_ask_turn_runs (
                        id, message_id, thread_id, user_id, reading_record_id,
                        base_id, generation, turn_id, run_attempt, status,
                        final_status, execution_version, envelope_fingerprint,
                        user_visible_output_json, resolved_evidence_json,
                        started_at, completed_at, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, 1, $7, 1, 'completed',
                        'ok', 'reader_record_ask_agentic_v2', $8,
                        $9::jsonb, '[]'::jsonb, $10, $10, $10, $10
                    )
                    """,
                    run_id,
                    asst_mid,
                    thread_id,
                    user_id,
                    reading_record_id,
                    base_id,
                    user_mid,
                    "f" * 64,
                    json.dumps(
                        {
                            "answer_text": f"seeded answer {index}",
                            "answer_blocks": [
                                {"kind": "text", "text": f"seeded answer {index}"}
                            ],
                            "web_search": None,
                        }
                    ),
                    ts,
                )
                await conn.execute(
                    "UPDATE reader_ask_messages SET current_turn_run_id=$1 WHERE id=$2",
                    run_id,
                    asst_mid,
                )
    finally:
        await pool.close()


async def cleanup_thread(thread_id: UUID) -> None:
    pool = await _open_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM reader_ask_threads WHERE id=$1",
                thread_id,
            )
    finally:
        await pool.close()
