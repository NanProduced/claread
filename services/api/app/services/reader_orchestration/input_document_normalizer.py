from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.schemas.reader_documents import StableDocumentBlock
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
    NormalizedInputDocument,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)

NORMALIZER_VERSION = "d6_i3b_plain_text_markdown_v1"

_PLAIN_TEXT_SOURCE_TYPES = frozenset({"pasted_text", "txt_file"})
_SUPPORTED_SOURCE_TYPES = frozenset(
    {"pasted_text", "txt_file", "markdown_file"}
)

_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*$")
_ORDERED_LIST_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<marker>\d+[.)])\s+(.+?)\s*$")
_UNORDERED_LIST_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<marker>[-+*])\s+(.+?)\s*$")
_BLOCKQUOTE_PATTERN = re.compile(r"^\s{0,3}>\s?(.*)$")
_DIVIDER_PATTERN = re.compile(r"^\s{0,3}((?:-{3,})|(?:\*{3,})|(?:_{3,}))\s*$")
_FENCE_PATTERN = re.compile(r"^\s*([`~]{3,})([^\n]*)$")
_INLINE_LINK_PATTERN = re.compile(
    r"\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
_INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
_STRONG_PATTERN = re.compile(r"(\*\*|__)(.+?)\1")
_EMPHASIS_PATTERN = re.compile(r"(?<![\*_])(\*|_)([^ \t\n].*?[^ \t\n]|[^ \t\n])\1(?![\*_])")


class InputDocumentNormalizationError(ValueError):
    def __init__(
        self,
        *,
        suitability: InputSuitabilityResult,
        message: str | None = None,
    ) -> None:
        self.suitability = suitability
        self.outcome = suitability.outcome
        self.flags = list(suitability.flags)
        self.reasons = list(suitability.reasons)
        super().__init__(
            message
            or (
                "Input cannot be normalized as a stable document: "
                f"outcome={self.outcome}, flags={self.flags}, reasons={self.reasons}"
            )
        )


@dataclass(slots=True)
class _BlockDraft:
    block_type: str
    text_content: str | None
    payload_json: dict[str, Any]
    line_start: int
    line_end: int
    links: list[dict[str, str]]


@dataclass(slots=True)
class _ActiveList:
    list_id: str
    ordered: bool
    depth: int
    indent_width: int
    next_ordinal: int = 1


class InputDocumentNormalizer:
    def normalize(
        self,
        request: InputSuitabilityRequest,
    ) -> NormalizedInputDocument:
        suitability = evaluate_input_suitability(request)
        if suitability.outcome != "stable_document_ready":
            raise InputDocumentNormalizationError(suitability=suitability)

        if request.source_type not in _SUPPORTED_SOURCE_TYPES:
            raise InputDocumentNormalizationError(
                suitability=suitability,
                message=(
                    "Input is stable-document-ready but unsupported by the plain text "
                    f"/ simple markdown normalizer: source_type={request.source_type!r}; "
                    f"flags={list(suitability.flags)}; reasons={list(suitability.reasons)}"
                ),
            )

        source_text = _normalize_source_text(request.text)
        if request.source_type in _PLAIN_TEXT_SOURCE_TYPES:
            drafts = _normalize_plain_text_blocks(source_text)
            title = None
        else:
            drafts, title = _normalize_markdown_blocks(source_text)

        blocks = [
            _draft_to_block(
                draft=draft,
                block_index=index,
                source_type=request.source_type,
                filename=request.filename,
            )
            for index, draft in enumerate(drafts)
        ]

        return NormalizedInputDocument(
            source_type=request.source_type,
            title=title,
            blocks=blocks,
            suitability=suitability,
            source_loss_flags=list(suitability.flags),
            warnings=[],
        )


def normalize_input_document(
    request: InputSuitabilityRequest,
) -> NormalizedInputDocument:
    return InputDocumentNormalizer().normalize(request)


def _normalize_source_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_plain_text_blocks(source_text: str) -> list[_BlockDraft]:
    blocks: list[_BlockDraft] = []
    lines = source_text.split("\n")
    paragraph_lines: list[str] = []
    paragraph_start: int | None = None

    def flush(line_end: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        if paragraph_start is None:
            return
        text, links = _strip_inline_markdown(_join_soft_lines(paragraph_lines))
        if text:
            blocks.append(
                _BlockDraft(
                    block_type="paragraph",
                    text_content=text,
                    payload_json={},
                    line_start=paragraph_start,
                    line_end=line_end,
                    links=links,
                )
            )
        paragraph_lines = []
        paragraph_start = None

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            flush(index - 1)
            continue
        if paragraph_start is None:
            paragraph_start = index
        paragraph_lines.append(line)

    flush(len(lines))
    return blocks


def _normalize_markdown_blocks(
    source_text: str,
) -> tuple[list[_BlockDraft], str | None]:
    blocks: list[_BlockDraft] = []
    lines = source_text.split("\n")
    line_count = len(lines)
    index = 0
    title: str | None = None
    list_counter = 0
    active_list: _ActiveList | None = None

    while index < line_count:
        line = lines[index]
        if not line.strip():
            active_list = None
            index += 1
            continue

        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            active_list = None
            block, index = _consume_fenced_code_block(lines, index, fence_match)
            blocks.append(block)
            continue

        divider_match = _DIVIDER_PATTERN.match(line)
        if divider_match:
            active_list = None
            marker = divider_match.group(1).strip()
            blocks.append(
                _BlockDraft(
                    block_type="unknown",
                    text_content=None,
                    payload_json={"kind": "divider", "marker": marker},
                    line_start=index + 1,
                    line_end=index + 1,
                    links=[],
                )
            )
            index += 1
            continue

        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            active_list = None
            heading_text, links = _strip_inline_markdown(heading_match.group(2).strip())
            level = len(heading_match.group(1))
            blocks.append(
                _BlockDraft(
                    block_type="heading",
                    text_content=heading_text,
                    payload_json={"level": level},
                    line_start=index + 1,
                    line_end=index + 1,
                    links=links,
                )
            )
            if title is None and heading_text:
                title = heading_text
            index += 1
            continue

        quote_match = _BLOCKQUOTE_PATTERN.match(line)
        if quote_match:
            active_list = None
            block, index = _consume_blockquote(lines, index)
            blocks.append(block)
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
            blocks.append(block)
            continue

        active_list = None
        block, index = _consume_paragraph(lines, index)
        blocks.append(block)

    return blocks, title


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
        closing_match = _FENCE_PATTERN.match(line)
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
                        "language": language,
                        "info_string": info_string,
                    },
                    line_start=start_line,
                    line_end=index + 1,
                    links=[],
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
                "language": language,
                "info_string": info_string,
            },
            line_start=start_line,
            line_end=len(lines),
            links=[],
        ),
        len(lines),
    )


