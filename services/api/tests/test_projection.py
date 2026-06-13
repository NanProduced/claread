from app.schemas.internal.analysis import (
    AnnotationOutput,
    Chunk,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    SentenceTranslation,
    SpanRef,
    VocabHighlight,
)
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
from app.services.analysis.postprocess.projection import (
    project_normalized_to_render_scene,
    project_to_render_scene,
)
from app.services.analysis.preprocess.input_preparation import prepare_input


def test_vocab_highlight_projects_to_inline_mark() -> None:
    prepared = prepare_input("The implementation of sustainable practices is challenging.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[VocabHighlight(sentence_id="s1", text="implementation")],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="可持续实践的实施是具有挑战性的。",
            )
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-001",
    )
    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "vocab_highlight"
    assert inline_mark.visual_tone == "vocab"
    assert inline_mark.render_type == "background"
    assert inline_mark.clickable is True


def test_grammar_note_projects_to_inline_mark_and_entry() -> None:
    prepared = prepare_input("So fundamental are these challenges that traditional methods fail.")
    plan = build_goal_execution_plan("exam", "gaokao")
    output = AnnotationOutput(
        annotations=[
            GrammarNote(
                sentence_id="s1",
                spans=[
                    SpanRef(text="So", role="trigger"),
                    SpanRef(text="fundamental", role="focus"),
                    SpanRef(text="that", role="conjunction"),
                ],
                label="结果状语从句（半倒装）",
                note_zh="so...that 结构，主语较长时使用部分倒装。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="这些挑战如此根本，以至于传统方法失败了。",
            )
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="exam",
        reading_variant="gaokao",
        profile_id=plan.prompt_profile,
        request_id="test-005",
    )
    assert len(outcome.result.inline_marks) == 1
    assert len(outcome.result.sentence_entries) == 1
    assert outcome.result.sentence_entries[0].entry_type == "grammar_note"
    assert outcome.result.sentence_entries[0].content == "so...that 结构，主语较长时使用部分倒装。"


def test_grammar_note_with_broad_anchor_still_projects_inline_mark() -> None:
    sentence = (
        "It wasn't until I began to research this widely accepted career advice "
        "that I understood how problematic it really was."
    )
    prepared = prepare_input(sentence)
    plan = build_goal_execution_plan("exam", "gaokao")
    output = AnnotationOutput(
        annotations=[
            GrammarNote(
                sentence_id="s1",
                spans=[SpanRef(text=sentence)],
                label="强调句型",
                note_zh="这是 not until 强调句型。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="直到开始研究这条建议，我才意识到它的问题。",
            )
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="exam",
        reading_variant="gaokao",
        profile_id=plan.prompt_profile,
        request_id="test-grammar-broad-anchor",
    )

    assert len(outcome.result.inline_marks) == 1
    assert outcome.result.inline_marks[0].annotation_type == "grammar_note"
    assert len(outcome.result.sentence_entries) == 1
    assert outcome.result.sentence_entries[0].entry_type == "grammar_note"
    assert outcome.warnings == []


def test_sentence_analysis_projects_to_entry_only() -> None:
    prepared = prepare_input(
        "They recognize that sustainable success requires "
        "a fundamental rethinking of core business models."
    )
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            SentenceAnalysis(
                sentence_id="s1",
                label="主宾从句",
                analysis_zh="本句主句为 They recognize，后接 that 引导的宾语从句。",
                chunks=[
                    Chunk(order=1, label="主语", text="They"),
                    Chunk(order=2, label="谓语", text="recognize"),
                    Chunk(
                        order=3,
                        label="that 宾语从句",
                        text="that sustainable success requires a fundamental rethinking",
                    ),
                    Chunk(order=4, label="of 介词短语", text="of core business models"),
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="他们认识到，可持续的成功需要对核心商业模式的根本性反思。",
            )
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-006",
    )
    assert len(outcome.result.inline_marks) == 0
    assert len(outcome.result.sentence_entries) == 1
    assert outcome.result.sentence_entries[0].entry_type == "sentence_analysis"
    assert (
        outcome.result.sentence_entries[0].analysis_text
        == "本句主句为 They recognize，后接 that 引导的宾语从句。"
    )
    assert outcome.result.sentence_entries[0].chunks == [
        {"order": 1, "label": "主语", "text": "They", "occurrence": None},
        {"order": 2, "label": "谓语", "text": "recognize", "occurrence": None},
        {
            "order": 3,
            "label": "that 宾语从句",
            "text": "that sustainable success requires a fundamental rethinking",
            "occurrence": None,
        },
        {"order": 4, "label": "of 介词短语", "text": "of core business models", "occurrence": None},
    ]
    assert (
        "本句主句为 They recognize，后接 that 引导的宾语从句。"
        in outcome.result.sentence_entries[0].content
    )
    assert "**1. 主语**" in outcome.result.sentence_entries[0].content


def test_mixed_annotations_project_correctly() -> None:
    prepared = prepare_input(
        "The implementation of sustainable practices requires fundamental rethinking. "
        "This concept has become a buzzword."
    )
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            VocabHighlight(sentence_id="s1", text="implementation"),
            PhraseGloss(sentence_id="s2", text="buzzword", phrase_type="compound", zh="流行术语"),
            ContextGloss(
                sentence_id="s1",
                text="requires",
                gloss="这里表示\u201c需要进行\u201d",
                reason="句中强调的是实现该动作的要求",
            ),
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="可持续发展实践的实施需要根本性的反思。",
            ),
            SentenceTranslation(
                sentence_id="s2",
                translation_zh="这个概念已经变成了一个流行术语。",
            ),
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-007",
    )
    assert len(outcome.result.inline_marks) == 3


