import pytest
from pydantic import ValidationError

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
from app.services.analysis.postprocess.draft_validators import (
    validate_context_gloss_business_rules,
    validate_phrase_gloss_business_rules,
    validate_vocab_highlight_business_rules,
)


def test_vocab_highlight_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VocabHighlight(
            sentence_id="s1",
            text="test",
            definition="bad",
        )


def test_vocab_highlight_rejects_spaces() -> None:
    with pytest.raises(ValidationError):
        VocabHighlight(sentence_id="s1", text="two words")


def test_phrase_gloss_single_word_requires_proper_type() -> None:
    with pytest.raises(ValidationError):
        PhraseGloss(sentence_id="s1", text="buzzword", phrase_type="collocation", zh="流行词")


def test_phrase_gloss_proper_noun_rejects_basic_word() -> None:
    with pytest.raises(ValidationError):
        PhraseGloss(sentence_id="s1", text="Andrew", phrase_type="proper_noun", zh="安德鲁")


def test_business_rule_helpers_match_schema_constraints() -> None:
    invalid_vocab = VocabHighlight.model_construct(
        type="vocab_highlight",
        sentence_id="s1",
        text="two words",
        occurrence=None,
    )
    invalid_phrase = PhraseGloss.model_construct(
        type="phrase_gloss",
        sentence_id="s1",
        text="buzzword",
        occurrence=None,
        phrase_type="collocation",
        zh="流行词",
    )
    invalid_proper = PhraseGloss.model_construct(
        type="phrase_gloss",
        sentence_id="s1",
        text="Andrew",
        occurrence=None,
        phrase_type="proper_noun",
        zh="安德鲁",
    )
    context = ContextGloss(sentence_id="s1", text="rendered", gloss="呈现", reason="词典义不足")

    assert validate_vocab_highlight_business_rules(invalid_vocab)
    assert validate_phrase_gloss_business_rules(invalid_phrase)
    assert validate_phrase_gloss_business_rules(invalid_proper)
    assert validate_context_gloss_business_rules(context) == []


def test_annotation_output_accepts_mixed_annotations() -> None:
    output = AnnotationOutput(
        annotations=[
            VocabHighlight(sentence_id="s1", text="constitutional"),
            PhraseGloss(
                sentence_id="s1",
                text="scored 100 per cent",
                phrase_type="collocation",
                zh="获得百分之百好评",
            ),
            ContextGloss(sentence_id="s1", text="rendered", gloss="呈现", reason="这里是视觉呈现义"),
            GrammarNote(sentence_id="s1", spans=[SpanRef(text="so", role="x")], label="语法", note_zh="注释"),
            SentenceAnalysis(
                sentence_id="s1",
                label="句型",
                analysis_zh="讲解",
                chunks=[Chunk(order=1, label="主", text="Main"), Chunk(order=2, label="谓", text="is")],
            ),
        ],
        sentence_translations=[SentenceTranslation(sentence_id="s1", translation_zh="翻译")],
    )
    assert len(output.annotations) == 5
