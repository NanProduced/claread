"""Deterministic product analysis-section planner.

Reuses :func:`plan_translation_windows`. Does not copy the greedy packer,
touch jobs, or encode ``section_v1`` geometry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.services.reader_orchestration.translation_window_plan import (
    TranslationWindowUnit,
    plan_translation_windows,
)

ANALYSIS_SECTION_PLAN_VERSION = "reader_analysis_sections_v1"
AnalysisSectionUnit = TranslationWindowUnit


@dataclass(frozen=True, slots=True)
class AnalysisSection:
    section_id: str
    order_index: int
    label: str
    start_unit_id: str
    end_unit_id: str
    target_unit_ids: tuple[str, ...]
    total_utf16_length: int


def plan_analysis_sections(
    base_id: str,
    units: list[AnalysisSectionUnit] | tuple[AnalysisSectionUnit, ...],
) -> list[AnalysisSection]:
    """Plan product analysis sections over every unit of an immutable base."""
    if not base_id:
        raise ValueError("base_id must be non-empty")
    _require_valid_units(units)
    windows = plan_translation_windows(units)
    sections: list[AnalysisSection] = []
    for order_index, window in enumerate(windows):
        unit_ids = window.target_unit_ids
        sections.append(
            AnalysisSection(
                section_id=_section_id(base_id, unit_ids),
                order_index=order_index,
                label=f"第 {order_index + 1} 部分",
                start_unit_id=window.units[0].unit_id,
                end_unit_id=window.units[-1].unit_id,
                target_unit_ids=unit_ids,
                total_utf16_length=sum(unit.text_length for unit in window.units),
            )
        )
    return sections


def _require_valid_units(
    units: list[AnalysisSectionUnit] | tuple[AnalysisSectionUnit, ...],
) -> None:
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for unit in units:
        if not unit.unit_id:
            raise ValueError("unit_id must be non-empty")
        if unit.unit_id in seen_ids:
            raise ValueError(f"duplicate unit_id: {unit.unit_id}")
        if unit.order_index in seen_orders:
            raise ValueError(f"duplicate order_index: {unit.order_index}")
        if unit.text_length <= 0:
            raise ValueError("text_length must be positive")
        seen_ids.add(unit.unit_id)
        seen_orders.add(unit.order_index)


def _section_id(base_id: str, unit_ids: tuple[str, ...]) -> str:
    payload = json.dumps(
        {
            "base_id": base_id,
            "plan_version": ANALYSIS_SECTION_PLAN_VERSION,
            "unit_ids": list(unit_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"ras1_{digest}"
