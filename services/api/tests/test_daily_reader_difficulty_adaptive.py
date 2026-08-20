"""A-2: difficulty-adaptive parsing — prompt build layer + refined grade.

Each of the five adaptive parameters must produce DIFFERENT prompt
instructions per article difficulty, sourced from
``prompts/agents/daily_*.yaml`` (no hardcoding). Plus the
``refined_difficulty`` whole-text re-grade override logic.
"""

from __future__ import annotations

from app.agents.daily_footer_agent import DailyFooterAgentDeps, build_daily_footer_prompt
from app.agents.daily_interpretation_agent import (
    DailyInterpretationAgentDeps,
    build_daily_interpretation_prompt,
)
from app.agents.daily_review_agent import DailyReviewAgentDeps, build_daily_review_prompt
from app.agents.daily_vocab_agent import DailyVocabAgentDeps, build_daily_vocab_prompt
from app.schemas.internal.daily_drafts import ParagraphNotesDraft
from app.services.daily_reader.workflow import _effective_difficulty
from app.services.prompting.daily_prompt_strategy import (
    normalize_daily_difficulty,
    resolve_refined_difficulty,
)

PARAGRAPHS = [
    {"paragraph_id": "p_0", "text": "The government announced sweeping reforms."},
]


def _vocab_prompt(difficulty: str) -> str:
    return build_daily_vocab_prompt(
        DailyVocabAgentDeps(paragraphs=PARAGRAPHS, difficulty=difficulty)
    )


def _footer_prompt(difficulty: str) -> str:
    return build_daily_footer_prompt(
        DailyFooterAgentDeps(full_text="text", title="T", difficulty=difficulty)
    )


def _interpretation_prompt(difficulty: str) -> str:
    return build_daily_interpretation_prompt(
        DailyInterpretationAgentDeps(full_text="text", title="T", difficulty=difficulty)
    )


class TestDifficultySectionInjection:
    def test_vocab_prompt_contains_difficulty_profile_section(self):
        prompt = _vocab_prompt("C1")
        assert "<difficulty_profile>" in prompt
        assert "本文难度：C1" in prompt

    def test_different_difficulty_produces_different_instructions(self):
        assert _vocab_prompt("B1") != _vocab_prompt("C1")
        assert _footer_prompt("B1") != _footer_prompt("C1")
        assert _interpretation_prompt("B1") != _interpretation_prompt("C1")

    def test_unknown_difficulty_falls_back_to_b2(self):
        assert normalize_daily_difficulty("weird") == "B2"
        assert normalize_daily_difficulty("") == "B2"
        assert "本文难度：B2" in _vocab_prompt("")


class TestParam1VocabFloor:
    """选词下限：C1 不标 B2 以下词，B2 不标 A 级词。"""

    def test_c1_floor_bans_below_b2(self):
        assert "不标注 B2 以下的词" in _vocab_prompt("C1")

    def test_b2_floor_bans_a_level(self):
        assert "不标注 A 级" in _vocab_prompt("B2")

    def test_b1_floor_keeps_b1_plus(self):
        prompt = _vocab_prompt("B1")
        assert "B1 及以上" in prompt
        assert "不标注 B2 以下的词" not in prompt


class TestParam2HighlightDensity:
    """高亮密度：低难度密而浅，高难度稀而深。"""

    def test_low_difficulty_dense_and_shallow(self):
        prompt = _vocab_prompt("B1")
        assert "密而浅" in prompt
        assert "3-4 个标注" in prompt
        assert "简洁的一句话中文释义" in prompt

    def test_high_difficulty_sparse_and_deep(self):
        prompt = _vocab_prompt("C1")
        assert "稀而深" in prompt
        assert "1-2 个标注" in prompt
        assert "用法说明" in prompt


class TestParam3SentenceComplexityThreshold:
    """长难句门槛：按句法复杂度选句，C1 必须选真难句。"""

    def test_c1_requires_genuinely_hard_sentences(self):
        prompt = _interpretation_prompt("C1")
        assert "必须选真正困难的句子" in prompt
        assert "多从句嵌套" in prompt
        assert "一律不合格" in prompt

    def test_b1_threshold_is_lower(self):
        prompt = _interpretation_prompt("B1")
        assert "明确结构学习点" in prompt
        assert "必须选真正困难的句子" not in prompt


class TestParam4ChineseScaffolding:
    """中文脚手架：B1 及以下配中文引导，C1 全英文。"""

    def test_b1_footer_guides_in_plain_chinese(self):
        prompt = _footer_prompt("B1")
        assert "平实的中文引导" in prompt
        assert "中文阅读提示" in prompt

    def test_c1_footer_guides_in_english(self):
        prompt = _footer_prompt("C1")
        assert "用英文撰写" in prompt
        assert "不配中文脚手架" in prompt

    def test_b1_discussion_questions_get_chinese_hints(self):
        prompt = _interpretation_prompt("B1")
        assert "中文思路提示" in prompt

    def test_c1_discussion_questions_pure_english(self):
        prompt = _interpretation_prompt("C1")
        assert "全英文，不附任何中文引导" in prompt


class TestParam5WritingMoveDepth:
    """写作借鉴深度：低难度句式模板，高难度修辞手法分析。"""

    def test_low_difficulty_reusable_templates_required(self):
        prompt = _interpretation_prompt("B1")
        assert "句式模板" in prompt
        assert "reusable_pattern 必须" in prompt

    def test_high_difficulty_rhetoric_analysis(self):
        prompt = _interpretation_prompt("C1")
        assert "修辞手法分析" in prompt


class TestReviewDifficultyDimension:
    def test_review_prompt_carries_difficulty_and_new_dimensions(self):
        prompt = build_daily_review_prompt(
            DailyReviewAgentDeps(
                original_text="text",
                highlights_json="[]",
                paragraph_notes_json="{}",
                takeaways_json="{}",
                difficulty="C1",
            )
        )
        assert "<article_difficulty>" in prompt
        assert "文章难度：C1" in prompt
        assert "vocab_difficulty_floor" in prompt
        assert "sentence_complexity" in prompt


class TestRefinedDifficulty:
    def test_paragraph_notes_schema_accepts_refined_difficulty(self):
        draft = ParagraphNotesDraft(
            article_summary="概述",
            reading_focus=[],
            notes=[],
            refined_difficulty="C1",
        )
        assert draft.model_dump()["refined_difficulty"] == "C1"

    def test_resolve_refined_difficulty_validation(self):
        assert resolve_refined_difficulty({"refined_difficulty": "C1"}) == "C1"
        assert resolve_refined_difficulty({"refined_difficulty": " c1 "}) == "C1"
        assert resolve_refined_difficulty({"refined_difficulty": "X9"}) is None
        assert resolve_refined_difficulty({"refined_difficulty": ""}) is None
        assert resolve_refined_difficulty({}) is None
        assert resolve_refined_difficulty(None) is None

    def test_effective_difficulty_prefers_refined(self):
        state = {
            "difficulty": "B2",
            "paragraph_notes_json": {"refined_difficulty": "C1"},
        }
        assert _effective_difficulty(state) == "C1"

    def test_effective_difficulty_falls_back_to_scorer_grade(self):
        state = {"difficulty": "B1", "paragraph_notes_json": {}}
        assert _effective_difficulty(state) == "B1"
        state = {"difficulty": "B1", "paragraph_notes_json": {"refined_difficulty": "nonsense"}}
        assert _effective_difficulty(state) == "B1"
