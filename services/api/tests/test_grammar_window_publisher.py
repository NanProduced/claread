"""Tests for GrammarWindowPublisher: multi-unit publish transaction.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §3.3 (unit-scoped publish) + §8.4 (publish transaction) + §8.5 (lock coverage)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
    PublishedWindowResult,
    WindowCandidateContent,
)
from app.services.reader_orchestration.window_selector import CandidateItem
from app.services.reader_orchestration.zplus_bootstrap import ZPlusBootstrapService
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0015_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0015_layer_analysis_plans.sql"
).read_text(encoding="utf-8")

ARTICLE_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff.\n\n"
    "The committee, which had spent six months reviewing export data, "
    "labor surveys, and municipal tax receipts that rarely lined up neatly, "
    "claimed that the recovery was broad enough to justify ending the emergency "
    "grant program.\n\n"
    "Several shop owners warned that the headline numbers hid a "
    "more fragile street-level reality, because customers were still delaying "
    "purchases whenever wages, school fees, and transport costs rose in the same "
    "week."
)


@dataclass
class _TestEnv:
    pool: asyncpg.Pool
    admin_conn: asyncpg.Connection
    schema_name: str
    original_pool: asyncpg.Pool | None
    plan_id: UUID
    window_id: UUID
    job_id: UUID
    base_id: UUID
    record_id: UUID
    target_unit_ids: list[str]
    target_anchor_ids: list[str]


async def _setup_test_env() -> _TestEnv:
    schema_name = f"test_grammar_window_pub_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
    await admin_conn.execute(BASELINE_SQL)
    await admin_conn.execute(MIGRATION_0015_SQL)
    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=ARTICLE_TEXT,
        title="Grammar Window Pub Test",
        language="en",
    )
    service = ZPlusBootstrapService(pool=pool)
    result = await service.bootstrap_grammar_window_plan(
        record_id=article.record_id, base_id=article.base_id,
    )

    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            """
            SELECT id, job_id, target_unit_ids, target_anchor_ids
            FROM analysis_windows
            WHERE plan_id = $1
            ORDER BY window_index
            LIMIT 1
            """,
            result.plan_id,
        )

    return _TestEnv(
        pool=pool,
        admin_conn=admin_conn,
        schema_name=schema_name,
        original_pool=original_pool,
        plan_id=result.plan_id,
        window_id=window["id"],
        job_id=window["job_id"],
        base_id=article.base_id,
        record_id=article.record_id,
        target_unit_ids=list(window["target_unit_ids"]),
        target_anchor_ids=list(window["target_anchor_ids"]),
    )


async def _cleanup_test_env(env: _TestEnv) -> None:
    await env.pool.close()
    db_connection.DB_POOL = env.original_pool
    await env.admin_conn.execute(f'DROP SCHEMA IF EXISTS "{env.schema_name}" CASCADE')
    await env.admin_conn.close()


async def _claim_job(pool: asyncpg.Pool, job_id: UUID) -> UUID:
    lease_token = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'claimed',
                lease_owner = 'test-worker',
                lease_token = $2,
                lease_expires_at = NOW() + INTERVAL '1 hour',
                claimed_at = NOW(),
                attempt_count = COALESCE(attempt_count, 0) + 1,
                updated_at = NOW()
            WHERE id = $1
            """,
            job_id,
            lease_token,
        )
    return lease_token


def _make_candidates(
    target_unit_ids: list[str], target_anchor_ids: list[str]
) -> list[CandidateItem]:
    if not target_unit_ids or not target_anchor_ids:
        return []
    candidates: list[CandidateItem] = []
    # Create grammar_note candidates (one per unit, up to 2, distinct anchors)
    for i, unit_id in enumerate(target_unit_ids[:2]):
        if i >= len(target_anchor_ids):
            break
        candidates.append(
            CandidateItem(
                item_type="grammar_note",
                anchor_segment_id=target_anchor_ids[i],
                spans=[{"unit_id": unit_id}],
                semantic_dedup_key=f"grammar-dedup-{i}",
                pattern_key=f"grammar-pattern-{i}",
                quality_score=0.8 - i * 0.1,
            )
        )
    # Create sentence_analysis candidate for first unit
    candidates.append(
        CandidateItem(
            item_type="sentence_analysis",
            anchor_segment_id=target_anchor_ids[0],
            spans=[{"unit_id": target_unit_ids[0]}],
            semantic_dedup_key="sentence-dedup-0",
            pattern_key=None,
            quality_score=0.9,
        )
    )
    return candidates


