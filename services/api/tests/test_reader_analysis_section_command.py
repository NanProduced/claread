"""RA-PROG-04: snapshot wiring, explicit section command, events."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import api_router
from app.database import connection as db_connection
from app.llm.call_guard import pop_blocked_real_llm_attempts
from app.services.model_execution_journal import ExecutionIdentity
from app.services.model_execution_journal.service import (
    ModelExecutionJournalService,
)
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_PROGRESS_CHANGED_EVENT,
    GRAMMAR_ANALYSIS_SECTION_FINGERPRINT,
    USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
    VOCABULARY_ANALYSIS_SECTION_FINGERPRINT,
)
from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
    AnalysisSectionUnit,
    plan_analysis_sections,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import (
    STATUS_FAILED_TERMINAL,
    STATUS_PAUSED,
    STATUS_QUEUED,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.snapshot import build_reader_plate_snapshot
from app.services.reader_orchestration.worker_loop import (
    ReaderEnhancementWorkerLoopService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    fixture_analysis_progress,
    insert_user,
    make_pool,
    submit_article_ready,
)
from tests.test_reader_analysis_progress_projection import _LONG_TEXT, _SHORT_TEXT
from tests.test_reader_orchestration_job_runtime import _begin_journal_for_claim

pytestmark = [pytest.mark.anyio]

AUTH_HEADERS = {"Authorization": "Bearer test_token"}


@pytest.fixture
async def command_env() -> asyncpg.Pool:
    schema_name = f"test_reader_section_cmd_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.close()
    pool = await make_pool(schema_name)
    previous = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


def _mock_auth(user_id: UUID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new_callable=AsyncMock,
        return_value=type(
            "SessionInfo",
            (),
            {"user_id": user_id, "session_id": uuid4()},
        )(),
    )


async def _client(pool: asyncpg.Pool) -> tuple[FastAPI, AsyncClient]:
    app = FastAPI()
    app.include_router(api_router)
    return app, AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


async def _submit(pool: asyncpg.Pool, user_id: UUID, text: str) -> UUID:
    result = await ArticleReadyPersistenceService(pool=pool).submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=text,
            title="Section Command",
            language="en",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )
    return result.record_id


_PROGRESS_EVENT_KEYS = frozenset(
    {"base_id", "generation", "accepted_section_ids", "mutation", "topic"}
)


async def _counts(pool: asyncpg.Pool, record_id: UUID) -> tuple[int, int, int]:
    async with pool.acquire() as conn:
        jobs = await conn.fetchval(
            "SELECT count(*) FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        runs = await conn.fetchval(
            "SELECT count(*) FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )
        events = await conn.fetchval(
            "SELECT count(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        )
    return int(jobs), int(runs), int(events)


async def _progress_events(
    pool: asyncpg.Pool, record_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT sequence, event_type, payload_json
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = $2
              AND payload_json->>'topic' = 'analysis_progress'
            ORDER BY sequence ASC
            """,
            record_id,
            ANALYSIS_PROGRESS_CHANGED_EVENT,
        )


async def _section_jobs(
    pool: asyncpg.Pool, record_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, job_type, status, target_key, operation_fingerprint,
                   input_json, pause_owner, rationale_code, failure_class,
                   failure_code
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type IN (
                'build_vocabulary_layer_article',
                'build_grammar_bundle'
              )
              AND (input_json->>'request_origin') IN (
                'automatic_analysis_section_v1',
                'user_explicit_analysis_section'
              )
            ORDER BY target_key ASC, job_type ASC, created_at ASC
            """,
            record_id,
        )


async def _explicit_jobs(
    pool: asyncpg.Pool, record_id: UUID, section_id: str
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, job_type, status, operation_fingerprint, input_json,
                   expected_generation, base_id
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND target_key = $2
              AND (input_json->>'request_origin') = $3
            ORDER BY job_type ASC, created_at ASC
            """,
            record_id,
            section_id,
            USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN,
        )


