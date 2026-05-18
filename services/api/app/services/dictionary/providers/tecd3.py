"""
TECD3 本地词典 Provider。

使用 PostgreSQL 中的 dict_entries / dict_lookup_targets 提供查询能力。

Lookup 优先级：
1. exact headword / lookup target
2. redirect
3. disambiguation
4. .nlp imported lookup targets
5. lemma fallback（LemmInflect 还原词形后再查）
6. 404
"""

from __future__ import annotations

import re
from typing import Any

from app.services.dictionary import cache as dict_cache
from app.services.dictionary.db_pg import CandidateRow, EntryRow, fetch_entry, lookup_candidates_batch
from app.services.dictionary.lemma import get_lemma_candidates
from app.services.dictionary.schemas import (
    DictionaryCandidate,
    DictionaryDisambiguationResult,
    DictionaryEntryPayload,
    DictionaryEntryResult,
    DictionaryExample,
    DictionaryLookupRequest,
    DictionaryMeaning,
    DictionaryPhrase,
    validate_lookup_result,
)

PROPER_NOUN_POS = {"pn", "propn", "proper_noun", "proper noun", "专名", "专有名词"}


class Tecd3Provider:
    source = "tecd3"
    cache_version = "v5"

    async def fetch(self, request: DictionaryLookupRequest) -> dict[str, Any]:
        import hashlib
        ctx_hash = hashlib.md5(request.context_sentence.encode()).hexdigest()[:8] if request.context_sentence else "none"
        occ = request.occurrence or 0
        cache_key = f"{self.source}:{self.cache_version}:lookup:q={request.query}:type={request.query_type}:ctx={ctx_hash}:occ={occ}:strategy=v2"
        cached = await dict_cache.get(cache_key)
        if cached is not None:
            result = validate_lookup_result(cached)
            result["cached"] = True
            return result

        from app.services.dictionary.phrase_candidates import generate_candidates

        # 1. 直接查询 query
        direct_forms = [request.query]

        # 2. 如果有 context_sentence，嗅探短语
        context_forms = []
        doc = None
        if request.context_sentence:
            from app.services.dictionary.nlp import check_dict_spacy_model, get_dict_nlp
            if check_dict_spacy_model():
                nlp = get_dict_nlp()
                doc = nlp(request.context_sentence)

        if doc and request.query_type == "word":
            context_forms = generate_candidates(request.query, request.context_sentence, request.occurrence, doc=doc)

        # 3. 如果 query 本身是 phrase，增加 canonical template
        if request.query_type == "phrase":
            from app.services.dictionary.phrase_templates import canonicalize_dictionary_phrase
            template_form = canonicalize_dictionary_phrase(request.query)
            if template_form and template_form != request.query:
                direct_forms.append(template_form)

            # 对自然短语用 spaCy 生成 template 候选,
            # 例如 "be there for you" → "be there for sb"
            try:
                from app.services.dictionary.nlp import check_dict_spacy_model, get_dict_nlp
                from app.services.dictionary.phrase_templates import canonicalize_sentence_span
                if check_dict_spacy_model():
                    # 这里如果是 phrase 查询，我们对 query 本身建 doc
                    # 注意：这跟 context_sentence 的 doc 不同
                    nlp = get_dict_nlp()
                    phrase_doc = nlp(request.query)
                    if len(phrase_doc) > 1:
                        span = phrase_doc[:]
                        # 不指定锚点 — phrase 查询中所有词都可以被替换为槽位
                        spacy_template = canonicalize_sentence_span(span, set())
                        if spacy_template and spacy_template not in direct_forms:
                            direct_forms.append(spacy_template)
            except Exception:
                pass  # spaCy 不可用时静默降级

        # 保存原始 forms 集合用于 phase 排序
        orig_context_forms = set(context_forms)
        orig_direct_forms = set(direct_forms)

        all_forms = []
        for f in context_forms + direct_forms:
            if f not in all_forms:
                all_forms.append(f)

        candidates = await lookup_candidates_batch(all_forms, source=self.source)

        if (
            candidates
            and request.query_type == "word"
            and " " not in request.query
            and len(candidates) == 1
            and candidates[0].entry_kind == "fragment"
        ):
            fragment_entry = await fetch_entry(candidates[0].entry_id, source=self.source)
            if fragment_entry and self._is_low_quality_fragment_entry(request.query, fragment_entry):
                candidates = []

        # 4. Lemma fallback
        is_lemma_fallback = False
        lemma_context_forms_set: set[str] = set()
        lemma_direct_forms_set: set[str] = set()

        if not candidates and request.query_type == "word" and " " not in request.query:
            is_lemma_fallback = True
            lemma_candidates_forms = get_lemma_candidates(request.query)
            lemma_all_forms = []

            for lemma in lemma_candidates_forms:
                if request.context_sentence:
                    ctx_f = generate_candidates(lemma, request.context_sentence, request.occurrence, doc=doc)
                    for f in ctx_f:
                        lemma_context_forms_set.add(f)
                        if f not in lemma_all_forms:
                            lemma_all_forms.append(f)
                if lemma not in lemma_all_forms:
                    lemma_all_forms.append(lemma)
                lemma_direct_forms_set.add(lemma)

            candidates = await lookup_candidates_batch(lemma_all_forms, source=self.source)

        if not candidates:
            await dict_cache.set_miss(cache_key)
            raise ValueError(f"Word not found: {request.query}")

        # 5. 重排规则
        def get_sort_key(c: CandidateRow):
            # phase_priority:
            # 0: 原始 context phrase exact/template match
            # 1: 原始 direct phrase/query exact match
            # 2: lemma context phrase match
            # 3: lemma direct match

            nf = c.normalized_form
            if nf in orig_context_forms:
                phase = 0
            elif nf in orig_direct_forms:
                phase = 1
            elif is_lemma_fallback and nf in lemma_context_forms_set:
                phase = 2
            elif is_lemma_fallback and nf in lemma_direct_forms_set:
                phase = 3
            else:
                phase = 3  # 未知来源排在最后

            query_type_priority = 0 if request.query_type == "phrase" and c.lookup_type == "phrase" else 1
            token_count = len(c.normalized_form.split())

            match_kind_priority = 4
            if c.match_kind in ("phrase", "phrase_template"):
                match_kind_priority = 0
            elif c.match_kind == "headword":
                match_kind_priority = 1
            elif c.match_kind == "redirect":
                match_kind_priority = 2
            elif c.match_kind == "nlp":
                match_kind_priority = 3

            entry_kind_priority = 0 if c.entry_kind == "entry" else 1

            return (
                phase,
                query_type_priority,
                -token_count,
                match_kind_priority,
                entry_kind_priority,
                c.rank,
                c.entry_id
            )

        candidates.sort(key=get_sort_key)

        unique_candidates = []
        seen = set()
        for c in candidates:
            if c.entry_id not in seen:
                seen.add(c.entry_id)
                unique_candidates.append(c)

        candidates = unique_candidates

        has_meaningful = any(c.has_meanings for c in candidates)
        if has_meaningful:
            candidates = [c for c in candidates if c.has_meanings]

        candidates = self._drop_capitalized_proper_noise(request.query, candidates)

        if len(candidates) == 1:
            entry = await fetch_entry(candidates[0].entry_id, source=self.source)
            if entry is None:
                raise ValueError(f"Word not found: {request.query}")
            result = self._build_entry_result(request.query, entry)
        else:
            result = self._build_disambiguation_result(request.query, candidates)

        await dict_cache.set(cache_key, result)
        return result

    async def _lemma_fallback(self, query: str) -> list[CandidateRow]:
        pass

    def _is_low_quality_fragment_entry(self, query: str, entry: EntryRow) -> bool:
        """Detect degenerate fragment rows such as 'released released'."""
        if entry.entry_kind != "fragment":
            return False
        if entry.phonetic or entry.examples_json or entry.phrases_json:
            return False

        meanings = self._parse_meanings(entry)
        if len(meanings) != 1:
            return False

        meaning = meanings[0]
        if meaning.part_of_speech.strip() or len(meaning.definitions) != 1:
            return False

        definition_text = meaning.definitions[0].meaning.strip()
        if not definition_text:
            return False

        normalized_query = self._normalize_fragment_text(query)
        definition_tokens = [
            token
            for token in self._normalize_fragment_text(definition_text).split(" ")
            if token
        ]
        if not normalized_query or not definition_tokens:
            return False

        return all(token == normalized_query for token in definition_tokens)

    def _normalize_fragment_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().lower()

    def _drop_capitalized_proper_noise(self, query: str, candidates: list[CandidateRow]) -> list[CandidateRow]:
        """Keep ordinary lowercase entries ahead of same-spelling proper names.

        TECD3 contains many capitalized name entries. For a lowercase in-sentence lookup
        such as "north", those should not force a disambiguation result when an ordinary
        lowercase headword is also available.
        """
        normalized_query = query.strip()
        if not normalized_query or normalized_query != normalized_query.lower() or " " in normalized_query:
            return candidates

        exact_lowercase = [
            c for c in candidates
            if (c.lookup_label or c.target_label).strip() == normalized_query
        ]
        if not exact_lowercase:
            return candidates

        return [
            c for c in candidates
            if (c.lookup_label or c.target_label).strip() == normalized_query
            or (c.lookup_label or c.target_label).strip().lower() != normalized_query
        ]


    async def fetch_entry(self, entry_id: int) -> dict[str, Any]:
        cache_key = f"{self.source}:{self.cache_version}:entry:{entry_id}"
        cached = await dict_cache.get(cache_key)
        if cached is not None:
            result = validate_lookup_result(cached)
            result["cached"] = True
            return result

        entry = await fetch_entry(entry_id, source=self.source)
        if entry is None:
            await dict_cache.set_miss(cache_key)
            raise ValueError(f"Entry not found: {entry_id}")

        result = self._build_entry_result(entry.display_headword, entry)
        await dict_cache.set(cache_key, result)
        return result

    def _build_entry_result(self, query: str, entry: EntryRow) -> dict[str, Any]:
        display_word = query if query.lower() != entry.display_headword.lower() else entry.display_headword
        base_word = entry.base_headword
        payload = DictionaryEntryPayload(
            id=entry.id,
            word=display_word,
            base_word=base_word,
            homograph_no=entry.homograph_no,
            phonetic=entry.phonetic,
            meanings=self._parse_meanings(entry),
            examples=self._parse_examples(entry),
            phrases=self._parse_phrases(entry),
            entry_kind=entry.entry_kind,  # type: ignore[arg-type]
            exchange=[], # TODO: extract exchange from db if available
            tags=entry.exam_tags or [],
        )
        return DictionaryEntryResult(
            query=query,
            provider=self.source,
            cached=False,
            entry=payload,
        ).model_dump()

    def _build_disambiguation_result(self, query: str, candidates: list[CandidateRow]) -> dict[str, Any]:
        ambiguity_kind = self._classify_ambiguity(query, candidates)
        selection_required = ambiguity_kind != "same_headword_senses"
        payload = [
            DictionaryCandidate(
                entry_id=item.entry_id,
                label=item.lookup_label or item.target_label,
                part_of_speech=item.target_pos,
                preview=item.preview_text,
                entry_kind=item.entry_kind,  # type: ignore[arg-type]
                match_kind=item.match_kind,
                lookup_type=item.lookup_type,  # type: ignore[arg-type]
                candidate_kind=self._classify_candidate_kind(query, item),  # type: ignore[arg-type]
            )
            for item in candidates
        ]
        return DictionaryDisambiguationResult(
            query=query,
            provider=self.source,
            cached=False,
            ambiguity_kind=ambiguity_kind,  # type: ignore[arg-type]
            selection_required=selection_required,
            candidates=payload,
        ).model_dump()

    def _classify_ambiguity(self, query: str, candidates: list[CandidateRow]) -> str:
        if not candidates:
            return "competing_entries"

        kinds = {self._classify_candidate_kind(query, item) for item in candidates}
        if "phrase" in kinds or "fragment" in kinds:
            return "phrase_vs_word"
        if "proper_noun" in kinds and any(kind in kinds for kind in ("word", "variant")):
            return "proper_vs_common"
        if "variant" in kinds:
            return "lemma_competing"

        labels = {
            self._normalize_candidate_label(item.lookup_label or item.target_label)
            for item in candidates
        }
        if len(labels) == 1:
            return "same_headword_senses"
        return "competing_entries"

    def _classify_candidate_kind(self, query: str, candidate: CandidateRow) -> str:
        if candidate.lookup_type == "phrase" or candidate.match_kind in ("phrase", "phrase_template"):
            return "phrase"
        if candidate.entry_kind == "fragment":
            return "fragment"
        if self._is_proper_candidate(query, candidate):
            return "proper_noun"
        normalized_query = query.strip().lower()
        if normalized_query and candidate.normalized_form.strip().lower() != normalized_query:
            return "variant"
        return "word"

    def _is_proper_candidate(self, query: str, candidate: CandidateRow) -> bool:
        pos = (candidate.target_pos or "").strip().lower()
        if pos in PROPER_NOUN_POS:
            return True
        label = (candidate.lookup_label or candidate.target_label).strip()
        normalized_query = query.strip()
        return normalized_query == normalized_query.lower() and label != label.lower()

    def _normalize_candidate_label(self, label: str) -> str:
        return label.strip().lower()

    def _parse_meanings(self, entry: EntryRow) -> list[DictionaryMeaning]:
        return [
            DictionaryMeaning.model_validate(item)
            for item in entry.meanings_json
            if item
        ]

    def _parse_examples(self, entry: EntryRow) -> list[DictionaryExample]:
        return [
            DictionaryExample.model_validate(item)
            for item in entry.examples_json
            if item
        ]

    def _parse_phrases(self, entry: EntryRow) -> list[DictionaryPhrase]:
        return [
            DictionaryPhrase.model_validate(item)
            for item in entry.phrases_json
            if item
        ]
