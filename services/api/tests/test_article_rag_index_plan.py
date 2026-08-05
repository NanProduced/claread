# task-history: D6-I4A (renamed from test_d6_i4a_article_rag_index_plan.py)
"""Tests for the Reader Article RAG index plan foundation.

Covers the 14 test requirements from the task spec:
 1. paragraph/heading/list_item/blockquote/caption default indexable
 2. explicit non-main_reading policies (table/image/footnote/code_block/
    unknown overrides) not indexable by default
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

Uses real PostgreSQL with a temporary schema (BASELINE_SQL from
infra/migrations/0001_initial.sql).
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
from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
    ArticleRagIndexPlanService,
    compute_plan_content_sha256,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_article_rag, pytest.mark.seam_service_integration, pytest.mark.life_permanent_regression, pytest.mark.life_characterization]

REPO_ROOT = Path(__file__).resolve().parents[3]

from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

# The document_blocks and article_rag_index_state schema live in the
# canonical baseline (infra/migrations/0001_initial.sql), so the full plan
# schema is just BASELINE_SQL.
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
# Test 2: explicit non-main_reading overrides not indexable by default
# ===================================================================


async def test_default_non_indexable_block_types_excluded(index_env: asyncpg.Pool) -> None:
    """Requirement 2: blocks explicitly routed to metadata_only /
    rag_ask_only are not indexed by default.  Since the Markdown
    ecosystem refactor (D2 / A1), code_block / table_cell DEFAULT to
    main_reading — this test pins that an explicit rag_ask_only
    override still excludes them (footnote keeps its rag_ask_only
    default).  With only non-indexable blocks, the plan fails closed."""
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
    # Non-indexable blocks (explicit overrides away from main_reading).
    # table: explicit metadata_only override, not eligible
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
    # code_block: explicit rag_ask_only override, eligible
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


async def test_empty_policy_code_block_defaults_main_reading(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a code_block must materialize as
    main_reading / eligible (Markdown ecosystem refactor D2 / A1) —
    indexed by default when canonical offsets are present."""
    code_text = "print('hello')"
    await _seed_full_environment(index_env, base_text=code_text)

    await _seed_block(
        index_env,
        block_id="code-1",
        order_index=0,
        block_type="code_block",
        text_content=code_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(code_text),
        interpretation_policy={},
    )

    service = _build_service(index_env)

    # main_reading + rag_eligible -> indexed by default (no
    # include_rag_ask_only needed).
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("code-1",)
    assert plan.chunks[0].source_scope == "code_block"
    assert plan.chunks[0].metadata_json["default_route"] == "main_reading"


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


async def test_empty_policy_table_cell_defaults_main_reading(
    index_env: asyncpg.Pool,
) -> None:
    """P1-1: ``{}`` policy on a table_cell must materialize as
    main_reading / eligible (Markdown ecosystem refactor D2 / A1) —
    indexed by default when canonical offsets are present."""
    cell_text = "Cell content."
    await _seed_full_environment(index_env, base_text=cell_text)

    await _seed_block(
        index_env,
        block_id="table-cell-1",
        order_index=0,
        block_type="table_cell",
        text_content=cell_text,
        canonical_text_start_utf16=0,
        canonical_text_end_utf16=utf16_code_unit_length(cell_text),
        interpretation_policy={},
    )

    service = _build_service(index_env)

    # main_reading + rag_eligible -> indexed by default (no
    # include_rag_ask_only needed).
    plan = await service.build_index_plan(record_id=_RECORD_ID, user_id=_USER_ID)
    assert len(plan.chunks) == 1
    assert plan.chunks[0].citation.block_ids == ("table-cell-1",)
    assert plan.chunks[0].source_scope == "table_cell"
    assert plan.chunks[0].metadata_json["default_route"] == "main_reading"


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
# detected here.  Literals are independently computable from the
# single-path block contract (SHA-256 of known strings) — the test
# does NOT re-implement the plan hash algorithm; it only asserts the
# production ``compute_plan_content_sha256`` result equals the frozen
# literal.
_P1E_BASE_TEXT = "Hello article RAG world."
_P1E_BASE_TEXT_UTF16_LEN = 24  # ASCII-only, 24 code units
_P1E_CHUNK_ID = "9c0de682d80dc1f0"
_P1E_CONTENT_SHA256 = (
    "d3e0a2214433bbc3728f44d75ddb2e530f63fb6af67a8ae9ed4a208f27db3c62"
)
_P1E_EMBEDDING_TEXT_SHA256 = (
    "d3e0a2214433bbc3728f44d75ddb2e530f63fb6af67a8ae9ed4a208f27db3c62"
)


