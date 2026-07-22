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
    quality_score: int = 3,
    reading_blocker: bool = False,
    dedup_hint: str = "hint1",
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
        dedup_hint=dedup_hint,
    )


def test_gate_dup_rejects_existing_semantic_dedup_key():
    """gate 1: scoped dedup key (anchor, hint) 已在 ledger"""
    ledger = SelectorLedger(
        published_dedup_keys_by_type={
            "grammar_note": [("a1", "though_concession:adverbial_clause")],
            "sentence_analysis": [],
        },
    )
    candidate = make_candidate(
        anchor_segment_id="a1",
        semantic_dedup_key="grammar_note:though_concession:adverbial_clause",
        dedup_hint="though_concession:adverbial_clause",
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
    """gate 7: projected annotated ratio > 0.30 (P1-4 fix).

    With projected ratio, accepting a candidate on a NEW anchor pushes
    the ratio up by 1/total. So 3/10 + new anchor → 4/10=0.40 > 0.30 → reject.
    A candidate on an ALREADY annotated anchor doesn't increase ratio.
    """
    # 3/10 annotated. Candidate on new anchor a4 → projected 4/10=0.40 > 0.30 → reject
    ledger = SelectorLedger(
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3"},
    )
    candidate_new = make_candidate(anchor_segment_id="a4", semantic_dedup_key="k4")
    result = select_candidates([candidate_new], ledger=ledger, window_budget={"grammar_note": 2})
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.ANCHOR_RATIO

    # Candidate on already-annotated anchor a3 → projected 3/10=0.30, not > 0.30 → pass
    # Use sentence_analysis to avoid ANCHOR_CAP (grammar_note already has a3:1)
    candidate_existing = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a3",
        semantic_dedup_key="k_existing",
    )
    result2 = select_candidates(
        [candidate_existing], ledger=ledger, window_budget={"sentence_analysis": 2}
    )
    assert len(result2.accepted) == 1
    assert len(result2.rejected) == 0


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
        make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=1),
        make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=5),
        make_candidate(anchor_segment_id="a3", semantic_dedup_key="k3", quality_score=3),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(), window_budget={"grammar_note": 3})
    # 应接受全部 3 个，按 quality_score 降序
    assert len(result.accepted) == 3
    assert result.accepted[0].anchor_segment_id == "a2"  # 5
    assert result.accepted[1].anchor_segment_id == "a3"  # 3
    assert result.accepted[2].anchor_segment_id == "a1"  # 1


def test_reading_blocker_prioritized():
    """reading_blocker=True 优先"""
    candidates = [
        make_candidate(anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=3, reading_blocker=False),
        make_candidate(anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=3, reading_blocker=True),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(), window_budget={"grammar_note": 2})
    assert result.accepted[0].anchor_segment_id == "a2"  # blocker 优先


def test_sentence_analysis_prioritized_over_grammar_note():
    """同 quality 时 grammar_note > sentence_analysis（grammar_note 优先）。

    reader-grammar-candidate-selection spec: 排序顺序 MUST 为
    grammar_note 优先于 sentence_analysis（sentence_analysis 应有更高准入门槛）。
    旧测试名保留向后兼容，但断言已对齐 spec。
    """
    candidates = [
        make_candidate(item_type="grammar_note", anchor_segment_id="a1", semantic_dedup_key="k1", quality_score=3, dedup_hint="hint_g"),
        make_candidate(item_type="sentence_analysis", anchor_segment_id="a2", semantic_dedup_key="k2", quality_score=3, dedup_hint="hint_s"),
    ]
    result = select_candidates(candidates, ledger=SelectorLedger(),
                                window_budget={"grammar_note": 2, "sentence_analysis": 2})
    assert result.accepted[0].item_type == "grammar_note"


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
    """reader-grammar-candidate-selection: 同 window 内两个 candidate 共享
    (anchor_segment_id, dedup_hint)，第二个必须被 DUP 拒绝。

    旧实现（cross-type single-field dedup）按 normalized hint 单字段扫描，
    不同 anchor 同 hint 也会被淘汰——本测试在新合同下要求两个 candidate
    共享 anchor_segment_id 才会触发 DUP。
    """
    ledger = SelectorLedger()  # ledger 中无任何 dedup key
    c1 = make_candidate(
        anchor_segment_id="a1",
        semantic_dedup_key="shared_key",
        dedup_hint="hint1",
    )
    c2 = make_candidate(
        anchor_segment_id="a1",  # same anchor + same hint → DUP
        semantic_dedup_key="shared_key2",  # different semantic_dedup_key
        dedup_hint="hint1",
    )
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].anchor_segment_id == "a1"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.DUP
    assert result.rejected[0].candidate.anchor_segment_id == "a1"


