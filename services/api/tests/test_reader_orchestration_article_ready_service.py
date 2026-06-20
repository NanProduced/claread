from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database.connection import init_connection
from app.services.reader_orchestration import (
    ArticleReadyPersistenceService,
    LoadedReaderSnapshotFacts,
    LowImpactReadingBaseBuildInput,
    PlainTextArticleReadySubmitRequest,
    ReaderOrchestrationRepository,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


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


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def _insert_record(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    generation: int = 1,
    title: str = "Reader Persistence Test",
    language: str = "en",
) -> UUID:
    async with pool.acquire() as conn:
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (user_id, source_type, title, language, generation)
            VALUES ($1, 'text', $2, $3, $4)
            RETURNING id
            """,
            user_id,
            title,
            language,
            generation,
        )
    assert isinstance(record_id, UUID)
    return record_id


async def _insert_base(
    pool: asyncpg.Pool,
    record_id: UUID,
    *,
    base_version: int = 1,
    record_generation: int = 1,
    text: str = "Base text.",
    status: str = "active",
) -> UUID:
    async with pool.acquire() as conn:
        base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id,
                base_version,
                record_generation,
                text,
                content_sha256,
                content_utf16_length,
                canonicalizer_version,
                builder_version,
                segmenter_version,
                language,
                title_snapshot,
                navigation_json,
                status
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                'd3-p3-canonicalizer',
                'd3-p3-builder',
                'd3-p3-segmenter',
                'en',
                'Stored title',
                '{"units":[]}'::jsonb,
                $7
            )
            RETURNING id
            """,
            record_id,
            base_version,
            record_generation,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            utf16_code_unit_length(text),
            status,
        )
    assert isinstance(base_id, UUID)
    return base_id


@pytest.fixture
async def reader_service_env() -> asyncpg.Pool:
    schema_name = f"test_reader_article_ready_{uuid4().hex}"
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


class FailingEventInsertRepository(ReaderOrchestrationRepository):
    async def insert_reader_event(  # type: ignore[override]
        self,
        conn: asyncpg.Connection,
        *,
        event_id: UUID,
        record_id: UUID,
        sequence: int,
        event_type: str,
        payload_json: dict[str, object],
        created_at,
    ) -> None:
        raise RuntimeError("boom-before-event-insert")


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeTransaction:
        self._conn.transaction_entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._conn.transaction_exited += 1


class _FakeConnection:
    def __init__(self) -> None:
        self.transaction_kwargs: dict[str, object] | None = None
        self.transaction_entered = 0
        self.transaction_exited = 0

    def transaction(self, **kwargs: object) -> _FakeTransaction:
        self.transaction_kwargs = dict(kwargs)
        return _FakeTransaction(self)


class _FakeAcquireContext:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._conn)


