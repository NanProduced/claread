"""D6-U4 V1c single-range persistence tests.

These tests lock the D6-U4 persistence contract. After
DATA-LEGACY-IDENTITY-EXIT the Reading Record anchor is the ONLY highlight /
note contract:

- A create request carries `anchor: UserEditorialAssetAnchor`; the anchor
  gate is the sole validation authority.
- The anchor path routes through `load_validated_reading_record_anchor`.
- Gate failure surfaces as a stable HTTP 400 with the gate error code.
- Gate success persists a real row into `user_annotations` / `reader_notes`
  with the Reading Record anchor columns populated. No 409 is returned.
- No write path references the legacy analysis identity.

DB writes are simulated by patching `db_connect.acquire_connection` to a
mock whose `fetchrow` returns a realistic inserted-row dict.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
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
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.schemas.reader_notes import ReaderNoteCreateRequest
from app.schemas.user_annotations import UserAnnotationCreateRequest
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor
from app.services.reader_notes import create_reader_note
from app.services.reader_orchestration.base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    StableReadingBase,
)
from app.services.user_annotations import create_user_annotation

USER_ID = UUID("00000000-0000-0000-0000-0000000000a5")
RECORD_ID = uuid4()
BASE_ID = uuid4()


# ---------------------------------------------------------------------------
# Test fixtures: Reading Record fixture + FakeRepository.
# ---------------------------------------------------------------------------


def _build_result(
    *,
    unit_text: str = "Hello 🧠 world",
    segment_start_utf16: int = 6,
    segment_end_utf16: int = 8,
) -> ReadingBaseBuildResult:
    segment_text = slice_by_utf16_offsets(
        unit_text,
        segment_start_utf16,
        segment_end_utf16,
    )
    assert segment_text is not None
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
        base_start_utf16=segment_start_utf16,
        base_end_utf16=segment_end_utf16,
        unit_start_utf16=segment_start_utf16,
        unit_end_utf16=segment_end_utf16,
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
    mock_conn.fetch.return_value = []
    mock_conn.execute.return_value = "UPDATE 1"
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_pool = MagicMock()
    # `acquire_connection()` is patched to return `mock_pool` directly, so
    # `async with acquire_connection() as conn:` calls `mock_pool.__aenter__`.
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


@pytest.fixture(autouse=True)
def _mock_active_fence():
    """The active-base fence and projection-event publish are not under test
    here; persistence tests lock the anchor-gate -> row-write contract only."""
    with (
        patch(
            "app.services.reader_orchestration.event_runtime.ReaderEventRuntime.is_active_fence",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.reader_orchestration.event_runtime.ReaderEventRuntime.publish_event_in_transaction",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


def _make_inserted_annotation_row(**overrides: object) -> dict:
    """A realistic user_annotations row as returned by RETURNING."""
    now = datetime(2026, 6, 24, 12, 0, 0)
    row = {
        "id": uuid4(),
        "analysis_record_id": None,
        "anchor_type": "text_range",
        "target_key": (
            f"reading-record:{RECORD_ID}:base:{BASE_ID}:gen:2:"
            f"unit:u1:segment:s1:range:6:8:{compute_text_range_hash('🧠')}"
        ),
        "paragraph_id": None,
        "sentence_id": None,
        "selected_text": "🧠",
        "start_offset": None,
        "end_offset": None,
        "text_hash": compute_text_range_hash("🧠"),
        "color": "warm_yellow",
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
        "reading_record_id": RECORD_ID,
        "base_id": BASE_ID,
        "generation": 2,
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "unit_start_utf16": 6,
        "unit_end_utf16": 8,
    }
    row.update(overrides)
    return row


def _rr_anchor_for_range(unit_text: str, start: int, end: int) -> UserEditorialAssetAnchor:
    selected_text = slice_by_utf16_offsets(unit_text, start, end)
    assert selected_text is not None
    return _new_anchor(
        start_offset=start,
        end_offset=end,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


def _make_annotation_row_for_range(
    unit_text: str,
    start: int,
    end: int,
    *,
    row_id: UUID | None = None,
    color: str = "warm_yellow",
    created_at: datetime | None = None,
    payload_json: dict | None = None,
) -> dict:
    selected_text = slice_by_utf16_offsets(unit_text, start, end)
    assert selected_text is not None
    text_hash = compute_text_range_hash(selected_text)
    now = created_at or datetime(2026, 6, 24, 12, 0, 0)
    return _make_inserted_annotation_row(
        id=row_id or uuid4(),
        target_key=(
            f"reading-record:{RECORD_ID}:base:{BASE_ID}:gen:2:"
            f"unit:u1:segment:s1:range:{start}:{end}:{text_hash}"
        ),
        selected_text=selected_text,
        text_hash=text_hash,
        color=color,
        payload_json=payload_json or {},
        created_at=now,
        updated_at=now,
        unit_start_utf16=start,
        unit_end_utf16=end,
    )


def _repository_for_unit_text(unit_text: str) -> _FakeRepository:
    return _FakeRepository(
        facts=SimpleNamespace(
            build_result=_build_result(
                unit_text=unit_text,
                segment_start_utf16=0,
                segment_end_utf16=utf16_code_unit_length(unit_text),
            )
        )
    )


def _request_for_range(
    unit_text: str,
    start: int,
    end: int,
    *,
    color: str,
    payload_json: dict | None = None,
) -> UserAnnotationCreateRequest:
    anchor = _rr_anchor_for_range(unit_text, start, end)
    return UserAnnotationCreateRequest(
        anchor=anchor,
        selected_text=anchor.selected_text,
        color=color,
        payload_json=payload_json or {},
    )


def _assert_merge_update_args(
    mock_conn: AsyncMock,
    *,
    unit_text: str,
    start: int,
    end: int,
    color: str,
) -> None:
    sql_arg = mock_conn.fetchrow.call_args.args[0]
    assert "UPDATE user_annotations" in sql_arg
    assert "INSERT INTO user_annotations" not in sql_arg

    selected_text = slice_by_utf16_offsets(unit_text, start, end)
    assert selected_text is not None
    text_hash = compute_text_range_hash(selected_text)
    assert mock_conn.fetchrow.call_args.args[1] == (
        f"reading-record:{RECORD_ID}:base:{BASE_ID}:gen:2:"
        f"unit:u1:segment:s1:range:{start}:{end}:{text_hash}"
    )
    assert mock_conn.fetchrow.call_args.args[2] == selected_text
    assert mock_conn.fetchrow.call_args.args[3] == text_hash
    assert mock_conn.fetchrow.call_args.args[4] == color
    assert mock_conn.fetchrow.call_args.args[11] == start
    assert mock_conn.fetchrow.call_args.args[12] == end


def _assert_insert_args(
    mock_conn: AsyncMock,
    *,
    unit_text: str,
    start: int,
    end: int,
    color: str,
) -> None:
    sql_arg = mock_conn.fetchrow.call_args.args[0]
    assert "INSERT INTO user_annotations" in sql_arg

    selected_text = slice_by_utf16_offsets(unit_text, start, end)
    assert selected_text is not None
    text_hash = compute_text_range_hash(selected_text)
    assert mock_conn.fetchrow.call_args.args[2] == (
        f"reading-record:{RECORD_ID}:base:{BASE_ID}:gen:2:"
        f"unit:u1:segment:s1:range:{start}:{end}:{text_hash}"
    )
    assert mock_conn.fetchrow.call_args.args[3] == selected_text
    assert mock_conn.fetchrow.call_args.args[4] == text_hash
    assert mock_conn.fetchrow.call_args.args[5] == color


def _make_inserted_note_row() -> dict:
    """A realistic reader_notes row as returned by RETURNING."""
    now = datetime(2026, 6, 24, 12, 0, 0)
    return {
        "id": uuid4(),
        "analysis_record_id": None,
        "anchor_sentence_id": None,
        "quote_mode": "text_range",
        "target_key": (
            f"reading-record:{RECORD_ID}:base:{BASE_ID}:gen:2:"
            f"unit:u1:segment:s1:range:6:8:{compute_text_range_hash('🧠')}"
        ),
        "paragraph_id": None,
        "sentence_id": None,
        "selected_text": "🧠",
        "start_offset": None,
        "end_offset": None,
        "text_hash": compute_text_range_hash("🧠"),
        "note_text": "note",
        "payload_json": {},
        "created_at": now,
        "updated_at": now,
        "reading_record_id": RECORD_ID,
        "base_id": BASE_ID,
        "generation": 2,
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "unit_start_utf16": 6,
        "unit_end_utf16": 8,
    }


# ---------------------------------------------------------------------------
# Schema acceptance — new anchor relaxes legacy required-field validators.
# ---------------------------------------------------------------------------


def test_user_annotation_schema_accepts_new_anchor_without_legacy_offsets() -> None:
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )
    assert req.anchor is not None
    # Post-exit: the legacy identity / render-scene fields no longer exist.
    assert not hasattr(req, "analysis_record_id")
    assert not hasattr(req, "sentence_id")
    assert not hasattr(req, "start_offset")


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
        selected_text="🧠",
        note_text="remember this",
    )
    assert req.anchor is not None
    # Post-exit: the legacy identity / render-scene fields no longer exist.
    assert not hasattr(req, "analysis_record_id")
    assert not hasattr(req, "anchor_sentence_id")
    assert not hasattr(req, "quote_mode")


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
# New-anchor branch: gate success -> real INSERT, no 409.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_persists_row_no_409() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetchrow.return_value = _make_inserted_annotation_row()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    # Gate was invoked once with the right fences.
    assert len(repository.calls) == 1
    call = repository.calls[0]
    assert call["user_id"] == USER_ID
    assert call["record_id"] == RECORD_ID
    assert call["expected_base_id"] == BASE_ID
    assert call["expected_generation"] == 2

    # A real INSERT was issued (fetchrow called once).
    mock_conn.transaction.assert_called_once()
    assert mock_conn.fetch.call_count == 1
    assert "FOR UPDATE" in mock_conn.fetch.call_args.args[0]
    assert mock_conn.fetchrow.call_count == 1
    sql_arg = mock_conn.fetchrow.call_args.args[0]
    assert "INSERT INTO user_annotations" in sql_arg

    # Response carries the Reading Record anchor columns.
    assert not hasattr(response, "analysis_record_id")
    assert response.reading_record_id == RECORD_ID
    assert response.base_id == BASE_ID
    assert response.generation == 2
    assert response.unit_id == "u1"
    assert response.anchor_segment_id == "s1"
    assert response.unit_start_utf16 == 6
    assert response.unit_end_utf16 == 8
    assert response.selected_text == "🧠"


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_exact_duplicate_updates_canonical_row() -> None:
    unit_text = "abcdefghij"
    existing_id = uuid4()
    existing = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        row_id=existing_id,
        color="warm_yellow",
    )
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        row_id=existing_id,
        color="soft_mint",
        payload_json={"source": "second"},
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(
        unit_text,
        2,
        5,
        color="soft_mint",
        payload_json={"source": "second"},
    )
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == existing_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 5
    assert response.color == "soft_mint"
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=5,
        color="soft_mint",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_subset_keeps_existing_range() -> None:
    unit_text = "abcdefghij"
    existing_id = uuid4()
    existing = _make_annotation_row_for_range(unit_text, 2, 8, row_id=existing_id)
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        8,
        row_id=existing_id,
        color="soft_rose",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(unit_text, 3, 5, color="soft_rose")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == existing_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 8
    assert response.color == "soft_rose"
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=8,
        color="soft_rose",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_superset_extends_existing_range() -> None:
    unit_text = "abcdefghij"
    existing_id = uuid4()
    existing = _make_annotation_row_for_range(unit_text, 3, 5, row_id=existing_id)
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        8,
        row_id=existing_id,
        color="soft_mint",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(unit_text, 2, 8, color="soft_mint")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == existing_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 8
    assert response.color == "soft_mint"
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=8,
        color="soft_mint",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_partial_overlap_merges_union() -> None:
    unit_text = "abcdefghij"
    existing_id = uuid4()
    existing = _make_annotation_row_for_range(unit_text, 2, 6, row_id=existing_id)
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        8,
        row_id=existing_id,
        color="soft_rose",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(unit_text, 4, 8, color="soft_rose")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == existing_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 8
    assert response.color == "soft_rose"
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=8,
        color="soft_rose",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_multiple_overlaps_merge_and_report_superseded_ids() -> None:
    unit_text = "abcdefghij"
    canonical_id = uuid4()
    superseded_id = uuid4()
    canonical = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        row_id=canonical_id,
        created_at=datetime(2026, 6, 24, 12, 0, 0),
    )
    superseded = _make_annotation_row_for_range(
        unit_text,
        6,
        9,
        row_id=superseded_id,
        created_at=datetime(2026, 6, 24, 12, 1, 0),
    )
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        9,
        row_id=canonical_id,
        color="soft_mint",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [canonical, superseded]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(unit_text, 4, 7, color="soft_mint")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == canonical_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 9
    assert response.superseded_ids == [superseded_id]
    assert mock_conn.execute.call_count == 1
    assert mock_conn.execute.call_args.args[2] == superseded_id
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=9,
        color="soft_mint",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_merge_reuses_existing_final_target_key_row() -> None:
    unit_text = "abcdefghij"
    active_a_id = uuid4()
    active_b_id = uuid4()
    final_target_id = uuid4()
    active_a = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        row_id=active_a_id,
        created_at=datetime(2026, 6, 24, 12, 0, 0),
    )
    active_b = _make_annotation_row_for_range(
        unit_text,
        6,
        9,
        row_id=active_b_id,
        created_at=datetime(2026, 6, 24, 12, 1, 0),
    )
    final_target = _make_annotation_row_for_range(
        unit_text,
        2,
        9,
        row_id=final_target_id,
        color="warm_yellow",
        created_at=datetime(2026, 6, 24, 11, 0, 0),
    )
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        9,
        row_id=final_target_id,
        color="soft_rose",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [active_a, active_b]
    mock_conn.fetchrow.side_effect = [final_target, updated]

    req = _request_for_range(unit_text, 4, 7, color="soft_rose")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == final_target_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 9
    assert response.color == "soft_rose"
    assert response.superseded_ids == [active_a_id, active_b_id]
    assert mock_conn.execute.call_count == 2
    assert [call.args[2] for call in mock_conn.execute.call_args_list] == [
        active_a_id,
        active_b_id,
    ]
    final_target_lock_sql = mock_conn.fetchrow.call_args_list[0].args[0]
    assert "target_key = $2" in final_target_lock_sql
    assert "FOR UPDATE" in final_target_lock_sql
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=9,
        color="soft_rose",
    )
    assert mock_conn.fetchrow.call_args.args[13] == final_target_id


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_adjacent_same_color_merges() -> None:
    unit_text = "abcdefghij"
    existing_id = uuid4()
    existing = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        row_id=existing_id,
        color="warm_yellow",
    )
    updated = _make_annotation_row_for_range(
        unit_text,
        2,
        8,
        row_id=existing_id,
        color="warm_yellow",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = updated

    req = _request_for_range(unit_text, 5, 8, color="warm_yellow")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == existing_id
    assert response.unit_start_utf16 == 2
    assert response.unit_end_utf16 == 8
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_merge_update_args(
        mock_conn,
        unit_text=unit_text,
        start=2,
        end=8,
        color="warm_yellow",
    )


@pytest.mark.asyncio
async def test_user_annotation_new_anchor_adjacent_different_color_stays_separate() -> None:
    unit_text = "abcdefghij"
    existing = _make_annotation_row_for_range(
        unit_text,
        2,
        5,
        color="warm_yellow",
    )
    inserted = _make_annotation_row_for_range(
        unit_text,
        5,
        8,
        color="soft_mint",
    )
    repository = _repository_for_unit_text(unit_text)
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = [existing]
    mock_conn.fetchrow.return_value = inserted

    req = _request_for_range(unit_text, 5, 8, color="soft_mint")
    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert response.id == inserted["id"]
    assert response.unit_start_utf16 == 5
    assert response.unit_end_utf16 == 8
    assert response.color == "soft_mint"
    assert response.superseded_ids == []
    mock_conn.execute.assert_not_called()
    _assert_insert_args(
        mock_conn,
        unit_text=unit_text,
        start=5,
        end=8,
        color="soft_mint",
    )


@pytest.mark.asyncio
async def test_reader_note_new_anchor_persists_row_no_409() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetchrow.return_value = _make_inserted_note_row()
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
        response = await create_reader_note(USER_ID, req, repository=repository)

    # Gate was invoked once.
    assert len(repository.calls) == 1

    # A real INSERT was issued.
    assert mock_conn.fetchrow.call_count == 1
    sql_arg = mock_conn.fetchrow.call_args.args[0]
    assert "INSERT INTO reader_notes" in sql_arg

    # Response carries the Reading Record anchor columns.
    assert not hasattr(response, "analysis_record_id")
    assert response.reading_record_id == RECORD_ID
    assert response.base_id == BASE_ID
    assert response.generation == 2
    assert response.unit_id == "u1"
    assert response.anchor_segment_id == "s1"
    assert response.unit_start_utf16 == 6
    assert response.unit_end_utf16 == 8
    assert response.note_text == "note"


@pytest.mark.asyncio
async def test_new_anchor_branch_insert_has_no_legacy_analysis_identity() -> None:
    repository = _FakeRepository(facts=SimpleNamespace(build_result=_build_result()))
    pool, mock_conn = _mock_db_pool()
    mock_conn.fetchrow.return_value = _make_inserted_annotation_row()
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(),
        selected_text="🧠",
    )

    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        response = await create_user_annotation(USER_ID, req, repository=repository)

    assert mock_conn.fetchrow.call_count == 1
    assert response.reading_record_id == RECORD_ID
    assert not hasattr(response, "analysis_record_id")

    # Post-exit: the INSERT SQL must not reference the legacy identity at all.
    sql_arg = mock_conn.fetchrow.call_args.args[0]
    assert "analysis_record_id" not in sql_arg


def test_user_annotation_schema_has_no_legacy_identity_fields() -> None:
    """Schemas must not carry analysis_record_id after the identity exit."""
    rr_id = "11111111-1111-1111-1111-111111111111"
    req = UserAnnotationCreateRequest(
        anchor=_new_anchor(record_id=rr_id),
        selected_text="🧠",
    )
    assert not hasattr(req, "analysis_record_id")


def test_reader_note_schema_has_no_legacy_identity_fields() -> None:
    rr_id = "11111111-1111-1111-1111-111111111111"
    req = ReaderNoteCreateRequest(
        anchor=_new_anchor(record_id=rr_id),
        selected_text="🧠",
        note_text="note",
    )
    assert not hasattr(req, "analysis_record_id")
    assert not hasattr(req, "anchor_sentence_id")


# ---------------------------------------------------------------------------
# List contract: Reading Record identity is the only list filter.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_annotations_without_record_id_only_returns_reading_record_rows() -> None:
    """The list-all branch must filter to Reading Record rows."""
    from app.services.user_annotations import list_user_annotations

    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = []

    with patch(
        "app.services.user_annotations.db_connect.acquire_connection",
        return_value=pool,
    ):
        await list_user_annotations(USER_ID)

    assert mock_conn.fetch.call_count == 1
    sql_arg = mock_conn.fetch.call_args.args[0]
    assert "reading_record_id IS NOT NULL" in sql_arg


@pytest.mark.asyncio
async def test_list_reader_notes_filters_by_reading_record_id() -> None:
    """list_reader_notes always filters by reading_record_id = $2."""
    from app.services.reader_notes import list_reader_notes

    pool, mock_conn = _mock_db_pool()
    mock_conn.fetch.return_value = []

    reading_record_id = str(uuid4())
    with patch(
        "app.services.reader_notes.db_connect.acquire_connection",
        return_value=pool,
    ):
        await list_reader_notes(USER_ID, reading_record_id)

    assert mock_conn.fetch.call_count == 1
    sql_arg = mock_conn.fetch.call_args.args[0]
    assert "reading_record_id = $2" in sql_arg
