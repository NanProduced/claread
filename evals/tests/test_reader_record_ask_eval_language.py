from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.language_consistency import (
    evaluate_language_consistency,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)


def _make_case(lang: str = "zh") -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-language",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskExpected(answer_language=lang),  # type: ignore[arg-type]
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-language",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


def test_positive_zh_all_chinese_with_proper_noun() -> None:
    case = _make_case("zh")
    artifact = _make_artifact("这篇文章引用了 BBC 的报道，讨论了城市绿化。")
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_positive_zh_scattered_english_words_tolerated() -> None:
    case = _make_case("zh")
    artifact = _make_artifact("文章讨论了 AI 在 app 中的应用。")
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is True


def test_negative_zh_whole_sentence_english() -> None:
    case = _make_case("zh")
    artifact = _make_artifact(
        "SYNTHETIC_TEST_SENTENCE_USED_ONLY_FOR_LANGUAGE_RATIO_DETECTION."
    )
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "english_ratio" in result.details


def test_negative_zh_mixed_with_whole_english_sentence() -> None:
    case = _make_case("zh")
    artifact = _make_artifact(
        "文章主要讨论环境问题。"
        "The author presents several arguments about climate change."
        "结论是值得关注的。"
    )
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is False
    assert result.severity == "medium"


def test_non_zh_language_skipped() -> None:
    case = _make_case("en")
    artifact = _make_artifact("The article discusses climate change.")
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is True
    assert "skip" in result.details


def test_proper_noun_whitelist_prevents_false_positive() -> None:
    case = _make_case("zh")
    # A sentence that is mostly "Thunder Bay" + Chinese should pass
    # because Thunder Bay is whitelisted.
    artifact = _make_artifact("Thunder Bay 是文章提到的城市之一。")
    result = evaluate_language_consistency(case, artifact)
    assert result.passed is True
