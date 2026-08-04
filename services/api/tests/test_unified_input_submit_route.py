# task-history: D6-I3F (renamed from test_d6_i3f_unified_input_submit_route.py)
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_input_adapter import InputSuitabilityRequest, InputSuitabilityResult
from app.schemas.reader_orchestration import ReaderSnapshotRecord
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationError,
    CandidateDocumentCreationResult,
)
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationError,
    StableReadyInputApplicationResult,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

READING_RECORD_ID = UUID("00000000-0000-0000-0000-000000000601")
STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000602")
CANDIDATE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000603")
USER_ID = UUID("00000000-0000-0000-0000-000000000604")
BASE_ID = UUID("00000000-0000-0000-0000-000000000605")
ARTICLE_READY_EVENT_ID = UUID("00000000-0000-0000-0000-000000000606")
ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000607")

RECORD_GENERATION = 1
DOCUMENT_VERSION = 1
ARTICLE_READY_SEQUENCE = 11
CONTENT_SHA256 = "a" * 64
CANONICAL_TEXT_SHA256 = "b" * 64
BLOCK_COUNT = 3
TITLE = "Unified Input Route"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path() -> str:
    return "/reader/records/input"


def test_plain_text_route_is_unregistered() -> None:
    app = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/reader/records/plain-text",
            json={"plain_text": "must use the unified input route"},
        )

    assert response.status_code == 404


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


def _mock_stable_application_service_init():
    return patch(
        "app.services.reader_orchestration.stable_ready_input_application_service."
        "StableReadyInputApplicationService.__init__",
        return_value=None,
    )


