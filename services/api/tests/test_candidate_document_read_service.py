"""S2: Candidate Recovery read model tests.

Covers the full test matrix from the spec §8.1:
- 404 four collapsed causes (not found / not owner / soft-deleted / no
  ready candidate)
- 409 open_reader (readable_enhancing + article_ready + active_base_id)
- 409 return_to_library (failed state)
- 409 multiple_ready_candidates
- 200 with three preview_mode (full_text / truncated_preview / outline_only)
- Response field whitelist: no blocks_json / quality_json / source_text /
  source_refs_json leak
- risk_items user_message does not leak quality_json internal keys

Uses a real PostgreSQL schema (per-test isolated schema). Tests the
service layer directly (not the HTTP route) to verify typed projection.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.reader_orchestration.candidate_document_read_service import (
    CandidateDocumentReadConflict,
    CandidateDocumentReadError,
    CandidateDocumentReadService,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]

from tests.test_reader_orchestration_schema_baseline import (  # noqa: E402
    BASELINE_SQL,
    DATABASE_URL,
)

_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
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
async def read_env() -> asyncpg.Pool:
    """Schema for read service tests."""
    schema_name = f"test_cand_read_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id,
        )


async def _seed_record(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    generation: int = 1,
    product_state: str = "needs_confirmation",
    readiness_state: str = "candidate_base_ready",
    active_base_id: UUID | None = None,
    deleted_at: datetime | None = None,
    title: str = "Test Record",
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Insert record first without active_base_id (deferred FK requires
            # the referenced reading_bases row to exist by commit time).
            await conn.execute(
                """
                INSERT INTO reading_records (
                    id, user_id, source_type, title, language,
                    lifecycle_status, product_state, readiness_state,
                    generation, active_base_id, deleted_at
                )
                VALUES ($1, $2, 'text', $3, 'en',
                        'active', $4, $5, $6, NULL, $7)
                """,
                record_id,
                user_id,
                title,
                product_state,
                readiness_state,
                generation,
                deleted_at,
            )
            if active_base_id is not None:
                base_text = "test base text"
                base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
                await conn.execute(
                    """
                    INSERT INTO reading_bases (
                        id, reading_record_id, base_version, record_generation,
                        text, content_sha256, content_utf16_length,
                        canonicalizer_version, builder_version, segmenter_version,
                        status, frozen_at, created_at
                    )
                    VALUES ($1, $2, 1, $3,
                            $4, $5, utf16_code_unit_length($4),
                            'test_v1', 'test_v1', 'test_v1',
                            'active', $6, $6)
                    """,
                    active_base_id,
                    record_id,
                    generation,
                    base_text,
                    base_sha,
                    _NOW,
                )
                await conn.execute(
                    "UPDATE reading_records SET active_base_id = $1 WHERE id = $2",
                    active_base_id,
                    record_id,
                )


async def _seed_original_input(
    pool: asyncpg.Pool,
    *,
    original_input_id: UUID,
    record_id: UUID,
    user_id: UUID,
    input_type: str = "plain_text",
    source_text: str = "test text",
    metadata_json: dict | None = None,
) -> None:
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, $4,
                    $5, '{}'::jsonb, $6::jsonb, $7)
            """,
            original_input_id,
            record_id,
            user_id,
            input_type,
            source_text,
            metadata_json or {},
            source_sha,
        )


