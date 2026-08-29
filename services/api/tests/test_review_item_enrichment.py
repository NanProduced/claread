"""R8 Commit 1 — Structured Review Item enrichment (unit tests).

Covers services/reader_orchestration/review_item_enrichment.py:

- deterministic, stable issue_id per (namespace, code, occurrence) — distinct
  ids for same-code items (no array-index identity),
- content_check product tier mapping (Routine / Attention per surface spec §7)
  with fail-closed default for unknown codes,
- target_scope mapping (document for full-document items, range otherwise),
- exact (no fuzzy-guessing) UTF-16 anchors + excerpt + anchor_hash for
  locatable fence issues (``has_unclosed_fence``),
- null + degrade for missing evidence / anchors / media coordinates,
- silent / adaptation_notice records pass through untouched (never surface
  review metadata), classification preserved verbatim.
"""

from __future__ import annotations

import hashlib

from app.schemas.reader_documents import (
    ReviewItemClassification,
    StructuredReviewItem,
)
from app.schemas.reader_input_adapter import (
    AdaptationClassification,
    AdaptationRecord,
)
from app.services.reader_orchestration.review_item_enrichment import (
    enrich_review_items,
)


def _record(
    code: str,
    *,
    classification: str = "content_check",
    message: str = "m",
) -> AdaptationRecord:
    return AdaptationRecord(
        code=code,
        message=message,
        classification=classification,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# issue_id semantics
# ---------------------------------------------------------------------------


def test_issue_id_is_deterministic_and_stable_across_calls() -> None:
    items_a = enrich_review_items(
        adaptations=[_record("ocr_low_confidence")],
        issue_namespace="record-1:generation-1",
    )
    items_b = enrich_review_items(
        adaptations=[_record("ocr_low_confidence")],
        issue_namespace="record-1:generation-1",
    )
    assert items_a[0]["issue_id"] == items_b[0]["issue_id"]
    assert items_a[0]["issue_id"] != ""
    assert len(items_a[0]["issue_id"]) == 16


def test_same_code_multiple_occurrences_get_distinct_stable_issue_ids() -> None:
    items = enrich_review_items(
        adaptations=[
            _record("has_unclosed_fence"),
            _record("has_unclosed_fence"),
        ],
        issue_namespace="record-1:generation-1",
    )
    assert len(items) == 2
    assert items[0]["issue_id"] != items[1]["issue_id"]
    # Deterministic: same input yields the same pair every time.
    again = enrich_review_items(
        adaptations=[
            _record("has_unclosed_fence"),
            _record("has_unclosed_fence"),
        ],
        issue_namespace="record-1:generation-1",
    )
    assert [item["issue_id"] for item in again] == [item["issue_id"] for item in items]


def test_issue_id_differs_across_namespaces() -> None:
    a = enrich_review_items(
        adaptations=[_record("ocr_low_confidence")],
        issue_namespace="record-1:generation-1",
    )[0]["issue_id"]
    b = enrich_review_items(
        adaptations=[_record("ocr_low_confidence")],
        issue_namespace="record-2:generation-1",
    )[0]["issue_id"]
    assert a != b


# ---------------------------------------------------------------------------
# tier / target_scope mapping (surface spec §7 closed code table)
# ---------------------------------------------------------------------------


def test_routine_tier_mapping() -> None:
    routine_codes = {
        "source_type_review_default",
        "ocr_low_confidence",
        "image_ocr_uncertain",
        "document_block_degraded",
        "footnote_reference",
        "task_list_unsupported",
    }
    for code in routine_codes:
        items = enrich_review_items(
            adaptations=[_record(code)],
            issue_namespace="ns",
        )
        assert items[0]["tier"] == "routine", code


def test_attention_tier_mapping() -> None:
    attention_codes = {
        "has_unclosed_fence",
        "table_structure_uncertain",
        "missing_source_range",
        "layout_order_uncertain",
        "code_dominant",
        "too_long_requires_envelope",
        "unclosed_html_aside",
    }
    for code in attention_codes:
        items = enrich_review_items(
            adaptations=[_record(code)],
            issue_namespace="ns",
        )
        assert items[0]["tier"] == "attention", code


def test_unknown_content_check_code_is_fail_closed_attention() -> None:
    items = enrich_review_items(
        adaptations=[_record("future_unknown_code")],
        issue_namespace="ns",
    )
    assert items[0]["tier"] == "attention"


def test_target_scope_document_for_doc_level_codes() -> None:
    for code in ("code_dominant", "too_long_requires_envelope"):
        items = enrich_review_items(
            adaptations=[_record(code)],
            issue_namespace="ns",
        )
        assert items[0]["target_scope"] == "document", code


def test_target_scope_range_for_local_codes() -> None:
    for code in ("has_unclosed_fence", "table_structure_uncertain"):
        items = enrich_review_items(
            adaptations=[_record(code)],
            issue_namespace="ns",
        )
        assert items[0]["target_scope"] == "range", code


# ---------------------------------------------------------------------------
# anchors: exact, no fuzzy guessing
# ---------------------------------------------------------------------------


def test_unclosed_fence_anchor_is_exact_utf16() -> None:
    text = "A\n\n```python\nprint(1)\n"
    opening_offset = text.index("```")
    items = enrich_review_items(
        adaptations=[_record("has_unclosed_fence")],
        issue_namespace="ns",
        document_text=text,
    )
    anchor = items[0]["source_anchor"]
    assert anchor is not None
    assert anchor["start_utf16"] == opening_offset
    assert anchor["end_utf16"] > anchor["start_utf16"]
    assert items[0]["anchor_hash"] is not None
    expected_excerpt = text[anchor["start_utf16"] : anchor["end_utf16"]]
    assert items[0]["evidence"]["excerpt"] == expected_excerpt
    assert items[0]["anchor_hash"] == hashlib.sha256(expected_excerpt.encode("utf-8")).hexdigest()


def test_indented_fence_marker_anchor_includes_leading_space() -> None:
    text = "  ```python\nx\n"
    items = enrich_review_items(
        adaptations=[_record("has_unclosed_fence")],
        issue_namespace="ns",
        document_text=text,
    )
    anchor = items[0]["source_anchor"]
    # Fence marker starts after the two leading spaces; the anchor covers
    # the whole (indented) opening line: offsets 2..13 ("  ```python" is
    # 11 chars), excerpt verbatim.
    assert anchor == {"block_id": None, "start_utf16": 2, "end_utf16": 13}
    assert items[0]["evidence"]["excerpt"] == "  ```python"


def test_closed_fences_do_not_fabricate_anchors() -> None:
    text = "```python\nprint(1)\n```\n\n```go\nx\n```\n"
    items = enrich_review_items(
        adaptations=[_record("has_unclosed_fence")],
        issue_namespace="ns",
        document_text=text,
    )
    # No unclosed fence exists: the fence item degrades to null anchors.
    assert items[0]["source_anchor"] is None
    assert items[0]["anchor_hash"] is None
    assert items[0]["evidence"]["excerpt"] is None


def test_anchors_assigned_in_order_for_multiple_fence_records() -> None:
    text = "```py\na\n"
    items = enrich_review_items(
        adaptations=[
            _record("has_unclosed_fence"),
            _record("has_unclosed_fence"),
        ],
        issue_namespace="ns",
        document_text=text,
    )
    # One locatable fence, two records: first gets the real anchor, the
    # second degrades to null (never duplicated or guessed).
    assert items[0]["source_anchor"] is not None
    assert items[1]["source_anchor"] is None


def test_no_anchor_for_non_fence_codes() -> None:
    text = "Table-like content without a fence\n| a | b |\n"
    items = enrich_review_items(
        adaptations=[_record("table_structure_uncertain")],
        issue_namespace="ns",
        document_text=text,
    )
    assert items[0]["source_anchor"] is None
    assert items[0]["anchor_hash"] is None


# ---------------------------------------------------------------------------
# null degrade & classification passthrough
# ---------------------------------------------------------------------------


def test_missing_document_text_degrades_all_evidence_fields() -> None:
    items = enrich_review_items(
        adaptations=[_record("has_unclosed_fence")],
        issue_namespace="ns",
    )
    assert items[0]["source_anchor"] is None
    assert items[0]["anchor_hash"] is None
    assert items[0]["evidence"] == {"excerpt": None, "proposed_patch": None}
    assert items[0]["source_media_coordinate"] is None


def test_media_coordinate_is_always_present_as_nullable_field() -> None:
    items = enrich_review_items(
        adaptations=[_record("ocr_low_confidence")],
        issue_namespace="ns",
    )
    assert "source_media_coordinate" in items[0]
    assert items[0]["source_media_coordinate"] is None


def test_adaptation_notice_passthrough_without_review_metadata() -> None:
    notice = _record("raw_html_block", classification="adaptation_notice")
    items = enrich_review_items(
        adaptations=[notice],
        issue_namespace="ns",
        document_text="<div>x</div>",
    )
    assert len(items) == 1
    assert items[0]["code"] == "raw_html_block"
    assert items[0]["classification"] == "adaptation_notice"
    # Notices are not review items: no review metadata fabricated at all.
    assert "tier" not in items[0]
    assert "target_scope" not in items[0]
    assert "source_anchor" not in items[0]
    assert "issue_id" not in items[0]


def test_code_message_classification_preserved_verbatim() -> None:
    items = enrich_review_items(
        adaptations=[
            _record(
                "has_unclosed_fence",
                classification="content_check",
                message="代码块缺少结束围栏",
            )
        ],
        issue_namespace="ns",
    )
    assert items[0]["code"] == "has_unclosed_fence"
    assert items[0]["message"] == "代码块缺少结束围栏"
    assert items[0]["classification"] == "content_check"


def test_empty_adaptations_returns_empty_list() -> None:
    assert enrich_review_items(adaptations=[], issue_namespace="ns") == []


# ---------------------------------------------------------------------------
# contract-shape round trips
# ---------------------------------------------------------------------------


def test_classification_literal_stays_in_sync_with_canonical() -> None:
    assert set(ReviewItemClassification.__args__) == set(AdaptationClassification.__args__)


def test_enriched_dict_validates_as_structured_review_item() -> None:
    items = enrich_review_items(
        adaptations=[
            _record(
                "has_unclosed_fence",
                classification="content_check",
                message="代码块缺少结束围栏",
            )
        ],
        issue_namespace="ns",
        document_text="A\n\n```python\nprint(1)\n",
    )
    model = StructuredReviewItem.model_validate(items[0])
    assert model.issue_id == items[0]["issue_id"]
    assert model.tier == "attention"
    assert model.target_scope == "range"
    assert model.source_anchor is not None
    assert model.anchor_hash is not None
    assert model.evidence is not None and model.evidence.excerpt is not None
    assert model.source_media_coordinate is None
