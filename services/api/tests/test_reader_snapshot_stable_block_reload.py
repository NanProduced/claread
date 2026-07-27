"""R1 Phase 3 — Reader 持久化重载 Stable Block 结构等价红灯测试。

封住的失效点：``repository.load_snapshot_facts`` 只读 ``reading_units``
的 legacy 字段，从不 JOIN active Stable Document，导致数据库中的 Stable
Document 结构正确、但 Reader Snapshot 重载后 stable_block_type /
heading_level / inline_marks / table_role / parent 全部丢失，Reader 把
标题与强调退化为普通段落（刚构建路径与重载路径结构不等价）。

测试策略（任务书 Phase 3 要求）：
- 优先使用隔离 PostgreSQL schema 的真实 repository 测试（非 fake connection）。
- 刚构建路径：``StableReadyInputApplicationService
  .freeze_stable_ready_input_and_load_snapshot``（生产统一输入全链路：
  normalize → gate → freeze → snapshot）。
- 重载路径：``ArticleReadyPersistenceService.load_snapshot``
  （repository.load_snapshot_facts → build_reader_plate_snapshot）。
- fail-soft：无 Stable Document / generation 失配 / 范围失配 / 重复精确
  匹配均不得抛错，且不得污染 unit。

精确测试文本与任务书一致（h2×1、h3×3、em×1、5×paragraph）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

R1_MARKDOWN = """## 6. Implementation Plan

*How we will roll this out safely, step by step.*

Since this refactoring is extensive, and AAT has a large number of servers running different platform versions, we first need to confirm a stable version as the baseline before proceeding. The specific steps are as follows:

### Step 1: Streamline Server Deployment Architecture

Optimize the platform's deployment on servers by reducing the number of unnecessary containers, freeing up memory to be allocated to MongoDB. This step will not affect any platform functionality — all running services will be properly preserved and handled.

### Step 2: Data Storage Migration & Feature Adaptation

Migrate playback statistics and related data from MySQL to MongoDB, and implement the previously customized statistical features according to AAT's requirements.

### Step 3: Canary Deployment & Validation

