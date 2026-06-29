from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.database.connection import init_connection
from app.schemas.reader_orchestration import (
    DEFAULT_READER_ORCHESTRATION_READING_GOAL,
    DEFAULT_READER_ORCHESTRATION_READING_VARIANT,
    READER_ORCHESTRATION_GOAL_VARIANT_MAP,
    ReaderPlainTextSubmitRequest,
    ReaderSnapshotRecord,
    ReaderSourceArtifactSubmitInputRequest,
    ReaderStableReadyInputSubmitRequest,
    ReaderUnifiedInputSubmitRequest,
)
from app.services.reader_orchestration import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------#
# Schema-layer contract: legal combos accepted, illegal combos rejected.
# ---------------------------------------------------------------------------#


_LEGAL_STRATEGY_COMBOS = [
    (g, v)
    for g, variants in READER_ORCHESTRATION_GOAL_VARIANT_MAP.items()
    for v in sorted(variants)
]


@pytest.mark.parametrize("goal,variant", _LEGAL_STRATEGY_COMBOS)
def test_reader_plain_text_submit_accepts_legal_strategy_combos(
    goal: str, variant: str
) -> None:
    request = ReaderPlainTextSubmitRequest(
        plain_text="Some reading text.",
        reading_goal=goal,  # type: ignore[arg-type]
        reading_variant=variant,  # type: ignore[arg-type]
    )
    assert request.reading_goal == goal
    assert request.reading_variant == variant


def test_reader_plain_text_submit_defaults_strategy_when_omitted() -> None:
    request = ReaderPlainTextSubmitRequest(plain_text="Some reading text.")
    assert request.reading_goal == DEFAULT_READER_ORCHESTRATION_READING_GOAL
    assert request.reading_variant == DEFAULT_READER_ORCHESTRATION_READING_VARIANT


