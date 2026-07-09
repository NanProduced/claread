"""T4.1 / T4.1a: focused unit tests for the deterministic document feature
extractor and the three-mode article route classifier.

These tests are PURE (no database, no LLM). They pin the routing contract
called out in the task:

    - BBC near-threshold regression: a ~1000-word / ~6300-char article that
      the legacy raw-``content_utf16_length`` router (>6000) would have sent
      into the heavy grouped/windowed path now stays on SHORT_BATCH.
    - A short article does not enter the heavy pipeline.
    - A medium article lands on STRUCTURED_BATCH (the missing middle tier),
      NOT grouped/windowed.
    - A clearly long article still lands on GROUPED_WINDOWED.
    - The char guardrail downgrades an oversized structured candidate to
      grouped/windowed.
    - CJK word counting, profile field population, and replayability.
"""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.document_feature_extractor import (
    DOCUMENT_FEATURE_EXTRACTOR_VERSION,
    ArticleRoute,
    SHORT_ARTICLE_MAX_WORD_COUNT,
    STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL,
    STRUCTURED_ARTICLE_MAX_WORD_COUNT,
    classify_article_route,
    extract_document_features,
)

# A pool of realistic BBC-style English sentences (~145 words when joined).
# Used to build near-threshold / medium / long fixtures with a realistic
# ~6.1 chars-per-word ratio so the char/word boundary is meaningful.
_BBC_SENTENCES = (
    "The findings were published in a peer-reviewed journal on Tuesday morning.",
    "Researchers said the experimental battery can hold more energy per unit of volume than common lithium-ion cells.",
    "The design uses abundant materials rather than rare metals that have constrained supply chains.",
    "Independent experts described the work as technically sound but cautioned that commercial deployment remains years away.",
    "Energy storage is widely seen as a central challenge in the transition away from fossil fuels.",
    "Solar and wind generation fluctuates with the weather so utilities need reliable ways to store surplus electricity.",
    "The team said the battery maintained its performance over thousands of charge and discharge cycles without significant degradation.",
    "Officials in several countries have indicated that storage technology will receive a growing share of public research funding.",
    "The next phase will involve building a larger prototype and partnering with industry manufacturers to test mass production.",
)
_BBC_PARAGRAPH = " ".join(_BBC_SENTENCES)


def _bbc_article(paragraph_count: int) -> str:
    """Build a deterministic BBC-style article with ``paragraph_count`` body
    paragraphs. Each paragraph is the realistic sentence pool prefixed by a
    short section label so paragraphs are not byte-identical."""
    return "\n\n".join(
        f"Section {idx}. {_BBC_PARAGRAPH}" for idx in range(1, paragraph_count + 1)
    )


# BBC near-threshold: 7 paragraphs -> ~1029 words / ~6300 chars. This crosses
# the legacy 6000-char raw-length threshold (so the old router would have
# grouped/windowed it) while staying under the word-based short threshold.
_BBC_NEAR_THRESHOLD_TEXT = _bbc_article(7)

# Medium: 10 paragraphs -> ~1450 words / ~8900 chars. Beyond the short word
# threshold, under the structured word cap, and under the char guardrail.
_MEDIUM_TEXT = _bbc_article(10)

# Long: 16 paragraphs -> ~2300 words / ~14300 chars. Beyond both the
# structured word cap and the char guardrail.
_LONG_TEXT = _bbc_article(16)


def _profile(text: str, unit_types: tuple[str, ...]) -> "object":
    return extract_document_features(
        base_text=text,
        unit_types=unit_types,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        requested_layers=("translation", "vocabulary", "grammar_bundle", "ask"),
    )


# ---------------------------------------------------------------------------#
# BBC near-threshold regression
# ---------------------------------------------------------------------------#


def test_bbc_near_threshold_text_exceeds_legacy_char_threshold() -> None:
    """Sanity: the BBC fixture crosses the legacy 6000-char raw-length
    threshold, which is precisely the condition under which the old router
    wrongly sent a ~1000-word article into the heavy grouped/windowed path."""
    assert len(_BBC_NEAR_THRESHOLD_TEXT) > 6000