After the changes are complete, we recommend performing a canary deployment on a test server first to validate the performance and behavior of the refactored service. Once confirmed stable, it can be gradually rolled out to all servers."""

STABLE_FIELD_KEYS = (
    "stableBlockType",
    "stableBlockId",
    "headingLevel",
    "inlineMarks",
    "tableRole",
    "parentStableBlockId",
)


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


@pytest.fixture
async def reload_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_r1_stable_reload_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for R1 reload tests: {exc}")
    pool = await _make_pool(schema_name)
    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users DEFAULT VALUES RETURNING id"
        )
    assert isinstance(user_id, UUID)
    return user_id


async def _freeze_r1_markdown(pool: asyncpg.Pool, user_id: UUID):
    service = StableReadyInputApplicationService(pool=pool)
    return await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="pasted_text",
        text=R1_MARKDOWN,
        language="en",
    )


def _source_blocks_by_unit(snapshot) -> dict[str, dict[str, Any]]:
    """Collect ``reader_source_block`` nodes from the snapshot Plate value,
    keyed by unit_id, projecting only the stable field contract."""
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "reader_source_block":
                unit_id = str(node.get("unit_id"))
                found[unit_id] = {
                    key: node.get(key)
                    for key in STABLE_FIELD_KEYS
                    if key in node
                }
            for child in node.get("children", []) or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(snapshot.value)
    return found


async def _load_facts(pool: asyncpg.Pool, record_id: UUID, user_id: UUID):
    repository = ReaderOrchestrationRepository(pool=pool)
    async with pool.acquire() as conn:
        return await repository.load_snapshot_facts(
            conn, record_id=record_id, user_id=user_id
        )


async def _load_snapshot(pool: asyncpg.Pool, record_id: UUID, user_id: UUID):
    service = ArticleReadyPersistenceService(pool=pool)
    return await service.load_snapshot(record_id=record_id, user_id=user_id)


async def test_fresh_and_reloaded_snapshots_are_structurally_equivalent(
    reload_env: asyncpg.Pool,
) -> None:
    """Gate 2 核心：同一条记录"刚构建"与"数据库重载"两条路径的对外
    Snapshot stable 结构必须等价。"""
    pool = reload_env
    user_id = await _insert_user(pool)
    result = await _freeze_r1_markdown(pool, user_id)
    record_id = result.reading_record_id

    fresh = _source_blocks_by_unit(result.snapshot)
    reloaded = _source_blocks_by_unit(
        await _load_snapshot(pool, record_id, user_id)
    )

    assert fresh, "fresh snapshot must carry reader_source_block nodes"
    assert set(fresh.keys()) == set(reloaded.keys())
    for unit_id, fresh_fields in fresh.items():
        assert reloaded[unit_id] == fresh_fields, (
            f"unit {unit_id}: reloaded stable fields {reloaded[unit_id]} "
            f"!= fresh {fresh_fields}"
        )

    # 精确期望：h2×1（level 2）、h3×3（level 3）。
    headings = {
        unit_id: fields
        for unit_id, fields in reloaded.items()
        if fields.get("stableBlockType") == "heading"
    }
    levels = sorted(
        fields.get("headingLevel") for fields in headings.values()
    )
    assert levels == [2, 3, 3, 3], f"heading levels after reload: {levels}"

    # 精确期望：斜体副标题段携带 1 个 emphasis inline mark。
    marked = [
        fields
        for fields in reloaded.values()
        if fields.get("inlineMarks")
    ]
    assert len(marked) == 1, f"marked units after reload: {marked}"
    marks = marked[0]["inlineMarks"]
    assert len(marks) == 1
    assert marks[0]["type"] == "em"



async def test_reloaded_facts_units_carry_stable_block_metadata(
    reload_env: asyncpg.Pool,
) -> None:
    """repository 层：重载后的 BuiltReadingUnit 必须携带 stable 元数据。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)

    facts = await _load_facts(reload_env, result.reading_record_id, user_id)
    units = facts.build_result.units

    heading_units = [u for u in units if u.stable_block_type == "heading"]
    assert len(heading_units) == 4
    assert all(u.heading_level is not None for u in heading_units)
    assert all(u.stable_block_id for u in heading_units)
    # A5 规则：只有 heading 覆盖 unit_type。
    assert all(u.unit_type == "heading" for u in heading_units)

    marked_units = [u for u in units if u.inline_marks]
    assert len(marked_units) == 1
    assert marked_units[0].inline_marks[0]["type"] == "em"
    # 无标记的段落不得携带 inline_marks。
    plain = [
        u
        for u in units
        if u.stable_block_type == "paragraph" and not u.inline_marks
    ]
    assert len(plain) == 4


async def test_reloaded_navigation_units_carry_stable_fields(
    reload_env: asyncpg.Pool,
) -> None:
    """NavigationUnitFact 公开合同：stable_block_type / heading_level。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)

    reloaded = await _load_snapshot(reload_env, result.reading_record_id, user_id)
    nav_by_unit = {u.unit_id: u for u in reloaded.navigation.units}

    facts = await _load_facts(reload_env, result.reading_record_id, user_id)
    for nav in facts.build_result.navigation_units:
        source = nav_by_unit[nav.unit_id]
        assert source.stable_block_type == nav.stable_block_type
        assert source.heading_level == nav.heading_level

    heading_nav = [
        u for u in reloaded.navigation.units if u.stable_block_type == "heading"
    ]
    assert len(heading_nav) == 4
    assert sorted(u.heading_level for u in heading_nav) == [2, 3, 3, 3]


async def test_record_without_stable_document_keeps_legacy_fallback(
    reload_env: asyncpg.Pool,
) -> None:
    """fail-soft：删除 Stable Document 后重载不得抛错，单元退回 legacy
    （无 stable 字段），Reader 仍渲染普通段落。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)
    record_id = result.reading_record_id

    async with reload_env.acquire() as conn:
        await conn.execute(
            "DELETE FROM stable_reading_documents WHERE reading_record_id = $1",
            record_id,
        )

    facts = await _load_facts(reload_env, record_id, user_id)
    assert all(u.stable_block_type is None for u in facts.build_result.units)
    assert all(not u.inline_marks for u in facts.build_result.units)
    assert all(u.heading_level is None for u in facts.build_result.units)

    snapshot = await _load_snapshot(reload_env, record_id, user_id)
    blocks = _source_blocks_by_unit(snapshot)
    assert blocks, "source blocks must still exist for legacy units"
    for fields in blocks.values():
        assert "stableBlockType" not in fields


