"""R8 — Owner-scoped source artifact preview delivery (source preview).

Security contract (frozen in apps/web/docs/design/
surface-read-intake-content-check.md §13.2):

- preview is a **short-lived, read-only GET** presigned URL produced by the
  existing presigner (never a reuse of the upload PUT URL);
- only the owner's non-deleted artifact in ``available`` status with an
  allowed preview MIME (PDF / images) may be previewed — every other state
  collapses to denial (404) without revealing the reason;
- the response exposes NO independent storage-coordinate FIELDS
  (``object_key`` / bucket / endpoint / credentials). The presigned URL
  value itself is a **sensitive temporary delivery value** — it addresses
  the object (bucket host + key path are inherent to the OSS presigned-URL
  model, same as the documented PUT contract) and must be treated as a
  credential-equivalent secret;
- **frozen Web consumption contract**: the raw presigned URL must NOT be
  written directly into ordinary DOM. Web delivery must go through a
  controlled same-origin BFF (proxy / redirect), a safe Blob URL obtained
  via a same-origin fetch, or an equivalent controlled delivery
  mechanism — never direct embedding of the raw URL in markup/attributes;
- ``preview_url`` is ``None`` when the presigner cannot produce a URL
  (fail closed — Candidate editing and confirmation flows are never
  blocked by preview availability),
- the URL is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.config.settings import get_settings
from app.database.connection import DB_POOL
from app.services.reader_orchestration.oss_presigner import (
    PresignedUpload,
    Presigner,
    build_default_presigner,
)

# Allowed preview media types: PDF plus the OCR image set.
PREVIEW_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/tiff",
    }
)


def evaluate_preview_gate(
    *,
    owner_user_id: Any,
    artifact_user_id: Any,
    status: str,
    content_type: str | None,
    storage_provider: str,
    deleted_at: datetime | None,
) -> bool:
    """Fail-closed preview gate (pure policy, unit-testable).

    Denies unless: owner match, not soft-deleted, ``available`` status,
    OSS storage, and an allowed preview MIME.
    """
    if artifact_user_id != owner_user_id:
        return False
    if deleted_at is not None:
        return False
    if status != "available":
        return False
    if storage_provider != "oss":
        return False
    if content_type not in PREVIEW_CONTENT_TYPES:
        return False
    return True


class SourceArtifactPreviewNotFoundError(ValueError):
    """404 collapse: not found / not owner / deleted / not available /
    unsupported MIME (no state leakage)."""


@dataclass(frozen=True, slots=True)
class SourceArtifactPreviewResult:
    preview_url: str | None
    expires_at: datetime | None
    content_type: str | None
    degraded: bool


class SourceArtifactPreviewService:
    """Creates short-lived read-only preview URLs for source artifacts."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        presigner: Presigner | None = None,
    ) -> None:
        self._pool = pool
        self._presigner = presigner

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        if DB_POOL is None:
            raise RuntimeError("database pool is not initialized")
        return DB_POOL

    async def create_record_preview(
        self,
        *,
        record_id: UUID,
        expected_generation: int,
        user_id: UUID,
    ) -> SourceArtifactPreviewResult:
        """Resolve the persisted original upload lineage, then preview it."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                artifact_id = await conn.fetchval(
                    """
                    SELECT artifact.id
                    FROM reading_records AS record
                    JOIN confirmed_source_documents AS confirmed
                      ON confirmed.reading_record_id = record.id
                     AND confirmed.user_id = record.user_id
                     AND confirmed.record_generation = record.generation
                     AND confirmed.status = 'draft'
                    JOIN original_inputs AS original_input
                      ON original_input.id = confirmed.original_input_id
                     AND original_input.reading_record_id = record.id
                     AND original_input.user_id = record.user_id
                    JOIN source_artifacts AS artifact
                      ON artifact.id::text =
                         original_input.source_ref_json ->> 'artifact_id'
                     AND artifact.reading_record_id = record.id
                     AND artifact.original_input_id = original_input.id
                     AND artifact.user_id = record.user_id
                     AND artifact.artifact_kind = 'original_upload'
                    WHERE record.id = $1
                      AND record.user_id = $2
                      AND record.deleted_at IS NULL
                      AND record.lifecycle_status = 'active'
                      AND record.generation = $3
                      AND record.product_state IN (
                          'processing',
                          'needs_confirmation',
                          'action_required'
                      )
                    """,
                    record_id,
                    user_id,
                    expected_generation,
                )
        if artifact_id is None:
            raise SourceArtifactPreviewNotFoundError("source artifact not found")
        return await self.create_preview(
            artifact_id=artifact_id,
            user_id=user_id,
        )

    async def create_preview(
        self,
        *,
        artifact_id: UUID,
        user_id: UUID,
    ) -> SourceArtifactPreviewResult:
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT user_id, status, content_type, storage_provider,
                           bucket, endpoint, object_key, deleted_at
                    FROM source_artifacts
                    WHERE id = $1
                    """,
                    artifact_id,
                )
                if row is None:
                    raise SourceArtifactPreviewNotFoundError("artifact not found")
                if not evaluate_preview_gate(
                    owner_user_id=user_id,
                    artifact_user_id=row["user_id"],
                    status=str(row["status"]),
                    content_type=row["content_type"],
                    storage_provider=str(row["storage_provider"]),
                    deleted_at=row["deleted_at"],
                ):
                    # Collapse: never reveal whether the artifact exists or
                    # why it was denied.
                    raise SourceArtifactPreviewNotFoundError("preview not permitted")
                bucket = str(row["bucket"] or "")
                endpoint = str(row["endpoint"] or "")
                object_key = str(row["object_key"])
                content_type = row["content_type"]

        presigner = self._presigner or build_default_presigner()
        presigned: PresignedUpload | None = None
        try:
            presigned = presigner.presign_get_object(
                bucket=bucket,
                endpoint=endpoint,
                object_key=object_key,
                expires_in=timedelta(
                    seconds=get_settings().aliyun_oss_presign_expires_seconds,
                ),
            )
        except Exception:
            # Fail closed: no URL is returned and nothing is logged with
            # the URL; preview unavailability never blocks Candidate flows.
            presigned = None

        if presigned is None:
            return SourceArtifactPreviewResult(
                preview_url=None,
                expires_at=None,
                content_type=content_type,
                degraded=True,
            )
        return SourceArtifactPreviewResult(
            preview_url=presigned.url,
            expires_at=presigned.expires_at,
            content_type=content_type,
            degraded=False,
        )
