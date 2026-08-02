"""Grammar RAG query construction and candidate sentence selection.

Provides:
- ``build_query_text``: builds a canonical colon-format query string for
  embedding retrieval, using open-vocabulary grammar tags derived from
  English sentence structural signals.
- ``extract_grammar_tags_from_sentence``: extracts grammar tags from English
  sentence structure (used by both build_query_text and grammar_rag_service
  for query-side tag generation).
- ``extract_signals`` / ``SentenceSignals`` / ``select_candidate_sentences``:
  lightweight structural signal extraction used as a cheap pre-filter for
  candidate sentence selection (not part of the example schema).

Query output fields: variant, output_type, grammar_tags, source_sentence.
Grammar tags are extracted from English sentence structural signals and
normalized through the open-vocabulary normalizer, falling back to
``["unclassified"]`` when no signals are detected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.prompting.rag.grammar_tag_normalization import (
    normalize_grammar_tags,
)

_LONG_SENTENCE_THRESHOLD = 20
_MANY_COMMA_THRESHOLD = 2


@dataclass
class SentenceSignals:
    """Structural signals extracted from a single sentence."""

    sentence: str = ""
    word_count: int = 0
    comma_count: int = 0
    long_sentence: bool = False
    has_that: bool = False
    has_which: bool = False
    has_who: bool = False
    has_whose: bool = False
    has_where: bool = False
    has_when: bool = False
    leading_vbn: bool = False
    leading_ving: bool = False
    has_inversion_trigger: bool = False
    has_comma_insertion: bool = False
    nested_structure: bool = False

    def to_signal_list(self) -> list[str]:
        """Convert to the signal list format used in seed JSONL and query_text."""
        signals: list[str] = []
        if self.long_sentence:
            signals.append("long_sentence")
        if self.has_that:
            signals.append("has_that_clause")
        if self.has_which or self.has_who or self.has_whose:
            signals.append("has_wh_clause")
        if self.leading_vbn:
            signals.append("leading_vbn")
        if self.leading_ving:
            signals.append("leading_ving")
        if self.has_inversion_trigger:
            signals.append("has_inversion")
        if self.has_comma_insertion:
            signals.append("has_comma_insertion")
        if self.nested_structure:
            signals.append("nested_structure")
        if not signals:
            signals.append("local_structure")
        return signals


def extract_signals(sentence: str) -> SentenceSignals:
    """Extract lightweight structural signals from a sentence.

    Args:
        sentence: A single English sentence.

    Returns:
        SentenceSignals with all detected signals.
    """
    words = sentence.split()
    word_count = len(words)
    comma_count = sentence.count(",")

    has_that = bool(re.search(r"\bthat\b", sentence, re.IGNORECASE))
    has_which = bool(re.search(r"\bwhich\b", sentence, re.IGNORECASE))
    has_who = bool(re.search(r"\bwho\b", sentence, re.IGNORECASE))
    has_whose = bool(re.search(r"\bwhose\b", sentence, re.IGNORECASE))
    has_where = bool(re.search(r"\bwhere\b", sentence, re.IGNORECASE))
    has_when = bool(re.search(r"\bwhen\b", sentence, re.IGNORECASE))

    leading_vbn = bool(re.match(r"^[A-Za-z]+ed\b", sentence))
    leading_ving = bool(re.match(r"^[A-Za-z]+ing\b", sentence))

    has_inversion_trigger = bool(
        re.match(
            r"^(?:Never|Rarely|Seldom|Not only|Had|Were|Should|Could|Can|May|Might|No sooner)\b",
            sentence,
            re.IGNORECASE,
        )
    )

    has_comma_insertion = (
        comma_count >= _MANY_COMMA_THRESHOLD
        or bool(re.search(r",\s*(?:which|who|whose|whom)\b", sentence))
        or bool(re.match(r"^[A-Za-z]+ed\b.*,\s*\w+", sentence))
        or bool(re.match(r"^[A-Za-z]+ing\b.*,\s*\w+", sentence))
    )

    clause_count = sum([
        has_that,
        has_which,
        has_who,
        bool(re.search(r"\balthough\b|\bthough\b|\bwhile\b", sentence, re.IGNORECASE)),
    ])
    nested_structure = clause_count >= 2 or (comma_count >= 2 and clause_count >= 1)

    return SentenceSignals(
        sentence=sentence,
        word_count=word_count,
        comma_count=comma_count,
        long_sentence=word_count > _LONG_SENTENCE_THRESHOLD,
        has_that=has_that,
        has_which=has_which,
        has_who=has_who,
        has_whose=has_whose,
        has_where=has_where,
        has_when=has_when,
        leading_vbn=leading_vbn,
        leading_ving=leading_ving,
        has_inversion_trigger=has_inversion_trigger,
        has_comma_insertion=has_comma_insertion,
        nested_structure=nested_structure,
    )


def extract_grammar_tags_from_sentence(sentence: str) -> list[str]:
    """Extract grammar tags from English sentence structural signals.

    Maps structural signals detected by ``extract_signals`` to open-vocabulary
    grammar tags, then normalizes them through ``normalize_grammar_tags``.
    This is the query-side counterpart to the example-side
    ``_rule_extract_grammar_tags`` which works on Chinese labels.

    Returns sorted, normalized tag list. Falls back to ``["unclassified"]``
    if no structural signals are detected.
    """
    signals = extract_signals(sentence)
    raw_tags: list[str] = []

    if signals.has_that:
        raw_tags.append("relative_clause")
    if signals.has_which or signals.has_who or signals.has_whose:
        raw_tags.append("relative_clause")
    if signals.has_inversion_trigger:
        raw_tags.append("inversion")
    if signals.leading_vbn:
        raw_tags.append("past_participle_adverbial")
    if signals.leading_ving:
        raw_tags.append("present_participle_adverbial")
    if signals.has_comma_insertion:
        raw_tags.append("main_clause_interruption")
    if signals.nested_structure:
        raw_tags.append("nested_clause")

    # Normalize through open-vocabulary normalizer (dedup, alias merge, etc.)
    tags = normalize_grammar_tags(raw_tags)
    return tags or ["unclassified"]


def select_candidate_sentences(
    sentences: list[dict],
    output_type: str = "grammar_note",
    budget: int = 4,
) -> list[dict]:
    """Select candidate sentences for RAG query based on structural signals.

    This is a cheap pre-filter. It prioritizes sentences with more
    structural signals (i.e., sentences more likely to benefit from
    grammar annotation).

    Args:
        sentences: List of {"sentence_id": str, "text": str}.
        output_type: "grammar_note" or "sentence_analysis".
        budget: Maximum number of sentences to select.

    Returns:
        Subset of input sentences, sorted by signal richness (descending).
    """
    if not sentences:
        return []

    scored: list[tuple[int, dict, list[str]]] = []
    for s in sentences:
        text = s.get("text", "")
        signals = extract_signals(text)
        signal_list = signals.to_signal_list()
        score = len(signal_list)
        if output_type == "grammar_note" and signals.leading_vbn:
            score += 1
        if output_type == "grammar_note" and signals.has_inversion_trigger:
            score += 1
        if output_type == "sentence_analysis" and signals.long_sentence:
            score += 1
        if output_type == "sentence_analysis" and signals.nested_structure:
            score += 1
        scored.append((score, s, signal_list))

    scored.sort(key=lambda x: -x[0])
    return [item[1] for item in scored[:budget]]


def build_query_text(
    sentence: str,
    variant: str,
    output_type: str,
) -> str:
    """Build a query_text for embedding in canonical colon format.

    Output fields: variant, output_type, grammar_tags, source_sentence.
    Grammar tags are extracted from English sentence structural signals via
    ``extract_grammar_tags_from_sentence`` and normalized through
    ``normalize_grammar_tags``.  Falls back to ``["unclassified"]``
    when no structural signals are detected.

    Args:
        sentence: The English sentence to query.
        variant: Reading variant (gaokao, cet, etc.).
        output_type: "grammar_note" or "sentence_analysis".

    Returns:
        A formatted query_text string in canonical colon format.
    """
    grammar_tags = extract_grammar_tags_from_sentence(sentence)

    lines = [
        f"variant: {variant}",
        f"output_type: {output_type}",
        f"grammar_tags: {', '.join(grammar_tags)}",
        f"source_sentence: {sentence}",
    ]

    return "\n".join(lines)
