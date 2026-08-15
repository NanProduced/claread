"""GROUPED_WINDOWED first-section auto bootstrap and translation-terminal gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.llm.call_guard import pop_blocked_real_llm_attempts
from app.services.reader_orchestration.analysis_section_jobs import (
    ANALYSIS_SECTION_REQUEST_ORIGIN,
    GRAMMAR_ANALYSIS_SECTION_FINGERPRINT,
    GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION,
    VOCABULARY_ANALYSIS_SECTION_FINGERPRINT,
    VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION,
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
from app.services.reader_orchestration.completion_finalizer import (
    COMPLETION_TARGET_READINESS_STATE,
    CompletionFinalizer,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementBootstrapJobCounts,
    EnhancementBootstrapSummary,
    EnhancementJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime
from app.services.reader_orchestration.pipeline_runner import (
    EnhancementOutcomeCounts,
    EnhancementWorkerTickCounts,
    ReaderPipelineRunSummary,
)
from app.services.reader_orchestration.worker_loop import (
    ENHANCEMENT_PIPELINE_JOB_TYPES,
    ReaderEnhancementWorkerLoopService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
)

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
    pytest.mark.anyio,
]

_LONG_TEXT = "\n\n".join(
    [
        " ".join(
            f"Word{i} placeholder sentence for long-form strategy bootstrap."
            for i in range(40)
        )
        for _ in range(8)
    ]
)
_SHORT_TEXT = (
    "The committee revised the plan and clarified the timeline. "
    "Everyone understood the tradeoff."
)


@pytest.fixture
async def segmented_env() -> asyncpg.Pool:
    schema_name = f"test_reader_segmented_auto_{uuid4().hex}"
    admin = await connect_admin()
    await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    await admin.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin.execute(f'SET search_path TO "{schema_name}", public')
    await admin.execute(BASELINE_SQL)
    await admin.close()
    pool = await make_pool(schema_name)
    previous_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        db_connection.DB_POOL = previous_pool
        await pool.close()
        cleanup = await connect_admin()
        await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await cleanup.close()


async def _submit(pool: asyncpg.Pool, *, user_id: UUID, text: str) -> UUID:
    result = await ArticleReadyPersistenceService(pool=pool).submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=text,
            title="Segmented Auto Bootstrap",
            language="en",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )
    return result.record_id


async def _record_ids(pool: asyncpg.Pool, record_id: UUID) -> tuple[UUID, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active_base_id, generation FROM reading_records WHERE id = $1",
            record_id,
        )
    assert row is not None
    return row["active_base_id"], int(row["generation"])


async def _jobs(pool: asyncpg.Pool, record_id: UUID) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, job_type, target_type, target_key, status,
                   operation_fingerprint, input_json, input_hash
            FROM reader_jobs
            WHERE reading_record_id = $1
            ORDER BY created_at ASC, id ASC
            """,
            record_id,
        )
    return [dict(row) for row in rows]


