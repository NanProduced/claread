from app.schemas.internal.analysis import SentenceTranslation
from app.schemas.internal.normalized import (
    CanonicalSpan,
    NormalizedAnnotationResult,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.projection import project_normalized_to_render_scene
from app.services.analysis.preprocess.input_preparation import prepare_input

# ── Normalized projection tests ─────────────────────────────────────


def _make_normalized_result(
    normalized_annotations: list[object],
    sentence_translations: list[SentenceTranslation] | None = None,
) -> NormalizedAnnotationResult:
    return NormalizedAnnotationResult(
        annotations=[],
        normalized_annotations=normalized_annotations,  # type: ignore[arg-type]
        sentence_translations=sentence_translations or [],
    )


def _plan_and_request():
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    return plan, {
        "source_type": "user_input",
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "profile_id": plan.prompt_profile,
        "request_id": "test-norm-001",
    }


def test_normalized_vocab_highlight_projects_range_anchor() -> None:
    prepared = prepare_input("The results prompted the team.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("prompted"),
                        end=rt.find("prompted") + len("prompted"),
                        text="prompted",
                        resolution_kind="exact",
                    )
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="结果促使了团队。")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.annotation_type == "vocab_highlight"
    assert mark.anchor.kind == "range"
    assert mark.anchor.range.text == "prompted"
    assert mark.anchor.range.start >= 0
    assert mark.anchor.range.end > mark.anchor.range.start
    assert mark.lookup_text == "prompted"
    assert mark.lookup_kind == "word"


def test_normalized_phrase_gloss_multi_span_projects_multi_range() -> None:
    prepared = prepare_input("Turn their passion into a stable income.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedPhraseGloss(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("Turn"),
                        end=rt.find("Turn") + len("Turn"),
                        text="Turn",
                        role="verb",
                        resolution_kind="exact",
                    ),
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("into"),
                        end=rt.find("into") + len("into"),
                        text="into",
                        role="preposition",
                        resolution_kind="exact",
                    ),
                ],
                label="turn ... into",
                phrase_type="phrasal_verb",
                zh="把……变成……",
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="把热情变成稳定收入。")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.annotation_type == "phrase_gloss"
    assert mark.anchor.kind == "multi_range"
    assert len(mark.anchor.ranges) == 2
    assert mark.anchor.ranges[0].text == "Turn"
    assert mark.anchor.ranges[0].role == "verb"
    assert mark.anchor.ranges[1].text == "into"
    assert mark.anchor.ranges[1].role == "preposition"
    assert mark.lookup_text == "turn ... into"


def test_normalized_context_gloss_display_as_lookup_text() -> None:
    prepared = prepare_input("The results prompted the team to rethink their approach.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedContextGloss(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("prompted"),
                        end=rt.find("prompted") + len("prompted"),
                        text="prompted",
                        resolution_kind="exact",
                    )
                ],
                display="prompt sb to do sth",
                gloss="促使某人做某事",
                reason="词典义不足以表达语境含义",
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="结果促使团队重新思考。")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.annotation_type == "context_gloss"
    assert mark.lookup_text == "prompt sb to do sth"
    assert mark.anchor.kind == "range"
    assert mark.anchor.range.text == "prompted"


def test_normalized_grammar_note_projects_entry_and_inline_mark() -> None:
    prepared = prepare_input("Not only did he win, but he also broke the record.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedGrammarNote(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("Not only did"),
                        end=rt.find("Not only did") + len("Not only did"),
                        text="Not only did",
                        role="inversion_trigger",
                        resolution_kind="exact",
                    ),
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("but he also"),
                        end=rt.find("but he also") + len("but he also"),
                        text="but he also",
                        role="paired_structure",
                        resolution_kind="exact",
                    ),
                ],
                grammar_point="not only 句首倒装",
                note_zh="Not only 位于句首时使用部分倒装。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="他不仅赢了，还打破了记录。")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    # Both sentence entry and inline mark
    assert len(outcome.result.sentence_entries) == 1
    assert outcome.result.sentence_entries[0].entry_type == "grammar_note"
    assert outcome.result.sentence_entries[0].label == "not only 句首倒装"

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.annotation_type == "grammar_note"
    assert mark.anchor.kind == "multi_range"
    assert len(mark.anchor.ranges) == 2
    assert mark.anchor.ranges[0].text == "Not only did"
    assert mark.anchor.ranges[0].role == "inversion_trigger"
    assert mark.anchor.ranges[1].text == "but he also"
    assert mark.anchor.ranges[1].role == "paired_structure"


