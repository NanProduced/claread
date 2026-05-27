"""Tests for reader_ask resolver: cross-language matching, weak-semantic matching,
disambiguation, and low-confidence rejection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.reader_ask import planner
from app.services.reader_ask import resolver


# ---------------------------------------------------------------------------
# _score_title_match unit tests
# ---------------------------------------------------------------------------


class TestScoreTitleMatch:
    """Unit tests for _score_title_match covering cross-language and weak matches."""

    def test_exact_match(self) -> None:
        assert resolver._score_title_match("Climate Policy", "Climate Policy") == 100

    def test_prefix_match(self) -> None:
        assert resolver._score_title_match("Climate", "Climate Policy") == 90

    def test_substring_match(self) -> None:
        assert resolver._score_title_match("Policy", "Climate Policy") == 80

    def test_chinese_query_english_title(self) -> None:
        """Chinese '气候' should match English title containing 'Climate'."""
        score = resolver._score_title_match("气候", "Climate Change Impact")
        assert score >= 50, f"Expected cross-lang match, got {score}"

    def test_chinese_query_english_title_ai(self) -> None:
        """Chinese '人工智能' should match English title containing 'AI'."""
        score = resolver._score_title_match("人工智能", "AI and the Future of Work")
        assert score >= 50, f"Expected cross-lang match, got {score}"

    def test_english_query_chinese_title(self) -> None:
        """English 'AI' should match Chinese title containing '人工智能'."""
        score = resolver._score_title_match("AI", "人工智能的未来")
        assert score >= 50, f"Expected cross-lang match, got {score}"

    def test_mixed_language_query(self) -> None:
        """Mixed '气候 Policy' should match 'Climate Policy' via English tokens."""
        score = resolver._score_title_match("气候 Policy", "Climate Policy")
        assert score >= 50, f"Expected mixed-lang match, got {score}"

    def test_no_match_unrelated(self) -> None:
        """Completely unrelated query and title should score 0."""
        score = resolver._score_title_match("烹饪技巧", "Quantum Computing Basics")
        assert score == 0, f"Expected no match, got {score}"

    def test_partial_title_match(self) -> None:
        """Partial title should still score reasonably."""
        score = resolver._score_title_match("Climate", "The Climate Change Report")
        assert score >= 60, f"Expected partial match, got {score}"

    def test_low_confidence_not_mistakenly_matched(self) -> None:
        """A very weak cross-lang match (single generic word) should not
        produce a high score that would bypass the <70 threshold."""
        score = resolver._score_title_match("问题", "The Problem of Evil")
        # "问题" maps to "problem"/"issue" — this IS a valid cross-lang match
        # but should score in the 50-55 range, below the 70 resolved threshold
        assert 50 <= score <= 55, f"Expected moderate cross-lang score, got {score}"


# ---------------------------------------------------------------------------
# _cross_lang_score unit tests
# ---------------------------------------------------------------------------


class TestCrossLangScore:
    def test_chinese_climate_english_climate(self) -> None:
        assert resolver._cross_lang_score("气候变化", "Climate Change Impact") >= 50

    def test_english_ai_chinese_ai(self) -> None:
        assert resolver._cross_lang_score("AI technology", "人工智能技术") >= 50

    def test_no_cross_lang_overlap(self) -> None:
        assert resolver._cross_lang_score("烹饪", "Quantum Computing") == 0

    def test_english_token_overlap_in_mixed_query(self) -> None:
        """When query has English tokens that appear in the title."""
        score = resolver._cross_lang_score("关于 Climate 的文章", "Climate Policy Review")
        assert score >= 50

    def test_ai_not_matching_asia(self) -> None:
        """Chinese '人工智能' maps to 'ai', but 'ai' must NOT match 'Asia'
        via substring — token-level matching prevents this false positive."""
        score = resolver._cross_lang_score("人工智能", "Economic Growth in Asia")
        assert score == 0, f"'ai' should not match 'Asia' via substring, got {score}"

    def test_ai_matches_ai_title(self) -> None:
        """Chinese '人工智能' maps to 'ai', and should match a title with
        'AI' as a standalone token."""
        score = resolver._cross_lang_score("人工智能", "AI and the Future of Work")
        assert score >= 50, f"Expected cross-lang match for 'AI' token, got {score}"

    def test_short_abbreviation_no_substring_false_positive(self) -> None:
        """Other short abbreviations should not cause substring false positives.
        'ml' maps from '机器学习' but must not match 'html', 'xml', etc."""
        score = resolver._cross_lang_score("机器学习", "Introduction to HTML and XML")
        assert score == 0, f"'ml' should not match 'html'/'xml' via substring, got {score}"

    def test_multiword_english_phrase_matches_chinese_title(self) -> None:
        """English 'artificial intelligence' should match Chinese title '人工智能的未来'."""
        score = resolver._cross_lang_score("artificial intelligence", "人工智能的未来")
        assert score >= 50, f"Expected multi-word phrase match, got {score}"

    def test_multiword_machine_learning_matches_chinese(self) -> None:
        """English 'machine learning' should match Chinese title '机器学习入门'."""
        score = resolver._cross_lang_score("machine learning", "机器学习入门指南")
        assert score >= 50, f"Expected multi-word phrase match, got {score}"

    def test_multiword_phrase_no_false_positive(self) -> None:
        """English 'artificial intelligence' should NOT match unrelated Chinese title."""
        score = resolver._cross_lang_score("artificial intelligence", "气候变化的影响")
        assert score == 0, f"Expected no match for unrelated title, got {score}"


# ---------------------------------------------------------------------------
# resolve_known_references integration tests
# ---------------------------------------------------------------------------


def _make_reference_needs(query: str) -> planner.ReaderAskReferenceNeeds:
    return planner.ReaderAskReferenceNeeds(requested=True, query=query)


async def test_resolve_chinese_query_finds_english_title() -> None:
    """When ILIKE returns nothing, fallback to recent records + cross-lang
    scoring should present the English-titled article as an ambiguous candidate."""
    user_id = uuid4()
    current_record_id = uuid4()
    target_record_id = uuid4()

    # ILIKE returns nothing (Chinese query won't match English title)
    ilike_finder = AsyncMock(return_value=[])

    # list_recent_records returns the target article
    recent_rows = [
        {"id": str(target_record_id), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        {"id": str(uuid4()), "title": "Quantum Computing Basics", "updated_at": "2026-05-02"},
    ]

    with (
        patch.object(resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
    ):
        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("气候"),
            finder=ilike_finder,
        )

    # Cross-lang score is 55 (50-69 range) → ambiguous, not not_found
    assert result.status == "ambiguous"
    assert result.ambiguous_records is not None
    # "Climate Change Impact" must be in the candidates
    candidate_titles = [r.get("title", "") for r in result.ambiguous_records]
    assert any("Climate" in t for t in candidate_titles), f"Expected 'Climate' in candidates, got {candidate_titles}"
    # "Quantum Computing" must NOT be in the candidates (no cross-lang match)
    assert not any("Quantum" in t for t in candidate_titles), f"Unexpected 'Quantum' in candidates: {candidate_titles}"


async def test_resolve_weak_title_goes_ambiguous() -> None:
    """When multiple articles have similar cross-lang scores, result should
    be ambiguous with both articles as candidates."""
    user_id = uuid4()
    current_record_id = uuid4()
    record_id_1 = uuid4()
    record_id_2 = uuid4()

    # Both articles match "经济" via cross-lang
    rows = [
        {"id": str(record_id_1), "title": "Economic Growth in Asia", "updated_at": "2026-05-01"},
        {"id": str(record_id_2), "title": "The Economics of AI", "updated_at": "2026-05-02"},
    ]
    finder = AsyncMock(return_value=rows)

    result = await resolver.resolve_known_references(
        user_id=user_id,
        current_record_id=current_record_id,
        reference_needs=_make_reference_needs("经济"),
        finder=finder,
    )

    # Both have cross-lang score ~55 (50-69 range) → ambiguous
    assert result.status == "ambiguous"
    assert result.ambiguous_records is not None
    assert len(result.ambiguous_records) == 2
    candidate_titles = [r.get("title", "") for r in result.ambiguous_records]
    assert "Economic Growth in Asia" in candidate_titles
    assert "The Economics of AI" in candidate_titles


async def test_resolve_exact_title_auto_resolves() -> None:
    """Exact title match should auto-resolve without disambiguation."""
    user_id = uuid4()
    current_record_id = uuid4()
    target_record_id = uuid4()

    rows = [
        {"id": str(target_record_id), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
    ]
    finder = AsyncMock(return_value=rows)

    result = await resolver.resolve_known_references(
        user_id=user_id,
        current_record_id=current_record_id,
        reference_needs=_make_reference_needs("Climate Change Impact"),
        finder=finder,
    )

    assert result.status == "resolved"
    assert result.resolved_records is not None
    assert len(result.resolved_records) == 1


async def test_resolve_low_confidence_returns_not_found() -> None:
    """When the best match score is below 50, return not_found instead of
    a wrong match or ambiguous candidates."""
    user_id = uuid4()
    current_record_id = uuid4()

    # Unrelated article — "烹饪" has no cross-lang mapping, so score = 0
    rows = [
        {"id": str(uuid4()), "title": "Quantum Computing Basics", "updated_at": "2026-05-01"},
    ]
    finder = AsyncMock(return_value=rows)

    result = await resolver.resolve_known_references(
        user_id=user_id,
        current_record_id=current_record_id,
        reference_needs=_make_reference_needs("烹饪技巧"),
        finder=finder,
    )

    assert result.status == "not_found"
    assert result.ambiguous_records is None or len(result.ambiguous_records) == 0


async def test_resolve_ai_query_excludes_asia_title() -> None:
    """Chinese '人工智能' maps to 'ai', but 'ai' must NOT match 'Asia'
    via substring. Only titles with 'AI' as a standalone token should appear."""
    user_id = uuid4()
    current_record_id = uuid4()
    ai_record_id = uuid4()
    asia_record_id = uuid4()

    rows = [
        {"id": str(asia_record_id), "title": "Economic Growth in Asia", "updated_at": "2026-05-01"},
        {"id": str(ai_record_id), "title": "AI and the Future of Work", "updated_at": "2026-05-02"},
    ]
    finder = AsyncMock(return_value=rows)

    result = await resolver.resolve_known_references(
        user_id=user_id,
        current_record_id=current_record_id,
        reference_needs=_make_reference_needs("人工智能"),
        finder=finder,
    )

    # Should be ambiguous (cross-lang score 55 for AI title, 0 for Asia title)
    assert result.status == "ambiguous"
    assert result.ambiguous_records is not None
    candidate_titles = [r.get("title", "") for r in result.ambiguous_records]
    # "AI and the Future of Work" must be in candidates
    assert any("AI" in t for t in candidate_titles), f"Expected 'AI' in candidates, got {candidate_titles}"
    # "Economic Growth in Asia" must NOT be in candidates (no token-level match)
    assert not any("Asia" in t for t in candidate_titles), f"Unexpected 'Asia' in candidates: {candidate_titles}"
