# task-history: (renamed from test_d6_i3d_stable_ready_input_route.py)
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_input_adapter import InputSuitabilityResult
from app.schemas.reader_orchestration import ReaderSnapshotRecord
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationError,
    StableReadyInputApplicationResult,
)
from tests.reader_orchestration_test_support import fixture_analysis_progress

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000401")
STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000402")
USER_ID = UUID("00000000-0000-0000-0000-000000000403")
BASE_ID = UUID("00000000-0000-0000-0000-000000000404")
ARTICLE_READY_EVENT_ID = UUID("00000000-0000-0000-0000-000000000405")

RECORD_GENERATION = 4
DOCUMENT_VERSION = 2
ARTICLE_READY_SEQUENCE = 19
CONTENT_SHA256 = "a" * 64
CANONICAL_TEXT_SHA256 = "b" * 64
BLOCK_COUNT = 3
TITLE = "Stable Ready Route"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path() -> str:
    return "/reader/records/stable-ready-input"


def _session_info(user_id: UUID = USER_ID) -> object:
    return type(
        "SessionInfo",
        (),
        {
            "user_id": user_id,
            "session_id": uuid4(),
        },
    )()


def _mock_auth(user_id: UUID = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


def _mock_application_service_init():
    return patch(
        "app.services.reader_orchestration.stable_ready_input_application_service."
        "StableReadyInputApplicationService.__init__",
        return_value=None,
    )


def _build_snapshot(
    *,
    record_id: UUID = READING_RECORD_ID,
    base_id: UUID = BASE_ID,
    generation: int = RECORD_GENERATION,
) -> object:
    build_result = build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=str(record_id),
            base_id=str(base_id),
            source_text="First sentence.\n\nSecond paragraph.",
            title=TITLE,
            language="en",
        )
    )
    record = ReaderSnapshotRecord(
        title=TITLE,
        created_at=NOW,
        source_type="text",
        source_metadata={"source_kind": "route_test"},
        generation=generation,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )
    return build_reader_plate_snapshot(build_result,
        analysis_progress=fixture_analysis_progress(),
snapshot_taken_at=NOW,
        last_event_sequence=ARTICLE_READY_SEQUENCE,
        record=record,
        snapshot_id="snapshot-stable-ready-route-test",
    )


def _build_suitability(source_type: str = "pasted_text") -> InputSuitabilityResult:
    return InputSuitabilityResult(
        outcome="stable_document_ready",
        source_type=source_type,
        word_count=180,
        english_word_ratio=0.99,
        natural_language_score=0.97,
        flags=[],
        reasons=[],
        normalized_preview="This is a stable-ready preview.",
    )


def _build_result(
    *,
    source_type: str = "pasted_text",
    title: str | None = TITLE,
) -> StableReadyInputApplicationResult:
    snapshot = _build_snapshot()
    return StableReadyInputApplicationResult(
        reading_record_id=READING_RECORD_ID,
        stable_document_id=STABLE_DOCUMENT_ID,
        base_id=BASE_ID,
        record_generation=RECORD_GENERATION,
        document_version=DOCUMENT_VERSION,
        title=title,
        content_sha256=CONTENT_SHA256,
        canonical_text_sha256=CANONICAL_TEXT_SHA256,
        block_count=BLOCK_COUNT,
        article_ready_event_id=ARTICLE_READY_EVENT_ID,
        article_ready_sequence=ARTICLE_READY_SEQUENCE,
        suitability=_build_suitability(source_type=source_type),
        snapshot=snapshot,
    )


def _application_error_with_cause(
    cause: Exception,
    message: str = "stable-ready input failed",
) -> StableReadyInputApplicationError:
    error = StableReadyInputApplicationError(message)
    error.__cause__ = cause
    return error


