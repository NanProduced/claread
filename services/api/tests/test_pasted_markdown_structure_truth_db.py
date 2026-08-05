"""L2 阶段 1 Gate — pasted_text Markdown 结构真相链路（真实 PostgreSQL）。

封住的合同（任务指定真实文本：h2 + blockquote + paragraph + h3 + list，
含 ``\\[Video]`` 转义方括号、普通 HTTPS 链接、Notion aside、30k+ 长文）：

1. pasted_text 粘贴的 Markdown 走 candidate 路径时 blocks_json 不再
   全 paragraph —— heading / blockquote / list / list_item 类型与
   层级（parent_block_id）保真；
2. ``\\[Video]`` 不触发 math / content_check；安全内容（普通链接、
   安全 aside）stable-ready；
3. confirm 后 stable_document_blocks 与 candidate blocks_json 的
   block type / 层级 / 顺序一致；reading_bases.text（canonical）不含
   raw ``##`` / ``>`` / ``- `` 标记；reading_units UTF-16 范围落在
   canonical 文本内；snapshot reload 仍带 headingLevel /
   stableBlockType / inlineMarks。

测试模式复用 ``test_table_code_metadata_reload.py``：隔离
PostgreSQL schema + 生产服务全链路，不做 fake connection。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationService,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationService,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

# 任务指定真实文本：h2 + blockquote + paragraph + h3 + list，
# 含 \[Video] 转义方括号与普通 HTTPS 链接。
PASTED_MARKDOWN = """## Morning Reading Notes

> The editor pulled this quote about reading habits and long-form attention
> because the committee wanted a memorable opening for the public summary.

The committee reviewed the **proposal** and agreed that the appendix should
remain available to every participant before the vote takes place next
month in the main hall.

### Action Items

- Finalize the budget by next Tuesday and notify all department leads
- Send out the stakeholder survey to collect feedback on the proposal
- Schedule a follow-up review session with the executive team

Watch the \\[Video] summary at https://example.com/reading-notes for the
background context before the next committee session begins.
"""

# Notion aside（安全 HTML）追加段：清洗后继续，不阻断、不触发 candidate。
NOTION_ASIDE = """
<aside class="note">This callout came from a Notion page export and carries
safe informational content for every reader of the document.</aside>
"""

# candidate 触发器：image 是合法 content_check 信号（media truth）。
IMAGE_BLOCK = """
![Architecture diagram](https://example.com/images/architecture.png)
"""


def _long_pasted_markdown() -> str:
    """程序生成 30k+ 字符、超过 8000 词（envelope 阈值）的结构化
    Markdown（标题 + 列表 + 段落）。"""
    paragraph = (
        "The committee reviewed the regional pilot results and recorded "
        "every measured outcome before drafting the summary for the public "
        "review session scheduled next month in the main hall. "
    )
    parts: list[str] = ["## Long Form Reading Notes", ""]
    for index in range(80):
        parts.append(f"### Section {index + 1} Findings")
        parts.append("")
        parts.append(paragraph * 3)
        parts.append("")
        parts.append("- Record the outcome and notify the department leads")
        parts.append("- Schedule the follow-up review session with the team")
        parts.append("")
    text = "\n".join(parts)
    assert len(text) > 30_000
    return text


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
async def db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_struct_truth_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for L2 structure-truth tests: {exc}")
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


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _fetch_candidate_blocks(
    pool: asyncpg.Pool, candidate_document_id: UUID
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT blocks_json, canonical_text_preview
            FROM candidate_reading_documents
            WHERE id = $1
            """,
            candidate_document_id,
        )
    assert row is not None
    blocks = _json(row["blocks_json"])
    assert isinstance(blocks, list)
    return blocks


async def _create_candidate(
    pool: asyncpg.Pool, user_id: UUID, text: str
):
    service = CandidateDocumentCreationService(pool=pool)
    return await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=text,
        language="en",
    )


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# ---------------------------------------------------------------------------
# 1. candidate blocks_json 结构保真
# ---------------------------------------------------------------------------