async def _seed_candidate(
    pool: asyncpg.Pool,
    *,
    candidate_id: UUID,
    record_id: UUID,
    user_id: UUID,
    generation: int = 1,
    status: str = "ready",
    title: str = "Test Candidate",
    blocks_json: list | None = None,
    quality_json: dict | None = None,
    source_refs_json: dict | None = None,
    canonical_text_preview: str = "",
    created_at: datetime = _NOW,
    confirmed_at: datetime | None = None,
) -> None:
    blocks = blocks_json or [
        {
            "block_id": "paragraph-0000",
            "order_index": 0,
            "block_type": "paragraph",
            "text_content": "Short test content.",
        }
    ]
    quality = quality_json or {
        "candidate_creation_version": "candidate_creation_v1",
        "suitability": {
            "outcome": "candidate_document_required",
            "flags": [],
            "reasons": [],
            "word_count": 5,
            "english_word_ratio": 1.0,
            "natural_language_score": 0.95,
        },
    }
    source_refs = source_refs_json or {
        "source_type": "pasted_text",
    }
    effective_confirmed_at = confirmed_at if status == "confirmed" else None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status, created_at, updated_at,
                confirmed_at
            )
            VALUES ($1, $2, $3, $4, $5,
                    $6::jsonb, $7, $8::jsonb, $9::jsonb, $10, $11, $11, $12)
            """,
            candidate_id,
            record_id,
            user_id,
            generation,
            title,
            blocks,
            canonical_text_preview,
            source_refs,
            quality,
            status,
            created_at,
            effective_confirmed_at,
        )


# ---------------------------------------------------------------------------
# Block builders for preview_mode tests
# ---------------------------------------------------------------------------


def _short_blocks() -> list[dict]:
    """Blocks with total_char_count <= 2000 → full_text."""
    return [
        {
            "block_id": "heading-0000",
            "order_index": 0,
            "block_type": "heading",
            "text_content": "Short Article",
        },
        {
            "block_id": "paragraph-0001",
            "order_index": 1,
            "block_type": "paragraph",
            "text_content": "This is a short paragraph for testing full_text mode.",
        },
    ]


def _medium_blocks() -> list[dict]:
    """Blocks with 2000 < total_char_count <= 8000 → truncated_preview."""
    # ~3000 chars
    long_text = "word " * 600  # ~3000 chars
    return [
        {
            "block_id": "heading-0000",
            "order_index": 0,
            "block_type": "heading",
            "text_content": "Medium Article",
        },
        {
            "block_id": "paragraph-0001",
            "order_index": 1,
            "block_type": "paragraph",
            "text_content": long_text,
        },
    ]


def _long_blocks() -> list[dict]:
    """Blocks with total_char_count > 8000 → outline_only."""
    # ~10000 chars
    long_text = "word " * 2000  # ~10000 chars
    return [
        {
            "block_id": "heading-0000",
            "order_index": 0,
            "block_type": "heading",
            "text_content": "Long Article",
        },
        {
            "block_id": "paragraph-0001",
            "order_index": 1,
            "block_type": "paragraph",
            "text_content": long_text,
        },
    ]


# ---------------------------------------------------------------------------
# Section 1: 404 collapsed causes
# ---------------------------------------------------------------------------


async def test_404_record_not_found(read_env: asyncpg.Pool) -> None:
    """Record does not exist → 404."""
    pool = read_env
    user_id = uuid4()
    await _seed_user(pool, user_id=user_id)

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadError) as exc_info:
        await service.load_candidate_document(
            record_id=uuid4(),
            user_id=user_id,
        )
    assert exc_info.value.reason == "not_found"


async def test_404_not_owner(read_env: asyncpg.Pool) -> None:
    """Record exists but belongs to another user → 404 (no leak)."""
    pool = read_env
    owner_id = uuid4()
    other_user_id = uuid4()
    record_id = uuid4()
    await _seed_user(pool, user_id=owner_id)
    await _seed_user(pool, user_id=other_user_id)
    await _seed_record(pool, record_id=record_id, user_id=owner_id)

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadError) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=other_user_id,
        )
    assert exc_info.value.reason == "not_found"


async def test_404_soft_deleted(read_env: asyncpg.Pool) -> None:
    """Record is soft-deleted → 404."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        deleted_at=_NOW,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadError) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.reason == "not_found"


