from app.schemas.internal.analysis import (
    Chunk,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    SentenceTranslation,
    SpanRef,
    AnnotationOutput,
    VocabHighlight,
)
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.postprocess.projection import project_to_render_scene
from app.services.analysis.planning.goal_planner import build_goal_execution_plan


def test_vocab_highlight_projects_to_inline_mark() -> None:
    prepared = prepare_input("The implementation of sustainable practices is challenging.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")
    output = AnnotationOutput(
        annotations=[VocabHighlight(sentence_id="s1", text="implementation")],
        sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="可持续实践的实施是具有挑战性的。")],
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
                spans=[SpanRef(text="So", role="trigger"), SpanRef(text="fundamental", role="focus"), SpanRef(text="that", role="conjunction")],
                label="结果状语从句（半倒装）",
                note_zh="so...that 结构，主语较长时使用部分倒装。",
            )
        ],
        sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="这些挑战如此根本，以至于传统方法失败了。")],
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


def test_sentence_analysis_projects_to_entry_only() -> None:
    prepared = prepare_input("They recognize that sustainable success requires a fundamental rethinking of core business models.")
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
                    Chunk(order=3, label="that 宾语从句", text="that sustainable success requires a fundamental rethinking"),
                    Chunk(order=4, label="of 介词短语", text="of core business models"),
                ],
            )
        ],
        sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="他们认识到，可持续的成功需要对核心商业模式的根本性反思。")],
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
    assert "本句主句为 They recognize，后接 that 引导的宾语从句。" in outcome.result.sentence_entries[0].content
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
            ContextGloss(sentence_id="s1", text="requires", gloss="这里表示“需要进行”", reason="句中强调的是实现该动作的要求"),
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="可持续发展实践的实施需要根本性的反思。"),
            SentenceTranslation(sentence_id="s2", translation_zh="这个概念已经变成了一个流行术语。"),
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
    assert any(warning.get("code") == "translation_coverage_incomplete" for warning in outcome.warnings)
