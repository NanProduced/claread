"""Lightweight structural signal extraction for grammar RAG query construction.

Per grammar-rag-design.md §10.3, these rules serve as a cheap pre-filter
for candidate sentence selection. They do NOT need to be perfectly accurate —
the real ranking happens at the rerank stage.

Signals extracted:
- Sentence length (word count)
- Comma count
- Presence of that/which/who/whose/where/when
- Leading V-ed / V-ing (participle openers)
- Inversion triggers (Never/Rarely/Not only/Had at sentence start)
- Insertion clause patterns (comma-separated mid-sentence interruption)
- Nested clause indicators
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    teaching_goal: str = "",
) -> str:
    """Build a query_text for embedding, matching the retrieval_text template.

    Per grammar-rag-design.md §9.1, the query side constructs a lightweight
    text in the same representation space as the seed retrieval_text.

    Args:
        sentence: The English sentence to query.
        variant: Reading variant (gaokao, cet, etc.).
        output_type: "grammar_note" or "sentence_analysis".
        teaching_goal: Optional teaching goal string.

    Returns:
        A formatted query_text string.
    """
    signals = extract_signals(sentence)
    signal_list = signals.to_signal_list()

    lines = [
        f"output_type={output_type}",
        f"variant={variant}",
        f"possible_signals={', '.join(signal_list)}",
    ]
    if teaching_goal:
        lines.append(f"teaching_goal={teaching_goal}")
    lines.append(f"sentence={sentence}")

    return "\n".join(lines)