@pytest.mark.parametrize(
    "goal,variant",
    [
        ("daily_reading", "gaokao"),
        ("daily_reading", "ielts_toefl"),
        ("exam", "beginner_reading"),
        ("exam", "intensive_reading"),
    ],
)
def test_reader_plain_text_submit_rejects_cross_goal_variants(
    goal: str, variant: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ReaderPlainTextSubmitRequest(
            plain_text="Some reading text.",
            reading_goal=goal,  # type: ignore[arg-type]
            reading_variant=variant,  # type: ignore[arg-type]
        )
    assert "does not belong to" in str(exc_info.value)


@pytest.mark.parametrize(
    "goal,variant",
    [
        ("academic", "academic_general"),
        ("academic", "beginner_reading"),
        ("academic_general", "beginner_reading"),
        ("daily_reading", "academic_general"),
    ],
)
def test_reader_plain_text_submit_fails_closed_for_academic(goal: str, variant: str) -> None:
    """`academic` / `academic_general` are not wired into the new orchestration.

    Submitting them must fail closed at the schema Literal layer rather than be
    silently mapped onto a daily/exam variant. `extra="forbid"` plus the
    Literal type means an unknown value is reported as a Literal validation
    error before the model_validator can even run.
    """
    with pytest.raises(ValidationError):
        ReaderPlainTextSubmitRequest(
            plain_text="Some reading text.",
            reading_goal=goal,  # type: ignore[arg-type]
            reading_variant=variant,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------#
# Reserved-key guard: source_metadata must not carry strategy keys at the
# top level. Prevents a client from splitting record truth (first-class
# columns) from Ask strategy (which legacy code still reads from
# source_metadata). Nested keys inside sub-objects are not affected.
# ---------------------------------------------------------------------------#


@pytest.mark.parametrize(
    "metadata",
    [
        {"reading_goal": "exam"},
        {"reading_variant": "gaokao"},
        {"reading_goal": "daily_reading", "reading_variant": "intensive_reading"},
        {"source_kind": "manual", "reading_goal": "exam"},
    ],
)
def test_plain_text_submit_rejects_reserved_strategy_keys_in_source_metadata(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ReaderPlainTextSubmitRequest(
            plain_text="Some reading text.",
            source_metadata=metadata,
        )
    assert "reserved strategy keys" in str(exc_info.value)


def test_stable_ready_input_submit_rejects_reserved_strategy_keys_in_source_metadata() -> None:
    with pytest.raises(ValidationError):
        ReaderStableReadyInputSubmitRequest(
            source_type="paste_text",
            text="Some reading text.",
            source_metadata={"reading_goal": "exam"},
        )


def test_unified_input_submit_inherits_reserved_strategy_key_rejection() -> None:
    """ReaderUnifiedInputSubmitRequest inherits the guard from
    ReaderStableReadyInputSubmitRequest via Pydantic field_validator
    inheritance."""
    with pytest.raises(ValidationError):
        ReaderUnifiedInputSubmitRequest(
            source_type="paste_text",
            text="Some reading text.",
            source_metadata={"reading_variant": "gaokao"},
        )


def test_source_artifact_submit_rejects_reserved_strategy_keys_in_source_metadata() -> None:
    with pytest.raises(ValidationError):
        ReaderSourceArtifactSubmitInputRequest(
            artifact_id=str(uuid4()),
            source_metadata={"reading_goal": "exam"},
        )


def test_plain_text_submit_accepts_nested_strategy_keys_in_source_metadata() -> None:
    """Nested keys inside sub-objects are not reserved. Only the top level of
    `source_metadata` is policed."""
    request = ReaderPlainTextSubmitRequest(
        plain_text="Some reading text.",
        source_metadata={
            "source_kind": "manual",
            "provenance": {"reading_goal": "exam", "reading_variant": "gaokao"},
        },
    )
    assert request.source_metadata is not None
    assert request.source_metadata["provenance"]["reading_goal"] == "exam"


def test_plain_text_submit_accepts_none_and_empty_source_metadata() -> None:
    request_none = ReaderPlainTextSubmitRequest(plain_text="Some reading text.")
    assert request_none.source_metadata is None

    request_empty = ReaderPlainTextSubmitRequest(
        plain_text="Some reading text.",
        source_metadata={},
    )
    assert request_empty.source_metadata == {}


def test_stable_ready_input_submit_rejects_cross_goal_variants() -> None:
    with pytest.raises(ValidationError):
        ReaderStableReadyInputSubmitRequest(
            source_type="paste_text",
            text="Some reading text.",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="intensive_reading",  # type: ignore[arg-type]
        )


def test_unified_input_submit_rejects_cross_goal_variants() -> None:
    with pytest.raises(ValidationError):
        ReaderUnifiedInputSubmitRequest(
            source_type="paste_text",
            text="Some reading text.",
            reading_goal="daily_reading",  # type: ignore[arg-type]
            reading_variant="gaokao",  # type: ignore[arg-type]
        )


def test_source_artifact_submit_rejects_cross_goal_variants() -> None:
    with pytest.raises(ValidationError):
        ReaderSourceArtifactSubmitInputRequest(
            artifact_id=str(uuid4()),
            reading_goal="daily_reading",  # type: ignore[arg-type]
            reading_variant="cet",  # type: ignore[arg-type]
        )


def test_reader_snapshot_record_rejects_cross_goal_variants() -> None:
    with pytest.raises(ValidationError):
        ReaderSnapshotRecord(
            title="Bad Pair",
            created_at=datetime.now(UTC),
            source_type="text",
            generation=1,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            reading_goal="exam",  # type: ignore[arg-type]
            reading_variant="intensive_reading",  # type: ignore[arg-type]
        )


def test_reader_snapshot_record_defaults_strategy_for_legacy_fixtures() -> None:
    record = ReaderSnapshotRecord(
        title="Legacy Fixture",
        created_at=datetime.now(UTC),
        source_type="text",
        generation=1,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )
    assert record.reading_goal == DEFAULT_READER_ORCHESTRATION_READING_GOAL
    assert record.reading_variant == DEFAULT_READER_ORCHESTRATION_READING_VARIANT


# ---------------------------------------------------------------------------#
# DB-backed persistence + snapshot exposure.
# ---------------------------------------------------------------------------#


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
async def reader_strategy_env() -> asyncpg.Pool:
    schema_name = f"test_reader_strategy_{uuid4().hex}"
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


async def test_submit_plain_text_persists_reading_strategy_columns(
    reader_strategy_env: asyncpg.Pool,
) -> None:
    user_id = await reader_strategy_env.fetchval(
        "INSERT INTO users DEFAULT VALUES RETURNING id"
    )
    service = ArticleReadyPersistenceService(pool=reader_strategy_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="First sentence.\n\nSecond paragraph for reading.",
        title="Strategy Persistence Example",
        language="en",
        source_metadata={"source_kind": "manual_submit"},
        client_record_id="client-strategy-1",
        reading_goal="exam",
        reading_variant="gaokao",
    )

    result = await service.submit_plain_text(request)

    assert result.snapshot.record.reading_goal == "exam"
    assert result.snapshot.record.reading_variant == "gaokao"

    async with reader_strategy_env.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reading_goal, reading_variant
            FROM reading_records
            WHERE id = $1
            """,
            result.record_id,
        )
        assert row is not None
        assert row["reading_goal"] == "exam"
        assert row["reading_variant"] == "gaokao"

        # Strategy MUST NOT be inferred from source_metadata. `reading_records`
        # has no `source_metadata` column at all; the client-supplied metadata
        # envelope lives on `original_inputs.metadata_json`. Asserting that
        # envelope does not carry strategy keys is the meaningful check.
        original_input_row = await conn.fetchrow(
            "SELECT metadata_json FROM original_inputs WHERE id = $1",
            result.original_input_id,
        )
        assert original_input_row is not None
        assert "reading_goal" not in original_input_row["metadata_json"]
        assert "reading_variant" not in original_input_row["metadata_json"]


async def test_submit_plain_text_defaults_strategy_when_client_omits_fields(
    reader_strategy_env: asyncpg.Pool,
) -> None:
    user_id = await reader_strategy_env.fetchval(
        "INSERT INTO users DEFAULT VALUES RETURNING id"
    )
    service = ArticleReadyPersistenceService(pool=reader_strategy_env)
    request = PlainTextArticleReadySubmitRequest(
        user_id=user_id,
        plain_text="A short reading for default strategy.",
        title="Default Strategy Example",
        language="en",
        source_metadata={"source_kind": "manual_submit"},
        client_record_id="client-strategy-default",
    )

    result = await service.submit_plain_text(request)

    assert result.snapshot.record.reading_goal == DEFAULT_READER_ORCHESTRATION_READING_GOAL
    assert (
        result.snapshot.record.reading_variant
        == DEFAULT_READER_ORCHESTRATION_READING_VARIANT
    )

    async with reader_strategy_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT reading_goal, reading_variant FROM reading_records WHERE id = $1",
            result.record_id,
        )
        assert row is not None
        assert row["reading_goal"] == DEFAULT_READER_ORCHESTRATION_READING_GOAL
        assert row["reading_variant"] == DEFAULT_READER_ORCHESTRATION_READING_VARIANT


async def test_db_check_constraint_rejects_variant_not_in_goal(
    reader_strategy_env: asyncpg.Pool,
) -> None:
    """Defense-in-depth: the DB CHECK constraint also fails closed."""
    user_id = await reader_strategy_env.fetchval(
        "INSERT INTO users DEFAULT VALUES RETURNING id"
    )
    async with reader_strategy_env.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reading_records (
                    user_id, source_type, title, language, generation,
                    reading_goal, reading_variant
                )
                VALUES ($1, 'text', 'Bad Pair', 'en', 1, 'daily_reading', 'gaokao')
                """,
                user_id,
            )


async def test_db_check_constraint_rejects_academic_goal(
    reader_strategy_env: asyncpg.Pool,
) -> None:
    user_id = await reader_strategy_env.fetchval(
        "INSERT INTO users DEFAULT VALUES RETURNING id"
    )
    async with reader_strategy_env.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reading_records (
                    user_id, source_type, title, language, generation,
                    reading_goal, reading_variant
                )
                VALUES ($1, 'text', 'Academic Pair', 'en', 1, 'academic', 'academic_general')
                """,
                user_id,
            )
