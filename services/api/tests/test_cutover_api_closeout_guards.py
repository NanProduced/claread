# task-history: CUTOVER-API-P-CLOSEOUT- (renamed from test_cutover_api_p_closeout_r2.py)
"""Static closeout guards for the API cutover."""

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

pytestmark = [
    pytest.mark.chain_infra,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

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


def _top_level_function_node(
    relative_path: str, name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(_read(relative_path), filename=relative_path)
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing top-level function: {relative_path}:{name}")


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
    schema_source = _read("app/schemas/reader_ask.py")
    for removed_name in {
        "ReaderAskEvidenceKind",
        "ReaderAskEvidenceScope",
        "ReaderAskEvidenceItem",
        "ReaderAskArticleRagStatus",
        "ReaderAskArticleRagCitationContent",
        "ReaderAskArticleRagCitation",
        "ReaderAskArticleRagSidecar",
    }:
        assert removed_name not in schema_source
    assert not (API_ROOT / "app/schemas/tasks.py").exists()


def test_model_option_resolution_is_v2_scoped_and_identity_fenced() -> None:
    node = _top_level_function_node(
        "app/services/reader_record_ask/thread_service.py",
        "resolve_and_persist_thread_model_option",
    )
    params = list(node.args.kwonlyargs)
    record_index = next(
        index for index, param in enumerate(params) if param.arg == "reading_record_id"
    )
    record_param = params[record_index]
    assert ast.unparse(record_param.annotation) == "UUID"
    assert node.args.kw_defaults[record_index] is None

    function_source = ast.unparse(node)
    assert "reading_record_id is not None" not in function_source
    # DATA-LEGACY-IDENTITY-EXIT: record_scope collapsed into the
    # reading_record_id identity fence.
    assert "thread.get('reading_record_id') != str(reading_record_id)" in function_source
    assert "reading_record" in function_source
    assert "str(reading_record_id)" in function_source
    assert "legacy stream" not in function_source.lower()
    assert "v2" in (ast.get_docstring(node) or "").lower()

    service_tree = ast.parse(
        _read("app/services/reader_record_ask/service.py"),
        filename="app/services/reader_record_ask/service.py",
    )
    calls = [
        call
        for call in ast.walk(service_tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "resolve_and_persist_thread_model_option"
    ]
    assert len(calls) == 2
    assert all(
        any(keyword.arg == "reading_record_id" for keyword in call.keywords)
        for call in calls
    )


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
