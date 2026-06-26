from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.schemas.reader_documents import CandidateReadingDocumentStatus, StableDocumentBlock
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.repository import ReaderOrchestrationRepository
from app.services.reader_orchestration.stable_ready_input_application_service import (
    _ORIGINAL_INPUT_TYPE_BY_INPUT_SOURCE,
    _READING_RECORD_SOURCE_TYPE_BY_INPUT_SOURCE,
    _source_ref_json,
)

_CANDIDATE_CREATION_VERSION = "candidate_creation_v1"
_READY_STATUS: CandidateReadingDocumentStatus = "ready"

_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*$")
_CODE_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_ORDERED_LIST_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<marker>\d+[.)])\s+(.+?)\s*$")
_UNORDERED_LIST_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<marker>[-+*])\s+(.+?)\s*$")
_BLOCKQUOTE_PATTERN = re.compile(r"^\s*>\s?")
_TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$"
)
_DIVIDER_PATTERN = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")


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


@dataclass(slots=True)
class _ActiveList:
    list_id: str
    ordered: bool
    depth: int
    indent_width: int
    next_ordinal: int = 1


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
    ) -> CandidateDocumentCreationResult:
        created_at = now or datetime.now(UTC)
        language_value = (language or "en").strip() or "en"
        source_metadata_value = dict(source_metadata or {})
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
                    suitability = evaluate_input_suitability(
                        InputSuitabilityRequest(
                            source_type=source_type,
                            text=text,
                            filename=filename,
                            source_metadata=source_metadata_value,
                        )
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
                        )
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
) -> dict[str, Any]:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "source_metadata": dict(source_metadata),
        "original_input_id": str(original_input_id),
    }
    if filename is not None:
        source_refs_json["filename"] = filename
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
) -> tuple[list[StableDocumentBlock], str | None]:
    normalized_text = _normalize_source_text(text)
    title = _title_from_source_metadata(source_metadata)
    if source_type == "markdown_file":
        drafts, markdown_title = _build_markdown_candidate_drafts(normalized_text)
        title = markdown_title or title
    else:
        drafts = _build_plain_candidate_drafts(normalized_text)
    blocks = [
        StableDocumentBlock(
            block_id=f"{draft.block_type}-{index:04d}",
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
            ),
        )
        for index, draft in enumerate(drafts)
    ]
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


def _build_markdown_candidate_drafts(
    source_text: str,
) -> tuple[list[_BlockDraft], str | None]:
    lines = source_text.split("\n")
    drafts: list[_BlockDraft] = []
    title: str | None = None
    list_counter = 0
    active_list: _ActiveList | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            active_list = None
            index += 1
            continue

        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            active_list = None
            heading_text = heading_match.group(2).strip()
            drafts.append(
                _BlockDraft(
                    block_type="heading",
                    text_content=heading_text or stripped,
                    payload_json={"level": len(heading_match.group(1))},
                    line_start=index + 1,
                    line_end=index + 1,
                )
            )
            if title is None and heading_text:
                title = heading_text
            index += 1
            continue

        fence_match = _CODE_FENCE_PATTERN.match(line)
        if fence_match:
            active_list = None
            block, index = _consume_fenced_code_block(lines, index, fence_match)
            drafts.append(block)
            continue

        if _looks_like_markdown_table_start(lines, index):
            active_list = None
            start = index
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                index += 1
            raw = "\n".join(lines[start:index]).strip()
            drafts.append(
                _BlockDraft(
                    block_type="table",
                    text_content=raw,
                    payload_json={
                        "candidate_placeholder": True,
                        "format": "markdown_table",
                        "row_count": index - start,
                    },
                    line_start=start + 1,
                    line_end=max(start + 1, index),
                )
            )
            continue

        if _DIVIDER_PATTERN.match(line):
            active_list = None
            drafts.append(
                _BlockDraft(
                    block_type="unknown",
                    text_content=stripped,
                    payload_json={
                        "candidate_placeholder": True,
                        "kind": "markdown_divider",
                    },
                    line_start=index + 1,
                    line_end=index + 1,
                )
            )
            index += 1
            continue

        if _BLOCKQUOTE_PATTERN.match(line):
            active_list = None
            start = index
            quote_lines: list[str] = []
            while index < len(lines):
                current = lines[index]
                if not current.strip() or not _BLOCKQUOTE_PATTERN.match(current):
                    break
                quote_lines.append(_BLOCKQUOTE_PATTERN.sub("", current, count=1).strip())
                index += 1
            drafts.append(
                _BlockDraft(
                    block_type="blockquote",
                    text_content="\n".join(part for part in quote_lines if part).strip()
                    or "\n".join(lines[start:index]).strip(),
                    payload_json={},
                    line_start=start + 1,
                    line_end=max(start + 1, index),
                )
            )
            continue

        list_match = _match_list_item(line)
        if list_match is not None:
            block, index, active_list, list_counter = _consume_list_item(
                lines=lines,
                index=index,
                match=list_match,
                active_list=active_list,
                list_counter=list_counter,
            )
            drafts.append(block)
            continue

        active_list = None
        start = index
        paragraph_lines: list[str] = []
        while index < len(lines):
            current = lines[index]
            if not current.strip():
                break
            if index != start and (
                _HEADING_PATTERN.match(current)
                or _CODE_FENCE_PATTERN.match(current)
                or _looks_like_markdown_table_start(lines, index)
                or _DIVIDER_PATTERN.match(current)
                or _BLOCKQUOTE_PATTERN.match(current)
                or _is_list_item_line(current)
            ):
                break
            paragraph_lines.append(current)
            index += 1
        raw = "\n".join(paragraph_lines).strip()
        drafts.append(
            _BlockDraft(
                block_type="paragraph",
                text_content=raw,
                payload_json={},
                line_start=start + 1,
                line_end=max(start + 1, index),
            )
        )

    return drafts or _build_plain_candidate_drafts(source_text), title


