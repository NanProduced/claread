from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.database.json_compat import ensure_json_array, ensure_json_object, jsonb_param
from app.schemas.reader_orchestration import (
    ReaderSnapshotLayer,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotRecord,
    ReadingRecordProductState,
)

from .base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    NavigationUnitFact,
    ReadingBaseBuildResult,
    StableReadingBase,
    validate_reading_base_build_result,
)


@dataclass(frozen=True, slots=True)
class LoadedReaderSnapshotFacts:
    build_result: ReadingBaseBuildResult
    record: ReaderSnapshotRecord
    last_event_sequence: int
    snapshot_taken_at: datetime
    enhancement_layers: tuple[ReaderSnapshotLayer, ...]
    parsed_decisions: tuple[ReaderSnapshotParsedDecision, ...]


@dataclass(frozen=True, slots=True)
class ReaderRecordSummary:
    record_id: UUID
    title: str | None
    source_type: str
    product_state: str
    readiness_state: str
    created_at: datetime
    source_metadata: dict[str, Any]
    last_event_sequence: int


class ReaderOrchestrationRepository:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def insert_reading_record(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        client_record_id: str | None,
        title: str,
        language: str,
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id,
                user_id,
                client_record_id,
                source_type,
                title,
                language,
                lifecycle_status,
                product_state,
                readiness_state,
                generation,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                'text',
                $4,
                $5,
                'active',
                'processing',
                'submitted',
                1,
                $6,
                $6
            )
            """,
            record_id,
            user_id,
            client_record_id,
            title,
            language,
            created_at,
        )

    async def insert_original_input(
        self,
        conn: asyncpg.Connection,
        *,
        original_input_id: UUID,
        record_id: UUID,
        user_id: UUID,
        source_text: str,
        source_metadata: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id,
                reading_record_id,
                user_id,
                input_type,
                source_text,
                source_ref_json,
                metadata_json,
                content_sha256,
                created_at
            )
            VALUES (
                $1,
                $2,
                $3,
                'plain_text',
                $4,
                '{}'::jsonb,
                $5::jsonb,
                $6,
                $7
            )
            """,
            original_input_id,
            record_id,
            user_id,
            source_text,
            jsonb_param(source_metadata),
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            created_at,
        )

    async def insert_reading_base(
        self,
        conn: asyncpg.Connection,
        *,
        base_id: UUID,
        build_result: ReadingBaseBuildResult,
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reading_bases (
                id,
                reading_record_id,
                base_version,
                record_generation,
                text,
                content_sha256,
                content_utf16_length,
                canonicalizer_version,
                builder_version,
                segmenter_version,
                language,
                title_snapshot,
                navigation_json,
                status,
                frozen_at,
                created_at
            )
            VALUES (
                $1,
                $2,
                1,
                1,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10,
                $11::jsonb,
                'active',
                $12,
                $12
            )
            """,
            base_id,
            UUID(build_result.base.reading_record_id),
            build_result.base.text,
            build_result.base.content_sha256,
            build_result.base.content_utf16_length,
            build_result.base.canonicalizer_version,
            build_result.base.builder_version,
            build_result.base.segmenter_version,
            build_result.base.language,
            build_result.base.title_snapshot,
            jsonb_param(_navigation_json_from_build_result(build_result)),
            created_at,
        )

    async def insert_reading_units(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        units: tuple[BuiltReadingUnit, ...],
    ) -> None:
        for unit in units:
            await conn.execute(
                """
                INSERT INTO reading_units (
                    reading_record_id,
                    base_id,
                    unit_id,
                    order_index,
                    unit_type,
                    boundary_quality,
                    base_start_utf16,
                    base_end_utf16,
                    text_hash,
                    metadata_json
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10::jsonb
                )
                """,
                record_id,
                base_id,
                unit.unit_id,
                unit.order_index,
                unit.unit_type,
                unit.boundary_quality,
                unit.base_start_utf16,
                unit.base_end_utf16,
                unit.text_hash,
                jsonb_param({}),
            )

    async def insert_anchor_segments(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        anchor_segments: tuple[BuiltAnchorSegment, ...],
    ) -> None:
        for segment in anchor_segments:
            await conn.execute(
                """
                INSERT INTO anchor_segments (
                    reading_record_id,
                    base_id,
                    unit_id,
                    anchor_segment_id,
                    sentence_id,
                    paragraph_id,
                    order_index,
                    unit_order_index,
                    segment_type,
                    base_start_utf16,
                    base_end_utf16,
                    unit_start_utf16,
                    unit_end_utf16,
                    text_hash,
                    boundary_quality
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10,
                    $11,
                    $12,
                    $13,
                    $14,
                    $15
                )
                """,
                record_id,
                base_id,
                segment.unit_id,
                segment.anchor_segment_id,
                segment.sentence_id,
                segment.paragraph_id,
                segment.order_index,
                segment.unit_order_index,
                segment.segment_type,
                segment.base_start_utf16,
                segment.base_end_utf16,
                segment.unit_start_utf16,
                segment.unit_end_utf16,
                segment.text_hash,
                segment.boundary_quality,
            )

    async def set_active_base_and_mark_article_ready(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        updated_at: datetime,
    ) -> None:
        base_row = await conn.fetchrow(
            """
            SELECT reading_record_id, record_generation, status
            FROM reading_bases
            WHERE id = $1
            """,
            base_id,
        )
        if base_row is None:
            raise ValueError(f"active base {base_id} does not exist")
        if base_row["reading_record_id"] != record_id:
            raise ValueError("active base must belong to the same reading record")
        if int(base_row["record_generation"]) != expected_generation:
            raise ValueError("active base must match the reading record generation")
        if base_row["status"] != "active":
            raise ValueError("active base must have status 'active'")

        result = await conn.execute(
            """
            UPDATE reading_records
            SET active_base_id = $2,
                lifecycle_status = 'active',
                product_state = 'readable_enhancing',
                readiness_state = 'article_ready',
                updated_at = $4
            WHERE id = $1
              AND generation = $3
            """,
            record_id,
            base_id,
            expected_generation,
            updated_at,
        )
        if result != "UPDATE 1":
            raise ValueError("reading record generation mismatch while setting active base")

    async def update_record_product_state_if_active(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        expected_generation: int,
        next_product_state: ReadingRecordProductState,
        updated_at: datetime,
    ) -> bool:
        result = await conn.execute(
            """
            UPDATE reading_records
            SET product_state = $3,
                updated_at = $4
            WHERE id = $1
              AND generation = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
              AND product_state IN ('processing', 'readable_enhancing')
            """,
            record_id,
            expected_generation,
            next_product_state,
            updated_at,
        )
        return result == "UPDATE 1"

    async def ensure_event_sequence_row(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        updated_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reader_event_sequences (reading_record_id, next_sequence, updated_at)
            VALUES ($1, 1, $2)
            ON CONFLICT (reading_record_id) DO NOTHING
            """,
            record_id,
            updated_at,
        )

    async def allocate_event_sequence(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
    ) -> int:
        sequence = await conn.fetchval(
            """
            UPDATE reader_event_sequences
            SET next_sequence = next_sequence + 1,
                updated_at = NOW()
            WHERE reading_record_id = $1
            RETURNING next_sequence - 1
            """,
            record_id,
        )
        if not isinstance(sequence, int):
            raise ValueError(f"reader_event_sequences missing for record {record_id}")
        return sequence

    async def insert_reader_event(
        self,
        conn: asyncpg.Connection,
        *,
        event_id: UUID,
        record_id: UUID,
        sequence: int,
        event_type: str,
        payload_json: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO reader_events (
                id,
                reading_record_id,
                sequence,
                event_type,
                payload_json,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            event_id,
            record_id,
            sequence,
            event_type,
            jsonb_param(payload_json),
            created_at,
        )

    async def upsert_parsed_decision(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        base_id: UUID,
        unit_id: str,
        policy_code: str,
        parsed_state: str,
        rationale_code: str | None = None,
        coverage_json: dict[str, Any] | None = None,
        source_layer_id: UUID | None = None,
        source_job_id: UUID | None = None,
        decision_json: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO parsed_decisions (
                reading_record_id,
                base_id,
                unit_id,
                policy_code,
                parsed_state,
                rationale_code,
                coverage_json,
                source_layer_id,
                source_job_id,
                decision_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
            ON CONFLICT (reading_record_id, base_id, unit_id, policy_code)
            DO UPDATE SET
                parsed_state = EXCLUDED.parsed_state,
                rationale_code = EXCLUDED.rationale_code,
                coverage_json = EXCLUDED.coverage_json,
                source_layer_id = EXCLUDED.source_layer_id,
                source_job_id = EXCLUDED.source_job_id,
                decision_json = EXCLUDED.decision_json,
                created_at = NOW()
            """,
            reading_record_id,
            base_id,
            unit_id,
            policy_code,
            parsed_state,
            rationale_code,
            jsonb_param(coverage_json or {}),
            source_layer_id,
            source_job_id,
            jsonb_param(decision_json or {}),
        )

    async def load_snapshot_facts(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> LoadedReaderSnapshotFacts:
        record_row = await conn.fetchrow(
            """
            SELECT
                r.id,
                r.user_id,
                r.source_type,
                r.title,
                r.language,
                r.product_state,
                r.generation,
                r.active_base_id,
                r.created_at AS record_created_at,
                r.updated_at AS record_updated_at,
                b.id AS base_id,
                b.record_generation,
                b.text,
                b.content_sha256,
                b.content_utf16_length,
                b.canonicalizer_version,
                b.builder_version,
                b.segmenter_version,
                b.language AS base_language,
                b.title_snapshot,
                b.navigation_json,
                b.status AS base_status,
                b.created_at AS base_created_at,
                seq.next_sequence
            FROM reading_records r
            LEFT JOIN reading_bases b
              ON b.id = r.active_base_id
            LEFT JOIN reader_event_sequences seq
              ON seq.reading_record_id = r.id
            WHERE r.id = $1
              AND r.user_id = $2
              AND r.deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
        if record_row is None:
            raise LookupError(f"reading record {record_id} not found for user {user_id}")

        active_base_id = record_row["active_base_id"]
        if active_base_id is None or record_row["base_id"] is None:
            raise ValueError("reader snapshot requires an active base")

        base_id = UUID(str(record_row["base_id"]))
        record_generation = int(record_row["generation"])
        base_generation = int(record_row["record_generation"])

        if expected_base_id is not None and base_id != expected_base_id:
            raise ValueError(
                f"snapshot base_id {base_id} does not match expected {expected_base_id}"
            )
        if expected_generation is not None and record_generation != expected_generation:
            raise ValueError(
                "snapshot generation "
                f"{record_generation} does not match expected {expected_generation}"
            )
        if UUID(str(active_base_id)) != base_id:
            raise ValueError("active_base_id does not resolve to the selected snapshot base")
        if base_generation != record_generation:
            raise ValueError("active base generation does not match the reading record generation")
        if record_row["base_status"] != "active":
            raise ValueError("reader snapshot requires active_base_id to point to status='active'")

        input_row = await conn.fetchrow(
            """
            SELECT metadata_json
            FROM original_inputs
            WHERE reading_record_id = $1
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            record_id,
        )

        latest_event_row = await conn.fetchrow(
            """
            SELECT sequence, created_at
            FROM reader_events
            WHERE reading_record_id = $1
            ORDER BY sequence DESC
            LIMIT 1
            """,
            record_id,
        )
        if latest_event_row is None:
            raise ValueError("reader snapshot requires at least one committed reader event")

        next_sequence = record_row["next_sequence"]
        if next_sequence is None:
            raise ValueError("reader snapshot requires reader_event_sequences state")
        last_event_sequence = int(next_sequence) - 1
        if last_event_sequence != int(latest_event_row["sequence"]):
            raise ValueError("reader event counter does not match the latest committed event")

        base_text = str(record_row["text"])
        if utf16_code_unit_length(base_text) != int(record_row["content_utf16_length"]):
            raise ValueError("reading_bases.content_utf16_length does not match stored base text")
        if hashlib.sha256(base_text.encode("utf-8")).hexdigest() != record_row["content_sha256"]:
            raise ValueError("reading_bases.content_sha256 does not match stored base text")

        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, unit_type, boundary_quality,
                   base_start_utf16, base_end_utf16, text_hash
            FROM reading_units
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
        anchor_rows = await conn.fetch(
            """
            SELECT unit_id, anchor_segment_id, sentence_id, paragraph_id,
                   order_index, unit_order_index, segment_type, boundary_quality,
                   base_start_utf16, base_end_utf16, unit_start_utf16, unit_end_utf16, text_hash
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
        if not unit_rows or not anchor_rows:
            raise ValueError("reader snapshot requires persisted units and anchor segments")

        navigation_map = _navigation_map_by_unit_id(record_row["navigation_json"])
        base_language = record_row["base_language"] or record_row["language"]
        title_snapshot = record_row["title_snapshot"] or record_row["title"]
        source_metadata = (
            ensure_json_object(input_row["metadata_json"]) if input_row is not None else {}
        )
        stable_base = StableReadingBase(
            reading_record_id=str(record_id),
            base_id=str(base_id),
            text=base_text,
            content_sha256=str(record_row["content_sha256"]),
            content_utf16_length=int(record_row["content_utf16_length"]),
            canonicalizer_version=str(record_row["canonicalizer_version"]),
            builder_version=str(record_row["builder_version"]),
            segmenter_version=str(record_row["segmenter_version"]),
            language=str(base_language) if base_language is not None else None,
            title_snapshot=str(title_snapshot) if title_snapshot is not None else None,
        )

        units: list[BuiltReadingUnit] = []
        navigation_units: list[NavigationUnitFact] = []
        for row in unit_rows:
            unit_id = str(row["unit_id"])
            unit_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            if unit_text is None:
                raise ValueError(
                    f"reading unit {unit_id} does not round-trip from stored base text"
                )
            if compute_text_range_hash(unit_text) != row["text_hash"]:
                raise ValueError(
                    f"reading unit {unit_id} text_hash does not match stored base text"
                )

            navigation_item = navigation_map.get(unit_id, {})
            boundary_quality = str(
                navigation_item.get("boundary_quality") or row["boundary_quality"]
            )
            label = navigation_item.get("label")
            label_text = label if isinstance(label, str) else None

            units.append(
                BuiltReadingUnit(
                    reading_record_id=str(record_id),
                    base_id=str(base_id),
                    unit_id=unit_id,
                    order_index=int(row["order_index"]),
                    unit_type=str(row["unit_type"]),
                    boundary_quality=boundary_quality,
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                    text_hash=str(row["text_hash"]),
                    text=unit_text,
                    label=label_text,
                )
            )
            navigation_units.append(
                NavigationUnitFact(
                    unit_id=unit_id,
                    order_index=int(row["order_index"]),
                    unit_type=str(row["unit_type"]),
                    boundary_quality=boundary_quality,
                    label=label_text,
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                )
            )

        units_by_id = {unit.unit_id: unit for unit in units}
        anchor_segments: list[BuiltAnchorSegment] = []
        for row in anchor_rows:
            unit_id = str(row["unit_id"])
            unit = units_by_id.get(unit_id)
            if unit is None:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} references unknown unit {unit_id}"
                )

            segment_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            if segment_text is None:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} does not round-trip from stored base text"
                )
            unit_local_text = slice_by_utf16_offsets(
                unit.text,
                int(row["unit_start_utf16"]),
                int(row["unit_end_utf16"]),
            )
            if unit_local_text != segment_text:
                raise ValueError(
                    f"anchor segment {row['anchor_segment_id']} local offsets do not match its unit"
                )
            if compute_text_range_hash(segment_text) != row["text_hash"]:
                raise ValueError(
                    "anchor segment "
                    f"{row['anchor_segment_id']} text_hash does not match stored text"
                )

            anchor_segments.append(
                BuiltAnchorSegment(
                    reading_record_id=str(record_id),
                    base_id=str(base_id),
                    unit_id=unit_id,
                    anchor_segment_id=str(row["anchor_segment_id"]),
                    sentence_id=str(row["sentence_id"] or row["anchor_segment_id"]),
                    paragraph_id=str(row["paragraph_id"]),
                    order_index=int(row["order_index"]),
                    unit_order_index=int(row["unit_order_index"]),
                    segment_type=str(row["segment_type"]),
                    boundary_quality=str(row["boundary_quality"]),
                    base_start_utf16=int(row["base_start_utf16"]),
                    base_end_utf16=int(row["base_end_utf16"]),
                    unit_start_utf16=int(row["unit_start_utf16"]),
                    unit_end_utf16=int(row["unit_end_utf16"]),
                    text_hash=str(row["text_hash"]),
                    text=segment_text,
                )
            )

        build_result = ReadingBaseBuildResult(
            base=stable_base,
            units=tuple(units),
            anchor_segments=tuple(anchor_segments),
            navigation_units=tuple(navigation_units),
        )
        validate_reading_base_build_result(build_result)

        enhancement_rows = await conn.fetch(
            """
            SELECT id, layer_type, layer_subtype, target_scope, target_key,
                   schema_version, output_json, published_at
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND status = 'published'
            ORDER BY layer_type, target_scope, target_key, published_at, id
            """,
            record_id,
            base_id,
            record_generation,
        )
        parsed_rows = await conn.fetch(
            """
            SELECT unit_id, policy_code, parsed_state, rationale_code
            FROM parsed_decisions
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY unit_id, policy_code
            """,
            record_id,
            base_id,
        )

        return LoadedReaderSnapshotFacts(
            build_result=build_result,
            record=ReaderSnapshotRecord(
                title=str(title_snapshot) if title_snapshot is not None else "Untitled Reading",
                created_at=record_row["record_created_at"],
                source_type=str(record_row["source_type"]),
                source_metadata=source_metadata,
                product_state=str(record_row["product_state"]),
            ),
            last_event_sequence=last_event_sequence,
            snapshot_taken_at=latest_event_row["created_at"],
            enhancement_layers=tuple(
                ReaderSnapshotLayer(
                    layer_id=str(row["id"]),
                    layer_type=str(row["layer_type"]),
                    layer_subtype=(
                        str(row["layer_subtype"]) if row["layer_subtype"] is not None else None
                    ),
                    base_id=str(base_id),
                    target_scope=str(row["target_scope"]),
                    target_key=str(row["target_key"]),
                    schema_version=int(row["schema_version"]),
                    output=row["output_json"],
                    published_at=row["published_at"],
                )
                for row in enhancement_rows
            ),
            parsed_decisions=tuple(
                ReaderSnapshotParsedDecision(
                    unit_id=str(row["unit_id"]),
                    policy_code=str(row["policy_code"]),
                    parsed_state=str(row["parsed_state"]),
                    rationale_code=(
                        str(row["rationale_code"]) if row["rationale_code"] is not None else None
                    ),
                )
                for row in parsed_rows
            ),
        )

    async def list_user_records(
        self,
        *,
        user_id: UUID,
        limit: int = 20,
    ) -> tuple[tuple[ReaderRecordSummary, ...], int]:
        pool = self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    r.id,
                    r.title,
                    r.source_type,
                    r.product_state,
                    r.readiness_state,
                    r.created_at,
                    COALESCE(
                        (SELECT metadata_json FROM original_inputs
                         WHERE reading_record_id = r.id
                         ORDER BY created_at ASC, id ASC
                         LIMIT 1),
                        '{}'::jsonb
                    ) AS source_metadata,
                    COALESCE(
                        (SELECT (next_sequence - 1)::bigint FROM reader_event_sequences
                         WHERE reading_record_id = r.id),
                        0
                    ) AS last_event_sequence
                FROM reading_records r
                WHERE r.user_id = $1
                  AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            total = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reading_records
                WHERE user_id = $1
                  AND deleted_at IS NULL
                """,
                user_id,
            )
        summaries = tuple(
            ReaderRecordSummary(
                record_id=row["id"],
                title=row["title"],
                source_type=str(row["source_type"]),
                product_state=str(row["product_state"]),
                readiness_state=str(row["readiness_state"]),
                created_at=row["created_at"],
                source_metadata=ensure_json_object(row["source_metadata"]),
                last_event_sequence=int(row["last_event_sequence"]),
            )
            for row in rows
        )
        return summaries, int(total)


def _navigation_json_from_build_result(
    build_result: ReadingBaseBuildResult,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "units": [
            {
                "unit_id": unit.unit_id,
                "order_index": unit.order_index,
                "unit_type": unit.unit_type,
                "boundary_quality": unit.boundary_quality,
                "label": unit.label,
                "base_start_utf16": unit.base_start_utf16,
                "base_end_utf16": unit.base_end_utf16,
            }
            for unit in build_result.navigation_units
        ]
    }


def _navigation_map_by_unit_id(navigation_json: Any) -> dict[str, dict[str, Any]]:
    navigation = ensure_json_object(navigation_json)
    units = ensure_json_array(navigation.get("units"))
    result: dict[str, dict[str, Any]] = {}
    for item in units:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        if isinstance(unit_id, str):
            result[unit_id] = dict(item)
    return result
