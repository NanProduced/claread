from __future__ import annotations

import re
from dataclasses import dataclass
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
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
    MarkdownParseResult,
    MarkdownSourceParser,
)

# M1: the normalizer now consumes the structured-source parser adapter
# for ``markdown_file`` input. The version bump reflects that frozen
# documents are no longer produced by a hand-written regex path; the
# parser identity triple (parser_name / parser_version / profile) is
# written into each markdown-sourced block's ``quality_json``.
NORMALIZER_VERSION = "d6_i3b_structured_source_v1"

_PARSER_IDENTITY: dict[str, str] = {
    "parser_name": PARSER_NAME,
    "parser_version": PARSER_VERSION,
    "profile": PROFILE,
}

_MARKDOWN_PARSER = MarkdownSourceParser()

_PLAIN_TEXT_SOURCE_TYPES = frozenset({"pasted_text", "txt_file"})
_SUPPORTED_SOURCE_TYPES = frozenset(
    {"pasted_text", "txt_file", "markdown_file"}
)

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
    parent_block_id: str | None = None


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
        used_markdown_parser = False
        if request.source_type in _PLAIN_TEXT_SOURCE_TYPES:
            # 方案 C (upgrade routing): parse first, then check for
            # Markdown-specific structure (any block type other than
            # ``paragraph``). Paragraphs alone do not trigger the
            # upgrade because the plain text path already handles them;
            # only headings / lists / blockquotes / tables / code blocks
            # / thematic breaks require the typed-block markdown path.
            probe_result = _MARKDOWN_PARSER.parse(source_text)
            has_markdown_structure = any(
                block.block_type != "paragraph" for block in probe_result.blocks
            )
            if has_markdown_structure:
                drafts, title = _normalize_markdown_blocks(
                    source_text, parse_result=probe_result
                )
                used_markdown_parser = True
            else:
                drafts = _normalize_plain_text_blocks(source_text)
                title = None
        else:
            drafts, title = _normalize_markdown_blocks(source_text)
            used_markdown_parser = True

        blocks = [
            _draft_to_block(
                draft=draft,
                block_index=index,
                source_type=request.source_type,
                filename=request.filename,
                used_markdown_parser=used_markdown_parser,
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
    *,
    parse_result: MarkdownParseResult | None = None,
) -> tuple[list[_BlockDraft], str | None]:
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
                links=[],
                parent_block_id=block.parent_block_id,
            )
        )

    return drafts, title


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


def _draft_to_block(
    *,
    draft: _BlockDraft,
    block_index: int,
    source_type: InputAdapterSourceType,
    filename: str | None,
    used_markdown_parser: bool,
) -> StableDocumentBlock:
    source_refs_json: dict[str, Any] = {
        "source_type": source_type,
        "line_start": draft.line_start,
        "line_end": draft.line_end,
    }
    if filename is not None:
        source_refs_json["filename"] = filename
    # Plain text path still extracts links into source_refs_json; the
    # markdown parser path keeps links in payload_json per the
    # Structured Source Contract.
    if draft.links:
        source_refs_json["links"] = draft.links

    quality_json: dict[str, Any] = {
        "normalizer_version": NORMALIZER_VERSION,
    }
    if used_markdown_parser:
        quality_json.update(_PARSER_IDENTITY)

    return StableDocumentBlock(
        block_id=f"b{block_index + 1}",
        parent_block_id=draft.parent_block_id,
        order_index=block_index,
        block_type=draft.block_type,
        text_content=draft.text_content,
        payload_json=draft.payload_json,
        source_refs_json=source_refs_json,
        quality_json=quality_json,
    )
