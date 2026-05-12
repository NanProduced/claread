"""
Regression tests for input_preparation.py layered redesign.

Covers:
- spaCy abbreviation handling (U.S., U.K., Ph.D., e.g., i.e., Dr., etc.)
- Short but valid English texts (fast-path for short inputs)
- Mixed Chinese-English texts
- structured_doc / parameter documentation
- spaCy unavailable → explicit regex fallback (observable via action name)
- Sentence span exact mapping
- _split_sentences_regex abbreviation protection (direct unit tests)
"""

import pytest

from app.schemas.common import TextSpan
from app.services.analysis.preprocess.input_preparation import (
    _ABBREVIATION_RE,
    _check_spacy_model,
    _is_fast_path_eligible,
    _spacy_available,
    _split_sentences_regex,
    _StructureHint,
    layer5_split,
    prepare_input,
    sanitize_text,
)


# ---------------------------------------------------------------------------
# _ABBREVIATION_RE correctness
# ---------------------------------------------------------------------------


def test_abbreviation_regex_matches_us() -> None:
    assert _ABBREVIATION_RE.search("The U.S. economy") is not None


def test_abbreviation_regex_matches_uk() -> None:
    assert _ABBREVIATION_RE.search("The U.K. government") is not None


def test_abbreviation_regex_matches_phd() -> None:
    assert _ABBREVIATION_RE.search("She earned a Ph.D. last year") is not None


def test_abbreviation_regex_matches_eg() -> None:
    assert _ABBREVIATION_RE.search("e.g., apple") is not None


def test_abbreviation_regex_matches_ie() -> None:
    """i.e. was incorrectly matched as i.g. before the fix."""
    assert _ABBREVIATION_RE.search("i.e., option A") is not None


def test_abbreviation_regex_does_not_match_ig() -> None:
    """i.g. should NOT be matched (it is not a real abbreviation)."""
    assert _ABBREVIATION_RE.search("i.g.") is None


def test_abbreviation_regex_matches_dr() -> None:
    assert _ABBREVIATION_RE.search("Dr. Smith") is not None


def test_abbreviation_regex_matches_mr_mrs_ms() -> None:
    assert _ABBREVIATION_RE.search("Mr. Jones and Mrs. Smith") is not None


def test_abbreviation_regex_matches_prof() -> None:
    assert _ABBREVIATION_RE.search("Prof. Li") is not None


def test_abbreviation_regex_matches_dept() -> None:
    assert _ABBREVIATION_RE.search("the Dept. of Health") is not None


def test_abbreviation_regex_matches_etc() -> None:
    assert _ABBREVIATION_RE.search("apples, oranges, etc.") is not None


def test_abbreviation_regex_matches_approx() -> None:
    assert _ABBREVIATION_RE.search("approx. 5 km") is not None


def test_abbreviation_regex_matches_case_insensitive() -> None:
    assert _ABBREVIATION_RE.search("the U.S.") is not None
    assert _ABBREVIATION_RE.search("the u.s. economy") is not None
    assert _ABBREVIATION_RE.search("PH.D. in linguistics") is not None


# ---------------------------------------------------------------------------
# _split_sentences_regex abbreviation protection (direct unit tests)
# These bypass spaCy and test ONLY the regex fallback path.
# ---------------------------------------------------------------------------

def test_regex_fallback_us_abbreviation_not_split() -> None:
    """Direct test of _split_sentences_regex: U.S. must not be split."""
    # Build paragraph spans manually
    text = "The U.S. Centers for Disease Control is important."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_ie_abbreviation_not_split() -> None:
    """Direct test of _split_sentences_regex: i.e. must not be split."""
    text = "The best choice, i.e., option A, was selected."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_eg_abbreviation_not_split() -> None:
    """Direct test of _split_sentences_regex: e.g. must not be split."""
    text = "Many fruits are healthy, e.g., apple, orange, and banana."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_phd_abbreviation_not_split() -> None:
    """Direct test of _split_sentences_regex: Ph.D. must not be split."""
    text = "She earned a Ph.D. in linguistics last year."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_dr_abbreviation_not_split() -> None:
    """Direct test of _split_sentences_regex: Dr. must not be split."""
    text = "Dr. Smith works at the local hospital."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_multiple_abbreviations_not_split() -> None:
    """Direct test: Dr. Jane Smith, Ph.D., works at the U.S. Dept. of Health."""
    text = "Dr. Jane Smith, Ph.D., works at the U.S. Dept. of Health."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_abbreviation_then_real_sentence_split() -> None:
    """After protecting abbreviations, real sentence boundaries still work."""
    text = "The U.S. economy is strong. The weather is nice today."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    assert len(sentences) == 2, f"Expected 2 sentences, got {len(sentences)}: {[s.text for s in sentences]}"


