"""Repair candidate builder and merge helper.

从 drop_log 构建 RepairPatchRequest，并将 RepairPatchResult 合并回
NormalizedAnnotationResult。Merge 跑完整 normalized postprocess
（dedup → conflict resolution → density control）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import (
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
)
from app.schemas.internal.normalized import (
    DropLogEntry,
    NormalizedAnnotation,
    NormalizedAnnotationResult,
)
from app.schemas.internal.repair import (
    RepairPatchRequest,
    RepairPatchResult,
    RepairTarget,
)
from app.services.analysis.postprocess.draft_to_normalized import (
    draft_to_normalized_annotation,
)
from app.services.analysis.postprocess.normalized_postprocess import (
    build_canonical_stats,
    postprocess_normalized_annotations,
)
from app.services.analysis.postprocess.repair_policy import is_repair_worthy_drop

# ── Repair merge result types ─────────────────────────────────────


@dataclass
class RepairMergeStats:
    """Repair merge 统计信息。"""

    target_count: int
    patched_count: int
    delete_count: int
    invalid_patch_count: int
    postprocess_drop_count: int


@dataclass
class RepairMergeResult:
    """Repair merge 返回值：更新后的结果 + 统计。"""

    result: NormalizedAnnotationResult
    stats: RepairMergeStats


@dataclass
class RepairPatchBuildStats:
    """Repair patch request 构建统计信息。"""

    repair_worthy_count: int
    missing_sentence_count: int
    selected_target_count: int


@dataclass
class RepairPatchBuildResult:
    """Repair patch request 构建返回值：请求 + 统计。

    request 为 None 表示无可用 target（全部 missing sentence 或无 repair-worthy drop），
    但 stats 始终可观测，便于区分"无 repair-worthy"和"有 repair-worthy 但全缺句子"。
    """

    request: RepairPatchRequest | None
    stats: RepairPatchBuildStats


# ── Draft payload matching ─────────────────────────────────────────


def _match_draft_payload(
    target: RepairTarget,
    vocabulary_draft: VocabularyDraft | None,
    grammar_draft: GrammarDraft | None,
) -> dict | None:
    """根据 target 的 source_agent 和 annotation_type 匹配 draft item。"""
    agent = target.source_agent
    atype = target.annotation_type
    sid = target.sentence_id
    anchor = target.anchor_text

    if agent == "vocabulary" and atype == "vocab_highlight":
        if vocabulary_draft is None:
            return None
        for item in vocabulary_draft.vocab_highlights:
            if item.sentence_id == sid and item.text == anchor:
                return item.model_dump()

    elif agent == "vocabulary" and atype == "phrase_gloss":
        if vocabulary_draft is None:
            return None
        for item in vocabulary_draft.phrase_glosses:
            if item.sentence_id != sid:
                continue
            if any(q.text == anchor for q in item.anchor_quotes):
                return item.model_dump()

    elif agent == "vocabulary" and atype == "context_gloss":
        if vocabulary_draft is None:
            return None
        for item in vocabulary_draft.context_glosses:
            if item.sentence_id != sid:
                continue
            if any(q.text == anchor for q in item.anchor_quotes):
                return item.model_dump()

    elif agent == "grammar" and atype == "grammar_note":
        if grammar_draft is None:
            return None
        for item in grammar_draft.grammar_notes:
            if item.sentence_id != sid:
                continue
            if any(q.text == anchor for q in item.anchor_quotes):
                return item.model_dump()

    elif agent == "grammar" and atype == "sentence_analysis":
        if grammar_draft is None:
            return None
        for item in grammar_draft.sentence_analyses:
            if item.sentence_id == sid:
                return item.model_dump()

    return None


# ── build_repair_patch_request ──────────────────────────────────────


def build_repair_patch_request(
    drop_log: list[DropLogEntry],
    sentences: list[PreparedSentence],
    *,
    vocabulary_draft: VocabularyDraft | None = None,
    grammar_draft: GrammarDraft | None = None,
    translation_draft: TranslationDraft | None = None,
    canonical_drop_log: list[DropLogEntry] | None = None,
    max_targets: int = 8,
) -> RepairPatchRequest | None:
    """从 drop_log 构建 RepairPatchRequest。

    合并 drop_log 和 canonical_drop_log，过滤出 repair-worthy 条目，
    匹配 draft payload，返回紧凑的 repair 请求。
    无需修复时返回 None。

    Design decision: 如果 drop target 的 sentence_id 不在 sentences 中，
    直接跳过该 target，避免 LLM 在没有 sentence text 时盲修。
    missing sentence 过滤在 max_targets 截断之前，避免有效 target 被饿死。
    """
    result = build_repair_patch_request_with_stats(
        drop_log,
        sentences,
        vocabulary_draft=vocabulary_draft,
        grammar_draft=grammar_draft,
        translation_draft=translation_draft,
        canonical_drop_log=canonical_drop_log,
        max_targets=max_targets,
    )
    if result.request is None:
        return None
    return result.request


def build_repair_patch_request_with_stats(
    drop_log: list[DropLogEntry],
    sentences: list[PreparedSentence],
    *,
    vocabulary_draft: VocabularyDraft | None = None,
    grammar_draft: GrammarDraft | None = None,
    translation_draft: TranslationDraft | None = None,
    canonical_drop_log: list[DropLogEntry] | None = None,
    max_targets: int = 8,
) -> RepairPatchBuildResult:
    """从 drop_log 构建 RepairPatchRequest，附带构建统计。

    与 build_repair_patch_request 相同逻辑，但始终返回 RepairPatchBuildResult，
    request 可为 None（无可用 target），stats 始终可观测。
    """
    sentence_map = {s.sentence_id: s for s in sentences}

    # 1. 合并两个 drop_log，标记 is_canonical
    combined: list[tuple[DropLogEntry, bool]] = []
    for entry in drop_log:
        combined.append((entry, False))
    if canonical_drop_log:
        for entry in canonical_drop_log:
            combined.append((entry, True))

    # 2. 过滤 repair-worthy 条目
    worthy = [
        (entry, is_canonical)
        for entry, is_canonical in combined
        if is_repair_worthy_drop(entry)
    ]
    repair_worthy_count = len(worthy)

    # 3. 先过滤 missing sentence，再截断到 max_targets
    #    （避免有效 target 被缺失句子饿死）
    with_sentence: list[tuple[DropLogEntry, bool]] = []
    missing_sentence_count = 0
    for entry, is_canonical in worthy:
        if entry.sentence_id not in sentence_map:
            missing_sentence_count += 1
        else:
            with_sentence.append((entry, is_canonical))

    selected = with_sentence[:max_targets]

    # 4. 无目标则返回 request=None，但 stats 始终可观测
    if not selected:
        return RepairPatchBuildResult(
            request=None,
            stats=RepairPatchBuildStats(
                repair_worthy_count=repair_worthy_count,
                missing_sentence_count=missing_sentence_count,
                selected_target_count=0,
            ),
        )

    # 5. 为每个 target 匹配 draft_payload
    targets: list[RepairTarget] = []
    seen_sentence_ids: set[str] = set()
    for entry, is_canonical in selected:
        target = RepairTarget(
            source_agent=entry.source_agent,
            annotation_type=entry.annotation_type,
            sentence_id=entry.sentence_id,
            anchor_text=entry.anchor_text,
            drop_reason=entry.drop_reason,
            drop_stage=entry.drop_stage,
            is_canonical=is_canonical,
            draft_payload=_match_draft_payload(
                RepairTarget(
                    source_agent=entry.source_agent,
                    annotation_type=entry.annotation_type,
                    sentence_id=entry.sentence_id,
                    anchor_text=entry.anchor_text,
                    drop_reason=entry.drop_reason,
                    drop_stage=entry.drop_stage,
                    is_canonical=is_canonical,
                ),
                vocabulary_draft,
                grammar_draft,
            ),
        )
        targets.append(target)
        seen_sentence_ids.add(entry.sentence_id)

    # 6. 收集受影响句子
    affected_sentences = [
        {"sentence_id": sid, "text": sentence_map[sid].text}
        for sid in sorted(seen_sentence_ids)
        if sid in sentence_map
    ]

    # 7. 返回请求 + 统计
    return RepairPatchBuildResult(
        request=RepairPatchRequest(
            sentences=affected_sentences, targets=targets,
        ),
        stats=RepairPatchBuildStats(
            repair_worthy_count=repair_worthy_count,
            missing_sentence_count=missing_sentence_count,
            selected_target_count=len(targets),
        ),
    )


# ── apply_repair_patches_to_normalized_result ───────────────────────


def apply_repair_patches_to_normalized_result(
    result: NormalizedAnnotationResult,
    patch_result: RepairPatchResult,
    patch_request: RepairPatchRequest,
    sentences: list[PreparedSentence],
    annotation_density: int = 3,
) -> RepairMergeResult:
    """将 RepairPatchResult 合并回 NormalizedAnnotationResult。

    处理 replace 和 delete 两种 action，然后跑完整 normalized postprocess
    （dedup → conflict resolution → density control），
    确保 repair patch 不会绕过安全规则。

    返回 RepairMergeResult 包含更新后的结果和统计信息。
    """
    sentence_map = {s.sentence_id: s for s in sentences}

    # ── Convert patches to annotations ────────────────────────────────
    new_annotations: list[NormalizedAnnotation] = []
    repair_drop_log: list[DropLogEntry] = []
    patched_count = 0
    delete_count = 0
    invalid_patch_count = 0

    for patch in patch_result.patches:
        # Fail-closed: skip out-of-range target_index
        if patch.target_index >= len(patch_request.targets):
            invalid_patch_count += 1
            repair_drop_log.append(
                DropLogEntry.model_construct(
                    source_agent="vocabulary",
                    annotation_type="unknown",
                    sentence_id="",
                    anchor_text="",
                    drop_reason="repair_invalid_target_index",
                    drop_stage="repair",
                )
            )
            continue

        target = patch_request.targets[patch.target_index]

        if patch.action == "delete":
            delete_count += 1
            repair_drop_log.append(
                DropLogEntry.model_construct(
                    source_agent=target.source_agent,
                    annotation_type=target.annotation_type,
                    sentence_id=target.sentence_id,
                    anchor_text=target.anchor_text,
                    drop_reason="repair_deleted",
                    drop_stage="repair",
                )
            )
            continue

        # action == "replace" (validator guarantees annotation is not None)
        if patch.annotation is None:
            # Defensive: should not happen due to model_validator
            invalid_patch_count += 1
            repair_drop_log.append(
                DropLogEntry.model_construct(
                    source_agent=target.source_agent,
                    annotation_type=target.annotation_type,
                    sentence_id=target.sentence_id,
                    anchor_text=target.anchor_text,
                    drop_reason="repair_malformed_patch",
                    drop_stage="repair",
                )
            )
            continue

        normalized = draft_to_normalized_annotation(
            patch.annotation,
            sentence_map,
            repair_drop_log,
            source_agent=target.source_agent,
        )
        if normalized is None:
            # drop 已由 draft_to_normalized_annotation 写入 repair_drop_log
            continue

        new_annotations.append(normalized)
        patched_count += 1

    # ── Full postprocess ──────────────────────────────────────────────
    # Merge existing + new, then run dedup → conflict → density
    pre_count = len(result.normalized_annotations) + len(new_annotations)
    candidates = list(result.normalized_annotations) + new_annotations
    postprocess_drop_log: list[DropLogEntry] = []

    final_annotations = postprocess_normalized_annotations(
        candidates, postprocess_drop_log, annotation_density,
    )
    postprocess_drop_count = pre_count - len(final_annotations)

    # ── Build result ──────────────────────────────────────────────────
    all_drop_log = (
        (result.canonical_drop_log or [])
        + repair_drop_log
        + postprocess_drop_log
    )
    canonical_stats = build_canonical_stats(final_annotations, all_drop_log)

    updated_result = NormalizedAnnotationResult.model_construct(
        annotations=result.annotations,
        normalized_annotations=final_annotations,
        sentence_translations=result.sentence_translations,
        drop_log=result.drop_log,
        canonical_stats=canonical_stats,
        canonical_drop_log=all_drop_log,
    )

    # Count missing sentence targets (those skipped in build phase)
    # Note: this is only non-zero if patch_request was manually constructed
    # with targets whose sentence_id is not in sentence_map.
    # For normal flow, use build_repair_patch_request_with_stats() instead.

    return RepairMergeResult(
        result=updated_result,
        stats=RepairMergeStats(
            target_count=len(patch_request.targets),
            patched_count=patched_count,
            delete_count=delete_count,
            invalid_patch_count=invalid_patch_count,
            postprocess_drop_count=postprocess_drop_count,
        ),
    )