async def test_404_no_ready_candidate(read_env: asyncpg.Pool) -> None:
    """Record exists, needs_confirmation, but no ready candidate → 404."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    # Seed a confirmed candidate (not ready)
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        status="confirmed",
        confirmed_at=_NOW,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadError) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.reason == "not_found"


# ---------------------------------------------------------------------------
# Section 2: 409 state advanced
# ---------------------------------------------------------------------------


async def test_409_open_reader(read_env: asyncpg.Pool) -> None:
    """Record advanced to readable_enhancing + article_ready + active_base_id
    → 409 open_reader."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        active_base_id=base_id,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadConflict) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.code == "record_state_advanced"
    assert exc_info.value.resolution == "open_reader"


async def test_409_open_reader_coverage_complete(read_env: asyncpg.Pool) -> None:
    """Record advanced to readable_enhancing + coverage_complete +
    active_base_id → 409 open_reader.

    P1-2 regression: coverage_complete is also a readable state when
    paired with active_base_id, so the user should still be sent to
    Reader rather than Library.
    """
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="readable_enhancing",
        readiness_state="coverage_complete",
        active_base_id=base_id,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadConflict) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.code == "record_state_advanced"
    assert exc_info.value.resolution == "open_reader"


async def test_409_return_to_library_article_ready_without_base(
    read_env: asyncpg.Pool,
) -> None:
    """readable_enhancing + article_ready but NO active_base_id →
    return_to_library.

    Defensive guard: even with a "readable" readiness_state, the
    absence of active_base_id means there is no Reader content to open
    yet. The user must return to Library.
    """
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        active_base_id=None,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadConflict) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.code == "record_state_advanced"
    assert exc_info.value.resolution == "return_to_library"


async def test_409_return_to_library_failed(read_env: asyncpg.Pool) -> None:
    """Record advanced to failed → 409 return_to_library."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(
        pool,
        record_id=record_id,
        user_id=user_id,
        product_state="failed",
        readiness_state="submitted",
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadConflict) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.code == "record_state_advanced"
    assert exc_info.value.resolution == "return_to_library"


async def test_409_multiple_ready_candidates(read_env: asyncpg.Pool) -> None:
    """Two ready candidates for same (record, generation) → 409
    multiple_ready_candidates + return_to_library."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_a = uuid4()
    candidate_b = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_candidate(
        pool,
        candidate_id=candidate_a,
        record_id=record_id,
        user_id=user_id,
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_b,
        record_id=record_id,
        user_id=user_id,
    )

    service = CandidateDocumentReadService(pool=pool)
    with pytest.raises(CandidateDocumentReadConflict) as exc_info:
        await service.load_candidate_document(
            record_id=record_id,
            user_id=user_id,
        )
    assert exc_info.value.code == "multiple_ready_candidates"
    assert exc_info.value.resolution == "return_to_library"


# ---------------------------------------------------------------------------
# Section 3: 200 with three preview_mode
# ---------------------------------------------------------------------------


async def test_200_full_text_preview(read_env: asyncpg.Pool) -> None:
    """200 with total_char_count <= 2000 → full_text."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    original_input_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_original_input(
        pool,
        original_input_id=original_input_id,
        record_id=record_id,
        user_id=user_id,
        input_type="plain_text",
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_short_blocks(),
        source_refs_json={
            "source_type": "pasted_text",
            "original_input_id": str(original_input_id),
        },
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    response = result.response
    assert response.status == "ready"
    assert response.record_generation == 1
    assert response.preview.preview_mode == "full_text"
    assert response.preview.is_truncated is False
    assert response.preview.total_char_count <= 2000
    assert "Short Article" in response.preview.preview_text
    assert len(response.preview.document_outline) == 2
    assert response.preview.document_outline[0].block_type_label == "heading"
    assert response.preview.document_outline[0].heading_text == "Short Article"
    assert response.preview.document_outline[1].block_type_label == "paragraph"
    assert response.preview.document_outline[1].heading_text is None
    assert response.source_type == "plain_text"
    assert response.source_label == "粘贴文本"


async def test_200_truncated_preview(read_env: asyncpg.Pool) -> None:
    """200 with 2000 < total_char_count <= 8000 → truncated_preview."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_medium_blocks(),
        source_refs_json={"source_type": "markdown_file", "filename": "test.md"},
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    response = result.response
    assert response.preview.preview_mode == "truncated_preview"
    assert response.preview.is_truncated is True
    assert 2000 < response.preview.total_char_count <= 8000
    assert len(response.preview.document_outline) == 2
    # Document outline must be non-empty for truncated_preview
    assert len(response.preview.document_outline) > 0
    # source_type falls back to candidate source_refs (no original_input)
    assert response.source_type == "markdown"
    assert response.filename == "test.md"


