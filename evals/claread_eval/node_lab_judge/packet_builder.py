from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from claread_eval.node_lab_judge.schemas import (
    EvidenceItem,
    JudgePreset,
    NodeLabRubricScoringResult,
    PairwiseFailedItem,
    PairwisePacket,
    PairwiseSelectedAnnotation,
    PairwiseSentenceUnit,
    PairwiseTranslationUnit,
    ProbePacket,
    ProbeQuestionSpec,
    ResolvedJudgeContext,
    RubricCriterionSpec,
    RubricPacket,
    RubricPacketSide,
    StrategyRubricSpec,
    TranslationOutputUnit,
)


def _sentence_map(result_entry: dict[str, Any] | None) -> dict[str, str]:
    prepared = result_entry.get("prepared_sentences") if isinstance(result_entry, dict) else []
    mapping: dict[str, str] = {}
    for item in prepared or []:
        sentence_id = str(item.get("sentence_id") or "")
        if sentence_id:
            mapping[sentence_id] = str(item.get("text") or "")
    return mapping


def _grammar_note_item(raw: dict[str, Any], sentence_text: str) -> EvidenceItem:
    spans = raw.get("spans") or []
    anchor_texts = [str(span.get("text") or "") for span in spans if span.get("text")]
    return EvidenceItem(
        item_id=f"grammar_note:{raw.get('sentence_id')}:{raw.get('label')}:{'|'.join(anchor_texts)}",
        item_type="grammar_note",
        sentence_id=raw.get("sentence_id"),
        label=raw.get("label"),
        source_excerpt=sentence_text,
        sentence_text=sentence_text,
        explanation=raw.get("note_zh"),
        anchor_texts=anchor_texts,
        raw_item=raw,
    )


def _sentence_analysis_item(raw: dict[str, Any], sentence_text: str) -> EvidenceItem:
    chunks = raw.get("chunks") or []
    anchor_texts = [str(chunk.get("text") or "") for chunk in chunks if chunk.get("text")]
    return EvidenceItem(
        item_id=f"sentence_analysis:{raw.get('sentence_id')}:{raw.get('label')}",
        item_type="sentence_analysis",
        sentence_id=raw.get("sentence_id"),
        label=raw.get("label"),
        source_excerpt=sentence_text,
        sentence_text=sentence_text,
        explanation=raw.get("analysis_zh"),
        anchor_texts=anchor_texts,
        raw_item=raw,
    )


def _vocabulary_item(item_type: str, raw: dict[str, Any], sentence_text: str) -> EvidenceItem:
    explanation = raw.get("zh") or raw.get("gloss") or raw.get("reason") or raw.get("text") or ""
    anchor_text = str(raw.get("text") or "")
    return EvidenceItem(
        item_id=f"{item_type}:{raw.get('sentence_id')}:{anchor_text}",
        item_type=item_type,
        sentence_id=raw.get("sentence_id"),
        label=raw.get("phrase_type") or item_type,
        source_excerpt=sentence_text,
        sentence_text=sentence_text,
        explanation=str(explanation or ""),
        anchor_texts=[anchor_text] if anchor_text else [],
        raw_item=raw,
    )


def _translation_style_hint(reading_goal: str, reading_variant: str) -> str:
    if reading_goal == "daily_reading":
        return "literal_support" if reading_variant == "beginner_reading" else "natural"
    if reading_goal == "exam":
        return {
            "gaokao": "literal_support",
            "cet": "natural",
            "ielts_toefl": "natural",
            "tem": "nuanced_aesthetic",
            "kaoyan": "academic",
        }.get(reading_variant, "natural")
    return "natural"


def _translation_output_unit(raw: dict[str, Any], sentence_text: str, strategy_hint: str) -> TranslationOutputUnit:
    return TranslationOutputUnit(
        sentence_id=str(raw.get("sentence_id") or "") or None,
        source_sentence=sentence_text,
        translation=str(raw.get("translation_zh") or ""),
        translation_strategy_hint=strategy_hint,
    )


def _sorted_grammar_items(items: list[EvidenceItem], profile: str | None) -> list[EvidenceItem]:
    if profile != "structure_first":
        return items
    priority = {"sentence_analysis": 0, "grammar_note": 1}
    return sorted(items, key=lambda item: (priority.get(item.item_type, 9), item.sentence_id or "", item.item_id))


def _sorted_vocabulary_items(items: list[EvidenceItem], profile: str | None) -> list[EvidenceItem]:
    priority_map = {
        "collocation_first": {"phrase_gloss": 0, "context_gloss": 1, "vocab_highlight": 2},
        "default": {"context_gloss": 0, "phrase_gloss": 1, "vocab_highlight": 2},
    }
    priority = priority_map.get(profile or "default", priority_map["default"])
    return sorted(items, key=lambda item: (priority.get(item.item_type, 9), item.sentence_id or "", item.item_id))


