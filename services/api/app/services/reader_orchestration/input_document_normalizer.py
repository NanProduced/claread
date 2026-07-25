from __future__ import annotations

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

# A4 — emitted in ``NormalizedInputDocument.warnings`` when a plain-text
# source is silently upgraded to the Markdown path because the parser
# detected non-paragraph block structure (heading / list / ...). The
# frontend can surface this so the reader understands why block-typed
# rendering kicked in for a ``pasted_text`` / ``txt_file`` input.
_WARNING_PLAINTEXT_UPGRADED_TO_MARKDOWN = "plaintext_upgraded_to_markdown"


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
        *,
        preparsed: MarkdownParseResult | None = None,
    ) -> NormalizedInputDocument:
        suitability = evaluate_input_suitability(request, preparsed=preparsed)
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
        # A4 — 解析结果共享: reuse the caller-provided parse result when
        # available; otherwise parse once here. Both the upgrade probe
        # and the block construction below consume this single result.
        parse_result = (
            preparsed
            if preparsed is not None
            else _MARKDOWN_PARSER.parse(source_text)
        )

        warnings: list[str] = [w.code for w in parse_result.warnings]
        used_markdown_parser = False
        if request.source_type in _PLAIN_TEXT_SOURCE_TYPES:
            # 方案 C (upgrade routing): check for Markdown-specific
            # structure (any block type other than ``paragraph``).
            # Paragraphs alone do not trigger the upgrade because the
            # plain text path already handles them; only headings /
            # lists / blockquotes / tables / code blocks / thematic
            # breaks require the typed-block markdown path.
            has_markdown_structure = any(
                block.block_type != "paragraph" for block in parse_result.blocks
            )
            if has_markdown_structure:
                drafts, title = _normalize_markdown_blocks(
                    source_text, parse_result=parse_result
                )
                used_markdown_parser = True
                # A4 — record the silent upgrade so the frontend can hint.
                if _WARNING_PLAINTEXT_UPGRADED_TO_MARKDOWN not in warnings:
                    warnings.append(_WARNING_PLAINTEXT_UPGRADED_TO_MARKDOWN)
            else:
                # A4 — plain-text path now reuses the parser inline
                # flatten (links / inline_marks) instead of the legacy
                # regex ``_strip_inline_markdown``. Only the blank-line
                # segmentation thin logic is preserved: the parser
                # already splits paragraphs on blank lines, so we map
                # each paragraph block to a draft directly. Soft line
                # breaks (parser-emitted "\n") are joined with a space
                # to preserve the legacy plain-text reading behavior.
                drafts, title = _normalize_plain_text_blocks_from_parser(
                    parse_result
                )
        else:
            drafts, title = _normalize_markdown_blocks(
                source_text, parse_result=parse_result
            )
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
            warnings=warnings,
            parser_identity=dict(_PARSER_IDENTITY) if used_markdown_parser else None,
        )


def normalize_input_document(
    request: InputSuitabilityRequest,
    *,
    preparsed: MarkdownParseResult | None = None,
) -> NormalizedInputDocument:
    return InputDocumentNormalizer().normalize(request, preparsed=preparsed)


def _normalize_source_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_plain_text_blocks_from_parser(
    parse_result: MarkdownParseResult,
) -> tuple[list[_BlockDraft], str | None]:
    """Build plain-text drafts from parser paragraph blocks.

    A4 — replaces the legacy ``_normalize_plain_text_blocks`` +
    ``_strip_inline_markdown`` regex path. The parser already:
      * splits paragraphs on blank lines,
      * flattens inline marks (bold / italic / code) into text,
      * extracts safe links into ``payload_json.links``,
      * records inline marks in ``payload_json.inline_marks``.
    The only plain-text-specific post-processing is joining soft line
    breaks (parser-emitted ``\\n``) with a space, preserving the legacy
    reading-flow behavior for non-markdown sources.
    """
    drafts: list[_BlockDraft] = []
    title: str | None = None

    for block in parse_result.blocks:
        # Skip non-paragraph blocks in the plain-text path; if the
        # probe detected structure, the upgrade path is used instead.
        if block.block_type != "paragraph":
            continue
        text_content = block.text_content or ""
        # Plain-text soft line breaks join with a space (legacy behavior).
        text_content = text_content.replace("\n", " ")
        payload = dict(block.payload_json)
        # Inline links: keep in payload_json per Structured Source
        # Contract, and also surface as ``links`` on the draft for
        # source_refs_json (preserving the legacy plain-text contract).
        links = list(payload.get("links", []))
        drafts.append(
            _BlockDraft(
                block_type="paragraph",
                text_content=text_content,
                payload_json=payload,
                line_start=block.source_range.line_start,
                line_end=block.source_range.line_end,
                links=links,
                parent_block_id=block.parent_block_id,
            )
        )

    return drafts, title


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