def test_selector_rejects_second_item_for_same_anchor_within_same_window():
    """P1-5: 同 window 内两个 candidate 落在相同 anchor + 相同 item_type，
    第二个必须被 ANCHOR_CAP 拒绝（per_anchor_cap=1）。

    旧实现只读 ledger.published_anchor_counts，未累计 window 内已接受的同 anchor
    item，所以会接受两个同 anchor 的 item。修复后 window_round 累计 anchor count。

    reader-grammar-candidate-selection: 两个 candidate 必须使用不同的
    ``dedup_hint``，否则会被 DUP gate（同 anchor 同 hint）优先淘汰而非
    ANCHOR_CAP。
    """
    ledger = SelectorLedger()  # ledger 中 anchor_counts 为空
    c1 = make_candidate(
        anchor_segment_id="a1", semantic_dedup_key="k1", dedup_hint="hint1"
    )
    c2 = make_candidate(
        anchor_segment_id="a1", semantic_dedup_key="k2", dedup_hint="hint2"
    )
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
    """P1-4: gate 7 checks projected ratio including current candidate +
    same-window accepted anchors (cross item_type).

    Per design §7.3: per_record <= 30% anchor ratio. The gate must check
    the PROJECTED ratio (what it would be AFTER accepting this candidate),
    not just the current ledger ratio.

    Cross-window accumulation via ledger.annotated_anchors (updated by
    publisher's _update_ledger after each window).

    Scenarios:
    - Window 1: ledger has 3/10 annotated (ratio 0.30). Candidate on a4
      (new anchor) → projected 4/10=0.40 > 0.30 → REJECT (P1-4 fix).
      Candidate on a3 (already annotated) → projected 3/10=0.30 → pass.
    - Window 2: ledger has 2/10 annotated (ratio 0.20). Two candidates on
      a3, a4 (both new) → a3: projected 3/10=0.30, pass; a4: projected
      4/10=0.40 > 0.30, reject (same window, after accepting a3).
    """
    # Window 1: ledger has 3/10 annotated. Candidate on new anchor a4
    # → projected 4/10=0.40 > 0.30 → REJECT (P1-4 fix)
    ledger_window1 = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1, "a2": 1, "a3": 1},
            "sentence_analysis": {},
        },
        total_anchors=10,
        annotated_anchors={"a1", "a2", "a3"},
    )
    candidate_new = make_candidate(
        anchor_segment_id="a4", semantic_dedup_key="key4"
    )
    result = select_candidates(
        [candidate_new], ledger=ledger_window1, window_budget={"grammar_note": 5}
    )
    # projected 4/10=0.40 > 0.30 → REJECT
    assert len(result.rejected) == 1, (
        f"candidate on new anchor should be rejected by ANCHOR_RATIO "
        f"(projected 0.40 > 0.30), got accepted: {result.accepted}"
    )
    assert result.rejected[0].gate == SelectionGate.ANCHOR_RATIO

    # Window 2: ledger has 2/10. Candidate on a2 (already annotated by grammar_note)
    # → projected 2/10=0.20 (no new anchor) → pass
    # Use sentence_analysis to avoid ANCHOR_CAP (grammar_note already has a2:1)
    ledger_window2 = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1, "a2": 1},
            "sentence_analysis": {},
        },
        total_anchors=10,
        annotated_anchors={"a1", "a2"},
    )
    candidate_existing = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a2",
        semantic_dedup_key="key_existing",
    )
    result2 = select_candidates(
        [candidate_existing], ledger=ledger_window2, window_budget={"sentence_analysis": 5}
    )
    # projected 2/10=0.20 (a2 already annotated) → pass
    assert len(result2.accepted) == 1, (
        f"candidate on already-annotated anchor should pass gate 7, "
        f"got: {result2.rejected}"
    )

    # Window 3: same window, two candidates on new anchors a3, a4
    # a3: projected 3/10=0.30, not > 0.30 → pass
    # a4: projected 4/10=0.40 > 0.30 → reject (after accepting a3 in same window)
    ledger_window3 = SelectorLedger(
        published_anchor_counts_by_type={
            "grammar_note": {"a1": 1, "a2": 1},
            "sentence_analysis": {},
        },
        total_anchors=10,
        annotated_anchors={"a1", "a2"},
    )
    candidate_a3 = make_candidate(
        anchor_segment_id="a3", semantic_dedup_key="key_a3"
    )
    candidate_a4 = make_candidate(
        anchor_segment_id="a4", semantic_dedup_key="key_a4"
    )
    result3 = select_candidates(
        [candidate_a3, candidate_a4],
        ledger=ledger_window3,
        window_budget={"grammar_note": 5},
    )
    accepted_ids = {c.anchor_segment_id for c in result3.accepted}
    assert "a3" in accepted_ids, (
        f"a3 should pass (projected 0.30 not > 0.30), got: {result3.rejected}"
    )
    a4_rejection = [
        r for r in result3.rejected if r.candidate.anchor_segment_id == "a4"
    ]
    assert len(a4_rejection) == 1, (
        f"a4 should be rejected by ANCHOR_RATIO (projected 0.40 > 0.30 "
        f"after accepting a3 in same window), got: {result3.rejected}"
    )
    assert a4_rejection[0].gate == SelectionGate.ANCHOR_RATIO