# Single-path block-plan golden literals for full plan / chunk /
# citation field coverage.  Independently derived from the block
# contract (fixed UUIDs, SHA-256 of known strings, fixed metadata
# dict) — the test does NOT re-implement the plan hash algorithm or
# call production helpers to generate expected values.  Multi-key or
# missing-key metadata drift MUST fail here (complete dict == equality,
# no issubset).
_P1E_STABLE_DOCUMENT_CONTENT_SHA256 = "a" * 64  # _DEFAULT_STABLE_SHA256
_P1E_CANONICAL_TEXT_SHA256 = _P1E_CONTENT_SHA256  # sha256 of base text
_P1E_SOURCE_SCOPE = "main_reading_text"
_P1E_EXPECTED_METADATA: dict = {
    "block_type": "paragraph",
    "block_order_index": 0,
    "source_scope": "main_reading_text",
    "default_route": "main_reading",
    "chunk_index": 0,
    "has_canonical_offsets": True,
}


async def _p1e_seed_minimal_v1_env(
    pool: asyncpg.Pool,
    *,
    base_text: str = _P1E_BASE_TEXT,
) -> str:
    """Seed the minimal single-path block-plan environment.

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
        canonical_text_end_utf16=_P1E_BASE_TEXT_UTF16_LEN,
        interpretation_policy=_main_reading_policy(),
    )
    return hashlib.sha256(base_text.encode("utf-8")).hexdigest()


# ===================================================================
# Single-path block-plan golden (post-convergence)
# ===================================================================
#
# After Round 1 + Milestone 1 convergence, the plan service is
# single-path: no index_version parameter, no chunker_version field,
# no profile resolver, no V2a evaluation builder.  This test freezes
# the new single-path contract.
#
# The new plan_content_sha256 golden is captured AFTER removing
# chunker_version from compute_plan_content_sha256.  The hash
# algorithm now covers only:
#   stable_document_id, base_id, record_generation, content_sha256,
#   canonical_text_sha256, chunk count, and per-chunk fields.

_SINGLE_PATH_PLAN_CONTENT_SHA256 = (
    "5484e72c7584ee48a1ff9835fbed2239d4b01a4e99402fd87c1981ce0f644674"
)


async def test_single_path_block_plan_golden(
    index_env: asyncpg.Pool,
) -> None:
    """Single-path block-plan golden after convergence.

    Verifies the post-convergence contract:
      * build_index_plan does NOT accept index_version parameter
      * ArticleRagIndexPlan has NO chunker_version field
      * compute_plan_content_sha256 does NOT include chunker_version
      * Deterministic plan hash (byte-stable across rebuilds)
      * V1 block behavior preserved (citation, UTF-16, metadata)
    """
    await _p1e_seed_minimal_v1_env(index_env)

    service = _build_service(index_env)

    # 1. build_index_plan must NOT accept index_version parameter.
    #    Passing it must raise TypeError.
    with pytest.raises(TypeError):
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
            index_version="article_rag_index_v1",
        )

    # 2. ArticleRagIndexPlan must NOT have chunker_version field.
    assert "chunker_version" not in ArticleRagIndexPlan.__dataclass_fields__, (
        f"ArticleRagIndexPlan must not have chunker_version field; "
        f"actual fields: {list(ArticleRagIndexPlan.__dataclass_fields__)}"
    )

    # 3. Build plan without index_version (must succeed).
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )
    assert not hasattr(plan, "chunker_version")

    # 4. V1 block behavior preserved — chunk count, chunk_id, content_sha.
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.chunk_id == _P1E_CHUNK_ID
    assert chunk.content_sha256 == _P1E_CONTENT_SHA256
    assert chunk.embedding_text_sha256 == _P1E_EMBEDDING_TEXT_SHA256
    assert chunk.metadata_json == _P1E_EXPECTED_METADATA

    # 5. Citation fields preserved.
    citation = chunk.citation
    assert citation.reading_record_id == _RECORD_ID
    assert citation.stable_document_id == _STABLE_DOC_ID
    assert citation.base_id == _BASE_ID
    assert citation.record_generation == 1
    assert citation.block_ids == ("paragraph-1",)
    assert citation.unit_ids == ()
    assert citation.anchor_segment_ids == ()
    assert citation.canonical_text_start_utf16 == 0
    assert citation.canonical_text_end_utf16 == _P1E_BASE_TEXT_UTF16_LEN

    # 6. Plan content sha256 — new golden (without chunker_version in hash).
    actual_plan_sha = compute_plan_content_sha256(plan)
    assert actual_plan_sha == _SINGLE_PATH_PLAN_CONTENT_SHA256, (
        f"Single-path plan_content_sha256 drift: expected "
        f"{_SINGLE_PATH_PLAN_CONTENT_SHA256}, got {actual_plan_sha}"
    )

    # 7. Determinism — rebuild in independent schema and compare hash.
    schema_b = f"test_i4a_single_path_b_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_b}"')
        await admin_conn.execute(f'SET search_path TO "{schema_b}", public')
        await admin_conn.execute(INDEX_PLAN_SCHEMA_SQL)
        pool_b = await _make_pool(schema_b)
        try:
            await _p1e_seed_minimal_v1_env(pool_b)
            plan_b = await _build_service(pool_b).build_index_plan(
                record_id=_RECORD_ID,
                user_id=_USER_ID,
            )
            assert compute_plan_content_sha256(plan_b) == actual_plan_sha
        finally:
            await pool_b.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA "{schema_b}" CASCADE')
        await admin_conn.close()


# ===================================================================
# M3 prerequisite: list wrapper eligibility (structural wrapper skip)
# ===================================================================


async def test_list_wrapper_with_empty_text_does_not_raise(
    index_env: asyncpg.Pool,
) -> None:
    """M3 前置 — list wrapper block (text_content=None、default_route=
    main_reading、rag_eligible=True) 必须在 chunk 构建阶段被跳过，
    而非抛出 ArticleRagIndexPlanError。

    list wrapper 是结构性容器：叙事文本在 list_item 子节点。这与
    document_freeze_plan L228-244 的跳过逻辑对称——freeze plan 在
    构建 canonical text 时跳过 list wrapper，RAG plan builder 在
    构建 chunks 时也应跳过。

    本测试构造 parser 真实产出的 list wrapper + list_item 子节点组合
    （parent_block_id 指向 wrapper），验证：
      * build_index_plan() 不抛错
      * list wrapper 不产生 chunk
      * list_item 子节点正常产生 chunk 且 canonical range 有效
    """
    item1_text = "First list item text."
    item2_text = "Second list item text."
    base_text, offsets = _build_base_text_and_offsets(item1_text, item2_text)
    await _seed_full_environment(index_env, base_text=base_text)

    # list wrapper block — 模拟 parser 产出：
    # block_type="list"、text_content=None、main_reading + rag_eligible、
    # 无 canonical offsets（freeze plan 跳过它，不分配 canonical text）。
    await _seed_block(
        index_env,
        block_id="list-wrapper-1",
        parent_block_id=None,
        order_index=0,
        block_type="list",
        text_content=None,
        canonical_text_start_utf16=None,
        canonical_text_end_utf16=None,
        interpretation_policy=_main_reading_policy(),
    )
    # list_item child 1 — parent 指向 wrapper。
    await _seed_block(
        index_env,
        block_id="list-item-1",
        parent_block_id="list-wrapper-1",
        order_index=1,
        block_type="list_item",
        text_content=item1_text,
        canonical_text_start_utf16=offsets[0][0],
        canonical_text_end_utf16=offsets[0][1],
        interpretation_policy=_main_reading_policy(),
    )
    # list_item child 2 — parent 指向 wrapper。
    await _seed_block(
        index_env,
        block_id="list-item-2",
        parent_block_id="list-wrapper-1",
        order_index=2,
        block_type="list_item",
        text_content=item2_text,
        canonical_text_start_utf16=offsets[1][0],
        canonical_text_end_utf16=offsets[1][1],
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # list wrapper 不产生 chunk；list_item 子节点正常产生 chunk。
    assert len(plan.chunks) == 2
    assert [c.citation.block_ids[0] for c in plan.chunks] == [
        "list-item-1",
        "list-item-2",
    ]
    # list_item chunk 携带有效的 canonical range（指向 base text）。
    assert plan.chunks[0].citation.canonical_text_start_utf16 == offsets[0][0]
    assert plan.chunks[0].citation.canonical_text_end_utf16 == offsets[0][1]
    assert plan.chunks[1].citation.canonical_text_start_utf16 == offsets[1][0]
    assert plan.chunks[1].citation.canonical_text_end_utf16 == offsets[1][1]


async def test_non_list_main_reading_empty_text_still_fails_closed(
    index_env: asyncpg.Pool,
) -> None:
    """M3 前置 — list wrapper 跳过不得掩盖其他 main_reading 无文本 block
    的 schema 不一致。非 list 类型的 main_reading block 在 text_content
    为空时仍必须 fail-closed，与 freeze plan L245-249 的对称行为一致。

    使用 table_cell（DB CHECK 允许 text_content=NULL）+ 显式 main_reading
    policy 来构造非 list 的 main_reading 无文本 block 场景。table_cell
    不是 list，因此不会被 list wrapper skip 逻辑跳过。"""
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
    # table_cell with empty text + main_reading policy — 不是 list，
    # 因此不会被 list wrapper skip 跳过，仍必须 fail closed。
    await _seed_block(
        index_env,
        block_id="table-cell-empty",
        order_index=1,
        block_type="table_cell",
        text_content=None,
        interpretation_policy=_main_reading_policy(),
    )

    service = _build_service(index_env)
    with pytest.raises(ArticleRagIndexPlanError, match="no text_content"):
        await service.build_index_plan(
            record_id=_RECORD_ID,
            user_id=_USER_ID,
        )


async def test_real_list_wrapper_fixture_builds_plan_without_raising(
    index_env: asyncpg.Pool,
) -> None:
    """M3 前置 — 用 real_list_wrapper G0 fixture 的 parser 真实产出
    驱动 build_index_plan()，补齐 G1 盲区：之前 RAG 测试只用 isolated
    list_item，未覆盖 parser 真实生成的 list wrapper + list_item 子节点
    组合。

    本测试解析 fixture input.md，把 parser 产出的 blocks 种入测试 DB
    （list wrapper: text_content=None、无 canonical offsets；list_item:
    有 text_content、有 canonical offsets），然后调用 build_index_plan()。

    断言要点：
      * build_index_plan() 不抛错（list wrapper 被 eligibility skip 跳过）
      * list wrapper 不产生 chunk
      * list_item 子节点正常产生 chunk 且 canonical range 有效
      * heading 和 paragraph 也正常产生 chunk
    """
    from pathlib import Path

    from app.schemas.reader_documents import default_interpretation_policy_for
    from app.services.reader_orchestration.markdown_source_parser import (
        MarkdownSourceParser,
    )

    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "markdown_structured_source"
        / "real_list_wrapper"
        / "input.md"
    )
    input_md = fixture_path.read_text(encoding="utf-8")
    parse_result = MarkdownSourceParser().parse(input_md)
    parsed_blocks = list(parse_result.blocks)

    # 构造 base_text：按 freeze plan 规则，跳过 list wrapper（text_content
    # 为 None），把有 text_content 的 block 用 "\n\n" 拼接。这与
    # document_freeze_plan._build_canonical_text 对 list wrapper 的跳过
    # 行为对称。
    separator = "\n\n"
    text_bearing_blocks = [
        b for b in parsed_blocks if b.text_content is not None
    ]
    base_text = separator.join(b.text_content or "" for b in text_bearing_blocks)

    # 计算 base_text 中每个 text-bearing block 的 canonical UTF-16 offsets。
    # list wrapper 不分配 canonical offsets（与 freeze plan 行为一致）。
    from app.contracts.annotation import utf16_code_unit_length as _utf16_len

    sep_utf16_len = _utf16_len(separator)
    canonical_offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for b in text_bearing_blocks:
        text_utf16_len = _utf16_len(b.text_content or "")
        canonical_offsets[b.block_id] = (cursor, cursor + text_utf16_len)
        cursor += text_utf16_len + sep_utf16_len

    await _seed_full_environment(index_env, base_text=base_text)

    # 把 parser 产出的 blocks 种入 DB。list wrapper 的 text_content=None、
    # canonical_offsets=None；其他 block 用计算的 canonical offsets。
    for block in parsed_blocks:
        offsets = canonical_offsets.get(block.block_id)
        start = offsets[0] if offsets else None
        end = offsets[1] if offsets else None
        policy = default_interpretation_policy_for(block.block_type)
        await _seed_block(
            index_env,
            block_id=block.block_id,
            parent_block_id=block.parent_block_id,
            order_index=block.order_index,
            block_type=block.block_type,
            text_content=block.text_content,
            canonical_text_start_utf16=start,
            canonical_text_end_utf16=end,
            interpretation_policy={
                "default_route": policy.default_route,
                "rag_eligible": policy.rag_eligible,
                "allowed_source_scope": list(policy.allowed_source_scope),
            },
        )

    service = _build_service(index_env)
    plan = await service.build_index_plan(
        record_id=_RECORD_ID,
        user_id=_USER_ID,
    )

    # list wrapper 不产生 chunk；heading + 2 paragraph + 6 list_item = 9 chunks。
    chunk_block_ids = [c.citation.block_ids[0] for c in plan.chunks]
    assert "b3" not in chunk_block_ids, (
        "list wrapper b3 (unordered) must not produce a chunk"
    )
    assert "b7" not in chunk_block_ids, (
        "list wrapper b7 (ordered) must not produce a chunk"
    )
    # heading(b1) + paragraph(b2) + 3 list_item(b4,b5,b6) +
    # 3 list_item(b8,b9,b10) + paragraph(b11) = 9 chunks
    assert len(plan.chunks) == 9, (
        f"expected 9 chunks (heading + paragraph + 6 list_items + paragraph), "
        f"got {len(plan.chunks)}: {chunk_block_ids}"
    )
    expected_chunk_ids = ["b1", "b2", "b4", "b5", "b6", "b8", "b9", "b10", "b11"]
    assert chunk_block_ids == expected_chunk_ids, (
        f"chunk block_ids mismatch: actual={chunk_block_ids}, "
        f"expected={expected_chunk_ids}"
    )

    # 每个 chunk 必须有有效的 canonical range（指向 base_text）。
    for chunk in plan.chunks:
        assert chunk.citation.canonical_text_start_utf16 is not None
        assert chunk.citation.canonical_text_end_utf16 is not None
        assert (
            chunk.citation.canonical_text_end_utf16
            > chunk.citation.canonical_text_start_utf16
        )
