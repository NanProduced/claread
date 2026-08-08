"""Interface-level tests for the stable annotation analysis module.

Only the public interface is exercised: analyze_stable_annotations in,
(accepted_annotations, diagnostics, policy_overrides) out.
"""

from __future__ import annotations

from app.services.reader_orchestration.stable_annotation_analysis import (
    ANNOTATION_CONFLICTING_DUPLICATE,
    ANNOTATION_DUPLICATE_CONSISTENT,
    ANNOTATION_INLINE_MARK_INVALID,
    ANNOTATION_MULTI_UNIT_OVERLAP,
    ANNOTATION_RANGE_EMPTY,
    ANNOTATION_RANGE_MISMATCH,
    ANNOTATION_RANGE_NON_INTEGER,
    ANNOTATION_RANGE_OUT_OF_BOUNDS,
    DIAGNOSTICS_VERSION,
    StableAnnotationAnalysis,
    StableBlockAnnotation,
    StableUnitRange,
    analyze_stable_annotations,
    empty_diagnostics_payload,
)

UNITS = [
    StableUnitRange(unit_id="u1", start_utf16=0, end_utf16=10),
    StableUnitRange(unit_id="u2", start_utf16=12, end_utf16=24),
    StableUnitRange(unit_id="u3", start_utf16=26, end_utf16=40),
]
BASE_LENGTH = 42


def ann(
    start: object,
    end: object,
    *,
    block_type: str = "paragraph",
    block_id: str = "b1",
    payload: dict | None = None,
) -> StableBlockAnnotation:
    return StableBlockAnnotation(
        start_utf16=start,  # type: ignore[arg-type]
        end_utf16=end,  # type: ignore[arg-type]
        block_type=block_type,
        block_id=block_id,
        payload_json=payload or {},
    )


def analyze(annotations, *, units=UNITS, base_length=BASE_LENGTH):
    return analyze_stable_annotations(
        raw_annotations=annotations,
        base_utf16_length=base_length,
        unit_ranges=units,
    )


def codes(result: StableAnnotationAnalysis) -> list[str]:
    return [item.code for item in result.diagnostics]


class TestJudgmentTable:
    def test_exact_match_accepts_with_no_diagnostic(self):
        result = analyze([ann(0, 10), ann(12, 24, block_id="b2")])
        assert [a.annotation.block_id for a in result.accepted_annotations] == ["b1", "b2"]
        assert result.diagnostics == ()
        assert result.policy_overrides == ()

    def test_non_integer_range_excluded_diagnostic_only(self):
        result = analyze([ann(0.5, 10), ann(True, 10, block_id="b2")])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_NON_INTEGER] * 2
        assert result.policy_overrides == ()

    def test_empty_or_reversed_range_excluded_without_endpoint_swap(self):
        result = analyze([ann(10, 10), ann(24, 12, block_id="b2")])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_EMPTY] * 2
        assert result.policy_overrides == ()

    def test_partially_out_of_base_overriding_overlapped_units(self):
        # Overlaps u2 (12-24) and u3 (26-40) when clipped to base length.
        result = analyze([ann(20, 99)])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_OUT_OF_BOUNDS]
        assert [(o.unit_id, o.reason_code) for o in result.policy_overrides] == [
            ("u2", ANNOTATION_RANGE_OUT_OF_BOUNDS),
            ("u3", ANNOTATION_RANGE_OUT_OF_BOUNDS),
        ]

    def test_fully_outside_base_is_diagnostic_only(self):
        result = analyze([ann(50, 60)])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_OUT_OF_BOUNDS]
        assert result.policy_overrides == ()

    def test_clipping_never_fabricates_a_new_annotation(self):
        # Range extends left of the base; clip affects only attribution.
        result = analyze([ann(-5, 8)])
        assert result.accepted_annotations == ()
        assert [(o.unit_id, o.reason_code) for o in result.policy_overrides] == [
            ("u1", ANNOTATION_RANGE_OUT_OF_BOUNDS),
        ]

    def test_multi_unit_overlap_overrides_every_overlapped_unit(self):
        result = analyze([ann(5, 30)])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_MULTI_UNIT_OVERLAP]
        assert [o.unit_id for o in result.policy_overrides] == ["u1", "u2", "u3"]
        assert {o.reason_code for o in result.policy_overrides} == {
            ANNOTATION_MULTI_UNIT_OVERLAP
        }

    def test_single_overlap_without_exact_match_is_range_mismatch(self):
        result = analyze([ann(2, 8)])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_MISMATCH]
        assert [(o.unit_id, o.reason_code) for o in result.policy_overrides] == [
            ("u1", ANNOTATION_RANGE_MISMATCH),
        ]

    def test_no_overlap_is_diagnostic_only(self):
        result = analyze([ann(10, 12)])
        assert result.accepted_annotations == ()
        assert codes(result) == [ANNOTATION_RANGE_MISMATCH]
        assert result.policy_overrides == ()

    def test_consistent_duplicate_accepts_first_and_diagnoses(self):
        first = ann(0, 10, block_id="b1")
        same = ann(0, 10, block_id="b1")
        result = analyze([first, same])
        assert [a.annotation.block_id for a in result.accepted_annotations] == ["b1"]
        assert codes(result) == [ANNOTATION_DUPLICATE_CONSISTENT]
        assert result.policy_overrides == ()

    def test_conflicting_duplicate_accepts_first_and_overrides_unit(self):
        first = ann(0, 10, block_id="b1")
        conflict = ann(0, 10, block_id="bX", block_type="heading")
        result = analyze([first, conflict])
        assert [a.annotation.block_id for a in result.accepted_annotations] == ["b1"]
        assert codes(result) == [ANNOTATION_CONFLICTING_DUPLICATE]
        assert [(o.unit_id, o.reason_code) for o in result.policy_overrides] == [
            ("u1", ANNOTATION_CONFLICTING_DUPLICATE),
        ]

    def test_primary_reason_precedence_is_frozen(self):
        # One unit hit by conflicting duplicate + out-of-bounds + mismatch:
        # the surviving record carries the conflicting-duplicate reason.
        result = analyze(
            [
                ann(0, 10, block_id="b1"),
                ann(0, 10, block_id="bX", block_type="heading"),
                ann(-3, 5, block_id="b2"),
                ann(2, 9, block_id="b3"),
            ]
        )
        assert [(o.unit_id, o.reason_code) for o in result.policy_overrides] == [
            ("u1", ANNOTATION_CONFLICTING_DUPLICATE),
        ]

    def test_deterministic_canonical_ordering(self):
        first = analyze(
            [ann(2, 8, block_id="z9"), ann(50, 60, block_id="a1"), ann(0, 10)]
        )
        second = analyze(
            [ann(0, 10), ann(50, 60, block_id="a1"), ann(2, 8, block_id="z9")]
        )
        assert first.diagnostics == second.diagnostics
        assert first.policy_overrides == second.policy_overrides


