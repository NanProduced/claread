"""D6-A5 dual-contract spike characterization tests.

These tests lock the current D6-A5 spike contract:

- Legacy note / highlight create + list behaviour is unchanged when the
  request does NOT carry the new `anchor` field.
- A request with `anchor: UserEditorialAssetAnchor` is accepted by the
  schema; the legacy required-field validator is relaxed.
- The new-anchor path routes through `load_validated_reading_record_anchor`.
- Gate failure surfaces as a stable HTTP 400 with the gate error code.
- Gate success surfaces as a stable HTTP 409 with
  `code = user_editorial_asset_write_pending` and a `validated: True`
  payload; the legacy `user_annotations` / `reader_notes` table is NOT
  written to on the new path (spike only — persistence deferred).
- The Reading Record id from the new anchor is never silently copied into
  the legacy `analysis_record_id` field.

DB writes are simulated by patching `db_connect.acquire_connection` to a
mock that records any calls. The new-anchor branch must not perform any
`fetchrow` / `execute` against the legacy tables.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.contracts.anchor_validation import (
    ANCHOR_SEGMENT_NOT_FOUND,
    OUTSIDE_ANCHOR_SEGMENT_RANGE,
    READING_RECORD_NOT_FOUND,
    UNIT_NOT_FOUND,
)
from app.contracts.annotation import (
    compute_text_range_hash,
    utf16_code_unit_length,
)
from app.schemas.reader_notes import ReaderNoteCreateRequest
from app.schemas.user_annotations import UserAnnotationCreateRequest
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor
from app.services.reader_notes import (
    READER_NOTE_WRITE_PENDING,
    create_reader_note,
)
from app.services.reader_orchestration.base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    StableReadingBase,
)
from app.services.user_annotations import (
    USER_EDITORIAL_ASSET_WRITE_PENDING,
    create_user_annotation,
)

USER_ID = UUID("00000000-0000-0000-0000-0000000000a5")
RECORD_ID = uuid4()
BASE_ID = uuid4()


# ---------------------------------------------------------------------------
# Test fixtures: Reading Record fixture + FakeRepository.
# ---------------------------------------------------------------------------


def _build_result() -> ReadingBaseBuildResult:
    unit_text = "Hello 🧠 world"
    base = StableReadingBase(
        reading_record_id=str(RECORD_ID),
        base_id=str(BASE_ID),
        text=unit_text,
        content_sha256=hashlib.sha256(unit_text.encode("utf-8")).hexdigest(),
        content_utf16_length=utf16_code_unit_length(unit_text),
        canonicalizer_version="test",
        builder_version="test",
        segmenter_version="test",
        language="en",
        title_snapshot="Test",
    )
    unit = BuiltReadingUnit(
        reading_record_id=str(RECORD_ID),
        base_id=str(BASE_ID),
        unit_id="u1",
        order_index=1,
        unit_type="body",
        boundary_quality="normal",
        base_start_utf16=0,
        base_end_utf16=utf16_code_unit_length(unit_text),
        text_hash=compute_text_range_hash(unit_text),
        text=unit_text,
    )
    segment_text = "🧠"
    segment = BuiltAnchorSegment(
        reading_record_id=str(RECORD_ID),
        base_id=str(BASE_ID),
        unit_id="u1",
        anchor_segment_id="s1",
        sentence_id="s1",
        paragraph_id="p1",
        order_index=1,
        unit_order_index=1,
        segment_type="sentence",
        boundary_quality="normal",
        base_start_utf16=6,
        base_end_utf16=8,
        unit_start_utf16=6,
        unit_end_utf16=8,
        text_hash=compute_text_range_hash(segment_text),
        text=segment_text,
    )
    return ReadingBaseBuildResult(
        base=base,
        units=(unit,),
        anchor_segments=(segment,),
        navigation_units=(),
    )


@dataclass
class _FakeRepository:
    facts: object | None = None
    error: Exception | None = None
    calls: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    async def load_snapshot_facts(
        self,
        conn,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ):
        self.calls.append(
            {
                "conn": conn,
                "record_id": record_id,
                "user_id": user_id,
                "expected_base_id": expected_base_id,
                "expected_generation": expected_generation,
            }
        )
        if self.error is not None:
            raise self.error
        return self.facts


def _new_anchor(**overrides: object) -> UserEditorialAssetAnchor:
    defaults: dict[str, object] = {
        "record_id": str(RECORD_ID),
        "base_id": str(BASE_ID),
        "generation": 2,
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "start_offset": 6,
        "end_offset": 8,
        "selected_text": "🧠",
        "text_hash": compute_text_range_hash("🧠"),
    }
    defaults.update(overrides)
    return UserEditorialAssetAnchor(**defaults)


def _mock_db_pool() -> tuple[MagicMock, AsyncMock]:
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


# ---------------------------------------------------------------------------
# Schema acceptance — new anchor relaxes legacy required-field validators.
# ---------------------------------------------------------------------------


def test_user_annotation_schema_accepts_new_anchor_without_legacy_offsets() -> None:
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )
    assert req.anchor is not None
    assert req.analysis_record_id is None
    assert req.sentence_id is None
    assert req.start_offset is None


def test_user_annotation_schema_rejects_new_anchor_with_empty_selected_text() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserAnnotationCreateRequest(
            anchor=_new_anchor(),
            selected_text="   ",
        )


def test_user_annotation_schema_rejects_new_anchor_selected_text_mismatch() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserAnnotationCreateRequest(
            anchor=_new_anchor(),
            selected_text="different",
        )


def test_reader_note_schema_accepts_new_anchor_without_legacy_offsets() -> None:
    req = ReaderNoteCreateRequest(
        anchor=_new_anchor(),
        quote_mode="text_range",
        selected_text="🧠",
        note_text="remember this",
    )
    assert req.anchor is not None
    assert req.analysis_record_id is None
    assert req.anchor_sentence_id is None


def test_reader_note_schema_requires_note_text_even_on_new_anchor() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReaderNoteCreateRequest(
            anchor=_new_anchor(),
            quote_mode="text_range",
            selected_text="🧠",
            note_text="",
        )


def test_reader_note_schema_rejects_new_anchor_selected_text_mismatch() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReaderNoteCreateRequest(
            anchor=_new_anchor(),
            quote_mode="text_range",
            selected_text="different",
            note_text="note",
        )


# ---------------------------------------------------------------------------
# Legacy path unchanged: an old-shape request still hits the legacy scene
# validation path; we don't go through the anchor gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_user_annotation_request_does_not_invoke_anchor_gate() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, conn = _mock_db_pool()

    legacy_record_id = str(uuid4())
    selected_text = "memory"
    req = UserAnnotationCreateRequest(
        analysis_record_id=legacy_record_id,
        anchor_type="text_range",
        paragraph_id="p1",
        sentence_id="s_legacy",
        selected_text=selected_text,
        start_offset=0,
        end_offset=len(selected_text),
        text_hash=compute_text_range_hash(selected_text),
    )

    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        # The legacy path calls load_render_scene + tries an INSERT. We just
        # assert the anchor gate is never touched — i.e., the spike's branch
        # is gated on `req.anchor is not None`.
        await _assert_no_repository_call(
            create_user_annotation(USER_ID, req, repository=repository),
            repository,
        )


@pytest.mark.asyncio
async def test_legacy_reader_note_request_does_not_invoke_anchor_gate() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, conn = _mock_db_pool()

    legacy_record_id = str(uuid4())
    selected_text = "policy"
    req = ReaderNoteCreateRequest(
        analysis_record_id=legacy_record_id,
        quote_mode="text_range",
        anchor_sentence_id="s_legacy",
        paragraph_id="p1",
        sentence_id="s_legacy",
        selected_text=selected_text,
        start_offset=0,
        end_offset=len(selected_text),
        text_hash=compute_text_range_hash(selected_text),
        note_text="note",
    )

    with patch(
        "app.services.reader_notes.db_connect.acquire_connection",
        return_value=pool,
    ):
        await _assert_no_repository_call(
            create_reader_note(USER_ID, req, repository=repository),
            repository,
        )


async def _assert_no_repository_call(coro, repository: _FakeRepository) -> None:
    try:
        await coro
    except HTTPException:
        # Legacy path may still raise (e.g. from a missing scene in tests).
        # The assertion that matters is that the repository was not touched.
        pass
    except Exception:
        pass
    assert repository.calls == [], (
        "legacy request must not invoke the Reading Record anchor gate"
    )


# ---------------------------------------------------------------------------
# New-anchor branch: gate failure -> HTTP 400 with typed code.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_400_on_unit_not_found() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, _ = _mock_db_pool()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(unit_id="u_missing"),
        selected_text="🧠",
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == UNIT_NOT_FOUND
    assert excinfo.value.detail["field"] == "anchor"


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_400_on_record_not_found() -> None:
    repository = _FakeRepository(
        error=LookupError("reading record not visible"),
    )
    pool, _ = _mock_db_pool()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == READING_RECORD_NOT_FOUND


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_400_on_anchor_segment_not_found() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, _ = _mock_db_pool()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(anchor_segment_id="s_missing"),
        selected_text="🧠",
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == ANCHOR_SEGMENT_NOT_FOUND


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_400_on_outside_anchor_segment_range() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, _ = _mock_db_pool()
    # Anchor payload offsets (20..30) lie past the anchor segment's
    # unit range (6..8). The schema accepts because it only checks the
    # payload's own internal length/hash invariants; the gate then fails
    # with OUTSIDE_ANCHOR_SEGMENT_RANGE once it loads the real unit.
    out_of_range_text = "x" * 10
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(
            selected_text=out_of_range_text,
            start_offset=20,
            end_offset=30,
            text_hash=compute_text_range_hash(out_of_range_text),
        ),
        selected_text=out_of_range_text,
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == OUTSIDE_ANCHOR_SEGMENT_RANGE


@pytest.mark.asyncio
async def test_reader_note_new_anchor_400_on_outside_anchor_segment_range() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, _ = _mock_db_pool()
    out_of_range_text = "x" * 10
    req = ReaderNoteCreateRequest(
        anchor=_new_anchor(
            selected_text=out_of_range_text,
            start_offset=20,
            end_offset=30,
            text_hash=compute_text_range_hash(out_of_range_text),
        ),
        quote_mode="text_range",
        selected_text=out_of_range_text,
        note_text="note",
    )
    with patch(
        "app.services.reader_notes.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_reader_note(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == OUTSIDE_ANCHOR_SEGMENT_RANGE


# ---------------------------------------------------------------------------
# New-anchor branch: gate success -> HTTP 409 with write-pending code; no DB
# write happens on the legacy tables.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_409_write_pending_no_db_write() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["code"] == USER_EDITORIAL_ASSET_WRITE_PENDING
    assert detail["validated"] is True
    assert detail["record_id"] == str(RECORD_ID)
    assert detail["unit_id"] == "u1"
    assert detail["anchor_segment_id"] == "s1"
    assert detail["selected_text"] == "🧠"

    # Repository must have been invoked once with the right fences.
    assert len(repository.calls) == 1
    call = repository.calls[0]
    assert call["user_id"] == USER_ID
    assert call["record_id"] == RECORD_ID
    assert call["expected_base_id"] == BASE_ID
    assert call["expected_generation"] == 2

    # No legacy-table INSERT/UPDATE call. The mock conn was acquired but
    # no fetchrow / execute targeted user_annotations on the new path.
    mock_conn.fetchrow.assert_not_called()
    mock_conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_reader_note_new_anchor_409_write_pending_no_db_write() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    req = ReaderNoteCreateRequest(
        anchor=_new_anchor(),
        quote_mode="text_range",
        selected_text="🧠",
        note_text="note",
    )
    with patch(
        "app.services.reader_notes.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_reader_note(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["code"] == READER_NOTE_WRITE_PENDING
    assert detail["validated"] is True

    mock_conn.fetchrow.assert_not_called()
    mock_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Reading Record id from new anchor is NEVER copied into analysis_record_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_anchor_branch_never_populates_legacy_analysis_record_id() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
        # Deliberately set analysis_record_id to a *different* UUID so we
        # can detect any silent overwrite.
        analysis_record_id=str(uuid4()),
    )

    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await create_user_annotation(USER_ID, req, repository=repository)

    assert excinfo.value.status_code == 409
    # Even when the request smuggles in a legacy analysis_record_id, the
    # spike never writes to user_annotations on the new anchor path.
    mock_conn.fetchrow.assert_not_called()
    mock_conn.execute.assert_not_called()


def test_user_annotation_schema_does_not_silently_remap_anchor_record_id() -> None:
    """Schemas must not auto-fill analysis_record_id from anchor.record_id."""
    rr_id = "11111111-1111-1111-1111-111111111111"
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(record_id=rr_id),
        selected_text="🧠",
    )
    assert req.analysis_record_id is None, (
        "schema must not auto-fill analysis_record_id from anchor.record_id"
    )


def test_reader_note_schema_does_not_silently_remap_anchor_record_id() -> None:
    rr_id = "11111111-1111-1111-1111-111111111111"
    req = ReaderNoteCreateRequest(
        anchor=_new_anchor(record_id=rr_id),
        quote_mode="text_range",
        selected_text="🧠",
        note_text="note",
    )
    assert req.analysis_record_id is None
    assert req.anchor_sentence_id is None
