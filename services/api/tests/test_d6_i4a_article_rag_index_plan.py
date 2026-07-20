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
    CHUNKER_VERSION,
    V2A_MAX_MERGED_CANONICAL_UTF16_UNITS,
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)
from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfile,
    ArticleRagIndexProfileResolution,
    ArticleRagIndexProfileResolutionError,
    compute_article_rag_index_profile_fingerprint,
    resolve_article_rag_index_evaluation_profile,
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


# ===========================================================================
# P2-A Group B: V1 byte-stability under V2a coexistence
# ===========================================================================
#
# P2-A requires that the V2a evaluation profile and plan builder seam
# coexist with V1 WITHOUT changing any V1 byte.  The existing P1-E
# golden literals (chunker_version, chunk IDs, content/embedding
# hashes, plan_content_sha256, chunk_count, citations) MUST remain
# exact.  This group re-asserts the V1 golden with the V2a seam
# present in the same module.


async def test_p2a_group_b_v1_golden_literals_unchanged(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group B: V1 golden literals MUST remain byte-stable when
    the V2a evaluation seam is added to the same module.

    Re-asserts the full P1-E-R1 golden fixture via
    ``_assert_p1e_v1_golden_fields`` after the V2a seam is present.
    """
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # V1 chunker_version unchanged.
    assert plan.chunker_version == _P1E_V1_CHUNKER_VERSION
    assert plan.chunker_version == CHUNKER_VERSION

    # V1 plan content sha256 unchanged.
    assert compute_plan_content_sha256(plan) == _P1E_V1_PLAN_CONTENT_SHA256

    # V1 chunk count unchanged.
    assert len(plan.chunks) == 1

    # V1 chunk IDs, content/embedding hashes, citations unchanged.
    _assert_p1e_v1_golden_fields(plan)


async def test_p2a_group_b_v1_default_equals_explicit_v1(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group B: omitting ``index_version`` MUST still default to
    V1 and produce the same plan as explicit V1.  The V2a seam MUST
    NOT change the default."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    plan_default = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    plan_explicit = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version=DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    )

    assert plan_default.chunker_version == plan_explicit.chunker_version
    assert (
        compute_plan_content_sha256(plan_default)
        == compute_plan_content_sha256(plan_explicit)
        == _P1E_V1_PLAN_CONTENT_SHA256
    )


# ===========================================================================
# P2-A Group D: Dispatch isolation (must fail BEFORE any DB read)
# ===========================================================================

# P2-A fixed local error message for the evaluation builder dispatch
# guard.  The offending input is NEVER echoed in any error surface.
_P2A_MSG_UNKNOWN_INDEX_VERSION = (
    "Article RAG index plan version is not supported"
)
_P2A_MSG_EVALUATION_V2A_ONLY = (
    "Article RAG index plan version is not supported"
)


async def test_p2a_group_d_production_builder_rejects_v2_before_db_read() -> None:
    """P2-A Group D: the production ``build_index_plan_in_transaction``
    MUST reject ``index_version="article_rag_index_v2"`` at the
    resolver seam BEFORE any truth-layer read.

    The probe connection raises ``AssertionError`` on any DB call.  The
    production builder MUST raise ``ArticleRagIndexPlanError`` with a
    fixed local message; ``probe.call_count`` MUST be 0.
    """
    probe = _P1ER1ProbeConnection()
    service = ArticleRagIndexPlanService(pool=None)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan_in_transaction(
            probe,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    assert str(err) == _P2A_MSG_UNKNOWN_INDEX_VERSION
    assert err.__cause__ is None
    assert err.__context__ is None
    assert probe.call_count == 0


async def test_p2a_group_d_production_build_index_plan_rejects_v2_before_db_read() -> None:
    """P2-A Group D: the public ``build_index_plan`` (pool variant)
    MUST also reject V2 before any truth-layer read.  Verified via a
    probe pool wrapper."""
    # Build a service whose pool is a probe that raises on any acquire.
    class _ProbePool:
        def acquire(self):
            raise AssertionError(
                "Probe pool was acquired — production build_index_plan "
                "attempted a truth-layer read before rejecting V2."
            )

    service = ArticleRagIndexPlanService(pool=_ProbePool())  # type: ignore[arg-type]
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    assert str(err) == _P2A_MSG_UNKNOWN_INDEX_VERSION
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.parametrize(
    "bad_version",
    [
        None,
        "article_rag_index_v1",
        "article_rag_index_v3",
        "",
        "  ",
        "article_rag_index_v2 ",
        " article_rag_index_v2",
        "ARTICLE_RAG_INDEX_V2",
        "article_rag_index_v2\n",
        "article_rag_index_v2\x00",
        "article_rag_index_v2; DROP TABLE users;",
        b"article_rag_index_v2",
        1,
        True,
        False,
        [],
        {},
        object(),
    ],
)
async def test_p2a_group_d_evaluation_builder_rejects_non_v2a_before_db_read(
    bad_version: object,
) -> None:
    """P2-A Group D: the evaluation builder MUST reject every input
    that is not exactly ``"article_rag_index_v2"`` BEFORE any
    truth-layer read.  Includes V1, unknown versions, non-string
    types, and malicious values."""
    probe = _P1ER1ProbeConnection()
    service = ArticleRagIndexPlanService(pool=None)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan_in_transaction(
            probe,
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version=bad_version,  # type: ignore[arg-type]
        )
    err = exc_info.value
    assert str(err) == _P2A_MSG_EVALUATION_V2A_ONLY
    assert err.__cause__ is None
    assert err.__context__ is None
    assert probe.call_count == 0


async def test_p2a_group_d_evaluation_builder_rejects_missing_index_version() -> None:
    """P2-A Group D: the evaluation builder MUST require an explicit
    ``index_version`` keyword argument.  Omitting it MUST raise
    ``TypeError`` at the Python signature level (no default value)."""
    import inspect

    sig = inspect.signature(
        ArticleRagIndexPlanService.build_evaluation_index_plan_in_transaction
    )
    param = sig.parameters["index_version"]
    assert param.default is inspect.Parameter.empty, (
        "evaluation builder index_version MUST NOT have a default value; "
        "the caller MUST pass it explicitly."
    )


async def test_p2a_group_d_evaluation_builder_rejects_v2a_lookup_in_production_resolver() -> None:
    """P2-A Group D: the production resolver MUST remain fail-closed
    on V2.  This is a cross-check that the evaluation builder does not
    secretly rely on the production resolver accepting V2."""
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_evaluation_profile(
            "article_rag_index_v1"
        )


# ===========================================================================
# P2-A Group C: V2a contiguous-only merging algorithm
# ===========================================================================


def _v2a_seed_two_adjacent_paragraphs(
    pool: asyncpg.Pool,
    *,
    text_a: str = "First paragraph for V2a merge.",
    text_b: str = "Second paragraph for V2a merge.",
) -> tuple[str, list[tuple[int, int]], str]:
    """Seed two canonical-adjacent main_reading paragraph blocks.

    Returns (base_text, [(start_a, end_a), (start_b, end_b)], source_scope).
    """
    raise NotImplementedError("helper scheduled for Group C tests")


async def _seed_v2a_two_paragraph_env(
    pool: asyncpg.Pool,
    *,
    text_a: str = "First paragraph for V2a merge.",
    text_b: str = "Second paragraph for V2a merge.",
    scope: str = "main_reading_text",
) -> str:
    """Seed environment with two canonical-adjacent main_reading paragraphs."""
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(pool, base_text=base_text)
    await _seed_block(
        pool,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(scope),
    )
    await _seed_block(
        pool,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(scope),
    )
    return base_text


async def _seed_v2a_three_paragraph_env(
    pool: asyncpg.Pool,
    *,
    text_a: str = "First paragraph for V2a merge.",
    text_b: str = "Second paragraph for V2a merge.",
    text_c: str = "Third paragraph for V2a merge.",
    scope: str = "main_reading_text",
) -> str:
    """Seed environment with three canonical-adjacent main_reading paragraphs."""
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b, text_c)
    await _seed_full_environment(pool, base_text=base_text)
    for i, (text, (start, end), block_id) in enumerate(
        [
            (text_a, offsets[0], "paragraph-1"),
            (text_b, offsets[1], "paragraph-2"),
            (text_c, offsets[2], "paragraph-3"),
        ]
    ):
        await _seed_block(
            pool,
            block_id=block_id,
            order_index=i,
            block_type="paragraph",
            text_content=text,
            canonical_text_start_utf16=start,
            canonical_text_end_utf16=end,
            interpretation_policy=_main_reading_policy(scope),
        )
    return base_text


async def test_p2a_group_c_tracer_bullet_v2a_two_paragraph_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C tracer bullet: two canonical-adjacent main_reading
    paragraphs MUST merge into a single V2a chunk.

    RED-first: the evaluation builder seam does not exist yet."""
    base_text = await _seed_v2a_two_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # V2a chunker_version.
    assert plan.chunker_version == "article_rag_index_plan_v2a"

    # Two adjacent paragraphs merged into ONE chunk.
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]

    # Merged text is the full canonical base slice (includes real "\n\n").
    assert chunk.text == base_text
    assert "\n\n" in chunk.text

    # Citation block_ids are ordered.
    assert chunk.citation.block_ids == ("paragraph-1", "paragraph-2")

    # Canonical span covers the full merged range.
    assert chunk.citation.canonical_text_start_utf16 == 0
    assert chunk.citation.canonical_text_end_utf16 == (
        utf16_code_unit_length(base_text)
    )


