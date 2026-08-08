"""Real-Postgres round-trip tests for stable annotation diagnostics.

Covers the frozen chain: analyzer output → ``reading_bases.diagnostics_json``
+ ``reading_units.metadata_json.semantic_integrity_override`` (same
transaction) → ``load_snapshot_facts`` readback → reload re-analysis
equivalence → recorded override policy winning on reload.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import ensure_json_object
from app.schemas.reader_documents import StableDocumentBlock
from app.services.reader_orchestration.automatic_layer_policy import (
    AutomaticLayerPolicy,
    policy_from_unit_metadata,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    persist_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)
from app.services.reader_orchestration.stable_annotation_analysis import (
    ANNOTATION_RANGE_MISMATCH,
    DIAGNOSTICS_VERSION,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.reader_orchestration_test_support import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.asyncio

CLEAN_MD = """# Community Libraries

Community libraries have shaped local learning for more than a century.
They began as small reading rooms supported by volunteers and local donors.
Over time they grew into public institutions with broad collections and
regular programs for children and adults alike.

## Modern Roles

Today a community library is more than a place to borrow books.
It offers free internet access, language classes, and quiet study rooms.
Many libraries also host workshops that teach practical digital skills.
"""


@pytest.fixture
async def diagnostics_db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_ann_diag_{uuid4().hex}"
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


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def _read_base_diagnostics(pool: asyncpg.Pool, record_id: UUID) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.diagnostics_json
            FROM reading_records r
            JOIN reading_bases b ON b.id = r.active_base_id
            WHERE r.id = $1
            """,
            record_id,
        )
    assert row is not None
    return ensure_json_object(row["diagnostics_json"])