class _FakeSnapshotRepository:
    def __init__(self, facts: LoadedReaderSnapshotFacts) -> None:
        self.facts = facts
        self.calls: list[dict[str, object]] = []

    def get_pool(self) -> asyncpg.Pool:
        raise AssertionError("test should provide pool explicitly")

    async def load_snapshot_facts(
        self,
        conn: _FakeConnection,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> LoadedReaderSnapshotFacts:
        self.calls.append(
            {
                "conn": conn,
                "record_id": record_id,
                "user_id": user_id,
                "expected_base_id": expected_base_id,
                "expected_generation": expected_generation,
            }
        )
        return self.facts


async def test_submit_plain_text_persists_article_ready_domain_facts(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="First sentence.\n\nSecond paragraph for reading.",
        title="Persistence Example",
        language="en",
        source_metadata={"source_kind": "manual_submit"},
        client_record_id="client-rec-1",
    )

    result = await service.submit_plain_text(request)

    assert result.article_ready_sequence == 1
    assert result.snapshot.record_id == str(result.record_id)
    assert result.snapshot.base.base_id == str(result.base_id)
    assert result.snapshot.last_event_sequence == 1

    async with reader_service_env.acquire() as conn:
        record_row = await conn.fetchrow(
            """
            SELECT user_id, client_record_id, title, language, lifecycle_status,
                   product_state, readiness_state, generation, active_base_id
            FROM reading_records
            WHERE id = $1
            """,
            result.record_id,
        )
        assert record_row is not None
        assert record_row["user_id"] == user_id
        assert record_row["client_record_id"] == "client-rec-1"
        assert record_row["title"] == "Persistence Example"
        assert record_row["language"] == "en"
        assert record_row["lifecycle_status"] == "active"
        assert record_row["product_state"] == "readable_enhancing"
        assert record_row["readiness_state"] == "article_ready"
        assert record_row["generation"] == 1
        assert record_row["active_base_id"] == result.base_id

        input_row = await conn.fetchrow(
            """
            SELECT source_text, content_sha256, metadata_json
            FROM original_inputs
            WHERE id = $1
            """,
            result.original_input_id,
        )
        assert input_row is not None
        assert input_row["source_text"] == request.plain_text
        assert input_row["content_sha256"] == hashlib.sha256(
            request.plain_text.encode("utf-8")
        ).hexdigest()
        assert input_row["metadata_json"] == {"source_kind": "manual_submit"}

        base_row = await conn.fetchrow(
            """
            SELECT text, status, navigation_json
            FROM reading_bases
            WHERE id = $1
            """,
            result.base_id,
        )
        assert base_row is not None
        assert base_row["status"] == "active"
        assert isinstance(base_row["navigation_json"], dict)
        assert base_row["navigation_json"]["units"]

        unit_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reading_units WHERE base_id = $1",
            result.base_id,
        )
        anchor_count = await conn.fetchval(
            "SELECT COUNT(*) FROM anchor_segments WHERE base_id = $1",
            result.base_id,
        )
        assert isinstance(unit_count, int) and unit_count >= 1
        assert isinstance(anchor_count, int) and anchor_count >= 1

        event_row = await conn.fetchrow(
            """
            SELECT sequence, event_type, payload_json
            FROM reader_events
            WHERE id = $1
            """,
            result.article_ready_event_id,
        )
        assert event_row is not None
        assert event_row["sequence"] == 1
        assert event_row["event_type"] == "article_ready"
        assert event_row["payload_json"]["base_id"] == str(result.base_id)

        seq_row = await conn.fetchrow(
            """
            SELECT next_sequence
            FROM reader_event_sequences
            WHERE reading_record_id = $1
            """,
            result.record_id,
        )
        assert seq_row is not None
        assert seq_row["next_sequence"] == 2


async def test_load_snapshot_uses_repeatable_read_readonly_transaction() -> None:
    record_id = uuid4()
    user_id = uuid4()
    base_id = uuid4()
    build_result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=str(record_id),
            base_id=str(base_id),
            source_text="Repeatable read snapshot.",
            title="Repeatable Read",
            language="en",
        )
    )
    facts = LoadedReaderSnapshotFacts(
        build_result=build_result,
        last_event_sequence=1,
        snapshot_taken_at=datetime.now(UTC),
        enhancement_layers=(),
        parsed_decisions=(),
    )
    conn = _FakeConnection()
    repository = _FakeSnapshotRepository(facts)
    service = ArticleReadyPersistenceService(
        repository=repository,
        pool=_FakePool(conn),  # type: ignore[arg-type]
    )

    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
        expected_base_id=base_id,
        expected_generation=1,
    )

    assert conn.transaction_kwargs == {
        "isolation": "repeatable_read",
        "readonly": True,
    }
    assert conn.transaction_entered == 1
    assert conn.transaction_exited == 1
    assert repository.calls == [
        {
            "conn": conn,
            "record_id": record_id,
            "user_id": user_id,
            "expected_base_id": base_id,
            "expected_generation": 1,
        }
    ]
    assert snapshot.last_event_sequence == 1


async def test_submit_plain_text_rolls_back_without_partial_rows(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    failing_repository = FailingEventInsertRepository(pool=reader_service_env)
    failing_service = ArticleReadyPersistenceService(
        repository=failing_repository,
        pool=reader_service_env,
    )
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="Rollback example.\n\nSecond paragraph.",
        title="Rollback Example",
        client_record_id="rollback-client-id",
    )

    with pytest.raises(RuntimeError, match="boom-before-event-insert"):
        await failing_service.submit_plain_text(request)

    async with reader_service_env.acquire() as conn:
        for table_name in (
            "reading_records",
            "original_inputs",
            "reading_bases",
            "reading_units",
            "anchor_segments",
            "reader_event_sequences",
            "reader_events",
        ):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")
            assert count == 0

    service = ArticleReadyPersistenceService(pool=reader_service_env)
    result = await service.submit_plain_text(request)
    assert result.article_ready_sequence == 1


