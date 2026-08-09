from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.services.model_execution_journal import (
    CaptureEnvelopeConflictError,
    ExecutionIdentity,
    JournalConflictError,
    prepare_capture_envelope,
)
from app.services.model_execution_journal.service import (
    ModelExecutionJournalService,
)

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = re.sub(
    r"^\s*SET search_path = public, pg_catalog;\s*$",
    "",
    (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    ),
    flags=re.MULTILINE,
)


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://claread:claread_dev@127.0.0.1:5432/claread",
    )


@pytest.fixture
async def journal_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_model_execution_journal_{uuid4().hex}"
    try:
        admin = await asyncpg.connect(_database_url())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for journal tests: {exc}")

    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            _database_url(),
            min_size=1,
            max_size=8,
            init=init_connection,
            server_settings={
                "search_path": f'"{schema_name}", public',
            },
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


def _identity() -> ExecutionIdentity:
    job_id = uuid4()
    return ExecutionIdentity(
        invocation_key=f"reader:grammar_batch:{job_id}:1:1",
        reader_job_id=None,
        reader_run_id=None,
        attempt_ordinal=1,
        execution_slot=1,
    )


def _prepared(
    *,
    input_tokens: int = 10,
    provider: str = "fake",
    unit_id: str = "unit-1",
):
    return prepare_capture_envelope(
        invocation_kind="reader.grammar_batch",
        resume_payload_kind="reader.grammar_batch.result",
        resume_payload_schema_version=1,
        usage_event_draft_schema_version=1,
        normalized_payload={
            "outputs": [
                {
                    "unit_id": unit_id,
                    "output": {
                        "schema_version": 1,
                        "grammar_notes": [],
                        "sentence_analyses": [],
                    },
                }
            ],
            "diagnostics": None,
        },
        usage_event_draft={
            "usage_scope": "system_internal",
            "capability_code": "reader_grammar_bundle",
            "billing_mode": "internal_only",
            "status": "model_call_completed",
            "model_route": "reader_layer_grammar_bundle",
            "model_provider": provider,
            "model_name": "fake-grammar",
            "usage_data": {
                "input_tokens": input_tokens,
                "output_tokens": 5,
                "total_tokens": input_tokens + 5,
            },
            "metadata_json": {"unit_count": 1},
        },
    )


async def test_begin_allows_only_the_new_durable_started_receipt(
    journal_pool: asyncpg.Pool,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()

    first = await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    second = await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )

    assert first.provider_call_allowed is True
    assert first.capture_state == "started"
    assert second.provider_call_allowed is False
    assert second.journal_id == first.journal_id

    conflicting_identity = ExecutionIdentity(
        invocation_key=identity.invocation_key,
        reader_job_id=None,
        reader_run_id=None,
        attempt_ordinal=1,
        execution_slot=2,
    )
    with pytest.raises(JournalConflictError, match="execution_identity_conflict"):
        await service.begin_execution(
            identity=conflicting_identity,
            invocation_kind="reader.grammar_batch",
        )


async def test_begin_rejects_zero_execution_slot(
    journal_pool: asyncpg.Pool,
) -> None:
    identity = _identity()
    zero_slot = ExecutionIdentity(
        invocation_key=f"reader:grammar_batch:{uuid4()}:1:0",
        reader_job_id=None,
        reader_run_id=None,
        attempt_ordinal=1,
        execution_slot=0,
    )

    with pytest.raises(ValueError, match="invalid_execution_identity"):
        await ModelExecutionJournalService(journal_pool).begin_execution(
            identity=zero_slot,
            invocation_kind="reader.grammar_batch",
        )

    assert identity.execution_slot == 1


@pytest.mark.parametrize(
    "conflicting_prepared",
    [
        _prepared(input_tokens=11),
        _prepared(provider="different-provider"),
        _prepared(unit_id="different-unit"),
    ],
    ids=["token-totals", "model-identity", "typed-result"],
)
async def test_capture_is_idempotent_only_for_the_complete_envelope(
    journal_pool: asyncpg.Pool,
    conflicting_prepared,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )

    first = await service.capture_execution(
        identity=identity,
        prepared=_prepared(),
    )
    same = await service.capture_execution(
        identity=identity,
        prepared=_prepared(),
    )

    assert first.idempotent_replay is False
    assert same.idempotent_replay is True
    assert same.journal_id == first.journal_id
    with pytest.raises(
        CaptureEnvelopeConflictError,
        match="capture_envelope_conflict",
    ):
        await service.capture_execution(
            identity=identity,
            prepared=conflicting_prepared,
        )