async def test_generation_fence_ignores_mismatched_stable_document(
    reload_env: asyncpg.Pool,
) -> None:
    """generation fence：stable document 的 record_generation 与记录不一致
    时必须 fail-soft 忽略，不得投影过期结构。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)
    record_id = result.reading_record_id

    async with reload_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE stable_reading_documents
            SET record_generation = record_generation + 1
            WHERE reading_record_id = $1
            """,
            record_id,
        )

    facts = await _load_facts(reload_env, record_id, user_id)
    assert all(u.stable_block_type is None for u in facts.build_result.units)


async def test_mismatched_block_range_does_not_pollute_unit(
    reload_env: asyncpg.Pool,
) -> None:
    """精确范围匹配：canonical 偏移失配的 block 不得投影到任何 unit，
    其他精确匹配的 unit 不受影响。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)
    record_id = result.reading_record_id

    async with reload_env.acquire() as conn:
        shifted = await conn.fetchval(
            """
            UPDATE stable_document_blocks
            SET canonical_text_start_utf16 = canonical_text_start_utf16 + 1
            WHERE stable_document_id = $1
              AND block_type = 'heading'
              AND order_index = (
                  SELECT min(order_index)
                  FROM stable_document_blocks
                  WHERE stable_document_id = $1 AND block_type = 'heading'
              )
            RETURNING 1
            """,
            result.stable_document_id,
        )
        assert shifted == 1

    facts = await _load_facts(reload_env, record_id, user_id)
    heading_units = [u for u in facts.build_result.units if u.unit_type == "heading"]
    # 4 个 heading unit 中，失配的那个退回 legacy，其余 3 个保持 stable。
    assert len(heading_units) == 4
    stable_headings = [u for u in heading_units if u.stable_block_type == "heading"]
    legacy_headings = [u for u in heading_units if u.stable_block_type is None]
    assert len(stable_headings) == 3
    assert len(legacy_headings) == 1


async def test_duplicate_exact_match_is_deterministic_first_wins(
    reload_env: asyncpg.Pool,
) -> None:
    """重复精确匹配：同一范围出现多个 block 时按 order_index 最小者
    取胜（与 builder annotations_by_range.setdefault 语义一致），结果确定。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_r1_markdown(reload_env, user_id)
    record_id = result.reading_record_id

    async with reload_env.acquire() as conn:
        first_heading = await conn.fetchrow(
            """
            SELECT block_id, text_content,
                   canonical_text_start_utf16 AS s,
                   canonical_text_end_utf16 AS e,
                   order_index
            FROM stable_document_blocks
            WHERE stable_document_id = $1 AND block_type = 'heading'
            ORDER BY order_index ASC
            LIMIT 1
            """,
            result.stable_document_id,
        )
        assert first_heading is not None
        max_order = await conn.fetchval(
            "SELECT max(order_index) FROM stable_document_blocks WHERE stable_document_id = $1",
            result.stable_document_id,
        )
        # 插入一个"抢注"同范围的 paragraph block（order_index 更大）。
        await conn.execute(
            """
            INSERT INTO stable_document_blocks (
                stable_document_id, block_id, order_index, block_type,
                text_content, payload_json, source_refs_json,
                canonical_text_start_utf16, canonical_text_end_utf16,
                interpretation_policy_json, quality_json
            )
            VALUES (
                $1, $2, $3, 'paragraph',
                $4, '{}'::jsonb, '{}'::jsonb,
                $5, $6,
                '{"route": "main_reading"}'::jsonb, '{}'::jsonb
            )
            """,
            result.stable_document_id,
            "r1-duplicate-block",
            int(max_order) + 1,
            first_heading["text_content"],
            first_heading["s"],
            first_heading["e"],
        )

    facts = await _load_facts(reload_env, record_id, user_id)
    matched = [
        u
        for u in facts.build_result.units
        if u.base_start_utf16 == first_heading["s"]
        and u.base_end_utf16 == first_heading["e"]
    ]
    assert len(matched) == 1
    # first-wins：原 heading block（order_index 最小）获胜，不被 paragraph 覆盖。
    assert matched[0].stable_block_type == "heading"
    assert matched[0].stable_block_id == first_heading["block_id"]
