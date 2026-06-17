"""Draft validators for V3 parallel agent outputs."""

from __future__ import annotations

from app.schemas.internal.analysis import (
    PreparedSentence,
    is_likely_basic_english_word,
    is_single_token,
)
from app.schemas.internal.drafts import (
    DraftContextGloss,
    DraftPhraseGloss,
    DraftVocabHighlight,
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
)
from app.services.analysis.postprocess.anchor_resolution import (
    resolve_explicit_anchor_parts,
    resolve_grammar_anchor_to_source,
    resolve_vocabulary_anchor_binding,
)
from app.services.analysis.postprocess.normalize import is_substring


class DraftValidationError(Exception):
    """校验失败异常。"""

    def __init__(self, message: str, draft_type: str, sentence_id: str | None = None):
        self.message = message
        self.draft_type = draft_type
        self.sentence_id = sentence_id
        super().__init__(self.message)


def validate_vocab_highlight_business_rules(item: DraftVocabHighlight) -> list[str]:
    warnings: list[str] = []
    if " " in item.text:
        warnings.append("vocab_highlight: text must be a single word without spaces")
    return warnings


def validate_phrase_gloss_business_rules(item: DraftPhraseGloss) -> list[str]:
    warnings: list[str] = []
    phrase_text = getattr(item, "label", None) or getattr(item, "text", "")
    if is_single_token(phrase_text) and item.phrase_type not in {"proper_noun", "compound"}:
        warnings.append(
            "phrase_gloss: single-token label only allowed for proper_noun or compound"
        )
    if item.phrase_type == "proper_noun" and is_likely_basic_english_word(phrase_text):
        warnings.append("phrase_gloss: proper_noun must not be a basic English word")
    return warnings


def validate_context_gloss_business_rules(item: DraftContextGloss) -> list[str]:
    warnings: list[str] = []
    if not item.gloss.strip():
        warnings.append("context_gloss: gloss must not be empty")
    if not item.reason.strip():
        warnings.append("context_gloss: reason must not be empty")
    return warnings


def _vocabulary_anchor_matches(
    text: str, sentence: PreparedSentence, occurrence: int | None,
) -> bool:
    if is_substring(text, sentence.text):
        return True
    return resolve_vocabulary_anchor_binding(sentence, text, occurrence) is not None


def _grammar_anchor_matches(text: str, sentence: PreparedSentence, occurrence: int | None) -> bool:
    return resolve_grammar_anchor_to_source(sentence, text, occurrence) is not None


def _phrase_gloss_anchor_quotes_match(
    item: DraftPhraseGloss,
    sentence: PreparedSentence,
) -> list[str]:
    if not item.anchor_quotes:
        return []

    warnings: list[str] = []
    previous_end: int | None = None
    for q in item.anchor_quotes:
        parts = [{"anchor_text": q.text, "role": q.role}]
        resolved_parts = resolve_explicit_anchor_parts(sentence, parts)
        if resolved_parts is None:
            warnings.append(
                f"phrase_gloss: anchor quote text '{q.text}' "
                f"not found in sentence {item.sentence_id}"
            )
            continue
        resolved_part = resolved_parts[0]
        if previous_end is not None and resolved_part.span.start < previous_end:
            warnings.append(
                f"phrase_gloss: anchor quotes out of source order in sentence {item.sentence_id}"
            )
            break
        previous_end = resolved_part.span.end

    return warnings


