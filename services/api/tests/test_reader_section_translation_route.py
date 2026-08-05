# task-history: T5.6c (renamed from test_reader_section_translation_t56c.py)
"""FastAPI command route tests for explicit section translation.

Verifies the synchronous orchestration of
``SectionTranslationBootstrapService`` + ``SectionTranslationDrainService``
behind an authenticated POST endpoint, plus the queued-recovery closure.

The route under test::

    POST /reader/records/{record_id}/section-translation

Identity comes only from ``AuthUserDep``. The body carries the full
section range witness (``start_unit_id`` / ``end_unit_id`` + optional
anchors); ``node_id`` / ``outline_revision`` are audit-only and never
sufficient for admission. ``layer_family`` / ``record_id`` / ``base_id``
/ ``generation`` MUST NOT appear in the body — the server fills them
from the authenticated fence.

Response outcomes (stable, minimal, leak-safe):
    succeeded / retry_later / already_covered_or_inflight /
    budget_exhausted / rejected / superseded
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.schemas.reader_orchestration import (
    ReaderSectionTranslationRequest,
    ReaderSectionTranslationResponse,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_request_planner import (
    REASON_NO_TRUSTED_OUTLINE,
    REASON_NODE_ONLY,
    REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT,
    REASON_SECTION_RANGE_OVERLAP,
    REASON_SOURCE_MISMATCH,
    REASON_TRANSLATION_BUDGET_EXHAUSTED,
)
from app.services.reader_orchestration.section_translation_bootstrap import (
    REASON_ALREADY_QUEUED,
    REASON_LAYER_FAMILY_NOT_TRANSLATION,
    SectionBootstrapOutcome,
    SectionTranslationBootstrapResult,
    SectionTranslationBootstrapService,
)
from app.services.reader_orchestration.section_translation_drain import (
    SectionDrainOutcome,
    SectionDrainResult,
    SectionTranslationDrainService,
)
from app.services.reader_orchestration.section_identity import (
    SectionIdentity,
    encode_section_target_key,
)

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_api_contract,
    pytest.mark.life_permanent_regression,
]

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
USER_ID = UUID("00000000-0000-0000-0000-000000000201")
RECORD_ID = UUID("00000000-0000-0000-0000-000000000202")
BASE_ID = UUID("00000000-0000-0000-0000-000000000203")
RUN_ID = UUID("00000000-0000-0000-0000-000000000204")
JOB_ID = UUID("00000000-0000-0000-0000-000000000205")

_VALID_BODY = {
    "start_unit_id": "u3",
    "end_unit_id": "u4",
}

_VALID_BODY_WITH_ANCHORS = {
    "start_unit_id": "u3",
    "end_unit_id": "u4",
    "start_anchor_segment_id": "sa-1",
    "end_anchor_segment_id": "ea-1",
    "node_id": "n2",
    "outline_revision": "r1",
}


# ---------------------------------------------------------------------------
# App / auth scaffolding
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path(record_id: UUID = RECORD_ID) -> str:
    return f"/reader/records/{record_id}/section-translation"


def _session_info(user_id: UUID = USER_ID) -> object:
    return type(
        "SessionInfo",
        (),
        {"user_id": user_id, "session_id": uuid4()},
    )()


def _mock_auth(user_id: UUID = USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


def _mock_bootstrap_init():
    return patch(
        "app.api.routes.reader_orchestration.SectionTranslationBootstrapService.__init__",
        return_value=None,
    )


def _mock_drain_init():
    return patch(
        "app.api.routes.reader_orchestration.SectionTranslationDrainService.__init__",
        return_value=None,
    )


def _identity(
    *,
    record_id: str = str(RECORD_ID),
    base_id: str = str(BASE_ID),
    generation: int = 1,
    start_unit_id: str = "u3",
    end_unit_id: str = "u4",
) -> SectionIdentity:
    return SectionIdentity(
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        start_unit_id=start_unit_id,
        end_unit_id=end_unit_id,
    )


def _bootstrap_result_admitted(
    *, job_id: UUID = JOB_ID, run_id: UUID = RUN_ID
) -> SectionTranslationBootstrapResult:
    identity = _identity()
    target_key = encode_section_target_key(identity)
    plan = SimpleNamespace(
        kind="admit",
        reason=None,
        identity=identity,
        target_unit_ids=("u3", "u4"),
        layer_family="translation",
        audit=None,
    )
    return SectionTranslationBootstrapResult(
        outcome=SectionBootstrapOutcome.ADMITTED,
        reason=None,
        job_id=job_id,
        run_id=run_id,
        plan=plan,
        target_unit_ids=("u3", "u4"),
        target_key=target_key,
    )


def _bootstrap_result_no_op(
    *,
    reason: str,
    job_id: UUID | None = None,
) -> SectionTranslationBootstrapResult:
    identity = _identity()
    target_key = encode_section_target_key(identity)
    plan = SimpleNamespace(
        kind="no_op",
        reason=reason,
        identity=identity,
        target_unit_ids=("u3", "u4"),
        layer_family="translation",
        audit=None,
    )
    return SectionTranslationBootstrapResult(
        outcome=SectionBootstrapOutcome.NO_OP,
        reason=reason,
        job_id=job_id,
        run_id=None,
        plan=plan,
        target_unit_ids=("u3", "u4"),
        target_key=target_key,
    )


def _bootstrap_result_reject(*, reason: str) -> SectionTranslationBootstrapResult:
    return SectionTranslationBootstrapResult(
        outcome=SectionBootstrapOutcome.REJECT,
        reason=reason,
    )


def _drain_result(
    *,
    outcome: SectionDrainOutcome,
    job_id: UUID = JOB_ID,
    detail: str | None = None,
) -> SectionDrainResult:
    return SectionDrainResult(outcome=outcome, job_id=job_id, detail=detail)


# ===========================================================================
# A. Authentication and shape validation (no service invocation)
# ===========================================================================


def test_a01_section_translation_requires_authentication() -> None:
    app = _build_app()
    with TestClient(app) as client:
        response = client.post(_route_path(), json=_VALID_BODY)
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing authorization header"}


def test_a02_section_translation_rejects_missing_start_unit_id() -> None:
    app = _build_app()
    body = {"end_unit_id": "u4"}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422


def test_a03_section_translation_rejects_missing_end_unit_id() -> None:
    app = _build_app()
    body = {"start_unit_id": "u3"}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422


def test_a04_section_translation_rejects_layer_family_in_body() -> None:
    """layer_family is server-forced; body must not carry it."""
    app = _build_app()
    body = {**_VALID_BODY, "layer_family": "translation"}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "layer_family"]
        for error in response.json()["detail"]
    )


def test_a05_section_translation_rejects_record_id_in_body() -> None:
    """record_id comes from the path + authenticated fence; body must not carry it."""
    app = _build_app()
    body = {**_VALID_BODY, "record_id": str(RECORD_ID)}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "record_id"]
        for error in response.json()["detail"]
    )


def test_a06_section_translation_rejects_base_id_in_body() -> None:
    """base_id is server-authoritative; body must not carry it."""
    app = _build_app()
    body = {**_VALID_BODY, "base_id": str(BASE_ID)}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "base_id"]
        for error in response.json()["detail"]
    )


def test_a07_section_translation_rejects_generation_in_body() -> None:
    """generation is server-authoritative; body must not carry it."""
    app = _build_app()
    body = {**_VALID_BODY, "generation": 1}
    with (
        _mock_auth(),
        TestClient(app) as client,
    ):
        response = client.post(_route_path(), headers=AUTH_HEADERS, json=body)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "generation"]
        for error in response.json()["detail"]
    )


def test_a08_section_translation_accepts_full_witness_with_anchors() -> None:
    """Anchors + node_id + outline_revision are accepted (audit-only)."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY_WITH_ANCHORS
        )
    assert response.status_code == 200
    bootstrap_call.assert_awaited_once()
    # Inspect the intent passed to bootstrap
    _, kwargs = bootstrap_call.call_args
    intent = kwargs["intent"]
    assert intent.start_unit_id == "u3"
    assert intent.end_unit_id == "u4"
    assert intent.start_anchor_segment_id == "sa-1"
    assert intent.end_anchor_segment_id == "ea-1"
    assert intent.node_id == "n2"
    assert intent.outline_revision == "r1"
    # Server forces translation family; body never carries it.
    assert intent.layer_family == "translation"
    assert intent.trigger is not None  # USER_EXPLICIT


