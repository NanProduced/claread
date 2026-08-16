"""Canonical analysis progress projection tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import slice_by_utf16_offsets
from app.database import connection as db_connection
from app.llm.call_guard import pop_blocked_real_llm_attempts
from app.services.reader_orchestration.analysis_progress_projection import (
    AnalysisProgressProjectionError,
    CapabilityJobFact,
    ReaderAnalysisProgressProjection,
    _needs_user_action,
    _reduce_overall,
    build_analysis_excerpt,
    reduce_capability_status,
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
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
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

_SHORT_TEXT = (
    "The committee revised the plan and clarified the timeline. "
    "Everyone understood the tradeoff."
)
# 6x25 stays in (1100, 2000] words and under the 12000 UTF-16 guardrail.
_STRUCTURED_TEXT = "\n\n".join(
    " ".join(
        f"Medium{i} structured sentence about local budgets and schools."
        for i in range(25)
    )
    for _ in range(6)
)
_LONG_TEXT = "\n\n".join(
    [
        " ".join(
            f"Word{i} placeholder sentence for long-form strategy bootstrap."
            for i in range(40)
        )
        for _ in range(8)
    ]
)


@pytest.fixture
async def progress_env() -> asyncpg.Pool:
    schema_name = f"test_reader_analysis_progress_{uuid4().hex}"
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


async def _submit(pool: asyncpg.Pool, *, user_id: UUID, text: str) -> UUID:
    result = await ArticleReadyPersistenceService(pool=pool).submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=text,
            title="Progress Projection",
            language="en",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )
    return result.record_id


async def _ids(pool: asyncpg.Pool, record_id: UUID) -> tuple[UUID, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active_base_id, generation FROM reading_records WHERE id = $1",
            record_id,
        )
    assert row is not None
    return row["active_base_id"], int(row["generation"])


async def _load(pool: asyncpg.Pool, record_id: UUID, user_id: UUID):
    return await ReaderAnalysisProgressProjection(pool=pool).load_progress(
        record_id=record_id, user_id=user_id
    )


def _fact(**overrides: object) -> CapabilityJobFact:
    payload: dict[str, object] = {
        "job_type": "build_vocabulary_layer_article",
        "status": "queued",
        "pause_owner": None,
        "rationale_code": None,
        "failure_code": None,
        "updated_at": datetime.now(UTC),
        "captured_resume_ready": False,
    }
    payload.update(overrides)
    return CapabilityJobFact(**payload)  # type: ignore[arg-type]


async def _set_status(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    job_types: tuple[str, ...],
    status: str,
    pause_owner: str | None = None,
    failure_code: str | None = None,
    rationale_code: str | None = None,
    failure_class: str | None = None,
    attempt_count: int | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = $3,
                pause_owner = $4,
                failure_code = COALESCE($5, failure_code),
                rationale_code = COALESCE($6, rationale_code),
                failure_class = COALESCE($7, failure_class),
                attempt_count = COALESCE($8, attempt_count),
                updated_at = NOW()
            WHERE reading_record_id = $1
              AND job_type = ANY($2::text[])
            """,
            record_id,
            list(job_types),
            status,
            pause_owner,
            failure_code,
            rationale_code,
            failure_class,
            attempt_count,
        )


async def _insert_captured_journal(
    pool: asyncpg.Pool, *, record_id: UUID, job_types: tuple[str, ...]
) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, attempt_count
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = ANY($2::text[])
            """,
            record_id,
            list(job_types),
        )
        for row in rows:
            await conn.execute(
                """
                INSERT INTO ai_model_execution_journal (
                    invocation_key, invocation_kind, reader_job_id,
                    attempt_ordinal, execution_slot, capture_state,
                    usage_delivery_state, resume_payload_kind,
                    resume_payload_schema_version,
                    usage_event_draft_schema_version,
                    normalized_payload_json, usage_event_draft_json,
                    capture_envelope_sha256, resume_payload_bytes,
                    usage_event_draft_bytes, captured_at
                )
                VALUES (
                    $1, 'reader.translation', $2, $3, 1, 'captured',
                    'pending', 'reader.translation.result', 1, 1,
                    '{}'::jsonb, '{}'::jsonb, repeat('a', 64), 2, 2, NOW()
                )
                """,
                f"reader:translation:{row['id']}:{row['attempt_count']}:1",
                row["id"],
                max(int(row["attempt_count"]), 1),
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
                    'proj_test', 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, NOW()
                )
                """,
                record_id,
                base_id,
                layer_type,
                unit_id,
                generation,
            )


