"""D6-I3N Extracted Artifact Text → Stable/Candidate Materialization.

Takes an **existing** reading_record + original_input (created by
``ArtifactInputApplicationService.submit_input`` and populated by the
``ArtifactExtractionWorkerService``) and materializes the extracted
``original_inputs.source_text`` into either:

- a **stable document** + reading_base + units + segments (when the input is
  suitable for direct stable freeze), or
- a **candidate_reading_documents** row on the same record (when the input
  requires user confirmation before freezing), or
- an **action_required** state transition (when the input is rejected).

This service does **not** create new ``reading_records`` or ``original_inputs``
— it operates on the existing ones. It does **not** do OCR/PDF parsing.

Transaction model: **service-owned single transaction**. The entire
validate → suitability → freeze/persist → set_active_base → publish_event
(or create_candidate → set_readiness_state, or set_action_required) pipeline
runs inside one ``async with conn.transaction():``. Any failure rolls back all
writes, preventing half-materialized records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
)
from app.services.reader_orchestration.base_builder import (
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    DETERMINISTIC_SEGMENTER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    _build_candidate_blocks,
    _canonical_text_preview,
    _candidate_quality_json,
    _candidate_source_refs_json,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    persist_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.event_runtime import ReaderEventRuntime
from app.services.reader_orchestration.input_document_normalizer import (
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository

MATERIALIZATION_SOURCE = "extracted_artifact_materialization"

# content_type → InputAdapterSourceType mapping for text artifacts.
# application/octet-stream is resolved by source_filename extension.
_MARKDOWN_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/markdown",
    "text/x-markdown",
})
_PLAIN_CONTENT_TYPES: frozenset[str] = frozenset({"text/plain"})


class ExtractedArtifactMaterializationError(ValueError):
    """Typed error for materialization failures (validation, fence, persistence)."""


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Result of materializing extracted artifact text.

    Fields populated depend on ``outcome``:
    - ``stable_document_ready``: stable_document_id, base_id,
      article_ready_event_id, article_ready_sequence
    - ``candidate_document_required``: candidate_document_id, block_count,
      canonical_text_preview
    - ``input_rejected_or_action_required``: only common fields
    """

    outcome: str
    reading_record_id: UUID
    original_input_id: UUID
    source_artifact_id: UUID
    record_generation: int
    suitability: InputSuitabilityResult
    # stable path
    stable_document_id: UUID | None = None
    base_id: UUID | None = None
    article_ready_event_id: UUID | None = None
    article_ready_sequence: int | None = None
    # candidate path
    candidate_document_id: UUID | None = None
    block_count: int | None = None
    canonical_text_preview: str | None = None
    # diagnostic
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class ExtractedArtifactMaterializationService:
    """Materializes extracted artifact text into stable or candidate documents.

    Operates on an existing reading_record + original_input (created by
    ``ArtifactInputApplicationService.submit_input``). Does NOT create new
    reading_records or original_inputs.

    Uses a **service-owned single transaction** for the full pipeline.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        from app.database import connection as db_connection

        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def materialize_extracted_artifact(
        self,
        *,
        reading_record_id: UUID,
        original_input_id: UUID,
        source_artifact_id: UUID,
        user_id: UUID,
        expected_generation: int,
    ) -> MaterializationResult:
        """Materialize extracted text for an existing artifact reading record.

        Runs the full validate → suitability → freeze/persist pipeline in a
        single service-owned transaction. Any failure rolls back all writes.

        The caller MUST pass the exact ``original_input_id`` and
        ``source_artifact_id`` to materialize — the service does NOT pick one
        arbitrarily. This prevents multi-input or derived-artifact scenarios
        from selecting the wrong source of truth.

        Raises ``ExtractedArtifactMaterializationError`` on validation/fence
        failures (stale generation, active_base already set, artifact not
        bound, empty source_text, etc.).
        """
        now = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # 1. Lock and validate reading_records
                record_row = await conn.fetchrow(
                    """
                    SELECT id, user_id, generation, active_base_id,
                           lifecycle_status, deleted_at, product_state,
                           readiness_state
                    FROM reading_records
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    reading_record_id,
                )
                if record_row is None:
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} not found"
                    )
                if record_row["user_id"] != user_id:
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} does not belong "
                        f"to user {user_id}"
                    )
                if record_row["deleted_at"] is not None:
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} is deleted"
                    )
                if record_row["lifecycle_status"] != "active":
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} lifecycle_status "
                        f"is {record_row['lifecycle_status']!r}, expected 'active'"
                    )
                if int(record_row["generation"]) != expected_generation:
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} generation "
                        f"is {record_row['generation']}, expected "
                        f"{expected_generation}"
                    )
                if record_row["active_base_id"] is not None:
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} already has "
                        f"active_base_id {record_row['active_base_id']}; "
                        f"materialization must run before any base exists"
                    )
                if (
                    record_row["product_state"] != "processing"
                    or record_row["readiness_state"] != "submitted"
                ):
                    raise ExtractedArtifactMaterializationError(
                        f"reading_record {reading_record_id} is not in "
                        f"processing/submitted state (got "
                        f"product_state={record_row['product_state']!r}, "
                        f"readiness_state={record_row['readiness_state']!r}); "
                        f"materialization has already run or state was advanced"
                    )

                # 2. Lock and validate the SPECIFIC original_input
                input_row = await conn.fetchrow(
                    """
                    SELECT id, reading_record_id, user_id, source_text,
                           source_ref_json, metadata_json, content_sha256
                    FROM original_inputs
                    WHERE id = $1
                      AND reading_record_id = $2
                      AND user_id = $3
                    FOR UPDATE
                    """,
                    original_input_id,
                    reading_record_id,
                    user_id,
                )
                if input_row is None:
                    raise ExtractedArtifactMaterializationError(
                        f"original_input {original_input_id} not found for "
                        f"reading_record {reading_record_id} / user {user_id}"
                    )
                source_text = input_row["source_text"]
                if source_text is None or not source_text.strip():
                    raise ExtractedArtifactMaterializationError(
                        f"original_input {input_row['id']} source_text is "
                        f"empty; extraction must complete before materialization"
                    )

                # 3. Lock and validate the SPECIFIC source_artifact
                artifact_row = await conn.fetchrow(
                    """
                    SELECT id, reading_record_id, original_input_id, user_id,
                           artifact_kind, storage_provider, bucket, object_key,
                           endpoint, content_type, byte_size, content_sha256,
                           source_filename, status
                    FROM source_artifacts
                    WHERE id = $1
                      AND reading_record_id = $2
                      AND original_input_id = $3
                      AND user_id = $4
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    source_artifact_id,
                    reading_record_id,
                    original_input_id,
                    user_id,
                )
                if artifact_row is None:
                    raise ExtractedArtifactMaterializationError(
                        f"source_artifact {source_artifact_id} not found for "
                        f"reading_record {reading_record_id} / original_input "
                        f"{original_input_id} / user {user_id}"
                    )
                if artifact_row["status"] != "available":
                    raise ExtractedArtifactMaterializationError(
                        f"source_artifact {artifact_row['id']} status is "
                        f"{artifact_row['status']!r}, expected 'available'"
                    )
                if artifact_row["artifact_kind"] != "original_upload":
                    raise ExtractedArtifactMaterializationError(
                        f"source_artifact {artifact_row['id']} artifact_kind is "
                        f"{artifact_row['artifact_kind']!r}, expected "
                        f"'original_upload'"
                    )
                if artifact_row["storage_provider"] != "oss":
                    raise ExtractedArtifactMaterializationError(
                        f"source_artifact {artifact_row['id']} storage_provider "
                        f"is {artifact_row['storage_provider']!r}, expected 'oss'"
                    )

                # 4. Derive source_type and build suitability request
                source_type = _derive_source_type(
                    artifact_row["content_type"],
                    artifact_row["source_filename"],
                )
                source_metadata = _build_source_metadata(artifact_row, input_row)
                filename = artifact_row["source_filename"]

                suitability_request = InputSuitabilityRequest(
                    source_type=source_type,
                    text=source_text,
                    filename=filename,
                    source_metadata=source_metadata,
                )
                suitability = evaluate_input_suitability(suitability_request)

                # 5. Branch on outcome
                if suitability.outcome == "stable_document_ready":
                    return await self._materialize_stable(
                        conn=conn,
                        record_id=reading_record_id,
                        user_id=user_id,
                        input_id=input_row["id"],
                        artifact_id=artifact_row["id"],
                        generation=expected_generation,
                        source_type=source_type,
                        filename=filename,
                        source_metadata=source_metadata,
                        source_text=source_text,
                        suitability=suitability,
                        now=now,
                    )
                elif suitability.outcome == "candidate_document_required":
                    return await self._materialize_candidate(
                        conn=conn,
                        record_id=reading_record_id,
                        user_id=user_id,
                        input_id=input_row["id"],
                        artifact_id=artifact_row["id"],
                        generation=expected_generation,
                        source_type=source_type,
                        filename=filename,
                        source_metadata=source_metadata,
                        source_text=source_text,
                        suitability=suitability,
                        now=now,
                    )
                else:
                    return await self._materialize_rejected(
                        conn=conn,
                        record_id=reading_record_id,
                        input_id=input_row["id"],
                        artifact_id=artifact_row["id"],
                        generation=expected_generation,
                        suitability=suitability,
                        now=now,
                    )

    # ------------------------------------------------------------------
    # Stable document path
    # ------------------------------------------------------------------

    async def _materialize_stable(
        self,
        *,
        conn: asyncpg.Connection,
        record_id: UUID,
        user_id: UUID,
        input_id: UUID,
        artifact_id: UUID,
        generation: int,
        source_type: InputAdapterSourceType,
        filename: str | None,
        source_metadata: dict[str, Any],
        source_text: str,
        suitability: InputSuitabilityResult,
        now: datetime,
    ) -> MaterializationResult:
        # Normalize → freeze plan → persist → set_active_base → publish event
        request = InputSuitabilityRequest(
            source_type=source_type,
            text=source_text,
            filename=filename,
            source_metadata=source_metadata,
        )
        normalized = normalize_input_document(request)

        source_profile_json: dict[str, Any] = {
            "source_type": source_type,
            "filename": filename,
            "source_metadata": source_metadata,
            "suitability": {
                "outcome": suitability.outcome,
                "flags": list(suitability.flags),
                "reasons": list(suitability.reasons),
            },
            "materialization_source": MATERIALIZATION_SOURCE,
        }
        if normalized.title is not None:
            source_profile_json["title"] = normalized.title

        plan = build_stable_document_freeze_plan(
            reading_record_id=str(record_id),
            record_generation=generation,
            document_version=generation,
            title=normalized.title,
            blocks=normalized.blocks,
            source_profile_json=source_profile_json,
        )

        freeze_result = await persist_stable_document_freeze_plan(
            conn,
            plan=plan,
            canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
            builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
            segmenter_version=DETERMINISTIC_SEGMENTER_VERSION,
            user_id=user_id,
            now=now,
        )
        if freeze_result.base_id is None:
            raise ExtractedArtifactMaterializationError(
                f"freeze persistence returned null base_id for record "
                f"{record_id}"
            )

        await self._repository.set_active_base_and_mark_article_ready(
            conn,
            record_id=record_id,
            base_id=freeze_result.base_id,
            expected_generation=generation,
            updated_at=now,
        )

        payload = _build_article_ready_payload(
            record_id=record_id,
            source_type=source_type,
            filename=filename,
            title=normalized.title,
            freeze_result=freeze_result,
            suitability=suitability,
        )
        event_envelope = await self._event_runtime.publish_event_in_transaction(
            conn,
            record_id=record_id,
            event_type="article_ready",
            payload_json=payload,
            created_at=now,
        )

        return MaterializationResult(
            outcome="stable_document_ready",
            reading_record_id=record_id,
            original_input_id=input_id,
            source_artifact_id=artifact_id,
            record_generation=generation,
            suitability=suitability,
            stable_document_id=freeze_result.stable_document_id,
            base_id=freeze_result.base_id,
            article_ready_event_id=event_envelope.event_id,
            article_ready_sequence=event_envelope.sequence,
            flags=list(suitability.flags),
            reasons=list(suitability.reasons),
        )

    # ------------------------------------------------------------------
    # Candidate document path
    # ------------------------------------------------------------------

    async def _materialize_candidate(
        self,
        *,
        conn: asyncpg.Connection,
        record_id: UUID,
        user_id: UUID,
        input_id: UUID,
        artifact_id: UUID,
        generation: int,
        source_type: InputAdapterSourceType,
        filename: str | None,
        source_metadata: dict[str, Any],
        source_text: str,
        suitability: InputSuitabilityResult,
        now: datetime,
    ) -> MaterializationResult:
        blocks, title = _build_candidate_blocks(
            source_type=source_type,
            text=source_text,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=input_id,
        )
        candidate_document_id = uuid4()
        preview = _canonical_text_preview(suitability=suitability, blocks=blocks)
        source_refs = _candidate_source_refs_json(
            source_type=source_type,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=input_id,
        )
        quality = _candidate_quality_json(suitability=suitability)

        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9::jsonb,
                    'ready', $10, $10)
            """,
            candidate_document_id,
            record_id,
            user_id,
            generation,
            title,
            jsonb_param([block.model_dump(mode="json") for block in blocks]),
            preview,
            jsonb_param(source_refs),
            jsonb_param(quality),
            now,
        )

        # Advance readiness_state to candidate_base_ready
        result = await conn.execute(
            """
            UPDATE reading_records
            SET readiness_state = 'candidate_base_ready',
                product_state = 'needs_confirmation',
                updated_at = $2
            WHERE id = $1
              AND generation = $3
              AND lifecycle_status = 'active'
              AND active_base_id IS NULL
            """,
            record_id,
            now,
            generation,
        )
        if result != "UPDATE 1":
            raise ExtractedArtifactMaterializationError(
                f"failed to advance reading_record {record_id} to "
                f"candidate_base_ready (expected UPDATE 1, got {result!r})"
            )

        return MaterializationResult(
            outcome="candidate_document_required",
            reading_record_id=record_id,
            original_input_id=input_id,
            source_artifact_id=artifact_id,
            record_generation=generation,
            suitability=suitability,
            candidate_document_id=candidate_document_id,
            block_count=len(blocks),
            canonical_text_preview=preview,
            flags=list(suitability.flags),
            reasons=list(suitability.reasons),
        )

    # ------------------------------------------------------------------
    # Rejected path
    # ------------------------------------------------------------------

    async def _materialize_rejected(
        self,
        *,
        conn: asyncpg.Connection,
        record_id: UUID,
        input_id: UUID,
        artifact_id: UUID,
        generation: int,
        suitability: InputSuitabilityResult,
        now: datetime,
    ) -> MaterializationResult:
        result = await conn.execute(
            """
            UPDATE reading_records
            SET product_state = 'action_required',
                updated_at = $2
            WHERE id = $1
              AND generation = $3
              AND lifecycle_status = 'active'
            """,
            record_id,
            now,
            generation,
        )
        if result != "UPDATE 1":
            raise ExtractedArtifactMaterializationError(
                f"failed to mark reading_record {record_id} as action_required "
                f"(expected UPDATE 1, got {result!r})"
            )

        return MaterializationResult(
            outcome="input_rejected_or_action_required",
            reading_record_id=record_id,
            original_input_id=input_id,
            source_artifact_id=artifact_id,
            record_generation=generation,
            suitability=suitability,
            flags=list(suitability.flags),
            reasons=list(suitability.reasons),
        )


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _derive_source_type(
    content_type: str | None,
    source_filename: str | None,
) -> InputAdapterSourceType:
    """Derive InputAdapterSourceType from artifact content_type + filename.

    Fail-closed: unknown content types raise instead of defaulting to txt_file.
    This prevents PDF/image extracted text from bypassing the suitability
    gate's ``pdf_text``/``ocr_text`` candidate-review rules.
    """
    ct = (content_type or "").strip().lower().split(";")[0].strip()
    lower_name = (source_filename or "").lower()

    if ct in _MARKDOWN_CONTENT_TYPES:
        return "markdown_file"
    if ct in _PLAIN_CONTENT_TYPES:
        return "txt_file"
    if ct == "application/pdf":
        return "pdf_text"
    if ct.startswith("image/"):
        return "ocr_text"
    if ct == "application/octet-stream":
        if lower_name.endswith(".md"):
            return "markdown_file"
        if lower_name.endswith(".txt"):
            return "txt_file"
        raise ExtractedArtifactMaterializationError(
            f"cannot derive source_type from application/octet-stream "
            f"artifact without .md/.txt filename extension "
            f"(filename={source_filename!r})"
        )
    raise ExtractedArtifactMaterializationError(
        f"cannot derive source_type from unknown content_type "
        f"{content_type!r} (filename={source_filename!r}); supported: "
        f"text/plain, text/markdown, text/x-markdown, application/pdf, "
        f"image/*, application/octet-stream+(.md|.txt)"
    )


