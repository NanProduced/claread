from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.contracts.anchor_validation import (
    ANCHOR_SEGMENT_NOT_FOUND,
    INVALID_BASE_ID,
    OUTSIDE_ANCHOR_SEGMENT_RANGE,
    READING_RECORD_NOT_FOUND,
    SELECTED_TEXT_MISMATCH,
    STALE_BASE_OR_GENERATION,
    TEXT_HASH_MISMATCH,
    UNIT_NOT_FOUND,
    AnchorValidationError,
)
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor
from app.services.reader_orchestration.anchor_gate import (
    ValidatedReadingRecordAnchor,
    load_validated_reading_record_anchor,
)
from app.services.reader_orchestration.base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    StableReadingBase,
)

USER_ID = UUID("00000000-0000-0000-0000-000000000011")
OTHER_USER_ID = UUID("00000000-0000-0000-0000-000000000012")
RECORD_ID = uuid4()
BASE_ID = uuid4()


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


def _facts():
    return SimpleNamespace(build_result=_build_result())


def _anchor(**overrides: object) -> UserEditorialAssetAnchor:
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


def _anchor_unchecked(**overrides: object) -> UserEditorialAssetAnchor:
    defaults = _anchor().model_dump()
    defaults.update(overrides)
    return UserEditorialAssetAnchor.model_construct(**defaults)


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_accepts_current_active_anchor() -> None:
    repository = _FakeRepository(facts=_facts())
    conn = object()

    result = await load_validated_reading_record_anchor(
        conn,
        repository=repository,
        user_id=USER_ID,
        anchor=_anchor(),
    )

    assert isinstance(result, ValidatedReadingRecordAnchor)
    assert result.record_id == RECORD_ID
    assert result.base_id == BASE_ID
    assert result.unit.unit_id == "u1"
    assert result.anchor_segment.anchor_segment_id == "s1"
    assert result.selected_text == "🧠"
    assert repository.calls == [
        {
            "conn": conn,
            "record_id": RECORD_ID,
            "user_id": USER_ID,
            "expected_base_id": BASE_ID,
            "expected_generation": 2,
        }
    ]


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_wrong_user() -> None:
    repository = _FakeRepository(
        error=LookupError(f"reading record {RECORD_ID} not found for user {OTHER_USER_ID}")
    )

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=OTHER_USER_ID,
            anchor=_anchor(),
        )

    assert exc_info.value.code == READING_RECORD_NOT_FOUND


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_stale_base_or_generation() -> None:
    repository = _FakeRepository(
        error=ValueError(
            f"snapshot base_id {uuid4()} does not match expected {BASE_ID}"
        )
    )

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(),
        )

    assert exc_info.value.code == STALE_BASE_OR_GENERATION


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_missing_unit() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(unit_id="u2"),
        )

    assert exc_info.value.code == UNIT_NOT_FOUND


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_missing_anchor_segment() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(anchor_segment_id="s2"),
        )

    assert exc_info.value.code == ANCHOR_SEGMENT_NOT_FOUND


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_offsets_outside_segment() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(
                start_offset=0,
                end_offset=5,
                selected_text="Hello",
                text_hash=compute_text_range_hash("Hello"),
            ),
        )

    assert exc_info.value.code == OUTSIDE_ANCHOR_SEGMENT_RANGE


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_selected_text_mismatch() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(
                selected_text="hi",
                start_offset=6,
                end_offset=8,
                text_hash=compute_text_range_hash("hi"),
            ),
        )

    assert exc_info.value.code == SELECTED_TEXT_MISMATCH


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_hash_mismatch() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor_unchecked(text_hash=compute_text_range_hash("xx")),
        )

    assert exc_info.value.code == TEXT_HASH_MISMATCH


@pytest.mark.asyncio
async def test_load_validated_reading_record_anchor_rejects_invalid_base_uuid() -> None:
    repository = _FakeRepository(facts=_facts())

    with pytest.raises(AnchorValidationError) as exc_info:
        await load_validated_reading_record_anchor(
            object(),
            repository=repository,
            user_id=USER_ID,
            anchor=_anchor(base_id="not-a-uuid"),
        )

    assert exc_info.value.code == INVALID_BASE_ID
