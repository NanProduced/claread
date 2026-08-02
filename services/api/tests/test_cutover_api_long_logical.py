"""Focused offline regressions for CUTOVER-API-LONG Logical stage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import health
from app.main import create_app
from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2
from app.services.reader_record_ask import repository, service, thread_service
from app.services.reader_record_ask.submission_gateway import build_retry_snapshot

RECORD_ID = "00000000-0000-0000-0000-0000000000a6"
THREAD_ID = UUID("00000000-0000-0000-0000-0000000000c6")
MESSAGE_ID = UUID("00000000-0000-0000-0000-0000000000d6")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_logical_route_surface_is_nested_v2_only() -> None:
    app = create_app()
    paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }

    assert "/reader/records/{reading_record_id}/ask/model-options" in paths
    assert "/reader/records/{reading_record_id}/ask/threads" in paths
    assert (
        "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream"
        in paths
    )
    assert (
        "/reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/"
        "{message_id}/retry/stream"
        in paths
    )
    assert (
        "/reader/records/{reading_record_id}/ask/threads/{thread_id}/submissions/"
        "{client_submission_id}"
        in paths
    )
    assert (
        "/reader/records/{reading_record_id}/ask/messages/{message_id}/citations/"
        "{citation_id}/navigate"
        in paths
    )

    forbidden_prefixes = (
        "/analyze",
        "/analysis-tasks",
        "/records",
        "/reader-ask",
        "/eval",
    )
    assert not any(
        path == prefix or path.startswith(f"{prefix}/")
        for path in paths
        for prefix in forbidden_prefixes
    )
    assert "/reader/records/{reading_record_id}/ask/messages" not in paths


def test_old_ingress_urls_return_404_without_entering_auth_or_db() -> None:
    client = TestClient(create_app())
    old_urls = (
        "/analyze",
        "/analysis-tasks",
        "/records",
        "/records/00000000-0000-0000-0000-0000000000a6",
        "/reader/example/scene",
        "/reader-ask",
        "/eval",
    )
    for url in old_urls:
        assert client.get(url).status_code == 404, url

    assert (
        client.post(
            f"/reader/records/{RECORD_ID}/ask/messages",
            json={"content": "old no-thread send"},
        ).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_health_does_not_require_or_report_old_worker_state(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            app_name="test",
            app_env="test",
            grammar_rag_enabled=False,
        ),
    )
    ready = AsyncMock(return_value=True)
    monkeypatch.setattr(health, "is_db_ready", ready)
    monkeypatch.setattr(health, "is_redis_ready", ready)

    payload = await health.health_check(object())  # type: ignore[arg-type]

    assert payload["status"] == "ok"
    assert "analysis_worker" not in payload
    assert "overview_worker" not in payload
    assert ready.await_count == 2


@pytest.mark.parametrize(
    "marker",
    [None, "reader_record_ask_agentic_v1", "legacy_unclassified", "unknown"],
)
def test_retry_execution_fence_rejects_non_v2_or_missing(marker: str | None) -> None:
    assistant: dict[str, object] = {"metadata_json": {}}
    user: dict[str, object] = {"metadata_json": {}}
    if marker is not None:
        assistant["metadata_json"] = {
            "retry_snapshot": {"execution_version": marker}
        }
        user["metadata_json"] = {"execution_version": marker}

    assert not service._has_persisted_v2_execution(
        assistant_msg=assistant,
        user_msg=user,
    )


def test_retry_execution_fence_accepts_explicit_consistent_v2() -> None:
    assert service._has_persisted_v2_execution(
        assistant_msg={
            "metadata_json": {
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                "retry_snapshot": {
                    "execution_version": EXECUTION_VERSION_AGENTIC_V2
                },
            },
            "turn_run_execution_version": EXECUTION_VERSION_AGENTIC_V2,
        },
        user_msg={
            "metadata_json": {
                "execution_version": EXECUTION_VERSION_AGENTIC_V2,
                "retry_snapshot": {
                    "execution_version": EXECUTION_VERSION_AGENTIC_V2
                },
            }
        },
    )


def test_new_send_snapshot_is_fixed_to_v2_without_a_lane_selector() -> None:
    snapshot = build_retry_snapshot(
        model_option_key="default",
        web_search_mode="disabled",
        route_identity="reader_record_ask",
    )

    assert snapshot["execution_version"] == EXECUTION_VERSION_AGENTIC_V2
    assert "retry_lane" not in snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker",
    [None, "reader_record_ask_agentic_v1", "legacy_unclassified", "unknown"],
)
async def test_retry_invalid_execution_fails_before_execution_resolution(
    monkeypatch,
    marker: str | None,
) -> None:
    class FakeRepository:
        async def get_thread(self, **kwargs):
            return {
                "id": str(THREAD_ID),
                "record_scope": "reading_record",
                "reading_record_id": RECORD_ID,
            }

        async def get_assistant_message_with_preceding_user_message(self, **kwargs):
            assistant: dict[str, object] = {"metadata_json": {}}
            user: dict[str, object] = {"metadata_json": {}}
            if marker is not None:
                assistant["metadata_json"] = {
                    "retry_snapshot": {"execution_version": marker}
                }
                user["metadata_json"] = {"execution_version": marker}
            return assistant, user

    resolver = AsyncMock()
    facts_loader = AsyncMock()
    monkeypatch.setattr(repository, "ReaderRecordAskRepository", FakeRepository)
    monkeypatch.setattr(service, "_resolve_agentic_execution", resolver)
    monkeypatch.setattr(service, "_load_snapshot_facts", facts_loader)

    with pytest.raises(HTTPException) as exc_info:
        await service.prepare_reading_record_ask_retry(
            user_id=USER_ID,
            reading_record_id=RECORD_ID,
            thread_id=THREAD_ID,
            message_id=MESSAGE_ID,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "retry_execution_version_untrusted"
    resolver.assert_not_awaited()
    facts_loader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, "reader_record_ask_agentic_v1", "unknown"])
async def test_history_rejects_non_v2_assistant_message(monkeypatch, marker: str | None) -> None:
    monkeypatch.setattr(
        thread_service.repo,
        "get_thread",
        AsyncMock(
            return_value={
                "record_scope": "reading_record",
                "reading_record_id": RECORD_ID,
            }
        ),
    )
    monkeypatch.setattr(
        thread_service.repo,
        "list_messages",
        AsyncMock(
            return_value=[
                {
                    "role": "assistant",
                    "execution_version": marker,
                }
            ]
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_service.get_reading_record_thread_detail(
            USER_ID,
            THREAD_ID,
            reading_record_id=UUID(RECORD_ID),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "history_execution_version_untrusted"


def test_reader_record_ask_sse_surface_has_no_legacy_aliases() -> None:
    package = Path(__file__).resolve().parents[1] / "app" / "services" / "reader_record_ask"
    forbidden = (
        "message.interrupted",
        "agentic.reasoning.started",
        "agentic.reasoning.delta",
        "agentic.reasoning.completed",
        "EVENT_MESSAGE_INTERRUPTED",
        "EVENT_AGENTIC_REASONING_",
    )
    for name in ("sse.py", "production_stream.py", "turn_lifecycle.py"):
        source = (package / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name}: {token}"


def test_active_api_has_no_legacy_shared_module_imports() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    forbidden = (
        "app.services.analysis.prompting",
        "app.services.analysis.credit_service",
        "app.workflow.tracing",
        "app.workflow.daily_reader_workflow",
    )
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path}: {token}"
