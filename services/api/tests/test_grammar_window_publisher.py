"""Tests for GrammarWindowPublisher: multi-unit publish transaction.

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §3.3 (unit-scoped publish) + §8.4 (publish transaction) + §8.5 (lock coverage)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
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
from app.services.reader_orchestration.grammar_candidate_policy import (
    DEDUP_HINT_DUPLICATE_REASON_CODE,
    DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
)
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
    PublishedWindowResult,
    WindowCandidateContent,
)
from app.services.reader_orchestration.window_selector import (
    CandidateItem,
    DedupRejectionMetadata,
    RejectedCandidate,
    SelectionGate,
    SelectorLedger,
)
from app.services.reader_orchestration.zplus_bootstrap import (
    ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
    ZPlusBootstrapService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio


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

# Long article (>1000 chars) for density gate tests. The RECORD_DENSITY gate
# uses density = published_count / max(base_len / 1000, 1.0); with a short
# article the denominator collapses to 1.0 and the cap acts as a raw count.
# This text is ~2600 chars, enough for density_denom > 1.0 so 3 published
# grammar_note items don't trigger cap 3.0.
_LONG_ARTICLE_PARAGRAPH = (
    "The researchers examined how municipal governments allocated emergency "
    "grants during the pandemic, comparing data across twelve regions and "
    "three fiscal quarters.\n\n"
    "They found that cities with pre-existing relief frameworks distributed "
    "funds more quickly, though unevenly, while those without such frameworks "
    "struggled to identify eligible recipients.\n\n"
    "Several economists noted that the headline numbers concealed significant "
    "delays in processing applications, particularly from small businesses "
    "that lacked dedicated accounting staff.\n\n"
)
LONG_ARTICLE_TEXT = _LONG_ARTICLE_PARAGRAPH * 8  # ~2600 chars, > 1000


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


async def _setup_test_env(
    *, article_text: str = ARTICLE_TEXT,
    title: str = "Grammar Window Pub Test",
) -> _TestEnv:
    schema_name = f"test_grammar_window_pub_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
    await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
    await admin_conn.execute(BASELINE_SQL)
    pool = await make_pool(schema_name)
    db_connection.DB_POOL = pool

    user_id = await insert_user(pool)
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=article_text,
        title=title,
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


async def _set_job_fingerprint(
    pool: asyncpg.Pool, job_id: UUID, fingerprint: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET operation_fingerprint = $2 WHERE id = $1",
            job_id,
            fingerprint,
        )


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
                quality_score=4 - i,  # int: 4, 3
                reading_blocker=False,
                dedup_hint=f"grammar-hint-{i}",
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
            quality_score=5,  # int
            reading_blocker=False,
            dedup_hint="sentence-hint-0",
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


@pytest.mark.parametrize(
    "fingerprint",
    [
        # Exact legacy base fingerprint.
        ZPLUS_GRAMMAR_OPERATION_FINGERPRINT,
        # Composed strategy fingerprint (base:{strategy_hash}).
        f"{ZPLUS_GRAMMAR_OPERATION_FINGERPRINT}:strategy_hash_abc123",
        # Composed strategy + semantic token (base:{strategy_hash}:{semantic}),
        # shape written by zplus_bootstrap; semantic token itself may contain ':'.
        f"{ZPLUS_GRAMMAR_OPERATION_FINGERPRINT}"
        ":strategy_hash_abc123:sem:legacy:legacy_open:mode:enforce",
    ],
)
async def test_publish_window_accepts_exact_or_composed_fingerprint(
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
    fingerprint: str,
) -> None:
    """Publisher accepts the exact base or a ``base:`` composed fingerprint."""
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
    await _set_job_fingerprint(pool, job_id, fingerprint)
    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert result.skipped is False
    assert result.accepted_count > 0


@pytest.mark.parametrize(
    "fingerprint",
    [
        # Boundary: version bump must not match v1.
        "grammar_bundle_window_v10",
        # Pseudo-prefix without ':' separator must not match.
        "grammar_bundle_window_v1abc",
        # Unrelated fingerprint.
        "grammar_bundle_v1",
    ],
)
async def test_publish_window_rejects_boundary_invalid_fingerprint(
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
    fingerprint: str,
) -> None:
    """Boundary-aware rejection: v10 / non-':' suffix / other fingerprints."""
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
    await _set_job_fingerprint(pool, job_id, fingerprint)
    publisher = GrammarWindowPublisher(pool=pool)
    from app.services.reader_orchestration.job_runtime import IllegalTransitionError

    with pytest.raises(IllegalTransitionError, match="operation_fingerprint mismatch"):
        await publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=candidates,
            candidate_contents=candidate_contents,
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
    # P1-2 self-rating contract: reading_blocker / dedup_hint are now part
    # of the audit trail in quality_json.
    assert "reading_blocker" in quality
    assert "dedup_hint" in quality
    assert isinstance(quality["reading_blocker"], bool)
    assert isinstance(quality["dedup_hint"], str)
    assert quality["dedup_hint"]  # non-empty
    # Per-item reading_blocker / dedup_hint
    for item in quality["items"]:
        assert "reading_blocker" in item
        assert "dedup_hint" in item
        assert isinstance(item["reading_blocker"], bool)
        assert isinstance(item["dedup_hint"], str)
        assert item["dedup_hint"]  # non-empty

    # Provenance absent from output_json
    for field in (
        "plan_id",
        "window_id",
        "semantic_dedup_key",
        "pattern_key",
        "quality_score",
        "reading_blocker",
        "dedup_hint",
    ):
        assert field not in output, (
            f"provenance field {field!r} must not appear in output_json"
        )


async def test_publisher_ledger_has_no_empty_dedup_keys(
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
    """P1-2 self-rating contract: published_dedup_keys_by_type JSONB must
    only contain non-empty [anchor, hint] 2-tuples after publish.

    The scoped dedup key is (anchor_segment_id, normalized_dedup_hint).
    Both elements MUST be non-empty. The publisher's fail-safe skips empty
    dedup_hint, and anchors always come from candidate.anchor_segment_id
    which is required to be non-empty by the INVALID_ANCHOR gate.
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
    assert result.accepted_count > 0

    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT published_dedup_keys_by_type "
            "FROM layer_analysis_plans WHERE id = $1",
            plan_id,
        )
    raw_keys = plan["published_dedup_keys_by_type"]
    if isinstance(raw_keys, str):
        raw_keys = json.loads(raw_keys)
    assert raw_keys, "expected non-empty published_dedup_keys_by_type after publish"
    for item_type, keys in raw_keys.items():
        assert isinstance(keys, list), (
            f"published_dedup_keys_by_type[{item_type!r}] must be a list, "
            f"got {type(keys).__name__}"
        )
        for key in keys:
            # New format: [anchor, hint] tuple (json array)
            assert isinstance(key, (list, tuple)) and len(key) == 2, (
                f"published_dedup_key must be a 2-tuple, got {key!r}"
            )
            anchor, hint = key
            assert anchor, (
                f"published_dedup_key anchor must be non-empty, got {key!r}"
            )
            assert hint, (
                f"published_dedup_key hint must be non-empty, got {key!r}"
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


async def test_publisher_fail_closed_when_candidate_contents_none_but_candidates_exist(
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
    """P2-1 fail closed: candidate_contents=None + candidates → ValueError.

    Previously the publisher fell back to a legacy selector-sidecar
    ``output_json`` shape when ``candidate_contents`` was None, which
    violated the §8.3 layer contract (no schema_version, sidecar fields
    in output_json). P2-1 removed that escape hatch: when candidates
    exist, the publisher must raise ValueError instead of publishing a
    non-contract shape. Production callers must derive
    ``candidate_contents`` from the executor output (see
    ``pipeline_runner._derive_candidate_contents``).
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
    with pytest.raises(ValueError, match="candidate_contents is required"):
        await publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=candidates,
            candidate_contents=None,
        )


# ---------------------------------------------------------------------------
# T3.4a: window diagnostics (no_op_cause / counts / reasons)
# ---------------------------------------------------------------------------


def _make_invalid_anchor_candidates(
    target_unit_ids: list[str],
) -> list[CandidateItem]:
    """Build candidates whose anchor_segment_id is NOT in target_anchor_ids.

    The selector's INVALID_ANCHOR pre-filter (§7.2 step 2) rejects every
    one, so the window becomes no-op with cause=selector_rejected_all
    while raw_candidate_count > 0.
    """
    if not target_unit_ids:
        return []
    return [
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="nonexistent-anchor-1",
            spans=[{"unit_id": target_unit_ids[0]}],
            semantic_dedup_key="grammar-invalid-1",
            pattern_key="pattern-invalid-1",
            quality_score=4,
            reading_blocker=False,
            dedup_hint="grammar-invalid-hint-1",
        ),
        CandidateItem(
            item_type="sentence_analysis",
            anchor_segment_id="nonexistent-anchor-2",
            spans=[{"unit_id": target_unit_ids[0]}],
            semantic_dedup_key="sentence-invalid-1",
            pattern_key=None,
            quality_score=3,
            reading_blocker=False,
            dedup_hint="sentence-invalid-hint-1",
        ),
    ]


async def test_diagnostics_llm_empty_when_no_candidates(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4a: LLM returns 0 candidates → no_op_cause=llm_empty.

    raw_candidate_count_by_type totals to 0; window status='no_op';
    diagnostics readable from both output_ref_json and analysis_windows.coverage.
    """
    pool, job_id, lease_token, plan_id, window_id = (
        test_db_pool_with_window_no_candidates
    )
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
        job = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
        window = await conn.fetchrow(
            "SELECT status, coverage FROM analysis_windows WHERE id = $1",
            window_id,
        )

    output_ref = job["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    diag = output_ref["diagnostics"]

    assert diag["no_op_cause"] == "llm_empty"
    assert diag["raw_candidate_count_by_type"] == {
        "grammar_note": 0,
        "sentence_analysis": 0,
    }
    assert diag["accepted_count_by_type"] == {
        "grammar_note": 0,
        "sentence_analysis": 0,
    }
    assert diag["rejected_count_by_type"] == {
        "grammar_note": 0,
        "sentence_analysis": 0,
    }
    assert diag["rejected_breakdown"] == []
    # window_meta carries identifying fields
    assert diag["window_meta"]["window_id"] == str(window_id)
    assert diag["window_meta"]["plan_id"] == str(plan_id)
    assert diag["window_meta"]["target_anchor_count"] >= 1
    # strategy metadata present (worker wrote it; publisher trusts it)
    assert diag["strategy"]["reading_goal"] is not None
    assert diag["strategy"]["reading_variant"] is not None
    assert diag["strategy"]["strategy_hash"] is not None

    # window status reflects no-op
    assert window["status"] == "no_op"

    # coverage also carries diagnostics (queryable without job join)
    coverage = window["coverage"]
    if isinstance(coverage, str):
        coverage = json.loads(coverage)
    assert coverage["diagnostics"]["no_op_cause"] == "llm_empty"


async def test_diagnostics_selector_rejected_all_when_candidates_invalid(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4a: LLM returns candidates but selector rejects all.

    raw_candidate_count > 0 but accepted_count = 0 → no_op_cause=
    selector_rejected_all. rejected_breakdown records the gates + reasons.
    """
    pool, job_id, lease_token, plan_id, window_id = (
        test_db_pool_with_window_no_candidates
    )
    # Need target_unit_ids to construct candidates with a valid unit_id
    # but invalid anchor_segment_id (rejected by INVALID_ANCHOR gate).
    async with pool.acquire() as conn:
        window_row = await conn.fetchrow(
            "SELECT target_unit_ids FROM analysis_windows WHERE id = $1",
            window_id,
        )
    target_unit_ids = list(window_row["target_unit_ids"])
    candidates = _make_invalid_anchor_candidates(target_unit_ids)

    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        # candidate_contents is allowed to be None when no candidates are
        # accepted (publisher only fails-closed when accepted > 0).
        candidate_contents=None,
    )
    assert result.accepted_count == 0

    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )

    output_ref = job["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    diag = output_ref["diagnostics"]

    assert diag["no_op_cause"] == "selector_rejected_all"
    # raw candidates were present
    assert diag["raw_candidate_count_by_type"]["grammar_note"] == 1
    assert diag["raw_candidate_count_by_type"]["sentence_analysis"] == 1
    # all rejected
    assert diag["accepted_count_by_type"] == {
        "grammar_note": 0,
        "sentence_analysis": 0,
    }
    assert diag["rejected_count_by_type"]["grammar_note"] == 1
    assert diag["rejected_count_by_type"]["sentence_analysis"] == 1
    # rejected_breakdown captures gates + reasons
    assert len(diag["rejected_breakdown"]) >= 1
    gates_seen = {entry["gate"] for entry in diag["rejected_breakdown"]}
    assert "INVALID_ANCHOR" in gates_seen
    # each breakdown entry has count + reason
    for entry in diag["rejected_breakdown"]:
        assert entry["count"] >= 1
        assert isinstance(entry["reason"], str)
        assert entry["item_type"] in ("grammar_note", "sentence_analysis")


async def test_diagnostics_accepted_count_by_type_when_candidates_accepted(
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
    """T3.4a: accepted candidates → accepted_count_by_type correct.

    Window is marked completed (not no-op); no_op_cause is None;
    accepted_count_by_type matches the layers actually published.
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
    assert result.accepted_count > 0

    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )

    output_ref = job["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    diag = output_ref["diagnostics"]

    # successful publish → no_op_cause is None (not a no-op window)
    assert diag["no_op_cause"] is None
    # raw_candidate_count_by_type sums to the candidates we passed
    raw_total = sum(diag["raw_candidate_count_by_type"].values())
    assert raw_total == len(candidates)
    # accepted_count_by_type sums to accepted_count
    accepted_total = sum(diag["accepted_count_by_type"].values())
    assert accepted_total == result.accepted_count
    # at least one grammar_note accepted (the fixture builds grammar_note
    # candidates)
    assert diag["accepted_count_by_type"]["grammar_note"] >= 1
    # window_meta strategy fields present
    assert diag["strategy"]["reading_goal"] is not None
    assert diag["strategy"]["strategy_hash"] is not None


async def test_diagnostics_no_op_window_queryable_from_coverage(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4a: no-op window diagnostics readable from analysis_windows.coverage.

    Even without joining reader_jobs, a query against analysis_windows
    returns the no_op_cause / counts / strategy so operators can
    diagnose why a window produced no layers.
    """
    pool, job_id, lease_token, plan_id, window_id = (
        test_db_pool_with_window_no_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )

    async with pool.acquire() as conn:
        window = await conn.fetchrow(
            "SELECT status, coverage FROM analysis_windows WHERE id = $1",
            window_id,
        )

    assert window["status"] == "no_op"
    coverage = window["coverage"]
    if isinstance(coverage, str):
        coverage = json.loads(coverage)
    diag = coverage["diagnostics"]
    assert diag["no_op_cause"] == "llm_empty"
    assert diag["window_meta"]["window_id"] == str(window_id)
    assert diag["window_meta"]["plan_id"] == str(plan_id)
    # budgets snapshot present
    assert "window_budget" in diag["budgets"]
    assert "record_budget_used" in diag["budgets"]
    assert "record_budget_total" in diag["budgets"]
    # covered_unit_ids preserved (empty for no-op)
    assert coverage["covered_unit_ids"] == []


# ---------------------------------------------------------------------------
# T3.4b: RECORD_DENSITY density calculation fix tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_pool_with_long_article_window() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID, UUID, UUID]
]:
    """Long article (>1000 chars) with first window running + job claimed.

    Returns (pool, job_id, lease_token, plan_id, window_id, base_id, record_id).
    Used by density gate tests where density_denom must be > 1.0.
    """
    env = await _setup_test_env(
        article_text=LONG_ARTICLE_TEXT, title="Long Article Density Test"
    )
    try:
        async with env.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_windows SET status = 'running' WHERE id = $1",
                env.window_id,
            )
        lease_token = await _claim_job(env.pool, env.job_id)
        yield (
            env.pool,
            env.job_id,
            lease_token,
            env.plan_id,
            env.window_id,
            env.base_id,
            env.record_id,
        )
    finally:
        await _cleanup_test_env(env)


async def test_ledger_loads_base_text_length_from_reading_bases(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4b: _load_ledger_from_plan injects reading_bases.content_utf16_length
    into SelectorLedger.base_text_length_utf16.

    Before the fix, base_text_length_utf16 defaulted to 0, collapsing the
    RECORD_DENSITY denominator to 1.0 and turning the per-1000-chars ratio
    cap into a raw absolute count cap.
    """
    pool, job_id, _lease_token, plan_id, _window_id = (
        test_db_pool_with_window_no_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            plan_id,
        )
        base_id = await conn.fetchval(
            "SELECT base_id FROM reader_jobs WHERE id = $1",
            job_id,
        )
        ledger = await publisher._load_ledger_from_plan(
            conn, plan_row, base_id
        )
        expected_len = await conn.fetchval(
            "SELECT content_utf16_length FROM reading_bases WHERE id = $1",
            base_id,
        )
    assert ledger.base_text_length_utf16 > 0, (
        "base_text_length_utf16 must be loaded from reading_bases, not left at 0"
    )
    assert ledger.base_text_length_utf16 == expected_len


async def test_density_gate_uses_real_base_length_not_raw_count(
    test_db_pool_with_long_article_window: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4b: With a long base (>1000 chars) and 3 pre-published grammar_note,
    a 4th grammar_note candidate is NOT rejected by RECORD_DENSITY.

    Before the fix: density = 3 / max(0/1000, 1.0) = 3.0 >= cap 3.0 → rejected.
    After the fix:  density = 3 / max(base_len/1000, 1.0) < 3.0 → accepted.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        base_id,
        _record_id,
    ) = test_db_pool_with_long_article_window

    # Pre-set density_by_record to simulate 3 grammar_note already published.
    # This makes the next grammar_note candidate hit RECORD_DENSITY if the
    # denominator is 1.0 (bug) but pass if the denominator is base_len/1000 (fix).
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE layer_analysis_plans
            SET density_by_record = '{"grammar_note": 3, "sentence_analysis": 0}'::jsonb
            WHERE id = $1
            """,
            plan_id,
        )
        # Fetch target anchors for building valid candidates
        window_row = await conn.fetchrow(
            "SELECT target_unit_ids, target_anchor_ids FROM analysis_windows WHERE id = $1",
            window_id,
        )
        content_len = await conn.fetchval(
            "SELECT content_utf16_length FROM reading_bases WHERE id = $1",
            base_id,
        )

    target_unit_ids = list(window_row["target_unit_ids"])
    target_anchor_ids = list(window_row["target_anchor_ids"])
    assert content_len > 1000, (
        f"test requires base > 1000 chars for density_denom > 1.0; got {content_len}"
    )

    candidates = _make_candidates(target_unit_ids, target_anchor_ids)
    assert len(candidates) >= 1
    candidate_contents = await _make_candidate_contents(
        pool, base_id, candidates
    )

    publisher = GrammarWindowPublisher(pool=pool)
    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=candidates,
        candidate_contents=candidate_contents,
    )
    assert result.accepted_count > 0, (
        "grammar_note candidate must be accepted when density = 3 / (base_len/1000) < 3.0"
    )

    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
    output_ref = job["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    diag = output_ref["diagnostics"]
    # grammar_note accepted_count > 0 means RECORD_DENSITY did not reject
    assert diag["accepted_count_by_type"]["grammar_note"] >= 1
    # rejected_breakdown should NOT contain RECORD_DENSITY for grammar_note
    for entry in diag["rejected_breakdown"]:
        assert not (
            entry["gate"] == "RECORD_DENSITY"
            and entry["item_type"] == "grammar_note"
        ), (
            f"grammar_note must not be rejected by RECORD_DENSITY when "
            f"base_len={content_len} > 1000; got reason: {entry['reason']}"
        )


async def test_ledger_falls_back_to_char_length_when_content_utf16_length_missing(
    test_db_pool_with_window_no_candidates: tuple[
        asyncpg.Pool, UUID, UUID, UUID, UUID
    ],
) -> None:
    """T3.4b: When content_utf16_length is 0, fall back to char_length(text).

    The CHECK constraint guarantees content_utf16_length >= 1, but we test
    the defensive fallback by dropping the constraint and setting it to 0.
    """
    pool, job_id, _lease_token, plan_id, _window_id = (
        test_db_pool_with_window_no_candidates
    )
    publisher = GrammarWindowPublisher(pool=pool)
    async with pool.acquire() as conn:
        base_id = await conn.fetchval(
            "SELECT base_id FROM reader_jobs WHERE id = $1",
            job_id,
        )
        # Drop the CHECK constraints so we can set content_utf16_length = 0.
        # There are two: the table-level named constraint
        # (ck_reading_bases_content_utf16_length, checks = utf16_code_unit_length(text))
        # and the inline column CHECK (reading_bases_content_utf16_length_check,
        # checks >= 1).
        await conn.execute(
            "ALTER TABLE reading_bases "
            "DROP CONSTRAINT IF EXISTS ck_reading_bases_content_utf16_length"
        )
        await conn.execute(
            "ALTER TABLE reading_bases "
            "DROP CONSTRAINT IF EXISTS reading_bases_content_utf16_length_check"
        )
        await conn.execute(
            "UPDATE reading_bases SET content_utf16_length = 0 WHERE id = $1",
            base_id,
        )
        plan_row = await conn.fetchrow(
            "SELECT * FROM layer_analysis_plans WHERE id = $1",
            plan_id,
        )
        ledger = await publisher._load_ledger_from_plan(
            conn, plan_row, base_id
        )
        expected_fallback = await conn.fetchval(
            "SELECT char_length(text) FROM reading_bases WHERE id = $1",
            base_id,
        )
    assert ledger.base_text_length_utf16 > 0, (
        "fallback to char_length(text) must produce a non-zero base length"
    )
    assert ledger.base_text_length_utf16 == expected_fallback


async def test_diagnostics_rejected_reason_base_len_not_zero(
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
    """T3.4b: When RECORD_DENSITY triggers, the rejected reason's base_len
    reflects the real base length, not 0.

    Uses the short ARTICLE_TEXT fixture (~450 chars, < 1000). With
    density_by_record[grammar_note] pre-set to 3, the density denominator is
    max(450/1000, 1.0) = 1.0, so density = 3/1.0 = 3.0 >= cap 3.0 → rejected.
    But the reason must show base_len=450 (or similar), NOT base_len=0.
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        candidates,
        candidate_contents,
        base_id,
        _record_id,
    ) = test_db_pool_with_window_and_candidates

    # Pre-set density_by_record to 3 so grammar_note candidates hit cap.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE layer_analysis_plans
            SET density_by_record = '{"grammar_note": 3, "sentence_analysis": 0}'::jsonb
            WHERE id = $1
            """,
            plan_id,
        )
        content_len = await conn.fetchval(
            "SELECT content_utf16_length FROM reading_bases WHERE id = $1",
            base_id,
        )
    assert content_len > 0

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
        job = await conn.fetchrow(
            "SELECT output_ref_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
    output_ref = job["output_ref_json"]
    if isinstance(output_ref, str):
        output_ref = json.loads(output_ref)
    diag = output_ref["diagnostics"]

    # grammar_note candidates must be rejected by RECORD_DENSITY
    assert diag["rejected_count_by_type"]["grammar_note"] >= 1
    density_rejections = [
        e
        for e in diag["rejected_breakdown"]
        if e["gate"] == "RECORD_DENSITY" and e["item_type"] == "grammar_note"
    ]
    assert len(density_rejections) >= 1
    for entry in density_rejections:
        reason = entry["reason"]
        # base_len must be the real content_utf16_length, NOT 0
        assert "base_len=0)" not in reason, (
            f"rejected reason must not contain base_len=0; got: {reason}"
        )
        assert f"base_len={content_len}" in reason, (
            f"rejected reason must contain base_len={content_len}; got: {reason}"
        )


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: publisher ledger contract
# ---------------------------------------------------------------------------


def test_update_ledger_never_writes_empty_dedup_key() -> None:
    """reader-grammar-candidate-selection: ``_update_ledger`` must always
    write a scoped dedup key ``(anchor_segment_id, normalized_hint)`` for
    each accepted candidate. The old implementation silently skipped
    empty hints; the new contract relies on
    ``CandidateItem.__post_init__`` to enforce non-empty normalized hints
    and ``scoped_dedup_key`` fail-closed, so no skip guard is needed.
    """
    publisher = GrammarWindowPublisher(pool=None)
    ledger = SelectorLedger()
    candidate = CandidateItem(
        item_type="grammar_note",
        anchor_segment_id="anchor-1",
        spans=[{"unit_id": "u1"}],
        semantic_dedup_key="k1",
        pattern_key=None,
        quality_score=3,
        reading_blocker=False,
        dedup_hint="  Though   Concession  ",
    )
    result = publisher._update_ledger(ledger, [candidate])
    keys = result["published_dedup_keys_by_type"]["grammar_note"]
    assert len(keys) == 1
    anchor, hint = keys[0]
    assert anchor == "anchor-1"
    assert hint == "though concession"
    assert hint, "publisher must never write an empty dedup hint"


async def test_load_ledger_fails_closed_on_legacy_string_dedup_key() -> None:
    """reader-grammar-candidate-selection: ``_load_ledger_from_plan`` must
    reject legacy string dedup keys (old shape: bare string). The old
    implementation converted string → ("", hint) and silently skipped
    unknown shapes; the new contract is fail-closed — malformed entries
    raise ValueError, no alias / backfill / data migration.
    """
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = {
        "budget_used": None,
        "budget_total": None,
        "published_anchor_counts_by_type": None,
        "published_dedup_keys_by_type": {
            "grammar_note": ["though_concession"],
            "sentence_analysis": [],
        },
        "published_pattern_keys_by_type": None,
        "density_by_record": None,
    }
    with pytest.raises(ValueError, match="malformed published_dedup_keys"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_fails_closed_on_unknown_dedup_key_shape() -> None:
    """reader-grammar-candidate-selection: ``_load_ledger_from_plan`` must
    reject unknown dedup key shapes (e.g. integer). Fail-closed, no
    silent skip.
    """
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = {
        "budget_used": None,
        "budget_total": None,
        "published_anchor_counts_by_type": None,
        "published_dedup_keys_by_type": {
            "grammar_note": [123],
            "sentence_analysis": [],
        },
        "published_pattern_keys_by_type": None,
        "density_by_record": None,
    }
    with pytest.raises(ValueError, match="malformed published_dedup_keys"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_fails_closed_on_wrong_length_tuple() -> None:
    """reader-grammar-candidate-selection: ``_load_ledger_from_plan`` must
    reject tuples of wrong length (not a 2-tuple). Fail-closed.
    """
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = {
        "budget_used": None,
        "budget_total": None,
        "published_anchor_counts_by_type": None,
        "published_dedup_keys_by_type": {
            "grammar_note": [["anchor-1", "hint-1", "extra"]],
            "sentence_analysis": [],
        },
        "published_pattern_keys_by_type": None,
        "density_by_record": None,
    }
    with pytest.raises(ValueError, match="malformed published_dedup_keys"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: structured reason_code in breakdown
# ---------------------------------------------------------------------------


def _make_rejected(
    *,
    item_type: str,
    gate: SelectionGate,
    reason: str,
    reason_code: str | None,
    dedup_metadata: DedupRejectionMetadata | None = None,
) -> RejectedCandidate:
    """Build a RejectedCandidate for ``_aggregate_rejected`` unit tests."""
    candidate = CandidateItem(
        item_type=item_type,
        anchor_segment_id="a1",
        spans=[{"unit_id": "u1"}],
        semantic_dedup_key="k1",
        pattern_key=None,
        quality_score=3,
        reading_blocker=False,
        dedup_hint="though concession",
    )
    return RejectedCandidate(
        candidate=candidate,
        gate=gate,
        reason=reason,
        reason_code=reason_code,
        dedup_metadata=dedup_metadata,
    )


def test_aggregate_rejected_outputs_reason_code_for_dup() -> None:
    """reader-grammar-candidate-selection: ``_aggregate_rejected`` MUST
    output independent ``reason_code`` field in each breakdown entry.
    DUP rejection MUST carry ``reason_code = "dedup_hint_duplicate"``;
    non-DUP rejection MUST carry ``reason_code = None``."""
    publisher = GrammarWindowPublisher(pool=None)
    dup_rejection = _make_rejected(
        item_type="grammar_note",
        gate=SelectionGate.DUP,
        reason="a1/though concession already accepted in current window",
        reason_code=DEDUP_HINT_DUPLICATE_REASON_CODE,
        dedup_metadata=DedupRejectionMetadata(
            normalized_hint="though concession",
            winner_item_type="grammar_note",
            winner_anchor_segment_id="a1",
            winner_item_index=0,
            winner_source=DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
        ),
    )
    density_rejection = _make_rejected(
        item_type="grammar_note",
        gate=SelectionGate.RECORD_DENSITY,
        reason="record grammar_note density 3.0000 >= cap 3.0 (base_len=450)",
        reason_code=None,
    )
    breakdown = publisher._aggregate_rejected([dup_rejection, density_rejection])
    assert len(breakdown) == 2
    dup_entry = next(e for e in breakdown if e["gate"] == "DUP")
    density_entry = next(e for e in breakdown if e["gate"] == "RECORD_DENSITY")
    # DUP MUST carry the structured reason_code
    assert dup_entry["reason_code"] == "dedup_hint_duplicate"
    # Non-DUP MUST carry None
    assert density_entry["reason_code"] is None
    # reason must NOT contain the code (human-readable only)
    assert "dedup_hint_duplicate" not in dup_entry["reason"]


def test_aggregate_rejected_invalid_anchor_has_null_reason_code() -> None:
    """reader-grammar-candidate-selection: INVALID_ANCHOR (pre-filter in
    ``select_candidates``) constructs ``RejectedCandidate`` without
    ``reason_code``; the breakdown MUST output ``reason_code: None``."""
    publisher = GrammarWindowPublisher(pool=None)
    rejection = _make_rejected(
        item_type="grammar_note",
        gate=SelectionGate.INVALID_ANCHOR,
        reason="anchor_segment_id bogus not in target_anchor_ids",
        reason_code=None,
    )
    breakdown = publisher._aggregate_rejected([rejection])
    assert len(breakdown) == 1
    assert breakdown[0]["reason_code"] is None


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: strict ledger canonical-content
# ---------------------------------------------------------------------------


def _make_plan_row(dedup_keys: dict[str, list[Any]]) -> dict[str, Any]:
    """Build a minimal plan_row for ``_load_ledger_from_plan`` tests."""
    return {
        "budget_used": None,
        "budget_total": None,
        "published_anchor_counts_by_type": None,
        "published_dedup_keys_by_type": dedup_keys,
        "published_pattern_keys_by_type": None,
        "density_by_record": None,
    }


async def test_load_ledger_rejects_empty_anchor() -> None:
    """Strict ledger: empty anchor_segment_id MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [["", "though concession"]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="anchor_segment_id must be a non-empty"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_whitespace_only_anchor() -> None:
    """Strict ledger: whitespace-only anchor MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [["   ", "though concession"]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="anchor_segment_id must be a non-empty"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_empty_hint() -> None:
    """Strict ledger: empty hint MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [["a1", ""]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="dedup_hint must be non-empty"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_whitespace_hint() -> None:
    """Strict ledger: whitespace-only hint MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [["a1", "   \t  "]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="dedup_hint must be non-empty"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_unnormalized_hint() -> None:
    """Strict ledger: hint that is not already normalized MUST fail-closed.

    ``"  Though   Concession  "`` normalizes to ``"though concession"``;
    since the stored value differs, it MUST be rejected (no silent fix-up).
    """
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {
            "grammar_note": [["a1", "  Though   Concession  "]],
            "sentence_analysis": [],
        }
    )
    with pytest.raises(ValueError, match="stored hint must already be normalized"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_non_string_hint() -> None:
    """Strict ledger: non-string hint (integer) MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [["a1", 123]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="normalized_hint must be a string"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_non_string_anchor() -> None:
    """Strict ledger: non-string anchor (integer) MUST fail-closed."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {"grammar_note": [[123, "though concession"]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="anchor_segment_id must be a non-empty"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_rejects_overlong_hint() -> None:
    """Strict ledger: hint > MAX_DEDUP_HINT_LENGTH MUST fail-closed."""
    from app.services.reader_orchestration.grammar_candidate_policy import (
        MAX_DEDUP_HINT_LENGTH,
    )

    publisher = GrammarWindowPublisher(pool=None)
    overlong = "a" * (MAX_DEDUP_HINT_LENGTH + 1)
    plan_row = _make_plan_row(
        {"grammar_note": [["a1", overlong]], "sentence_analysis": []}
    )
    with pytest.raises(ValueError, match="exceeds"):
        await publisher._load_ledger_from_plan(None, plan_row, None)


async def test_load_ledger_accepts_valid_canonical_scoped_key() -> None:
    """Strict ledger: a valid ``[anchor, normalized_hint]`` 2-tuple loads
    successfully and is stored as a tuple."""
    publisher = GrammarWindowPublisher(pool=None)
    plan_row = _make_plan_row(
        {
            "grammar_note": [["anchor-1", "though concession"]],
            "sentence_analysis": [["anchor-2", "nominal subject"]],
        }
    )
    ledger = await publisher._load_ledger_from_plan(None, plan_row, None)
    gn_keys = ledger.published_dedup_keys_by_type["grammar_note"]
    sa_keys = ledger.published_dedup_keys_by_type["sentence_analysis"]
    assert gn_keys == [("anchor-1", "though concession")]
    assert sa_keys == [("anchor-2", "nominal subject")]
