"""Tests for provenance fence re-check (R0.1 §8.1).

A1 stub: 待 A1 完成后移除（schema/mapping 走 conftest 注入的 _stub）
"""

from __future__ import annotations

import pytest

from app.services.reader_record_ask.thread_memory.fence import (
    check_all_bindings,
    check_binding_validity,
)
from app.services.reader_record_ask.thread_memory.schema import SourceBinding


def _article_binding(
    *,
    binding_id: str = "b1",
    stable_document_id: str = "doc_1",
    base_id: str = "base_1",
    record_generation: int = 2,
    reading_record_id: str = "rec_1",
    status: str = "unchecked",
) -> SourceBinding:
    return SourceBinding(
        binding_id=binding_id,
        source_type="article",
        source_id=stable_document_id,
        fence_type="stable_document",
        fence_values={
            "reading_record_id": reading_record_id,
            "stable_document_id": stable_document_id,
            "base_id": base_id,
            "record_generation": record_generation,
        },
        validity_check={"status": status, "last_validated_turn": 0},
    )


def _web_binding(binding_id: str = "w1") -> SourceBinding:
    return SourceBinding(
        binding_id=binding_id,
        source_type="web",
        source_id="https://example.com/page",
        fence_type="reading_record",
        fence_values={
            "canonical_url": "https://example.com/page",
            "source_fingerprint": "abc",
            "retrieved_at": "2026-07-30",
        },
        validity_check={"status": "unchecked", "last_validated_turn": 0},
    )


# ---------------------------------------------------------------------------
# check_binding_validity
# ---------------------------------------------------------------------------


def test_check_binding_validity_success_marks_valid():
    binding = _article_binding()
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "valid"
    assert "invalidation_reason" not in result.validity_check


def test_check_binding_validity_generation_changed():
    binding = _article_binding(record_generation=2)
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=3,  # differs
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "invalid"
    assert result.validity_check["invalidation_reason"] == "generation_changed"


def test_check_binding_validity_base_changed():
    binding = _article_binding(base_id="base_1")
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_2",  # differs
    )
    assert result.validity_check["status"] == "invalid"
    assert result.validity_check["invalidation_reason"] == "base_changed"


def test_check_binding_validity_document_missing():
    """Binding with empty stable_document_id → document_missing."""
    binding = _article_binding(stable_document_id="")
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "invalid"
    assert result.validity_check["invalidation_reason"] == "document_missing"


def test_check_binding_validity_record_missing():
    """Live reading_record_id empty → record_missing."""
    binding = _article_binding(reading_record_id="rec_1")
    result = check_binding_validity(
        binding,
        reading_record_id="",  # live record gone
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "invalid"
    assert result.validity_check["invalidation_reason"] == "record_missing"


def test_check_binding_validity_record_id_mismatch_is_record_missing():
    """Binding's reading_record_id does not match live → record_missing."""
    binding = _article_binding(reading_record_id="rec_old")
    result = check_binding_validity(
        binding,
        reading_record_id="rec_new",
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "invalid"
    assert result.validity_check["invalidation_reason"] == "record_missing"


def test_check_binding_validity_web_binding_returns_unchecked():
    """Web bindings have no article fence (R0.1 §8.1 + H7)."""
    binding = _web_binding()
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["status"] == "unchecked"


def test_check_binding_validity_returns_new_instance_via_model_copy():
    """Frozen Pydantic model: must use model_copy, original unchanged."""
    binding = _article_binding()
    original_status = binding.validity_check["status"]
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_1",
    )
    assert binding.validity_check["status"] == original_status  # unchanged
    assert result is not binding
    assert result.validity_check["status"] == "valid"


def test_check_binding_validity_preserves_last_validated_turn():
    binding = SourceBinding(
        binding_id="b1",
        source_type="article",
        source_id="doc_1",
        fence_type="stable_document",
        fence_values={
            "reading_record_id": "rec_1",
            "stable_document_id": "doc_1",
            "base_id": "base_1",
            "record_generation": 2,
        },
        validity_check={"status": "unchecked", "last_validated_turn": 5},
    )
    result = check_binding_validity(
        binding,
        reading_record_id="rec_1",
        current_generation=2,
        current_base_id="base_1",
    )
    assert result.validity_check["last_validated_turn"] == 5


# ---------------------------------------------------------------------------
# check_all_bindings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_all_bindings_batch_validates():
    bindings = [
        _article_binding(binding_id="b1", record_generation=2),
        _article_binding(binding_id="b2", record_generation=3),  # will mismatch
        _web_binding(binding_id="w1"),
    ]
    context = {
        "reading_record_id": "rec_1",
        "current_generation": 2,
        "current_base_id": "base_1",
    }
    results = await check_all_bindings(bindings, context)
    assert len(results) == 3
    assert results[0].validity_check["status"] == "valid"
    assert results[1].validity_check["status"] == "invalid"
    assert results[1].validity_check["invalidation_reason"] == "generation_changed"
    assert results[2].validity_check["status"] == "unchecked"  # web


@pytest.mark.asyncio
async def test_check_all_bindings_empty_input():
    results = await check_all_bindings([], {"reading_record_id": "r1"})
    assert results == []


@pytest.mark.asyncio
async def test_check_all_bindings_handles_missing_context_fields():
    """Missing context fields default to safe values (0 / empty)."""
    bindings = [_article_binding(binding_id="b1", record_generation=2)]
    context = {}  # no reading_record_id / current_generation / current_base_id
    results = await check_all_bindings(bindings, context)
    # Empty reading_record_id → record_missing.
    assert results[0].validity_check["status"] == "invalid"
    assert results[0].validity_check["invalidation_reason"] == "record_missing"