def test_excerpt_collapses_whitespace_and_truncates_unicode() -> None:
    raw = "hello\n\n\tworld"
    assert build_analysis_excerpt(raw) == "hello world"
    long_text = "字" * 90
    excerpt = build_analysis_excerpt(long_text)
    assert excerpt == ("字" * 80) + "…"
    assert len(excerpt) == 81


def test_excerpt_utf16_slice_keeps_emoji_intact() -> None:
    text = "A😀B   C"
    sliced = slice_by_utf16_offsets(text, 0, 4)
    assert sliced == "A😀B"
    assert build_analysis_excerpt(sliced + "\n\n extra") == "A😀B extra"


def test_reducer_partial_and_quota_and_empty_eligible() -> None:
    eligible = frozenset({"u1", "u2"})
    partial = reduce_capability_status(
        eligible_ids=eligible,
        jobs=[_fact(status="failed_terminal", failure_code="provider_error")],
        completed_ids={"u1"},
    )
    assert partial[0] == "partial"
    quota = reduce_capability_status(
        eligible_ids=eligible,
        jobs=[
            _fact(
                job_type="build_grammar_bundle",
                status="paused",
                pause_owner="quota",
                rationale_code="quota_paused",
                failure_code="budget_exhausted",
            )
        ],
        completed_ids=set(),
    )
    assert quota == ("paused_quota", "budget_exhausted")
    empty = reduce_capability_status(
        eligible_ids=frozenset(),
        jobs=[],
        completed_ids=set(),
    )
    assert empty == ("completed", None)


def test_reducer_captured_resume_ready_is_queued() -> None:
    status, code = reduce_capability_status(
        eligible_ids=frozenset({"u1"}),
        jobs=[
            _fact(
                status="paused",
                pause_owner="system",
                rationale_code="model_execution_captured_resume_required",
                failure_code="post_provider_resume_required",
                captured_resume_ready=True,
            )
        ],
        completed_ids=set(),
    )
    assert (status, code) == ("queued", None)


def test_reducer_captured_resume_missing_journal_or_fence_is_failed() -> None:
    similar = _fact(
        status="paused",
        pause_owner="system",
        rationale_code="model_execution_captured_resume_required",
        failure_code="post_provider_resume_required",
        captured_resume_ready=False,
    )
    status, code = reduce_capability_status(
        eligible_ids=frozenset({"u1"}),
        jobs=[similar],
        completed_ids=set(),
    )
    assert status == "failed"
    assert code == "post_provider_resume_required"


def test_reducer_old_failure_then_success_clears_stale_code() -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    status, code = reduce_capability_status(
        eligible_ids=frozenset({"u1", "u2"}),
        jobs=[
            _fact(
                status="failed_terminal",
                failure_code="provider_error",
                updated_at=older,
            ),
            _fact(status="succeeded", updated_at=newer),
        ],
        completed_ids={"u1", "u2"},
    )
    assert (status, code) == ("completed", None)


def test_reducer_failed_terminal_without_codes_uses_stable_fallback() -> None:
    status, code = reduce_capability_status(
        eligible_ids=frozenset({"u1"}),
        jobs=[_fact(status="failed_terminal")],
        completed_ids=set(),
    )
    assert (status, code) == ("failed", "analysis_job_failed")


def test_reducer_unrecoverable_pause_without_codes_uses_stable_fallback() -> None:
    status, code = reduce_capability_status(
        eligible_ids=frozenset({"u1"}),
        jobs=[_fact(status="paused", pause_owner="system")],
        completed_ids=set(),
    )
    assert (status, code) == ("failed", "analysis_job_paused")


def test_overall_processing_keeps_independent_needs_user_action() -> None:
    overall = _reduce_overall(
        mode="segmented_on_demand",
        translation_status="completed",
        section_statuses=["processing", "not_started"],
        any_can_start=False,
        translation_jobs=[],
    )
    assert overall == "processing"
    assert (
        _needs_user_action(
            overall=overall,
            capability_statuses=["completed", "processing", "paused_quota"],
            jobs=[],
        )
        is True
    )


