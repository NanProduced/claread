"""TECD3 Provider integration tests with lemma fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.dictionary.providers.tecd3 import Tecd3Provider
from app.services.dictionary.schemas import DictionaryLookupRequest


def _make_candidate(
    entry_id: int,
    target_label: str,
    entry_kind: str = "entry",
    has_meanings: bool = True,
) -> object:
    """Create a mock CandidateRow-like object."""
    return _CandidateMock(
        entry_id=entry_id,
        target_label=target_label,
        entry_kind=entry_kind,
        has_meanings=has_meanings,
    )


def _make_entry(entry_id: int, word: str) -> object:
    """Create a mock EntryRow-like object."""
    return _EntryMock(
        id=entry_id,
        source="tecd3",
        source_entry_key=word,
        entry_kind="entry",
        display_headword=word,
        base_headword=word,
        homograph_no=None,
        phonetic=None,
        meanings_json=[],
        examples_json=[],
        phrases_json=[],
        sections_json=[],
        raw_html=None,
        parse_version="1",
    )


class _CandidateMock:
    """Minimal stand-in for db_pg.CandidateRow."""

    def __init__(self, entry_id: int, target_label: str, entry_kind: str = "entry", has_meanings: bool = True) -> None:
        self.entry_id = entry_id
        self.target_label = target_label
        self.normalized_form = target_label
        self.lookup_label = target_label
        self.target_pos = "n."
        self.preview_text = f"preview for {target_label}"
        self.rank = 1
        self.match_kind = "exact"
        self.entry_kind = entry_kind
        self.lookup_type = "word"
        self.has_meanings = has_meanings


class _EntryMock:
    """Minimal stand-in for db_pg.EntryRow."""

    def __init__(
        self,
        id: int,
        source: str,
        source_entry_key: str,
        entry_kind: str,
        display_headword: str,
        base_headword: str | None,
        homograph_no: int | None,
        phonetic: str | None,
        meanings_json: list,
        examples_json: list,
        phrases_json: list,
        sections_json: list,
        raw_html: str | None,
        parse_version: str,
        exam_tags: list[str] | None = None,
    ) -> None:
        self.id = id
        self.source = source
        self.source_entry_key = source_entry_key
        self.entry_kind = entry_kind
        self.display_headword = display_headword
        self.base_headword = base_headword
        self.homograph_no = homograph_no
        self.phonetic = phonetic
        self.meanings_json = meanings_json
        self.examples_json = examples_json
        self.phrases_json = phrases_json
        self.sections_json = sections_json
        self.raw_html = raw_html
        self.parse_version = parse_version
        self.exam_tags = exam_tags or []


class TestTecd3ProviderLemmaFallback:
    """Test lemma fallback behavior in Tecd3Provider.fetch()."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear L1 cache before each test to prevent cross-test pollution."""
        from app.services.dictionary import cache as cache_module
        cache_module._L1_CACHE.clear()
        cache_module._cache_hits = 0
        cache_module._cache_misses = 0

    @pytest.fixture
    def provider(self) -> Tecd3Provider:
        return Tecd3Provider()

    @pytest.mark.asyncio
    async def test_exact_match_not_disrupted_by_lemma_fallback(
        self, provider: Tecd3Provider
    ) -> None:
        """Exact match takes priority over lemma fallback."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "human" in forms:
                    return [_make_candidate(entry_id=1, target_label="human")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="human")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="human", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "human"
            assert mock_lookup.call_count == 1

    @pytest.mark.asyncio
    async def test_lemma_fallback_fires_for_unknown_word(
        self, provider: Tecd3Provider
    ) -> None:
        """When exact fails, lemma fallback fires for single words."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "human" in forms:
                    return [_make_candidate(entry_id=5, target_label="human")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="human")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="humans", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "humans"
            assert result["entry"]["base_word"] == "human"
            assert mock_lookup.call_count == 2
            assert mock_lookup.call_args_list[0][0][0] == ["humans"]
            assert mock_lookup.call_args_list[1][0][0] == ["human"]

    @pytest.mark.asyncio
    async def test_phrase_raises_not_found(self, provider: Tecd3Provider) -> None:
        """Phrases without results raise ValueError (no lemma fallback)."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            mock_lookup.return_value = []

            with pytest.raises(ValueError, match="Word not found"):
                await provider.fetch(DictionaryLookupRequest(query="hello world", query_type="word"))
            assert mock_lookup.call_count == 1

    @pytest.mark.asyncio
    async def test_hopes_falls_back_to_hope(self, provider: Tecd3Provider) -> None:
        """'hopes' lemma fallback to 'hope'."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "hope" in forms:
                    return [_make_candidate(entry_id=7, target_label="hope")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="hope")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="hopes", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "hopes"
            assert result["entry"]["base_word"] == "hope"
            assert mock_lookup.call_count == 2

    @pytest.mark.asyncio
    async def test_landings_falls_back_to_landing(
        self, provider: Tecd3Provider
    ) -> None:
        """'landings' lemma fallback to 'landing'."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "landing" in forms:
                    return [_make_candidate(entry_id=9, target_label="landing")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="landing")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="landings", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "landings"
            assert result["entry"]["base_word"] == "landing"

    @pytest.mark.asyncio
    async def test_crewed_falls_back_to_crew(self, provider: Tecd3Provider) -> None:
        """'crewed' lemma fallback to 'crew'."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "crew" in forms:
                    return [_make_candidate(entry_id=3, target_label="crew")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="crew")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="crewed", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "crewed"
            assert result["entry"]["base_word"] == "crew"

    @pytest.mark.asyncio
    async def test_low_quality_fragment_falls_back_to_lemma_entry(
        self, provider: Tecd3Provider
    ) -> None:
        """Degenerate fragment rows should not block lemma fallback."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "app.services.dictionary.providers.tecd3.get_lemma_candidates",
            return_value=["release"],
        ):
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if forms == ["released"]:
                    return [_make_candidate(entry_id=11, target_label="released", entry_kind="fragment")]
                if forms == ["release"]:
                    return [_make_candidate(entry_id=12, target_label="release", entry_kind="entry")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                if entry_id == 11:
                    return _EntryMock(
                        id=11,
                        source="tecd3",
                        source_entry_key="released",
                        entry_kind="fragment",
                        display_headword="released",
                        base_headword="released",
                        homograph_no=None,
                        phonetic=None,
                        meanings_json=[
                            {
                                "part_of_speech": "",
                                "definitions": [
                                    {
                                        "meaning": "released released",
                                        "example": None,
                                        "example_translation": None,
                                    }
                                ],
                            }
                        ],
                        examples_json=[],
                        phrases_json=[],
                        sections_json=[],
                        raw_html=None,
                        parse_version="1",
                    )
                return _make_entry(entry_id=12, word="release")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="released", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "released"
            assert result["entry"]["base_word"] == "release"
            assert mock_lookup.call_count == 2
            assert mock_lookup.call_args_list[0][0][0] == ["released"]
            assert mock_lookup.call_args_list[1][0][0] == ["release"]

    @pytest.mark.asyncio
    async def test_disambiguation_not_disrupted_by_lemma_fallback(
        self, provider: Tecd3Provider
    ) -> None:
        """Exact disambiguation (multiple candidates) still works normally."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            mock_lookup.return_value = [
                _make_candidate(entry_id=10, target_label="anti"),
                _make_candidate(entry_id=11, target_label="anti-"),
            ]
            result = await provider.fetch(DictionaryLookupRequest(query="anti", query_type="word"))
            assert result["result_type"] == "disambiguation"
            assert len(result["candidates"]) == 2
            assert mock_lookup.call_count == 1

    @pytest.mark.asyncio
    async def test_lemma_single_hit_returns_entry(
        self, provider: Tecd3Provider
    ) -> None:
        """Single lemma hit → returns entry result (no disambiguation)."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                if "crew" in forms:
                    return [_make_candidate(entry_id=3, target_label="crew")]
                return []

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _make_entry(entry_id=entry_id, word="crew")

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="crewed", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "crewed"
            assert result["entry"]["base_word"] == "crew"

    @pytest.mark.asyncio
    async def test_lemma_multiple_hits_triggers_disambiguation(
        self, provider: Tecd3Provider
    ) -> None:
        """Multiple lemma hits → triggers disambiguation (e.g. axes → axis + axe)."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            # "axes" → noun lemmas: ["axis", "axe"]; both exist in DB
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                res = []
                if "axis" in forms:
                    res.append(_make_candidate(entry_id=1, target_label="axis"))
                if "axe" in forms:
                    res.append(_make_candidate(entry_id=2, target_label="axe"))
                return res

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(DictionaryLookupRequest(query="axes", query_type="word"))
            assert result["result_type"] == "disambiguation"
            assert len(result["candidates"]) == 2
            labels = {c["label"] for c in result["candidates"]}
            assert labels == {"axis", "axe"}

    @pytest.mark.asyncio
    async def test_lemma_collects_all_hits_not_first_only(
        self, provider: Tecd3Provider
    ) -> None:
        """All lemma hits are collected and deduplicated before returning."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            # Simulate: "axes" exact fails, lemma "axis" hits (entry_id=1),
            # lemma "axe" also hits (entry_id=2) → disambiguation
            async def fake_lookup(forms: list[str], source: str = "tecd3"):
                res = []
                if "axis" in forms:
                    res.append(_make_candidate(entry_id=1, target_label="axis"))
                if "axe" in forms:
                    res.append(_make_candidate(entry_id=2, target_label="axe"))
                return res

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                word = "axis" if entry_id == 1 else "axe"
                return _make_entry(entry_id=entry_id, word=word)

            mock_lookup.side_effect = fake_lookup
            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="axes", query_type="word"))
            # Both lemmas hit → disambiguation, not entry
            assert result["result_type"] == "disambiguation"
            assert len(result["candidates"]) == 2


class TestTecd3ProviderFragmentFallback:
    """Test fragment derivative fallback behavior in Tecd3Provider.fetch()."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        from app.services.dictionary import cache as cache_module
        cache_module._L1_CACHE.clear()
        cache_module._cache_hits = 0
        cache_module._cache_misses = 0

    @pytest.fixture
    def provider(self) -> Tecd3Provider:
        return Tecd3Provider()

    @pytest.mark.asyncio
    async def test_chronically_falls_back_to_chronic(
        self, provider: Tecd3Provider
    ) -> None:
        """'chronically' returns parent entry 'chronic' with meanings."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            fragment_candidate = _CandidateMock(
                entry_id=35972, target_label="chronically",
                entry_kind="fragment", has_meanings=False,
            )
            parent_candidate = _CandidateMock(
                entry_id=35970, target_label="chronic",
                entry_kind="entry", has_meanings=True,
            )
            mock_lookup.return_value = [fragment_candidate, parent_candidate]

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                if entry_id == 35970:
                    return _EntryMock(
                        id=35970, source="tecd3", source_entry_key="chronic",
                        entry_kind="entry", display_headword="chronic",
                        base_headword="chronic", homograph_no=None, phonetic=None,
                        meanings_json=[{"part_of_speech": "adj.", "definitions": [{"meaning": "慢性的"}]}],
                        examples_json=[], phrases_json=[], sections_json=[],
                        raw_html=None, parse_version="1",
                    )
                return _make_entry(entry_id=entry_id, word="chronically")

            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="chronically", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "chronically"
            assert result["entry"]["base_word"] == "chronic"
            assert len(result["entry"]["meanings"]) > 0

    @pytest.mark.asyncio
    async def test_empty_fragment_filtered_when_parent_exists(
        self, provider: Tecd3Provider
    ) -> None:
        """Empty fragment is filtered out when a meaningful parent candidate exists."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            fragment_candidate = _CandidateMock(
                entry_id=1, target_label="significantly",
                entry_kind="fragment", has_meanings=False,
            )
            parent_candidate = _CandidateMock(
                entry_id=2, target_label="significant",
                entry_kind="entry", has_meanings=True,
            )
            mock_lookup.return_value = [fragment_candidate, parent_candidate]

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _EntryMock(
                    id=entry_id, source="tecd3", source_entry_key="significant",
                    entry_kind="entry", display_headword="significant",
                    base_headword="significant", homograph_no=None, phonetic=None,
                    meanings_json=[{"part_of_speech": "adj.", "definitions": [{"meaning": "重要的"}]}],
                    examples_json=[], phrases_json=[], sections_json=[],
                    raw_html=None, parse_version="1",
                )

            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="significantly", query_type="word"))
            assert result["result_type"] == "entry"
            assert len(result["entry"]["meanings"]) > 0

    @pytest.mark.asyncio
    async def test_fragment_only_kept_when_no_meaningful_alternative(
        self, provider: Tecd3Provider
    ) -> None:
        """Empty fragment is kept when no meaningful alternative exists."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            fragment_candidate = _CandidateMock(
                entry_id=1, target_label="orphanword",
                entry_kind="fragment", has_meanings=False,
            )
            mock_lookup.return_value = [fragment_candidate]

            async def fake_fetch(entry_id: int, source: str = "tecd3"):
                return _EntryMock(
                    id=entry_id, source="tecd3", source_entry_key="orphanword",
                    entry_kind="fragment", display_headword="orphanword",
                    base_headword="orphanword", homograph_no=None, phonetic=None,
                    meanings_json=[], examples_json=[], phrases_json=[],
                    sections_json=[], raw_html=None, parse_version="1",
                )

            mock_fetch.side_effect = fake_fetch

            result = await provider.fetch(DictionaryLookupRequest(query="orphanword", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["entry_kind"] == "fragment"

    @pytest.mark.asyncio
    async def test_normal_entry_not_filtered(
        self, provider: Tecd3Provider
    ) -> None:
        """Normal entries with meanings are not affected by fragment filtering."""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_lookup.return_value = [_make_candidate(entry_id=1, target_label="apple")]
            mock_fetch.return_value = _make_entry(entry_id=1, word="apple")

            result = await provider.fetch(DictionaryLookupRequest(query="apple", query_type="word"))
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "apple"