# ---------------------------------------------------------------------------
# Phase 5: density_cap symmetry + cross-type coexist design choice
# ---------------------------------------------------------------------------


def test_sentence_analysis_default_density_cap_is_2_0():
    """Phase 5: sentence_analysis default density_cap is 2.0.

    Old default 1.0 was asymmetric with grammar_note's 3.0 and caused
    sentence_analysis candidates to be silently rejected by RECORD_DENSITY
    before they could compete with grammar_note on merit. Symmetric 2.0
    keeps sentence_analysis viable while still bounded.
    """
    ledger = SelectorLedger()
    assert ledger.density_cap["grammar_note"] == 3.0
    assert ledger.density_cap["sentence_analysis"] == 2.0


def test_grammar_note_and_sentence_analysis_can_coexist_on_same_anchor():
    """Phase 5 design choice: backend does not perform cross-type
    deduplication between grammar_note and sentence_analysis on the same
    anchor *as long as their dedup_hints differ*. They compete at the
    prompt layer (shared teaching contract declares same-point competition
    as a generation responsibility, not a backend gate).

    Gates 1 (DUP) and 3 (ANCHOR_CAP) are per-item-type, so a grammar_note
    and a sentence_analysis marking the same anchor with *different*
    dedup_hints are both accepted. Cross-type dedup on the *same* dedup_hint
    is the selector's job (scoped dedup key contract); cross-type dedup on
    *different* dedup_hints is the LLM's job (prompt-level).
    """
    ledger = SelectorLedger(total_anchors=10)
    grammar_candidate = make_candidate(
        item_type="grammar_note",
        anchor_segment_id="a1",
        semantic_dedup_key="shared_point",
        quality_score=3,
        dedup_hint="grammar_point_x",
    )
    sentence_candidate = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a1",
        semantic_dedup_key="shared_point",  # same key, different item_type
        quality_score=3,
        dedup_hint="sentence_point_y",  # different dedup_hint → no DUP
    )
    result = select_candidates(
        [grammar_candidate, sentence_candidate],
        ledger=ledger,
        window_budget={"grammar_note": 2, "sentence_analysis": 2},
    )
    # Both accepted — no cross-type dedup at the selector layer (different hints)
    assert len(result.accepted) == 2
    assert {c.item_type for c in result.accepted} == {
        "grammar_note",
        "sentence_analysis",
    }


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: scoped dedup key (anchor, hint)
# ---------------------------------------------------------------------------


