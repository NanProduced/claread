"""Regression: reloaded stable tree must preserve document order.

The reload SELECT previously dropped ``b.order_index``, so every
reloaded tree node fell back to ``0`` and child sorting degraded to
lexicographic block-id order (``b10`` < ``b9``), visibly swapping list
items in any document with ten or more blocks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)
from app.services.reader_orchestration.snapshot import (
    _build_stable_document_tree,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.reader_orchestration_test_support import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.asyncio

# Ten blocks before the ordered list so list items land on multi-digit
# block ids (b10+), which lexicographic fallback would misorder.
ORDER_DOC_MD = """# Doc

Intro paragraph one explains how community reading rooms grew over time.

Intro paragraph two describes volunteers keeping small libraries open.

Intro paragraph three covers reading clubs meeting every weekend.

Intro paragraph four mentions archives collecting local history.

Intro paragraph five reviews funding from neighbourhood fundraisers.

Intro paragraph six closes the overview with future plans.

1. First ordered item alpha.
2. Second ordered item beta.
3. Third ordered item gamma.
"""


@pytest.fixture
async def tree_order_db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_tree_order_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )
    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_reloaded_tree_keeps_list_items_in_document_order(
    tree_order_db_env: asyncpg.Pool,
) -> None:
    pool = tree_order_db_env
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="order-doc.md",
        text=ORDER_DOC_MD,
        language="en",
    )

    repo = ReaderOrchestrationRepository(pool=pool)
    async with pool.acquire() as conn:
        facts = await repo.load_snapshot_facts(
            conn,
            record_id=result.reading_record_id,
            user_id=user_id,
        )
    # Reload must carry the real order_index on every block row; the
    # zero-filled fallback is what caused the lexicographic mis-sort.
    reload_order_indexes = [
        row["order_index"] for row in facts.build_result.stable_document_blocks
    ]
    assert all(isinstance(value, int) for value in reload_order_indexes)
    assert sorted(reload_order_indexes) == list(range(len(reload_order_indexes)))

    tree = _build_stable_document_tree(facts.build_result)
    ordered_lists = [
        node
        for node in tree
        if node.block_type == "list" and node.payload.get("ordered") is True
    ]
    assert len(ordered_lists) == 1
    texts = [
        (child.text_content or "") for child in ordered_lists[0].children
    ]
    assert texts == [
        "First ordered item alpha.",
        "Second ordered item beta.",
        "Third ordered item gamma.",
    ]