async def _section_unit_ids(
    pool: asyncpg.Pool, record_id: UUID, section_id: str
) -> tuple[UUID, int, list[str]]:
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT active_base_id, generation
            FROM reading_records WHERE id = $1
            """,
            record_id,
        )
        rows = await conn.fetch(
            """
            SELECT unit_id, order_index, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            record["active_base_id"],
        )
    planned = plan_analysis_sections(
        str(record["active_base_id"]),
        [
            AnalysisSectionUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ],
    )
    section = next(item for item in planned if item.section_id == section_id)
    return record["active_base_id"], int(record["generation"]), list(
        section.target_unit_ids
    )


async def _publish_units(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    generation: int,
    layer_type: str,
    unit_ids: list[str],
) -> None:
    async with pool.acquire() as conn:
        for unit_id in unit_ids:
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id, base_id, layer_type, target_scope,
                    target_key, generation, status, operation_fingerprint,
                    schema_version, output_json, coverage_json, quality_json,
                    published_at
                )
                VALUES (
                    $1, $2, $3, 'unit', $4, $5, 'published',
                    'cmd_test', 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, NOW()
                )
                """,
                record_id,
                base_id,
                layer_type,
                unit_id,
                generation,
            )


def test_snapshot_id_changes_when_analysis_progress_changes() -> None:
    from datetime import UTC, datetime

    from app.services.reader_orchestration.base_builder import (
        LowImpactReadingBaseBuildInput,
        build_low_impact_reading_base,
    )

    result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-1",
            base_id="base-1",
            source_text="Hello world. Another sentence.",
            title="ID",
            language="en",
        )
    )
    taken = datetime(2026, 8, 16, tzinfo=UTC)
    first = build_reader_plate_snapshot(
        result,
        analysis_progress=fixture_analysis_progress(overall_status="queued"),
        snapshot_taken_at=taken,
        last_event_sequence=1,
    )
    second = build_reader_plate_snapshot(
        result,
        analysis_progress=fixture_analysis_progress(overall_status="waiting_user"),
        snapshot_taken_at=taken,
        last_event_sequence=1,
    )
    assert first.snapshot_id != second.snapshot_id


async def test_snapshot_includes_real_analysis_progress(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _SHORT_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            response = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
    assert response.status_code == 200
    body = response.json()
    progress = body["analysis_progress"]
    assert progress["mode"] == "automatic"
    assert progress["plan_version"] == ANALYSIS_SECTION_PLAN_VERSION
    assert progress["sections"]
    assert pop_blocked_real_llm_attempts() == []


async def test_missing_and_other_user_are_404(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    other = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _SHORT_TEXT)
    _app, client = await _client(command_env)
    payload = {"scope": "single", "section_id": "ras1_missing"}
    with _mock_auth(user_id):
        async with client:
            missing = await client.post(
                f"/reader/records/{uuid4()}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json=payload,
            )
    with _mock_auth(other):
        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://testserver",
        ) as other_client:
            stolen = await other_client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json=payload,
            )
    assert missing.status_code == 404
    assert stolen.status_code == 404
    assert missing.json()["detail"] == stolen.json()["detail"]


async def test_invalid_body_is_422(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            single = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single"},
            )
            remaining = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "remaining", "section_id": "ras1_x"},
            )
    assert single.status_code == 422
    assert remaining.status_code == 422


async def test_automatic_mode_rejected(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _SHORT_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            snap = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
            section_id = snap.json()["analysis_progress"]["sections"][0]["section_id"]
            response = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": section_id},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "rejected"
    assert body["reason_code"] == "analysis_mode_not_segmented"
    assert body["event_sequence"] is None


async def test_forged_section_rejected(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            response = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": "ras1_forged"},
            )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"
    assert response.json()["reason_code"] == "analysis_section_not_found"


async def test_single_started_is_idempotent_and_emits_event(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    await EnhancementJobBootstrapService(pool=command_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            snap = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
            sections = snap.json()["analysis_progress"]["sections"]
            assert len(sections) >= 2
            target = sections[1]["section_id"]
            first = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            after_first = await _counts(command_env, record_id)
            second = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            events = await client.get(
                f"/reader/records/{record_id}/events",
                headers=AUTH_HEADERS,
                params={"after_sequence": 0},
            )
            after = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
    assert first.status_code == 200
    started = first.json()
    assert started["outcome"] == "started"
    assert started["accepted_section_ids"] == [target]
    assert started["event_sequence"] is not None
    second_body = second.json()
    assert second_body["outcome"] == "already_active"
    assert second_body["event_sequence"] is None
    payload_events = [
        item
        for item in events.json()["events"]
        if item["event_type"] == ANALYSIS_PROGRESS_CHANGED_EVENT
        and item["payload"].get("topic") == "analysis_progress"
    ]
    assert len(payload_events) == 1
    payload = payload_events[0]["payload"]
    assert payload["accepted_section_ids"] == [target]
    assert set(payload) <= _PROGRESS_EVENT_KEYS
    assert "input_json" not in payload
    assert "exception" not in payload
    assert "prompt" not in payload
    after_status = after.json()["analysis_progress"]["sections"][1]["status"]
    assert after_status in {"queued", "processing"}
    jobs = await _explicit_jobs(command_env, record_id, target)
    assert {row["job_type"] for row in jobs} == {
        "build_vocabulary_layer_article",
        "build_grammar_bundle",
    }
    assert all(
        row["input_json"]["analysis_section_plan_version"]
        == ANALYSIS_SECTION_PLAN_VERSION
        for row in jobs
    )
    assert await _counts(command_env, record_id) == after_first
    assert pop_blocked_real_llm_attempts() == []


async def test_remaining_starts_in_reading_order(command_env: asyncpg.Pool) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    await EnhancementJobBootstrapService(pool=command_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            snap = await client.get(
                f"/reader/records/{record_id}/snapshot",
                headers=AUTH_HEADERS,
            )
            ordered = [
                section["section_id"]
                for section in snap.json()["analysis_progress"]["sections"]
            ]
            response = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "remaining"},
            )
    body = response.json()
    assert body["outcome"] == "started"
    accepted = body["accepted_section_ids"]
    assert accepted
    assert accepted == [item for item in ordered if item in set(accepted)]
    jobs = await _section_jobs(command_env, record_id)
    by_section: dict[str, list[asyncpg.Record]] = {}
    for job in jobs:
        by_section.setdefault(str(job["target_key"]), []).append(job)
        assert job["status"] != "superseded"
        assert str(job["target_key"]) == job["input_json"]["analysis_section_id"]
    assert len(by_section) >= 2
    for section_id in accepted:
        types = {row["job_type"] for row in by_section[section_id]}
        assert types == {
            "build_vocabulary_layer_article",
            "build_grammar_bundle",
        }
        assert {row["status"] for row in by_section[section_id]} <= {
            "queued",
            "claimed",
            "retry_later",
        }
    first_auto = [
        row
        for row in by_section.get(ordered[0], [])
        if row["input_json"]["request_origin"] == "automatic_analysis_section_v1"
    ]
    assert first_auto
    assert {row["status"] for row in first_auto} <= {
        "queued",
        "claimed",
        "retry_later",
    }
    fingerprints = {row["operation_fingerprint"] for row in jobs}
    assert len(fingerprints) >= 2
    assert pop_blocked_real_llm_attempts() == []


async def test_coverage_complete_scan_requires_trusted_explicit_job(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    submitted = await submit_article_ready(
        command_env, user_id=user_id, title="Complete"
    )
    async with command_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = 'coverage_complete'
            WHERE id = $1
            """,
            submitted.record_id,
        )
    loop = ReaderEnhancementWorkerLoopService(pool=command_env)
    empty = await loop.scan_eligible_records(batch_size=20)
    assert submitted.record_id not in {row.record_id for row in empty}

    async with command_env.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'enhancement', 'queued', 1, 'test', 'user')
            RETURNING id
            """,
            submitted.record_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, status, expected_generation,
                operation_fingerprint, idempotency_key, input_hash,
                input_json, max_attempts
            )
            VALUES (
                $1, $2, $3, $4, 'build_vocabulary_layer_article', 'unit_range',
                'forged', 'queued', 1, 'not-allowlisted',
                $5, $5,
                jsonb_build_object('request_origin', 'user_explicit_analysis_section'),
                3
            )
            """,
            submitted.record_id,
            submitted.base_id,
            run_id,
            user_id,
            f"forged-{uuid4()}",
        )
    forged = await loop.scan_eligible_records(batch_size=20)
    assert submitted.record_id not in {row.record_id for row in forged}

    async with command_env.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'enhancement', 'queued', 1, 'test', 'user')
            RETURNING id
            """,
            submitted.record_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, status, expected_generation,
                operation_fingerprint, idempotency_key, input_hash,
                input_json, max_attempts
            )
            VALUES (
                $1, $2, $3, $4, 'build_vocabulary_layer_article', 'unit_range',
                'trusted', 'queued', 1, $6,
                $5, $5,
                jsonb_build_object(
                    'request_origin', $7::text,
                    'analysis_section_plan_version', $8::text
                ),
                3
            )
            """,
            submitted.record_id,
            submitted.base_id,
            run_id,
            user_id,
            f"trusted-{uuid4()}",
            f"{VOCABULARY_ANALYSIS_SECTION_FINGERPRINT}:policy",
            str(USER_EXPLICIT_ANALYSIS_SECTION_ORIGIN),
            str(ANALYSIS_SECTION_PLAN_VERSION),
        )
    trusted = await loop.scan_eligible_records(batch_size=20)
    assert submitted.record_id in {row.record_id for row in trusted}


