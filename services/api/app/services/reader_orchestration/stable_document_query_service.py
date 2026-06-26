from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)


class StableDocumentQueryError(ValueError):
    """Raised when the active stable document projection facts are incomplete."""


@dataclass(frozen=True, slots=True)
class StableDocumentProjectionBase:
    base_id: UUID
    content_sha256: str
    content_utf16_length: int
    canonicalizer_version: str
    builder_version: str
    segmenter_version: str
    language: str | None
    title_snapshot: str | None
    navigation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StableDocumentProjectionStableDocument:
    stable_document_id: UUID
    document_version: int
    title: str | None
    language: str | None
    source_profile: dict[str, Any]
    content_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class StableDocumentProjectionBlock:
    block_id: str
    parent_block_id: str | None
    order_index: int
    block_type: str
    text_content: str | None
    payload: dict[str, Any]
    source_refs: dict[str, Any]
    quality: dict[str, Any]
    canonical_text_start_utf16: int | None
    canonical_text_end_utf16: int | None
    interpretation_policy: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StableDocumentProjectionResult:
    reading_record_id: UUID
    record_generation: int
    active_base_id: UUID
    base: StableDocumentProjectionBase
    stable_document: StableDocumentProjectionStableDocument
    blocks: tuple[StableDocumentProjectionBlock, ...]


def _coerce_json_object(raw: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StableDocumentQueryError(
                f"{field_name} is not valid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise StableDocumentQueryError(
                f"{field_name} parses to a non-object JSON value"
            )
        return dict(parsed)
    raise StableDocumentQueryError(f"{field_name} must be a JSON object")


class StableDocumentQueryService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return ReaderOrchestrationRepository().get_pool()

    async def load_active_stable_document(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> StableDocumentProjectionResult:
        pool = self._get_pool()

        async with pool.acquire() as conn:
            record_row = await conn.fetchrow(
                """
                SELECT generation, active_base_id
                FROM reading_records
                WHERE id = $1
                  AND user_id = $2
                  AND deleted_at IS NULL
                  AND lifecycle_status = 'active'
                """,
                record_id,
                user_id,
            )
            if record_row is None:
                raise LookupError(
                    f"Reading record {record_id} was not found for user {user_id}."
                )

            record_generation = int(record_row["generation"])
            active_base_id_raw = record_row["active_base_id"]
            if active_base_id_raw is None:
                raise StableDocumentQueryError(
                    f"Reading record {record_id} has no active base."
                )
            active_base_id = UUID(str(active_base_id_raw))

            stable_document_row = await conn.fetchrow(
                """
                SELECT id, document_version, title, source_profile_json, content_sha256, status
                FROM stable_reading_documents
                WHERE reading_record_id = $1
                  AND record_generation = $2
                  AND status = 'active'
                """,
                record_id,
                record_generation,
            )
            if stable_document_row is None:
                raise StableDocumentQueryError(
                    f"Reading record {record_id} has no active stable document for generation "
                    f"{record_generation}."
                )

            base_row = await conn.fetchrow(
                """
                SELECT
                    id,
                    content_sha256,
                    content_utf16_length,
                    canonicalizer_version,
                    builder_version,
                    segmenter_version,
                    language,
                    title_snapshot,
                    navigation_json
                FROM reading_bases
                WHERE id = $1
                  AND reading_record_id = $2
                  AND record_generation = $3
                  AND status = 'active'
                """,
                active_base_id,
                record_id,
                record_generation,
            )
            if base_row is None:
                raise StableDocumentQueryError(
                    f"Reading record {record_id} has no active reading base for base_id "
                    f"{active_base_id} generation {record_generation}."
                )

            stable_document_id = UUID(str(stable_document_row["id"]))
            block_rows = await conn.fetch(
                """
                SELECT
                    block_id,
                    parent_block_id,
                    order_index,
                    block_type,
                    text_content,
                    payload_json,
                    source_refs_json,
                    quality_json,
                    canonical_text_start_utf16,
                    canonical_text_end_utf16,
                    interpretation_policy_json
                FROM stable_document_blocks
                WHERE stable_document_id = $1
                ORDER BY order_index ASC
                """,
                stable_document_id,
            )
            if not block_rows:
                raise StableDocumentQueryError(
                    f"Stable document {stable_document_id} has no ordered blocks."
                )

        base_language = (
            str(base_row["language"]) if base_row["language"] is not None else None
        )
        base = StableDocumentProjectionBase(
            base_id=UUID(str(base_row["id"])),
            content_sha256=str(base_row["content_sha256"]),
            content_utf16_length=int(base_row["content_utf16_length"]),
            canonicalizer_version=str(base_row["canonicalizer_version"]),
            builder_version=str(base_row["builder_version"]),
            segmenter_version=str(base_row["segmenter_version"]),
            language=base_language,
            title_snapshot=(
                str(base_row["title_snapshot"])
                if base_row["title_snapshot"] is not None
                else None
            ),
            navigation=_coerce_json_object(
                base_row["navigation_json"],
                field_name="reading_bases.navigation_json",
            ),
        )

        stable_document = StableDocumentProjectionStableDocument(
            stable_document_id=stable_document_id,
            document_version=int(stable_document_row["document_version"]),
            title=(
                str(stable_document_row["title"])
                if stable_document_row["title"] is not None
                else None
            ),
            # stable_reading_documents currently has no dedicated language column.
            language=base_language,
            source_profile=_coerce_json_object(
                stable_document_row["source_profile_json"],
                field_name="stable_reading_documents.source_profile_json",
            ),
            content_sha256=str(stable_document_row["content_sha256"]),
            status=str(stable_document_row["status"]),
        )

        blocks = tuple(
            StableDocumentProjectionBlock(
                block_id=str(row["block_id"]),
                parent_block_id=(
                    str(row["parent_block_id"])
                    if row["parent_block_id"] is not None
                    else None
                ),
                order_index=int(row["order_index"]),
                block_type=str(row["block_type"]),
                text_content=(
                    str(row["text_content"]) if row["text_content"] is not None else None
                ),
                payload=_coerce_json_object(
                    row["payload_json"],
                    field_name=(
                        "stable_document_blocks.payload_json"
                        f"[block_id={row['block_id']}]"
                    ),
                ),
                source_refs=_coerce_json_object(
                    row["source_refs_json"],
                    field_name=(
                        "stable_document_blocks.source_refs_json"
                        f"[block_id={row['block_id']}]"
                    ),
                ),
                quality=_coerce_json_object(
                    row["quality_json"],
                    field_name=(
                        "stable_document_blocks.quality_json"
                        f"[block_id={row['block_id']}]"
                    ),
                ),
                canonical_text_start_utf16=(
                    int(row["canonical_text_start_utf16"])
                    if row["canonical_text_start_utf16"] is not None
                    else None
                ),
                canonical_text_end_utf16=(
                    int(row["canonical_text_end_utf16"])
                    if row["canonical_text_end_utf16"] is not None
                    else None
                ),
                interpretation_policy=_coerce_json_object(
                    row["interpretation_policy_json"],
                    field_name=(
                        "stable_document_blocks.interpretation_policy_json"
                        f"[block_id={row['block_id']}]"
                    ),
                ),
            )
            for row in block_rows
        )

        return StableDocumentProjectionResult(
            reading_record_id=record_id,
            record_generation=record_generation,
            active_base_id=active_base_id,
            base=base,
            stable_document=stable_document,
            blocks=blocks,
        )
