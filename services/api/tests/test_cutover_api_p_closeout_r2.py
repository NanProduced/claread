"""Static closeout guards for CUTOVER-API-P-CLOSEOUT-R2."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.reader_record_ask_stream import ReaderRecordAskHistoryMessage
from app.services.reader_record_ask import thread_service

API_ROOT = Path(__file__).resolve().parents[1]
RECORD_ID = UUID("00000000-0000-0000-0000-0000000000a6")
THREAD_ID = UUID("00000000-0000-0000-0000-0000000000c6")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _read(relative_path: str) -> str:
    return (API_ROOT / relative_path).read_text(encoding="utf-8")


def _top_level_function_names(relative_path: str) -> set[str]:
    tree = ast.parse(_read(relative_path), filename=relative_path)
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def _class_names(relative_path: str) -> set[str]:
    tree = ast.parse(_read(relative_path), filename=relative_path)
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def test_reader_input_api_fixtures_are_canonical_without_transport_rewrite() -> None:
    source = _read("tests/test_reader_orchestration_api.py")

    assert "_UnifiedInputTestClient" not in source
    assert '"plain_text"' not in source
    assert '"source_type": "pasted_text"' in source
    assert '"text":' in source


def test_analysis_thread_crud_and_legacy_schema_symbols_are_absent() -> None:
    thread_functions = _top_level_function_names(
        "app/services/reader_record_ask/thread_service.py"
    )
    repository_functions = _top_level_function_names(
        "app/services/reader_record_ask/repository.py"
    )
    assert not thread_functions & {
        "list_analysis_threads",
        "create_analysis_thread",
        "get_thread_detail",
        "reset_analysis_thread",
    }
    assert not repository_functions & {
        "ensure_record_access",
        "list_threads",
        "get_or_create_default_thread",
    }

    schema_classes = _class_names("app/schemas/reader_ask.py")
    assert not schema_classes & {
        "ReaderAskThreadCreateRequest",
        "ReaderAskMessage",
        "ReaderAskThreadDetail",
        "ReaderAskMessageStreamRequest",
        "ReaderAskUserVisibleOutput",
        "ReaderAskCompletedPayload",
    }
    assert not (API_ROOT / "app/schemas/tasks.py").exists()


def test_plain_text_route_is_absent_without_banning_valid_analysis_record_id() -> None:
    route_source = _read("app/api/routes/reader_orchestration.py")
    assert "/records/plain-text" not in route_source

    client = TestClient(create_app())
    response = client.post(
        "/reader/records/plain-text",
        json={"source_type": "pasted_text", "text": "must use input"},
    )
    assert response.status_code == 404


def test_v2_history_wire_drops_old_sidecar_and_v1_identity() -> None:
    fields = set(ReaderRecordAskHistoryMessage.model_fields)
    assert "article_rag" not in fields
    assert "reader_record_ask_agentic_v1" not in str(
        ReaderRecordAskHistoryMessage.model_fields["execution_version"].annotation
    )
    assert "reader_record_ask_agentic_v2" in str(
        ReaderRecordAskHistoryMessage.model_fields["execution_version"].annotation
    )

    projection_source = _read(
        "app/services/reader_record_ask/history_projection.py"
    )
    assert '"article_rag":' not in projection_source
    assert "quarantine_untrusted_agentic_claim" in projection_source
    assert 'visible.get("execution_version") != EXECUTION_VERSION_AGENTIC_V2' in (
        projection_source
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker",
    ["reader_record_ask_agentic_v1", None, "unknown"],
)
async def test_history_v1_missing_unknown_markers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    marker: str | None,
) -> None:
    monkeypatch.setattr(
        thread_service.repo,
        "get_thread",
        AsyncMock(
            return_value={
                "record_scope": "reading_record",
                "reading_record_id": str(RECORD_ID),
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
            reading_record_id=RECORD_ID,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "history_execution_version_untrusted"
