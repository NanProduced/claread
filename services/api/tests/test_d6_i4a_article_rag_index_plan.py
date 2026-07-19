"""Tests for D6-I4A Reader Article RAG Index Plan Foundation.

Covers the 14 test requirements from the task spec:
 1. paragraph/heading/list_item/blockquote/caption default indexable
 2. table/image/footnote/code_block/unknown default not indexable
 3. explicit policy promotes image_ocr/table_cell into rag_ask_only
 4. metadata_only blocks not indexed
 5. chunk content_sha256 deterministic
 6. dict ordering does not affect metadata hash
 7. canonical offsets aligned with block offsets
 8. non-contiguous offsets not merged
 9. no Plate/Markdown fields in citation refs
10. stale/inactive stable document fail closed
11. active base mismatch fail closed
12. empty eligible text fail closed
13. focused pytest pass
14. git diff --check pass

Uses real PostgreSQL with a temporary schema (BASELINE_SQL, which now
includes 0004_reader_document_blocks.sql).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration import article_rag_index_plan as plan_module
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    CHUNKER_VERSION,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfile,
    ArticleRagIndexProfileResolution,
    compute_article_rag_index_profile_fingerprint,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]

from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# 0004 (document_blocks) and 0010 (article_rag_index_state) are now in
# BASELINE_SQL, so the full plan schema is just BASELINE_SQL.
INDEX_PLAN_SCHEMA_SQL = BASELINE_SQL

# Fixed UUIDs for deterministic seeding.
_USER_ID = UUID("00000000-0000-0000-0000-0000000d4a01")
_RECORD_ID = UUID("00000000-0000-0000-0000-0000000d4a02")
_BASE_ID = UUID("00000000-0000-0000-0000-0000000d4a03")
_STABLE_DOC_ID = UUID("00000000-0000-0000-0000-0000000d4a04")
_OTHER_USER_ID = UUID("00000000-0000-0000-0000-0000000d4a05")

_DEFAULT_STABLE_SHA256 = "a" * 64


# ---------------------------------------------------------------------------
# Pool / schema fixtures
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


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def index_env() -> asyncpg.Pool:
    schema_name = f"test_i4a_rag_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(INDEX_PLAN_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(pool: asyncpg.Pool, user_id: UUID = _USER_ID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )


async def _seed_record(
    pool: asyncpg.Pool,
    *,
    user_id: UUID = _USER_ID,
    record_id: UUID = _RECORD_ID,
    generation: int = 1,
    active_base_id: UUID | None = None,
    lifecycle_status: str = "active",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, active_base_id
            )
            VALUES ($1, $2, 'text', 'I4A Test', 'en',
                    $3, 'processing', 'article_ready',
                    $4, $5)
            """,
            record_id,
            user_id,
            lifecycle_status,
            generation,
            active_base_id,
        )


async def _seed_base(
    pool: asyncpg.Pool,
    *,
    base_id: UUID = _BASE_ID,
    reading_record_id: UUID = _RECORD_ID,
    record_generation: int = 1,
    text: str = "Hello article RAG world.",
    status: str = "active",
) -> str:
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    content_utf16_length = utf16_code_unit_length(text)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id, reading_record_id, base_version, record_generation,
                text, content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                language, title_snapshot, navigation_json, status
            )
            VALUES ($1, $2, 1, $3,
                    $4, $5, $6,
                    'test_canon_v1', 'test_builder_v1', 'test_seg_v1',
                    'en', 'I4A Test', '{"units":[]}'::jsonb, $7)
            """,
            base_id,
            reading_record_id,
            record_generation,
            text,
            content_sha,
            content_utf16_length,
            status,
        )
    return content_sha


async def _seed_stable_document(
    pool: asyncpg.Pool,
    *,
    stable_document_id: UUID = _STABLE_DOC_ID,
    reading_record_id: UUID = _RECORD_ID,
    record_generation: int = 1,
    content_sha256: str = _DEFAULT_STABLE_SHA256,
    status: str = "active",
    document_version: int = 1,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO stable_reading_documents (
                id, reading_record_id, record_generation, title,
                document_version, source_profile_json, content_sha256, status
            )
            VALUES ($1, $2, $3, 'I4A Test',
                    $4, '{}'::jsonb, $5, $6)
            """,
            stable_document_id,
            reading_record_id,
            record_generation,
            document_version,
            content_sha256,
            status,
        )


async def _seed_block(
    pool: asyncpg.Pool,
    *,
    stable_document_id: UUID = _STABLE_DOC_ID,
    block_id: str,
    parent_block_id: str | None = None,
    order_index: int,
    block_type: str,
    text_content: str | None,
    canonical_text_start_utf16: int | None = None,
    canonical_text_end_utf16: int | None = None,
    interpretation_policy: dict | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO stable_document_blocks (
                stable_document_id, block_id, parent_block_id, order_index,
                block_type, text_content,
                canonical_text_start_utf16, canonical_text_end_utf16,
                interpretation_policy_json, payload_json, source_refs_json,
                quality_json
            )
            VALUES ($1, $2, $3, $4,
                    $5, $6,
                    $7, $8,
                    $9::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)
            """,
            stable_document_id,
            block_id,
            parent_block_id,
            order_index,
            block_type,
            text_content,
            canonical_text_start_utf16,
            canonical_text_end_utf16,
            jsonb_param(interpretation_policy or {}),
        )


async def _seed_unit(
    pool: asyncpg.Pool,
    *,
    base_id: UUID = _BASE_ID,
    reading_record_id: UUID = _RECORD_ID,
    unit_id: str,
    order_index: int = 1,
    unit_type: str = "body",
    base_start_utf16: int = 0,
    base_end_utf16: int = 10,
) -> None:
    text_hash = hashlib.sha256(f"{unit_id}:{base_start_utf16}:{base_end_utf16}".encode()).hexdigest()[:8]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_units (
                reading_record_id, base_id, unit_id, order_index,
                unit_type, boundary_quality,
                base_start_utf16, base_end_utf16,
                text_hash, metadata_json
            )
            VALUES ($1, $2, $3, $4,
                    $5, 'normal',
                    $6, $7,
                    $8, '{}'::jsonb)
            """,
            reading_record_id,
            base_id,
            unit_id,
            order_index,
            unit_type,
            base_start_utf16,
            base_end_utf16,
            text_hash,
        )