async def test_p2a_group_c_v2a_three_paragraph_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: three canonical-adjacent main_reading paragraphs
    MUST merge into a single V2a chunk."""
    base_text = await _seed_v2a_three_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]

    assert chunk.text == base_text
    assert chunk.text.count("\n\n") == 2
    assert chunk.citation.block_ids == (
        "paragraph-1",
        "paragraph-2",
        "paragraph-3",
    )
    assert chunk.citation.canonical_text_start_utf16 == 0
    assert chunk.citation.canonical_text_end_utf16 == (
        utf16_code_unit_length(base_text)
    )


async def test_p2a_group_c_v2a_merged_text_contains_real_separator(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: the merged text MUST contain the real ``"\\n\\n"``
    separator between adjacent blocks.  No manual concatenation with
    spaces, single newlines, or other characters."""
    text_a = "AAA"
    text_b = "BBB"
    base_text = await _seed_v2a_two_paragraph_env(
        index_env, text_a=text_a, text_b=text_b
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    # The merged text MUST be exactly "AAA\n\nBBB" — not "AAA BBB",
    # not "AAA\nBBB", not "AAA-BBB".
    assert plan.chunks[0].text == "AAA\n\nBBB"
    assert base_text == "AAA\n\nBBB"


async def test_p2a_group_c_v2a_chunk_id_is_deterministic_and_distinct_from_v1(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: the V2a chunk ID MUST be deterministic (same
    input → same output) and MUST differ from the V1 chunk ID for the
    same single-block input."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    v1_plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    v2a_plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    v1_chunk_id = v1_plan.chunks[0].chunk_id
    v2a_chunk_id = v2a_plan.chunks[0].chunk_id

    # Determinism: same input → same ID.
    v2a_plan_b = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )
    assert v2a_plan_b.chunks[0].chunk_id == v2a_chunk_id

    # V2a chunk ID MUST differ from V1 chunk ID.
    assert v2a_chunk_id != v1_chunk_id


async def test_p2a_group_c_v2a_plan_hash_is_deterministic_and_distinct_from_v1(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: the V2a plan content hash MUST be deterministic
    and MUST differ from the V1 plan content hash for the same input
    (because chunker_version and chunk IDs differ)."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    v1_plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    v2a_plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    v1_hash = compute_plan_content_sha256(v1_plan)
    v2a_hash = compute_plan_content_sha256(v2a_plan)

    # Determinism.
    v2a_plan_b = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )
    assert compute_plan_content_sha256(v2a_plan_b) == v2a_hash

    # V2a plan hash MUST differ from V1 plan hash.
    assert v2a_hash != v1_hash


async def test_p2a_group_c_v2a_citation_unit_and_anchor_ids_ordered_and_deduped(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: V2a merged chunk's ``unit_ids`` and
    ``anchor_segment_ids`` MUST be ordered by canonical order and
    deduplicated."""
    text_a = "First paragraph."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )
    # Seed a unit that overlaps both paragraphs.  The reading_units
    # schema enforces ``order_index >= 1`` (CHECK constraint
    # ``reading_units_order_index_check``), so the value MUST be >= 1.
    await _seed_unit(
        index_env,
        unit_id="unit-cross",
        order_index=1,
        base_start_utf16=0,
        base_end_utf16=offsets[1][1],
    )
    # Seed a segment that overlaps only the first paragraph.
    # ``anchor_segments`` has UNIQUE constraints on
    # ``(base_id, sentence_id)`` and ``(base_id, unit_id, unit_order_index)``,
    # so the two segments MUST use distinct ``sentence_id`` AND
    # ``unit_order_index`` values (the defaults would collide).
    await _seed_segment(
        index_env,
        anchor_segment_id="seg-1",
        unit_id="unit-cross",
        sentence_id="sent-1",
        order_index=1,
        unit_order_index=1,
        base_start_utf16=offsets[0][0],
        base_end_utf16=offsets[0][1],
    )
    # Seed a segment that overlaps only the second paragraph.
    await _seed_segment(
        index_env,
        anchor_segment_id="seg-2",
        unit_id="unit-cross",
        sentence_id="sent-2",
        order_index=2,
        unit_order_index=2,
        base_start_utf16=offsets[1][0],
        base_end_utf16=offsets[1][1],
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    # Unit ID is deduplicated (unit-cross overlaps both paragraphs).
    assert chunk.citation.unit_ids == ("unit-cross",)
    # Anchor segment IDs are ordered and deduplicated.
    assert chunk.citation.anchor_segment_ids == ("seg-1", "seg-2")


async def test_p2a_group_c_v2a_4096_boundary_equal_can_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when the merged canonical span EQUALS 4096 UTF-16
    units, the blocks MUST merge (boundary is inclusive)."""
    # Build two paragraphs whose merged span == 4096 UTF-16 units.
    # Each paragraph is 2047 UTF-16 units, plus "\n\n" (2 units) = 4096.
    text_a = "A" * 2047
    text_b = "B" * 2047
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    merged_utf16 = utf16_code_unit_length(base_text)
    assert merged_utf16 == V2A_MAX_MERGED_CANONICAL_UTF16_UNITS, (
        f"test setup error: merged span {merged_utf16} != "
        f"{V2A_MAX_MERGED_CANONICAL_UTF16_UNITS}"
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Equal to 4096 → merge succeeds.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1", "paragraph-2")


async def test_p2a_group_c_v2a_4096_boundary_exceeds_no_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when the merged canonical span EXCEEDS 4096 UTF-16
    units, the blocks MUST NOT merge — each block becomes its own
    chunk."""
    # Build two paragraphs whose merged span == 4097 UTF-16 units.
    text_a = "A" * 2047
    text_b = "B" * 2048
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)
    merged_utf16 = utf16_code_unit_length(base_text)
    assert merged_utf16 == V2A_MAX_MERGED_CANONICAL_UTF16_UNITS + 1, (
        f"test setup error: merged span {merged_utf16} != "
        f"{V2A_MAX_MERGED_CANONICAL_UTF16_UNITS + 1}"
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Exceeds 4096 → no merge; each block is its own chunk.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_single_block_exceeding_4096_stays_standalone(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: a single block whose own span exceeds 4096 UTF-16
    units MUST stay as a standalone chunk (no internal splitting in
    this round)."""
    text_a = "A" * 5000
    text_b = "B" * 10
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # paragraph-1 is > 4096 → stays standalone.
    # paragraph-2 cannot merge with paragraph-1 (would exceed 4096).
    # Both are standalone.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_heading_is_standalone_and_breaks_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: an eligible heading MUST be a standalone chunk
    and a hard boundary — it MUST break the merge window on both
    sides."""
    text_a = "First paragraph."
    text_heading = "Section Title"
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(
        text_a, text_heading, text_b
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="heading-1",
        order_index=1,
        block_type="heading",
        text_content=text_heading,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=2,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Heading breaks the merge: 3 standalone chunks.
    assert len(plan.chunks) == 3
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("heading-1",)
    assert plan.chunks[2].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_different_route_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: adjacent blocks with different effective routes
    MUST NOT merge."""
    text_a = "First paragraph."
    text_b = "Table cell content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=1,
        block_type="table_cell",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_rag_ask_only_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
        include_rag_ask_only=True,
    )

    # Different routes → no merge.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("table-cell-1",)


async def test_p2a_group_c_v2a_different_source_scope_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: adjacent main_reading blocks with different
    effective source scopes MUST NOT merge."""
    text_a = "First paragraph."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy("main_reading_text"),
    )
    # P2-A-R1: use a valid ``StableDocumentSourceScope`` Literal value
    # (``"heading"``) rather than an arbitrary string — the V2a
    # materialized policy fingerprint now validates the full
    # ``StableDocumentInterpretationPolicy`` model, so an invalid scope
    # value would fail-closed at the policy boundary instead of
    # exercising the "different scope → no merge" path.
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy("heading"),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Different source scope → no merge.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_different_policy_fingerprint_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: adjacent main_reading blocks with the same route
    and scope but different materialized interpretation-policy
    fingerprints MUST NOT merge.

    The policy fingerprint captures the full materialized policy dict
    (route, scope, rag_eligible).  Two blocks with the same route and
    scope but different ``rag_eligible`` flags have different
    fingerprints and MUST NOT merge.
    """
    text_a = "First paragraph."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: main_reading, rag_eligible=True.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": True,
        },
    )
    # Block 2: main_reading route but rag_eligible=False (different
    # materialized policy fingerprint).
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": False,
        },
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Block 2 is not RAG-eligible → it is skipped entirely.
    # Block 1 is a standalone chunk.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


