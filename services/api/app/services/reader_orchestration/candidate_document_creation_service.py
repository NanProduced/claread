from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.schemas.reader_documents import (
    CandidateReadingDocumentStatus,
    ConfirmedSourceDocument,
    StableDocumentBlock,
)
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
)
from app.schemas.reader_orchestration import (
    DEFAULT_READER_ORCHESTRATION_READING_GOAL,
    DEFAULT_READER_ORCHESTRATION_READING_VARIANT,
    ReaderOrchestrationReadingGoal,
    ReaderOrchestrationReadingVariant,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    candidate_confirmed_source_refs,
    insert_confirmed_source,
)
from app.services.reader_orchestration.input_format import (
    detect_input_format,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
    MarkdownParseResult,
    MarkdownSourceParser,
)
from app.services.reader_orchestration.repository import (
    CandidateWriteLockError,
    ReaderOrchestrationRepository,
    lock_record_for_candidate_write,
    supersede_ready_candidates_for_locked_record,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    _ORIGINAL_INPUT_TYPE_BY_INPUT_SOURCE,
    _READING_RECORD_SOURCE_TYPE_BY_INPUT_SOURCE,
    _source_ref_json,
)

_CANDIDATE_CREATION_VERSION = "candidate_creation_v1"
_READY_STATUS: CandidateReadingDocumentStatus = "ready"

_MARKDOWN_PARSER = MarkdownSourceParser()

# Parser identity triple (Clause 1) written into each markdown-sourced
# candidate block's ``quality_json`` to preserve provenance symmetry with
# the normalizer path (``input_document_normalizer._PARSER_IDENTITY``).
# Plain-text candidate blocks do NOT carry this triple.
_PARSER_IDENTITY: dict[str, str] = {
    "parser_name": PARSER_NAME,
    "parser_version": PARSER_VERSION,
    "profile": PROFILE,
}


@dataclass(frozen=True, slots=True)
class CandidateDocumentCreationResult:
    reading_record_id: UUID
    candidate_document_id: UUID
    record_generation: int
    status: CandidateReadingDocumentStatus
    suitability: InputSuitabilityResult
    title: str | None
    block_count: int
    source_type: InputAdapterSourceType
    filename: str | None
    original_input_id: UUID