def _select_vocabulary_profile(preset: JudgePreset, reading_variant: str) -> str:
    by_variant = preset.packet_policy.priority_profile_by_variant
    return by_variant.get(reading_variant) or by_variant.get("default") or "default"


def _build_rubric_bundle(
    strategy_spec: StrategyRubricSpec,
    preset: JudgePreset,
) -> dict[str, list[RubricCriterionSpec]]:
    bundle: dict[str, list[RubricCriterionSpec]] = {}
    if strategy_spec.output_level is not None:
        allowed = set(preset.rubric_bundle.get("output_level") or [])
        bundle["output_level"] = [
            criterion for criterion in strategy_spec.output_level.criteria if criterion.id in allowed
        ]
    for item_type, allowed_ids in preset.rubric_bundle.items():
        item_spec = strategy_spec.item_types.get(item_type)
        if item_spec is None:
            continue
        allowed = set(allowed_ids)
        bundle[item_type] = [criterion for criterion in item_spec.criteria if criterion.id in allowed]
    return bundle


def _build_grammar_side(
    participant: Literal["baseline", "candidate"],
    result_entry: dict[str, Any],
    preset: JudgePreset,
) -> RubricPacketSide:
    sentence_map = _sentence_map(result_entry)
    output = result_entry.get("node_output") or {}
    items: list[EvidenceItem] = []
    for raw in output.get("sentence_analyses") or []:
        sentence_id = str(raw.get("sentence_id") or "")
        items.append(_sentence_analysis_item(raw, sentence_map.get(sentence_id, "")))
    for raw in output.get("grammar_notes") or []:
        sentence_id = str(raw.get("sentence_id") or "")
        items.append(_grammar_note_item(raw, sentence_map.get(sentence_id, "")))
    items = _sorted_grammar_items(items, preset.packet_policy.priority_profile)
    items = items[: preset.packet_policy.max_items_per_side or len(items)]
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.item_type] += 1
    return RubricPacketSide(participant=participant, item_count_by_type=dict(counts), items=items)


def _build_vocabulary_side(
    participant: Literal["baseline", "candidate"],
    result_entry: dict[str, Any],
    preset: JudgePreset,
    reading_variant: str,
) -> RubricPacketSide:
    sentence_map = _sentence_map(result_entry)
    output = result_entry.get("node_output") or {}
    items: list[EvidenceItem] = []
    for raw in output.get("context_glosses") or []:
        sentence_id = str(raw.get("sentence_id") or "")
        items.append(_vocabulary_item("context_gloss", raw, sentence_map.get(sentence_id, "")))
    for raw in output.get("phrase_glosses") or []:
        sentence_id = str(raw.get("sentence_id") or "")
        items.append(_vocabulary_item("phrase_gloss", raw, sentence_map.get(sentence_id, "")))
    for raw in output.get("vocab_highlights") or []:
        sentence_id = str(raw.get("sentence_id") or "")
        items.append(_vocabulary_item("vocab_highlight", raw, sentence_map.get(sentence_id, "")))
    items = _sorted_vocabulary_items(items, _select_vocabulary_profile(preset, reading_variant))
    items = items[: preset.packet_policy.max_items_per_side or len(items)]
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.item_type] += 1
    return RubricPacketSide(participant=participant, item_count_by_type=dict(counts), items=items)


def _sample_translations(
    translations: list[dict[str, Any]],
    max_sentences: int,
) -> list[dict[str, Any]]:
    if len(translations) <= max_sentences:
        return translations
    half = max_sentences // 2
    tail = max_sentences - half
    return translations[:half] + translations[-tail:]


def _build_translation_side(
    participant: Literal["baseline", "candidate"],
    result_entry: dict[str, Any],
    preset: JudgePreset,
    reading_goal: str,
    reading_variant: str,
) -> RubricPacketSide:
    sentence_map = _sentence_map(result_entry)
    output = result_entry.get("node_output") or {}
    strategy_hint = _translation_style_hint(reading_goal, reading_variant)
    translations = _sample_translations(
        list(output.get("sentence_translations") or []),
        preset.packet_policy.max_sentences_per_side or 8,
    )
    output_units = [
        _translation_output_unit(raw, sentence_map.get(str(raw.get("sentence_id") or ""), ""), strategy_hint)
        for raw in translations
    ]
    return RubricPacketSide(
        participant=participant,
        item_count_by_type={"sentence_translation": len(output_units)},
        output_units=output_units,
    )


