"""R4-A4-1A tests for the Answer Correctness Policy deep module.

Pure deterministic tests through the public interface only — no model
calls, no I/O, no agent/runtime/validator wiring. The policy module is a
leaf module: it MUST NOT import from agent / runtime / runtime_deps /
grounding_validator / baseline_context / finalizer / evidence* / pydantic_ai.

Authoritative contract: design report §21 (TMP-reader-record-ask-r4-a4-1-
correctness-policy-design-2026-07-19.md). If §6–§18 conflict with §21,
§21 wins.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from app.services.reader_record_ask.answer_correctness_policy import (
    STRICT_ARTICLE_QUESTION_FORMS,
    AnswerCorrectnessPolicy,
    ExplicitOutputConstraint,
    PolicyViolation,
    build_answer_correctness_policy,
)

# ---------------------------------------------------------------------------
# Constants — must match §21.2 exactly
# ---------------------------------------------------------------------------

_RAW_STRICT_FORMS: tuple[str, ...] = (
    "这篇文章在讲什么",
    "这篇文章主要说了什么",
    "概括这篇文章的核心观点",
    "作者最想说明什么",
    "这篇文章是怎么展开论证的",
    "帮我出一道练习题",
    "基于这篇文章出一道小练习",
    "文章提到了哪些城市",
    "文章是什么时候发生/发布的",
    "只用一句话概括文章",
    "文章没有提到的年份是什么？不得猜测",
    "基于文章出一道选择题，只允许一题",
)


# ---------------------------------------------------------------------------
# 1. Interface shape — 3 dataclasses + 1 builder function (§21.1)
# ---------------------------------------------------------------------------


def test_explicit_output_constraint_is_frozen_slots_dataclass() -> None:
    """ExplicitOutputConstraint is @dataclass(frozen=True, slots=True)."""
    assert dataclasses.is_dataclass(ExplicitOutputConstraint)
    params = ExplicitOutputConstraint.__dataclass_params__
    assert params.frozen is True
    assert set(ExplicitOutputConstraint.__slots__) == {
        field.name for field in dataclasses.fields(ExplicitOutputConstraint)
    }
    fields = {f.name for f in dataclasses.fields(ExplicitOutputConstraint)}
    assert fields == {"kind", "requested_count", "extraction_confidence"}


def test_policy_violation_is_frozen_slots_dataclass() -> None:
    """PolicyViolation is @dataclass(frozen=True, slots=True)."""
    assert dataclasses.is_dataclass(PolicyViolation)
    params = PolicyViolation.__dataclass_params__
    assert params.frozen is True
    assert set(PolicyViolation.__slots__) == {
        field.name for field in dataclasses.fields(PolicyViolation)
    }
    fields = {f.name for f in dataclasses.fields(PolicyViolation)}
    assert fields == {"kind", "detail"}


def test_answer_correctness_policy_is_frozen_slots_dataclass() -> None:
    """AnswerCorrectnessPolicy is @dataclass(frozen=True, slots=True)."""
    assert dataclasses.is_dataclass(AnswerCorrectnessPolicy)
    params = AnswerCorrectnessPolicy.__dataclass_params__
    assert params.frozen is True
    assert set(AnswerCorrectnessPolicy.__slots__) == {
        field.name for field in dataclasses.fields(AnswerCorrectnessPolicy)
    }
    fields = {f.name for f in dataclasses.fields(AnswerCorrectnessPolicy)}
    assert fields == {
        "temporal_allowset",
        "explicit_output",
        "is_article_only_strict",
        "baseline_is_complete",
    }


def test_builder_signature_keyword_only() -> None:
    """build_answer_correctness_policy takes only keyword args:
    user_message, model_visible_chunk_texts, baseline_is_complete."""
    import inspect

    sig = inspect.signature(build_answer_correctness_policy)
    params = sig.parameters
    assert set(params) == {
        "user_message",
        "model_visible_chunk_texts",
        "baseline_is_complete",
    }
    # All must be keyword-only (no positional).
    for name, p in params.items():
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, name


# ---------------------------------------------------------------------------
# 2. Strict article question classification (§21.2)
# ---------------------------------------------------------------------------


def test_strict_forms_set_has_twelve_members() -> None:
    """§21.2: STRICT_ARTICLE_QUESTION_FORMS has exactly 12 entries."""
    assert isinstance(STRICT_ARTICLE_QUESTION_FORMS, frozenset)
    assert len(STRICT_ARTICLE_QUESTION_FORMS) == 12


@pytest.mark.parametrize("raw", _RAW_STRICT_FORMS)
def test_strict_forms_exact_match(raw: str) -> None:
    """Each raw strict form (after NFKC + whitespace fold + trim + trailing
    punct strip) must classify is_article_only_strict=True."""
    policy = build_answer_correctness_policy(
        user_message=raw,
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is True


@pytest.mark.parametrize(
    "raw",
    [
        # Trailing punctuation variants — must still match.
        "这篇文章在讲什么。",
        "这篇文章在讲什么！",
        "这篇文章在讲什么？",
        "这篇文章主要说了什么?",
        "概括这篇文章的核心观点!",
        "作者最想说明什么。",
        "帮我出一道练习题?",
        "文章是什么时候发生/发布的。",
        "文章没有提到的年份是什么？不得猜测。",
        "文章没有提到的年份是什么?不得猜测!",
        # Whitespace fold variants.
        "  这篇文章在讲什么  ",
        # Full-width → half-width via NFKC.
        "文章是什么时候发生／发布的",  # ／ → /
    ],
)
def test_strict_forms_normalize_then_match(raw: str) -> None:
    """Normalization (NFKC + whitespace fold + trim + trailing punct strip)
    must bring these variants into the strict set."""
    policy = build_answer_correctness_policy(
        user_message=raw,
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is True, f"expected strict for: {raw!r}"


@pytest.mark.parametrize(
    "raw",
    [
        # Mixed / near-match — must fail-open.
        "这篇文章主要说了什么？和 2024 年比较",
        "概括文章",  # near hint but not exact
        "概括核心观点",  # old hint, not in new strict set
        "这篇文章主要讲了什么",  # tense variant
        "作者想说明什么",  # missing 最
        "基于文章出一道选择题",  # missing 只允许一题
        "基于文章出一道选择题，只允许两题",  # different count
        "请帮我出一道练习题",  # prefix
        "这篇文章在讲什么呢",  # suffix
        "出一道练习题",  # missing 帮我
        # Empty / whitespace.
        "",
        "   ",
        # Questions with years — not in strict set.
        "2025 年的文章说了什么",
        "是不是 2025 年？",
    ],
)
def test_non_strict_forms_fail_open(raw: str) -> None:
    """Non-exact-match questions must classify is_article_only_strict=False."""
    policy = build_answer_correctness_policy(
        user_message=raw,
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is False, f"expected non-strict for: {raw!r}"


# ---------------------------------------------------------------------------
# 3. Temporal token extraction (§7.2 positive + §7.3 negative)
# ---------------------------------------------------------------------------


def _policy_with_chunks(
    *chunks: str,
    user_message: str = "这篇文章主要说了什么",
    baseline_is_complete: bool = True,
) -> AnswerCorrectnessPolicy:
    return build_answer_correctness_policy(
        user_message=user_message,
        model_visible_chunk_texts=tuple(chunks),
        baseline_is_complete=baseline_is_complete,
    )


@pytest.mark.parametrize(
    "chunk,expected",
    [
        # §7.6 positive cases (19).
        ("2025 年", {"2025"}),
        ("2025年", {"2025"}),
        ("2025年1月", {"2025"}),
        ("2025 年初", {"2025"}),
        ("2025 年底", {"2025"}),
        ("2025-01-15", {"2025"}),
        ("2025-01", {"2025"}),
        ("January 2025", {"2025"}),
        ("Jan 2025", {"2025"}),
        ("in 2025", {"2025"}),
        ("since 2025", {"2025"}),
        ("by 2025", {"2025"}),
        ("from 2025", {"2025"}),
        ("during 2025", {"2025"}),
        ("Q1 2025", {"2025"}),
        ("2025 Q1", {"2025"}),
        ("2024-2025 年", {"2024", "2025"}),
        ("2024-2025 学年", {"2024", "2025"}),
        ("2024-2025 academic year", {"2024", "2025"}),
    ],
)
def test_temporal_positive_patterns(chunk: str, expected: set[str]) -> None:
    """§7.2 positive temporal patterns must extract the 4-digit year(s)."""
    policy = _policy_with_chunks(chunk)
    assert set(policy.temporal_allowset) == expected


@pytest.mark.parametrize(
    "chunk",
    [
        # §7.6 negative cases (14).
        "2024 个用户",
        "2024 人",
        "v2.0.2024",
        "$2024",
        "ID 2024",
        "RFC 2024",
        "第 2024 条",
        "page 2024",
        "0.2024",
        "2024.5",
        "#2024",
        "2024 号",
        "最近",
        "今年",
    ],
)
def test_temporal_negative_patterns(chunk: str) -> None:
    """§7.3 negative patterns must NOT extract any token."""
    policy = _policy_with_chunks(chunk)
    assert policy.temporal_allowset == frozenset()


def test_temporal_allowset_aggregates_across_chunks() -> None:
    """Multiple chunks contribute to a single allowset."""
    policy = _policy_with_chunks("2020 年", "in 2021", "no year here")
    assert set(policy.temporal_allowset) == {"2020", "2021"}


def test_temporal_allowset_empty_when_no_chunks() -> None:
    """Empty chunks tuple → empty allowset."""
    policy = _policy_with_chunks()
    assert policy.temporal_allowset == frozenset()


def test_temporal_allowset_does_not_extract_from_user_message() -> None:
    """§21.3: years in user_message must NOT enter the allowset."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么 2024 年",  # not strict (has year)
        model_visible_chunk_texts=("no years here",),
        baseline_is_complete=True,
    )
    assert policy.temporal_allowset == frozenset()