class CandidateDocumentCreationError(ValueError):
    """Raised when candidate-required input cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class _BlockDraft:
    block_type: str
    text_content: str | None
    payload_json: dict[str, Any]
    line_start: int
    line_end: int
    links: list[dict[str, str]] = field(default_factory=list)
    parent_block_id: str | None = None
    # G2a-A policy carrier: parser-explicit interpretation policy (e.g.
    # image-only table_cell metadata_only); ``None`` keeps the
    # StableDocumentBlock block-type default.
    interpretation_policy: dict[str, Any] | None = None


class CandidateDocumentCreationService:
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

    async def create_candidate_document_from_input(
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
        reading_goal: ReaderOrchestrationReadingGoal = (
            DEFAULT_READER_ORCHESTRATION_READING_GOAL
        ),
        reading_variant: ReaderOrchestrationReadingVariant = (
            DEFAULT_READER_ORCHESTRATION_READING_VARIANT
        ),
        preparsed: MarkdownParseResult | None = None,
    ) -> CandidateDocumentCreationResult:
        created_at = now or datetime.now(UTC)
        language_value = (language or "en").strip() or "en"
        source_metadata_value = dict(source_metadata or {})
        # L2/ — 每请求只解析一次：当调用方未提供 preparsed 时，在
        # 这里解析一次并同时喂给 gate 与 candidate 块构造。格式检测
        # （detected_format）也由这同一份 MarkdownParseResult 决定，
        # 禁止再按 source_type 决定是否保留结构。
        if preparsed is None:
            preparsed = _MARKDOWN_PARSER.parse(_normalize_source_text(text))
        pool = self._get_pool()

        record_id: UUID | None = None
        original_input_id: UUID | None = None
        candidate_document_id: UUID | None = None
        title: str | None = None
        suitability: InputSuitabilityResult | None = None
        blocks: list[StableDocumentBlock] = []

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # 解析结果共享: thread the caller-provided parse
                    # result through the gate and the candidate block
                    # builder so the markdown parser runs at most once
                    # per request. ``preparsed=None`` preserves the
                    # legacy behavior (parse inside the gate).
                    suitability = evaluate_input_suitability(
                        InputSuitabilityRequest(
                            source_type=source_type,
                            text=text,
                            filename=filename,
                            source_metadata=source_metadata_value,
                        ),
                        preparsed=preparsed,
                    )
                    if suitability.outcome == "stable_document_ready":
                        raise CandidateDocumentCreationError(
                            "Input is stable-document-ready and must use the "
                            "stable-ready input route instead of candidate "
                            f"document creation: flags={list(suitability.flags)}, "
                            f"reasons={list(suitability.reasons)}"
                        )
                    if suitability.outcome == "input_rejected_or_action_required":
                        raise CandidateDocumentCreationError(
                            "Input cannot create a candidate document: "
                            f"outcome={suitability.outcome}, "
                            f"flags={list(suitability.flags)}, "
                            f"reasons={list(suitability.reasons)}"
                        )
                    if suitability.outcome != "candidate_document_required":
                        raise CandidateDocumentCreationError(
                            "Unsupported suitability outcome for candidate "
                            f"document creation: {suitability.outcome!r}"
                        )

                    record_id = uuid4()
                    original_input_id = uuid4()
                    candidate_document_id = uuid4()
                    blocks, title = _build_candidate_blocks(
                        source_type=source_type,
                        text=text,
                        filename=filename,
                        source_metadata=source_metadata_value,
                        original_input_id=original_input_id,
                        preparsed=preparsed,
                    )
                    if not blocks:
                        raise CandidateDocumentCreationError(
                            "Candidate document creation produced zero blocks. "
                            "Refusing to persist an empty candidate envelope."
                        )

                    try:
                        await _insert_reading_record(
                            conn,
                            record_id=record_id,
                            user_id=user_id,
                            client_record_id=client_record_id,
                            source_type=source_type,
                            title=title,
                            language=language_value,
                            created_at=created_at,
                            reading_goal=reading_goal,
                            reading_variant=reading_variant,
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
                            created_at=created_at,
                        )
                        # L2：同一事务内、在 candidate insert 之前插入
                        # Confirmed Source 行（revision=1, edit_source=
                        # 'initial'）。正文为 _normalize_source_text 后
                        # 的文本，与 blocks/reparse 输入严格同源；
                        # original_inputs.source_text 恒 NULL。
                        confirmed_source = await insert_confirmed_source(
                            conn,
                            source_document_id=uuid4(),
                            record_id=record_id,
                            user_id=user_id,
                            generation=1,
                            original_input_id=original_input_id,
                            markdown_text=_normalize_source_text(text),
                            edit_source="initial",
                            now=created_at,
                        )
                        # Lock the parent reading_records row (FOR UPDATE)
                        # and validate generation=1. The lock is held for
                        # the rest of the transaction, serializing
                        # concurrent candidate writes for the same record.
                        # Although the creation path always inserts a fresh
                        # record (uuid4), this call guarantees the
                        # write-side uniqueness invariant is enforced
                        # through the shared helper, consistent with the
                        # materialization path.
                        try:
                            await lock_record_for_candidate_write(
                                conn,
                                record_id=record_id,
                                user_id=user_id,
                                expected_generation=1,
                            )
                        except CandidateWriteLockError as exc:
                            raise CandidateDocumentCreationError(
                                "Candidate write lock failed during "
                                f"creation: {exc}"
                            ) from exc
                        # Supersede any existing ready candidates for this
                        # (record_id, generation) immediately before
                        # inserting the new ready candidate. The lock
                        # acquired above guarantees no concurrent writer
                        # can insert another ready candidate between
                        # supersede and INSERT.
                        try:
                            await supersede_ready_candidates_for_locked_record(
                                conn,
                                record_id=record_id,
                                user_id=user_id,
                                generation=1,
                                now=created_at,
                            )
                        except CandidateWriteLockError as exc:
                            raise CandidateDocumentCreationError(
                                "Candidate supersede failed during "
                                f"creation: {exc}"
                            ) from exc
                        await _insert_candidate_document(
                            conn,
                            candidate_document_id=candidate_document_id,
                            record_id=record_id,
                            user_id=user_id,
                            title=title,
                            blocks=blocks,
                            source_type=source_type,
                            filename=filename,
                            source_metadata=source_metadata_value,
                            original_input_id=original_input_id,
                            suitability=suitability,
                            created_at=created_at,
                            confirmed_source=confirmed_source,
                        )
                    except CandidateDocumentCreationError:
                        raise
                    except Exception as exc:
                        raise CandidateDocumentCreationError(
                            "Failed to persist the candidate-required input "
                            f"envelope: {exc}"
                        ) from exc
        except CandidateDocumentCreationError:
            raise
        except Exception as exc:
            raise CandidateDocumentCreationError(
                f"Candidate document creation failed unexpectedly: {exc}"
            ) from exc

        assert record_id is not None
        assert original_input_id is not None
        assert candidate_document_id is not None
        assert suitability is not None

        return CandidateDocumentCreationResult(
            reading_record_id=record_id,
            candidate_document_id=candidate_document_id,
            record_generation=1,
            status=_READY_STATUS,
            suitability=suitability,
            title=title,
            block_count=len(blocks),
            source_type=source_type,
            filename=filename,
            original_input_id=original_input_id,
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
    reading_goal: str,
    reading_variant: str,
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
            reading_goal,
            reading_variant,
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
            $8,
            $9,
            $9
        )
        """,
        record_id,
        user_id,
        client_record_id,
        _READING_RECORD_SOURCE_TYPE_BY_INPUT_SOURCE[source_type],
        title,
        language,
        reading_goal,
        reading_variant,
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
    # L2：original_inputs 只保留 lineage——source_text 恒 NULL（正文
    # 唯一载体是 confirmed_source_documents.markdown_text），
    # content_sha256 保留为原始提交文本的 hash（lineage 元数据，
    # 非正文）；source_ref_json 始终非空（ck_original_inputs_has_source）。
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
        None,
        jsonb_param(_source_ref_json(source_type=source_type, filename=filename)),
        jsonb_param(source_metadata),
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_at,
    )