def build_rubric_packet(
    *,
    compare_payload: dict[str, Any],
    preset: JudgePreset,
    context: ResolvedJudgeContext,
    strategy_spec: StrategyRubricSpec,
    reading_goal: str,
    reading_variant: str,
) -> RubricPacket:
    baseline = compare_payload.get("baseline") or {}
    candidate = compare_payload.get("candidate") or {}
    if preset.strategy == "grammar_item_review":
        baseline_side = _build_grammar_side("baseline", baseline, preset)
        candidate_side = _build_grammar_side("candidate", candidate, preset)
    elif preset.strategy == "vocabulary_item_review":
        baseline_side = _build_vocabulary_side("baseline", baseline, preset, reading_variant)
        candidate_side = _build_vocabulary_side("candidate", candidate, preset, reading_variant)
    else:
        baseline_side = _build_translation_side("baseline", baseline, preset, reading_goal, reading_variant)
        candidate_side = _build_translation_side("candidate", candidate, preset, reading_goal, reading_variant)
    return RubricPacket(
        node_name=preset.node_name,
        strategy=preset.strategy,
        method=preset.method,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        context=context,
        rubric_bundle=_build_rubric_bundle(strategy_spec, preset),
        baseline=baseline_side,
        candidate=candidate_side,
        compare_summary=compare_payload.get("compare_summary") or {},
    )


def _collect_failed_items_by_sentence(
    rubric_result: NodeLabRubricScoringResult,
) -> tuple[dict[str, list[PairwiseFailedItem]], list[PairwiseFailedItem]]:
    failed_by_sentence: dict[str, list[PairwiseFailedItem]] = defaultdict(list)
    all_failed: list[PairwiseFailedItem] = []
    for participant, side in (("baseline", rubric_result.baseline), ("candidate", rubric_result.candidate)):
        for item in side.items:
            for criterion in item.criteria:
                if criterion.score == 1:
                    continue
                failed = PairwiseFailedItem(
                    participant=participant,
                    item_id=item.item_id,
                    item_type=item.item_type,
                    criterion_id=criterion.criterion_id,
                    reason=criterion.reason,
                    evidence=criterion.evidence,
                )
                sentence_id = item.sentence_id or "global"
                failed_by_sentence[sentence_id].append(failed)
                all_failed.append(failed)
        for criterion in side.output_level_scores:
            if criterion.score == 1:
                continue
            failed = PairwiseFailedItem(
                participant=participant,
                item_id="output_level",
                item_type="output_level",
                criterion_id=criterion.criterion_id,
                reason=criterion.reason,
                evidence=criterion.evidence,
            )
            failed_by_sentence["global"].append(failed)
            all_failed.append(failed)
    return failed_by_sentence, all_failed


def _summarize_watchouts(items: list[PairwiseFailedItem]) -> list[str]:
    watchouts: list[str] = []
    for item in items[:4]:
        watchouts.append(f"{item.participant} {item.item_type} / {item.criterion_id}: {item.reason}")
    return watchouts


def _annotation_preview(item_type: str, raw: dict[str, Any]) -> PairwiseSelectedAnnotation:
    if item_type == "grammar_note":
        anchors = [str(span.get("text") or "") for span in raw.get("spans") or [] if span.get("text")]
        return PairwiseSelectedAnnotation(
            item_type=item_type,
            label=str(raw.get("label") or ""),
            content=str(raw.get("note_zh") or ""),
            anchor_text_preview=" / ".join(anchors[:2]) or None,
        )
    if item_type == "sentence_analysis":
        anchors = [str(chunk.get("text") or "") for chunk in raw.get("chunks") or [] if chunk.get("text")]
        return PairwiseSelectedAnnotation(
            item_type=item_type,
            label=str(raw.get("label") or ""),
            content=str(raw.get("analysis_zh") or ""),
            anchor_text_preview=" / ".join(anchors[:2]) or None,
        )
    if item_type == "context_gloss":
        return PairwiseSelectedAnnotation(
            item_type=item_type,
            label=str(raw.get("text") or "context_gloss"),
            content="；".join(
                part for part in [str(raw.get("zh") or ""), str(raw.get("reason") or "")] if part
            )
            or None,
        )
    if item_type == "phrase_gloss":
        return PairwiseSelectedAnnotation(
            item_type=item_type,
            label=str(raw.get("text") or raw.get("phrase_type") or "phrase_gloss"),
            content=str(raw.get("zh") or raw.get("gloss") or ""),
        )
    return PairwiseSelectedAnnotation(
        item_type=item_type,
        label=str(raw.get("text") or raw.get("phrase_type") or "vocab_highlight"),
        content=str(raw.get("text") or ""),
    )