def _consume_fenced_code_block(
    lines: list[str],
    index: int,
    opening_match: re.Match[str],
) -> tuple[_BlockDraft, int]:
    opening_fence = opening_match.group(1)
    info_string = opening_match.group(2).strip()
    language = info_string.split()[0] if info_string else None
    opening_char = opening_fence[0]
    required_length = len(opening_fence)
    start_line = index + 1
    code_lines: list[str] = []
    index += 1

    while index < len(lines):
        line = lines[index]
        closing_match = _CODE_FENCE_PATTERN.match(line)
        if (
            closing_match
            and closing_match.group(1)[0] == opening_char
            and len(closing_match.group(1)) >= required_length
        ):
            return (
                _BlockDraft(
                    block_type="code_block",
                    text_content="\n".join(code_lines),
                    payload_json={
                        "candidate_placeholder": True,
                        "language": language,
                        "info_string": info_string,
                        "closed": True,
                        "raw_fence_marker": opening_fence,
                    },
                    line_start=start_line,
                    line_end=index + 1,
                ),
                index + 1,
            )
        code_lines.append(line)
        index += 1

    return (
        _BlockDraft(
            block_type="code_block",
            text_content="\n".join(code_lines),
            payload_json={
                "candidate_placeholder": True,
                "language": language,
                "info_string": info_string,
                "closed": False,
                "raw_fence_marker": opening_fence,
            },
            line_start=start_line,
            line_end=len(lines),
        ),
        len(lines),
    )


def _consume_list_item(
    *,
    lines: list[str],
    index: int,
    match: re.Match[str],
    active_list: _ActiveList | None,
    list_counter: int,
) -> tuple[_BlockDraft, int, _ActiveList, int]:
    ordered = match.re is _ORDERED_LIST_PATTERN
    indent_width = _leading_indent_width(match.group("indent"))
    depth = indent_width // 2
    marker = match.group("marker")
    start_line = index + 1
    content_lines = [match.group(3)]
    index += 1
    end_line = start_line

    while index < len(lines):
        next_line = lines[index]
        if not next_line.strip():
            break
        if _starts_markdown_block(next_line):
            break
        next_indent = _leading_indent_width(next_line)
        if next_indent <= indent_width:
            break
        content_lines.append(next_line.strip())
        end_line = index + 1
        index += 1

    if (
        active_list is None
        or active_list.ordered != ordered
        or active_list.depth != depth
        or active_list.indent_width != indent_width
    ):
        list_counter += 1
        active_list = _ActiveList(
            list_id=f"l{list_counter}",
            ordered=ordered,
            depth=depth,
            indent_width=indent_width,
        )

    block = _BlockDraft(
        block_type="list_item",
        text_content=_join_soft_lines(content_lines),
        payload_json={
            "list_id": active_list.list_id,
            "ordered": ordered,
            "ordinal": active_list.next_ordinal,
            "depth": depth,
            "marker": marker,
        },
        line_start=start_line,
        line_end=end_line,
    )
    active_list.next_ordinal += 1
    return block, index, active_list, list_counter


def _block_source_refs_json(
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
    original_input_id: UUID,
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "original_input_id": str(original_input_id),
        "line_start": line_start,
        "line_end": line_end,
    }
    if filename is not None:
        source_refs_json["filename"] = filename
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


def _looks_like_markdown_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].strip()
    next_line = lines[index + 1].strip()
    return "|" in current and bool(_TABLE_SEPARATOR_PATTERN.match(next_line))


def _match_list_item(line: str) -> re.Match[str] | None:
    ordered_match = _ORDERED_LIST_PATTERN.match(line)
    if ordered_match is not None:
        return ordered_match
    return _UNORDERED_LIST_PATTERN.match(line)


def _starts_markdown_block(line: str) -> bool:
    return bool(
        _HEADING_PATTERN.match(line)
        or _CODE_FENCE_PATTERN.match(line)
        or _BLOCKQUOTE_PATTERN.match(line)
        or _DIVIDER_PATTERN.match(line)
        or _match_list_item(line)
    )


def _join_soft_lines(lines: list[str]) -> str:
    return re.sub(r"[ \t]+", " ", " ".join(line.strip() for line in lines)).strip()


def _leading_indent_width(value: str) -> int:
    if not value:
        return 0
    indent_match = re.match(r"^\s*", value)
    if indent_match is None:
        return 0
    return len(indent_match.group(0).expandtabs(4))


def _is_list_item_line(line: str) -> bool:
    return _match_list_item(line) is not None


def _strip_list_marker(line: str) -> str:
    stripped = _ORDERED_LIST_PATTERN.sub("", line, count=1)
    stripped = _UNORDERED_LIST_PATTERN.sub("", stripped, count=1)
    return stripped.strip() or line.strip()


def text_or_placeholder(text: str) -> str:
    normalized = text.strip()
    if normalized:
        return normalized
    return "[empty candidate placeholder]"