async def test_active_base_service_invariant_same_record_generation_and_status(
    reader_service_env: asyncpg.Pool,
) -> None:
    repository = ReaderOrchestrationRepository(pool=reader_service_env)
    user_id = await _insert_user(reader_service_env)
    record_id = await _insert_record(reader_service_env, user_id, generation=1)
    other_record_id = await _insert_record(reader_service_env, user_id, generation=1, title="Other")
    base_other_record = await _insert_base(reader_service_env, other_record_id, record_generation=1)
    base_wrong_generation = await _insert_base(
        reader_service_env,
        record_id,
        base_version=2,
        record_generation=2,
    )
    base_superseded = await _insert_base(
        reader_service_env,
        record_id,
        base_version=3,
        record_generation=1,
        status="superseded",
    )

    async with reader_service_env.acquire() as conn:
        with pytest.raises(ValueError, match="same reading record"):
            await repository.set_active_base_and_mark_article_ready(
                conn,
                record_id=record_id,
                base_id=base_other_record,
                expected_generation=1,
                updated_at=await conn.fetchval("SELECT NOW()"),
            )

        with pytest.raises(ValueError, match="reading record generation"):
            await repository.set_active_base_and_mark_article_ready(
                conn,
                record_id=record_id,
                base_id=base_wrong_generation,
                expected_generation=1,
                updated_at=await conn.fetchval("SELECT NOW()"),
            )

        with pytest.raises(ValueError, match="status 'active'"):
            await repository.set_active_base_and_mark_article_ready(
                conn,
                record_id=record_id,
                base_id=base_superseded,
                expected_generation=1,
                updated_at=await conn.fetchval("SELECT NOW()"),
            )


async def test_snapshot_reloads_from_db_facts_equivalent_to_builder(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="First sentence.\n\nSecond paragraph for equality.",
        title="Equivalence Example",
        language="en",
    )

    result = await service.submit_plain_text(request)

    expected_build_result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=str(result.record_id),
            base_id=str(result.base_id),
            source_text=request.plain_text,
            title="Equivalence Example",
            language="en",
        )
    )
    expected_snapshot = build_reader_plate_snapshot(
        expected_build_result,
        snapshot_taken_at=result.snapshot.snapshot_taken_at,
        last_event_sequence=result.article_ready_sequence,
    )

    assert result.snapshot.model_dump(mode="json") == expected_snapshot.model_dump(mode="json")


async def test_load_snapshot_rejects_wrong_expected_base_or_generation(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="Snapshot rejection example.",
            title="Snapshot Reject",
        )
    )

    with pytest.raises(ValueError, match="does not match expected"):
        await service.load_snapshot(
            record_id=result.record_id,
            user_id=user_id,
            expected_base_id=uuid4(),
        )

    with pytest.raises(ValueError, match="does not match expected"):
        await service.load_snapshot(
            record_id=result.record_id,
            user_id=user_id,
            expected_generation=2,
        )


async def test_load_snapshot_rejects_visible_unit_gap_corruption(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="Alpha.\n\nBeta.",
            title="Gap Corruption",
        )
    )

    async with reader_service_env.acquire() as conn:
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE id = $1",
            result.base_id,
        )
        second_unit = await conn.fetchrow(
            """
            SELECT unit_id, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE base_id = $1
              AND order_index = 2
            """,
            result.base_id,
        )
        assert isinstance(base_text, str)
        assert second_unit is not None
        second_anchor = await conn.fetchrow(
            """
            SELECT anchor_segment_id, base_end_utf16
            FROM anchor_segments
            WHERE base_id = $1
              AND unit_id = $2
            """,
            result.base_id,
            second_unit["unit_id"],
        )
        assert second_anchor is not None

        new_start = int(second_unit["base_start_utf16"]) + 1
        new_text = slice_by_utf16_offsets(
            base_text,
            new_start,
            int(second_unit["base_end_utf16"]),
        )
        assert new_text is not None

        await conn.execute(
            """
            UPDATE reading_units
            SET base_start_utf16 = $1,
                text_hash = $2
            WHERE base_id = $3
              AND unit_id = $4
            """,
            new_start,
            compute_text_range_hash(new_text),
            result.base_id,
            second_unit["unit_id"],
        )
        await conn.execute(
            """
            UPDATE anchor_segments
            SET base_start_utf16 = $1,
                unit_start_utf16 = 0,
                unit_end_utf16 = $2,
                text_hash = $3
            WHERE base_id = $4
              AND anchor_segment_id = $5
            """,
            new_start,
            utf16_code_unit_length(new_text),
            compute_text_range_hash(new_text),
            result.base_id,
            second_anchor["anchor_segment_id"],
        )

    with pytest.raises(ValueError, match="unit gaps must be whitespace only"):
        await service.load_snapshot(record_id=result.record_id, user_id=user_id)


