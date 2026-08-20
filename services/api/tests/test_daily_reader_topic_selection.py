"""T-B2: Daily Reader candidate ordering and daily topic/source diversity.

Locks the B-2 selection contract:

- scored candidates order by content score first; a qualified cover only
  breaks ties;
- "same topic at most once per daily run" is computed from scorer
  ``score.tags`` (not ``article.tags``) with minimal normalization
  (strip + casefold, drop empty tags);
- same source at most twice per daily run (source compared after
  strip + casefold);
- the constraints bind the final successful output, including the
  workflow-failure fallback pool, not just the initial oversample;
- independent candidate pools (separate daily runs) may each pick the
  same topic — no cross-run state;
- discovery source config and the SCORING_MAX_CANDIDATES cap.

Every external boundary (discovery, cover probe, LLM scoring, workflow,
alerts) is mocked. No network, no DB writes, no real provider calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.daily_reader.discovery import ARTICLE_SOURCES, DiscoveredArticle
from app.services.daily_reader.pipeline import (
    SCORING_MAX_CANDIDATES,
    run_daily_pipeline,
    select_diverse_candidates,
)
from app.services.daily_reader.scoring import ArticleScore


def _candidate(
    *,
    url: str,
    title: str,
    source: str,
    score: float,
    tags: list[str],
    article_tags: list[str] | None = None,
    has_cover: bool = False,
    word_count: int = 800,
) -> tuple[DiscoveredArticle, ArticleScore]:
    """Build one (article, score) pair with unique url/title/text."""
    article = DiscoveredArticle(
        url=url,
        title=title,
        source=source,
        description=f"Description for {title}",
        text=f"Unique body text for {title} with enough words. " * 30,
        tags=article_tags if article_tags is not None else [f"{url}-article-tag"],
        word_count=word_count,
        needs_extraction=False,
        has_qualified_cover=has_cover,
    )
    return article, ArticleScore(score=score, difficulty="B1", tags=tags)


async def _run_pipeline(
    candidates: list[tuple[DiscoveredArticle, ArticleScore]],
    *,
    max_count: int = 3,
    covers: dict[str, bool] | None = None,
    failing_urls: set[str] | None = None,
) -> tuple[object, AsyncMock, AsyncMock]:
    """Run run_daily_pipeline with every external boundary mocked.

    Returns (result, workflow_mock, score_mock) so tests can assert call
    order and cap behavior.
    """
    covers = covers or {}
    failing_urls = failing_urls or set()
    score_by_url = {a.url: s for a, s in candidates}
    articles = [a for a, _ in candidates]

    async def _fake_probe(article: DiscoveredArticle) -> bool:
        return covers.get(article.url, article.has_qualified_cover)

    async def _fake_score(article: DiscoveredArticle) -> ArticleScore:
        return score_by_url[article.url]

    async def _fake_workflow(article, score, tracker=None):
        if article.url in failing_urls:
            raise RuntimeError(f"workflow failed: {article.url}")
        return {"url": article.url, "source": article.source, "score_tags": score.tags}

    score_mock = AsyncMock(side_effect=_fake_score)
    workflow_mock = AsyncMock(side_effect=_fake_workflow)

    with (
        patch(
            "app.services.daily_reader.pipeline.discover_guardian",
            new=AsyncMock(return_value=articles),
        ),
        patch(
            "app.services.daily_reader.pipeline.discover_rss_sources",
            new=AsyncMock(return_value=[]),
        ),
        patch("app.services.daily_reader.pipeline.probe_cover_eligible", new=_fake_probe),
        patch(
            "app.services.daily_reader.pipeline._get_existing_text_hashes",
            new=AsyncMock(return_value=set()),
        ),
        patch("app.services.daily_reader.pipeline.score_article", new=score_mock),
        patch("app.services.daily_reader.pipeline._run_workflow_and_store", new=workflow_mock),
        patch("app.services.daily_reader.pipeline.emit_pipeline_alerts", new=AsyncMock()),
    ):
        result = await run_daily_pipeline(max_count=max_count)
    return result, workflow_mock, score_mock


class TestSelectDiverseCandidates:
    def test_score_tag_overlap_blocks_second_same_topic(self):
        # article.tags differ, but score.tags overlap after normalization.
        c1 = _candidate(
            url="u1", title="Alpha", source="s1", score=9.0,
            tags=["Artificial Intelligence"], article_tags=["machine-learning"],
        )
        c2 = _candidate(
            url="u2", title="Beta", source="s2", score=8.5,
            tags=["artificial intelligence "], article_tags=["space"],
        )
        result = select_diverse_candidates([c1, c2], max_count=3)
        assert [a.url for a, _ in result] == ["u1"]

    def test_tag_normalization_case_and_whitespace(self):
        c1 = _candidate(
            url="u1", title="Alpha", source="s1", score=9.0, tags=["  Technology  ", "AI"],
        )
        c2 = _candidate(
            url="u2", title="Beta", source="s2", score=8.5, tags=["technology"],
        )
        result = select_diverse_candidates([c1, c2], max_count=3)
        assert [a.url for a, _ in result] == ["u1"]

    def test_empty_tags_do_not_create_pseudo_conflicts(self):
        c1 = _candidate(url="u1", title="Alpha", source="s1", score=9.0, tags=["", "   "])
        c2 = _candidate(url="u2", title="Beta", source="s2", score=8.5, tags=[""])
        result = select_diverse_candidates([c1, c2], max_count=3)
        assert [a.url for a, _ in result] == ["u1", "u2"]

    def test_candidates_without_score_tags_are_not_topic_blocked(self):
        # No score.tags → we do not guess a topic, so nothing conflicts.
        c1 = _candidate(url="u1", title="Alpha", source="s1", score=9.0, tags=[])
        c2 = _candidate(url="u2", title="Beta", source="s2", score=8.5, tags=[])
        result = select_diverse_candidates([c1, c2], max_count=3)
        assert [a.url for a, _ in result] == ["u1", "u2"]

    def test_source_cap_uses_normalized_source(self):
        c1 = _candidate(url="u1", title="Alpha", source="BBC", score=9.0, tags=["t1"])
        c2 = _candidate(url="u2", title="Beta", source=" bbc ", score=8.5, tags=["t2"])
        c3 = _candidate(url="u3", title="Gamma", source="BbC", score=8.0, tags=["t3"])
        result = select_diverse_candidates([c1, c2, c3], max_count=3)
        assert [a.url for a, _ in result] == ["u1", "u2"]


class TestPipelineOrdering:
    async def test_higher_content_score_outranks_cover(self):
        high_score_no_cover = _candidate(
            url="u-high", title="High Score No Cover", source="s1",
            score=8.5, tags=["t1"], has_cover=False,
        )
        low_score_with_cover = _candidate(
            url="u-low", title="Low Score With Cover", source="s2",
            score=7.2, tags=["t2"], has_cover=True,
        )
        result, workflow_mock, _ = await _run_pipeline(
            [low_score_with_cover, high_score_no_cover],
            max_count=1,
            covers={"u-low": True, "u-high": False},
        )
        assert workflow_mock.call_args_list[0].args[0].url == "u-high"
        assert result.articles[0]["url"] == "u-high"

    async def test_cover_only_breaks_equal_score_tie(self):
        no_cover = _candidate(
            url="u-a", title="Equal No Cover", source="s1",
            score=8.5, tags=["t1"], has_cover=False,
        )
        with_cover = _candidate(
            url="u-b", title="Equal With Cover", source="s2",
            score=8.5, tags=["t2"], has_cover=True,
        )
        result, workflow_mock, _ = await _run_pipeline(
            [no_cover, with_cover], max_count=1, covers={"u-a": False, "u-b": True},
        )
        assert workflow_mock.call_args_list[0].args[0].url == "u-b"
        assert result.articles[0]["url"] == "u-b"


class TestFallbackConstraints:
    async def test_fallback_respects_topic_and_source_constraints(self):
        # selected oversample: c1..c5; c3/c4/c5 fail their workflows, so the
        # fallback pool (c6/c7/c8) must refill under the same constraints:
        # c6 → source S1 already has 2 successes; c7 → topic-b already used.
        candidates = [
            _candidate(url="c1", title="C One", source="S1", score=9.0, tags=["topic-a"]),
            _candidate(url="c2", title="C Two", source="S1", score=8.8, tags=["topic-b"]),
            _candidate(url="c3", title="C Three", source="S2", score=8.6, tags=["topic-c"]),
            _candidate(url="c4", title="C Four", source="S3", score=8.4, tags=["topic-d"]),
            _candidate(url="c5", title="C Five", source="S4", score=8.2, tags=["topic-e"]),
            _candidate(url="c6", title="C Six", source="S1", score=8.0, tags=["topic-f"]),
            _candidate(url="c7", title="C Seven", source="S2", score=7.8, tags=["topic-b"]),
            _candidate(url="c8", title="C Eight", source="S5", score=7.6, tags=["topic-g"]),
        ]
        result, workflow_mock, _ = await _run_pipeline(
            candidates, max_count=3, failing_urls={"c3", "c4", "c5"},
        )

        assert [a["url"] for a in result.articles] == ["c1", "c2", "c8"]
        called_urls = [call.args[0].url for call in workflow_mock.call_args_list]
        assert called_urls == ["c1", "c2", "c3", "c4", "c5", "c8"]

        topics = [tag for a in result.articles for tag in a["score_tags"]]
        assert len(topics) == len(set(topics))

        source_counts: dict[str, int] = {}
        for a in result.articles:
            source_counts[a["source"]] = source_counts.get(a["source"], 0) + 1
        assert max(source_counts.values()) <= 2


class TestIndependentRuns:
    async def test_independent_pools_each_pick_same_topic(self):
        day1 = [
            _candidate(url="d1-a", title="Day One Climate", source="s1", score=9.0,
                       tags=["climate"]),
            _candidate(url="d1-b", title="Day One Energy", source="s2", score=8.5,
                       tags=["energy"]),
        ]
        day2 = [
            _candidate(url="d2-a", title="Day Two Climate", source="s3", score=9.0,
                       tags=["climate"]),
            _candidate(url="d2-b", title="Day Two AI", source="s4", score=8.5,
                       tags=["ai"]),
        ]
        result1, _, _ = await _run_pipeline(day1, max_count=2)
        result2, _, _ = await _run_pipeline(day2, max_count=2)

        assert [a["score_tags"] for a in result1.articles] == [["climate"], ["energy"]]
        assert [a["score_tags"] for a in result2.articles] == [["climate"], ["ai"]]


class TestSourceConfigAndCap:
    def test_guardian_sections_include_expanded_slugs(self):
        sections = ARTICLE_SOURCES["guardian"]["sections"]
        for slug in ("science", "technology", "culture", "society", "artanddesign",
                     "lifeandstyle"):
            assert slug in sections

    def test_bbc_feeds_include_health_and_entertainment_arts(self):
        feeds = ARTICLE_SOURCES["bbc"]["feeds"]
        assert feeds["health"] == "https://feeds.bbci.co.uk/news/health/rss.xml"
        assert (
            feeds["entertainment_and_arts"]
            == "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"
        )
        for url in feeds.values():
            assert url.startswith("https://feeds.bbci.co.uk/news/")
            assert url.endswith("/rss.xml")

    def test_scoring_max_candidates_is_10(self):
        assert SCORING_MAX_CANDIDATES == 10

    async def test_scoring_cap_limits_llm_scored_candidates(self):
        candidates = [
            _candidate(
                url=f"cap-{i:02d}", title=f"Cap Candidate {i:02d}", source=f"src-{i}",
                score=8.0, tags=[f"topic-{i}"], word_count=1000,
            )
            for i in range(10)
        ]
        # Lower heuristic score (word_count bracket) → these two are trimmed.
        candidates += [
            _candidate(
                url=f"cap-{i:02d}", title=f"Cap Candidate {i:02d}", source=f"src-{i}",
                score=8.0, tags=[f"topic-{i}"], word_count=1400,
            )
            for i in range(10, 12)
        ]

        _, _, score_mock = await _run_pipeline(candidates, max_count=3)

        assert score_mock.call_count == 10
        scored_urls = {call.args[0].url for call in score_mock.call_args_list}
        assert scored_urls == {f"cap-{i:02d}" for i in range(10)}