# ---------------------------------------------------------------------------
# 4. Temporal guard — complete/partial × strict/non-strict four-quadrant
# ---------------------------------------------------------------------------


def _draft_with_year(year: str) -> str:
    return f"文章报道了 {year} 年的事件。"


def test_temporal_quadrant_complete_strict_violation() -> None:
    """Q1: baseline_is_complete=True + strict + unsupported year → violation."""
    policy = _policy_with_chunks(
        "no year in this chunk",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=True,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2025"))
    assert len(violations) == 1
    assert violations[0].kind == "temporal_claim_unsupported"


def test_temporal_quadrant_partial_strict_fail_open() -> None:
    """Q2: baseline_is_complete=False + strict + unsupported year → pass.

    Partial baseline always fail-open — read_range / search_current_article
    might supply legitimate article years not in the frozen allowset.
    """
    policy = _policy_with_chunks(
        "no year in this chunk",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=False,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2025"))
    assert violations == ()


def test_temporal_quadrant_complete_non_strict_fail_open() -> None:
    """Q3: baseline_is_complete=True + non-strict + unsupported year → pass.

    Non-strict questions allow external knowledge; temporal guard disabled.
    """
    policy = _policy_with_chunks(
        "no year in this chunk",
        user_message="结合现实解释",  # not in strict set
        baseline_is_complete=True,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2025"))
    assert violations == ()


def test_temporal_quadrant_partial_non_strict_fail_open() -> None:
    """Q4: baseline_is_complete=False + non-strict + unsupported year → pass."""
    policy = _policy_with_chunks(
        "no year in this chunk",
        user_message="结合现实解释",
        baseline_is_complete=False,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2025"))
    assert violations == ()


def test_temporal_supported_year_in_allowset_passes() -> None:
    """When guard enabled and answer year ∈ allowset → pass."""
    policy = _policy_with_chunks(
        "文章发表于 2023 年 5 月。",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=True,
    )
    assert "2023" in policy.temporal_allowset
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2023"))
    assert violations == ()


def test_temporal_partial_baseline_does_not_block_tool_supplied_year() -> None:
    """Regression: partial baseline must not block a year that a tool call
    might legitimately surface later. Even if the answer mentions 2024 and
    2024 is not in the partial allowset, partial → fail-open."""
    policy = _policy_with_chunks(
        "first chunk has no year",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=False,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2024"))
    assert violations == ()


def test_temporal_no_high_confidence_token_in_answer_passes() -> None:
    """Answer with only relative time / bare 4-digit quantity → pass."""
    policy = _policy_with_chunks(
        "no year",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=True,
    )
    # "2024 个用户" is a bare quantity; "最近" is relative time.
    violations = policy.evaluate_draft(
        draft_answer_text="文章提到 2024 个用户。最近发表了相关研究。"
    )
    assert violations == ()


def test_temporal_violation_detail_uses_frozen_message() -> None:
    """§21.3: retry detail must be the fixed message and must NOT suggest
    'label as general knowledge'."""
    policy = _policy_with_chunks(
        "no year",
        user_message="这篇文章主要说了什么",
        baseline_is_complete=True,
    )
    violations = policy.evaluate_draft(draft_answer_text=_draft_with_year("2025"))
    assert len(violations) == 1
    detail = violations[0].detail
    assert "general knowledge" not in detail.lower()
    assert "label as" not in detail.lower()
    # The authoritative §21.3 message:
    expected = (
        "The complete article context does not contain that date. Remove the "
        "unsupported date, or state that the article does not provide it "
        "without repeating a specific date."
    )
    assert detail == expected


# ---------------------------------------------------------------------------
# 5. Explicit exercise-count request extraction (§21.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,count",
    [
        ("一道题", 1),
        ("一道练习题", 1),
        ("一道小练习", 1),
        ("一道选择题", 1),
        ("一题", 1),
        ("只允许一题", 1),
        ("只要一题", 1),
        ("两道题", 2),
        ("两道练习题", 2),
        ("两题", 2),
        ("三道题", 3),
        ("三道练习题", 3),
        ("三题", 3),
    ],
)
def test_count_request_high_confidence_phrases(phrase: str, count: int) -> None:
    """§21.4 request-side table — each phrase maps to a specific count."""
    policy = build_answer_correctness_policy(
        user_message=f"请帮我出{phrase}",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.kind == "exercise_items"
    assert explicit.requested_count == count
    assert explicit.extraction_confidence == "high"


@pytest.mark.parametrize(
    "phrase",
    ["几道题", "若干题", "一组题"],
)
def test_count_request_indeterminate_phrases(phrase: str) -> None:
    """§21.4 indeterminate phrases → kind=exercise_items, count=None."""
    policy = build_answer_correctness_policy(
        user_message=f"出{phrase}",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.kind == "exercise_items"
    assert explicit.requested_count is None
    assert explicit.extraction_confidence == "indeterminate"


def test_count_request_no_phrase_means_none() -> None:
    """No exercise phrase → kind=none."""
    policy = build_answer_correctness_policy(
        user_message="概括文章",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.kind == "none"
    assert explicit.requested_count is None
    assert explicit.extraction_confidence == "indeterminate"


@pytest.mark.parametrize(
    "user_message",
    [
        "给我一个观点",
        "列出三个城市",
        "举两个例子",
        "文章有几个段落",
        "一共有多少章节",
    ],
)
def test_count_request_non_exercise_numbers_do_not_trigger(user_message: str) -> None:
    """§21.4: plain 一个/两个/三个 must NOT be interpreted as exercise count."""
    policy = build_answer_correctness_policy(
        user_message=user_message,
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.kind == "none"
    assert explicit.requested_count is None
    assert explicit.extraction_confidence == "indeterminate"


def test_count_request_conflict_is_indeterminate() -> None:
    """§21.4: conflicting counts (e.g. '出两道题，只允许一题') → indeterminate."""
    policy = build_answer_correctness_policy(
        user_message="出两道题，只允许一题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.kind == "exercise_items"
    assert explicit.requested_count is None
    assert explicit.extraction_confidence == "indeterminate"


def test_count_request_specific_plus_indeterminate_is_indeterminate() -> None:
    """Mixing a specific count with an indeterminate phrase → indeterminate."""
    policy = build_answer_correctness_policy(
        user_message="出一道题或几道题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    assert explicit.extraction_confidence == "indeterminate"
    assert explicit.requested_count is None


def test_count_request_first_question_marker_not_counted() -> None:
    """'第一题' references question 1, not a request for one question.
    Must not trigger exercise_items with count=1."""
    policy = build_answer_correctness_policy(
        user_message="请回答第一题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    explicit = policy.explicit_output
    # "第一题" should not be treated as a request for one exercise item.
    assert explicit.kind == "none"


# ---------------------------------------------------------------------------
# 6. Explicit exercise-count answer parser (§21.4 + §9.2-9.6)
# ---------------------------------------------------------------------------


def _count_policy(requested: int) -> AnswerCorrectnessPolicy:
    """Build a policy with a high-confidence exercise count request."""
    return build_answer_correctness_policy(
        user_message=f"出{requested}道题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )


@pytest.mark.parametrize(
    "draft,requested",
    [
        # Exact count — pass.
        ("1. 题目\nA. 选项\nB. 选项", 1),
        ("1. 题\n2. 题\n3. 题", 3),
        ("1. 题", 1),
        # Q-marker and 第N题 marker.
        ("Q1. 题目", 1),
        ("第1题. 题目", 1),
        # 1) and 、 markers.
        ("1) 题目", 1),
        ("1、 题目\n2、 题目", 2),
    ],
)
def test_count_answer_exact_match_passes(draft: str, requested: int) -> None:
    """Answer with exactly `requested` top-level markers → no violation."""
    policy = _count_policy(requested)
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


@pytest.mark.parametrize(
    "draft,requested",
    [
        # Too many — retry.
        ("1. 题\n2. 题\n3. 题\n4. 题\n5. 题", 1),
        ("1. 题\n2. 题", 1),
        ("Q1. 题目\nQ2. 题目", 1),
        ("1) 题目\n2) 题目", 1),
        ("1、 题目\n2、 题目", 1),
        ("1. 题\n2. 题\n3. 题\n4. 题", 2),
        # Too few — retry (§9.5: exact count check, not just exceeded).
        ("1. 题", 3),
        ("1. 题\n2. 题", 3),
    ],
)
def test_count_answer_mismatch_triggers_violation(draft: str, requested: int) -> None:
    """Answer with wrong count (too many or too few) → explicit_count_mismatch."""
    policy = _count_policy(requested)
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert len(count_violations) == 1
    assert str(requested) in count_violations[0].detail


def test_count_answer_indented_subitems_excluded() -> None:
    """Indented sub-items (≥1 leading whitespace) are NOT counted as top-level."""
    policy = _count_policy(1)
    # 1 top-level + 2 indented sub-items → actual=1.
    draft = "1. 题目\n  1. 子问题\n  2. 子问题"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_indented_options_excluded() -> None:
    """A-D options (indented or column-0) are NOT counted as exercise items."""
    policy = _count_policy(1)
    draft = "1. 题\n  A. 选项\n  B. 选项"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_column_zero_options_excluded() -> None:
    """Column-0 A. is an option, not a top-level exercise item."""
    policy = _count_policy(1)
    draft = "1. 题\nA. 选项\nB. 选项"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_section_stripped() -> None:
    """Answer section (答案：/ Answer：/ 解析：/ etc.) and everything after
    is stripped before counting."""
    policy = _count_policy(1)
    drafts = [
        "1. 题目\nA. 选项\nB. 选项\n答案：B",
        "1. 题目\n解析：这是解析",
        "1. 题目\n参考答案：\n1. 答案步骤1\n2. 答案步骤2",
        "1. 题目\nAnswer: something",
        "1. 题目\nExplanation: something",
    ]
    for draft in drafts:
        violations = policy.evaluate_draft(draft_answer_text=draft)
        count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
        assert count_violations == [], f"failed for: {draft!r}"


def test_count_answer_indented_answer_marker_not_stripped() -> None:
    """Indented '答案：' is NOT a high-confidence section marker — it's part
    of the question. Counting continues."""
    policy = _count_policy(2)
    draft = "1. 题\n  答案：xxx\n2. 题"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_decimal_not_matched() -> None:
    """Decimals like '1.5' must not be counted as top-level markers."""
    policy = _count_policy(1)
    draft = "1.5 是一个小数"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []  # indeterminate → fail-open


def test_count_answer_version_not_matched() -> None:
    """Version numbers like 'v1.2.3' must not be counted."""
    policy = _count_policy(1)
    draft = "v1.2.3 是版本号"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_single_interrogative_counts_as_one() -> None:
    """§21.4 step 5: count==0 + exactly one ?/？ → actual=1, high."""
    policy = _count_policy(1)
    draft = "题目内容？"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_multiple_interrogatives_indeterminate() -> None:
    """§21.4 step 6: count==0 + multiple ? → indeterminate → fail-open."""
    policy = _count_policy(1)
    draft = "题目1？题目2？"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_no_marker_no_interrogative_indeterminate() -> None:
    """§21.4 step 6: count==0 + no ? → indeterminate → fail-open."""
    policy = _count_policy(1)
    draft = "这是一段普通文本，没有列表。"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_options_only_indeterminate() -> None:
    """§21.4: options-only (no markers, no ?) → indeterminate → fail-open."""
    policy = _count_policy(1)
    draft = "A. 选项\nB. 选项\nC. 选项\nD. 选项"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_empty_indeterminate() -> None:
    """Empty answer → indeterminate → fail-open (no count violation)."""
    policy = _count_policy(1)
    violations = policy.evaluate_draft(draft_answer_text="")
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_indeterminate_request_fail_open() -> None:
    """When request confidence is indeterminate ('几道题'), no count check."""
    policy = build_answer_correctness_policy(
        user_message="出几道题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    # Even with 5 top-level markers, indeterminate request → no violation.
    draft = "1. 题\n2. 题\n3. 题\n4. 题\n5. 题"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_answer_no_explicit_request_fail_open() -> None:
    """When kind=none, no count check at all."""
    policy = build_answer_correctness_policy(
        user_message="概括文章",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    draft = "1. 题\n2. 题\n3. 题"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert count_violations == []


def test_count_violation_detail_uses_frozen_template() -> None:
    """§12.2: detail must use the fixed template with only requested/actual
    interpolated — no other caller-supplied content."""
    policy = _count_policy(1)
    draft = "1. 题\n2. 题"  # actual=2, requested=1
    violations = policy.evaluate_draft(draft_answer_text=draft)
    count_violations = [v for v in violations if v.kind == "explicit_count_mismatch"]
    assert len(count_violations) == 1
    detail = count_violations[0].detail
    assert "1" in detail  # requested
    assert "2" in detail  # actual
    # No snippet / identity / draft content.
    assert "题" not in detail  # the draft text "题" must not appear


# ---------------------------------------------------------------------------
# 7. Dual violation stable sort
# ---------------------------------------------------------------------------


def test_dual_violation_stable_sort() -> None:
    """When both temporal and count violations occur, the returned tuple is
    sorted by kind for deterministic ordering."""
    # Strict question + count=1 + complete baseline + no years in chunks.
    policy = build_answer_correctness_policy(
        user_message="基于文章出一道选择题，只允许一题",
        model_visible_chunk_texts=("no year in chunk",),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is True
    assert policy.explicit_output.requested_count == 1

    # Draft has unsupported year (2025) AND too many items (2).
    draft = "文章报道了 2025 年的事件。\n1. 题\n2. 题"
    violations = policy.evaluate_draft(draft_answer_text=draft)

    kinds = [v.kind for v in violations]
    # Sorted alphabetically: explicit_count_mismatch < temporal_claim_unsupported
    assert kinds == sorted(kinds)
    assert "temporal_claim_unsupported" in kinds
    assert "explicit_count_mismatch" in kinds


def test_evaluate_draft_returns_tuple_not_list() -> None:
    """§21.1: evaluate_draft returns tuple[PolicyViolation, ...]."""
    policy = _count_policy(1)
    violations = policy.evaluate_draft(draft_answer_text="1. 题\n2. 题")
    assert isinstance(violations, tuple)
    for v in violations:
        assert isinstance(v, PolicyViolation)


def test_evaluate_draft_no_violations_returns_empty_tuple() -> None:
    """No violations → empty tuple (not None, not list)."""
    policy = build_answer_correctness_policy(
        user_message="概括文章",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    violations = policy.evaluate_draft(draft_answer_text="普通回答")
    assert violations == ()


# ---------------------------------------------------------------------------
# 8. Renderer — determinism, privacy, content (§21.1)
# ---------------------------------------------------------------------------


def test_render_prompt_block_deterministic() -> None:
    """Same policy → same render output, every call."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("文章发表于 2024 年。",),
        baseline_is_complete=True,
    )
    a = policy.render_prompt_block()
    b = policy.render_prompt_block()
    assert a == b


def test_render_prompt_block_does_not_leak_user_message() -> None:
    """Renderer must not echo the user's question text."""
    user_message = "这篇文章主要说了什么"
    policy = build_answer_correctness_policy(
        user_message=user_message,
        model_visible_chunk_texts=("文章发表于 2024 年。",),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    assert user_message not in rendered


def test_render_prompt_block_does_not_leak_chunk_body() -> None:
    """Renderer must not echo chunk body text (only extracted years may appear)."""
    unique_marker = "UNIQUE_CHUNK_MARKER_XYZ789"
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=(f"文章发表于 2024 年。{unique_marker}",),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    assert unique_marker not in rendered
    # Year 2024 may appear (it's in the allowset).
    assert "2024" in rendered


def test_render_prompt_block_does_not_leak_internal_field_names() -> None:
    """Renderer must not contain internal field names."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("2024 年",),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    forbidden_names = [
        "temporal_allowset",
        "explicit_output",
        "is_article_only_strict",
        "baseline_is_complete",
        "extraction_confidence",
        "requested_count",
        "kind",
        "PolicyViolation",
        "ExplicitOutputConstraint",
        "AnswerCorrectnessPolicy",
    ]
    for name in forbidden_names:
        assert name not in rendered, f"renderer leaked field name: {name!r}"


def test_render_prompt_block_does_not_leak_identity_fields() -> None:
    """Renderer must not contain envelope/record/identity fields."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("2024 年",),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    forbidden = [
        "envelope",
        "fingerprint",
        "record_id",
        "reading_record",
        "base_id",
        "generation",
        "user_id",
        "handle",
        "evh_",
    ]
    for name in forbidden:
        assert name not in rendered.lower(), f"renderer leaked: {name!r}"


def test_render_prompt_block_strict_complete_shows_temporal_constraint() -> None:
    """When guard enabled with a non-empty allowset, the rendered block
    mentions the years."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("2024 年", "2025 年"),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    assert "2024" in rendered
    assert "2025" in rendered


def test_render_prompt_block_strict_complete_empty_allowset_warns() -> None:
    """When guard enabled with empty allowset, the rendered block says the
    article provides no years."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("no years here",),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    # Mention that no years are available (wording is implementation-defined
    # but the constraint must be visible).
    assert "year" in rendered.lower() or "日期" in rendered or "时间" in rendered


def test_render_prompt_block_partial_baseline_no_temporal_constraint() -> None:
    """When baseline is partial, temporal guard is disabled — the rendered
    block must NOT enumerate years (even if some are in the allowset)."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("2024 年",),
        baseline_is_complete=False,
    )
    rendered = policy.render_prompt_block()
    # "2024" may appear in allowset but the renderer should not present it
    # as a hard constraint in partial mode. We accept either: (a) "2024"
    # does not appear, or (b) the block says temporal restriction is off.
    # For strictness, we require the renderer NOT to present a hard
    # "do not output other years" instruction in partial mode.
    assert "do not output any other specific year" not in rendered.lower()


def test_render_prompt_block_exercise_count_high_confidence() -> None:
    """When count is high confidence, the rendered block names the exact
    requested count."""
    policy = build_answer_correctness_policy(
        user_message="基于文章出一道选择题，只允许一题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    assert "1" in rendered


def test_render_prompt_block_no_count_constraint_when_indeterminate() -> None:
    """When count is indeterminate, the rendered block must not impose a
    specific count."""
    policy = build_answer_correctness_policy(
        user_message="出几道题",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    rendered = policy.render_prompt_block()
    # Must not contain "exactly N" instruction.
    assert "exactly 1" not in rendered.lower()
    assert "exactly 2" not in rendered.lower()
    assert "exactly 3" not in rendered.lower()


# ---------------------------------------------------------------------------
# 9. Frozen / slots — write-once semantics
# ---------------------------------------------------------------------------


def test_policy_is_frozen_cannot_reassign() -> None:
    """Frozen dataclass raises on field reassignment."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("2024 年",),
        baseline_is_complete=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.is_article_only_strict = False  # type: ignore[misc]


def test_policy_has_slots_no_dict() -> None:
    """Slots dataclass has no __dict__ (no arbitrary attribute setting)."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=(),
        baseline_is_complete=True,
    )
    assert not hasattr(policy, "__dict__")


def test_explicit_output_constraint_is_frozen_cannot_reassign() -> None:
    explicit = ExplicitOutputConstraint(
        kind="exercise_items",
        requested_count=1,
        extraction_confidence="high",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        explicit.requested_count = 2  # type: ignore[misc]


def test_policy_violation_is_frozen_cannot_reassign() -> None:
    v = PolicyViolation(
        kind="temporal_claim_unsupported",
        detail="some detail",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.detail = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. Import boundary — leaf module (§15.1)
# ---------------------------------------------------------------------------


def test_import_boundary_no_internal_dependencies() -> None:
    """The policy module must NOT import from agent / runtime / runtime_deps /
    grounding_validator / baseline_context / finalizer / evidence* /
    pydantic_ai. It is a leaf module with only stdlib dependencies."""
    policy_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "services"
        / "reader_record_ask"
        / "answer_correctness_policy.py"
    )
    source = policy_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_prefixes = (
        "app.services.reader_record_ask.",
        "pydantic_ai",
        "pydantic",
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes), (
                    f"forbidden import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith(forbidden_prefixes), f"forbidden import: {module}"


def test_module_does_not_import_model_context_chunk() -> None:
    """Specifically: ModelContextChunk must not be imported (§21.1)."""
    from app.services.reader_record_ask import answer_correctness_policy as mod

    # The module must not reference ModelContextChunk at all.
    source = inspect_source(mod)
    assert "ModelContextChunk" not in source


def inspect_source(mod: object) -> str:
    import inspect

    return inspect.getsource(mod)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 11. §13 counter-examples — normal mixed answers not falsely blocked
# ---------------------------------------------------------------------------


def test_counter_example_1_external_year_comparison_passes() -> None:
    """§13 反例 1: article summary + external year comparison + article
    citation must not be blocked (non-strict → fail-open)."""
    policy = build_answer_correctness_policy(
        user_message="概括这篇文章。和 2024 年的同类事件比较。",
        model_visible_chunk_texts=("文章发表于 2023 年 5 月。",),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is False  # not exact match
    draft = "文章主要报道了 2023 年 5 月的一次野火事件。与 2024 年的类似事件相比，这次规模较小。"
    violations = policy.evaluate_draft(draft_answer_text=draft)
    assert violations == ()


def test_counter_example_4_pure_summary_with_article_year_passes() -> None:
    """§13 反例 4: pure article summary with article-visible year → pass."""
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("文章报道了 2020 年的事件。",),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is True
    assert "2020" in policy.temporal_allowset
    violations = policy.evaluate_draft(draft_answer_text="文章报道了 2020 年的事件。")
    assert violations == ()


def test_counter_example_5_indeterminate_scope_external_year_passes() -> None:
    """§13 反例 5: non-strict scope + external year in answer → pass (fail-open).
    Evaluator catches unsupported temporal; deterministic validator does not."""
    policy = build_answer_correctness_policy(
        user_message="概括文章。",
        model_visible_chunk_texts=("no years",),
        baseline_is_complete=True,
    )
    assert policy.is_article_only_strict is False
    violations = policy.evaluate_draft(
        draft_answer_text="文章报道了一次野火事件。这次事件发生在 2025 年。"
    )
    assert violations == ()


# ---------------------------------------------------------------------------
# 12. Builder does not depend on excluded inputs (§21.1 frozen conclusions)
# ---------------------------------------------------------------------------


def test_builder_does_not_accept_model_context_chunk_argument() -> None:
    """§21.1: builder takes model_visible_chunk_texts: tuple[str, ...],
    NOT ModelContextChunk. Verify by signature."""
    import inspect

    sig = inspect.signature(build_answer_correctness_policy)
    assert "model_visible_chunk_texts" in sig.parameters
    assert "model_context_chunks" not in sig.parameters
    assert "client_hinted_constraint" not in sig.parameters
    assert "user_question_year_tokens" not in sig.parameters


def test_evaluate_draft_does_not_accept_cited_handle_ids() -> None:
    """§21.1: evaluate_draft takes only draft_answer_text (no cited_handle_ids)."""
    import inspect

    sig = inspect.signature(AnswerCorrectnessPolicy.evaluate_draft)
    params = sig.parameters
    # self + draft_answer_text only.
    non_self = {n: p for n, p in params.items() if n != "self"}
    assert set(non_self) == {"draft_answer_text"}
    for name, p in non_self.items():
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, name


def test_answer_correctness_policy_has_no_user_question_year_tokens_field() -> None:
    """§21.1: user_question_year_tokens field is deleted."""
    fields = {f.name for f in dataclasses.fields(AnswerCorrectnessPolicy)}
    assert "user_question_year_tokens" not in fields


def test_explicit_output_kind_has_only_two_values() -> None:
    """§21.1: ExplicitOutputKind = Literal['exercise_items', 'none'] (2 kinds,
    not 4 — sentences / list_items are deleted)."""
    # Build both kinds and verify they're constructible.
    a = ExplicitOutputConstraint(
        kind="exercise_items", requested_count=1, extraction_confidence="high"
    )
    b = ExplicitOutputConstraint(
        kind="none", requested_count=None, extraction_confidence="indeterminate"
    )
    assert a.kind == "exercise_items"
    assert b.kind == "none"


def test_policy_violation_kind_has_only_two_values() -> None:
    """§21.1: PolicyViolation.kind = Literal['temporal_claim_unsupported',
    'explicit_count_mismatch']. No external_temporal_with_article_handle,
    no knowledge_scope_violation."""
    v1 = PolicyViolation(kind="temporal_claim_unsupported", detail="d")
    v2 = PolicyViolation(kind="explicit_count_mismatch", detail="d")
    assert v1.kind == "temporal_claim_unsupported"
    assert v2.kind == "explicit_count_mismatch"
