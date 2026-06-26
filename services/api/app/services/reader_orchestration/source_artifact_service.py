from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.schemas.reader_input_adapter import (
    SourceArtifactKind,
    SourceArtifactStatus,
    SourceArtifactStorageProvider,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository

# Dev-only fallback metadata for local development when env is not configured.
_DEFAULT_OSS_BUCKET = "claread-dev"
_DEFAULT_OSS_ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"
_DEFAULT_ENV_PREFIX = "dev"
_FALLBACK_FILENAME = "artifact.bin"
_CONTENT_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceArtifactRegistrationResult:
    artifact_id: UUID
    storage_provider: SourceArtifactStorageProvider
    bucket: str | None
    object_key: str
    artifact_kind: SourceArtifactKind
    content_type: str | None
    byte_size: int | None
    content_sha256: str | None
    source_filename: str
    status: SourceArtifactStatus


@dataclass(frozen=True, slots=True)
class SourceArtifactCompletionResult:
    artifact_id: UUID
    artifact_kind: SourceArtifactKind
    storage_provider: SourceArtifactStorageProvider
    bucket: str
    endpoint: str
    object_key: str
    status: SourceArtifactStatus
    content_type: str | None
    byte_size: int | None
    content_sha256: str | None
    source_filename: str
    idempotent_noop: bool


class SourceArtifactError(ValueError):
    """Raised when source artifact metadata or persistence is invalid."""


class SourceArtifactNotFoundError(SourceArtifactError):
    """Raised when a source artifact cannot be found for the current user."""


class SourceArtifactConflictError(SourceArtifactError):
    """Raised when a source artifact lifecycle transition would be invalid."""


class SourceArtifactService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        oss_bucket: str | None = None,
        oss_endpoint: str | None = None,
        object_key_prefix: str = _DEFAULT_ENV_PREFIX,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._oss_bucket = (
            oss_bucket
            or os.getenv("ALIYUN_OSS_BUCKET")
            or _DEFAULT_OSS_BUCKET
        )
        self._oss_endpoint = (
            oss_endpoint
            or os.getenv("ALIYUN_OSS_ENDPOINT")
            or _DEFAULT_OSS_ENDPOINT
        )
        self._object_key_prefix = object_key_prefix.strip("/") or _DEFAULT_ENV_PREFIX

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return self._repository.get_pool()

    def build_object_key(
        self,
        *,
        user_id: UUID,
        artifact_id: UUID,
        source_filename: str | None,
        artifact_kind: SourceArtifactKind,
    ) -> str:
        kind_value = _coerce_literal_value(
            "artifact_kind",
            artifact_kind,
            get_args(SourceArtifactKind),
        )
        safe_filename = _sanitize_filename(source_filename)
        if kind_value == "original_upload":
            return (
                f"{self._object_key_prefix}/original-inputs/"
                f"{user_id}/{artifact_id}/{safe_filename}"
            )
        return (
            f"{self._object_key_prefix}/derived-artifacts/"
            f"{kind_value}/{user_id}/{artifact_id}/{safe_filename}"
        )

    def build_oss_object_ref(
        self,
        *,
        user_id: UUID,
        artifact_id: UUID,
        source_filename: str | None,
        artifact_kind: SourceArtifactKind,
    ) -> dict[str, str]:
        return {
            "storage_provider": "oss",
            "bucket": self._oss_bucket,
            "object_key": self.build_object_key(
                user_id=user_id,
                artifact_id=artifact_id,
                source_filename=source_filename,
                artifact_kind=artifact_kind,
            ),
            "endpoint": self._oss_endpoint,
        }

    def validate_artifact_metadata(
        self,
        *,
        artifact_kind: str,
        storage_provider: str,
        status: str,
        bucket: str | None,
        object_key: str,
        endpoint: str | None,
        content_type: str | None,
        byte_size: int | None,
        content_sha256: str | None,
        source_filename: str | None,
        source_refs_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        quality_json: dict[str, Any] | None,
    ) -> dict[str, Any]:
        artifact_kind_value = _coerce_literal_value(
            "artifact_kind",
            artifact_kind,
            get_args(SourceArtifactKind),
        )
        storage_provider_value = _coerce_literal_value(
            "storage_provider",
            storage_provider,
            get_args(SourceArtifactStorageProvider),
        )
        status_value = _coerce_literal_value(
            "status",
            status,
            get_args(SourceArtifactStatus),
        )
        if byte_size is not None and byte_size < 0:
            raise SourceArtifactError("byte_size must be >= 0 when provided")
        if content_sha256 is not None and not _CONTENT_SHA256_PATTERN.fullmatch(
            content_sha256
        ):
            raise SourceArtifactError(
                "content_sha256 must be a 64-character lowercase hex string"
            )
        object_key_value = object_key.strip()
        if not object_key_value:
            raise SourceArtifactError("object_key must not be blank")

        source_filename_value = _sanitize_filename(source_filename)
        source_refs_value = _coerce_json_object(
            "source_refs_json",
            source_refs_json,
        )
        metadata_value = _coerce_json_object(
            "metadata_json",
            metadata_json,
        )
        quality_value = _coerce_json_object(
            "quality_json",
            quality_json,
        )

        bucket_value = _normalize_optional_text(bucket)
        endpoint_value = _normalize_optional_text(endpoint)
        content_type_value = _normalize_optional_text(content_type)

        if storage_provider_value == "local":
            bucket_value = None
            endpoint_value = None

        return {
            "artifact_kind": artifact_kind_value,
            "storage_provider": storage_provider_value,
            "status": status_value,
            "bucket": bucket_value,
            "object_key": object_key_value,
            "endpoint": endpoint_value,
            "content_type": content_type_value,
            "byte_size": byte_size,
            "content_sha256": content_sha256,
            "source_filename": source_filename_value,
            "source_refs_json": source_refs_value,
            "metadata_json": metadata_value,
            "quality_json": quality_value,
        }

    async def _validate_artifact_ownership(
        self,
        conn: asyncpg.Connection,
        *,
        user_id: UUID,
        reading_record_id: UUID | None,
        original_input_id: UUID | None,
    ) -> None:
        if reading_record_id is not None:
            try:
                reading_record_row = await conn.fetchrow(
                    """
                    SELECT id
                    FROM reading_records
                    WHERE id = $1
                      AND user_id = $2
                      AND deleted_at IS NULL
                    """,
                    reading_record_id,
                    user_id,
                )
            except Exception as exc:
                raise SourceArtifactError(
                    f"Failed to validate reading_record_id ownership: {exc}"
                ) from exc
            if reading_record_row is None:
                raise SourceArtifactError(
                    f"reading_record_id {reading_record_id} not found for this user"
                )

        original_input_record_id: UUID | None = None
        if original_input_id is not None:
            try:
                original_input_row = await conn.fetchrow(
                    """
                    SELECT reading_record_id
                    FROM original_inputs
                    WHERE id = $1
                      AND user_id = $2
                    """,
                    original_input_id,
                    user_id,
                )
            except Exception as exc:
                raise SourceArtifactError(
                    f"Failed to validate original_input_id ownership: {exc}"
                ) from exc
            if original_input_row is None:
                raise SourceArtifactError(
                    f"original_input_id {original_input_id} not found for this user"
                )
            original_input_record_id = original_input_row["reading_record_id"]

        if (
            reading_record_id is not None
            and original_input_record_id is not None
            and original_input_record_id != reading_record_id
        ):
            raise SourceArtifactError(
                f"original_input_id {original_input_id} does not belong to "
                f"reading_record_id {reading_record_id}"
            )

    async def register_source_artifact(
        self,
        *,
        user_id: UUID,
        artifact_kind: SourceArtifactKind,
        reading_record_id: UUID | None = None,
        original_input_id: UUID | None = None,
        artifact_id: UUID | None = None,
        storage_provider: SourceArtifactStorageProvider = "oss",
        bucket: str | None = None,
        object_key: str | None = None,
        endpoint: str | None = None,
        content_type: str | None = None,
        byte_size: int | None = None,
        content_sha256: str | None = None,
        source_filename: str | None = None,
        status: SourceArtifactStatus = "available",
        source_refs_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        quality_json: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SourceArtifactRegistrationResult:
        artifact_id_value = artifact_id or uuid4()
        created_at = now or datetime.now(UTC)
        safe_filename = _sanitize_filename(source_filename)

        storage_provider_value = _coerce_literal_value(
            "storage_provider",
            storage_provider,
            get_args(SourceArtifactStorageProvider),
        )
        artifact_kind_value = _coerce_literal_value(
            "artifact_kind",
            artifact_kind,
            get_args(SourceArtifactKind),
        )

        if object_key is None:
            if storage_provider_value == "oss":
                object_ref = self.build_oss_object_ref(
                    user_id=user_id,
                    artifact_id=artifact_id_value,
                    source_filename=safe_filename,
                    artifact_kind=artifact_kind_value,
                )
                bucket = bucket or object_ref["bucket"]
                object_key = object_ref["object_key"]
                endpoint = endpoint or object_ref["endpoint"]
            else:
                object_key = self.build_object_key(
                    user_id=user_id,
                    artifact_id=artifact_id_value,
                    source_filename=safe_filename,
                    artifact_kind=artifact_kind_value,
                )

        normalized = self.validate_artifact_metadata(
            artifact_kind=artifact_kind_value,
            storage_provider=storage_provider_value,
            status=status,
            bucket=bucket,
            object_key=object_key,
            endpoint=endpoint,
            content_type=content_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            source_filename=safe_filename,
            source_refs_json=source_refs_json,
            metadata_json=metadata_json,
            quality_json=quality_json,
        )

        pool = self._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    try:
                        await self._validate_artifact_ownership(
                            conn,
                            user_id=user_id,
                            reading_record_id=reading_record_id,
                            original_input_id=original_input_id,
                        )
                        await _insert_source_artifact(
                            conn,
                            artifact_id=artifact_id_value,
                            reading_record_id=reading_record_id,
                            original_input_id=original_input_id,
                            user_id=user_id,
                            artifact_kind=normalized["artifact_kind"],
                            storage_provider=normalized["storage_provider"],
                            bucket=normalized["bucket"],
                            object_key=normalized["object_key"],
                            endpoint=normalized["endpoint"],
                            content_type=normalized["content_type"],
                            byte_size=normalized["byte_size"],
                            content_sha256=normalized["content_sha256"],
                            source_filename=normalized["source_filename"],
                            status=normalized["status"],
                            source_refs_json=normalized["source_refs_json"],
                            metadata_json=normalized["metadata_json"],
                            quality_json=normalized["quality_json"],
                            created_at=created_at,
                        )
                    except Exception as exc:
                        raise SourceArtifactError(
                            f"Failed to persist source artifact metadata: {exc}"
                        ) from exc
        except SourceArtifactError:
            raise
        except Exception as exc:
            raise SourceArtifactError(
                f"Source artifact registration failed unexpectedly: {exc}"
            ) from exc

        return SourceArtifactRegistrationResult(
            artifact_id=artifact_id_value,
            storage_provider=normalized["storage_provider"],
            bucket=normalized["bucket"],
            object_key=normalized["object_key"],
            artifact_kind=normalized["artifact_kind"],
            content_type=normalized["content_type"],
            byte_size=normalized["byte_size"],
            content_sha256=normalized["content_sha256"],
            source_filename=normalized["source_filename"],
            status=normalized["status"],
        )

    async def complete_source_artifact_upload(
        self,
        *,
        user_id: UUID,
        artifact_id: UUID,
        content_type: str | None = None,
        byte_size: int | None = None,
        content_sha256: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        quality_json: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SourceArtifactCompletionResult:
        content_type_value = _normalize_optional_text(content_type)
        if byte_size is not None and byte_size < 0:
            raise SourceArtifactError("byte_size must be >= 0 when provided")
        if content_sha256 is not None and not _CONTENT_SHA256_PATTERN.fullmatch(
            content_sha256
        ):
            raise SourceArtifactError(
                "content_sha256 must be a 64-character lowercase hex string"
            )

        completion_metadata = _coerce_json_object("metadata_json", metadata_json)
        completion_quality = _coerce_json_object("quality_json", quality_json)
        updated_at = now or datetime.now(UTC)

        pool = self._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    try:
                        row = await conn.fetchrow(
                            """
                            SELECT
                                id,
                                artifact_kind,
                                storage_provider,
                                bucket,
                                object_key,
                                endpoint,
                                content_type,
                                byte_size,
                                content_sha256,
                                source_filename,
                                status,
                                metadata_json,
                                quality_json
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
                        raise SourceArtifactError(
                            f"Failed to load source artifact for completion: {exc}"
                        ) from exc

                    if row is None:
                        raise SourceArtifactNotFoundError("source artifact not found")

                    artifact_kind_value = _coerce_literal_value(
                        "artifact_kind",
                        row["artifact_kind"],
                        get_args(SourceArtifactKind),
                    )
                    storage_provider_value = _coerce_literal_value(
                        "storage_provider",
                        row["storage_provider"],
                        get_args(SourceArtifactStorageProvider),
                    )
                    status_value = _coerce_literal_value(
                        "status",
                        row["status"],
                        get_args(SourceArtifactStatus),
                    )

                    if artifact_kind_value != "original_upload":
                        raise SourceArtifactConflictError(
                            "only original_upload artifacts can be completed"
                        )
                    if storage_provider_value != "oss":
                        raise SourceArtifactConflictError(
                            "only oss source artifacts can be completed"
                        )

                    existing_content_type = _normalize_optional_text(row["content_type"])
                    existing_byte_size = row["byte_size"]
                    existing_content_sha256 = row["content_sha256"]
                    existing_metadata = _coerce_json_object(
                        "metadata_json",
                        row["metadata_json"],
                    )
                    existing_quality = _coerce_json_object(
                        "quality_json",
                        row["quality_json"],
                    )
                    merged_metadata = _merge_artifact_json_object(
                        "metadata_json",
                        existing=existing_metadata,
                        incoming=completion_metadata,
                    )
                    merged_quality = _merge_artifact_json_object(
                        "quality_json",
                        existing=existing_quality,
                        incoming=completion_quality,
                    )

                    if status_value == "available":
                        _ensure_completed_field_matches(
                            "content_type",
                            existing=existing_content_type,
                            provided=content_type_value,
                        )
                        _ensure_completed_field_matches(
                            "byte_size",
                            existing=existing_byte_size,
                            provided=byte_size,
                        )
                        _ensure_completed_field_matches(
                            "content_sha256",
                            existing=existing_content_sha256,
                            provided=content_sha256,
                        )
                        if merged_metadata != existing_metadata:
                            raise SourceArtifactConflictError(
                                "metadata_json does not match the completed artifact state"
                            )
                        if merged_quality != existing_quality:
                            raise SourceArtifactConflictError(
                                "quality_json does not match the completed artifact state"
                            )
                        return _build_source_artifact_completion_result(
                            artifact_id=artifact_id,
                            artifact_kind=artifact_kind_value,
                            storage_provider=storage_provider_value,
                            bucket=row["bucket"],
                            endpoint=row["endpoint"],
                            object_key=row["object_key"],
                            status=status_value,
                            content_type=existing_content_type,
                            byte_size=existing_byte_size,
                            content_sha256=existing_content_sha256,
                            source_filename=row["source_filename"],
                            default_bucket=self._oss_bucket,
                            default_endpoint=self._oss_endpoint,
                            idempotent_noop=True,
                        )

                    if status_value != "pending":
                        raise SourceArtifactConflictError(
                            f"source artifact cannot be completed from status {status_value!r}"
                        )

                    resolved_content_type = _resolve_completion_value(
                        "content_type",
                        existing=existing_content_type,
                        provided=content_type_value,
                    )
                    resolved_byte_size = _resolve_completion_value(
                        "byte_size",
                        existing=existing_byte_size,
                        provided=byte_size,
                    )
                    resolved_content_sha256 = _resolve_completion_value(
                        "content_sha256",
                        existing=existing_content_sha256,
                        provided=content_sha256,
                    )

                    try:
                        await _update_source_artifact_completion(
                            conn,
                            artifact_id=artifact_id,
                            status="available",
                            content_type=resolved_content_type,
                            byte_size=resolved_byte_size,
                            content_sha256=resolved_content_sha256,
                            metadata_json=merged_metadata,
                            quality_json=merged_quality,
                            updated_at=updated_at,
                        )
                    except Exception as exc:
                        raise SourceArtifactError(
                            f"Failed to persist source artifact upload completion: {exc}"
                        ) from exc

                    return _build_source_artifact_completion_result(
                        artifact_id=artifact_id,
                        artifact_kind=artifact_kind_value,
                        storage_provider=storage_provider_value,
                        bucket=row["bucket"],
                        endpoint=row["endpoint"],
                        object_key=row["object_key"],
                        status="available",
                        content_type=resolved_content_type,
                        byte_size=resolved_byte_size,
                        content_sha256=resolved_content_sha256,
                        source_filename=row["source_filename"],
                        default_bucket=self._oss_bucket,
                        default_endpoint=self._oss_endpoint,
                        idempotent_noop=False,
                    )
        except SourceArtifactError:
            raise
        except Exception as exc:
            raise SourceArtifactError(
                f"Source artifact upload completion failed unexpectedly: {exc}"
            ) from exc


async def _insert_source_artifact(
    conn: asyncpg.Connection,
    *,
    artifact_id: UUID,
    reading_record_id: UUID | None,
    original_input_id: UUID | None,
    user_id: UUID,
    artifact_kind: SourceArtifactKind,
    storage_provider: SourceArtifactStorageProvider,
    bucket: str | None,
    object_key: str,
    endpoint: str | None,
    content_type: str | None,
    byte_size: int | None,
    content_sha256: str | None,
    source_filename: str,
    status: SourceArtifactStatus,
    source_refs_json: dict[str, Any],
    metadata_json: dict[str, Any],
    quality_json: dict[str, Any],
    created_at: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO source_artifacts (
            id,
            reading_record_id,
            original_input_id,
            user_id,
            artifact_kind,
            storage_provider,
            bucket,
            object_key,
            endpoint,
            content_type,
            byte_size,
            content_sha256,
            source_filename,
            status,
            source_refs_json,
            metadata_json,
            quality_json,
            created_at,
            updated_at,
            deleted_at
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
            $15::jsonb,
            $16::jsonb,
            $17::jsonb,
            $18,
            $18,
            NULL
        )
        """,
        artifact_id,
        reading_record_id,
        original_input_id,
        user_id,
        artifact_kind,
        storage_provider,
        bucket,
        object_key,
        endpoint,
        content_type,
        byte_size,
        content_sha256,
        source_filename,
        status,
        jsonb_param(source_refs_json),
        jsonb_param(metadata_json),
        jsonb_param(quality_json),
        created_at,
    )


async def _update_source_artifact_completion(
    conn: asyncpg.Connection,
    *,
    artifact_id: UUID,
    status: SourceArtifactStatus,
    content_type: str | None,
    byte_size: int | None,
    content_sha256: str | None,
    metadata_json: dict[str, Any],
    quality_json: dict[str, Any],
    updated_at: datetime,
) -> None:
    await conn.execute(
        """
        UPDATE source_artifacts
        SET
            status = $2,
            content_type = $3,
            byte_size = $4,
            content_sha256 = $5,
            metadata_json = $6::jsonb,
            quality_json = $7::jsonb,
            updated_at = $8
        WHERE id = $1
        """,
        artifact_id,
        status,
        content_type,
        byte_size,
        content_sha256,
        jsonb_param(metadata_json),
        jsonb_param(quality_json),
        updated_at,
    )


def _coerce_literal_value(
    field_name: str,
    value: str,
    allowed_values: tuple[str, ...],
) -> str:
    if value not in allowed_values:
        raise SourceArtifactError(
            f"invalid {field_name} {value!r}; allowed values are {allowed_values}"
        )
    return value


def _coerce_json_object(
    field_name: str,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SourceArtifactError(f"{field_name} must be a JSON object when provided")
    return dict(value)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _resolve_completion_value(
    field_name: str,
    *,
    existing: str | int | None,
    provided: str | int | None,
) -> str | int | None:
    if provided is None:
        return existing
    if existing is None:
        return provided
    if existing != provided:
        raise SourceArtifactConflictError(
            f"{field_name} does not match the initialized artifact metadata"
        )
    return existing


def _ensure_completed_field_matches(
    field_name: str,
    *,
    existing: str | int | None,
    provided: str | int | None,
) -> None:
    if provided is not None and existing != provided:
        raise SourceArtifactConflictError(
            f"{field_name} does not match the completed artifact state"
        )


def _merge_artifact_json_object(
    field_name: str,
    *,
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in merged and merged[key] != value:
            raise SourceArtifactConflictError(
                f"{field_name}.{key} conflicts with the existing artifact value"
            )
        merged.setdefault(key, value)
    return merged


def _build_source_artifact_completion_result(
    *,
    artifact_id: UUID,
    artifact_kind: SourceArtifactKind,
    storage_provider: SourceArtifactStorageProvider,
    bucket: str | None,
    endpoint: str | None,
    object_key: str,
    status: SourceArtifactStatus,
    content_type: str | None,
    byte_size: int | None,
    content_sha256: str | None,
    source_filename: str | None,
    default_bucket: str,
    default_endpoint: str,
    idempotent_noop: bool,
) -> SourceArtifactCompletionResult:
    object_key_value = object_key.strip()
    if not object_key_value:
        raise SourceArtifactError("stored source artifact object_key must not be blank")
    bucket_value = _normalize_optional_text(bucket) or default_bucket
    endpoint_value = _normalize_optional_text(endpoint) or default_endpoint
    return SourceArtifactCompletionResult(
        artifact_id=artifact_id,
        artifact_kind=artifact_kind,
        storage_provider=storage_provider,
        bucket=bucket_value,
        endpoint=endpoint_value,
        object_key=object_key_value,
        status=status,
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        source_filename=_sanitize_filename(source_filename),
        idempotent_noop=idempotent_noop,
    )


def _sanitize_filename(source_filename: str | None) -> str:
    normalized = _normalize_optional_text(source_filename)
    if normalized is None:
        return _FALLBACK_FILENAME
    basename = normalized.replace("\\", "/").split("/")[-1].strip()
    if not basename or basename in {".", ".."}:
        return _FALLBACK_FILENAME
    safe = re.sub(r"[^\w.\-]+", "_", basename).strip("._")
    if not safe:
        return _FALLBACK_FILENAME
    return safe
