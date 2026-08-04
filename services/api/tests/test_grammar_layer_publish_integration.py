"""T4.2a-PUX-R4-R2.2-P2b-R1: grammar_note publish path integration tests.

聚焦 grammar_note 首发路径的三个集成场景：

1. validator 失败时同事务回滚（layer INSERT / event INSERT / sequence 增量全部回滚）。
2. 空 grammar output 保持现有 no-op 路径（不发布 layer/event）。
3. payload 脱敏：扩展 payload 不含 note / selected_text / grammar_point /
   pattern / spans / text 等 forbidden key。

参考 spec: ``.trae/specs/t42a-pux-r4-r2-2-p2b-r1-grammar-layer-payload/spec.md``
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
    GrammarNoteItem,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
)
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.grammar_layer_payload import (
    build_grammar_layer_published_payload,
)
from app.services.reader_orchestration.grammar_window_publisher import (
    GrammarWindowPublisher,
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
MIGRATION_0015_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql

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

# 纯单元测试用短文本（与 builder 测试一致）。
_PLAIN_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff."
)


# ---------------------------------------------------------------------------
# DB 测试环境搭建（复用 test_grammar_window_publisher.py 的模式）
# ---------------------------------------------------------------------------


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
    schema_name = f"test_grammar_layer_pub_{uuid4().hex}"
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
        title="Grammar Layer Pub Integration",
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
    """构造 grammar_note + sentence_analysis 候选，覆盖前两个 unit。"""
    if not target_unit_ids or not target_anchor_ids:
        return []
    candidates: list[CandidateItem] = []
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
    """从 DB segment 数据构造合法 ReaderTextRangeAnchor。"""
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
    """为每个 candidate 构造 WindowCandidateContent（按 dedup_key 匹配）。"""
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
async def env_with_candidates() -> AsyncIterator[
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
    """Window status='running'，job claimed，附带 grammar_note + sentence_analysis 候选。"""
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
async def env_no_candidates() -> AsyncIterator[
    tuple[asyncpg.Pool, UUID, UUID, UUID, UUID, UUID]
]:
    """Window status='running'，job claimed，无候选（empty grammar output 路径）。"""
    env = await _setup_test_env()
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
            env.record_id,
        )
    finally:
        await _cleanup_test_env(env)


# ---------------------------------------------------------------------------
# Task 5 #1: validator 失败时同事务回滚
# ---------------------------------------------------------------------------


async def test_validator_failure_rolls_back_layer_event_and_sequence(
    env_with_candidates: tuple[
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validator 校验失败 → layer INSERT / event INSERT / sequence 增量全部回滚。

    通过 mock ``grammar_window_publisher`` 模块内的 validator 使其抛出
    ValueError，模拟 payload 校验失败。校验发生在 ``_insert_layer`` 内、
    ``publish_event_in_transaction`` 之前，因此：
    - enhancement_layers 的 INSERT 已执行但事务回滚 → 无行残留。
    - reader_events 从未写入。
    - reader_event_sequences 从未推进（publish_event_in_transaction 未被调用）。
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
    ) = env_with_candidates

    # mock validator：对 grammar_note payload 强制抛出 ValueError。
    def _failing_validate(payload: dict[str, object]) -> None:
        raise ValueError("forced validator failure for rollback test")

    monkeypatch.setattr(
        "app.services.reader_orchestration.grammar_window_publisher."
        "validate_grammar_layer_published_payload",
        _failing_validate,
    )

    event_runtime = ReaderEventRuntime(pool=pool)
    publisher = GrammarWindowPublisher(pool=pool, event_runtime=event_runtime)

    # 发布前：捕获 reader_event_sequences 当前 next_sequence（article 提交时
    # 可能已写入初始事件，因此 sequence 行可能已存在）。validator 失败后
    # 事务回滚，next_sequence 不应推进。
    async with pool.acquire() as conn:
        pre_seq_row = await conn.fetchrow(
            """
            SELECT next_sequence FROM reader_event_sequences
            WHERE reading_record_id = $1
            """,
            record_id,
        )
    pre_next_sequence = (
        int(pre_seq_row["next_sequence"]) if pre_seq_row is not None else None
    )

    # 发布应抛出 ValueError（validator 失败），事务回滚。
    with pytest.raises(ValueError, match="forced validator failure"):
        await publisher.publish_window_grammar_bundle(
            job_id=job_id,
            lease_token=lease_token,
            plan_id=plan_id,
            window_id=window_id,
            candidates=candidates,
            candidate_contents=candidate_contents,
        )

    async with pool.acquire() as conn:
        # 1. 无 enhancement_layers 行（INSERT 已回滚）。
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            record_id,
        )
        assert layer_count == 0, (
            f"expected 0 enhancement_layers rows after rollback, got {layer_count}"
        )

        # 2. 无 layer_published reader_event（validator 在
        #    publish_event_in_transaction 之前失败，layer_published 事件从未写入；
        #    即便 enhancement_layers INSERT 已执行也被事务回滚）。
        layer_published_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
            """,
            record_id,
        )
        assert layer_published_count == 0, (
            f"expected 0 layer_published events after rollback, "
            f"got {layer_published_count}"
        )

        # 3. reader_event_sequences 未推进：next_sequence 与发布前一致。
        #    validator 在 publish_event_in_transaction 之前失败，所以
        #    reader_event_sequences 的增量 UPDATE 从未执行 → next_sequence 不变。
        post_seq_row = await conn.fetchrow(
            """
            SELECT next_sequence FROM reader_event_sequences
            WHERE reading_record_id = $1
            """,
            record_id,
        )
        post_next_sequence = (
            int(post_seq_row["next_sequence"]) if post_seq_row is not None else None
        )
        assert post_next_sequence == pre_next_sequence, (
            f"reader_event_sequences.next_sequence advanced despite rollback: "
            f"pre={pre_next_sequence}, post={post_next_sequence}"
        )


