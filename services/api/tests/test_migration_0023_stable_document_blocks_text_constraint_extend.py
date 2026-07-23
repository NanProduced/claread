"""Migration test for 0023_stable_document_blocks_text_constraint_extend.sql.

Verifies that two CHECK constraints on ``stable_document_blocks`` were
extended to include ``list`` and ``thematic_break`` block types:

1. ``stable_document_blocks_block_type_check`` — the block_type allow
   list (migration 0004 omitted ``list`` and ``thematic_break``; M1
   added them to StableDocumentBlockType but not to the DB constraint).
2. ``ck_stable_document_blocks_text_for_textual_types`` — the
   text_content exemption list for structural block types that may
   carry NULL text_content (markdown-it-py's list wrapper and
   thematic break tokens have no text content).

Background: migration 0004 defined both constraints. M1 (Markdown
Structured Source Contract) added ``list`` and ``thematic_break`` to
``StableDocumentBlockType`` and ``_STRUCTURAL_BLOCK_TYPES`` in
app/schemas/reader_documents.py but did not sync the DB constraints.
The gap was hidden until breakpoint 2 (pasted_text/txt_file upgrade
routing) made the markdown parser path reachable for pasted text, at
which point both constraints violated in sequence: first the
block_type allow list rejected ``list``, then (after that was fixed)
the text_content exemption list rejected NULL text_content.

The test connects to the main (public schema) database and SKIPS if
migration 0023 has not been applied yet. This mirrors the pattern in
test_migration_0022_ai_usage_events_invocation_key.py: the test does
not apply the migration itself, it only verifies the post-apply state.

See infra/migrations/0023_stable_document_blocks_text_constraint_extend.sql.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import asyncpg
import pytest

from tests.test_reader_orchestration_schema_baseline import DATABASE_URL

pytestmark = pytest.mark.anyio

_BLOCK_TYPE_CONSTRAINT = "stable_document_blocks_block_type_check"
_TEXT_CONTENT_CONSTRAINT = "ck_stable_document_blocks_text_for_textual_types"

_SKIP_REASON = (
    "Migration 0023 not applied: stable_document_blocks constraints do not "
    "include list/thematic_break in both block_type allow list and "
    "text_content exemption list. Run migration 0023 to enable this test."
)


async def _constraint_definition(
    conn: asyncpg.Connection,
    constraint_name: str,
) -> str | None:
    return cast(
        "str | None",
        await conn.fetchval(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = $1
            LIMIT 1
            """,
            constraint_name,
        ),
    )


async def _migration_0023_applied(conn: asyncpg.Connection) -> bool:
    """Return True if both constraints include list + thematic_break."""
    block_type_def = await _constraint_definition(conn, _BLOCK_TYPE_CONSTRAINT)
    text_content_def = await _constraint_definition(conn, _TEXT_CONTENT_CONSTRAINT)
    if block_type_def is None or text_content_def is None:
        return False
    block_type_lower = block_type_def.lower()
    text_content_lower = text_content_def.lower()
    return (
        "'list'" in block_type_lower
        and "'thematic_break'" in block_type_lower
        and "'list'" in text_content_lower
        and "'thematic_break'" in text_content_lower
    )


async def _skip_if_migration_0023_not_applied(conn: asyncpg.Connection) -> None:
    if not await _migration_0023_applied(conn):
        pytest.skip(_SKIP_REASON)


@pytest.fixture
async def main_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()


