"""Tests for AnchorQuote → CanonicalSpan resolver."""

from __future__ import annotations

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import AnchorQuote
from app.services.analysis.postprocess.anchor_quote_resolver import (
    resolve_anchor_quotes,
    resolve_vocab_text_to_canonical_span,
)


def _sentence(text: str, sentence_id: str = "s1") -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sentence_id,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


class TestResolveAnchorQuotesExactMatch:
    def test_single_exact_match(self) -> None:
        sentence = _sentence("The results prompted the team to rethink.")
        quotes = [AnchorQuote(text="prompted")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 1
        assert len(errors) == 0
        assert spans[0].text == "prompted"
        assert spans[0].resolution_kind == "exact"
        assert spans[0].source_quote == "prompted"

    def test_multi_quote_exact_match(self) -> None:
        sentence = _sentence(
            "The results prompted the team to rethink their approach.",
        )
        quotes = [
            AnchorQuote(text="prompted"),
            AnchorQuote(text="to rethink"),
        ]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 2
        assert len(errors) == 0
        assert spans[0].text == "prompted"
        assert spans[1].text == "to rethink"
        assert spans[0].end <= spans[1].start  # 顺序正确

    def test_quote_with_role(self) -> None:
        sentence = _sentence("He turned the idea into reality.")
        quotes = [
            AnchorQuote(text="turned", role="verb"),
            AnchorQuote(text="into", role="preposition"),
        ]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 2
        assert spans[0].role == "verb"
        assert spans[1].role == "preposition"


class TestResolveAnchorQuotesCanonicalized:
    def test_case_difference_resolved_as_canonicalized(self) -> None:
        sentence = _sentence("The Results Prompted The Team.")
        quotes = [AnchorQuote(text="prompted")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 1
        assert spans[0].resolution_kind == "canonicalized"
        assert spans[0].text == "Prompted"
        assert spans[0].source_quote == "prompted"

    def test_curly_quote_resolved_as_canonicalized(self) -> None:
        """Quote with curly quotes should resolve against sentence with
        straight quotes (pre-normalized). canonicalize_text_anchor_to_source
        returns canonicalized because quote text != source text."""
        # PreparedSentence.text is pre-normalized: curly → straight
        sentence = _sentence('He said "urgent" matters.')
        # LLM outputs curly quotes in anchor quote
        quotes = [AnchorQuote(text='\u201curgent\u201d')]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 1
        assert spans[0].resolution_kind == "canonicalized"
        assert spans[0].text == '"urgent"'
        assert spans[0].source_quote == '\u201curgent\u201d'

    def test_straight_quote_resolves_against_curly_source(self) -> None:
        """A straight-quote model output should not be rejected before the
        canonicalized resolver can match curly quotes in source text."""
        sentence = _sentence("He said \u201curgent\u201d matters.")
        quotes = [AnchorQuote(text='"urgent"')]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(errors) == 0
        assert len(spans) == 1
        assert spans[0].resolution_kind == "canonicalized"
        assert spans[0].text == "urgent"


class TestResolveAnchorQuotesNotFound:
    def test_quote_not_in_sentence(self) -> None:
        sentence = _sentence("The results prompted the team.")
        quotes = [AnchorQuote(text="NONEXISTENT")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_not_found"
        assert errors[0].quote_text == "NONEXISTENT"

    def test_schematic_ellipsis_not_accepted(self) -> None:
        """Schematic ellipsis quotes like 'turn ... into' should
        not be resolved by the strict resolver."""
        sentence = _sentence("He turned the idea into reality.")
        quotes = [AnchorQuote(text="turned ... into")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_not_found"


class TestResolveAnchorQuotesAmbiguous:
    def test_duplicate_text_without_disambiguation(self) -> None:
        sentence = _sentence("The team and the other team agreed.")
        quotes = [AnchorQuote(text="team")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_ambiguous"

    def test_case_variant_duplicate_is_ambiguous(self) -> None:
        sentence = _sentence("Team and team agreed.")
        quotes = [AnchorQuote(text="team")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_ambiguous"


class TestResolveAnchorQuotesOutOfOrder:
    def test_multi_quote_out_of_order(self) -> None:
        """Quotes that resolve to out-of-order positions should fail."""
        sentence = _sentence(
            "Not only did he win, but he also broke the record.",
        )
        quotes = [
            AnchorQuote(text="but he also"),
            AnchorQuote(text="Not only did"),
        ]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_out_of_order"


class TestResolveAnchorQuotesTooShort:
    def test_single_short_function_word(self) -> None:
        sentence = _sentence("It is what it is.")
        quotes = [AnchorQuote(text="it")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_too_short"

    def test_single_two_char_word(self) -> None:
        sentence = _sentence("He went to the store.")
        quotes = [AnchorQuote(text="to")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 0
        assert len(errors) == 1
        assert errors[0].reason == "quote_too_short"

    def test_short_word_as_part_of_multi_range(self) -> None:
        """Short function words are allowed as part of multi-range."""
        sentence = _sentence("He turned the idea into reality.")
        quotes = [
            AnchorQuote(text="turned", role="verb"),
            AnchorQuote(text="into", role="preposition"),
        ]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 2
        assert len(errors) == 0

    def test_three_char_word_not_too_short(self) -> None:
        sentence = _sentence("The cat sat on the mat.")
        quotes = [AnchorQuote(text="cat")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 1
        assert len(errors) == 0


class TestResolveAnchorQuotesEmoji:
    def test_emoji_in_sentence(self) -> None:
        sentence = _sentence("I love Python 🐍!")
        quotes = [AnchorQuote(text="Python")]
        spans, errors = resolve_anchor_quotes(sentence, quotes)
        assert len(spans) == 1
        assert spans[0].text == "Python"


class TestResolveVocabTextToCanonicalSpan:
    def test_exact_match(self) -> None:
        sentence = _sentence("The results prompted the team.")
        span, errors = resolve_vocab_text_to_canonical_span(sentence, "prompted")
        assert span is not None
        assert span.text == "prompted"
        assert span.resolution_kind == "exact"
        assert len(errors) == 0

    def test_not_found(self) -> None:
        sentence = _sentence("The results prompted the team.")
        span, errors = resolve_vocab_text_to_canonical_span(sentence, "NONEXISTENT")
        assert span is None
        assert len(errors) == 1
        assert errors[0].reason == "quote_not_found"

    def test_ambiguous(self) -> None:
        sentence = _sentence("The team and the other team agreed.")
        span, errors = resolve_vocab_text_to_canonical_span(sentence, "team")
        assert span is None
        assert len(errors) == 1
        assert errors[0].reason == "quote_ambiguous"

    def test_case_variant_ambiguous(self) -> None:
        sentence = _sentence("Team and team agreed.")
        span, errors = resolve_vocab_text_to_canonical_span(sentence, "team")
        assert span is None
        assert len(errors) == 1
        assert errors[0].reason == "quote_ambiguous"

    def test_too_short(self) -> None:
        sentence = _sentence("It is what it is.")
        span, errors = resolve_vocab_text_to_canonical_span(sentence, "it")
        assert span is None
        assert len(errors) == 1
        assert errors[0].reason == "quote_too_short"