# ---------------------------------------------------------------------------
# Task 5 #2: 空 grammar output 保持现有 no-op 路径
# ---------------------------------------------------------------------------


async def test_empty_grammar_output_follows_existing_noop_path(
    env_no_candidates: tuple[asyncpg.Pool, UUID, UUID, UUID, UUID, UUID],
) -> None:
    """空 grammar output → 既有 no-op 路径（不发布 grammar_note layer/event）。

    window publisher 的空 output 处理在 caller 层：当
    ``accepted_by_unit[unit_id][GRAMMAR_NOTE_LAYER_TYPE]`` 为空时，
    ``_insert_layer`` 不被调用（grammar_window_publisher.py L370 的
    ``if items[GRAMMAR_NOTE_LAYER_TYPE]:`` 守卫）。因此：
    - 无 grammar_note enhancement_layers 行。
    - 无 layer_published reader_event。
    - window 状态为 no_op。
    """
    (
        pool,
        job_id,
        lease_token,
        plan_id,
        window_id,
        record_id,
    ) = env_no_candidates

    event_runtime = ReaderEventRuntime(pool=pool)
    publisher = GrammarWindowPublisher(pool=pool, event_runtime=event_runtime)

    result = await publisher.publish_window_grammar_bundle(
        job_id=job_id,
        lease_token=lease_token,
        plan_id=plan_id,
        window_id=window_id,
        candidates=[],
    )

    # accepted_count == 0 → no-op window。
    assert result.accepted_count == 0
    assert result.grammar_note_layer_ids == ()
    assert result.sentence_analysis_layer_ids == ()

    async with pool.acquire() as conn:
        # 无 enhancement_layers 行。
        layer_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1
            """,
            record_id,
        )
        assert layer_count == 0

        # 无 layer_published reader_event（_insert_layer 从未被调用）。
        # 注意：article 提交 / job 状态转移可能写入其他 event_type，
        # 这里只校验 grammar_note 首发应产生的 layer_published 事件。
        layer_published_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
            """,
            record_id,
        )
        assert layer_published_count == 0

        # window 状态为 no_op。
        window = await conn.fetchrow(
            "SELECT status FROM analysis_windows WHERE id = $1",
            window_id,
        )
        assert window is not None
        assert window["status"] == "no_op"


# ---------------------------------------------------------------------------
# Task 5 #3: payload 脱敏测试（纯单元测试）
# ---------------------------------------------------------------------------


def _build_unit_result():
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-sanitize",
            base_id="base-sanitize",
            source_text=_PLAIN_TEXT,
            title="Sanitize Test",
            language="en",
        )
    )


