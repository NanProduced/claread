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
    """gate 5: per-1000-chars density >= density_cap（P2-6 新语义）

    base_text_length_utf16=1000，density_denom=1.0
    ledger 已发布 3 个 grammar_note，density=3/1.0=3.0，>= cap 3.0 → reject
    """
    ledger = SelectorLedger(
        density_by_record={"grammar_note": 3, "sentence_analysis": 0},
        density_cap={"grammar_note": 3.0, "sentence_analysis": 1.0},
        base_text_length_utf16=1000,
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


# ---------------------------------------------------------------------------
# P1-5: 同 window 内 dedup / anchor / budget 累计 gate 检查
# ---------------------------------------------------------------------------


def test_selector_rejects_duplicate_semantic_key_within_same_window():
    """P1-5: 同 window 内两个 candidate 共用 semantic_dedup_key，第二个必须被 DUP 拒绝。

    旧实现只读 ledger，未累计 window 内已接受的 dedup key，所以会同时接受两个
    重复 key 的 candidate。修复后 window_round 累计 dedup key 到 DUP gate。
    """
    ledger = SelectorLedger()  # ledger 中无任何 dedup key
    c1 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="shared_key")
    c2 = make_candidate(anchor_segment_id="a2", semantic_dedup_key="shared_key")
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].anchor_segment_id == "a1"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.DUP
    assert result.rejected[0].candidate.anchor_segment_id == "a2"


def test_selector_rejects_second_item_for_same_anchor_within_same_window():
    """P1-5: 同 window 内两个 candidate 落在相同 anchor + 相同 item_type，
    第二个必须被 ANCHOR_CAP 拒绝（per_anchor_cap=1）。

    旧实现只读 ledger.published_anchor_counts，未累计 window 内已接受的同 anchor
    item，所以会接受两个同 anchor 的 item。修复后 window_round 累计 anchor count。
    """
    ledger = SelectorLedger()  # ledger 中 anchor_counts 为空
    c1 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1")
    c2 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="k2")
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].semantic_dedup_key == "k1"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.ANCHOR_CAP
    assert result.rejected[0].candidate.semantic_dedup_key == "k2"


def test_selector_rejects_when_window_round_exceeds_record_budget():
    """P1-5: ledger budget_used 接近 total，window 内多个 candidate 累计后超 total，
    第一个通过，第二个必须被 RECORD_BUDGET 拒绝。

    旧实现只读 ledger.budget_used，window 内已接受的 candidate 不会累计到 budget
    检查上，导致 window 接受数超过 record budget。修复后 window_round.budget_used
    叠加在 ledger.budget_used 之上。
    """
    ledger = SelectorLedger(
        budget_used={"grammar_note": {"count": 13}, "sentence_analysis": {"count": 0}},
        budget_total={"grammar_note": {"count": 14}, "sentence_analysis": {"count": 5}},
    )
    c1 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1")
    c2 = make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2")
    c3 = make_candidate(anchor_segment_id="a3", semantic_dedup_key="k3")
    result = select_candidates(
        [c1, c2, c3], ledger=ledger, window_budget={"grammar_note": 5}
    )
    # c1: ledger_used=13 + window_used=0 = 13 < 14 → accept, window_used=1
    # c2: ledger_used=13 + window_used=1 = 14 >= 14 → reject by RECORD_BUDGET
    # c3: ledger_used=13 + window_used=1 = 14 >= 14 → reject by RECORD_BUDGET
    assert len(result.accepted) == 1
    assert result.accepted[0].anchor_segment_id == "a1"
    assert len(result.rejected) == 2
    for rej in result.rejected:
        assert rej.gate == SelectionGate.RECORD_BUDGET


# ---------------------------------------------------------------------------
# P2-6: density gate 改为 per-1000-chars ratio
# ---------------------------------------------------------------------------


def test_density_gate_uses_per_1000_chars_ratio():
    """P2-6: density = total_published_count / max(base_text_length_utf16/1000, 1.0)。

    base_text_length_utf16=2000 → density_denom=2.0
    density_cap grammar_note=3.0 → 最多容纳 6 个 grammar_note（density=3.0），
    第 7 个会触发 RECORD_DENSITY。

    ledger 已有 5 个，window 内接受第 6 个（density=2.5 → 3.0），第 7 个被拒。
    """
    ledger = SelectorLedger(
        density_by_record={"grammar_note": 5, "sentence_analysis": 0},
        density_cap={"grammar_note": 3.0, "sentence_analysis": 1.0},
        base_text_length_utf16=2000,
    )
    c1 = make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1")
    c2 = make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2")
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    # c1: total=5+0=5, density=5/2.0=2.5 < 3.0 → accept, window_round density=1
    # c2: total=5+1=6, density=6/2.0=3.0 >= 3.0 → reject by RECORD_DENSITY
    assert len(result.accepted) == 1
    assert result.accepted[0].anchor_segment_id == "a1"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.RECORD_DENSITY