async def test_materializer_reconciles_once_and_never_replays_usage(
    journal_pool: asyncpg.Pool,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())

    first, second = await asyncio.gather(
        service.materialize_pending(limit=1),
        service.materialize_pending(limit=1),
    )
    after_crash_retry = await service.materialize_pending(limit=1)

    assert first.reconciled + second.reconciled == 1
    assert after_crash_retry.reconciled == 0
    async with journal_pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT count(*) FROM ai_usage_events WHERE invocation_key = $1",
            identity.invocation_key,
        )
        row = await conn.fetchrow(
            """
            SELECT capture_state, usage_delivery_state, ai_usage_event_id
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
    assert event_count == 1
    assert row["capture_state"] == "captured"
    assert row["usage_delivery_state"] == "reconciled"
    assert row["ai_usage_event_id"] is not None


@pytest.mark.parametrize(
    ("column", "path", "replacement"),
    [
        ("normalized_payload_json", ["outputs", "0", "unit_id"], "unit-2"),
        ("usage_event_draft_json", ["usage_data", "input_tokens"], 11),
        ("usage_event_draft_json", ["model_name"], "tampered-model"),
        ("usage_event_draft_json", ["model_provider"], "tampered-provider"),
        (
            "usage_event_draft_json",
            [],
            {"usage_scope": "anonymous_trial", "billing_mode": "trial"},
        ),
    ],
    ids=["typed-result", "tokens", "model", "provider", "billing"],
)
async def test_materializer_dead_letters_structurally_valid_envelope_tampering(
    journal_pool: asyncpg.Pool,
    column: str,
    path: list[str],
    replacement: object,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())
    async with journal_pool.acquire() as conn:
        if path:
            await conn.execute(
                f"""
                UPDATE ai_model_execution_journal
                SET {column} = jsonb_set({column}, $2::text[], $3::jsonb)
                WHERE invocation_key = $1
                """,
                identity.invocation_key,
                path,
                json.dumps(replacement),
            )
        else:
            await conn.execute(
                f"""
                UPDATE ai_model_execution_journal
                SET {column} = {column} || $2::jsonb
                WHERE invocation_key = $1
                """,
                identity.invocation_key,
                replacement,
            )

    summary = await service.materialize_pending(limit=1, max_attempts=1)

    assert summary.dead_lettered == 1
    async with journal_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT usage_delivery_state, delivery_last_error_code
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM ai_usage_events WHERE invocation_key = $1",
            identity.invocation_key,
        )
    assert row["usage_delivery_state"] == "dead_letter"
    assert row["delivery_last_error_code"] == "stored_capture_envelope_mismatch"
    assert event_count == 0


@pytest.mark.parametrize(
    "byte_column",
    ["resume_payload_bytes", "usage_event_draft_bytes"],
)
async def test_materializer_dead_letters_stored_payload_byte_count_mismatch(
    journal_pool: asyncpg.Pool,
    byte_column: str,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())
    async with journal_pool.acquire() as conn:
        await conn.execute(
            f"""
            UPDATE ai_model_execution_journal
            SET {byte_column} = {byte_column} + 1
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )

    summary = await service.materialize_pending(limit=1, max_attempts=1)

    assert summary.dead_lettered == 1
    async with journal_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT delivery_last_error_code
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM ai_usage_events WHERE invocation_key = $1",
            identity.invocation_key,
        )
    assert row["delivery_last_error_code"] == "stored_capture_envelope_mismatch"
    assert event_count == 0


async def test_materializer_dead_letters_unsupported_stored_schema_version(
    journal_pool: asyncpg.Pool,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())
    async with journal_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET usage_event_draft_schema_version = 2
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )

    summary = await service.materialize_pending(limit=1, max_attempts=1)

    assert summary.dead_lettered == 1
    async with journal_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT delivery_last_error_code
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM ai_usage_events WHERE invocation_key = $1",
            identity.invocation_key,
        )
    assert row["delivery_last_error_code"] == "unsupported_usage_event_draft"
    assert event_count == 0


async def test_materializer_dead_letters_strictly_malformed_stored_draft(
    journal_pool: asyncpg.Pool,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())
    async with journal_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ai_model_execution_journal
            SET usage_event_draft_json =
                usage_event_draft_json || '{"unexpected":"value"}'::jsonb
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )

    summary = await service.materialize_pending(limit=1, max_attempts=1)

    assert summary.dead_lettered == 1
    async with journal_pool.acquire() as conn:
        state = await conn.fetchval(
            """
            SELECT usage_delivery_state
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
    assert state == "dead_letter"


async def test_materializer_rolls_back_failed_insert_before_dead_lettering(
    journal_pool: asyncpg.Pool,
) -> None:
    service = ModelExecutionJournalService(journal_pool)
    identity = _identity()
    await service.begin_execution(
        identity=identity,
        invocation_kind="reader.grammar_batch",
    )
    await service.capture_execution(identity=identity, prepared=_prepared())
    async with journal_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE FUNCTION reject_test_usage_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'test usage insert failure';
            END;
            $$
            """
        )
        await conn.execute(
            """
            CREATE TRIGGER reject_test_usage_insert
            BEFORE INSERT ON ai_usage_events
            FOR EACH ROW EXECUTE FUNCTION reject_test_usage_insert()
            """
        )

    summary = await service.materialize_pending(limit=1, max_attempts=1)

    assert summary.dead_lettered == 1
    async with journal_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT usage_delivery_state, delivery_attempt_count,
                   delivery_last_error_message
            FROM ai_model_execution_journal
            WHERE invocation_key = $1
            """,
            identity.invocation_key,
        )
    assert row["usage_delivery_state"] == "dead_letter"
    assert row["delivery_attempt_count"] == 1
    assert row["delivery_last_error_message"] == "usage materialization failed"