def test_bbc_near_threshold_routes_to_short_batch() -> None:
    """T4.1a regression fix: a BBC-style ~1000-word / ~6300-char article
    routes to SHORT_BATCH under the new word-based router, even though its
    raw char length exceeds the legacy 6000 threshold."""
    profile = _profile(_BBC_NEAR_THRESHOLD_TEXT, ("body",) * 7)
    assert profile.estimated_word_count <= SHORT_ARTICLE_MAX_WORD_COUNT
    assert profile.estimated_word_count > 900  # genuinely near-threshold
    assert classify_article_route(profile) is ArticleRoute.SHORT_BATCH


def test_bbc_near_threshold_token_estimate_under_2000() -> None:
    """Design §6.1: short-batch articles should sit under ~2000 estimated
    tokens. The BBC ~1000-word fixture estimates ~1400 tokens."""
    profile = _profile(_BBC_NEAR_THRESHOLD_TEXT, ("body",) * 7)
    assert profile.estimated_token_count < 2000
    assert 1200 <= profile.estimated_token_count <= 1500


# ---------------------------------------------------------------------------#
# Short article does not enter the heavy pipeline
# ---------------------------------------------------------------------------#


def test_short_article_routes_to_short_batch() -> None:
    short_text = (
        "First sentence for the short article fixture.\n\n"
        "Second paragraph with a little more prose.\n\n"
        "Third paragraph to close the short article."
    )
    profile = _profile(short_text, ("body",) * 3)
    assert profile.estimated_word_count <= SHORT_ARTICLE_MAX_WORD_COUNT
    assert classify_article_route(profile) is ArticleRoute.SHORT_BATCH


def test_empty_text_routes_to_short_batch() -> None:
    """Defensive: empty base text -> 0 words -> SHORT_BATCH. (The
    job_bootstrap integration special-cases a missing base row to
    GROUPED_WINDOWED before profiling; this test only pins the pure
    classifier behavior on an empty string.)"""
    profile = _profile("", ())
    assert profile.estimated_word_count == 0
    assert classify_article_route(profile) is ArticleRoute.SHORT_BATCH


# ---------------------------------------------------------------------------#
# Medium article -> STRUCTURED_BATCH (the missing middle tier)
# ---------------------------------------------------------------------------#


def test_medium_article_routes_to_structured_batch() -> None:
    """T4.1a: a medium article (~1450 words / ~8900 chars) routes to
    STRUCTURED_BATCH -- a single whole-article batch job -- NOT to the
    grouped/windowed heavy path."""
    assert len(_MEDIUM_TEXT) > 6000  # legacy router would have grouped it
    profile = _profile(_MEDIUM_TEXT, ("body",) * 10)
    assert SHORT_ARTICLE_MAX_WORD_COUNT < profile.estimated_word_count
    assert profile.estimated_word_count <= STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert profile.content_utf16_length <= STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL
    assert classify_article_route(profile) is ArticleRoute.STRUCTURED_BATCH


def test_medium_article_is_not_grouped_windowed() -> None:
    """Explicit negative: the medium fixture must NOT produce
    GROUPED_WINDOWED, which is the bug being fixed."""
    profile = _profile(_MEDIUM_TEXT, ("body",) * 10)
    assert classify_article_route(profile) is not ArticleRoute.GROUPED_WINDOWED


# ---------------------------------------------------------------------------#
# Long article -> GROUPED_WINDOWED
# ---------------------------------------------------------------------------#


def test_long_article_routes_to_grouped_windowed() -> None:
    """T4.1a: a clearly long article (~2300 words / ~14300 chars) still
    routes to GROUPED_WINDOWED so the existing T3.1 / T3.2b windowed
    execution contract is preserved."""
    profile = _profile(_LONG_TEXT, ("body",) * 16)
    assert profile.estimated_word_count > STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


def test_long_article_exceeding_word_cap_but_under_char_guardrail_still_grouped() -> None:
    """When the word count alone exceeds the structured cap, the article
    routes to grouped/windowed regardless of the char guardrail. This text
    is word-dense but char-sparse (2100 one-letter words / ~4200 chars):
    word cap triggers grouped even though chars are well under the guardrail."""
    word_dense_text = " ".join("a" for _ in range(2100))
    profile = _profile(word_dense_text, ("body",) * 10)
    assert profile.estimated_word_count > STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert profile.content_utf16_length <= STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


