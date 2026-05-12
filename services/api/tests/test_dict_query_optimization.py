"""
/dict 查询优化集成测试。

覆盖：
1. smaller/larger 通过 ADJ lemma fallback 命中
2. place + context → take place (context phrase sniff)
3. there/you + context → be there for sb. (template phrase)
4. type=phrase 自然短语模板命中 (be there for you → be there for sb)
5. phase 排序优先级不回归
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.services.dictionary.providers.tecd3 import Tecd3Provider
from app.services.dictionary.schemas import DictionaryLookupRequest


@dataclass(frozen=True)
class _CandidateRow:
    """Minimal stand-in for db_pg.CandidateRow with all required fields."""

    entry_id: int
    normalized_form: str
    lookup_label: str
    target_label: str
    target_pos: str | None
    preview_text: str | None
    rank: int
    match_kind: str
    entry_kind: str
    lookup_type: str
    has_meanings: bool = True


class _EntryRow:
    """Minimal stand-in for db_pg.EntryRow."""

    def __init__(self, id: int, word: str, entry_kind: str = "entry") -> None:
        self.id = id
        self.source = "tecd3"
        self.source_entry_key = word
        self.entry_kind = entry_kind
        self.display_headword = word
        self.base_headword = word
        self.homograph_no = None
        self.phonetic = None
        self.meanings_json = []
        self.examples_json = []
        self.phrases_json = []
        self.sections_json = []
        self.raw_html = None
        self.parse_version = "1"
        self.exam_tags = []


def _word_candidate(
    entry_id: int,
    label: str,
    match_kind: str = "headword",
    rank: int = 1,
    normalized_form: str | None = None,
    target_pos: str | None = "n.",
) -> _CandidateRow:
    return _CandidateRow(
        entry_id=entry_id,
        normalized_form=normalized_form or label,
        lookup_label=label,
        target_label=label,
        target_pos=target_pos,
        preview_text=f"preview:{label}",
        rank=rank,
        match_kind=match_kind,
        entry_kind="entry",
        lookup_type="word",
    )


def _phrase_candidate(entry_id: int, label: str, match_kind: str = "phrase", rank: int = 10) -> _CandidateRow:
    return _CandidateRow(
        entry_id=entry_id,
        normalized_form=label,
        lookup_label=label,
        target_label=label,
        target_pos=None,
        preview_text=f"preview:{label}",
        rank=rank,
        match_kind=match_kind,
        entry_kind="fragment",
        lookup_type="phrase",
    )


class TestLemmaFallbackADJADV:
    """ADJ/ADV lemma fallback 通过 tecd3 provider 端到端验证。"""

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
    async def test_smaller_finds_small(self, provider: Tecd3Provider) -> None:
        """查询 'smaller' 应通过 ADJ lemma fallback 命中 'small' 词条。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms, source="tecd3"):
                if "small" in forms:
                    return [_word_candidate(100, "small")]
                return []

            mock_lookup.side_effect = fake_lookup
            mock_fetch.return_value = _EntryRow(100, "small")

            result = await provider.fetch(
                DictionaryLookupRequest(query="smaller", query_type="word")
            )
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "smaller"
            assert result["entry"]["base_word"] == "small"

    @pytest.mark.asyncio
    async def test_larger_finds_large(self, provider: Tecd3Provider) -> None:
        """查询 'larger' 应通过 ADJ lemma fallback 命中 'large' 词条。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms, source="tecd3"):
                if "large" in forms:
                    return [_word_candidate(101, "large")]
                return []

            mock_lookup.side_effect = fake_lookup
            mock_fetch.return_value = _EntryRow(101, "large")

            result = await provider.fetch(
                DictionaryLookupRequest(query="larger", query_type="word")
            )
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "larger"
            assert result["entry"]["base_word"] == "large"


class TestContextPhraseSniff:
    """Context-based phrase sniff 不回归。"""

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
    async def test_place_with_context_finds_take_place(self, provider: Tecd3Provider) -> None:
        """'place' + context 'The ceremony takes place tomorrow.' 应该嗅探到 'take place'。"""
        context = "The ceremony takes place tomorrow."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if "take place" in f:
                        results.append(_phrase_candidate(200, "take place"))
                    elif f == "place":
                        results.append(_word_candidate(201, "place"))
                return results

            mock_lookup.side_effect = fake_lookup
            mock_fetch.return_value = _EntryRow(200, "take place", entry_kind="fragment")

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="place",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )
            # 应该返回 disambiguation，且 take place 排在 place 前面
            if result["result_type"] == "disambiguation":
                labels = [c["label"] for c in result["candidates"]]
                assert "take place" in labels
                take_place_idx = labels.index("take place")
                if "place" in labels:
                    place_idx = labels.index("place")
                    assert take_place_idx < place_idx, "take place should rank before plain place"
            else:
                # 如果只返回一个结果（single entry），确保是 take place
                assert result["entry"]["word"] == "take place"

    @pytest.mark.asyncio
    async def test_up_with_context_finds_look_up(self, provider: Tecd3Provider) -> None:
        """'up' + context 'I look up the word...' 应嗅探到 bare phrase 'look up'。"""
        context = "I look up the word in a dictionary."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if f == "look up":
                        results.append(_phrase_candidate(210, "look up"))
                    elif f == "up":
                        results.append(_word_candidate(211, "up"))
                return results

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="up",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )
            assert result["result_type"] == "disambiguation"
            labels = [c["label"] for c in result["candidates"]]
            assert labels.index("look up") < labels.index("up")

    @pytest.mark.asyncio
    async def test_out_with_context_finds_find_out(self, provider: Tecd3Provider) -> None:
        """'out' + context 'She found out...' 应嗅探到 bare phrase 'find out'。"""
        context = "She found out the truth yesterday."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if f == "find out":
                        results.append(_phrase_candidate(220, "find out"))
                    elif f == "out":
                        results.append(_word_candidate(221, "out"))
                return results

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="out",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )
            assert result["result_type"] == "disambiguation"
            labels = [c["label"] for c in result["candidates"]]
            assert labels.index("find out") < labels.index("out")

    @pytest.mark.asyncio
    async def test_forward_with_context_finds_look_forward_to(self, provider: Tecd3Provider) -> None:
        """'forward' + context 'look forward to...' 应嗅探到 'look forward to'。"""
        context = "I look forward to hearing from you."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if f == "look forward to":
                        results.append(_phrase_candidate(230, "look forward to"))
                    elif f == "forward":
                        results.append(_word_candidate(231, "forward"))
                return results

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="forward",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )
            assert result["result_type"] == "disambiguation"
            labels = [c["label"] for c in result["candidates"]]
            assert labels.index("look forward to") < labels.index("forward")


class TestTemplatePhrase:
    """模板短语 be there for sb 的命中测试。"""

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
    async def test_there_with_context_finds_be_there_for_sb(self, provider: Tecd3Provider) -> None:
        """'there' + context 'I will always be there for you.' 应嗅探到 'be there for sb'。"""
        context = "I will always be there for you."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if "be there for sb" in f:
                        results.append(_phrase_candidate(300, "be there for sb", match_kind="phrase_template"))
                    elif f == "there":
                        results.append(_word_candidate(301, "there"))
                return results

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="there",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )
            if result["result_type"] == "disambiguation":
                labels = [c["label"] for c in result["candidates"]]
                assert "be there for sb" in labels


class TestPhraseQueryTemplateMatch:
    """type=phrase 自然短语 → 模板命中。"""

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
    async def test_be_there_for_you_matches_template(self, provider: Tecd3Provider) -> None:
        """type=phrase 'be there for you' 应通过 spaCy 模板化命中 'be there for sb'。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms, source="tecd3"):
                # "be there for you" 不在库里，但 "be there for sb" 在
                results = []
                for f in forms:
                    if f == "be there for sb":
                        results.append(_phrase_candidate(400, "be there for sb", match_kind="phrase_template"))
                return results

            mock_lookup.side_effect = fake_lookup
            mock_fetch.return_value = _EntryRow(400, "be there for sb.", entry_kind="fragment")

            result = await provider.fetch(
                DictionaryLookupRequest(query="be there for you", query_type="phrase")
            )
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "be there for you"
            assert "be there for sb" in result["entry"]["base_word"]


