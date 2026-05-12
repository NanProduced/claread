from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.schemas.analysis import (
    AcademicInlineGlossary,
    AcademicInlineMark,
    AcademicRenderSceneModel,
    AcademicSentenceEntry,
    AnalyzeRequestMeta,
    ArticleParagraph,
    ArticleSentence,
    ArticleStructure,
    TextAnchor,
    TranslationItem,
    Warning,
)
from app.schemas.internal.academic_drafts import ContentSummary, InterpretationNote, LogicNote, TermNote
from app.schemas.internal.academic_normalized import AcademicNormalizedResult
from app.schemas.internal.analysis import PreparedSentence
from app.services.analysis.postprocess.anchor_resolution import resolve_text_anchor
from app.services.analysis.preprocess.input_preparation import PreparedInput


@dataclass
class AcademicProjectionOutcome:
    result: AcademicRenderSceneModel
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
                paragraph_id=p.paragraph_id,
                text=p.text,
                render_span=p.render_span,
                sentence_ids=p.sentence_ids,
            )
            for p in prepared_input.paragraphs
        ],
        sentences=[
            ArticleSentence(
                sentence_id=s.sentence_id,
                paragraph_id=s.paragraph_id,
                text=s.text,
                sentence_span=s.sentence_span,
            )
            for s in prepared_input.sentences
        ],
    )


def _project_term_note(
    note: TermNote,
    sentence_obj: PreparedSentence,
) -> tuple[AcademicInlineMark | None, list[AcademicSentenceEntry], list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    entries: list[AcademicSentenceEntry] = []
    primary_sid = note.sentence_ids[0] if note.sentence_ids else ""

    resolved = resolve_text_anchor(sentence_obj, note.text, note.occurrence)
    if resolved is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"TermNote 锚点解析失败: {note.text}",
            "sentence_id": primary_sid,
        })
        return None, [], warnings

    anchor = TextAnchor(
        kind="text",
        sentence_id=primary_sid,
        anchor_text=note.text,
        occurrence=note.occurrence,
    )

    glossary = AcademicInlineGlossary(
        zh=note.zh,
        zh_uncertain=note.zh_uncertain,
        context_definition=note.context_definition,
        term_category=note.term_category,
    )

    stable_payload = note.model_dump()

    inline_mark = AcademicInlineMark(
        id=_stable_id("aim", {"type": "term_note", "shared_binding": stable_payload}),
        annotation_type="term_note",
        anchor=anchor,
        render_type="background",
        visual_tone="term",
        clickable=True,
        lookup_text=note.text,
        glossary=glossary,
    )

    base_label = f"{note.text}: {note.zh}"
    base_content = note.context_definition

    entries.append(AcademicSentenceEntry(
        id=_stable_id("ase", {"type": "term_note", "shared_binding": stable_payload}),
        sentence_id=primary_sid,
        entry_type="term_note",
        label=base_label,
        title=note.zh,
        content=base_content,
    ))

    for sid in note.sentence_ids[1:]:
        entries.append(AcademicSentenceEntry(
            id=_stable_id("ase", {"type": "term_note", "role": "cross_sentence_ref", "anchor": note.model_dump(), "ref_sid": sid}),
            sentence_id=sid,
            entry_type="term_note",
            label=f"↗ {base_label}",
            title=note.zh,
            content=base_content,
        ))

    return inline_mark, entries, warnings


def _project_logic_note(
    note: LogicNote,
    sentence_obj: PreparedSentence,
) -> tuple[AcademicInlineMark | None, list[AcademicSentenceEntry], list[dict[str, object]]]:
    warnings: list[dict[str, object]] = []
    entries: list[AcademicSentenceEntry] = []
    primary_sid = note.sentence_ids[0] if note.sentence_ids else ""

    resolved = resolve_text_anchor(sentence_obj, note.anchor_text, note.occurrence)
    if resolved is None:
        warnings.append({
            "code": "anchor_resolve_failed",
            "level": "warning",
            "message": f"LogicNote 锚点解析失败: {note.anchor_text}",
            "sentence_id": primary_sid,
        })
        return None, [], warnings

    anchor = TextAnchor(
        kind="text",
        sentence_id=primary_sid,
        anchor_text=note.anchor_text,
        occurrence=note.occurrence,
    )

    glossary = AcademicInlineGlossary(
        logic_type=note.logic_type,
        hedging_detected=note.hedging_detected,
        hedging_words=note.hedging_words,
    )

    stable_payload = note.model_dump()

    inline_mark = AcademicInlineMark(
        id=_stable_id("aim", {"type": "logic_note", "shared_binding": stable_payload}),
        annotation_type="logic_note",
        anchor=anchor,
        render_type="underline",
        visual_tone="logic",
        clickable=True,
        lookup_text=note.anchor_text,
        glossary=glossary,
    )

    base_label = f"{note.logic_type}: {note.anchor_text}"
    base_content = note.explanation

    entries.append(AcademicSentenceEntry(
        id=_stable_id("ase", {"type": "logic_note", "shared_binding": stable_payload}),
        sentence_id=primary_sid,
        entry_type="logic_note",
        label=base_label,
        title=note.logic_type,
        content=base_content,
    ))

    for sid in note.sentence_ids[1:]:
        entries.append(AcademicSentenceEntry(
            id=_stable_id("ase", {"type": "logic_note", "role": "cross_sentence_ref", "anchor": note.model_dump(), "ref_sid": sid}),
            sentence_id=sid,
            entry_type="logic_note",
            label=f"↗ {base_label}",
            title=note.logic_type,
            content=base_content,
        ))

    return inline_mark, entries, warnings