async def _seed_segment(
    pool: asyncpg.Pool,
    *,
    base_id: UUID = _BASE_ID,
    reading_record_id: UUID = _RECORD_ID,
    unit_id: str = "u1",
    anchor_segment_id: str = "s1",
    sentence_id: str | None = "s1",
    paragraph_id: str = "p1",
    order_index: int = 1,
    unit_order_index: int = 1,
    base_start_utf16: int = 0,
    base_end_utf16: int = 10,
    unit_start_utf16: int = 0,
    unit_end_utf16: int = 10,
) -> None:
    text_hash = hashlib.sha256(
        f"{anchor_segment_id}:{base_start_utf16}:{base_end_utf16}".encode()
    ).hexdigest()[:8]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO anchor_segments (
                reading_record_id, base_id, unit_id, anchor_segment_id,
                sentence_id, paragraph_id, order_index, unit_order_index,
                segment_type, base_start_utf16, base_end_utf16,
                unit_start_utf16, unit_end_utf16,
                text_hash, boundary_quality
            )
            VALUES ($1, $2, $3, $4,
                    $5, $6, $7, $8,
                    'sentence', $9, $10,
                    $11, $12,
                    $13, 'normal')
            """,
            reading_record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            sentence_id,
            paragraph_id,
            order_index,
            unit_order_index,
            base_start_utf16,
            base_end_utf16,
            unit_start_utf16,
            unit_end_utf16,
            text_hash,
        )


def _build_service(pool: asyncpg.Pool) -> ArticleRagIndexPlanService:
    return ArticleRagIndexPlanService(pool=pool)


def _build_base_text_and_offsets(
    *block_texts: str,
    separator: str = "\n\n",
) -> tuple[str, list[tuple[int, int]]]:
    """Concatenate block texts with a separator and compute UTF-16 offsets.

    Returns (base_text, [(start, end), ...]) where each (start, end) pair
    is the UTF-16 offset range of the corresponding block text within
    base_text.  This ensures canonical offsets always align with the base
    text for the P1-2 offset validation.
    """
    if not block_texts:
        return "", []
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    sep_utf16_len = utf16_code_unit_length(separator)
    for i, text in enumerate(block_texts):
        if i > 0:
            parts.append(separator)
            pos += sep_utf16_len
        start = pos
        parts.append(text)
        pos += utf16_code_unit_length(text)
        offsets.append((start, pos))
    return "".join(parts), offsets


async def _seed_full_environment(
    pool: asyncpg.Pool,
    *,
    base_text: str = "Hello article RAG world.",
    record_generation: int = 1,
    active_base_id: UUID = _BASE_ID,
    stable_document_id: UUID = _STABLE_DOC_ID,
) -> str:
    """Seed user + record + base + stable document. Returns base content_sha256."""
    await _seed_user(pool)
    # Insert record with active_base_id=NULL first (circular FK).
    await _seed_record(pool, active_base_id=None, generation=record_generation)
    base_sha = await _seed_base(
        pool,
        base_id=active_base_id,
        record_generation=record_generation,
        text=base_text,
    )
    # Link record to base.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            active_base_id,
        )
    await _seed_stable_document(
        pool,
        stable_document_id=stable_document_id,
        record_generation=record_generation,
    )
    return base_sha


# ---------------------------------------------------------------------------
# Main-reading policy helpers
# ---------------------------------------------------------------------------


def _main_reading_policy(scope: str = "main_reading_text") -> dict:
    return {
        "allowed_source_scope": [scope],
        "default_route": "main_reading",
        "rag_eligible": True,
    }


def _rag_ask_only_policy(scope: str = "table_cell") -> dict:
    return {
        "allowed_source_scope": [scope],
        "default_route": "rag_ask_only",
        "rag_eligible": True,
    }


def _metadata_only_policy(scope: str = "table_cell") -> dict:
    return {
        "allowed_source_scope": [scope],
        "default_route": "metadata_only",
        "rag_eligible": False,
    }


# ===================================================================
# Test 1: paragraph/heading/list_item/blockquote/caption default indexable
# ===================================================================


async def test_default_indexable_block_types_produce_chunks(index_env: asyncpg.Pool) -> None:
    """Requirement 1: paragraph/heading/list_item/blockquote/caption are
    indexable by default (main_reading route, rag_eligible=True)."""
    block_specs = [
        ("heading", "heading-1", "Section Title", "heading"),
        ("paragraph", "paragraph-1", "First paragraph text.", "main_reading_text"),
        ("list_item", "list-1", "List item text.", "main_reading_text"),
        ("blockquote", "quote-1", "Quote text.", "main_reading_text"),
        ("caption", "caption-1", "Caption text.", "main_reading_text"),
    ]
    base_text, offsets = _build_base_text_and_offsets(
        *(spec[2] for spec in block_specs)
    )
    await _seed_full_environment(index_env, base_text=base_text)

    for order_index, ((block_type, block_id, text, scope), (start, end)) in enumerate(
        zip(block_specs, offsets, strict=True)
    ):
        await _seed_block(
            index_env,
            block_id=block_id,
            order_index=order_index,
            block_type=block_type,
            text_content=text,
            canonical_text_start_utf16=start,
            canonical_text_end_utf16=end,
            interpretation_policy=_main_reading_policy(scope),
        )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    assert isinstance(plan, ArticleRagIndexPlan)
    assert len(plan.chunks) == 5
    assert [c.citation.block_ids[0] for c in plan.chunks] == [
        "heading-1",
        "paragraph-1",
        "list-1",
        "quote-1",
        "caption-1",
    ]
    # Each chunk's text matches its block text_content.
    for chunk, (_, _, expected_text, _) in zip(plan.chunks, block_specs, strict=True):
        assert chunk.text == expected_text


# ===================================================================
# Test 2: table/image/footnote/code_block/unknown default not indexable
# ===================================================================


async def test_default_non_indexable_block_types_excluded(index_env: asyncpg.Pool) -> None:
    """Requirement 2: table/image/footnote/code_block/unknown are not
    indexed by default.  Footnote/code_block are rag_eligible but route to
    rag_ask_only; table/image/unknown are metadata_only / not eligible.
    With only non-indexable blocks, the plan fails closed."""
    para_text = "Indexable paragraph."
    await _seed_full_environment(index_env, base_text=para_text)

    # Seed one paragraph (indexable) so the plan doesn't fail closed.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=para_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(para_text),
        interpretation_policy=_main_reading_policy(),
    )
    # Non-indexable blocks (by default route / eligibility).
    # table: metadata_only, not eligible
    await _seed_block(
        index_env,
        block_id="table-1",
        order_index=1,
        block_type="table",
        text_content=None,
        interpretation_policy=_metadata_only_policy("table_cell"),
    )
    # image: metadata_only, not eligible
    await _seed_block(
        index_env,
        block_id="image-1",
        order_index=2,
        block_type="image",
        text_content=None,
        interpretation_policy=_metadata_only_policy("image_ocr"),
    )
    # footnote: rag_ask_only, eligible (but not main_reading)
    await _seed_block(
        index_env,
        block_id="footnote-1",
        order_index=3,
        block_type="footnote",
        text_content="Footnote text.",
        interpretation_policy=_rag_ask_only_policy("footnote"),
    )
    # code_block: rag_ask_only, eligible
    await _seed_block(
        index_env,
        block_id="code-1",
        order_index=4,
        block_type="code_block",
        text_content="print('hello')",
        interpretation_policy=_rag_ask_only_policy("code_block"),
    )
    # unknown: metadata_only, not eligible
    await _seed_block(
        index_env,
        block_id="unknown-1",
        order_index=5,
        block_type="unknown",
        text_content=None,
        interpretation_policy=_metadata_only_policy("published_layer"),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # Only the paragraph is indexed; the rest are excluded by default.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


# ===================================================================
# Test 3: explicit policy promotes image_ocr/table_cell into rag_ask_only
# ===================================================================


async def test_rag_ask_only_blocks_indexed_when_requested(index_env: asyncpg.Pool) -> None:
    """Requirement 3: with include_rag_ask_only=True, image_ocr and
    table_cell blocks (rag_ask_only route) are indexed."""
    await _seed_full_environment(index_env)

    await _seed_block(
        index_env,
        block_id="image-ocr-1",
        order_index=0,
        block_type="image_ocr",
        text_content="OCR extracted text.",
        interpretation_policy=_rag_ask_only_policy("image_ocr"),
    )
    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=1,
        block_type="table_cell",
        text_content="Cell content.",
        interpretation_policy=_rag_ask_only_policy("table_cell"),
    )

    service = _build_service(index_env)

    # Default: no chunks from rag_ask_only blocks -> fail closed.
    with pytest.raises(ArticleRagIndexPlanError, match="No RAG-eligible"):
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
        )

    # With include_rag_ask_only=True: both blocks indexed.
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("image-ocr-1",)
    assert plan.chunks[1].citation.block_ids == ("table-cell-1",)
    assert plan.chunks[0].source_scope == "image_ocr"
    assert plan.chunks[1].source_scope == "table_cell"
    # rag_ask_only blocks don't have canonical offsets.
    assert plan.chunks[0].citation.canonical_text_start_utf16 is None
    assert plan.chunks[0].citation.canonical_text_end_utf16 is None


# ===================================================================
# Test 4: metadata_only blocks not indexed
# ===================================================================


async def test_metadata_only_blocks_never_indexed(index_env: asyncpg.Pool) -> None:
    """Requirement 4: metadata_only blocks are excluded even with
    include_rag_ask_only=True."""
    para_text = "Indexable paragraph."
    await _seed_full_environment(index_env, base_text=para_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=para_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(para_text),
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="table-1",
        order_index=1,
        block_type="table",
        text_content=None,
        interpretation_policy=_metadata_only_policy("table_cell"),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    # Only the paragraph; the table (metadata_only) is never indexed.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


# ===================================================================
# Test 5: chunk content_sha256 deterministic
# ===================================================================


async def test_content_sha256_deterministic_across_rebuilds(index_env: asyncpg.Pool) -> None:
    """Requirement 5: building the plan twice produces identical
    content_sha256 and chunk_id values."""
    block_text = "Deterministic text."
    await _seed_full_environment(index_env, base_text=block_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(block_text),
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan1 = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)
    plan2 = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan1.chunks) == 1
    assert len(plan2.chunks) == 1
    c1 = plan1.chunks[0]
    c2 = plan2.chunks[0]
    assert c1.chunk_id == c2.chunk_id
    assert c1.content_sha256 == c2.content_sha256
    assert c1.content_sha256 == hashlib.sha256("Deterministic text.".encode("utf-8")).hexdigest()
    assert c1.embedding_text_sha256 == c2.embedding_text_sha256


# ===================================================================
# Test 6: dict ordering does not affect metadata hash
# ===================================================================


async def test_metadata_independent_of_policy_dict_key_order(index_env: asyncpg.Pool) -> None:
    """Requirement 6: interpretation_policy_json with different key orders
    produces identical chunk metadata."""
    block_text = "Same text for both blocks."
    base_text, offsets = _build_base_text_and_offsets(block_text, block_text)
    await _seed_full_environment(index_env, base_text=base_text)

    policy_a = {
        "allowed_source_scope": ["main_reading_text"],
        "default_route": "main_reading",
        "rag_eligible": True,
    }
    # Same values, different insertion order.
    policy_b = {
        "rag_eligible": True,
        "default_route": "main_reading",
        "allowed_source_scope": ["main_reading_text"],
    }

    await _seed_block(
        index_env,
        block_id="block-a",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=policy_a,
    )
    await _seed_block(
        index_env,
        block_id="block-b",
        order_index=1,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=policy_b,
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 2
    c1, c2 = plan.chunks
    # Different block_ids -> different chunk_ids (block_id is in the hash input).
    assert c1.chunk_id != c2.chunk_id
    # But same text -> same content_sha256.
    assert c1.content_sha256 == c2.content_sha256
    # Same metadata (block_type, block_order_index differs, but the rest
    # is the same since policy fields extracted are identical).
    assert c1.metadata_json["block_type"] == c2.metadata_json["block_type"]
    assert c1.metadata_json["source_scope"] == c2.metadata_json["source_scope"]
    assert c1.metadata_json["default_route"] == c2.metadata_json["default_route"]
    assert c1.metadata_json["has_canonical_offsets"] == c2.metadata_json["has_canonical_offsets"]


# ===================================================================
# Test 7: canonical offsets aligned with block offsets
# ===================================================================


async def test_canonical_offsets_aligned_with_block_offsets(index_env: asyncpg.Pool) -> None:
    """Requirement 7: chunk citation canonical offsets match the block's
    canonical_text_start/end_utf16 AND the slice content matches the base
    text at those offsets."""
    block_text = "Aligned offset text."
    prefix = "PPPPP"  # 5 UTF-16 code units
    start = utf16_code_unit_length(prefix)
    end = start + utf16_code_unit_length(block_text)
    base_text = prefix + block_text
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=start,
        canonical_text_end_utf16=end,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.citation.canonical_text_start_utf16 == start
    assert chunk.citation.canonical_text_end_utf16 == end


# ===================================================================
# Test 8: non-contiguous offsets not merged
# ===================================================================


async def test_non_contiguous_offsets_produce_separate_chunks(
    index_env: asyncpg.Pool,
) -> None:
    """Requirement 8: two blocks with non-contiguous canonical offsets
    produce two separate chunks, not one merged chunk."""
    block1_text = "First block"
    block2_text = "Second block"
    gap_text = "X" * 39  # gap between block1 end and block2 start
    base_text = block1_text + gap_text + block2_text
    start1 = 0
    end1 = utf16_code_unit_length(block1_text)
    start2 = end1 + utf16_code_unit_length(gap_text)
    end2 = start2 + utf16_code_unit_length(block2_text)
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block1_text,
        canonical_text_start_utf16=start1,
        canonical_text_end_utf16=end1,
        interpretation_policy=_main_reading_policy(),
    )
    # Block 2: non-contiguous (gap between end1 and start2)
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=block2_text,
        canonical_text_start_utf16=start2,
        canonical_text_end_utf16=end2,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 2
    c1, c2 = plan.chunks
    assert c1.citation.block_ids == ("paragraph-1",)
    assert c2.citation.block_ids == ("paragraph-2",)
    assert c1.citation.canonical_text_start_utf16 == start1
    assert c1.citation.canonical_text_end_utf16 == end1
    assert c2.citation.canonical_text_start_utf16 == start2
    assert c2.citation.canonical_text_end_utf16 == end2
    # No single chunk spans the gap.
    for chunk in plan.chunks:
        assert chunk.citation.canonical_text_end_utf16 is not None
        assert chunk.citation.canonical_text_start_utf16 is not None
        assert chunk.citation.canonical_text_end_utf16 > chunk.citation.canonical_text_start_utf16


# ===================================================================
# Test 9: no Plate/Markdown fields in citation refs
# ===================================================================


async def test_no_plate_or_markdown_fields_in_citation(index_env: asyncpg.Pool) -> None:
    """Requirement 9: ArticleRagCitationRef contains only canonical truth
    fields — no Plate JSON, Slate path, DOM selection, or Markdown offset."""
    block_text = "Citation field check."
    await _seed_full_environment(index_env, base_text=block_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(block_text),
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    citation = plan.chunks[0].citation
    assert isinstance(citation, ArticleRagCitationRef)

    # Allowed fields on ArticleRagCitationRef.
    allowed_fields = {
        "reading_record_id",
        "stable_document_id",
        "base_id",
        "record_generation",
        "block_ids",
        "unit_ids",
        "anchor_segment_ids",
        "canonical_text_start_utf16",
        "canonical_text_end_utf16",
    }
    actual_fields = set(citation.__slots__)  # type: ignore[attr-defined]
    assert actual_fields == allowed_fields, (
        f"Citation ref has unexpected fields: {actual_fields - allowed_fields}"
    )

    # Explicitly verify forbidden fields are absent.
    forbidden = {
        "plate", "plate_json", "slate_path", "dom_selection",
        "markdown_offset", "markdown_syntax", "ui_display_group",
        "display_group",
    }
    assert not (actual_fields & forbidden)


# ===================================================================
# Test 10: stale/inactive stable document fail closed
# ===================================================================


async def test_stale_stable_document_fail_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 10: when the stable document is superseded (no active),
    the plan fails closed."""
    await _seed_user(index_env)
    await _seed_record(index_env, active_base_id=None)
    await _seed_base(index_env)
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            _BASE_ID,
        )
    # Only a superseded stable document (no active).
    await _seed_stable_document(index_env, status="superseded")

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no active stable document"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_inactive_stable_document_fail_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 10 variant: stable document with status != 'active'
    is not indexed."""
    await _seed_user(index_env)
    await _seed_record(index_env, active_base_id=None)
    await _seed_base(index_env)
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            _BASE_ID,
        )
    # No stable document at all.
    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no active stable document"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


# ===================================================================
# Test 11: active base mismatch fail closed
# ===================================================================


async def test_superseded_active_base_fail_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 11: when the active_base_id points to a superseded base,
    the plan fails closed."""
    await _seed_user(index_env)
    await _seed_record(index_env, active_base_id=None)
    # Base with status='superseded' (not active).
    await _seed_base(index_env, status="superseded")
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            _BASE_ID,
        )
    await _seed_stable_document(index_env)

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no active reading base"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_base_generation_mismatch_fail_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 11 variant: base record_generation != record generation."""
    await _seed_user(index_env)
    # Record with generation=2.
    await _seed_record(index_env, active_base_id=None, generation=2)
    # Base with generation=1 (FK allows this because record.active_base_id
    # is initially NULL; we can't link them due to the composite FK).
    # Instead, create a base with generation=2 but a separate stable
    # document with generation=1 to trigger the stable-vs-record mismatch.
    await _seed_base(index_env, record_generation=2)
    async with index_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            _RECORD_ID,
            _BASE_ID,
        )
    # Stable document with generation=1 (mismatches record's generation=2).
    await _seed_stable_document(index_env, record_generation=1)

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="generation"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


