from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.schemas.internal.academic_drafts import (
    AcademicSentenceTranslation,
    AcademicTranslationDraft,
    InterpretationNote,
    LogicNote,
    ParagraphRole,
    TermDraft,
    TermNote,
    UnderstandingDraft,
)
from app.schemas.internal.academic_normalized import AcademicNormalizedResult, AcademicQualityState
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.execution_plan import AcademicGoalPolicy
from app.schemas.internal.normalized import DropLogEntry


ACADEMIC_DROP_SOURCE = Literal["term", "translation", "understanding"]

ACADEMIC_SIGNAL_WORDS: set[str] = {
    "algorithm", "analysis", "approach", "assessment", "baseline",
    "coefficient", "correlation", "covariate", "dataset", "derivative",
    "dimension", "distribution", "effect", "empirical", "estimate",
    "evaluation", "experiment", "framework", "function", "gradient",
    "hypothesis", "implementation", "index", "inference", "latent",
    "linear", "logistic", "matrix", "measurement", "method",
    "model", "network", "neural", "optimization", "parameter",
    "polynomial", "prediction", "probability", "regression", "sample",
    "significance", "simulation", "spectrum", "statistic", "stochastic",
    "strategy", "threshold", "trajectory", "validation", "variance",
    "vector", "significant", "methodology", "longitudinal", "cross-sectional",
    "mediated", "moderated", "robust", "systematic", "meta-analysis",
    "randomized", "controlled", "quantitative", "qualitative",
    "epidemiological", "retrospective", "prospective", "cohort",
    "placebo", "blinded", "multivariate", "univariate", "nonparametric",
}


@dataclass
class AcademicNormalizationContext:
    sentences: list[PreparedSentence]
    sentence_map: dict[str, PreparedSentence]
    paragraph_map: dict[str, list[str]]
    policy: AcademicGoalPolicy


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


