"""Phase 1 Draft schema validation tests."""

import pytest
from pydantic import ValidationError

from app.schemas.internal.analysis import (
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    SpanRef,
    VocabHighlight,
)
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftAnnotation,
    DraftContextGloss,
    DraftGrammarNote,
    DraftPhraseGloss,
    DraftSentenceAnalysis,
    DraftVocabHighlight,
    GrammarDraft,
    VocabularyDraft,
    draft_to_annotation,
)

# ── AnchorQuote ─────────────────────────────────────────────────────


class TestAnchorQuote:
    def test_valid_text_only(self) -> None:
        q = AnchorQuote(text="hello")
        assert q.text == "hello"
        assert q.role is None

    def test_valid_text_and_role(self) -> None:
        q = AnchorQuote(text="turned", role="verb")
        assert q.text == "turned"
        assert q.role == "verb"

    def test_empty_text_fails(self) -> None:
        with pytest.raises(ValidationError):
            AnchorQuote(text="")

    def test_ellipsis_allowed_at_anchor_quote_level(self) -> None:
        """Ellipsis is a soft warning in validators, not a hard schema rejection."""
        q = AnchorQuote(text="turn ... into")
        assert q.text == "turn ... into"


# ── DraftVocabHighlight ────────────────────────────────────────────


class TestDraftVocabHighlight:
    def test_valid_single_word(self) -> None:
        v = DraftVocabHighlight(sentence_id="s1", text="constitutional")
        assert v.text == "constitutional"
        assert v.type == "vocab_highlight"

    def test_text_with_spaces_fails(self) -> None:
        with pytest.raises(ValidationError):
            DraftVocabHighlight(sentence_id="s1", text="two words")

    def test_no_occurrence_field(self) -> None:
        v = DraftVocabHighlight(sentence_id="s1", text="word")
        assert not hasattr(v, "occurrence")


# ── DraftPhraseGloss ───────────────────────────────────────────────


class TestDraftPhraseGloss:
    def _make_valid(self, **overrides) -> DraftPhraseGloss:
        defaults = dict(
            sentence_id="s1",
            label="turn into",
            anchor_quotes=[AnchorQuote(text="turned")],
            phrase_type="phrasal_verb",
            zh="变成",
        )
        defaults.update(overrides)
        return DraftPhraseGloss(**defaults)

    def test_valid_full(self) -> None:
        p = self._make_valid()
        assert p.label == "turn into"
        assert p.type == "phrase_gloss"

    def test_anchor_quotes_with_ellipsis_allowed(self) -> None:
        """Ellipsis is a soft warning in validators, not a hard schema rejection."""
        p = self._make_valid(
            label="turn into",
            anchor_quotes=[AnchorQuote(text="turn ... into")],
            phrase_type="phrasal_verb",
        )
        assert p.anchor_quotes[0].text == "turn ... into"

    def test_single_token_label_non_special_type_fails(self) -> None:
        with pytest.raises(ValidationError):
            self._make_valid(
                label="buzzword",
                anchor_quotes=[AnchorQuote(text="buzzword")],
                phrase_type="collocation",
            )

    def test_single_token_label_proper_noun_succeeds(self) -> None:
        p = self._make_valid(
            label="UNESCO",
            anchor_quotes=[AnchorQuote(text="UNESCO")],
            phrase_type="proper_noun",
        )
        assert p.phrase_type == "proper_noun"

    def test_single_token_label_compound_succeeds(self) -> None:
        p = self._make_valid(
            label="blockchain",
            anchor_quotes=[AnchorQuote(text="blockchain")],
            phrase_type="compound",
        )
        assert p.phrase_type == "compound"

    def test_proper_noun_basic_english_word_fails(self) -> None:
        with pytest.raises(ValidationError):
            self._make_valid(
                label="Andrew",
                anchor_quotes=[AnchorQuote(text="Andrew")],
                phrase_type="proper_noun",
            )

    def test_anchor_quotes_min_length_1(self) -> None:
        with pytest.raises(ValidationError):
            self._make_valid(anchor_quotes=[])

    def test_anchor_quotes_max_length_4(self) -> None:
        five_quotes = [AnchorQuote(text=f"w{i}") for i in range(5)]
        with pytest.raises(ValidationError):
            self._make_valid(
                label="multi word phrase here",
                anchor_quotes=five_quotes,
                phrase_type="collocation",
            )

    def test_anchor_quotes_max_4_accepted(self) -> None:
        four_quotes = [AnchorQuote(text=f"w{i}") for i in range(4)]
        p = self._make_valid(
            label="multi word phrase here",
            anchor_quotes=four_quotes,
            phrase_type="collocation",
        )
        assert len(p.anchor_quotes) == 4


# ── DraftContextGloss ───────────────────────────────────────────────


