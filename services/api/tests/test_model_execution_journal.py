from __future__ import annotations

import inspect
import json

import pytest

from app.services.model_execution_journal.payload_codec import (
    PayloadContractError,
    canonical_json_bytes,
    decode_resume_payload,
    prepare_capture_envelope,
)
from app.services.model_execution_journal.service import (
    ModelExecutionJournalService,
)


def _grammar_payload() -> dict[str, object]:
    return {
        "outputs": [
            {
                "unit_id": "unit-1",
                "output": {
                    "schema_version": 1,
                    "grammar_notes": [],
                    "sentence_analyses": [],
                },
            }
        ],
        "diagnostics": None,
    }


def _usage_draft() -> dict[str, object]:
    return {
        "usage_scope": "system_internal",
        "capability_code": "reader_grammar_bundle",
        "billing_mode": "internal_only",
        "status": "model_call_completed",
        "model_route": "reader_layer_grammar_bundle",
        "model_provider": "fake",
        "model_name": "fake-grammar",
        "usage_data": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        "metadata_json": {"unit_count": 1},
    }


def test_grammar_batch_v1_payload_round_trips_strictly() -> None:
    prepared = prepare_capture_envelope(
        invocation_kind="reader.grammar_batch",
        resume_payload_kind="reader.grammar_batch.result",
        resume_payload_schema_version=1,
        usage_event_draft_schema_version=1,
        normalized_payload=_grammar_payload(),
        usage_event_draft=_usage_draft(),
    )

    decoded = decode_resume_payload(
        kind=prepared.resume_payload_kind,
        schema_version=prepared.resume_payload_schema_version,
        payload=prepared.normalized_payload,
    )

    assert decoded.model_dump(mode="json") == _grammar_payload()
    assert prepared.resume_payload_bytes == len(
        canonical_json_bytes(_grammar_payload())
    )
    assert len(prepared.capture_envelope_sha256) == 64


@pytest.mark.parametrize(
    ("invocation_kind", "resume_payload_kind", "payload"),
    [
        (
            "reader.display_title",
            "reader.display_title.result",
            {"title_zh": "城市补贴政策争议"},
        ),
        (
            "reader.semantic_outline",
            "reader.semantic_outline.result",
            {
                "candidates": [
                    {
                        "candidate_ref": "root",
                        "parent_candidate_ref": None,
                        "depth": 1,
                        "title": "Root",
                        "start_unit_id": "u1",
                        "end_unit_id": "u1",
                        "start_anchor_segment_id": None,
                        "end_anchor_segment_id": None,
                    }
                ],
                "worker_failure": False,
                "model": "fake-outline",
            },
        ),
    ],
)
def test_reader_wave_one_payloads_round_trip_strictly(
    invocation_kind: str,
    resume_payload_kind: str,
    payload: dict[str, object],
) -> None:
    prepared = prepare_capture_envelope(
        invocation_kind=invocation_kind,
        resume_payload_kind=resume_payload_kind,
        resume_payload_schema_version=1,
        usage_event_draft_schema_version=1,
        normalized_payload=payload,
        usage_event_draft=_usage_draft(),
    )

    decoded = decode_resume_payload(
        kind=prepared.resume_payload_kind,
        schema_version=prepared.resume_payload_schema_version,
        payload=prepared.normalized_payload,
    )

    assert decoded.model_dump(mode="json") == payload
    with pytest.raises(PayloadContractError, match="invalid_resume_payload"):
        decode_resume_payload(
            kind=resume_payload_kind,
            schema_version=1,
            payload={**payload, "unexpected": True},
        )


def test_capture_hash_uses_canonical_json_key_order() -> None:
    usage_draft = _usage_draft()
    reversed_usage_draft = dict(reversed(list(usage_draft.items())))

    first = prepare_capture_envelope(
        invocation_kind="reader.grammar_batch",
        resume_payload_kind="reader.grammar_batch.result",
        resume_payload_schema_version=1,
        usage_event_draft_schema_version=1,
        normalized_payload=_grammar_payload(),
        usage_event_draft=usage_draft,
    )
    second = prepare_capture_envelope(
        invocation_kind="reader.grammar_batch",
        resume_payload_kind="reader.grammar_batch.result",
        resume_payload_schema_version=1,
        usage_event_draft_schema_version=1,
        normalized_payload=json.loads(
            json.dumps(_grammar_payload(), sort_keys=False)
        ),
        usage_event_draft=reversed_usage_draft,
    )

    assert first.capture_envelope_sha256 == second.capture_envelope_sha256


def test_journal_service_does_not_own_reader_job_state() -> None:
    source = inspect.getsource(ModelExecutionJournalService)

    assert "reader_jobs" not in source
    assert "reader_job_events" not in source
    assert "enhancement_layers" not in source
    assert "analysis_windows" not in source
    assert "reader." not in source
    assert "_pause_owning_job_for_conflict" not in source


@pytest.mark.parametrize(
    ("kind", "version"),
    [
        ("reader.unknown", 1),
        ("reader.grammar_batch.result", 999),
    ],
)
def test_resume_payload_rejects_unknown_kind_or_version(
    kind: str,
    version: int,
) -> None:
    with pytest.raises(PayloadContractError, match="unsupported_resume_payload"):
        decode_resume_payload(
            kind=kind,
            schema_version=version,
            payload=_grammar_payload(),
        )


def test_capture_rejects_extra_malformed_and_oversized_payloads() -> None:
    extra_payload = {**_grammar_payload(), "provider_response": {}}
    with pytest.raises(PayloadContractError, match="invalid_resume_payload"):
        prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload=extra_payload,
            usage_event_draft=_usage_draft(),
        )

    with pytest.raises(PayloadContractError, match="invalid_resume_payload"):
        prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload={"outputs": "not-a-list", "diagnostics": None},
            usage_event_draft=_usage_draft(),
        )

    oversized = _grammar_payload()
    oversized["diagnostics"] = {"note": "x" * 1_048_576}
    with pytest.raises(PayloadContractError, match="resume_payload_too_large"):
        prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload=oversized,
            usage_event_draft=_usage_draft(),
        )


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"system_prompt": "do not persist"},
        {"authorization": "Bearer secret-value"},
        {"raw_provider_response": {"body": "secret"}},
        {"note": "sk-secret-value-1234567890"},
    ],
)
def test_capture_rejects_prompt_secret_and_raw_response_sentinels(
    payload_patch: dict[str, object],
) -> None:
    payload = _grammar_payload()
    payload["diagnostics"] = payload_patch

    with pytest.raises(PayloadContractError, match="forbidden_payload_content"):
        prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload=payload,
            usage_event_draft=_usage_draft(),
        )
