"""D6-I3N Extracted Artifact Text → Stable/Candidate Materialization.

Takes an **existing** reading_record + original_input (created by
``ArtifactInputApplicationService.submit_input``) and materializes the
confirmed-source text persisted by ``ArtifactExtractionWorkerService`` into
either:

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
from app.schemas.reader_documents import ConfirmedSourceDocument
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
)
from app.services.reader_orchestration.article_rag_auto_ensure_service import (
    ArticleRagAutoEnsureService,
    build_default_auto_ensure_service,
)
from app.services.reader_orchestration._text import (
    resolve_default_reader_language,
)
from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    _build_candidate_blocks,
    _candidate_quality_json,
    _candidate_source_refs_json,
    _canonical_text_preview,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    freeze_confirmed_source,
    lock_confirmed_source_for_update,
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
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownParseResult,
    MarkdownSourceParser,
)
from app.services.reader_orchestration.repository import (
    CandidateWriteLockError,
    ReaderOrchestrationRepository,
    lock_record_for_candidate_write,
    supersede_ready_candidates_for_locked_record,
)

MATERIALIZATION_SOURCE = "extracted_artifact_materialization"

# content_type → InputAdapterSourceType mapping for text artifacts.
# application/octet-stream is resolved by source_filename extension.
_MARKDOWN_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/markdown",
    "text/x-markdown",
})
_PLAIN_CONTENT_TYPES: frozenset[str] = frozenset({"text/plain"})

# A4 — 解析结果共享: single module-level parser used to pre-parse the
# extracted artifact source text once and share the result across the
# suitability gate, the normalizer, and the candidate block builder.
# Reusing one parser instance avoids re-instantiating per request; the
# parser is stateless and deterministic.
_MARKDOWN_PARSER = MarkdownSourceParser()


def _normalize_source_text(text: str) -> str:
    """Mirror the gate's text normalization so the pre-parse runs on the
    same text the downstream pipeline will see."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class ExtractedArtifactMaterializationError(ValueError):
    """Typed error for materialization failures (validation, fence, persistence).

    ``reason_code`` is a stable identifier the worker switches on to map
    failures to ``superseded`` vs ``failed_terminal`` job transitions.
    Unset / ``"materialize_failed"`` defaults to ``failed_terminal``; the
    three ``superseded`` reasons are:
    ``stale_generation``, ``active_base_already_exists``,
    ``materialization_already_run``.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code or "materialize_failed"


# reason_codes that the materialization worker maps to ``superseded``:
# the record has advanced past the materialization point, so the job is
# no longer relevant. All other reason_codes map to ``failed_terminal``.
MATERIALIZATION_SUPERSEDED_REASONS: frozenset[str] = frozenset({
    "stale_generation",
    "active_base_already_exists",
    "materialization_already_run",
})


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
        auto_ensure_service: ArticleRagAutoEnsureService | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._auto_ensure_service = auto_ensure_service

    def _get_auto_ensure_service(self) -> ArticleRagAutoEnsureService:
        if self._auto_ensure_service is None:
            self._auto_ensure_service = build_default_auto_ensure_service()
        return self._auto_ensure_service

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
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                return await self.materialize_extracted_artifact_in_transaction(
                    conn,
                    reading_record_id=reading_record_id,
                    original_input_id=original_input_id,
                    source_artifact_id=source_artifact_id,
                    user_id=user_id,
                    expected_generation=expected_generation,
                )

    async def materialize_extracted_artifact_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        original_input_id: UUID,
        source_artifact_id: UUID,
        user_id: UUID,
        expected_generation: int,
    ) -> MaterializationResult:
        """Caller-managed transaction variant of materialize_extracted_artifact.

        The caller MUST already hold an open transaction on ``conn``. This
        method runs the full validate → suitability → freeze/persist pipeline
        within that transaction. Any failure raises
        ``ExtractedArtifactMaterializationError`` (or the original exception)
        and the caller's transaction rolls back.

        This is the entry point for the materialization worker, which needs
        to run materialization + job transition in the same transaction to
        avoid state drift (materialization succeeds but job transition fails).

        Fails closed with ``ExtractedArtifactMaterializationError`` if
        ``conn`` is not in an active transaction, mirroring
        ``persist_stable_document_freeze_plan`` and
        ``confirm_candidate_document``. This prevents the multi-step
        materialization pipeline from partially committing under autocommit.
        """
        if not conn.is_in_transaction():
            raise ExtractedArtifactMaterializationError(
                "materialize_extracted_artifact_in_transaction must be "
                "called within an active transaction. Refusing to execute "
                "outside a transaction to prevent half-materialized state.",
                reason_code="caller_transaction_required",
            )

        now = datetime.now(UTC)

        # 1. Lock reading_records via the shared candidate-write helper.
        #    This acquires SELECT ... FOR UPDATE on the parent
        #    reading_records row (filtered by id + user_id + deleted_at IS
        #    NULL) and validates expected_generation. The lock serializes
        #    all concurrent materialization / candidate-creation attempts
        #    for the same record, preserving the original record-validation
        #    serializability.
        #
        #    This function does NOT supersede candidates — that happens
        #    ONLY in the candidate_document_required branch
        #    (_materialize_candidate), immediately before the new
        #    candidate INSERT. stable_document_ready and rejected branches
        #    never touch candidate_reading_documents.
        try:
            await lock_record_for_candidate_write(
                conn,
                record_id=reading_record_id,
                user_id=user_id,
                expected_generation=expected_generation,
            )
        except CandidateWriteLockError as exc:
            if exc.reason_code == "generation_mismatch":
                raise ExtractedArtifactMaterializationError(
                    str(exc),
                    reason_code="stale_generation",
                ) from exc
            if exc.reason_code == "transaction_required":
                raise ExtractedArtifactMaterializationError(
                    str(exc),
                    reason_code="caller_transaction_required",
                ) from exc
            raise ExtractedArtifactMaterializationError(
                str(exc),
                reason_code="record_not_found",
            ) from exc

        # 2. Fetch additional fields for materialization-specific
        #    validations. The row is already locked by the helper (same
        #    transaction, same connection), so a plain SELECT without
        #    FOR UPDATE is safe and correct.
        record_row = await conn.fetchrow(
            """
            SELECT active_base_id, lifecycle_status, product_state,
                   readiness_state, language
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
            """,
            reading_record_id,
            user_id,
        )
        if record_row is None:
            raise ExtractedArtifactMaterializationError(
                f"reading_record {reading_record_id} disappeared after "
                f"lock; this should never happen",
                reason_code="record_not_found",
            )
        if record_row["lifecycle_status"] != "active":
            raise ExtractedArtifactMaterializationError(
                f"reading_record {reading_record_id} lifecycle_status "
                f"is {record_row['lifecycle_status']!r}, expected 'active'",
                reason_code="record_not_active",
            )
        if record_row["active_base_id"] is not None:
            raise ExtractedArtifactMaterializationError(
                f"reading_record {reading_record_id} already has "
                f"active_base_id {record_row['active_base_id']}; "
                f"materialization must run before any base exists",
                reason_code="active_base_already_exists",
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
                f"materialization has already run or state was advanced",
                reason_code="materialization_already_run",
            )

        # R7-1: reading_records.language is the authoritative language
        # source for the sentence-segmentation policy (never guessed
        # from the body text). Missing/blank values use the Reader-wide
        # default rule, matching the article-ready / stable-ready
        # submission paths.
        language_value = resolve_default_reader_language(record_row["language"])

        # 3. Lock and validate the SPECIFIC original_input (lineage only).
        #    L2: 正文不再从 original_inputs.source_text 读取（该列对新
        #    输入恒 NULL）；正文唯一载体是 confirmed_source_documents，
        #    由 step 4 之后的 source 行锁提供（保持 input → artifact →
        #    source 的既有 fail-closed 校验顺序）。
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
                f"reading_record {reading_record_id} / user {user_id}",
                reason_code="original_input_not_found",
            )

        # 4. Lock and validate the SPECIFIC source_artifact
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
                f"{original_input_id} / user {user_id}",
                reason_code="source_artifact_not_found",
            )
        if artifact_row["status"] != "available":
            raise ExtractedArtifactMaterializationError(
                f"source_artifact {artifact_row['id']} status is "
                f"{artifact_row['status']!r}, expected 'available'",
                reason_code="artifact_not_available",
            )
        if artifact_row["artifact_kind"] != "original_upload":
            raise ExtractedArtifactMaterializationError(
                f"source_artifact {artifact_row['id']} artifact_kind is "
                f"{artifact_row['artifact_kind']!r}, expected "
                f"'original_upload'",
                reason_code="artifact_kind_wrong",
            )
        if artifact_row["storage_provider"] != "oss":
            raise ExtractedArtifactMaterializationError(
                f"source_artifact {artifact_row['id']} storage_provider "
                f"is {artifact_row['storage_provider']!r}, expected 'oss'",
                reason_code="storage_provider_wrong",
            )

        # 4b. L2: lock the confirmed-source row 并读取正文（锁顺序
        #     record → source → candidate，设计文档 §3.4；校验顺序保持
        #     既有 input → artifact → source）。空值 fail-closed 语义
        #     平移自原 original_inputs.source_text 检查。
        confirmed_source = await lock_confirmed_source_for_update(
            conn,
            record_id=reading_record_id,
            user_id=user_id,
            generation=expected_generation,
        )
        if confirmed_source is None:
            raise ExtractedArtifactMaterializationError(
                f"confirmed_source_documents row not found for "
                f"reading_record {reading_record_id} generation "
                f"{expected_generation}; source text is empty — "
                f"extraction must complete before materialization",
                reason_code="source_text_empty",
            )
        source_text = confirmed_source.markdown_text
        if not source_text.strip():
            raise ExtractedArtifactMaterializationError(
                f"confirmed_source_documents {confirmed_source.id} "
                f"markdown_text is empty; extraction must complete "
                f"before materialization",
                reason_code="source_text_empty",
            )

        # 5. Derive source_type and build suitability request
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
        # A4 — 解析结果共享: parse once on the normalized text and
        # thread the result through the gate + downstream stable /
        # candidate path. The gate, normalizer (stable path), and
        # ``_build_candidate_blocks`` (candidate path) all consume this
        # single parse, eliminating 2 redundant parses per request.
        # The parser is deterministic and the source text is immutable
        # within the transaction, so sharing is safe.
        preparsed = _MARKDOWN_PARSER.parse(_normalize_source_text(source_text))
        suitability = evaluate_input_suitability(
            suitability_request, preparsed=preparsed
        )

        # 6. Branch on outcome
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
                language=language_value,
                suitability=suitability,
                preparsed=preparsed,
                confirmed_source=confirmed_source,
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
                preparsed=preparsed,
                confirmed_source=confirmed_source,
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
        language: str,
        suitability: InputSuitabilityResult,
        preparsed: MarkdownParseResult | None = None,
        confirmed_source: ConfirmedSourceDocument,
        now: datetime,
    ) -> MaterializationResult:
        # Normalize → freeze plan → persist → set_active_base → publish event
        request = InputSuitabilityRequest(
            source_type=source_type,
            text=source_text,
            filename=filename,
            source_metadata=source_metadata,
        )
        # A4 — 解析结果共享: reuse the parse result produced by the
        # caller; the normalizer MUST NOT re-parse.
        normalized = normalize_input_document(request, preparsed=preparsed)

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
            segmenter_version=AUTO_SEGMENTER_POLICY,
            language=language,
            user_id=user_id,
            now=now,
        )
        if freeze_result.base_id is None:
            raise ExtractedArtifactMaterializationError(
                f"freeze persistence returned null base_id for record "
                f"{record_id}",
                reason_code="freeze_persistence_failed",
            )

        # L2 插入点 B 镜像：source 冻结与 Stable Document 在同一事务
        # 原子提交（期望 UPDATE 1，与 confirm/stable-ready 路径同一
        # freeze 语义）。
        await freeze_confirmed_source(
            conn,
            source_document_id=UUID(confirmed_source.id),
            now=now,
        )

        await self._repository.set_active_base_and_mark_article_ready(
            conn,
            record_id=record_id,
            base_id=freeze_result.base_id,
            expected_generation=generation,
            updated_at=now,
        )

        # D6-I4V: Article RAG index auto-ensure (fail-soft).
        rag_result = await self._get_auto_ensure_service().ensure_in_transaction(
            conn,
            reading_record_id=record_id,
            user_id=user_id,
            expected_generation=generation,
            now=now,
        )

        payload = _build_article_ready_payload(
            record_id=record_id,
            source_type=source_type,
            filename=filename,
            title=normalized.title,
            freeze_result=freeze_result,
            suitability=suitability,
        )
        payload["article_rag_index"] = {
            "status": rag_result.status,
            "reason_code": rag_result.reason_code,
        }
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
        preparsed: MarkdownParseResult | None = None,
        confirmed_source: ConfirmedSourceDocument,
        now: datetime,
    ) -> MaterializationResult:
        # A4 — 解析结果共享: reuse the parse result produced by the
        # caller; ``_build_candidate_blocks`` MUST NOT re-parse the
        # markdown source.
        blocks, title = _build_candidate_blocks(
            source_type=source_type,
            text=source_text,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=input_id,
            preparsed=preparsed,
        )
        candidate_document_id = uuid4()
        preview = _canonical_text_preview(suitability=suitability, blocks=blocks)
        # L2 — candidate source_refs_json 三 key（设计文档 §6）：引用
        # 当前 source revision/hash，confirm 插入点 A 据此校验。
        source_refs = _candidate_source_refs_json(
            source_type=source_type,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=input_id,
            confirmed_source=confirmed_source,
        )
        quality = _candidate_quality_json(suitability=suitability)

        # Supersede any existing ready candidates for this (record_id,
        # generation) immediately before inserting the new ready
        # candidate. The lock was acquired at the start of
        # materialize_extracted_artifact_in_transaction and is still held
        # (same transaction, same connection), so no concurrent writer can
        # insert another ready candidate between supersede and INSERT.
        # This is the ONLY branch that calls supersede — stable and
        # rejected branches never touch candidate_reading_documents.
        try:
            await supersede_ready_candidates_for_locked_record(
                conn,
                record_id=record_id,
                user_id=user_id,
                generation=generation,
                now=now,
            )
        except CandidateWriteLockError as exc:
            raise ExtractedArtifactMaterializationError(
                f"Failed to supersede existing ready candidates during "
                f"materialization: {exc}",
                reason_code="caller_transaction_required",
            ) from exc

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
                f"candidate_base_ready (expected UPDATE 1, got {result!r})",
                reason_code="candidate_advance_failed",
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
                f"(expected UPDATE 1, got {result!r})",
                reason_code="action_required_advance_failed",
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
            f"(filename={source_filename!r})",
            reason_code="source_type_derivation_failed",
        )
    raise ExtractedArtifactMaterializationError(
        f"cannot derive source_type from unknown content_type "
        f"{content_type!r} (filename={source_filename!r}); supported: "
        f"text/plain, text/markdown, text/x-markdown, application/pdf, "
        f"image/*, application/octet-stream+(.md|.txt)",
        reason_code="source_type_derivation_failed",
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