async def test_200_outline_only(read_env: asyncpg.Pool) -> None:
    """200 with total_char_count > 8000 → outline_only."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_long_blocks(),
        source_refs_json={"source_type": "pasted_text"},
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    response = result.response
    assert response.preview.preview_mode == "outline_only"
    assert response.preview.is_truncated is True
    assert response.preview.total_char_count > 8000
    # outline_only has empty preview_text
    assert response.preview.preview_text == ""
    # Document outline must be non-empty
    assert len(response.preview.document_outline) > 0


# ---------------------------------------------------------------------------
# Section 4: Field whitelist — no raw JSON leak
# ---------------------------------------------------------------------------


async def test_response_does_not_leak_raw_json_fields(
    read_env: asyncpg.Pool,
) -> None:
    """200 response must NOT contain blocks_json, quality_json,
    source_refs_json, source_text, original_input_id."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    original_input_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_original_input(
        pool,
        original_input_id=original_input_id,
        record_id=record_id,
        user_id=user_id,
        source_text="SECRET_SOURCE_TEXT_NOT_IN_RESPONSE",
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_short_blocks(),
        quality_json={
            "candidate_creation_version": "candidate_creation_v1",
            "suitability": {
                "outcome": "candidate_document_required",
                "flags": ["ocr_low_confidence"],
                "reasons": [],
            },
        },
        source_refs_json={
            "source_type": "pasted_text",
            "original_input_id": str(original_input_id),
            "source_metadata": {"secret_key": "secret_value"},
        },
        canonical_text_preview="canonical_preview_not_in_response",
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    # Serialize to dict to check for leaked fields
    response_dict = result.response.model_dump(mode="json")
    response_json = str(response_dict)

    # Must NOT contain raw internal field names
    assert "blocks_json" not in response_json
    assert "quality_json" not in response_json
    assert "source_refs_json" not in response_json
    assert "canonical_text_preview" not in response_json
    assert "original_input_id" not in response_json
    assert "source_text" not in response_json
    # Must NOT contain the actual secret values
    assert "SECRET_SOURCE_TEXT_NOT_IN_RESPONSE" not in response_json
    assert "secret_key" not in response_json
    assert "secret_value" not in response_json
    assert "canonical_preview_not_in_response" not in response_json
    # Must NOT contain internal block fields
    assert "block_id" not in response_json
    assert "parent_block_id" not in response_json
    assert "payload_json" not in response_json
    assert "interpretation_policy" not in response_json
    assert "canonical_text_start_utf16" not in response_json
    assert "canonical_text_end_utf16" not in response_json
    # Must NOT contain quality_json internal keys
    assert "candidate_creation_version" not in response_json
    assert "suitability" not in response_json
    assert "english_word_ratio" not in response_json
    assert "natural_language_score" not in response_json

    # Verify the allowed fields ARE present
    assert "record_id" in response_dict
    assert "candidate_document_id" in response_dict
    assert "record_generation" in response_dict
    assert "status" in response_dict
    assert "title" in response_dict
    assert "preview" in response_dict
    assert "source_type" in response_dict
    assert "filename" in response_dict
    assert "source_label" in response_dict
    assert "created_at" in response_dict
    assert "updated_at" in response_dict

    # Verify preview sub-fields
    preview = response_dict["preview"]
    assert "preview_mode" in preview
    assert "preview_text" in preview
    assert "is_truncated" in preview
    assert "total_char_count" in preview
    assert "document_outline" in preview
    assert "risk_items" in preview


