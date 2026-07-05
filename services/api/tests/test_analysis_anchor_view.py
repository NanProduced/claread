from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database import connection as db_connection
from app.services.reader_orchestration.analysis_anchor_view import (
    AnalysisAnchorView,
    load_analysis_anchor_views,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff."
)


@pytest.fixture
async def test_db_pool() -> asyncpg.Pool:
    schema_name = f"test_anchor_view_empty_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


@pytest.fixture
async def test_db_pool_with_grammar_record():
    schema_name = f"test_anchor_view_rec_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        user_id = await insert_user(pool)
        article = await submit_article_ready(
            pool,
            user_id=user_id,
            plain_text=ARTICLE_TEXT,
            title="Anchor View Slice",
            language="en",
        )
        try:
            yield pool, article.record_id, article.base_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_stable_document_and_blocks(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
) -> tuple[UUID, list[dict]]:
    """Insert one active stable_reading_documents row + paragraph block covering the article.

    Returns (stable_document_id, block_rows) where block_rows is the list of
    inserted block dicts for assertion convenience.
    """
    async with pool.acquire() as conn:
        base_row = await conn.fetchrow(
            """
            SELECT text, content_utf16_length, record_generation
            FROM reading_bases
            WHERE id = $1
            """,
            base_id,
        )
        text = base_row["text"]
        text_len = base_row["content_utf16_length"]
        record_generation = base_row["record_generation"]
        content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        stable_document_id = await conn.fetchval(
            """
            INSERT INTO stable_reading_documents (
                reading_record_id, record_generation, title,
                document_version, content_sha256, status
            )
            VALUES ($1, $2, 'Anchor View Slice', 1, $3, 'active')
            RETURNING id
            """,
            record_id,
            record_generation,
            content_sha,
        )

        # Single paragraph block covering the whole article text. canonical_text_*
        # map into reading_bases.text (the v1 Canonical Text Layer carrier).
        await conn.execute(
            """
            INSERT INTO stable_document_blocks (
                stable_document_id, block_id, order_index, block_type,
                text_content, canonical_text_start_utf16, canonical_text_end_utf16
            )
            VALUES ($1, 'b1', 0, 'paragraph', $2, 0, $3)
            """,
            stable_document_id,
            text,
            text_len,
        )

    return stable_document_id, [
        {
            "block_id": "b1",
            "block_type": "paragraph",
            "canonical_text_start_utf16": 0,
            "canonical_text_end_utf16": text_len,
        }
    ]


@pytest.fixture
async def test_db_pool_with_stable_blocks():
    schema_name = f"test_anchor_view_blk_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        user_id = await insert_user(pool)
        article = await submit_article_ready(
            pool,
            user_id=user_id,
            plain_text=ARTICLE_TEXT,
            title="Anchor View Slice",
            language="en",
        )
        stable_document_id, _ = await _insert_stable_document_and_blocks(
            pool, record_id=article.record_id, base_id=article.base_id
        )
        try:
            yield pool, article.base_id, stable_document_id
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_load_anchor_views_returns_typed_view(
    test_db_pool_with_grammar_record,
) -> None:
    """加载 anchor views 含 anchor_segment_id (TEXT) + unit_char_count (来自 reading_units range) + block_id (range intersection)"""
    pool, _record_id, base_id = test_db_pool_with_grammar_record
    views = await load_analysis_anchor_views(pool, base_id=base_id)

    assert len(views) > 0
    view = views[0]

    # anchor_segment_id 是 TEXT，不是 UUID
    assert isinstance(view.anchor_segment_id, str)
    assert view.anchor_row_id is not None  # UUID 主键
    assert isinstance(view.anchor_row_id, UUID)

    # unit_char_count 来自 reading_units.base_*_utf16
    assert view.unit_char_count == view.unit_base_end_utf16 - view.unit_base_start_utf16
    assert view.unit_char_count >= view.anchor_char_count  # unit 至少含 anchor

    # views 按 order_index 升序
    for i in range(1, len(views)):
        assert views[i - 1].order_index <= views[i].order_index


async def test_load_anchor_views_block_intersection(
    test_db_pool_with_stable_blocks,
) -> None:
    """anchor 的 [base_start, base_end) 与 block 的 canonical_text_* 区间求交"""
    pool, base_id, _stable_document_id = test_db_pool_with_stable_blocks
    views = await load_analysis_anchor_views(pool, base_id=base_id)

    assert len(views) > 0
    for view in views:
        if view.crosses_block_boundary:
            continue
        if view.canonical_text_start_utf16 is None:
            assert view.block_type == "unknown"
            continue
        assert view.canonical_text_start_utf16 <= view.base_start_utf16
        assert view.canonical_text_end_utf16 >= view.base_end_utf16


async def test_load_anchor_views_empty_for_unknown_base(test_db_pool: asyncpg.Pool) -> None:
    """不存在的 base_id 返回空 tuple"""
    pool = test_db_pool
    views = await load_analysis_anchor_views(pool, base_id=uuid4())
    assert views == ()


async def test_load_anchor_views_no_stable_document(
    test_db_pool_with_grammar_record,
) -> None:
    """没有 stable_document 时 block_id=None, block_type='unknown'"""
    pool, _record_id, base_id = test_db_pool_with_grammar_record
    views = await load_analysis_anchor_views(pool, base_id=base_id)
    assert len(views) > 0
    for view in views:
        # 没有对应 stable_document_blocks 时全部应为 unknown
        assert view.block_type == "unknown"
        assert view.block_id is None


async def test_load_anchor_views_with_stable_block_returns_paragraph_type(
    test_db_pool_with_stable_blocks,
) -> None:
    """有 stable_document_blocks 时返回 paragraph block_type"""
    pool, base_id, _stable_document_id = test_db_pool_with_stable_blocks
    views = await load_analysis_anchor_views(pool, base_id=base_id)
    assert len(views) > 0
    expected_end = utf16_code_unit_length(ARTICLE_TEXT)
    for view in views:
        # 单 paragraph block 覆盖整篇 text，所有 anchor 都应映射到该 block
        assert view.block_type == "paragraph"
        assert view.block_id == "b1"
        assert view.canonical_text_start_utf16 == 0
        assert view.canonical_text_end_utf16 == expected_end
        assert not view.crosses_block_boundary


def test_analysis_anchor_view_dataclass_fields() -> None:
    """AnalysisAnchorView 字段集与 §3.1 设计契约一致"""
    fields = {f for f in AnalysisAnchorView.__dataclass_fields__}
    expected = {
        "anchor_segment_id",
        "anchor_row_id",
        "unit_id",
        "unit_order_index",
        "base_id",
        "order_index",
        "base_start_utf16",
        "base_end_utf16",
        "unit_base_start_utf16",
        "unit_base_end_utf16",
        "unit_char_count",
        "block_id",
        "block_type",
        "canonical_text_start_utf16",
        "canonical_text_end_utf16",
        "anchor_char_count",
        "crosses_block_boundary",
    }
    assert fields == expected, f"missing: {expected - fields}, extra: {fields - expected}"