class TestDraftContextGloss:
    def test_valid_full(self) -> None:
        c = DraftContextGloss(
            sentence_id="s1",
            display="rendered",
            anchor_quotes=[AnchorQuote(text="rendered")],
            gloss="呈现",
            reason="词典义不足以解释",
        )
        assert c.type == "context_gloss"
        assert c.gloss == "呈现"

    def test_anchor_quotes_min_length_1(self) -> None:
        with pytest.raises(ValidationError):
            DraftContextGloss(
                sentence_id="s1",
                display="rendered",
                anchor_quotes=[],
                gloss="呈现",
                reason="词典义不足以解释",
            )

    def test_anchor_quotes_max_length_4(self) -> None:
        five_quotes = [AnchorQuote(text=f"word{i}") for i in range(5)]
        with pytest.raises(ValidationError):
            DraftContextGloss(
                sentence_id="s1",
                display="rendered",
                anchor_quotes=five_quotes,
                gloss="呈现",
                reason="词典义不足以解释",
            )


# ── DraftGrammarNote ───────────────────────────────────────────────


class TestDraftGrammarNote:
    def test_valid_without_pattern(self) -> None:
        g = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="not only 句首倒装",
            anchor_quotes=[AnchorQuote(text="Not only")],
            note_zh="倒装结构",
        )
        assert g.type == "grammar_note"
        assert g.pattern is None

    def test_valid_with_pattern(self) -> None:
        g = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="not only 句首倒装",
            pattern="Not only + auxiliary + subject + verb",
            anchor_quotes=[AnchorQuote(text="Not only")],
            note_zh="倒装结构",
        )
        assert g.pattern == "Not only + auxiliary + subject + verb"

    def test_anchor_quotes_min_length_1(self) -> None:
        with pytest.raises(ValidationError):
            DraftGrammarNote(
                sentence_id="s1",
                grammar_point="倒装",
                anchor_quotes=[],
                note_zh="注释",
            )

    def test_anchor_quotes_max_length_4(self) -> None:
        five_quotes = [AnchorQuote(text=f"word{i}") for i in range(5)]
        with pytest.raises(ValidationError):
            DraftGrammarNote(
                sentence_id="s1",
                grammar_point="倒装",
                anchor_quotes=five_quotes,
                note_zh="注释",
            )


# ── DraftSentenceAnalysis ──────────────────────────────────────────


class TestDraftSentenceAnalysis:
    def test_valid_without_chunks(self) -> None:
        s = DraftSentenceAnalysis(
            sentence_id="s1",
            label="复合句",
            analysis_zh="主干是…",
        )
        assert s.type == "sentence_analysis"
        assert s.chunks is None

    def test_valid_with_chunks(self) -> None:
        from app.schemas.internal.analysis import Chunk

        s = DraftSentenceAnalysis(
            sentence_id="s1",
            label="复合句",
            analysis_zh="主干是…",
            chunks=[Chunk(order=1, label="主句", text="Main clause")],
        )
        assert len(s.chunks) == 1


# ── DraftAnnotation union ──────────────────────────────────────────


class TestDraftAnnotationUnion:
    def test_discriminator_vocab_highlight(self) -> None:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(DraftAnnotation)
        v = adapter.validate_python(
            {"type": "vocab_highlight", "sentence_id": "s1", "text": "word"}
        )
        assert isinstance(v, DraftVocabHighlight)

    def test_discriminator_phrase_gloss(self) -> None:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(DraftAnnotation)
        v = adapter.validate_python(
            {
                "type": "phrase_gloss",
                "sentence_id": "s1",
                "label": "turn into",
                "anchor_quotes": [{"text": "turned"}],
                "phrase_type": "phrasal_verb",
                "zh": "变成",
            }
        )
        assert isinstance(v, DraftPhraseGloss)

    def test_discriminator_context_gloss(self) -> None:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(DraftAnnotation)
        v = adapter.validate_python(
            {
                "type": "context_gloss",
                "sentence_id": "s1",
                "display": "rendered",
                "anchor_quotes": [{"text": "rendered"}],
                "gloss": "呈现",
                "reason": "词典义不足",
            }
        )
        assert isinstance(v, DraftContextGloss)

    def test_discriminator_grammar_note(self) -> None:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(DraftAnnotation)
        v = adapter.validate_python(
            {
                "type": "grammar_note",
                "sentence_id": "s1",
                "grammar_point": "倒装",
                "anchor_quotes": [{"text": "Not only"}],
                "note_zh": "注释",
            }
        )
        assert isinstance(v, DraftGrammarNote)

    def test_discriminator_sentence_analysis(self) -> None:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(DraftAnnotation)
        v = adapter.validate_python(
            {
                "type": "sentence_analysis",
                "sentence_id": "s1",
                "label": "复合句",
                "analysis_zh": "讲解",
            }
        )
        assert isinstance(v, DraftSentenceAnalysis)


# ── VocabularyDraft container ──────────────────────────────────────