async def _insert_candidate_document(
    conn: asyncpg.Connection,
    *,
    candidate_document_id: UUID,
    record_id: UUID,
    user_id: UUID,
    title: str | None,
    blocks: list[StableDocumentBlock],
    source_type: InputAdapterSourceType,
    filename: str | None,
    source_metadata: dict[str, Any],
    original_input_id: UUID,
    suitability: InputSuitabilityResult,
    created_at: datetime,
    confirmed_source: ConfirmedSourceDocument | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO candidate_reading_documents (
            id,
            reading_record_id,
            user_id,
            record_generation,
            title,
            blocks_json,
            canonical_text_preview,
            source_refs_json,
            quality_json,
            status,
            created_at,
            updated_at
        )
        VALUES (
            $1,
            $2,
            $3,
            1,
            $4,
            $5::jsonb,
            $6,
            $7::jsonb,
            $8::jsonb,
            'ready',
            $9,
            $9
        )
        """,
        candidate_document_id,
        record_id,
        user_id,
        title,
        jsonb_param([block.model_dump(mode="json") for block in blocks]),
        _canonical_text_preview(suitability=suitability, blocks=blocks),
        jsonb_param(
            _candidate_source_refs_json(
                source_type=source_type,
                filename=filename,
                source_metadata=source_metadata,
                original_input_id=original_input_id,
                confirmed_source=confirmed_source,
            )
        ),
        jsonb_param(_candidate_quality_json(suitability=suitability)),
        created_at,
    )


def _candidate_source_refs_json(
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
    source_metadata: dict[str, Any],
    original_input_id: UUID,
    confirmed_source: ConfirmedSourceDocument | None = None,
) -> dict[str, Any]:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "source_metadata": dict(source_metadata),
        "original_input_id": str(original_input_id),
    }
    if filename is not None:
        source_refs_json["filename"] = filename
    if confirmed_source is not None:
        # L2 — candidate 引用最新 source revision/hash（设计文档 §6：
        # 三 key 放 JSONB，不加列）。confirm 插入点 A 据此校验
        # stale_candidate_revision。
        source_refs_json.update(candidate_confirmed_source_refs(confirmed_source))
    return source_refs_json


def _candidate_quality_json(
    *,
    suitability: InputSuitabilityResult,
) -> dict[str, Any]:
    return {
        "candidate_creation_version": _CANDIDATE_CREATION_VERSION,
        "suitability": {
            "outcome": suitability.outcome,
            "flags": list(suitability.flags),
            "reasons": list(suitability.reasons),
            "word_count": suitability.word_count,
            "english_word_ratio": suitability.english_word_ratio,
            "natural_language_score": suitability.natural_language_score,
            # L1: three-level adaptation records (silent /
            # adaptation_notice / content_check).
            "adaptations": [
                record.model_dump() for record in suitability.adaptations
            ],
        },
    }


def _canonical_text_preview(
    *,
    suitability: InputSuitabilityResult,
    blocks: list[StableDocumentBlock],
) -> str:
    preview = suitability.normalized_preview.strip()
    if preview:
        return preview
    text_parts = [block.text_content.strip() for block in blocks if block.text_content]
    return "\n\n".join(part for part in text_parts if part)[:500]


def _build_candidate_blocks(
    *,
    source_type: InputAdapterSourceType,
    text: str,
    filename: str | None,
    source_metadata: dict[str, Any],
    original_input_id: UUID,
    preparsed: MarkdownParseResult | None = None,
) -> tuple[list[StableDocumentBlock], str | None]:
    normalized_text = _normalize_source_text(text)
    title = _title_from_source_metadata(source_metadata)
    # L2 — format 驱动的结构化构造：parser 是块结构的 single source
    # of truth，内容格式（而非输入来源）决定是否保留 Markdown 结构。
    # 粘贴的 Markdown（pasted_text）与 markdown_file 走同一条 parser
    # 路径；无 Markdown 标记的纯文本保持空行分段的纯文本行为。
    parse_result = (
        preparsed
        if preparsed is not None
        else _MARKDOWN_PARSER.parse(normalized_text)
    )
    content_format = detect_input_format(
        source_type=source_type,
        parse_result=parse_result,
    )
    if content_format == "markdown":
        drafts, markdown_title = _build_markdown_drafts_from_parser(
            normalized_text, parse_result=parse_result
        )
        title = markdown_title or title
        # markdown-format blocks carry the parser identity triple in
        # quality_json for provenance symmetry with the normalizer path.
        block_quality_json: dict[str, Any] = dict(_PARSER_IDENTITY)
    else:
        drafts = _build_plain_candidate_drafts(normalized_text)
        # Plain-text candidate blocks are not produced by the structured
        # source parser; quality_json stays empty to avoid false
        # provenance attribution.
        block_quality_json = {}
    blocks = [
        StableDocumentBlock(
            block_id=f"b{index + 1}",
            parent_block_id=draft.parent_block_id,
            order_index=index,
            block_type=draft.block_type,
            text_content=draft.text_content,
            payload_json=draft.payload_json,
            source_refs_json=_block_source_refs_json(
                source_type=source_type,
                filename=filename,
                original_input_id=original_input_id,
                line_start=draft.line_start,
                line_end=draft.line_end,
                links=draft.links,
            ),
            interpretation_policy=draft.interpretation_policy,
            quality_json=dict(block_quality_json),
        )
        for index, draft in enumerate(drafts)
    ]
    # Single semantic classification seam — do not copy rules here.
    from .semantic_classifier import attach_semantic_to_stable_blocks

    blocks = attach_semantic_to_stable_blocks(blocks)
    return blocks, title


def _build_plain_candidate_drafts(source_text: str) -> list[_BlockDraft]:
    lines = source_text.split("\n")
    drafts: list[_BlockDraft] = []
    start_index: int | None = None
    chunk_lines: list[str] = []

    def flush(end_index: int) -> None:
        nonlocal start_index, chunk_lines
        if start_index is None:
            return
        raw = "\n".join(chunk_lines).strip()
        if raw:
            drafts.append(
                _BlockDraft(
                    block_type="paragraph",
                    text_content=raw,
                    payload_json={},
                    line_start=start_index + 1,
                    line_end=end_index,
                )
            )
        start_index = None
        chunk_lines = []

    for index, line in enumerate(lines):
        if not line.strip():
            flush(index)
            continue
        if start_index is None:
            start_index = index
        chunk_lines.append(line)

    flush(len(lines))
    if drafts:
        return drafts
    return [
        _BlockDraft(
            block_type="unknown",
            text_content=source_text.strip() or text_or_placeholder(source_text),
            payload_json={"candidate_placeholder": True},
            line_start=1,
            line_end=max(1, len(lines)),
        )
    ]


def _build_markdown_drafts_from_parser(
    source_text: str,
    *,
    parse_result: MarkdownParseResult | None = None,
) -> tuple[list[_BlockDraft], str | None]:
    # 解析结果共享: reuse the caller-provided parse result when
    # available; otherwise parse once here. The caller (candidate
    # creation service) may already have parsed for the gate, so the
    # same MarkdownParseResult is threaded through to avoid a second
    # parse on the same text.
    result = (
        parse_result
        if parse_result is not None
        else _MARKDOWN_PARSER.parse(source_text)
    )
    title: str | None = None
    drafts: list[_BlockDraft] = []

    for block in result.blocks:
        if title is None and block.block_type == "heading":
            title = block.text_content

        drafts.append(
            _BlockDraft(
                block_type=block.block_type,
                text_content=block.text_content,
                payload_json=dict(block.payload_json),
                line_start=block.source_range.line_start,
                line_end=block.source_range.line_end,
                parent_block_id=block.parent_block_id,
                interpretation_policy=(
                    dict(block.interpretation_policy)
                    if block.interpretation_policy is not None
                    else None
                ),
            )
        )

    return drafts or _build_plain_candidate_drafts(source_text), title


def _block_source_refs_json(
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
    original_input_id: UUID,
    line_start: int,
    line_end: int,
    links: list[dict[str, str]],
) -> dict[str, Any]:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "original_input_id": str(original_input_id),
        "line_start": line_start,
        "line_end": line_end,
    }
    if filename is not None:
        source_refs_json["filename"] = filename
    if links:
        source_refs_json["links"] = list(links)
    return source_refs_json


def _title_from_source_metadata(source_metadata: dict[str, Any]) -> str | None:
    for key in ("title", "page_title", "document_title"):
        value = source_metadata.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _normalize_source_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def text_or_placeholder(text: str) -> str:
    normalized = text.strip()
    if normalized:
        return normalized
    return "[empty candidate placeholder]"
