"""L1 — table/code metadata 全链路 DB reload 证据（真实 PostgreSQL）。

封住的合同：含确定性 GFM table（带列对齐）+ fenced code（带语言）的
Markdown 输入：

1. gate 路由 ``stable_document_ready``（table 不再一刀切 candidate）；
2. stable-ready freeze 后 ``stable_document_blocks.payload_json`` 保留
   ``language`` / ``alignments`` / ``header_rows`` / ``is_header`` /
   ``alignment``；
3. Reader snapshot（刚构建路径与 DB 重载路径）``reader_source_block``
   节点投影 ``codeLanguage`` / ``tableIsHeader`` / ``tableAlignment``，
   且两条路径结构等价。

测试模式复用 ``test_reader_snapshot_stable_block_reload.py``：隔离
PostgreSQL schema + 生产服务全链路，不做 fake connection。
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
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

L1_MARKDOWN = """# Quarterly Field Notes

The research group compared three regional pilots and recorded every
measured outcome before drafting the summary for the public review
session next month.

| Region | Score | Trend |
| :--- | :---: | ---: |
| North | 42 | rising |
| South | 37 | steady |

```python
def normalize(scores):
    return [s / max(scores) for s in scores]
```

The closing paragraph explains how the committee weighed the table
against the code audit and why the combined evidence supports the final
recommendation for readers.
"""

STABLE_FIELD_KEYS = (
    "stableBlockType",
    "stableBlockId",
    "headingLevel",
    "inlineMarks",
    "tableRole",
    "parentStableBlockId",
    # L1 projection contract.
    "codeLanguage",
    "tableIsHeader",
    "tableAlignment",
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
    schema_name = f"test_l1_meta_reload_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for L1 reload tests: {exc}")
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


async def _freeze_l1_markdown(pool: asyncpg.Pool, user_id: UUID):
    service = StableReadyInputApplicationService(pool=pool)
    return await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="quarterly-field-notes.md",
        text=L1_MARKDOWN,
        language="en",
    )


def _source_blocks_by_unit(snapshot) -> dict[str, dict[str, Any]]:
    """Collect ``reader_source_block`` nodes from the snapshot Plate value,
    keyed by unit_id, projecting the stable + L1 metadata field contract."""
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


async def _load_snapshot(pool: asyncpg.Pool, record_id: UUID, user_id: UUID):
    service = ArticleReadyPersistenceService(pool=pool)
    return await service.load_snapshot(record_id=record_id, user_id=user_id)


async def test_deterministic_table_and_code_freeze_via_stable_ready_route(
    reload_env: asyncpg.Pool,
) -> None:
    """Gate 层证据：确定性 table + 带语言 code 走 stable-ready（非 candidate）。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_l1_markdown(reload_env, user_id)

    assert result.suitability.outcome == "stable_document_ready"
    assert "table_structure_uncertain" not in result.suitability.flags
    assert "markdown_complex_structure" not in result.suitability.flags


async def test_table_and_code_payloads_persisted_in_stable_document_blocks(
    reload_env: asyncpg.Pool,
) -> None:
    """DB 层证据：freeze 后 payload_json 原样保留 language/alignment/header。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_l1_markdown(reload_env, user_id)

    async with reload_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.block_type, b.payload_json
            FROM stable_reading_documents d
            JOIN stable_document_blocks b
              ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1
            ORDER BY b.order_index ASC
            """,
            result.reading_record_id,
        )
    assert rows, "stable document blocks must be persisted"

    def _payload(row: asyncpg.Record) -> dict[str, Any]:
        raw = row["payload_json"]
        if isinstance(raw, str):
            import json

            return json.loads(raw)
        return dict(raw)

    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_type.setdefault(str(row["block_type"]), []).append(_payload(row))

    # code_block：语言透传。
    (code_payload,) = by_type["code_block"]
    assert code_payload["language"] == "python"
    assert code_payload["fenced"] is True
    assert code_payload["closed"] is True

    # table wrapper：列对齐 + 表头行数 + 无结构不确定标记。
    (table_payload,) = by_type["table"]
    assert table_payload["alignments"] == ["left", "center", "right"]
    assert table_payload["column_count"] == 3
    assert table_payload["header_rows"] == 1
    assert table_payload.get("structure_uncertain") is not True

    # table_cell：逐单元格 header 标记与对齐（3 表头 + 2×3 正文 = 9）。
    cell_payloads = by_type["table_cell"]
    assert len(cell_payloads) == 9
    header_cells = [p for p in cell_payloads if p["is_header"] is True]
    body_cells = [p for p in cell_payloads if p["is_header"] is False]
    assert len(header_cells) == 3
    assert len(body_cells) == 6
    assert [p["alignment"] for p in header_cells] == ["left", "center", "right"]
    assert [p["alignment"] for p in body_cells] == ["left", "center", "right"] * 2


async def test_snapshot_projects_code_language_and_table_metadata_after_reload(
    reload_env: asyncpg.Pool,
) -> None:
    """Snapshot DTO 层证据：刚构建与 DB 重载路径都投影 L1 metadata 且等价。"""
    user_id = await _insert_user(reload_env)
    result = await _freeze_l1_markdown(reload_env, user_id)
    record_id = result.reading_record_id

    fresh = _source_blocks_by_unit(result.snapshot)
    reloaded = _source_blocks_by_unit(
        await _load_snapshot(reload_env, record_id, user_id)
    )

    assert fresh, "fresh snapshot must carry reader_source_block nodes"
    # 结构等价（含 L1 字段）。
    assert set(fresh.keys()) == set(reloaded.keys())
    for unit_id, fresh_fields in fresh.items():
        assert reloaded[unit_id] == fresh_fields, (
            f"unit {unit_id}: reloaded fields {reloaded[unit_id]} "
            f"!= fresh {fresh_fields}"
        )

    # code_block 节点：codeLanguage 投影。
    code_nodes = [
        fields
        for fields in reloaded.values()
        if fields.get("stableBlockType") == "code_block"
    ]
    assert len(code_nodes) == 1, f"code_block nodes: {code_nodes}"
    assert code_nodes[0]["codeLanguage"] == "python"

    # table_cell 节点：表头标记与对齐投影。
    cell_nodes = [
        fields
        for fields in reloaded.values()
        if fields.get("stableBlockType") == "table_cell"
    ]
    assert len(cell_nodes) == 9, f"table_cell nodes: {cell_nodes}"
    header_cells = [f for f in cell_nodes if f.get("tableIsHeader") is True]
    body_cells = [f for f in cell_nodes if f.get("tableIsHeader") is False]
    assert len(header_cells) == 3
    assert len(body_cells) == 6
    assert sorted(f["tableAlignment"] for f in header_cells) == [
        "center",
        "left",
        "right",
    ]
    assert sorted(f["tableAlignment"] for f in body_cells) == sorted(
        ["left", "center", "right"] * 2
    )