def test_submit_stable_ready_input_happy_path_pasted_text_calls_service_and_serializes_response() -> None:
    app = _build_app()
    result = _build_result(source_type="pasted_text")
    mock_freeze = AsyncMock(return_value=result)

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "A valid stable-ready paragraph for routing.",
                "source_metadata": {
                    "source_kind": "route_test",
                    "import_id": "import-001",
                },
                "client_record_id": " client-route-001 ",
                "language": "en",
            },
        )

    assert response.status_code == 200
    mock_freeze.assert_awaited_once_with(
        user_id=USER_ID,
        source_type="pasted_text",
        text="A valid stable-ready paragraph for routing.",
        filename=None,
        source_metadata={
            "source_kind": "route_test",
            "import_id": "import-001",
        },
        client_record_id="client-route-001",
        language="en",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    assert response.json() == {
        "reading_record_id": str(result.reading_record_id),
        "stable_document_id": str(result.stable_document_id),
        "base_id": str(result.base_id),
        "record_generation": result.record_generation,
        "document_version": result.document_version,
        "title": result.title,
        "content_sha256": result.content_sha256,
        "canonical_text_sha256": result.canonical_text_sha256,
        "block_count": result.block_count,
        "article_ready_event_id": str(result.article_ready_event_id),
        "article_ready_sequence": result.article_ready_sequence,
        "suitability": result.suitability.model_dump(mode="json"),
        "snapshot": result.snapshot.model_dump(mode="json"),
    }


def test_submit_stable_ready_input_happy_path_markdown_file_passes_filename() -> None:
    app = _build_app()
    result = _build_result(source_type="markdown_file")
    mock_freeze = AsyncMock(return_value=result)

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "markdown_file",
                "text": "# Stable Ready\n\nParagraph body.",
                "filename": "weekly-review.md",
            },
        )

    assert response.status_code == 200
    mock_freeze.assert_awaited_once_with(
        user_id=USER_ID,
        source_type="markdown_file",
        text="# Stable Ready\n\nParagraph body.",
        filename="weekly-review.md",
        source_metadata=None,
        client_record_id=None,
        language=None,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )


def test_submit_stable_ready_input_rejects_unknown_extra_field() -> None:
    app = _build_app()
    mock_freeze = AsyncMock()

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "Valid body.",
                "unexpected": True,
            },
        )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "unexpected"] for error in response.json()["detail"])
    mock_freeze.assert_not_awaited()


def test_submit_stable_ready_input_rejects_blank_text() -> None:
    app = _build_app()
    mock_freeze = AsyncMock()

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "   ",
            },
        )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "text"] for error in response.json()["detail"])
    mock_freeze.assert_not_awaited()


def test_submit_stable_ready_input_rejects_unknown_source_type() -> None:
    app = _build_app()
    mock_freeze = AsyncMock()

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "docx_file",
                "text": "Valid body.",
            },
        )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "source_type"] for error in response.json()["detail"])
    mock_freeze.assert_not_awaited()


def test_submit_stable_ready_input_maps_normalization_rejection_to_422() -> None:
    app = _build_app()
    suitability = InputSuitabilityResult(
        outcome="candidate_document_required",
        source_type="markdown_file",
        word_count=420,
        english_word_ratio=0.98,
        natural_language_score=0.91,
        flags=["markdown_complex_structure"],
        reasons=["contains table structure that requires candidate confirmation"],
        normalized_preview="Preview",
    )
    mock_freeze = AsyncMock(
        side_effect=_application_error_with_cause(
            InputDocumentNormalizationError(suitability=suitability)
        )
    )

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "markdown_file",
                "text": "# Title\n\n| A | B |",
                "filename": "table.md",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "Stable-ready input normalization failed: "
            "outcome=candidate_document_required, "
            "flags=['markdown_complex_structure'], "
            "reasons=['contains table structure that requires candidate confirmation']"
        )
    }
    assert "Traceback" not in response.text


def test_submit_stable_ready_input_maps_client_record_id_unique_conflict_to_409() -> None:
    app = _build_app()
    unique_violation = asyncpg.UniqueViolationError("duplicate client_record_id")
    unique_violation.constraint_name = "uq_reading_records_user_client_active"
    wrapped_cause = RuntimeError("shell create failed")
    wrapped_cause.__cause__ = unique_violation
    mock_freeze = AsyncMock(
        side_effect=_application_error_with_cause(
            wrapped_cause,
            message="Failed to create the stable-ready reading record shell",
        )
    )

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "Valid body.",
                "client_record_id": "dup-001",
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "client_record_id already exists for this user"
    }
    assert "Traceback" not in response.text


def test_submit_stable_ready_input_maps_other_application_error_to_409() -> None:
    app = _build_app()
    mock_freeze = AsyncMock(
        side_effect=StableReadyInputApplicationError("stable-ready freeze failed")
    )

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "Valid body.",
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "stable-ready freeze failed"}
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    "field_name",
    [
        "canonicalizer_version",
        "builder_version",
        "segmenter_version",
    ],
)
def test_submit_stable_ready_input_rejects_client_version_override_fields(
    field_name: str,
) -> None:
    app = _build_app()
    mock_freeze = AsyncMock()

    with (
        _mock_auth(),
        _mock_application_service_init(),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_freeze,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "Valid body.",
                field_name: "client-side-version",
            },
        )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", field_name] for error in response.json()["detail"])
    mock_freeze.assert_not_awaited()