def test_phrase_gloss_schematic_anchor_projects_to_multi_text() -> None:
    prepared = prepare_input("People often refer to this pattern as a shortcut.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            PhraseGloss(
                sentence_id="s1",
                text="refer to ... as",
                phrase_type="collocation",
                zh="把……称作……",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="人们常把这种模式称作捷径。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-phrase-multi",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "phrase_gloss"
    assert inline_mark.lookup_text == "refer to ... as"
    assert inline_mark.anchor.kind == "multi_text"
    assert [part.anchor_text for part in inline_mark.anchor.parts] == ["refer to", "as"]


def test_phrase_gloss_single_explicit_span_projects_to_text_anchor() -> None:
    prepared = prepare_input("They settled down in the village.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            PhraseGloss(
                sentence_id="s1",
                text="settled down",
                spans=[SpanRef(text="settled down")],
                phrase_type="phrasal_verb",
                zh="安顿下来",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="他们在村里安顿下来。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-phrase-single-span",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "phrase_gloss"
    assert inline_mark.lookup_text == "settled down"
    assert inline_mark.anchor.kind == "text"
    assert inline_mark.anchor.anchor_text == "settled down"


def test_phrase_gloss_explicit_spans_project_to_multi_text() -> None:
    prepared = prepare_input("People can turn their passion into progress.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            PhraseGloss(
                sentence_id="s1",
                text="turn ... into",
                spans=[SpanRef(text="turn"), SpanRef(text="into")],
                phrase_type="phrasal_verb",
                zh="把……变成……",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="人们可以把热情转化为进步。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-phrase-explicit-multi",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "phrase_gloss"
    assert inline_mark.lookup_text == "turn ... into"
    assert inline_mark.anchor.kind == "multi_text"
    assert [part.anchor_text for part in inline_mark.anchor.parts] == ["turn", "into"]


def test_context_gloss_schematic_anchor_projects_to_multi_text_with_phrase_lookup() -> None:
    prepared = prepare_input("They apply the rule to unfamiliar cases.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            ContextGloss(
                sentence_id="s1",
                text="apply ... to",
                gloss="把……应用到……",
                reason="这里强调把规则迁移到新情境中的用法。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="他们把这条规则应用到不熟悉的案例中。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-context-multi",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "context_gloss"
    assert inline_mark.lookup_text == "apply ... to"
    assert inline_mark.lookup_kind == "phrase"
    assert inline_mark.anchor.kind == "multi_text"
    assert [part.anchor_text for part in inline_mark.anchor.parts] == ["apply", "to"]


def test_context_gloss_single_token_keeps_word_lookup() -> None:
    prepared = prepare_input("The proposal requires careful review.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[
            ContextGloss(
                sentence_id="s1",
                text="requires",
                gloss="这里表示“需要”",
                reason="强调后面动作是必要条件。",
            )
        ],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="这项提案需要仔细审查。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-context-word",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "context_gloss"
    assert inline_mark.lookup_text == "requires"
    assert inline_mark.lookup_kind == "word"
    assert inline_mark.anchor.kind == "text"


def test_missing_translation_adds_warning() -> None:
    prepared = prepare_input("First sentence. Second sentence.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[],
        sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="第一句。")],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-008",
    )
    assert any(
        warning.get("code") == "translation_coverage_incomplete"
        for warning in outcome.warnings
    )


def test_context_gloss_with_display_and_spans_projects_correctly() -> None:
    """DraftContextGloss(display="prompt sb to do sth", anchor_quotes=["prompt", "to rethink"])
    经过 draft_to_annotation → normalize → projection 后，
    lookup_text 为 display，anchor 为 multi_text，parts 为两个真实原文片段。"""
    from app.schemas.internal.drafts import (
        AnchorQuote,
        DraftContextGloss,
        draft_to_annotation,
    )

    prepared = prepare_input("The results prompted the team to rethink their approach.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    draft = DraftContextGloss(
        sentence_id="s1",
        display="prompt sb to do sth",
        anchor_quotes=[
            AnchorQuote(text="prompted"),
            AnchorQuote(text="to rethink"),
        ],
        gloss="促使某人做某事",
        reason='词典义"提示"不足以表达"促使/推动"的语境含义。',
    )
    annotation = draft_to_annotation(draft)

    output = AnnotationOutput(
        annotations=[annotation],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="结果促使团队重新思考他们的方法。",
            )
        ],
    )

    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-ctx-display-spans",
    )

    assert len(outcome.result.inline_marks) == 1
    inline_mark = outcome.result.inline_marks[0]
    assert inline_mark.annotation_type == "context_gloss"
    assert inline_mark.lookup_text == "prompt sb to do sth"
    assert inline_mark.lookup_kind == "phrase"
    assert inline_mark.anchor.kind == "multi_text"
    assert [part.anchor_text for part in inline_mark.anchor.parts] == [
        "prompted",
        "to rethink",
    ]


# ── Phase 2.4A: Normalized projection tests ──────────────────────────


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


def test_old_projection_still_works() -> None:
    """旧 AnnotationOutput projection 测试仍通过。"""
    prepared = prepare_input("The implementation of sustainable practices is challenging.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[VocabHighlight(sentence_id="s1", text="implementation")],
        sentence_translations=[
            SentenceTranslation(
                sentence_id="s1",
                translation_zh="可持续实践的实施是具有挑战性的。",
            )
        ],
    )
    outcome = project_to_render_scene(
        annotation_output=output,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="test-old-compat",
    )
    assert len(outcome.result.inline_marks) == 1
    assert outcome.result.inline_marks[0].anchor.kind == "text"