async def _build_text_range_anchor(
    pool: asyncpg.Pool,
    base_id: UUID,
    anchor_segment_id: str,
) -> ReaderTextRangeAnchor:
    """Construct a valid ReaderTextRangeAnchor covering the full anchor segment.

    Queries the DB for the segment + unit + base text, then slices the
    selected_text from the unit text using the segment's UTF-16 offsets.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT seg.unit_id, seg.sentence_id, seg.segment_type,
                   seg.unit_start_utf16, seg.unit_end_utf16,
                   base.text AS base_text,
                   unit.base_start_utf16, unit.base_end_utf16
            FROM anchor_segments seg
            JOIN reading_bases base
              ON base.id = seg.base_id
             AND base.reading_record_id = seg.reading_record_id
            JOIN reading_units unit
              ON unit.reading_record_id = seg.reading_record_id
             AND unit.base_id = seg.base_id
             AND unit.unit_id = seg.unit_id
            WHERE seg.base_id = $1 AND seg.anchor_segment_id = $2
            """,
            base_id,
            anchor_segment_id,
        )
    if row is None:
        raise ValueError(f"anchor segment {anchor_segment_id} not found")

    unit_text = slice_by_utf16_offsets(
        str(row["base_text"]),
        int(row["base_start_utf16"]),
        int(row["base_end_utf16"]),
    )
    if unit_text is None or not unit_text:
        raise ValueError(
            f"could not slice unit text for anchor {anchor_segment_id}"
        )
    selected_text = slice_by_utf16_offsets(
        unit_text,
        int(row["unit_start_utf16"]),
        int(row["unit_end_utf16"]),
    )
    if selected_text is None or not selected_text:
        raise ValueError(
            f"could not slice selected_text for anchor {anchor_segment_id}"
        )
    return ReaderTextRangeAnchor(
        base_id=str(base_id),
        unit_id=str(row["unit_id"]),
        anchor_segment_id=anchor_segment_id,
        sentence_id=str(row["sentence_id"]) if row["sentence_id"] is not None else None,
        segment_type=str(row["segment_type"]),
        start_offset=int(row["unit_start_utf16"]),
        end_offset=int(row["unit_end_utf16"]),
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


async def _make_candidate_contents(
    pool: asyncpg.Pool,
    base_id: UUID,
    candidates: list[CandidateItem],
) -> list[WindowCandidateContent]:
    """Build WindowCandidateContent for each candidate, matched by dedup_key.

    Constructs proper ReaderTextRangeAnchor from DB segment data so the
    publisher can build valid GrammarNoteLayerOutput / SentenceAnalysisLayerOutput.
    """
    contents: list[WindowCandidateContent] = []
    for c in candidates:
        anchor = await _build_text_range_anchor(pool, base_id, c.anchor_segment_id)
        if c.item_type == "grammar_note":
            contents.append(
                WindowCandidateContent(
                    semantic_dedup_key=c.semantic_dedup_key,
                    grammar_point=f"grammar_point:{c.anchor_segment_id}",
                    pattern=c.pattern_key,
                    note=f"grammar note for {c.anchor_segment_id}",
                    spans=[anchor],
                )
            )
        else:  # sentence_analysis
            contents.append(
                WindowCandidateContent(
                    semantic_dedup_key=c.semantic_dedup_key,
                    label=f"label:{c.anchor_segment_id}",
                    analysis=f"analysis for {c.anchor_segment_id}",
                    chunks=[
                        SentenceAnalysisChunk(
                            order=1,
                            label="clause",
                            text=anchor.selected_text,
                        )
                    ],
                    anchor=anchor,
                )
            )
    return contents


@pytest.fixture
async def test_db_pool_with_window_and_candidates() -> AsyncIterator[
    tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ]
]:
    """Window status='running', job status='claimed', with test candidates.

    Yields ``(pool, job_id, lease_token, plan_id, window_id, candidates,
    candidate_contents, base_id, record_id)`` so tests can exercise both the
    legacy fallback path (omit ``candidate_contents``) and the §8.3 contract
    path (pass ``candidate_contents``).
    """
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        lease_token = await _claim_job(env.pool, env.job_id)
        candidates = _make_candidates(env.target_unit_ids, env.target_anchor_ids)
        candidate_contents = await _make_candidate_contents(
            env.pool, env.base_id, candidates
        )
        yield (
            env.pool,
            env.job_id,
            lease_token,
            env.plan_id,
            env.window_id,
            candidates,
            candidate_contents,
            env.base_id,
            env.record_id,
        )
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_pending_window() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='pending' (not 'running'); job claimed."""
    env = await _setup_test_env()
    try:
        # Leave window status as 'pending' (default from bootstrap)
        lease_token = await _claim_job(env.pool, env.job_id)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_queued_job_window() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='running'; job status='queued' (not 'claimed')."""
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        # Leave job status as 'queued' (default from bootstrap)
        lease_token = uuid4()  # token won't be checked (job status check fails first)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