def _consume_blockquote(
    lines: list[str],
    index: int,
) -> tuple[_BlockDraft, int]:
    start_line = index + 1
    quote_lines: list[str] = []

    while index < len(lines):
        match = _BLOCKQUOTE_PATTERN.match(lines[index])
        if match is None:
            break
        quote_lines.append(match.group(1))
        index += 1

    text, links = _strip_inline_markdown(_join_soft_lines(quote_lines))
    return (
        _BlockDraft(
            block_type="blockquote",
            text_content=text,
            payload_json={},
            line_start=start_line,
            line_end=index,
            links=links,
        ),
        index,
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

    text, links = _strip_inline_markdown(_join_soft_lines(content_lines))
    block = _BlockDraft(
        block_type="list_item",
        text_content=text,
        payload_json={
            "list_id": active_list.list_id,
            "ordered": ordered,
            "ordinal": active_list.next_ordinal,
            "depth": depth,
            "marker": marker,
        },
        line_start=start_line,
        line_end=end_line,
        links=links,
    )
    active_list.next_ordinal += 1
    return block, index, active_list, list_counter


def _consume_paragraph(
    lines: list[str],
    index: int,
) -> tuple[_BlockDraft, int]:
    start_line = index + 1
    paragraph_lines = [lines[index]]
    index += 1

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            break
        if _starts_markdown_block(line):
            break
        paragraph_lines.append(line)
        index += 1

    text, links = _strip_inline_markdown(_join_soft_lines(paragraph_lines))
    return (
        _BlockDraft(
            block_type="paragraph",
            text_content=text,
            payload_json={},
            line_start=start_line,
            line_end=start_line + len(paragraph_lines) - 1,
            links=links,
        ),
        index,
    )


def _match_list_item(line: str) -> re.Match[str] | None:
    ordered_match = _ORDERED_LIST_PATTERN.match(line)
    if ordered_match is not None:
        return ordered_match
    return _UNORDERED_LIST_PATTERN.match(line)


def _starts_markdown_block(line: str) -> bool:
    return any(
        pattern.match(line) is not None
        for pattern in (
            _FENCE_PATTERN,
            _DIVIDER_PATTERN,
            _HEADING_PATTERN,
            _BLOCKQUOTE_PATTERN,
            _ORDERED_LIST_PATTERN,
            _UNORDERED_LIST_PATTERN,
        )
    )


def _join_soft_lines(lines: list[str]) -> str:
    return re.sub(r"[ \t]+", " ", " ".join(line.strip() for line in lines)).strip()


def _strip_inline_markdown(text: str) -> tuple[str, list[dict[str, str]]]:
    links: list[dict[str, str]] = []

    def replace_link(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        url = match.group("url").strip()
        links.append({"label": label, "url": url})
        return label

    text = _INLINE_LINK_PATTERN.sub(replace_link, text)
    text = _INLINE_CODE_PATTERN.sub(r"\1", text)
    text = _STRONG_PATTERN.sub(r"\2", text)
    text = _EMPHASIS_PATTERN.sub(r"\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, links


def _leading_indent_width(value: str) -> int:
    if not value:
        return 0
    indent_match = re.match(r"^\s*", value)
    if indent_match is None:
        return 0
    return len(indent_match.group(0).expandtabs(4))


def _draft_to_block(
    *,
    draft: _BlockDraft,
    block_index: int,
    source_type: InputAdapterSourceType,
    filename: str | None,
) -> StableDocumentBlock:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "line_start": draft.line_start,
        "line_end": draft.line_end,
    }
    if filename is not None:
        source_refs_json["filename"] = filename
    if draft.links:
        source_refs_json["links"] = draft.links

    return StableDocumentBlock(
        block_id=f"b{block_index + 1}",
        parent_block_id=None,
        order_index=block_index,
        block_type=draft.block_type,
        text_content=draft.text_content,
        payload_json=draft.payload_json,
        source_refs_json=source_refs_json,
        quality_json={
            "normalizer_version": NORMALIZER_VERSION,
        },
    )