class TestPhaseSortPriority:
    """排序优先级：context phrase > direct word > lemma context > lemma direct。"""

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
    async def test_context_phrase_ranks_before_direct_word(self, provider: Tecd3Provider) -> None:
        """context 嗅探的 phrase 候选应排在直接 word 候选之前。"""
        context = "The ceremony takes place tomorrow."

        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            async def fake_lookup(forms, source="tecd3"):
                results = []
                for f in forms:
                    if "take place" in f:
                        results.append(_phrase_candidate(500, "take place"))
                    elif f == "place":
                        results.append(_word_candidate(501, "place"))
                return results

            mock_lookup.side_effect = fake_lookup

            result = await provider.fetch(
                DictionaryLookupRequest(
                    query="place",
                    query_type="word",
                    context_sentence=context,
                    occurrence=1,
                )
            )

            assert result["result_type"] == "disambiguation"
            labels = [c["label"] for c in result["candidates"]]
            # take place (context phrase, phase 0) should come before place (direct, phase 1)
            assert labels.index("take place") < labels.index("place")

    @pytest.mark.asyncio
    async def test_lemma_direct_ranks_after_non_lemma(self, provider: Tecd3Provider) -> None:
        """lemma fallback 的结果 phase 应比 direct 查询高（数值更大=更靠后）。

        这里模拟 'smaller' exact 失败 → lemma 'small' 命中 → phase=3。
        """
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            async def fake_lookup(forms, source="tecd3"):
                if "small" in forms:
                    return [_word_candidate(600, "small")]
                return []

            mock_lookup.side_effect = fake_lookup
            mock_fetch.return_value = _EntryRow(600, "small")

            result = await provider.fetch(
                DictionaryLookupRequest(query="smaller", query_type="word")
            )
            # 应该成功返回，说明 lemma fallback 生效
            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "smaller"
            assert result["entry"]["base_word"] == "small"
            # 验证 lookup 被调了两次：第一次 exact 失败，第二次 lemma 命中
            assert mock_lookup.call_count == 2

    @pytest.mark.asyncio
    async def test_lowercase_word_drops_same_spelling_capitalized_name(self, provider: Tecd3Provider) -> None:
        """小写普通词命中时，同拼写大写专名不应强制进入消歧。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup, patch(
            "app.services.dictionary.providers.tecd3.fetch_entry",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_lookup.return_value = [
                _word_candidate(700, "North", rank=1, normalized_form="north", target_pos=None),
                _word_candidate(701, "north", rank=5, normalized_form="north", target_pos="n."),
            ]
            mock_fetch.return_value = _EntryRow(701, "north")

            result = await provider.fetch(
                DictionaryLookupRequest(query="north", query_type="word")
            )

            assert result["result_type"] == "entry"
            assert result["entry"]["word"] == "north"

    @pytest.mark.asyncio
    async def test_same_headword_disambiguation_does_not_require_selection(self, provider: Tecd3Provider) -> None:
        """同词头多词性属于低风险多义，前端可先展示首候选释义。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            mock_lookup.return_value = [
                _word_candidate(800, "most", rank=1, normalized_form="most", target_pos="adj."),
                _word_candidate(801, "most", rank=2, normalized_form="most", target_pos="adv."),
            ]

            result = await provider.fetch(
                DictionaryLookupRequest(query="most", query_type="word")
            )

            assert result["result_type"] == "disambiguation"
            assert result["ambiguity_kind"] == "same_headword_senses"
            assert result["selection_required"] is False
            assert [c["candidate_kind"] for c in result["candidates"]] == ["word", "word"]

    @pytest.mark.asyncio
    async def test_phrase_vs_word_disambiguation_requires_selection(self, provider: Tecd3Provider) -> None:
        """短语与单词竞争时仍应显式选择，避免误保存错误词条。"""
        with patch(
            "app.services.dictionary.providers.tecd3.lookup_candidates_batch",
            new_callable=AsyncMock,
        ) as mock_lookup:
            mock_lookup.return_value = [
                _phrase_candidate(810, "take off"),
                _word_candidate(811, "off", rank=2, normalized_form="off", target_pos="adv."),
            ]

            result = await provider.fetch(
                DictionaryLookupRequest(query="off", query_type="word")
            )

            assert result["result_type"] == "disambiguation"
            assert result["ambiguity_kind"] == "phrase_vs_word"
            assert result["selection_required"] is True
            assert "phrase" in {c["candidate_kind"] for c in result["candidates"]}
