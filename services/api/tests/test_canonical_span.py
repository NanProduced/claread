"""Tests for CanonicalSpan and NormalizedAnnotation schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.internal.normalized import (
    CanonicalSpan,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)


def _span(**overrides: object) -> CanonicalSpan:
    defaults = {
        "sentence_id": "s1",
        "start": 0,
        "end": 5,
        "text": "Hello",
        "resolution_kind": "exact",
    }
    defaults.update(overrides)
    return CanonicalSpan(**defaults)  # type: ignore[arg-type]


class TestCanonicalSpan:
    def test_valid_exact(self) -> None:
        span = _span()
        assert span.start == 0
        assert span.end == 5
        assert span.text == "Hello"
        assert span.resolution_kind == "exact"
        assert span.role is None
        assert span.source_quote is None
        assert span.occurrence is None

    def test_valid_canonicalized(self) -> None:
        span = _span(resolution_kind="canonicalized")
        assert span.resolution_kind == "canonicalized"

    def test_valid_boundary_trimmed(self) -> None:
        span = _span(resolution_kind="boundary_trimmed")
        assert span.resolution_kind == "boundary_trimmed"

    def test_with_optional_fields(self) -> None:
        span = _span(
            role="verb",
            source_quote="turned",
            occurrence=2,
        )
        assert span.role == "verb"
        assert span.source_quote == "turned"
        assert span.occurrence == 2

    def test_start_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            _span(start=-1)

    def test_end_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _span(end=0)

    def test_text_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            _span(text="")

    def test_invalid_resolution_kind(self) -> None:
        with pytest.raises(ValidationError):
            _span(resolution_kind="schematic_ellipsis_expanded")

    def test_occurrence_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _span(occurrence=0)

    def test_end_must_be_greater_than_start(self) -> None:
        with pytest.raises(ValidationError, match="must be greater"):
            _span(start=10, end=5)

    def test_end_equals_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be greater"):
            _span(start=5, end=5)


class TestNormalizedVocabHighlight:
    def test_valid_single_span(self) -> None:
        span = _span()
        nv = NormalizedVocabHighlight(sentence_id="s1", spans=[span])
        assert nv.type == "vocab_highlight"
        assert len(nv.spans) == 1

    def test_must_have_exactly_one_span(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedVocabHighlight(sentence_id="s1", spans=[])

    def test_cannot_have_multiple_spans(self) -> None:
        span1 = _span(start=0, end=5, text="Hello")
        span2 = _span(start=6, end=11, text="World")
        with pytest.raises(ValidationError):
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[span1, span2],
            )

    def test_extra_fields_forbidden(self) -> None:
        span = _span()
        with pytest.raises(ValidationError):
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[span],
                unexpected="field",  # type: ignore[call-arg]
            )


class TestNormalizedPhraseGloss:
    def test_valid(self) -> None:
        span = _span()
        np = NormalizedPhraseGloss(
            sentence_id="s1",
            spans=[span],
            label="turn ... into",
            phrase_type="phrasal_verb",
            zh="把……变成……",
        )
        assert np.type == "phrase_gloss"
        assert np.label == "turn ... into"

    def test_multi_span(self) -> None:
        span1 = _span(start=4, end=11, text="prompted")
        span2 = _span(start=20, end=30, text="to rethink")
        np = NormalizedPhraseGloss(
            sentence_id="s1",
            spans=[span1, span2],
            label="prompt sb to do sth",
            phrase_type="phrasal_verb",
            zh="促使某人做某事",
        )
        assert len(np.spans) == 2

    def test_max_4_spans(self) -> None:
        spans = [_span(start=i * 6, end=i * 6 + 5, text=f"word{i}") for i in range(5)]
        with pytest.raises(ValidationError):
            NormalizedPhraseGloss(
                sentence_id="s1",
                spans=spans,
                label="test",
                phrase_type="collocation",
                zh="测试",
            )


class TestNormalizedContextGloss:
    def test_valid(self) -> None:
        span = _span()
        nc = NormalizedContextGloss(
            sentence_id="s1",
            spans=[span],
            display="prompt sb to do sth",
            gloss="促使某人做某事",
            reason="词典义不足以表达语境含义",
        )
        assert nc.type == "context_gloss"
        assert nc.display == "prompt sb to do sth"


class TestNormalizedGrammarNote:
    def test_valid(self) -> None:
        span = _span()
        ng = NormalizedGrammarNote(
            sentence_id="s1",
            spans=[span],
            grammar_point="not only 句首倒装",
            pattern="Not only + auxiliary + subject + verb",
            note_zh="Not only 位于句首时使用部分倒装。",
        )
        assert ng.type == "grammar_note"
        assert ng.pattern == "Not only + auxiliary + subject + verb"

    def test_pattern_is_optional(self) -> None:
        span = _span()
        ng = NormalizedGrammarNote(
            sentence_id="s1",
            spans=[span],
            grammar_point="with 复合结构",
            note_zh="with 复合结构由 with + 宾语 + 宾补构成。",
        )
        assert ng.pattern is None


class TestNormalizedSentenceAnalysis:
    def test_valid_without_chunks(self) -> None:
        ns = NormalizedSentenceAnalysis(
            sentence_id="s1",
            label="主从复合句",
            analysis_zh="主句 + that 宾语从句。",
        )
        assert ns.type == "sentence_analysis"
        assert ns.chunks is None
