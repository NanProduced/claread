"""ASK-RETRY-CONTRACT-R5 unit tests (no real DB / no migration execution)."""

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


def test_r5_snapshot_contract_version() -> None:
    snap = build_retry_snapshot(
        lane="agentic",
        model_option_key="ask-clarity",
        web_search_mode="allowed",
    )
    assert snap["retry_contract_version"] == RETRY_CONTRACT_VERSION
    assert snap["model_option_key"] == "ask-clarity"
    assert snap["retry_lane"] == "agentic"


def test_r5_legacy_snapshot_lane() -> None:
    snap = build_retry_snapshot(
        lane="legacy",
        model_option_key="ask-fast",
        web_search_mode="disabled",
    )
    assert snap["retry_lane"] == "legacy"
    assert snap["execution_version"] == "reader_record_ask_legacy"


def test_r5_missing_lane_fail_closed() -> None:
    assert (
        _resolve_persisted_retry_lane(
            assistant_msg={"metadata_json": {}},
            user_msg={"metadata_json": {}},
        )
        is None
    )


def test_r5_model_key_from_snapshot() -> None:
    key = _extract_snapshot_model_option_key(
        assistant_msg={
            "metadata_json": {
                "retry_snapshot": {"model_option_key": "ask-clarity"}
            }
        },
        user_msg={"metadata_json": {}},
    )
    assert key == "ask-clarity"


def test_r5_reconcile_response_schema_has_hydrate_fields() -> None:
    from app.schemas.reader_ask import (
        ReaderAskSubmissionPublicMessage,
        ReaderAskSubmissionReconcileResponse,
    )

    pub = ReaderAskSubmissionPublicMessage(
        id=str(uuid4()),
        thread_id=str(uuid4()),
        role="assistant",
        status="completed",
        content_md="完整回答正文。",
    )
    dto = ReaderAskSubmissionReconcileResponse(
        client_submission_id=str(uuid4()),
        thread_id=str(uuid4()),
        status="completed",
        assistant_message_id=pub.id,
        assistant_message=pub,
        action_hint="none",
    )
    assert dto.assistant_message is not None
    assert dto.assistant_message.content_md == "完整回答正文。"


def test_r5_submission_idempotency_unavailable_type() -> None:
    from app.services.reader_record_ask.repository import (
        SubmissionIdempotencyUnavailable,
    )

    err = SubmissionIdempotencyUnavailable()
    assert "0026" in str(err)


def test_r5_0026_has_claim_generation() -> None:
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "migrations"
        / "0026_reader_ask_client_submission_idempotency.sql"
    ).read_text(encoding="utf-8")
    assert "claim_generation" in sql
    assert "PRIMARY KEY (thread_id, client_submission_id)" in sql
