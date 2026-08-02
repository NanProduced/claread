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
from app.schemas.analysis import SentenceEntry


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


def test_phrase_gloss_accepts_title_text_with_explicit_spans() -> None:
    item = PhraseGloss(
        sentence_id="s1",
        text="turn ... into",
        spans=[SpanRef(text="turn"), SpanRef(text="into")],
        phrase_type="phrasal_verb",
        zh="把……变成……",
    )

    assert item.text == "turn ... into"
    assert [span.text for span in item.spans or []] == ["turn", "into"]


def test_phrase_gloss_rejects_ellipsis_inside_explicit_spans() -> None:
    with pytest.raises(ValidationError):
        PhraseGloss(
            sentence_id="s1",
            text="turn ... into",
            spans=[SpanRef(text="turn ... into")],
            phrase_type="phrasal_verb",
            zh="把……变成……",
        )


def test_annotation_output_accepts_mixed_annotations() -> None:
    output = AnnotationOutput(
        annotations=[
            VocabHighlight(sentence_id="s1", text="constitutional"),
            PhraseGloss(
                sentence_id="s1",
                text="scored 100 per cent",
                spans=[SpanRef(text="scored 100 per cent")],
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


def test_sentence_entry_accepts_reader_ask_supplement_projection_fields() -> None:
    entry = SentenceEntry.model_validate(
        {
            "id": "ask-supplement:supp-1",
            "sentence_id": "s1",
            "entry_type": "grammar_note",
            "label": "AI 补充语法旁注",
            "title": "补充说明",
            "content": "补充内容",
            "source_kind": "ask_supplement",
            "supplement_id": "supp-1",
            "deletable": True,
            "target_key": "sentence:s1",
            "paragraph_id": "p1",
            "created_from_turn_run_id": "run-1",
            "schema_version": "reader-ask-supplement-v1",
            "lifecycle_status": "persisted",
        }
    )

    assert entry.source_kind == "ask_supplement"
    assert entry.supplement_id == "supp-1"
    assert entry.deletable is True
    assert entry.created_from_turn_run_id == "run-1"
