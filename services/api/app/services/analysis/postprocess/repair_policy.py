"""Repair trigger policy for normalized annotation drops."""

from __future__ import annotations

from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult

DETERMINISTIC_DROP_REASONS = {
    "duplicate",
    "conflict_resolution",
    "low_value_word",
}


def is_repair_worthy_drop(drop: DropLogEntry) -> bool:
    """Return whether a drop should count toward LLM repair.

    Normalize can safely clean duplicates, density overflow, low-value vocab, and
    deterministic vocabulary conflicts. Those should not spend another LLM call.
    """
    if drop.drop_stage in {"density_control", "deduplication", "conflict_resolution"}:
        return False
    if drop.drop_reason in DETERMINISTIC_DROP_REASONS:
        return False
    if drop.drop_reason.startswith("subsumed_by_"):
        return False
    return True


def repair_worthy_drop_count(drop_log: list[DropLogEntry]) -> int:
    return sum(1 for drop in drop_log if is_repair_worthy_drop(drop))


def deterministic_drop_count(drop_log: list[DropLogEntry]) -> int:
    return sum(1 for drop in drop_log if not is_repair_worthy_drop(drop))


def should_trigger_repair(
    normalized_result: NormalizedAnnotationResult | None,
    *,
    threshold: float,
) -> bool:
    if normalized_result is None:
        return False

    repair_drop_count = repair_worthy_drop_count(normalized_result.drop_log or [])
    annotation_count = len(normalized_result.annotations)

    if annotation_count == 0:
        return repair_drop_count > 0

    failure_ratio = repair_drop_count / (annotation_count + repair_drop_count)
    return failure_ratio > threshold


def should_trigger_patch_repair(
    normalized_result: NormalizedAnnotationResult | None,
    *,
    threshold: float,
) -> bool:
    """Patch repair 触发策略：合并 drop_log + canonical_drop_log，用 normalized_annotations 计数。

    与 should_trigger_repair 的区别：
    - 统计范围：drop_log + canonical_drop_log 中的 repair-worthy drops
    - annotation count：len(normalized_annotations) 而非 len(annotations)
    - 这确保 canonical resolver 失败但旧链路无感时仍能触发 patch repair
    """
    if normalized_result is None:
        return False

    combined_drops = list(normalized_result.drop_log or [])
    combined_drops.extend(normalized_result.canonical_drop_log or [])

    repair_drop_count = repair_worthy_drop_count(combined_drops)
    annotation_count = len(normalized_result.normalized_annotations)

    if annotation_count == 0:
        return repair_drop_count > 0

    failure_ratio = repair_drop_count / (annotation_count + repair_drop_count)
    return failure_ratio > threshold