@pytest.fixture
async def test_db_pool_with_window_no_candidates() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID]
]:
    """Window status='running'; job claimed; empty candidates list."""
    env = await _setup_test_env()
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        lease_token = await _claim_job(env.pool, env.job_id)
        yield (env.pool, env.job_id, lease_token, env.plan_id, env.window_id)
    finally:
        await _cleanup_test_env(env)


async def test_publish_window_publishes_multiple_units_in_one_transaction(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """§3.3 One window transaction publishes multiple unit-targeted layers."""
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert result.accepted_count > 0
    assert len(result.grammar_note_layer_ids) >= 1
    assert result.skipped is False
    assert isinstance(result, PublishedWindowResult)

    # Verify each accepted layer has target_scope='unit' and status='published'
    async with pool.acquire() as conn:
        for layer_id in result.grammar_note_layer_ids:
            layer = await conn.fetchrow(
                "SELECT target_scope, target_key, status FROM enhancement_layers WHERE id = $1",
                layer_id,
            )
            assert layer is not None
            assert layer["target_scope"] == "unit"
            assert layer["status"] == "published"


async def test_publish_window_skips_when_window_status_not_running(
    test_db_pool_with_pending_window: tuple[asyncpg.Pool, UUID, UUID, UUID, UUID],
) -> None:
    """Window status != 'running' → publish skipped."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_pending_window
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )
    assert result.skipped is True
    assert result.accepted_count == 0
    assert result.grammar_note_layer_ids == ()


async def test_publish_window_rejects_when_job_status_not_claimed(
    test_db_pool_with_queued_job_window: tuple[asyncpg.Pool, UUID, UUID, UUID, UUID],
) -> None:
    """Job status != 'claimed' → IllegalTransitionError."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_queued_job_window
    publisher = GrammarWindowPublisher(pool=pool)
    from app.services.reader_orchestration.job_runtime import IllegalTransitionError

    with pytest.raises(IllegalTransitionError):
        await publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=[],
        )


async def test_publish_window_updates_ledger_after_publish(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """Publish updates ledger: budget_used / covered_window_ids."""
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )

    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            plan_id,
        )
        budget_used = (
            plan["budget_used"]
            if isinstance(plan["budget_used"], dict)
            else json.loads(plan["budget_used"])
        )
        grammar_used = budget_used.get("grammar_note", {}).get("count", 0)
        assert grammar_used > 0

        covered = (
            plan["covered_window_ids"]
            if isinstance(plan["covered_window_ids"], list)
            else json.loads(plan["covered_window_ids"])
        )
        assert str(window_id) in covered or window_id in covered


async def test_publish_window_marks_window_completed_after_publish(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """Publish success → window.status='completed', completed_at set."""
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            "SELECT status, completed_at FROM analysis_windows WHERE id = $1",
            window_id,
        )
        assert window is not None
        assert window["status"] == "completed"
        assert window["completed_at"] is not None


