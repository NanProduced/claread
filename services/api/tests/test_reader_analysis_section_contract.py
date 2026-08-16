"""Product analysis-section planner and DTO contract.

Public seams:
    - plan_analysis_sections(base_id, units)
    - ReaderAnalysisProgress / ReaderAnalysisSectionRequest DTOs

No DB, jobs, routes, snapshot projection, or provider calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.llm.call_guard import pop_blocked_real_llm_attempts
from app.schemas.reader_orchestration import (
    ReaderAnalysisProgress,
    ReaderAnalysisSectionProgress,
    ReaderAnalysisSectionRequest,
    ReaderAnalysisSectionRequestResponse,
)
from app.services.reader_orchestration import analysis_section_plan
from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
    AnalysisSectionUnit,
    plan_analysis_sections,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT,
    TRANSLATION_WINDOW_TARGET_CHAR_COUNT,
    TranslationWindowUnit,
    plan_translation_windows,
)
from app.services.reader_orchestration.translation_window_plan import (
    plan_translation_windows as extracted_plan_translation_windows,
)

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

_BASE_ID = "base-frozen-1"
_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _unit(unit_id: str, order_index: int, text_length: int) -> AnalysisSectionUnit:
    return AnalysisSectionUnit(
        unit_id=unit_id,
        order_index=order_index,
        text_length=text_length,
    )


def _expected_section_id(base_id: str, unit_ids: list[str]) -> str:
    payload = json.dumps(
        {
            "base_id": base_id,
            "plan_version": "reader_analysis_sections_v1",
            "unit_ids": unit_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ras1_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _section_progress(
    *,
    section_id: str = "ras1_a",
    order_index: int = 0,
    label: str = "第 1 部分",
    status: str = "not_started",
    can_start: bool = True,
) -> ReaderAnalysisSectionProgress:
    return ReaderAnalysisSectionProgress(
        section_id=section_id,
        order_index=order_index,
        label=label,
        excerpt="Once upon a time",
        start_unit_id="u1",
        end_unit_id="u2",
        status=status,
        vocabulary_status=status,
        grammar_status=status,
        can_start=can_start,
        updated_at=_NOW,
        failure_code=None,
    )


def test_empty_units_yield_empty_plan() -> None:
    assert plan_analysis_sections(_BASE_ID, []) == []


def test_single_section_covers_one_unit() -> None:
    units = [_unit("u1", 0, 500)]
    sections = plan_analysis_sections(_BASE_ID, units)
    assert len(sections) == 1
    section = sections[0]
    assert section.order_index == 0
    assert section.label == "第 1 部分"
    assert section.start_unit_id == "u1"
    assert section.end_unit_id == "u1"
    assert section.target_unit_ids == ("u1",)
    assert section.total_utf16_length == 500
    assert section.section_id == _expected_section_id(_BASE_ID, ["u1"])
    assert ANALYSIS_SECTION_PLAN_VERSION == "reader_analysis_sections_v1"


def test_unordered_input_emits_stable_order_index_sequence() -> None:
    units = [
        _unit("u3", 2, 100),
        _unit("u1", 0, 100),
        _unit("u2", 1, 100),
    ]
    sections = plan_analysis_sections(_BASE_ID, units)
    assert len(sections) == 1
    assert sections[0].target_unit_ids == ("u1", "u2", "u3")
    assert sections[0].start_unit_id == "u1"
    assert sections[0].end_unit_id == "u3"
    assert [section.order_index for section in sections] == [0]


def test_target_boundary_closes_at_6000() -> None:
    units = [
        _unit("a", 0, 3000),
        _unit("b", 1, 3000),
        _unit("c", 2, 100),
    ]
    sections = plan_analysis_sections(_BASE_ID, units)
    assert TRANSLATION_WINDOW_TARGET_CHAR_COUNT == 6000
    assert [section.target_unit_ids for section in sections] == [
        ("a", "b"),
        ("c",),
    ]
    assert sections[0].total_utf16_length == 6000
    assert sections[1].label == "第 2 部分"


def test_safety_max_boundary_does_not_exceed_10000() -> None:
    units = [
        _unit("a", 0, 6000),
        _unit("b", 1, 4001),
    ]
    sections = plan_analysis_sections(_BASE_ID, units)
    assert TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT == 10000
    assert [section.target_unit_ids for section in sections] == [("a",), ("b",)]


def test_oversized_unit_is_its_own_section() -> None:
    units = [
        _unit("small1", 0, 2000),
        _unit("huge", 1, 12000),
        _unit("small2", 2, 2000),
    ]
    sections = plan_analysis_sections(_BASE_ID, units)
    assert [section.target_unit_ids for section in sections] == [
        ("small1",),
        ("huge",),
        ("small2",),
    ]
    assert sections[1].total_utf16_length == 12000


def test_full_coverage_without_overlap() -> None:
    units = [_unit(f"u{i}", i, 2500) for i in range(8)]
    sections = plan_analysis_sections(_BASE_ID, units)
    planned_ids = [unit_id for section in sections for unit_id in section.target_unit_ids]
    assert planned_ids == [f"u{i}" for i in range(8)]
    assert len(planned_ids) == len(set(planned_ids))
    windows = plan_translation_windows(
        [
            TranslationWindowUnit(
                unit_id=unit.unit_id,
                order_index=unit.order_index,
                text_length=unit.text_length,
            )
            for unit in units
        ]
    )
    assert [section.target_unit_ids for section in sections] == [
        window.target_unit_ids for window in windows
    ]


def test_replay_produces_identical_section_ids() -> None:
    units = [_unit("u1", 0, 4000), _unit("u2", 1, 4000), _unit("u3", 2, 4000)]
    first = plan_analysis_sections(_BASE_ID, units)
    second = plan_analysis_sections(_BASE_ID, list(reversed(units)))
    assert [section.section_id for section in first] == [
        section.section_id for section in second
    ]
    assert first[0].section_id == _expected_section_id(_BASE_ID, ["u1", "u2"])
    assert first[1].section_id == _expected_section_id(_BASE_ID, ["u3"])


def test_section_id_changes_when_base_or_membership_changes() -> None:
    units = [_unit("u1", 0, 100), _unit("u2", 1, 100)]
    original = plan_analysis_sections(_BASE_ID, units)[0].section_id
    other_base = plan_analysis_sections("base-other", units)[0].section_id
    other_membership = plan_analysis_sections(
        _BASE_ID,
        [_unit("u1", 0, 100), _unit("u9", 1, 100)],
    )[0].section_id
    assert original != other_base
    assert original != other_membership
    assert original == _expected_section_id(_BASE_ID, ["u1", "u2"])


@pytest.mark.parametrize(
    "units",
    [
        [_unit("", 0, 10)],
        [_unit("u1", 0, 10), _unit("u1", 1, 10)],
        [_unit("u1", 0, 10), _unit("u2", 0, 10)],
        [_unit("u1", 0, 0)],
        [_unit("u1", 0, -3)],
    ],
)
def test_invalid_units_fail_closed(units: list[AnalysisSectionUnit]) -> None:
    with pytest.raises(ValueError):
        plan_analysis_sections(_BASE_ID, units)


def test_empty_base_id_fails_closed() -> None:
    with pytest.raises(ValueError):
        plan_analysis_sections("", [_unit("u1", 0, 10)])


def test_analysis_section_plan_reuses_translation_planner() -> None:
    assert (
        analysis_section_plan.plan_translation_windows
        is extracted_plan_translation_windows
        is plan_translation_windows
    )


def test_dto_enums_and_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="short_batch",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="completed",
            active_phase=None,
            translation_status="completed",
            completed_section_count=0,
            total_section_count=0,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=[],
        )
    valid = _section_progress()
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionProgress.model_validate(
            {**valid.model_dump(), "status": "succeeded"}
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequest.model_validate(
            {"scope": "single", "section_id": "ras1_a", "estimate": 12}
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequestResponse.model_validate(
            {
                "outcome": "started",
                "accepted_section_ids": ["ras1_a"],
                "event_sequence": 1,
                "reason_code": None,
                "prompt": "nope",
            }
        )


def test_section_request_single_requires_section_id() -> None:
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequest(scope="single", section_id=None)
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequest(scope="single", section_id="")
    request = ReaderAnalysisSectionRequest(scope="single", section_id="ras1_a")
    assert request.section_id == "ras1_a"


def test_section_request_remaining_rejects_section_id() -> None:
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequest(scope="remaining", section_id="ras1_a")
    with pytest.raises(ValidationError):
        ReaderAnalysisSectionRequest(scope="remaining", section_id="")
    request = ReaderAnalysisSectionRequest(scope="remaining", section_id=None)
    assert request.section_id is None


def test_analysis_progress_count_uniqueness_order_and_active_section() -> None:
    sections = [
        _section_progress(section_id="ras1_a", order_index=0, label="第 1 部分"),
        _section_progress(section_id="ras1_b", order_index=1, label="第 2 部分"),
    ]
    progress = ReaderAnalysisProgress(
        mode="segmented_on_demand",
        plan_version=ANALYSIS_SECTION_PLAN_VERSION,
        overall_status="waiting_user",
        active_phase=None,
        translation_status="completed",
        completed_section_count=1,
        total_section_count=2,
        active_section_id="ras1_b",
        needs_user_action=True,
        last_progress_at=_NOW,
        sections=sections,
    )
    assert progress.total_section_count == 2

    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="automatic",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="completed",
            active_phase=None,
            translation_status="completed",
            completed_section_count=3,
            total_section_count=2,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=sections,
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="automatic",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="completed",
            active_phase=None,
            translation_status="completed",
            completed_section_count=1,
            total_section_count=1,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=sections,
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="automatic",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="processing",
            active_phase="analysis",
            translation_status="completed",
            completed_section_count=0,
            total_section_count=2,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=[
                _section_progress(section_id="ras1_a", order_index=0),
                _section_progress(section_id="ras1_a", order_index=1),
            ],
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="automatic",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="processing",
            active_phase="translation",
            translation_status="processing",
            completed_section_count=0,
            total_section_count=2,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=[
                _section_progress(section_id="ras1_a", order_index=0),
                _section_progress(section_id="ras1_b", order_index=0),
            ],
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="automatic",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="processing",
            active_phase="analysis",
            translation_status="completed",
            completed_section_count=0,
            total_section_count=2,
            active_section_id=None,
            needs_user_action=False,
            last_progress_at=None,
            sections=list(reversed(sections)),
        )
    with pytest.raises(ValidationError):
        ReaderAnalysisProgress(
            mode="segmented_on_demand",
            plan_version=ANALYSIS_SECTION_PLAN_VERSION,
            overall_status="processing",
            active_phase="analysis",
            translation_status="completed",
            completed_section_count=0,
            total_section_count=2,
            active_section_id="ras1_missing",
            needs_user_action=False,
            last_progress_at=None,
            sections=sections,
        )


def test_planner_makes_zero_provider_attempts() -> None:
    plan_analysis_sections(_BASE_ID, [_unit("u1", 0, 80)])
    assert pop_blocked_real_llm_attempts() == []