# ---------------------------------------------------------------------------#
# Char guardrail downgrade
# ---------------------------------------------------------------------------#


def test_structured_word_range_exceeding_char_guardrail_falls_to_grouped() -> None:
    """If the word count says structured but the UTF-16 length exceeds the
    structured char guardrail, the article falls through to grouped/windowed
    so a single batch job never receives an oversized input."""
    # Build a profile manually in the structured word range but with a char
    # length over the guardrail. Use the real extractor on text that is
    # word-sparse but char-dense (long tokens), then assert the downgrade.
    # 1500 whitespace tokens of ~20 chars each -> ~1500 words / ~30000 chars.
    dense_text = " ".join(f"supercalifragilistic{i:04d}expialidocious" for i in range(1500))
    profile = _profile(dense_text, ("body",))
    assert SHORT_ARTICLE_MAX_WORD_COUNT < profile.estimated_word_count
    assert profile.estimated_word_count <= STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert profile.content_utf16_length > STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


# ---------------------------------------------------------------------------#
# CJK word counting
# ---------------------------------------------------------------------------#


def test_cjk_text_counts_ideographs_as_words() -> None:
    """CJK ideographs are counted individually (CJK does not separate words
    with spaces). A ~960-ideograph Chinese article routes to SHORT_BATCH."""
    # 12 ideographs per repetition (the 。 is CJK punctuation, not counted).
    # 80 repetitions -> 960 ideographs, under the 1100-word short cap.
    cjk_text = "今天天气很好适合外出散步。" * 80
    profile = _profile(cjk_text, ("body",) * 5)
    assert profile.estimated_word_count == 960
    # 960 CJK ideographs -> ~1440 estimated tokens (1.5 factor)
    assert profile.estimated_token_count == 1440
    assert classify_article_route(profile) is ArticleRoute.SHORT_BATCH


def test_mixed_cjk_and_latin_word_counting() -> None:
    """Mixed text counts Latin whitespace tokens AND CJK ideographs."""
    mixed = "The report says 今天天气很好 and the team agreed on the plan."
    profile = _profile(mixed, ("body",))
    # Latin tokens with at least one ASCII alnum: The, report, says, and,
    # the, team, agreed, on, the, plan = 10. CJK ideographs: 今,天,天,气,很,好 = 6.
    assert profile.estimated_word_count == 16


# ---------------------------------------------------------------------------#
# Profile field population from unit_types
# ---------------------------------------------------------------------------#


def test_profile_unit_fields_populated_from_unit_types() -> None:
    unit_types = ("heading", "body", "body", "list", "quote", "unknown", "fallback")
    profile = _profile("Some text here.", unit_types)
    assert profile.unit_count == 7
    assert profile.heading_count == 1
    assert profile.paragraph_count == 3  # body + body + fallback
    assert profile.list_item_count == 1
    assert profile.quote_count == 1
    assert profile.unknown_block_count == 1
    # structural_noise_ratio = (list + quote + unknown) / unit_count = 3/7
    assert profile.structural_noise_ratio == pytest.approx(3 / 7)


def test_profile_empty_unit_types_has_zero_noise_ratio() -> None:
    profile = _profile("Some text here.", ())
    assert profile.unit_count == 0
    assert profile.paragraph_count == 0
    assert profile.structural_noise_ratio == 0.0


def test_profile_records_strategy_signals() -> None:
    profile = extract_document_features(
        base_text="hello world",
        unit_types=("body",),
        reading_goal="exam",
        reading_variant="cet",
        requested_layers=("translation", "vocabulary", "grammar_bundle", "ask"),
    )
    assert profile.reading_goal == "exam"
    assert profile.reading_variant == "cet"
    assert profile.requested_layers == (
        "translation",
        "vocabulary",
        "grammar_bundle",
        "ask",
    )


# ---------------------------------------------------------------------------#
# Replayability
# ---------------------------------------------------------------------------#