def test_gate_dup_allows_different_anchor_same_hint():
    """不同 anchor + 同 dedup_hint：DUP gate MUST NOT 淘汰任一候选。

    旧实现按 normalized hint 单字段跨 item_type 桶扫描，会把不同 anchor 上
    同一学习点也当作重复淘汰。新合同把 dedup 身份收窄为 (anchor, hint) 元组，
    全文重复控制交给 PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET。
    """
    ledger = SelectorLedger()  # ledger 中无任何 dedup key
    c1 = make_candidate(
        anchor_segment_id="a1",
        semantic_dedup_key="k1",
        dedup_hint="same_hint",
    )
    c2 = make_candidate(
        anchor_segment_id="a2",  # different anchor, same hint
        semantic_dedup_key="k2",
        dedup_hint="same_hint",
    )
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    # 两个都应被接受 — 不同 anchor 同 hint 不触发 DUP
    assert len(result.accepted) == 2
    assert len(result.rejected) == 0
    accepted_anchors = {c.anchor_segment_id for c in result.accepted}
    assert accepted_anchors == {"a1", "a2"}


def test_gate_dup_rejects_same_anchor_same_hint_cross_type():
    """同 anchor + 同 dedup_hint + 跨 item_type：DUP gate MUST 淘汰后到达的候选。

    winner 由 sort order 决定（quality_score desc → reading_blocker true first
    → grammar_note 优先）。本测试给 grammar_note 更高分数，使其胜出；
    sentence_analysis 被淘汰。
    """
    ledger = SelectorLedger(total_anchors=10)
    grammar_candidate = make_candidate(
        item_type="grammar_note",
        anchor_segment_id="a1",
        semantic_dedup_key="k_g",
        quality_score=5,
        reading_blocker=False,
        dedup_hint="same_hint",
    )
    sentence_candidate = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a1",  # same anchor
        semantic_dedup_key="k_s",
        quality_score=3,
        reading_blocker=False,
        dedup_hint="same_hint",  # same hint, different item_type
    )
    result = select_candidates(
        [grammar_candidate, sentence_candidate],
        ledger=ledger,
        window_budget={"grammar_note": 2, "sentence_analysis": 2},
    )
    # 只接受一个 — grammar_note 胜出（更高分）
    assert len(result.accepted) == 1
    assert result.accepted[0].item_type == "grammar_note"
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.DUP
    assert result.rejected[0].candidate.item_type == "sentence_analysis"


def test_gate_dup_rejects_same_anchor_same_hint_same_type():
    """同 anchor + 同 dedup_hint + 同 item_type：DUP gate MUST 淘汰第二个。

    两个 candidate 同 anchor 同 hint 同 item_type，但 semantic_dedup_key
    不同（避免被当作完全相同的候选）。第二个被 DUP 淘汰（DUP 在 ANCHOR_CAP
    之前检查）。
    """
    ledger = SelectorLedger()
    c1 = make_candidate(
        anchor_segment_id="a1",
        semantic_dedup_key="k1",
        quality_score=5,
        dedup_hint="same_hint",
    )
    c2 = make_candidate(
        anchor_segment_id="a1",  # same anchor
        semantic_dedup_key="k2",  # different semantic_dedup_key
        quality_score=3,
        dedup_hint="same_hint",  # same hint, same item_type
    )
    result = select_candidates(
        [c1, c2], ledger=ledger, window_budget={"grammar_note": 5}
    )
    # 只接受一个 — c1 胜出（更高分）
    assert len(result.accepted) == 1
    assert result.accepted[0].semantic_dedup_key == "k1"
    assert len(result.rejected) == 1
    # DUP 在 ANCHOR_CAP 之前检查，所以应该是 DUP
    assert result.rejected[0].gate == SelectionGate.DUP
    assert result.rejected[0].candidate.semantic_dedup_key == "k2"