def _collect_sentence_annotations(
    result_entry: dict[str, Any],
    *,
    node_name: str,
) -> dict[str, list[PairwiseSelectedAnnotation]]:
    output = result_entry.get("node_output") or {}
    grouped: dict[str, list[PairwiseSelectedAnnotation]] = defaultdict(list)
    if node_name == "grammar":
        for raw in output.get("grammar_notes") or []:
            sentence_id = str(raw.get("sentence_id") or "")
            if sentence_id:
                grouped[sentence_id].append(_annotation_preview("grammar_note", raw))
        for raw in output.get("sentence_analyses") or []:
            sentence_id = str(raw.get("sentence_id") or "")
            if sentence_id:
                grouped[sentence_id].append(_annotation_preview("sentence_analysis", raw))
    elif node_name == "vocabulary":
        for raw in output.get("context_glosses") or []:
            sentence_id = str(raw.get("sentence_id") or "")
            if sentence_id:
                grouped[sentence_id].append(_annotation_preview("context_gloss", raw))
        for raw in output.get("phrase_glosses") or []:
            sentence_id = str(raw.get("sentence_id") or "")
            if sentence_id:
                grouped[sentence_id].append(_annotation_preview("phrase_gloss", raw))
        for raw in output.get("vocab_highlights") or []:
            sentence_id = str(raw.get("sentence_id") or "")
            if sentence_id:
                grouped[sentence_id].append(_annotation_preview("vocab_highlight", raw))
    return grouped


def _ordered_sentence_ids(
    compare_payload: dict[str, Any],
    node_name: str,
    failed_by_sentence: dict[str, list[PairwiseFailedItem]],
) -> list[str]:
    baseline_map = _sentence_map(compare_payload.get("baseline") or {})
    candidate_map = _sentence_map(compare_payload.get("candidate") or {})
    baseline_ann = _collect_sentence_annotations(compare_payload.get("baseline") or {}, node_name=node_name)
    candidate_ann = _collect_sentence_annotations(compare_payload.get("candidate") or {}, node_name=node_name)
    ordered: list[str] = []
    for sentence_id in failed_by_sentence:
        if sentence_id != "global" and sentence_id not in ordered:
            ordered.append(sentence_id)
    for sentence_id in list(baseline_map.keys()) + list(candidate_map.keys()):
        has_content = baseline_ann.get(sentence_id) or candidate_ann.get(sentence_id)
        if sentence_id and has_content and sentence_id not in ordered:
            ordered.append(sentence_id)
    return ordered


def _select_pairwise_sentence_units(
    *,
    compare_payload: dict[str, Any],
    node_name: Literal["grammar", "vocabulary"],
    failed_by_sentence: dict[str, list[PairwiseFailedItem]],
    max_units: int,
) -> list[PairwiseSentenceUnit]:
    baseline_entry = compare_payload.get("baseline") or {}
    candidate_entry = compare_payload.get("candidate") or {}
    baseline_map = _sentence_map(baseline_entry)
    candidate_map = _sentence_map(candidate_entry)
    baseline_ann = _collect_sentence_annotations(baseline_entry, node_name=node_name)
    candidate_ann = _collect_sentence_annotations(candidate_entry, node_name=node_name)
    units: list[PairwiseSentenceUnit] = []
    for sentence_id in _ordered_sentence_ids(compare_payload, node_name, failed_by_sentence):
        source_sentence = baseline_map.get(sentence_id) or candidate_map.get(sentence_id) or ""
        baseline_selected = baseline_ann.get(sentence_id, [])[:3]
        candidate_selected = candidate_ann.get(sentence_id, [])[:3]
        if not source_sentence or (not baseline_selected and not candidate_selected):
            continue
        units.append(
            PairwiseSentenceUnit(
                sentence_id=sentence_id,
                source_sentence=source_sentence,
                baseline_selected_annotations=baseline_selected,
                candidate_selected_annotations=candidate_selected,
                rubric_watchouts=_summarize_watchouts(failed_by_sentence.get(sentence_id, [])),
            )
        )
        if len(units) >= max_units:
            break
    return units