def test_overall_translation_quota_blocks_dependent_queued() -> None:
    overall = _reduce_overall(
        mode="segmented_on_demand",
        translation_status="paused_quota",
        section_statuses=["queued"],
        any_can_start=False,
        translation_jobs=[
            _fact(
                job_type="translate_article",
                status="paused",
                pause_owner="quota",
                failure_code="budget_exhausted",
            )
        ],
    )
    assert overall == "paused_quota"
    assert (
        _needs_user_action(
            overall=overall,
            capability_statuses=["paused_quota", "queued"],
            jobs=[],
        )
        is True
    )


def test_overall_unrecoverable_translation_pause_blocks_dependent_queued() -> None:
    pause = _fact(
        job_type="translate_article",
        status="paused",
        pause_owner="system",
        rationale_code="provider_unavailable",
    )
    overall = _reduce_overall(
        mode="segmented_on_demand",
        translation_status="failed",
        section_statuses=["queued"],
        any_can_start=False,
        translation_jobs=[pause],
    )
    assert overall == "failed"
    assert (
        _needs_user_action(
            overall=overall,
            capability_statuses=["failed", "queued"],
            jobs=[pause],
        )
        is True
    )


async def test_short_batch_is_automatic_and_cannot_start(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_SHORT_TEXT)
    progress = await _load(progress_env, record_id, user_id)
    assert progress.mode == "automatic"
    assert progress.plan_version == ANALYSIS_SECTION_PLAN_VERSION
    assert progress.sections
    assert all(section.can_start is False for section in progress.sections)


async def test_structured_batch_is_not_segmented(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_STRUCTURED_TEXT)
    progress = await _load(progress_env, record_id, user_id)
    assert progress.mode == "automatic"
    assert all(section.can_start is False for section in progress.sections)


async def test_grouped_translation_queued_blocks_later_sections(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.mode == "segmented_on_demand"
    assert progress.active_phase == "translation"
    assert progress.translation_status == "queued"
    assert progress.sections[0].status in {"queued", "not_started"}
    assert all(section.can_start is False for section in progress.sections)
    if len(progress.sections) > 1:
        assert all(
            section.status == "not_started" for section in progress.sections[1:]
        )


async def test_translation_terminal_moves_active_phase_to_analysis(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article", "generate_display_title_zh"),
        status="succeeded",
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.active_phase == "analysis"
    assert progress.sections[0].status == "queued"
    assert progress.sections[0].can_start is False


async def test_first_section_complete_unlocks_waiting_user(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, generation = await _ids(progress_env, record_id)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    planned = await _planned_sections(progress_env, record_id, base_id)
    first = planned[0]
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=(
            "translate_article",
            "generate_display_title_zh",
            "build_vocabulary_layer_article",
            "build_grammar_bundle",
        ),
        status="succeeded",
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="translation",
        unit_ids=[
            str(unit["unit_id"])
            for unit in await _unit_rows(progress_env, record_id, base_id)
        ],
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="vocabulary",
        unit_ids=list(first.target_unit_ids),
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="grammar_note",
        unit_ids=list(first.target_unit_ids),
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].status == "completed"
    assert progress.completed_section_count == 1
    assert progress.overall_status == "waiting_user"
    assert progress.needs_user_action is True
    assert progress.active_phase is None
    if len(progress.sections) > 1:
        assert progress.sections[1].can_start is True
        assert progress.sections[0].can_start is False


async def test_active_section_is_lowest_order_index(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, _generation = await _ids(progress_env, record_id)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article", "generate_display_title_zh"),
        status="succeeded",
    )
    planned = await _planned_sections(progress_env, record_id, base_id)
    assert len(planned) >= 2
    async with progress_env.acquire() as conn:
        template = await conn.fetchrow(
            """
            SELECT * FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
            """,
            record_id,
        )
        assert template is not None
        payload = dict(template["input_json"])
        payload["analysis_section_id"] = planned[1].section_id
        payload["analysis_section_order_index"] = 1
        payload["analysis_section_unit_ids"] = list(planned[1].target_unit_ids)
        payload["target_unit_ids"] = list(planned[1].target_unit_ids)
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, status, priority, expected_generation,
                operation_fingerprint, idempotency_key, input_hash, input_json,
                max_attempts
            )
            SELECT reading_record_id, base_id, run_id, user_id, job_type,
                   target_type, $2, 'queued', priority, expected_generation,
                   operation_fingerprint, $3, $3, $4::jsonb, max_attempts
            FROM reader_jobs WHERE id = $1
            """,
            template["id"],
            planned[1].section_id,
            f"dup-{planned[1].section_id}",
            payload,
        )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.active_section_id == planned[0].section_id
    assert progress.completed_section_count == 0


async def test_one_success_one_failure_is_partial(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, generation = await _ids(progress_env, record_id)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    planned = await _planned_sections(progress_env, record_id, base_id)
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article", "generate_display_title_zh"),
        status="succeeded",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_vocabulary_layer_article",),
        status="succeeded",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_grammar_bundle",),
        status="failed_terminal",
        failure_code="provider_error",
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="vocabulary",
        unit_ids=list(planned[0].target_unit_ids),
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].vocabulary_status == "completed"
    assert progress.sections[0].grammar_status == "failed"
    assert progress.sections[0].status == "partial"
    assert progress.sections[0].failure_code == "provider_error"


async def test_quota_pause_is_paused_quota(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="succeeded",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_vocabulary_layer_article",),
        status="paused",
        pause_owner="quota",
        failure_code="budget_exhausted",
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].status == "paused_quota"
    assert progress.overall_status == "paused_quota"
    assert progress.needs_user_action is True
    assert progress.sections[0].can_start is False


async def test_policy_zero_targets_complete_without_jobs(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, _generation = await _ids(progress_env, record_id)
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
    async with progress_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_units
            SET metadata_json = $3::jsonb
            WHERE reading_record_id = $1 AND base_id = $2
            """,
            record_id,
            base_id,
            skip_policy,
        )
    progress = await _load(progress_env, record_id, user_id)
    assert all(section.vocabulary_status == "completed" for section in progress.sections)
    assert all(section.grammar_status == "completed" for section in progress.sections)
    assert all(section.status == "completed" for section in progress.sections)


