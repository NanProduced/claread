"""Tests for the shared normalize module (postprocess/normalize.py)."""

from __future__ import annotations

from app.services.analysis.postprocess.normalize import is_substring, normalize_for_comparison


# ---------------------------------------------------------------------------
# normalize_for_comparison
# ---------------------------------------------------------------------------


def test_normalize_for_comparison_curly_quotes() -> None:
    """Curly quotes should be normalized so LLM output matches sanitized text."""
    # LLM outputs curly quotes, sanitized text has straight quotes
    assert normalize_for_comparison("\u201cHello\u201d") == '"Hello"'


def test_normalize_for_comparison_em_dash() -> None:
    """Em dash should be normalized to hyphen for matching."""
    assert normalize_for_comparison("result\u2014as expected") == "result-as expected"


def test_normalize_for_comparison_en_dash() -> None:
    """En dash should be normalized to hyphen for matching."""
    assert normalize_for_comparison("pages 10\u201320") == "pages 10-20"


def test_normalize_for_comparison_nfc() -> None:
    """NFD text should be NFC-normalized for matching."""
    # e + combining acute accent → precomposed é
    nfd_text = "e\u0301lite"
    nfc_text = "\u00e9lite"
    assert normalize_for_comparison(nfd_text) == normalize_for_comparison(nfc_text)


def test_normalize_for_comparison_zero_width() -> None:
    """Zero-width characters should be removed for matching."""
    assert normalize_for_comparison("Hello\u200bworld") == "Helloworld"


def test_normalize_for_comparison_soft_hyphen() -> None:
    """Soft hyphen should be removed for matching."""
    assert normalize_for_comparison("infor\u00admation") == "information"


def test_normalize_for_comparison_ellipsis() -> None:
    """Ellipsis character should be normalized to three dots."""
    assert normalize_for_comparison("and then\u2026") == "and then..."


def test_normalize_for_comparison_whitespace_compression() -> None:
    """Multiple spaces should be compressed to single space."""
    assert normalize_for_comparison("Hello   world") == "Hello world"


def test_normalize_for_comparison_nbsp() -> None:
    """NBSP should be treated as regular space after normalization."""
    # Note: NBSP is not in _UNICODE_SPACE_MAP in normalize.py,
    # but it IS whitespace, so " ".join(text.split()) handles it.
    assert normalize_for_comparison("Hello\u00a0world") == "Hello world"


# ---------------------------------------------------------------------------
# is_substring
# ---------------------------------------------------------------------------


def test_is_substring_strict_match() -> None:
    """Strict substring match should succeed without normalization."""
    assert is_substring("Hello world", "I say Hello world today")


def test_is_substring_normalized_fallback_curly_quotes() -> None:
    """LLM output with curly quotes should match sanitized text with straight quotes."""
    # sentence_text has straight quotes (after sanitize_text)
    # anchor_text has curly quotes (from LLM output)
    assert is_substring("\u201cHello\u201d", 'I say "Hello" today')


def test_is_substring_normalized_fallback_em_dash() -> None:
    """LLM output with em dash should match sanitized text with hyphen."""
    assert is_substring("result\u2014as expected", "the result-as expected was clear")


def test_is_substring_normalized_fallback_whitespace() -> None:
    """Whitespace differences should be handled by normalization."""
    assert is_substring("Hello  world", "I say Hello world today")


def test_is_substring_no_match() -> None:
    """Completely different text should not match."""
    assert not is_substring("xyz", "Hello world")


def test_is_substring_nfc_fallback() -> None:
    """NFD anchor text should match NFC sentence text."""
    nfd_anchor = "e\u0301lite"
    nfc_sentence = "The \u00e9lite group was selected."
    assert is_substring(nfd_anchor, nfc_sentence)


def test_is_substring_zero_width_fallback() -> None:
    """Anchor with zero-width space should match sentence without it."""
    assert is_substring("Hello\u200bworld", "I say Helloworld today")