async def test_pasted_markdown_candidate_blocks_are_typed_in_db(
    db_env: asyncpg.Pool,
) -> None:
    """pasted_text Markdown（image 触发 candidate）blocks_json 不再全 paragraph。"""
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, PASTED_MARKDOWN + IMAGE_BLOCK)

    assert result.suitability.outcome == "candidate_document_required"
    assert result.suitability.detected_format == "markdown"

    blocks = await _fetch_candidate_blocks(db_env, result.candidate_document_id)
    block_types = [block["block_type"] for block in blocks]

    assert "heading" in block_types, f"block_types: {block_types}"
    assert "blockquote" in block_types, f"block_types: {block_types}"
    assert "list" in block_types, f"block_types: {block_types}"
    assert "list_item" in block_types, f"block_types: {block_types}"
    assert "paragraph" in block_types, f"block_types: {block_types}"
    # 不是修复前的"全 paragraph"。
    assert set(block_types) != {"paragraph"}

    # 层级：list_item 通过 parent_block_id 挂在 list 容器下。
    by_id = {block["block_id"]: block for block in blocks}
    for item in (b for b in blocks if b["block_type"] == "list_item"):
        assert item["parent_block_id"] is not None
        assert by_id[item["parent_block_id"]]["block_type"] == "list"

    # heading payload 保留 level；inline marks 保真（**proposal**）。
    h2 = next(b for b in blocks if b["block_type"] == "heading")
    assert h2["payload_json"]["level"] == 2
    paragraph_with_marks = next(
        b for b in blocks
        if b["block_type"] == "paragraph" and b["payload_json"].get("inline_marks")
    )
    assert any(
        mark.get("type") == "strong"
        for mark in paragraph_with_marks["payload_json"]["inline_marks"]
    )

    # candidate 文本不含 raw markdown 块标记。
    for block in blocks:
        text = block.get("text_content") or ""
        assert not text.startswith("##"), text
        assert not text.startswith(">"), text
        assert not text.startswith("- "), text


# ---------------------------------------------------------------------------
# 2. 安全内容 stable-ready：\[Video] / 普通链接 / Notion aside
# ---------------------------------------------------------------------------


async def test_escaped_video_link_and_aside_are_stable_ready(
    db_env: asyncpg.Pool,
) -> None:
    """``\\[Video]`` + 普通 HTTPS 链接 + 安全 aside → stable_document_ready。"""
    user_id = await _insert_user(db_env)
    service = StableReadyInputApplicationService(pool=db_env)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="pasted_text",
        text=PASTED_MARKDOWN + NOTION_ASIDE,
        language="en",
    )

    suitability = result.suitability
    assert suitability.outcome == "stable_document_ready", (
        f"outcome={suitability.outcome}, flags={suitability.flags}, "
        f"reasons={suitability.reasons}"
    )
    assert suitability.detected_format == "markdown"
    # \[Video] 不触发 math；aside 只产生 adaptation_notice。
    assert "document_block_degraded" not in suitability.flags
    assert "markdown_complex_structure" not in suitability.flags
    adaptations = {
        record.code: record.classification for record in suitability.adaptations
    }
    assert adaptations.get("raw_html_block") == "adaptation_notice"
    assert not any(
        record.classification == "content_check"
        for record in suitability.adaptations
    ), f"adaptations: {adaptations}"

    # canonical 文本不含 raw markdown 块标记；\[Video] 以字面 [Video] 保留。
    async with db_env.acquire() as conn:
        base_row = await conn.fetchrow(
            """
            SELECT b.text
            FROM reading_bases b
            WHERE b.reading_record_id = $1 AND b.status = 'active'
            """,
            result.reading_record_id,
        )
    assert base_row is not None
    canonical = str(base_row["text"])
    for line in canonical.split("\n"):
        assert not line.startswith("##"), line
        assert not line.startswith(">"), line
        assert not line.startswith("- "), line
    assert "[Video]" in canonical


# ---------------------------------------------------------------------------
# 3. confirm 保真：candidate → stable blocks / canonical / units / snapshot
# ---------------------------------------------------------------------------


