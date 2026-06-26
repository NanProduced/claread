from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.base_builder import (
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    DETERMINISTIC_SEGMENTER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceError,
    StableDocumentFreezePersistenceResult,
    persist_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_plan import (
    StableDocumentFreezePlan,
    StableDocumentFreezePlanError,
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.event_runtime import (
    ReaderEventEnvelope,
    ReaderEventRuntime,
)
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
    normalize_input_document,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)

_READING_RECORD_SOURCE_TYPE_BY_INPUT_SOURCE: dict[InputAdapterSourceType, str] = {
    "pasted_text": "text",
    "txt_file": "file",
    "markdown_file": "markdown",
    "ocr_text": "ocr",
    "pdf_text": "pdf",
    "url_text": "url",
}

_ORIGINAL_INPUT_TYPE_BY_INPUT_SOURCE: dict[InputAdapterSourceType, str] = {
    "pasted_text": "plain_text",
    "txt_file": "file_ref",
    "markdown_file": "markdown",
    "ocr_text": "image_ref",
    "pdf_text": "file_ref",
    "url_text": "url",
}


class StableReadyInputApplicationError(ValueError):
    """Raised when stable-ready input freeze cannot complete."""


@dataclass(frozen=True, slots=True)
class StableReadyInputApplicationResult:
    reading_record_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    document_version: int
    title: str | None
    content_sha256: str
    canonical_text_sha256: str
    block_count: int
    article_ready_event_id: UUID
    article_ready_sequence: int
    suitability: InputSuitabilityResult
    snapshot: ReaderPlateSnapshot


class StableReadyInputApplicationService:
    """Freeze stable-ready input into a new article-ready reading record."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        snapshot_service: ArticleReadyPersistenceService | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._snapshot_service = snapshot_service or ArticleReadyPersistenceService(
            pool=pool,
            repository=self._repository,
        )

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return self._repository.get_pool()

    async def freeze_stable_ready_input_and_load_snapshot(
        self,
        *,
        user_id: UUID,
        source_type: InputAdapterSourceType,
        text: str,
        filename: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        client_record_id: str | None = None,
        language: str | None = "en",
        now: datetime | None = None,
    ) -> StableReadyInputApplicationResult:
        frozen_at = now or datetime.now(UTC)
        language_value = (language or "en").strip() or "en"
        source_metadata_value = dict(source_metadata or {})
        pool = self._get_pool()

        record_id: UUID | None = None
        normalized_title: str | None = None
        suitability: InputSuitabilityResult | None = None
        freeze_result: StableDocumentFreezePersistenceResult | None = None
        envelope: ReaderEventEnvelope | None = None

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    try:
                        normalized = normalize_input_document(
                            InputSuitabilityRequest(
                                source_type=source_type,
                                text=text,
                                filename=filename,
                                source_metadata=source_metadata_value,
                            )
                        )
                    except InputDocumentNormalizationError as exc:
                        raise StableReadyInputApplicationError(
                            "Stable-ready input normalization failed: "
                            f"outcome={exc.outcome}, flags={exc.flags}, reasons={exc.reasons}"
                        ) from exc

                    suitability = normalized.suitability
                    normalized_title = normalized.title
                    record_id = uuid4()
                    original_input_id = uuid4()

                    try:
                        await _insert_reading_record(
                            conn,
                            record_id=record_id,
                            user_id=user_id,
                            client_record_id=client_record_id,
                            source_type=source_type,
                            title=normalized.title,
                            language=language_value,
                            created_at=frozen_at,
                        )
                        await _insert_original_input(
                            conn,
                            original_input_id=original_input_id,
                            record_id=record_id,
                            user_id=user_id,
                            source_type=source_type,
                            text=text,
                            filename=filename,
                            source_metadata=source_metadata_value,
                            created_at=frozen_at,
                        )
                    except Exception as exc:
                        raise StableReadyInputApplicationError(
                            "Failed to create the stable-ready reading record shell: "
                            f"{exc}"
                        ) from exc

                    try:
                        plan = build_stable_document_freeze_plan(
                            reading_record_id=str(record_id),
                            record_generation=1,
                            document_version=1,
                            title=normalized.title,
                            blocks=normalized.blocks,
                            source_profile_json={
                                "source_type": source_type,
                                "filename": filename,
                                "source_metadata": source_metadata_value,
                                "suitability": {
                                    "outcome": normalized.suitability.outcome,
                                    "flags": list(normalized.suitability.flags),
                                    "reasons": list(normalized.suitability.reasons),
                                },
                            },
                        )
                    except (StableDocumentFreezePlanError, ValueError) as exc:
                        raise StableReadyInputApplicationError(
                            f"Failed to build a stable-document freeze plan: {exc}"
                        ) from exc

                    try:
                        freeze_result = await persist_stable_document_freeze_plan(
                            conn,
                            plan=plan,
                            canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
                            builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
                            segmenter_version=DETERMINISTIC_SEGMENTER_VERSION,
                            language=language_value,
                            now=frozen_at,
                        )
                    except (
                        StableDocumentFreezePersistenceError,
                        ValueError,
                        LookupError,
                        RuntimeError,
                        TypeError,
                    ) as exc:
                        raise StableReadyInputApplicationError(
                            f"Stable document freeze persistence failed: {exc}"
                        ) from exc

                    if freeze_result.base_id is None:
                        raise StableReadyInputApplicationError(
                            "Stable-ready freeze returned base_id=None. "
                            "Cannot mark article_ready without an active base."
                        )

                    try:
                        await self._repository.set_active_base_and_mark_article_ready(
                            conn,
                            record_id=record_id,
                            base_id=freeze_result.base_id,
                            expected_generation=freeze_result.record_generation,
                            updated_at=frozen_at,
                        )
                    except (ValueError, LookupError, RuntimeError) as exc:
                        raise StableReadyInputApplicationError(
                            f"Failed to mark reading record {record_id} as article_ready: {exc}"
                        ) from exc

                    payload_json = _build_article_ready_payload(
                        record_id=record_id,
                        source_type=source_type,
                        filename=filename,
                        title=normalized.title,
                        freeze_result=freeze_result,
                        suitability=normalized.suitability,
                    )
                    try:
                        envelope = await self._event_runtime.publish_event_in_transaction(
                            conn,
                            record_id=record_id,
                            event_type="article_ready",
                            payload_json=payload_json,
                            created_at=frozen_at,
                        )
                    except (ValueError, LookupError, RuntimeError, TypeError) as exc:
                        raise StableReadyInputApplicationError(
                            f"Failed to publish article_ready event for reading record {record_id}: {exc}"
                        ) from exc
        except StableReadyInputApplicationError:
            raise
        except Exception as exc:
            raise StableReadyInputApplicationError(
                f"Stable-ready input application service failed unexpectedly: {exc}"
            ) from exc

        assert record_id is not None
        assert suitability is not None
        assert freeze_result is not None
        assert freeze_result.base_id is not None
        assert envelope is not None

        try:
            snapshot = await self._snapshot_service.load_snapshot(
                record_id=record_id,
                user_id=user_id,
                expected_base_id=freeze_result.base_id,
                expected_generation=freeze_result.record_generation,
            )
        except (ValueError, LookupError, RuntimeError) as exc:
            raise StableReadyInputApplicationError(
                f"Failed to reload snapshot after committing stable-ready input for reading record {record_id}: {exc}"
            ) from exc

        return StableReadyInputApplicationResult(
            reading_record_id=record_id,
            stable_document_id=freeze_result.stable_document_id,
            base_id=freeze_result.base_id,
            record_generation=freeze_result.record_generation,
            document_version=freeze_result.document_version,
            title=normalized_title,
            content_sha256=freeze_result.content_sha256,
            canonical_text_sha256=freeze_result.canonical_text_sha256,
            block_count=freeze_result.block_count,
            article_ready_event_id=envelope.event_id,
            article_ready_sequence=envelope.sequence,
            suitability=suitability,
            snapshot=snapshot,
        )
async def _insert_reading_record(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    client_record_id: str | None,
    source_type: InputAdapterSourceType,
    title: str | None,
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
            $4,
            $5,
            $6,
            'active',
            'processing',
            'submitted',
            1,
            $7,
            $7
        )
        """,
        record_id,
        user_id,
        client_record_id,
        _READING_RECORD_SOURCE_TYPE_BY_INPUT_SOURCE[source_type],
        title,
        language,
        created_at,
    )


async def _insert_original_input(
    conn: asyncpg.Connection,
    *,
    original_input_id: UUID,
    record_id: UUID,
    user_id: UUID,
    source_type: InputAdapterSourceType,
    text: str,
    filename: str | None,
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
            $4,
            $5,
            $6::jsonb,
            $7::jsonb,
            $8,
            $9
        )
        """,
        original_input_id,
        record_id,
        user_id,
        _ORIGINAL_INPUT_TYPE_BY_INPUT_SOURCE[source_type],
        text,
        jsonb_param(_source_ref_json(source_type=source_type, filename=filename)),
        jsonb_param(source_metadata),
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_at,
    )


def _source_ref_json(
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
) -> dict[str, Any]:
    if source_type == "pasted_text" and filename is None:
        return {}

    source_ref: dict[str, Any] = {"adapter_source_type": source_type}
    if filename is not None:
        source_ref["filename"] = filename
    return source_ref


def _build_article_ready_payload(
    *,
    record_id: UUID,
    source_type: InputAdapterSourceType,
    filename: str | None,
    title: str | None,
    freeze_result: StableDocumentFreezePersistenceResult,
    suitability: InputSuitabilityResult,
) -> dict[str, Any]:
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
        "source": "stable_ready_input",
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
