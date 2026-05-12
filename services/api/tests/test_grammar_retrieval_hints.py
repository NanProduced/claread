"""Tests for grammar_retrieval_hints module."""

from __future__ import annotations

from app.services.analysis.prompting.rag.grammar_retrieval_hints import (
    SentenceSignals,
    build_query_text,
    extract_signals,
    select_candidate_sentences,
)


class TestExtractSignals:
    def test_simple_sentence(self):
        s = extract_signals("He gave up smoking last year.")
        assert s.word_count == 6
        assert not s.long_sentence
        assert not s.has_that
        assert not s.has_which
        assert not s.leading_vbn
        assert not s.leading_ving
        assert not s.has_inversion_trigger
        assert not s.has_comma_insertion
        assert not s.nested_structure

    def test_long_sentence_with_that_and_which(self):
        s = extract_signals(
            "The study suggests that the approach, which was initially designed "
            "for urban areas, has failed to address the needs of rural communities."
        )
        assert s.long_sentence
        assert s.has_that
        assert s.has_which
        assert s.has_comma_insertion
        assert s.nested_structure

    def test_leading_vbn(self):
        s = extract_signals("Inspired by the speech, the students decided to start their own project.")
        assert s.leading_vbn
        assert not s.leading_ving
        assert s.has_comma_insertion

    def test_leading_ving(self):
        s = extract_signals("Walking through the park, she noticed a strange bird.")
        assert s.leading_ving
        assert not s.leading_vbn

    def test_inversion_trigger(self):
        s = extract_signals("Never had she felt so alone.")
        assert s.has_inversion_trigger

        s2 = extract_signals("Not only did the policy raise costs, but it also reduced supply.")
        assert s2.has_inversion_trigger

        s3 = extract_signals("Had the company invested earlier, it would have dominated the market.")
        assert s3.has_inversion_trigger

    def test_comma_insertion_with_which(self):
        s = extract_signals("The study, which was conducted by researchers, found that exercise helps.")
        assert s.has_comma_insertion
        assert s.has_which
        assert s.has_that

    def test_who_whose_where(self):
        s = extract_signals("The man who lives whose house is where the road ends is old.")
        assert s.has_who
        assert s.has_whose
        assert s.has_where

    def test_to_signal_list_local_structure_default(self):
        s = extract_signals("She runs fast.")
        signals = s.to_signal_list()
        assert signals == ["local_structure"]

    def test_to_signal_list_multiple(self):
        s = extract_signals(
            "The study suggests that the approach, which was initially designed "
            "for urban areas, has failed to address the needs of rural communities."
        )
        signals = s.to_signal_list()
        assert "long_sentence" in signals
        assert "has_that_clause" in signals
        assert "has_wh_clause" in signals
        assert "has_comma_insertion" in signals
        assert "nested_structure" in signals


class TestSelectCandidateSentences:
    def test_empty_input(self):
        result = select_candidate_sentences([])
        assert result == []

    def test_selects_rich_sentences(self):
        sentences = [
            {"sentence_id": "1", "text": "She runs fast."},
            {"sentence_id": "2", "text": "The study, which was conducted by researchers, found that exercise helps."},
            {"sentence_id": "3", "text": "He gave up smoking."},
        ]
        result = select_candidate_sentences(sentences, budget=2)
        assert len(result) == 2
        assert result[0]["sentence_id"] == "2"

    def test_respects_budget(self):
        sentences = [
            {"sentence_id": str(i), "text": f"Sentence number {i} that has a clause."}
            for i in range(10)
        ]
        result = select_candidate_sentences(sentences, budget=3)
        assert len(result) == 3

    def test_grammar_note_boosts_vbn(self):
        simple = {"sentence_id": "1", "text": "She runs fast."}
        vbn = {"sentence_id": "2", "text": "Inspired by the speech, the students decided to act."}
        result = select_candidate_sentences([simple, vbn], output_type="grammar_note", budget=1)
        assert result[0]["sentence_id"] == "2"

    def test_sentence_analysis_boosts_long(self):
        short = {"sentence_id": "1", "text": "She runs fast."}
        long_s = {
            "sentence_id": "2",
            "text": "The research conducted by scientists from different countries shows "
            "that climate change has affected the lives of millions of people, "
            "which is a serious concern.",
        }
        result = select_candidate_sentences(
            [short, long_s], output_type="sentence_analysis", budget=1
        )
        assert result[0]["sentence_id"] == "2"


class TestBuildQueryText:
    def test_grammar_note_query(self):
        qt = build_query_text(
            sentence="Inspired by the speech, the students decided to start their own project.",
            variant="gaokao",
            output_type="grammar_note",
            teaching_goal="explicit_exam",
        )
        assert "output_type=grammar_note" in qt
        assert "variant=gaokao" in qt
        assert "teaching_goal=explicit_exam" in qt
        assert "sentence=Inspired by the speech" in qt
        assert "possible_signals=" in qt

    def test_sentence_analysis_query(self):
        qt = build_query_text(
            sentence="The study suggests that the approach has failed.",
            variant="kaoyan",
            output_type="sentence_analysis",
        )
        assert "output_type=sentence_analysis" in qt
        assert "variant=kaoyan" in qt
        assert "sentence=The study suggests" in qt
        assert "teaching_goal=" not in qt
