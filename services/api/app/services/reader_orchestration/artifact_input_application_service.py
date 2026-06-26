from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository

ArtifactBoundReadingSourceType = Literal["file", "pdf", "image"]
ArtifactBoundOriginalInputType = Literal["file_ref", "image_ref"]


class ArtifactInputApplicationError(ValueError):
    """Raised when an available source artifact cannot be submitted as input."""


class ArtifactInputApplicationNotFoundError(ArtifactInputApplicationError):
    """Raised when the source artifact is missing or not owned by the user."""


class ArtifactInputApplicationConflictError(ArtifactInputApplicationError):
    """Raised when the source artifact cannot transition into a bound input."""


@dataclass(frozen=True, slots=True)
class ArtifactInputApplicationResult:
    reading_record_id: UUID
    original_input_id: UUID
    artifact_id: UUID
    record_generation: int
    source_type: ArtifactBoundReadingSourceType
    input_type: ArtifactBoundOriginalInputType
    product_state: Literal["processing"]
    readiness_state: Literal["submitted"]
    title: str
    language: str | None
    bucket: str
    endpoint: str
    object_key: str
    content_type: str | None
    byte_size: int | None
    content_sha256: str | None
    source_filename: str


class ArtifactInputApplicationService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return self._repository.get_pool()

    async def submit_available_artifact_as_input(
        self,
        *,
        user_id: UUID,
        artifact_id: UUID,
        title: str | None = None,
        language: str | None = None,
        client_record_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ArtifactInputApplicationResult:
        created_at = now or datetime.now(UTC)
        title_value = _normalize_optional_text(title)
        language_value = _normalize_optional_text(language)
        client_record_id_value = _normalize_optional_text(client_record_id)
        source_metadata_value = _coerce_json_object("source_metadata", source_metadata)
        original_input_metadata = _merge_json_object_strict(
            "source_metadata",
            existing=source_metadata_value,
            incoming={"source_artifact_status": "available"},
        )
        pool = self._get_pool()

        reading_record_id: UUID | None = None
        original_input_id: UUID | None = None
        result: ArtifactInputApplicationResult | None = None

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    artifact_row = await _load_available_source_artifact_for_binding(
                        conn,
                        artifact_id=artifact_id,
                        user_id=user_id,
                    )

                    source_type, input_type = _source_type_and_input_type_from_content_type(
                        artifact_row["content_type"]
                    )
                    source_filename_value = _required_text(
                        "source_filename",
                        artifact_row["source_filename"],
                    )
                    bucket_value = _required_text("bucket", artifact_row["bucket"])
                    endpoint_value = _required_text("endpoint", artifact_row["endpoint"])
                    object_key_value = _required_text(
                        "object_key",
                        artifact_row["object_key"],
                    )
                    title_resolved = title_value or source_filename_value

                    source_ref_json = _artifact_source_ref_json(
                        artifact_id=artifact_id,
                        artifact_kind=artifact_row["artifact_kind"],
                        storage_provider=artifact_row["storage_provider"],
                        bucket=bucket_value,
                        endpoint=endpoint_value,
                        object_key=object_key_value,
                        content_type=artifact_row["content_type"],
                        byte_size=artifact_row["byte_size"],
                        content_sha256=artifact_row["content_sha256"],
                        source_filename=source_filename_value,
                    )
                    original_input_content_sha256 = (
                        artifact_row["content_sha256"]
                        or _deterministic_source_ref_content_sha256(source_ref_json)
                    )

                    reading_record_id = uuid4()
                    original_input_id = uuid4()

                    try:
                        await _insert_reading_record(
                            conn,
                            record_id=reading_record_id,
                            user_id=user_id,
                            client_record_id=client_record_id_value,
                            source_type=source_type,
                            title=title_resolved,
                            language=language_value,
                            created_at=created_at,
                        )
                        await _insert_original_input(
                            conn,
                            original_input_id=original_input_id,
                            reading_record_id=reading_record_id,
                            user_id=user_id,
                            input_type=input_type,
                            source_ref_json=source_ref_json,
                            metadata_json=original_input_metadata,
                            content_sha256=original_input_content_sha256,
                            created_at=created_at,
                        )
                        await _bind_source_artifact_to_input(
                            conn,
                            artifact_id=artifact_id,
                            reading_record_id=reading_record_id,
                            original_input_id=original_input_id,
                            updated_at=created_at,
                        )
                    except Exception as exc:
                        raise ArtifactInputApplicationError(
                            f"Failed to persist the artifact-backed input envelope: {exc}"
                        ) from exc

                    result = ArtifactInputApplicationResult(
                        reading_record_id=reading_record_id,
                        original_input_id=original_input_id,
                        artifact_id=artifact_id,
                        record_generation=1,
                        source_type=source_type,
                        input_type=input_type,
                        product_state="processing",
                        readiness_state="submitted",
                        title=title_resolved,
                        language=language_value,
                        bucket=bucket_value,
                        endpoint=endpoint_value,
                        object_key=object_key_value,
                        content_type=_normalize_optional_text(artifact_row["content_type"]),
                        byte_size=artifact_row["byte_size"],
                        content_sha256=artifact_row["content_sha256"],
                        source_filename=source_filename_value,
                    )
        except ArtifactInputApplicationError:
            raise
        except Exception as exc:
            raise ArtifactInputApplicationError(
                f"Artifact-backed input submission failed unexpectedly: {exc}"
            ) from exc

        assert result is not None
        return result


async def _load_available_source_artifact_for_binding(
    conn: asyncpg.Connection,
    *,
    artifact_id: UUID,
    user_id: UUID,
) -> Mapping[str, Any]:
    try:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                artifact_kind,
                storage_provider,
                bucket,
                endpoint,
                object_key,
                content_type,
                byte_size,
                content_sha256,
                source_filename,
                status,
                reading_record_id,
                original_input_id
            FROM source_artifacts
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
            FOR UPDATE
            """,
            artifact_id,
            user_id,
        )
    except Exception as exc:
        raise ArtifactInputApplicationError(
            f"Failed to load source artifact for input submission: {exc}"
        ) from exc

    if row is None:
        raise ArtifactInputApplicationNotFoundError("source artifact not found")
    if row["status"] != "available":
        raise ArtifactInputApplicationConflictError(
            f"source artifact status must be 'available' before input submission; got {row['status']!r}"
        )
    if row["artifact_kind"] != "original_upload":
        raise ArtifactInputApplicationConflictError(
            f"source artifact kind must be 'original_upload'; got {row['artifact_kind']!r}"
        )
    if row["storage_provider"] != "oss":
        raise ArtifactInputApplicationConflictError(
            f"source artifact storage_provider must be 'oss'; got {row['storage_provider']!r}"
        )
    if row["reading_record_id"] is not None or row["original_input_id"] is not None:
        raise ArtifactInputApplicationConflictError(
            "source artifact is already bound to a reading_record/original_input"
        )
    return row


async def _insert_reading_record(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
    client_record_id: str | None,
    source_type: ArtifactBoundReadingSourceType,
    title: str,
    language: str | None,
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
        source_type,
        title,
        language,
        created_at,
    )


async def _insert_original_input(
    conn: asyncpg.Connection,
    *,
    original_input_id: UUID,
    reading_record_id: UUID,
    user_id: UUID,
    input_type: ArtifactBoundOriginalInputType,
    source_ref_json: dict[str, Any],
    metadata_json: dict[str, Any],
    content_sha256: str,
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
            NULL,
            $5::jsonb,
            $6::jsonb,
            $7,
            $8
        )
        """,
        original_input_id,
        reading_record_id,
        user_id,
        input_type,
        jsonb_param(source_ref_json),
        jsonb_param(metadata_json),
        content_sha256,
        created_at,
    )