def test_density_gate_rejects_when_density_exceeds_cap():
    """P2-6: base_text_length_utf16=1000，已发布 3 个 grammar_note（density=3.0），
    第 4 个必须被 RECORD_DENSITY 拒绝。
    """
    ledger = SelectorLedger(
        density_by_record={"grammar_note": 3, "sentence_analysis": 0},
        density_cap={"grammar_note": 3.0, "sentence_analysis": 1.0},
        base_text_length_utf16=1000,
    )
    candidate = make_candidate(
        anchor_segment_id="a_new", semantic_dedup_key="new_key"
    )
    result = select_candidates(
        [candidate], ledger=ledger, window_budget={"grammar_note": 2}
    )
    # total=3+0=3, density=3/1.0=3.0 >= 3.0 → reject
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.RECORD_DENSITY
    assert len(result.accepted) == 0


# ---------------------------------------------------------------------------
# P1-3: anchor_segment_id ∈ target_anchor_ids pre-filter
# ---------------------------------------------------------------------------


def test_invalid_anchor_rejected_when_target_anchor_ids_provided():
    """P1-3: candidate with anchor_segment_id ∉ target_anchor_ids is rejected."""
    ledger = SelectorLedger()
    candidate_valid = make_candidate(
        anchor_segment_id="a1", semantic_dedup_key="key1"
    )
    candidate_invalid = make_candidate(
        anchor_segment_id="a999", semantic_dedup_key="key2"
    )
    result = select_candidates(
        [candidate_valid, candidate_invalid],
        ledger=ledger,
        window_budget={"grammar_note": 5},
        target_anchor_ids={"a1", "a2", "a3"},
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].anchor_segment_id == "a1"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.INVALID_ANCHOR
    assert "a999" in result.rejected[0].reason


def test_invalid_anchor_not_filtered_when_target_anchor_ids_is_none():
    """P1-3: when target_anchor_ids is None, no pre-filter (backward-compat)."""
    ledger = SelectorLedger()
    candidate = make_candidate(
        anchor_segment_id="a_unknown", semantic_dedup_key="key1"
    )
    result = select_candidates(
        [candidate], ledger=ledger, window_budget={"grammar_note": 5}
    )
    assert len(result.accepted) == 1
    assert len(result.rejected) == 0


def test_invalid_anchor_not_filtered_when_target_anchor_ids_is_empty():
    """P1-3: empty set is treated as None (defensive, skip pre-filter)."""
    ledger = SelectorLedger()
    candidate = make_candidate(
        anchor_segment_id="a1", semantic_dedup_key="key1"
    )
    result = select_candidates(
        [candidate],
        ledger=ledger,
        window_budget={"grammar_note": 5},
        target_anchor_ids=set(),
    )
    # Empty set → None → no pre-filter
    assert len(result.accepted) == 1
    assert len(result.rejected) == 0


# ---------------------------------------------------------------------------
# P1-1: gate 7 (ANCHOR_RATIO) cross-window accumulation
# ---------------------------------------------------------------------------


def test_gate7_anchor_ratio_accumulates_across_windows():
    """P1-1: gate 7 reads ledger.annotated_anchors which accumulates across
    windows via publisher's _update_ledger.

    Per design contract §7.2: gate 7 checks the ledger's current state
    (cross-window accumulated). Within a single window, accepting a
    candidate does NOT update annotated_anchors for subsequent gate 7
    checks — the update happens in the publisher's _update_ledger after
    the window's publish transaction.

    This test verifies the cross-window accumulation:
    - Window 1: ledger has 3/10 annotated (ratio 0.30, not > 0.30) → pass
    - Window 2: ledger has 5/10 annotated (ratio 0.50 > 0.30) → reject all
    """
    # Window 1: ledger after previous windows published 3 anchors
    ledger_window1 = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1, "a2": 1, "a3": 1},
            "sentence_analysis": {},
        },
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3"},
    )
    candidate_w1 = make_candidate(
        anchor_segment_id="a4", semantic_dedup_key="key4"
    )
    result_w1 = select_candidates(
        [candidate_w1], ledger=ledger_window1, window_budget={"grammar_note": 5}
    )
    # ratio 3/10=0.30, not > 0.30 → pass
    assert len(result_w1.accepted) == 1, (
        f"window 1 candidate should pass gate 7 (ratio 0.30 not > 0.30), "
        f"got: {result_w1.rejected}"
    )

    # Window 2: ledger updated by publisher after window 1 → 4 annotated
    # Now add more: 5/10 = 0.50 > 0.30 → reject
    ledger_window2 = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1, "a2": 1, "a3": 1, "a4": 1, "a5": 1},
            "sentence_analysis": {},
        },
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3", "a4", "a5"},
    )
    candidate_w2 = make_candidate(
        anchor_segment_id="a6", semantic_dedup_key="key6"
    )
    result_w2 = select_candidates(
        [candidate_w2], ledger=ledger_window2, window_budget={"grammar_note": 5}
    )
    # ratio 5/10=0.50 > 0.30 → reject
    assert len(result_w2.rejected) == 1, (
        f"window 2 candidate should be rejected by ANCHOR_RATIO "
        f"(ratio 0.50 > 0.30), got accepted: {result_w2.accepted}"
    )
    assert result_w2.rejected[0].gate == SelectionGate.ANCHOR_RATIO