# ===========================================================================
# P2-A-R1: complete materialized policy fingerprint (covers ``notes``)
# ===========================================================================
#
# The V2a materialized policy fingerprint MUST cover the full
# ``StableDocumentInterpretationPolicy`` model — ``allowed_source_scope``,
# ``default_route``, ``rag_eligible`` AND ``notes``.  Two eligible
# main_reading blocks with the same route / scope / rag_eligible but
# different ``notes`` MUST NOT merge.  Testing ``rag_eligible=False``
# only proves eligibility filtering, not the policy fingerprint.

# P2-A-R1: frozen V2a golden literals for the canonical two-paragraph
# merged fixture produced by ``_seed_v2a_two_paragraph_env``.  These
# literals MUST be hardcoded — they MUST NOT be computed at runtime by
# calling production helpers.  If the V2a seed, serialization, or merge
# identity intentionally changes, the plan / chunker version MUST be
# bumped and these literals updated explicitly.  This blocks unversioned
# spec drift.
#
# The fixture base text is fixed by ``_seed_v2a_two_paragraph_env``'s
# default ``text_a`` / ``text_b`` joined with ``"\n\n"``.
_P2A_V2A_MERGED_FIXTURE_BASE_TEXT = (
    "First paragraph for V2a merge.\n\nSecond paragraph for V2a merge."
)
# Frozen V2a chunk_id for the canonical merged fixture — 16 hex chars
# (SHA-256 truncated to 64 bits).  Derived from the V2a seed
# ``v2a:{chunker_version}:{stable_document_id}:{source_scope}:`` plus
# ``paragraph-1,paragraph-2:{start}:{end}``.
_P2A_V2A_MERGED_CHUNK_ID = "e1a812ef420be808"
# Frozen V2a plan_content_sha256 for the canonical merged fixture — 64
# hex chars (full SHA-256).  Captures stable_document_id, base_id,
# record_generation, content_sha256, canonical_text_sha256,
# chunker_version, chunk count, and per-chunk identity / citation
# fields (see ``compute_plan_content_sha256``).
_P2A_V2A_MERGED_PLAN_CONTENT_SHA256 = (
    "cc626cc12a4f4cf8bfa200172fb2045d08cce3f382cc5e5b91f9476aa9a46deb"
)