async def test_load_snapshot_rejects_unit_overlap_corruption(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="Alpha.\n\nBeta.",
            title="Overlap Corruption",
        )
    )

    async with reader_service_env.acquire() as conn:
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE id = $1",
            result.base_id,
        )
        second_unit = await conn.fetchrow(
            """
            SELECT unit_id, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE base_id = $1
              AND order_index = 2
            """,
            result.base_id,
        )
        assert isinstance(base_text, str)
        assert second_unit is not None
        second_anchor = await conn.fetchrow(
            """
            SELECT anchor_segment_id, base_end_utf16
            FROM anchor_segments
            WHERE base_id = $1
              AND unit_id = $2
            """,
            result.base_id,
            second_unit["unit_id"],
        )
        assert second_anchor is not None

        new_start = int(second_unit["base_start_utf16"]) - 3
        new_text = slice_by_utf16_offsets(
            base_text,
            new_start,
            int(second_unit["base_end_utf16"]),
        )
        assert new_text is not None

        await conn.execute(
            """
            UPDATE reading_units
            SET base_start_utf16 = $1,
                text_hash = $2
            WHERE base_id = $3
              AND unit_id = $4
            """,
            new_start,
            compute_text_range_hash(new_text),
            result.base_id,
            second_unit["unit_id"],
        )
        await conn.execute(
            """
            UPDATE anchor_segments
            SET base_start_utf16 = $1,
                unit_start_utf16 = 0,
                unit_end_utf16 = $2,
                text_hash = $3
            WHERE base_id = $4
              AND anchor_segment_id = $5
            """,
            new_start,
            utf16_code_unit_length(new_text),
            compute_text_range_hash(new_text),
            result.base_id,
            second_anchor["anchor_segment_id"],
        )

    with pytest.raises(ValueError, match="unit spans must not overlap"):
        await service.load_snapshot(record_id=result.record_id, user_id=user_id)


async def test_load_snapshot_rejects_missing_anchor_coverage_corruption(
    reader_service_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(reader_service_env)
    service = ArticleReadyPersistenceService(pool=reader_service_env)
    result = await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text="Alpha beta.",
            title="Anchor Coverage Corruption",
        )
    )

    async with reader_service_env.acquire() as conn:
        base_text = await conn.fetchval(
            "SELECT text FROM reading_bases WHERE id = $1",
            result.base_id,
        )
        anchor = await conn.fetchrow(
            """
            SELECT anchor_segment_id, base_start_utf16, base_end_utf16,
                   unit_start_utf16, unit_end_utf16
            FROM anchor_segments
            WHERE base_id = $1
              AND order_index = 1
            """,
            result.base_id,
        )
        assert isinstance(base_text, str)
        assert anchor is not None

        new_base_start = int(anchor["base_start_utf16"]) + 1
        new_unit_start = int(anchor["unit_start_utf16"]) + 1
        new_text = slice_by_utf16_offsets(
            base_text,
            new_base_start,
            int(anchor["base_end_utf16"]),
        )
        assert new_text is not None

        await conn.execute(
            """
            UPDATE anchor_segments
            SET base_start_utf16 = $1,
                unit_start_utf16 = $2,
                text_hash = $3
            WHERE base_id = $4
              AND anchor_segment_id = $5
            """,
            new_base_start,
            new_unit_start,
            compute_text_range_hash(new_text),
            result.base_id,
            anchor["anchor_segment_id"],
        )

    with pytest.raises(ValueError, match="leading anchor gap"):
        await service.load_snapshot(record_id=result.record_id, user_id=user_id)


def test_article_ready_service_modules_do_not_reference_render_scene_json() -> None:
    for path in (
        API_ROOT / "app" / "services" / "reader_orchestration" / "article_ready_service.py",
        API_ROOT / "app" / "services" / "reader_orchestration" / "repository.py",
    ):
        assert "render_scene_json" not in path.read_text(encoding="utf-8")
