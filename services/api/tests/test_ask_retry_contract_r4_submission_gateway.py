"""ASK-RETRY-CONTRACT-R4 unit tests for submission gateway + lane (no DB)."""

from __future__ import annotations

from uuid import uuid4

from app.services.reader_record_ask.service import (
    _extract_snapshot_model_option_key,
    _resolve_persisted_retry_lane,
)
from app.services.reader_record_ask.submission_gateway import (
    RETRY_CONTRACT_VERSION,
    build_retry_snapshot,
)


def test_retry_snapshot_is_immutable_shape() -> None:
    snap = build_retry_snapshot(
        lane="agentic",
        model_option_key="ask-clarity",
        web_search_mode="disabled",
        route_identity="reader_ask",
    )
    assert snap["retry_contract_version"] == RETRY_CONTRACT_VERSION
    assert snap["retry_lane"] == "agentic"
    assert snap["model_option_key"] == "ask-clarity"
    assert snap["web_search_mode"] == "disabled"
    assert "execution_version" in snap


def test_lane_from_retry_snapshot_prefers_snapshot() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={
            "metadata_json": {
                "retry_snapshot": {
                    "retry_lane": "legacy",
                    "execution_version": "reader_record_ask_legacy",
                }
            }
        },
        user_msg={"metadata_json": {}},
    )
    assert lane == "legacy"


def test_lane_agentic_snapshot_ignores_missing_flag_semantics() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={
            "metadata_json": {
                "retry_snapshot": {
                    "retry_lane": "agentic",
                    "execution_version": "reader_record_ask_agentic_v2",
                    "model_option_key": "ask-fast",
                }
            }
        },
        user_msg={"metadata_json": {}},
    )
    assert lane == "agentic"


def test_model_option_from_snapshot_only() -> None:
    key = _extract_snapshot_model_option_key(
        assistant_msg={
            "metadata_json": {
                "retry_snapshot": {"model_option_key": "ask-clarity"}
            }
        },
        user_msg={"metadata_json": {}},
    )
    assert key == "ask-clarity"


def test_unknown_lane_token_fail_closed() -> None:
    assert (
        _resolve_persisted_retry_lane(
            assistant_msg={"metadata_json": {"execution_version": "weird"}},
            user_msg={"metadata_json": {}},
        )
        is None
    )


def test_empty_metadata_fail_closed_not_legacy_guess() -> None:
    assert (
        _resolve_persisted_retry_lane(
            assistant_msg={"metadata_json": {}},
            user_msg={"metadata_json": {}},
        )
        is None
    )


def test_submission_reconcile_status_literals_on_dto() -> None:
    from app.schemas.reader_ask import ReaderAskSubmissionReconcileResponse

    dto = ReaderAskSubmissionReconcileResponse(
        client_submission_id=str(uuid4()),
        thread_id=str(uuid4()),
        status="completed",
        assistant_message_id=str(uuid4()),
        user_message_id=str(uuid4()),
    )
    assert dto.status == "completed"