def test_regex_fallback_spans_are_correct() -> None:
    """Sentence spans from regex fallback must match the original text exactly."""
    text = "The U.S. economy is strong. The weather is nice today."
    spans = [(0, len(text))]
    sentences = _split_sentences_regex(text, spans)
    for sent in sentences:
        extracted = text[sent.sentence_span.start:sent.sentence_span.end]
        assert extracted == sent.text, (
            f"Span mismatch: span={sent.sentence_span} "
            f"extracted={extracted!r} expected={sent.text!r}"
        )


# ---------------------------------------------------------------------------
# spaCy unavailable → explicit fallback action names
# ---------------------------------------------------------------------------


def test_layer5_split_no_spacy_records_explicit_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When spaCy is unavailable and fast_path=True is requested,
    layer5_split must record 'regex_sentence_split_no_spacy', NOT 'regex_sentence_split'.
    """
    # Simulate spaCy being unavailable by patching _spacy_available
    import app.services.analysis.preprocess.input_preparation as inp

    monkeypatch.setattr(inp, "_spacy_available", False)
    # Reset the singleton so layer5_split will use the patched value
    original_nlp = inp._nlp
    inp._nlp = None

    try:
        _, sentences, actions = inp.layer5_split("Hello, world!", fast_path=True)
        split_actions = [a for a in actions if "sentence_split" in a]
        assert "regex_sentence_split_no_spacy" in split_actions, (
            f"Expected 'regex_sentence_split_no_spacy' in {split_actions}"
        )
        assert "regex_sentence_split" not in split_actions, (
            f"'regex_sentence_split' should NOT appear when spaCy is unavailable, got {split_actions}"
        )
    finally:
        inp._nlp = original_nlp


def test_layer5_split_spaCy_available_records_spaCy_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When spaCy IS available and fast_path=True,
    layer5_split must record 'spacy_sentence_split'.
    """
    import app.services.analysis.preprocess.input_preparation as inp

    # Ensure spaCy is checked
    inp._check_spacy_model()
    if not inp._spacy_available:
        pytest.skip("spaCy not available in this environment")

    original_nlp = inp._nlp
    inp._nlp = None  # Force reload

    try:
        _, sentences, actions = inp.layer5_split("Hello, world!", fast_path=True)
        split_actions = [a for a in actions if "sentence_split" in a]
        assert "spacy_sentence_split" in split_actions, (
            f"Expected 'spacy_sentence_split' in {split_actions}"
        )
    finally:
        inp._nlp = original_nlp