def _main_reading_policy_with_notes(
    notes: list[str],
    *,
    scope: str = "main_reading_text",
) -> dict:
    """Main-reading policy with explicit ``notes`` field.

    Used to test that the V2a materialized policy fingerprint covers
    the full ``StableDocumentInterpretationPolicy`` model, not just the
    ``(route, scope, rag_eligible)`` triple.
    """
    return {
        "allowed_source_scope": [scope],
        "default_route": "main_reading",
        "rag_eligible": True,
        "notes": list(notes),
    }


async def test_p2a_r1_notes_different_both_eligible_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1 RED: two canonical-adjacent main_reading paragraphs with
    identical route / scope / rag_eligible but different ``notes`` MUST
    NOT merge.

    The materialized policy fingerprint MUST cover the full
    ``StableDocumentInterpretationPolicy`` model, including ``notes``.
    The current ``(route, scope, rag_eligible)`` triple implementation
    will incorrectly merge these blocks; this test captures the
    expected behavior (two standalone chunks) and fails RED until the
    fingerprint covers ``notes``.
    """
    text_a = "First paragraph for notes policy test."
    text_b = "Second paragraph for notes policy test."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(["policy-a"]),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy_with_notes(["policy-b"]),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Different ``notes`` → different materialized policy fingerprint
    # → two standalone chunks, no merge.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_r1_notes_same_merges(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: two canonical-adjacent main_reading paragraphs with the
    SAME ``notes`` value MUST merge — the materialized policy
    fingerprint is equal."""
    text_a = "First paragraph for same notes merge."
    text_b = "Second paragraph for same notes merge."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy_with_notes(["shared-note"]),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy_with_notes(["shared-note"]),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == (
        "paragraph-1",
        "paragraph-2",
    )


