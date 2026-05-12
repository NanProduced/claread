from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.schemas.analysis import (
    ArticleParagraph,
    ArticleSentence,
    ArticleStructure,
    InlineGlossary,
    InlineMark,
    MultiTextAnchor,
    RenderSceneModel,
    SentenceEntry,
    SpanRefPart,
    TextAnchor,
    TranslationItem,
)
from app.schemas.internal.analysis import (
    Chunk,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    SpanRef,
    AnnotationOutput,
    VocabHighlight,
)
from app.services.analysis.postprocess.anchor_resolution import resolve_multi_text_anchor, resolve_text_anchor
from app.services.analysis.preprocess.input_preparation import PreparedInput

if TYPE_CHECKING:
    from app.schemas.internal.analysis import PreparedSentence

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


def _resolve_span_ref(
    sentence_obj: PreparedSentence,
    sentence_id: str,
    text: str,
    occurrence: int | None,
) -> TextAnchor | None:
    resolved = resolve_text_anchor(sentence_obj, text, occurrence)
    if resolved is None:
        return None
    return TextAnchor(kind="text", sentence_id=sentence_id, anchor_text=text, occurrence=occurrence)


def _resolve_multi_span_refs(
    sentence_obj: PreparedSentence,
    sentence_id: str,
    spans: list[SpanRef],
) -> MultiTextAnchor | None:
    parts: list[dict[str, object]] = [
        {"anchor_text": span.text, "occurrence": span.occurrence, "role": span.role}
        for span in spans
    ]
    resolved = resolve_multi_text_anchor(sentence_obj, parts)
    if resolved is None:
        return None
    return MultiTextAnchor(
        kind="multi_text",
        sentence_id=sentence_id,
        parts=[
            SpanRefPart(
                anchor_text=part["anchor_text"],
                occurrence=part.get("occurrence"),
                role=part.get("role"),
            )
            for part in parts
        ],
    )


def _project_vocab_highlight(
    annotation: VocabHighlight,
    sentence_obj: PreparedSentence,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    resolved_anchor = _resolve_span_ref(
        sentence_obj, annotation.sentence_id, annotation.text, annotation.occurrence
    )
    if resolved_anchor is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"VocabHighlight 锚点解析失败: {annotation.text}",
            "sentence_id": annotation.sentence_id,
        })
        return None, warnings
    inline_mark = InlineMark(
        id=_stable_id("im", {"type": annotation.type, "anchor": annotation.model_dump()}),
        annotation_type="vocab_highlight",
        anchor=resolved_anchor,
        render_type="background",
        visual_tone="vocab",
        clickable=True,
        lookup_text=annotation.text,
        lookup_kind="word",
        glossary=None,
    )
    return inline_mark, warnings


def _project_phrase_gloss(
    annotation: PhraseGloss,
    sentence_obj: PreparedSentence,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    resolved_anchor = _resolve_span_ref(
        sentence_obj, annotation.sentence_id, annotation.text, annotation.occurrence
    )
    if resolved_anchor is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"PhraseGloss 锚点解析失败: {annotation.text}",
            "sentence_id": annotation.sentence_id,
        })
        return None, warnings
    inline_mark = InlineMark(
        id=_stable_id("im", {"type": annotation.type, "anchor": annotation.model_dump()}),
        annotation_type="phrase_gloss",
        anchor=resolved_anchor,
        render_type="background",
        visual_tone="phrase",
        clickable=True,
        lookup_text=annotation.text,
        lookup_kind="phrase",
        glossary=InlineGlossary(zh=annotation.zh, phrase_type=annotation.phrase_type),
    )
    return inline_mark, warnings


def _project_context_gloss(
    annotation: ContextGloss,
    sentence_obj: PreparedSentence,
) -> tuple[InlineMark | None, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    resolved_anchor = _resolve_span_ref(
        sentence_obj, annotation.sentence_id, annotation.text, annotation.occurrence
    )
    if resolved_anchor is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"ContextGloss 锚点解析失败: {annotation.text}",
            "sentence_id": annotation.sentence_id,
        })
        return None, warnings
    inline_mark = InlineMark(
        id=_stable_id("im", {"type": annotation.type, "anchor": annotation.model_dump()}),
        annotation_type="context_gloss",
        anchor=resolved_anchor,
        render_type="underline",
        visual_tone="context",
        clickable=True,
        lookup_text=annotation.text,
        lookup_kind="word",
        glossary=InlineGlossary(gloss=annotation.gloss, reason=annotation.reason),
    )
    return inline_mark, warnings


