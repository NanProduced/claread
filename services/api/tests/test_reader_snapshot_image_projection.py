"""G2a-B · stable_document_tree image projection + backend loadability 合同测试。

设计依据：tmp/reader-markdown-optimization/g2a-image-representation-contract.md
（§6.5.6 snapshot value/tree 决议、§7.4 派生字段、§10.1 八规则、
§10.2 允许/拒绝矩阵、§12 #1/#6a/#6b/#13/#19/#23/#24、§13 G2a-B）。

RED-first：本轮先只写测试（生产代码零修改），失败断言全部来自
``effective_url`` 缺失 / 投影行为缺失：
  A. standalone image 在 stable_document_tree 投影 source_url + effective_url，
     snapshot.value 保持 unit-driven（无 image node / 假 unit）；
  B. inline_images 泛化：paragraph / heading / list_item / blockquote /
     table_cell（mixed）与 image-only metadata_only table_cell 的数组序、
     alt/title/before_utf16 不变，每项派生 effective_url；
  C. §10.2 URL 参数矩阵（ALLOW 11 项 / REJECT 26 项，含 R3 新增行）；
  D. raw truth：raw backslash/space 与 %5C/%20 区分、unsafe 原样保留、
     投影不原地修改 stable payload 对象；
  E. fresh/reload PostgreSQL：冻结后两次 snapshot 的 tree 逐字段一致、
     snapshot.value 一致且无 image 假节点、teardown 删除 schema。

公开 seam：现有 ``snapshot._build_stable_document_tree`` / 完整
``build_reader_plate_snapshot`` / ``StableReadyInputApplicationService`` /
``ArticleReadyPersistenceService.load_snapshot``；不冻结任何新 public API。
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.base_builder import (
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.snapshot import (
    _build_stable_document_tree,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.reader_orchestration_test_support import fixture_analysis_progress
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

# ---------------------------------------------------------------------------
# Seam helpers：构建 ReadingBaseBuildResult（稳定块行 = repository 重载
# 同款 dict 形态），再经现有 _build_stable_document_tree / 完整 snapshot。
# ---------------------------------------------------------------------------


def _make_block(
    block_id: str,
    order_index: int,
    block_type: str,
    payload: dict[str, Any],
    *,
    parent_block_id: str | None = None,
    text_content: str | None = None,
    start: int | None = None,
    end: int | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "parent_block_id": parent_block_id,
        "order_index": order_index,
        "block_type": block_type,
        "text_content": text_content,
        "payload_json": payload,
        "source_refs_json": {},
        "quality_json": {},
        "interpretation_policy_json": policy or {},
        "block_start_utf16": start,
        "block_end_utf16": end,
    }


def _build_result(blocks: list[dict[str, Any]], *, canonical_text: str | None = None):
    if canonical_text is None:
        canonical_text = (
            "\n\n".join(b["text_content"] or "" for b in blocks if b["text_content"])
            or "Placeholder body text for a structural-only tree."
        )
    base = build_reading_base_from_canonical_text(
        reading_record_id="record-1",
        base_id="base-1",
        canonical_text=canonical_text,
        title=None,
        language="en",
    )
    return replace(base, stable_document_blocks=tuple(blocks))


def _tree(blocks: list[dict[str, Any]], *, canonical_text: str | None = None):
    return _build_stable_document_tree(_build_result(blocks, canonical_text=canonical_text))


def _snapshot(blocks: list[dict[str, Any]], *, canonical_text: str | None = None):
    return build_reader_plate_snapshot(
        _build_result(blocks, canonical_text=canonical_text),
        snapshot_taken_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        last_event_sequence=1,
        analysis_progress=fixture_analysis_progress(),
    )


def _standalone_image_node(url: str):
    payload = {
        "source_url": url,
        "alt_text": "a",
        "title": None,
        "position_kind": "standalone",
    }
    tree = _tree([_make_block("img-1", 0, "image", payload, text_content=None)])
    images = [n for n in tree if n.block_type == "image"]
    assert len(images) == 1
    return images[0]


def _walk_nodes(nodes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(_walk_nodes(node["children"]))
    return out


def _value_has_image_node(value: list[dict[str, Any]]) -> bool:
    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            if node.get("type") == "image" or node.get("block_type") == "image":
                return True
            return any(walk(child) for child in node.get("children", []) or [])
        if isinstance(node, list):
            return any(walk(item) for item in node)
        return False

    return walk(value)


# ---------------------------------------------------------------------------
# A. standalone image（§12 #1 / #6a tree 侧）
# ---------------------------------------------------------------------------


def test_standalone_image_node_projects_source_and_effective_url() -> None:
    payload = {
        "source_url": "https://example.com/a.png",
        "alt_text": "a",
        "title": None,
        "position_kind": "standalone",
    }
    blocks = [
        _make_block(
            "para-1",
            0,
            "paragraph",
            {},
            text_content="Hello world.",
            start=0,
            end=12,
        ),
        _make_block("img-1", 1, "image", payload, text_content=None),
    ]
    tree = _tree(blocks)
    images = [n for n in tree if n.block_type == "image"]
    assert len(images) == 1
    node = images[0]
    assert node.payload["source_url"] == "https://example.com/a.png"
    assert node.payload["alt_text"] == "a"
    assert node.payload["title"] is None
    assert node.payload["position_kind"] == "standalone"
    assert node.text_content is None
    # RED：effective_url 投影尚不存在。
    assert "effective_url" in node.payload
    assert node.payload["effective_url"] == "https://example.com/a.png"
    assert node.unit_id is None
    assert node.anchor_segment_ids == []


def test_snapshot_value_stays_unit_driven_without_image_node() -> None:
    payload = {
        "source_url": "https://example.com/a.png",
        "alt_text": "a",
        "title": None,
        "position_kind": "standalone",
    }
    blocks = [
        _make_block(
            "para-1",
            0,
            "paragraph",
            {},
            text_content="Hello world.",
            start=0,
            end=12,
        ),
        _make_block("img-1", 1, "image", payload, text_content=None),
    ]
    snapshot = _snapshot(blocks)
    assert len(snapshot.value) == 1, "value 只含 paragraph unit，无 image 假节点"
    assert not _value_has_image_node(snapshot.value)
    value_json = json.dumps(snapshot.value)
    assert "inline_images" not in value_json
    assert "effective_url" not in value_json
    # tree 同时携带两个 block（结构真相在 tree，不在 value）。
    assert [n.block_type for n in snapshot.stable_document_tree] == [
        "paragraph",
        "image",
    ]


# ---------------------------------------------------------------------------
# B. inline_images 泛化（§7.4 / §12 #19 / §6.5.2）
# ---------------------------------------------------------------------------

_MIXED_ENTRIES: list[dict[str, Any]] = [
    {
        "source_url": "https://example.com/a.png",
        "alt_text": "a",
        "title": "T",
        "before_utf16": 5,
    },
    {
        "source_url": "javascript:alert(1)",
        "alt_text": "b",
        "title": None,
        "before_utf16": 5,
    },
]

_IMAGE_ONLY_CELL_ENTRIES: list[dict[str, Any]] = [
    {
        "source_url": "https://example.com/c.png",
        "alt_text": "c",
        "title": None,
        "before_utf16": 0,
    },
]

_INLINE_CASES: list[tuple[str, str | None, list[dict[str, Any]], bool]] = [
    ("paragraph", "Paragraph text with an inline image at the end.", _MIXED_ENTRIES, True),
    ("heading", "Heading with an inline image.", _MIXED_ENTRIES, True),
    ("list_item", "List item text with an inline image.", _MIXED_ENTRIES, True),
    ("blockquote", "Quoted line with an inline image.", _MIXED_ENTRIES, True),
    ("table_cell", "Cell text with an inline image.", _MIXED_ENTRIES, True),
    ("table_cell_image_only", None, _IMAGE_ONLY_CELL_ENTRIES, False),
]


@pytest.mark.parametrize(
    ("case_name", "text_content", "entries", "expect_unit"),
    _INLINE_CASES,
    ids=[case for case, _, _, _ in _INLINE_CASES],
)
def test_inline_images_project_into_owning_node(
    case_name: str,
    text_content: str | None,
    entries: list[dict[str, Any]],
    expect_unit: bool,
) -> None:
    block_type = "table_cell" if case_name == "table_cell_image_only" else case_name
    start = end = None
    if text_content is not None:
        start, end = 0, len(text_content)
    payload = {"inline_images": copy.deepcopy(entries)}
    blocks = [
        _make_block(
            "block-1",
            0,
            block_type,
            payload,
            text_content=text_content,
            start=start,
            end=end,
        )
    ]
    tree = _tree(blocks)
    assert len(tree) == 1
    node = tree[0]
    assert node.block_type == block_type
    # 数组序不变：inline_ordinal 语义不变。
    projected = node.payload["inline_images"]
    assert [item["source_url"] for item in projected] == [item["source_url"] for item in entries]
    for original, item in zip(entries, projected, strict=True):
        assert item["alt_text"] == original["alt_text"]
        assert item["title"] == original["title"]
        assert item["before_utf16"] == original["before_utf16"]
        # RED：每项 effective_url 投影尚不存在。
        assert "effective_url" in item
    assert projected[0]["effective_url"] == (
        "https://example.com/c.png"
        if case_name == "table_cell_image_only"
        else "https://example.com/a.png"
    )
    if len(entries) > 1:
        assert projected[1]["effective_url"] is None
    # owning node 本身不产生额外 image child。
    assert node.children == []
    if case_name == "table_cell_image_only":
        assert node.text_content is None
        assert node.unit_id is None
        assert node.anchor_segment_ids == []
    else:
        assert node.unit_id is not None


# ---------------------------------------------------------------------------
# C. §10.2 URL allow/reject 矩阵（完整真值表，经 tree 投影观察）
# ---------------------------------------------------------------------------

_ALLOW_URLS = [
    "https://example.com/a.png",
    "http://example.com/a.png",
    "HTTP://Example.COM/a.png",
    "http://example.com:65535/a.png",
    "http://example.com:8080/a.png?q=1#f",
    "http://127.0.0.1/a.png",
    "http://[::1]:8080/a.png",
    "https://xn--r8jz45g.jp/a.png",
    "http://example.com",
    "https://example.com/a%20b.png",
    "http://example.com/%5C@evil.com/a.png",
]

_REJECT_URLS = [
    "",
    "  https://example.com/a.png  ",
    "/a.png",
    "a.png",
    "//example.com/a.png",
    "http:foo",
    "https:foo",
    "http://",
    "https:///",
    "http://user:pass@example.com/a.png",
    "http://user@example.com/a.png",
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "file:///etc/passwd",
    "blob:https://x/y",
    "mailto:a@b.com",
    "http://exa\u0000mple.com/a.png",
    "http://example.com/a\u0001.png",
    "http://exa mple.com/a.png",
    "http://example.com/a b.png",
    "http://example.com\\@evil.com/a.png",
    "http://example.com\\evil/a.png",
    "http://example.com:bad/a.png",
    "http://example.com:65536/a.png",
    "http://example.com:99999/a.png",
    "http://example.com:-1/a.png",
    "http://[::1",
]


@pytest.mark.parametrize("url", _ALLOW_URLS)
def test_url_matrix_allows(url: str) -> None:
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert "effective_url" in node.payload
    assert node.payload["effective_url"] == url


@pytest.mark.parametrize("url", _REJECT_URLS)
def test_url_matrix_rejects(url: str) -> None:
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert "effective_url" in node.payload
    assert node.payload["effective_url"] is None


# ---------------------------------------------------------------------------
# D. raw truth（§12 #23/#24 + §10.1 补充不变量）
# ---------------------------------------------------------------------------


def test_raw_backslash_preserved_but_effective_url_null() -> None:
    url = "http://example.com/a\\b.png"
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert node.payload["effective_url"] is None


def test_percent_5C_preserved_and_effective_url_derived() -> None:
    url = "http://example.com/a%5Cb.png"
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert node.payload["effective_url"] == url


def test_raw_space_preserved_but_effective_url_null() -> None:
    url = "http://example.com/a b.png"
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert node.payload["effective_url"] is None


def test_percent_20_preserved_and_effective_url_derived() -> None:
    url = "https://example.com/a%20b.png"
    node = _standalone_image_node(url)
    assert node.payload["source_url"] == url
    assert node.payload["effective_url"] == url


def test_unsafe_url_never_trimmed_lowercased_or_overwritten() -> None:
    padded = "  https://example.com/a.png  "
    node = _standalone_image_node(padded)
    assert node.payload["source_url"] == padded
    assert node.payload["effective_url"] is None

    unsafe = "javascript:alert(1)"
    node = _standalone_image_node(unsafe)
    assert node.payload["source_url"] == unsafe
    assert node.payload["effective_url"] is None

    mixed_case = "HTTP://Example.COM/a.png"
    node = _standalone_image_node(mixed_case)
    assert node.payload["source_url"] == mixed_case
    assert node.payload["effective_url"] == mixed_case


def test_projection_does_not_mutate_source_payload_objects() -> None:
    standalone_payload = {
        "source_url": "https://example.com/a.png",
        "alt_text": "a",
        "title": None,
        "position_kind": "standalone",
    }
    standalone_original = copy.deepcopy(standalone_payload)
    blocks = [_make_block("img-1", 0, "image", standalone_payload, text_content=None)]
    _tree(blocks)
    assert blocks[0]["payload_json"] == standalone_original
    assert "effective_url" not in standalone_payload

    inline_payload = {
        "inline_images": [
            {
                "source_url": "https://example.com/i.png",
                "alt_text": "i",
                "title": None,
                "before_utf16": 0,
            }
        ]
    }
    inline_original = copy.deepcopy(inline_payload)
    blocks = [_make_block("cell-1", 0, "table_cell", inline_payload, text_content=None)]
    _tree(blocks)
    assert blocks[0]["payload_json"] == inline_original
    assert "effective_url" not in inline_payload["inline_images"][0]


# ---------------------------------------------------------------------------
# E. fresh/reload PostgreSQL（§12 #13 / #19 / #6b 后端侧）
# ---------------------------------------------------------------------------


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
async def image_projection_db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_img_proj_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for image projection tests: {exc}")
    pool = await _make_pool(schema_name)
    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        remaining = await admin_conn.fetchval(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = $1",
            schema_name,
        )
        await admin_conn.close()
        assert int(remaining) == 0, f"schema {schema_name} must be dropped at teardown"


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


_ENGLISH_PARAGRAPH = (
    "The committee reviewed the regional pilot results and recorded every "
    "measured outcome before drafting the summary for the public review "
    "session scheduled next month in the main hall near the river. "
    "Participants agreed that the appendix should remain available to every "
    "reader before the final vote takes place, and the editors promised to "
    "publish the complete dataset together with the annotated methodology "
    "section so that anyone can verify each recorded number independently."
)

# 合法正文 + standalone image + paragraph inline image + 非 paragraph
# owning block（heading）inline image + image-only table_cell + safe/拒绝 URL。
_IMAGE_DOC_MARKDOWN = (
    "# Mixed Heading with words ![head](https://example.com/h.png)\n\n"
    + _ENGLISH_PARAGRAPH
    + " leading text ![inline](https://example.com/i.png) and an unsafe tail "
    + "![bad](javascript:alert(1)).\n\n"
    + "![standalone](http://example.com/s.png)\n\n"
    + "| figure | note |\n"
    + "| --- | --- |\n"
    + "| ![cell](https://example.com/c.png) | supporting note |\n"
)


def _project_tree(nodes) -> list[dict[str, Any]]:
    def project(node) -> dict[str, Any]:
        return {
            "block_id": node.block_id,
            "parent_block_id": node.parent_block_id,
            "order_index": node.order_index,
            "block_type": node.block_type,
            "text_content": node.text_content,
            "unit_id": node.unit_id,
            "anchor_segment_ids": node.anchor_segment_ids,
            "payload": node.payload,
            "children": [project(child) for child in node.children],
        }

    return [project(node) for node in nodes]


async def test_image_tree_survives_fresh_and_reloaded_snapshots(
    image_projection_db_env: asyncpg.Pool,
) -> None:
    pool = image_projection_db_env
    user_id = await _insert_user(pool)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="pasted_text",
        text=_IMAGE_DOC_MARKDOWN,
        language="en",
    )
    record_id = result.reading_record_id
    fresh = result.snapshot

    loader = ArticleReadyPersistenceService(pool=pool)
    reloaded = await loader.load_snapshot(record_id=record_id, user_id=user_id)

    # tree 逐字段一致（parent / order / inline 数组序 / source_url /
    # effective_url / unit / anchor）。
    fresh_tree = _project_tree(fresh.stable_document_tree)
    reloaded_tree = _project_tree(reloaded.stable_document_tree)
    assert fresh_tree == reloaded_tree, "fresh/reload stable tree image projection mismatch"
    fresh_nodes = _walk_nodes(fresh_tree)

    # standalone image：source_url 原样 + effective_url 派生 + 无 unit/anchor。
    image_nodes = [n for n in fresh_nodes if n["block_type"] == "image"]
    assert len(image_nodes) == 1
    standalone = image_nodes[0]
    assert standalone["payload"]["source_url"] == "http://example.com/s.png"
    assert standalone["payload"]["effective_url"] == "http://example.com/s.png"
    assert standalone["payload"]["position_kind"] == "standalone"
    assert standalone["unit_id"] is None
    assert standalone["anchor_segment_ids"] == []

    # owning block inline_images：heading / paragraph / table_cell。
    heading = next(n for n in fresh_nodes if n["block_type"] == "heading")
    head_images = heading["payload"]["inline_images"]
    assert head_images[0]["source_url"] == "https://example.com/h.png"
    assert head_images[0]["effective_url"] == "https://example.com/h.png"

    paragraph = next(n for n in fresh_nodes if n["block_type"] == "paragraph")
    para_images = paragraph["payload"]["inline_images"]
    by_url = {item["source_url"]: item for item in para_images}
    assert by_url["https://example.com/i.png"]["effective_url"] == ("https://example.com/i.png")
    assert by_url["javascript:alert(1)"]["effective_url"] is None

    # image-only table_cell：无 unit/anchor，metadata_only 结构保留。
    image_only_cells = [
        n for n in fresh_nodes if n["block_type"] == "table_cell" and n["text_content"] is None
    ]
    assert len(image_only_cells) == 1
    cell = image_only_cells[0]
    cell_images = cell["payload"]["inline_images"]
    assert cell_images[0]["source_url"] == "https://example.com/c.png"
    assert cell_images[0]["effective_url"] == "https://example.com/c.png"
    assert cell["unit_id"] is None
    assert cell["anchor_segment_ids"] == []

    # snapshot.value 守恒：fresh == reloaded，且无 image 假节点。
    assert json.dumps(fresh.value, sort_keys=True) == json.dumps(reloaded.value, sort_keys=True)
    assert not _value_has_image_node(fresh.value)
    value_json = json.dumps(fresh.value)
    assert "inline_images" not in value_json
    assert "effective_url" not in value_json

    # 投影不得改写 DB 行 payload。
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload_json FROM stable_document_blocks WHERE stable_document_id = $1",
            result.stable_document_id,
        )
    assert rows
    for row in rows:
        payload = _json(row["payload_json"])
        assert "effective_url" not in payload
        for entry in payload.get("inline_images") or []:
            assert "effective_url" not in entry


# ---------------------------------------------------------------------------
# F. image_source_overrides 纯内存投影 tracer
#
# seam：build_reader_plate_snapshot(image_source_overrides=...) 的
# standalone key = (block_id, None)，inline key = (block_id, ordinal)。
# override 存在性用 `key in overrides` 判定（空串也是存在的 override）。
# ---------------------------------------------------------------------------

_G2D_STANDALONE_PAYLOAD: dict[str, Any] = {
    "source_url": "https://example.com/a.png",
    "alt_text": "a",
    "title": None,
    "position_kind": "standalone",
}


def _snapshot_with_overrides(
    blocks: list[dict[str, Any]],
    overrides: dict[tuple[str, int | None], str],
):
    return build_reader_plate_snapshot(
        _build_result(blocks),
        snapshot_taken_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        last_event_sequence=1,
        analysis_progress=fixture_analysis_progress(),
        image_source_overrides=overrides,
    )


def _g2d_standalone_node(overrides: dict[tuple[str, int | None], str]):
    blocks = [
        _make_block(
            "img-1",
            0,
            "image",
            copy.deepcopy(_G2D_STANDALONE_PAYLOAD),
            text_content=None,
        )
    ]
    snapshot = _snapshot_with_overrides(blocks, overrides)
    images = [n for n in snapshot.stable_document_tree if n.block_type == "image"]
    assert len(images) == 1
    return images[0]


def test_g2d_standalone_safe_override_projects_raw_and_effective() -> None:
    override = "https://cdn.example.com/replaced.png"
    node = _g2d_standalone_node({("img-1", None): override})
    assert node.payload["source_url"] == "https://example.com/a.png"
    assert node.payload["override_url"] == override
    assert node.payload["effective_url"] == override


def test_g2d_standalone_invalid_override_keeps_raw_and_never_falls_back() -> None:
    override = "javascript:alert(1)"
    node = _g2d_standalone_node({("img-1", None): override})
    # source_url 本身 safe，但 override 存在时绝不回退。
    assert node.payload["source_url"] == "https://example.com/a.png"
    assert node.payload["override_url"] == override
    assert node.payload["effective_url"] is None


def test_g2d_no_override_row_omits_key_and_derives_from_source() -> None:
    # override 行指向其他 block：本节点键缺失、effective 从 source 派生。
    node = _g2d_standalone_node({("other-block", None): "https://cdn.example.com/x.png"})
    assert "override_url" not in node.payload
    assert node.payload["effective_url"] == "https://example.com/a.png"


def test_g2d_empty_string_override_is_present_with_null_effective() -> None:
    node = _g2d_standalone_node({("img-1", None): ""})
    assert "override_url" in node.payload
    assert node.payload["override_url"] == ""
    assert node.payload["effective_url"] is None


def test_g2d_inline_ordinal_hits_only_targeted_item() -> None:
    entries = [
        {
            "source_url": "https://example.com/i0.png",
            "alt_text": "i0",
            "title": None,
            "before_utf16": 0,
        },
        {
            "source_url": "https://example.com/i1.png",
            "alt_text": "i1",
            "title": None,
            "before_utf16": 3,
        },
    ]
    payload = {"inline_images": copy.deepcopy(entries)}
    blocks = [
        _make_block(
            "block-1",
            0,
            "paragraph",
            payload,
            text_content="Text with two inline images here.",
            start=0,
            end=len("Text with two inline images here."),
        )
    ]
    override = "https://cdn.example.com/only-1.png"
    snapshot = _snapshot_with_overrides(blocks, {("block-1", 1): override})
    node = snapshot.stable_document_tree[0]
    projected = node.payload["inline_images"]
    assert "override_url" not in projected[0]
    assert projected[0]["effective_url"] == "https://example.com/i0.png"
    assert projected[0]["source_url"] == "https://example.com/i0.png"
    assert projected[1]["override_url"] == override
    assert projected[1]["effective_url"] == override
    assert projected[1]["source_url"] == "https://example.com/i1.png"


def test_g2d_override_projection_does_not_mutate_input_payloads() -> None:
    standalone_payload = copy.deepcopy(_G2D_STANDALONE_PAYLOAD)
    standalone_original = copy.deepcopy(standalone_payload)
    inline_payload = {
        "inline_images": [
            {
                "source_url": "https://example.com/i.png",
                "alt_text": "i",
                "title": None,
                "before_utf16": 0,
            }
        ]
    }
    inline_original = copy.deepcopy(inline_payload)
    blocks = [
        _make_block("img-1", 0, "image", standalone_payload, text_content=None),
        _make_block(
            "cell-1",
            1,
            "table_cell",
            inline_payload,
            text_content=None,
        ),
    ]
    _snapshot_with_overrides(
        blocks,
        {("img-1", None): "https://cdn.example.com/s.png", ("cell-1", 0): ""},
    )
    assert standalone_payload == standalone_original
    assert inline_payload == inline_original


def test_g2d_snapshot_value_stays_free_of_override_and_image_nodes() -> None:
    blocks = [
        _make_block(
            "para-1",
            0,
            "paragraph",
            {},
            text_content="Hello world.",
            start=0,
            end=12,
        ),
        _make_block(
            "img-1",
            1,
            "image",
            copy.deepcopy(_G2D_STANDALONE_PAYLOAD),
            text_content=None,
        ),
    ]
    snapshot = _snapshot_with_overrides(blocks, {("img-1", None): "https://cdn.example.com/s.png"})
    assert not _value_has_image_node(snapshot.value)
    value_json = json.dumps(snapshot.value)
    assert "override_url" not in value_json
    assert "inline_images" not in value_json
    assert "effective_url" not in value_json
