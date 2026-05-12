"""Normalize and ground for V3 workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.schemas.internal.analysis import (
    Annotation,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    PreparedSentence,
    SentenceAnalysis,
    SentenceTranslation,
    VocabHighlight,
)
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.schemas.internal.execution_plan import GoalPolicy
from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult
from app.services.analysis.postprocess.draft_validators import (
    validate_context_gloss_business_rules,
    validate_phrase_gloss_business_rules,
    validate_vocab_highlight_business_rules,
)

PRIORITY_RANK: dict[str, int] = {
    "context_gloss": 3,
    "phrase_gloss": 2,
    "vocab_highlight": 1,
    "grammar_note": 10,
    "sentence_analysis": 10,
}

LOW_VALUE_WORDS: set[str] = {
    "this", "that", "these", "those", "the", "a", "an",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must",
    "shall", "can", "need", "dare", "ought", "used",
    "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "under", "again",
    "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very",
    "just", "but", "and", "or", "if", "because", "until",
    "while", "review", "series", "site", "item", "case",
    "page", "form", "part", "point", "way", "time",
    "year", "week", "day", "hour", "minute", "second",
    "number", "percent", "type", "kind", "sort", "class",
}

@dataclass
class NormalizationContext:
    sentences: list[PreparedSentence]
    sentence_map: dict[str, PreparedSentence]
    policy: GoalPolicy


def _make_anchor_key(annotation_type: str, sentence_id: str, anchor_text: str) -> str:
    canonical = json.dumps(
        {"type": annotation_type, "sentence_id": sentence_id, "text": anchor_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{annotation_type}_{sentence_id}_{digest}"


def _is_substring(text: str, sentence_text: str) -> bool:
    return text in sentence_text


def _log_drop(
    source_agent: Literal["vocabulary", "grammar", "translation"],
    annotation_type: str,
    sentence_id: str,
    anchor_text: str,
    drop_reason: str,
    drop_stage: Literal["grounding", "deduplication", "conflict_resolution", "density_control", "pruning"],
    drop_log: list[DropLogEntry],
) -> None:
    drop_log.append(
        DropLogEntry(
            source_agent=source_agent,
            annotation_type=annotation_type,
            sentence_id=sentence_id,
            anchor_text=anchor_text,
            drop_reason=drop_reason,
            drop_stage=drop_stage,
            dropped_at=datetime.now(),
        )
    )


def _grounding_check(
    annotation_type: str,
    text: str,
    sentence_id: str,
    sentence_map: dict[str, PreparedSentence],
    source_agent: Literal["vocabulary", "grammar", "translation"],
    drop_log: list[DropLogEntry],
) -> bool:
    sentence_obj = sentence_map.get(sentence_id)
    if sentence_obj is None:
        _log_drop(
            source_agent, annotation_type, sentence_id, text,
            "sentence_id_not_found", "grounding", drop_log
        )
        return False
    if not _is_substring(text, sentence_obj.text):
        _log_drop(
            source_agent, annotation_type, sentence_id, text,
            "anchor_not_substring", "grounding", drop_log
        )
        return False
    return True


def _check_low_value_word(text: str) -> bool:
    return text.lower() in LOW_VALUE_WORDS


def _passes_business_rules(
    annotation: VocabHighlight | PhraseGloss | ContextGloss,
    drop_log: list[DropLogEntry],
) -> bool:
    if annotation.type == "vocab_highlight":
        reasons = validate_vocab_highlight_business_rules(annotation)
    elif annotation.type == "phrase_gloss":
        reasons = validate_phrase_gloss_business_rules(annotation)
    else:
        reasons = validate_context_gloss_business_rules(annotation)

    if not reasons:
        return True

    for reason in reasons:
        _log_drop(
            "vocabulary",
            annotation.type,
            annotation.sentence_id,
            annotation.text,
            reason.replace(":", "_").replace(" ", "_"),
            "pruning",
            drop_log,
        )
    return False


def _normalize_vocab_highlights(
    draft: VocabularyDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[VocabHighlight]:
    result: list[VocabHighlight] = []
    seen_keys: set[str] = set()

    for item in draft.vocab_highlights:
        if not _grounding_check(item.type, item.text, item.sentence_id, ctx.sentence_map, "vocabulary", drop_log):
            continue
        if not _passes_business_rules(item, drop_log):
            continue
        if _check_low_value_word(item.text):
            _log_drop("vocabulary", item.type, item.sentence_id, item.text, "low_value_word", "pruning", drop_log)
            continue
        key = _make_anchor_key(item.type, item.sentence_id, item.text)
        if key in seen_keys:
            _log_drop("vocabulary", item.type, item.sentence_id, item.text, "duplicate", "deduplication", drop_log)
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_phrase_glosses(
    draft: VocabularyDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[PhraseGloss]:
    result: list[PhraseGloss] = []
    seen_keys: set[str] = set()

    for item in draft.phrase_glosses:
        if not _grounding_check(item.type, item.text, item.sentence_id, ctx.sentence_map, "vocabulary", drop_log):
            continue
        if not _passes_business_rules(item, drop_log):
            continue
        key = _make_anchor_key(item.type, item.sentence_id, item.text)
        if key in seen_keys:
            _log_drop("vocabulary", item.type, item.sentence_id, item.text, "duplicate", "deduplication", drop_log)
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_context_glosses(
    draft: VocabularyDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[ContextGloss]:
    result: list[ContextGloss] = []
    seen_keys: set[str] = set()

    for item in draft.context_glosses:
        if not _grounding_check(item.type, item.text, item.sentence_id, ctx.sentence_map, "vocabulary", drop_log):
            continue
        if not _passes_business_rules(item, drop_log):
            continue
        key = _make_anchor_key(item.type, item.sentence_id, item.text)
        if key in seen_keys:
            _log_drop("vocabulary", item.type, item.sentence_id, item.text, "duplicate", "deduplication", drop_log)
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_grammar_notes(
    draft: GrammarDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[GrammarNote]:
    result: list[GrammarNote] = []
    seen_keys: set[str] = set()

    for item in draft.grammar_notes:
        if any(
            not _grounding_check(item.type, span.text, item.sentence_id, ctx.sentence_map, "grammar", drop_log)
            for span in item.spans
        ):
            continue
        primary_text = item.spans[0].text if item.spans else ""
        key = _make_anchor_key(item.type, item.sentence_id, primary_text)
        if key in seen_keys:
            _log_drop("grammar", item.type, item.sentence_id, primary_text, "duplicate", "deduplication", drop_log)
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_sentence_analyses(
    draft: GrammarDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[SentenceAnalysis]:
    result: list[SentenceAnalysis] = []
    seen_keys: set[str] = set()

    for item in draft.sentence_analyses:
        if item.chunks and any(
            not _grounding_check(item.type, chunk.text, item.sentence_id, ctx.sentence_map, "grammar", drop_log)
            for chunk in item.chunks
        ):
            continue
        key = _make_anchor_key(item.type, item.sentence_id, item.label)
        if key in seen_keys:
            _log_drop("grammar", item.type, item.sentence_id, item.label, "duplicate", "deduplication", drop_log)
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _merge_and_resolve_conflicts(
    vocab_result: list[VocabHighlight],
    phrase_result: list[PhraseGloss],
    context_result: list[ContextGloss],
    grammar_result: list[GrammarNote],
    sentence_analysis_result: list[SentenceAnalysis],
    drop_log: list[DropLogEntry],
) -> list[Annotation]:
    merged: list[Annotation] = []
    merged.extend(grammar_result)
    merged.extend(sentence_analysis_result)

    keyed_candidates: dict[tuple[str, str], list[Annotation]] = {}
    for item in [*vocab_result, *phrase_result, *context_result]:
        key = (item.sentence_id, item.text)
        keyed_candidates.setdefault(key, []).append(item)

    for (sentence_id, text), candidates in keyed_candidates.items():
        candidates.sort(key=lambda item: PRIORITY_RANK.get(item.type, 0), reverse=True)
        winner = candidates[0]
        merged.append(winner)
        for loser in candidates[1:]:
            _log_drop(
                "vocabulary",
                loser.type,
                sentence_id,
                text,
                "conflict_resolution",
                "conflict_resolution",
                drop_log,
            )

    return merged


def _annotation_identity(annotation: Annotation) -> str:
    anchor_text = getattr(annotation, "text", None)
    if anchor_text is not None:
        return f"{annotation.sentence_id}:{annotation.type}:{anchor_text}"
    if annotation.type == "grammar_note":
        return f"{annotation.sentence_id}:{annotation.type}:{annotation.spans[0].text if annotation.spans else annotation.label}"
    return f"{annotation.sentence_id}:{annotation.type}:{annotation.label}"


def _density_control(
    annotations: list[Annotation],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[Annotation]:
    max_per_sentence = ctx.policy.annotation_density
    grouped: dict[str, list[Annotation]] = {}
    for annotation in annotations:
        grouped.setdefault(annotation.sentence_id, []).append(annotation)

    survivors: set[str] = set()
    for sentence_id, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: (PRIORITY_RANK.get(item.type, 0), _annotation_identity(item)),
            reverse=True,
        )
        for item in ranked[:max_per_sentence]:
            survivors.add(_annotation_identity(item))
        for item in ranked[max_per_sentence:]:
            _log_drop(
                "vocabulary" if item.type in {"vocab_highlight", "phrase_gloss", "context_gloss"} else "grammar",
                item.type,
                sentence_id,
                getattr(item, "text", getattr(item, "label", "")),
                f"density_exceeded_max_{max_per_sentence}",
                "density_control",
                drop_log,
            )

    return [annotation for annotation in annotations if _annotation_identity(annotation) in survivors]


def _normalize_translations(
    draft: TranslationDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[SentenceTranslation]:
    result: list[SentenceTranslation] = []
    seen_ids: set[str] = set()

    for item in draft.sentence_translations:
        if item.sentence_id not in ctx.sentence_map:
            _log_drop("translation", "sentence_translation", item.sentence_id, "", "sentence_id_not_found", "grounding", drop_log)
            continue
        if not item.translation_zh.strip():
            _log_drop("translation", "sentence_translation", item.sentence_id, "", "empty_translation", "pruning", drop_log)
            continue
        if item.sentence_id in seen_ids:
            _log_drop("translation", "sentence_translation", item.sentence_id, "", "duplicate", "deduplication", drop_log)
            continue
        seen_ids.add(item.sentence_id)
        result.append(item)

    return result


def normalize_and_ground(
    vocabulary_draft: VocabularyDraft,
    grammar_draft: GrammarDraft,
    translation_draft: TranslationDraft,
    sentences: list[PreparedSentence],
    policy: GoalPolicy,
) -> NormalizedAnnotationResult:
    ctx = NormalizationContext(
        sentences=sentences,
        sentence_map={sentence.sentence_id: sentence for sentence in sentences},
        policy=policy,
    )
    drop_log: list[DropLogEntry] = []

    vocab_result = _normalize_vocab_highlights(vocabulary_draft, ctx, drop_log)
    phrase_result = _normalize_phrase_glosses(vocabulary_draft, ctx, drop_log)
    context_result = _normalize_context_glosses(vocabulary_draft, ctx, drop_log)
    grammar_result = _normalize_grammar_notes(grammar_draft, ctx, drop_log)
    sentence_analysis_result = _normalize_sentence_analyses(grammar_draft, ctx, drop_log)
    translation_result = _normalize_translations(translation_draft, ctx, drop_log)

    merged_annotations = _merge_and_resolve_conflicts(
        vocab_result,
        phrase_result,
        context_result,
        grammar_result,
        sentence_analysis_result,
        drop_log,
    )

    final_annotations = _density_control(merged_annotations, ctx, drop_log)

    return NormalizedAnnotationResult(
        annotations=final_annotations,
        sentence_translations=translation_result,
        drop_log=drop_log,
    )