def test_gate_dup_emits_dedup_hint_duplicate_reason_code():
    """DUP gate 淘汰时 MUST 在独立 ``reason_code`` 字段设置
    ``dedup_hint_duplicate``。

    reader-grammar-candidate-selection: ``reason_code`` 为独立结构化字段，
    不再由 ``reason`` 字符串承担。``reason`` 仅保留人类可读详情。
    """
    ledger = SelectorLedger(total_anchors=10)
    grammar_candidate = make_candidate(
        item_type="grammar_note",
        anchor_segment_id="a1",
        semantic_dedup_key="k_g",
        quality_score=5,
        dedup_hint="same_hint",
    )
    sentence_candidate = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a1",
        semantic_dedup_key="k_s",
        quality_score=3,
        dedup_hint="same_hint",
    )
    result = select_candidates(
        [grammar_candidate, sentence_candidate],
        ledger=ledger,
        window_budget={"grammar_note": 2, "sentence_analysis": 2},
    )
    assert len(result.rejected) == 1
    assert result.rejected[0].gate == SelectionGate.DUP
    assert result.rejected[0].reason_code == "dedup_hint_duplicate"
    # reason 仍是人类可读详情，不再承担 code 合同
    assert "dedup_hint_duplicate" not in result.rejected[0].reason


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: CandidateItem.__post_init__ validation
# ---------------------------------------------------------------------------


def test_candidate_item_rejects_bool_quality_score():
    """``bool`` is a subclass of ``int`` but must be rejected as quality_score."""
    with pytest.raises(TypeError, match="quality_score"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=True,  # bool, not int
            reading_blocker=False,
            dedup_hint="hint1",
        )


def test_candidate_item_rejects_float_quality_score():
    """``float`` must be rejected as quality_score."""
    with pytest.raises(TypeError, match="quality_score"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=3.0,  # float, not int
            reading_blocker=False,
            dedup_hint="hint1",
        )


def test_candidate_item_rejects_out_of_range_quality_score():
    """quality_score must be in 1..5."""
    with pytest.raises(ValueError, match="quality_score"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=6,
            reading_blocker=False,
            dedup_hint="hint1",
        )


def test_candidate_item_rejects_non_bool_reading_blocker():
    """``reading_blocker`` must be exact ``bool``, not ``int``."""
    with pytest.raises(TypeError, match="reading_blocker"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=3,
            reading_blocker=1,  # int, not bool
            dedup_hint="hint1",
        )


def test_candidate_item_rejects_empty_dedup_hint():
    """``dedup_hint`` must be non-empty after trim/normalize."""
    with pytest.raises(ValueError, match="dedup_hint"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=3,
            reading_blocker=False,
            dedup_hint="",
        )


def test_candidate_item_rejects_whitespace_only_dedup_hint():
    """``dedup_hint`` whitespace-only must fail after trim/normalize."""
    with pytest.raises(ValueError, match="dedup_hint"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=3,
            reading_blocker=False,
            dedup_hint="   \t\n  ",
        )


def test_candidate_item_rejects_overlong_dedup_hint():
    """``dedup_hint`` > 120 chars after normalization must fail."""
    from app.services.reader_orchestration.grammar_candidate_policy import (
        MAX_DEDUP_HINT_LENGTH,
    )

    with pytest.raises(ValueError, match="dedup_hint"):
        CandidateItem(
            item_type="grammar_note",
            anchor_segment_id="a1",
            spans=[{"unit_id": "u1"}],
            semantic_dedup_key="k1",
            pattern_key="p1",
            quality_score=3,
            reading_blocker=False,
            dedup_hint="x" * (MAX_DEDUP_HINT_LENGTH + 1),
        )


def test_candidate_item_saves_normalized_dedup_hint():
    """``__post_init__`` writes back the normalized hint (trim + lowercase + collapse)."""
    candidate = CandidateItem(
        item_type="grammar_note",
        anchor_segment_id="a1",
        spans=[{"unit_id": "u1"}],
        semantic_dedup_key="k1",
        pattern_key="p1",
        quality_score=3,
        reading_blocker=False,
        dedup_hint="  Foo   BAR  ",
    )
    assert candidate.dedup_hint == "foo bar"


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: DUP diagnostic structured metadata
# ---------------------------------------------------------------------------