def _build_source_metadata(
    artifact_row: asyncpg.Record,
    input_row: asyncpg.Record,
) -> dict[str, Any]:
    """Build source_metadata dict from artifact + original_input rows."""
    metadata: dict[str, Any] = {
        "content_type": artifact_row["content_type"],
        "artifact_kind": artifact_row["artifact_kind"],
        "storage_provider": artifact_row["storage_provider"],
        "bucket": artifact_row["bucket"],
        "object_key": artifact_row["object_key"],
    }
    if artifact_row["byte_size"] is not None:
        metadata["byte_size"] = artifact_row["byte_size"]
    if artifact_row["content_sha256"] is not None:
        metadata["content_sha256"] = artifact_row["content_sha256"]
    if artifact_row["endpoint"] is not None:
        metadata["endpoint"] = artifact_row["endpoint"]
    if input_row["content_sha256"] is not None:
        metadata["original_input_content_sha256"] = input_row["content_sha256"]
    return metadata


def _build_article_ready_payload(
    *,
    record_id: UUID,
    source_type: InputAdapterSourceType,
    filename: str | None,
    title: str | None,
    freeze_result: Any,
    suitability: InputSuitabilityResult,
) -> dict[str, Any]:
    """Build the article_ready event payload for the materialization path."""
    payload: dict[str, Any] = {
        "record_id": str(record_id),
        "stable_document_id": str(freeze_result.stable_document_id),
        "base_id": str(freeze_result.base_id),
        "generation": freeze_result.record_generation,
        "document_version": freeze_result.document_version,
        "readiness_state": "article_ready",
        "product_state": "readable_enhancing",
        "content_sha256": freeze_result.content_sha256,
        "canonical_text_sha256": freeze_result.canonical_text_sha256,
        "block_count": freeze_result.block_count,
        "source": MATERIALIZATION_SOURCE,
        "source_type": source_type,
        "suitability": {
            "outcome": suitability.outcome,
            "flags": list(suitability.flags),
            "reasons": list(suitability.reasons),
        },
    }
    if filename is not None:
        payload["filename"] = filename
    if title is not None:
        payload["title"] = title
    return payload