async def _plan_first_section(
    pool: asyncpg.Pool, record_id: UUID, base_id: UUID
) -> tuple[object, list[str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT unit_id, order_index, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            """,
            record_id,
            base_id,
        )
    units = [
        AnalysisSectionUnit(
            unit_id=str(row["unit_id"]),
            order_index=int(row["order_index"]),
            text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
        )
        for row in rows
    ]
    sections = plan_analysis_sections(str(base_id), units)
    assert sections
    return sections[0], [str(row["unit_id"]) for row in rows]


async def _set_job_status(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    job_types: tuple[str, ...],
    status: str,
    available_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = $3,
                available_at = COALESCE($4, available_at),
                updated_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = ANY($2::text[])
            """,
            record_id,
            list(job_types),
            status,
            available_at,
        )


def _section_jobs(jobs: list[dict], job_type: str) -> list[dict]:
    return [
        job
        for job in jobs
        if job["job_type"] == job_type
        and job["input_json"].get("request_origin") == ANALYSIS_SECTION_REQUEST_ORIGIN
    ]


async def test_grouped_windowed_creates_first_section_depth_jobs_only(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    base_id, _generation = await _record_ids(segmented_env, record_id)
    first, all_unit_ids = await _plan_first_section(segmented_env, record_id, base_id)
    assert len(first.target_unit_ids) < len(all_unit_ids)

    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    jobs = await _jobs(segmented_env, record_id)

    translation = [job for job in jobs if job["job_type"] == "translate_article"]
    assert len(translation) >= 2
    translation_units = [
        unit_id
        for job in translation
        for unit_id in job["input_json"]["target_unit_ids"]
    ]
    assert set(translation_units) == set(all_unit_ids)

    vocab = _section_jobs(jobs, "build_vocabulary_layer_article")
    grammar = _section_jobs(jobs, "build_grammar_bundle")
    assert len(vocab) == 1
    assert len(grammar) == 1
    for job in (*vocab, *grammar):
        payload = job["input_json"]
        assert job["target_type"] == "unit_range"
        assert job["target_key"] == first.section_id
        assert payload["analysis_section_id"] == first.section_id
        assert payload["analysis_section_plan_version"] == ANALYSIS_SECTION_PLAN_VERSION
        assert payload["analysis_section_order_index"] == 0
        assert payload["analysis_section_unit_ids"] == list(first.target_unit_ids)
        assert payload["requires_translation_terminal"] is True
        assert payload["article_route"] == "grouped_windowed"
        assert set(payload["target_unit_ids"]) <= set(first.target_unit_ids)
        assert payload["target_unit_ids"]
        later = set(all_unit_ids) - set(first.target_unit_ids)
        assert later
        assert not set(payload["target_unit_ids"]) & later

    assert vocab[0]["operation_fingerprint"].startswith(
        VOCABULARY_ANALYSIS_SECTION_FINGERPRINT + ":"
    )
    assert grammar[0]["operation_fingerprint"].startswith(
        GRAMMAR_ANALYSIS_SECTION_FINGERPRINT + ":"
    )
    async with segmented_env.acquire() as conn:
        policy = await conn.fetchval(
            "SELECT policy_version FROM reader_runs WHERE id = "
            "(SELECT run_id FROM reader_jobs WHERE id = $1)",
            vocab[0]["id"],
        )
        grammar_policy = await conn.fetchval(
            "SELECT policy_version FROM reader_runs WHERE id = "
            "(SELECT run_id FROM reader_jobs WHERE id = $1)",
            grammar[0]["id"],
        )
        window_jobs = await conn.fetchval(
            "SELECT count(*) FROM reader_jobs "
            "WHERE reading_record_id = $1 AND job_type = 'build_grammar_bundle_window'",
            record_id,
        )
        analysis_windows = await conn.fetchval("SELECT count(*) FROM analysis_windows")
        layer_plans = await conn.fetchval("SELECT count(*) FROM layer_analysis_plans")
    assert policy == VOCABULARY_ANALYSIS_SECTION_POLICY_VERSION
    assert grammar_policy == GRAMMAR_ANALYSIS_SECTION_POLICY_VERSION
    assert window_jobs == 0
    assert analysis_windows == 0
    assert layer_plans == 0


async def test_grouped_windowed_bootstrap_is_idempotent(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    service = EnhancementJobBootstrapService(pool=segmented_env)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    first = await _jobs(segmented_env, record_id)
    await service.bootstrap_missing_jobs(record_id=record_id, user_id=user_id)
    second = await _jobs(segmented_env, record_id)
    assert {job["id"] for job in first} == {job["id"] for job in second}
    assert len(_section_jobs(second, "build_vocabulary_layer_article")) == 1
    assert len(_section_jobs(second, "build_grammar_bundle")) == 1


async def test_active_translation_blocks_claim_and_scan(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    runtime = ReaderJobRuntime(pool=segmented_env)
    vocab = await runtime.claim_next_job(
        lease_owner="w1",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer_article",
        reading_record_id=record_id,
    )
    grammar = await runtime.claim_next_job(
        lease_owner="w1",
        lease_duration=timedelta(seconds=30),
        job_type="build_grammar_bundle",
        reading_record_id=record_id,
    )
    assert vocab is None
    assert grammar is None

    await _set_job_status(
        segmented_env,
        record_id=record_id,
        job_types=("generate_display_title_zh", "translate_article"),
        status="retry_later",
        available_at=datetime.now(UTC) + timedelta(hours=1),
    )
    candidates = await ReaderEnhancementWorkerLoopService(
        pool=segmented_env
    ).scan_eligible_records(batch_size=20)
    match = [row for row in candidates if row.record_id == record_id]
    assert match == [] or match[0].runnable_job_count == 0

    await _set_job_status(
        segmented_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
    )
    paused = await ReaderEnhancementWorkerLoopService(
        pool=segmented_env
    ).scan_eligible_records(batch_size=20)
    paused_match = [row for row in paused if row.record_id == record_id]
    assert paused_match == [] or paused_match[0].runnable_job_count == 0

    await _set_job_status(
        segmented_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="failed_terminal",
    )
    await _set_job_status(
        segmented_env,
        record_id=record_id,
        job_types=("generate_display_title_zh",),
        status="succeeded",
    )
    after = await ReaderEnhancementWorkerLoopService(
        pool=segmented_env
    ).scan_eligible_records(batch_size=20)
    after_match = next(row for row in after if row.record_id == record_id)
    assert after_match.runnable_job_count >= 2
    claimed = await runtime.claim_next_job(
        lease_owner="w1",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer_article",
        reading_record_id=record_id,
    )
    assert claimed is not None
    assert claimed.job_type == "build_vocabulary_layer_article"


async def test_first_section_jobs_gate_coverage_complete(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    base_id, generation = await _record_ids(segmented_env, record_id)
    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    summary = ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=generation,
        bootstrap=EnhancementBootstrapSummary(
            record_id=record_id,
            base_id=base_id,
            expected_generation=generation,
            last_event_sequence=1,
            job_counts=EnhancementBootstrapJobCounts(),
        ),
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(no_job=3),
        total_ticks=3,
        total_jobs=0,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason="all_workers_no_job",
        stopped_outcome=None,
        attention_code=None,
    )
    finalizer = CompletionFinalizer()
    event_runtime = ReaderEventRuntime(pool=segmented_env)
    async with segmented_env.acquire() as conn:
        async with conn.transaction():
            blocked = await finalizer.finalize_completion_state(
                conn,
                record_id=record_id,
                base_id=base_id,
                expected_generation=generation,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )
    assert blocked.finalized is False
    assert blocked.skip_reason == "non_terminal_jobs_present"

    await _set_job_status(
        segmented_env,
        record_id=record_id,
        job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
        status="succeeded",
    )
    async with segmented_env.acquire() as conn:
        async with conn.transaction():
            done = await finalizer.finalize_completion_state(
                conn,
                record_id=record_id,
                base_id=base_id,
                expected_generation=generation,
                summary=summary,
                enhancement_job_types=ENHANCEMENT_PIPELINE_JOB_TYPES,
                event_runtime=event_runtime,
                updated_at=datetime.now(UTC),
            )
    assert done.finalized is True
    async with segmented_env.acquire() as conn:
        state = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            record_id,
        )
    assert state == COMPLETION_TARGET_READINESS_STATE


async def test_policy_filtered_first_section_creates_no_depth_job(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    base_id, _generation = await _record_ids(segmented_env, record_id)
    first, _all_ids = await _plan_first_section(segmented_env, record_id, base_id)
    skip_policy = {
        "semantic": {
            "contract_version": "semantic_contract_v1",
            "resolver_version": "automatic_layer_policy_v1",
            "content_role": "quotation",
            "automatic_layer_policy": {
                "translation": True,
                "vocabulary": False,
                "grammar_note": False,
                "sentence_analysis": False,
            },
        }
    }
    async with segmented_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_units
            SET metadata_json = $3::jsonb
            WHERE reading_record_id = $1
              AND base_id = $2
            """,
            record_id,
            base_id,
            skip_policy,
        )
    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    jobs = await _jobs(segmented_env, record_id)
    assert _section_jobs(jobs, "build_vocabulary_layer_article") == []
    assert _section_jobs(jobs, "build_grammar_bundle") == []
    assert any(job["job_type"] == "translate_article" for job in jobs)
    async with segmented_env.acquire() as conn:
        stored = await conn.fetchval(
            """
            SELECT metadata_json->'semantic'->'automatic_layer_policy'
            FROM reading_units
            WHERE reading_record_id = $1 AND unit_id = $2
            """,
            record_id,
            first.start_unit_id,
        )
    assert stored["vocabulary"] is False
    assert stored["grammar_note"] is False


async def test_short_batch_topology_unchanged(segmented_env: asyncpg.Pool) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_SHORT_TEXT)
    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    jobs = await _jobs(segmented_env, record_id)
    vocab = [job for job in jobs if job["job_type"] == "build_vocabulary_layer_article"]
    grammar = [
        job
        for job in jobs
        if job["job_type"] == "build_grammar_bundle" and job["target_type"] == "unit_range"
    ]
    assert len(vocab) == 1
    assert len(grammar) == 1
    assert vocab[0]["input_json"].get("request_origin") != ANALYSIS_SECTION_REQUEST_ORIGIN
    assert grammar[0]["input_json"].get("request_origin") != ANALYSIS_SECTION_REQUEST_ORIGIN
    assert vocab[0]["target_key"] == str(record_id)
    assert vocab[0]["operation_fingerprint"].startswith("vocabulary_article_v1:")


async def test_segmented_auto_bootstrap_makes_zero_provider_attempts(
    segmented_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(segmented_env)
    record_id = await _submit(segmented_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=segmented_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    assert pop_blocked_real_llm_attempts() == []