def test_dup_current_window_carries_complete_winner_metadata():
    """同 window DUP rejection 携带完整 winner metadata。

    winner_source=current_window, winner_item_index 为 winner 在
    sorted_candidates 中的真实 index, winner_item_type / winner_anchor_segment_id
    与 winner 一致。
    """
    from app.services.reader_orchestration.grammar_candidate_policy import (
        DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
    )

    ledger = SelectorLedger(total_anchors=10)
    # grammar_note quality=5 wins over sentence_analysis quality=3
    # (same anchor + same hint → DUP). Both share anchor "a1" + hint "same".
    grammar = make_candidate(
        item_type="grammar_note",
        anchor_segment_id="a1",
        semantic_dedup_key="k_g",
        quality_score=5,
        dedup_hint="same",
    )
    sentence = make_candidate(
        item_type="sentence_analysis",
        anchor_segment_id="a1",
        semantic_dedup_key="k_s",
        quality_score=3,
        dedup_hint="same",
    )
    result = select_candidates(
        [grammar, sentence],
        ledger=ledger,
        window_budget={"grammar_note": 2, "sentence_analysis": 2},
    )
    assert len(result.rejected) == 1
    rejected = result.rejected[0]
    assert rejected.gate == SelectionGate.DUP
    assert rejected.dedup_metadata is not None
    md = rejected.dedup_metadata
    assert md.normalized_hint == "same"
    assert md.winner_item_type == "grammar_note"
    assert md.winner_anchor_segment_id == "a1"
    assert md.winner_item_index == 0  # grammar is first in sorted order
    assert md.winner_source == DEDUP_WINNER_SOURCE_CURRENT_WINDOW


def test_dup_published_ledger_carries_null_index_metadata():
    """published ledger DUP rejection: winner_item_index=null, winner_source=published_ledger.

    ledger winner 的 index 为 null，不得伪造。
    """
    from app.services.reader_orchestration.grammar_candidate_policy import (
        DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER,
    )

    ledger = SelectorLedger(
        published_dedup_keys_by_type={
            "grammar_note": [("a1", "already_published")],
            "sentence_analysis": [],
        },
        total_anchors=10,
    )
    candidate = make_candidate(
        item_type="grammar_note",
        anchor_segment_id="a1",
        semantic_dedup_key="k1",
        dedup_hint="already_published",
    )
    result = select_candidates(
        [candidate], ledger=ledger, window_budget={"grammar_note": 2}
    )
    assert len(result.rejected) == 1
    rejected = result.rejected[0]
    assert rejected.gate == SelectionGate.DUP
    assert rejected.dedup_metadata is not None
    md = rejected.dedup_metadata
    assert md.normalized_hint == "already_published"
    assert md.winner_item_type == "grammar_note"
    assert md.winner_anchor_segment_id == "a1"
    assert md.winner_item_index is None  # ledger winner index must not be fabricated
    assert md.winner_source == DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER


def test_non_dup_rejection_has_no_dedup_metadata():
    """Non-DUP gate rejections do not carry dedup_metadata and have
    ``reason_code is None``."""
    ledger = SelectorLedger(total_anchors=10)
    candidate = make_candidate(
        anchor_segment_id="a1",
        dedup_hint="hint1",
        semantic_dedup_key="k1",
        pattern_key="p1",
    )
    # Fill pattern_key count to 3 in ledger to trigger PATTERN_DENSE
    ledger = SelectorLedger(
        published_pattern_keys_by_type={
            "grammar_note": ["p1", "p1", "p1"],
            "sentence_analysis": [],
        },
        total_anchors=10,
    )
    result = select_candidates(
        [candidate], ledger=ledger, window_budget={"grammar_note": 2}
    )
    assert len(result.rejected) == 1
    assert result.rejected[0].dedup_metadata is None
    # reader-grammar-candidate-selection: 非 DUP gate 的 reason_code 为 None
    assert result.rejected[0].reason_code is None
