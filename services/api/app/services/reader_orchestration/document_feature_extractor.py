"""Deterministic document feature extractor + article route classifier.

This is the T4.1 small Module that turns a stable reading base's text +
``reading_units`` unit-type sequence + resolved ``ReaderVariantStrategy``
into a deterministic, replayable feature profile, and then classifies the
article into one of three routing modes:

    - ``SHORT_BATCH``        -> single whole-article batch job
    - ``STRUCTURED_BATCH``   -> single whole-article batch job (medium tier;
                                 landing zone for the future bounded planner)
    - ``GROUPED_WINDOWED``   -> per-window batch jobs (translate_article /
                                 build_vocabulary_layer_article windows)

Design contract (see docs/initiatives/reader-agentic-orchestration/
adaptive-reader-orchestration-design.md §5.2 / §6.1 / §6.3):

    - Raw ``content_utf16_length`` is NO LONGER the sole short/non-short
      discriminator. ``estimated_word_count`` is the PRIMARY router; the
      UTF-16 length survives only as a coarse guardrail capping the
      structured-batch tier so a single batch job never receives an
      oversized input.
    - The extractor and classifier are PURE functions of their inputs.
      Given the same ``(base_text, unit_types, reading_goal,
      reading_variant, requested_layers)`` they always return the same
      profile and route. This makes every routing decision replayable
      offline without a database or LLM.
    - No tokenizer dependency. ``estimated_token_count`` is a deterministic
      word/char heuristic (non-CJK ~1.4 tokens/word, CJK ~1.5 tokens/char),
      calibrated so a 984-English-word BBC article estimates ~1380 tokens,
      matching design §6.1 (``estimated_token_count < 2000``).

This module does NOT:
    - call the database (the caller loads ``base_text`` and ``unit_types``),
    - call any LLM (no profiler),
    - decide window boundaries (that stays in ``job_bootstrap`` window
      planners),
    - change structured-batch execution (this round only fixes routing;
      STRUCTURED_BATCH reuses the existing whole-article batch path).

Stable base / job runtime / publisher / publish fence / usage event
boundaries are untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

DOCUMENT_FEATURE_EXTRACTOR_VERSION = "document_feature_v1"

# ---------------------------------------------------------------------------#
# Routing thresholds
# ---------------------------------------------------------------------------#
#
# ``SHORT_ARTICLE_MAX_WORD_COUNT`` is the PRIMARY short-batch discriminator.
# It covers the reuters_bbc_970 golden sample (~984 words) with margin so a
# slightly-longer BBC near-threshold article (~990-1010 words / ~6100-6500
# chars) -- which the legacy raw-``content_utf16_length`` router sent into
# the heavy grouped/windowed path -- is correctly kept on the short batch
# path. See design §6.1: ``estimated_token_count < 2000`` (~1400 tokens for
# 984 English words).
SHORT_ARTICLE_MAX_WORD_COUNT: Final[int] = 1100

# ``STRUCTURED_ARTICLE_MAX_WORD_COUNT`` caps the structured-batch tier. An
# article whose word count falls in ``(SHORT_ARTICLE_MAX_WORD_COUNT,
# STRUCTURED_ARTICLE_MAX_WORD_COUNT]`` and whose UTF-16 length stays under
# the char guardrail routes to STRUCTURED_BATCH -- a single whole-article
# batch job, NOT grouped/windowed. This is the missing middle tier called
# out in the implementation plan ("中间缺少一个真正的 structured batch
# 中档模式").
#
# The cap is chosen so the existing long-article fixtures -- 8 paragraphs of
# 40 placeholder sentences (~2240 words / ~18k chars) -- stay on the
# grouped/windowed path with margin, preserving the T3.1 / T3.2b window
# contracts.
STRUCTURED_ARTICLE_MAX_WORD_COUNT: Final[int] = 2000

# ``STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL`` is the coarse UTF-16 guardrail
# (design §6.1) capping the structured tier. Even when the word count says
# "structured", an article whose UTF-16 length exceeds this guardrail falls
# through to grouped/windowed so a single batch job never receives an
# oversized input. 12000 is 2x the legacy short char threshold and safely
# above the medium-fixture size (~10-11k chars) while below the long-fixture
# size (~18k chars).
STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL: Final[int] = 12000

# Token-estimate factors (deterministic, no tokenizer).
_NON_CJK_TOKEN_FACTOR: Final[float] = 1.4
_CJK_TOKEN_FACTOR: Final[float] = 1.5

# CJK ideographs / Hangul / Hiragana-Katakana -- each counted as one word
# because CJK text does not separate words with spaces. These ranges are
# ALSO excluded from the non-CJK word-char class below so a pure-CJK token
# is not double-counted (once as a "word" and again per ideograph).
_CJK_CHAR_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3040-\u309f\u30a0-\u30ff"
    r"\uac00-\ud7af]"
)
# Build the negated-CJK range string once so both the char class and the
# word-char class stay in sync if ranges are ever extended.
_CJK_RANGES_IN_CLASS = (
    r"\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3040-\u309f\u30a0-\u30ff"
    r"\uac00-\ud7af"
)
# A "word" character for non-CJK scripts: any Unicode letter or digit
# (``\w`` minus ``_``), EXCLUDING CJK/Hangul/Kana so those ideographs are
# only counted via ``_CJK_CHAR_PATTERN``. This covers Latin, Cyrillic,
# Arabic, Greek, Devanagari, Thai, etc. -- fixing the P1 regression where
# a long Cyrillic/Arabic article was counted as 0 words and misrouted to
# SHORT_BATCH.
_NON_CJK_WORD_CHAR = re.compile(rf"[^\W_{_CJK_RANGES_IN_CLASS}]")


class ArticleRoute(str, Enum):
    """Three-mode article routing decision (design §6 strategy families)."""

    SHORT_BATCH = "short_batch"
    STRUCTURED_BATCH = "structured_batch"
    GROUPED_WINDOWED = "grouped_windowed"


@dataclass(frozen=True, slots=True)
class DocumentFeatureProfile:
    """Deterministic, replayable feature profile of a stable reading base.

    Every field is derived purely from ``(base_text, unit_types,
    reading_goal, reading_variant, requested_layers)``. Two records with
    the same inputs produce identical profiles, so the routing decision is
    fully replayable offline.

    Attributes:
        content_utf16_length: UTF-16 code-unit length of ``base_text``
            (matches the ``reading_bases.content_utf16_length`` column
            semantics; surrogate pairs count as 2). Retained as a coarse
            guardrail and observability signal, NOT the primary router.
        estimated_word_count: Non-CJK whitespace-token count + CJK
            ideograph count. Covers Latin, Cyrillic, Arabic, Greek, etc.
            Primary routing signal.
        estimated_token_count: Deterministic token estimate
            (non-CJK words * 1.4 + CJK chars * 1.5), rounded. Coarse; used
            for observability and future planner budgets.
        unit_count: Number of stable reading units (``len(unit_types)``).
        paragraph_count: Units classified as ``body`` or ``fallback``
            (paragraph-like) by the base builder.
        heading_count: Units classified as ``heading``.
        list_item_count: Units classified as ``list``.
        quote_count: Units classified as ``quote``.
        unknown_block_count: Units classified as ``unknown``.
        structural_noise_ratio: ``(list + quote + unknown) / unit_count``
            (0.0 when ``unit_count == 0``). High values indicate
            non-plain-paragraph structure; recorded for the future bounded
            planner, not used as a primary router this round.
        requested_layers: Tuple of layer names from the resolved strategy
            (e.g. ``("translation", "vocabulary", "grammar_bundle", "ask")``).
        reading_goal: The resolved strategy's ``reading_goal``, if any.
        reading_variant: The resolved strategy's ``reading_variant``, if any.
        extractor_version: Version label for replayability.
    """

    content_utf16_length: int
    estimated_word_count: int
    estimated_token_count: int
    unit_count: int
    paragraph_count: int
    heading_count: int
    list_item_count: int
    quote_count: int
    unknown_block_count: int
    structural_noise_ratio: float
    requested_layers: tuple[str, ...]
    reading_goal: str | None
    reading_variant: str | None
    extractor_version: str = DOCUMENT_FEATURE_EXTRACTOR_VERSION


def _count_words(base_text: str) -> tuple[int, int]:
    """Return ``(non_cjk_word_count, cjk_char_count)`` for ``base_text``.

    Non-CJK words are whitespace-separated tokens containing at least one
    Unicode letter or digit OUTSIDE the CJK/Hangul/Kana ranges (so a
    pure-CJK token is not double-counted). This covers Latin, Cyrillic,
    Arabic, Greek, Devanagari, Thai, etc. -- any script that separates
    words with whitespace. Pure-punctuation tokens (e.g. "--", "...") are
    not counted. CJK / Hangul / kana ideographs are counted individually
    because CJK does not separate words with spaces.
    """
    non_cjk_words = 0
    for token in base_text.split():
        if _NON_CJK_WORD_CHAR.search(token):
            non_cjk_words += 1
    cjk_chars = len(_CJK_CHAR_PATTERN.findall(base_text))
    return non_cjk_words, cjk_chars


def _utf16_code_unit_length(text: str) -> int:
    """UTF-16 code-unit length (surrogate pairs count as 2).

    Matches ``app.contracts.annotation.utf16_code_unit_length`` and the
    ``reading_bases.content_utf16_length`` CHECK constraint. Inlined here
    to keep the extractor pure and free of cross-module imports.
    """
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def extract_document_features(
    *,
    base_text: str,
    unit_types: tuple[str, ...],
    reading_goal: str | None,
    reading_variant: str | None,
    requested_layers: tuple[str, ...],
) -> DocumentFeatureProfile:
    """Build a deterministic :class:`DocumentFeatureProfile`.

    Pure function: identical inputs always yield an identical profile.

    Args:
        base_text: The canonical stable reading base text (already
            canonicalized by the base builder / stable document freeze
            plan). May be empty only for a missing-base defensive path;
            the caller normally guarantees non-empty.
        unit_types: Ordered tuple of ``reading_units.unit_type`` values
            (``body`` / ``heading`` / ``list`` / ``quote`` / ``unknown`` /
            ``fallback``) for every unit of the active base. The caller
            loads this from the database; passing it in keeps the
            extractor pure.
        reading_goal: Resolved strategy ``reading_goal`` (``None`` if the
            caller has no resolved strategy, e.g. a defensive path).
        reading_variant: Resolved strategy ``reading_variant``.
        requested_layers: Layer names from the resolved strategy
            (``strategy.layers.keys()``). Pass an empty tuple when no
            strategy is available.
    """
    non_cjk_words, cjk_chars = _count_words(base_text)
    estimated_word_count = non_cjk_words + cjk_chars
    estimated_token_count = int(
        round(non_cjk_words * _NON_CJK_TOKEN_FACTOR + cjk_chars * _CJK_TOKEN_FACTOR)
    )

    unit_count = len(unit_types)
    paragraph_count = sum(
        1 for unit_type in unit_types if unit_type in ("body", "fallback")
    )
    heading_count = sum(1 for unit_type in unit_types if unit_type == "heading")
    list_item_count = sum(1 for unit_type in unit_types if unit_type == "list")
    quote_count = sum(1 for unit_type in unit_types if unit_type == "quote")
    unknown_block_count = sum(
        1 for unit_type in unit_types if unit_type == "unknown"
    )
    structural_noise_ratio = (
        (list_item_count + quote_count + unknown_block_count) / unit_count
        if unit_count > 0
        else 0.0
    )

    return DocumentFeatureProfile(
        content_utf16_length=_utf16_code_unit_length(base_text),
        estimated_word_count=estimated_word_count,
        estimated_token_count=estimated_token_count,
        unit_count=unit_count,
        paragraph_count=paragraph_count,
        heading_count=heading_count,
        list_item_count=list_item_count,
        quote_count=quote_count,
        unknown_block_count=unknown_block_count,
        structural_noise_ratio=structural_noise_ratio,
        requested_layers=tuple(requested_layers),
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        extractor_version=DOCUMENT_FEATURE_EXTRACTOR_VERSION,
    )


def classify_article_route(profile: DocumentFeatureProfile) -> ArticleRoute:
    """Classify a profile into one of three routing modes.

    Routing rules (deterministic, replayable):

        1. ``estimated_word_count <= SHORT_ARTICLE_MAX_WORD_COUNT``
           -> :data:`ArticleRoute.SHORT_BATCH`.
           Word count is the PRIMARY discriminator. This fixes the
           near-threshold BBC regression where a ~990-word / ~6100-char
           article was wrongly sent to grouped/windowed by the legacy
           raw-``content_utf16_length`` router.

        2. ``SHORT_ARTICLE_MAX_WORD_COUNT < estimated_word_count
           <= STRUCTURED_ARTICLE_MAX_WORD_COUNT`` AND
           ``content_utf16_length <= STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL``
           -> :data:`ArticleRoute.STRUCTURED_BATCH`.
           The missing middle tier: medium articles that still fit safely
           in a single whole-article batch job. The char guardrail is a
           coarse safety cap (design §6.1) so one batch job never receives
           an oversized input.

        3. Otherwise -> :data:`ArticleRoute.GROUPED_WINDOWED`.

    Note: ``STRUCTURED_BATCH`` and ``SHORT_BATCH`` both execute via the
    existing whole-article batch path this round; the route label exists
    to (a) fix the routing decision and (b) provide the landing zone for
    the future bounded planner. Structured-batch execution details are
    intentionally not changed here.
    """
    words = profile.estimated_word_count
    chars = profile.content_utf16_length
    if words <= SHORT_ARTICLE_MAX_WORD_COUNT:
        return ArticleRoute.SHORT_BATCH
    if (
        words <= STRUCTURED_ARTICLE_MAX_WORD_COUNT
        and chars <= STRUCTURED_ARTICLE_MAX_CHAR_GUARDRAIL
    ):
        return ArticleRoute.STRUCTURED_BATCH
    return ArticleRoute.GROUPED_WINDOWED