class TestVocabularyDraft:
    def test_default_empty_lists(self) -> None:
        vd = VocabularyDraft()
        assert vd.vocab_highlights == []
        assert vd.phrase_glosses == []
        assert vd.context_glosses == []

    def test_contains_all_vocab_types(self) -> None:
        vd = VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="word"),
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="turn into",
                    anchor_quotes=[AnchorQuote(text="turned")],
                    phrase_type="phrasal_verb",
                    zh="变成",
                ),
            ],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="rendered",
                    anchor_quotes=[AnchorQuote(text="rendered")],
                    gloss="呈现",
                    reason="词典义不足",
                ),
            ],
        )
        assert len(vd.vocab_highlights) == 1
        assert len(vd.phrase_glosses) == 1
        assert len(vd.context_glosses) == 1


# ── GrammarDraft container ─────────────────────────────────────────


class TestGrammarDraft:
    def test_default_empty_lists(self) -> None:
        gd = GrammarDraft()
        assert gd.grammar_notes == []
        assert gd.sentence_analyses == []

    def test_contains_all_grammar_types(self) -> None:
        gd = GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="倒装",
                    anchor_quotes=[AnchorQuote(text="Not only")],
                    note_zh="注释",
                ),
            ],
            sentence_analyses=[
                DraftSentenceAnalysis(
                    sentence_id="s1",
                    label="复合句",
                    analysis_zh="讲解",
                ),
            ],
        )
        assert len(gd.grammar_notes) == 1
        assert len(gd.sentence_analyses) == 1


# ── draft_to_annotation() conversion ───────────────────────────────


class TestDraftToAnnotation:
    def test_vocab_highlight_conversion(self) -> None:
        draft = DraftVocabHighlight(sentence_id="s1", text="constitutional")
        result = draft_to_annotation(draft)
        assert isinstance(result, VocabHighlight)
        assert result.text == "constitutional"
        assert result.occurrence is None

    def test_phrase_gloss_conversion(self) -> None:
        draft = DraftPhraseGloss(
            sentence_id="s1",
            label="turn into",
            anchor_quotes=[
                AnchorQuote(text="turned", role="verb"),
                AnchorQuote(text="into"),
            ],
            phrase_type="phrasal_verb",
            zh="变成",
        )
        result = draft_to_annotation(draft)
        assert isinstance(result, PhraseGloss)
        assert result.text == "turn into"
        assert result.occurrence is None
        assert result.phrase_type == "phrasal_verb"
        assert result.zh == "变成"
        assert len(result.spans) == 2
        assert isinstance(result.spans[0], SpanRef)
        assert result.spans[0].text == "turned"
        assert result.spans[0].role == "verb"
        assert result.spans[0].occurrence is None

    def test_context_gloss_conversion(self) -> None:
        draft = DraftContextGloss(
            sentence_id="s1",
            display="rendered",
            anchor_quotes=[AnchorQuote(text="rendered")],
            gloss="呈现",
            reason="词典义不足",
        )
        result = draft_to_annotation(draft)
        assert isinstance(result, ContextGloss)
        assert result.text == "rendered"
        assert result.display is None  # display == text, so not stored
        assert result.spans is not None
        assert len(result.spans) == 1
        assert result.spans[0].text == "rendered"
        assert result.occurrence is None
        assert result.gloss == "呈现"
        assert result.reason == "词典义不足"

    def test_context_gloss_preserves_display_when_different(self) -> None:
        draft = DraftContextGloss(
            sentence_id="s1",
            display="refer to ... as",
            anchor_quotes=[AnchorQuote(text="refer"), AnchorQuote(text="as")],
            gloss="把……称为……",
            reason="词典义不足",
        )
        result = draft_to_annotation(draft)
        assert isinstance(result, ContextGloss)
        assert result.text == "refer"  # first quote for binding
        assert result.display == "refer to ... as"  # preserved
        assert result.spans is not None
        assert len(result.spans) == 2
        assert result.spans[0].text == "refer"
        assert result.spans[1].text == "as"
        assert result.gloss == "把……称为……"

    def test_grammar_note_conversion(self) -> None:
        draft = DraftGrammarNote(
            sentence_id="s1",
            grammar_point="not only 倒装",
            anchor_quotes=[AnchorQuote(text="Not only", role="inversion_trigger")],
            note_zh="倒装结构",
        )
        result = draft_to_annotation(draft)
        assert isinstance(result, GrammarNote)
        assert result.label == "not only 倒装"
        assert len(result.spans) == 1
        assert isinstance(result.spans[0], SpanRef)
        assert result.spans[0].text == "Not only"
        assert result.spans[0].role == "inversion_trigger"
        assert result.note_zh == "倒装结构"

    def test_sentence_analysis_conversion(self) -> None:
        from app.schemas.internal.analysis import Chunk

        draft = DraftSentenceAnalysis(
            sentence_id="s1",
            label="复合句",
            analysis_zh="讲解",
            chunks=[Chunk(order=1, label="主句", text="Main")],
        )
        result = draft_to_annotation(draft)
        assert isinstance(result, SentenceAnalysis)
        assert result.label == "复合句"
        assert result.analysis_zh == "讲解"
        assert len(result.chunks) == 1