def validate_vocabulary_draft(
    draft: VocabularyDraft,
    sentences: list[PreparedSentence],
) -> list[str]:
    warnings: list[str] = []
    sentence_map = {s.sentence_id: s for s in sentences}

    for v in draft.vocab_highlights:
        warnings.extend(validate_vocab_highlight_business_rules(v))
        if v.sentence_id not in sentence_map:
            warnings.append(f"vocab_highlight: sentence_id {v.sentence_id} not found")
            continue
        sentence = sentence_map[v.sentence_id]
        if not _vocabulary_anchor_matches(v.text, sentence, None):
            warnings.append(
                f"vocab_highlight: text '{v.text}' not found in sentence {v.sentence_id}"
            )

    for p in draft.phrase_glosses:
        warnings.extend(validate_phrase_gloss_business_rules(p))
        for q in p.anchor_quotes:
            if "..." in q.text:
                warnings.append(
                    f"phrase_gloss: anchor quote text '{q.text}' "
                    f"contains ellipsis in sentence {p.sentence_id}"
                )
        if p.sentence_id not in sentence_map:
            warnings.append(f"phrase_gloss: sentence_id {p.sentence_id} not found")
            continue
        sentence = sentence_map[p.sentence_id]
        if p.anchor_quotes:
            warnings.extend(_phrase_gloss_anchor_quotes_match(p, sentence))
        elif not _vocabulary_anchor_matches(p.label, sentence, None):
            warnings.append(
                f"phrase_gloss: label '{p.label}' not found in sentence {p.sentence_id}"
            )

    for c in draft.context_glosses:
        warnings.extend(validate_context_gloss_business_rules(c))
        for q in c.anchor_quotes:
            if "..." in q.text:
                warnings.append(
                    f"context_gloss: anchor quote text '{q.text}' "
                    f"contains ellipsis in sentence {c.sentence_id}"
                )
        if c.sentence_id not in sentence_map:
            warnings.append(f"context_gloss: sentence_id {c.sentence_id} not found")
            continue
        sentence = sentence_map[c.sentence_id]
        for q in c.anchor_quotes:
            if not _vocabulary_anchor_matches(q.text, sentence, None):
                warnings.append(
                    f"context_gloss: anchor quote text '{q.text}' "
                    f"not found in sentence {c.sentence_id}"
                )

    return warnings


def validate_grammar_draft(
    draft: GrammarDraft,
    sentences: list[PreparedSentence],
) -> list[str]:
    warnings: list[str] = []
    sentence_map = {s.sentence_id: s for s in sentences}

    for g in draft.grammar_notes:
        for q in g.anchor_quotes:
            if "..." in q.text:
                warnings.append(
                    f"grammar_note: anchor quote text '{q.text}' "
                    f"contains ellipsis in sentence {g.sentence_id}"
                )
        if g.sentence_id not in sentence_map:
            warnings.append(f"grammar_note: sentence_id {g.sentence_id} not found")
            continue
        sentence = sentence_map[g.sentence_id]
        for q in g.anchor_quotes:
            if not _grammar_anchor_matches(q.text, sentence, None):
                warnings.append(
                    f"grammar_note: anchor quote text '{q.text}' "
                    f"not found in sentence {g.sentence_id}"
                )

    for s in draft.sentence_analyses:
        if s.sentence_id not in sentence_map:
            warnings.append(f"sentence_analysis: sentence_id {s.sentence_id} not found")
            continue
        sent_text = sentence_map[s.sentence_id].text
        if not s.chunks:
            warnings.append(
                f"sentence_analysis: chunks missing for sentence {s.sentence_id}; "
                "SentenceAnalysis should usually include 2-6 chunks"
            )
        if s.chunks:
            for chunk in s.chunks:
                if not is_substring(chunk.text, sent_text):
                    warnings.append(
                        f"sentence_analysis: chunk text '{chunk.text}' "
                        f"not found in sentence {s.sentence_id}"
                    )

    return warnings


def validate_translation_draft(
    draft: TranslationDraft,
    sentences: list[PreparedSentence],
) -> list[str]:
    warnings: list[str] = []
    sentence_ids = {s.sentence_id for s in sentences}
    translated_ids = {t.sentence_id for t in draft.sentence_translations}

    missing = sentence_ids - translated_ids
    if missing:
        warnings.append(f"translation missing for sentence_ids: {sorted(missing)}")

    for t in draft.sentence_translations:
        if t.sentence_id not in sentence_ids:
            warnings.append(f"translation: sentence_id {t.sentence_id} not found in sentences")
        if not t.translation_zh.strip():
            warnings.append(f"translation: empty translation for sentence_id {t.sentence_id}")

    return warnings


def validate_all_drafts(
    vocabulary_draft: VocabularyDraft,
    grammar_draft: GrammarDraft,
    translation_draft: TranslationDraft,
    sentences: list[PreparedSentence],
) -> list[str]:
    all_warnings: list[str] = []
    all_warnings.extend(validate_vocabulary_draft(vocabulary_draft, sentences))
    all_warnings.extend(validate_grammar_draft(grammar_draft, sentences))
    all_warnings.extend(validate_translation_draft(translation_draft, sentences))
    return all_warnings
