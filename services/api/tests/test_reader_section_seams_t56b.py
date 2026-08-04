"""T5.6b-P1 dynamic seam tests against real PostgreSQL.

Covers CV-02 (coverage count isolation), WL-01/02/03 (worker candidate SQL),
SU-01/02/03 (ordinary supersede isolation), and dual-drain concurrency.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_TARGET_SCOPE,
    _supersede_stale_fingerprint_jobs,
)
from app.services.reader_orchestration.job_runtime import (
    STATUS_QUEUED,
    STATUS_SUPERSEDED,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_translation_drain import (
    SectionDrainOutcome,
    SectionTranslationDrainService,
)
from app.services.reader_orchestration.worker_loop import (
    ENHANCEMENT_PIPELINE_JOB_TYPES,
    ReaderEnhancementWorkerLoopService,
)
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]
# translate_article / unit_range batch job types (T1.1)
_MIGRATION_0017_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql


@pytest.fixture
def anyio_backend() -> str:
    """Seam tests only need asyncio; skip trio double-run."""
    return "asyncio"


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
        max_size=6,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def section_seam_env() -> asyncpg.Pool:
    schema_name = f"test_section_seams_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        await admin_conn.execute(_MIGRATION_0017_SQL)
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


async def _seed(
    pool: asyncpg.Pool,
    *,
    generation: int = 1,
    product_state: str = "readable_enhancing",
    readiness_state: str = "article_ready",
) -> tuple[UUID, UUID, UUID, UUID]:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
        record_id = await conn.fetchval(
            """
            INSERT INTO reading_records (
                user_id, source_type, title, language, generation,
                product_state, readiness_state, lifecycle_status
            )
            VALUES ($1, 'text', 'Section Seam', 'en', $2, $3, $4, 'active')
            RETURNING id
            """,
            user_id,
            generation,
            product_state,
            readiness_state,
        )
        text = "Unit one. Unit two. Unit three."
        base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, text,
                content_sha256, content_utf16_length,
                canonicalizer_version, builder_version, segmenter_version,
                language, title_snapshot, navigation_json, status
            )
            VALUES (
                $1, 1, $2, $3, $4, $5,
                'd3-p4-canonicalizer', 'd3-p4-builder', 'd3-p4-segmenter',
                'en', 'Seam Title', '{"units":[]}'::jsonb, 'active'
            )
            RETURNING id
            """,
            record_id,
            generation,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            utf16_code_unit_length(text),
        )
        await conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            record_id,
            base_id,
        )
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'enhancement', 'queued', $3, '{}'::jsonb, 't56b', 'user')
            RETURNING id
            """,
            record_id,
            user_id,
            generation,
        )
    return user_id, record_id, base_id, run_id


async def _insert_job(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    run_id: UUID,
    user_id: UUID,
    base_id: UUID,
    job_type: str = TRANSLATION_BATCH_JOB_TYPE,
    target_type: str = TRANSLATION_BATCH_TARGET_SCOPE,
    target_key: str | None = None,
    status: str = STATUS_QUEUED,
    expected_generation: int = 1,
    operation_fingerprint: str = "translation_article_v1:ordinary",
    input_json: dict | None = None,
    idempotency_key: str | None = None,
) -> UUID:
    if target_key is None:
        target_key = str(uuid4())
    if idempotency_key is None:
        idempotency_key = f"id-{uuid4().hex}"
    payload = input_json if input_json is not None else {}
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                expected_generation, operation_fingerprint, idempotency_key,
                input_json, max_attempts, attempt_count
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8,
                $9, $10, $11,
                $12::jsonb, 3, 0
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            job_type,
            target_type,
            target_key,
            status,
            expected_generation,
            operation_fingerprint,
            idempotency_key,
            jsonb_param(payload),
        )
    assert isinstance(job_id, UUID)
    return job_id


def _section_input() -> dict:
    return {
        "request_origin": SECTION_REQUEST_ORIGIN,
        "target_unit_ids": ["u1", "u2"],
        "section_identity": {
            "start_unit_id": "u1",
            "end_unit_id": "u2",
        },
    }


# ---------------------------------------------------------------------------
# CV-02: coverage counts exclude section_v1
# ---------------------------------------------------------------------------


async def test_cv02_ordinary_blocks_coverage_section_excluded(
    section_seam_env: asyncpg.Pool,
) -> None:
    """Ordinary translate_article counts; section_v1 jobs of any status do not."""
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    ordinary = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ordinary-range",
        operation_fingerprint="translation_article_v1:o1",
        input_json={"target_unit_ids": ["u1", "u2", "u3"]},
        status="queued",
    )
    for status in ("queued", "retry_later", "succeeded", "failed_terminal"):
        await _insert_job(
            section_seam_env,
            record_id=record_id,
            run_id=run_id,
            user_id=user_id,
            base_id=base_id,
            target_key=f"section-{status}",
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{status}",
            input_json=_section_input(),
            status=status,
        )

    repo = ReaderOrchestrationRepository(pool=section_seam_env)
    async with section_seam_env.acquire() as conn:
        counts = await repo.count_enhancement_jobs_by_terminal_status(
            conn,
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            job_types=[TRANSLATION_BATCH_JOB_TYPE],
        )
    assert counts["queued"] == 1  # only ordinary
    assert counts["retry_later"] == 0
    assert counts["succeeded"] == 0
    assert counts["failed_terminal"] == 0
    # Sanity: ordinary row still present
    async with section_seam_env.acquire() as conn:
        ordinary_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", ordinary
        )
    assert ordinary_status == "queued"


# ---------------------------------------------------------------------------
# WL-01/02/03: worker candidate SQL ordinary-only tracking
# ---------------------------------------------------------------------------


async def test_wl01_section_only_record_not_runnable(
    section_seam_env: asyncpg.Pool,
) -> None:
    """Only section_v1 jobs → runnable_job_count=0; tracked=0 → may still appear
    as tracked_job_count=0 candidate (bootstrap path), not as runnable work."""
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="sec-only",
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:a",
        input_json=_section_input(),
        status="queued",
    )
    loop = ReaderEnhancementWorkerLoopService(pool=section_seam_env)
    candidates = await loop.scan_eligible_records(batch_size=20)
    match = [c for c in candidates if c.record_id == record_id]
    # tracked=0 so record can appear for bootstrap, but runnable must be 0
    if match:
        assert match[0].runnable_job_count == 0
        assert match[0].tracked_job_count == 0
    # Either way, section must never contribute to runnable.
    assert all(c.runnable_job_count == 0 for c in match)


async def test_wl02_ordinary_queued_is_runnable(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-run",
        operation_fingerprint="translation_article_v1:o",
        input_json={},  # null origin → ordinary
        status="queued",
    )
    # Also plant section noise
    await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="sec-noise",
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:n",
        input_json=_section_input(),
        status="queued",
    )
    loop = ReaderEnhancementWorkerLoopService(pool=section_seam_env)
    candidates = await loop.scan_eligible_records(batch_size=20)
    match = [c for c in candidates if c.record_id == record_id]
    assert len(match) == 1
    assert match[0].runnable_job_count == 1
    assert match[0].tracked_job_count == 1  # section excluded


async def test_wl03_null_origin_historical_counts_as_ordinary(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="hist-null",
        operation_fingerprint="translation_article_v1:hist",
        input_json=None,  # → {}
        status="succeeded",
    )
    loop = ReaderEnhancementWorkerLoopService(pool=section_seam_env)
    # succeeded ordinary is tracked but not runnable → may appear if tracked=1
    # and runnable=0 only when tracked=0 OR runnable>0; so with tracked=1 runnable=0
    # the record is EXCLUDED from candidates (already has ordinary tracked work).
    candidates = await loop.scan_eligible_records(batch_size=20)
    match = [c for c in candidates if c.record_id == record_id]
    assert match == []  # has ordinary tracked, no runnable → not re-picked

    # Direct count via repository for coverage path
    repo = ReaderOrchestrationRepository(pool=section_seam_env)
    async with section_seam_env.acquire() as conn:
        counts = await repo.count_enhancement_jobs_by_terminal_status(
            conn,
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            job_types=list(ENHANCEMENT_PIPELINE_JOB_TYPES),
        )
    assert counts["succeeded"] == 1


# ---------------------------------------------------------------------------
# SU-01/02/03: supersede isolation
# ---------------------------------------------------------------------------


async def test_su01_ordinary_supersede_does_not_touch_section(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    ordinary = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-stale",
        operation_fingerprint="translation_article_v1:old",
        input_json={},
        status="queued",
    )
    section = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="sec-keep",
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:old",
        input_json=_section_input(),
        status="queued",
    )
    async with section_seam_env.acquire() as conn:
        n = await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            current_fingerprint="translation_article_v1:new",
        )
    assert n == 1
    async with section_seam_env.acquire() as conn:
        ord_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", ordinary
        )
        sec_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", section
        )
    assert ord_status == STATUS_SUPERSEDED
    assert sec_status == STATUS_QUEUED


async def test_su02_section_fingerprint_rotation_does_not_cancel_ordinary(
    section_seam_env: asyncpg.Pool,
) -> None:
    """Calling supersede with a section-like current fp still only hits ordinary."""
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    ordinary = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-keep",
        operation_fingerprint="translation_article_v1:stable",
        input_json={},
        status="queued",
    )
    section = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="sec-diff",
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:old",
        input_json=_section_input(),
        status="queued",
    )
    # Ordinary bootstrap path: current ordinary fingerprint equals ordinary job → 0
    async with section_seam_env.acquire() as conn:
        n = await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            current_fingerprint="translation_article_v1:stable",
        )
    assert n == 0
    async with section_seam_env.acquire() as conn:
        ord_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", ordinary
        )
        sec_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1", section
        )
    assert ord_status == STATUS_QUEUED
    assert sec_status == STATUS_QUEUED


async def test_su03_ordinary_stale_ordinary_still_superseded(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    old = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-old",
        operation_fingerprint="translation_article_v1:v1",
        input_json={"request_origin": "ordinary_client"},
        status="queued",
    )
    null_origin = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-null",
        operation_fingerprint="translation_article_v1:v1",
        input_json={},
        status="queued",
    )
    async with section_seam_env.acquire() as conn:
        n = await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=record_id,
            base_id=base_id,
            expected_generation=1,
            job_type=TRANSLATION_BATCH_JOB_TYPE,
            target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
            current_fingerprint="translation_article_v1:v2",
        )
    assert n == 2
    async with section_seam_env.acquire() as conn:
        statuses = await conn.fetch(
            "SELECT id, status FROM reader_jobs WHERE id = ANY($1::uuid[])",
            [old, null_origin],
        )
    assert {r["status"] for r in statuses} == {STATUS_SUPERSEDED}


# ---------------------------------------------------------------------------
# Dual-drain concurrency (one lease / LLM)
# ---------------------------------------------------------------------------


async def test_dr_dual_drain_only_one_claim(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    job_id = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="unit_range_v1|2.u1|2.u2|0.|0.",
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:conc",
        input_json=_section_input(),
        status="queued",
    )

    # Fresh budget: no durable plan rows → load_durable leaves remaining open.
    # For exhausted tests we'd insert plan; here we want claim race.
    runtime = ReaderJobRuntime(pool=section_seam_env)
    worker_calls: list[UUID] = []

    class _FakeWorker:
        async def process_claimed_translation_batch_job(self, *, claim, retry_delay):
            worker_calls.append(claim.job_id)
            # Keep claimed briefly so the peer cannot re-claim.
            await asyncio.sleep(0.05)
            return type("R", (), {"status": "succeeded"})()

    service = SectionTranslationDrainService(
        pool=section_seam_env,
        job_runtime=runtime,
        translation_worker=_FakeWorker(),  # type: ignore[arg-type]
    )

    # Ensure budget not exhausted: load_durable with no plan → typically not exhausted.
    # If ExecutionBudget treats missing plan as exhausted, force remaining via patch.
    from unittest.mock import AsyncMock, patch

    from app.services.reader_orchestration.execution_budget import (
        DurableBudgetLoadResult,
        ExecutionBudgetSnapshot,
    )

    fresh = DurableBudgetLoadResult(
        layer_snapshots={
            "translation": ExecutionBudgetSnapshot(
                planned_calls=1,
                max_effective_calls=6,
                consumed_calls=0,
                remaining_calls=6,
                exhausted=False,
            ),
            "vocabulary": ExecutionBudgetSnapshot(
                planned_calls=0,
                max_effective_calls=0,
                consumed_calls=0,
                remaining_calls=0,
                exhausted=False,
            ),
            "grammar": ExecutionBudgetSnapshot(
                planned_calls=0,
                max_effective_calls=0,
                consumed_calls=0,
                remaining_calls=0,
                exhausted=False,
            ),
        },
        non_superseded_fingerprints={
            "translation": (),
            "vocabulary": (),
            "grammar": (),
        },
    )

    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=fresh),
    ):
        r1, r2 = await asyncio.gather(
            service.process_job_id(
                job_id=job_id,
                lease_owner="drain-a",
                expected_reading_record_id=record_id,
                expected_base_id=base_id,
                expected_generation=1,
            ),
            service.process_job_id(
                job_id=job_id,
                lease_owner="drain-b",
                expected_reading_record_id=record_id,
                expected_base_id=base_id,
                expected_generation=1,
            ),
        )

    outcomes = {r1.outcome, r2.outcome}
    assert SectionDrainOutcome.SUCCEEDED in outcomes
    assert SectionDrainOutcome.ALREADY_CLAIMED in outcomes or (
        outcomes == {SectionDrainOutcome.SUCCEEDED, SectionDrainOutcome.ALREADY_CLAIMED}
    )
    # Exactly one LLM/process call
    assert len(worker_calls) == 1
    async with section_seam_env.acquire() as conn:
        attempt = await conn.fetchval(
            "SELECT attempt_count FROM reader_jobs WHERE id = $1", job_id
        )
        lease_owners = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_job_events
            WHERE job_id = $1 AND event_type = 'job_claimed'
            """,
            job_id,
        )
    assert int(attempt) == 1
    assert int(lease_owners) == 1


