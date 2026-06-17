"""Tests for reader_ask resolver: cross-language matching, weak-semantic matching,
disambiguation, and low-confidence rejection."""

from __future__ import annotations
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.reader_ask import planner
from app.services.reader_ask import resolver
from app.services.reader_ask import known_reference_resolver


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
        produce a high score that would auto-resolve."""
        score = resolver._score_title_match("问题", "The Problem of Evil")
        # "问题" maps to "problem"/"issue" — this IS a valid cross-lang match
        # but should score in the 50-55 range, below auto-resolve policy.
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


def _make_scored(
    score: int,
    *,
    record_id: str | None = None,
    title: str = "Climate Policy",
    updated_at: str | None = "2026-05-01",
) -> resolver.ScoredReferenceCandidate:
    """Helper to build a ScoredReferenceCandidate for policy tests."""
    return resolver.ScoredReferenceCandidate(
        score=score,
        candidate=resolver.ReferenceCandidate(
            record_id=record_id or str(uuid4()),
            title=title,
            updated_at=updated_at,
        ),
    )


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
        patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
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


# ---------------------------------------------------------------------------
# P3-S6A: Resolver contract freeze tests
# ---------------------------------------------------------------------------


class TestCrossLangScoreCannotAutoResolve:
    """P3-S6A: Cross-language matches stay far below the 90+ auto-resolve
    policy, so they can only produce 'ambiguous', never 'resolved'."""

    @pytest.mark.parametrize(
        "query,title",
        [
            ("气候", "Climate Change Impact"),
            ("人工智能", "AI and the Future of Work"),
            ("经济", "Economic Growth in Asia"),
            ("AI", "人工智能的未来"),
            ("machine learning", "机器学习入门指南"),
        ],
    )
    def test_cross_lang_score_stays_ambiguous_range(self, query: str, title: str) -> None:
        score = resolver._cross_lang_score(query, title)
        assert 40 <= score <= 55, f"Cross-lang score should be 40-55, got {score}"


class TestGenericMappingCannotAutoResolve:
    """P3-S6A: Generic words like '问题'/problem produce low-confidence
    cross-lang matches that can only be ambiguous, never auto-resolved."""

    def test_generic_wenti_scores_ambiguous_range(self) -> None:
        """'问题' maps to 'problem'/'issue' — generic, not specific enough."""
        score = resolver._score_title_match("问题", "The Problem of Evil")
        assert 50 <= score <= 55, f"Generic mapping score should be 50-55, got {score}"

    def test_generic_fazhan_scores_ambiguous_range(self) -> None:
        """'发展' maps to 'development' — generic."""
        score = resolver._score_title_match("发展", "Sustainable Development Goals")
        assert 50 <= score <= 55, f"Generic mapping score should be 50-55, got {score}"

    def test_generic_yingxiang_scores_ambiguous_range(self) -> None:
        """'影响' maps to 'impact' — generic."""
        score = resolver._score_title_match("影响", "The Impact of Technology")
        assert 50 <= score <= 55, f"Generic mapping score should be 50-55, got {score}"


class TestScoreTitleMatchContract:
    """P3-S6A: Freeze _score_title_match scoring tiers as contract."""

    def test_exact_match_100(self) -> None:
        assert resolver._score_title_match("Climate Policy", "Climate Policy") == 100

    def test_prefix_match_90(self) -> None:
        assert resolver._score_title_match("Climate", "Climate Policy") == 90

    def test_substring_match_80(self) -> None:
        assert resolver._score_title_match("Policy", "Climate Policy") == 80

    def test_all_tokens_match_70(self) -> None:
        """All query tokens present in title (but not as substring) → 70."""
        # "climate policy" is NOT a substring of "The climate policy review report"
        # because of the extra words, so it falls to token matching at 70.
        # But "climate policy" IS a substring of "The Climate Policy Review"
        # because "climate policy" appears in "the climate policy review".
        # Use a title where the query is not a contiguous substring.
        assert resolver._score_title_match("climate policy", "climate and policy analysis") == 70

    def test_fuzzy_match_60_exists(self) -> None:
        """Fuzzy stripped punctuation match returns exactly 60."""
        assert resolver._score_title_match("climatepolicy", "Climate-Policy") == 60

    def test_no_match_0(self) -> None:
        assert resolver._score_title_match("烹饪技巧", "Quantum Computing Basics") == 0


class TestResolutionPolicyContract:
    """P3-S6A: Freeze resolution policy separately from raw scoring."""

    async def test_substring_score_80_returns_ambiguous_not_resolved(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()
        finder = AsyncMock(
            return_value=[
                {"id": str(target_record_id), "title": "Climate Policy", "updated_at": "2026-05-01"}
            ]
        )

        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Policy"),
            finder=finder,
        )

        assert resolver._score_title_match("Policy", "Climate Policy") == 80
        assert result.status == "ambiguous"
        assert not result.resolved_records

    async def test_all_tokens_score_70_returns_ambiguous_not_resolved(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()
        finder = AsyncMock(
            return_value=[
                {
                    "id": str(target_record_id),
                    "title": "climate and policy analysis",
                    "updated_at": "2026-05-01",
                }
            ]
        )

        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("climate policy"),
            finder=finder,
        )

        assert resolver._score_title_match("climate policy", "climate and policy analysis") == 70
        assert result.status == "ambiguous"
        assert not result.resolved_records


class TestRecentRecordsFallbackContract:
    """P3-S6A: Recent records fallback is only a candidate pool,
    not semantic success."""

    async def test_recent_records_no_score_match_returns_not_found(self) -> None:
        """When ILIKE returns nothing and recent records have no
        _score_title_match ≥ 50, result is not_found."""
        user_id = uuid4()
        current_record_id = uuid4()

        ilike_finder = AsyncMock(return_value=[])
        recent_rows = [
            {"id": str(uuid4()), "title": "Quantum Computing Basics", "updated_at": "2026-05-01"},
            {"id": str(uuid4()), "title": "Introduction to HTML and XML", "updated_at": "2026-05-02"},
        ]

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=_make_reference_needs("烹饪技巧"),
                finder=ilike_finder,
            )

        assert result.status == "not_found"

    async def test_recent_records_with_cross_lang_match_returns_ambiguous(self) -> None:
        """When ILIKE returns nothing but recent records have cross-lang
        matches, result is ambiguous (not resolved)."""
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()

        ilike_finder = AsyncMock(return_value=[])
        recent_rows = [
            {"id": str(target_record_id), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        ]

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=_make_reference_needs("气候"),
                finder=ilike_finder,
            )

        # Cross-lang score 55 → ambiguous, not resolved
        assert result.status == "ambiguous"
        assert result.ambiguous_records is not None
        assert len(result.ambiguous_records) >= 1


class TestNoQueryContract:
    """P3-S6A: When no query is provided, return recent candidates
    as ambiguous — never guess or auto-resolve."""

    async def test_no_query_returns_ambiguous_with_recent(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()

        recent_rows = [
            {"id": str(uuid4()), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
            {"id": str(uuid4()), "title": "AI and the Future", "updated_at": "2026-05-02"},
        ]

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=planner.ReaderAskReferenceNeeds(requested=True, query=None),
            )

        assert result.status == "ambiguous"
        assert result.ambiguous_records is not None
        assert len(result.ambiguous_records) == 2

    async def test_no_query_no_recent_returns_ambiguous(self) -> None:
        """When no query and no recent records, still ambiguous (not not_found)."""
        user_id = uuid4()
        current_record_id = uuid4()

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=[])),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=planner.ReaderAskReferenceNeeds(requested=True, query=None),
            )

        assert result.status == "ambiguous"


class TestUnrelatedQueryContract:
    """P3-S6A: Completely unrelated query must return not_found."""

    async def test_unrelated_query_not_found(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()

        rows = [
            {"id": str(uuid4()), "title": "Quantum Computing Basics", "updated_at": "2026-05-01"},
            {"id": str(uuid4()), "title": "Introduction to Cooking", "updated_at": "2026-05-02"},
        ]
        finder = AsyncMock(return_value=rows)

        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("太空探索的历史"),
            finder=finder,
        )

        assert result.status == "not_found"


# ---------------------------------------------------------------------------
# Phase 4 Round 1: Candidate pool, scoring, policy, and metadata tests
# ---------------------------------------------------------------------------


class TestBuildReferenceCandidatePool:
    """Test build_reference_candidate_pool strategy detection."""

    async def test_ilike_results_returns_title_search(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        rows = [
            {"id": str(uuid4()), "title": "Climate Policy", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=rows)

        candidates, strategy = await resolver.build_reference_candidate_pool(
            user_id=user_id,
            current_record_id=current_record_id,
            query="Climate",
            finder=finder,
        )

        assert strategy == planner.RESOLUTION_STRATEGY_TITLE_SEARCH
        assert len(candidates) == 1
        assert isinstance(candidates[0], resolver.ReferenceCandidate)

    async def test_ilike_empty_falls_back_to_recent(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        recent_rows = [
            {"id": str(uuid4()), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=[])

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            candidates, strategy = await resolver.build_reference_candidate_pool(
                user_id=user_id,
                current_record_id=current_record_id,
                query="气候",
                finder=finder,
            )

        assert strategy == planner.RESOLUTION_STRATEGY_RECENT_FALLBACK
        assert len(candidates) == 1
        assert isinstance(candidates[0], resolver.ReferenceCandidate)

    async def test_no_query_returns_no_query_recent(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        recent_rows = [
            {"id": str(uuid4()), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        ]

        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            candidates, strategy = await resolver.build_reference_candidate_pool(
                user_id=user_id,
                current_record_id=current_record_id,
                query=None,
            )

        assert strategy == planner.RESOLUTION_STRATEGY_NO_QUERY_RECENT
        assert len(candidates) == 1

    async def test_candidate_pool_filters_rows_without_record_id(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        rows = [
            {"title": "Missing ID"},
            {"id": "", "title": "Blank ID"},
            {"id": " valid-id ", "title": "Climate Policy", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=rows)

        candidates, strategy = await resolver.build_reference_candidate_pool(
            user_id=user_id,
            current_record_id=current_record_id,
            query="Climate",
            finder=finder,
        )

        assert strategy == planner.RESOLUTION_STRATEGY_TITLE_SEARCH
        assert len(candidates) == 1
        assert candidates[0].record_id == "valid-id"


class TestScoreReferenceCandidates:
    """Test score_reference_candidates pure function."""

    def test_empty_candidates_returns_empty(self) -> None:
        result = resolver.score_reference_candidates("Climate", [])
        assert result == []

    def test_ranks_by_score_descending(self) -> None:
        candidates = [
            resolver.ReferenceCandidate(record_id="1", title="Climate Policy", updated_at="2026-05-01"),
            resolver.ReferenceCandidate(record_id="2", title="Climate Policy Review", updated_at="2026-05-02"),
        ]
        result = resolver.score_reference_candidates("Climate Policy", candidates)
        # Exact match should be first (score 100)
        assert result[0].score == 100
        assert result[0].candidate.record_id == "1"
        # Prefix match second (score 90)
        assert result[1].score == 90
        assert result[1].candidate.record_id == "2"

    def test_all_zero_scores_returns_empty(self) -> None:
        candidates = [
            resolver.ReferenceCandidate(record_id="1", title="Quantum Computing Basics", updated_at="2026-05-01"),
        ]
        result = resolver.score_reference_candidates("烹饪技巧", candidates)
        assert result == []


class TestApplyReferenceResolutionPolicy:
    """Test apply_reference_resolution_policy pure function."""

    def test_exact_score_90_unique_margin_resolved(self) -> None:
        ranked = [_make_scored(100)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "resolved"
        assert len(result.resolved_records) == 1

    def test_prefix_score_90_unique_margin_resolved(self) -> None:
        ranked = [_make_scored(90)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "resolved"

    def test_substring_80_returns_ambiguous(self) -> None:
        ranked = [_make_scored(80)]
        result = resolver.apply_reference_resolution_policy(
            query="Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "ambiguous"
        assert not result.resolved_records

    def test_all_tokens_70_returns_ambiguous(self) -> None:
        ranked = [_make_scored(70, title="climate and policy analysis")]
        result = resolver.apply_reference_resolution_policy(
            query="climate policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "ambiguous"
        assert not result.resolved_records

    def test_cross_lang_legacy_50_55_returns_ambiguous(self) -> None:
        score = resolver._score_title_match("气候", "Climate Change Impact")
        assert 50 <= score <= 55
        ranked = [_make_scored(score, title="Climate Change Impact")]
        result = resolver.apply_reference_resolution_policy(
            query="气候",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_RECENT_FALLBACK,
            candidate_count=1,
        )
        assert result.status == "ambiguous"
        assert not result.resolved_records

    def test_generic_mapping_no_auto_resolve(self) -> None:
        """Generic cross-lang matches (e.g. '问题' → 'problem') must not auto-resolve."""
        score = resolver._score_title_match("问题", "The Problem of Evil")
        assert 50 <= score <= 55
        ranked = [_make_scored(score, title="The Problem of Evil")]
        result = resolver.apply_reference_resolution_policy(
            query="问题",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "ambiguous"
        assert not result.resolved_records

    def test_empty_ranked_returns_not_found(self) -> None:
        result = resolver.apply_reference_resolution_policy(
            query="太空探索",
            ranked=[],
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=0,
        )
        assert result.status == "not_found"

    def test_recent_fallback_no_meaningful_score_returns_not_found(self) -> None:
        """ILIKE empty + recent records with no meaningful score → not_found."""
        score = resolver._score_title_match("烹饪技巧", "Quantum Computing Basics")
        assert score == 0
        # No scored candidates, but normalized pool had 3 records
        ranked: list[resolver.ScoredReferenceCandidate] = []
        result = resolver.apply_reference_resolution_policy(
            query="烹饪技巧",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_RECENT_FALLBACK,
            candidate_count=3,
        )
        assert result.status == "not_found"
        # candidate_count should reflect normalized pool, not scored count
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 3
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 0

    def test_recent_fallback_cross_lang_returns_ambiguous(self) -> None:
        """ILIKE empty + recent records with cross-lang match → ambiguous."""
        score = resolver._score_title_match("气候", "Climate Change Impact")
        assert score >= 50
        ranked = [_make_scored(score, title="Climate Change Impact")]
        result = resolver.apply_reference_resolution_policy(
            query="气候",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_RECENT_FALLBACK,
            candidate_count=5,
        )
        assert result.status == "ambiguous"

    def test_score_below_50_returns_not_found(self) -> None:
        """Levenshtein fuzzy score 40 should return not_found."""
        ranked = [_make_scored(40)]
        result = resolver.apply_reference_resolution_policy(
            query="xyz",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "not_found"

    def test_no_margin_returns_ambiguous(self) -> None:
        """Two candidates with same top score >= 90 → ambiguous (no margin)."""
        ranked = [
            _make_scored(90, record_id=str(uuid4())),
            _make_scored(90, title="Climate Policy Review", record_id=str(uuid4())),
        ]
        result = resolver.apply_reference_resolution_policy(
            query="Climate",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=2,
        )
        assert result.status == "ambiguous"

    def test_insufficient_margin_returns_ambiguous(self) -> None:
        """Top score 90, runner-up 75 → margin 15 < 20 → ambiguous."""
        ranked = [
            _make_scored(90, record_id=str(uuid4())),
            _make_scored(75, title="Climate Change Report", record_id=str(uuid4())),
        ]
        result = resolver.apply_reference_resolution_policy(
            query="Climate",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=2,
        )
        assert result.status == "ambiguous"


class TestResolutionMetaStability:
    """Test that resolution_meta is present and correct across all paths."""

    async def test_not_requested_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=planner.ReaderAskReferenceNeeds(requested=False),
        )
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_NOT_REQUESTED
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 0
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 0
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] is None
        assert result.resolution_meta[planner.RESOLUTION_META_RUNNER_UP_SCORE] is None
        assert result.resolution_meta[planner.RESOLUTION_META_FALLBACK_REASON] is None

    async def test_title_search_resolved_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()
        rows = [
            {"id": str(target_record_id), "title": "Climate Policy", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=rows)
        result = await resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate Policy"),
            finder=finder,
        )
        assert result.status == "resolved"
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_TITLE_SEARCH
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] == 100
        assert result.resolution_meta[planner.RESOLUTION_META_RUNNER_UP_SCORE] is None
        assert result.resolution_meta[planner.RESOLUTION_META_FALLBACK_REASON] is None
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 1
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 1

    async def test_recent_fallback_ambiguous_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()
        ilike_finder = AsyncMock(return_value=[])
        recent_rows = [
            {"id": str(target_record_id), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        ]
        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=_make_reference_needs("气候"),
                finder=ilike_finder,
            )
        assert result.status == "ambiguous"
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_RECENT_FALLBACK
        assert result.resolution_meta[planner.RESOLUTION_META_FALLBACK_REASON] == planner.RESOLUTION_FALLBACK_ILIKE_EMPTY
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] >= 50

    async def test_no_query_recent_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        recent_rows = [
            {"id": str(uuid4()), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
            {"id": str(uuid4()), "title": "AI and the Future", "updated_at": "2026-05-02"},
        ]
        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=recent_rows)),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=planner.ReaderAskReferenceNeeds(requested=True, query=None),
            )
        assert result.status == "ambiguous"
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_NO_QUERY_RECENT
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] is None
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 2

    async def test_no_query_no_recent_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
        with (
            patch.object(known_reference_resolver.repo, "list_recent_records", new=AsyncMock(return_value=[])),
        ):
            result = await resolver.resolve_known_references(
                user_id=user_id,
                current_record_id=current_record_id,
                reference_needs=planner.ReaderAskReferenceNeeds(requested=True, query=None),
            )
        assert result.status == "ambiguous"
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_NO_QUERY_RECENT
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 0
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] is None

    async def test_not_found_meta(self) -> None:
        user_id = uuid4()
        current_record_id = uuid4()
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
        assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] == planner.RESOLUTION_STRATEGY_TITLE_SEARCH
        # candidate_count reflects normalized pool (1 ILIKE result), scored_candidate_count is 0
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 1
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 0
        assert result.resolution_meta[planner.RESOLUTION_META_TOP_SCORE] is None

    def test_policy_meta_fields_present(self) -> None:
        """All apply_reference_resolution_policy results have complete meta."""
        ranked = [_make_scored(100)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        meta = result.resolution_meta
        assert planner.RESOLUTION_META_STRATEGY in meta
        assert planner.RESOLUTION_META_CANDIDATE_COUNT in meta
        assert planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT in meta
        assert planner.RESOLUTION_META_TOP_SCORE in meta
        assert planner.RESOLUTION_META_RUNNER_UP_SCORE in meta
        assert planner.RESOLUTION_META_FALLBACK_REASON in meta

    def test_meta_keys_match_contract_fields(self) -> None:
        """resolution_meta keys are exactly RESOLUTION_META_FIELDS."""
        ranked = [_make_scored(100)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert set(result.resolution_meta.keys()) == planner.RESOLUTION_META_FIELDS

    def test_strategy_value_is_valid(self) -> None:
        """strategy values are always from RESOLUTION_STRATEGIES."""
        ranked = [_make_scored(100)]
        for strategy in [planner.RESOLUTION_STRATEGY_TITLE_SEARCH, planner.RESOLUTION_STRATEGY_RECENT_FALLBACK]:
            result = resolver.apply_reference_resolution_policy(
                query="Climate Policy",
                ranked=ranked,
                strategy=strategy,
                candidate_count=1,
            )
            assert result.resolution_meta[planner.RESOLUTION_META_STRATEGY] in planner.RESOLUTION_STRATEGIES


# ---------------------------------------------------------------------------
# Phase 4 Round 3: Typed candidate contract tests
# ---------------------------------------------------------------------------


class TestToReferenceCandidate:
    """Test _to_reference_candidate normalize helper."""

    def test_full_row_normalizes_correctly(self) -> None:
        row = {
            "id": "abc-123",
            "title": "Climate Policy",
            "updated_at": "2026-05-01",
            "render_scene_json": {"sentence_entries": [{"title": "t", "content": "c"}]},
            "page_state_json": {"scroll": 0},
        }
        c = resolver._to_reference_candidate(row)
        assert c is not None
        assert isinstance(c, resolver.ReferenceCandidate)
        assert c.record_id == "abc-123"
        assert c.title == "Climate Policy"
        assert c.updated_at == "2026-05-01"
        assert c.render_scene_json == {"sentence_entries": [{"title": "t", "content": "c"}]}
        assert c.page_state_json == {"scroll": 0}

    def test_missing_optional_fields_defaults(self) -> None:
        """Missing optional fields should not crash; defaults applied."""
        row = {"id": "abc-123", "title": "Climate Policy"}
        c = resolver._to_reference_candidate(row)
        assert c is not None
        assert c.record_id == "abc-123"
        assert c.title == "Climate Policy"
        assert c.updated_at is None
        assert c.render_scene_json == {}
        assert c.page_state_json == {}

    def test_missing_id_returns_none(self) -> None:
        row = {"title": "No ID"}
        assert resolver._to_reference_candidate(row) is None

    def test_blank_id_returns_none(self) -> None:
        row = {"id": "   ", "title": "Blank ID"}
        assert resolver._to_reference_candidate(row) is None

    def test_missing_title_defaults_empty_string(self) -> None:
        row = {"id": "123"}
        c = resolver._to_reference_candidate(row)
        assert c is not None
        assert c.record_id == "123"
        assert c.title == ""

    def test_empty_row_returns_none(self) -> None:
        assert resolver._to_reference_candidate({}) is None

    def test_render_scene_json_non_dict_defaults_empty(self) -> None:
        """If render_scene_json is not a dict, default to empty dict."""
        row = {"id": "1", "title": "Test", "render_scene_json": "not a dict"}
        c = resolver._to_reference_candidate(row)
        assert c is not None
        assert c.render_scene_json == {}

    def test_page_state_json_non_dict_defaults_empty(self) -> None:
        """If page_state_json is not a dict, default to empty dict."""
        row = {"id": "1", "title": "Test", "page_state_json": None}
        c = resolver._to_reference_candidate(row)
        assert c is not None
        assert c.page_state_json == {}


class TestTypedCandidatePayloadContract:
    """Test that _candidate_payload_from_typed produces correct output shape."""

    def test_payload_has_required_keys(self) -> None:
        c = resolver.ReferenceCandidate(
            record_id="abc-123",
            title="Climate Policy",
            updated_at="2026-05-01",
        )
        payload = resolver._candidate_payload_from_typed(c)
        assert payload["record_id"] == "abc-123"
        assert payload["title"] == "Climate Policy"
        assert payload["updated_at"] == "2026-05-01"

    def test_payload_title_fallback_to_untitled(self) -> None:
        c = resolver.ReferenceCandidate(record_id="1", title="")
        payload = resolver._candidate_payload_from_typed(c)
        assert payload["title"] == "Untitled"

    def test_payload_updated_at_none(self) -> None:
        c = resolver.ReferenceCandidate(record_id="1", title="Test", updated_at=None)
        payload = resolver._candidate_payload_from_typed(c)
        assert payload["updated_at"] is None

    def test_payload_overview_hint_from_render_scene(self) -> None:
        c = resolver.ReferenceCandidate(
            record_id="1",
            title="Test",
            render_scene_json={"content_summary": {"overview": "A summary of the article."}},
            page_state_json={},
        )
        payload = resolver._candidate_payload_from_typed(c)
        assert "overview_hint" in payload
        assert payload["overview_hint"] is not None

    def test_payload_no_overview_hint_when_empty(self) -> None:
        c = resolver.ReferenceCandidate(record_id="1", title="Test")
        payload = resolver._candidate_payload_from_typed(c)
        assert "overview_hint" not in payload


class TestTypedCandidateIntegrationContract:
    """Test that typed candidates flow correctly through the pipeline
    and produce correct resolved_records / ambiguous_records shapes."""

    def test_resolved_payload_includes_updated_at(self) -> None:
        """updated_at from typed candidate appears in resolved_records."""
        ranked = [_make_scored(100, record_id="r1", title="Climate Policy", updated_at="2026-05-01")]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "resolved"
        assert result.resolved_records[0]["updated_at"] == "2026-05-01"

    def test_ambiguous_payload_includes_updated_at(self) -> None:
        """updated_at from typed candidate appears in ambiguous_records."""
        ranked = [_make_scored(55, title="Climate Change Impact", updated_at="2026-06-01")]
        result = resolver.apply_reference_resolution_policy(
            query="气候",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_RECENT_FALLBACK,
            candidate_count=1,
        )
        assert result.status == "ambiguous"
        assert result.ambiguous_records[0]["updated_at"] == "2026-06-01"

    def test_resolved_payload_includes_overview_hint(self) -> None:
        """overview_hint from render_scene_json appears in resolved_records."""
        c = resolver.ReferenceCandidate(
            record_id="r1",
            title="Climate Policy",
            updated_at="2026-05-01",
            render_scene_json={"content_summary": {"overview": "A summary."}},
        )
        ranked = [resolver.ScoredReferenceCandidate(score=100, candidate=c)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=1,
        )
        assert result.status == "resolved"
        assert "overview_hint" in result.resolved_records[0]

    def test_candidate_count_reflects_normalized_pool(self) -> None:
        """candidate_count is normalized pool size, not scored count."""
        # 3 candidates in pool, but only 1 scored above 0
        ranked = [_make_scored(100)]
        result = resolver.apply_reference_resolution_policy(
            query="Climate Policy",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=3,
        )
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 3
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 1

    def test_scored_candidate_count_is_score_above_zero(self) -> None:
        """scored_candidate_count counts candidates with score > 0."""
        ranked = [
            _make_scored(100, record_id="r1"),
            _make_scored(80, title="Climate Policy Review", record_id="r2"),
        ]
        result = resolver.apply_reference_resolution_policy(
            query="Climate",
            ranked=ranked,
            strategy=planner.RESOLUTION_STRATEGY_TITLE_SEARCH,
            candidate_count=5,
        )
        assert result.resolution_meta[planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT] == 2
        assert result.resolution_meta[planner.RESOLUTION_META_CANDIDATE_COUNT] == 5


# ---------------------------------------------------------------------------
# Phase 4 Round 4: Module boundary tests
# ---------------------------------------------------------------------------


class TestModuleBoundary:
    """Round 4: Verify module split boundaries."""

    def test_service_does_not_import_internal_types(self) -> None:
        """service.py must not reference internal resolver types."""
        import importlib.util

        spec = importlib.util.find_spec("app.services.reader_ask.service")
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            content = f.read()
        assert "ReferenceCandidate" not in content
        assert "ScoredReferenceCandidate" not in content
        assert "ReferenceReranker" not in content
        assert "IdentityReferenceReranker" not in content
        assert "LlmReferenceReranker" not in content
        assert "SemanticRerankInput" not in content
        assert "SemanticRerankOutput" not in content

    def test_resolver_facade_re_exports_resolve_known_references(self) -> None:
        """resolver facade re-exports resolve_known_references."""
        assert hasattr(resolver, "resolve_known_references")

    def test_resolver_facade_re_exports_build_reference_candidate_pool(self) -> None:
        assert hasattr(resolver, "build_reference_candidate_pool")

    def test_resolver_facade_re_exports_score_reference_candidates(self) -> None:
        assert hasattr(resolver, "score_reference_candidates")

    def test_resolver_facade_re_exports_apply_reference_resolution_policy(self) -> None:
        assert hasattr(resolver, "apply_reference_resolution_policy")

    def test_resolver_facade_re_exports_typed_candidates(self) -> None:
        assert hasattr(resolver, "ReferenceCandidate")
        assert hasattr(resolver, "ScoredReferenceCandidate")

    def test_resolver_facade_re_exports_scoring_helpers(self) -> None:
        """Test-referenced private scoring symbols are re-exported."""
        assert hasattr(resolver, "_score_title_match")
        assert hasattr(resolver, "_cross_lang_score")
        assert hasattr(resolver, "_to_reference_candidate")
        assert hasattr(resolver, "_candidate_payload_from_typed")

    def test_known_reference_resolver_module_importable(self) -> None:
        from app.services.reader_ask import known_reference_resolver
        assert hasattr(known_reference_resolver, "resolve_known_references")
        assert hasattr(known_reference_resolver, "ReferenceCandidate")
        assert hasattr(known_reference_resolver, "ScoredReferenceCandidate")

    def test_resolver_facade_retains_structured_asset_functions(self) -> None:
        """Structured asset resolution still lives in resolver.py."""
        assert hasattr(resolver, "lookup_structured_record_assets")
        assert hasattr(resolver, "resolve_structured_asset_references")


# ---------------------------------------------------------------------------
# Phase 4 Round 5: Semantic reranker contract stub tests
# ---------------------------------------------------------------------------


class TestIdentityReferenceReranker:
    """Round 5: Identity reranker contract tests."""

    async def test_identity_reranker_returns_same_list(self) -> None:
        reranker = known_reference_resolver.IdentityReferenceReranker()
        ranked = [_make_scored(100), _make_scored(80, title="Other")]
        result = await reranker.rerank("Climate Policy", ranked)
        assert result is ranked

    async def test_identity_reranker_preserves_order_and_scores(self) -> None:
        reranker = known_reference_resolver.IdentityReferenceReranker()
        ranked = [_make_scored(90), _make_scored(55, title="Other")]
        result = await reranker.rerank("Climate", ranked)
        assert len(result) == 2
        assert result[0].score == 90
        assert result[1].score == 55

    async def test_identity_reranker_empty_list(self) -> None:
        reranker = known_reference_resolver.IdentityReferenceReranker()
        result = await reranker.rerank("test", [])
        assert result == []

    def test_identity_reranker_satisfies_protocol(self) -> None:
        reranker = known_reference_resolver.IdentityReferenceReranker()
        assert isinstance(reranker, known_reference_resolver.ReferenceReranker)


class TestRerankReferenceCandidates:
    """Round 5: rerank_reference_candidates hook tests."""

    async def test_default_reranker_preserves_order(self) -> None:
        ranked = [_make_scored(100), _make_scored(80)]
        result = await known_reference_resolver.rerank_reference_candidates("test", ranked)
        assert len(result) == 2
        assert result[0].score == 100
        assert result[1].score == 80

    async def test_custom_reranker_is_called(self) -> None:
        call_log: list[str] = []

        class LoggingReranker:
            async def rerank(self, query: str, ranked: list[known_reference_resolver.ScoredReferenceCandidate]) -> list[known_reference_resolver.ScoredReferenceCandidate]:
                call_log.append(query)
                return ranked

        ranked = [_make_scored(100)]
        result = await known_reference_resolver.rerank_reference_candidates(
            "Climate", ranked, reranker=LoggingReranker()
        )
        assert call_log == ["Climate"]
        assert result[0].score == 100

    async def test_reranker_reorder_is_normalized_by_score_desc(self) -> None:
        """ReverseReranker returns reversed list, but hook re-sorts by score desc."""
        class ReverseReranker:
            async def rerank(self, query: str, ranked: list[known_reference_resolver.ScoredReferenceCandidate]) -> list[known_reference_resolver.ScoredReferenceCandidate]:
                return list(reversed(ranked))

        ranked = [_make_scored(100), _make_scored(80)]
        result = await known_reference_resolver.rerank_reference_candidates(
            "test", ranked, reranker=ReverseReranker()
        )
        # Re-sorted by score desc after reranker returns
        assert result[0].score == 100
        assert result[1].score == 80

    async def test_reranker_adjusts_scores_policy_uses_adjusted(self) -> None:
        """Reranker that adjusts scores: policy uses the adjusted scores."""
        class PromoteReranker:
            async def rerank(self, query: str, ranked: list[known_reference_resolver.ScoredReferenceCandidate]) -> list[known_reference_resolver.ScoredReferenceCandidate]:
                # Promote second candidate to top score
                result = list(ranked)
                if len(result) >= 2:
                    result[1] = known_reference_resolver.ScoredReferenceCandidate(
                        score=95, candidate=result[1].candidate
                    )
                return result

        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Climate Policy")
        c2 = known_reference_resolver.ReferenceCandidate(record_id="r2", title="Climate Action")
        ranked = [
            known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c1),
            known_reference_resolver.ScoredReferenceCandidate(score=55, candidate=c2),
        ]
        result = await known_reference_resolver.rerank_reference_candidates(
            "Climate", ranked, reranker=PromoteReranker()
        )
        # After re-sort by adjusted scores, c2 (score 95) should be first
        assert result[0].score == 95
        assert result[0].candidate.record_id == "r2"
        assert result[1].score == 80
        assert result[1].candidate.record_id == "r1"


class TestRerankHookInResolveKnownReferences:
    """Round 5: Verify rerank hook is called in resolve_known_references
    but default behavior is unchanged."""

    async def test_resolve_known_references_unchanged_with_identity_rerank(self) -> None:
        """End-to-end: resolve_known_references produces same results as before."""
        user_id = uuid4()
        current_record_id = uuid4()
        target_record_id = uuid4()
        rows = [
            {"id": str(target_record_id), "title": "Climate Policy", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=rows)
        result = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate Policy"),
            finder=finder,
        )
        assert result.status == "resolved"
        assert len(result.resolved_records) == 1

    async def test_resolve_known_references_ambiguous_unchanged(self) -> None:
        """End-to-end: ambiguous path still works with identity rerank."""
        user_id = uuid4()
        current_record_id = uuid4()
        rows = [
            {"id": str(uuid4()), "title": "Climate Change Impact", "updated_at": "2026-05-01"},
        ]
        finder = AsyncMock(return_value=rows)
        # "Change Impact" is a substring match (score 80) → ambiguous
        result = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Change Impact"),
            finder=finder,
        )
        assert result.status == "ambiguous"

    async def test_custom_reranker_through_resolve_known_references(self) -> None:
        """Custom reranker passed to resolve_known_references affects the result."""
        user_id = uuid4()
        current_record_id = uuid4()
        r1 = str(uuid4())
        r2 = str(uuid4())
        rows = [
            {"id": r1, "title": "Climate Policy", "updated_at": "2026-05-01"},
            {"id": r2, "title": "Climate Action Plan", "updated_at": "2026-05-02"},
        ]
        finder = AsyncMock(return_value=rows)

        # Without reranker: "Climate" is a prefix of both → ambiguous (no margin)
        result_default = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate"),
            finder=finder,
        )
        assert result_default.status == "ambiguous"

        # With a reranker that promotes r2 to score 100 (unique top, margin ≥ 20)
        class PromoteSecond:
            async def rerank(self, query: str, ranked: list[known_reference_resolver.ScoredReferenceCandidate]) -> list[known_reference_resolver.ScoredReferenceCandidate]:
                result = list(ranked)
                if len(result) >= 2:
                    result[1] = known_reference_resolver.ScoredReferenceCandidate(
                        score=100, candidate=result[1].candidate
                    )
                return result

        result_reranked = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate"),
            finder=finder,
            reranker=PromoteSecond(),
        )
        # After re-sort: r2 (score 100) first, r1 (score 90) second → margin 10 < 20
        # Still ambiguous because margin is insufficient
        assert result_reranked.status == "ambiguous"

    async def test_reranker_promote_with_clear_margin_resolves(self) -> None:
        """Reranker that creates a clear margin (≥20) produces resolved."""
        user_id = uuid4()
        current_record_id = uuid4()
        r1 = str(uuid4())
        r2 = str(uuid4())
        rows = [
            {"id": r1, "title": "Climate Policy", "updated_at": "2026-05-01"},
            {"id": r2, "title": "Climate Action Plan", "updated_at": "2026-05-02"},
        ]
        finder = AsyncMock(return_value=rows)

        # Reranker that promotes r2 to 100 and demotes r1 to 70 → margin 30 ≥ 20
        class PromoteWithMargin:
            async def rerank(self, query: str, ranked: list[known_reference_resolver.ScoredReferenceCandidate]) -> list[known_reference_resolver.ScoredReferenceCandidate]:
                result = list(ranked)
                if len(result) >= 2:
                    result[0] = known_reference_resolver.ScoredReferenceCandidate(
                        score=70, candidate=result[0].candidate
                    )
                    result[1] = known_reference_resolver.ScoredReferenceCandidate(
                        score=100, candidate=result[1].candidate
                    )
                return result

        result = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate"),
            finder=finder,
            reranker=PromoteWithMargin(),
        )
        assert result.status == "resolved"
        assert result.resolved_records[0]["record_id"] == r2


# ---------------------------------------------------------------------------
# Phase 4 Round 6: LLM semantic reranker adapter tests
# ---------------------------------------------------------------------------


class TestLlmReferenceReranker:
    """Round 6: LLM semantic reranker adapter tests."""

    async def test_llm_reranker_promotes_candidate(self) -> None:
        """LLM callback promotes a candidate by score_adjustment."""
        async def promote_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(
                    record_id=inputs[1].record_id,
                    score_adjustment=30,
                    reason="semantic match",
                ),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(promote_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Climate Policy")
        c2 = known_reference_resolver.ReferenceCandidate(record_id="r2", title="Climate Action")
        ranked = [
            known_reference_resolver.ScoredReferenceCandidate(score=90, candidate=c1),
            known_reference_resolver.ScoredReferenceCandidate(score=55, candidate=c2),
        ]
        result = await reranker.rerank("Climate", ranked)
        assert result[0].candidate.record_id == "r1"
        assert result[0].score == 90
        assert result[1].candidate.record_id == "r2"
        assert result[1].score == 85  # 55 + 30

    async def test_llm_reranker_unknown_record_id_ignored(self) -> None:
        """LLM returns unknown record_id → ignored, original scores kept."""
        async def unknown_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(
                    record_id="nonexistent",
                    score_adjustment=50,
                ),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(unknown_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert len(result) == 1
        assert result[0].score == 80

    async def test_llm_reranker_duplicate_record_id_keeps_first(self) -> None:
        """LLM returns duplicate record_id → first adjustment wins."""
        async def dup_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(record_id="r1", score_adjustment=10),
                known_reference_resolver.SemanticRerankOutput(record_id="r1", score_adjustment=20),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(dup_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert result[0].score == 90  # 80 + 10 (first adjustment)

    async def test_llm_reranker_empty_result_fallback(self) -> None:
        """LLM returns empty list → fallback to original ranked."""
        async def empty_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return []

        reranker = known_reference_resolver.LlmReferenceReranker(empty_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert result is ranked

    async def test_llm_reranker_exception_fallback(self) -> None:
        """LLM callback raises exception → fallback to original ranked."""
        async def error_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            raise RuntimeError("LLM unavailable")

        reranker = known_reference_resolver.LlmReferenceReranker(error_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert result is ranked

    async def test_llm_reranker_score_clamp_high(self) -> None:
        """Score adjustment pushes above 100 → clamped to 100."""
        async def clamp_high_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(record_id="r1", score_adjustment=50),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(clamp_high_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=90, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert result[0].score == 100

    async def test_llm_reranker_score_clamp_low(self) -> None:
        """Score adjustment pushes below 0 → clamped to 0."""
        async def clamp_low_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(record_id="r1", score_adjustment=-100),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(clamp_low_cb)
        c1 = known_reference_resolver.ReferenceCandidate(record_id="r1", title="Test")
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=30, candidate=c1)]
        result = await reranker.rerank("test", ranked)
        assert result[0].score == 0

    async def test_llm_reranker_empty_input(self) -> None:
        """Empty ranked list → returns empty without calling callback."""
        call_count = 0

        async def counting_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            nonlocal call_count
            call_count += 1
            return []

        reranker = known_reference_resolver.LlmReferenceReranker(counting_cb)
        result = await reranker.rerank("test", [])
        assert result == []
        assert call_count == 0

    def test_llm_reranker_satisfies_protocol(self) -> None:
        """LlmReferenceReranker satisfies ReferenceReranker protocol."""
        async def noop_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return []

        reranker = known_reference_resolver.LlmReferenceReranker(noop_cb)
        assert isinstance(reranker, known_reference_resolver.ReferenceReranker)

    async def test_llm_reranker_build_inputs_excludes_internal_fields(self) -> None:
        """_build_inputs only exposes safe fields, not render_scene_json."""
        c = known_reference_resolver.ReferenceCandidate(
            record_id="r1",
            title="Test",
            render_scene_json={"internal": "data"},
            page_state_json={"also": "internal"},
        )
        ranked = [known_reference_resolver.ScoredReferenceCandidate(score=80, candidate=c)]
        inputs = known_reference_resolver.LlmReferenceReranker._build_inputs(ranked)
        assert len(inputs) == 1
        assert inputs[0].record_id == "r1"
        assert inputs[0].title == "Test"
        assert inputs[0].deterministic_score == 80
        # SemanticRerankInput has no render_scene_json / page_state_json fields
        assert not hasattr(inputs[0], "render_scene_json")

    async def test_llm_reranker_through_resolve_known_references(self) -> None:
        """LlmReferenceReranker works through resolve_known_references."""
        user_id = uuid4()
        current_record_id = uuid4()
        r1 = str(uuid4())
        r2 = str(uuid4())
        rows = [
            {"id": r1, "title": "Climate Policy", "updated_at": "2026-05-01"},
            {"id": r2, "title": "Climate Action Plan", "updated_at": "2026-05-02"},
        ]
        finder = AsyncMock(return_value=rows)

        async def promote_r2_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return [
                known_reference_resolver.SemanticRerankOutput(record_id=r1, score_adjustment=-20),
                known_reference_resolver.SemanticRerankOutput(record_id=r2, score_adjustment=10),
            ]

        reranker = known_reference_resolver.LlmReferenceReranker(promote_r2_cb)
        result = await known_reference_resolver.resolve_known_references(
            user_id=user_id,
            current_record_id=current_record_id,
            reference_needs=_make_reference_needs("Climate"),
            finder=finder,
            reranker=reranker,
        )
        # r1: 90-20=70, r2: 90+10=100 → r2 top with margin 30 → resolved
        assert result.status == "resolved"
        assert result.resolved_records[0]["record_id"] == r2


# ---------------------------------------------------------------------------
# Phase 4 Round 7: build_reference_reranker factory + wiring tests
# ---------------------------------------------------------------------------


class TestBuildReferenceReranker:
    """Round 7: build_reference_reranker factory tests."""

    def test_default_disabled_returns_none(self) -> None:
        result = known_reference_resolver.build_reference_reranker(enabled=False)
        assert result is None

    def test_enabled_without_callback_returns_none(self) -> None:
        result = known_reference_resolver.build_reference_reranker(enabled=True, callback=None)
        assert result is None

    def test_enabled_with_callback_returns_llm_reranker(self) -> None:
        async def fake_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return []

        result = known_reference_resolver.build_reference_reranker(enabled=True, callback=fake_cb)
        assert isinstance(result, known_reference_resolver.LlmReferenceReranker)

    def test_disabled_ignores_callback(self) -> None:
        async def fake_cb(query: str, inputs: list[known_reference_resolver.SemanticRerankInput]) -> list[known_reference_resolver.SemanticRerankOutput]:
            return []

        result = known_reference_resolver.build_reference_reranker(enabled=False, callback=fake_cb)
        assert result is None
