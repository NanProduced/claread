"""R8 narrow-repair — Structured Review Item public DTO validation.

The public ``content_check`` items must satisfy (frozen contract):

- ``classification`` is EXACTLY ``content_check`` (silent / notices never
  surface here),
- ``issue_id`` / ``tier`` / ``target_scope`` / ``evidence`` are REQUIRED,
- ``issue_id`` is exactly 16 lowercase hex chars,
- ``source_anchor`` is nullable but, when present, is exactly ONE form:
  a non-empty ``block_id`` OR a complete UTF-16 range with ``end > start``
  (never both, never empty),
- ``target_scope='range'`` REQUIRES a valid ``source_anchor``
  (no fabricated ranges).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.reader_documents import StructuredReviewItem

_ISSUE_ID = "a1b2c3d4e5f6a7b8"
_HASH = "d" * 64


def _valid_item(**overrides) -> dict:
    item = {
        "code": "has_unclosed_fence",
        "message": "代码块缺少结束围栏",
        "classification": "content_check",
        "issue_id": _ISSUE_ID,
        "tier": "attention",
        "target_scope": "range",
        "source_anchor": {"block_id": None, "start_utf16": 3, "end_utf16": 12},
        "anchor_hash": _HASH,
        "evidence": {"excerpt_text": "```python", "proposed_patch": None},
        "source_media_coordinate": None,
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# classification must be exactly content_check
# ---------------------------------------------------------------------------


def test_classification_silent_rejected() -> None:
    item = _valid_item(classification="silent")
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_classification_adaptation_notice_rejected() -> None:
    item = _valid_item(classification="adaptation_notice")
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_classification_content_check_accepted() -> None:
    model = StructuredReviewItem.model_validate(_valid_item())
    assert model.classification == "content_check"


# ---------------------------------------------------------------------------
# required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["issue_id", "tier", "target_scope", "evidence"])
def test_required_field_missing_rejected(missing: str) -> None:
    item = _valid_item()
    del item[missing]
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


# ---------------------------------------------------------------------------
# issue_id format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "a1b2c3d4e5f6a7b8c9",  # too long
        "a1b2c3d4e5f6a7",  # too short
        "A1B2C3D4E5F6A7B8",  # uppercase
        "zzzzzzzzzzzzzzzz",  # non-hex
        "",
    ],
)
def test_issue_id_must_be_16_lowercase_hex(bad: str) -> None:
    item = _valid_item(issue_id=bad)
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_issue_id_accepts_16_lowercase_hex() -> None:
    model = StructuredReviewItem.model_validate(_valid_item())
    assert len(model.issue_id) == 16
    assert model.issue_id == _ISSUE_ID


# ---------------------------------------------------------------------------
# source_anchor single-form contract
# ---------------------------------------------------------------------------


def test_anchor_empty_rejected() -> None:
    item = _valid_item(
        source_anchor={},
        target_scope="document",
        anchor_hash=None,
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_anchor_block_id_and_range_together_rejected() -> None:
    item = _valid_item(
        source_anchor={
            "block_id": "b1",
            "start_utf16": 0,
            "end_utf16": 5,
        },
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_anchor_empty_block_id_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": "", "start_utf16": None, "end_utf16": None},
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_anchor_incomplete_range_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": None, "start_utf16": 3, "end_utf16": None},
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_anchor_inverted_range_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": None, "start_utf16": 12, "end_utf16": 3},
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_anchor_block_id_alone_accepted() -> None:
    model = StructuredReviewItem.model_validate(
        _valid_item(
            source_anchor={
                "block_id": "b42",
                "start_utf16": None,
                "end_utf16": None,
            },
            target_scope="range",
            evidence={"excerpt_text": None, "proposed_patch": None},
        )
    )
    assert model.source_anchor is not None
    assert model.source_anchor.block_id == "b42"
    assert model.source_anchor.start_utf16 is None


def test_anchor_range_alone_accepted() -> None:
    model = StructuredReviewItem.model_validate(_valid_item())
    assert model.source_anchor is not None
    assert model.source_anchor.block_id is None
    assert model.source_anchor.end_utf16 == 12


def test_anchor_null_present_accepted() -> None:
    model = StructuredReviewItem.model_validate(
        _valid_item(
            source_anchor=None,
            target_scope="document",
            anchor_hash=None,
            evidence={"excerpt_text": None, "proposed_patch": None},
        )
    )
    assert model.source_anchor is None


# ---------------------------------------------------------------------------
# range scope requires a valid anchor (no fabricated ranges)
# ---------------------------------------------------------------------------


def test_range_scope_without_anchor_rejected() -> None:
    item = _valid_item(
        source_anchor=None,
        target_scope="range",
        anchor_hash=None,
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_document_scope_without_anchor_accepted() -> None:
    model = StructuredReviewItem.model_validate(
        _valid_item(
            source_anchor=None,
            target_scope="document",
            anchor_hash=None,
            evidence={"excerpt_text": None, "proposed_patch": None},
        )
    )
    assert model.target_scope == "document"
    assert model.source_anchor is None


def test_degraded_item_without_anchor_but_range_scope_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": None, "start_utf16": None, "end_utf16": None},
        target_scope="range",
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


# ---------------------------------------------------------------------------
# scope / anchor / hash consistency (narrow repair)
# ---------------------------------------------------------------------------


def test_whitespace_block_id_rejected() -> None:
    item = _valid_item(
        source_anchor={
            "block_id": "   ",
            "start_utf16": None,
            "end_utf16": None,
        },
        target_scope="range",
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_range_scope_without_anchor_hash_rejected() -> None:
    # range requires BOTH source_anchor AND anchor_hash.
    item = _valid_item(
        source_anchor={"block_id": None, "start_utf16": 3, "end_utf16": 12},
        target_scope="range",
        anchor_hash=None,
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_range_scope_block_id_without_anchor_hash_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": "b9", "start_utf16": None, "end_utf16": None},
        target_scope="range",
        anchor_hash=None,
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_document_scope_with_local_anchor_rejected() -> None:
    item = _valid_item(
        source_anchor={"block_id": None, "start_utf16": 3, "end_utf16": 12},
        target_scope="document",
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)


def test_document_scope_with_anchor_hash_rejected() -> None:
    item = _valid_item(
        source_anchor=None,
        target_scope="document",
        evidence={"excerpt_text": None, "proposed_patch": None},
    )
    with pytest.raises(ValidationError):
        StructuredReviewItem.model_validate(item)