# ---------------------------------------------------------------------------
# Drain: ordinary + exhausted budget must leave ordinary untouched (DB)
# ---------------------------------------------------------------------------


async def test_bg_exhausted_ordinary_unchanged_db(
    section_seam_env: asyncpg.Pool,
) -> None:
    user_id, record_id, base_id, run_id = await _seed(section_seam_env)
    ordinary_id = await _insert_job(
        section_seam_env,
        record_id=record_id,
        run_id=run_id,
        user_id=user_id,
        base_id=base_id,
        target_key="ord-budget",
        operation_fingerprint="translation_article_v1:o",
        input_json={"target_unit_ids": ["u1"]},
        status="queued",
    )
    from unittest.mock import AsyncMock, patch

    from app.services.reader_orchestration.execution_budget import (
        DurableBudgetLoadResult,
        ExecutionBudgetSnapshot,
    )

    exhausted = DurableBudgetLoadResult(
        layer_snapshots={
            "translation": ExecutionBudgetSnapshot(
                planned_calls=1,
                max_effective_calls=3,
                consumed_calls=3,
                remaining_calls=0,
                exhausted=True,
            ),
            "vocabulary": ExecutionBudgetSnapshot(
                planned_calls=0,
                max_effective_calls=0,
                consumed_calls=0,
                remaining_calls=0,
                exhausted=False,
            ),
            "grammar": ExecutionBudgetSnapshot(
                planned_calls=0,
                max_effective_calls=0,
                consumed_calls=0,
                remaining_calls=0,
                exhausted=False,
            ),
        },
        non_superseded_fingerprints={
            "translation": (),
            "vocabulary": (),
            "grammar": (),
        },
    )
    runtime = ReaderJobRuntime(pool=section_seam_env)
    worker = type(
        "W",
        (),
        {
            "process_claimed_translation_batch_job": AsyncMock(
                side_effect=AssertionError("LLM must not run")
            )
        },
    )()
    service = SectionTranslationDrainService(
        pool=section_seam_env,
        job_runtime=runtime,
        translation_worker=worker,  # type: ignore[arg-type]
    )
    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=exhausted),
    ):
        result = await service.process_job_id(
            job_id=ordinary_id,
            lease_owner="drain",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.REJECTED
    async with section_seam_env.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, failure_code, rationale_code FROM reader_jobs WHERE id = $1",
            ordinary_id,
        )
        run_status = await conn.fetchval(
            "SELECT status FROM reader_runs WHERE id = $1", run_id
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_job_events WHERE job_id = $1",
            ordinary_id,
        )
    assert row["status"] == "queued"
    assert row["failure_code"] is None
    assert row["rationale_code"] is None
    assert run_status == "queued"
    assert int(events) == 0