async def _bootstrap_and_second_section(
    pool: asyncpg.Pool, client: AsyncClient, user_id: UUID, record_id: UUID
) -> str:
    await EnhancementJobBootstrapService(pool=pool).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    snap = await client.get(
        f"/reader/records/{record_id}/snapshot",
        headers=AUTH_HEADERS,
    )
    sections = snap.json()["analysis_progress"]["sections"]
    assert len(sections) >= 2
    return str(sections[1]["section_id"])


async def test_already_complete_creates_no_job_run_or_event(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            base_id, generation, unit_ids = await _section_unit_ids(
                command_env, record_id, target
            )
            await _publish_units(
                command_env,
                record_id=record_id,
                base_id=base_id,
                generation=generation,
                layer_type="vocabulary",
                unit_ids=unit_ids,
            )
            await _publish_units(
                command_env,
                record_id=record_id,
                base_id=base_id,
                generation=generation,
                layer_type="grammar_note",
                unit_ids=unit_ids,
            )
            before = await _counts(command_env, record_id)
            events_before = await _progress_events(command_env, record_id)
            response = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
    body = response.json()
    assert body["outcome"] == "already_complete"
    assert body["event_sequence"] is None
    assert await _counts(command_env, record_id) == before
    assert await _explicit_jobs(command_env, record_id, target) == []
    assert await _progress_events(command_env, record_id) == events_before
    assert pop_blocked_real_llm_attempts() == []


async def test_partial_capability_creates_only_missing_job(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            base_id, generation, unit_ids = await _section_unit_ids(
                command_env, record_id, target
            )
            await _publish_units(
                command_env,
                record_id=record_id,
                base_id=base_id,
                generation=generation,
                layer_type="vocabulary",
                unit_ids=unit_ids,
            )
            vocab_only = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            jobs = await _explicit_jobs(command_env, record_id, target)
            assert vocab_only.json()["outcome"] == "started"
            assert [row["job_type"] for row in jobs] == ["build_grammar_bundle"]
    user_id_b = await insert_user(command_env)
    record_b = await _submit(command_env, user_id_b, _LONG_TEXT)
    _app_b, client_b = await _client(command_env)
    with _mock_auth(user_id_b):
        async with client_b:
            target_b = await _bootstrap_and_second_section(
                command_env, client_b, user_id_b, record_b
            )
            base_b, gen_b, units_b = await _section_unit_ids(
                command_env, record_b, target_b
            )
            await _publish_units(
                command_env,
                record_id=record_b,
                base_id=base_b,
                generation=gen_b,
                layer_type="grammar_note",
                unit_ids=units_b,
            )
            grammar_only = await client_b.post(
                f"/reader/records/{record_b}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target_b},
            )
    jobs_b = await _explicit_jobs(command_env, record_b, target_b)
    assert grammar_only.json()["outcome"] == "started"
    assert [row["job_type"] for row in jobs_b] == ["build_vocabulary_layer_article"]
    assert pop_blocked_real_llm_attempts() == []


async def test_failed_terminal_retry_inserts_one_new_job(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            jobs = await _explicit_jobs(command_env, record_id, target)
            vocab = next(
                row
                for row in jobs
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            async with command_env.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET status = $2, pause_owner = NULL
                    WHERE id = $1
                    """,
                    vocab["id"],
                    STATUS_FAILED_TERMINAL,
                )
            retry = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            after_retry = await _explicit_jobs(command_env, record_id, target)
            again = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
    assert retry.json()["outcome"] == "started"
    original = next(row for row in after_retry if row["id"] == vocab["id"])
    assert original["status"] == STATUS_FAILED_TERMINAL
    new_vocab = [
        row
        for row in after_retry
        if row["job_type"] == "build_vocabulary_layer_article"
        and row["id"] != vocab["id"]
    ]
    assert len(new_vocab) == 1
    assert new_vocab[0]["status"] == STATUS_QUEUED
    assert again.json()["outcome"] == "already_active"
    assert len(await _explicit_jobs(command_env, record_id, target)) == len(
        after_retry
    )
    assert pop_blocked_real_llm_attempts() == []


async def test_quota_pause_resumes_once_and_can_repause(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            jobs = await _explicit_jobs(command_env, record_id, target)
            for job in jobs:
                await runtime.transition(
                    job_id=job["id"],
                    target_status=STATUS_PAUSED,
                    pause_owner="quota",
                    failure_code="budget_exhausted",
                    rationale_code="quota_paused",
                )
            before_jobs, before_runs, _before_events = await _counts(
                command_env, record_id
            )
            resumed = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            after_resume = await _explicit_jobs(command_env, record_id, target)
            duplicate = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
    assert resumed.json()["outcome"] == "started"
    assert {row["status"] for row in after_resume} == {STATUS_QUEUED}
    assert duplicate.json()["outcome"] == "already_active"
    after_jobs, after_runs, _after_events = await _counts(command_env, record_id)
    assert (after_jobs, after_runs) == (before_jobs, before_runs)
    for job in after_resume:
        await runtime.transition(
            job_id=job["id"],
            target_status=STATUS_PAUSED,
            pause_owner="quota",
            failure_code="budget_exhausted",
            rationale_code="quota_paused",
        )
    paused = await _explicit_jobs(command_env, record_id, target)
    assert {row["status"] for row in paused} == {STATUS_PAUSED}
    assert pop_blocked_real_llm_attempts() == []


async def test_resume_fence_rejects_stale_and_captured_pauses(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            jobs = await _explicit_jobs(command_env, record_id, target)
            vocab = next(
                row
                for row in jobs
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            grammar = next(
                row
                for row in jobs
                if row["job_type"] == "build_grammar_bundle"
            )
            await runtime.transition(
                job_id=vocab["id"],
                target_status=STATUS_PAUSED,
                pause_owner="system",
                failure_class="model_execution",
                failure_code="post_provider_resume_required",
                rationale_code="model_execution_captured_resume_required",
            )
            captured = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            captured_jobs = await _explicit_jobs(command_env, record_id, target)
            assert captured.json()["outcome"] == "already_active"
            assert captured.json()["event_sequence"] is None
            vocab_after = next(row for row in captured_jobs if row["id"] == vocab["id"])
            assert vocab_after["status"] == STATUS_PAUSED
            async with command_env.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET input_json = jsonb_set(
                        input_json, '{analysis_section_plan_version}', '"old_plan"'
                    ),
                    status = 'paused',
                    pause_owner = 'quota',
                    failure_code = 'budget_exhausted'
                    WHERE id = $1
                    """,
                    grammar["id"],
                )
            stale_plan = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            after_plan = await _explicit_jobs(command_env, record_id, target)
            grammar_after_plan = next(row for row in after_plan if row["id"] == grammar["id"])
            assert stale_plan.json()["outcome"] == "paused_quota"
            assert grammar_after_plan["status"] == STATUS_PAUSED
            async with command_env.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET operation_fingerprint = $2,
                        status = 'paused',
                        pause_owner = 'quota',
                        failure_code = 'budget_exhausted'
                    WHERE id = $1
                    """,
                    grammar["id"],
                    f"{GRAMMAR_ANALYSIS_SECTION_FINGERPRINT}:stale",
                )
            stale_fp = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            after_fp = await _explicit_jobs(command_env, record_id, target)
    grammar_original = next(row for row in after_fp if row["id"] == grammar["id"])
    new_grammar = [
        row
        for row in after_fp
        if row["job_type"] == "build_grammar_bundle" and row["id"] != grammar["id"]
    ]
    assert stale_fp.json()["outcome"] == "started"
    assert grammar_original["status"] != STATUS_QUEUED
    assert len(new_grammar) == 1
    assert new_grammar[0]["status"] == STATUS_QUEUED
    assert pop_blocked_real_llm_attempts() == []


async def test_command_event_write_failure_rolls_back_jobs(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            before = await _counts(command_env, record_id)
            with patch.object(
                ReaderEventRuntime,
                "publish_event_in_transaction",
                side_effect=RuntimeError("event write failed"),
            ):
                with pytest.raises(RuntimeError, match="event write failed"):
                    await client.post(
                        f"/reader/records/{record_id}/analysis-sections/requests",
                        headers=AUTH_HEADERS,
                        json={"scope": "single", "section_id": target},
                    )
    assert await _explicit_jobs(command_env, record_id, target) == []
    assert await _progress_events(command_env, record_id) == []
    assert await _counts(command_env, record_id) == before
    assert pop_blocked_real_llm_attempts() == []


_RUNNABLE = frozenset({"queued", "claimed", "retry_later"})


async def _capability_status(
    client: AsyncClient, record_id: UUID, section_id: str
) -> tuple[dict, dict]:
    snap = await client.get(
        f"/reader/records/{record_id}/snapshot",
        headers=AUTH_HEADERS,
    )
    progress = snap.json()["analysis_progress"]
    section = next(
        row for row in progress["sections"] if row["section_id"] == section_id
    )
    return progress, section


async def _pause_job_captured_resume(
    pool: asyncpg.Pool, job_id: UUID
) -> object:
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_job_by_id(
        job_id=job_id,
        lease_owner="captured-owner",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None and claim.job_id == job_id
    identity = await _begin_journal_for_claim(pool, claim=claim, captured=True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET lease_expires_at = NOW() - INTERVAL '1 second'
            WHERE id = $1
            """,
            job_id,
        )
    assert await runtime.recover_stale_leases() == 1
    row = await conn_fetch_job(pool, job_id)
    assert row["status"] == STATUS_PAUSED
    assert row["rationale_code"] == "model_execution_captured_resume_required"
    return identity


async def conn_fetch_job(pool: asyncpg.Pool, job_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1", job_id)


async def test_same_section_stale_fingerprint_does_not_touch_other_section(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    await EnhancementJobBootstrapService(pool=command_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    _app, client = await _client(command_env)
    with _mock_auth(user_id):
        async with client:
            remaining = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "remaining"},
            )
            assert remaining.json()["outcome"] == "started"
            accepted = remaining.json()["accepted_section_ids"]
            assert accepted
            before = await _section_jobs(command_env, record_id)
            section_ids = list(dict.fromkeys(str(row["target_key"]) for row in before))
            assert len(section_ids) >= 2
            target = accepted[0]
            other = next(item for item in section_ids if item != target)
            other_before = [row for row in before if row["target_key"] == other]
            victim = next(
                row
                for row in before
                if row["target_key"] == target
                and row["job_type"] == "build_grammar_bundle"
            )
            async with command_env.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET operation_fingerprint = $2,
                        status = 'paused',
                        pause_owner = 'quota',
                        failure_code = 'budget_exhausted'
                    WHERE id = $1
                    """,
                    victim["id"],
                    f"{GRAMMAR_ANALYSIS_SECTION_FINGERPRINT}:stale-other",
                )
            rotated = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
    assert rotated.json()["outcome"] == "started"
    after = await _section_jobs(command_env, record_id)
    original = next(row for row in after if row["id"] == victim["id"])
    assert original["status"] == "superseded"
    new_jobs = [
        row
        for row in after
        if row["target_key"] == target
        and row["job_type"] == "build_grammar_bundle"
        and row["id"] != victim["id"]
    ]
    assert len(new_jobs) == 1
    assert new_jobs[0]["status"] in _RUNNABLE
    other_after = [row for row in after if row["target_key"] == other]
    assert {row["id"] for row in other_after} == {row["id"] for row in other_before}
    assert {row["status"] for row in other_after} == {row["status"] for row in other_before}
    assert pop_blocked_real_llm_attempts() == []


async def test_captured_resume_receipt_invalid_emits_one_progress_event(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            vocab = next(
                row
                for row in await _explicit_jobs(command_env, record_id, target)
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            identity = await _pause_job_captured_resume(command_env, vocab["id"])
            progress_before, section_before = await _capability_status(
                client, record_id, target
            )
            assert section_before["vocabulary_status"] == "queued"
            events_before = await _progress_events(command_env, record_id)
            async with command_env.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE ai_model_execution_journal
                    SET normalized_payload_json = '{"raw_provider_response":{}}'
                    WHERE invocation_key = $1
                    """,
                    identity.invocation_key,
                )
            recovered = await runtime.claim_captured_resume(
                job_id=vocab["id"],
                lease_owner="must-not-provider",
                lease_duration=timedelta(seconds=30),
            )
            assert recovered is None
            progress_after, section_after = await _capability_status(
                client, record_id, target
            )
            job = await conn_fetch_job(command_env, vocab["id"])
    assert job["status"] == STATUS_PAUSED
    assert job["rationale_code"] == "model_execution_receipt_invalid"
    assert section_after["vocabulary_status"] == "failed"
    assert progress_after["needs_user_action"] is True
    events_after = await _progress_events(command_env, record_id)
    assert len(events_after) == len(events_before) + 1
    assert set(events_after[-1]["payload_json"]) <= _PROGRESS_EVENT_KEYS
    assert progress_before["needs_user_action"] is False
    assert pop_blocked_real_llm_attempts() == []


async def test_captured_resume_ambiguous_emits_one_progress_event(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            vocab = next(
                row
                for row in await _explicit_jobs(command_env, record_id, target)
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            await _pause_job_captured_resume(command_env, vocab["id"])
            job = await conn_fetch_job(command_env, vocab["id"])
            journal = ModelExecutionJournalService(command_env)
            second = ExecutionIdentity(
                invocation_key=f"reader:grammar_batch:{vocab['id']}:{job['attempt_count']}:2",
                reader_job_id=vocab["id"],
                reader_run_id=job["run_id"],
                attempt_ordinal=int(job["attempt_count"]),
                execution_slot=2,
            )
            begun = await journal.begin_execution(
                identity=second,
                invocation_kind="reader.grammar_batch",
            )
            assert begun.provider_call_allowed is True
            events_before = await _progress_events(command_env, record_id)
            _, section_before = await _capability_status(
                client, record_id, target
            )
            assert section_before["vocabulary_status"] == "queued"
            recovered = await runtime.claim_captured_resume(
                job_id=vocab["id"],
                lease_owner="ambiguous-owner",
                lease_duration=timedelta(seconds=30),
            )
            assert recovered is None
            progress_after, section_after = await _capability_status(
                client, record_id, target
            )
            after_job = await conn_fetch_job(command_env, vocab["id"])
    assert after_job["status"] == STATUS_PAUSED
    assert after_job["rationale_code"] == "model_execution_ambiguous"
    assert section_after["vocabulary_status"] == "failed"
    assert progress_after["needs_user_action"] is True
    events_after = await _progress_events(command_env, record_id)
    assert len(events_after) == len(events_before) + 1
    assert pop_blocked_real_llm_attempts() == []


async def test_paused_to_paused_same_disposition_does_not_emit(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            vocab = next(
                row
                for row in await _explicit_jobs(command_env, record_id, target)
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            await runtime.transition(
                job_id=vocab["id"],
                target_status=STATUS_PAUSED,
                pause_owner="quota",
                failure_code="budget_exhausted",
                rationale_code="quota_paused",
            )
            events_before = await _progress_events(command_env, record_id)
            async with command_env.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT * FROM reader_jobs WHERE id = $1",
                        vocab["id"],
                    )
                    await runtime._apply_transition(
                        conn,
                        job_row=row,
                        target_status=STATUS_PAUSED,
                        available_at=None,
                        pause_owner="quota",
                        output_ref=None,
                        failure_class=row["failure_class"],
                        failure_code="budget_exhausted",
                        failure_message=None,
                        rationale_code="quota_paused",
                    )
    assert await _progress_events(command_env, record_id) == events_before
    assert pop_blocked_real_llm_attempts() == []


async def test_disposition_event_write_failure_rolls_back_fields(
    command_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(command_env)
    record_id = await _submit(command_env, user_id, _LONG_TEXT)
    _app, client = await _client(command_env)
    runtime = ReaderJobRuntime(pool=command_env)
    with _mock_auth(user_id):
        async with client:
            target = await _bootstrap_and_second_section(
                command_env, client, user_id, record_id
            )
            started = await client.post(
                f"/reader/records/{record_id}/analysis-sections/requests",
                headers=AUTH_HEADERS,
                json={"scope": "single", "section_id": target},
            )
            assert started.json()["outcome"] == "started"
            vocab = next(
                row
                for row in await _explicit_jobs(command_env, record_id, target)
                if row["job_type"] == "build_vocabulary_layer_article"
            )
            identity = await _pause_job_captured_resume(command_env, vocab["id"])
            before = await conn_fetch_job(command_env, vocab["id"])
            events_before = await _progress_events(command_env, record_id)
            async with command_env.acquire() as conn:
                job_events_before = await conn.fetchval(
                    "SELECT count(*) FROM reader_job_events WHERE job_id = $1",
                    vocab["id"],
                )
                await conn.execute(
                    """
                    UPDATE ai_model_execution_journal
                    SET normalized_payload_json = '{"raw_provider_response":{}}'
                    WHERE invocation_key = $1
                    """,
                    identity.invocation_key,
                )
            with patch.object(
                ReaderEventRuntime,
                "publish_event_in_transaction",
                side_effect=RuntimeError("event write failed"),
            ):
                with pytest.raises(RuntimeError, match="event write failed"):
                    await runtime.claim_captured_resume(
                        job_id=vocab["id"],
                        lease_owner="rollback-owner",
                        lease_duration=timedelta(seconds=30),
                    )
            after = await conn_fetch_job(command_env, vocab["id"])
            async with command_env.acquire() as conn:
                job_events_after = await conn.fetchval(
                    "SELECT count(*) FROM reader_job_events WHERE job_id = $1",
                    vocab["id"],
                )
    assert after["status"] == STATUS_PAUSED
    assert after["rationale_code"] == before["rationale_code"]
    assert after["failure_code"] == before["failure_code"]
    assert after["failure_class"] == before["failure_class"]
    assert after["pause_owner"] == before["pause_owner"]
    assert await _progress_events(command_env, record_id) == events_before
    assert job_events_after == job_events_before
    assert pop_blocked_real_llm_attempts() == []
