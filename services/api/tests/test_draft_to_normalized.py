"""Tests for DraftAnnotation → NormalizedAnnotation mapper."""

from __future__ import annotations

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftContextGloss,
    DraftGrammarNote,
    DraftPhraseGloss,
    DraftSentenceAnalysis,
    DraftVocabHighlight,
)
from app.schemas.internal.normalized import (
    DropLogEntry,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)
from app.services.analysis.postprocess.draft_to_normalized import (
    draft_to_normalized_annotation,
)


def _sentence(text: str, sentence_id: str = "s1") -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sentence_id,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


def _sentence_map(*sentences: PreparedSentence) -> dict[str, PreparedSentence]:
    return {s.sentence_id: s for s in sentences}


class TestMapVocabHighlight:
    def test_success(self) -> None:
        sentence = _sentence("The results prompted the team.")
        draft = DraftVocabHighlight(sentence_id="s1", text="prompted")
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedVocabHighlight)
        assert len(result.spans) == 1
        assert result.spans[0].text == "prompted"

    def test_not_found(self) -> None:
        sentence = _sentence("The results prompted the team.")
        draft = DraftVocabHighlight(sentence_id="s1", text="NONEXISTENT")
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert result is None
        assert len(drop_log) == 1
        assert drop_log[0].drop_reason == "quote_not_found"

    def test_ambiguous(self) -> None:
        sentence = _sentence("The team and the other team agreed.")
        draft = DraftVocabHighlight(sentence_id="s1", text="team")
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert result is None
        assert len(drop_log) == 1
        assert drop_log[0].drop_reason == "quote_ambiguous"

    def test_too_short(self) -> None:
        sentence = _sentence("It is what it is.")
        draft = DraftVocabHighlight(sentence_id="s1", text="it")
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert result is None
        assert len(drop_log) == 1
        assert drop_log[0].drop_reason == "quote_too_short"

    def test_sentence_id_not_found(self) -> None:
        draft = DraftVocabHighlight(sentence_id="s99", text="prompted")
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(), drop_log,
        )
        assert result is None
        assert len(drop_log) == 1
        assert drop_log[0].drop_reason == "sentence_id_invalid"


class TestMapPhraseGloss:
    def test_success_single_quote(self) -> None:
        sentence = _sentence("He turned the idea into reality.")
        draft = DraftPhraseGloss(
            sentence_id="s1",
            label="turn ... into",
            anchor_quotes=[AnchorQuote(text="turned")],
            phrase_type="phrasal_verb",
            zh="把……变成……",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedPhraseGloss)
        assert result.label == "turn ... into"
        assert result.phrase_type == "phrasal_verb"
        assert result.zh == "把……变成……"
        assert len(result.spans) == 1

    def test_success_multi_quote(self) -> None:
        sentence = _sentence(
            "The results prompted the team to rethink their approach.",
        )
        draft = DraftPhraseGloss(
            sentence_id="s1",
            label="prompt sb to do sth",
            anchor_quotes=[
                AnchorQuote(text="prompted"),
                AnchorQuote(text="to rethink"),
            ],
            phrase_type="phrasal_verb",
            zh="促使某人做某事",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedPhraseGloss)
        assert len(result.spans) == 2
        assert result.spans[0].text == "prompted"
        assert result.spans[1].text == "to rethink"

    def test_quote_not_found(self) -> None:
        sentence = _sentence("The results prompted the team.")
        draft = DraftPhraseGloss(
            sentence_id="s1",
            label="test phrase",
            anchor_quotes=[AnchorQuote(text="NONEXISTENT")],
            phrase_type="collocation",
            zh="测试",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert result is None
        assert any(
            e.drop_reason == "quote_not_found" for e in drop_log
        )


class TestMapContextGloss:
    def test_success_with_display(self) -> None:
        sentence = _sentence(
            "The results prompted the team to rethink their approach.",
        )
        draft = DraftContextGloss(
            sentence_id="s1",
            display="prompt sb to do sth",
            anchor_quotes=[
                AnchorQuote(text="prompted"),
                AnchorQuote(text="to rethink"),
            ],
            gloss="促使某人做某事",
            reason="词典义不足以表达语境含义",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedContextGloss)
        assert result.display == "prompt sb to do sth"
        assert result.gloss == "促使某人做某事"
        assert len(result.spans) == 2


class TestMapGrammarNote:
    def test_success(self) -> None:
        sentence = _sentence(
            "Not only did he win, but he also broke the record.",
        )
        draft = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="not only 句首倒装",
            pattern="Not only + auxiliary + subject + verb",
            anchor_quotes=[
                AnchorQuote(text="Not only did", role="inversion_trigger"),
                AnchorQuote(text="but he also", role="paired_structure"),
            ],
            note_zh="Not only 位于句首时使用部分倒装。",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedGrammarNote)
        assert result.grammar_point == "not only 句首倒装"
        assert result.pattern == "Not only + auxiliary + subject + verb"
        assert len(result.spans) == 2
        assert result.spans[0].role == "inversion_trigger"
        assert result.spans[1].role == "paired_structure"

    def test_quote_ambiguous(self) -> None:
        sentence = _sentence("The team and the other team agreed.")
        draft = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="test grammar",
            anchor_quotes=[AnchorQuote(text="team")],
            note_zh="测试",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert result is None
        assert any(
            e.drop_reason == "quote_ambiguous" for e in drop_log
        )


class TestMapSentenceAnalysis:
    def test_success(self) -> None:
        sentence = _sentence("This is a simple sentence.")
        draft = DraftSentenceAnalysis(
            sentence_id="s1",
            label="简单句",
            analysis_zh="这是一个简单句。",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert isinstance(result, NormalizedSentenceAnalysis)
        assert result.label == "简单句"
        assert result.chunks is None

    def test_sentence_id_not_found(self) -> None:
        draft = DraftSentenceAnalysis(
            sentence_id="s99",
            label="简单句",
            analysis_zh="这是一个简单句。",
        )
        drop_log: list[DropLogEntry] = []
        result = draft_to_normalized_annotation(
            draft, _sentence_map(), drop_log,
        )
        assert result is None
        assert any(
            e.drop_reason == "sentence_id_invalid" for e in drop_log
        )


class TestSourceAgentParameter:
    def test_default_source_agent(self) -> None:
        sentence = _sentence("The results prompted the team.")
        draft = DraftVocabHighlight(sentence_id="s1", text="NONEXISTENT")
        drop_log: list[DropLogEntry] = []
        draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
        )
        assert drop_log[0].source_agent == "vocabulary"

    def test_grammar_source_agent(self) -> None:
        sentence = _sentence("The team and the other team agreed.")
        draft = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="test",
            anchor_quotes=[AnchorQuote(text="team")],
            note_zh="测试",
        )
        drop_log: list[DropLogEntry] = []
        draft_to_normalized_annotation(
            draft, _sentence_map(sentence), drop_log,
            source_agent="grammar",
        )
        assert drop_log[0].source_agent == "grammar"