# ---------------------------------------------------------------------------
# Section 5: risk_items do not leak quality_json internal keys
# ---------------------------------------------------------------------------


async def test_risk_items_user_message_does_not_leak_quality_keys(
    read_env: asyncpg.Pool,
) -> None:
    """risk_items[].user_message must NOT contain quality_json internal
    key names like 'ocr_low_confidence', 'markdown_complex_structure'."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_short_blocks(),
        quality_json={
            "candidate_creation_version": "candidate_creation_v1",
            "suitability": {
                "outcome": "candidate_document_required",
                "flags": [
                    "ocr_low_confidence",
                    "markdown_complex_structure",
                    "table_structure_uncertain",
                ],
                "reasons": [],
            },
        },
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    risk_items = result.response.preview.risk_items
    # Should have at least 2 risk items (low_confidence_ocr + structure_fragmented)
    assert len(risk_items) >= 2

    for risk in risk_items:
        msg = risk.user_message
        # user_message must NOT contain internal flag names
        assert "ocr_low_confidence" not in msg
        assert "markdown_complex_structure" not in msg
        assert "table_structure_uncertain" not in msg
        assert "layout_order_uncertain" not in msg
        assert "image_ocr_uncertain" not in msg
        assert "footnote_or_caption_merged" not in msg
        assert "document_block_degraded" not in msg
        assert "code_dominant" not in msg
        assert "link_list_dominant" not in msg
        assert "suitability" not in msg
        assert "candidate_creation_version" not in msg
        # user_message must be non-empty Chinese text
        assert len(msg) > 0

    # Verify risk_kind values are from the controlled enum
    risk_kinds = {r.risk_kind for r in risk_items}
    assert risk_kinds.issubset({
        "low_confidence_ocr",
        "short_content",
        "language_mixed",
        "encoding_warning",
        "structure_fragmented",
        "other",
    })
    # ocr_low_confidence flag should map to low_confidence_ocr risk_kind
    assert "low_confidence_ocr" in risk_kinds
    # markdown_complex_structure + table_structure_uncertain should map to
    # structure_fragmented risk_kind
    assert "structure_fragmented" in risk_kinds


# ---------------------------------------------------------------------------
# Section 6: source_label and source_type projection
# ---------------------------------------------------------------------------


async def test_source_label_with_filename(read_env: asyncpg.Pool) -> None:
    """source_label should include filename for file_ref types."""
    pool = read_env
    user_id = uuid4()
    record_id = uuid4()
    candidate_id = uuid4()
    original_input_id = uuid4()
    await _seed_user(pool, user_id=user_id)
    await _seed_record(pool, record_id=record_id, user_id=user_id)
    await _seed_original_input(
        pool,
        original_input_id=original_input_id,
        record_id=record_id,
        user_id=user_id,
        input_type="file_ref",
        metadata_json={"filename": "report.pdf"},
    )
    await _seed_candidate(
        pool,
        candidate_id=candidate_id,
        record_id=record_id,
        user_id=user_id,
        blocks_json=_short_blocks(),
        source_refs_json={
            "source_type": "pdf_text",
            "original_input_id": str(original_input_id),
            "filename": "report.pdf",
        },
    )

    service = CandidateDocumentReadService(pool=pool)
    result = await service.load_candidate_document(
        record_id=record_id,
        user_id=user_id,
    )

    assert result.response.source_type == "file_ref"
    assert result.response.filename == "report.pdf"
    assert "report.pdf" in result.response.source_label
    assert "上传文件" in result.response.source_label