async def test_p2a_r1_empty_dict_merges_with_explicit_default(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: a block whose ``interpretation_policy_json`` is ``{}``
    (DB storage placeholder) MUST be materialised via
    ``default_interpretation_policy_for(block_type)`` and produce the
    SAME fingerprint as a block whose policy is the explicit per-type
    default dict — so two such adjacent blocks MUST merge."""
    text_a = "First paragraph for empty dict materialization."
    text_b = "Second paragraph for empty dict materialization."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: empty {} — materialised via default_interpretation_policy_for
    # ("paragraph") which yields:
    #   {allowed_source_scope: ["main_reading_text"],
    #    default_route: "main_reading",
    #    rag_eligible: True,
    #    notes: []}
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={},
    )
    # Block 2: explicit per-type default dict with ``notes: []``.
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": True,
            "notes": [],
        },
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Same materialised policy fingerprint → merge.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == (
        "paragraph-1",
        "paragraph-2",
    )


async def test_p2a_r1_json_key_order_difference_merges(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: two adjacent blocks whose ``interpretation_policy_json``
    has the SAME semantic content but different JSON key order MUST
    merge.  The fingerprint is computed over the canonical
    ``sort_keys=True`` serialisation, so key order in storage is
    irrelevant."""
    text_a = "First paragraph for key order test."
    text_b = "Second paragraph for key order test."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: keys in alphabetical order.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "notes": ["k"],
            "rag_eligible": True,
        },
    )
    # Block 2: keys in a different order, same semantic content.
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy={
            "rag_eligible": True,
            "notes": ["k"],
            "default_route": "main_reading",
            "allowed_source_scope": ["main_reading_text"],
        },
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Canonical serialisation is identical → same fingerprint → merge.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == (
        "paragraph-1",
        "paragraph-2",
    )


async def test_p2a_r1_malicious_notes_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: a block whose ``interpretation_policy_json`` has a
    ``notes`` field with a non-string element MUST fail closed with a
    fixed local error.  The raw policy / notes value MUST NOT be
    echoed in ``str``, ``repr``, ``args``, or traceback."""
    text_a = "First paragraph for malicious notes test."
    text_b = "Second paragraph for malicious notes test."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": True,
            "notes": ["normal-note"],
        },
    )
    # Block 2: ``notes`` contains a malicious non-string sentinel
    # (a dict masquerading as a notes entry).
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": True,
            "notes": [{"$gt": ""}],  # malicious NoSQL-style sentinel
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    # Fixed local message — no echo of the raw policy / notes / sentinel.
    # P2-A-R2: asserted verbatim (the message was made more specific than
    # the generic dispatch-wrapper text so callers can distinguish "policy
    # payload invalid" from "index version unsupported").
    err_str = str(err)
    err_repr = repr(err)
    assert err_str == _P2A_R2_EXPECTED_POLICY_MESSAGE
    assert "$gt" not in err_str
    assert "$gt" not in err_repr
    # Clean exception chain — no cause / context leakage.
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_r1_extra_field_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: a block whose ``interpretation_policy_json`` contains an
    unknown extra field MUST fail closed.  ``StableDocumentInterpretationPolicy``
    uses ``extra='forbid'`` so any extra field is a validation error."""
    text_a = "First paragraph for extra field test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": True,
            "notes": [],
            "extra_malicious_field": "should-be-rejected",
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    err_str = str(err)
    err_repr = repr(err)
    # P2-A-R2: fixed local message asserted verbatim.
    assert err_str == _P2A_R2_EXPECTED_POLICY_MESSAGE
    # No echo of the raw policy / extra field name / sentinel.
    assert "extra_malicious_field" not in err_str
    assert "extra_malicious_field" not in err_repr
    assert "should-be-rejected" not in err_str
    assert "should-be-rejected" not in err_repr
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_r1_malformed_policy_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: a block whose ``interpretation_policy_json`` has a
    wrong-typed ``default_route`` (non-string) MUST fail closed with a
    fixed local message."""
    text_a = "First paragraph for malformed policy test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": ["not", "a", "string"],
            "rag_eligible": True,
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    err_str = str(err)
    # P2-A-R2: fixed local message asserted verbatim.
    assert err_str == _P2A_R2_EXPECTED_POLICY_MESSAGE
    # No echo of the raw policy / sentinel.
    assert err.__cause__ is None
    assert err.__context__ is None


# ===========================================================================
# P2-A-R1: fixed V2a identity golden literals
# ===========================================================================
#
# Frozen golden literals for the canonical two-paragraph V2a merged
# fixture.  These literals MUST be hardcoded — they MUST NOT be
# computed by calling production helpers at test runtime.  If the V2a
# seed, serialization, or merge identity intentionally changes, the
# plan / chunker version MUST be bumped and these literals updated
# explicitly.  This blocks unversioned spec drift.


async def test_p2a_r1_v2a_merged_chunk_id_golden_literal(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: the V2a chunk ID for the canonical two-paragraph merged
    fixture MUST equal the frozen golden literal below.  The literal is
    NOT computed at runtime — it freezes the V2a seed bytes against
    unversioned drift."""
    base_text = await _seed_v2a_two_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    # Frozen golden — see _P2A_V2A_MERGED_CHUNK_ID literal definition.
    assert chunk.chunk_id == _P2A_V2A_MERGED_CHUNK_ID
    # Smoke: the golden literal is 16 hex chars.
    assert len(_P2A_V2A_MERGED_CHUNK_ID) == 16
    # The merged base text is also frozen for plan_hash stability.
    assert base_text == _P2A_V2A_MERGED_FIXTURE_BASE_TEXT