async def _insert_user(conn: asyncpg.Connection) -> UUID:
    return cast(UUID, await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id"))


async def _insert_reading_record(
    conn: asyncpg.Connection,
    user_id: UUID,
) -> UUID:
    return cast(
        UUID,
        await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title, language, generation)
            VALUES ($1, 'pasted_text', 'Migration 0023 Test', 'en', 1)
            RETURNING id
            """,
            user_id,
        ),
    )


async def _insert_stable_document(
    conn: asyncpg.Connection,
    reading_record_id: UUID,
) -> UUID:
    return cast(
        UUID,
        await conn.fetchval(
            """
            INSERT INTO stable_reading_documents (reading_record_id, record_generation)
            VALUES ($1, 1)
            RETURNING id
            """,
            reading_record_id,
        ),
    )


async def _insert_block(
    conn: asyncpg.Connection,
    *,
    stable_document_id: UUID,
    block_id: str,
    order_index: int,
    block_type: str,
    text_content: str | None,
    payload_json: str = "{}",
    source_refs_json: str = "{}",
) -> None:
    await conn.execute(
        """
        INSERT INTO stable_document_blocks (
            stable_document_id, block_id, order_index, block_type,
            text_content, payload_json, source_refs_json
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
        """,
        stable_document_id,
        block_id,
        order_index,
        block_type,
        text_content,
        payload_json,
        source_refs_json,
    )


async def _cleanup(
    conn: asyncpg.Connection,
    *,
    stable_document_id: UUID,
    reading_record_id: UUID,
    user_id: UUID,
) -> None:
    await conn.execute(
        "DELETE FROM stable_document_blocks WHERE stable_document_id = $1",
        stable_document_id,
    )
    await conn.execute(
        "DELETE FROM stable_reading_documents WHERE id = $1",
        stable_document_id,
    )
    await conn.execute(
        "DELETE FROM reading_records WHERE id = $1",
        reading_record_id,
    )
    await conn.execute("DELETE FROM users WHERE id = $1", user_id)


# --- Test 1a: block_type allow list includes list + thematic_break ------------


async def test_block_type_allow_list_includes_list_and_thematic_break(
    main_conn: asyncpg.Connection,
) -> None:
    """Migration 0023 must extend the block_type allow list with list + thematic_break."""
    await _skip_if_migration_0023_not_applied(main_conn)

    definition = await _constraint_definition(main_conn, _BLOCK_TYPE_CONSTRAINT)
    assert definition is not None, (
        f"constraint {_BLOCK_TYPE_CONSTRAINT} must exist"
    )
    definition_lower = definition.lower()
    assert "'list'" in definition_lower, (
        f"block_type allow list must include 'list'; got: {definition}"
    )
    assert "'thematic_break'" in definition_lower, (
        f"block_type allow list must include 'thematic_break'; got: {definition}"
    )
    # Sanity: the original allow-list entries must still be present.
    for original in (
        "'paragraph'",
        "'heading'",
        "'list_item'",
        "'code_block'",
        "'unknown'",
    ):
        assert original in definition_lower, (
            f"block_type allow list must still include {original}; got: {definition}"
        )


# --- Test 1b: text_content exemption list includes list + thematic_break ------


async def test_text_content_exemption_list_includes_list_and_thematic_break(
    main_conn: asyncpg.Connection,
) -> None:
    """Migration 0023 must extend the text_content exemption list with list + thematic_break."""
    await _skip_if_migration_0023_not_applied(main_conn)

    definition = await _constraint_definition(main_conn, _TEXT_CONTENT_CONSTRAINT)
    assert definition is not None, (
        f"constraint {_TEXT_CONTENT_CONSTRAINT} must exist"
    )
    definition_lower = definition.lower()
    assert "'list'" in definition_lower, (
        f"text_content exemption list must include 'list'; got: {definition}"
    )
    assert "'thematic_break'" in definition_lower, (
        f"text_content exemption list must include 'thematic_break'; got: {definition}"
    )
    # Sanity: the original exemptions must still be present.
    for original in ("'table'", "'code_block'", "'unknown'"):
        assert original in definition_lower, (
            f"text_content exemption list must still include {original}; got: {definition}"
        )


# --- Test 2: list wrapper block with NULL text_content is accepted -----------


async def test_list_wrapper_block_with_null_text_content_is_accepted(
    main_conn: asyncpg.Connection,
) -> None:
    """A ``list`` block with text_content=NULL must satisfy the constraint.

    This is the regression that caused stable-ready persistence to fail
    before migration 0023: markdown-it-py's ordered_list_open /
    bullet_list_open tokens carry no text content, so the normalizer
    emits text_content=None for the list wrapper block.
    """
    await _skip_if_migration_0023_not_applied(main_conn)

    user_id = await _insert_user(main_conn)
    reading_record_id = await _insert_reading_record(main_conn, user_id)
    stable_document_id = await _insert_stable_document(main_conn, reading_record_id)
    try:
        # This insert must succeed (no CheckViolationError).
        await _insert_block(
            main_conn,
            stable_document_id=stable_document_id,
            block_id="b1",
            order_index=0,
            block_type="list",
            text_content=None,
            payload_json='{"ordered": true, "depth": 0}',
            source_refs_json='{"source_type": "pasted_text", "line_start": 1, "line_end": 3}',
        )
    finally:
        await _cleanup(
            main_conn,
            stable_document_id=stable_document_id,
            reading_record_id=reading_record_id,
            user_id=user_id,
        )


# --- Test 3: thematic_break block with NULL text_content is accepted ---------


async def test_thematic_break_block_with_null_text_content_is_accepted(
    main_conn: asyncpg.Connection,
) -> None:
    """A ``thematic_break`` block with text_content=NULL must satisfy the constraint.

    markdown-it-py's hr token (``---`` / ``***`` / ``___``) carries no
    text content.
    """
    await _skip_if_migration_0023_not_applied(main_conn)

    user_id = await _insert_user(main_conn)
    reading_record_id = await _insert_reading_record(main_conn, user_id)
    stable_document_id = await _insert_stable_document(main_conn, reading_record_id)
    try:
        await _insert_block(
            main_conn,
            stable_document_id=stable_document_id,
            block_id="b1",
            order_index=0,
            block_type="thematic_break",
            text_content=None,
            payload_json='{}',
            source_refs_json='{"source_type": "pasted_text", "line_start": 1, "line_end": 1}',
        )
    finally:
        await _cleanup(
            main_conn,
            stable_document_id=stable_document_id,
            reading_record_id=reading_record_id,
            user_id=user_id,
        )


# --- Test 4: textual block with NULL text_content is still rejected ---------


async def test_paragraph_block_with_null_text_content_is_still_rejected(
    main_conn: asyncpg.Connection,
) -> None:
    """A ``paragraph`` block with text_content=NULL must still violate the constraint.

    The exemption extension is narrow: only structural block types
    (list / table / table_row / table_cell / image / code_block /
    thematic_break / unknown) may have NULL text_content. Narrative
    types like paragraph / heading / list_item / blockquote must still
    require non-empty text.
    """
    await _skip_if_migration_0023_not_applied(main_conn)

    user_id = await _insert_user(main_conn)
    reading_record_id = await _insert_reading_record(main_conn, user_id)
    stable_document_id = await _insert_stable_document(main_conn, reading_record_id)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_block(
                main_conn,
                stable_document_id=stable_document_id,
                block_id="b1",
                order_index=0,
                block_type="paragraph",
                text_content=None,
                payload_json='{}',
                source_refs_json='{"source_type": "pasted_text", "line_start": 1, "line_end": 1}',
            )
    finally:
        # No block was inserted (CheckViolation rolled back the implicit
        # txn), so only the document/record/user need cleanup.
        await main_conn.execute(
            "DELETE FROM stable_reading_documents WHERE id = $1",
            stable_document_id,
        )
        await main_conn.execute(
            "DELETE FROM reading_records WHERE id = $1",
            reading_record_id,
        )
        await main_conn.execute("DELETE FROM users WHERE id = $1", user_id)