def test_normalized_sentence_analysis_projects_entry_only() -> None:
    prepared = prepare_input("Higher gas prices result in farmers being forced to pay more.")
    plan, kwargs = _plan_and_request()

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedSentenceAnalysis(
                sentence_id="s1",
                label="主句加 result in 压缩结构",
                analysis_zh="先抓主句，再看后面的结果结构。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="更高的油价导致农民被迫支付更多。")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 0
    assert len(outcome.result.sentence_entries) == 1
    assert outcome.result.sentence_entries[0].entry_type == "sentence_analysis"


def test_normalized_range_validation_failure_skips_mark() -> None:
    """range 校验失败时 skip mark，warnings 包含 canonical_range_validation_failed。"""
    prepared = prepare_input("The results prompted the team.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    # Intentionally wrong text that won't match render_text at the given offset
    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("prompted"),
                        end=rt.find("prompted") + len("prompted"),
                        text="WRONGTEXT",  # won't match
                        resolution_kind="exact",
                    )
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="翻译")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    # Mark should be skipped (fail-closed)
    assert len(outcome.result.inline_marks) == 0
    # Warning should have canonical_range_validation_failed
    assert any(
        w.get("code") == "canonical_range_validation_failed"
        for w in outcome.warnings
    )


def test_normalized_multi_range_validation_failure_skips_mark() -> None:
    """multi_range 任一 part 校验失败时 skip 整条 mark。

    warnings 包含 canonical_range_validation_failed。
    """
    prepared = prepare_input("The results prompted the team to reconsider.")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    # Create a ContextGloss with multi_range where one part has wrong text
    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedContextGloss(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("prompted"),
                        end=rt.find("prompted") + len("prompted"),
                        text="prompted",
                        resolution_kind="exact",
                    ),
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("reconsider"),
                        end=rt.find("reconsider") + len("reconsider"),
                        text="WRONGTEXT",  # won't match
                        resolution_kind="exact",
                    ),
                ],
                display="prompt sb to do sth",
                gloss="促使某人做某事",
                reason="词典义不足以表达语境含义",
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="翻译")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    # Entire mark should be skipped because one part failed validation
    assert len(outcome.result.inline_marks) == 0
    assert any(
        w.get("code") == "canonical_range_validation_failed"
        for w in outcome.warnings
    )


def test_normalized_chinese_text_range_anchor() -> None:
    """UTF-16 range conversion works for Chinese text."""
    prepared = prepare_input("Hello你好世界")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("你好"),
                        end=rt.find("你好") + len("你好"),
                        text="你好",
                        resolution_kind="exact",
                    )
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="你好世界")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.anchor.kind == "range"
    assert mark.anchor.range.text == "你好"


def test_normalized_emoji_text_range_anchor() -> None:
    """UTF-16 range conversion works for emoji (surrogate pairs)."""
    prepared = prepare_input("Say😀hi")
    plan, kwargs = _plan_and_request()
    rt = prepared.render_text

    result = _make_normalized_result(
        normalized_annotations=[
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=rt.find("😀"),
                        end=rt.find("😀") + len("😀"),
                        text="😀",
                        resolution_kind="exact",
                    )
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="说😀嗨")
        ],
    )

    outcome = project_normalized_to_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        **kwargs,
    )

    assert len(outcome.result.inline_marks) == 1
    mark = outcome.result.inline_marks[0]
    assert mark.anchor.kind == "range"
    assert mark.anchor.range.text == "😀"
    # Emoji takes 2 UTF-16 units
    assert mark.anchor.range.end - mark.anchor.range.start == 2