async def test_publish_window_marks_window_no_op_when_no_accepted(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """All candidates rejected / empty → window.status='no_op'."""
    pool, job_id, lease_token, plan_id, window_id = test_db_pool_with_window_no_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )
    assert result.accepted_count == 0

    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1",
            window_id,
        )
        assert window is not None
        assert window["status"] == "no_op"


async def test_publish_window_marks_job_succeeded(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """Publish → reader_jobs.status='succeeded'."""
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, rationale_code FROM reader_jobs WHERE id = $1",
            job_id,
        )
        assert job is not None
        assert job["status"] == "succeeded"


# ---------------------------------------------------------------------------
# P1-4 / P2-7 tests: §8.3 layer contract + layer_published reader_event
# ---------------------------------------------------------------------------


async def test_publisher_output_json_uses_grammar_note_layer_contract(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """P1-4: grammar_note output_json conforms to GrammarNoteLayerOutput (§8.3).

    output_json.schema_version == 1
    output_json.items[0] has grammar_point / pattern / note / spans
    output_json.items[0] does NOT carry selector sidecar fields
    (semantic_dedup_key / pattern_key / quality_score).
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert len(result.grammar_note_layer_ids) >= 1

    async with pool.acquire() as conn:
        layer = await conn.fetchrow(
            "SELECT output_json FROM enhancement_layers WHERE id = $1",
            result.grammar_note_layer_ids[0],
        )
    output = layer["output_json"]
    if isinstance(output, str):
        output = json.loads(output)

    assert output["schema_version"] == 1
    assert len(output["items"]) >= 1
    item = output["items"][0]
    assert item["item_type"] == "grammar_note"
    assert item["grammar_point"]
    assert "note" in item and item["note"]
    assert "spans" in item and len(item["spans"]) >= 1
    # Selector sidecar fields MUST NOT appear in output_json (P1-4 fix)
    assert "semantic_dedup_key" not in item
    assert "pattern_key" not in item
    assert "quality_score" not in item


async def test_publisher_output_json_uses_sentence_analysis_layer_contract(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """P1-4: sentence_analysis output_json conforms to SentenceAnalysisLayerOutput.

    items[0] has anchor / label / analysis / chunks (≥1 chunk with order/label/text).
    Selector sidecar fields absent.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert len(result.sentence_analysis_layer_ids) >= 1

    async with pool.acquire() as conn:
        layer = await conn.fetchrow(
            "SELECT output_json FROM enhancement_layers WHERE id = $1",
            result.sentence_analysis_layer_ids[0],
        )
    output = layer["output_json"]
    if isinstance(output, str):
        output = json.loads(output)

    assert output["schema_version"] == 1
    item = output["items"][0]
    assert item["item_type"] == "sentence_analysis"
    assert "anchor" in item
    assert item["label"]
    assert item["analysis"]
    assert len(item["chunks"]) >= 1
    chunk = item["chunks"][0]
    assert chunk["order"] >= 1
    assert chunk["label"]
    assert chunk["text"]
    # Selector sidecar fields absent
    assert "semantic_dedup_key" not in item
    assert "pattern_key" not in item
    assert "quality_score" not in item


async def test_publisher_quality_json_stores_provenance_not_in_output_json(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """P1-4: provenance lives in quality_json, NOT in output_json.

    quality_json has plan_id / window_id / semantic_dedup_key /
    pattern_key / quality_score. output_json MUST NOT have any of these
    provenance fields at the top level.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert len(result.grammar_note_layer_ids) >= 1

    async with pool.acquire() as conn:
        layer = await conn.fetchrow(
            "SELECT output_json, quality_json FROM enhancement_layers WHERE id = $1",
            result.grammar_note_layer_ids[0],
        )
    output = layer["output_json"]
    if isinstance(output, str):
        output = json.loads(output)
    quality = layer["quality_json"]
    if isinstance(quality, str):
        quality = json.loads(quality)

    # Provenance present in quality_json
    assert quality["plan_id"] == str(plan_id)
    assert quality["window_id"] == str(window_id)
    assert "semantic_dedup_key" in quality
    assert "pattern_key" in quality
    assert "quality_score" in quality
    # Per-item provenance array
    assert "items" in quality and len(quality["items"]) >= 1

    # Provenance absent from output_json
    for field in (
        "plan_id",
        "window_id",
        "semantic_dedup_key",
        "pattern_key",
        "quality_score",
    ):
        assert field not in output, (
            f"provenance field {field!r} must not appear in output_json"
        )


async def test_publisher_emits_layer_published_event(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """P2-7: with event_runtime injected, layer_published events are appended.

    After publish, reader_events table contains one ``layer_published`` row
    per accepted layer, with payload_json.layer_id matching, and
    source_layer_id / source_job_id / source_run_id wired up.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        _base_id,
        record_id,
    ) = test_db_pool_with_window_and_candidates
    event_runtime = ReaderEventRuntime(pool=pool)
    publisher = GrammarWindowPublisher(pool=pool, event_runtime=event_runtime)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert result.accepted_count > 0

    expected_layer_ids = set(
        result.grammar_note_layer_ids + result.sentence_analysis_layer_ids
    )
    assert expected_layer_ids, "expected at least one accepted layer"

    async with pool.acquire() as conn:
        events = await conn.fetch(
            """
            SELECT event_type, payload_json, source_layer_id, source_job_id,
                   source_run_id
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
            ORDER BY sequence ASC
            """,
            record_id,
        )
    assert len(events) >= len(expected_layer_ids), (
        f"expected >= {len(expected_layer_ids)} layer_published events, "
        f"got {len(events)}"
    )

    seen_layer_ids: set[UUID] = set()
    for ev in events:
        payload = ev["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["record_id"] == str(record_id)
        assert payload["layer_type"] in (
            "grammar_note",
            "sentence_analysis",
        )
        assert payload["target_scope"] == "unit"
        assert payload["plan_id"] == str(plan_id)
        assert payload["window_id"] == str(window_id)
        layer_id = UUID(payload["layer_id"])
        # source_layer_id column should match payload layer_id
        assert ev["source_layer_id"] == layer_id
        assert ev["source_job_id"] == job_id
        seen_layer_ids.add(layer_id)

    # Every accepted layer has a matching event
    assert expected_layer_ids.issubset(seen_layer_ids), (
        f"missing events for layers: {expected_layer_ids - seen_layer_ids}"
    )


async def test_publisher_legacy_fallback_when_candidate_contents_none(
    test_db_pool_with_window_and_candidates: tuple[
        asyncpg.Pool,
        UUID,
        UUID,
        UUID,
        UUID,
        list[CandidateItem],
        list[WindowCandidateContent],
        UUID,
        UUID,
    ],
) -> None:
    """Backward compatibility: candidate_contents=None uses legacy sidecar shape.

    When called without ``candidate_contents`` (existing callers such as the
    pipeline runner / BBC regression), output_json retains the legacy
    selector-sidecar shape (items with semantic_dedup_key / pattern_key /
    quality_score) so ``_extract_dedup_keys`` in test_zplus_bbc_regression
    keeps working.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        _candidate_contents,
        _base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=None,
    )
    assert result.accepted_count > 0
    assert len(result.grammar_note_layer_ids) >= 1

    async with pool.acquire() as conn:
        layer = await conn.fetchrow(
            "SELECT output_json, quality_json FROM enhancement_layers WHERE id = $1",
            result.grammar_note_layer_ids[0],
        )
    output = layer["output_json"]
    if isinstance(output, str):
        output = json.loads(output)
    quality = layer["quality_json"]
    if isinstance(quality, str):
        quality = json.loads(quality)

    # Legacy shape: items carry sidecar fields
    item = output["items"][0]
    assert "semantic_dedup_key" in item
    assert "pattern_key" in item
    assert "quality_score" in item
    # quality_json still carries plan_id / window_id
    assert quality["plan_id"] == str(plan_id)
    assert quality["window_id"] == str(window_id)
