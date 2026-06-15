from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.schemas.analysis import (
    ArticleParagraph,
    ArticleSentence,
    ArticleStructure,
    InlineGlossary,
    InlineMark,
    MultiRangeAnchor,
    RangeAnchor,
    RangePart,
    RenderSceneModel,
    SentenceEntry,
    TranslationItem,
)
from app.schemas.internal.analysis import (
    Chunk,
    GrammarNote,
    PreparedSentence,
    SentenceAnalysis,
)
from app.schemas.internal.normalized import (
    CanonicalSpan,
    NormalizedAnnotationResult,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)
from app.services.analysis.postprocess.utf16_offsets import python_range_to_utf16_range
from app.services.analysis.preprocess.input_preparation import PreparedInput

ANCHOR_FAILURE_THRESHOLD = 0.20


@dataclass
class ProjectionOutcome:
    result: RenderSceneModel
    warnings: list[dict[str, object]]
    dropped_count: int


def _stable_id(prefix: str, payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _build_article(prepared_input: PreparedInput, source_type: str) -> ArticleStructure:
    return ArticleStructure(
        source_type=source_type,
        source_text=prepared_input.source_text,
        render_text=prepared_input.render_text,
        paragraphs=[
            ArticleParagraph(
                paragraph_id=paragraph.paragraph_id,
                text=paragraph.text,
                render_span=paragraph.render_span,
                sentence_ids=paragraph.sentence_ids,
            )
            for paragraph in prepared_input.paragraphs
        ],
        sentences=[
            ArticleSentence(
                sentence_id=sentence.sentence_id,
                paragraph_id=sentence.paragraph_id,
                text=sentence.text,
                sentence_span=sentence.sentence_span,
            )
            for sentence in prepared_input.sentences
        ],
    )


def _format_grammar_note_content(annotation: GrammarNote) -> str:
    return annotation.note_zh


def _format_chunks(chunks: list[Chunk] | None) -> str:
    if not chunks:
        return ""
    return "\n".join(
        f"- **{chunk.order}. {chunk.label}**：`{chunk.text}`"
        for chunk in sorted(chunks, key=lambda item: item.order)
    )


def _format_sentence_analysis_content(annotation: SentenceAnalysis) -> str:
    chunks_text = _format_chunks(annotation.chunks)
    if not chunks_text:
        return annotation.analysis_zh
    return "\n\n".join([annotation.analysis_zh, chunks_text])


# ── Normalized projection ──────────────────────────────────────────────


def _canonical_span_to_range_part(
    span: CanonicalSpan,
    sentence_obj: PreparedSentence,
    render_text: str,
) -> RangePart | None:
    """Convert a CanonicalSpan to a sentence-local UTF-16 RangePart.

    Returns None if validation fails (fail-closed).
    """
    result = python_range_to_utf16_range(
        render_text=render_text,
        sentence_text=sentence_obj.text,
        sentence_start_in_render=sentence_obj.sentence_span.start,
        python_start=span.start,
        python_end=span.end,
        expected_text=span.text,
    )
    if result is None:
        return None
    utf16_start, utf16_end = result
    return RangePart(
        start=utf16_start,
        end=utf16_end,
        text=span.text,
        role=span.role,
        source_quote=span.source_quote,
        resolution_kind=span.resolution_kind,
    )


def _project_normalized_vocab_highlight(
    annotation: NormalizedVocabHighlight,
    sentence_obj: PreparedSentence,
    render_text: str,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    span = annotation.spans[0]
    range_part = _canonical_span_to_range_part(span, sentence_obj, render_text)
    if range_part is None:
        warnings.append({
            "code": "canonical_range_validation_failed",
            "level": "warning",
            "message": f"NormalizedVocabHighlight range 校验失败: {span.text}",
            "sentence_id": annotation.sentence_id,
        })
        return None, warnings

    anchor = RangeAnchor(
        kind="range",
        sentence_id=annotation.sentence_id,
        range=range_part,
    )
    inline_mark = InlineMark(
        id=_stable_id("im", {
            "type": annotation.type,
            "sid": annotation.sentence_id,
            "spans": [{"s": s.start, "e": s.end} for s in annotation.spans],
        }),
        annotation_type="vocab_highlight",
        anchor=anchor,
        render_type="background",
        visual_tone="vocab",
        clickable=True,
        lookup_text=span.text,
        lookup_kind="word",
        glossary=None,
    )
    return inline_mark, warnings


def _project_normalized_phrase_gloss(
    annotation: NormalizedPhraseGloss,
    sentence_obj: PreparedSentence,
    render_text: str,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    range_parts: list[RangePart] = []
    for span in annotation.spans:
        rp = _canonical_span_to_range_part(span, sentence_obj, render_text)
        if rp is None:
            warnings.append({
                "code": "canonical_range_validation_failed",
                "level": "warning",
                "message": f"NormalizedPhraseGloss range 校验失败: {span.text}",
                "sentence_id": annotation.sentence_id,
            })
            return None, warnings
        range_parts.append(rp)

    if len(range_parts) == 1:
        anchor: RangeAnchor | MultiRangeAnchor = RangeAnchor(
            kind="range",
            sentence_id=annotation.sentence_id,
            range=range_parts[0],
        )
    else:
        anchor = MultiRangeAnchor(
            kind="multi_range",
            sentence_id=annotation.sentence_id,
            ranges=range_parts,
        )

    inline_mark = InlineMark(
        id=_stable_id("im", {
            "type": annotation.type,
            "sid": annotation.sentence_id,
            "spans": [{"s": s.start, "e": s.end} for s in annotation.spans],
        }),
        annotation_type="phrase_gloss",
        anchor=anchor,
        render_type="background",
        visual_tone="phrase",
        clickable=True,
        lookup_text=annotation.label,
        lookup_kind="phrase",
        glossary=InlineGlossary(zh=annotation.zh, phrase_type=annotation.phrase_type),
    )
    return inline_mark, warnings


def _project_normalized_context_gloss(
    annotation: NormalizedContextGloss,
    sentence_obj: PreparedSentence,
    render_text: str,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    range_parts: list[RangePart] = []
    for span in annotation.spans:
        rp = _canonical_span_to_range_part(span, sentence_obj, render_text)
        if rp is None:
            warnings.append({
                "code": "canonical_range_validation_failed",
                "level": "warning",
                "message": f"NormalizedContextGloss range 校验失败: {span.text}",
                "sentence_id": annotation.sentence_id,
            })
            return None, warnings
        range_parts.append(rp)

    if len(range_parts) == 1:
        anchor: RangeAnchor | MultiRangeAnchor = RangeAnchor(
            kind="range",
            sentence_id=annotation.sentence_id,
            range=range_parts[0],
        )
    else:
        anchor = MultiRangeAnchor(
            kind="multi_range",
            sentence_id=annotation.sentence_id,
            ranges=range_parts,
        )

    lookup_text = annotation.display
    lookup_kind: str = (
        "phrase"
        if len(annotation.spans) > 1 or any(c.isspace() for c in lookup_text.strip())
        else "word"
    )

    inline_mark = InlineMark(
        id=_stable_id("im", {
            "type": annotation.type,
            "sid": annotation.sentence_id,
            "spans": [{"s": s.start, "e": s.end} for s in annotation.spans],
        }),
        annotation_type="context_gloss",
        anchor=anchor,
        render_type="underline",
        visual_tone="context",
        clickable=True,
        lookup_text=lookup_text,
        lookup_kind=lookup_kind,
        glossary=InlineGlossary(gloss=annotation.gloss, reason=annotation.reason),
    )
    return inline_mark, warnings


def _project_normalized_grammar_note(
    annotation: NormalizedGrammarNote,
    sentence_obj: PreparedSentence,
    render_text: str,
) -> tuple[InlineMark | None, SentenceEntry, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []

    stable_payload = {
        "type": annotation.type,
        "sid": annotation.sentence_id,
        "grammar_point": annotation.grammar_point,
        "spans": [{"s": s.start, "e": s.end} for s in annotation.spans],
    }

    sentence_entry = SentenceEntry(
        id=_stable_id("se", {"type": annotation.type, "shared_binding": stable_payload}),
        sentence_id=annotation.sentence_id,
        entry_type="grammar_note",
        label=annotation.grammar_point,
        title=annotation.grammar_point,
        content=annotation.note_zh,
    )

    range_parts: list[RangePart] = []
    for span in annotation.spans:
        rp = _canonical_span_to_range_part(span, sentence_obj, render_text)
        if rp is None:
            warnings.append({
                "code": "canonical_range_validation_failed",
                "level": "warning",
                "message": f"NormalizedGrammarNote range 校验失败: {span.text}",
                "sentence_id": annotation.sentence_id,
            })
            return None, sentence_entry, warnings
        range_parts.append(rp)

    if len(range_parts) == 1:
        anchor: RangeAnchor | MultiRangeAnchor = RangeAnchor(
            kind="range",
            sentence_id=annotation.sentence_id,
            range=range_parts[0],
        )
    else:
        anchor = MultiRangeAnchor(
            kind="multi_range",
            sentence_id=annotation.sentence_id,
            ranges=range_parts,
        )

    inline_mark = InlineMark(
        id=_stable_id("im", {"type": annotation.type, "shared_binding": stable_payload}),
        annotation_type="grammar_note",
        anchor=anchor,
        render_type="underline",
        visual_tone="grammar",
        clickable=False,
        glossary=None,
    )
    return inline_mark, sentence_entry, warnings


def _project_normalized_sentence_analysis(
    annotation: NormalizedSentenceAnalysis,
) -> SentenceEntry:
    return SentenceEntry(
        id=_stable_id("se", {
            "type": annotation.type,
            "sid": annotation.sentence_id,
            "label": annotation.label,
        }),
        sentence_id=annotation.sentence_id,
        entry_type="sentence_analysis",
        label=annotation.label,
        title=annotation.label,
        analysis_text=annotation.analysis_zh,
        chunks=[chunk.model_dump() for chunk in (annotation.chunks or [])],
        content=annotation.analysis_zh,
    )


def project_normalized_to_render_scene(
    normalized_result: NormalizedAnnotationResult,
    prepared_input: PreparedInput,
    source_type: str,
    reading_goal: str,
    reading_variant: str,
    profile_id: str,
    request_id: str,
) -> ProjectionOutcome:
    """Project NormalizedAnnotationResult to RenderScene using range anchors.

    This is the canonical projection path that converts CanonicalSpan
    offsets directly to UTF-16 ranges without text-search fallback.
    """
    warnings: list[dict[str, object]] = []
    inline_marks: list[InlineMark] = []
    sentence_entries: list[SentenceEntry] = []
    failed_annotations = 0
    total_annotations = len(normalized_result.normalized_annotations)
    sentence_map = {s.sentence_id: s for s in prepared_input.sentences}
    render_text = prepared_input.render_text

    for annotation in normalized_result.normalized_annotations:
        sentence_obj = sentence_map.get(annotation.sentence_id)
        if sentence_obj is None:
            warnings.append({
                "code": "sentence_id_invalid",
                "level": "error",
                "message": f"未找到 sentence_id={annotation.sentence_id} 对应句子",
                "sentence_id": annotation.sentence_id,
            })
            failed_annotations += 1
            continue

        if isinstance(annotation, NormalizedVocabHighlight):
            inline_mark, proj_warnings = _project_normalized_vocab_highlight(
                annotation, sentence_obj, render_text,
            )
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(proj_warnings)

        elif isinstance(annotation, NormalizedPhraseGloss):
            inline_mark, proj_warnings = _project_normalized_phrase_gloss(
                annotation, sentence_obj, render_text,
            )
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(proj_warnings)

        elif isinstance(annotation, NormalizedContextGloss):
            inline_mark, proj_warnings = _project_normalized_context_gloss(
                annotation, sentence_obj, render_text,
            )
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(proj_warnings)

        elif isinstance(annotation, NormalizedGrammarNote):
            inline_mark, sentence_entry, proj_warnings = _project_normalized_grammar_note(
                annotation, sentence_obj, render_text,
            )
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            sentence_entries.append(sentence_entry)
            warnings.extend(proj_warnings)

        elif isinstance(annotation, NormalizedSentenceAnalysis):
            sentence_entry = _project_normalized_sentence_analysis(annotation)
            sentence_entries.append(sentence_entry)

    if total_annotations > 0:
        failure_ratio = failed_annotations / total_annotations
        if failure_ratio > ANCHOR_FAILURE_THRESHOLD:
            warnings.append({
                "code": "anchor_failure_ratio_high",
                "level": "warning",
                "message": (
                    f"锚点失败率 {failure_ratio:.1%} "
                    f"超过阈值 {ANCHOR_FAILURE_THRESHOLD:.1%}"
                ),
            })

    translations = [
        TranslationItem(sentence_id=item.sentence_id, translation_zh=item.translation_zh)
        for item in normalized_result.sentence_translations
    ]
    expected_ids = {s.sentence_id for s in prepared_input.sentences}
    translated_ids = {item.sentence_id for item in translations}
    missing = expected_ids - translated_ids
    if missing:
        warnings.append({
            "code": "translation_coverage_incomplete",
            "level": "error",
            "message": f"缺少以下句子的翻译: {sorted(missing)}",
        })

    result = RenderSceneModel(
        schema_version="3.0.0",
        request={
            "request_id": request_id,
            "source_type": source_type,
            "reading_goal": reading_goal,
            "reading_variant": reading_variant,
            "profile_id": profile_id,
        },
        article=_build_article(prepared_input, source_type),
        translations=translations,
        inline_marks=inline_marks,
        sentence_entries=sentence_entries,
        warnings=warnings,
    )
    return ProjectionOutcome(result=result, warnings=warnings, dropped_count=failed_annotations)