def _project_grammar_note(
    annotation: GrammarNote,
    sentence_obj: PreparedSentence,
) -> tuple[InlineMark | None, SentenceEntry, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    if len(annotation.spans) == 1:
        span = annotation.spans[0]
        resolved_anchor: TextAnchor | MultiTextAnchor | None = _resolve_span_ref(
            sentence_obj, annotation.sentence_id, span.text, span.occurrence
        )
    else:
        resolved_anchor = _resolve_multi_span_refs(
            sentence_obj, annotation.sentence_id, annotation.spans
        )

    content = _format_grammar_note_content(annotation)

    # NOTE: To allow frontend correlation, we use the EXACT same dictionary structure to generate the stable ID suffix.
    stable_payload_dict = annotation.model_dump()

    sentence_entry = SentenceEntry(
        id=_stable_id("se", {"type": annotation.type, "shared_binding": stable_payload_dict}),
        sentence_id=annotation.sentence_id,
        entry_type="grammar_note",
        label=annotation.label,
        title=annotation.label,
        content=content,
    )

    if resolved_anchor is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"GrammarNote 锚点解析失败: {[s.text for s in annotation.spans]}",
            "sentence_id": annotation.sentence_id,
        })
        return None, sentence_entry, warnings

    inline_mark = InlineMark(
        id=_stable_id("im", {"type": annotation.type, "shared_binding": stable_payload_dict}),
        annotation_type="grammar_note",
        anchor=resolved_anchor,
        render_type="underline",
        visual_tone="grammar",
        clickable=False,
        glossary=None,
    )
    return inline_mark, sentence_entry, warnings


def _validate_chunks(chunks: list[Chunk] | None, sentence_text: str) -> bool:
    if not chunks:
        return True  # chunks 为可选，空值通过验证
    orders = [chunk.order for chunk in chunks]
    if sorted(orders) != list(range(1, len(chunks) + 1)):
        return False
    return all(chunk.text in sentence_text for chunk in chunks)


def _format_grammar_spans(spans: list[SpanRef]) -> str:
    if not spans:
        return ""
    return "\n".join(
        f"- **{span.role or '关键成分'}**：`{span.text}`"
        for span in spans
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


def _project_sentence_analysis(
    annotation: SentenceAnalysis,
    sentence_obj: PreparedSentence,
) -> tuple[None, SentenceEntry, list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    if not _validate_chunks(annotation.chunks, sentence_obj.text):
        warnings.append({
            "code": "chunks_validation_failed",
            "level": "warning",
            "message": "SentenceAnalysis chunks 校验失败，降级为普通讲解",
            "sentence_id": annotation.sentence_id,
        })
    content = _format_sentence_analysis_content(annotation)
    sentence_entry = SentenceEntry(
        id=_stable_id("se", {"type": annotation.type, "model": annotation.model_dump()}),
        sentence_id=annotation.sentence_id,
        entry_type="sentence_analysis",
        label=annotation.label,
        title=annotation.label,
        content=content,
    )
    return None, sentence_entry, warnings


def project_to_render_scene(
    annotation_output: AnnotationOutput,
    prepared_input: PreparedInput,
    source_type: str,
    reading_goal: str,
    reading_variant: str,
    profile_id: str,
    request_id: str,
) -> ProjectionOutcome:
    warnings: list[dict[str, object]] = []
    inline_marks: list[InlineMark] = []
    sentence_entries: list[SentenceEntry] = []
    failed_annotations = 0
    total_annotations = len(annotation_output.annotations)
    sentence_map = {s.sentence_id: s for s in prepared_input.sentences}

    for annotation in annotation_output.annotations:
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
        if isinstance(annotation, VocabHighlight):
            inline_mark, projection_warnings = _project_vocab_highlight(annotation, sentence_obj)
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(projection_warnings)
        elif isinstance(annotation, PhraseGloss):
            inline_mark, projection_warnings = _project_phrase_gloss(annotation, sentence_obj)
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(projection_warnings)
        elif isinstance(annotation, ContextGloss):
            inline_mark, projection_warnings = _project_context_gloss(annotation, sentence_obj)
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            warnings.extend(projection_warnings)
        elif isinstance(annotation, GrammarNote):
            inline_mark, sentence_entry, projection_warnings = _project_grammar_note(
                annotation, sentence_obj
            )
            if inline_mark is not None:
                inline_marks.append(inline_mark)
            else:
                failed_annotations += 1
            sentence_entries.append(sentence_entry)
            warnings.extend(projection_warnings)
        elif isinstance(annotation, SentenceAnalysis):
            _, sentence_entry, projection_warnings = _project_sentence_analysis(
                annotation, sentence_obj
            )
            sentence_entries.append(sentence_entry)
            warnings.extend(projection_warnings)
        else:
            warnings.append({
                "code": "unknown_annotation_type",
                "level": "warning",
                "message": f"未知的 annotation 类型，已跳过：{type(annotation).__name__}",
                "sentence_id": annotation.sentence_id,
            })
            failed_annotations += 1

    if total_annotations > 0:
        failure_ratio = failed_annotations / total_annotations
        if failure_ratio > ANCHOR_FAILURE_THRESHOLD:
            warnings.append({
                "code": "anchor_failure_ratio_high",
                "level": "warning",
                "message": f"锚点失败率 {failure_ratio:.1%} 超过阈值 {ANCHOR_FAILURE_THRESHOLD:.1%}",  # noqa: E501
            })

    translations = [
        TranslationItem(sentence_id=item.sentence_id, translation_zh=item.translation_zh)
        for item in annotation_output.sentence_translations
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
