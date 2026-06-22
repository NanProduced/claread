from __future__ import annotations

from typing import Any
from uuid import UUID

TRANSLATION_PARSED_POLICY_CODE = "translation_layer_v1"
TRANSLATION_PARSED_RATIONALE_CODE = "translation_layer_published"
TRANSLATION_PARSED_POLICY_VERSION = "d4-p2-translation-parsed"
TRANSLATION_PARSED_TRIGGER = "translation_layer_published"


def build_translation_parsed_decision_documents(
    *,
    layer_id: UUID,
    unit_id: str,
    generation: int,
    source_language: str,
    target_language: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage_json: dict[str, Any] = {
        "translation_layer_id": str(layer_id),
        "target_language": target_language,
        "source_language": source_language,
    }
    decision_json: dict[str, Any] = {
        "policy_version": TRANSLATION_PARSED_POLICY_VERSION,
        "trigger": TRANSLATION_PARSED_TRIGGER,
        "generation": generation,
        "unit_id": unit_id,
    }
    return coverage_json, decision_json


def build_translation_parsed_decision_event_payload(
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    source_layer_id: UUID,
    source_job_id: UUID,
) -> dict[str, str]:
    return {
        "record_id": str(reading_record_id),
        "base_id": str(base_id),
        "unit_id": unit_id,
        "policy_code": TRANSLATION_PARSED_POLICY_CODE,
        "parsed_state": "parsed",
        "rationale_code": TRANSLATION_PARSED_RATIONALE_CODE,
        "source_layer_id": str(source_layer_id),
        "source_job_id": str(source_job_id),
    }
