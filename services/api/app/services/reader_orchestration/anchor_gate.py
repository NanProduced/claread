from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.contracts.anchor_validation import (
    ANCHOR_SEGMENT_NOT_FOUND,
    ANCHOR_SEGMENT_UNIT_MISMATCH,
    INVALID_BASE_ID,
    INVALID_RECORD_ID,
    READING_RECORD_NOT_FOUND,
    READING_RECORD_SNAPSHOT_INVALID,
    STALE_BASE_OR_GENERATION,
    UNIT_NOT_FOUND,
    AnchorSegmentRange,
    AnchorValidationError,
    validate_text_anchor_against_unit,
)
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor
from app.services.reader_orchestration.base_builder import BuiltAnchorSegment, BuiltReadingUnit
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository


@dataclass(frozen=True, slots=True)
class ValidatedReadingRecordAnchor:
    record_id: UUID
    base_id: UUID
    generation: int
    unit: BuiltReadingUnit
    anchor_segment: BuiltAnchorSegment
    selected_text: str


def _parse_uuid(value: str, *, field_name: str, code: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AnchorValidationError(code, f"{field_name} must be a UUID") from exc


def _normalize_snapshot_load_error(exc: ValueError) -> AnchorValidationError:
    message = str(exc)
    stale_markers = (
        "does not match expected",
        "active_base_id does not resolve",
        "active base generation does not match",
        "requires an active base",
        "status='active'",
    )
    if any(marker in message for marker in stale_markers):
        return AnchorValidationError(STALE_BASE_OR_GENERATION, message)
    return AnchorValidationError(READING_RECORD_SNAPSHOT_INVALID, message)


async def load_validated_reading_record_anchor(
    conn,
    *,
    repository: ReaderOrchestrationRepository,
    user_id: UUID,
    anchor: UserEditorialAssetAnchor,
) -> ValidatedReadingRecordAnchor:
    record_id = _parse_uuid(anchor.record_id, field_name="record_id", code=INVALID_RECORD_ID)
    base_id = _parse_uuid(anchor.base_id, field_name="base_id", code=INVALID_BASE_ID)

    try:
        facts = await repository.load_snapshot_facts(
            conn,
            record_id=record_id,
            user_id=user_id,
            expected_base_id=base_id,
            expected_generation=anchor.generation,
        )
    except LookupError as exc:
        raise AnchorValidationError(READING_RECORD_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise _normalize_snapshot_load_error(exc) from exc

    units_by_id = {unit.unit_id: unit for unit in facts.build_result.units}
    unit = units_by_id.get(anchor.unit_id)
    if unit is None:
        raise AnchorValidationError(
            UNIT_NOT_FOUND,
            f"unit_id {anchor.unit_id} does not exist in the current active base",
        )

    segments_by_id = {
        segment.anchor_segment_id: segment
        for segment in facts.build_result.anchor_segments
    }
    anchor_segment = segments_by_id.get(anchor.anchor_segment_id)
    if anchor_segment is None:
        raise AnchorValidationError(
            ANCHOR_SEGMENT_NOT_FOUND,
            f"anchor_segment_id {anchor.anchor_segment_id} does not exist",
        )
    if anchor_segment.unit_id != anchor.unit_id:
        raise AnchorValidationError(
            ANCHOR_SEGMENT_UNIT_MISMATCH,
            f"anchor segment {anchor.anchor_segment_id} does not belong to unit {anchor.unit_id}",
        )

    selected_text = validate_text_anchor_against_unit(
        offset_unit=anchor.offset_unit,
        start_offset=anchor.start_offset,
        end_offset=anchor.end_offset,
        selected_text=anchor.selected_text,
        text_hash=anchor.text_hash,
        unit_text=unit.text,
        anchor_segment=AnchorSegmentRange(
            anchor_segment_id=anchor_segment.anchor_segment_id,
            unit_start_utf16=anchor_segment.unit_start_utf16,
            unit_end_utf16=anchor_segment.unit_end_utf16,
        ),
        hash_algorithm=anchor.hash_algorithm,
    )

    return ValidatedReadingRecordAnchor(
        record_id=record_id,
        base_id=base_id,
        generation=anchor.generation,
        unit=unit,
        anchor_segment=anchor_segment,
        selected_text=selected_text,
    )
