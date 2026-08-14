from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.reader_orchestration import (
    ReaderPlateSnapshot,
    ReaderSemanticOutlineProjection,
)
from app.services.reader_orchestration.semantic_outline import (
    SemanticOutlineValidationContext,
    SemanticOutlineValidationInput,
    validate_semantic_outline_projection,
)


_CASES_PATH = Path(__file__).parent / "fixtures" / "semantic_outline" / "v1" / "cases.json"


def _cases() -> list[dict[str, object]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda item: str(item["id"]))
def test_semantic_outline_validator_matches_shared_contract(case: dict[str, object]) -> None:
    context = SemanticOutlineValidationContext.from_mapping(case["context"])
    validation_input = SemanticOutlineValidationInput.from_mapping(case["input"])

    result = validate_semantic_outline_projection(context, validation_input)
    expected = case["expected"]

    assert result.status == expected["status"]
    assert [node.node_id for node in result.nodes] == expected["node_ids"]
    assert [node.title for node in result.nodes] == expected["titles"]
    assert sorted(drop.reason_code for drop in result.diagnostics.drops) == sorted(
        expected["drop_reasons"]
    )
    if "skipped_node_count" in expected:
        assert result.diagnostics.skipped_node_count == expected["skipped_node_count"]
    if "start_anchor_segment_ids" in expected:
        assert [node.start_anchor_segment_id for node in result.nodes] == expected[
            "start_anchor_segment_ids"
        ]


def test_canonical_semantic_outline_fragment_is_strict_and_optional_on_snapshot() -> None:
    """Fragment stays strict; optional snapshot field defaults to None."""
    projection = ReaderSemanticOutlineProjection.model_validate(
        {
            "schema_kind": "reader_semantic_outline",
            "schema_version": 1,
            "status": "ready",
            "source_identity": {"base_id": "base_a", "generation": 1},
            "publication": {"outline_revision": "outline_r1"},
            "provenance": {
                "kind": "llm",
                "builder": "outline_builder",
                "model": "test-model",
            },
            "nodes": [
                {
                    "node_id": "oln_1",
                    "depth": 1,
                    "title": "Chapter One",
                    "start_unit_id": "u1",
                    "end_unit_id": "u1",
                    "order_index": 1,
                }
            ],
            "diagnostics": {"drops": [], "skipped_node_count": 0},
        }
    )

    assert projection.schema_kind == "reader_semantic_outline"
    assert projection.provenance.kind == "llm"
    assert projection.diagnostics.skipped_node_count == 0
    with pytest.raises(ValidationError):
        ReaderSemanticOutlineProjection.model_validate(
            {**projection.model_dump(), "unexpected_field": True}
        )
    # Optional trusted ready|partial only; default None (JSON null wire).
    assert "semantic_outline" in ReaderPlateSnapshot.model_fields
    assert ReaderPlateSnapshot.model_fields["semantic_outline"].default is None