def _project_interpretation_note(
    note: InterpretationNote,
) -> AcademicSentenceEntry:
    title_mapping = {
        "decontextualization": "语境还原",
        "disambiguation": "消除歧义",
    }
    title = title_mapping.get(note.interpretation_type, note.interpretation_type)

    stable_payload = note.model_dump()

    return AcademicSentenceEntry(
        id=_stable_id("ase", {"type": "interpretation_note", "shared_binding": stable_payload}),
        sentence_id=note.sentence_id,
        entry_type="interpretation_note",
        label="解释",
        title=title,
        content=note.interpretation,
    )


def _project_content_summary(
    summary: ContentSummary,
) -> AcademicSentenceEntry:
    parts = [summary.overview]
    if summary.research_question:
        parts.append(f"\n研究问题: {summary.research_question}")
    if summary.methodology:
        parts.append(f"\n方法: {summary.methodology}")
    if summary.key_findings:
        parts.append(f"\n主要发现: {'; '.join(summary.key_findings)}")
    if summary.limitations:
        parts.append(f"\n局限性: {'; '.join(summary.limitations)}")

    return AcademicSentenceEntry(
        id=_stable_id("ase", {"type": "content_summary", "completeness": summary.completeness}),
        sentence_id="",
        entry_type="content_summary",
        label="内容概要",
        title="内容概要",
        content="\n".join(parts),
    )


def project_to_academic_render_scene(
    normalized_result: AcademicNormalizedResult,
    prepared_input: PreparedInput,
    source_type: str,
    reading_goal: str,
    reading_variant: str,
    profile_id: str,
    request_id: str,
) -> AcademicProjectionOutcome:
    warnings: list[dict[str, object]] = []
    inline_marks: list[AcademicInlineMark] = []
    sentence_entries: list[AcademicSentenceEntry] = []
    failed_annotations = 0
    sentence_map = {s.sentence_id: s for s in prepared_input.sentences}

    for note in normalized_result.term_annotations:
        if not note.sentence_ids:
            warnings.append({
                "code": "empty_sentence_ids",
                "level": "error",
                "message": "TermNote 缺少 sentence_ids",
                "sentence_id": "",
            })
            failed_annotations += 1
            continue

        primary_sid = note.sentence_ids[0]
        if primary_sid not in sentence_map:
            warnings.append({
                "code": "sentence_id_invalid",
                "level": "error",
                "message": f"未找到 sentence_id={primary_sid} 对应句子",
                "sentence_id": primary_sid,
            })
            failed_annotations += 1
            continue

        all_valid = True
        for sid in note.sentence_ids[1:]:
            if sid not in sentence_map:
                warnings.append({
                    "code": "sentence_id_invalid",
                    "level": "error",
                    "message": f"未找到 sentence_id={sid} 对应句子",
                    "sentence_id": sid,
                })
                all_valid = False
        if not all_valid:
            failed_annotations += 1
            continue

        sentence_obj = sentence_map[primary_sid]
        inline_mark, note_entries, proj_warnings = _project_term_note(note, sentence_obj)
        warnings.extend(proj_warnings)
        if inline_mark is not None:
            inline_marks.append(inline_mark)
        else:
            failed_annotations += 1
        sentence_entries.extend(note_entries)

    for note in normalized_result.logic_notes:
        if not note.sentence_ids:
            warnings.append({
                "code": "empty_sentence_ids",
                "level": "error",
                "message": "LogicNote 缺少 sentence_ids",
                "sentence_id": "",
            })
            failed_annotations += 1
            continue

        primary_sid = note.sentence_ids[0]
        if primary_sid not in sentence_map:
            warnings.append({
                "code": "sentence_id_invalid",
                "level": "error",
                "message": f"未找到 sentence_id={primary_sid} 对应句子",
                "sentence_id": primary_sid,
            })
            failed_annotations += 1
            continue

        all_valid = True
        for sid in note.sentence_ids[1:]:
            if sid not in sentence_map:
                warnings.append({
                    "code": "sentence_id_invalid",
                    "level": "error",
                    "message": f"未找到 sentence_id={sid} 对应句子",
                    "sentence_id": sid,
                })
                all_valid = False
        if not all_valid:
            failed_annotations += 1
            continue

        sentence_obj = sentence_map[primary_sid]
        inline_mark, note_entries, proj_warnings = _project_logic_note(note, sentence_obj)
        warnings.extend(proj_warnings)
        if inline_mark is not None:
            inline_marks.append(inline_mark)
        else:
            failed_annotations += 1
        sentence_entries.extend(note_entries)

    for note in normalized_result.interpretation_notes:
        entry = _project_interpretation_note(note)
        sentence_entries.append(entry)

    if normalized_result.content_summary is not None:
        entry = _project_content_summary(normalized_result.content_summary)
        sentence_entries.append(entry)

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

    result = AcademicRenderSceneModel(
        schema_version="3.0.0-academic",
        request=AnalyzeRequestMeta(
            request_id=request_id,
            source_type=source_type,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            profile_id=profile_id,
        ),
        article=_build_article(prepared_input, source_type),
        translations=translations,
        inline_marks=inline_marks,
        sentence_entries=sentence_entries,
        content_summary=normalized_result.content_summary,
        title=normalized_result.title,
        warnings=[Warning(**w) for w in warnings],
    )
    return AcademicProjectionOutcome(result=result, warnings=warnings, dropped_count=failed_annotations)
