"""S2: Candidate Recovery read-only application service.

Loads the current ``(record_id, generation)`` ready candidate and
projects it into a safe typed read model for the
``GET /reader/records/{record_id}/candidate-document`` endpoint.

State decision table:
    - record not found / not owner / soft-deleted / no ready candidate
      → ``CandidateDocumentReadError(reason="not_found")`` → HTTP 404
    - ``product_state != 'needs_confirmation'`` (record has advanced):
      - ``product_state == 'readable_enhancing'`` AND
        ``readiness_state`` IN ('article_ready', 'coverage_complete')
        AND ``active_base_id`` IS NOT NULL
        → ``CandidateDocumentReadConflict(code=
        "record_state_advanced", resolution="open_reader")`` → HTTP 409
      - otherwise
        → ``CandidateDocumentReadConflict(code=
        "record_state_advanced", resolution="return_to_library")`` →
        HTTP 409
    - 2+ ready candidates → ``CandidateDocumentReadConflict(code=
      "multiple_ready_candidates", resolution="return_to_library")`` →
      HTTP 409
    - exactly 1 ready candidate + ``product_state='needs_confirmation'``
      → ``CandidateDocumentReadResult`` with typed preview → HTTP 200

The service NEVER leaks ``blocks_json`` / ``quality_json`` /
``source_refs_json`` / ``canonical_text_preview`` /
``original_input_id`` / ``source_text`` to the API boundary. All raw
fields are projected through
:mod:`candidate_preview_projection` into safe typed DTOs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.schemas.reader_orchestration import (
    ReaderCandidateDocumentConflictResolution,
    ReaderCandidateDocumentConflictCode,
    ReaderCandidateDocumentReadResponseDto,
    ReaderCandidateDocumentSourceType,
)
from app.services.reader_orchestration.candidate_preview_projection import (
    CandidatePreviewProjectionError,
    build_candidate_preview_projection,
    build_source_label,
)
from app.services.reader_orchestration.repository import (
    CandidateReadRow,
    CandidateReadRecordRow,
    ReaderOrchestrationRepository,
    get_ready_candidates_for_record,
    load_original_input_metadata_for_candidate_read,
    load_record_for_candidate_read,
)


class CandidateDocumentReadError(ValueError):
    """Raised when the read endpoint should return 404.

    All four 404 causes (record not found, not owner, soft-deleted, no
    ready candidate) are collapsed into this single error with
    ``reason="not_found"``. The route layer maps it to HTTP 404 with a
    generic message that does not leak which cause triggered it.
    """

    def __init__(self, message: str, *, reason: str = "not_found") -> None:
        super().__init__(message)
        self.reason = reason


class CandidateDocumentReadConflict(ValueError):
    """Raised when the read endpoint should return 409.

    Attributes:
        code: ``"record_state_advanced"`` or ``"multiple_ready_candidates"``.
        resolution: ``"open_reader"`` or ``"return_to_library"``.
        message: Backend-generated Chinese user-facing message.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ReaderCandidateDocumentConflictCode,
        resolution: ReaderCandidateDocumentConflictResolution,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.resolution = resolution
        self.message = message


@dataclass(frozen=True, slots=True)
class CandidateDocumentReadResult:
    """The 200-case result carrying the typed projection DTO."""

    response: ReaderCandidateDocumentReadResponseDto


# ----------------------------------------------------------------------
# source_type projection
# ----------------------------------------------------------------------

# Maps original_inputs.input_type to the controlled source_type enum
# exposed in the read model. Falls back to candidate source_refs_json.
_INPUT_TYPE_TO_SOURCE_TYPE: dict[str, ReaderCandidateDocumentSourceType] = {
    "plain_text": "plain_text",
    "markdown": "markdown",
    "file_ref": "file_ref",
    "url": "url",
    "image_ref": "image_ref",
}

# Maps candidate source_refs_json.source_type (InputAdapterSourceType)
# to the read model source_type enum.
_CANDIDATE_SOURCE_TYPE_TO_READ: dict[str, ReaderCandidateDocumentSourceType] = {
    "pasted_text": "plain_text",
    "txt_file": "file_ref",
    "markdown_file": "markdown",
    "ocr_text": "image_ref",
    "pdf_text": "file_ref",
    "url_text": "url",
}