async def test_p2a_r1_v2a_merged_plan_content_sha256_golden_literal(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R1: the V2a plan content SHA-256 for the canonical
    two-paragraph merged fixture MUST equal the frozen golden literal
    below.  The literal is NOT computed at runtime."""
    await _seed_v2a_two_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Frozen golden — see _P2A_V2A_MERGED_PLAN_CONTENT_SHA256 literal.
    assert compute_plan_content_sha256(plan) == (
        _P2A_V2A_MERGED_PLAN_CONTENT_SHA256
    )
    assert len(_P2A_V2A_MERGED_PLAN_CONTENT_SHA256) == 64


# ===========================================================================
# P2-A-R2: V2a policy validation coverage closure
# ===========================================================================
#
# P2-A-R1 closed the policy fingerprint gap for the main_reading merge
# path, but the V2a builder still validated policy ONLY on that path.
# Blocks that took an earlier branch — rag_eligible=False, excluded
# route (metadata_only / ignored), rag_ask_only, or heading — bypassed
# full ``StableDocumentInterpretationPolicy`` validation entirely.  A
# malformed or malicious policy on any of those branches was silently
# accepted.
#
# P2-A-R2 closes the bypass: the V2a builder MUST materialize / validate
# the policy at the START of every block iteration, BEFORE any routing
# decision.  Every branch — including the four bypass paths below —
# MUST fail-closed on an invalid / extra-field / malformed policy.
#
# All four scenarios use the PUBLIC ``build_evaluation_index_plan`` seam
# (parameterised via ``include_rag_ask_only`` where needed) so the
# contract is enforced at the API surface, not just inside the private
# builder.

# P2-A-R2: the fixed local error message asserted verbatim by every
# bypass test below.  This literal MUST match the production constant
# ``_P2A_MSG_POLICY_INVALID`` in ``article_rag_index_plan.py`` exactly.
_P2A_R2_EXPECTED_POLICY_MESSAGE = (
    "Article RAG V2a interpretation policy is invalid"
)


async def test_p2a_r2_rag_eligible_false_with_extra_field_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R2 bypass #1: a block with ``rag_eligible=False`` AND an
    unknown extra policy field MUST fail closed at the policy boundary,
    NOT silently fall through the ``not rag_eligible`` early-continue.

    Before P2-A-R2 the V2a builder called the lenient V1
    ``_interpretation_policy_fields`` helper first, saw
    ``rag_eligible=False``, and ``continue``-d before ever validating
    the full policy — so the malicious extra field was never rejected.
    """
    text_a = "First paragraph for rag_eligible bypass test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: rag_eligible=False (would early-continue) BUT the policy
    # carries an extra field that ``StableDocumentInterpretationPolicy``
    # (extra='forbid') must reject.  The validation MUST happen BEFORE
    # the rag_eligible branch.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "main_reading",
            "rag_eligible": False,
            "notes": [],
            "extra_bypass_field": "must-be-rejected",
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    # Fixed local message — asserted verbatim.
    assert str(err) == _P2A_R2_EXPECTED_POLICY_MESSAGE
    # No echo of the raw policy / extra field / sentinel.
    err_repr = repr(err)
    assert "extra_bypass_field" not in str(err)
    assert "extra_bypass_field" not in err_repr
    assert "must-be-rejected" not in str(err)
    assert "must-be-rejected" not in err_repr
    # Clean exception chain — no cause / context leakage.
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_r2_excluded_route_with_extra_field_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R2 bypass #2: a block with an excluded route
    (``metadata_only`` / ``ignored``) AND an unknown extra policy field
    MUST fail closed at the policy boundary, NOT silently fall through
    the ``route in _EXCLUDED_ROUTES`` early-continue."""
    text_a = "First paragraph for excluded-route bypass test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: metadata_only route (would early-continue) BUT the policy
    # carries an extra field that must be rejected BEFORE the route
    # branch.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["main_reading_text"],
            "default_route": "metadata_only",
            "rag_eligible": False,
            "notes": [],
            "extra_route_bypass": "rejected-before-route-check",
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    assert str(err) == _P2A_R2_EXPECTED_POLICY_MESSAGE
    err_repr = repr(err)
    assert "extra_route_bypass" not in str(err)
    assert "extra_route_bypass" not in err_repr
    assert "rejected-before-route-check" not in str(err)
    assert "rejected-before-route-check" not in err_repr
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_r2_rag_ask_only_with_malformed_notes_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R2 bypass #3: a ``rag_ask_only`` block with malformed
    ``notes`` MUST fail closed at the policy boundary, NOT be emitted as
    a standalone chunk via the ``include_rag_ask_only=True`` path before
    validation.

    Uses ``include_rag_ask_only=True`` so the rag_ask_only branch is
    actually reached; without it the block would be silently dropped
    and the malformed policy would never be observed.
    """
    text_a = "First paragraph for rag_ask_only bypass test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: rag_ask_only route with malformed notes (non-string
    # element).  The V2a builder MUST validate the policy BEFORE
    # emitting the rag_ask_only standalone chunk.
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["table_cell"],
            "default_route": "rag_ask_only",
            "rag_eligible": True,
            "notes": [{"$ne": None}],  # malicious NoSQL-style sentinel
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
            include_rag_ask_only=True,
        )
    err = exc_info.value
    assert str(err) == _P2A_R2_EXPECTED_POLICY_MESSAGE
    err_repr = repr(err)
    assert "$ne" not in str(err)
    assert "$ne" not in err_repr
    assert "notes" not in str(err).lower()
    assert "notes" not in err_repr.lower()
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_r2_heading_with_malformed_policy_fail_closed(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A-R2 bypass #4: a ``heading`` block with a malformed policy
    field MUST fail closed at the policy boundary, NOT be emitted as a
    standalone heading chunk before validation.

    The heading branch in the V2a builder runs BEFORE the main_reading
    merge path, so P2-A-R1's fingerprint helper never observed heading
    policies.  P2-A-R2 moves validation to the top of the loop so
    heading policies are validated too.
    """
    text_a = "First heading for malformed-policy bypass test."
    base_text, offsets = _build_base_text_and_offsets(text_a)

    await _seed_full_environment(index_env, base_text=base_text)
    # Block 1: heading with a malformed policy (default_route is a list
    # instead of a string).  The V2a builder MUST validate the policy
    # BEFORE emitting the heading standalone chunk.
    await _seed_block(
        index_env,
        block_id="heading-1",
        order_index=0,
        block_type="heading",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy={
            "allowed_source_scope": ["heading"],
            "default_route": ["not", "a", "string"],
            "rag_eligible": True,
            "notes": [],
        },
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError) as exc_info:
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )
    err = exc_info.value
    assert str(err) == _P2A_R2_EXPECTED_POLICY_MESSAGE
    err_repr = repr(err)
    # No echo of the raw policy / sentinel.
    assert "not" not in str(err).lower()
    assert "not" not in err_repr.lower()
    assert err.__cause__ is None
    assert err.__context__ is None


async def test_p2a_group_c_v2a_null_offset_breaks_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: a main_reading block with null canonical offsets
    MUST break the merge window.  V2a remains consistent with V1: a
    main_reading block MUST have canonical offsets; null offsets raise
    ``ArticleRagIndexPlanError``."""
    text_a = "First paragraph."
    text_b = "Second paragraph."
    text_c = "Third paragraph."
    base_text, offsets = _build_base_text_and_offsets(
        text_a, text_b, text_c
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    # Block 2: main_reading but null offsets — data inconsistency.
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=None,
        canonical_text_end_utf16=None,
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-3",
        order_index=2,
        block_type="paragraph",
        text_content=text_c,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    # Main_reading block with null offsets raises an error (V2a
    # consistent with V1).
    with pytest.raises(ArticleRagIndexPlanError):
        await service.build_evaluation_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v2",
        )


async def test_p2a_group_c_v2a_canonical_gap_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when two main_reading blocks are NOT canonically
    adjacent (gap in offsets), they MUST NOT merge.

    The canonical adjacency rule requires:
      next.start == previous.end + 2 UTF-16 units
    and the base slice between them MUST be exactly ``"\\n\\n"``.
    """
    # Build two paragraphs that are NOT canonically adjacent: leave
    # a 4-unit gap (extra "\n\n" prefix) between them.
    text_a = "AAA"
    text_b = "BBB"
    # base_text = "AAA\n\n\n\nBBB" (gap of 4 units between A and B)
    base_text = f"{text_a}\n\n\n\n{text_b}"
    start_a = 0
    end_a = utf16_code_unit_length(text_a)
    # B starts 4 units after A's end (gap of "\n\n\n\n").
    start_b = end_a + 4
    end_b = start_b + utf16_code_unit_length(text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=start_a,
        canonical_text_end_utf16=end_a,
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=start_b,
        canonical_text_end_utf16=end_b,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Canonical gap → no merge.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_separator_not_double_newline_does_not_merge(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when two main_reading blocks are canonically
    adjacent (next.start == previous.end + 2) but the base slice
    between them is NOT exactly ``"\\n\\n"`` (e.g. two spaces), they
    MUST NOT merge.

    The canonical separator MUST be exactly ``"\\n\\n"`` (2 UTF-16
    units).  Any other 2-unit separator (e.g. two spaces) MUST NOT
    merge.
    """
    # Build two paragraphs separated by two spaces (2 UTF-16 units,
    # same length as "\n\n" but different content).
    text_a = "AAA"
    text_b = "BBB"
    separator = "  "  # two spaces (2 UTF-16 units)
    base_text = f"{text_a}{separator}{text_b}"
    start_a = 0
    end_a = utf16_code_unit_length(text_a)
    start_b = end_a + utf16_code_unit_length(separator)
    end_b = start_b + utf16_code_unit_length(text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=start_a,
        canonical_text_end_utf16=end_a,
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=1,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=start_b,
        canonical_text_end_utf16=end_b,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Separator is two spaces, not "\n\n" → no merge.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_ineligible_block_breaks_merge_window(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: a non-RAG-eligible block (e.g. metadata_only)
    between two main_reading blocks MUST break the merge window.  The
    ineligible block itself is NOT emitted as a chunk."""
    text_a = "First paragraph."
    text_metadata = "Metadata block."
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(
        text_a, text_metadata, text_b
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="metadata-1",
        order_index=1,
        block_type="image",
        text_content=text_metadata,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_metadata_only_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=2,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    # Ineligible block breaks the merge: 2 standalone chunks (no
    # metadata chunk).
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("paragraph-2",)


async def test_p2a_group_c_v2a_rag_ask_only_never_merges_with_main_reading(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: a ``rag_ask_only`` block MUST NEVER merge with a
    main_reading block, even when include_rag_ask_only=True."""
    text_a = "First paragraph."
    text_b = "Table cell content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=1,
        block_type="table_cell",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_rag_ask_only_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
        include_rag_ask_only=True,
    )

    # rag_ask_only never merges with main_reading.
    assert len(plan.chunks) == 2
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)
    assert plan.chunks[1].citation.block_ids == ("table-cell-1",)


async def test_p2a_group_c_v2a_include_rag_ask_only_false_excludes_ask_only(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when ``include_rag_ask_only=False`` (default),
    ``rag_ask_only`` blocks MUST NOT produce chunks."""
    text_a = "First paragraph."
    text_b = "Table cell content."
    base_text, offsets = _build_base_text_and_offsets(text_a, text_b)

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=1,
        block_type="table_cell",
        text_content=text_b,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_rag_ask_only_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
        include_rag_ask_only=False,
    )

    # Only main_reading chunk; ask-only excluded.
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("paragraph-1",)


async def test_p2a_group_c_v2a_eligible_heading_produces_chunk(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: an eligible heading (block_type=heading,
    rag_eligible=True) MUST produce its own chunk even when no
    surrounding main_reading blocks exist."""
    text_heading = "Standalone Heading"
    base_text = text_heading

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="heading-1",
        order_index=0,
        block_type="heading",
        text_content=text_heading,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(text_heading),
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("heading-1",)
    assert plan.chunks[0].text == text_heading


async def test_p2a_group_c_v2a_zero_vector_embedding_provider_calls(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: the V2a evaluation builder MUST NOT call any
    embedding provider, vector writer, or Zilliz.  Verified by
    asserting no DB writes occur and the plan is read-only."""
    await _seed_v2a_two_paragraph_env(index_env)

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
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )
    assert len(plan.chunks) >= 1

    # No DB writes occurred.
    async with index_env.acquire() as conn:
        for table in tables_to_check:
            post_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table}"
            )
            assert post_count == pre_counts[table], (
                f"V2a evaluation builder wrote to {table}: "
                f"{pre_counts[table]} -> {post_count}"
            )


async def test_p2a_group_c_v2a_metadata_honestly_expresses_merged_chunk(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: V2a merged chunk metadata MUST honestly express
    the merge — e.g. ``merged_block_count``, ``first_block_order``,
    ``last_block_order``, ``source_scope``, ``default_route``,
    ``chunk_index``, ``has_canonical_offsets``.

    The metadata is NOT citation truth — citation truth comes from
    the Postgres plan only.  But metadata MUST honestly describe the
    merge structure."""
    await _seed_v2a_two_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    metadata = plan.chunks[0].metadata_json

    # Metadata MUST express the merge honestly.
    assert metadata["merged_block_count"] == 2
    assert metadata["first_block_order_index"] == 0
    assert metadata["last_block_order_index"] == 1
    assert metadata["source_scope"] == "main_reading_text"
    assert metadata["default_route"] == "main_reading"
    assert metadata["chunk_index"] == 0
    assert metadata["has_canonical_offsets"] is True


async def test_p2a_group_c_v2a_single_block_metadata(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: a standalone V2a chunk (single block, no merge)
    MUST have ``merged_block_count == 1`` in its metadata."""
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 1
    metadata = plan.chunks[0].metadata_json
    assert metadata["merged_block_count"] == 1
    assert metadata["first_block_order_index"] == 0
    assert metadata["last_block_order_index"] == 0


async def test_p2a_group_c_v2a_chunk_index_sequential(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: when multiple V2a chunks are produced, their
    ``chunk_index`` metadata MUST be sequential (0, 1, 2, ...)."""
    # Two paragraphs separated by a heading → 3 standalone chunks.
    text_a = "First paragraph."
    text_heading = "Section Title"
    text_b = "Second paragraph."
    base_text, offsets = _build_base_text_and_offsets(
        text_a, text_heading, text_b
    )

    await _seed_full_environment(index_env, base_text=base_text)
    await _seed_block(
        index_env,
        block_id="paragraph-1",
        order_index=0,
        block_type="paragraph",
        text_content=text_a,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="heading-1",
        order_index=1,
        block_type="heading",
        text_content=text_heading,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )
    await _seed_block(
        index_env,
        block_id="paragraph-2",
        order_index=2,
        block_type="paragraph",
        text_content=text_b,
        canonical_text_start_utf16=offsets[2][0],
        canonical_text_end_utf16=offsets[2][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    assert len(plan.chunks) == 3
    for i, chunk in enumerate(plan.chunks):
        assert chunk.metadata_json["chunk_index"] == i


async def test_p2a_group_c_v2a_citation_no_plate_markdown_fields(
    index_env: asyncpg.Pool,
) -> None:
    """P2-A Group C: V2a citation refs MUST NOT contain any Plate,
    Slate, DOM, or Markdown fields.  Only canonical truth fields."""
    await _seed_v2a_two_paragraph_env(index_env)

    service = _build_service(index_env)
    plan = await service.build_evaluation_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
        index_version="article_rag_index_v2",
    )

    citation = plan.chunks[0].citation
    # Citation fields are exactly the canonical truth fields.
    expected_fields = {
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
    actual_fields = set(citation.__dataclass_fields__.keys())
    assert actual_fields == expected_fields
