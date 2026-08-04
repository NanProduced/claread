"""B2-1: ask_supplements projection into ReaderPlateSnapshot.

These tests verify that Reading Record ask supplements are loaded from
`reader_ask_supplements` and projected into the snapshot via
`build_reader_plate_snapshot(ask_supplements=...)`.

Constraints mirrored from the user_assets contract:
- Only rows matching active base_id + generation with reading_record_id
  matching the current record and deleted_at IS NULL are returned.
- Rows failing defensive anchor validation are silently skipped.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database.connection import init_connection
from app.services.reader_orchestration import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


@pytest.fixture
async def reader_service_env() -> asyncpg.Pool:
    schema_name = f"test_b2_ask_supplements_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_reading_record_supplement(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    record_id: UUID,
    base_id: UUID,
    generation: int,
    unit_id: str,
    anchor_segment_id: str,
    start_offset: int,
    end_offset: int,
    selected_text: str,
    text_hash: str,
    supplement_type: str = "grammar_note",
    title: str = "AI 补充语法旁注",
    content_md: str = "This supplement explains the grammar point in detail.",
    target_key: str | None = None,
    sentence_id: str | None = None,
    paragraph_id: str | None = None,
    schema_version: str = "reader-ask-supplement-v1",
    created_from_turn_run_id: str = "",
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> UUID:
    effective_created_at = created_at or datetime.now(UTC)
    effective_target_key = target_key or (
        f"reading-record:{record_id}:segment:{anchor_segment_id}:"
        f"{start_offset}:{end_offset}"
    )
    effective_sentence_id = sentence_id or anchor_segment_id
    anchor_payload = {
        "record_id": str(record_id),
        "base_id": str(base_id),
        "generation": generation,
        "unit_id": unit_id,
        "anchor_segment_id": anchor_segment_id,
        "scope": "ask_supplement",
        "offset_unit": "utf16",
        "start_offset": start_offset,
        "end_offset": end_offset,
        "selected_text": selected_text,
        "text_hash": text_hash,
        "hash_algorithm": "fnv1a32-utf16",
    }
    metadata_payload = {
        "id": f"ask-supplement:pending",
        "entry_type": supplement_type,
        "label": "AI 补充语法旁注",
        "title": title,
        "content": content_md,
        "source_kind": "ask_supplement",
        "deletable": True,
        "target_key": effective_target_key,
        "paragraph_id": paragraph_id,
        "created_from_turn_run_id": created_from_turn_run_id,
        "schema_version": schema_version,
        "lifecycle_status": "persisted",
    }
    async with pool.acquire() as conn:
        supplement_id = await conn.fetchval(
            """
            INSERT INTO reader_ask_supplements (
                id, user_id, reading_record_id, supplement_type,
                target_key, sentence_id, paragraph_id,
                title, content_md, anchor_payload_json, metadata_json, schema_version,
                created_from_turn_run_id, created_at, updated_at, deleted_at,
                base_id, generation, unit_id, anchor_segment_id,
                start_offset, end_offset, text_hash, hash_algorithm
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, $10::jsonb, $11::jsonb, $12,
                $13, $14, $14, $15,
                $16, $17, $18, $19,
                $20, $21, $22, $23
            )
            RETURNING id
            """,
            uuid4(),
            user_id,
            record_id,
            supplement_type,
            effective_target_key,
            effective_sentence_id,
            paragraph_id,
            title,
            content_md,
            json.dumps(anchor_payload),
            json.dumps(metadata_payload),
            schema_version,
            created_from_turn_run_id,
            effective_created_at,
            deleted_at,
            base_id,
            generation,
            unit_id,
            anchor_segment_id,
            start_offset,
            end_offset,
            text_hash,
            "fnv1a32-utf16",
        )
    assert isinstance(supplement_id, UUID)
    return supplement_id


async def _fetch_first_anchor_segment(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
) -> tuple[str, str, int, int, str]:
    async with pool.acquire() as conn:
        seg_row = await conn.fetchrow(
            """
            SELECT unit_id, anchor_segment_id,
                   unit_start_utf16, unit_end_utf16,
                   text_hash
            FROM anchor_segments
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            LIMIT 1
            """,
            record_id,
            base_id,
        )
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE id = $1",
            base_id,
        )
    assert seg_row is not None
    assert isinstance(base_text, str) and base_text
    return (
        str(seg_row["unit_id"]),
        str(seg_row["anchor_segment_id"]),
        int(seg_row["unit_start_utf16"]),
        int(seg_row["unit_end_utf16"]),
        base_text,
    )


async def test_snapshot_includes_reading_record_ask_supplements(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: snapshot.ask_supplements exposes Reading Record supplements."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Hello 🧠 world. Another sentence here.",
        title="Ask Supplement Projection",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )
    target_prefix = "Hello 🧠 "
    target_start = seg_start + utf16_code_unit_length(target_prefix)
    target_end = target_start + utf16_code_unit_length("world")
    assert seg_start < target_start < target_end < seg_end
    selected_text = slice_by_utf16_offsets(base_text, target_start, target_end)
    assert selected_text == "world"
    text_hash = compute_text_range_hash(selected_text)

    supplement_created_at = datetime(2026, 6, 25, 8, 0, tzinfo=UTC)
    supplement_id = await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=target_start,
        end_offset=target_end,
        selected_text=selected_text,
        text_hash=text_hash,
        title="Grammar note for 'world'",
        content_md="The word 'world' is used here as a noun.",
        created_at=supplement_created_at,
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert len(snapshot.ask_supplements) == 1
    supplement = snapshot.ask_supplements[0]
    assert supplement.supplement_id == str(supplement_id)
    assert supplement.owner == "ask_supplement"
    assert supplement.created_at == supplement_created_at
    assert supplement.anchor is not None
    assert supplement.anchor.base_id == str(base_id)
    assert supplement.anchor.unit_id == unit_id
    assert supplement.anchor.anchor_segment_id == anchor_segment_id
    assert supplement.anchor.start_offset == target_start
    assert supplement.anchor.end_offset == target_end
    assert supplement.anchor.selected_text == selected_text
    assert supplement.anchor.text_hash == text_hash

    content_payload = supplement.content
    assert isinstance(content_payload, dict)
    assert content_payload["supplement_type"] == "grammar_note"
    assert content_payload["title"] == "Grammar note for 'world'"
    assert content_payload["content_md"] == "The word 'world' is used here as a noun."
    assert content_payload["lifecycle_status"] == "persisted"
    assert content_payload["record_id"] == str(record_id)
    assert content_payload["base_id"] == str(base_id)
    assert content_payload["generation"] == 1


async def test_snapshot_excludes_deleted_supplements(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: supplements with deleted_at IS NOT NULL are excluded."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Deleted supplement filtering test text.",
        title="Deleted Filter",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )
    selected_text = slice_by_utf16_offsets(base_text, seg_start, seg_end)
    text_hash = compute_text_range_hash(selected_text)

    deleted_at = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
    await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        content_md="This supplement was deleted and must not appear.",
        deleted_at=deleted_at,
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert snapshot.ask_supplements == []


async def test_snapshot_excludes_supplements_with_mismatched_base_or_generation(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: supplements with stale base_id or generation are excluded."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Stale base filter test text here.",
        title="Stale Base Filter",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )
    selected_text = slice_by_utf16_offsets(base_text, seg_start, seg_end)
    text_hash = compute_text_range_hash(selected_text)

    stale_base_id = uuid4()
    await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=stale_base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        content_md="Stale base_id supplement that must be excluded.",
    )
    await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=99,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        content_md="Stale generation supplement that must be excluded.",
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert snapshot.ask_supplements == []


async def test_snapshot_skips_supplements_with_invalid_anchor(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: supplements failing defensive anchor validation are silently skipped."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Invalid anchor defensive skip test text.",
        title="Defensive Skip",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )

    # Build a supplement whose stored selected_text does NOT match the
    # unit_text at the stored offsets. This will fail defensive validation.
    bogus_selected_text = "this text does not exist in the unit"
    bogus_text_hash = compute_text_range_hash(bogus_selected_text)
    await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=bogus_selected_text,
        text_hash=bogus_text_hash,
        content_md="This supplement has a bogus anchor and must be skipped.",
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert snapshot.ask_supplements == []


async def test_snapshot_skips_supplements_with_unknown_unit_id(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: supplements pointing to a non-existent unit_id are skipped."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Unknown unit defensive skip test text.",
        title="Unknown Unit Skip",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    _, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )
    selected_text = slice_by_utf16_offsets(base_text, seg_start, seg_end)
    text_hash = compute_text_range_hash(selected_text)

    await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id="nonexistent-unit-id",
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        content_md="This supplement points to a nonexistent unit and must be skipped.",
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert snapshot.ask_supplements == []


async def test_snapshot_supplements_sorted_by_created_at(
    reader_service_env: asyncpg.Pool,
) -> None:
    """B2-1: supplements are sorted by (created_at, supplement_id)."""
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Sort order test text for multiple supplements.",
        title="Sort Order",
        language="en",
    )
    result = await service.submit_plain_text(request)
    record_id = result.record_id
    base_id = result.base_id

    unit_id, anchor_segment_id, seg_start, seg_end, base_text = (
        await _fetch_first_anchor_segment(
            reader_service_env,
            record_id=record_id,
            base_id=base_id,
        )
    )
    selected_text = slice_by_utf16_offsets(base_text, seg_start, seg_end)
    text_hash = compute_text_range_hash(selected_text)

    later_created_at = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    earlier_created_at = datetime(2026, 6, 25, 8, 0, tzinfo=UTC)

    later_id = await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        title="Later supplement",
        content_md="This was created later.",
        created_at=later_created_at,
    )
    earlier_id = await _insert_reading_record_supplement(
        reader_service_env,
        user_id=user_id,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        start_offset=seg_start,
        end_offset=seg_end,
        selected_text=selected_text,
        text_hash=text_hash,
        title="Earlier supplement",
        content_md="This was created earlier.",
        created_at=earlier_created_at,
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
    )

    assert len(snapshot.ask_supplements) == 2
    assert snapshot.ask_supplements[0].supplement_id == str(earlier_id)
    assert snapshot.ask_supplements[0].created_at == earlier_created_at
    assert snapshot.ask_supplements[1].supplement_id == str(later_id)
    assert snapshot.ask_supplements[1].created_at == later_created_at