def _coerce_json_object(raw: Any, *, field_name: str) -> dict[str, Any]:
    """Coerce a JSONB-like value into a plain dict. Degrades to {} on
    None (candidate may have empty source_refs_json)."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
        return {}
    if raw is None:
        return {}
    return {}


class CandidateDocumentReadService:
    """Application service for the candidate-document read endpoint."""

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return ReaderOrchestrationRepository().get_pool()

    async def load_candidate_document(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> CandidateDocumentReadResult:
        """Load the current ready candidate for a reading record.

        Args:
            record_id: The reading record id.
            user_id: The current user id (from AuthUserDep).

        Returns:
            A :class:`CandidateDocumentReadResult` containing the typed
            projection DTO.

        Raises:
            CandidateDocumentReadError: 404 — record not found / not
                owner / soft-deleted / no ready candidate.
            CandidateDocumentReadConflict: 409 — record state advanced
                or multiple ready candidates.
        """
        pool = self._get_pool()

        async with pool.acquire() as conn:
            # Step 1: Load record (ownership + state pre-checks).
            record_row = await load_record_for_candidate_read(
                conn,
                record_id=record_id,
                user_id=user_id,
            )

            # 404: record does not exist at all.
            if record_row is None:
                raise CandidateDocumentReadError(
                    "未找到待确认内容，可能已在其他设备处理。"
                )

            # 404: not owner OR soft-deleted (collapsed, no leak).
            if record_row.user_id != user_id or record_row.deleted_at is not None:
                raise CandidateDocumentReadError(
                    "未找到待确认内容，可能已在其他设备处理。"
                )

            # 409: record state has advanced past needs_confirmation.
            if record_row.product_state != "needs_confirmation":
                self._raise_state_advanced_conflict(record_row)

            # Step 2: Query ready candidates for current generation.
            # Returns a list — never LIMIT 1.
            candidates = await get_ready_candidates_for_record(
                conn,
                record_id=record_id,
                user_id=user_id,
                generation=record_row.generation,
            )

            # 404: no ready candidate (confirmed or superseded).
            if len(candidates) == 0:
                raise CandidateDocumentReadError(
                    "未找到待确认内容，可能已在其他设备处理。"
                )

            # 409: multiple ready candidates — never silently select.
            if len(candidates) > 1:
                raise CandidateDocumentReadConflict(
                    "该内容存在多个待确认版本，请联系支持或重新提交。",
                    code="multiple_ready_candidates",
                    resolution="return_to_library",
                )

            # Step 3: Exactly one ready candidate — project to typed DTO.
            candidate = candidates[0]
            return await self._project_candidate(
                candidate=candidate,
                record_row=record_row,
                conn=conn,
            )

    def _raise_state_advanced_conflict(
        self,
        record_row: CandidateReadRecordRow,
    ) -> None:
        """Decide 409 resolution: open_reader vs return_to_library.

        Caller has already verified ``product_state !=
        'needs_confirmation'``. The decision is:

        - open_reader: record has a readable advanced state —
          ``product_state == 'readable_enhancing'`` AND
          ``active_base_id`` IS NOT NULL AND ``readiness_state`` IN
          (``'article_ready'``, ``'coverage_complete'``). The frontend
          should redirect to Reader because the user can already open
          the content.
        - return_to_library: any other advanced state (failed /
          action_required / readable_enhancing without base /
          article_ready without base / coverage_complete without base /
          processing / deleted / etc.). The frontend should return to
          Library.
        """
        is_readable = (
            record_row.product_state == "readable_enhancing"
            and record_row.readiness_state
            in ("article_ready", "coverage_complete")
            and record_row.active_base_id is not None
        )
        if is_readable:
            raise CandidateDocumentReadConflict(
                "该内容已确认，正在为你打开阅读。",
                code="record_state_advanced",
                resolution="open_reader",
            )
        raise CandidateDocumentReadConflict(
            "该内容已处理，请返回列表查看。",
            code="record_state_advanced",
            resolution="return_to_library",
        )

    async def _project_candidate(
        self,
        *,
        candidate: CandidateReadRow,
        record_row: CandidateReadRecordRow,
        conn: asyncpg.Connection,
    ) -> CandidateDocumentReadResult:
        """Project a single ready candidate into the typed read DTO."""
        source_refs = _coerce_json_object(
            candidate.source_refs_json,
            field_name="source_refs_json",
        )

        # Resolve source_type + filename + original_input_id.
        filename = source_refs.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            filename = None

        original_input_id_str = source_refs.get("original_input_id")
        original_input_id: UUID | None = None
        if isinstance(original_input_id_str, str):
            try:
                original_input_id = UUID(original_input_id_str)
            except ValueError:
                original_input_id = None

        # Load original_inputs for input_type + metadata (never source_text).
        source_type: ReaderCandidateDocumentSourceType
        if original_input_id is not None:
            oi_result = await load_original_input_metadata_for_candidate_read(
                conn,
                original_input_id=original_input_id,
                reading_record_id=record_row.record_id,
                user_id=record_row.user_id,
            )
            if oi_result is not None:
                input_type, metadata_json = oi_result
                source_type = _INPUT_TYPE_TO_SOURCE_TYPE.get(
                    input_type, "file_ref"
                )
                # Fallback filename from metadata_json if source_refs lacked it.
                if filename is None:
                    meta_filename = metadata_json.get("filename") if isinstance(
                        metadata_json, Mapping
                    ) else None
                    if isinstance(meta_filename, str) and meta_filename.strip():
                        filename = meta_filename.strip()
            else:
                # original_input not found — degrade to candidate source_refs.
                source_type = self._source_type_from_candidate_refs(source_refs)
        else:
            source_type = self._source_type_from_candidate_refs(source_refs)

        # Build typed preview projection (never leaks raw JSON).
        try:
            preview = build_candidate_preview_projection(
                blocks_json=candidate.blocks_json,
                quality_json=candidate.quality_json,
                canonical_text_preview=candidate.canonical_text_preview,
            )
        except CandidatePreviewProjectionError as exc:
            # Candidate has invalid blocks_json — this should not happen
            # for a persisted candidate. Treat as 500 (route layer
            # catches all other exceptions).
            raise RuntimeError(
                f"Failed to project candidate preview for candidate "
                f"{candidate.candidate_document_id}: {exc}"
            ) from exc

        source_label = build_source_label(
            source_type=source_type,
            filename=filename,
        )

        response = ReaderCandidateDocumentReadResponseDto(
            record_id=str(candidate.reading_record_id),
            candidate_document_id=str(candidate.candidate_document_id),
            record_generation=record_row.generation,
            status="ready",
            title=candidate.title,
            preview=preview,
            source_type=source_type,
            filename=filename,
            source_label=source_label,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )

        return CandidateDocumentReadResult(response=response)

    def _source_type_from_candidate_refs(
        self,
        source_refs: dict[str, Any],
    ) -> ReaderCandidateDocumentSourceType:
        """Fallback: derive source_type from candidate source_refs_json."""
        raw_source_type = source_refs.get("source_type")
        if isinstance(raw_source_type, str):
            return _CANDIDATE_SOURCE_TYPE_TO_READ.get(raw_source_type, "file_ref")
        return "file_ref"