async def test_stale_generation_and_malformed_jobs_do_not_pollute(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, _generation = await _ids(progress_env, record_id)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    async with progress_env.acquire() as conn:
        stale_base_id = await conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, text,
                content_sha256, content_utf16_length, canonicalizer_version,
                builder_version, segmenter_version, language, title_snapshot,
                navigation_json, diagnostics_json, status
            )
            SELECT reading_record_id, base_version + 1, record_generation + 1,
                   text, content_sha256, content_utf16_length,
                   canonicalizer_version, builder_version, segmenter_version,
                   language, title_snapshot, navigation_json, diagnostics_json,
                   'superseded'
            FROM reading_bases
            WHERE id = $1
            RETURNING id
            """,
            base_id,
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET base_id = $2,
                expected_generation = 2,
                status = 'succeeded'
            WHERE reading_record_id = $1
              AND job_type = 'build_vocabulary_layer_article'
            """,
            record_id,
            stale_base_id,
        )
        await conn.execute(
            """
            UPDATE reader_jobs
            SET input_json = '{"request_origin":"automatic_analysis_section_v1"}'::jsonb,
                status = 'succeeded'
            WHERE reading_record_id = $1
              AND job_type = 'build_grammar_bundle'
            """,
            record_id,
        )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].vocabulary_status != "completed"
    assert progress.sections[0].grammar_status == "failed"
    assert progress.sections[0].failure_code == "malformed_analysis_job_input"


async def test_missing_and_non_owner_records_raise_lookup_error(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_SHORT_TEXT)
    projection = ReaderAnalysisProgressProjection(pool=progress_env)
    with pytest.raises(LookupError):
        await projection.load_progress(record_id=uuid4(), user_id=user_id)
    with pytest.raises(LookupError):
        await projection.load_progress(record_id=record_id, user_id=uuid4())