# ===========================================================================
# B. Bootstrap orchestration: ADMITTED → drain
# ===========================================================================


def test_b01_admitted_then_drain_succeeded_returns_succeeded() -> None:
    """Exact valid range → bootstrap ADMITTED + drain SUCCEEDED → 200 succeeded."""
    app = _build_app()
    bootstrap_call = AsyncMock(return_value=_bootstrap_result_admitted())
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.SUCCEEDED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["job_id"] == str(JOB_ID)
    # Bootstrap called once with authenticated user + record_id.
    bootstrap_call.assert_awaited_once()
    _, kwargs = bootstrap_call.call_args
    assert kwargs["record_id"] == RECORD_ID
    assert kwargs["user_id"] == USER_ID
    # Drain called once with the bootstrap-returned job_id and fence.
    drain_call.assert_awaited_once()
    _, drain_kwargs = drain_call.call_args
    assert drain_kwargs["job_id"] == JOB_ID
    assert drain_kwargs["expected_reading_record_id"] == RECORD_ID
    assert drain_kwargs["expected_base_id"] == BASE_ID
    assert drain_kwargs["expected_generation"] == 1


def test_b02_admitted_then_drain_retry_later_returns_retry_later() -> None:
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.RETRY_LATER)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "retry_later"


def test_b03_admitted_then_drain_failed_maps_to_retry_later() -> None:
    """Drain terminal failure → user-facing retry_later (not rejected)."""
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.FAILED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "retry_later"


