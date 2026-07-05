import pytest
from app.services.reader_orchestration.window_selector import (
    SelectorLedger,
    CandidateItem,
    RejectedCandidate,
    SelectionResult,
    SelectionGate,
    select_candidates,
    PER_ANCHOR_CAP,
    PATTERN_DENSE_THRESHOLD,
    ANCHOR_RATIO_THRESHOLD,
)


def make_candidate(
    *,
    item_type: str = "grammar_note",
    anchor_segment_id: str = "a1",
    semantic_dedup_key: str = "key1",
    pattern_key: str | None = "pattern1",
    spans: list[dict] | None = None,
    quality_score: float = 0.5,
    reading_blocker: bool = False,
) -> CandidateItem:
    if spans is None:
        spans = [{"unit_id": "u1"}]
    return CandidateItem(
        item_type=item_type,
        anchor_segment_id=anchor_segment_id,
        spans=spans,
        semantic_dedup_key=semantic_dedup_key,
        pattern_key=pattern_key,
        quality_score=quality_score,
        reading_blocker=reading_blocker,
    )


def test_gate_dup_rejects_existing_semantic_dedup_key():
    """gate 1: semantic_dedup_key 已在 ledger"""
    ledger = SelectorLedger(
        published_dedup_keys_by_type={
            "grammar_note": ["grammar_note:though_concession:adverbial_clause"],
            "sentence_analysis": [],
        },
    )
    candidate = make_candidate(
        semantic_dedup_key="grammar_note:though_concession:adverbial_clause",
    )
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.DUP
    assert len(result.accepted) == 0


def test_gate_pattern_dense_rejects_after_3_occurrences():
    """gate 2: pattern_key 出现 3 次后拒绝（仅 grammar_note）"""
    ledger = SelectorLedger(
        published_pattern_keys_by_type={
            "grammar_note": ["though_concession", "though_concession", "though_concession"],
            "sentence_analysis": [],
        },
    )
    candidate = make_candidate(pattern_key="though_concession")
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.PATTERN_DENSE


def test_gate_pattern_dense_not_applied_to_sentence_analysis():
    """gate 2 仅对 grammar_note 生效，sentence_analysis 不受限"""
    ledger = SelectorLedger(
        published_pattern_keys_by_type={
            "grammar_note": [],
            "sentence_analysis": ["pattern1", "pattern1", "pattern1"],
        },
    )
    candidate = make_candidate(item_type="sentence_analysis", pattern_key="pattern1")
    result = select_candidates([candidate], ledger=ledger, window_budget={"sentence_analysis": 2})
    assert len(result.accepted) == 1


def test_gate_anchor_cap_rejects_per_anchor_exceed():
    """gate 3: anchor 已达 cap (1)"""
    ledger = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1},
            "sentence_analysis": {},
        },
    )
    candidate = make_candidate(anchor_segment_id="a1", semantic_dedup_key="new_key")
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.ANCHOR_CAP


def test_gate_window_cap_rejects_when_window_budget_exhausted():
    """gate 4: 当前 window 已发布 count >= window_budget"""
    ledger = SelectorLedger()
    # 第一个 candidate 通过
    c1 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1")
    c2 = make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2")
    c3 = make_candidate(anchor_segment_id="a3", semantic_dedup_key="k3")
    result = select_candidates([c1, c2, c3], ledger=ledger, window_budget={"grammar_note": 2})
    # 应接受 2 个，拒绝 1 个（WINDOW_CAP）
    assert len(result.accepted) == 2
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.WINDOW_CAP


def test_gate_record_density_rejects_when_cap_reached():
    """gate 5: density_by_record >= density_cap"""
    ledger = SelectorLedger(
        density_by_record={"grammar_note": 3, "sentence_analysis": 0},
        density_cap={"grammar_note": 3, "sentence_analysis": 1},
    )
    candidate = make_candidate()
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.RECORD_DENSITY