async def test_projection_makes_zero_provider_attempts(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await _load(progress_env, record_id, user_id)
    assert pop_blocked_real_llm_attempts() == []


async def test_captured_resume_journal_is_queued(progress_env: asyncpg.Pool) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
        pause_owner="system",
        rationale_code="model_execution_captured_resume_required",
        failure_code="post_provider_resume_required",
        failure_class="model_execution",
        attempt_count=1,
    )
    await _insert_captured_journal(
        progress_env, record_id=record_id, job_types=("translate_article",)
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.translation_status == "queued"
    assert progress.overall_status == "queued"


async def test_captured_resume_missing_journal_or_fence_is_failed(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
        pause_owner="system",
        rationale_code="model_execution_captured_resume_required",
        failure_code="post_provider_resume_required",
        failure_class="model_execution",
        attempt_count=1,
    )
    missing_journal = await _load(progress_env, record_id, user_id)
    assert missing_journal.translation_status == "failed"
    assert missing_journal.overall_status == "failed"
    assert missing_journal.needs_user_action is True

    await _insert_captured_journal(
        progress_env, record_id=record_id, job_types=("translate_article",)
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
        pause_owner="system",
        rationale_code="model_execution_captured_resume_required",
        failure_code="post_provider_resume_required",
        failure_class="other",
        attempt_count=1,
    )
    missing_fence = await _load(progress_env, record_id, user_id)
    assert missing_fence.translation_status == "failed"
    assert missing_fence.needs_user_action is True


async def test_old_failure_then_success_clears_section_failure(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    base_id, generation = await _ids(progress_env, record_id)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    planned = await _planned_sections(progress_env, record_id, base_id)
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article", "generate_display_title_zh"),
        status="succeeded",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_vocabulary_layer_article", "build_grammar_bundle"),
        status="failed_terminal",
        failure_code="provider_error",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_vocabulary_layer_article", "build_grammar_bundle"),
        status="succeeded",
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="vocabulary",
        unit_ids=list(planned[0].target_unit_ids),
    )
    await _publish_units(
        progress_env,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        layer_type="grammar_note",
        unit_ids=list(planned[0].target_unit_ids),
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].vocabulary_status == "completed"
    assert progress.sections[0].grammar_status == "completed"
    assert progress.sections[0].status == "completed"
    assert progress.sections[0].failure_code is None


async def test_processing_plus_quota_keeps_needs_user_action(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article", "generate_display_title_zh"),
        status="succeeded",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_vocabulary_layer_article",),
        status="paused",
        pause_owner="quota",
        failure_code="budget_exhausted",
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("build_grammar_bundle",),
        status="claimed",
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.sections[0].status == "processing"
    assert progress.overall_status == "processing"
    assert progress.needs_user_action is True
    assert progress.sections[0].vocabulary_status == "paused_quota"


async def test_translation_quota_blocks_dependent_queued(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
        pause_owner="quota",
        failure_code="budget_exhausted",
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.translation_status == "paused_quota"
    assert progress.sections[0].status in {"queued", "not_started"}
    assert progress.overall_status == "paused_quota"
    assert progress.needs_user_action is True


async def test_unrecoverable_translation_pause_blocks_dependent_queued(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_LONG_TEXT)
    await EnhancementJobBootstrapService(pool=progress_env).bootstrap_missing_jobs(
        record_id=record_id, user_id=user_id
    )
    await _set_status(
        progress_env,
        record_id=record_id,
        job_types=("translate_article",),
        status="paused",
        pause_owner="system",
        rationale_code="provider_unavailable",
    )
    progress = await _load(progress_env, record_id, user_id)
    assert progress.translation_status == "failed"
    assert progress.sections[0].status in {"queued", "not_started"}
    assert progress.overall_status == "failed"
    assert progress.needs_user_action is True


async def test_null_active_base_is_inconsistent_active_base(
    progress_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(progress_env)
    record_id = await _submit(progress_env, user_id=user_id, text=_SHORT_TEXT)
    async with progress_env.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
            record_id,
        )
    projection = ReaderAnalysisProgressProjection(pool=progress_env)
    with pytest.raises(AnalysisProgressProjectionError) as exc_info:
        await projection.load_progress(record_id=record_id, user_id=user_id)
    assert exc_info.value.code == "inconsistent_active_base"


async def _unit_rows(
    pool: asyncpg.Pool, record_id: UUID, base_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT unit_id, order_index, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            """,
            record_id,
            base_id,
        )


async def _planned_sections(pool: asyncpg.Pool, record_id: UUID, base_id: UUID):
    rows = await _unit_rows(pool, record_id, base_id)
    return plan_analysis_sections(
        str(base_id),
        [
            AnalysisSectionUnit(
                unit_id=str(row["unit_id"]),
                order_index=int(row["order_index"]),
                text_length=int(row["base_end_utf16"]) - int(row["base_start_utf16"]),
            )
            for row in rows
        ],
    )