def _make_anchor(segment, selected_text: str, base_id: str) -> ReaderTextRangeAnchor:
    segment_text = segment.text
    selected_start = segment_text.index(selected_text)
    computed_start = segment.unit_start_utf16 + _utf16_len(segment_text[:selected_start])
    computed_end = computed_start + _utf16_len(selected_text)
    return ReaderTextRangeAnchor(
        base_id=base_id,
        unit_id=segment.unit_id,
        anchor_segment_id=segment.anchor_segment_id,
        sentence_id=segment.sentence_id,
        segment_type=segment.segment_type,  # type: ignore[arg-type]
        start_offset=computed_start,
        end_offset=computed_end,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


def _utf16_len(s: str) -> int:
    # 复用 contracts.annotation 的 utf16 code unit 长度计算。
    from app.contracts.annotation import utf16_code_unit_length

    return utf16_code_unit_length(s)


def _make_grammar_item(segment, *, base_id: str, grammar_point: str) -> GrammarNoteItem:
    selected_text = segment.text.split()[0]
    anchor = _make_anchor(segment, selected_text, base_id)
    return GrammarNoteItem(
        spans=[anchor],
        grammar_point=grammar_point,
        pattern=f"pattern for {grammar_point}",
        note=f"这是 {grammar_point} 的 note 正文，不应出现在 payload 中。",
    )


def test_payload_does_not_leak_sensitive_content() -> None:
    """扩展 payload 不含 note / selected_text / grammar_point / pattern /
    spans / text 等 forbidden key。

    构造一个 typed GrammarNoteLayerOutput，其 items 包含所有上述敏感字段，
    通过 builder 构造扩展 payload，验证 payload 顶层与 insertions[] 内均
    不含这些 forbidden key（只含稳定 identity）。
    """
    result = _build_unit_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    # 构造 3 个 item，跨 2 个 anchor，每个 item 都带 note/grammar_point/pattern/spans。
    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="pointA"),
        _make_grammar_item(seg_1, base_id=result.base.base_id, grammar_point="pointB"),
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="pointC"),
    ]
    layer_id = "layer_grammar_sanitize"
    typed_output = {
        "schema_version": 1,
        "items": [item.model_dump(mode="json") for item in items],
    }
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

    base_payload = {
        "record_id": "rec_sanitize",
        "base_id": result.base.base_id,
        "layer_id": layer_id,
        "layer_type": "grammar_note",
        "target_scope": "unit",
        "target_key": unit.unit_id,
        "generation": 1,
    }

    payload = build_grammar_layer_published_payload(
        base_payload=base_payload,
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )

    # builder 产出了扩展字段（非 no-op）。
    assert payload["operation"] == "insert_after_anchor"
    assert isinstance(payload["insertions"], list)
    assert len(payload["insertions"]) == 2

    # forbidden key 集合：note / selected_text / grammar_point / pattern /
    # spans / text（以及 content / body / raw_output 等同类敏感字段）。
    forbidden_keys = {
        "note",
        "selected_text",
        "grammar_point",
        "pattern",
        "spans",
        "text",
        "content",
        "body",
        "raw_output",
        "output_json",
    }

    # 顶层 payload 不含 forbidden key。
    top_level_keys = set(payload.keys())
    assert not (top_level_keys & forbidden_keys), (
        f"forbidden keys leaked into top-level payload: "
        f"{top_level_keys & forbidden_keys}"
    )

    # insertions[] 的每个 descriptor 不含 forbidden key。
    for idx, desc in enumerate(payload["insertions"]):
        desc_keys = set(desc.keys())
        assert not (desc_keys & forbidden_keys), (
            f"forbidden keys leaked into insertions[{idx}]: "
            f"{desc_keys & forbidden_keys}"
        )
        # descriptor 只含稳定 identity 字段。
        assert desc_keys == {
            "unit_id",
            "anchor_segment_id",
            "kind",
            "layer_id",
            "item_ids",
        }

    # 递归遍历 payload 的所有 key（含嵌套 dict/list），确保无 forbidden key。
    def _collect_all_keys(value, acc: set[str]) -> None:
        if isinstance(value, dict):
            acc.update(value.keys())
            for v in value.values():
                _collect_all_keys(v, acc)
        elif isinstance(value, list):
            for v in value:
                _collect_all_keys(v, acc)

    all_keys: set[str] = set()
    _collect_all_keys(payload, all_keys)
    leaked = all_keys & forbidden_keys
    assert not leaked, f"forbidden keys leaked anywhere in payload: {leaked}"

    # 验证 payload 不含原始 note / selected_text 的字符串值。
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    for item in items:
        assert item.note not in serialized, (
            f"raw note text leaked into serialized payload: {item.note!r}"
        )
        assert item.spans[0].selected_text not in serialized, (
            f"raw selected_text leaked into serialized payload: "
            f"{item.spans[0].selected_text!r}"
        )
        assert item.grammar_point not in serialized, (
            f"raw grammar_point leaked into serialized payload: "
            f"{item.grammar_point!r}"
        )