def test_gate_record_budget_rejects_when_total_exhausted():
    """gate 6: budget_used >= budget_total"""
    ledger = SelectorLedger(
        budget_used={"grammar_note": {"count": 14}, "sentence_analysis": {"count": 0}},
        budget_total={"grammar_note": {"count": 14}, "sentence_analysis": {"count": 5}},
    )
    candidate = make_candidate()
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.RECORD_BUDGET


def test_gate_anchor_ratio_rejects_when_threshold_exceeded():
    """gate 7: annotated ratio > 0.30"""
    ledger = SelectorLedger(
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3"},  # 3/10 = 0.30，加一个会到 0.40 > 0.30
    )
    candidate = make_candidate(anchor_segment_id="a4", semantic_dedup_key="k4")
    result = select_candidates([candidate], ledger=ledger, window_budget={"grammar_note": 2})
    # 0.30 不 > 0.30，应该通过；再加一个 a5 才 > 0.30
    # 但实际上 3/10 = 0.30, +1 = 4/10 = 0.40 > 0.30
    assert len(result.accepted) == 1  # a4 通过

    # 再加 a5 应被拒绝
    ledger2 = SelectorLedger(
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3", "a4"},  # 4/10 = 0.40
    )
    candidate2 = make_candidate(anchor_segment_id="a5", semantic_dedup_key="k5")
    result2 = select_candidates([candidate2], ledger=ledger2, window_budget={"grammar_note": 2})
    assert len(result2.rejected) == 1
    assert result2.rejected[0].gate == SelectionGate.ANCHOR_RATIO


def test_gate_multi_unit_span_rejects_cross_unit():
    """gate 8: candidate spans 跨 unit"""
    candidate = make_candidate(
        spans=[{"unit_id": "u1"}, {"unit_id": "u2"}],
    )
    result = select_candidates([candidate], ledger=SelectorLedger(), window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.MULTI_UNIT_SPAN


def test_typed_counters_isolate_grammar_from_sentence():
    """grammar_note 和 sentence_analysis 的 counter 互不影响"""
    ledger = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1},
            "sentence_analysis": {},
        },
    )
    sentence_candidate = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a1",
        semantic_dedup_key="sentence_key",
    )
    result = select_candidates(
        [sentence_candidate], ledger=ledger, window_budget={"sentence_analysis": 1}
    )
    assert len(result.accepted) == 1  # sentence 不受 grammar anchor cap 影响


def test_sort_by_quality_score_descending():
    """高 quality_score 优先"""
    candidates = [
        make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=0.3),
        make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=0.9),
        make_candidate(anchor_segment_id="a3", semantic_dedup_key="k3", quality_score=0.6),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(), window_budget={"grammar_note": 3})
    # 应接受全部 3 个，按 quality_score 降序
    assert len(result.accepted) == 3
    assert result.accepted[0].anchor_segment_id == "a2"  # 0.9
    assert result.accepted[1].anchor_segment_id == "a3"  # 0.6
    assert result.accepted[2].anchor_segment_id == "a1"  # 0.3


def test_reading_blocker_prioritized():
    """reading_blocker=True 优先"""
    candidates = [
        make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=0.5, reading_blocker=False),
        make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=0.5, reading_blocker=True),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(), window_budget={"grammar_note": 2})
    assert result.accepted[0].anchor_segment_id == "a2"  # blocker 优先


def test_sentence_analysis_prioritized_over_grammar_note():
    """同 quality 时 sentence_analysis > grammar_note"""
    candidates = [
        make_candidate(item_type="grammar_note", anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=0.5),
        make_candidate(item_type="sentence_analysis", anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=0.5),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(),
                                window_budget={"grammar_note": 2, "sentence_analysis": 2})
    assert result.accepted[0].item_type == "sentence_analysis"


def test_empty_candidates_returns_empty_result():
    """空输入返回空 result"""
    result = select_candidates([], ledger=SelectorLedger(), window_budget={})
    assert len(result.accepted) == 0
    assert len(result.rejected) == 0


def test_constants():
    """验证阈值常量"""
    assert PER_ANCHOR_CAP == 1
    assert PATTERN_DENSE_THRESHOLD == 3
    assert ANCHOR_RATIO_THRESHOLD == 0.30
