"""Helper script to generate vocabulary seed case JSON files.

Run from `evals/` as:

    uv run python scripts/build_vocabulary_seed.py

The script writes deterministic execution snapshots for each case by
applying the same resolver logic that D5-V3 worker uses. The output is
intended to be checked into the repository and reviewed manually.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claread_eval.graders.vocabulary import (
    _fnv1a32_utf16,
    _utf16_code_units,
)


def make_snapshot(
    *,
    items: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    fail_closed: bool = False,
    fail_closed_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "output": {"schema_version": 1, "items": items},
        "diagnostics": diagnostics,
        "fail_closed": fail_closed,
        "fail_closed_reason": fail_closed_reason,
    }


def write_case(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def basic_unit_offsets(unit_text: str, sentence_id: str, text: str) -> tuple[int, int, str]:
    idx = unit_text.find(text)
    if idx < 0:
        raise ValueError(f"text={text!r} not found in unit_text={unit_text!r}")
    start_utf16 = len(unit_text[:idx].encode("utf-16-le", "surrogatepass")) // 2
    end_utf16 = start_utf16 + _utf16_code_units(text)
    return start_utf16, end_utf16, text


def make_resolved(
    *,
    item_type: str,
    anchor_segment_id: str,
    start: int,
    end: int,
    selected_text: str,
) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "anchor_segment_id": anchor_segment_id,
        "unit_start_utf16": start,
        "unit_end_utf16": end,
        "selected_text": selected_text,
        "text_hash": _fnv1a32_utf16(selected_text),
    }


def diagnostics_for(*, candidate_count: int, skipped: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = max(0, len(skipped) - 8)
    return {
        "candidate_item_count": candidate_count,
        "resolved_item_count": max(0, candidate_count - len(skipped)),
        "skipped_item_count": len(skipped),
        "skipped_items": skipped[:8],
        "skipped_items_truncated_count": truncated,
    }


def case_01_no_value() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "vocab-no-value",
        "description": "All NGSL K1 words; worker must return empty output.",
        "unit_id": "u1",
        "unit_text": "She went to the store.",
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": _utf16_code_units("She went to the store."),
                "text": "She went to the store.",
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 0,
            "resolved_item_count": 0,
            "skipped_item_count": 0,
            "skipped_reason_codes": [],
            "skipped_items_truncated_count": 0,
        },
        "unicode_pitfall": None,
        "difficulty_band": "K1",
        "tags": ["no_value", "empty_output"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(candidate_count=0, skipped=[]),
        ),
    }


def case_02_single_highlight() -> dict[str, Any]:
    unit_text = "The committee adjourned after lengthy debate."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "adjourned"
    start, end, _ = basic_unit_offsets(unit_text, "s1", target)
    return {
        "schema_version": 1,
        "id": "vocab-single-highlight",
        "description": "Single vocab_highlight with AWL-ish word.",
        "unit_id": "u2",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [
            {
                "item_type": "vocab_highlight",
                "anchor_segment_id": "s1",
                "selected_text": target,
                "headword": target,
                "gloss": "延期；休会",
                "brief_explanation": "暂停会议",
                "reason": "useful_for_current_goal",
            }
        ],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 1,
            "skipped_item_count": 0,
            "skipped_reason_codes": [],
        },
        "unicode_pitfall": None,
        "difficulty_band": "AWL",
        "tags": ["single_highlight", "awl"],
        "execution": make_snapshot(
            items=[
                make_resolved(
                    item_type="vocab_highlight",
                    anchor_segment_id="s1",
                    start=start,
                    end=end,
                    selected_text=target,
                )
            ],
            diagnostics=diagnostics_for(candidate_count=1, skipped=[]),
        ),
    }


def case_03_phrase_priority() -> dict[str, Any]:
    unit_text = "She kicked the bucket last winter."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "kicked the bucket"
    start, end, _ = basic_unit_offsets(unit_text, "s1", target)
    return {
        "schema_version": 1,
        "id": "vocab-phrase-priority",
        "description": "Idiomatic phrase; phrase_gloss wins over vocab_highlight.",
        "unit_id": "u3",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [
            {
                "item_type": "phrase_gloss",
                "anchor_segment_id": "s1",
                "selected_text": target,
                "phrase": target,
                "phrase_type": "idiom",
                "gloss": "俚语：去世",
                "reason": "useful_for_current_goal",
            }
        ],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 1,
            "skipped_item_count": 0,
            "skipped_reason_codes": [],
        },
        "unicode_pitfall": None,
        "difficulty_band": "off-list",
        "tags": ["phrase_priority", "idiom"],
        "execution": make_snapshot(
            items=[
                make_resolved(
                    item_type="phrase_gloss",
                    anchor_segment_id="s1",
                    start=start,
                    end=end,
                    selected_text=target,
                )
            ],
            diagnostics=diagnostics_for(candidate_count=1, skipped=[]),
        ),
    }


def case_04_context_priority() -> dict[str, Any]:
    unit_text = "She deposited the check at the local bank."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "bank"
    start, end, _ = basic_unit_offsets(unit_text, "s1", target)
    return {
        "schema_version": 1,
        "id": "vocab-context-priority",
        "description": "Polysem depends on local context; context_gloss wins.",
        "unit_id": "u4",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [
            {
                "item_type": "context_gloss",
                "anchor_segment_id": "s1",
                "selected_text": target,
                "display": target,
                "gloss": "金融机构，依赖 deposit / check 等上下文",
                "reason": "polysem resolved by deposit + check context",
            }
        ],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 1,
            "skipped_item_count": 0,
            "skipped_reason_codes": [],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K2",
        "tags": ["context_priority", "polysem"],
        "execution": make_snapshot(
            items=[
                make_resolved(
                    item_type="context_gloss",
                    anchor_segment_id="s1",
                    start=start,
                    end=end,
                    selected_text=target,
                )
            ],
            diagnostics=diagnostics_for(candidate_count=1, skipped=[]),
        ),
    }


def case_05_ambiguous_selected_text() -> dict[str, Any]:
    unit_text = "We can can the leftover vegetables tomorrow."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "can"
    return {
        "schema_version": 1,
        "id": "vocab-ambiguous-selected-text",
        "description": "selected_text appears twice; skip with selected_text_ambiguous.",
        "unit_id": "u5",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 0,
            "skipped_item_count_at_least": 1,
            "skipped_reason_codes_at_least": ["selected_text_ambiguous"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K2",
        "tags": ["ambiguous", "skip_reason"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(
                candidate_count=1,
                skipped=[
                    {
                        "item_index": 0,
                        "item_type": "vocab_highlight",
                        "anchor_segment_id": "s1",
                        "selected_text": target,
                        "reason_code": "selected_text_ambiguous",
                    }
                ],
            ),
        ),
    }


def case_06_not_found() -> dict[str, Any]:
    unit_text = "She enjoys reading on weekends."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    return {
        "schema_version": 1,
        "id": "vocab-not-found",
        "description": "selected_text not present in segment; selected_text_not_found skip.",
        "unit_id": "u6",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 0,
            "skipped_item_count_at_least": 1,
            "skipped_reason_codes_at_least": ["selected_text_not_found"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K1",
        "tags": ["not_found", "skip_reason"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(
                candidate_count=1,
                skipped=[
                    {
                        "item_index": 0,
                        "item_type": "vocab_highlight",
                        "anchor_segment_id": "s1",
                        "selected_text": "phantom",
                        "reason_code": "selected_text_not_found",
                    }
                ],
            ),
        ),
    }


def case_07_unknown_segment() -> dict[str, Any]:
    unit_text = "They agreed on the proposal."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "agreed"
    return {
        "schema_version": 1,
        "id": "vocab-unknown-segment",
        "description": "anchor_segment_id not present in unit; anchor_segment_unknown skip.",
        "unit_id": "u7",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 0,
            "skipped_item_count_at_least": 1,
            "skipped_reason_codes_at_least": ["anchor_segment_unknown"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K1",
        "tags": ["unknown_segment", "skip_reason"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(
                candidate_count=1,
                skipped=[
                    {
                        "item_index": 0,
                        "item_type": "vocab_highlight",
                        "anchor_segment_id": "s_ghost",
                        "selected_text": target,
                        "reason_code": "anchor_segment_unknown",
                    }
                ],
            ),
        ),
    }


def case_08_same_span_conflict() -> dict[str, Any]:
    unit_text = "The market rallied on the news."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "rallied"
    start, end, _ = basic_unit_offsets(unit_text, "s1", target)
    return {
        "schema_version": 1,
        "id": "vocab-same-span-conflict",
        "description": "Same span resolves to context_gloss; phrase + vocab skipped.",
        "unit_id": "u8",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [
            {
                "item_type": "context_gloss",
                "anchor_segment_id": "s1",
                "selected_text": target,
                "display": target,
                "gloss": "此处依赖 market + news 语境",
                "reason": "context-bound price move",
            }
        ],
        "expected_diagnostics": {
            "candidate_item_count": 3,
            "resolved_item_count": 1,
            "skipped_item_count": 2,
            "skipped_reason_codes_at_least": ["span_conflict_higher_priority_kept"],
            "skipped_reason_codes": ["span_conflict_higher_priority_kept"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K2",
        "tags": ["same_span_conflict", "priority"],
        "execution": make_snapshot(
            items=[
                make_resolved(
                    item_type="context_gloss",
                    anchor_segment_id="s1",
                    start=start,
                    end=end,
                    selected_text=target,
                )
            ],
            diagnostics=diagnostics_for(
                candidate_count=3,
                skipped=[
                    {
                        "item_index": 1,
                        "item_type": "phrase_gloss",
                        "anchor_segment_id": "s1",
                        "selected_text": target,
                        "reason_code": "span_conflict_higher_priority_kept",
                    },
                    {
                        "item_index": 2,
                        "item_type": "vocab_highlight",
                        "anchor_segment_id": "s1",
                        "selected_text": target,
                        "reason_code": "span_conflict_higher_priority_kept",
                    },
                ],
            ),
        ),
    }


def case_09_unicode_pitfall() -> dict[str, Any]:
    unit_text = "She said “yes” without hesitation."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    return {
        "schema_version": 1,
        "id": "vocab-unicode-pitfall",
        "description": "Smart quote never matches; expect selected_text_not_found.",
        "unit_id": "u9",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 0,
            "skipped_item_count_at_least": 1,
            "skipped_reason_codes_at_least": ["selected_text_not_found"],
        },
        "unicode_pitfall": "smart_quote",
        "difficulty_band": "K2",
        "tags": ["unicode_pitfall", "smart_quote"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(
                candidate_count=1,
                skipped=[
                    {
                        "item_index": 0,
                        "item_type": "vocab_highlight",
                        "anchor_segment_id": "s1",
                        "selected_text": '"yes"',
                        "reason_code": "selected_text_not_found",
                    }
                ],
            ),
        ),
    }


def case_10_utf16_surrogate() -> dict[str, Any]:
    unit_text = "We 📚 love reading every day."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    target = "📚"
    start, end, _ = basic_unit_offsets(unit_text, "s1", target)
    return {
        "schema_version": 1,
        "id": "vocab-utf16-surrogate",
        "description": "Emoji surrogate pair offsets must compute via utf-16-le (2 code units).",
        "unit_id": "u10",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [
            {
                "item_type": "vocab_highlight",
                "anchor_segment_id": "s1",
                "selected_text": target,
                "headword": "books",
                "gloss": "书本（emoji 表达）",
                "reason": "useful_for_current_goal",
            }
        ],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 1,
            "skipped_item_count": 0,
        },
        "unicode_pitfall": "surrogate_pair",
        "difficulty_band": "K1",
        "tags": ["surrogate_pair", "utf16"],
        "execution": make_snapshot(
            items=[
                make_resolved(
                    item_type="vocab_highlight",
                    anchor_segment_id="s1",
                    start=start,
                    end=end,
                    selected_text=target,
                )
            ],
            diagnostics=diagnostics_for(candidate_count=1, skipped=[]),
        ),
    }


def case_11_diagnostics_truncation() -> dict[str, Any]:
    unit_text = "She can can the canned goods without any concern."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    skipped_entries: list[dict[str, Any]] = []
    for index in range(10):
        skipped_entries.append(
            {
                "item_index": index,
                "item_type": "vocab_highlight",
                "anchor_segment_id": "s1",
                "selected_text": "can",
                "reason_code": "selected_text_ambiguous",
            }
        )
    return {
        "schema_version": 1,
        "id": "vocab-diagnostics-truncation",
        "description": "10 ambiguous items; diagnostics truncated to MAX_ITEMS=8.",
        "unit_id": "u11",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 10,
            "resolved_item_count": 0,
            "skipped_item_count": 10,
            "skipped_reason_codes_at_least": ["selected_text_ambiguous"],
            "skipped_items_truncated_count": 2,
        },
        "unicode_pitfall": None,
        "difficulty_band": "K2",
        "tags": ["diagnostics_truncation"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(candidate_count=10, skipped=skipped_entries),
        ),
    }


def case_12_empty_with_diagnostics() -> dict[str, Any]:
    unit_text = "The cat purred softly."
    seg_text = unit_text
    seg_end = _utf16_code_units(seg_text)
    skipped_entries = [
        {
            "item_index": 0,
            "item_type": "phrase_gloss",
            "anchor_segment_id": "s1",
            "selected_text": "purred softly",
            "reason_code": "span_conflict_higher_priority_kept",
        },
        {
            "item_index": 1,
            "item_type": "vocab_highlight",
            "anchor_segment_id": "s1",
            "selected_text": "purred softly",
            "reason_code": "span_conflict_higher_priority_kept",
        },
    ]
    return {
        "schema_version": 1,
        "id": "vocab-empty-with-diagnostics",
        "description": "Empty output but LLM proposed items; worker must report skip reasons.",
        "unit_id": "u12",
        "unit_text": unit_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 2,
            "resolved_item_count": 0,
            "skipped_item_count": 2,
            "skipped_reason_codes_at_least": ["span_conflict_higher_priority_kept"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "K1",
        "tags": ["empty_output_with_diagnostics"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(candidate_count=2, skipped=skipped_entries),
        ),
    }


def case_13_too_many_candidates_fail_closed() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "vocab-too-many-fail-closed",
        "description": "More than MAX_VOCABULARY_ITEMS=5"
        "candidate items -> structured output validation fails closed.",
        "unit_id": "u13",
        "unit_text": "She bought a brand-new notebook for the trip.",
        "anchor_segments": [
            {
                "anchor_segment_id": "s1",
                "sentence_id": "s1",
                "segment_type": "sentence",
                "unit_start_utf16": 0,
                "unit_end_utf16": _utf16_code_units(
                    "She bought a brand-new notebook for the trip."
                ),
                "text": "She bought a brand-new notebook for the trip.",
                "boundary_quality": "normal",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {},
        "unicode_pitfall": None,
        "difficulty_band": "K2",
        "tags": ["candidate_overflow", "fail_closed"],
        "execution": make_snapshot(
            items=[],
            diagnostics={
                "candidate_item_count": 0,
                "resolved_item_count": 0,
                "skipped_item_count": 0,
                "skipped_items": [],
                "skipped_items_truncated_count": 0,
            },
            fail_closed=True,
            fail_closed_reason="model_output_invalid: items exceeds MAX_VOCABULARY_ITEMS",
        ),
    }


def case_14_fallback_window_skip() -> dict[str, Any]:
    """Vocabulary candidate on a fallback_window segment must be skipped with
    reason_code=boundary_low_fallback_window, mirroring grammar bundle's
    policy (D5 boundary alignment)."""
    seg_text = "longlongword " * 24
    seg_text = seg_text.strip()
    seg_end = _utf16_code_units(seg_text)
    target = "longlongword"
    skipped_entries = [
        {
            "item_index": 0,
            "item_type": "vocab_highlight",
            "anchor_segment_id": "fb1",
            "selected_text": target,
            "reason_code": "boundary_low_fallback_window",
        }
    ]
    return {
        "schema_version": 1,
        "id": "vocab-fallback-window-skip",
        "description": (
            "Candidate on fallback_window segment must skip with "
            "boundary_low_fallback_window."
        ),
        "unit_id": "u14",
        "unit_text": seg_text,
        "anchor_segments": [
            {
                "anchor_segment_id": "fb1",
                "sentence_id": "fb1",
                "segment_type": "fallback_window",
                "unit_start_utf16": 0,
                "unit_end_utf16": seg_end,
                "text": seg_text,
                "boundary_quality": "low",
            }
        ],
        "gold_items": [],
        "expected_diagnostics": {
            "candidate_item_count": 1,
            "resolved_item_count": 0,
            "skipped_item_count": 1,
            "skipped_reason_codes": ["boundary_low_fallback_window"],
        },
        "unicode_pitfall": None,
        "difficulty_band": "off-list",
        "tags": ["fallback_window", "boundary_quality", "skip_reason"],
        "execution": make_snapshot(
            items=[],
            diagnostics=diagnostics_for(candidate_count=1, skipped=skipped_entries),
        ),
    }


CASES: list[tuple[str, dict[str, Any]]] = [
    ("01-vocab-no-value.json", case_01_no_value()),
    ("02-vocab-single-highlight.json", case_02_single_highlight()),
    ("03-vocab-phrase-priority.json", case_03_phrase_priority()),
    ("04-vocab-context-priority.json", case_04_context_priority()),
    ("05-vocab-ambiguous-selected-text.json", case_05_ambiguous_selected_text()),
    ("06-vocab-not-found.json", case_06_not_found()),
    ("07-vocab-unknown-segment.json", case_07_unknown_segment()),
    ("08-vocab-same-span-conflict.json", case_08_same_span_conflict()),
    ("09-vocab-unicode-pitfall.json", case_09_unicode_pitfall()),
    ("10-vocab-utf16-surrogate.json", case_10_utf16_surrogate()),
    ("11-vocab-diagnostics-truncation.json", case_11_diagnostics_truncation()),
    ("12-vocab-empty-with-diagnostics.json", case_12_empty_with_diagnostics()),
    ("13-vocab-too-many-fail-closed.json", case_13_too_many_candidates_fail_closed()),
    ("14-vocab-fallback-window-skip.json", case_14_fallback_window_skip()),
]


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"
    cases_dir = root / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in CASES:
        write_case(cases_dir / filename, payload)
    print(f"Wrote {len(CASES)} cases under {cases_dir}")


if __name__ == "__main__":
    main()