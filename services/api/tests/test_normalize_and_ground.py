from app.schemas.common import TextSpan
from app.schemas.internal.analysis import (
    Chunk,
    GrammarNote,
    PhraseGloss,
    PreparedSentence,
    SentenceAnalysis,
    SentenceTranslation,
    SpanRef,
    VocabHighlight,
)
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.schemas.internal.execution_plan import GoalPolicy
from app.services.analysis.postprocess.normalize_and_ground import normalize_and_ground


def _sentence(sentence_id: str, text: str) -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sentence_id,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


def test_normalize_drops_spaced_vocab_highlight() -> None:
    invalid_vocab = VocabHighlight.model_construct(
        type="vocab_highlight",
        sentence_id="s1",
        text="extreme lengths",
        occurrence=None,
    )
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft.model_construct(
            vocab_highlights=[invalid_vocab],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=GrammarDraft(grammar_notes=[], sentence_analyses=[]),
        translation_draft=TranslationDraft(
            title="测试标题",
            sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="翻译")]
        ),
        sentences=[_sentence("s1", "Shopkeepers are going to extreme lengths.")],
        policy=GoalPolicy(annotation_density=3, vocabulary_focus="high_value_only", grammar_focus="balanced", translation_focus="natural"),
    )
    assert result.annotations == []
    assert any("single_word" in item.drop_reason or "single" in item.drop_reason for item in result.drop_log)


def test_normalize_drops_invalid_single_word_phrase_gloss() -> None:
    invalid_phrase = PhraseGloss.model_construct(
        type="phrase_gloss",
        sentence_id="s1",
        text="buzzword",
        occurrence=None,
        phrase_type="collocation",
        zh="流行词",
    )
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft.model_construct(
            vocab_highlights=[],
            phrase_glosses=[invalid_phrase],
            context_glosses=[],
        ),
        grammar_draft=GrammarDraft(grammar_notes=[], sentence_analyses=[]),
        translation_draft=TranslationDraft(
            title="测试标题",
            sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="翻译")]
        ),
        sentences=[_sentence("s1", "This concept became a buzzword.")],
        policy=GoalPolicy(annotation_density=3, vocabulary_focus="high_value_only", grammar_focus="balanced", translation_focus="natural"),
    )
    assert result.annotations == []
    assert any("single-token" in item.drop_reason or "single_token" in item.drop_reason for item in result.drop_log)


def test_density_control_uses_profile_limit() -> None:
    annotations = [
        GrammarNote(
            sentence_id="s1",
            spans=[SpanRef(text="which", role="rel")],
            label="定语从句",
            note_zh="说明 which 引导定语从句。",
        ),
        GrammarNote(
            sentence_id="s1",
            spans=[SpanRef(text="that", role="comp")],
            label="宾语从句",
            note_zh="说明 that 引导从句。",
        ),
        VocabHighlight(sentence_id="s1", text="constitutional"),
        VocabHighlight(sentence_id="s1", text="monarchy"),
    ]
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[a for a in annotations if isinstance(a, VocabHighlight)],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=GrammarDraft(
            grammar_notes=[a for a in annotations if isinstance(a, GrammarNote)],
            sentence_analyses=[],
        ),
        translation_draft=TranslationDraft(
            title="测试标题",
            sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="翻译")]
        ),
        sentences=[_sentence("s1", "The constitutional monarchy, which many say matters, is something that people debate.")],
        policy=GoalPolicy(annotation_density=2, vocabulary_focus="high_value_only", grammar_focus="focused", translation_focus="natural"),
    )
    assert len(result.annotations) == 2
    assert any(item.drop_stage == "density_control" for item in result.drop_log)


def test_sentence_analysis_with_result_in_being_done_survives_normalize() -> None:
    analysis = SentenceAnalysis(
        sentence_id="s1",
        label="主句加 result in 压缩结构",
        analysis_zh="先抓主句，再看后面的结果结构。",
        chunks=[
            Chunk(order=1, label="主句", text="Higher gas prices result in"),
            Chunk(order=2, label="结果结构", text="farmers being forced to pay more"),
        ],
    )
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(vocab_highlights=[], phrase_glosses=[], context_glosses=[]),
        grammar_draft=GrammarDraft(grammar_notes=[], sentence_analyses=[analysis]),
        translation_draft=TranslationDraft(
            title="测试标题",
            sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="翻译")]
        ),
        sentences=[_sentence("s1", "Higher gas prices result in farmers being forced to pay more for fertilizer.")],
        policy=GoalPolicy(annotation_density=3, vocabulary_focus="high_value_only", grammar_focus="balanced", translation_focus="natural"),
    )
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "sentence_analysis"