def _mock_candidate_creation_service_init():
    return patch(
        "app.services.reader_orchestration.candidate_document_creation_service."
        "CandidateDocumentCreationService.__init__",
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
    return build_reader_plate_snapshot(
        build_result,
        snapshot_taken_at=NOW,
        last_event_sequence=ARTICLE_READY_SEQUENCE,
        record=record,
        snapshot_id="snapshot-unified-route-test",
    )


def _build_suitability(
    *,
    outcome: str,
    source_type: str,
    word_count: int = 180,
    english_word_ratio: float = 0.99,
    natural_language_score: float = 0.97,
    flags: list[str] | None = None,
    reasons: list[str] | None = None,
    normalized_preview: str = "This is a route preview.",
) -> InputSuitabilityResult:
    return InputSuitabilityResult(
        outcome=outcome,
        source_type=source_type,
        word_count=word_count,
        english_word_ratio=english_word_ratio,
        natural_language_score=natural_language_score,
        flags=flags or [],
        reasons=reasons or [],
        normalized_preview=normalized_preview,
    )


def _build_stable_result(
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
        suitability=_build_suitability(
            outcome="stable_document_ready",
            source_type=source_type,
        ),
        snapshot=snapshot,
    )


def _build_candidate_result(
    *,
    source_type: str = "markdown_file",
    filename: str | None = "table.md",
    title: str | None = TITLE,
) -> CandidateDocumentCreationResult:
    return CandidateDocumentCreationResult(
        reading_record_id=READING_RECORD_ID,
        candidate_document_id=CANDIDATE_DOCUMENT_ID,
        record_generation=RECORD_GENERATION,
        status="ready",
        suitability=_build_suitability(
            outcome="candidate_document_required",
            source_type=source_type,
            flags=["markdown_complex_structure"],
            reasons=["table structure requires candidate review"],
        ),
        title=title,
        block_count=BLOCK_COUNT,
        source_type=source_type,
        filename=filename,
        original_input_id=ORIGINAL_INPUT_ID,
    )


def _stable_application_error_with_cause(
    cause: Exception,
    message: str = "stable-ready input failed",
) -> StableReadyInputApplicationError:
    error = StableReadyInputApplicationError(message)
    error.__cause__ = cause
    return error


def _candidate_creation_error_with_cause(
    cause: Exception,
    message: str = "candidate document creation failed",
) -> CandidateDocumentCreationError:
    error = CandidateDocumentCreationError(message)
    error.__cause__ = cause
    return error


def test_submit_reader_input_stable_ready_routes_to_stable_service_and_trims_empty_client_record_id(
) -> None:
    app = _build_app()
    suitability = _build_suitability(
        outcome="stable_document_ready",
        source_type="pasted_text",
    )
    result = _build_stable_result(source_type="pasted_text")
    mock_stable = AsyncMock(return_value=result)
    mock_candidate = AsyncMock()

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=suitability,
        ) as mock_gate,
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "A valid stable-ready paragraph for routing.",
                "source_metadata": {"source_kind": "route_test"},
                "client_record_id": "   ",
                "language": "en",
            },
        )

    assert response.status_code == 200
    mock_gate.assert_called_once()
    gate_request = mock_gate.call_args.args[0]
    assert isinstance(gate_request, InputSuitabilityRequest)
    assert gate_request.model_dump() == {
        "source_type": "pasted_text",
        "text": "A valid stable-ready paragraph for routing.",
        "filename": None,
        "source_metadata": {"source_kind": "route_test"},
    }
    mock_stable.assert_awaited_once_with(
        user_id=USER_ID,
        source_type="pasted_text",
        text="A valid stable-ready paragraph for routing.",
        filename=None,
        source_metadata={"source_kind": "route_test"},
        client_record_id=None,
        language="en",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        # L2/A4 — route parses once and shares the MarkdownParseResult.
        preparsed=ANY,
    )
    mock_candidate.assert_not_awaited()
    assert response.json() == {
        "outcome": "stable_document_ready",
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


def test_submit_reader_input_candidate_routes_to_candidate_service() -> None:
    app = _build_app()
    suitability = _build_suitability(
        outcome="candidate_document_required",
        source_type="markdown_file",
        flags=["markdown_complex_structure"],
        reasons=["table structure requires candidate review"],
    )
    result = _build_candidate_result(source_type="markdown_file", filename="table.md")
    mock_stable = AsyncMock()
    mock_candidate = AsyncMock(return_value=result)

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=suitability,
        ) as mock_gate,
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
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

    assert response.status_code == 200
    gate_request = mock_gate.call_args.args[0]
    assert gate_request.model_dump() == {
        "source_type": "markdown_file",
        "text": "# Title\n\n| A | B |",
        "filename": "table.md",
        "source_metadata": {},
    }
    mock_candidate.assert_awaited_once_with(
        user_id=USER_ID,
        source_type="markdown_file",
        text="# Title\n\n| A | B |",
        filename="table.md",
        source_metadata=None,
        client_record_id=None,
        language=None,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        # L2/A4 — route parses once and shares the MarkdownParseResult.
        preparsed=ANY,
    )
    mock_stable.assert_not_awaited()
    assert response.json() == {
        "outcome": "candidate_document_required",
        "reading_record_id": str(result.reading_record_id),
        "candidate_document_id": str(result.candidate_document_id),
        "record_generation": result.record_generation,
        "status": result.status,
        "title": result.title,
        "block_count": result.block_count,
        "source_type": result.source_type,
        "filename": result.filename,
        "original_input_id": str(result.original_input_id),
        "suitability": result.suitability.model_dump(mode="json"),
    }


def test_submit_reader_input_rejected_returns_200_without_calling_services() -> None:
    app = _build_app()
    suitability = _build_suitability(
        outcome="input_rejected_or_action_required",
        source_type="pasted_text",
        word_count=9,
        english_word_ratio=0.95,
        natural_language_score=0.88,
        flags=["too_short_for_learning"],
        reasons=["English content is too short for learning."],
        normalized_preview="Too short preview.",
    )
    mock_stable = AsyncMock()
    mock_candidate = AsyncMock()

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=suitability,
        ) as mock_gate,
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text",
                "text": "Too short body.",
            },
        )

    assert response.status_code == 200
    mock_gate.assert_called_once()
    mock_stable.assert_not_awaited()
    mock_candidate.assert_not_awaited()
    assert response.json() == {
        "outcome": "input_rejected_or_action_required",
        "suitability": suitability.model_dump(mode="json"),
    }


def test_submit_reader_input_rejects_unknown_source_type() -> None:
    app = _build_app()

    with (_mock_auth(), TestClient(app) as client):
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


def test_submit_reader_input_rejects_blank_text() -> None:
    app = _build_app()

    with (_mock_auth(), TestClient(app) as client):
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