def test_layer5_split_forced_regex_action_records_exactly_that(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When forced_regex_action='regex_sentence_split_no_spacy' is passed,
    the recorded action must be exactly that — not generic 'regex_sentence_split'.
    """
    import app.services.analysis.preprocess.input_preparation as inp

    monkeypatch.setattr(inp, "_spacy_available", True)  # spaCy available but we force regex

    _, sentences, actions = inp.layer5_split(
        "Hello, world!",
        fast_path=False,
        forced_regex_action="regex_sentence_split_no_spacy",
    )
    split_actions = [a for a in actions if "sentence_split" in a]
    assert "regex_sentence_split_no_spacy" in split_actions
    assert "regex_sentence_split" not in split_actions


def test_normal_regex_path_records_generic_action() -> None:
    """
    When fast_path=False with NO forced_regex_action (normal structured_doc path),
    action should be generic 'regex_sentence_split'.
    """
    import app.services.analysis.preprocess.input_preparation as inp

    # Temporarily set _spacy_available to True to isolate the fast_path=False path
    saved = inp._spacy_available
    inp._spacy_available = True

    try:
        _, sentences, actions = inp.layer5_split(
            "- item1\n- item2\n- item3",
            fast_path=False,
        )
        split_actions = [a for a in actions if "sentence_split" in a]
        assert "regex_sentence_split" in split_actions
    finally:
        inp._spacy_available = saved


# ---------------------------------------------------------------------------
# prepare_input integration tests
# ---------------------------------------------------------------------------


def _fast_path_sentences(text: str) -> list[str]:
    """Helper: run prepare_input and return sentence texts."""
    result = prepare_input(text)
    return [s.text for s in result.sentences]


def test_us_abbreviation_not_split() -> None:
    """The U.S. Centers for Disease Control should be ONE sentence."""
    sentences = _fast_path_sentences("The U.S. Centers for Disease Control is important.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"
    assert "The U.S. Centers for Disease Control is important." in sentences[0]


def test_uk_abbreviation_not_split() -> None:
    """The U.K. government should be ONE sentence."""
    sentences = _fast_path_sentences("The U.K. government announced new policies.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_phd_abbreviation_not_split() -> None:
    """She earned a Ph.D. in linguistics should be ONE sentence."""
    sentences = _fast_path_sentences("She earned a Ph.D. in linguistics last year.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_eg_abbreviation_not_split() -> None:
    """e.g., apple should be part of the same sentence."""
    sentences = _fast_path_sentences("Many fruits are healthy, e.g., apple, orange, and banana.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_ie_abbreviation_not_split() -> None:
    """i.e., (that is) should not cause a split."""
    sentences = _fast_path_sentences("The best choice, i.e., option A, was selected.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_dr_abbreviation_not_split() -> None:
    """Dr. Smith works here should be ONE sentence."""
    sentences = _fast_path_sentences("Dr. Smith works at the local hospital.")
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_mr_mrs_ms_abbreviations() -> None:
    """Common titles should not cause splits."""
    sentences = _fast_path_sentences(
        "Mr. Smith and Mrs. Jones attended the meeting with Ms. Brown."
    )
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_multiple_abbreviations_in_one_sentence() -> None:
    """Text with multiple abbreviations should still be one sentence."""
    sentences = _fast_path_sentences(
        "Dr. Jane Smith, Ph.D., works at the U.S. Dept. of Health."
    )
    assert len(sentences) == 1, f"Expected 1 sentence, got {len(sentences)}: {sentences}"


def test_normal_period_after_abbreviation_followed_by_real_sentence_end() -> None:
    """After protecting abbreviations, normal sentence splits should still work."""
    sentences = _fast_path_sentences(
        "The U.S. economy is strong. The weather is nice today."
    )
    assert len(sentences) == 2, f"Expected 2 sentences, got {len(sentences)}: {sentences}"


# ---------------------------------------------------------------------------
# Short but valid English texts (fast-path should handle these)
# ---------------------------------------------------------------------------


def test_short_english_single_sentence() -> None:
    """A short single sentence should still use spaCy (fast_path)."""
    result = prepare_input("Hello, world!")
    assert len(result.sentences) == 1
    assert result.sentences[0].text == "Hello, world!"
    assert result.fast_path is True, f"Expected fast_path=True for short English, got {result.fast_path}"


def test_short_english_two_sentences() -> None:
    """Short two-sentence English should still use spaCy."""
    result = prepare_input("Hello, world! How are you?")
    assert len(result.sentences) == 2
    assert result.fast_path is True, f"Expected fast_path=True for short English, got {result.fast_path}"


def test_short_english_no_below_50_chars() -> None:
    """A short English paragraph (below 50 chars) should still process."""
    result = prepare_input("Hello!")
    assert len(result.sentences) >= 1
    assert "quality_fail_too_short" in result.sanitize_report.actions


# ---------------------------------------------------------------------------
# Mixed language and structured text
# ---------------------------------------------------------------------------


def test_mixed_chinese_english() -> None:
    """Mixed Chinese-English text should be classified appropriately."""
    text = "今天天气很好。The sun is shining. 这是一个测试。"
    result = prepare_input(text)
    assert result.text_type in ("article_mixed", "article_en"), (
        f"Expected article_mixed or article_en, got {result.text_type}"
    )
    assert result.fast_path is False, "Mixed language should not use fast_path"


def test_structured_doc_parameter_style() -> None:
    """Parameter documentation with bullet list should be classified as structured_doc."""
    result = prepare_input(
        """
- name: string
- age: number
- email: string
- active: boolean
        """.strip()
    )
    assert result.text_type == "structured_doc", (
        f"Expected structured_doc, got {result.text_type}"
    )
    assert result.fast_path is False, "structured_doc should not use fast_path"


def test_html_like_text() -> None:
    """Dense HTML should be classified as html_like."""
    result = prepare_input("<div><p>Hello</p><p>World</p></div>Visit <a href=\"http://example.com\">here</a>.")
    assert "<div>" not in result.render_text
    assert "Hello" in result.render_text


# ---------------------------------------------------------------------------
# Sentence span mapping accuracy
# ---------------------------------------------------------------------------


def test_sentence_span_exact_mapping() -> None:
    """Each sentence's span should exactly match the text in render_text."""
    result = prepare_input("Hello, world! How are you today?")
    for sent in result.sentences:
        extracted = result.render_text[sent.sentence_span.start:sent.sentence_span.end]
        assert extracted == sent.text, (
            f"Span mismatch: span={sent.sentence_span} "
            f"extracted={extracted!r} expected={sent.text!r}"
        )


def test_paragraph_span_exact_mapping() -> None:
    """Each paragraph's span should exactly match the text in render_text."""
    result = prepare_input("First paragraph.\n\nSecond paragraph.")
    for para in result.paragraphs:
        extracted = result.render_text[para.render_span.start:para.render_span.end]
        assert extracted == para.text, (
            f"Paragraph span mismatch: span={para.render_span} "
            f"extracted={extracted!r} expected={para.text!r}"
        )


def test_crlf_blank_line_preserves_two_paragraphs() -> None:
    """CRLF blank lines must survive sanitization so paragraphs do not merge."""
    result = prepare_input("First paragraph.\r\n\r\nSecond paragraph.")

    assert result.render_text == "First paragraph.\n\nSecond paragraph."
    assert len(result.paragraphs) == 2, [p.text for p in result.paragraphs]
    assert result.paragraphs[0].text == "First paragraph."
    assert result.paragraphs[1].text == "Second paragraph."


def test_heading_line_is_split_from_first_body_sentence() -> None:
    """
    A short heading line followed by prose should not be merged into the first
    body sentence, otherwise translation/grammar alignment drifts.
    """
    text = (
        "April Fool's traditions\n"
        "In the UK, jokes and tricks can be played up until noon on 1 April.\n"
        "After midday it's considered bad luck to play a trick."
    )
    result = prepare_input(text)

    assert len(result.paragraphs) == 2, [p.text for p in result.paragraphs]
    assert result.paragraphs[0].text == "April Fool's traditions"
    assert result.paragraphs[1].text.startswith("In the UK, jokes and tricks")

    assert len(result.sentences) == 3, [s.text for s in result.sentences]
    assert result.sentences[0].text == "April Fool's traditions"
    assert result.sentences[1].text == "In the UK, jokes and tricks can be played up until noon on 1 April."
    assert result.sentences[2].text == "After midday it's considered bad luck to play a trick."


# ---------------------------------------------------------------------------
# _check_spacy_model behavior
# ---------------------------------------------------------------------------


def test_check_spacy_model_returns_bool() -> None:
    """_check_spacy_model() returns a bool (True/False), never raises."""
    result = _check_spacy_model()
    assert isinstance(result, bool)
    # Second call should return cached value
    result2 = _check_spacy_model()
    assert result == result2


def test_fast_path_check_no_length_gate() -> None:
    """
    _is_fast_path_eligible must NOT block short English texts from fast_path.
    The length check belongs in quality detection, not fast-path decision.
    """
    short_english_hint = _StructureHint(
        has_html_tags=False,
        html_tag_count=0,
        has_code_fences=False,
        bullet_density=0.0,
        cjk_ratio=0.0,
        text_type="article_en",
    )
    eligible, reason = _is_fast_path_eligible(
        hint=short_english_hint,
        english_ratio=0.95,
        text="Hello, world!",  # Short but valid English
    )
    assert eligible is True, f"Short English should be fast_path eligible, reason={reason}"
    assert reason is None