def test_extract_is_pure_and_replayable() -> None:
    """Identical inputs must yield identical profiles (offline replay)."""
    kwargs = {
        "base_text": _BBC_NEAR_THRESHOLD_TEXT,
        "unit_types": ("body",) * 7,
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "requested_layers": ("translation", "vocabulary", "grammar_bundle", "ask"),
    }
    a = extract_document_features(**kwargs)
    b = extract_document_features(**kwargs)
    assert a == b
    assert a.extractor_version == DOCUMENT_FEATURE_EXTRACTOR_VERSION


def test_classify_route_is_deterministic() -> None:
    profile = _profile(_MEDIUM_TEXT, ("body",) * 10)
    assert classify_article_route(profile) is classify_article_route(profile)


def test_content_utf16_length_counts_surrogate_pairs() -> None:
    """A supplementary-plane character (emoji) counts as 2 UTF-16 code
    units, matching the ``reading_bases.content_utf16_length`` column."""
    profile = _profile("hello 🌍 world", ("body",))
    # "hello 🌍 world" -> h,e,l,l,o,space,emoji(2),space,w,o,r,l,d
    # code points: 13; UTF-16: 14 (emoji is a surrogate pair)
    assert profile.content_utf16_length == 14


# ---------------------------------------------------------------------------#
# Non-ASCII, non-CJK scripts (Cyrillic / Arabic / Greek) -- P1 regression
# ---------------------------------------------------------------------------#


def test_long_cyrillic_article_is_not_misrouted_to_short_batch() -> None:
    """P1 regression: a long Cyrillic article must NOT be counted as 0
    words. The original ``[A-Za-z0-9]`` word pattern ignored Cyrillic
    entirely, so a 16500-char Russian article was misrouted to
    SHORT_BATCH. The Unicode-aware word counter now counts Cyrillic
    whitespace tokens and routes the article to GROUPED_WINDOWED."""
    cyrillic_text = "привет мир " * 1500  # ~3000 Cyrillic words
    profile = _profile(cyrillic_text, ("body",) * 10)
    assert profile.estimated_word_count > 0
    assert profile.estimated_word_count > STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert profile.content_utf16_length > STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


def test_long_arabic_article_is_not_misrouted_to_short_batch() -> None:
    """P1 regression: a long Arabic article must NOT be counted as 0
    words. Arabic letters are Unicode word characters; the Unicode-aware
    counter routes a long Arabic article to GROUPED_WINDOWED."""
    # "مرحبا بالعالم" = "hello world" in Arabic, 2 whitespace tokens.
    arabic_text = "مرحبا بالعالم " * 1500  # ~3000 Arabic words
    profile = _profile(arabic_text, ("body",) * 10)
    assert profile.estimated_word_count > STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


def test_long_greek_article_is_not_misrouted_to_short_batch() -> None:
    """P1 regression: a long Greek article must NOT be counted as 0
    words. Greek letters are Unicode word characters."""
    # "καλημέρα κόσμε" = "good morning world" in Greek, 2 tokens.
    greek_text = "καλημέρα κόσμε " * 1500
    profile = _profile(greek_text, ("body",) * 10)
    assert profile.estimated_word_count > STRUCTURED_ARTICLE_MAX_WORD_COUNT
    assert classify_article_route(profile) is ArticleRoute.GROUPED_WINDOWED


def test_short_cyrillic_article_routes_to_short_batch() -> None:
    """A genuinely short Cyrillic article still routes to SHORT_BATCH --
    the fix does not over-correct by pushing short non-Latin articles up
    to a heavier tier."""
    short_cyrillic = "Привет мир, это короткая статья для чтения."
    profile = _profile(short_cyrillic, ("body",))
    assert profile.estimated_word_count <= SHORT_ARTICLE_MAX_WORD_COUNT
    assert classify_article_route(profile) is ArticleRoute.SHORT_BATCH


def test_pure_punctuation_tokens_not_counted_as_words() -> None:
    """Pure-punctuation tokens (no Unicode letter or digit) are still
    excluded from the word count after the Unicode broadening."""
    text = "... --- ... " * 50  # 150 pure-punctuation tokens, 0 words
    profile = _profile(text, ("body",))
    assert profile.estimated_word_count == 0
