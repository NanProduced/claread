"""冻结后图片 source URL override 的 PUT/DELETE API 与持久化合同测试。

设计依据：
- 图片 override 存储 API 设计（B1 表结构、§8.1/§8.2 状态矩阵、§9 API 合同、
  §10 双 partial upsert、§13 R1-R26）
- 冻结图片表示合同 §7.4/§8.2/§10.1。

观察 seam：HTTP interface、
ArticleReadyPersistenceService.load_snapshot、build_reader_plate_snapshot、
build_representation_payload/reader_events、PostgreSQL constraint（仅 R18）、
直接 DB 读取（守恒证明）。

DB 纪律：真实 PostgreSQL、per-test 随机 schema、finally DROP CASCADE。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import connection as db_connection
from app.database.connection import init_connection
from app.main import app
from app.services.reader_image_overrides import (
    ImageSourceOverrideError,
    StableImageSourceOverrideService,
)
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

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

# 五类 owning block（paragraph/heading/list_item/blockquote/table_cell）
# + standalone image + same-block 多图 + safe/unsafe source URL。
_G2D_MARKDOWN = (
    "# Alpha heading ![h](https://example.com/h.png)\n\n"
    "First paragraph with ![p0](https://example.com/p0.png) middle and "
    "![p1](https://example.com/p1.png) plus ![bad](javascript:alert(1)) tail.\n\n"
    "- list item with ![li](https://example.com/li.png)\n\n"
    "> quote line with ![bq](https://example.com/bq.png)\n\n"
    "![standalone](http://example.com/s.png)\n\n"
    "| figure | note |\n"
    "| --- | --- |\n"
    "| ![cell](https://example.com/c.png) | supporting note |\n"
)


def _session_info(user_id: UUID) -> object:
    return type(
        "SessionInfo",
        (),
        {
            "user_id": user_id,
            "session_id": uuid4(),
        },
    )()


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=6,
        init=_init_conn,
        setup=_setup_conn,
    )


@pytest.fixture
async def override_db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_g2d_override_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for override tests: {exc}")
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


async def _freeze_image_document(pool: asyncpg.Pool, user_id: UUID):
    service = StableReadyInputApplicationService(pool=pool)
    return await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="pasted_text",
        text=_G2D_MARKDOWN,
        language="en",
    )


def _walk_nodes(nodes) -> list:
    out = []
    for node in nodes:
        out.append(node)
        out.extend(_walk_nodes(node.children))
    return out


def _find_block(snapshot, *, block_type: str, text_prefix: str | None = None):
    matches = [
        node
        for node in _walk_nodes(snapshot.stable_document_tree)
        if node.block_type == block_type
        and (text_prefix is None or (node.text_content or "").startswith(text_prefix))
    ]
    assert len(matches) == 1, f"expected exactly one {block_type} block, got {len(matches)}"
    return matches[0]


async def _current_sequence(pool: asyncpg.Pool, record_id: UUID) -> int:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT next_sequence - 1 FROM reader_event_sequences WHERE reading_record_id = $1",
            record_id,
        )
    assert value is not None
    return int(value)


def _put_override(
    *,
    user_id: UUID,
    record_id: UUID,
    stable_document_id: UUID,
    block_id: str,
    url: str,
    inline_ordinal: int | None = None,
):
    body: dict[str, object] = {
        "stable_document_id": str(stable_document_id),
        "block_id": block_id,
        "url": url,
    }
    if inline_ordinal is not None:
        body["inline_ordinal"] = inline_ordinal
    return _request("PUT", user_id, record_id, body)


async def _request(
    method: str,
    user_id: UUID,
    record_id: UUID,
    body: dict[str, object] | None = None,
    *,
    path_suffix: str = "",
    params: dict[str, object] | None = None,
):
    """httpx ASGITransport 在测试事件循环内驱动 app（不跑 lifespan），
    与隔离 schema 的 asyncpg 池同循环，无跨 loop 冲突。"""
    with _mock_auth(user_id):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            request = client.build_request(
                method,
                f"/reader/records/{record_id}/image-source-overrides{path_suffix}",
                json=body,
                params=params,
                headers=AUTH_HEADERS,
            )
            return await client.send(request)


@pytest.fixture
def override_http_env(
    override_db_env: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> asyncpg.Pool:
    # Route 内无参构造 service/runtime 时解析全局池；指到本测试隔离 schema。
    monkeypatch.setattr(db_connection, "DB_POOL", override_db_env)
    return override_db_env


# ---------------------------------------------------------------------------
# Slice 2 · standalone PUT：持久化 + 错误矩阵 + 输入边界
# ---------------------------------------------------------------------------


async def test_put_standalone_override_persists_raw_and_advances_sequence(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    record_id = result.reading_record_id
    stable_document_id = result.stable_document_id
    image_block = _find_block(result.snapshot, block_type="image")
    before = await _current_sequence(pool, record_id)

    override = "https://cdn.example.com/replaced.png"
    response = await _put_override(
        user_id=user_id,
        record_id=record_id,
        stable_document_id=stable_document_id,
        block_id=image_block.block_id,
        url=override,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["last_event_sequence"] == before + 1

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT stable_document_id, block_id, inline_ordinal, override_url "
            "FROM stable_image_source_overrides"
        )
        event_row = await conn.fetchrow(
            "SELECT event_type, payload_json FROM reader_events "
            "WHERE reading_record_id = $1 ORDER BY sequence DESC LIMIT 1",
            record_id,
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["stable_document_id"] == stable_document_id
    assert row["block_id"] == image_block.block_id
    assert row["inline_ordinal"] is None
    assert row["override_url"] == override
    assert event_row is not None
    assert event_row["event_type"] == "projection_ops"
    event_payload = event_row["payload_json"]
    if isinstance(event_payload, str):
        event_payload = json.loads(event_payload)
    assert event_payload["representation_section"] == "image_overrides"
    assert event_payload["operation"] == "upsert"
    assert event_payload["target_keys"] == [f"{stable_document_id}:{image_block.block_id}:-"]
    assert override not in json.dumps(event_payload)


async def test_put_long_url_stored_verbatim(override_http_env: asyncpg.Pool) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")

    long_url = "https://example.com/" + "a" * 3000 + ".png"
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url=long_url,
    )
    assert response.status_code == 200, response.text

    async with pool.acquire() as conn:
        stored = await conn.fetchval("SELECT override_url FROM stable_image_source_overrides")
    assert stored == long_url


async def test_put_null_character_url_rejected_before_database(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    before = await _current_sequence(pool, result.reading_record_id)

    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url="https://example.com/a\u0000b.png",
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"][0]["type"] == "url_null_character_not_persistable"

    async with pool.acquire() as conn:
        row_count = await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")
    assert int(row_count) == 0
    # sequence 不变 ⇒ 零事件（计数器与最新事件一致性由 load 路径断言）。
    assert await _current_sequence(pool, result.reading_record_id) == before


@pytest.mark.parametrize(
    "body_mutation",
    [
        lambda body: body.update(url=None),
        lambda body: body.update(url="https://example.com/a.png", effective_url="x"),
        lambda body: body.update(url="https://example.com/a.png", source_url="x"),
        lambda body: body.update(url="https://example.com/a.png", unknown_field=1),
        lambda body: body.update(inline_ordinal=-1),
        lambda body: body.update(block_id=""),
    ],
    ids=[
        "null_url",
        "effective_url_field",
        "source_url_field",
        "unknown_field",
        "negative_ordinal",
        "empty_block_id",
    ],
)
async def test_put_schema_rejects_invalid_bodies(
    override_http_env: asyncpg.Pool, body_mutation
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    body: dict[str, object] = {
        "stable_document_id": str(result.stable_document_id),
        "block_id": image_block.block_id,
        "url": "https://example.com/a.png",
    }
    body_mutation(body)
    response = await _request(
        "PUT",
        user_id,
        result.reading_record_id,
        body,
    )
    assert response.status_code == 422, response.text
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0


async def test_put_record_ownership_collapsed_404(override_http_env: asyncpg.Pool) -> None:
    pool = override_http_env
    owner_id = await _insert_user(pool)
    intruder_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, owner_id)
    image_block = _find_block(result.snapshot, block_type="image")

    # 非本人 record → collapsed 404（与非存在同形，不泄露存在性）。
    response = await _put_override(
        user_id=intruder_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 404

    # 不存在的 record → 同形 404。
    response = await _put_override(
        user_id=owner_id,
        record_id=uuid4(),
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 404


async def test_put_document_not_belonging_to_record_collapsed_404(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    owner_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, owner_id)
    other_result = await _freeze_image_document(pool, other_user_id)
    image_block = _find_block(result.snapshot, block_type="image")

    # 随机文档 → 404 collapse。
    response = await _put_override(
        user_id=owner_id,
        record_id=result.reading_record_id,
        stable_document_id=uuid4(),
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 404

    # 他人文档指向自己 record → 404 collapse（跨用户存在性不泄露）。
    response = await _put_override(
        user_id=owner_id,
        record_id=result.reading_record_id,
        stable_document_id=other_result.stable_document_id,
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 404


async def test_put_unknown_block_returns_image_block_not_found_404(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id="b9999",
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "image_block_not_found"


async def test_put_non_image_standalone_target_returns_422(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=paragraph.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "image_target_not_found"


async def test_composite_fk_rejects_mismatched_document_block(
    override_db_env: asyncpg.Pool,
) -> None:
    pool = override_db_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)

    # R18：错配 (stable_document_id, block_id) 组合（该文档中不存在的
    # block）被复合 FK 拒绝；API 路径先行 404，此处证明约束级兜底。
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO stable_image_source_overrides "
                "(stable_document_id, block_id, override_url) "
                "VALUES ($1, $2, $3)",
                result.stable_document_id,
                "b9999",
                "https://cdn.example.com/x.png",
            )


# ---------------------------------------------------------------------------
# Slice 3 · inline locator + 双 partial-index upsert
# ---------------------------------------------------------------------------

_INLINE_OWNING_CASES = [
    ("heading", "Alpha heading"),
    ("paragraph", "First paragraph"),
    ("list_item", "list item with"),
    ("blockquote", "quote line with"),
    ("table_cell", None),
]


@pytest.mark.parametrize(
    ("block_type", "text_prefix"),
    _INLINE_OWNING_CASES,
    ids=[case for case, _ in _INLINE_OWNING_CASES],
)
async def test_put_inline_locator_hits_each_owning_block_type(
    override_http_env: asyncpg.Pool, block_type: str, text_prefix: str | None
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    if block_type == "table_cell":
        cells = [
            node
            for node in _walk_nodes(result.snapshot.stable_document_tree)
            if node.block_type == "table_cell"
            and isinstance(node.payload.get("inline_images"), list)
            and node.payload["inline_images"]
        ]
        assert len(cells) == 1
        owner = cells[0]
    else:
        owner = _find_block(result.snapshot, block_type=block_type, text_prefix=text_prefix)

    override = "https://cdn.example.com/inline.png"
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=owner.block_id,
        url=override,
        inline_ordinal=0,
    )
    assert response.status_code == 200, response.text

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT block_id, inline_ordinal, override_url FROM stable_image_source_overrides"
        )
    assert len(rows) == 1
    assert rows[0]["block_id"] == owner.block_id
    assert rows[0]["inline_ordinal"] == 0
    assert rows[0]["override_url"] == override


async def test_put_inline_ordinal_out_of_range_returns_422(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")
    # 该 paragraph 恰有 3 张 inline image；ordinal=3 越界。
    assert len(paragraph.payload["inline_images"]) == 3
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=paragraph.block_id,
        url="https://cdn.example.com/x.png",
        inline_ordinal=3,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "image_target_not_found"


async def test_put_inline_on_block_without_inline_images_returns_422(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
        inline_ordinal=0,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "image_target_not_found"


async def test_same_block_multi_image_ordinals_do_not_interfere(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    override0 = "https://cdn.example.com/only-0.png"
    override1 = "https://cdn.example.com/only-1.png"
    for ordinal, override in ((0, override0), (1, override1)):
        response = await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=paragraph.block_id,
            url=override,
            inline_ordinal=ordinal,
        )
        assert response.status_code == 200, response.text

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT inline_ordinal, override_url FROM stable_image_source_overrides "
            "ORDER BY inline_ordinal ASC"
        )
    assert [(row["inline_ordinal"], row["override_url"]) for row in rows] == [
        (0, override0),
        (1, override1),
    ]


async def test_repeated_put_is_idempotent_single_row(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    for url in (
        "https://cdn.example.com/v1.png",
        "https://cdn.example.com/v2.png",
    ):
        response = await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
            url=url,
        )
        assert response.status_code == 200, response.text
    for url in (
        "https://cdn.example.com/i-v1.png",
        "https://cdn.example.com/i-v2.png",
    ):
        response = await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=paragraph.block_id,
            url=url,
            inline_ordinal=0,
        )
        assert response.status_code == 200, response.text

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT block_id, inline_ordinal, override_url, created_at, updated_at "
            "FROM stable_image_source_overrides ORDER BY block_id ASC"
        )
    # standalone 重复 PUT 行数保持 1；inline ordinal 0 幂等；LWW 取最新值。
    assert len(rows) == 2
    by_locator = {(row["block_id"], row["inline_ordinal"]): row for row in rows}
    assert by_locator[(image_block.block_id, None)]["override_url"] == (
        "https://cdn.example.com/v2.png"
    )
    assert by_locator[(paragraph.block_id, 0)]["override_url"] == (
        "https://cdn.example.com/i-v2.png"
    )
    for row in rows:
        assert row["updated_at"] >= row["created_at"]


async def test_standalone_and_inline_rows_coexist_for_same_block(
    override_db_env: asyncpg.Pool,
) -> None:
    pool = override_db_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    # R19 约束级证明：两个 partial unique index 互不干扰，同一 (doc, block)
    # 的 standalone 行与 inline 行可共存（直接 SQL，seam 6）。
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO stable_image_source_overrides "
            "(stable_document_id, block_id, inline_ordinal, override_url) "
            "VALUES ($1, $2, NULL, $3)",
            result.stable_document_id,
            paragraph.block_id,
            "https://cdn.example.com/standalone.png",
        )
        await conn.execute(
            "INSERT INTO stable_image_source_overrides "
            "(stable_document_id, block_id, inline_ordinal, override_url) "
            "VALUES ($1, $2, 0, $3)",
            result.stable_document_id,
            paragraph.block_id,
            "https://cdn.example.com/inline.png",
        )
        row_count = await conn.fetchval(
            "SELECT count(*) FROM stable_image_source_overrides "
            "WHERE stable_document_id = $1 AND block_id = $2",
            result.stable_document_id,
            paragraph.block_id,
        )
    assert int(row_count) == 2


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://example.com/a%20b.png",
        "http://example.com/%5C@evil.com/a.png",
        "https://example.com/a\tb.png",
        "https://example.com/a\u0001b.png",
        "  https://example.com/padded.png  ",
        "javascript:alert(1)",
    ],
    ids=["percent_20", "percent_5C", "tab_control", "u0001_control", "padded", "javascript"],
)
async def test_raw_override_persisted_verbatim(
    override_http_env: asyncpg.Pool, raw_url: str
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url=raw_url,
    )
    assert response.status_code == 200, response.text
    async with pool.acquire() as conn:
        stored = await conn.fetchval("SELECT override_url FROM stable_image_source_overrides")
    # 存储层零改写：不 trim、不解码、不校验 scheme（U+0000 除外）。
    assert stored == raw_url


# ---------------------------------------------------------------------------
# Slice 4 · DELETE（幂等、无行不发布事件、恢复 source 判定）
# ---------------------------------------------------------------------------


def _delete_override(
    *,
    user_id: UUID,
    record_id: UUID,
    stable_document_id: UUID,
    block_id: str,
    inline_ordinal: int | None = None,
):
    params: dict[str, object] | None = (
        {"inline_ordinal": inline_ordinal} if inline_ordinal is not None else None
    )
    return _request(
        "DELETE",
        user_id,
        record_id,
        path_suffix=f"/{stable_document_id}/{block_id}",
        params=params,
    )


async def _put_then_delete(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    record_id: UUID,
    stable_document_id: UUID,
    block_id: str,
    inline_ordinal: int | None = None,
):
    put = await _put_override(
        user_id=user_id,
        record_id=record_id,
        stable_document_id=stable_document_id,
        block_id=block_id,
        url="https://cdn.example.com/to-delete.png",
        inline_ordinal=inline_ordinal,
    )
    assert put.status_code == 200, put.text
    return await _delete_override(
        user_id=user_id,
        record_id=record_id,
        stable_document_id=stable_document_id,
        block_id=block_id,
        inline_ordinal=inline_ordinal,
    )


async def test_delete_removes_row_and_publishes_delete_event(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    before = await _current_sequence(pool, result.reading_record_id)

    response = await _put_then_delete(
        pool,
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["last_event_sequence"] == before + 2

    async with pool.acquire() as conn:
        row_count = await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")
        event_row = await conn.fetchrow(
            "SELECT event_type, payload_json FROM reader_events "
            "WHERE reading_record_id = $1 ORDER BY sequence DESC LIMIT 1",
            result.reading_record_id,
        )
    assert int(row_count) == 0
    assert event_row is not None
    assert event_row["event_type"] == "projection_ops"
    event_payload = event_row["payload_json"]
    if isinstance(event_payload, str):
        event_payload = json.loads(event_payload)
    assert event_payload["representation_section"] == "image_overrides"
    assert event_payload["operation"] == "delete"
    assert event_payload["target_keys"] == [f"{result.stable_document_id}:{image_block.block_id}:-"]


async def test_delete_inline_locator_query_param(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    response = await _put_then_delete(
        pool,
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=paragraph.block_id,
        inline_ordinal=1,
    )
    assert response.status_code == 200, response.text
    async with pool.acquire() as conn:
        row_count = await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")
    assert int(row_count) == 0


async def test_delete_without_row_is_idempotent_no_event(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    before = await _current_sequence(pool, result.reading_record_id)
    async with pool.acquire() as conn:
        event_count_before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            result.reading_record_id,
        )

    # 无行 DELETE：仍 200、返回当前 last_event_sequence、不发布事件、
    # sequence 不变（行为冻结，本测试锁定）。
    for _ in range(2):
        response = await _delete_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
        )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert response.json()["last_event_sequence"] == before

    async with pool.acquire() as conn:
        event_count_after = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            result.reading_record_id,
        )
    # 无行 DELETE 未追加任何事件；计数器与最新事件保持一致。
    assert event_count_after == event_count_before
    assert await _current_sequence(pool, result.reading_record_id) == before


async def test_delete_ownership_and_target_errors_match_put(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    owner_id = await _insert_user(pool)
    intruder_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, owner_id)
    image_block = _find_block(result.snapshot, block_type="image")
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    # 越权 → collapsed 404。
    response = await _delete_override(
        user_id=intruder_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
    )
    assert response.status_code == 404

    # 未知 block → 404 image_block_not_found。
    response = await _delete_override(
        user_id=owner_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id="b9999",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "image_block_not_found"

    # 非图片目标 → 422 image_target_not_found（与 PUT 同源）。
    response = await _delete_override(
        user_id=owner_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=paragraph.block_id,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "image_target_not_found"

    # standalone locator 缺省：对 paragraph 传 inline_ordinal 越界 → 422。
    response = await _delete_override(
        user_id=owner_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=paragraph.block_id,
        inline_ordinal=99,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Slice 6 · repository reload overlay + stable_document_id（O-D1-A）
# ---------------------------------------------------------------------------


def _project_nodes(snapshot) -> list[dict[str, object]]:
    def project(node) -> dict[str, object]:
        return {
            "block_id": node.block_id,
            "block_type": node.block_type,
            "order_index": node.order_index,
            "text_content": node.text_content,
            "payload": node.payload,
            "children": [project(child) for child in node.children],
        }

    return [project(node) for node in _walk_nodes(snapshot.stable_document_tree)]


async def test_reload_snapshot_projects_overrides_and_exposes_stable_document_id(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    record_id = result.reading_record_id
    stable_document_id = result.stable_document_id
    image_block = _find_block(result.snapshot, block_type="image")
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    safe_override = "https://cdn.example.com/replaced.png"
    invalid_override = "javascript:alert(66)"
    assert (
        await _put_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=image_block.block_id,
            url=safe_override,
        )
    ).status_code == 200
    assert (
        await _put_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=paragraph.block_id,
            url=invalid_override,
            inline_ordinal=1,
        )
    ).status_code == 200

    loader = ArticleReadyPersistenceService(pool=pool)
    reloaded = await loader.load_snapshot(record_id=record_id, user_id=user_id)

    # Reader snapshot base 暴露 active stable document id。
    assert reloaded.base.stable_document_id == str(stable_document_id)

    nodes = {n.block_id: n for n in _walk_nodes(reloaded.stable_document_tree)}

    # standalone：source_url 原样、override_url raw 可见、effective 从 override 派生。
    standalone = nodes[image_block.block_id]
    assert standalone.payload["source_url"] == "http://example.com/s.png"
    assert standalone.payload["override_url"] == safe_override
    assert standalone.payload["effective_url"] == safe_override

    # inline：命中项 raw 原样 + effective=null（非法 override 不回退 source）；
    # 同 block 未命中项不变；无 override 的 heading 无 override_url 键。
    para_items = nodes[paragraph.block_id].payload["inline_images"]
    assert "override_url" not in para_items[0]
    assert para_items[0]["effective_url"] == "https://example.com/p0.png"
    assert para_items[1]["override_url"] == invalid_override
    assert para_items[1]["effective_url"] is None
    assert para_items[1]["source_url"] == "https://example.com/p1.png"
    assert "override_url" not in para_items[2]
    assert para_items[2]["effective_url"] is None  # unsafe source，G2a-B 行为

    heading = _find_block(reloaded, block_type="heading")
    assert "override_url" not in heading.payload["inline_images"][0]
    assert heading.payload["inline_images"][0]["effective_url"] == ("https://example.com/h.png")

    # snapshot.value 不携带 override_url / image 节点。
    value_json = json.dumps(reloaded.value)
    assert "override_url" not in value_json
    assert "inline_images" not in value_json


async def test_delete_restores_source_derivation_after_reload(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    record_id = result.reading_record_id
    stable_document_id = result.stable_document_id
    image_block = _find_block(result.snapshot, block_type="image")
    paragraph = _find_block(result.snapshot, block_type="paragraph", text_prefix="First paragraph")

    # safe source：override 后 DELETE → 回归 S1（effective=source）。
    assert (
        await _put_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=image_block.block_id,
            url="https://cdn.example.com/x.png",
        )
    ).status_code == 200
    assert (
        await _delete_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=image_block.block_id,
        )
    ).status_code == 200

    # unsafe source（javascript:）：override 后 DELETE → 回归 S2（effective=null）。
    assert (
        await _put_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=paragraph.block_id,
            url="https://cdn.example.com/y.png",
            inline_ordinal=2,
        )
    ).status_code == 200
    assert (
        await _delete_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=paragraph.block_id,
            inline_ordinal=2,
        )
    ).status_code == 200

    loader = ArticleReadyPersistenceService(pool=pool)
    reloaded = await loader.load_snapshot(record_id=record_id, user_id=user_id)
    nodes = {n.block_id: n for n in _walk_nodes(reloaded.stable_document_tree)}

    standalone = nodes[image_block.block_id]
    assert "override_url" not in standalone.payload
    assert standalone.payload["effective_url"] == "http://example.com/s.png"

    bad_item = nodes[paragraph.block_id].payload["inline_images"][2]
    assert "override_url" not in bad_item
    assert bad_item["effective_url"] is None


async def test_reload_is_stable_across_repeated_snapshots_with_overrides(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    assert (
        await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
            url="https://cdn.example.com/x.png",
        )
    ).status_code == 200

    loader = ArticleReadyPersistenceService(pool=pool)
    first = await loader.load_snapshot(record_id=result.reading_record_id, user_id=user_id)
    second = await loader.load_snapshot(record_id=result.reading_record_id, user_id=user_id)
    assert _project_nodes(first) == _project_nodes(second)
    assert json.dumps(first.value, sort_keys=True) == json.dumps(second.value, sort_keys=True)
    # fresh freeze 路径与带 override 的 reload：fresh 不含 override 键
    # （新 doc 无行），结构字段逐字段一致。
    fresh_nodes = {n.block_id: n for n in _walk_nodes(result.snapshot.stable_document_tree)}
    assert "override_url" not in fresh_nodes[image_block.block_id].payload
    assert fresh_nodes[image_block.block_id].payload["source_url"] == ("http://example.com/s.png")


async def test_stable_truth_invariants_unchanged_by_override_writes(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    record_id = result.reading_record_id
    stable_document_id = result.stable_document_id

    async def _capture_truth() -> dict[str, object]:
        async with pool.acquire() as conn:
            blocks = await conn.fetch(
                "SELECT block_id, payload_json, text_content "
                "FROM stable_document_blocks WHERE stable_document_id = $1 "
                "ORDER BY order_index ASC",
                stable_document_id,
            )
            doc_row = await conn.fetchrow(
                "SELECT content_sha256 FROM stable_reading_documents WHERE id = $1",
                stable_document_id,
            )
            base_row = await conn.fetchrow(
                "SELECT content_sha256, text FROM reading_bases "
                "WHERE reading_record_id = $1 AND status = 'active'",
                record_id,
            )
            unit_rows = await conn.fetch(
                "SELECT unit_id, order_index, text_hash FROM reading_units "
                "WHERE reading_record_id = $1 ORDER BY order_index ASC",
                record_id,
            )
        assert doc_row is not None and base_row is not None
        return {
            "blocks": [
                (
                    row["block_id"],
                    json.dumps(row["payload_json"], sort_keys=True),
                    row["text_content"],
                )
                for row in blocks
            ],
            "document_sha": doc_row["content_sha256"],
            "base_sha": base_row["content_sha256"],
            "base_text": base_row["text"],
            "units": [(row["unit_id"], row["order_index"], row["text_hash"]) for row in unit_rows],
        }

    before = await _capture_truth()

    image_block = _find_block(result.snapshot, block_type="image")
    assert (
        await _put_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=image_block.block_id,
            url="https://cdn.example.com/x.png",
        )
    ).status_code == 200
    assert (
        await _delete_override(
            user_id=user_id,
            record_id=record_id,
            stable_document_id=stable_document_id,
            block_id=image_block.block_id,
        )
    ).status_code == 200

    # Stable payload / content_sha256 / canonical text / units 逐字节不变。
    assert await _capture_truth() == before

    # override 表无 effective_url / source_url 派生列（恰 7 列）。
    async with pool.acquire() as conn:
        columns = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'stable_image_source_overrides'"
        )
    assert sorted(row["column_name"] for row in columns) == [
        "block_id",
        "created_at",
        "id",
        "inline_ordinal",
        "override_url",
        "stable_document_id",
        "updated_at",
    ]


async def test_superseded_document_rejects_put_with_409(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")

    # R24a（setup-only 状态迁移）：L2 confirmed-source 链在冻结后返回
    # source_frozen 409，无法从产品链到达 gen-2 supersede；此处直接翻转到
    # schema 定义的 superseded 态验证 409 映射。
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE stable_reading_documents SET status = 'superseded' WHERE id = $1",
            result.stable_document_id,
        )
    response = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url="https://cdn.example.com/x.png",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "stable_document_not_active"

    response = await _delete_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
    )
    assert response.status_code == 409


async def test_overrides_bound_to_other_document_never_leak_into_snapshot(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    owner_id = await _insert_user(pool)
    other_user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, owner_id)
    other_result = await _freeze_image_document(pool, other_user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    other_block = _find_block(other_result.snapshot, block_type="image")

    # R24b/R25 隔离属性：override 行按 stable_document_id 绑定；属于另一文档
    # （supersede 后旧文档的同构情形：doc id 不同）的行绝不进入本快照。
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO stable_image_source_overrides "
            "(stable_document_id, block_id, override_url) VALUES ($1, $2, $3)",
            other_result.stable_document_id,
            other_block.block_id,
            "https://cdn.example.com/leak-attempt.png",
        )

    loader = ArticleReadyPersistenceService(pool=pool)
    reloaded = await loader.load_snapshot(record_id=result.reading_record_id, user_id=owner_id)
    nodes = {n.block_id: n for n in _walk_nodes(reloaded.stable_document_tree)}
    assert "override_url" not in nodes[image_block.block_id].payload
    assert nodes[image_block.block_id].payload["effective_url"] == ("http://example.com/s.png")


# ---------------------------------------------------------------------------
# 回归锁 · Finding #1 / #2：机器可读 422 type 与 active fence 接线
# ---------------------------------------------------------------------------


async def test_put_block_id_nul_returns_422_machine_type_via_http(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    # block_id NUL 通过 Pydantic before-validator 拒绝，detail.type 为机器码
    body = {
        "stable_document_id": str(result.stable_document_id),
        "block_id": "b\x00x",
        "url": "https://example.com/a.png",
    }
    raw = json.dumps(body, ensure_ascii=True).encode()
    with _mock_auth(user_id):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.request(
                "PUT",
                f"/reader/records/{result.reading_record_id}/image-source-overrides",
                content=raw,
                headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["detail"][0]["type"] == "block_id_null_character_not_persistable"
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0


async def test_put_url_surrogate_returns_422_machine_code_via_http(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    body = {
        "stable_document_id": str(result.stable_document_id),
        "block_id": image_block.block_id,
        "url": "\ud800",
    }
    raw = json.dumps(body, ensure_ascii=True).encode()
    with _mock_auth(user_id):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.request(
                "PUT",
                f"/reader/records/{result.reading_record_id}/image-source-overrides",
                content=raw,
                headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            )
    assert resp.status_code == 422
    # 统一由 service 层以 {code} 信封返回（Pydantic 层已不再直接拒绝 surrogate）
    assert resp.json()["code"] == "url_not_representable_as_postgres_text"
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0


async def test_put_block_id_surrogate_returns_422_machine_code_via_http(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    before_seq = await _current_sequence(pool, result.reading_record_id)
    body = {
        "stable_document_id": str(result.stable_document_id),
        "block_id": "b\ud800x",
        "url": "https://example.com/a.png",
    }
    raw = json.dumps(body, ensure_ascii=True).encode()
    with _mock_auth(user_id):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.request(
                "PUT",
                f"/reader/records/{result.reading_record_id}/image-source-overrides",
                content=raw,
                headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            )
    assert resp.status_code == 422
    assert resp.json()["code"] == "block_id_not_representable_as_postgres_text"
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0
        assert await _current_sequence(pool, result.reading_record_id) == before_seq
        assert (
            int(
                await conn.fetchval(
                    "SELECT count(*) FROM reader_events WHERE reading_record_id=$1",
                    result.reading_record_id,
                )
            )
            == 1
        )


async def test_put_literal_marker_url_persists_verbatim(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    literal_url = "__URL_SURROGATE__"
    resp = await _put_override(
        user_id=user_id,
        record_id=result.reading_record_id,
        stable_document_id=result.stable_document_id,
        block_id=image_block.block_id,
        url=literal_url,
    )
    assert resp.status_code == 200
    async with pool.acquire() as conn:
        stored = await conn.fetchval("SELECT override_url FROM stable_image_source_overrides")
        assert stored == literal_url
    # reload 驗證投影原樣
    loader = ArticleReadyPersistenceService(pool=pool)
    reloaded = await loader.load_snapshot(record_id=result.reading_record_id, user_id=user_id)
    node = next(
        n for n in _walk_nodes(reloaded.stable_document_tree) if n.block_id == image_block.block_id
    )
    assert node.payload["override_url"] == literal_url


def test_schema_preserves_literal_marker_block_id() -> None:
    from app.schemas.reader_image_overrides import ImageSourceOverrideUpsertRequest

    req = ImageSourceOverrideUpsertRequest(
        stable_document_id=uuid4(),
        block_id="__BLOCK_ID_SURROGATE__",
        url="https://example.com/a.png",
    )
    assert req.block_id == "__BLOCK_ID_SURROGATE__"


async def test_delete_block_id_not_representable_returns_422_machine_code(
    override_db_env: asyncpg.Pool,
) -> None:
    # httpx 无法在 URL path 中直接编码 lone surrogate（client 端即 500），
    # 此处以 service 直调锁定机器码，HTTP 路径的同码由 service 层同函数保证
    pool = override_db_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    svc = StableImageSourceOverrideService(pool=pool)
    with pytest.raises(ImageSourceOverrideError) as exc_info:
        await svc.delete_override(
            record_id=result.reading_record_id,
            user_id=user_id,
            stable_document_id=result.stable_document_id,
            block_id="b\ud800x",
            inline_ordinal=None,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "block_id_not_representable_as_postgres_text"
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0
        before = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id=$1",
            result.reading_record_id,
        )
        assert int(before) == 1  # 仅 freeze，无新增事件


async def test_upsert_active_fence_false_yields_409_zero_mutation(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    before_seq = await _current_sequence(pool, result.reading_record_id)
    # 模拟并发：fence 判定 stale → 必须 409 且零行零事件
    with patch(
        "app.services.reader_image_overrides.ReaderEventRuntime.is_active_fence",
        new=AsyncMock(return_value=False),
    ):
        resp = await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
            url="https://cdn.example.com/should-not-persist.png",
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "stable_document_not_active"
    async with pool.acquire() as conn:
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 0
        assert await _current_sequence(pool, result.reading_record_id) == before_seq
        assert (
            int(
                await conn.fetchval(
                    "SELECT count(*) FROM reader_events WHERE reading_record_id=$1",
                    result.reading_record_id,
                )
            )
            == 1
        )  # 仅初始 freeze 产生的事件


async def test_delete_active_fence_false_yields_409_zero_mutation(
    override_http_env: asyncpg.Pool,
) -> None:
    pool = override_http_env
    user_id = await _insert_user(pool)
    result = await _freeze_image_document(pool, user_id)
    image_block = _find_block(result.snapshot, block_type="image")
    # 先写入一行，备用删除
    assert (
        await _put_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
            url="https://cdn.example.com/to-keep.png",
        )
    ).status_code == 200
    before_seq = await _current_sequence(pool, result.reading_record_id)
    with patch(
        "app.services.reader_image_overrides.ReaderEventRuntime.is_active_fence",
        new=AsyncMock(return_value=False),
    ):
        resp = await _delete_override(
            user_id=user_id,
            record_id=result.reading_record_id,
            stable_document_id=result.stable_document_id,
            block_id=image_block.block_id,
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "stable_document_not_active"
    async with pool.acquire() as conn:
        # 行未被删除，事件未新增
        assert int(await conn.fetchval("SELECT count(*) FROM stable_image_source_overrides")) == 1
        assert await _current_sequence(pool, result.reading_record_id) == before_seq