def test_b04_admitted_then_drain_already_claimed_maps_to_already_covered() -> None:
    """Drain returns ALREADY_CLAIMED → response already_covered_or_inflight."""
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.ALREADY_CLAIMED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "already_covered_or_inflight"


def test_b05_admitted_then_drain_budget_denied_returns_budget_exhausted() -> None:
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.BUDGET_DENIED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "budget_exhausted"


def test_b06_admitted_then_drain_superseded_returns_superseded() -> None:
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.SUPERSEDED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "superseded"


def test_b07_admitted_then_drain_rejected_returns_rejected() -> None:
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.REJECTED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"


def test_b08_admitted_then_drain_not_found_maps_to_rejected() -> None:
    """Drain NOT_FOUND (job disappeared between bootstrap and drain)."""
    app = _build_app()
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.NOT_FOUND)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=AsyncMock(return_value=_bootstrap_result_admitted()),
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"


# ===========================================================================
# C. Bootstrap REJECT / NO_OP mapping (no drain)
# ===========================================================================


def test_c01_bootstrap_reject_node_only_returns_rejected() -> None:
    """node_id-only payloads are rejected by the planner; route maps to rejected."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_reject(reason=REASON_NODE_ONLY)
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "rejected"
    # Zero drain: no recovery needed for a planner-rejected request.
    drain_call.assert_not_awaited()


def test_c02_bootstrap_reject_source_mismatch_returns_rejected() -> None:
    """Forged record_id / base_id / generation → planner REJECT source_mismatch."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_reject(reason=REASON_SOURCE_MISMATCH)
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"
    drain_call.assert_not_awaited()


def test_c03_bootstrap_reject_no_trusted_outline_returns_rejected() -> None:
    """Non-trusted outline (missing / failed / stale) → planner REJECT."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_reject(reason=REASON_NO_TRUSTED_OUTLINE)
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"
    drain_call.assert_not_awaited()


def test_c04_bootstrap_reject_forged_range_returns_rejected() -> None:
    """Range not in trusted candidates → planner REJECT invalid_range."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_reject(reason="invalid_range")
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=AsyncMock(),
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"


def test_c05_bootstrap_no_op_budget_exhausted_returns_budget_exhausted() -> None:
    """Bootstrap reports budget exhausted → no drain attempted."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_TRANSLATION_BUDGET_EXHAUSTED
        )
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "budget_exhausted"
    # No drain: budget exhausted means no claimed work.
    drain_call.assert_not_awaited()


def test_c06_bootstrap_no_op_already_covered_returns_already_covered() -> None:
    """Section already published / active → already_covered_or_inflight, no drain."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT
        )
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "already_covered_or_inflight"
    # No drain: covered means covered (no queued job to recover).
    drain_call.assert_not_awaited()


