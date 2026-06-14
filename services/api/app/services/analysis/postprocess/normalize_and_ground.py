"""Normalize and ground for V3 workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import (
    Annotation,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    PreparedSentence,
    SentenceAnalysis,
    SentenceTranslation,
    SpanRef,
    VocabHighlight,
)
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftAnnotation,
    DraftContextGloss,
    DraftPhraseGloss,
    DraftVocabHighlight,
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
    draft_to_annotation,
)
from app.schemas.internal.execution_plan import GoalPolicy
from app.schemas.internal.normalized import (
    DropLogEntry,
    NormalizedAnnotation,
    NormalizedAnnotationResult,
)
from app.services.analysis.postprocess.anchor_resolution import (
    resolve_explicit_anchor_parts,
    resolve_grammar_anchor_to_source,
    resolve_vocabulary_anchor_binding,
    resolve_vocabulary_anchor_spans,
)
from app.services.analysis.postprocess.draft_to_normalized import (
    draft_to_normalized_annotation,
)
from app.services.analysis.postprocess.draft_validators import (
    validate_context_gloss_business_rules,
    validate_phrase_gloss_business_rules,
    validate_vocab_highlight_business_rules,
)
from app.services.analysis.postprocess.normalize import is_substring
from app.services.analysis.postprocess.normalized_postprocess import (
    PRIORITY_RANK,
    build_canonical_stats,
    log_drop,
    postprocess_normalized_annotations,
)

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


def _make_anchor_key(annotation_type: str, sentence_id: str, anchor_payload: object) -> str:
    canonical = json.dumps(
        {"type": annotation_type, "sentence_id": sentence_id, "anchor": anchor_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{annotation_type}_{sentence_id}_{digest}"


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
        log_drop(
            source_agent, annotation_type, sentence_id, text,
            "sentence_id_not_found", "grounding", drop_log
        )
        return False
    if not is_substring(text, sentence_obj.text):
        log_drop(
            source_agent, annotation_type, sentence_id, text,
            "anchor_not_substring", "grounding", drop_log
        )
        return False
    return True


def _canonicalize_grammar_span(
    span: SpanRef,
    item: GrammarNote,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
) -> SpanRef | None:
    sentence_obj = sentence_map.get(item.sentence_id)
    if sentence_obj is None:
        log_drop(
            "grammar",
            item.type,
            item.sentence_id,
            span.text,
            "sentence_id_not_found",
            "grounding",
            drop_log,
        )
        return None

    resolved_anchor = resolve_grammar_anchor_to_source(
        sentence_obj,
        span.text,
        span.occurrence,
    )
    if resolved_anchor is not None:
        return span.model_copy(update={"text": resolved_anchor.text})

    drop_reason = (
        "schematic_anchor_not_groundable"
        if "..." in span.text and span.text not in sentence_obj.text
        else "anchor_not_substring"
    )
    log_drop(
        "grammar",
        item.type,
        item.sentence_id,
        span.text,
        drop_reason,
        "grounding",
        drop_log,
    )
    return None


def _canonicalize_vocabulary_anchor(
    annotation: VocabHighlight | PhraseGloss | ContextGloss,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
) -> VocabHighlight | PhraseGloss | ContextGloss | None:
    """Ground vocabulary anchors while preserving schematic teaching notation.

    Continuous anchors are canonicalized back to source text when recoverable.
    Schematic anchors such as ``refer to ... as`` stay in their teaching form and
    are later projected to ``multi_text`` parts by the render projection layer.
    """
    sentence_obj = sentence_map.get(annotation.sentence_id)
    if sentence_obj is None:
        log_drop(
            "vocabulary",
            annotation.type,
            annotation.sentence_id,
            annotation.text,
            "sentence_id_not_found",
            "grounding",
            drop_log,
        )
        return None

    if isinstance(annotation, PhraseGloss) and annotation.spans:
        parts = [
            {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
            for span in annotation.spans
        ]
        resolved_parts = resolve_explicit_anchor_parts(sentence_obj, parts)
        if resolved_parts is None:
            log_drop(
                "vocabulary",
                annotation.type,
                annotation.sentence_id,
                annotation.text,
                "anchor_not_substring",
                "grounding",
                drop_log,
            )
            return None
        normalized_spans = [
            span.model_copy(update={"text": resolved.text, "occurrence": resolved.occurrence})
            for span, resolved in zip(annotation.spans, resolved_parts, strict=False)
        ]
        return annotation.model_copy(update={"spans": normalized_spans})

    if isinstance(annotation, ContextGloss) and annotation.spans:
        parts = [
            {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
            for span in annotation.spans
        ]
        resolved_parts = resolve_explicit_anchor_parts(sentence_obj, parts)
        if resolved_parts is None:
            log_drop(
                "vocabulary",
                annotation.type,
                annotation.sentence_id,
                annotation.text,
                "anchor_not_substring",
                "grounding",
                drop_log,
            )
            return None
        normalized_spans = [
            span.model_copy(update={"text": resolved.text, "occurrence": resolved.occurrence})
            for span, resolved in zip(annotation.spans, resolved_parts, strict=False)
        ]
        return annotation.model_copy(update={"spans": normalized_spans})

    if annotation.text in sentence_obj.text:
        return annotation

    comparison_match = is_substring(annotation.text, sentence_obj.text)
    resolved = resolve_vocabulary_anchor_binding(
        sentence_obj,
        annotation.text,
        annotation.occurrence,
    )
    if resolved is not None:
        if resolved.kind == "text" and resolved.text is not None:
            return annotation.model_copy(update={"text": resolved.text})
        return annotation
    if comparison_match:
        return annotation

    log_drop(
        "vocabulary",
        annotation.type,
        annotation.sentence_id,
        annotation.text,
        "anchor_not_substring",
        "grounding",
        drop_log,
    )
    return None


def _check_low_value_word(text: str) -> bool:
    return text.lower() in LOW_VALUE_WORDS


def _passes_business_rules(
    annotation: VocabHighlight | PhraseGloss | ContextGloss,
    drop_log: list[DropLogEntry],
) -> bool:
    """在旧 Annotation 类型上做 business rules 校验。

    Phase 1 后 validate_*_business_rules 期望 Draft 类型，
    此处将旧类型字段映射回 Draft 语义进行校验。
    """
    if annotation.type == "vocab_highlight":
        draft = DraftVocabHighlight.model_construct(
            type="vocab_highlight",
            sentence_id=annotation.sentence_id,
            text=annotation.text,
        )
        reasons = validate_vocab_highlight_business_rules(draft)
    elif annotation.type == "phrase_gloss":
        draft = DraftPhraseGloss.model_construct(
            type="phrase_gloss",
            sentence_id=annotation.sentence_id,
            label=annotation.text,
            anchor_quotes=[
                AnchorQuote(text=span.text, role=span.role)
                for span in (annotation.spans or [])
            ],
            phrase_type=annotation.phrase_type,
            zh=annotation.zh,
        )
        reasons = validate_phrase_gloss_business_rules(draft)
    else:
        draft = DraftContextGloss.model_construct(
            type="context_gloss",
            sentence_id=annotation.sentence_id,
            display=annotation.text,
            anchor_quotes=[AnchorQuote(text=annotation.text)],
            gloss=annotation.gloss,
            reason=annotation.reason,
        )
        reasons = validate_context_gloss_business_rules(draft)

    if not reasons:
        return True

    for reason in reasons:
        log_drop(
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
    items: list[VocabHighlight],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[VocabHighlight]:
    result: list[VocabHighlight] = []
    seen_keys: set[str] = set()

    for item in items:
        normalized_item = _canonicalize_vocabulary_anchor(item, ctx.sentence_map, drop_log)
        if normalized_item is None:
            continue
        item = normalized_item
        if not _passes_business_rules(item, drop_log):
            continue
        if _check_low_value_word(item.text):
            log_drop(
                "vocabulary", item.type,
                item.sentence_id, item.text,
                "low_value_word", "pruning", drop_log,
            )
            continue
        key = _make_anchor_key(
            item.type, item.sentence_id,
            _annotation_anchor_payload(item, ctx.sentence_map),
        )
        if key in seen_keys:
            log_drop(
                "vocabulary", item.type,
                item.sentence_id, item.text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_phrase_glosses(
    items: list[PhraseGloss],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[PhraseGloss]:
    result: list[PhraseGloss] = []
    seen_keys: set[str] = set()

    for item in items:
        normalized_item = _canonicalize_vocabulary_anchor(item, ctx.sentence_map, drop_log)
        if normalized_item is None:
            continue
        item = normalized_item
        if not _passes_business_rules(item, drop_log):
            continue
        key = _make_anchor_key(
            item.type, item.sentence_id,
            _annotation_anchor_payload(item, ctx.sentence_map),
        )
        if key in seen_keys:
            log_drop(
                "vocabulary", item.type,
                item.sentence_id, item.text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_context_glosses(
    items: list[ContextGloss],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[ContextGloss]:
    result: list[ContextGloss] = []
    seen_keys: set[str] = set()

    for item in items:
        normalized_item = _canonicalize_vocabulary_anchor(item, ctx.sentence_map, drop_log)
        if normalized_item is None:
            continue
        item = normalized_item
        if not _passes_business_rules(item, drop_log):
            continue
        key = _make_anchor_key(
            item.type, item.sentence_id,
            _annotation_anchor_payload(item, ctx.sentence_map),
        )
        if key in seen_keys:
            log_drop(
                "vocabulary", item.type,
                item.sentence_id, item.text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_grammar_notes(
    items: list[GrammarNote],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[GrammarNote]:
    result: list[GrammarNote] = []
    seen_keys: set[str] = set()

    for item in items:
        normalized_spans: list[SpanRef] = []
        failed = False
        for span in item.spans:
            normalized_span = _canonicalize_grammar_span(span, item, ctx.sentence_map, drop_log)
            if normalized_span is None:
                failed = True
                break
            normalized_spans.append(normalized_span)
        if failed:
            continue
        item = item.model_copy(update={"spans": normalized_spans})
        primary_text = item.spans[0].text if item.spans else ""
        key = _make_anchor_key(item.type, item.sentence_id, primary_text)
        if key in seen_keys:
            log_drop(
                "grammar", item.type,
                item.sentence_id, primary_text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_sentence_analyses(
    items: list[SentenceAnalysis],
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[SentenceAnalysis]:
    result: list[SentenceAnalysis] = []
    seen_keys: set[str] = set()

    for item in items:
        if item.chunks and any(
            not _grounding_check(
                item.type, chunk.text,
                item.sentence_id, ctx.sentence_map,
                "grammar", drop_log,
            )
            for chunk in item.chunks
        ):
            continue
        key = _make_anchor_key(item.type, item.sentence_id, item.label)
        if key in seen_keys:
            log_drop(
                "grammar", item.type,
                item.sentence_id, item.label,
                "duplicate", "deduplication", drop_log,
            )
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
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
) -> list[Annotation]:
    merged: list[Annotation] = []
    merged.extend(grammar_result)
    merged.extend(sentence_analysis_result)

    keyed_candidates: dict[tuple[str, str], list[Annotation]] = {}
    for item in [*vocab_result, *phrase_result, *context_result]:
        key = (item.sentence_id, _annotation_anchor_token(item, sentence_map))
        keyed_candidates.setdefault(key, []).append(item)

    vocabulary_winners: list[Annotation] = []
    for (sentence_id, _anchor_token), candidates in keyed_candidates.items():
        candidates.sort(key=lambda item: PRIORITY_RANK.get(item.type, 0), reverse=True)
        winner = candidates[0]
        vocabulary_winners.append(winner)
        for loser in candidates[1:]:
            log_drop(
                "vocabulary",
                loser.type,
                sentence_id,
                getattr(loser, "text", ""),
                "conflict_resolution",
                "conflict_resolution",
                drop_log,
            )

    merged.extend(_drop_subsumed_vocab_highlights(vocabulary_winners, sentence_map, drop_log))
    return merged


def _annotation_spans(
    annotation: Annotation,
    sentence_map: dict[str, PreparedSentence],
) -> tuple[TextSpan, ...] | None:
    if annotation.type not in {"vocab_highlight", "phrase_gloss", "context_gloss"}:
        return None
    sentence_obj = sentence_map.get(annotation.sentence_id)
    if sentence_obj is None:
        return None
    if annotation.type == "phrase_gloss" and annotation.spans:
        parts = [
            {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
            for span in annotation.spans
        ]
        resolved_parts = resolve_explicit_anchor_parts(sentence_obj, parts)
        if resolved_parts is None:
            return None
        return tuple(part.span for part in resolved_parts)
    if annotation.type == "context_gloss" and annotation.spans:
        parts = [
            {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
            for span in annotation.spans
        ]
        resolved_parts = resolve_explicit_anchor_parts(sentence_obj, parts)
        if resolved_parts is None:
            return None
        return tuple(part.span for part in resolved_parts)
    return resolve_vocabulary_anchor_spans(
        sentence_obj,
        getattr(annotation, "text", ""),
        getattr(annotation, "occurrence", None),
    )


def _annotation_anchor_payload(
    annotation: Annotation,
    sentence_map: dict[str, PreparedSentence],
) -> object:
    spans = _annotation_spans(annotation, sentence_map)
    if spans is not None:
        return [{"start": span.start, "end": span.end} for span in spans]
    anchor_text = getattr(annotation, "text", None)
    if anchor_text is not None:
        return anchor_text
    if annotation.type == "grammar_note":
        return [
            {"text": span.text, "occurrence": span.occurrence}
            for span in annotation.spans
        ]
    return annotation.label


def _annotation_anchor_token(
    annotation: Annotation,
    sentence_map: dict[str, PreparedSentence],
) -> str:
    return json.dumps(
        _annotation_anchor_payload(annotation, sentence_map),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _span_contains(container: TextSpan, inner: TextSpan) -> bool:
    return container.start <= inner.start and inner.end <= container.end


def _span_groups_overlap(left: tuple[TextSpan, ...], right: tuple[TextSpan, ...]) -> bool:
    return any(
        l_span.start < r_span.end and r_span.start < l_span.end
        for l_span in left
        for r_span in right
    )


def _span_group_contains(container: tuple[TextSpan, ...], inner: TextSpan) -> bool:
    return any(_span_contains(span, inner) for span in container)


def _drop_subsumed_vocab_highlights(
    vocabulary_winners: list[Annotation],
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
) -> list[Annotation]:
    rich_annotations = sorted(
        [
            item for item in vocabulary_winners
            if item.type in {"context_gloss", "phrase_gloss"}
        ],
        key=lambda item: PRIORITY_RANK.get(item.type, 0),
        reverse=True,
    )
    rich_spans = [
        (item, spans)
        for item in rich_annotations
        if (spans := _annotation_spans(item, sentence_map)) is not None
    ]

    survivors: list[Annotation] = []
    for item in vocabulary_winners:
        if item.type != "vocab_highlight":
            survivors.append(item)
            continue

        item_spans = _annotation_spans(item, sentence_map)
        subsumer = None
        if item_spans:
            item_span = item_spans[0]
            for rich_item, rich_span_group in rich_spans:
                if rich_item.sentence_id == item.sentence_id and _span_group_contains(
                    rich_span_group,
                    item_span,
                ):
                    subsumer = rich_item
                    break

        if subsumer is None:
            survivors.append(item)
            continue

        log_drop(
            "vocabulary",
            item.type,
            item.sentence_id,
            item.text,
            f"subsumed_by_{subsumer.type}",
            "conflict_resolution",
            drop_log,
        )

    return survivors


def _annotation_identity(annotation: Annotation) -> str:
    if annotation.type in {"phrase_gloss", "context_gloss"} and annotation.spans:
        span_payload = json.dumps(
            [
                {"text": span.text, "occurrence": span.occurrence}
                for span in annotation.spans
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            f"{annotation.sentence_id}:{annotation.type}"
            f":{annotation.text}:{span_payload}"
        )
    anchor_text = getattr(annotation, "text", None)
    if anchor_text is not None:
        return f"{annotation.sentence_id}:{annotation.type}:{anchor_text}"
    if annotation.type == "grammar_note":
        primary = (
            annotation.spans[0].text
            if annotation.spans else annotation.label
        )
        return f"{annotation.sentence_id}:{annotation.type}:{primary}"
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
            log_drop(
                (
                    "vocabulary"
                    if item.type in {
                        "vocab_highlight", "phrase_gloss",
                        "context_gloss",
                    }
                    else "grammar"
                ),
                item.type,
                sentence_id,
                getattr(item, "text", getattr(item, "label", "")),
                f"density_exceeded_max_{max_per_sentence}",
                "density_control",
                drop_log,
            )

    return [
        annotation for annotation in annotations
        if _annotation_identity(annotation) in survivors
    ]


def _normalize_translations(
    draft: TranslationDraft,
    ctx: NormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[SentenceTranslation]:
    result: list[SentenceTranslation] = []
    seen_ids: set[str] = set()

    for item in draft.sentence_translations:
        if item.sentence_id not in ctx.sentence_map:
            log_drop(
                "translation", "sentence_translation",
                item.sentence_id, "",
                "sentence_id_not_found", "grounding", drop_log,
            )
            continue
        if not item.translation_zh.strip():
            log_drop(
                "translation", "sentence_translation",
                item.sentence_id, "",
                "empty_translation", "pruning", drop_log,
            )
            continue
        if item.sentence_id in seen_ids:
            log_drop(
                "translation", "sentence_translation",
                item.sentence_id, "",
                "duplicate", "deduplication", drop_log,
            )
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

    # Phase 1: Draft → Annotation 兼容转换
    vocab_highlights = [draft_to_annotation(v) for v in vocabulary_draft.vocab_highlights]
    phrase_glosses = [draft_to_annotation(p) for p in vocabulary_draft.phrase_glosses]
    context_glosses = [draft_to_annotation(c) for c in vocabulary_draft.context_glosses]
    grammar_notes = [draft_to_annotation(g) for g in grammar_draft.grammar_notes]
    sentence_analyses = [draft_to_annotation(s) for s in grammar_draft.sentence_analyses]

    vocab_result = _normalize_vocab_highlights(vocab_highlights, ctx, drop_log)
    phrase_result = _normalize_phrase_glosses(phrase_glosses, ctx, drop_log)
    context_result = _normalize_context_glosses(context_glosses, ctx, drop_log)
    grammar_result = _normalize_grammar_notes(grammar_notes, ctx, drop_log)
    sentence_analysis_result = _normalize_sentence_analyses(sentence_analyses, ctx, drop_log)
    translation_result = _normalize_translations(translation_draft, ctx, drop_log)

    merged_annotations = _merge_and_resolve_conflicts(
        vocab_result,
        phrase_result,
        context_result,
        grammar_result,
        sentence_analysis_result,
        ctx.sentence_map,
        drop_log,
    )

    final_annotations = _density_control(merged_annotations, ctx, drop_log)

    # ── Canonical shadow path ──────────────────────────────────────
    # 并行生成 normalized_annotations，使用 draft_to_normalized_annotation。
    # canonical resolve 失败只进入 canonical drop log / stats，
    # 不影响旧 annotations 输出。
    canonical_drop_log: list[DropLogEntry] = []
    normalized_annotations: list[NormalizedAnnotation] = []

    all_drafts: list[tuple[DraftAnnotation, str]] = []
    for v in vocabulary_draft.vocab_highlights:
        all_drafts.append((v, "vocabulary"))
    for p in vocabulary_draft.phrase_glosses:
        all_drafts.append((p, "vocabulary"))
    for c in vocabulary_draft.context_glosses:
        all_drafts.append((c, "vocabulary"))
    for g in grammar_draft.grammar_notes:
        all_drafts.append((g, "grammar"))
    for s in grammar_draft.sentence_analyses:
        all_drafts.append((s, "grammar"))

    for draft, source_agent in all_drafts:
        normalized = draft_to_normalized_annotation(
            draft, ctx.sentence_map, canonical_drop_log,
            source_agent=source_agent,
        )
        if normalized is not None:
            normalized_annotations.append(normalized)

    # ── Normalized postprocess ─────────────────────────────────────
    normalized_annotations = postprocess_normalized_annotations(
        normalized_annotations, canonical_drop_log, ctx.policy.annotation_density,
    )

    canonical_stats = build_canonical_stats(
        normalized_annotations, canonical_drop_log,
    )

    return NormalizedAnnotationResult(
        annotations=final_annotations,
        normalized_annotations=normalized_annotations,
        sentence_translations=translation_result,
        drop_log=drop_log,
        canonical_stats=canonical_stats,
        canonical_drop_log=canonical_drop_log,
    )