class TestInlineMarkValidation:
    def test_valid_marks_survive_and_nesting_overlap_is_legal(self):
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "strong", "start": 0, "end": 6},
                            {"type": "em", "start": 2, "end": 4},
                        ]
                    },
                )
            ]
        )
        assert result.diagnostics == ()
        marks = result.accepted_annotations[0].inline_marks
        assert marks == (
            {"type": "strong", "start": 0, "end": 6},
            {"type": "em", "start": 2, "end": 4},
        )

    def test_nested_strong_and_em_both_survive(self):
        # Red-line regression: overlapping marks must never be dropped.
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "strong", "start": 1, "end": 9},
                            {"type": "em", "start": 3, "end": 5},
                            {"type": "strong", "start": 4, "end": 8},
                        ]
                    },
                )
            ]
        )
        assert len(result.accepted_annotations[0].inline_marks) == 3
        assert result.diagnostics == ()

    def test_exact_duplicates_dedupe_deterministically(self):
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "strong", "start": 1, "end": 4},
                            {"type": "strong", "start": 1, "end": 4},
                        ]
                    },
                )
            ]
        )
        assert len(result.accepted_annotations[0].inline_marks) == 1
        assert result.diagnostics == ()

    def test_same_range_different_href_is_not_a_duplicate(self):
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "link", "start": 1, "end": 4, "href": "https://a.example"},
                            {"type": "link", "start": 1, "end": 4, "href": "https://b.example"},
                        ]
                    },
                )
            ]
        )
        assert len(result.accepted_annotations[0].inline_marks) == 2

    def test_invalid_marks_drop_with_diagnostic_but_annotation_survives(self):
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "unknown", "start": 0, "end": 2},
                            {"type": "strong", "start": True, "end": 4},
                            {"type": "strong", "start": 4, "end": 4},
                            {"type": "strong", "start": 8, "end": 99},
                            {"type": "strong", "start": 0, "end": 3, "extra": 1},
                            {"type": "strong", "start": 0, "end": 3, "href": "https://x.example"},
                            {"type": "link", "start": 0, "end": 3},
                            {"type": "link", "start": 0, "end": 3, "href": "javascript:alert(1)"},
                            {"type": "strong", "start": 0, "end": 5},
                            "not-a-dict",
                        ]
                    },
                )
            ]
        )
        accepted = result.accepted_annotations
        assert len(accepted) == 1
        assert accepted[0].inline_marks == ({"type": "strong", "start": 0, "end": 5},)
        assert codes(result).count(ANNOTATION_INLINE_MARK_INVALID) == 9
        # Mark corruption never triggers a unit all-off.
        assert result.policy_overrides == ()

    def test_link_href_rules(self):
        result = analyze(
            [
                ann(
                    0,
                    10,
                    payload={
                        "inline_marks": [
                            {"type": "link", "start": 0, "end": 2, "href": "https://ok.example"},
                            {"type": "link", "start": 2, "end": 4, "href": "mailto:a@b.example"},
                            {"type": "link", "start": 4, "end": 6, "href": "/relative/path"},
                            {"type": "link", "start": 6, "end": 8, "href": "#fragment"},
                        ]
                    },
                )
            ]
        )
        assert result.diagnostics == ()
        assert len(result.accepted_annotations[0].inline_marks) == 4

    def test_non_list_inline_marks_payload_is_a_diagnostic(self):
        result = analyze([ann(0, 10, payload={"inline_marks": "broken"})])
        assert codes(result) == [ANNOTATION_INLINE_MARK_INVALID]
        assert result.accepted_annotations[0].inline_marks == ()


class TestPayload:
    def test_diagnostics_payload_is_the_versioned_object(self):
        result = analyze([ann(50, 60)])
        payload = result.diagnostics_payload()
        assert payload["version"] == DIAGNOSTICS_VERSION
        assert payload["items"] == [
            {
                "code": ANNOTATION_RANGE_OUT_OF_BOUNDS,
                "severity": "warning",
                "scope": "block",
                "ref_id": "b1",
                "detail": "annotation range exceeds the canonical base",
            }
        ]

    def test_empty_payload_is_versioned_object_not_bare_list(self):
        assert empty_diagnostics_payload() == {
            "version": DIAGNOSTICS_VERSION,
            "items": [],
        }