async def test_confirm_preserves_block_types_hierarchy_canonical_and_snapshot(
    db_env: asyncpg.Pool,
) -> None:
    """Candidate → Stable：block type / 层级 / 顺序 / canonical / units 保真。"""
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, PASTED_MARKDOWN + IMAGE_BLOCK)
    candidate_blocks = await _fetch_candidate_blocks(
        db_env, created.candidate_document_id
    )

    confirm_service = CandidateDocumentConfirmApplicationService(pool=db_env)
    confirmed = await confirm_service.confirm_candidate_document_and_load_snapshot(
        candidate_document_id=created.candidate_document_id,
        reading_record_id=created.reading_record_id,
        user_id=user_id,
        canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
        builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
        segmenter_version=AUTO_SEGMENTER_POLICY,
        language="en",
    )
    assert confirmed.candidate_confirmed is True

    async with db_env.acquire() as conn:
        stable_rows = await conn.fetch(
            """
            SELECT b.block_id, b.parent_block_id, b.order_index, b.block_type,
                   b.text_content, b.payload_json
            FROM stable_reading_documents d
            JOIN stable_document_blocks b
              ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1
            ORDER BY b.order_index ASC
            """,
            created.reading_record_id,
        )
        base_row = await conn.fetchrow(
            """
            SELECT b.id, b.text, b.content_utf16_length
            FROM reading_bases b
            WHERE b.reading_record_id = $1 AND b.status = 'active'
            """,
            created.reading_record_id,
        )
        unit_rows = await conn.fetch(
            """
            SELECT u.unit_id, u.order_index, u.base_start_utf16, u.base_end_utf16
            FROM reading_units u
            WHERE u.reading_record_id = $1
            ORDER BY u.order_index ASC
            """,
            created.reading_record_id,
        )

    # block type / 层级 / 顺序与 candidate blocks_json 完全一致。
    assert len(stable_rows) == len(candidate_blocks)
    for row, candidate_block in zip(stable_rows, candidate_blocks, strict=True):
        assert str(row["block_id"]) == candidate_block["block_id"]
        assert str(row["block_type"]) == candidate_block["block_type"]
        assert int(row["order_index"]) == candidate_block["order_index"]
        assert row["parent_block_id"] == candidate_block["parent_block_id"]
        assert row["text_content"] == candidate_block["text_content"]

    # canonical：无 raw markdown 块标记；UTF-16 长度一致。
    assert base_row is not None
    canonical = str(base_row["text"])
    assert int(base_row["content_utf16_length"]) == _utf16_len(canonical)
    for line in canonical.split("\n"):
        assert not line.startswith("##"), line
        assert not line.startswith(">"), line
        assert not line.startswith("- "), line
    assert "[Video]" in canonical

    # reading_units：非空、顺序单调、范围落在 canonical UTF-16 长度内。
    assert unit_rows, "reading_units must exist after confirm"
    canonical_utf16 = _utf16_len(canonical)
    previous_end = 0
    for unit in unit_rows:
        start = int(unit["base_start_utf16"])
        end = int(unit["base_end_utf16"])
        assert 0 <= start <= end <= canonical_utf16
        assert start >= previous_end - 1  # 允许相邻 unit 共享边界
        previous_end = end

    # snapshot reload：reader_source_block 带 headingLevel / stableBlockType /
    # inlineMarks。
    snapshot_service = ArticleReadyPersistenceService(pool=db_env)
    snapshot = await snapshot_service.load_snapshot(
        record_id=created.reading_record_id,
        user_id=user_id,
        expected_base_id=confirmed.base_id,
        expected_generation=confirmed.record_generation,
    )
    source_nodes: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "reader_source_block":
                source_nodes.append(node)
            for child in node.get("children", []) or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(snapshot.value)
    assert source_nodes, "snapshot must carry reader_source_block nodes"
    heading_nodes = [
        n for n in source_nodes if n.get("stableBlockType") == "heading"
    ]
    assert heading_nodes, "heading blocks must project to snapshot"
    assert all(n.get("headingLevel") for n in heading_nodes)
    assert {n.get("headingLevel") for n in heading_nodes} == {2, 3}
    # inline marks（**proposal** strong）保真到 snapshot。
    assert any(
        n.get("inlineMarks")
        for n in source_nodes
        if n.get("stableBlockType") == "paragraph"
    ), "paragraph inline marks must project to snapshot"


# ---------------------------------------------------------------------------
# 4. 30k+ 长文：too_long → candidate，块结构仍保真
# ---------------------------------------------------------------------------


async def test_long_pasted_markdown_30k_candidate_keeps_typed_blocks(
    db_env: asyncpg.Pool,
) -> None:
    """30k+ pasted Markdown 走 candidate（too_long），typed blocks 保真。"""
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _long_pasted_markdown())

    assert result.suitability.outcome == "candidate_document_required"
    assert "too_long_requires_envelope" in result.suitability.flags
    assert result.suitability.detected_format == "markdown"

    blocks = await _fetch_candidate_blocks(db_env, result.candidate_document_id)
    block_types = {block["block_type"] for block in blocks}
    assert "heading" in block_types
    assert "list_item" in block_types
    assert "paragraph" in block_types

    # order_index 连续且与数组顺序一致。
    for index, block in enumerate(blocks):
        assert block["order_index"] == index

    # 层级一致：list_item 的 parent 是 list。
    by_id = {block["block_id"]: block for block in blocks}
    for item in (b for b in blocks if b["block_type"] == "list_item"):
        assert by_id[item["parent_block_id"]]["block_type"] == "list"
