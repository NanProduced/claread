"""ASK-RETRY-CONTRACT-R0–R3 focused unit tests (no real model / DB)."""

from __future__ import annotations

from uuid import uuid4

from app.services.reader_record_ask.service import _resolve_persisted_retry_lane


def test_retry_lane_agentic_from_turn_run_execution_version() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={
            "turn_run_execution_version": "reader_record_ask_agentic_v2",
            "metadata_json": {},
        },
        user_msg={"metadata_json": {}},
    )
    assert lane == "agentic"


def test_retry_lane_agentic_from_user_metadata() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={"metadata_json": {}},
        user_msg={
            "metadata_json": {
                "execution_version": "reader_record_ask_agentic_v2",
            }
        },
    )
    assert lane == "agentic"


def test_retry_lane_missing_snapshot_fail_closed() -> None:
    """R4: no trusted snapshot → None (409), never guess legacy."""
    lane = _resolve_persisted_retry_lane(
        assistant_msg={"metadata_json": {}},
        user_msg={"metadata_json": {}},
    )
    assert lane is None


def test_retry_lane_legacy_explicit_marker() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={"metadata_json": {"retry_lane": "legacy"}},
        user_msg={"metadata_json": {}},
    )
    assert lane == "legacy"


def test_retry_lane_unknown_token_fail_closed() -> None:
    lane = _resolve_persisted_retry_lane(
        assistant_msg={"metadata_json": {"execution_version": "totally_unknown_lane"}},
        user_msg={"metadata_json": {}},
    )
    assert lane is None


def test_retry_lane_ignores_live_flag_semantics() -> None:
    """Lane resolution must not import or consult the live feature flag."""
    agentic = _resolve_persisted_retry_lane(
        assistant_msg={
            "metadata_json": {"execution_version": "reader_record_ask_agentic_v1"}
        },
        user_msg={"metadata_json": {}},
    )
    missing = _resolve_persisted_retry_lane(
        assistant_msg={"metadata_json": {}},
        user_msg={"metadata_json": {}},
    )
    assert agentic == "agentic"
    assert missing is None


def test_client_submission_id_is_uuid_field_on_request_schema() -> None:
    from app.schemas.reader_ask import ReaderRecordAskMessageRequest

    sid = uuid4()
    body = ReaderRecordAskMessageRequest(
        content="hello",
        client_submission_id=sid,
    )
    assert body.client_submission_id == sid


def test_submission_reconcile_schema_status_literals() -> None:
    from app.schemas.reader_ask import ReaderAskSubmissionReconcileResponse

    dto = ReaderAskSubmissionReconcileResponse(
        client_submission_id=str(uuid4()),
        thread_id=str(uuid4()),
        status="not_found",
    )
    assert dto.status == "not_found"
    assert dto.user_message_id is None