# ===================================================================
# Test 12: empty eligible text fail closed
# ===================================================================


async def test_empty_eligible_text_fail_closed(index_env: asyncpg.Pool) -> None:
    """Requirement 12: a RAG-eligible block with no text_content fails
    closed (conservative fail-closed choice)."""
    await _seed_full_environment(index_env)

    # table_cell is rag_eligible=True by default; text_content can be NULL
    # per the migration CHECK constraint.  With include_rag_ask_only=True,
    # the block passes the route filter but hits the empty-text guard.
    await _seed_block(
        index_env,
        block_id="table-cell-empty",
        order_index=0,
        block_type="table_cell",
        text_content=None,
        interpretation_policy=_rag_ask_only_policy("table_cell"),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no text_content"):
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            include_rag_ask_only=True,
        )


# ===================================================================
# Additional coverage: ownership, units/segments, plan structure
# ===================================================================


async def test_wrong_user_raises_lookup_error(index_env: asyncpg.Pool) -> None:
    """Record not found for a different user -> LookupError."""
    await _seed_full_environment(index_env)

    service = _build_service(index_env)
    with pytest.raises(LookupError):
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_OTHER_USER_ID,
        )


async def test_unit_and_segment_overlap_attached_to_citation(
    index_env: asyncpg.Pool,
) -> None:
    """Units and segments overlapping the block's canonical offsets are
    attached to the chunk's citation ref."""
    base_text = "Hello article RAG world."
    await _seed_full_environment(index_env, base_text=base_text)

    # Block spans offsets 0-10 in the base text.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content="Hello arti",
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=10,
        interpretation_policy=_main_reading_policy(),
    )
    # Unit overlapping (0-10).
    await _seed_unit(
        index_env,
        unit_id="u1",
        order_index=1,
        base_start_utf16=0,
        base_end_utf16=12,
    )
    # Unit NOT overlapping (starts after block ends).
    await _seed_unit(
        index_env,
        unit_id="u2",
        order_index=2,
        base_start_utf16=15,
        base_end_utf16=25,
    )
    # Segment overlapping (0-10).
    await _seed_segment(
        index_env,
        unit_id="u1",
        anchor_segment_id="s1",
        order_index=1,
        base_start_utf16=0,
        base_end_utf16=5,
        unit_start_utf16=0,
        unit_end_utf16=5,
    )
    # Segment NOT overlapping.
    await _seed_segment(
        index_env,
        unit_id="u2",
        anchor_segment_id="s2",
        sentence_id="s2",
        order_index=2,
        unit_order_index=1,
        base_start_utf16=15,
        base_end_utf16=25,
        unit_start_utf16=0,
        unit_end_utf16=10,
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    citation = plan.chunks[0].citation
    assert citation.unit_ids == ("u1",)
    assert citation.anchor_segment_ids == ("s1",)


async def test_plan_structure_fields(index_env: asyncpg.Pool) -> None:
    """The ArticleRagIndexPlan carries the correct top-level fields."""
    base_text = "Hello article RAG world."
    base_sha = await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content="Hello article RAG world.",
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=24,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert plan.reading_record_id == _RECORD_ID
    assert plan.stable_document_id == _STABLE_DOC_ID
    assert plan.base_id == _BASE_ID
    assert plan.record_generation == 1
    assert plan.content_sha256 == _DEFAULT_STABLE_SHA256
    assert plan.canonical_text_sha256 == base_sha
    assert plan.chunker_version == CHUNKER_VERSION
    assert plan.warnings == ()


async def test_main_reading_block_without_canonical_offsets_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """A main_reading block without canonical offsets is a data
    inconsistency and fails closed."""
    await _seed_full_environment(index_env)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content="Missing offsets.",
        canonical_text_start_utf16=None,
        canonical_text_end_utf16=None,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no canonical_text offsets"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_no_blocks_fail_closed(index_env: asyncpg.Pool) -> None:
    """Stable document with no blocks fails closed."""
    await _seed_full_environment(index_env)

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no blocks"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_chunk_metadata_structure(index_env: asyncpg.Pool) -> None:
    """Each chunk's metadata_json has the expected deterministic keys."""
    block_text = "Metadata check."
    await _seed_full_environment(index_env, base_text=block_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(block_text),
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    metadata = plan.chunks[0].metadata_json
    assert set(metadata.keys()) == {
        "block_type",
        "block_order_index",
        "source_scope",
        "default_route",
        "chunk_index",
        "has_canonical_offsets",
    }
    assert metadata["block_type"] == "paragraph"
    assert metadata["block_order_index"] == 0
    assert metadata["source_scope"] == "main_reading_text"
    assert metadata["default_route"] == "main_reading"
    assert metadata["chunk_index"] == 0
    assert metadata["has_canonical_offsets"] is True


# ===================================================================
# P1-1: Empty {} policy materialization per block_type
# ===================================================================


async def test_empty_policy_table_excluded(index_env: asyncpg.Pool) -> None:
    """P1-1: ``{}`` policy on a table block must materialize as
    metadata_only / not eligible — not main_reading / eligible."""
    para_text = "Indexable paragraph."
    await _seed_full_environment(index_env, base_text=para_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=para_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(para_text),
        interpretation_policy=_main_reading_policy(),
    )
    # table with empty {} policy — must default to metadata_only / not eligible
    await _seed_block(
        index_env,
        block_id="table-1",
        order_index=1,
        block_type="table",
        text_content=None,
        interpretation_policy={},
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    # Only paragraph; table excluded even with include_rag_ask_only.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


async def test_empty_policy_image_excluded(index_env: asyncpg.Pool) -> None:
    """P1-1: ``{}`` policy on an image block must materialize as
    metadata_only / not eligible."""
    para_text = "Indexable paragraph."
    await _seed_full_environment(index_env, base_text=para_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=para_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(para_text),
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="image-1",
        order_index=1,
        block_type="image",
        text_content=None,
        interpretation_policy={},
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


async def test_empty_policy_unknown_excluded(index_env: asyncpg.Pool) -> None:
    """P1-1: ``{}`` policy on an unknown block must materialize as
    metadata_only / not eligible."""
    para_text = "Indexable paragraph."
    await _seed_full_environment(index_env, base_text=para_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=para_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(para_text),
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="unknown-1",
        order_index=1,
        block_type="unknown",
        text_content=None,
        interpretation_policy={},
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )

    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


async def test_empty_policy_footnote_defaults_rag_ask_only(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a footnote block must materialize as
    rag_ask_only (not main_reading).  Not indexed by default; indexed
    with include_rag_ask_only=True."""
    footnote_text = "Footnote text."
    await _seed_full_environment(index_env, base_text=footnote_text)

    await _seed_block(
        index_env,
        block_id="footnote-1",
        order_index=0,
        block_type="footnote",
        text_content=footnote_text,
        interpretation_policy={},
    )

    service = _build_service(index_env)

    # Default: rag_ask_only not indexed -> fail closed.
    with pytest.raises(ArticleRagIndexPlanError, match="No RAG-eligible"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    # With include_rag_ask_only=True: indexed with rag_ask_only route.
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("footnote-1",)
    assert plan.chunks[0].source_scope == "footnote"
    assert plan.chunks[0].metadata_json["default_route"] == "rag_ask_only"


async def test_empty_policy_code_block_defaults_rag_ask_only(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a code_block must materialize as
    rag_ask_only (not main_reading)."""
    code_text = "print('hello')"
    await _seed_full_environment(index_env, base_text=code_text)

    await _seed_block(
        index_env,
        block_id="code-1",
        order_index=0,
        block_type="code_block",
        text_content=code_text,
        interpretation_policy={},
    )

    service = _build_service(index_env)

    with pytest.raises(ArticleRagIndexPlanError, match="No RAG-eligible"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("code-1",)
    assert plan.chunks[0].source_scope == "code_block"
    assert plan.chunks[0].metadata_json["default_route"] == "rag_ask_only"


async def test_empty_policy_paragraph_defaults_main_reading(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a paragraph must materialize as
    main_reading / eligible — indexed by default."""
    block_text = "Default policy paragraph."
    await _seed_full_environment(index_env, base_text=block_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(block_text),
        interpretation_policy={},
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[0].source_scope == "main_reading_text"
    assert plan.chunks[0].metadata_json["default_route"] == "main_reading"


async def test_empty_policy_heading_defaults_main_reading_heading_scope(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a heading must materialize as main_reading
    with heading scope (not main_reading_text)."""
    block_text = "Heading Title"
    await _seed_full_environment(index_env, base_text=block_text)

    await _seed_block(
        index_env,
        block_id="heading-1",
        order_index=0,
        block_type="heading",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(block_text),
        interpretation_policy={},
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    assert plan.chunks[0].source_scope == "heading"
    assert plan.chunks[0].metadata_json["default_route"] == "main_reading"


async def test_empty_policy_table_cell_defaults_rag_ask_only(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a table_cell must materialize as
    rag_ask_only / eligible (not metadata_only)."""
    cell_text = "Cell content."
    await _seed_full_environment(index_env, base_text=cell_text)

    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=0,
        block_type="table_cell",
        text_content=cell_text,
        interpretation_policy={},
    )

    service = _build_service(index_env)

    # Default: rag_ask_only not indexed -> fail closed.
    with pytest.raises(ArticleRagIndexPlanError, match="No RAG-eligible"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        include_rag_ask_only=True,
    )
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("table-cell-1",)
    assert plan.chunks[0].source_scope == "table_cell"


# ===================================================================
# P1-2: Canonical offset validation against base text
# ===================================================================


async def test_offset_out_of_bounds_fail_closed(index_env: asyncpg.Pool) -> None:
    """P1-2: canonical end offset exceeds base text UTF-16 length."""
    block_text = "Short text."
    await _seed_full_environment(index_env, base_text=block_text)

    # end=100 but base text is only 11 UTF-16 code units.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=100,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="out of bounds"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_offset_start_beyond_base_length_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-2: start offset beyond base text UTF-16 length."""
    base_text = "Short text."  # 11 UTF-16 code units
    block_text = "ab"  # 2 UTF-16 code units
    await _seed_full_environment(index_env, base_text=base_text)

    # start=20, end=22 — both within DB CHECK (end > start, both >= 0)
    # but start > base_utf16_length (11).
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=20,
        canonical_text_end_utf16=22,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="out of bounds"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_offset_span_length_mismatch_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-2: canonical span length != text_content UTF-16 length."""
    base_text = "A longer base text for testing."
    block_text = "Short."
    # block_text is 6 UTF-16 code units, but offsets span 0-15 (15 units).
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=15,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="span length"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_offset_slice_content_mismatch_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P1-2: base text slice at canonical offsets != text_content."""
    base_text = "ABCDEFGHIJ"  # 10 UTF-16 code units
    block_text = "XYZ"  # 3 UTF-16 code units
    # Span length matches (3), but slice content differs.
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=3,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="slice does not match"):
        await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)


async def test_offset_valid_alignment_passes(index_env: asyncpg.Pool) -> None:
    """P1-2: valid offsets (bounds + span + slice all match) produce a chunk."""
    base_text = "PREFIXHello article RAG world."
    block_text = "Hello article RAG world."
    start = utf16_code_unit_length("PREFIX")
    end = start + utf16_code_unit_length(block_text)
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=start,
        canonical_text_end_utf16=end,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.text == block_text
    assert chunk.citation.canonical_text_start_utf16 == start
    assert chunk.citation.canonical_text_end_utf16 == end


async def test_offset_bmp_emoji_alignment(index_env: asyncpg.Pool) -> None:
    """P1-2: UTF-16 alignment with astral plane character (emoji, 2 code units)."""
    block_text = "Hello world."
    emoji_prefix = "\U0001F600"  # 1 Python char = 2 UTF-16 code units
    base_text = emoji_prefix + block_text
    start = utf16_code_unit_length(emoji_prefix)  # 2
    end = start + utf16_code_unit_length(block_text)  # 2 + 12 = 14
    await _seed_full_environment(index_env, base_text=base_text)

    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=block_text,
        canonical_text_start_utf16=start,
        canonical_text_end_utf16=end,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)

    assert len(plan.chunks) == 1
    assert plan.chunks[0].text == block_text
    assert plan.chunks[0].citation.canonical_text_start_utf16 == 2
    assert plan.chunks[0].citation.canonical_text_end_utf16 == 14


# ===================================================================
# P1-E: V1 characterization baseline + version-aware plan dispatch
# ===================================================================


# V1 frozen literals captured from the V1 plan builder.  These are
# byte-stability anchors: any change to V1 plan / hash / chunk_id /
# content_sha256 / embedding_text_sha256 / citation fields must be
# detected here.  Literals are independently computable from the V1
# contract (SHA-256 of known strings) — the test does NOT re-implement
# the plan hash algorithm; it only asserts the production
# ``compute_plan_content_sha256`` result equals the frozen literal.
_P1E_V1_BASE_TEXT = "Hello article RAG world."
_P1E_V1_BASE_TEXT_UTF16_LEN = 24  # ASCII-only, 24 code units
_P1E_V1_CHUNKER_VERSION = "article_rag_index_plan_v1"
_P1E_V1_CHUNK_ID = "9c0de682d80dc1f0"
_P1E_V1_CONTENT_SHA256 = (
    "d3e0a2214433bbc3728f44d75ddb2e530f63fb6af67a8ae9ed4a208f27db3c62"
)
_P1E_V1_EMBEDDING_TEXT_SHA256 = (
    "d3e0a2214433bbc3728f44d75ddb2e530f63fb6af67a8ae9ed4a208f27db3c62"
)
# V1 plan_content_sha256 captured from the first green run of the
# V1 characterization baseline.  Byte-stability anchor: any change to
# V1 plan serialization (field order, separators, types) MUST be
# detected here.
_P1E_V1_PLAN_CONTENT_SHA256 = (
    "48abeff21b4e5dedd7d06b60b27ecf37b0c50f4aca4487f11cec5798c4c40c8a"
)


# ===================================================================
# P1-E-R1: Fail-closed dispatch closure + golden coverage expansion
# ===================================================================

# P1-E-R1: the fixed local error message used by the plan service's
# version-aware dispatch wrapper.  The offending input is NEVER
# echoed in str / repr / args / traceback.  This literal is
# independently asserted by the fail-closed tests below.
_P1E_R1_EXPECTED_FIXED_MESSAGE = (
    "Article RAG index plan version is not supported"
)

# P1-E-R1: V1 golden literals for full plan / chunk / citation field
# coverage.  Independently derived from the V1 contract (fixed UUIDs,
# SHA-256 of known strings, fixed metadata dict) — the test does NOT
# re-implement the plan hash algorithm or call production helpers to
# generate expected values.  Multi-key or missing-key metadata drift
# MUST fail here (complete dict == equality, no issubset).
_P1E_V1_STABLE_DOCUMENT_CONTENT_SHA256 = "a" * 64  # _DEFAULT_STABLE_SHA256
_P1E_V1_CANONICAL_TEXT_SHA256 = _P1E_V1_CONTENT_SHA256  # sha256 of base text
_P1E_V1_SOURCE_SCOPE = "main_reading_text"
_P1E_V1_EXPECTED_METADATA: dict = {
    "block_type": "paragraph",
    "block_order_index": 0,
    "source_scope": "main_reading_text",
    "default_route": "main_reading",
    "chunk_index": 0,
    "has_canonical_offsets": True,
}


class _P1ER1ProbeConnection:
    """Probe connection that records all DB calls and raises
    AssertionError if any truth-layer read is attempted.

    Used to prove that the plan service fails closed BEFORE any
    database read when the resolver rejects ``index_version``.
    """

    def __init__(self) -> None:
        self.call_count = 0

    async def fetchrow(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError(
            "Probe connection fetchrow was called — plan service "
            "attempted a truth-layer read before the resolver rejected "
            "the offending index_version."
        )

    async def fetch(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError(
            "Probe connection fetch was called — plan service "
            "attempted a truth-layer read before the resolver rejected "
            "the offending index_version."
        )

    async def fetchval(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError(
            "Probe connection fetchval was called — plan service "
            "attempted a truth-layer read before the resolver rejected "
            "the offending index_version."
        )

    async def execute(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError(
            "Probe connection execute was called — plan service "
            "attempted a truth-layer write before the resolver rejected "
            "the offending index_version."
        )


def _assert_p1e_v1_golden_fields(plan: ArticleRagIndexPlan) -> None:
    """Assert the plan matches the V1 golden literal fixture.

    Covers all spec-required fields:
      * plan.reading_record_id / stable_document_id / base_id /
        record_generation / content_sha256 / canonical_text_sha256 /
        chunker_version / warnings
      * chunk.text / source_scope / metadata_json (complete dict ==)
        / content_sha256 / embedding_text_sha256 / chunk_id
      * all citation fields (block_ids / unit_ids /
        anchor_segment_ids / canonical UTF-16 offsets / record /
        document / base / generation)
      * compute_plan_content_sha256(plan)
      * ordered chunk IDs

    Expected values are test-side literals — no production helpers
    are called to generate them, and the plan hash algorithm is not
    re-implemented.  metadata_json uses complete dict ``==`` (no
    issubset, no production helper, no algorithm re-implementation);
    a missing or extra key MUST fail.
    """
    # Plan-level fields.
    assert plan.reading_record_id == _RECORD_ID
    assert plan.stable_document_id == _STABLE_DOC_ID
    assert plan.base_id == _BASE_ID
    assert plan.record_generation == 1
    assert plan.content_sha256 == _P1E_V1_STABLE_DOCUMENT_CONTENT_SHA256
    assert plan.canonical_text_sha256 == _P1E_V1_CANONICAL_TEXT_SHA256
    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION
    assert plan.warnings == ()

    # Chunk-level fields.
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.chunk_id == _P1E_V1_CHUNK_ID
    assert chunk.text == _P1E_V1_BASE_TEXT
    assert chunk.source_scope == _P1E_V1_SOURCE_SCOPE
    assert chunk.content_sha256 == _P1E_V1_CONTENT_SHA256
    assert chunk.embedding_text_sha256 == _P1E_V1_EMBEDDING_TEXT_SHA256
    # metadata_json: complete dict strict equality (no issubset,
    # no production helper, no algorithm re-implementation).
    assert chunk.metadata_json == _P1E_V1_EXPECTED_METADATA
    # Extra-key / missing-key guard: the dict must have EXACTLY the
    # expected keys.
    assert set(chunk.metadata_json.keys()) == set(
        _P1E_V1_EXPECTED_METADATA.keys()
    )

    # Citation-level fields.
    citation = chunk.citation
    assert citation.reading_record_id == _RECORD_ID
    assert citation.stable_document_id == _STABLE_DOC_ID
    assert citation.base_id == _BASE_ID
    assert citation.record_generation == 1
    assert citation.block_ids == ("paragraph-1",)
    assert citation.unit_ids == ()
    assert citation.anchor_segment_ids == ()
    assert citation.canonical_text_start_utf16 == 0
    assert citation.canonical_text_end_utf16 == _P1E_V1_BASE_TEXT_UTF16_LEN

    # Ordered chunk IDs.
    assert tuple(c.chunk_id for c in plan.chunks) == (_P1E_V1_CHUNK_ID,)

    # Plan content sha256 (frozen literal — captured from production
    # ``compute_plan_content_sha256``, NOT re-implemented here).
    assert compute_plan_content_sha256(plan) == _P1E_V1_PLAN_CONTENT_SHA256


async def _p1e_seed_minimal_v1_env(
    pool: asyncpg.Pool,
    *,
    base_text: str = _P1E_V1_BASE_TEXT,
) -> str:
    """Seed the minimal V1 characterization environment.

    1 paragraph block, no units, no segments.  Returns base content_sha256.
    """
    await _seed_full_environment(pool, base_text=base_text)
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=base_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=_P1E_V1_BASE_TEXT_UTF16_LEN,
        interpretation_policy=_main_reading_policy(),
    )
    return hashlib.sha256(base_text.encode("utf-8")).hexdigest()


async def test_p1e_v1_characterization_baseline(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E Step 1: V1 characterization baseline.

    Freeze V1 outputs as fixed literals BEFORE modifying production
    code.  If this test passes on the first run, report it as a
    characterization baseline (no fake RED).
    """
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # V1 chunker_version (frozen literal).
    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION
    assert plan.chunker_version == CHUNKER_VERSION

    # V1 plan content sha256 (frozen literal — captured from production
    # ``compute_plan_content_sha256``).
    actual_plan_sha = compute_plan_content_sha256(plan)
    assert actual_plan_sha == _P1E_V1_PLAN_CONTENT_SHA256, (
        f"V1 plan_content_sha256 drift: expected "
        f"{_P1E_V1_PLAN_CONTENT_SHA256}, got {actual_plan_sha}"
    )

    # V1 chunk count (frozen literal).
    assert len(plan.chunks) == 1

    chunk = plan.chunks[0]

    # V1 chunk_id (frozen literal — first 16 hex of sha256 of the
    # canonical chunk_id format string).
    assert chunk.chunk_id == _P1E_V1_CHUNK_ID

    # V1 content_sha256 (frozen literal — sha256 of the block text).
    assert chunk.content_sha256 == _P1E_V1_CONTENT_SHA256

    # V1 embedding_text_sha256 (frozen literal — V1 embedding_text ==
    # text, so this equals content_sha256).
    assert chunk.embedding_text_sha256 == _P1E_V1_EMBEDDING_TEXT_SHA256

    # V1 citation fields (frozen literals).
    citation = chunk.citation
    assert citation.reading_record_id == _RECORD_ID
    assert citation.stable_document_id == _STABLE_DOC_ID
    assert citation.base_id == _BASE_ID
    assert citation.record_generation == 1
    assert citation.block_ids == ("paragraph-1",)
    assert citation.unit_ids == ()
    assert citation.anchor_segment_ids == ()
    assert citation.canonical_text_start_utf16 == 0
    assert citation.canonical_text_end_utf16 == _P1E_V1_BASE_TEXT_UTF16_LEN

    # P1-E-R1: full V1 golden coverage — complete plan / chunk /
    # citation / metadata_json field table with strict dict ``==``
    # (no issubset, no production helper, no algorithm re-implementation).
    # Extra-key / missing-key metadata drift MUST fail.
    _assert_p1e_v1_golden_fields(plan)


async def test_p1e_tracer_bullet_index_version_kwarg_accepted(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E Step 2: tracer bullet RED.

    The plan service public API must accept an ``index_version`` keyword
    argument so bootstrap / worker can pass their already-frozen /
    validated ``index_version`` through.  The current API does NOT
    accept this kwarg, so this test records the real RED.
    """
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    # The tracer bullet: passing ``index_version`` must be accepted.
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    )
    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION


async def test_p1e_v1_default_omitted_equals_explicit_v1(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: omitting ``index_version`` MUST produce the same V1 plan
    as explicitly passing ``DEFAULT_ARTICLE_RAG_INDEX_VERSION``.

    Equivalent across chunker_version, plan_content_sha256, chunk_count,
    chunk_ids, content_sha256, embedding_text_sha256, and citation
    fields.
    """
    # Two independent schemas so the two plans do not collide.
    schema_a = f"test_i4a_p1e_a_{uuid4().hex}"
    schema_b = f"test_i4a_p1e_b_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        for schema in (schema_a, schema_b):
            await admin_conn.execute(f'CREATE SCHEMA "{schema}"')
            await admin_conn.execute(f'SET search_path TO "{schema}", public')
            await admin_conn.execute(INDEX_PLAN_SCHEMA_SQL)
        pool_a = await _make_pool(schema_a)
        pool_b = await _make_pool(schema_b)
        try:
            await _p1e_seed_minimal_v1_env(pool_a)
            await _p1e_seed_minimal_v1_env(pool_b)

            plan_default = await _build_service(pool_a).build_index_plan(
                record_id=_RECORD_ID,
                user_id=_USER_ID,
            )
            plan_explicit = await _build_service(pool_b).build_index_plan(
                record_id=_RECORD_ID,
                user_id=_USER_ID,
                index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
            )

            assert plan_default.chunker_version == plan_explicit.chunker_version
            assert (
                compute_plan_content_sha256(plan_default)
                == compute_plan_content_sha256(plan_explicit)
            )
            assert len(plan_default.chunks) == len(plan_explicit.chunks)
            for c_default, c_explicit in zip(
                plan_default.chunks, plan_explicit.chunks, strict=True
            ):
                assert c_default.chunk_id == c_explicit.chunk_id
                assert c_default.content_sha256 == c_explicit.content_sha256
                assert (
                    c_default.embedding_text_sha256
                    == c_explicit.embedding_text_sha256
                )
                assert c_default.citation == c_explicit.citation
        finally:
            await pool_a.close()
            await pool_b.close()
    finally:
        for schema in (schema_a, schema_b):
            await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin_conn.close()


async def test_p1e_explicit_v1_matches_characterization(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: explicit V1 call MUST reproduce the V1 characterization
    frozen literals."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    )

    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.chunk_id == _P1E_V1_CHUNK_ID
    assert chunk.content_sha256 == _P1E_V1_CONTENT_SHA256
    assert chunk.embedding_text_sha256 == _P1E_V1_EMBEDDING_TEXT_SHA256
    assert chunk.citation.block_ids == ("paragraph-1",)
    assert chunk.citation.unit_ids == ()
    assert chunk.citation.anchor_segment_ids == ()
    assert chunk.citation.canonical_text_start_utf16 == 0
    assert chunk.citation.canonical_text_end_utf16 == _P1E_V1_BASE_TEXT_UTF16_LEN

    # P1-E-R1: explicit V1 characterization must cover the same key
    # fields as the baseline (single test-side expected literal
    # fixture, no production algorithm copy).
    _assert_p1e_v1_golden_fields(plan)


async def test_p1e_unknown_index_version_fails_closed_no_fallback(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: unknown ``index_version`` MUST fail closed via the public
    resolver; no fallback to V1, no DB writes, no plan construction."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    unknown_version = "article_rag_index_v999"
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=unknown_version,
        )

    err = exc_info.value
    # The offending input MUST NOT be echoed in any surface.
    assert unknown_version not in str(err)
    assert unknown_version not in repr(err)


async def test_p1e_malicious_index_version_not_in_surfaces(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: malicious ``index_version`` (whitespace-padded, sentinel,
    HTML/script payload) MUST NOT appear in str / repr / traceback."""
    import traceback as tb_module

    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    malicious_inputs = [
        " article_rag_index_v1",  # leading whitespace
        "article_rag_index_v1\n",  # trailing newline
        "<script>alert('xss')</script>",
        "article_rag_index_v2_malicious_sentinel_DO_NOT_LEAK",
    ]
    for malicious in malicious_inputs:
        with pytest.raises(ArticleRagIndexPlanError) as exc_info:
            await service.build_index_plan(
                record_id=_RECORD_ID,
                user_id=_USER_ID,
                index_version=malicious,
            )
        err = exc_info.value
        err_str = str(err)
        err_repr = repr(err)
        err_tb = "".join(
            tb_module.format_exception(type(err), err, err.__traceback__)
        )
        assert malicious not in err_str, (
            f"malicious input leaked into str: {err_str!r}"
        )
        assert malicious not in err_repr, (
            f"malicious input leaked into repr: {err_repr!r}"
        )
        assert malicious not in err_tb, (
            f"malicious input leaked into traceback: {err_tb!r}"
        )


async def test_p1e_resolver_wrapper_closes_exception_chain(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: the plan service resolver wrapper MUST close the
    exception chain.  The outer ``ArticleRagIndexPlanError`` must have
    ``__cause__ is None`` AND ``__context__ is None``.

    Triggered via the public ``build_index_plan`` seam.
    """
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v999_unknown",
        )
    err = exc_info.value
    assert err.__cause__ is None, (
        f"__cause__ must be None, got {err.__cause__!r}"
    )
    assert err.__context__ is None, (
        f"__context__ must be None, got {err.__context__!r}"
    )


async def test_p1e_plan_service_is_read_only(
    index_env: asyncpg.Pool,
) -> None:
    """P1-E: the plan service MUST be read-only.  It never writes to
    the database, never calls embedding providers, never calls vector
    writers.  Verified by inspecting the public API surface and
    asserting no DB writes occur during plan construction.
    """
    await _p1e_seed_minimal_v1_env(index_env)

    # Snapshot all tables the plan service could conceivably touch.
    tables_to_check = [
        "reader_article_rag_index_runs",
        "reader_jobs",
        "reader_runs",
        "reader_job_events",
    ]
    pre_counts: dict[str, int] = {}
    async with index_env.acquire() as conn:
        for table in tables_to_check:
            pre_counts[table] = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table}"
            )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    )
    assert len(plan.chunks) == 1

    # No DB writes occurred.
    async with index_env.acquire() as conn:
        for table in tables_to_check:
            post_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table}"
            )
            assert post_count == pre_counts[table], (
                f"Plan service wrote to {table}: "
                f"{pre_counts[table]} -> {post_count}"
            )


# ===================================================================
# P1-E-R1: explicit None fail-closed + non-string matrix
# ===================================================================


async def test_p1e_r1_explicit_none_index_version_fails_closed_before_db_read() -> None:
    """P1-E-R1: explicit ``index_version=None`` MUST fail closed at the
    resolver seam BEFORE any truth-layer read.

    Before fix (real RED): ``None`` is normalized to
    ``DEFAULT_ARTICLE_RAG_INDEX_VERSION`` inside the function body, the
    resolver succeeds, and the plan service proceeds to a truth-layer
    read on the probe connection.  The probe raises ``AssertionError``
    — wrong error type, so ``pytest.raises(ArticleRagIndexPlanError)``
    fails.  This is the real RED recorded for this round.

    After fix: ``None`` flows directly to the resolver, which rejects
    non-string inputs with ``ArticleRagIndexProfileResolutionError``;
    the wrapper raises ``ArticleRagIndexPlanError`` with a fixed local
    message.  The probe connection's ``call_count`` MUST be 0, proving
    the plan service never reached the truth layer.

    Contract:
      * raises ``ArticleRagIndexPlanError``
      * fixed safe message (no echo of None)
      * ``__cause__ is None`` AND ``__context__ is None``
      * ``probe.call_count == 0``
      * "None" does not appear in str / repr / args / traceback
    """
    import traceback as tb_module

    probe = _P1ER1ProbeConnection()
    service = ArticleRagIndexPlanService(pool=None)
    # Wrap the sentinel in a variable so the literal ``None`` does not
    # appear at the call site (and therefore not in the traceback
    # source line display).
    sentinel = None
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan_in_transaction(
            probe,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=sentinel,
        )
    err = exc_info.value
    # Fixed safe message — no echo of the offending input.
    assert str(err) == _P1E_R1_EXPECTED_FIXED_MESSAGE, (
        f"unexpected error message: {str(err)!r}"
    )
    assert err.args == (_P1E_R1_EXPECTED_FIXED_MESSAGE,), (
        f"unexpected error args: {err.args!r}"
    )
    # Exception chain closure — both chain attributes MUST be None.
    assert err.__cause__ is None, (
        f"__cause__ must be None, got {err.__cause__!r}"
    )
    assert err.__context__ is None, (
        f"__context__ must be None, got {err.__context__!r}"
    )
    # DB call_count == 0 — the plan service never reached the truth
    # layer.
    assert probe.call_count == 0, (
        f"probe was called {probe.call_count} times; plan service "
        f"reached the truth layer before the resolver rejected None."
    )
    # Sentinel leak check across all resolver-controlled error
    # surfaces.  ``str(None)`` is "None"; the fixed local message
    # contains no "None", and the traceback source line shows
    # ``index_version=sentinel`` (not the literal).  Only the
    # exception message line of the traceback is checked — the full
    # traceback naturally contains source code lines, file paths, and
    # line numbers that are NOT resolver-echoed content.
    err_str = str(err)
    err_repr = repr(err)
    tb_lines = tb_module.format_exception(
        type(err), err, err.__traceback__
    )
    exception_message_line = tb_lines[-1] if tb_lines else ""
    assert "None" not in err_str, (
        f"'None' leaked into str: {err_str!r}"
    )
    assert "None" not in err_repr, (
        f"'None' leaked into repr: {err_repr!r}"
    )
    assert "None" not in exception_message_line, (
        f"'None' leaked into exception message line: "
        f"{exception_message_line!r}"
    )


async def test_p1e_r1_non_string_index_version_matrix_fails_closed() -> None:
    """P1-E-R1: every non-string ``index_version`` MUST fail closed at
    the resolver seam BEFORE any truth-layer read.

    Matrix (all through the public ``build_index_plan_in_transaction``
    seam):
      * ``None``      — currently normalized to V1 (real RED)
      * ``True``      — bool, must not be accepted as 1
      * ``1``         — int, must not be str-coerced
      * ``1.5``       — float, must not be str-coerced
      * ``[]``        — list, must not be str-coerced to "[]"
      * ``{}``        — dict, must not be str-coerced to "{}"
      * ``object()``  — arbitrary object

    For each sentinel:
      * raises ``ArticleRagIndexPlanError``
      * fixed safe message (no echo, no str-coerce, no normalize)
      * ``__cause__ is None`` AND ``__context__ is None``
      * ``probe.call_count == 0`` (fails before any truth-layer read)
      * no fallback to V1
      * ``str(sentinel)`` and ``repr(sentinel)`` do not appear in
        str / repr / args of the error, nor in the exception message
        line of the traceback (the resolver-controlled surface).  The
        full traceback naturally contains source code lines, file
        paths, and line numbers that may incidentally contain short
        markers like "1"; those are NOT resolver-echoed content.
    """
    import traceback as tb_module

    # Matrix of non-string sentinels.  Each entry is the sentinel
    # value itself; leak markers are derived from ``str(sentinel)`` and
    # ``repr(sentinel)``.  ``None`` is included to close the
    # normalization gap; the other entries characterize the
    # fail-closed behavior for non-string types.
    matrix: list = [
        None,
        True,
        1,
        1.5,
        [],
        {},
        object(),
    ]

    for sentinel in matrix:
        probe = _P1ER1ProbeConnection()
        service = ArticleRagIndexPlanService(pool=None)
        with pytest.raises(ArticleRagIndexPlanError) as exc_info:
            await service.build_index_plan_in_transaction(
                probe,
                record_id=_RECORD_ID,
                user_id=_USER_ID,
                index_version=sentinel,
            )
        err = exc_info.value
        # Fixed safe message — no echo, no str-coerce, no normalize.
        assert str(err) == _P1E_R1_EXPECTED_FIXED_MESSAGE, (
            f"sentinel={sentinel!r} unexpected error message: {str(err)!r}"
        )
        assert err.args == (_P1E_R1_EXPECTED_FIXED_MESSAGE,), (
            f"sentinel={sentinel!r} unexpected error args: {err.args!r}"
        )
        # Exception chain closure.
        assert err.__cause__ is None, (
            f"sentinel={sentinel!r} __cause__ must be None, "
            f"got {err.__cause__!r}"
        )
        assert err.__context__ is None, (
            f"sentinel={sentinel!r} __context__ must be None, "
            f"got {err.__context__!r}"
        )
        # Fails before any truth-layer read.
        assert probe.call_count == 0, (
            f"sentinel={sentinel!r} probe was called "
            f"{probe.call_count} times; plan service reached the truth "
            f"layer before the resolver rejected the non-string input."
        )
        # Sentinel leak check across all resolver-controlled error
        # surfaces: str / repr / args of the error, plus the exception
        # message line of the traceback (the final
        # "ExceptionType: message" line that the resolver is
        # responsible for).  The full traceback naturally contains
        # source code lines, file paths, and line numbers that may
        # incidentally contain short markers like "1" (e.g. function
        # name ``test_p1e_r1_...``, line numbers); those are NOT
        # resolver-echoed content and are excluded from the check.
        err_str = str(err)
        err_repr = repr(err)
        # Extract only the exception message line from the traceback
        # (the final "ExceptionType: message" line).  This is the
        # resolver-controlled surface; source lines and line numbers
        # are excluded.
        tb_lines = tb_module.format_exception(
            type(err), err, err.__traceback__
        )
        exception_message_line = tb_lines[-1] if tb_lines else ""
        leak_markers: list[str] = []
        try:
            leak_markers.append(str(sentinel))
        except Exception:
            pass
        try:
            leak_markers.append(repr(sentinel))
        except Exception:
            pass
        for marker in leak_markers:
            if not marker:
                continue
            assert marker not in err_str, (
                f"sentinel={sentinel!r} marker {marker!r} leaked "
                f"into str: {err_str!r}"
            )
            assert marker not in err_repr, (
                f"sentinel={sentinel!r} marker {marker!r} leaked "
                f"into repr: {err_repr!r}"
            )
            assert marker not in exception_message_line, (
                f"sentinel={sentinel!r} marker {marker!r} leaked "
                f"into exception message line: "
                f"{exception_message_line!r}"
            )


# ===================================================================
# P1-E-R1: unsupported identity public seam coverage
# ===================================================================


def _build_p1e_r1_fake_resolution(
    *,
    plan_version: str = CHUNKER_VERSION,
    chunker_version: str = CHUNKER_VERSION,
) -> ArticleRagIndexProfileResolution:
    """Build a fake ``ArticleRagIndexProfileResolution`` from public
    P1-A / P1-B value objects with an internally consistent
    fingerprint.

    Used by the unsupported identity tests to monkeypatch the plan
    module's ``resolve_article_rag_index_profile`` collaborator.  The
    fake resolver succeeds, but the resolved profile's
    ``plan_version`` or ``chunker_version`` is intentionally set to an
    unsupported literal — the plan service's dispatch guard must
    fail-closed.

    The fingerprint is computed via the public
    :func:`compute_article_rag_index_profile_fingerprint`, so the
    resolution construction itself passes the public P1-B invariant
    check (``profile_fingerprint`` must equal the canonical
    fingerprint of ``profile``).
    """
    profile = ArticleRagIndexProfile(
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        plan_version=plan_version,
        chunker_version=chunker_version,
        document_embedding_model="text-embedding-v4",
        document_embedding_dimension=1024,
        document_embedding_text_type="provider_default",
        query_embedding_model="text-embedding-v4",
        query_embedding_text_type="provider_default",
        vector_namespace="article_rag_index_v1",
        retrieval_schema_version="article_rag_retrieval_v1",
        citation_mode_version="article_rag_citation_v1",
    )
    fingerprint = compute_article_rag_index_profile_fingerprint(profile)
    return ArticleRagIndexProfileResolution(
        profile=profile,
        profile_fingerprint=fingerprint,
    )


# Fixed non-sensitive substitute literals used by the unsupported
# identity tests.  These are intentionally distinct from
# ``CHUNKER_VERSION`` so the dispatch guard rejects them, and they
# are never echoed in any error surface.
_P1E_R1_UNSUPPORTED_PLAN_VERSION_LITERAL = (
    "article_rag_index_plan_v1_test_unsupported_plan"
)
_P1E_R1_UNSUPPORTED_CHUNKER_VERSION_LITERAL = (
    "article_rag_index_plan_v1_test_unsupported_chunker"
)


async def test_p1e_r1_resolved_unsupported_plan_version_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-E-R1: resolver succeeds with an unsupported ``plan_version``
    → dispatch MUST fail closed.

    The fake resolver returns a public P1-A / P1-B value object with
    an internally consistent fingerprint, but its ``plan_version`` is
    set to a fixed unsupported literal (``chunker_version`` remains
    the supported V1 identity).  The plan service's dispatch guard
    MUST raise ``ArticleRagIndexPlanError`` with a fixed local message;
    the offending identity is never echoed.

    Contract:
      * raises ``ArticleRagIndexPlanError``
      * fixed safe message (no echo of the unsupported literal)
      * ``__cause__ is None`` AND ``__context__ is None``
      * ``probe.call_count == 0``
      * no V2 registration, no runtime override / registry mutation
    """
    import traceback as tb_module

    fake_resolution = _build_p1e_r1_fake_resolution(
        plan_version=_P1E_R1_UNSUPPORTED_PLAN_VERSION_LITERAL,
        chunker_version=CHUNKER_VERSION,
    )

    def fake_resolver(_index_version: str) -> ArticleRagIndexProfileResolution:
        return fake_resolution

    monkeypatch.setattr(
        plan_module,
        "resolve_article_rag_index_profile",
        fake_resolver,
    )

    probe = _P1ER1ProbeConnection()
    service = ArticleRagIndexPlanService(pool=None)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan_in_transaction(
            probe,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        )
    err = exc_info.value
    assert str(err) == _P1E_R1_EXPECTED_FIXED_MESSAGE, (
        f"unexpected error message: {str(err)!r}"
    )
    assert err.args == (_P1E_R1_EXPECTED_FIXED_MESSAGE,), (
        f"unexpected error args: {err.args!r}"
    )
    assert err.__cause__ is None, (
        f"__cause__ must be None, got {err.__cause__!r}"
    )
    assert err.__context__ is None, (
        f"__context__ must be None, got {err.__context__!r}"
    )
    assert probe.call_count == 0, (
        f"probe was called {probe.call_count} times; plan service "
        f"reached the truth layer despite the unsupported plan_version."
    )
    # Unsupported literal must not appear in any resolver-controlled
    # error surface (str / repr / args / exception message line).
    err_str = str(err)
    err_repr = repr(err)
    tb_lines = tb_module.format_exception(
        type(err), err, err.__traceback__
    )
    exception_message_line = tb_lines[-1] if tb_lines else ""
    assert _P1E_R1_UNSUPPORTED_PLAN_VERSION_LITERAL not in err_str, (
        f"unsupported plan_version leaked into str: {err_str!r}"
    )
    assert _P1E_R1_UNSUPPORTED_PLAN_VERSION_LITERAL not in err_repr, (
        f"unsupported plan_version leaked into repr: {err_repr!r}"
    )
    assert _P1E_R1_UNSUPPORTED_PLAN_VERSION_LITERAL not in exception_message_line, (
        f"unsupported plan_version leaked into exception message line: "
        f"{exception_message_line!r}"
    )


async def test_p1e_r1_resolved_unsupported_chunker_version_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-E-R1: resolver succeeds with an unsupported
    ``chunker_version`` → dispatch MUST fail closed.

    The fake resolver returns a public P1-A / P1-B value object with
    an internally consistent fingerprint, but its ``chunker_version``
    is set to a fixed unsupported literal (``plan_version`` remains
    the supported V1 identity).  The plan service's dispatch guard
    MUST raise ``ArticleRagIndexPlanError`` with a fixed local message;
    the offending identity is never echoed.

    Contract:
      * raises ``ArticleRagIndexPlanError``
      * fixed safe message (no echo of the unsupported literal)
      * ``__cause__ is None`` AND ``__context__ is None``
      * ``probe.call_count == 0``
      * no V2 registration, no runtime override / registry mutation
    """
    import traceback as tb_module

    fake_resolution = _build_p1e_r1_fake_resolution(
        plan_version=CHUNKER_VERSION,
        chunker_version=_P1E_R1_UNSUPPORTED_CHUNKER_VERSION_LITERAL,
    )

    def fake_resolver(_index_version: str) -> ArticleRagIndexProfileResolution:
        return fake_resolution

    monkeypatch.setattr(
        plan_module,
        "resolve_article_rag_index_profile",
        fake_resolver,
    )

    probe = _P1ER1ProbeConnection()
    service = ArticleRagIndexPlanService(pool=None)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan_in_transaction(
            probe,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
        )
    err = exc_info.value
    assert str(err) == _P1E_R1_EXPECTED_FIXED_MESSAGE, (
        f"unexpected error message: {str(err)!r}"
    )
    assert err.args == (_P1E_R1_EXPECTED_FIXED_MESSAGE,), (
        f"unexpected error args: {err.args!r}"
    )
    assert err.__cause__ is None, (
        f"__cause__ must be None, got {err.__cause__!r}"
    )
    assert err.__context__ is None, (
        f"__context__ must be None, got {err.__context__!r}"
    )
    assert probe.call_count == 0, (
        f"probe was called {probe.call_count} times; plan service "
        f"reached the truth layer despite the unsupported chunker_version."
    )
    # Unsupported literal must not appear in any resolver-controlled
    # error surface (str / repr / args / exception message line).
    err_str = str(err)
    err_repr = repr(err)
    tb_lines = tb_module.format_exception(
        type(err), err, err.__traceback__
    )
    exception_message_line = tb_lines[-1] if tb_lines else ""
    assert _P1E_R1_UNSUPPORTED_CHUNKER_VERSION_LITERAL not in err_str, (
        f"unsupported chunker_version leaked into str: {err_str!r}"
    )
    assert _P1E_R1_UNSUPPORTED_CHUNKER_VERSION_LITERAL not in err_repr, (
        f"unsupported chunker_version leaked into repr: {err_repr!r}"
    )
    assert _P1E_R1_UNSUPPORTED_CHUNKER_VERSION_LITERAL not in exception_message_line, (
        f"unsupported chunker_version leaked into exception message line: "
        f"{exception_message_line!r}"
    )