def _log_academic_drop(
    source_agent: ACADEMIC_DROP_SOURCE,
    annotation_type: str,
    sentence_id: str,
    anchor_text: str,
    drop_reason: str,
    drop_stage: Literal["grounding", "deduplication", "density_control", "pruning"],
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


def _normalize_term_notes(
    draft: TermDraft,
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[TermNote]:
    result: list[TermNote] = []
    seen_keys: set[str] = set()

    for item in draft.term_notes:
        if not item.sentence_ids:
            _log_academic_drop(
                "term", "term_note", "", item.text,
                "empty_sentence_ids", "grounding", drop_log,
            )
            continue

        primary_sid = item.sentence_ids[0]
        primary_obj = ctx.sentence_map.get(primary_sid)
        if primary_obj is None:
            _log_academic_drop(
                "term", "term_note", primary_sid, item.text,
                "sentence_id_not_found", "grounding", drop_log,
            )
            continue
        if not _is_substring(item.text, primary_obj.text):
            _log_academic_drop(
                "term", "term_note", primary_sid, item.text,
                "anchor_not_substring", "grounding", drop_log,
            )
            continue

        secondary_valid = True
        for sid in item.sentence_ids[1:]:
            if sid not in ctx.sentence_map:
                _log_academic_drop(
                    "term", "term_note", sid, item.text,
                    "secondary_sentence_id_not_found", "grounding", drop_log,
                )
                secondary_valid = False
                break
        if not secondary_valid:
            continue

        key = _make_anchor_key("term_note", primary_sid, item.text)
        if key in seen_keys:
            _log_academic_drop(
                "term", "term_note", primary_sid, item.text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_academic_translations(
    draft: AcademicTranslationDraft,
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[AcademicSentenceTranslation]:
    result: list[AcademicSentenceTranslation] = []
    seen_ids: set[str] = set()

    for item in draft.sentence_translations:
        if item.sentence_id not in ctx.sentence_map:
            _log_academic_drop(
                "translation", "academic_sentence_translation", item.sentence_id, "",
                "sentence_id_not_found", "grounding", drop_log,
            )
            continue
        if not item.translation_zh.strip():
            _log_academic_drop(
                "translation", "academic_sentence_translation", item.sentence_id, "",
                "empty_translation", "pruning", drop_log,
            )
            continue
        if item.sentence_id in seen_ids:
            _log_academic_drop(
                "translation", "academic_sentence_translation", item.sentence_id, "",
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_ids.add(item.sentence_id)
        result.append(item)

    return result


def _normalize_logic_notes(
    draft: UnderstandingDraft,
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[LogicNote]:
    result: list[LogicNote] = []
    seen_keys: set[str] = set()

    for item in draft.logic_notes:
        if not item.sentence_ids:
            _log_academic_drop(
                "understanding", "logic_note", "", item.anchor_text,
                "empty_sentence_ids", "grounding", drop_log,
            )
            continue

        primary_sid = item.sentence_ids[0]
        primary_obj = ctx.sentence_map.get(primary_sid)
        if primary_obj is None:
            _log_academic_drop(
                "understanding", "logic_note", primary_sid, item.anchor_text,
                "sentence_id_not_found", "grounding", drop_log,
            )
            continue
        if not _is_substring(item.anchor_text, primary_obj.text):
            _log_academic_drop(
                "understanding", "logic_note", primary_sid, item.anchor_text,
                "anchor_not_substring", "grounding", drop_log,
            )
            continue

        secondary_valid = True
        for sid in item.sentence_ids[1:]:
            if sid not in ctx.sentence_map:
                _log_academic_drop(
                    "understanding", "logic_note", sid, item.anchor_text,
                    "secondary_sentence_id_not_found", "grounding", drop_log,
                )
                secondary_valid = False
                break
        if not secondary_valid:
            continue

        key = _make_anchor_key("logic_note", primary_sid, item.anchor_text)
        if key in seen_keys:
            _log_academic_drop(
                "understanding", "logic_note", primary_sid, item.anchor_text,
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_keys.add(key)
        result.append(item)

    return result


def _normalize_interpretation_notes(
    draft: UnderstandingDraft,
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[InterpretationNote]:
    result: list[InterpretationNote] = []
    seen_ids: set[str] = set()

    for item in draft.interpretation_notes:
        if item.sentence_id not in ctx.sentence_map:
            _log_academic_drop(
                "understanding", "interpretation_note", item.sentence_id, "",
                "sentence_id_not_found", "grounding", drop_log,
            )
            continue
        if item.sentence_id in seen_ids:
            _log_academic_drop(
                "understanding", "interpretation_note", item.sentence_id, "",
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_ids.add(item.sentence_id)
        result.append(item)

    return result


def _normalize_paragraph_roles(
    draft: UnderstandingDraft,
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[ParagraphRole]:
    result: list[ParagraphRole] = []
    seen_ids: set[str] = set()

    for item in draft.paragraph_roles:
        if item.paragraph_id not in ctx.paragraph_map:
            _log_academic_drop(
                "understanding", "paragraph_role", item.paragraph_id, "",
                "paragraph_id_not_found", "grounding", drop_log,
            )
            continue
        if item.paragraph_id in seen_ids:
            _log_academic_drop(
                "understanding", "paragraph_role", item.paragraph_id, "",
                "duplicate", "deduplication", drop_log,
            )
            continue
        seen_ids.add(item.paragraph_id)
        result.append(item)

    return result


def _density_control_term(
    term_notes: list[TermNote],
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[TermNote]:
    max_per_sentence = ctx.policy.term_density
    grouped: dict[str, list[TermNote]] = {}
    for note in term_notes:
        primary_sid = note.sentence_ids[0] if note.sentence_ids else ""
        grouped.setdefault(primary_sid, []).append(note)

    survivors: set[str] = set()
    for sentence_id, items in grouped.items():
        for item in items[:max_per_sentence]:
            key = _make_anchor_key("term_note", sentence_id, item.text)
            survivors.add(key)
        for item in items[max_per_sentence:]:
            _log_academic_drop(
                "term", "term_note", sentence_id, item.text,
                f"density_exceeded_max_{max_per_sentence}",
                "density_control", drop_log,
            )

    return [n for n in term_notes if _make_anchor_key("term_note", n.sentence_ids[0] if n.sentence_ids else "", n.text) in survivors]


def _density_control_logic(
    logic_notes: list[LogicNote],
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[LogicNote]:
    max_per_sentence = ctx.policy.logic_density
    grouped: dict[str, list[LogicNote]] = {}
    for note in logic_notes:
        primary_sid = note.sentence_ids[0] if note.sentence_ids else ""
        grouped.setdefault(primary_sid, []).append(note)

    survivors: set[str] = set()
    for sentence_id, items in grouped.items():
        for item in items[:max_per_sentence]:
            key = _make_anchor_key("logic_note", sentence_id, item.anchor_text)
            survivors.add(key)
        for item in items[max_per_sentence:]:
            _log_academic_drop(
                "understanding", "logic_note", sentence_id, item.anchor_text,
                f"density_exceeded_max_{max_per_sentence}",
                "density_control", drop_log,
            )

    return [n for n in logic_notes if _make_anchor_key("logic_note", n.sentence_ids[0] if n.sentence_ids else "", n.anchor_text) in survivors]


def _density_control_interpretation(
    interpretation_notes: list[InterpretationNote],
    ctx: AcademicNormalizationContext,
    drop_log: list[DropLogEntry],
) -> list[InterpretationNote]:
    max_per_sentence = ctx.policy.interpretation_density
    if max_per_sentence <= 0:
        for note in interpretation_notes:
            _log_academic_drop(
                "understanding", "interpretation_note", note.sentence_id, "",
                "density_disabled_interpretation_density_0",
                "density_control", drop_log,
            )
        return []

    grouped: dict[str, list[InterpretationNote]] = {}
    for note in interpretation_notes:
        grouped.setdefault(note.sentence_id, []).append(note)

    survivors: set[str] = set()
    for sentence_id, items in grouped.items():
        for item in items[:max_per_sentence]:
            survivors.add(item.sentence_id)
        for item in items[max_per_sentence:]:
            _log_academic_drop(
                "understanding", "interpretation_note", sentence_id, "",
                f"density_exceeded_max_{max_per_sentence}",
                "density_control", drop_log,
            )

    return [n for n in interpretation_notes if n.sentence_id in survivors]


def _estimate_term_density(sentences: list[PreparedSentence]) -> float:
    if not sentences:
        return 0.0
    total_words = 0
    signal_hits = 0
    for s in sentences:
        words = s.text.split()
        total_words += len(words)
        for w in words:
            cleaned = w.strip(".,;:()[]{}\"'").casefold()
            if cleaned in ACADEMIC_SIGNAL_WORDS:
                signal_hits += 1
    if total_words == 0:
        return 0.0
    return signal_hits / total_words


TERM_DENSITY_THRESHOLD = 0.04


def _assess_quality_state(
    *,
    term_annotations: list[TermNote],
    sentence_translations: list[AcademicSentenceTranslation],
    logic_notes: list[LogicNote],
    sentences: list[PreparedSentence],
    drop_log: list[DropLogEntry],
) -> tuple[AcademicQualityState, list[str]]:
    issues: list[str] = []

    if not sentence_translations and sentences:
        issues.append("translations_missing")

    if not term_annotations and sentences:
        estimated = _estimate_term_density(sentences)
        if estimated >= TERM_DENSITY_THRESHOLD:
            issues.append(f"term_annotations_empty_with_academic_density_{estimated:.2f}")

    if not logic_notes and sentences:
        estimated = _estimate_term_density(sentences)
        if estimated >= TERM_DENSITY_THRESHOLD * 2:
            issues.append(f"logic_notes_empty_with_high_complexity_{estimated:.2f}")

    if issues:
        return "degraded", issues
    return "normal", []


def academic_normalize_and_ground(
    term_draft: TermDraft,
    translation_draft: AcademicTranslationDraft,
    understanding_draft: UnderstandingDraft,
    sentences: list[PreparedSentence],
    policy: AcademicGoalPolicy,
) -> AcademicNormalizedResult:
    sentence_map = {s.sentence_id: s for s in sentences}
    paragraph_map: dict[str, list[str]] = {}
    for s in sentences:
        paragraph_map.setdefault(s.paragraph_id, []).append(s.sentence_id)

    ctx = AcademicNormalizationContext(
        sentences=sentences,
        sentence_map=sentence_map,
        paragraph_map=paragraph_map,
        policy=policy,
    )
    drop_log: list[DropLogEntry] = []

    term_result = _normalize_term_notes(term_draft, ctx, drop_log)
    term_result = _density_control_term(term_result, ctx, drop_log)

    translation_result = _normalize_academic_translations(translation_draft, ctx, drop_log)

    logic_result = _normalize_logic_notes(understanding_draft, ctx, drop_log)
    logic_result = _density_control_logic(logic_result, ctx, drop_log)

    interpretation_result = _normalize_interpretation_notes(understanding_draft, ctx, drop_log)
    interpretation_result = _density_control_interpretation(interpretation_result, ctx, drop_log)

    paragraph_roles = _normalize_paragraph_roles(understanding_draft, ctx, drop_log)

    content_summary = understanding_draft.content_summary

    quality_state, quality_issues = _assess_quality_state(
        term_annotations=term_result,
        sentence_translations=translation_result,
        logic_notes=logic_result,
        sentences=sentences,
        drop_log=drop_log,
    )

    return AcademicNormalizedResult(
        term_annotations=term_result,
        sentence_translations=translation_result,
        logic_notes=logic_result,
        interpretation_notes=interpretation_result,
        paragraph_roles=paragraph_roles,
        content_summary=content_summary,
        title=translation_draft.title,
        quality_state=quality_state,
        quality_issues=quality_issues,
        drop_log=drop_log,
    )