async def _bind_source_artifact_to_input(
    conn: asyncpg.Connection,
    *,
    artifact_id: UUID,
    reading_record_id: UUID,
    original_input_id: UUID,
    updated_at: datetime,
) -> None:
    await conn.execute(
        """
        UPDATE source_artifacts
        SET
            reading_record_id = $2,
            original_input_id = $3,
            updated_at = $4
        WHERE id = $1
        """,
        artifact_id,
        reading_record_id,
        original_input_id,
        updated_at,
    )


def _source_type_and_input_type_from_content_type(
    content_type: str | None,
) -> tuple[ArtifactBoundReadingSourceType, ArtifactBoundOriginalInputType]:
    normalized = _normalize_content_type(content_type)
    if normalized == "application/pdf":
        return "pdf", "file_ref"
    if normalized is not None and normalized.startswith("image/"):
        return "image", "image_ref"
    if normalized in {"text/markdown", "text/x-markdown"}:
        return "file", "file_ref"
    return "file", "file_ref"


def _artifact_source_ref_json(
    *,
    artifact_id: UUID,
    artifact_kind: str,
    storage_provider: str,
    bucket: str,
    endpoint: str,
    object_key: str,
    content_type: str | None,
    byte_size: int | None,
    content_sha256: str | None,
    source_filename: str,
) -> dict[str, Any]:
    return {
        "artifact_id": str(artifact_id),
        "storage_provider": storage_provider,
        "bucket": bucket,
        "endpoint": endpoint,
        "object_key": object_key,
        "artifact_kind": artifact_kind,
        "content_type": _normalize_optional_text(content_type),
        "byte_size": byte_size,
        "content_sha256": content_sha256,
        "source_filename": source_filename,
    }


def _deterministic_source_ref_content_sha256(source_ref_json: dict[str, Any]) -> str:
    canonical_json = json.dumps(
        source_ref_json,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _coerce_json_object(
    field_name: str,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ArtifactInputApplicationError(
            f"{field_name} must be a JSON object when provided"
        )
    return dict(value)


def _merge_json_object_strict(
    field_name: str,
    *,
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in merged and merged[key] != value:
            raise ArtifactInputApplicationConflictError(
                f"{field_name}.{key} conflicts with the existing value"
            )
        merged.setdefault(key, value)
    return merged


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_content_type(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return normalized.split(";", 1)[0].strip().lower() or None


def _required_text(field_name: str, value: str | None) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        raise ArtifactInputApplicationError(
            f"source artifact {field_name} must not be blank"
        )
    return normalized