async def test_clean_freeze_persists_empty_versioned_diagnostics(
    diagnostics_db_env: asyncpg.Pool,
) -> None:
    pool = diagnostics_db_env
    user_id = await _insert_user(pool)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="clean-sample.md",
        text=CLEAN_MD,
        language="en",
    )

    payload = await _read_base_diagnostics(pool, result.reading_record_id)
    assert payload == {"version": DIAGNOSTICS_VERSION, "items": []}

    # Readback entry: reload re-analysis reproduces the same empty
    # diagnostics, and no unit carries an override.
    repo = ReaderOrchestrationRepository(pool=pool)
    async with pool.acquire() as conn:
        facts = await repo.load_snapshot_facts(
            conn,
            record_id=result.reading_record_id,
            user_id=user_id,
        )
    analysis = facts.build_result.annotation_analysis
    assert analysis is not None
    assert analysis.diagnostics == ()
    assert analysis.policy_overrides == ()
    assert analysis.diagnostics_payload() == payload

    async with pool.acquire() as conn:
        override_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM reading_units
            WHERE reading_record_id = $1
              AND metadata_json ? 'semantic_integrity_override'
            """,
            result.reading_record_id,
        )
    assert override_count == 0


async def test_misaligned_annotation_round_trips_into_override(
    diagnostics_db_env: asyncpg.Pool,
) -> None:
    pool = diagnostics_db_env
    user_id = await _insert_user(pool)
    record_id = uuid4()

    plan = build_stable_document_freeze_plan(
        reading_record_id=str(record_id),
        record_generation=1,
        document_version=1,
        title="Dirty Sample",
        blocks=[
            {
                "block_id": "para_1",
                "order_index": 0,
                "block_type": "paragraph",
                "text_content": "Alpha paragraph.",
            },
            {
                "block_id": "para_2",
                "order_index": 1,
                "block_type": "paragraph",
                "text_content": "Beta paragraph.",
            },
        ],
    )

    # A structurally misaligned block: canonical range overlaps the first
    # unit without exactly matching it. It is appended AFTER plan building
    # (the freeze plan itself stays consistent) so it reaches the analyzer
    # as a raw annotation.
    first_block = next(block for block in plan.blocks if block.block_id == "para_1")
    assert first_block.canonical_text_start_utf16 is not None
    assert first_block.canonical_text_end_utf16 is not None
    bogus = StableDocumentBlock(
        block_id="bogus_overlap",
        order_index=2,
        block_type="paragraph",
        text_content="Bogus overlapping block text.",
        canonical_text_start_utf16=first_block.canonical_text_start_utf16 + 1,
        canonical_text_end_utf16=first_block.canonical_text_end_utf16,
    )
    dirty_plan = plan.model_copy(
        deep=True,
        update={"blocks": [*plan.blocks, bogus]},
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO reading_records (
                    id, user_id, source_type, generation
                ) VALUES ($1, $2, 'markdown', 1)
                """,
                record_id,
                user_id,
            )
            persisted = await persist_stable_document_freeze_plan(
                conn,
                plan=dirty_plan,
                canonicalizer_version="exact_canonical_text_v1",
                builder_version="test_builder",
                segmenter_version="regex_sentence_clause_window_v2",
                language="en",
                user_id=user_id,
            )
            # Snapshot reload requires a committed reader event; publish it
            # through the same repository seam as the service path.
            repository = ReaderOrchestrationRepository(pool=pool)
            now = datetime.now(UTC)
            await repository.set_active_base_and_mark_article_ready(
                conn,
                record_id=record_id,
                base_id=persisted.base_id,
                expected_generation=1,
                updated_at=now,
            )
            await repository.ensure_event_sequence_row(
                conn,
                record_id=record_id,
                updated_at=now,
            )
            sequence = await repository.allocate_event_sequence(
                conn,
                record_id=record_id,
            )
            await repository.insert_reader_event(
                conn,
                event_id=uuid4(),
                record_id=record_id,
                sequence=sequence,
                event_type="article_ready",
                payload_json={
                    "record_id": str(record_id),
                    "base_id": str(persisted.base_id),
                    "generation": 1,
                    "readiness_state": "article_ready",
                    "product_state": "readable_enhancing",
                },
                created_at=now,
            )

    # diagnostics_json readback: the mismatch item is persisted verbatim.
    payload = await _read_base_diagnostics(pool, record_id)
    assert payload["version"] == DIAGNOSTICS_VERSION
    assert [item["code"] for item in payload["items"]] == [
        ANNOTATION_RANGE_MISMATCH
    ]
    assert payload["items"][0]["ref_id"] == "bogus_overlap"

    # The affected unit carries the versioned override in the same
    # generation; the clean unit does not.
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, metadata_json
            FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index
            """,
            record_id,
        )
    assert unit_rows, "expected reading_units"
    overridden = {
        row["unit_id"]: ensure_json_object(row["metadata_json"])
        for row in unit_rows
        if "semantic_integrity_override" in ensure_json_object(row["metadata_json"])
    }
    assert list(overridden) == ["u1"]
    override = overridden["u1"]["semantic_integrity_override"]
    assert override["override_version"] == "structural_integrity_override_v1"
    assert override["reason_code"] == ANNOTATION_RANGE_MISMATCH
    assert AutomaticLayerPolicy.from_mapping(override["policy"]) == (
        AutomaticLayerPolicy.all_off()
    )

    # Recorded override wins on read: the recorded semantic policy (if any)
    # cannot re-open the unit.
    resolved = policy_from_unit_metadata(overridden["u1"])
    assert resolved.policy == AutomaticLayerPolicy.all_off()

    # Reload round-trip: the reload re-analysis reproduces the exact
    # persisted diagnostics and the same override attribution.
    repo = ReaderOrchestrationRepository(pool=pool)
    async with pool.acquire() as conn:
        facts = await repo.load_snapshot_facts(
            conn,
            record_id=record_id,
            user_id=user_id,
        )
    analysis = facts.build_result.annotation_analysis
    assert analysis is not None
    assert analysis.diagnostics_payload() == payload
    assert [(o.unit_id, o.reason_code) for o in analysis.policy_overrides] == [
        ("u1", ANNOTATION_RANGE_MISMATCH),
    ]