def test_submit_reader_input_rejects_unknown_extra_field() -> None:
    app = _build_app()

    with (_mock_auth(), TestClient(app) as client):
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


@pytest.mark.parametrize(
    "field_name",
    [
        "canonicalizer_version",
        "builder_version",
        "segmenter_version",
    ],
)
def test_submit_reader_input_rejects_client_version_override_fields(
    field_name: str,
) -> None:
    app = _build_app()

    with (_mock_auth(), TestClient(app) as client):
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


def test_submit_reader_input_maps_stable_service_normalization_error_to_422() -> None:
    app = _build_app()
    gate_suitability = _build_suitability(
        outcome="stable_document_ready",
        source_type="pasted_text",
    )
    normalization_suitability = _build_suitability(
        outcome="candidate_document_required",
        source_type="markdown_file",
        flags=["markdown_complex_structure"],
        reasons=["contains table structure that requires candidate confirmation"],
    )
    mock_stable = AsyncMock(
        side_effect=_stable_application_error_with_cause(
            InputDocumentNormalizationError(suitability=normalization_suitability)
        )
    )
    mock_candidate = AsyncMock()

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=gate_suitability,
        ),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
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

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "Stable-ready input normalization failed: "
            "outcome=candidate_document_required, "
            "flags=['markdown_complex_structure'], "
            "reasons=['contains table structure that requires candidate confirmation']"
        )
    }
    mock_candidate.assert_not_awaited()


def test_submit_reader_input_maps_candidate_service_error_to_409() -> None:
    app = _build_app()
    suitability = _build_suitability(
        outcome="candidate_document_required",
        source_type="markdown_file",
        flags=["markdown_complex_structure"],
        reasons=["table structure requires candidate review"],
    )
    mock_stable = AsyncMock()
    mock_candidate = AsyncMock(
        side_effect=CandidateDocumentCreationError("candidate creation failed")
    )

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=suitability,
        ),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
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

    assert response.status_code == 409
    assert response.json() == {"detail": "candidate creation failed"}
    mock_stable.assert_not_awaited()


@pytest.mark.parametrize("branch", ["stable", "candidate"])
def test_submit_reader_input_maps_client_record_id_unique_conflict_to_409(
    branch: str,
) -> None:
    app = _build_app()
    unique_violation = asyncpg.UniqueViolationError("duplicate client_record_id")
    unique_violation.constraint_name = "uq_reading_records_user_client_active"
    wrapped_cause = RuntimeError("shell create failed")
    wrapped_cause.__cause__ = unique_violation

    if branch == "stable":
        gate_suitability = _build_suitability(
            outcome="stable_document_ready",
            source_type="pasted_text",
        )
        mock_stable = AsyncMock(
            side_effect=_stable_application_error_with_cause(
                wrapped_cause,
                message="stable service shell create failed",
            )
        )
        mock_candidate = AsyncMock()
    else:
        gate_suitability = _build_suitability(
            outcome="candidate_document_required",
            source_type="markdown_file",
            flags=["markdown_complex_structure"],
            reasons=["table structure requires candidate review"],
        )
        mock_stable = AsyncMock()
        mock_candidate = AsyncMock(
            side_effect=_candidate_creation_error_with_cause(
                wrapped_cause,
                message="candidate service shell create failed",
            )
        )

    with (
        _mock_auth(),
        _mock_stable_application_service_init(),
        _mock_candidate_creation_service_init(),
        patch(
            "app.api.routes.reader_orchestration.evaluate_input_suitability",
            return_value=gate_suitability,
        ),
        patch(
            "app.services.reader_orchestration.stable_ready_input_application_service."
            "StableReadyInputApplicationService."
            "freeze_stable_ready_input_and_load_snapshot",
            new=mock_stable,
        ),
        patch(
            "app.services.reader_orchestration.candidate_document_creation_service."
            "CandidateDocumentCreationService."
            "create_candidate_document_from_input",
            new=mock_candidate,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "source_type": "pasted_text" if branch == "stable" else "markdown_file",
                "text": "Valid body." if branch == "stable" else "# Title\n\n| A | B |",
                "filename": None if branch == "stable" else "table.md",
                "client_record_id": "dup-001",
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "client_record_id already exists for this user"}
    assert "Traceback" not in response.text
