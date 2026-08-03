"""L2 — Confirmed Source 应用服务（合同 “Confirmed Source 生命周期” GET/PUT 端点后端）。

GET /records/{id}/confirmed-source：draft 读取 / resume 入口（编辑入口，
返回正文；仅当 source 为 draft 且 record 处于可编辑状态时返回 200，
其余 404 collapse / 409 record_state_advanced）。

PUT /records/{id}/confirmed-source：整篇更新 + reparse。单事务内
（锁顺序 record → source → candidate，§3.4）：
    1. lock_record_for_candidate_write（generation fence 复用）；
    2. source 行 FOR UPDATE；frozen → 409 source_frozen；
    3. revision 乐观并发；stale → 409 stale_source_revision（不覆盖
       较新草稿）；
    4. 规范化文本同 hash → 幂等 no-op（不 supersede，revision 不变）；
    5. reparse 一次（阶段 1 的 preparsed + detected_format + 三级
       分类机制）→ gate 分类；
    6. candidate 分支：UPDATE source（revision+1）→ supersede →
       插入新 candidate（source_refs 三 key）；
       stable 分支（Q2 镜像 submit 自动 freeze）：UPDATE source →
       normalize → freeze plan → persist → set_active_base → 同事务
       freeze source（插入点 B 同一 _freeze 步骤）→ publish event，
       commit 后 reload snapshot；
       rejected 分支：source 已保存 draft，product_state 推进
       action_required（_materialize_rejected 模式），无 candidate。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.schemas.reader_documents import ConfirmedSourceDocument
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
)
from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration._text import (
    resolve_default_reader_language,
)
from app.services.reader_orchestration.article_rag_auto_ensure_service import (
    ArticleRagAutoEnsureService,
    build_default_auto_ensure_service,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
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
    _normalize_source_text,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    ConfirmedSourceError,
    confirmed_source_content_sha256,
    freeze_confirmed_source,
    load_confirmed_source,
    lock_confirmed_source_for_update,
    update_confirmed_source_with_expected_revision,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    persist_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.event_runtime import (
    ReaderEventRuntime,
)
from app.services.reader_orchestration.input_document_normalizer import (
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)
from app.services.reader_orchestration.repository import (
    CandidateWriteLockError,
    ReaderOrchestrationRepository,
    lock_record_for_candidate_write,
    supersede_ready_candidates_for_locked_record,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    _build_article_ready_payload,
)

_MARKDOWN_PARSER = MarkdownSourceParser()

# original_inputs.input_type → 输入 source_type（PUT reparse 用；
# 与 stable_ready/_ORIGINAL_INPUT_TYPE_BY_INPUT_SOURCE 反向映射）。
_SOURCE_TYPE_BY_ORIGINAL_INPUT_TYPE: dict[str, InputAdapterSourceType] = {
    "plain_text": "pasted_text",
    "markdown": "markdown_file",
    "file_ref": "txt_file",
    "image_ref": "ocr_text",
    "url": "url_text",
}


class ConfirmedSourceApplicationError(ValueError):
    """服务层失败（5xx 语义，可重试）。"""


class ConfirmedSourceNotFoundError(ValueError):
    """404 collapse：not found / not owner / deleted / 无 draft source
    （不区分原因，合同 “GET draft / resume 语义”）。"""


class ConfirmedSourceConflictError(ValueError):
    """409 可恢复冲突（root-level 错误合同）。"""

    def __init__(
        self,
        message: str,
        *,
        code: Literal[
            "record_state_advanced",
            "source_frozen",
            "stale_source_revision",
        ],
        resolution: Literal["open_reader", "reload", "return_to_library"],
        current_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.resolution = resolution
        self.current_revision = current_revision


@dataclass(frozen=True, slots=True)
class ConfirmedSourceCandidateSummary:
    candidate_document_id: UUID
    status: str
    canonical_text_preview: str


@dataclass(frozen=True, slots=True)
class ConfirmedSourceGetResult:
    source: ConfirmedSourceDocument
    updated_at: datetime
    candidate: ConfirmedSourceCandidateSummary | None
    # L2 联调：Content Check 首载/刷新恢复所需的三级分类信息（与 PUT
    # 响应语义一致，来自最新 ready candidate 的 quality_json；
    # 无 candidate 时 {} / []）。
    quality: dict[str, Any] = field(default_factory=dict)
    adaptation_notice: list[dict[str, Any]] = field(default_factory=list)
    content_check: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConfirmedSourceUpdateResult:
    revision: int
    content_sha256: str
    outcome: str
    candidate: ConfirmedSourceCandidateSummary | None
    quality: dict[str, Any]
    adaptation_notice: list[dict[str, Any]]
    content_check: list[dict[str, Any]]
    snapshot: ReaderPlateSnapshot | None


def _coerce_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


class ConfirmedSourceApplicationService:
    """GET / PUT confirmed-source 的应用服务。"""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        snapshot_service: ArticleReadyPersistenceService | None = None,
        auto_ensure_service: ArticleRagAutoEnsureService | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._snapshot_service = snapshot_service or ArticleReadyPersistenceService(
            pool=pool,
            repository=self._repository,
        )
        self._auto_ensure_service = auto_ensure_service

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return self._repository.get_pool()

    def _get_auto_ensure_service(self) -> ArticleRagAutoEnsureService:
        if self._auto_ensure_service is None:
            self._auto_ensure_service = build_default_auto_ensure_service()
        return self._auto_ensure_service

    # ------------------------------------------------------------------
    # 内部查询 helpers
    # ------------------------------------------------------------------

    async def _load_record_row(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> asyncpg.Record | None:
        return await conn.fetchrow(
            """
            SELECT id, generation, product_state, lifecycle_status
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
            """,
            record_id,
            user_id,
        )

    async def _load_ready_candidate_summary(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        generation: int,
    ) -> ConfirmedSourceCandidateSummary | None:
        row = await conn.fetchrow(
            """
            SELECT id, status, canonical_text_preview
            FROM candidate_reading_documents
            WHERE reading_record_id = $1
              AND record_generation = $2
              AND status = 'ready'
            """,
            record_id,
            generation,
        )
        if row is None:
            return None
        return ConfirmedSourceCandidateSummary(
            candidate_document_id=UUID(str(row["id"])),
            status=str(row["status"]),
            canonical_text_preview=str(row["canonical_text_preview"] or ""),
        )

    async def _load_ready_candidate_adaptations(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        generation: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """最新 ready candidate 的 quality_json 与三级分类拆分（L2 联调：
        Content Check 首载/刷新恢复）。无 candidate 时返回 ({}, [], [])。"""
        row = await conn.fetchrow(
            """
            SELECT quality_json
            FROM candidate_reading_documents
            WHERE reading_record_id = $1
              AND record_generation = $2
              AND status = 'ready'
            """,
            record_id,
            generation,
        )
        if row is None:
            return {}, [], []
        quality = _coerce_json_object(row["quality_json"])
        suitability = _coerce_json_object(quality.get("suitability"))
        adaptations_raw = suitability.get("adaptations")
        adaptation_notice: list[dict[str, Any]] = []
        content_check: list[dict[str, Any]] = []
        if isinstance(adaptations_raw, list):
            for item in adaptations_raw:
                if not isinstance(item, Mapping):
                    continue
                record = dict(item)
                if record.get("classification") == "adaptation_notice":
                    adaptation_notice.append(record)
                elif record.get("classification") == "content_check":
                    content_check.append(record)
        return quality, adaptation_notice, content_check

    async def _load_source_updated_at(
        self,
        conn: asyncpg.Connection,
        *,
        source_document_id: UUID,
    ) -> datetime:
        value = await conn.fetchval(
            "SELECT updated_at FROM confirmed_source_documents WHERE id = $1",
            source_document_id,
        )
        assert isinstance(value, datetime)
        return value

    # ------------------------------------------------------------------
    # GET — draft 读取 / resume 入口
    # ------------------------------------------------------------------

    async def get_confirmed_source(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> ConfirmedSourceGetResult:
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                record_row = await self._load_record_row(
                    conn, record_id=record_id, user_id=user_id
                )
                if record_row is None:
                    raise ConfirmedSourceNotFoundError(
                        f"reading_record {record_id} not found for confirmed "
                        "source read (collapsed: not found / not owner / "
                        "deleted)."
                    )
                generation = int(record_row["generation"])
                source = await load_confirmed_source(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                )
                if source is None:
                    # legacy record（无 source 行）—— 404 collapse。
                    raise ConfirmedSourceNotFoundError(
                        f"no confirmed_source_documents row for "
                        f"reading_record {record_id} generation "
                        f"{generation} (legacy record or never submitted)."
                    )
                # 仅 draft + 可编辑状态返回 200；已冻结 / 已推进 → 409。
                if source.status != "draft":
                    raise ConfirmedSourceConflictError(
                        f"confirmed source {source.id} is already frozen; "
                        "the record has advanced to a readable state.",
                        code="record_state_advanced",
                        resolution="open_reader",
                        current_revision=source.revision,
                    )
                product_state = str(record_row["product_state"])
                if product_state not in (
                    "needs_confirmation",
                    "processing",
                    "action_required",
                ):
                    raise ConfirmedSourceConflictError(
                        f"reading_record {record_id} product_state="
                        f"{product_state!r} no longer accepts source edits.",
                        code="record_state_advanced",
                        resolution="open_reader",
                        current_revision=source.revision,
                    )
                candidate = await self._load_ready_candidate_summary(
                    conn, record_id=record_id, generation=generation
                )
                (
                    quality,
                    adaptation_notice,
                    content_check,
                ) = await self._load_ready_candidate_adaptations(
                    conn, record_id=record_id, generation=generation
                )
                updated_at = await self._load_source_updated_at(
                    conn, source_document_id=UUID(source.id)
                )
        return ConfirmedSourceGetResult(
            source=source,
            updated_at=updated_at,
            candidate=candidate,
            quality=quality,
            adaptation_notice=adaptation_notice,
            content_check=content_check,
        )

    # ------------------------------------------------------------------
    # PUT — 整篇更新 + reparse
    # ------------------------------------------------------------------

    async def update_confirmed_source(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_revision: int,
        markdown_text: str,
        edit_source: Literal["wysiwyg", "source_mode", "content_check"],
        language: str | None = "en",
        now: datetime | None = None,
    ) -> ConfirmedSourceUpdateResult:
        updated_at = now or datetime.now(UTC)
        language_value = resolve_default_reader_language(language)
        normalized_text = _normalize_source_text(markdown_text)
        if not normalized_text:
            raise ConfirmedSourceApplicationError(
                "markdown_text is blank after normalization; refusing to "
                "store an empty confirmed source body."
            )

        pool = self._get_pool()
        outcome: str | None = None
        new_revision: int | None = None
        new_hash: str | None = None
        candidate_summary: ConfirmedSourceCandidateSummary | None = None
        quality: dict[str, Any] = {}
        snapshot_base_id: UUID | None = None
        snapshot_generation: int | None = None

        async with pool.acquire() as conn:
            async with conn.transaction():
                # (1) record 行锁（generation fence 复用）。先读
                # generation 再加锁——全库当前恒为 1，fence 语义不变。
                record_row = await self._load_record_row(
                    conn, record_id=record_id, user_id=user_id
                )
                if record_row is None:
                    raise ConfirmedSourceNotFoundError(
                        f"reading_record {record_id} not found for "
                        "confirmed source update (collapsed)."
                    )
                generation = int(record_row["generation"])
                try:
                    await lock_record_for_candidate_write(
                        conn,
                        record_id=record_id,
                        user_id=user_id,
                        expected_generation=generation,
                    )
                except CandidateWriteLockError as exc:
                    raise ConfirmedSourceNotFoundError(
                        f"confirmed source update lock failed for "
                        f"reading_record {record_id}: {exc}"
                    ) from exc

                # (2) source 行 FOR UPDATE（锁顺序 record → source）。
                source = await lock_confirmed_source_for_update(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    generation=generation,
                )
                if source is None:
                    raise ConfirmedSourceNotFoundError(
                        f"no confirmed_source_documents row for "
                        f"reading_record {record_id} generation "
                        f"{generation} (legacy record)."
                    )
                if source.status == "frozen":
                    raise ConfirmedSourceConflictError(
                        f"confirmed source {source.id} is frozen; edits "
                        "are no longer accepted.",
                        code="source_frozen",
                        resolution="open_reader",
                        current_revision=source.revision,
                    )
                # (3) revision 乐观并发——stale 不覆盖较新草稿。
                if source.revision != expected_revision:
                    raise ConfirmedSourceConflictError(
                        f"confirmed source {source.id} revision is "
                        f"{source.revision}, expected "
                        f"{expected_revision}; reload and replay the edit.",
                        code="stale_source_revision",
                        resolution="reload",
                        current_revision=source.revision,
                    )

                # (4) 规范化同 hash → 幂等 no-op（不 supersede，
                # revision 不变，返回当前 ready candidate）。
                if confirmed_source_content_sha256(normalized_text) == (
                    source.content_sha256
                ):
                    candidate_summary = await self._load_ready_candidate_summary(
                        conn, record_id=record_id, generation=generation
                    )
                    return ConfirmedSourceUpdateResult(
                        revision=source.revision,
                        content_sha256=source.content_sha256,
                        outcome="idempotent_noop",
                        candidate=candidate_summary,
                        quality={},
                        adaptation_notice=[],
                        content_check=[],
                        snapshot=None,
                    )

                # (5) reparse 一次 + gate 分类（阶段 1 机制：
                # preparsed + detected_format + 三级分类）。
                source_type = await self._derive_source_type(
                    conn, record_id=record_id, generation=generation,
                    source=source,
                )
                filename, source_metadata = await self._load_input_context(
                    conn, source=source
                )
                preparsed = _MARKDOWN_PARSER.parse(normalized_text)
                suitability = evaluate_input_suitability(
                    InputSuitabilityRequest(
                        source_type=source_type,
                        text=normalized_text,
                        filename=filename,
                        source_metadata=source_metadata,
                    ),
                    preparsed=preparsed,
                )
                quality = _candidate_quality_json(suitability=suitability)

                # (6) 同事务 UPDATE source（revision+1，乐观并发由
                # 上面的行锁 + revision 校验保证）。
                try:
                    updated_source = (
                        await update_confirmed_source_with_expected_revision(
                            conn,
                            source_document_id=UUID(source.id),
                            record_id=record_id,
                            expected_revision=expected_revision,
                            markdown_text=normalized_text,
                            edit_source=edit_source,
                            now=updated_at,
                        )
                    )
                except ConfirmedSourceError as exc:
                    raise ConfirmedSourceApplicationError(
                        f"confirmed source update failed: {exc}"
                    ) from exc
                new_revision = updated_source.revision
                new_hash = updated_source.content_sha256

                if suitability.outcome == "candidate_document_required":
                    candidate_summary = await self._apply_candidate_branch(
                        conn,
                        record_id=record_id,
                        user_id=user_id,
                        generation=generation,
                        source_type=source_type,
                        filename=filename,
                        source_metadata=source_metadata,
                        normalized_text=normalized_text,
                        suitability=suitability,
                        preparsed=preparsed,
                        updated_source=updated_source,
                        now=updated_at,
                    )
                    outcome = "candidate_document_required"
                elif suitability.outcome == "stable_document_ready":
                    snapshot_base_id = await self._apply_stable_branch(
                        conn,
                        record_id=record_id,
                        user_id=user_id,
                        generation=generation,
                        source_type=source_type,
                        filename=filename,
                        source_metadata=source_metadata,
                        normalized_text=normalized_text,
                        suitability=suitability,
                        preparsed=preparsed,
                        updated_source=updated_source,
                        language=language_value,
                        now=updated_at,
                    )
                    snapshot_generation = generation
                    outcome = "stable_document_ready"
                else:
                    await self._apply_rejected_branch(
                        conn,
                        record_id=record_id,
                        generation=generation,
                        now=updated_at,
                    )
                    outcome = "input_rejected_or_action_required"

        # commit 后：stable 分支 reload snapshot（镜像 confirm/submit）。
        snapshot: ReaderPlateSnapshot | None = None
        if outcome == "stable_document_ready":
            assert snapshot_base_id is not None
            assert snapshot_generation is not None
            snapshot = await self._snapshot_service.load_snapshot(
                record_id=record_id,
                user_id=user_id,
                expected_base_id=snapshot_base_id,
                expected_generation=snapshot_generation,
            )

        adaptation_notice = [
            record.model_dump()
            for record in suitability.adaptations
            if record.classification == "adaptation_notice"
        ]
        content_check = [
            record.model_dump()
            for record in suitability.adaptations
            if record.classification == "content_check"
        ]
        assert new_revision is not None
        assert new_hash is not None
        return ConfirmedSourceUpdateResult(
            revision=new_revision,
            content_sha256=new_hash,
            outcome=outcome or "input_rejected_or_action_required",
            candidate=candidate_summary,
            quality=quality,
            adaptation_notice=adaptation_notice,
            content_check=content_check,
            snapshot=snapshot,
        )

    # ------------------------------------------------------------------
    # PUT 内部分支
    # ------------------------------------------------------------------

    async def _derive_source_type(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        generation: int,
        source: ConfirmedSourceDocument,
    ) -> InputAdapterSourceType:
        """恢复原始提交的 source_type：优先最新 candidate 的
        source_refs_json.source_type，回退 original_inputs.input_type
        反映射，最终回退 pasted_text。"""
        row = await conn.fetchrow(
            """
            SELECT source_refs_json
            FROM candidate_reading_documents
            WHERE reading_record_id = $1
              AND record_generation = $2
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            record_id,
            generation,
        )
        if row is not None:
            refs = _coerce_json_object(row["source_refs_json"])
            candidate_source_type = refs.get("source_type")
            if isinstance(candidate_source_type, str) and candidate_source_type:
                return candidate_source_type  # type: ignore[return-value]
        if source.original_input_id is not None:
            input_type = await conn.fetchval(
                "SELECT input_type FROM original_inputs WHERE id = $1",
                UUID(source.original_input_id),
            )
            if isinstance(input_type, str):
                mapped = _SOURCE_TYPE_BY_ORIGINAL_INPUT_TYPE.get(input_type)
                if mapped is not None:
                    return mapped
        return "pasted_text"

    async def _load_input_context(
        self,
        conn: asyncpg.Connection,
        *,
        source: ConfirmedSourceDocument,
    ) -> tuple[str | None, dict[str, Any]]:
        """读取 original_input 的 filename / source_metadata（reparse
        上下文）；无 original_input 时为空。"""
        if source.original_input_id is None:
            return None, {}
        row = await conn.fetchrow(
            """
            SELECT source_ref_json, metadata_json
            FROM original_inputs
            WHERE id = $1
            """,
            UUID(source.original_input_id),
        )
        if row is None:
            return None, {}
        source_ref = _coerce_json_object(row["source_ref_json"])
        filename_raw = source_ref.get("filename")
        filename = str(filename_raw) if isinstance(filename_raw, str) else None
        metadata = _coerce_json_object(row["metadata_json"])
        return filename, metadata

    async def _apply_candidate_branch(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        generation: int,
        source_type: InputAdapterSourceType,
        filename: str | None,
        source_metadata: dict[str, Any],
        normalized_text: str,
        suitability: InputSuitabilityResult,
        preparsed: Any,
        updated_source: ConfirmedSourceDocument,
        now: datetime,
    ) -> ConfirmedSourceCandidateSummary:
        """版本化 candidate supersede（合同 “PUT whole-document update”）。"""
        original_input_id = (
            UUID(updated_source.original_input_id)
            if updated_source.original_input_id is not None
            else uuid4()
        )
        blocks, title = _build_candidate_blocks(
            source_type=source_type,
            text=normalized_text,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=original_input_id,
            preparsed=preparsed,
        )
        candidate_document_id = uuid4()
        preview = _canonical_text_preview(suitability=suitability, blocks=blocks)
        source_refs = _candidate_source_refs_json(
            source_type=source_type,
            filename=filename,
            source_metadata=source_metadata,
            original_input_id=original_input_id,
            confirmed_source=updated_source,
        )
        quality = _candidate_quality_json(suitability=suitability)

        try:
            await supersede_ready_candidates_for_locked_record(
                conn,
                record_id=record_id,
                user_id=user_id,
                generation=generation,
                now=now,
            )
        except CandidateWriteLockError as exc:
            raise ConfirmedSourceApplicationError(
                f"confirmed source update failed to supersede ready "
                f"candidates: {exc}"
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

        # 推进 record 到 needs_confirmation（允许从 processing /
        # action_required 重新进入；与 materialization candidate 分支
        # 同一 guard 模式）。
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
            raise ConfirmedSourceApplicationError(
                f"failed to advance reading_record {record_id} to "
                f"needs_confirmation (expected UPDATE 1, got {result!r})"
            )
        return ConfirmedSourceCandidateSummary(
            candidate_document_id=candidate_document_id,
            status="ready",
            canonical_text_preview=preview,
        )

    async def _apply_stable_branch(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
        generation: int,
        source_type: InputAdapterSourceType,
        filename: str | None,
        source_metadata: dict[str, Any],
        normalized_text: str,
        suitability: InputSuitabilityResult,
        preparsed: Any,
        updated_source: ConfirmedSourceDocument,
        language: str,
        now: datetime,
    ) -> UUID:
        """stable-ready-after-edit 自动 freeze（Q2 镜像 submit）：
        normalize → freeze plan → persist → set_active_base → 同事务
        freeze source（插入点 B 同一步骤）→ publish event。返回 base_id
        供 commit 后 snapshot reload。"""
        normalized = normalize_input_document(
            InputSuitabilityRequest(
                source_type=source_type,
                text=normalized_text,
                filename=filename,
                source_metadata=source_metadata,
            ),
            preparsed=preparsed,
        )
        plan = build_stable_document_freeze_plan(
            reading_record_id=str(record_id),
            record_generation=generation,
            document_version=generation,
            title=normalized.title,
            blocks=normalized.blocks,
            source_profile_json={
                "source_type": source_type,
                "filename": filename,
                "source_metadata": source_metadata,
                "suitability": {
                    "outcome": suitability.outcome,
                    "flags": list(suitability.flags),
                    "reasons": list(suitability.reasons),
                    "adaptations": [
                        record.model_dump()
                        for record in suitability.adaptations
                    ],
                },
                "parser_identity": (
                    dict(normalized.parser_identity)
                    if normalized.parser_identity is not None
                    else None
                ),
                "materialization_source": "confirmed_source_update",
            },
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
            raise ConfirmedSourceApplicationError(
                f"freeze persistence returned null base_id for record "
                f"{record_id}"
            )

        # 插入点 B（同一 _freeze 步骤）：source 冻结与 Stable
        # Document 同事务原子提交（期望 UPDATE 1）。
        try:
            await freeze_confirmed_source(
                conn,
                source_document_id=UUID(updated_source.id),
                now=now,
            )
        except ConfirmedSourceError as exc:
            raise ConfirmedSourceApplicationError(
                f"confirmed source freeze failed: {exc}"
            ) from exc

        try:
            await self._repository.set_active_base_and_mark_article_ready(
                conn,
                record_id=record_id,
                base_id=freeze_result.base_id,
                expected_generation=generation,
                updated_at=now,
            )
        except (ValueError, LookupError, RuntimeError) as exc:
            raise ConfirmedSourceApplicationError(
                f"failed to mark reading_record {record_id} "
                f"article_ready: {exc}"
            ) from exc

        # D6-I4V: Article RAG index auto-ensure (fail-soft)，镜像
        # stable-ready / confirm 路径。
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
        payload["source"] = "confirmed_source_update"
        payload["article_rag_index"] = {
            "status": rag_result.status,
            "reason_code": rag_result.reason_code,
        }
        await self._event_runtime.publish_event_in_transaction(
            conn,
            record_id=record_id,
            event_type="article_ready",
            payload_json=payload,
            created_at=now,
        )
        return freeze_result.base_id

    async def _apply_rejected_branch(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        generation: int,
        now: datetime,
    ) -> None:
        """rejected：source 已保存 draft，product_state 推进
        action_required（_materialize_rejected 模式），无 candidate。"""
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
            raise ConfirmedSourceApplicationError(
                f"failed to mark reading_record {record_id} as "
                f"action_required (expected UPDATE 1, got {result!r})"
            )