def _translation_units_from_compare(
    compare_payload: dict[str, Any],
    *,
    reading_goal: str,
    reading_variant: str,
    max_units: int,
    failed_by_sentence: dict[str, list[PairwiseFailedItem]],
) -> list[PairwiseTranslationUnit]:
    baseline = compare_payload.get("baseline") or {}
    candidate = compare_payload.get("candidate") or {}
    baseline_map = _sentence_map(baseline)
    candidate_map = _sentence_map(candidate)
    baseline_outputs = {
        str(raw.get("sentence_id") or ""): str(raw.get("translation_zh") or "")
        for raw in (baseline.get("node_output") or {}).get("sentence_translations") or []
    }
    candidate_outputs = {
        str(raw.get("sentence_id") or ""): str(raw.get("translation_zh") or "")
        for raw in (candidate.get("node_output") or {}).get("sentence_translations") or []
    }
    sentence_ids = [sid for sid in list(baseline_outputs.keys()) + list(candidate_outputs.keys()) if sid]
    ordered: list[str] = []
    for sid in failed_by_sentence:
        if sid != "global" and sid not in ordered:
            ordered.append(sid)
    for sid in sentence_ids:
        if sid not in ordered:
            ordered.append(sid)
    if len(ordered) > max_units:
        half = max_units // 2
        ordered = ordered[:half] + ordered[-(max_units - half) :]
    units: list[PairwiseTranslationUnit] = []
    for sentence_id in ordered:
        source_sentence = baseline_map.get(sentence_id) or candidate_map.get(sentence_id) or ""
        if not source_sentence:
            continue
        units.append(
            PairwiseTranslationUnit(
                sentence_id=sentence_id,
                source_sentence=source_sentence,
                baseline_translation=baseline_outputs.get(sentence_id) or None,
                candidate_translation=candidate_outputs.get(sentence_id) or None,
                rubric_watchouts=_summarize_watchouts(
                    failed_by_sentence.get(sentence_id, []) or failed_by_sentence.get("global", [])
                ),
            )
        )
    return units


def _overall_watchouts(
    rubric_result: NodeLabRubricScoringResult,
    failed_items: list[PairwiseFailedItem],
) -> list[str]:
    watchouts = [
        f"baseline 通过 {rubric_result.baseline.aggregate.passed} / {rubric_result.baseline.aggregate.criteria_count}",
        f"candidate 通过 {rubric_result.candidate.aggregate.passed} / {rubric_result.candidate.aggregate.criteria_count}",
    ]
    if failed_items:
        watchouts.append(f"共有 {len(failed_items)} 条局部风险，请重点关注代表性失败项。")
    return watchouts


def build_pairwise_packet(
    *,
    compare_payload: dict[str, Any],
    preset: JudgePreset,
    context: ResolvedJudgeContext,
    reading_goal: str,
    reading_variant: str,
    rubric_result: NodeLabRubricScoringResult,
) -> PairwisePacket:
    failed_by_sentence, all_failed = _collect_failed_items_by_sentence(rubric_result)
    aggregate = {
        "baseline": rubric_result.baseline.aggregate.model_dump(mode="json"),
        "candidate": rubric_result.candidate.aggregate.model_dump(mode="json"),
    }
    packet = PairwisePacket(
        node_name=preset.node_name,
        strategy=preset.strategy,
        method=preset.method,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        context=context,
        aggregate=aggregate,
        watchouts=_overall_watchouts(rubric_result, all_failed),
        failed_items=all_failed[:8],
        question=preset.pairwise.question if preset.pairwise and preset.pairwise.question else "请给出整体对比评估意见。",
    )
    if preset.node_name == "translation":
        packet.translation_units = _translation_units_from_compare(
            compare_payload,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            max_units=min(preset.packet_policy.max_sentences_per_side or 8, 6),
            failed_by_sentence=failed_by_sentence,
        )
    else:
        packet.sentence_units = _select_pairwise_sentence_units(
            compare_payload=compare_payload,
            node_name=preset.node_name,
            failed_by_sentence=failed_by_sentence,
            max_units=min(max((preset.packet_policy.max_items_per_side or 6) // 2, 3), 5),
        )
    return packet


def build_probe_packet(
    *,
    compare_payload: dict[str, Any],
    preset: JudgePreset,
    context: ResolvedJudgeContext,
    reading_goal: str,
    reading_variant: str,
) -> ProbePacket:
    baseline_side = _build_grammar_side("baseline", compare_payload.get("baseline") or {}, preset)
    candidate_side = _build_grammar_side("candidate", compare_payload.get("candidate") or {}, preset)
    questions = (preset.probe_appendix or {}).get("questions") or []
    return ProbePacket(
        node_name="grammar",
        strategy="grammar_item_review",
        method="anti_template_probe",
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        context=context,
        baseline_items=baseline_side.items,
        candidate_items=candidate_side.items,
        questions=[ProbeQuestionSpec.model_validate(question) for question in questions],
    )