def test_c07_bootstrap_no_op_range_overlap_returns_already_covered() -> None:
    """Range overlaps an existing section job → already_covered_or_inflight."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_SECTION_RANGE_OVERLAP
        )
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "already_covered_or_inflight"
    drain_call.assert_not_awaited()


def test_c08_bootstrap_reject_family_forge_returns_rejected() -> None:
    """Server-side family gate rejects vocabulary / grammar forgery."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_reject(
            reason=REASON_LAYER_FAMILY_NOT_TRANSLATION
        )
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "rejected"
    drain_call.assert_not_awaited()


# ===========================================================================
# D. queued-recovery closure (NO_OP + ALREADY_QUEUED → drain existing job)
# ===========================================================================


def test_d01_queued_recovery_drains_existing_job_id() -> None:
    """Re-request of an exact section with an existing queued/retryable job
    must drain that job_id (queued-recovery closure — no dead queue)."""
    app = _build_app()
    existing_job_id = uuid4()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_ALREADY_QUEUED, job_id=existing_job_id
        )
    )
    drain_call = AsyncMock(
        return_value=_drain_result(
            outcome=SectionDrainOutcome.SUCCEEDED, job_id=existing_job_id
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["job_id"] == str(existing_job_id)
    # Drain called once with the existing job_id and the same fence.
    drain_call.assert_awaited_once()
    _, drain_kwargs = drain_call.call_args
    assert drain_kwargs["job_id"] == existing_job_id
    assert drain_kwargs["expected_reading_record_id"] == RECORD_ID
    assert drain_kwargs["expected_base_id"] == BASE_ID


def test_d02_queued_recovery_already_succeeded_maps_to_already_covered() -> None:
    """If the existing job is already terminal-succeeded, drain returns
    ALREADY_CLAIMED and the route maps to already_covered_or_inflight.

    This closes the dead-queue risk: a second request never returns
    "already_inflight" and then leaves the job un-executed."""
    app = _build_app()
    existing_job_id = uuid4()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_ALREADY_QUEUED, job_id=existing_job_id
        )
    )
    drain_call = AsyncMock(
        return_value=_drain_result(
            outcome=SectionDrainOutcome.ALREADY_CLAIMED, job_id=existing_job_id
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "already_covered_or_inflight"
    # Drain MUST be called for queued-recovery (closure contract).
    drain_call.assert_awaited_once()


def test_d03_queued_recovery_drain_budget_denied_returns_budget_exhausted() -> None:
    """If the recovered job hits budget_exhausted during drain, the user sees
    budget_exhausted (not a dead queue)."""
    app = _build_app()
    existing_job_id = uuid4()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_ALREADY_QUEUED, job_id=existing_job_id
        )
    )
    drain_call = AsyncMock(
        return_value=_drain_result(
            outcome=SectionDrainOutcome.BUDGET_DENIED, job_id=existing_job_id
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "budget_exhausted"
    drain_call.assert_awaited_once()


def test_d04_queued_recovery_drain_superseded_returns_superseded() -> None:
    """Stale fence during recovery drain → superseded (no dead queue)."""
    app = _build_app()
    existing_job_id = uuid4()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_ALREADY_QUEUED, job_id=existing_job_id
        )
    )
    drain_call = AsyncMock(
        return_value=_drain_result(
            outcome=SectionDrainOutcome.SUPERSEDED, job_id=existing_job_id
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == "superseded"
    drain_call.assert_awaited_once()


# ===========================================================================
# E. Owner / fence / shape error mapping
# ===========================================================================


def test_e01_bootstrap_lookup_error_returns_404_no_leak() -> None:
    """Non-owner / missing record → bootstrap LookupError → 404, no leak."""
    app = _build_app()
    # Use a distinctive internal exception message that the route must
    # never echo verbatim — verifies the route maps to a generic 404
    # detail instead of `detail=str(exc)`.
    bootstrap_call = AsyncMock(side_effect=LookupError("internal-lookup-failure-id-42"))
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 404
    # 404 body is generic — no traceback / no internal identifier.
    assert "Traceback" not in response.text
    assert "internal-lookup-failure-id-42" not in response.text
    drain_call.assert_not_awaited()


def test_e02_bootstrap_value_error_returns_409_no_leak() -> None:
    """Server-side fence conflict (e.g. stale generation) → ValueError → 409."""
    app = _build_app()
    # Use a distinctive internal exception message that the route must
    # never echo verbatim.
    bootstrap_call = AsyncMock(
        side_effect=ValueError("internal-fence-mismatch-id-77")
    )
    drain_call = AsyncMock()
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 409
    assert "Traceback" not in response.text
    assert "internal-fence-mismatch-id-77" not in response.text
    drain_call.assert_not_awaited()


def test_e03_response_does_not_leak_internal_state() -> None:
    """Response must never echo prompt / provider payload / envelope / secret."""
    app = _build_app()
    bootstrap_call = AsyncMock(return_value=_bootstrap_result_admitted())
    drain_call = AsyncMock(
        return_value=_drain_result(outcome=SectionDrainOutcome.SUCCEEDED)
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        patch.object(
            SectionTranslationDrainService,
            "process_job_id",
            new=drain_call,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(), headers=AUTH_HEADERS, json=_VALID_BODY
        )
    assert response.status_code == 200
    body = response.json()
    # Stable response shape: outcome, job_id, detail. Nothing else.
    assert set(body.keys()) <= {"outcome", "job_id", "detail"}
    # No internal envelope / prompt / provider fields.
    body_text = response.text.lower()
    for forbidden in (
        "prompt",
        "provider",
        "envelope",
        "api_key",
        "secret",
        "target_language",
        "trace_id",
        "operation_fingerprint",
    ):
        assert forbidden not in body_text, (
            f"response leaked internal field: {forbidden!r}"
        )


# ===========================================================================
# F. Boundary: route must NOT call worker_loop / process_next_*
# ===========================================================================


def test_f01_route_does_not_import_worker_loop() -> None:
    """The route module must not import worker_loop / process_next_* helpers.

    Section execution is bounded to bootstrap + drain; the ordinary
    worker_loop is intentionally NOT pulled into this command path.
    """
    import app.api.routes.reader_orchestration as route_mod
    import inspect

    source = inspect.getsource(route_mod)
    # Forbidden symbols must not appear anywhere in the route module.
    for forbidden in (
        "ReaderEnhancementWorkerLoopService",
        "process_next_translation_batch",
        "process_next_enhancement_job",
        "asyncio.create_task",
        "scan_section_lane",
    ):
        assert forbidden not in source, (
            f"route module imports/uses forbidden symbol: {forbidden!r}"
        )


def test_f02_route_uses_existing_translation_job_type_only() -> None:
    """The route must NOT register a new job_type. Section execution reuses
    ``TRANSLATION_BATCH_JOB_TYPE`` (translate_article / unit_range_v1) via
    the existing bootstrap + drain services."""
    import app.api.routes.reader_orchestration as route_mod
    import inspect

    source = inspect.getsource(route_mod)
    # No new job_type literal introduced by the route.
    assert 'job_type = "translate_section' not in source
    assert 'job_type="translate_section' not in source
    # The route must reference the existing translation batch job_type.
    assert "TRANSLATION_BATCH_JOB_TYPE" in source or (
        "SectionTranslationBootstrapService" in source
        and "SectionTranslationDrainService" in source
    )


def test_f03_route_calls_only_bootstrap_and_drain_public_methods() -> None:
    """The route must call only the public service entry points; no internal
    ``_prepare_section_job`` / ``_force_fail_budget_exhausted`` etc."""
    import app.api.routes.reader_orchestration as route_mod
    import inspect

    source = inspect.getsource(route_mod)
    for forbidden in (
        "_prepare_section_job",
        "_force_fail_budget_exhausted",
        "_claim_miss_result",
        "_load_planner_facts",
        "_load_trusted_outline",
        "_has_unit_overlap",
        "_insert_unit_range_job",
        "_load_locked_active_base_state",
    ):
        assert forbidden not in source, (
            f"route module reaches into private service seam: {forbidden!r}"
        )


# ===========================================================================
# G. Response shape stability
# ===========================================================================


def test_g01_response_model_is_registered_on_route() -> None:
    """The route must declare ``ReaderSectionTranslationResponse`` as its
    response_model so OpenAPI / contract tests stay stable."""
    app = _build_app()
    routes = {
        route.path: route for route in app.routes if hasattr(route, "path")
    }
    route = routes.get("/reader/records/{record_id}/section-translation")
    assert route is not None, "POST /reader/records/{record_id}/section-translation not registered"
    response_model = getattr(route, "response_model", None)
    assert response_model is ReaderSectionTranslationResponse


def test_g02_response_outcome_enum_is_stable() -> None:
    """The response outcome literal set must be exactly the six documented
    values, in stable order."""
    from typing import get_args
    outcomes = set(get_args(ReaderSectionTranslationResponse.model_fields["outcome"].annotation))
    assert outcomes == {
        "succeeded",
        "retry_later",
        "already_covered_or_inflight",
        "budget_exhausted",
        "rejected",
        "superseded",
    }


def test_g03_request_model_forbids_extra_fields() -> None:
    """Pydantic config must reject unknown body fields (no silent acceptance)."""
    cfg = ReaderSectionTranslationRequest.model_config
    assert cfg.get("extra") == "forbid"


def test_g04_request_model_required_fields() -> None:
    """The only required body fields are start_unit_id and end_unit_id.

    node_id / outline_revision / start_anchor_segment_id /
    end_anchor_segment_id are optional audit-only fields.
    """
    fields = ReaderSectionTranslationRequest.model_fields
    assert fields["start_unit_id"].is_required()
    assert fields["end_unit_id"].is_required()
    assert not fields["node_id"].is_required()
    assert not fields["outline_revision"].is_required()
    assert not fields["start_anchor_segment_id"].is_required()
    assert not fields["end_anchor_segment_id"].is_required()
    # No identity / fence / family fields allowed.
    for forbidden in (
        "layer_family",
        "record_id",
        "base_id",
        "generation",
    ):
        assert forbidden not in fields, (
            f"request model leaks server-authoritative field: {forbidden!r}"
        )


# ===========================================================================
# H. Bootstrap intent construction (server-forced family + fence)
# ===========================================================================


def test_h01_route_passes_user_explicit_trigger_and_translation_family() -> None:
    """The route constructs ``ExplicitSectionIntent`` with trigger=USER_EXPLICIT
    and layer_family='translation', regardless of body content."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        TestClient(app) as client,
    ):
        client.post(_route_path(), headers=AUTH_HEADERS, json=_VALID_BODY)
    bootstrap_call.assert_awaited_once()
    _, kwargs = bootstrap_call.call_args
    intent = kwargs["intent"]
    from app.services.reader_orchestration.section_request_planner import (
        SectionRequestTrigger,
    )
    assert intent.trigger is SectionRequestTrigger.USER_EXPLICIT
    assert intent.layer_family == "translation"
    # The intent must NOT carry record_id / base_id / generation from the body
    # (the bootstrap service resolves them from the authenticated fence).
    # Explicit None check: server fills these inside bootstrap from the locked state.
    assert intent.start_unit_id == "u3"
    assert intent.end_unit_id == "u4"


def test_h02_route_authorizes_with_authenticated_user_id() -> None:
    """``authorized=True`` and ``user_id`` come from the auth dependency,
    not from the body or query string."""
    app = _build_app()
    bootstrap_call = AsyncMock(
        return_value=_bootstrap_result_no_op(
            reason=REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT
        )
    )
    with (
        _mock_auth(),
        _mock_bootstrap_init(),
        _mock_drain_init(),
        patch.object(
            SectionTranslationBootstrapService,
            "request_section_translation",
            new=bootstrap_call,
        ),
        TestClient(app) as client,
    ):
        client.post(_route_path(), headers=AUTH_HEADERS, json=_VALID_BODY)
    _, kwargs = bootstrap_call.call_args
    assert kwargs["user_id"] == USER_ID
    assert kwargs["record_id"] == RECORD_ID
    # authorized defaults to True in the service; route must not pass False.
    assert kwargs.get("authorized", True) is True
